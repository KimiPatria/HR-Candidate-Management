"""Cached translations of transcript turns and reference documents.

Two design notes worth keeping.

SEPARATE TABLES, NOT COLUMNS. The obvious shape is `translated_text` on TranscriptTurn
and on InterviewDocument. It does not work here: `core/db.py` creates the schema with
`Base.metadata.create_all` and there is no Alembic, so a new column on an existing table
is created in a fresh database and silently missing from the one this app is already
running on. New tables are created either way, so the state lives beside the rows it
describes rather than inside them. Swap this for real columns whenever migrations land.

CACHED, NOT COMPUTED ON READ. Translation is billed per character, and an HR reviewer
opening the same candidate three times should pay once. Both tables are a read-through
cache keyed by (row, target language): look, translate the misses, write them back.
"""

from sqlalchemy import ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, IdMixin, TimestampMixin


class TurnTranslation(Base, IdMixin, TimestampMixin):
    __tablename__ = "turn_translations"
    __table_args__ = (
        # One translation per turn per target language; re-translating replaces it.
        UniqueConstraint("turn_id", "target_language", name="uq_turn_translation"),
    )

    turn_id: Mapped[str] = mapped_column(
        ForeignKey("transcript_turns.id", ondelete="CASCADE"), index=True
    )
    # Denormalised from the turn so a whole transcript's translations load in one query,
    # the same reasoning as DocumentChunk.interview_id.
    session_id: Mapped[str] = mapped_column(
        ForeignKey("interview_sessions.id", ondelete="CASCADE"), index=True
    )
    target_language: Mapped[str] = mapped_column(String(16))
    # What the provider said the source was, when it was asked to detect rather than told.
    source_language: Mapped[str | None] = mapped_column(String(16), default=None)
    text: Mapped[str] = mapped_column(Text)
    # provider: byteplus | mock. Recorded so a transcript translated while MOCK_AI was on
    # is identifiable rather than quietly wrong.
    provider: Mapped[str] = mapped_column(String(32), default="byteplus")


class DocumentTranslation(Base, IdMixin, TimestampMixin):
    __tablename__ = "document_translations"
    __table_args__ = (
        UniqueConstraint("document_id", "target_language", name="uq_document_translation"),
    )

    document_id: Mapped[str] = mapped_column(
        ForeignKey("interview_documents.id", ondelete="CASCADE"), index=True
    )
    target_language: Mapped[str] = mapped_column(String(16))
    source_language: Mapped[str | None] = mapped_column(String(16), default=None)
    text: Mapped[str] = mapped_column(Text)
    provider: Mapped[str] = mapped_column(String(32), default="byteplus")
    # True when this is the text that was actually chunked and embedded, rather than a
    # translation produced only for a human to read. Retrieval quality depends on knowing
    # which language the vectors are in, and DocumentChunk cannot record it (existing
    # table, see the module docstring).
    used_for_indexing: Mapped[bool] = mapped_column(default=False)
