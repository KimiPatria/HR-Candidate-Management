"""Candidate triage: the list HR scans, and the detail page they open from it.

The list carries each candidate's verdict so a shortlist is readable without opening
anyone. The detail route is where the interview actually gets read - the tier, the
plain-language summary, the per-dimension evidence behind it, and the transcript those
citations point into, all on one page.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.security import HRUser
from app.models import Evaluation, Interview, InterviewSession
from app.schemas import CandidateDetail, CandidateOut, EvaluationOut
from app.services import evaluator
from app.services import rubric as rubric_service

log = logging.getLogger(__name__)
router = APIRouter(prefix="/candidates", tags=["candidates"])

# Statuses whose transcript is final. Scoring a live interview would judge half an answer.
SCOREABLE_STATUSES = {"completed", "expired", "failed"}


@router.get("", response_model=list[CandidateOut])
async def list_candidates(
    db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> list[CandidateOut]:
    rows = await db.execute(
        select(InterviewSession, Interview, Evaluation)
        .join(Interview, Interview.id == InterviewSession.interview_id)
        # Outer join: most sessions have no evaluation, and the ones that do not are
        # exactly the ones HR is waiting on.
        .outerjoin(Evaluation, Evaluation.session_id == InterviewSession.id)
        .order_by(InterviewSession.created_at.desc())
    )
    return [
        CandidateOut(
            id=session.id,
            name=session.candidate_name,
            email=session.candidate_email,
            position_title=interview.position_title,
            session_status=session.status,
            interviewed_at=session.started_at,
            verdict=evaluation.verdict if evaluation else None,
            evaluation_status=evaluation.status if evaluation else None,
            interview_completeness=(
                evaluation.interview_completeness if evaluation else None
            ),
        )
        for session, interview, evaluation in rows.all()
    ]


@router.get("/{session_id}", response_model=CandidateDetail)
async def get_candidate(
    session_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    return await _detail(db, await _require_session(db, session_id))


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_candidate(
    session_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> None:
    """Remove a candidate session and everything hanging off it - transcript, plan
    progress, evaluation - via the same cascade the interview-delete route relies on."""
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate session not found")
    await db.delete(session)
    await db.commit()


@router.post("/{session_id}/score", response_model=CandidateDetail)
async def score_candidate(
    session_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    """Run or re-run the judge on demand.

    Scoring normally happens on its own when an interview ends; this covers the cases
    that path cannot - the rubric was approved after the fact, the rubric changed, or the
    first pass failed. It is one model call, and it replaces the previous verdict.
    """
    session = await _require_session(db, session_id)
    if session.status not in SCOREABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "This interview has not finished yet - the transcript is still being written.",
        )
    try:
        await evaluator.evaluate(db, session)
    except evaluator.NotScoreable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return await _detail(db, session)


async def _require_session(db: AsyncSession, session_id: str) -> InterviewSession:
    stmt = (
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .options(selectinload(InterviewSession.turns))
    )
    session = (await db.execute(stmt)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Candidate session not found")
    return session


async def _detail(db: AsyncSession, session: InterviewSession) -> dict:
    interview = await db.get(Interview, session.interview_id)
    rubric = await rubric_service.load(db, session.interview_id)
    evaluation = await evaluator.load(db, session.id)
    await db.refresh(session, ["turns"])

    approved = rubric is not None and rubric.status == "approved"
    blockers: list[str] = []
    if session.status not in SCOREABLE_STATUSES:
        blockers.append("The interview has not finished yet")
    if not approved:
        blockers.append("The interview has no approved scoring rubric")

    return {
        "id": session.id,
        "name": session.candidate_name,
        "email": session.candidate_email,
        "interview_id": session.interview_id,
        "interview_title": interview.title if interview else "",
        "position_title": interview.position_title if interview else "",
        "language": interview.language if interview else "en",
        "session_status": session.status,
        "created_at": session.created_at,
        "interviewed_at": session.started_at,
        "ended_at": session.ended_at,
        "failure_reason": session.failure_reason,
        "turns": session.turns,
        "evaluation": (
            EvaluationOut.model_validate(evaluator.serialize(evaluation))
            if evaluation
            else None
        ),
        "rubric": rubric_service.serialize(rubric),
        "scoring": {
            "can_score": not blockers,
            "blockers": blockers,
            "rubric_approved": approved,
            "rubric_current_version": rubric.version if rubric else None,
            "scored_against_version": evaluation.rubric_version if evaluation else None,
            # Only meaningful once both numbers exist: a verdict produced against wording
            # that has since been edited is a reading of a rubric no longer on screen.
            "rubric_stale": bool(
                evaluation
                and rubric
                and evaluation.rubric_version is not None
                and evaluation.rubric_version != rubric.version
            ),
        },
    }
