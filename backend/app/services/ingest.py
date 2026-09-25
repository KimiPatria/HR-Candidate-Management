"""Turning something HR handed us into an indexed document.

Three surfaces feed this - a file upload, pasted text, and a file picked out of Google
Drive or OneDrive - across two scopes: one interview's job requirements, and the
company-wide knowledge base. That is six routes over two routers, and all six do the
same three things: recover plain text, store the row, queue indexing. They do it here
once so they cannot drift apart, and so a change to how documents are stored is one edit
rather than six.
"""

import logging
import os
import uuid

from fastapi import HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Interview, InterviewDocument
from app.services import extract

log = logging.getLogger(__name__)

VALID_DOC_TYPES = {"job_requirement", "knowledge_base"}
# Where a document can come from. The two drive values are recorded rather than
# flattened into "upload" so the document list can say where a file actually came from,
# and so `source_url` has something to be read alongside.
VALID_PROVIDERS = {"google_drive", "onedrive"}


async def index_in_background(document_id: str) -> None:
    """Index one document, and for a job description carry straight on into the question
    plan and the rubric draft.

    BackgroundTasks runs after the response is sent, so the request-scoped session is
    already closed - open a fresh one.

    The chaining lives here rather than inside `rag.index_document` so that indexing
    stays a pure retrieval concern, and because this is the one place every ingest route
    passes through.

    The interview is marked `running` BEFORE indexing starts, not after it succeeds.
    Claiming it afterwards left a window - between the document reading "indexed" and the
    interview reading "running" - in which the setup page saw nothing in flight, stopped
    polling, and sat on an empty plan until somebody reloaded. From the upload response
    onwards the interview is now always in exactly one of running, done or failed.
    """
    from app.services import autopilot, rag

    async with SessionLocal() as db:
        doc = await db.get(InterviewDocument, document_id)
        if doc is None:
            return
        interview_id = doc.interview_id
        chains = interview_id is not None and doc.doc_type == "job_requirement"

        if chains:
            interview = await db.get(Interview, interview_id)
            if interview is not None:
                interview.autopilot_status = "running"
                interview.autopilot_step = "indexing"
                interview.autopilot_error = None
                await db.commit()

        await rag.index_document(db, document_id)
        await db.refresh(doc)
        indexed = doc.index_status == "indexed"
        index_error = doc.index_error

        if chains and not indexed:
            # Nothing downstream can run on a document that produced no text, and saying
            # so on the interview is what stops the page waiting for a plan that is never
            # coming.
            interview = await db.get(Interview, interview_id)
            if interview is not None:
                interview.autopilot_status = "failed"
                interview.autopilot_step = None
                interview.autopilot_error = f"Reading the document: {index_error}"[:1000]
                await db.commit()

    if chains and indexed and interview_id is not None:
        await autopilot.run(interview_id, claimed=True)


def _require_doc_type(doc_type: str) -> None:
    if doc_type not in VALID_DOC_TYPES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"doc_type must be one of {sorted(VALID_DOC_TYPES)}"
        )


def _store_original(filename: str, data: bytes) -> str:
    """Keep the bytes as uploaded alongside the extracted text.

    Two reasons, and the second is the one that matters day to day: a bad extraction is
    recoverable, and HR can open the document they actually uploaded when they come back
    to review an interview weeks later.
    """
    safe_name = f"{uuid.uuid4().hex}-{filename.replace('/', '_')[:80]}"
    stored = settings.upload_path / safe_name
    stored.write_bytes(data)
    return str(stored)


async def _from_bytes(
    db: AsyncSession,
    *,
    interview_id: str | None,
    doc_type: str,
    source: str,
    filename: str,
    data: bytes,
    source_url: str | None = None,
) -> InterviewDocument:
    _require_doc_type(doc_type)
    if len(data) > extract.MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"File exceeds {extract.MAX_BYTES // (1024 * 1024)}MB",
        )
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "File is empty")

    try:
        text = extract.extract_text(filename, data)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    doc = InterviewDocument(
        interview_id=interview_id,
        doc_type=doc_type,
        source=source,
        filename=filename,
        stored_path=_store_original(filename, data),
        source_url=source_url,
        content_text=text,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


async def from_upload(
    db: AsyncSession,
    *,
    interview_id: str | None,
    doc_type: str,
    file: UploadFile,
) -> InterviewDocument:
    return await _from_bytes(
        db,
        interview_id=interview_id,
        doc_type=doc_type,
        source="upload",
        filename=file.filename or "upload.txt",
        data=await file.read(),
    )


async def from_drive(
    db: AsyncSession,
    *,
    interview_id: str | None,
    doc_type: str,
    provider: str,
    file: UploadFile,
    source_url: str | None,
) -> InterviewDocument:
    """A file the browser pulled out of Google Drive or OneDrive.

    The bytes arrive as an ordinary multipart upload because the download happens in the
    browser, against the drive's own API, with the token the user just consented to.
    Nothing about that token reaches this process, and this process never fetches a URL
    somebody else chose - which is what keeps a "paste a link and we will fetch it"
    feature from becoming a request forgery primitive pointed at our own network.
    """
    if provider not in VALID_PROVIDERS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"provider must be one of {sorted(VALID_PROVIDERS)}",
        )
    return await _from_bytes(
        db,
        interview_id=interview_id,
        doc_type=doc_type,
        source=provider,
        filename=file.filename or "drive-file.txt",
        data=await file.read(),
        source_url=source_url or None,
    )


async def from_paste(
    db: AsyncSession,
    *,
    interview_id: str | None,
    doc_type: str,
    content_text: str,
    filename: str | None,
) -> InterviewDocument:
    _require_doc_type(doc_type)
    doc = InterviewDocument(
        interview_id=interview_id,
        doc_type=doc_type,
        source="pasted",
        filename=filename,
        content_text=content_text,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)
    return doc


def file_response(doc: InterviewDocument) -> FileResponse:
    """Hand back the original file, byte for byte. Shared by both document routers -
    the two failure modes are the same whichever scope asked."""
    if not doc.stored_path:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "This document was pasted as text, so there is no original file to download",
        )
    if not os.path.exists(doc.stored_path):
        raise HTTPException(
            status.HTTP_410_GONE, "The stored copy of this file is no longer on disk"
        )
    return FileResponse(
        doc.stored_path,
        filename=doc.filename or "document",
        media_type="application/octet-stream",
    )
