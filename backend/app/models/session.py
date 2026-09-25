from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin, TZDateTime


class InterviewSession(Base, IdMixin, TimestampMixin):
    __tablename__ = "interview_sessions"

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    candidate_name: Mapped[str] = mapped_column(String(200))
    candidate_email: Mapped[str | None] = mapped_column(String(255), default=None)

    join_token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(
        TZDateTime, default=None
    )
    consent_accepted_at: Mapped[datetime | None] = mapped_column(
        TZDateTime, default=None
    )

    rtc_room_id: Mapped[str | None] = mapped_column(String(128), default=None)
    rtc_task_id: Mapped[str | None] = mapped_column(String(128), default=None)
    rtc_user_id: Mapped[str | None] = mapped_column(String(128), default=None)

    # status: created | joined | in_progress | completed | expired | failed
    status: Mapped[str] = mapped_column(String(32), default="created")
    failure_reason: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)

    interview: Mapped["Interview"] = relationship(back_populates="sessions")  # noqa: F821
    turns: Mapped[list["TranscriptTurn"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="TranscriptTurn.turn_index",
    )
    progress: Mapped[list["SessionPlanProgress"]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )
    # At most one current verdict per candidate. Re-scoring replaces this row.
    evaluation: Mapped["Evaluation | None"] = relationship(  # noqa: F821
        back_populates="session", cascade="all, delete-orphan", uselist=False
    )


class TranscriptTurn(Base, IdMixin):
    __tablename__ = "transcript_turns"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, default=0)
    # speaker: ai | candidate | system
    speaker: Mapped[str] = mapped_column(String(16))
    text: Mapped[str] = mapped_column(Text)
    # Which agenda question this turn belongs to, when known.
    plan_item_id: Mapped[str | None] = mapped_column(
        ForeignKey("plan_items.id", ondelete="SET NULL"), default=None
    )
    # Set when the guardrail layer rewrote or deflected the response.
    guardrail_action: Mapped[str | None] = mapped_column(String(32), default=None)
    timestamp: Mapped[datetime] = mapped_column(TZDateTime)

    session: Mapped["InterviewSession"] = relationship(back_populates="turns")


class SessionPlanProgress(Base, IdMixin):
    """Per-candidate coverage of the interview agenda. This is the state that lets the
    backend decide what to ask next and when the interview is done."""

    __tablename__ = "session_plan_progress"

    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True
    )
    plan_item_id: Mapped[str] = mapped_column(ForeignKey("plan_items.id", ondelete="CASCADE"))
    # status: pending | asked | covered | skipped
    status: Mapped[str] = mapped_column(String(16), default="pending")
    follow_up_count: Mapped[int] = mapped_column(Integer, default=0)
    asked_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)
    covered_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)

    session: Mapped["InterviewSession"] = relationship(back_populates="progress")
