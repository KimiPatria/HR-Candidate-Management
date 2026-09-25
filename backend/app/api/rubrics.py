"""Rubric authoring routes.

All five hang off an interview because a rubric belongs to a position, not to a
candidate: every candidate for the same job must be measured against the same wording,
or the tiers mean nothing across a shortlist.

    GET    /interviews/{id}/rubric          the rubric, or null if none exists yet
    POST   /interviews/{id}/rubric/blank    start the manual path from empty dimensions
    POST   /interviews/{id}/rubric/draft    start the automated path from the job spec
    PUT    /interviews/{id}/rubric          save HR's edits (sends approved back to draft)
    POST   /interviews/{id}/rubric/approve  the explicit sign-off that permits scoring
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import HRUser
from app.models import Interview
from app.schemas import RubricOut, RubricSave
from app.services import rubric as rubric_service

router = APIRouter(prefix="/interviews/{interview_id}/rubric", tags=["rubric"])


async def _require_interview(db: AsyncSession, interview_id: str) -> Interview:
    interview = await db.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return interview


@router.get("", response_model=RubricOut | None)
async def get_rubric(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict | None:
    await _require_interview(db, interview_id)
    return rubric_service.serialize(await rubric_service.load(db, interview_id))


@router.post("/blank", response_model=RubricOut)
async def create_blank(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    """The manual authoring path: three fixed dimensions, empty bands, ready to type in."""
    await _require_interview(db, interview_id)
    return rubric_service.serialize(await rubric_service.ensure(db, interview_id))  # type: ignore[return-value]


@router.post("/draft", response_model=RubricOut)
async def draft_rubric(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    """The automated path. Returns a draft, never an approved rubric - HR still signs off."""
    interview = await _require_interview(db, interview_id)
    try:
        return rubric_service.serialize(await rubric_service.draft(db, interview))  # type: ignore[return-value]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc


@router.put("", response_model=RubricOut)
async def save_rubric(
    interview_id: str,
    payload: RubricSave,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> dict:
    await _require_interview(db, interview_id)
    rubric = await rubric_service.ensure(db, interview_id)
    bands = {
        d.key: {"strong": d.strong, "decent": d.decent, "not_fit": d.not_fit}
        for d in payload.dimensions
    }
    return rubric_service.serialize(await rubric_service.save_bands(db, rubric, bands))  # type: ignore[return-value]


@router.post("/approve", response_model=RubricOut)
async def approve_rubric(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> dict:
    await _require_interview(db, interview_id)
    rubric = await rubric_service.load(db, interview_id)
    if rubric is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, "There is no rubric on this interview to approve"
        )
    try:
        return rubric_service.serialize(await rubric_service.approve(db, rubric))  # type: ignore[return-value]
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
