"""The custom LLM endpoint RTC calls once per candidate turn.

Shaped as OpenAI /v1/chat/completions with SSE streaming, because that is the contract
RTC custom-LLM mode speaks. Streaming is not optional here: RTC feeds each chunk to TTS
as it arrives, so time-to-first-token is what the candidate hears as responsiveness.
Buffering the full completion first would add seconds of silence to every turn.

VERIFY: confirm the exact request shape RTC sends (especially how it identifies the
session) against the BytePlus RTC conversational-AI docs. `_resolve_session_id` accepts
several plausible carriers so this keeps working whichever one it turns out to be.

Turn pipeline, ordered so nothing avoidable sits in front of the first token:
  1. rule-based guardrail on the candidate utterance   (microseconds, no network)
  2. planner decides ask / follow-up / wrap-up          (local DB)
  3. retrieval + memory, concurrently                   (one network round trip)
  4. retrieval-confidence guardrail                     (local)
  5. stream ModelArk
  6. persist turns, then run the LLM audit off the hot path
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.models import Interview, InterviewSession
from app.services import guardrails, memory, modelark, planner, prompts, rag, turns

log = logging.getLogger(__name__)
router = APIRouter(tags=["llm"])

MODEL_NAME = "ai-interviewer"


def _authorise(request: Request) -> None:
    """RTC sends the APIKey from LLMConfig as a bearer token."""
    expected = settings.rtc_app_key
    if settings.mock_ai or not expected:
        return
    header = request.headers.get("authorization", "")
    provided = header.removeprefix("Bearer ").strip()
    if provided != expected:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid LLM endpoint credentials")


def _resolve_session_id(request: Request, body: dict) -> str | None:
    for header in ("x-session-id", "x-interview-session", "session-id"):
        if value := request.headers.get(header):
            return value
    for key in ("session_id", "user", "conversation_id"):
        if value := body.get(key):
            return str(value)
    # Last resort: a system message carrying the id, for RTC builds that only allow
    # prompt injection rather than custom headers.
    for message in body.get("messages", []):
        content = message.get("content") or ""
        if isinstance(content, str) and content.startswith("SESSION_ID:"):
            return content.split(":", 1)[1].strip()
    return None


def _last_user_message(body: dict) -> str:
    for message in reversed(body.get("messages", [])):
        if message.get("role") == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
            if isinstance(content, list):
                return " ".join(
                    part.get("text", "") for part in content if isinstance(part, dict)
                )
    return ""


async def _load_context(session_id: str) -> tuple[InterviewSession, Interview]:
    async with SessionLocal() as db:
        stmt = (
            select(InterviewSession, Interview)
            .join(Interview, Interview.id == InterviewSession.interview_id)
            .where(InterviewSession.id == session_id)
        )
        row = (await db.execute(stmt)).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown session {session_id}")
    return row[0], row[1]


async def _retrieve(interview_id: str, query: str) -> list[rag.RetrievedChunk]:
    """Own session so this can run concurrently with the memory fetch."""
    async with SessionLocal() as db:
        return await rag.search(db, interview_id, query, top_k=4)


async def _recall(session_id: str, query: str) -> list[dict]:
    async with SessionLocal() as db:
        return await memory.recall(db, session_id, query)


async def _audit(session_id: str, candidate_text: str, ai_text: str) -> None:
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
                    select(turns.TranscriptTurn)
                    .where(turns.TranscriptTurn.session_id == session_id)
                    .order_by(turns.TranscriptTurn.turn_index.desc())
                    .limit(1)
                )
                latest = (await db.execute(stmt)).scalar_one_or_none()
                if latest is not None and latest.speaker == "ai":
                    latest.guardrail_action = f"flagged:{severity}"
                    await db.commit()
    except Exception:  # noqa: BLE001
        log.exception("Guardrail audit failed for session %s", session_id)


def _sse_chunk(completion_id: str, created: int, delta: dict, finish: str | None) -> str:
    payload = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": MODEL_NAME,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/v1/chat/completions")
@router.post("/llm/respond")
async def chat_completions(
    request: Request,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    _authorise(request)
    body = await request.json()

    session_id = _resolve_session_id(request, body)
    if not session_id:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No session id on the request. Check the CustomHeaders in the RTC LLMConfig.",
        )

    session, interview = await _load_context(session_id)
    candidate_text = _last_user_message(body).strip()
    stream = bool(body.get("stream", True))

    reply_text, plan_item_id, guardrail_action = await _compose(
        db, session, interview, candidate_text
    )

    if candidate_text:
        await turns.record_turn(db, session.id, "candidate", candidate_text)

    if stream:
        return StreamingResponse(
            _stream_response(
                session.id, candidate_text, reply_text, plan_item_id, guardrail_action
            ),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    resolved = "".join([chunk async for chunk in reply_text])
    await turns.record_turn(
        db, session.id, "ai", resolved, plan_item_id=plan_item_id,
        guardrail_action=guardrail_action,
    )
    background.add_task(_audit, session.id, candidate_text, resolved)
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": MODEL_NAME,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": resolved},
                "finish_reason": "stop",
            }
        ],
    }


async def _compose(
    db: AsyncSession,
    session: InterviewSession,
    interview: Interview,
    candidate_text: str,
) -> tuple[AsyncIterator[str], str | None, str | None]:
    """Run the turn pipeline and return an async iterator of response text."""
    # 1. Blunt guardrail cases short-circuit before any network call at all.
    decision = await planner.decide_next_action(db, session, candidate_text)
    next_question = decision.plan_item.question if decision.plan_item else None
    plan_item_id = decision.plan_item.id if decision.plan_item else None

    verdict = guardrails.check_input(
        candidate_text, language=interview.language, next_question=next_question
    )
    if verdict.blocked:
        log.info("Guardrail %s on session %s: %s", verdict.reason, session.id, verdict.matched)
        return _literal(verdict.deflection or ""), plan_item_id, f"blocked:{verdict.reason}"

    # 2. Retrieval and memory in parallel - one round trip instead of two.
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
        return (
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

    return modelark.stream_chat(messages, max_tokens=300), plan_item_id, None


async def _literal(text: str) -> AsyncIterator[str]:
    """Wrap a canned deflection in the same streaming interface as a live completion."""
    yield text


async def _stream_response(
    session_id: str,
    candidate_text: str,
    source: AsyncIterator[str],
    plan_item_id: str | None,
    guardrail_action: str | None,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    collected: list[str] = []

    yield _sse_chunk(completion_id, created, {"role": "assistant"}, None)
    try:
        async for piece in source:
            collected.append(piece)
            yield _sse_chunk(completion_id, created, {"content": piece}, None)
    except Exception:  # noqa: BLE001
        # A mid-turn LLM failure must not leave the candidate in silence.
        log.exception("LLM stream failed for session %s", session_id)
        fallback = "Sorry, I lost my train of thought there. Could you say that again?"
        collected.append(fallback)
        yield _sse_chunk(completion_id, created, {"content": fallback}, None)

    yield _sse_chunk(completion_id, created, {}, "stop")
    yield "data: [DONE]\n\n"

    reply = "".join(collected).strip()
    if reply:
        async with SessionLocal() as db:
            await turns.record_turn(
                db,
                session_id,
                "ai",
                reply,
                plan_item_id=plan_item_id,
                guardrail_action=guardrail_action,
            )
        # Fire-and-forget: BackgroundTasks on a StreamingResponse may already be
        # finalised by the time the generator gets here.
        asyncio.create_task(_audit(session_id, candidate_text, reply))  # noqa: RUF006
