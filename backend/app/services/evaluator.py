"""The LLM judge: one completed transcript in, one categorical verdict out.

Design constraints, and why the code looks the way it does:

**Four wide tiers, never a number.** An LLM judge cannot reproduce a 0-100 score, or even
a 1-5 star, consistently across runs on the same transcript - the noise swamps the signal
at that resolution. It can reliably tell "clearly good", "mixed", "clearly short" and
"I could not read this" apart. So the output is four named tiers and the prompt forbids
inventing numbers anywhere.

**One pass.** No self-consistency sampling, no repeated calls. Per completed interview
this is exactly one ModelArk request.

**Reasoning precedes the label.** The judge must fill in all three dimensions and the
transcript-quality assessment before it names an overall tier, and the JSON key order in
the prompt enforces that ordering in the generation itself. A model that emits the label
first will rationalise towards it for the rest of the response.

**A short interview is not a weak candidate.** How well the words came through and how
much of the interview happened are two different facts, so they are two different fields.
`transcript_quality` covers only transcription damage; `interview_completeness` records
whether the conversation actually finished. Folding the second into the first is what
makes a truncated interview unreadable to HR - a clean recording of two answers would be
filed as a damaged transcript, when the recording was fine and the interview was not.
Completeness never moves the verdict: it qualifies it, so a Decent Fit reached on two
answers is never mistaken for one reached on a full conversation.

**Transcript noise is not candidate weakness.** ASR mangles proper nouns, drops syllables
and truncates sentences. Scoring that as an inarticulate candidate would punish people for
our own pipeline, so `transcript_quality` is a first-class output and an `unusable` rating
forces Inconclusive regardless of what the criteria found. That gate is in Python, below -
it is far too important to leave to the model's own discipline.

**No fabricated evidence.** Every dimension verdict must cite candidate turns by index.
`_apply_evidence_gate` checks each citation against the real transcript and drops the ones
that do not exist or that point at the interviewer rather than the candidate. A positive
verdict left with no surviving citation is downgraded to `not_evidenced` - which is why
that value exists separately from `not_a_fit`.
"""

import asyncio
import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import (
    CRITERIA_VERDICTS,
    DIMENSION_INTENT,
    DIMENSION_KEYS,
    DIMENSION_LABELS,
    DIMENSION_VERDICTS,
    INTERVIEW_COMPLETENESS,
    TRANSCRIPT_QUALITY,
    VERDICT_DECENT,
    VERDICT_INCONCLUSIVE,
    VERDICT_NOT_A_FIT,
    VERDICT_STRONG,
    Evaluation,
    EvaluationDimension,
    Interview,
    InterviewSession,
    Rubric,
    TranscriptTurn,
)
from app.services import modelark
from app.services import rubric as rubric_service
from app.services.planner import LANGUAGE_NAMES

log = logging.getLogger(__name__)

# Below this much candidate speech there is nothing to judge, and calling the model would
# only invite it to invent a reading of silence. Short-circuits to Inconclusive.
MIN_CANDIDATE_WORDS = 10

# The transcript is the bulk of the prompt. Long enough for a full 30-minute interview,
# capped so a runaway session cannot blow the context window.
TRANSCRIPT_CHAR_BUDGET = 24000

NOTE_MAX_CHARS = 1500
SUMMARY_MAX_CHARS = 2000


class NotScoreable(Exception):
    """Raised when scoring cannot be attempted at all - no approved rubric, no transcript
    to read. Distinct from a judge failure, which produces a `failed` Evaluation row."""


# ------------------------------------------------------------------ transcript prep


def _render_transcript(turns: list[TranscriptTurn]) -> tuple[str, set[int], int]:
    """Render the transcript for the prompt, and report which turn indexes the judge is
    allowed to cite.

    Turns are labelled with their real `turn_index`, not their position in this list, so
    a citation maps straight back to a row HR can open. Only candidate turns are citable:
    crediting the candidate for something the interviewer said is one of the easier ways
    for a judge to hallucinate competence.
    """
    lines: list[str] = []
    citable: set[int] = set()
    candidate_words = 0

    for turn in turns:
        if turn.speaker == "candidate":
            citable.add(turn.turn_index)
            candidate_words += len(turn.text.split())
            speaker = "CANDIDATE"
        elif turn.speaker == "ai":
            speaker = "INTERVIEWER"
        else:
            continue
        lines.append(f"[{turn.turn_index}] {speaker}: {turn.text}")

    body = "\n".join(lines)
    if len(body) > TRANSCRIPT_CHAR_BUDGET:
        # Keep the tail: the later questions are the ones that probe hardest, and the
        # opening is mostly greeting. Say so, so the judge does not read the cut as the
        # candidate having stopped talking.
        body = (
            "[... the opening of this transcript was truncated for length; it is not "
            "missing from the interview ...]\n"
            + body[-TRANSCRIPT_CHAR_BUDGET:]
        )
    return body, citable, candidate_words


def _render_rubric(rubric: Rubric) -> str:
    by_key = {d.key: d for d in rubric.dimensions}
    blocks: list[str] = []
    for key in DIMENSION_KEYS:
        dimension = by_key.get(key)
        blocks.append(
            f'### {DIMENSION_LABELS[key]}  (key: "{key}")\n'
            f"What this dimension is about: {DIMENSION_INTENT[key]}\n"
            f"STRONG evidence looks like: {(dimension.strong_band if dimension else '').strip() or 'not specified'}\n"
            f"DECENT evidence looks like: {(dimension.decent_band if dimension else '').strip() or 'not specified'}\n"
            f"NOT A FIT evidence looks like: {(dimension.not_fit_band if dimension else '').strip() or 'not specified'}"
        )
    return "\n\n".join(blocks)


# ----------------------------------------------------------------------- the prompt


def _system_prompt(language: str) -> str:
    lang = LANGUAGE_NAMES.get(language, "English")
    return (
        "You evaluate the transcript of a short get-to-know job interview conducted by an "
        "AI interviewer, against a rubric written by the hiring team. Your output helps HR "
        "triage candidates. You do not make hiring decisions and you never address the "
        "candidate.\n"
        "\n"
        "RULES\n"
        "1. Work in the order of the output schema. Evaluate all three dimensions first, "
        "then assess transcript quality and interview completeness, then and only then "
        "name the overall verdict, then write the summary. Do not decide the verdict "
        "first and justify it afterwards.\n"
        "2. Judge only what the CANDIDATE actually said. Do not infer a skill that was not "
        "demonstrated. Do not credit the candidate for anything the INTERVIEWER said. Do "
        "not assume experience, seniority or tooling that was not stated.\n"
        "3. Every dimension verdict must cite the candidate turns it rests on, by the "
        "number in square brackets at the start of the line. Cite only lines marked "
        "CANDIDATE. If a dimension has nothing in the transcript to rest on, its verdict "
        'is "not_evidenced" and its evidence list is empty - do not stretch an unrelated '
        "answer to cover it.\n"
        "4. Speech-to-text errors are NOT candidate weaknesses. Garbled words, wrong "
        "homophones, dropped syllables, nonsense fragments mid-sentence and abruptly cut "
        "sentences are artefacts of automatic transcription. When you see them, report "
        "them in transcript_quality. Never mark a dimension down for them, and never "
        "describe the candidate as unclear when it is the transcript that is unclear.\n"
        "5. This is a lightweight conversation, not a technical exam. \"strong\" means the "
        "candidate gave clear, credible signal on that dimension. It does not require an "
        "exhaustive or quantified answer.\n"
        "6. Never produce a number, score, percentage, rating or ranking anywhere in your "
        "output. The tiers are the only measurement that exists.\n"
        "7. Judge the candidate against the rubric bands as written, not against your own "
        "idea of a good hire.\n"
        "\n"
        "TRANSCRIPT QUALITY RATINGS\n"
        "This is only about how well the words came through. A short interview that was "
        'cleanly recorded is "usable" - how much of the interview happened is a separate '
        "question, answered under completeness below.\n"
        '- "usable": readable; any errors are cosmetic.\n'
        '- "degraded": noticeable transcription damage - garbled words, dropped audio, '
        "nonsense fragments - but enough intact answers survive to judge the candidate "
        "fairly.\n"
        '- "unusable": so garbled or incoherent that judging this candidate would be '
        "judging the transcription. Use this whenever a fair reading is not possible - it "
        "protects the candidate, and HR is told to re-interview rather than given a "
        "verdict built on noise.\n"
        "\n"
        "INTERVIEW COMPLETENESS\n"
        "Did the conversation actually finish? Read how the transcript ends. An "
        "interviewer closing with thanks and a next-steps line is a finished interview. A "
        "transcript that stops mid-thread, where the candidate stops responding, or where "
        "the interviewer ends it early because of a technical problem, is not.\n"
        '- "complete": the interviewer worked through their questions and closed the '
        "conversation normally.\n"
        '- "partial": the interview ended before it had run its course, so whole '
        "dimensions may never have been asked about. In the note, say where it stopped "
        "and what was therefore never covered.\n"
        "A partial interview is NOT a mark against the candidate and must never lower a "
        'dimension verdict. Dimensions that were never reached are "not_evidenced".\n'
        "\n"
        "OUTPUT\n"
        "Return ONLY a JSON object, with the keys in exactly this order:\n"
        "{\n"
        '  "dimensions": [{"key": "background_fit", "note": str, '
        '"evidence": [{"turn": int, "quote": str}], '
        '"verdict": "strong"|"decent"|"not_a_fit"|"not_evidenced"}, '
        "... one entry per dimension, in the order given in the rubric ...],\n"
        '  "transcript_quality": {"rating": "usable"|"degraded"|"unusable", "note": str},\n'
        '  "interview_completeness": {"rating": "complete"|"partial", "note": str},\n'
        '  "criteria_verdict": "strong_fit"|"decent_fit"|"not_a_fit",\n'
        '  "summary": str\n'
        "}\n"
        'Within each dimension, write "note" before choosing "verdict": the note is your '
        "reasoning, the verdict is its conclusion. Each evidence \"quote\" is a short "
        "paraphrase of what the candidate said in that turn.\n"
        '"criteria_verdict" covers only how the candidate did against the three '
        'dimensions. Never return "inconclusive" here - if the transcript is unreadable, '
        'say so in transcript_quality and still give your best reading of the criteria.\n'
        '"summary" is three to five plain sentences written for a busy HR reader: what the '
        "candidate showed, what is thin or missing, and anything about the transcript they "
        "should know. No jargon, no bullet points, no scores.\n"
        f"Write every note, quote and the summary in {lang}."
    )


def _user_prompt(interview: Interview, session: InterviewSession, rubric: Rubric, transcript: str) -> str:
    return (
        f"POSITION: {interview.position_title}\n"
        f"CANDIDATE: {session.candidate_name}\n"
        f"HR NOTES ON THIS INTERVIEW: {interview.guardrail_notes or 'none'}\n\n"
        f"RUBRIC\n{_render_rubric(rubric)}\n\n"
        f"TRANSCRIPT\n{transcript}"
    )


# ------------------------------------------------------------------------- the gates


def _apply_evidence_gate(
    parsed_dimensions: list[dict], citable: set[int]
) -> tuple[list[dict], bool]:
    """Check every citation against the real transcript.

    Returns the cleaned dimensions and whether any verdict had to be downgraded. A
    citation survives only if it names a turn that exists AND that the candidate spoke.
    A positive verdict with no surviving citation is not a verdict, it is an assertion,
    so it becomes `not_evidenced`.
    """
    downgraded = False
    cleaned: list[dict] = []

    for dimension in parsed_dimensions:
        evidence: list[dict] = []
        dropped = 0
        for item in dimension.get("evidence") or []:
            if not isinstance(item, dict):
                dropped += 1
                continue
            try:
                turn = int(item.get("turn"))
            except (TypeError, ValueError):
                dropped += 1
                continue
            if turn not in citable:
                dropped += 1
                continue
            evidence.append({"turn": turn, "quote": str(item.get("quote") or "")[:600]})

        warnings: list[str] = []
        if dropped:
            warnings.append(
                f"{dropped} citation(s) did not match a candidate turn in this transcript "
                "and were removed."
            )

        verdict = dimension.get("verdict")
        if verdict in ("strong", "decent") and not evidence:
            verdict = "not_evidenced"
            downgraded = True
            warnings.append(
                "Downgraded to not evidenced: the judge gave a positive verdict but cited "
                "nothing in the transcript that supports it."
            )

        cleaned.append(
            {
                "key": dimension["key"],
                "verdict": verdict,
                "note": dimension.get("note", ""),
                "evidence": evidence,
                "warning": " ".join(warnings) or None,
            }
        )

    return cleaned, downgraded


def _resolve_verdict(
    criteria_verdict: str,
    quality: str,
    downgraded: bool,
) -> tuple[str, str | None]:
    """Turn the judge's criteria reading into the final tier.

    Two overrides, both deterministic and both stated to HR rather than applied silently:
    a strong_fit cannot stand on a dimension that lost its evidence, and an unusable
    transcript forces Inconclusive no matter how the criteria read.
    """
    verdict = criteria_verdict
    reason: str | None = None

    if downgraded and verdict == VERDICT_STRONG:
        verdict = VERDICT_DECENT
        reason = (
            "Lowered from Strong Fit: at least one dimension lost its supporting evidence "
            "when the judge's citations were checked against the transcript, so "
            "clear evidence across all three dimensions does not hold."
        )

    if quality == "unusable":
        verdict = VERDICT_INCONCLUSIVE
        reason = (
            "Forced to Inconclusive: the transcript was judged unusable, so this is a "
            "transcription failure rather than a reading of the candidate. Re-interview "
            "or review the recording before drawing any conclusion."
        )

    return verdict, reason


# ----------------------------------------------------------------------- persistence


async def _replace(
    db: AsyncSession,
    session_id: str,
    *,
    rubric: Rubric | None,
    fields: dict,
    dimensions: list[dict],
) -> Evaluation:
    """Write the one current evaluation for a session, replacing any previous run."""
    existing = (
        await db.execute(select(Evaluation).where(Evaluation.session_id == session_id))
    ).scalar_one_or_none()
    if existing is not None:
        await db.delete(existing)
        await db.flush()

    evaluation = Evaluation(
        session_id=session_id,
        rubric_id=rubric.id if rubric else None,
        rubric_version=rubric.version if rubric else None,
        model="mock" if settings.mock_ai else (settings.modelark_endpoint_id or "modelark"),
        completed_at=datetime.now(timezone.utc),
        **fields,
    )
    db.add(evaluation)
    await db.flush()

    for order, key in enumerate(DIMENSION_KEYS):
        found = next((d for d in dimensions if d["key"] == key), None)
        db.add(
            EvaluationDimension(
                evaluation_id=evaluation.id,
                key=key,
                order_index=order,
                verdict=(found or {}).get("verdict") or "not_evidenced",
                note=str((found or {}).get("note") or "")[:NOTE_MAX_CHARS],
                evidence_json=json.dumps((found or {}).get("evidence") or []),
                evidence_warning=(found or {}).get("warning"),
            )
        )

    await db.commit()
    return await load(db, session_id)  # type: ignore[return-value]


async def load(db: AsyncSession, session_id: str) -> Evaluation | None:
    stmt = (
        select(Evaluation)
        .where(Evaluation.session_id == session_id)
        .options(selectinload(Evaluation.dimensions))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


def serialize(evaluation: Evaluation | None) -> dict | None:
    if evaluation is None:
        return None
    by_key = {d.key: d for d in evaluation.dimensions}
    dimensions = []
    for order, key in enumerate(DIMENSION_KEYS):
        found = by_key.get(key)
        try:
            evidence = json.loads(found.evidence_json) if found else []
        except (json.JSONDecodeError, TypeError):
            evidence = []
        dimensions.append(
            {
                "key": key,
                "label": DIMENSION_LABELS[key],
                "intent": DIMENSION_INTENT[key],
                "order_index": order,
                "verdict": (found.verdict if found else None) or "not_evidenced",
                "note": (found.note if found else "") or "",
                "evidence": [e for e in evidence if isinstance(e, dict) and "turn" in e],
                "evidence_warning": found.evidence_warning if found else None,
            }
        )
    return {
        "id": evaluation.id,
        "status": evaluation.status,
        "error": evaluation.error,
        "verdict": evaluation.verdict,
        "criteria_verdict": evaluation.criteria_verdict,
        "adjustment_reason": evaluation.adjustment_reason,
        "transcript_quality": evaluation.transcript_quality,
        "transcript_quality_note": evaluation.transcript_quality_note,
        "interview_completeness": evaluation.interview_completeness,
        "interview_completeness_note": evaluation.interview_completeness_note,
        "summary": evaluation.summary or "",
        "model": evaluation.model,
        "candidate_turn_count": evaluation.candidate_turn_count,
        "rubric_version": evaluation.rubric_version,
        "completed_at": evaluation.completed_at,
        "dimensions": dimensions,
    }


# ---------------------------------------------------------------------------- entry


async def evaluate(db: AsyncSession, session: InterviewSession) -> Evaluation:
    """Score one completed interview. Exactly one model call, or none at all."""
    interview = await db.get(Interview, session.interview_id)
    if interview is None:
        raise NotScoreable("The interview this session belongs to no longer exists")

    approved = await rubric_service.load(db, interview.id)
    if approved is None or approved.status != "approved":
        raise NotScoreable(
            "This interview has no approved scoring rubric. Author one on the interview "
            "setup page and approve it, then score this candidate."
        )

    turns = list(
        (
            await db.execute(
                select(TranscriptTurn)
                .where(TranscriptTurn.session_id == session.id)
                .order_by(TranscriptTurn.turn_index)
            )
        ).scalars().all()
    )
    transcript, citable, candidate_words = _render_transcript(turns)

    # Nothing to read. Answered here rather than by the model, which would otherwise be
    # asked to form an impression of silence.
    if not citable or candidate_words < MIN_CANDIDATE_WORDS:
        return await _replace(
            db,
            session.id,
            rubric=approved,
            fields={
                "status": "complete",
                "verdict": VERDICT_INCONCLUSIVE,
                "criteria_verdict": None,
                "transcript_quality": "unusable",
                "transcript_quality_note": (
                    "The candidate said almost nothing that reached the transcript "
                    f"({candidate_words} word(s) across {len(citable)} turn(s)). That is an "
                    "incomplete interview, not a weak one."
                ),
                "interview_completeness": "partial",
                "interview_completeness_note": (
                    "There is no evidence the interview ran its course - almost nothing "
                    "from the candidate reached the transcript at all."
                ),
                "summary": (
                    "This interview produced no usable candidate speech, so there is nothing "
                    "to evaluate. The session may have ended early, or the microphone or "
                    "transcription may have failed. Re-interview before drawing any "
                    "conclusion about this candidate."
                ),
                "adjustment_reason": (
                    "Forced to Inconclusive before the judge ran: an empty transcript cannot "
                    "be scored."
                ),
                "candidate_turn_count": len(citable),
            },
            dimensions=[],
        )

    messages = [
        {"role": "system", "content": _system_prompt(interview.language)},
        {
            "role": "user",
            "content": _user_prompt(interview, session, approved, transcript),
        },
    ]

    try:
        raw = await modelark.chat_json(messages, max_tokens=2500)
    except Exception as exc:
        log.exception("Scoring call failed for session %s", session.id)
        return await _replace(
            db,
            session.id,
            rubric=approved,
            fields={
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}"[:1000],
                "summary": "",
                "candidate_turn_count": len(citable),
            },
            dimensions=[],
        )

    parsed = _parse(raw)
    if parsed is None:
        return await _replace(
            db,
            session.id,
            rubric=approved,
            fields={
                "status": "failed",
                "error": (
                    "The model did not return a usable evaluation. No verdict was recorded "
                    "rather than guessing one - re-run scoring to try again."
                ),
                "summary": "",
                "candidate_turn_count": len(citable),
            },
            dimensions=[],
        )

    dimensions, downgraded = _apply_evidence_gate(parsed["dimensions"], citable)
    verdict, reason = _resolve_verdict(
        parsed["criteria_verdict"], parsed["transcript_quality"], downgraded
    )

    return await _replace(
        db,
        session.id,
        rubric=approved,
        fields={
            "status": "complete",
            "verdict": verdict,
            "criteria_verdict": parsed["criteria_verdict"],
            "adjustment_reason": reason,
            "transcript_quality": parsed["transcript_quality"],
            "transcript_quality_note": parsed["transcript_quality_note"],
            "interview_completeness": parsed["interview_completeness"],
            "interview_completeness_note": parsed["interview_completeness_note"],
            "summary": parsed["summary"],
            "candidate_turn_count": len(citable),
        },
        dimensions=dimensions,
    )


def _parse(raw: dict | list) -> dict | None:
    """Pull the judge's response into a known shape, or give up.

    Giving up is a real outcome here. Coercing a half-parsed response into a tier would
    produce a verdict nobody stands behind, on a page HR uses to triage people.
    """
    if not isinstance(raw, dict):
        return None

    quality_block = raw.get("transcript_quality")
    if isinstance(quality_block, dict):
        quality = str(quality_block.get("rating") or "").strip().lower()
        quality_note = str(quality_block.get("note") or "")[:NOTE_MAX_CHARS]
    else:
        quality = str(quality_block or "").strip().lower()
        quality_note = ""
    if quality not in TRANSCRIPT_QUALITY:
        # An unrecognised rating must not silently read as "fine". Degraded is the
        # conservative middle: it warns HR without forcing Inconclusive on its own.
        quality = "degraded"
        quality_note = (quality_note + " (the judge did not return a recognised transcript"
                        " quality rating)").strip()

    completeness_block = raw.get("interview_completeness")
    if isinstance(completeness_block, dict):
        completeness = str(completeness_block.get("rating") or "").strip().lower()
        completeness_note = str(completeness_block.get("note") or "")[:NOTE_MAX_CHARS]
    else:
        completeness = str(completeness_block or "").strip().lower()
        completeness_note = ""
    if completeness not in INTERVIEW_COMPLETENESS:
        # Unlike transcript quality, there is no safe default here. Guessing "complete"
        # would hide a truncated interview, and guessing "partial" would put a warning on
        # a conversation that finished perfectly well. Record nothing, and the page shows
        # what it showed before this field existed.
        completeness = None  # type: ignore[assignment]
        completeness_note = ""

    criteria = str(raw.get("criteria_verdict") or "").strip().lower()
    if criteria not in CRITERIA_VERDICTS:
        criteria = None  # type: ignore[assignment]

    dimensions: list[dict] = []
    seen: set[str] = set()
    for item in raw.get("dimensions") or []:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in DIMENSION_KEYS or key in seen:
            continue
        seen.add(key)
        verdict = str(item.get("verdict") or "").strip().lower()
        dimensions.append(
            {
                "key": key,
                "verdict": verdict if verdict in DIMENSION_VERDICTS else "not_evidenced",
                "note": str(item.get("note") or "")[:NOTE_MAX_CHARS],
                "evidence": item.get("evidence") or [],
            }
        )

    summary = str(raw.get("summary") or "").strip()[:SUMMARY_MAX_CHARS]

    # A response with no dimensions and no summary is not an evaluation, whatever else it
    # contained.
    if not dimensions and not summary:
        return None

    if criteria is None:
        # Only reached when the model named a tier we do not recognise. Derive one from
        # the dimensions it did produce rather than discarding the whole pass.
        criteria = _derive_criteria(dimensions)

    return {
        "dimensions": dimensions,
        "transcript_quality": quality,
        "transcript_quality_note": quality_note or None,
        "interview_completeness": completeness,
        "interview_completeness_note": completeness_note or None,
        "criteria_verdict": criteria,
        "summary": summary,
    }


def _derive_criteria(dimensions: list[dict]) -> str:
    """Fallback only: an overall tier from the three dimension verdicts."""
    verdicts = [d["verdict"] for d in dimensions]
    strong = verdicts.count("strong")
    weak = verdicts.count("not_a_fit") + verdicts.count("not_evidenced")
    if strong == len(DIMENSION_KEYS):
        return VERDICT_STRONG
    if weak >= 2:
        return VERDICT_NOT_A_FIT
    return VERDICT_DECENT


def schedule(session_id: str) -> None:
    """Score this session once the current request is out of the way.

    Called from every path that can finish an interview - HR ending it, the candidate
    ending it, and the RTC callback that fires when they simply close the tab - so a
    verdict is waiting whichever way the interview stopped. Nothing awaits the result.

    The task is held in a module-level set because asyncio keeps only a weak reference to
    a running task: without this, a garbage collection between the response and the
    model's reply would silently cancel the scoring pass and leave HR looking at a blank
    verdict with nothing in the log.
    """
    task = asyncio.create_task(score_in_background(session_id))
    _pending.add(task)
    task.add_done_callback(_pending.discard)


_pending: set[asyncio.Task] = set()


async def score_in_background(session_id: str) -> None:
    """Fire-and-forget scoring, used when a session ends.

    Opens its own DB session because the request that ended the interview is already
    finished by the time this runs, and swallows everything: a judge that cannot run is
    never a reason for the candidate's `end` call to fail.
    """
    try:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, session_id)
            if session is None:
                return
            evaluation = await evaluate(db, session)
            log.info(
                "Scored session %s: %s (%s)",
                session_id,
                evaluation.verdict or "no verdict",
                evaluation.status,
            )
    except NotScoreable as exc:
        log.info("Session %s not scored: %s", session_id, exc)
    except Exception:
        log.exception("Background scoring failed for session %s", session_id)
