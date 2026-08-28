"""Document ingestion.

Upload and paste are separate routes because one is multipart and the other is JSON -
FastAPI cannot accept both on a single path. Both converge on the same indexing path.
"""

import logging
import uuid

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    UploadFile,
    status,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal, get_db
from app.core.security import HRUser
from app.models import Interview, InterviewDocument
from app.schemas import DocumentOut, PasteDocument
from app.services import extract, rag

log = logging.getLogger(__name__)
router = APIRouter(prefix="/interviews/{interview_id}/documents", tags=["documents"])

VALID_DOC_TYPES = {"job_requirement", "knowledge_base"}


async def _index_in_background(document_id: str) -> None:
    """BackgroundTasks runs after the response is sent, so the request-scoped session is
    already closed. Open a fresh one."""
    async with SessionLocal() as db:
        await rag.index_document(db, document_id)


async def _require_interview(db: AsyncSession, interview_id: str) -> Interview:
    interview = await db.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return interview


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    interview_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> list[InterviewDocument]:
    await _require_interview(db, interview_id)
    stmt = (
        select(InterviewDocument)
        .where(InterviewDocument.interview_id == interview_id)
        .order_by(InterviewDocument.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    interview_id: str,
    background: BackgroundTasks,
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    await _require_interview(db, interview_id)
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"doc_type must be one of {VALID_DOC_TYPES}")

    data = await file.read()
    if len(data) > extract.MAX_BYTES:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, "File exceeds 20MB")
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    try:
        text = extract.extract_text(file.filename or "upload.txt", data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    # Keep the original alongside the extracted text so a bad extraction is recoverable.
    safe_name = f"{uuid.uuid4().hex}-{(file.filename or 'upload').replace('/', '_')[:80]}"
    stored = settings.upload_path / safe_name
    stored.write_bytes(data)

    doc = InterviewDocument(
        interview_id=interview_id,
        doc_type=doc_type,
        source="upload",
        filename=file.filename,
        stored_path=str(stored),
        content_text=text,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    background.add_task(_index_in_background, doc.id)
    return doc


@router.post("/paste", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def paste_document(
    interview_id: str,
    payload: PasteDocument,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    await _require_interview(db, interview_id)
    doc = InterviewDocument(
        interview_id=interview_id,
        doc_type=payload.doc_type,
        source="pasted",
        filename=payload.filename,
        content_text=payload.content_text,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    background.add_task(_index_in_background, doc.id)
    return doc


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    interview_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> None:
    doc = await db.get(InterviewDocument, document_id)
    if doc is None or doc.interview_id != interview_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    await db.delete(doc)
    await db.commit()


@router.post("/{document_id}/reindex", response_model=DocumentOut)
async def reindex_document(
    interview_id: str,
    document_id: str,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    doc = await db.get(InterviewDocument, document_id)
    if doc is None or doc.interview_id != interview_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    doc.index_status = "pending"
    doc.index_error = None
    await db.commit()
    await db.refresh(doc)
    background.add_task(_index_in_background, doc.id)
    return doc
