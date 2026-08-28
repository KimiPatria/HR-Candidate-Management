"""In-process fan-out of live transcript turns to connected WebSocket clients.

Scope limit: this is per-process. Running uvicorn with more than one worker means a
subscriber on worker A never sees a turn published on worker B. Moving to multiple
workers requires swapping this for Redis pub/sub - the interface here is deliberately
small enough to make that a single-file change.
"""

import asyncio
import logging
from collections import defaultdict

log = logging.getLogger(__name__)

_subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)
QUEUE_MAXSIZE = 100


def subscribe(session_id: str) -> asyncio.Queue:
    queue: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)
    _subscribers[session_id].add(queue)
    return queue


def unsubscribe(session_id: str, queue: asyncio.Queue) -> None:
    subs = _subscribers.get(session_id)
    if not subs:
        return
    subs.discard(queue)
    if not subs:
        _subscribers.pop(session_id, None)


def publish(session_id: str, event: dict) -> None:
    """Never blocks. A slow client loses events rather than stalling the interview."""
    for queue in list(_subscribers.get(session_id, ())):
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            log.warning("Dropping transcript event for slow subscriber on %s", session_id)
