"""The company knowledge base: one static set of facts, shared by every interview.

Split out of the per-interview document routes because it is a different kind of thing
on a different clock. A job description is written per position and read once; company
facts - what the business does, how the teams are organised, what the benefits are -
change once or twice a year and apply to every candidate who ever interviews. Keeping a
copy of them on each interview meant re-uploading the same handbook per position and
having no single place to correct it.

Same tables as `app/api/documents.py`, with a null interview_id. `rag.search` treats a
null-scoped chunk as in scope for every interview, so nothing here needs its own
retrieval path.
"""

import logging

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
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.security import HRUser
from app.models import InterviewDocument
from app.schemas import DocumentContentOut, DocumentOut, PasteDocument
from app.services import ingest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/knowledge/documents", tags=["knowledge"])

DOC_TYPE = "knowledge_base"


async def _require_document(db: AsyncSession, document_id: str) -> InterviewDocument:
    doc = await db.get(InterviewDocument, document_id)
    # The interview_id check is the scope check: a job-requirement document must not be
    # reachable, let alone deletable, through the company routes.
    if doc is None or doc.interview_id is not None or doc.doc_type != DOC_TYPE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


@router.get("", response_model=list[DocumentOut])
async def list_documents(
    db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> list[InterviewDocument]:
    stmt = (
        select(InterviewDocument)
        .where(
            InterviewDocument.interview_id.is_(None),
            InterviewDocument.doc_type == DOC_TYPE,
        )
        .order_by(InterviewDocument.created_at)
    )
    return list((await db.execute(stmt)).scalars().all())


@router.post("/upload", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    doc = await ingest.from_upload(db, interview_id=None, doc_type=DOC_TYPE, file=file)
    background.add_task(ingest.index_in_background, doc.id)
    return doc


@router.post("/import", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def import_document(
    background: BackgroundTasks,
    provider: str = Form(...),
    source_url: str = Form(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    doc = await ingest.from_drive(
        db,
        interview_id=None,
        doc_type=DOC_TYPE,
        provider=provider,
        file=file,
        source_url=source_url,
    )
    background.add_task(ingest.index_in_background, doc.id)
    return doc


@router.post("/paste", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def paste_document(
    payload: PasteDocument,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    doc = await ingest.from_paste(
        db,
        interview_id=None,
        doc_type=DOC_TYPE,
        content_text=payload.content_text,
        filename=payload.filename,
    )
    background.add_task(ingest.index_in_background, doc.id)
    return doc


@router.get("/{document_id}/content", response_model=DocumentContentOut)
async def read_document(
    document_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> InterviewDocument:
    return await _require_document(db, document_id)


@router.get("/{document_id}/download")
async def download_document(
    document_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> FileResponse:
    return ingest.file_response(await _require_document(db, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    document_id: str, db: AsyncSession = Depends(get_db), user: dict = HRUser
) -> None:
    doc = await _require_document(db, document_id)
    await db.delete(doc)
    await db.commit()


@router.post("/{document_id}/reindex", response_model=DocumentOut)
async def reindex_document(
    document_id: str,
    background: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    doc = await _require_document(db, document_id)
    doc.index_status = "pending"
    doc.index_error = None
    await db.commit()
    await db.refresh(doc)
    background.add_task(ingest.index_in_background, doc.id)
    return doc
