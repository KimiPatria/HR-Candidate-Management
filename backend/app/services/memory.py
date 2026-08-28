"""Conversation memory.

Live mode writes to VikingDB Memory so long interviews stay coherent without replaying
the whole transcript into every prompt. Mock mode reads the recent turns straight out of
TranscriptTurn, which is also the fallback whenever the memory service is unreachable -
an interview should degrade to "shorter memory", never to a failed turn.

VERIFY: VikingDB Memory endpoint paths and payload shapes below are placeholders.
"""

import logging

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models import TranscriptTurn

log = logging.getLogger(__name__)

RECENT_TURN_LIMIT = 12


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.vikingdb_memory_api_key}",
        "Content-Type": "application/json",
    }


async def recall(db: AsyncSession, session_id: str, query: str) -> list[dict]:
    """Return prior conversation as OpenAI-style messages, oldest first."""
    if not settings.mock_ai and settings.vikingdb_memory_api_key:
        try:
            return await _remote_recall(session_id, query)
        except Exception:  # noqa: BLE001
            log.exception("Memory recall failed for session %s; using local turns", session_id)
    return await _local_recall(db, session_id)


async def remember(session_id: str, speaker: str, text: str) -> None:
    """Best-effort write. Never raises: the turn is already persisted in TranscriptTurn,
    so a memory-service failure must not surface to the candidate."""
    if settings.mock_ai or not settings.vikingdb_memory_api_key:
        return
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{settings.vikingdb_memory_base_url}/api/memory/messages/add",
                headers=_headers(),
                json={
                    "session_id": session_id,
                    "messages": [
                        {"role": "assistant" if speaker == "ai" else "user", "content": text}
                    ],
                },
            )
            resp.raise_for_status()
    except Exception:  # noqa: BLE001
        log.exception("Memory write failed for session %s", session_id)


async def _local_recall(db: AsyncSession, session_id: str) -> list[dict]:
    stmt = (
        select(TranscriptTurn)
        .where(TranscriptTurn.session_id == session_id)
        .order_by(TranscriptTurn.turn_index.desc())
        .limit(RECENT_TURN_LIMIT)
    )
    turns = list((await db.execute(stmt)).scalars().all())
    turns.reverse()
    return [
        {"role": "assistant" if t.speaker == "ai" else "user", "content": t.text}
        for t in turns
        if t.speaker in ("ai", "candidate")
    ]


async def _remote_recall(session_id: str, query: str) -> list[dict]:
    async with httpx.AsyncClient(timeout=5.0) as client:
        resp = await client.post(
            f"{settings.vikingdb_memory_base_url}/api/memory/search",
            headers=_headers(),
            json={"session_id": session_id, "query": query, "limit": RECENT_TURN_LIMIT},
        )
        resp.raise_for_status()
        items = resp.json().get("data", {}).get("result_list", [])
    return [
        {"role": item.get("role", "user"), "content": item.get("content", "")}
        for item in items
        if item.get("content")
    ]
