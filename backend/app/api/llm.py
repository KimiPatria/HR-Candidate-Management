"""The custom LLM endpoint RTC calls once per candidate turn (VOICE_MODE=rtc only).

This is a thin adapter, not the interview itself. It translates between BytePlus RTC's
CustomLLM contract - OpenAI-shaped /v1/chat/completions with SSE streaming - and
`services/interview_turn.py`, which holds the actual interview logic and is shared with
the local voice pipeline. Anything about how the interview *thinks* belongs there; only
wire-format concerns belong here.

Streaming is not optional on this path: RTC feeds each chunk to TTS as it arrives, so
time-to-first-token is what the candidate hears as responsiveness. Buffering the full
completion first would add seconds of silence to every turn.

VERIFY: confirm the exact request shape RTC sends (especially how it identifies the
session) against the BytePlus RTC conversational-AI docs. `_resolve_session_id` accepts
several plausible carriers so this keeps working whichever one it turns out to be.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.services import interview_turn, turns

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
    """Find the session id, trying every carrier RTC might use, cheapest first.

    Three of these are populated deliberately by `byteplus_rtc._voice_chat_config`, and
    any one of them is sufficient. That redundancy is the point: BytePlus documents the
    query parameter and `ExtraHeader` as the sanctioned ways to attach per-task business
    data to a CustomLLM request, but its documented request body lists only `user` and
    `assistant` roles - so a `system` message carrying SESSION_ID may or may not be
    forwarded. Depending on that one undocumented carrier alone would 400 every single
    turn, and the candidate would just hear silence.
    """
    # Documented: "append it directly to LLMConfig.Url as a query parameter"
    # e.g. https://api.my-custom-agent.com/v1/chat-stream?session_id=12345
    for param in ("session_id", "sessionId"):
        if value := request.query_params.get(param):
            return value
    # Documented: LLMConfig.ExtraHeader, a JSON map sent as extra HTTP headers.
    for header in ("x-session-id", "x-interview-session", "session-id"):
        if value := request.headers.get(header):
            return value
    for key in ("session_id", "user", "conversation_id"):
        if value := body.get(key):
            return str(value)
    # Last resort: a system message carrying the id, for RTC builds that forward
    # LLMConfig.SystemMessages verbatim into the messages array.
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
            "No session id on the request. Expected it in the Url query string, in the "
            "X-Session-Id header (LLMConfig.ExtraHeader), or in a SESSION_ID: system "
            "message (LLMConfig.SystemMessages).",
        )

    context = await interview_turn.load_context(session_id)
    if context is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown session {session_id}")
    session, interview = context

    candidate_text = _last_user_message(body).strip()
    stream = bool(body.get("stream", True))

    composed = await interview_turn.compose(db, session, interview, candidate_text)

    if candidate_text:
        await turns.record_turn(db, session.id, "candidate", candidate_text)

    if stream:
        return StreamingResponse(
            _stream_response(session.id, candidate_text, composed),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    resolved = "".join([chunk async for chunk in composed.text])
    await turns.record_turn(
        db,
        session.id,
        "ai",
        resolved,
        plan_item_id=composed.plan_item_id,
        guardrail_action=composed.guardrail_action,
    )
    background.add_task(interview_turn.audit, session.id, candidate_text, resolved)
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


async def _stream_response(
    session_id: str,
    candidate_text: str,
    composed: interview_turn.ComposedTurn,
) -> AsyncIterator[str]:
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    collected: list[str] = []

    yield _sse_chunk(completion_id, created, {"role": "assistant"}, None)
    try:
        async for piece in composed.text:
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
                plan_item_id=composed.plan_item_id,
                guardrail_action=composed.guardrail_action,
            )
        # Fire-and-forget: BackgroundTasks on a StreamingResponse may already be
        # finalised by the time the generator gets here.
        asyncio.create_task(  # noqa: RUF006
            interview_turn.audit(session_id, candidate_text, reply)
        )
