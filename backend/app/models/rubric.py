"""Scoring rubric and the judge's verdict.

Two deliberate shapes here, both driven by the same constraint: an LLM judge cannot
reproduce a fine-grained number consistently across runs, but it can tell four
wide-margin, named tiers apart.

  - The rubric's three dimensions are FIXED for every interview. What HR authors per
    job is only what "strong / decent / not a fit" evidence concretely looks like for
    that role. Fixing the dimensions is what keeps two candidates for two different
    positions comparable at all.
  - The evaluation stores the judge's per-dimension reasoning alongside the tier, plus
    the transcript-quality assessment that can override it. A verdict without traceable
    evidence is exactly the failure mode this feature exists to avoid, so the evidence
    is a column, not a prompt instruction.
"""

from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin, TZDateTime, utcnow

# ---------------------------------------------------------------- the fixed three
#
# This is a get-to-know interview - extract past experience, gauge JD fit with simple
# questions, watch the candidate reason on the spot. It is not an exhaustive technical
# exam, and the dimensions are chosen to match what such a conversation can actually
# evidence. Adding a fourth here is a schema change on purpose: the set is meant to be
# stable across every job so scores stay comparable.

DIMENSION_KEYS: tuple[str, ...] = (
    "background_fit",
    "on_the_spot_reasoning",
    "communication_clarity",
)

DIMENSION_LABELS: dict[str, str] = {
    "background_fit": "Relevant Background & Domain Fit",
    "on_the_spot_reasoning": "On-the-Spot Reasoning",
    "communication_clarity": "Communication Clarity",
}

# What each dimension is asking about. Shown to HR under the dimension heading and fed
# to the drafting LLM so an auto-draft cannot quietly redefine a dimension.
DIMENSION_INTENT: dict[str, str] = {
    "background_fit": (
        "Whether the candidate's stated past experience credibly matches what this role "
        "requires - a real technical project for an engineering role, closing the books "
        "for an accounting role, and so on."
    ),
    "on_the_spot_reasoning": (
        "Whether the candidate can think through a role-relevant scenario or question "
        "posed during the interview, rather than only reciting a prepared answer."
    ),
    "communication_clarity": (
        "Whether the candidate can explain their own experience and thinking in a way "
        "that is understandable to the listener."
    ),
}

# Overall tiers. Deliberately four, named, and wide - never a sliding scale.
VERDICT_STRONG = "strong_fit"
VERDICT_DECENT = "decent_fit"
VERDICT_NOT_A_FIT = "not_a_fit"
VERDICT_INCONCLUSIVE = "inconclusive"

# What the criteria alone are allowed to produce. Inconclusive is NOT in this set: it is
# reachable only through the transcript-quality gate, so a candidate can never be filed
# as unjudgeable for any reason other than a degraded transcript.
CRITERIA_VERDICTS: tuple[str, ...] = (VERDICT_STRONG, VERDICT_DECENT, VERDICT_NOT_A_FIT)
ALL_VERDICTS: tuple[str, ...] = (*CRITERIA_VERDICTS, VERDICT_INCONCLUSIVE)

# Per-dimension outcomes. "not_evidenced" is separate from "not_a_fit" for the same
# reason Inconclusive is separate at the top level: nothing said is not the same as
# something said badly.
DIMENSION_VERDICTS: tuple[str, ...] = ("strong", "decent", "not_a_fit", "not_evidenced")

TRANSCRIPT_QUALITY: tuple[str, ...] = ("usable", "degraded", "unusable")

# Whether the conversation got through the interview at all. Deliberately separate from
# TRANSCRIPT_QUALITY, because folding the two together is what makes a truncated
# interview unreadable to HR: "we could not make out what she said" and "she was never
# asked" are different facts with different remedies - one is our audio pipeline, the
# other is a conversation that needs finishing. A clean recording of half an interview is
# a usable transcript of a partial interview, and the page has to be able to say that.
#
# Nullable on the Evaluation: rows judged before this existed, and any run where the
# model did not answer, record nothing rather than guessing "complete".
INTERVIEW_COMPLETENESS: tuple[str, ...] = ("complete", "partial")


class Rubric(Base, IdMixin, TimestampMixin):
    """One rubric per interview. Both authoring paths - LLM draft and blank manual -
    converge on this same editable row; `source` only records which one started it."""

    __tablename__ = "rubrics"

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), unique=True, index=True
    )
    # status: draft | approved. An auto-draft is a starting point, never a final
    # artifact - only an explicit approval makes a rubric usable for scoring.
    status: Mapped[str] = mapped_column(String(16), default="draft")
    # source: ai_draft | manual
    source: Mapped[str] = mapped_column(String(16), default="manual")
    # Bumped on every content save. Evaluations record the version they were judged
    # against, so HR can see at a glance when a score predates the rubric on screen.
    version: Mapped[int] = mapped_column(Integer, default=1)
    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)
    updated_at: Mapped[datetime] = mapped_column(TZDateTime, default=utcnow)

    interview: Mapped["Interview"] = relationship(back_populates="rubric")  # noqa: F821
    dimensions: Mapped[list["RubricDimension"]] = relationship(
        back_populates="rubric",
        cascade="all, delete-orphan",
        order_by="RubricDimension.order_index",
    )

    @property
    def is_complete(self) -> bool:
        """Every dimension carries all three bands. The precondition for approval."""
        by_key = {d.key: d for d in self.dimensions}
        return all(
            key in by_key and by_key[key].is_filled for key in DIMENSION_KEYS
        )


class RubricDimension(Base, IdMixin):
    """The band definitions for one fixed dimension - what Strong, Decent and Not a Fit
    concretely look like for this particular job."""

    __tablename__ = "rubric_dimensions"

    rubric_id: Mapped[str] = mapped_column(
        ForeignKey("rubrics.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(48))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    strong_band: Mapped[str] = mapped_column(Text, default="")
    decent_band: Mapped[str] = mapped_column(Text, default="")
    not_fit_band: Mapped[str] = mapped_column(Text, default="")

    rubric: Mapped["Rubric"] = relationship(back_populates="dimensions")

    @property
    def is_filled(self) -> bool:
        return bool(
            (self.strong_band or "").strip()
            and (self.decent_band or "").strip()
            and (self.not_fit_band or "").strip()
        )


class Evaluation(Base, IdMixin, TimestampMixin):
    """One judge pass over one completed transcript. Re-scoring replaces the row rather
    than appending, so a candidate always has exactly one current verdict."""

    __tablename__ = "evaluations"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), unique=True, index=True
    )
    # Kept nullable and SET NULL: deleting a rubric must not delete the record of how a
    # candidate was judged.
    rubric_id: Mapped[str | None] = mapped_column(
        ForeignKey("rubrics.id", ondelete="SET NULL"), default=None
    )
    rubric_version: Mapped[int | None] = mapped_column(Integer, default=None)

    # status: complete | failed. A failed run keeps `verdict` NULL rather than inventing
    # a tier - "the judge did not run" and "the judge found nothing" are different facts.
    status: Mapped[str] = mapped_column(String(16), default="complete")
    error: Mapped[str | None] = mapped_column(Text, default=None)

    verdict: Mapped[str | None] = mapped_column(String(24), default=None)
    # What the three dimensions alone supported, before the transcript-quality gate. Kept
    # so HR can see that an Inconclusive was a transcript problem, not a weak candidate.
    criteria_verdict: Mapped[str | None] = mapped_column(String(24), default=None)
    adjustment_reason: Mapped[str | None] = mapped_column(Text, default=None)

    transcript_quality: Mapped[str | None] = mapped_column(String(16), default=None)
    transcript_quality_note: Mapped[str | None] = mapped_column(Text, default=None)

    # complete | partial. Does NOT move the verdict - a partial interview still gets the
    # judge's honest reading of what was said. It qualifies it, so that a Decent Fit on
    # two answers is never mistaken for a Decent Fit on a full conversation.
    interview_completeness: Mapped[str | None] = mapped_column(String(16), default=None)
    interview_completeness_note: Mapped[str | None] = mapped_column(Text, default=None)

    summary: Mapped[str] = mapped_column(Text, default="")

    model: Mapped[str | None] = mapped_column(String(120), default=None)
    candidate_turn_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)

    session: Mapped["InterviewSession"] = relationship(  # noqa: F821
        back_populates="evaluation"
    )
    dimensions: Mapped[list["EvaluationDimension"]] = relationship(
        back_populates="evaluation",
        cascade="all, delete-orphan",
        order_by="EvaluationDimension.order_index",
    )


class EvaluationDimension(Base, IdMixin):
    """The judge's reasoning for one dimension, written before it committed to a tier."""

    __tablename__ = "evaluation_dimensions"

    evaluation_id: Mapped[str] = mapped_column(
        ForeignKey("evaluations.id", ondelete="CASCADE"), index=True
    )
    key: Mapped[str] = mapped_column(String(48))
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    verdict: Mapped[str] = mapped_column(String(24), default="not_evidenced")
    note: Mapped[str] = mapped_column(Text, default="")
    # JSON list of {"turn": int, "quote": str} - the candidate turns this verdict rests
    # on, after every cited turn has been checked against the real transcript. Text
    # rather than a JSON column so SQLite and Postgres stay interchangeable.
    evidence_json: Mapped[str] = mapped_column(Text, default="[]")
    # Set when citations were dropped or a verdict was downgraded for lack of evidence.
    # Surfaced to HR rather than silently applied.
    evidence_warning: Mapped[str | None] = mapped_column(Text, default=None)

    evaluation: Mapped["Evaluation"] = relationship(back_populates="dimensions")
