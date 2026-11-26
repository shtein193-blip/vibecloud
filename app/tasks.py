"""Fire-and-forget tasks that are not lost to the garbage collector and always log their errors."""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from typing import Any, Coroutine, Generic, TypeVar

log = logging.getLogger(__name__)
_tasks: set[asyncio.Task] = set()


def _done(t: asyncio.Task) -> None:
    _tasks.discard(t)
    if not t.cancelled() and t.exception():
        log.warning("background task %s failed: %r", t.get_name(), t.exception())


def spawn(coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task:
    """asyncio.create_task that keeps a strong reference until the task finishes."""
    t = asyncio.create_task(coro, name=name)
    _tasks.add(t)
    t.add_done_callback(_done)
    return t


K = TypeVar("K")
V = TypeVar("V")


class TTLCache(Generic[K, V]):
    """Small bounded dict with expiry — for pending choices, rate limits and similar in-memory state."""

    def __init__(self, maxsize: int = 500, ttl: float = 3600):
        self.maxsize, self.ttl = maxsize, ttl
        self._d: OrderedDict[K, tuple[float, V]] = OrderedDict()

    def get(self, key: K, default: V | None = None) -> V | None:
        hit = self._d.get(key)
        if not hit:
            return default
        if hit[0] < time.monotonic():
            self._d.pop(key, None)
            return default
        return hit[1]

    def __setitem__(self, key: K, value: V) -> None:
        self._d[key] = (time.monotonic() + self.ttl, value)
        self._d.move_to_end(key)
        while len(self._d) > self.maxsize:
            self._d.popitem(last=False)

    def __contains__(self, key: K) -> bool:
        return self.get(key) is not None

    def pop(self, key: K, default: V | None = None) -> V | None:
        hit = self._d.pop(key, None)
        return hit[1] if hit and hit[0] >= time.monotonic() else default

    def __len__(self) -> int:
        return len(self._d)
