"""The interview brain: candidate utterance in, interviewer reply out.

This module is deliberately transport-agnostic. It knows nothing about RTC, WebSockets,
SSE, audio, or who is asking - only about a session, what the candidate just said, and
what the interviewer should say back. That is what lets the same interview run over two
completely different voice pipelines without the behaviour drifting between them:

  - `api/llm.py` wraps `compose()` in an OpenAI-shaped SSE endpoint, which is the
    contract BytePlus RTC's CustomLLM mode speaks (VOICE_MODE=rtc).
  - `services/voice/pipeline.py` calls `compose()` directly between its own ASR and TTS
    legs (VOICE_MODE=local).

Anything that belongs to "how the interview thinks" - guardrails, the plan, retrieval,
memory, the audit - lives here. Anything that belongs to "how the audio moves" lives in
the caller. Keep that line and switching pipelines stays a config change.

Turn pipeline, ordered so nothing avoidable sits in front of the first token:
  1. planner decides ask / follow-up / wrap-up          (local DB)
  2. rule-based guardrail on the candidate utterance    (microseconds, no network)
  3. retrieval + memory, concurrently                   (one network round trip)
  4. retrieval-confidence guardrail                     (local)
  5. stream ModelArk
  6. the caller persists turns; `audit()` runs off the hot path

`compose()` narrates that sequence through an optional `notify` callback. Which stage the
turn is in is knowledge only this module has, and a candidate staring at a silent screen
needs it more than anyone - so it is reported outward rather than left for the caller to
guess from timing. Callers that have nowhere to put it pass nothing.
"""

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.models import Interview, InterviewSession, TranscriptTurn
from app.services import guardrails, memory, modelark, planner, prompts, rag

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ComposedTurn:
    """What the interviewer will say, plus the bookkeeping the caller must persist.

    `text` is an async iterator rather than a string because time-to-first-token is what
    the candidate hears as responsiveness - both callers stream it onward (to SSE, or
    into TTS) rather than waiting for the full completion.
    """

    text: AsyncIterator[str]
    plan_item_id: str | None
    guardrail_action: str | None


async def load_context(session_id: str) -> tuple[InterviewSession, Interview] | None:
    """Fetch the session and its interview in one query, on its own DB session."""
    async with SessionLocal() as db:
        stmt = (
            select(InterviewSession, Interview)
            .join(Interview, Interview.id == InterviewSession.interview_id)
            .where(InterviewSession.id == session_id)
        )
        row = (await db.execute(stmt)).first()
    return (row[0], row[1]) if row is not None else None


async def _retrieve(interview_id: str, query: str) -> list[rag.RetrievedChunk]:
    """Own session so this can run concurrently with the memory fetch."""
    async with SessionLocal() as db:
        return await rag.search(db, interview_id, query, top_k=4)


async def _recall(session_id: str, query: str) -> list[dict]:
    async with SessionLocal() as db:
        return await memory.recall(db, session_id, query)


async def compose(
    db: AsyncSession,
    session: InterviewSession,
    interview: Interview,
    candidate_text: str,
    *,
    notify: Callable[[str], None] | None = None,
) -> ComposedTurn:
    """Run the turn pipeline for one candidate utterance.

    `notify` is called with a stage id ("thinking", "retrieving", "composing") as the
    turn moves through the pipeline. It must not block or raise - it is on the hot path
    in front of the first token, which is the latency the candidate actually hears.
    """
    say = notify or (lambda _stage: None)

    # 1. Blunt guardrail cases short-circuit before any network call at all.
    say("thinking")
    decision = await planner.decide_next_action(db, session, candidate_text)
    next_question = decision.plan_item.question if decision.plan_item else None
    plan_item_id = decision.plan_item.id if decision.plan_item else None

    verdict = guardrails.check_input(
        candidate_text, language=interview.language, next_question=next_question
    )
    if verdict.blocked:
        log.info("Guardrail %s on session %s: %s", verdict.reason, session.id, verdict.matched)
        return ComposedTurn(
            _literal(verdict.deflection or ""), plan_item_id, f"blocked:{verdict.reason}"
        )

    # 2. Retrieval and memory in parallel - one round trip instead of two.
    say("retrieving")
    chunks, history = await asyncio.gather(
        _retrieve(interview.id, candidate_text or interview.position_title),
        _recall(session.id, candidate_text),
    )

    # 3. If the candidate asked something the documents do not cover, deflect rather
    #    than letting the model invent an answer about the company.
    retrieval_verdict = guardrails.check_retrieval(
        rag.confidence(chunks),
        is_question=guardrails.looks_like_question(candidate_text),
        language=interview.language,
        next_question=next_question,
    )
    if retrieval_verdict.blocked:
        return ComposedTurn(
            _literal(retrieval_verdict.deflection or ""),
            plan_item_id,
            f"blocked:{retrieval_verdict.reason}",
        )

    system = prompts.build_system_prompt(
        interview, decision.as_directive(interview.language), chunks, session.candidate_name
    )
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    if candidate_text:
        messages.append({"role": "user", "content": candidate_text})

    # The model call has not started yet - stream_chat is lazy - but this is the last
    # thing that happens before it does, and the wait the candidate is about to sit
    # through is generation, not retrieval.
    say("composing")
    return ComposedTurn(modelark.stream_chat(messages, max_tokens=300), plan_item_id, None)


async def _literal(text: str) -> AsyncIterator[str]:
    """Wrap a canned deflection in the same streaming interface as a live completion."""
    yield text


async def opening_greeting(
    db: AsyncSession, session: InterviewSession, interview: Interview
) -> str:
    """The interviewer speaks first.

    Generated at session start so the candidate is greeted the moment they connect
    rather than sitting in silence waiting to speak first. RTC mode hands this to
    StartVoiceChat as its WelcomeMessage; local mode speaks it through our own TTS as
    soon as the candidate's socket opens.
    """
    decision = await planner.opening_decision(db, session)
    chunks = await rag.search(
        db, interview.id, interview.position_title, doc_type="job_requirement", top_k=3
    )
    first_question = (
        decision.plan_item.question if decision.plan_item else "Tell me about yourself."
    )
    directive = (
        f"NEXT ACTION: ASK: {first_question}\n"
        "This is the very start of the interview. Greet the candidate by name, say one "
        "sentence about the role, then ask this first question."
    )
    system = prompts.build_system_prompt(interview, directive, chunks, session.candidate_name)
    return await modelark.chat(
        [{"role": "system", "content": system}, {"role": "user", "content": "(begin)"}],
        max_tokens=200,
    )


async def audit(session_id: str, candidate_text: str, ai_text: str) -> None:
    """Post-hoc LLM review. Runs after the candidate has already heard the answer, so
    its latency costs nothing. Advisory only - it annotates, it does not censor."""
    try:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, session_id)
            if session is None:
                return
            interview = await db.get(Interview, session.interview_id)
            verdict = await guardrails.review_turn(
                candidate_text, ai_text, interview.guardrail_notes if interview else None
            )
            severity = verdict.get("severity")
            if severity in ("medium", "high"):
                log.warning(
                    "Guardrail audit flagged session %s (%s): %s",
                    session_id,
                    severity,
                    verdict.get("concern"),
                )
                stmt = (
                    select(TranscriptTurn)
                    .where(TranscriptTurn.session_id == session_id)
                    .order_by(TranscriptTurn.turn_index.desc())
                    .limit(1)
                )
                latest = (await db.execute(stmt)).scalar_one_or_none()
                if latest is not None and latest.speaker == "ai":
                    latest.guardrail_action = f"flagged:{severity}"
                    await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Guardrail audit failed for session %s", session_id)
