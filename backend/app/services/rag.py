"""Retrieval over per-interview documents.

Live mode talks to VDB KnowledgeBase. Mock mode runs a local lexical search over the
document text already stored in Postgres/SQLite - deliberately real enough that the
guardrail confidence thresholds and prompt assembly can be tuned before credentials land.

VERIFY: VDB KnowledgeBase endpoint paths and payload shapes below are placeholders.
"""

import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import InterviewDocument

log = logging.getLogger(__name__)

CHUNK_SIZE = 800
CHUNK_OVERLAP = 120
_WORD = re.compile(r"[a-z0-9]+")


@dataclass
class RetrievedChunk:
    text: str
    score: float
    doc_type: str
    doc_id: str


def chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Paragraph-aware chunking: pack whole paragraphs up to `size`, only hard-splitting
    a paragraph that exceeds it on its own."""
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > size:
            if buf:
                chunks.append(buf)
                buf = ""
            for i in range(0, len(para), size - overlap):
                chunks.append(para[i : i + size])
            continue
        if len(buf) + len(para) + 2 > size:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def collection_name(interview_id: str, doc_type: str) -> str:
    return f"interview-{interview_id}-{doc_type.replace('_', '-')}"


async def index_document(db: AsyncSession, document_id: str) -> None:
    """Chunk and index one document. Runs as a BackgroundTask; every exit path writes a
    terminal index_status so the setup page never shows a permanently spinning document."""
    doc = await db.get(InterviewDocument, document_id)
    if doc is None:
        return
    doc.index_status = "indexing"
    await db.commit()

    try:
        chunks = chunk_text(doc.content_text)
        if not chunks:
            raise ValueError("Document produced no text to index")
        ref = collection_name(doc.interview_id, doc.doc_type)
        if not settings.mock_ai:
            await _kb_upsert(ref, doc.id, chunks)
        doc.vdb_collection_ref = ref
        doc.chunk_count = len(chunks)
        doc.index_status = "indexed"
        doc.indexed_at = datetime.now(timezone.utc)
        doc.index_error = None
    except Exception as exc:  # noqa: BLE001 - surface any failure to the HR page
        log.exception("Indexing failed for document %s", document_id)
        doc.index_status = "failed"
        doc.index_error = str(exc)[:1000]
    await db.commit()


async def search(
    db: AsyncSession,
    interview_id: str,
    query: str,
    *,
    doc_type: str | None = None,
    top_k: int = 4,
) -> list[RetrievedChunk]:
    if settings.mock_ai:
        return await _local_search(db, interview_id, query, doc_type=doc_type, top_k=top_k)
    try:
        return await _kb_search(interview_id, query, doc_type=doc_type, top_k=top_k)
    except Exception:  # noqa: BLE001
        # A retrieval outage must not end a live interview - fall back to local text.
        log.exception("KnowledgeBase search failed; falling back to local search")
        return await _local_search(db, interview_id, query, doc_type=doc_type, top_k=top_k)


def confidence(chunks: list[RetrievedChunk]) -> float:
    return max((c.score for c in chunks), default=0.0)


# --------------------------------------------------------------------------- local


async def _local_search(
    db: AsyncSession,
    interview_id: str,
    query: str,
    *,
    doc_type: str | None,
    top_k: int,
) -> list[RetrievedChunk]:
    stmt = select(InterviewDocument).where(InterviewDocument.interview_id == interview_id)
    if doc_type:
        stmt = stmt.where(InterviewDocument.doc_type == doc_type)
    docs = (await db.execute(stmt)).scalars().all()

    corpus: list[tuple[list[str], str, InterviewDocument]] = []
    for doc in docs:
        for chunk in chunk_text(doc.content_text):
            corpus.append((_WORD.findall(chunk.lower()), chunk, doc))
    if not corpus:
        return []

    q_terms = set(_WORD.findall(query.lower()))
    if not q_terms:
        return []

    # Document frequency per query term, so common words do not dominate the ranking.
    df = {term: sum(1 for terms, _, _ in corpus if term in terms) for term in q_terms}
    n = len(corpus)

    scored: list[RetrievedChunk] = []
    for terms, chunk, doc in corpus:
        if not terms:
            continue
        counts: dict[str, int] = {}
        for t in terms:
            counts[t] = counts.get(t, 0) + 1
        score = 0.0
        for term in q_terms:
            tf = counts.get(term, 0)
            if not tf:
                continue
            idf = math.log((n + 1) / (df.get(term, 0) + 1)) + 1.0
            score += (tf / len(terms)) * idf
        if score > 0:
            scored.append(
                RetrievedChunk(text=chunk, score=score, doc_type=doc.doc_type, doc_id=doc.id)
            )

    scored.sort(key=lambda c: c.score, reverse=True)
    top = scored[:top_k]
    # Squash into 0..1 so `confidence` thresholds mean roughly the same thing in both
    # modes. 0.15 raw is treated as a solid lexical match.
    for c in top:
        c.score = min(1.0, c.score / 0.15)
    return top


# ------------------------------------------------------------------------ live KB


def _kb_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.vdb_kb_api_key}",
        "Content-Type": "application/json",
    }


async def _kb_upsert(collection: str, doc_id: str, chunks: list[str]) -> None:
    payload = {
        "collection_name": collection,
        "fields": [
            {"doc_id": f"{doc_id}-{i}", "content": chunk} for i, chunk in enumerate(chunks)
        ],
    }
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{settings.vdb_kb_base_url}/api/knowledge/doc/add",
            headers=_kb_headers(),
            json=payload,
        )
        resp.raise_for_status()


async def _kb_search(
    interview_id: str, query: str, *, doc_type: str | None, top_k: int
) -> list[RetrievedChunk]:
    types = [doc_type] if doc_type else ["job_requirement", "knowledge_base"]
    out: list[RetrievedChunk] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        for dt in types:
            resp = await client.post(
                f"{settings.vdb_kb_base_url}/api/knowledge/collection/search_knowledge",
                headers=_kb_headers(),
                json={
                    "collection_name": collection_name(interview_id, dt),
                    "query": query,
                    "limit": top_k,
                },
            )
            resp.raise_for_status()
            for item in resp.json().get("data", {}).get("result_list", []):
                out.append(
                    RetrievedChunk(
                        text=item.get("content", ""),
                        score=float(item.get("score", 0.0)),
                        doc_type=dt,
                        doc_id=item.get("doc_id", ""),
                    )
                )
    out.sort(key=lambda c: c.score, reverse=True)
    return out[:top_k]
