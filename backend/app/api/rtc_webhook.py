"""RTC event webhook.

Transcript turns are NOT persisted from here. The LLM endpoint already sees the final
ASR text for every candidate turn and writes it once, so persisting subtitles too would
double every line. What this adds is the live layer: interim subtitles the transcript
panel can show while someone is still speaking, plus room lifecycle so a candidate who
closes the tab does not leave a session stuck in progress.

VERIFY: event names and payload shape against the RTC callback docs. `_event_name` and
`_payload` read defensively so an unexpected envelope logs rather than 500s.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request

from app.core.db import SessionLocal
from app.models import InterviewSession
from app.services import byteplus_rtc, evaluator, transcript_hub

log = logging.getLogger(__name__)
router = APIRouter(prefix="/rtc", tags=["rtc"])

SUBTITLE_EVENTS = {"Subtitle", "subtitle", "ASRSubtitle", "TranscriptUpdate"}
LEAVE_EVENTS = {"UserLeaveRoom", "user_leave_room", "VisibleUserLeave"}
JOIN_EVENTS = {"UserJoinRoom", "user_join_room", "VisibleUserJoin"}
ERROR_EVENTS = {"TaskFailed", "AgentError", "task_failed"}


def _event_name(body: dict) -> str:
    for key in ("EventType", "event_type", "eventType", "Event", "type"):
        if value := body.get(key):
            return str(value)
    return "unknown"


def _payload(body: dict) -> dict:
    for key in ("EventData", "event_data", "Data", "data", "payload"):
        value = body.get(key)
        if isinstance(value, dict):
            return value
    return body


def _session_id(body: dict, data: dict) -> str | None:
    for source in (data, body):
        for key in ("TaskId", "task_id", "SessionId", "session_id"):
            if value := source.get(key):
                return str(value)
    return None


@router.post("/callback")
async def rtc_callback(request: Request) -> dict:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        log.warning("RTC callback with non-JSON body")
        return {"ok": True}

    event = _event_name(body)
    data = _payload(body)
    session_id = _session_id(body, data)

    if not session_id:
        log.info("RTC callback %s without a resolvable session id", event)
        return {"ok": True}

    if event in SUBTITLE_EVENTS:
        _handle_subtitle(session_id, data)
    elif event in JOIN_EVENTS:
        await _set_status(session_id, "joined", only_if={"created"})
    elif event in LEAVE_EVENTS:
        await _handle_leave(session_id)
    elif event in ERROR_EVENTS:
        await _handle_error(session_id, data)
    else:
        log.debug("Unhandled RTC event %s for session %s", event, session_id)

    return {"ok": True}


def _handle_subtitle(session_id: str, data: dict) -> None:
    """Interim captions only. Published live, never written to the transcript."""
    text = data.get("Text") or data.get("text") or ""
    if not text:
        return
    is_final = bool(data.get("Definite", data.get("definite", False)))
    speaker = "ai" if data.get("UserId", "").startswith("agent-") else "candidate"
    transcript_hub.publish(
        session_id,
        {"type": "partial", "speaker": speaker, "text": text, "final": is_final},
    )


async def _set_status(session_id: str, status_value: str, *, only_if: set[str]) -> None:
    async with SessionLocal() as db:
        session = await db.get(InterviewSession, session_id)
        if session is None or session.status not in only_if:
            return
        session.status = status_value
        await db.commit()
    transcript_hub.publish(session_id, {"type": "status", "status": status_value})


async def _handle_leave(session_id: str) -> None:
    """The candidate left. End the session rather than leaving the agent running and
    billing against an empty room."""
    async with SessionLocal() as db:
        session = await db.get(InterviewSession, session_id)
        if session is None or session.status == "completed":
            return
        await byteplus_rtc.stop_voice_chat(session)
        session.status = "completed"
        session.ended_at = datetime.now(timezone.utc)
        await db.commit()
    transcript_hub.publish(session_id, {"type": "status", "status": "completed"})
    # Closing the tab is the most common way an interview actually ends, so this path
    # matters as much as the explicit one in api/sessions.py.
    evaluator.schedule(session_id)


async def _handle_error(session_id: str, data: dict) -> None:
    reason = str(data.get("Message") or data.get("message") or data)[:1000]
    log.error("RTC reported a failure on session %s: %s", session_id, reason)
    async with SessionLocal() as db:
        session = await db.get(InterviewSession, session_id)
        if session is None or session.status == "completed":
            return
        session.status = "failed"
        session.failure_reason = reason
        session.ended_at = datetime.now(timezone.utc)
        await db.commit()
    transcript_hub.publish(
        session_id, {"type": "status", "status": "failed", "reason": reason}
    )
    # Still worth scoring. A session that died mid-way usually has a partial transcript,
    # and the judge's transcript-quality gate is exactly what decides whether that partial
    # is enough to read - which beats leaving HR with a bare "failed" and no idea whether
    # the candidate got a fair hearing.
    evaluator.schedule(session_id)
