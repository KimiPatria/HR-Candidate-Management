"""Rubric authoring: the LLM draft path and the blank manual path.

Both paths land on the same row and the same editor. The only difference between them is
what the three dimensions contain when HR first sees them - drafted bands, or empty ones.
Neither is usable until HR presses approve, which is the whole point: an auto-drafted
rubric is a starting point, and scoring a real candidate against un-reviewed model output
would launder a guess into a hiring signal.

The three dimensions themselves are never authored. They are fixed in models/rubric.py so
that two candidates for two different jobs are still being measured on the same axes; what
varies per job is only what evidence counts as Strong, Decent or Not a Fit.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    DIMENSION_INTENT,
    DIMENSION_KEYS,
    DIMENSION_LABELS,
    Interview,
    InterviewDocument,
    Rubric,
    RubricDimension,
)
from app.services import modelark
from app.services.planner import LANGUAGE_NAMES

log = logging.getLogger(__name__)

BAND_MAX_CHARS = 2000


def serialize(rubric: Rubric | None) -> dict | None:
    """Wire shape. Always emits the three dimensions in their fixed order, even for a
    rubric whose rows predate a key, so the editor never has to cope with a gap."""
    if rubric is None:
        return None
    by_key = {d.key: d for d in rubric.dimensions}
    return {
        "id": rubric.id,
        "interview_id": rubric.interview_id,
        "status": rubric.status,
        "source": rubric.source,
        "version": rubric.version,
        "approved_at": rubric.approved_at,
        "updated_at": rubric.updated_at,
        "complete": rubric.is_complete,
        "dimensions": [
            {
                "key": key,
                "label": DIMENSION_LABELS[key],
                "intent": DIMENSION_INTENT[key],
                "order_index": order,
                "strong": (by_key[key].strong_band if key in by_key else "") or "",
                "decent": (by_key[key].decent_band if key in by_key else "") or "",
                "not_fit": (by_key[key].not_fit_band if key in by_key else "") or "",
                "filled": key in by_key and by_key[key].is_filled,
            }
            for order, key in enumerate(DIMENSION_KEYS)
        ],
    }


async def load(db: AsyncSession, interview_id: str) -> Rubric | None:
    stmt = (
        select(Rubric)
        .where(Rubric.interview_id == interview_id)
        .options(selectinload(Rubric.dimensions))
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def ensure(db: AsyncSession, interview_id: str) -> Rubric:
    """Get the interview's rubric, creating a blank one if it has none.

    Blank means the three fixed dimensions exist with empty bands - so the manual path
    starts from a real, editable structure rather than from nothing.
    """
    rubric = await load(db, interview_id)
    if rubric is not None:
        return rubric

    rubric = Rubric(interview_id=interview_id, status="draft", source="manual", version=1)
    db.add(rubric)
    await db.flush()
    for order, key in enumerate(DIMENSION_KEYS):
        db.add(RubricDimension(rubric_id=rubric.id, key=key, order_index=order))
    await db.commit()
    return await load(db, interview_id)  # type: ignore[return-value]


async def save_bands(
    db: AsyncSession, rubric: Rubric, bands: dict[str, dict[str, str]]
) -> Rubric:
    """Apply HR edits. Any content change sends an approved rubric back to draft.

    That demotion is deliberate rather than annoying: the approval is a statement about
    specific wording, so once the wording moves the approval no longer refers to anything.
    Silently keeping it would let an edit slip into live scoring unreviewed.
    """
    existing = {d.key: d for d in rubric.dimensions}
    changed = False

    for order, key in enumerate(DIMENSION_KEYS):
        dimension = existing.get(key)
        if dimension is None:
            dimension = RubricDimension(rubric_id=rubric.id, key=key, order_index=order)
            db.add(dimension)
            changed = True
        dimension.order_index = order
        incoming = bands.get(key) or {}
        for field, band in (
            ("strong_band", "strong"),
            ("decent_band", "decent"),
            ("not_fit_band", "not_fit"),
        ):
            value = str(incoming.get(band, getattr(dimension, field)) or "")[:BAND_MAX_CHARS]
            if value != getattr(dimension, field):
                setattr(dimension, field, value)
                changed = True

    if changed:
        rubric.version += 1
        rubric.updated_at = datetime.now(timezone.utc)
        if rubric.status == "approved":
            rubric.status = "draft"
            rubric.approved_at = None

    await db.commit()
    return await load(db, rubric.interview_id)  # type: ignore[return-value]


async def approve(db: AsyncSession, rubric: Rubric) -> Rubric:
    """The explicit sign-off that makes a rubric usable for scoring."""
    if not rubric.is_complete:
        raise ValueError(
            "Every dimension needs all three band definitions filled in before approval"
        )
    rubric.status = "approved"
    rubric.approved_at = datetime.now(timezone.utc)
    await db.commit()
    return await load(db, rubric.interview_id)  # type: ignore[return-value]


async def draft(db: AsyncSession, interview: Interview) -> Rubric:
    """Draft band definitions from the interview's job-requirement documents.

    Replaces the band text and marks the rubric ai_draft + draft. It never approves:
    HR reviews and signs off, always.
    """
    stmt = select(InterviewDocument).where(
        InterviewDocument.interview_id == interview.id,
        InterviewDocument.doc_type == "job_requirement",
    )
    docs = (await db.execute(stmt)).scalars().all()
    requirements = "\n\n".join(d.content_text for d in docs if d.content_text).strip()
    if not requirements:
        raise ValueError(
            "Add at least one job-requirement document before drafting a rubric"
        )

    lang = LANGUAGE_NAMES.get(interview.language, "English")
    dimension_brief = "\n".join(
        f'- "{key}" ({DIMENSION_LABELS[key]}): {DIMENSION_INTENT[key]}'
        for key in DIMENSION_KEYS
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You write scoring rubrics for a hiring team. The rubric has exactly three "
                "fixed dimensions, given below. You do not invent, rename, merge or drop "
                "dimensions - you only write what Strong, Decent and Not a Fit evidence "
                "concretely looks like for one specific job, on each of the three.\n\n"
                f"THE THREE FIXED DIMENSIONS:\n{dimension_brief}\n\n"
                "Calibration, and this matters more than anything else here: the interview "
                "these bands will be applied to is a SHORT GET-TO-KNOW CONVERSATION - "
                "roughly 8-12 spoken questions, no take-home, no whiteboard, no deep dive. "
                "'Strong' therefore means the candidate gave clear, credible signal on the "
                "dimension. It does NOT mean they exhausted the topic, quantified an "
                "outcome, or proved anything at interrogation depth. A band that no "
                "candidate could satisfy in a fifteen-minute chat is a broken band.\n\n"
                "Write each band as 2-4 sentences describing observable evidence a reader "
                "could check against a transcript. Be concrete and specific to THIS role - "
                "name the systems, domains and kinds of work the requirements mention. "
                "Never write a band as a number, a score, a percentage, or a count of "
                "years. Never reference age, religion, ethnicity, gender, marital or "
                "family status, disability, pregnancy, or nationality.\n"
                f"Write every band in {lang}.\n\n"
                "Return ONLY a JSON object of the form "
                '{"dimensions": [{"key": "<one of the three keys>", "strong": str, '
                '"decent": str, "not_fit": str}, ...]} with all three dimensions present.'
            ),
        },
        {
            "role": "user",
            "content": (
                f"Position: {interview.position_title}\n"
                f"Interview: {interview.title}\n"
                f"HR notes: {interview.guardrail_notes or 'none'}\n\n"
                f"JOB REQUIREMENTS:\n{requirements[:12000]}"
            ),
        },
    ]

    raw = await modelark.chat_json(messages, max_tokens=2500)
    items = raw.get("dimensions") if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        items = []

    bands: dict[str, dict[str, str]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("key") or "").strip()
        if key not in DIMENSION_KEYS:
            continue
        bands[key] = {
            "strong": str(item.get("strong") or "").strip(),
            "decent": str(item.get("decent") or "").strip(),
            "not_fit": str(item.get("not_fit") or "").strip(),
        }

    if not bands:
        raise ValueError(
            "The model did not return a usable rubric. Try again, or write the bands by hand."
        )
    if missing := [k for k in DIMENSION_KEYS if k not in bands]:
        log.warning("Rubric draft missing dimensions %s for interview %s", missing, interview.id)

    rubric = await ensure(db, interview.id)
    rubric = await save_bands(db, rubric, bands)
    rubric.source = "ai_draft"
    # A draft is never approved on arrival, even if it overwrote an approved rubric.
    rubric.status = "draft"
    rubric.approved_at = None
    await db.commit()
    return await load(db, interview.id)  # type: ignore[return-value]
