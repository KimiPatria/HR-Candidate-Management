from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import get_db
from app.core.security import HRUser
from app.models import Interview, Rubric
from app.schemas import (
    InterviewCreate,
    InterviewDetail,
    InterviewOut,
    InterviewUpdate,
    PlanItemOut,
)
from app.services import autopilot, planner, tts_voice
from app.services import rubric as rubric_service

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
) -> list[dict]:
    # The `status` column only tracks whether a plan was last generated - it does not
    # reflect blockers added later (a document un-indexed, TTS voice unset), so the list
    # can't just echo it. Eager-load what `_blockers` needs and compute the same
    # can_create_sessions the detail page shows, so the list never says "ready" when the
    # detail view says otherwise.
    stmt = (
        select(Interview)
        .options(selectinload(Interview.documents), selectinload(Interview.plan_items))
        .order_by(Interview.created_at.desc())
    )
    interviews = list((await db.execute(stmt)).scalars().all())
    return [
        {
            **InterviewOut.model_validate(interview).model_dump(),
            "can_create_sessions": not _blockers(interview)[2],
        }
        for interview in interviews
    ]


@router.get("/{interview_id}", response_model=InterviewDetail)
async def get_interview(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    interview = await _load(db, interview_id)
    detail = InterviewDetail.model_validate(interview).model_dump()
    readiness = _readiness(interview, await rubric_service.load(db, interview_id))
    detail["readiness"] = readiness
    detail["can_create_sessions"] = readiness["can_create_sessions"]
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


@router.post("/{interview_id}/setup", response_model=InterviewOut)
async def rerun_setup(
    interview_id: str,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> Interview:
    """Re-run what indexing a job description runs on its own: the question plan, then a
    rubric draft. Here for the case where one of the two failed, or where HR edited the
    documents and wants both rebuilt without pressing two buttons in two places.

    Returns immediately with autopilot_status "running" - the setup page polls. An
    approved rubric is still left alone; see services/autopilot.py.
    """
    interview = await _load(db, interview_id)
    if not any(
        d.doc_type == "job_requirement" and d.index_status == "indexed"
        for d in interview.documents
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Add and index a job-requirement document first - both the plan and the "
            "rubric are generated from it",
        )
    interview.autopilot_status = "running"
    interview.autopilot_step = "plan"
    interview.autopilot_error = None
    await db.commit()
    await db.refresh(interview)
    background.add_task(autopilot.run, interview_id, claimed=True)
    return interview


def _blockers(interview: Interview) -> tuple[dict, bool, list[str]]:
    """What's still needed before HR can create sessions, shared by the list (cheap:
    no rubric lookup) and detail (full `_readiness`) views so they never disagree."""
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
    return assets, has_requirements, blockers


def _readiness(interview: Interview, rubric: Rubric | None) -> dict:
    assets, has_requirements, blockers = _blockers(interview)

    # Deliberately NOT a blocker. An interview with no approved rubric still runs and
    # still produces a transcript; it just does not produce a verdict until HR authors
    # one, and a candidate can be scored retroactively at any point afterwards. Making
    # this a blocker would strand every interview that predates the scoring feature.
    rubric_status = rubric.status if rubric else "none"
    return {
        **assets,
        "has_requirements": has_requirements,
        "plan_item_count": len(interview.plan_items),
        "can_create_sessions": not blockers,
        "blockers": blockers,
        "rubric_status": rubric_status,
        "rubric_approved": rubric_status == "approved",
    }
