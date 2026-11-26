"""In-memory pub/sub used for live updates in the Mini App (SSE)."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subs: dict[int, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, uid: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=2000)
        self._subs[uid].add(q)
        return q

    def unsubscribe(self, uid: int, q: asyncio.Queue) -> None:
        self._subs[uid].discard(q)
        if not self._subs[uid]:
            self._subs.pop(uid, None)

    def online(self, uid: int) -> bool:
        return bool(self._subs.get(uid))

    def publish(self, uid: int, event: str, data: Any) -> None:
        for q in list(self._subs.get(uid, ())):
            try:
                q.put_nowait((event, data))
            except asyncio.QueueFull:
                pass

    def publish_many(self, uids: list[int], event: str, data: Any) -> None:
        for uid in uids:
            self.publish(uid, event, data)
