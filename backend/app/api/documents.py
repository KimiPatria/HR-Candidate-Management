"""Job-requirement documents, scoped to one interview.

Upload, drive-import and paste are separate routes because two are multipart and one is
JSON - FastAPI cannot accept both shapes on a single path. All three converge on
`services/ingest.py`, and from there on the same indexing path.

Company knowledge-base documents are NOT here: they belong to the company rather than to
any one position, and live in `app/api/knowledge.py` against the same tables with a null
interview_id.
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
from app.models import Interview, InterviewDocument
from app.schemas import DocumentContentOut, DocumentOut, PasteDocument
from app.services import ingest

log = logging.getLogger(__name__)
router = APIRouter(prefix="/interviews/{interview_id}/documents", tags=["documents"])


def _require_job_requirement(doc_type: str) -> None:
    """Only job requirements are scoped to an interview.

    Knowledge base used to be accepted here too, and that is exactly what produced the
    same company handbook uploaded once per position with no single place to correct it.
    Refusing it at the door keeps the old shape from creeping back in through a stale
    client, and the error says where it went.
    """
    if doc_type != "job_requirement":
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Only job-requirement documents belong to an interview. Company knowledge "
            "is shared across every interview - add it under /knowledge/documents.",
        )


async def _require_interview(db: AsyncSession, interview_id: str) -> Interview:
    interview = await db.get(Interview, interview_id)
    if interview is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Interview not found")
    return interview


async def _require_document(
    db: AsyncSession, interview_id: str, document_id: str
) -> InterviewDocument:
    doc = await db.get(InterviewDocument, document_id)
    if doc is None or doc.interview_id != interview_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Document not found")
    return doc


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
    _require_job_requirement(doc_type)
    doc = await ingest.from_upload(
        db, interview_id=interview_id, doc_type=doc_type, file=file
    )
    background.add_task(ingest.index_in_background, doc.id)
    return doc


@router.post("/import", response_model=DocumentOut, status_code=status.HTTP_201_CREATED)
async def import_document(
    interview_id: str,
    background: BackgroundTasks,
    doc_type: str = Form(...),
    provider: str = Form(...),
    source_url: str = Form(""),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    """A file picked out of Google Drive or OneDrive. See `ingest.from_drive` for why the
    bytes arrive as a plain upload rather than as a URL for us to fetch."""
    await _require_interview(db, interview_id)
    _require_job_requirement(doc_type)
    doc = await ingest.from_drive(
        db,
        interview_id=interview_id,
        doc_type=doc_type,
        provider=provider,
        file=file,
        source_url=source_url,
    )
    background.add_task(ingest.index_in_background, doc.id)
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
    _require_job_requirement(payload.doc_type)
    doc = await ingest.from_paste(
        db,
        interview_id=interview_id,
        doc_type=payload.doc_type,
        content_text=payload.content_text,
        filename=payload.filename,
    )
    background.add_task(ingest.index_in_background, doc.id)
    return doc


@router.get("/{document_id}/content", response_model=DocumentContentOut)
async def read_document(
    interview_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> InterviewDocument:
    """The text that was actually chunked and embedded.

    Indexing is not a shredder: the extracted text is kept on the row, and this is what
    lets HR re-read the job description behind a question plan months later instead of
    inferring it from the questions.
    """
    return await _require_document(db, interview_id, document_id)


@router.get("/{document_id}/download")
async def download_document(
    interview_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> FileResponse:
    """The original file, byte for byte. Only exists for uploads and drive imports -
    pasted text was never a file."""
    return ingest.file_response(await _require_document(db, interview_id, document_id))


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_document(
    interview_id: str,
    document_id: str,
    db: AsyncSession = Depends(get_db),
    user: dict = HRUser,
) -> None:
    doc = await _require_document(db, interview_id, document_id)
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
    doc = await _require_document(db, interview_id, document_id)
    doc.index_status = "pending"
    doc.index_error = None
    await db.commit()
    await db.refresh(doc)
    background.add_task(ingest.index_in_background, doc.id)
    return doc
