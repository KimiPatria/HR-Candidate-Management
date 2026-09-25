"""What happens on its own once a job description is indexed.

Both the question plan and the scoring rubric are derived from exactly one input: the
job-requirement documents. Asking HR to upload the job description and then press
"Generate plan" and then press "Draft with AI" is asking them to press two buttons whose
answer was already determined the moment the upload finished. So indexing a
job-requirement document runs both.

The one thing that stays manual is rubric APPROVAL, and deliberately so. A draft rubric
is model output; scoring real people against un-reviewed model output would launder a
guess into a hiring decision. Autopilot therefore never approves, and never touches a
rubric that has already been approved - regenerating band definitions underneath an
approved rubric would silently change what every future candidate is measured against.

Failures are recorded on the interview and surfaced on the setup page rather than
raised: this runs detached from any request, so there is nobody left to return an error
to, and the manual buttons remain as the fallback path.
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.db import SessionLocal
from app.models import Interview
from app.services import planner
from app.services import rubric as rubric_service

log = logging.getLogger(__name__)


async def run(interview_id: str, *, claimed: bool = False) -> None:
    """Generate the plan, then draft the rubric.

    Safe to call concurrently: a second call arriving while one is already running is
    dropped rather than queued, because both stages rebuild from the same documents and
    running them twice produces the same answer at twice the cost.

    `claimed` is for the manual re-run route, which flips the status to running itself so
    the response already reads "running" to the page that is about to poll. It also
    doubles as the way out of a status left stuck at "running" by a process that died
    mid-run: the button works regardless of what the last run left behind.
    """
    async with SessionLocal() as db:
        interview = await _load(db, interview_id)
        if interview is None:
            return
        if not claimed and interview.autopilot_status == "running":
            return
        await _mark(db, interview, "running", step="plan")

        try:
            await planner.generate_plan(db, interview)
        except Exception as exc:  # noqa: BLE001 - detached task; the page reports it
            log.exception("Autopilot: plan generation failed for interview %s", interview_id)
            await _mark(db, interview, "failed", error=f"Question plan: {exc}")
            return

        await _mark(db, interview, "running", step="rubric")
        try:
            await _draft_rubric(db, interview)
        except Exception as exc:  # noqa: BLE001
            log.exception("Autopilot: rubric draft failed for interview %s", interview_id)
            # The plan did land, so this is a partial success. Saying which half failed
            # is the difference between "press one button" and "start over".
            await _mark(db, interview, "failed", error=f"Scoring rubric: {exc}")
            return

        await _mark(db, interview, "done")


async def _draft_rubric(db: AsyncSession, interview: Interview) -> None:
    existing = await rubric_service.load(db, interview.id)
    if existing is not None and existing.status == "approved":
        log.info(
            "Autopilot: interview %s already has an approved rubric, leaving it alone",
            interview.id,
        )
        return
    await rubric_service.draft(db, interview)


async def _load(db: AsyncSession, interview_id: str) -> Interview | None:
    stmt = (
        select(Interview)
        .where(Interview.id == interview_id)
        .options(selectinload(Interview.documents), selectinload(Interview.plan_items))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def _mark(
    db: AsyncSession,
    interview: Interview,
    status: str,
    *,
    step: str | None = None,
    error: str | None = None,
) -> None:
    interview.autopilot_status = status
    interview.autopilot_step = step
    interview.autopilot_error = error[:1000] if error else None
    await db.commit()
