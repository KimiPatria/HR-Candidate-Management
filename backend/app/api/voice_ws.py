"""Candidate audio WebSocket (VOICE_MODE=local).

Replaces the BytePlus RTC room entirely on the local path. The browser opens one socket
and uses it in both directions:

    up    binary   16 kHz mono s16le PCM from the microphone
    up    text     JSON control messages (end / speech)
    down  binary   PCM from Seed TTS, at settings.seed_tts_sample_rate
    down  text     JSON control messages (ready / speaking / flush_audio / error)

Transcript turns and interim captions do NOT travel here - they keep using the existing
`/api/sessions/{id}/ws` channel, so the transcript panel is fed identically whichever
voice pipeline is running.

Auth is the join token, matching every other candidate-facing route: whoever holds the
interview link can speak into that interview and nothing else.
"""

import asyncio
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Interview, InterviewSession, TranscriptTurn
from app.services.voice.asr import SAMPLE_RATE as ASR_SAMPLE_RATE
from app.services.voice.pipeline import LocalVoicePipeline

log = logging.getLogger(__name__)
router = APIRouter(tags=["voice"])

# Close codes the browser distinguishes, so a candidate sees the right message.
_CLOSE_UNAUTHORISED = 4401
_CLOSE_WRONG_MODE = 4409
_CLOSE_FAILED = 4500


@router.websocket("/join/{token}/voice")
async def voice_socket(websocket: WebSocket, token: str) -> None:
    if not settings.local_voice:
        # Nothing here is wired up in RTC mode; failing loudly beats a socket that
        # accepts audio and silently drops it.
        await websocket.close(code=_CLOSE_WRONG_MODE)
        return

    async with SessionLocal() as db:
        stmt = select(InterviewSession).where(InterviewSession.join_token == token)
        session = (await db.execute(stmt)).scalar_one_or_none()
        if session is None or session.status in ("completed", "expired"):
            await websocket.close(code=_CLOSE_UNAUTHORISED)
            return
        interview = await db.get(Interview, session.interview_id)
        if interview is None:
            await websocket.close(code=_CLOSE_UNAUTHORISED)
            return
        greeting = await _greeting_text(db, session.id)

    await websocket.accept()
    await websocket.send_json(
        {
            "type": "config",
            # The browser downsamples to this before sending; Seed ASR takes 16 kHz only.
            "input_sample_rate": ASR_SAMPLE_RATE,
            "output_sample_rate": settings.seed_tts_sample_rate,
        }
    )

    async def send_audio(chunk: bytes) -> None:
        await websocket.send_bytes(chunk)

    async def send_event(event: dict) -> None:
        await websocket.send_json(event)

    pipeline = LocalVoicePipeline(
        session, interview, send_audio=send_audio, send_event=send_event
    )

    try:
        await pipeline.start(greeting)
    except Exception as exc:  # noqa: BLE001
        log.exception("Could not start the local voice pipeline for %s", session.id)
        await websocket.send_json({"type": "error", "message": str(exc)[:300]})
        await websocket.close(code=_CLOSE_FAILED)
        return

    # The ASR result loop and the browser receive loop both run for the life of the
    # socket; whichever finishes first ends the interview leg.
    consumer = asyncio.create_task(pipeline.run())
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            if (payload := message.get("bytes")) is not None:
                await pipeline.push_audio(payload)
            elif (text := message.get("text")) is not None and await _handle_control(
                pipeline, text
            ):
                break
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("Voice socket failed for session %s", session.id)
    finally:
        consumer.cancel()
        await pipeline.stop()


async def _handle_control(pipeline: LocalVoicePipeline, text: str) -> bool:
    """Act on one JSON control message from the browser. True means hang up.

    `speech` is the browser's capture gate opening and closing. It matters because the
    recogniser used for languages outside the streaming model's set reports nothing until
    it is told the utterance is over - so on those interviews this message is what makes
    the candidate audible at all, and a malformed one costs them their turn. Hence
    parsing it properly rather than sniffing the frame for a substring.
    """
    try:
        message = json.loads(text)
    except json.JSONDecodeError:
        log.warning("Ignoring an unparseable control frame: %s", text[:120])
        return False
    if not isinstance(message, dict):
        return False

    kind = message.get("type")
    if kind == "end":
        return True
    if kind == "speech":
        await pipeline.set_speech_gate(bool(message.get("value")))
        return False
    log.debug("Ignoring an unknown control frame: %s", kind)
    return False


async def _greeting_text(db, session_id: str) -> str:
    """The opening line, generated and stored when the session was started.

    Reading it back rather than regenerating keeps the transcript honest: the candidate
    hears exactly the turn that is already recorded against the session.
    """
    stmt = (
        select(TranscriptTurn)
        .where(TranscriptTurn.session_id == session_id, TranscriptTurn.speaker == "ai")
        .order_by(TranscriptTurn.turn_index.asc())
        .limit(1)
    )
    turn = (await db.execute(stmt)).scalar_one_or_none()
    return turn.text if turn is not None else ""
