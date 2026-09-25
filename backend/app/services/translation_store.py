"""Read-through translation cache over `services/translate.py`.

Keeps the API layer thin and keeps `translate.py` free of any database dependency: that
module knows how to talk to BytePlus and nothing else, this one knows what has already
been paid for.

The rule that matters here: only a translation that actually came back from the provider
is ever written. Anything that degraded to returning its input on failure would store the
untranslated text as the translation and serve it forever, indistinguishable from a real
one - so `translate` raises, and everything below lets the failure propagate up to the
route, which is where a failure gets softened into "here is the original, and why".
"""

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import DocumentTranslation, InterviewDocument, TranscriptTurn, TurnTranslation
from app.services import translate

log = logging.getLogger(__name__)


def _provider() -> str:
    return "mock" if settings.mock_ai else "byteplus"


async def transcript_translations(
    db: AsyncSession, session_id: str, target: str
) -> dict[str, str]:
    """Every turn of a session translated into `target`, keyed by turn id.

    Cached rows are reused; only the misses are sent to the provider, so re-opening a
    candidate costs nothing and a transcript that grew by two turns costs two turns.

    Raises whatever `translate` raises. The caller decides whether a failure means an
    error response or falling back to the original text.
    """
    turns = list(
        (
            await db.execute(
                select(TranscriptTurn)
                .where(TranscriptTurn.session_id == session_id)
                .order_by(TranscriptTurn.turn_index)
            )
        )
        .scalars()
        .all()
    )
    if not turns:
        return {}

    cached = list(
        (
            await db.execute(
                select(TurnTranslation).where(
                    TurnTranslation.session_id == session_id,
                    TurnTranslation.target_language == target,
                )
            )
        )
        .scalars()
        .all()
    )
    out = {row.turn_id: row.text for row in cached}

    missing = [t for t in turns if t.id not in out and t.text.strip()]
    if not missing:
        return out

    log.info(
        "Translating %d/%d turn(s) of session %s into %r (%d already cached)",
        len(missing),
        len(turns),
        session_id,
        target,
        len(out),
    )
    # Batching happens inside translate_texts; handing it the whole list lets it pack
    # full requests rather than one per turn.
    results = await translate.translate_texts([t.text for t in missing], target)

    for turn, text in zip(missing, results):
        db.add(
            TurnTranslation(
                turn_id=turn.id,
                session_id=session_id,
                target_language=target,
                text=text,
                provider=_provider(),
            )
        )
        out[turn.id] = text
    await db.commit()
    return out


async def cached_document_translation(
    db: AsyncSession, document_id: str, target: str
) -> DocumentTranslation | None:
    """The stored translation, or None. Never calls the provider - never costs anything."""
    return (
        await db.execute(
            select(DocumentTranslation).where(
                DocumentTranslation.document_id == document_id,
                DocumentTranslation.target_language == target,
            )
        )
    ).scalar_one_or_none()


async def document_translation(
    db: AsyncSession,
    document_id: str,
    target: str,
    *,
    source: str | None = None,
    used_for_indexing: bool = False,
) -> DocumentTranslation | None:
    """Translate one document into `target`, reusing a cached row when there is one.

    Returns None if the document does not exist or has no text. `used_for_indexing`
    marks the row as the text that was actually embedded, which is the only record of
    which language the vectors are in - see models/translation.py.
    """
    existing = await cached_document_translation(db, document_id, target)
    if existing is not None:
        if used_for_indexing and not existing.used_for_indexing:
            existing.used_for_indexing = True
            await db.commit()
        return existing

    doc = await db.get(InterviewDocument, document_id)
    if doc is None or not (doc.content_text or "").strip():
        return None

    translated = await translate.translate_document(doc.content_text, target, source)
    row = DocumentTranslation(
        document_id=document_id,
        target_language=target,
        source_language=source,
        text=translated,
        provider=_provider(),
        used_for_indexing=used_for_indexing,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row
