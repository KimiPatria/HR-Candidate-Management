"""Session lifecycle: HR creates a session, the candidate joins it with a token."""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status
from itsdangerous import BadSignature
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.core.security import (
    SESSION_COOKIE,
    SESSION_MAX_AGE,
    HRUser,
    hr_cookie_serializer,
    new_join_token,
)
from app.models import Interview, InterviewSession, PlanItem
from app.schemas import (
    JoinInfo,
    RTCCredentials,
    SessionCreate,
    SessionCreated,
    SessionOut,
    TranscriptOut,
)
from app.services import (
    byteplus_rtc,
    evaluator,
    interview_turn,
    planner,
    transcript_hub,
    turns,
)

log = logging.getLogger(__name__)
router = APIRouter(tags=["sessions"])

ACTIVE_STATUSES = {"created", "joined", "in_progress"}


async def _session_by_token(db: AsyncSession, token: str) -> InterviewSession:
    stmt = select(InterviewSession).where(InterviewSession.join_token == token)
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Invalid interview link")
    if session.token_expires_at and session.token_expires_at < datetime.now(timezone.utc):
        if session.status in ACTIVE_STATUSES:
            session.status = "expired"
            await db.commit()
        raise HTTPException(status.HTTP_410_GONE, "This interview link has expired")
    return session


async def _require_session(db: AsyncSession, session_id: str) -> InterviewSession:
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


# ---------------------------------------------------------------------- HR routes


@router.post(
    "/interviews/{interview_id}/sessions",
    response_model=SessionCreated,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    interview_id: str,
    payload: SessionCreate,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> dict:
    interview = await db.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")

    plan_items = (
        await db.execute(select(PlanItem).where(PlanItem.interview_id == interview_id))
    ).scalars().all()
    if not plan_items:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Generate the interview plan before creating candidate sessions",
        )

    token = new_join_token()
    session = InterviewSession(
        interview_id=interview_id,
        candidate_name=payload.candidate_name,
        candidate_email=payload.candidate_email,
        join_token=token,
        token_expires_at=datetime.now(timezone.utc) + timedelta(hours=payload.expires_in_hours),
        rtc_room_id=f"interview-{uuid.uuid4().hex[:16]}",
        rtc_user_id=f"candidate-{uuid.uuid4().hex[:12]}",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)

    return {
        **SessionOut.model_validate(session).model_dump(),
        "join_url": f"{settings.candidate_app_url}/join/{token}",
        "join_token": token,
        "token_expires_at": session.token_expires_at,
    }


@router.get("/interviews/{interview_id}/sessions", response_model=list[SessionOut])
async def list_sessions(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> list[InterviewSession]:
    stmt = (
        select(InterviewSession)
        .where(InterviewSession.interview_id == interview_id)
        .order_by(InterviewSession.created_at.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


@router.get("/sessions/{session_id}", response_model=SessionOut)
async def get_session(
    session_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> InterviewSession:
    return await _require_session(db, session_id)


@router.get("/sessions/{session_id}/transcript", response_model=TranscriptOut)
async def get_transcript(
    session_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    session = await _require_session(db, session_id)
    await db.refresh(session, ["turns"])
    return {"session_id": session.id, "status": session.status, "turns": session.turns}


@router.post("/sessions/{session_id}/end", response_model=SessionOut)
async def end_session_hr(
    session_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> InterviewSession:
    session = await _require_session(db, session_id)
    return await _end(db, session)


# --------------------------------------------------------------- candidate routes


@router.get("/join/{token}", response_model=JoinInfo)
async def join_info(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    session = await _session_by_token(db, token)
    interview = await db.get(Interview, session.interview_id)
    return {
        "session_id": session.id,
        "candidate_name": session.candidate_name,
        "position_title": interview.position_title if interview else "",
        "interview_title": interview.title if interview else "",
        "language": interview.language if interview else "en",
        "status": session.status,
        "consent_required": session.consent_accepted_at is None,
    }


@router.post("/join/{token}/consent")
async def accept_consent(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    session = await _session_by_token(db, token)
    if session.consent_accepted_at is None:
        session.consent_accepted_at = datetime.now(timezone.utc)
        await db.commit()
    return {"ok": True, "accepted_at": session.consent_accepted_at}


@router.post("/join/{token}/start", response_model=RTCCredentials)
async def start_session(token: str, db: AsyncSession = Depends(get_db)) -> dict:
    session = await _session_by_token(db, token)
    if session.consent_accepted_at is None:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "Recording consent must be accepted before joining"
        )
    if session.status in ("completed", "expired"):
        raise HTTPException(status.HTTP_409_CONFLICT, "This interview has already ended")

    interview = await db.get(Interview, session.interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")

    await planner.ensure_progress(db, session)

    if session.status in ("created", "joined"):
        session.status = "in_progress"
        session.started_at = datetime.now(timezone.utc)
        await db.commit()

        # The greeting is generated and recorded in both modes. RTC mode hands it to
        # StartVoiceChat as the WelcomeMessage; local mode reads it back off the
        # transcript when the candidate's voice socket connects and speaks it through
        # our own TTS. Either way the transcript shows exactly what was said.
        greeting = await interview_turn.opening_greeting(db, session, interview)
        await turns.record_turn(db, session.id, "ai", greeting)

        if not settings.local_voice:
            try:
                session.rtc_task_id = await byteplus_rtc.start_voice_chat(
                    session, interview, welcome_message=greeting
                )
            except Exception as exc:  # noqa: BLE001
                log.exception("StartVoiceChat failed for session %s", session.id)
                session.status = "failed"
                session.failure_reason = str(exc)[:1000]
                await db.commit()
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY, "Could not start the interview agent"
                ) from exc
        await db.commit()

    if settings.local_voice:
        # No room, no token, no agent task: the browser talks to us directly.
        return {"voice_mode": "local", "avatar_id": None}

    return {
        "voice_mode": "rtc",
        "app_id": settings.rtc_app_id or "mock-app-id",
        "room_id": session.rtc_room_id,
        "user_id": session.rtc_user_id,
        "token": byteplus_rtc.generate_token(session.rtc_room_id, session.rtc_user_id),
        "task_id": session.rtc_task_id,
        "avatar_id": interview.avatar_id or settings.avatar_id or None,
    }


@router.post("/join/{token}/end", response_model=SessionOut)
async def end_session_candidate(token: str, db: AsyncSession = Depends(get_db)) -> InterviewSession:
    session = await _session_by_token(db, token)
    return await _end(db, session)


async def _end(db: AsyncSession, session: InterviewSession) -> InterviewSession:
    if session.status == "completed":
        return session
    await byteplus_rtc.stop_voice_chat(session)
    session.status = "completed"
    session.ended_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(session)
    transcript_hub.publish(session.id, {"type": "status", "status": "completed"})
    # The transcript is final as of this commit, so the judge can read it. Off the
    # response path: nobody ending an interview should wait on a scoring model, and a
    # missing rubric must never turn the candidate's "end interview" click into an error.
    evaluator.schedule(session.id)
    return session


# ------------------------------------------------------------------- live updates


def _turn_event(turn) -> dict:
    return {
        "type": "turn",
        "id": turn.id,
        "turn_index": turn.turn_index,
        "speaker": turn.speaker,
        "text": turn.text,
        "guardrail_action": turn.guardrail_action,
        "timestamp": turn.timestamp.isoformat(),
    }


@router.websocket("/sessions/{session_id}/ws")
async def transcript_stream(websocket: WebSocket, session_id: str) -> None:
    """Live transcript for both the HR view and the candidate page.

    Auth is either the HR cookie or the session join token as a query parameter, so a
    candidate can watch their own transcript without an HR login but cannot read anyone
    else session.
    """
    token = websocket.query_params.get("token")
    authorised = False

    cookie = websocket.cookies.get(SESSION_COOKIE)
    if cookie:
        try:
            hr_cookie_serializer().loads(cookie, max_age=SESSION_MAX_AGE)
            authorised = True
        except BadSignature:
            authorised = False

    if not authorised and token:
        async with SessionLocal() as db:
            session = await db.get(InterviewSession, session_id)
            authorised = session is not None and session.join_token == token

    if not authorised:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    queue = transcript_hub.subscribe(session_id)
    try:
        # Replay what already happened so a reconnecting client is not missing turns.
        async with SessionLocal() as db:
            existing = await db.get(InterviewSession, session_id)
            if existing:
                await db.refresh(existing, ["turns"])
                for turn in existing.turns:
                    await websocket.send_json(_turn_event(turn))

        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
            except asyncio.TimeoutError:
                # Keeps intermediaries from dropping an idle interview connection.
                await websocket.send_json({"type": "ping"})
                continue
            await websocket.send_json(event)
    except WebSocketDisconnect:
        pass
    finally:
        transcript_hub.unsubscribe(session_id, queue)
