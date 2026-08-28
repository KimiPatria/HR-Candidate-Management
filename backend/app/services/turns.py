"""Single write path for transcript turns.

Everything that produces a turn - the LLM endpoint, the RTC subtitle callback, session
lifecycle events - goes through here, so turn_index stays consistent, live subscribers
always see the same stream, and memory writes are never forgotten.
"""

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TranscriptTurn
from app.services import memory, transcript_hub


async def record_turn(
    db: AsyncSession,
    session_id: str,
    speaker: str,
    text: str,
    *,
    plan_item_id: str | None = None,
    guardrail_action: str | None = None,
) -> TranscriptTurn:
    next_index = (
        await db.execute(
            select(func.coalesce(func.max(TranscriptTurn.turn_index), -1) + 1).where(
                TranscriptTurn.session_id == session_id
            )
        )
    ).scalar_one()

    turn = TranscriptTurn(
        session_id=session_id,
        turn_index=next_index,
        speaker=speaker,
        text=text,
        plan_item_id=plan_item_id,
        guardrail_action=guardrail_action,
        timestamp=datetime.now(timezone.utc),
    )
    db.add(turn)
    await db.commit()
    await db.refresh(turn)

    transcript_hub.publish(
        session_id,
        {
            "type": "turn",
            "id": turn.id,
            "turn_index": turn.turn_index,
            "speaker": turn.speaker,
            "text": turn.text,
            "guardrail_action": turn.guardrail_action,
            "timestamp": turn.timestamp.isoformat(),
        },
    )
    await memory.remember(session_id, speaker, text)
    return turn
