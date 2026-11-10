"""A shared, short-lived HTTP cache in front of any `Http`.

Several locations, or several users of a server, often need the same
answer: the radar index, the radar tiles around them, the daily snow map.
This layer asks once and hands the same answer to everyone who asks within
its lifetime. Two requests for the same URL that overlap in time are merged
into one, so locations that update together send one request, not two.

It never changes what is sent: the cache key is the full URL with its
parameters, so a location only ever gets an answer to exactly the request
it would have sent itself. Errors are not cached, so a failure is retried
by the next caller. Memory is bounded: entries expire, and the least
recently used ones go first when the byte budget is reached.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .safety import MAX_IMAGE_BYTES, MAX_JSON_BYTES, MAX_TEXT_BYTES
from .sources.base import Http

DEFAULT_TTL = 120.0  # seconds: merges the updates of locations that run together
# Answers that never change once published may be kept longer.
LONG_TTL: list[tuple[str, float]] = [
    ("https://tilecache.rainviewer.com/v2/radar/", 3 * 3600.0),  # a tile of one past frame
    ("https://tilecache.rainviewer.com/v2/coverage/", 12 * 3600.0),
    ("https://www.rainviewer.com/files/", 12 * 3600.0),  # the colour table
    # Searches: the same text is not asked again for a day (Nominatim asks
    # applications to cache).
    ("https://geocoding-api.open-meteo.com/", 24 * 3600.0),
    ("https://nominatim.openstreetmap.org/", 24 * 3600.0),
]
SUFFIX_TTL: list[tuple[str, float]] = [
    (".asc.gz", 24 * 3600.0),  # one day's snow map: the file name holds the date
]
MAX_BYTES = 24 * 1024 * 1024
JSON_COST = 64 * 1024  # a parsed answer's size is not known; count each as this much


def ttl_for(url: str) -> float:
    for prefix, ttl in LONG_TTL:
        if url.startswith(prefix):
            return ttl
    for suffix, ttl in SUFFIX_TTL:
        if url.endswith(suffix):
            return ttl
    return DEFAULT_TTL


@dataclass
class _Entry:
    value: Any
    expires: float
    cost: int


class CachingHttp:
    """`Http` with a shared cache. Pass the same instance to every location."""

    def __init__(self, inner: Http, clock: Callable[[], float] = time.monotonic, max_bytes: int = MAX_BYTES) -> None:
        self._inner = inner
        self._clock = clock
        self._max_bytes = max_bytes
        self._entries: OrderedDict[tuple, _Entry] = OrderedDict()
        self._inflight: dict[tuple, asyncio.Task] = {}
        self._bytes = 0
        self.hits = 0
        self.misses = 0

    async def get_json(self, url: str, params: dict[str, str] | None = None, limit: int = MAX_JSON_BYTES) -> Any:
        value = await self._get("json", url, params, limit, lambda: self._inner.get_json(url, params, limit))
        return copy.deepcopy(value)  # parsed answers are mutable: every caller gets its own copy

    async def get_bytes(self, url: str, params: dict[str, str] | None = None, limit: int = MAX_IMAGE_BYTES) -> bytes:
        return await self._get("bytes", url, params, limit, lambda: self._inner.get_bytes(url, params, limit))

    async def get_text(self, url: str, params: dict[str, str] | None = None, limit: int = MAX_TEXT_BYTES) -> str:
        return await self._get("text", url, params, limit, lambda: self._inner.get_text(url, params, limit))

    async def _get(self, kind: str, url: str, params: dict[str, str] | None, limit: int,
                   fetch: Callable[[], Awaitable[Any]]) -> Any:
        key = (kind, url, tuple(sorted((params or {}).items())), limit)
        now = self._clock()
        entry = self._entries.get(key)
        if entry is not None:
            if entry.expires > now:
                self._entries.move_to_end(key)
                self.hits += 1
                return entry.value
            self._drop(key)
        task = self._inflight.get(key)
        if task is None:
            self.misses += 1
            task = asyncio.ensure_future(fetch())
            self._inflight[key] = task
            task.add_done_callback(lambda t, k=key, u=url: self._finished(k, u, t))
        else:
            self.hits += 1
        # shield: one caller giving up must not cancel the request for the others
        return await asyncio.shield(task)

    def _finished(self, key: tuple, url: str, task: asyncio.Task) -> None:
        self._inflight.pop(key, None)
        if task.cancelled() or task.exception() is not None:
            return  # errors are never cached
        value = task.result()
        if value is None:
            return  # "nothing for this area" (404) is asked again next time
        cost = len(value) if isinstance(value, (bytes, str)) else JSON_COST
        if cost > self._max_bytes // 4:
            return
        self._entries[key] = _Entry(value, self._clock() + ttl_for(url), cost)
        self._bytes += cost
        while self._bytes > self._max_bytes and self._entries:
            self._drop(next(iter(self._entries)))

    def _drop(self, key: tuple) -> None:
        entry = self._entries.pop(key, None)
        if entry is not None:
            self._bytes -= entry.cost

    def stats(self) -> dict[str, int]:
        return {"entries": len(self._entries), "bytes": self._bytes, "hits": self.hits, "misses": self.misses}
