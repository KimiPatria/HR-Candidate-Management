from datetime import datetime

from sqlalchemy import Boolean, ForeignKey, Integer, LargeBinary, String, Text
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

    # Setup autopilot: indexing a job-requirement document chains straight into plan
    # generation and a rubric draft, so HR does not have to press two more buttons to
    # reach the thing they actually came to review. See services/autopilot.py.
    # autopilot_status: idle | running | done | failed
    autopilot_status: Mapped[str] = mapped_column(String(16), default="idle")
    # Which stage is running, so the setup page can name it instead of showing a bare
    # spinner: plan | rubric | None.
    autopilot_step: Mapped[str | None] = mapped_column(String(32), default=None)
    autopilot_error: Mapped[str | None] = mapped_column(Text, default=None)

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
    # One rubric per interview, created lazily the first time HR drafts or edits one.
    # Optional by design: interviews created before scoring existed keep working, and a
    # missing rubric is surfaced as a readiness warning rather than a blocker.
    rubric: Mapped["Rubric | None"] = relationship(  # noqa: F821
        back_populates="interview", cascade="all, delete-orphan", uselist=False
    )


class InterviewDocument(Base, IdMixin, TimestampMixin):
    __tablename__ = "interview_documents"

    # NULL means company-wide: the knowledge base is one static set of company facts
    # shared by every interview, not a copy per position. Only job_requirement documents
    # are ever scoped to a single interview. See app/api/knowledge.py.
    interview_id: Mapped[str | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, default=None
    )
    # doc_type: job_requirement | knowledge_base
    doc_type: Mapped[str] = mapped_column(String(32))
    # source: upload | pasted | google_drive | onedrive
    source: Mapped[str] = mapped_column(String(32))
    filename: Mapped[str | None] = mapped_column(String(255), default=None)
    stored_path: Mapped[str | None] = mapped_column(String(512), default=None)
    # Where a drive-imported document came from, so HR can open the original in the
    # drive it lives in rather than guessing which file was picked.
    source_url: Mapped[str | None] = mapped_column(String(1024), default=None)
    content_text: Mapped[str] = mapped_column(Text, default="")
    vdb_collection_ref: Mapped[str | None] = mapped_column(String(255), default=None)
    # index_status: pending | indexing | indexed | failed
    index_status: Mapped[str] = mapped_column(String(16), default="pending")
    index_error: Mapped[str | None] = mapped_column(Text, default=None)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0)
    indexed_at: Mapped[datetime | None] = mapped_column(TZDateTime, default=None)

    interview: Mapped["Interview | None"] = relationship(back_populates="documents")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    @property
    def char_count(self) -> int:
        """Length of the extracted text. Read by the API so the document list can say how
        much text was actually recovered from a file - the number that distinguishes a
        parsed PDF from a scan that yielded three characters of header."""
        return len(self.content_text or "")

    @property
    def has_original_file(self) -> bool:
        """Whether the bytes as uploaded are still on disk and can be handed back."""
        return bool(self.stored_path)


class DocumentChunk(Base, IdMixin, TimestampMixin):
    """One embedded, retrievable slice of an indexed document - the local vector store
    used whenever the live KnowledgeBase is not configured or is unreachable."""

    __tablename__ = "document_chunks"

    document_id: Mapped[str] = mapped_column(
        ForeignKey("interview_documents.id", ondelete="CASCADE"), index=True
    )
    # Denormalised from the parent document so search can filter without a join.
    # NULL carries the same meaning as it does on the parent: a company-wide chunk, in
    # scope for every interview's retrieval rather than one.
    interview_id: Mapped[str | None] = mapped_column(
        ForeignKey("interviews.id", ondelete="CASCADE"), index=True, default=None
    )
    doc_type: Mapped[str] = mapped_column(String(32))
    chunk_index: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    # float32 vector, raw bytes (see app/services/embeddings.py to_bytes/from_bytes).
    embedding: Mapped[bytes] = mapped_column(LargeBinary)

    document: Mapped["InterviewDocument"] = relationship(back_populates="chunks")


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
