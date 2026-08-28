"""Interview agenda: what to ask, when to probe, when to stop.

This is the state the interviewer runs on. Without it the AI only ever reacts to the
candidate, which produces interviews that wander and coverage that is not comparable
between candidates.

The plan is generated once per Interview (not per session) so every candidate for a
position gets the same core questions. Per-candidate coverage lives in
SessionPlanProgress.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Interview, InterviewDocument, InterviewSession, PlanItem
from app.models import SessionPlanProgress, TranscriptTurn
from app.services import modelark

log = logging.getLogger(__name__)

MAX_FOLLOW_UPS = 2
# A short answer is one that almost certainly has not covered the competency yet.
SUBSTANTIVE_WORD_COUNT = 25
MAX_TURNS = 60
DEFAULT_MAX_MINUTES = 30

ASK = "ASK"
FOLLOW_UP = "FOLLOW_UP"
WRAP_UP = "WRAP_UP"

LANGUAGE_NAMES = {
    "en": "English",
    "id": "Bahasa Indonesia",
    "zh": "Mandarin Chinese",
}


@dataclass
class PlanDecision:
    action: str
    plan_item: PlanItem | None = None
    reason: str = ""

    def as_directive(self, language: str = "en") -> str:
        """Rendered into the system prompt so the LLM knows its job for this turn."""
        lang = LANGUAGE_NAMES.get(language, "English")
        if self.action == ASK and self.plan_item:
            return (
                f"NEXT ACTION: ASK: {self.plan_item.question}\n"
                f"Acknowledge the previous answer in one short clause, then ask this "
                f"question. Ask it in {lang}. Do not ask anything else."
            )
        if self.action == FOLLOW_UP and self.plan_item:
            return (
                "NEXT ACTION: FOLLOW_UP\n"
                f"The candidate has not yet covered: {self.plan_item.question}\n"
                f"What a full answer needs: {self.plan_item.coverage_hint or 'a concrete example'}\n"
                f"Ask one short probing follow-up in {lang}. Do not move on yet."
            )
        return (
            "NEXT ACTION: WRAP_UP\n"
            f"All planned questions are covered ({self.reason}). Thank the candidate in "
            f"{lang}, tell them the team will follow up, and close warmly. Do not ask "
            "another question."
        )


async def generate_plan(db: AsyncSession, interview: Interview) -> list[PlanItem]:
    """Build the question agenda from the interview's job-requirement documents.
    Replaces any existing plan for the interview."""
    stmt = select(InterviewDocument).where(
        InterviewDocument.interview_id == interview.id,
        InterviewDocument.doc_type == "job_requirement",
    )
    docs = (await db.execute(stmt)).scalars().all()
    requirements = "\n\n".join(d.content_text for d in docs if d.content_text).strip()
    if not requirements:
        raise ValueError("Add at least one job-requirement document before generating a plan")

    lang = LANGUAGE_NAMES.get(interview.language, "English")
    messages = [
        {
            "role": "system",
            "content": (
                "You design structured job interviews. From the job requirements, produce "
                "8-12 interview questions that a human interviewer would ask, ordered from "
                "warm-up to most demanding. Cover each distinct competency at most twice. "
                "Never produce questions about age, religion, ethnicity, marital or family "
                "status, disability, or pregnancy.\n"
                f"Write every question in {lang}.\n"
                "Return ONLY a JSON array. Each element: "
                '{"question": str, "competency": str, "must_ask": bool, '
                '"coverage_hint": str}. coverage_hint describes what a complete answer '
                "contains, used later to judge whether to probe further."
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

    raw = await modelark.chat_json(messages)
    items = raw if isinstance(raw, list) else raw.get("questions", [])
    if not items:
        items = _fallback_plan(interview)

    existing = (
        await db.execute(select(PlanItem).where(PlanItem.interview_id == interview.id))
    ).scalars().all()
    for item in existing:
        await db.delete(item)

    created: list[PlanItem] = []
    for i, item in enumerate(items):
        if not isinstance(item, dict) or not item.get("question"):
            continue
        plan_item = PlanItem(
            interview_id=interview.id,
            order_index=i,
            question=str(item["question"])[:2000],
            competency=str(item.get("competency") or "")[:120] or None,
            must_ask=bool(item.get("must_ask", True)),
            coverage_hint=str(item.get("coverage_hint") or "")[:2000] or None,
        )
        db.add(plan_item)
        created.append(plan_item)

    interview.status = "ready" if created else "draft"
    await db.commit()
    for item in created:
        await db.refresh(item)
    return created


def _fallback_plan(interview: Interview) -> list[dict]:
    """Used when the LLM returns nothing parseable, so a session can still run."""
    pos = interview.position_title
    return [
        {
            "question": f"To start, walk me through your background and what drew you to the {pos} role.",
            "competency": "motivation",
            "must_ask": True,
            "coverage_hint": "relevant experience and a specific reason for applying",
        },
        {
            "question": "Tell me about a project you owned end to end. What was your specific contribution?",
            "competency": "ownership",
            "must_ask": True,
            "coverage_hint": "a concrete project, their own role, and the outcome",
        },
        {
            "question": "Describe a time you disagreed with a colleague. How did you resolve it?",
            "competency": "collaboration",
            "must_ask": True,
            "coverage_hint": "a real disagreement, their approach, and the resolution",
        },
        {
            "question": "What questions do you have about the role or the team?",
            "competency": "closing",
            "must_ask": False,
            "coverage_hint": "any question from the candidate",
        },
    ]


async def ensure_progress(db: AsyncSession, session: InterviewSession) -> None:
    """Create the per-session coverage rows. Idempotent."""
    existing = (
        await db.execute(
            select(func.count(SessionPlanProgress.id)).where(
                SessionPlanProgress.session_id == session.id
            )
        )
    ).scalar_one()
    if existing:
        return

    items = (
        await db.execute(
            select(PlanItem)
            .where(PlanItem.interview_id == session.interview_id)
            .order_by(PlanItem.order_index)
        )
    ).scalars().all()
    for item in items:
        db.add(SessionPlanProgress(session_id=session.id, plan_item_id=item.id))
    await db.commit()


async def _ordered_progress(
    db: AsyncSession, session_id: str
) -> list[tuple[SessionPlanProgress, PlanItem]]:
    rows = await db.execute(
        select(SessionPlanProgress, PlanItem)
        .join(PlanItem, PlanItem.id == SessionPlanProgress.plan_item_id)
        .where(SessionPlanProgress.session_id == session_id)
        .order_by(PlanItem.order_index)
    )
    return list(rows.all())


async def decide_next_action(
    db: AsyncSession,
    session: InterviewSession,
    candidate_text: str,
) -> PlanDecision:
    """Called once per candidate turn, before the LLM runs.

    Coverage is judged heuristically rather than with an LLM call because this sits on
    the latency-critical path. The follow-up cap guarantees the interview always makes
    forward progress even when the heuristic is wrong.
    """
    now = datetime.now(timezone.utc)

    if session.started_at:
        elapsed = (now - session.started_at).total_seconds() / 60
        if elapsed > DEFAULT_MAX_MINUTES:
            return PlanDecision(WRAP_UP, reason="time limit reached")

    turn_count = (
        await db.execute(
            select(func.count(TranscriptTurn.id)).where(TranscriptTurn.session_id == session.id)
        )
    ).scalar_one()
    if turn_count >= MAX_TURNS:
        return PlanDecision(WRAP_UP, reason="turn limit reached")

    rows = await _ordered_progress(db, session.id)
    if not rows:
        return PlanDecision(WRAP_UP, reason="no plan configured")

    current = next((r for r in rows if r[0].status == "asked"), None)

    if current is not None:
        progress, item = current
        answered = len(candidate_text.split()) >= SUBSTANTIVE_WORD_COUNT
        if not answered and progress.follow_up_count < MAX_FOLLOW_UPS:
            progress.follow_up_count += 1
            await db.commit()
            return PlanDecision(FOLLOW_UP, plan_item=item, reason="answer looked thin")
        progress.status = "covered"
        progress.covered_at = now
        await db.commit()

    nxt = next((r for r in rows if r[0].status == "pending"), None)
    if nxt is None:
        return PlanDecision(WRAP_UP, reason="all questions covered")

    progress, item = nxt
    progress.status = "asked"
    progress.asked_at = now
    await db.commit()
    return PlanDecision(ASK, plan_item=item, reason="next planned question")


async def opening_decision(db: AsyncSession, session: InterviewSession) -> PlanDecision:
    """The interviewer speaks first; this picks the opening question."""
    rows = await _ordered_progress(db, session.id)
    if not rows:
        return PlanDecision(WRAP_UP, reason="no plan configured")
    progress, item = rows[0]
    progress.status = "asked"
    progress.asked_at = datetime.now(timezone.utc)
    await db.commit()
    return PlanDecision(ASK, plan_item=item, reason="opening question")
