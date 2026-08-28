"""Candidate management - stub for now.

Returns real sessions rather than an empty list so the page has something to render as
soon as an interview has been run. Scoring and rubric fields are deliberately absent:
evaluation is out of MVP scope.
"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import HRUser
from app.models import Interview, InterviewSession
from app.schemas import CandidateOut

router = APIRouter(prefix="/candidates", tags=["candidates"])


@router.get("", response_model=list[CandidateOut])
async def list_candidates(
    db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> list[CandidateOut]:
    rows = await db.execute(
        select(InterviewSession, Interview)
        .join(Interview, Interview.id == InterviewSession.interview_id)
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
        )
        for session, interview in rows.all()
    ]
