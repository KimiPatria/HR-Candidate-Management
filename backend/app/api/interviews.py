from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.security import HRUser
from app.models import Interview
from app.schemas import (
    InterviewCreate,
    InterviewDetail,
    InterviewOut,
    InterviewUpdate,
    PlanItemOut,
)
from app.services import planner, tts_voice

router = APIRouter(prefix="/interviews", tags=["interviews"])


async def _load(db: AsyncSession, interview_id: str) -> Interview:
    stmt = (
        select(Interview)
        .where(Interview.id == interview_id)
        .options(selectinload(Interview.documents), selectinload(Interview.plan_items))
    )
    interview = (await db.execute(stmt)).scalar_one_or_none()
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return interview


@router.post("", response_model=InterviewOut, status_code=status.HTTP_201_CREATED)
async def create_interview(
    payload: InterviewCreate, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> Interview:
    interview = Interview(**payload.model_dump())
    tts_voice.assign_defaults(interview)
    db.add(interview)
    await db.commit()
    await db.refresh(interview)
    return interview


@router.get("", response_model=list[InterviewOut])
async def list_interviews(
    db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> list[Interview]:
    stmt = select(Interview).order_by(Interview.created_at.desc())
    return list((await db.execute(stmt)).scalars().all())


@router.get("/{interview_id}", response_model=InterviewDetail)
async def get_interview(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    interview = await _load(db, interview_id)
    detail = InterviewDetail.model_validate(interview).model_dump()
    detail["readiness"] = _readiness(interview)
    return detail


@router.patch("/{interview_id}", response_model=InterviewOut)
async def update_interview(
    interview_id: str,
    payload: InterviewUpdate,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> Interview:
    interview = await _load(db, interview_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(interview, field, value)
    await db.commit()
    await db.refresh(interview)
    return interview


@router.delete("/{interview_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_interview(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> None:
    interview = await _load(db, interview_id)
    await db.delete(interview)
    await db.commit()


@router.post("/{interview_id}/plan", response_model=list[PlanItemOut])
async def generate_plan(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> list:
    """Generate the question agenda from the job-requirement documents. Replaces any
    existing plan, so HR can regenerate after editing documents."""
    interview = await _load(db, interview_id)
    unindexed = [d for d in interview.documents if d.index_status not in ("indexed", "failed")]
    if unindexed:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"{len(unindexed)} document(s) still indexing. Try again in a moment.",
        )
    try:
        return await planner.generate_plan(db, interview)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


def _readiness(interview: Interview) -> dict:
    assets = tts_voice.readiness(interview)
    has_requirements = any(
        d.doc_type == "job_requirement" and d.index_status == "indexed"
        for d in interview.documents
    )
    blockers = list(assets["missing"])
    if not has_requirements:
        blockers.append("At least one indexed job-requirement document")
    if not interview.plan_items:
        blockers.append("A generated interview plan")
    return {
        **assets,
        "has_requirements": has_requirements,
        "plan_item_count": len(interview.plan_items),
        "can_create_sessions": not blockers,
        "blockers": blockers,
    }
