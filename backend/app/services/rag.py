"""Retrieval over per-interview documents.

Live mode talks to VDB KnowledgeBase. Whenever that is not configured (no
VDB_KB_API_KEY) - which is the only mode this project actually runs in today - indexing
and search both use a local vector store: chunks are embedded on-device (see
app/services/embeddings.py) and persisted as DocumentChunk rows, then searched by cosine
similarity. A lexical fallback covers the case where the embedding model itself cannot be
loaded (e.g. no network for its one-time download).

VERIFY: VDB KnowledgeBase endpoint paths and payload shapes below are placeholders.
"""

import asyncio
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import DocumentChunk, InterviewDocument
from app.services import embeddings

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


COMPANY_COLLECTION = "company-knowledge-base"


def collection_name(interview_id: str | None, doc_type: str) -> str:
    """Company knowledge (interview_id None) lives in one collection shared by every
    interview; job requirements get a collection per interview."""
    if interview_id is None:
        return COMPANY_COLLECTION
    return f"interview-{interview_id}-{doc_type.replace('_', '-')}"


async def _text_to_index(db: AsyncSession, doc: InterviewDocument) -> str:
    """The text that actually gets chunked and embedded for this document.

    Normally that is `doc.content_text` verbatim. When TRANSLATE_INDEX_LANGUAGE is set,
    non-matching documents are translated into that language first.

    This is a retrieval-quality fix rather than a convenience. `embeddings.py` runs
    BAAI/bge-small-en-v1.5, an English-tuned model: an Indonesian document embeds into
    roughly the wrong region of the space, so it scores poorly against an English query
    and well against nothing in particular. Normalising the corpus to one language before
    embedding is what makes a bilingual knowledge base retrievable at all.

    The consequence is that retrieval then hands the interviewer English snippets while
    the interview runs in Indonesian. That is fine and needs no prompt change: retrieved
    context is model input, never spoken verbatim, and prompts.py already pins the spoken
    language. It is called out here because it looks like a bug to the next reader.

    Any failure falls back to indexing the document as uploaded - degraded retrieval is a
    great deal better than a document that will not index at all.
    """
    from app.services import translate, translation_store

    target = (settings.translate_index_language or "").strip()
    if not target:
        return doc.content_text

    detected, confidence = await translate.detect_language(doc.content_text)
    if detected is None:
        log.info(
            "Document %s: language undetermined, indexing as uploaded", doc.id
        )
        return doc.content_text
    if detected == target:
        return doc.content_text

    try:
        row = await translation_store.document_translation(
            db, doc.id, target, source=detected, used_for_indexing=True
        )
    except Exception:  # noqa: BLE001 - indexing the original beats not indexing at all
        log.exception(
            "Document %s: %s -> %s translation failed, indexing as uploaded",
            doc.id,
            detected,
            target,
        )
        return doc.content_text

    if row is None:
        return doc.content_text
    log.info(
        "Document %s: indexing the %s translation (detected %s, confidence %.2f)",
        doc.id,
        target,
        detected,
        confidence,
    )
    return row.text


async def index_document(db: AsyncSession, document_id: str) -> None:
    """Chunk and index one document. Runs as a BackgroundTask; every exit path writes a
    terminal index_status so the setup page never shows a permanently spinning document.

    Always builds the local vector store, even when the live KnowledgeBase is also
    configured: search() falls back to it on any live-search failure, so it must stay
    populated regardless of which path indexing itself took.
    """
    doc = await db.get(InterviewDocument, document_id)
    if doc is None:
        return
    doc.index_status = "indexing"
    await db.commit()

    try:
        text = await _text_to_index(db, doc)
        chunks = chunk_text(text)
        if not chunks:
            raise ValueError("Document produced no text to index")
        ref = collection_name(doc.interview_id, doc.doc_type)
        if not settings.mock_ai and settings.vdb_kb_api_key:
            await _kb_upsert(ref, doc.id, chunks)
        await _local_index(db, doc, chunks)
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


async def _local_index(db: AsyncSession, doc: InterviewDocument, chunks: list[str]) -> None:
    """(Re)build this document's rows in the local vector store."""
    await db.execute(delete(DocumentChunk).where(DocumentChunk.document_id == doc.id))
    # CPU-bound ONNX inference - off the event loop so it cannot stall other candidates'
    # live voice sessions running concurrently.
    vectors = await asyncio.to_thread(embeddings.embed_passages, chunks)
    for i, (text, vector) in enumerate(zip(chunks, vectors)):
        db.add(
            DocumentChunk(
                document_id=doc.id,
                interview_id=doc.interview_id,
                doc_type=doc.doc_type,
                chunk_index=i,
                text=text,
                embedding=embeddings.to_bytes(vector),
            )
        )


async def search(
    db: AsyncSession,
    interview_id: str,
    query: str,
    *,
    doc_type: str | None = None,
    top_k: int = 4,
) -> list[RetrievedChunk]:
    # Retrieval sits on the interview's hot path: the candidate is waiting in silence
    # while it runs. Attempting a KnowledgeBase call with no credentials configured cost
    # a measured 2.3s per turn - a connection attempt that could only ever fail - against
    # a local vector search that returns in tens of milliseconds. Skipping it outright is
    # the single cheapest latency win available here, and costs nothing: without a key
    # the remote call has no chance of succeeding anyway.
    if settings.mock_ai or not settings.vdb_kb_api_key:
        return await _vector_search(db, interview_id, query, doc_type=doc_type, top_k=top_k)
    try:
        return await _kb_search(interview_id, query, doc_type=doc_type, top_k=top_k)
    except Exception:  # noqa: BLE001
        # A retrieval outage must not end a live interview - fall back to local search.
        log.exception("KnowledgeBase search failed; falling back to local search")
        return await _vector_search(db, interview_id, query, doc_type=doc_type, top_k=top_k)


def confidence(chunks: list[RetrievedChunk]) -> float:
    return max((c.score for c in chunks), default=0.0)


# --------------------------------------------------------------------------- local

# Raw cosine similarity from this embedding model does not land in the same range
# TF-IDF's score did: even unrelated passages typically score ~0.4-0.5 with bge-small,
# so guardrails.LOW_CONFIDENCE_THRESHOLD (tuned for the old lexical score) would rarely
# fire against a raw cosine value. These are a starting estimate to bring scores back
# into a comparable 0..1 range, not a measured calibration.
# VERIFY: re-check both this rescale and LOW_CONFIDENCE_THRESHOLD once real interview
# documents/questions are available to tune against.
_COSINE_FLOOR = 0.4
_COSINE_CEIL = 0.9


def _normalize_cosine(raw: float) -> float:
    return max(0.0, min(1.0, (raw - _COSINE_FLOOR) / (_COSINE_CEIL - _COSINE_FLOOR)))


def _in_scope(column, interview_id: str) -> object:
    """Rows this interview can retrieve: its own, plus everything company-wide.

    A NULL interview_id is the company knowledge base - static facts that apply to every
    position - so it is in scope for all of them. Written once and shared by both local
    search paths, because the two silently disagreeing is exactly the bug that would
    present as "the interviewer knows the company on some questions and not others".
    """
    return or_(column == interview_id, column.is_(None))


async def _vector_search(
    db: AsyncSession,
    interview_id: str,
    query: str,
    *,
    doc_type: str | None,
    top_k: int,
) -> list[RetrievedChunk]:
    stmt = select(DocumentChunk).where(_in_scope(DocumentChunk.interview_id, interview_id))
    if doc_type:
        stmt = stmt.where(DocumentChunk.doc_type == doc_type)
    rows = (await db.execute(stmt)).scalars().all()
    if not rows:
        return []

    try:
        # CPU-bound ONNX inference - off the event loop, same reasoning as indexing.
        query_vector = await asyncio.to_thread(embeddings.embed_query, query)
    except Exception:  # noqa: BLE001
        # Model failed to load (e.g. no network for its one-time download) - degrade to
        # keyword search rather than going silent on retrieval for the whole interview.
        log.exception("Local embedding model unavailable; falling back to lexical search")
        return await _lexical_search(db, interview_id, query, doc_type=doc_type, top_k=top_k)

    scored = [
        RetrievedChunk(
            text=row.text,
            score=_normalize_cosine(
                embeddings.cosine_similarity(query_vector, embeddings.from_bytes(row.embedding))
            ),
            doc_type=row.doc_type,
            doc_id=row.document_id,
        )
        for row in rows
    ]
    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:top_k]


async def _lexical_search(
    db: AsyncSession,
    interview_id: str,
    query: str,
    *,
    doc_type: str | None,
    top_k: int,
) -> list[RetrievedChunk]:
    """Keyword/TF-IDF fallback for when the embedding model itself is unavailable."""
    stmt = select(InterviewDocument).where(
        _in_scope(InterviewDocument.interview_id, interview_id)
    )
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
            # Knowledge base is company-wide, so it is looked up in the shared collection
            # rather than one named after this interview.
            scope = None if dt == "knowledge_base" else interview_id
            resp = await client.post(
                f"{settings.vdb_kb_base_url}/api/knowledge/collection/search_knowledge",
                headers=_kb_headers(),
                json={
                    "collection_name": collection_name(scope, dt),
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
