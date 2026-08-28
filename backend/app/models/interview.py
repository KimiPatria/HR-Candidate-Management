from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, IdMixin, TimestampMixin, TZDateTime

# status: draft | ready | archived
# A plan can only be generated once at least one job_requirement document is indexed.


class Interview(Base, IdMixin, TimestampMixin):
    __tablename__ = "interviews"

    title: Mapped[str] = mapped_column(String(200))
    position_title: Mapped[str] = mapped_column(String(200))
    language: Mapped[str] = mapped_column(String(16), default="en")
    guardrail_notes: Mapped[str | None] = mapped_column(Text, default=None)
    status: Mapped[str] = mapped_column(String(32), default="draft")

    # Populated once per interview by tts_voice.ensure_voice / avatar selection.
    voice_id: Mapped[str | None] = mapped_column(String(128), default=None)
    avatar_id: Mapped[str | None] = mapped_column(String(128), default=None)

    documents: Mapped[list["InterviewDocument"]] = relationship(
        back_populates="interview", cascade="all, delete-orphan"
    )
    plan_items: Mapped[list["PlanItem"]] = relationship(
        back_populates="interview",
        cascade="all, delete-orphan",
        order_by="PlanItem.order_index",
    )
    sessions: Mapped[list["InterviewSession"]] = relationship(  # noqa: F821
        back_populates="interview", cascade="all, delete-orphan"
    )


class InterviewDocument(Base, IdMixin, TimestampMixin):
    __tablename__ = "interview_documents"

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    # doc_type: job_requirement | knowledge_base
    doc_type: Mapped[str] = mapped_column(String(32))
    # source: upload | pasted
    source: Mapped[str] = mapped_column(String(16))
    filename: Mapped[str | None] = mapped_column(String(255), default=None)
    stored_path: Mapped[str | None] = mapped_column(String(512), default=None)
    content_text: Mapped[str] = mapped_column(Text, default="")
    vdb_collection_ref: Mapped[str | None] = mapped_column(String(255), default=None)
    # index_status: pending | indexing | indexed | failed
    index_status: Mapped[str] = mapped_column(String(16), default="pending")
    index_error: Mapped[str | None] = mapped_column(Text, default=None)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)

    interview: Mapped["Interview"] = relationship(back_populates="documents")


class PlanItem(Base, IdMixin, TimestampMixin):
    """One question on the interview agenda. Generated once per interview so that every
    candidate is asked the same core set and answers stay comparable."""

    __tablename__ = "plan_items"

    interview_id: Mapped[str] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True
    )
    order_index: Mapped[int] = mapped_column(Integer, default=0)
    question: Mapped[str] = mapped_column(Text)
    competency: Mapped[str | None] = mapped_column(String(120), default=None)
    must_ask: Mapped[bool] = mapped_column(Boolean, default=True)
    # What a good answer touches on; used to judge whether the topic is covered yet.
    coverage_hint: Mapped[str | None] = mapped_column(Text, default=None)

    interview: Mapped["Interview"] = relationship(back_populates="plan_items")
