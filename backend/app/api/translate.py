"""HR-facing translation routes.

Read-through cached, so opening the same candidate twice costs one translation. Every
route is HR-only: candidates never see these, and the interview itself is unaffected by
anything here - a translated transcript is a reading aid for the reviewer, not a change
to what was said.
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_db
from app.core.security import HRUser
from app.models import InterviewDocument, InterviewSession
from app.schemas import DocumentTranslationOut, Language, TranscriptTranslationOut
from app.services import translation_store

log = logging.getLogger(__name__)
router = APIRouter(tags=["translation"])


async def _require_document(db: AsyncSession, document_id: str) -> InterviewDocument:
    doc = await db.get(InterviewDocument, document_id)
    if doc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


def _require_enabled() -> None:
    if not (settings.translate_enabled or settings.mock_ai):
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Translation is not configured. Set BYTEPLUS_ACCESS_KEY and "
            "BYTEPLUS_SECRET_KEY, and activate BytePlus Translate for this account.",
        )


@router.post(
    "/sessions/{session_id}/transcript/translate",
    response_model=TranscriptTranslationOut,
)
async def translate_transcript(
    session_id: str,
    target: Language = Query(..., description="Language to translate the transcript into"),
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> TranscriptTranslationOut:
    _require_enabled()
    session = await db.get(InterviewSession, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")

    try:
        translations = await translation_store.transcript_translations(db, session_id, target)
    except Exception as exc:  # noqa: BLE001 - a failed translation must not 500 the page
        # Deliberately a 200 with translated=False rather than an error status: the
        # reviewer still has the original transcript on screen and the useful outcome is
        # a banner saying the translation is unavailable, not a broken panel.
        log.exception("Transcript translation failed for session %s", session_id)
        return TranscriptTranslationOut(
            session_id=session_id,
            target_language=target,
            translations={},
            translated=False,
            detail=str(exc)[:300],
        )

    return TranscriptTranslationOut(
        session_id=session_id,
        target_language=target,
        translations=translations,
    )


@router.get("/documents/{document_id}/translation", response_model=DocumentTranslationOut)
async def get_document_translation(
    document_id: str,
    target: Language = Query(...),
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> DocumentTranslationOut:
    """Cached translation only. Never calls the provider, so the UI can offer a toggle
    without the page load paying for a translation nobody asked to see.

    Addressed by document id alone rather than nested under an interview: a company
    knowledge-base document belongs to no interview, and one translation route beats two
    that would have to stay in step.
    """
    await _require_document(db, document_id)

    row = await translation_store.cached_document_translation(db, document_id, target)
    if row is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"No cached {target} translation for this document",
        )
    return DocumentTranslationOut.model_validate(row)


@router.post("/documents/{document_id}/translate", response_model=DocumentTranslationOut)
async def translate_document(
    document_id: str,
    target: Language = Query(...),
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> DocumentTranslationOut:
    _require_enabled()
    await _require_document(db, document_id)

    try:
        row = await translation_store.document_translation(db, document_id, target)
    except Exception as exc:  # noqa: BLE001
        log.exception("Document translation failed for %s", document_id)
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"Translation provider rejected this document: {str(exc)[:200]}",
        ) from exc

    if row is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "This document has no extracted text to translate"
        )
    return DocumentTranslationOut.model_validate(row)
