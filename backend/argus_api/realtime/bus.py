"""Event fan-out: fusion / ingest → Redis pub/sub → every connected WebSocket.

Two implementations behind one interface. ``RedisBus`` is what a split deployment uses
(ingest worker, fusion worker and API in separate processes). ``MemoryBus`` is for a single
process — tests, and the one-command demo — and needs no infrastructure.

Publishing is synchronous and callable from any thread (the workers are not async);
subscribing is async (the WebSocket handler is).
"""

from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any, Protocol

import structlog

from argus_api.core.config import get_settings

log = structlog.get_logger()

LIVE_CHANNEL = "argus:live"
FUSION_WAKE_KEY = "argus:fusion:wake"

EVENT_TYPES = {"asset.created", "asset.updated", "asset.resolved", "incident", "telemetry"}


def envelope(event_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"type": event_type, "at": datetime.now(UTC).isoformat(), "data": data}


class EventBus(Protocol):
    def publish(self, event_type: str, data: dict[str, Any]) -> None: ...
    def subscribe(self) -> AsyncIterator[dict[str, Any]]: ...
    def wake_fusion(self) -> None: ...
    def wait_for_fusion_work(self, timeout_s: float) -> bool: ...


class MemoryBus:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._subs: list[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = []
        self._wake = threading.Event()
        self.published: list[dict[str, Any]] = []  # bounded tail, handy in tests

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        msg = envelope(event_type, data)
        with self._lock:
            self.published.append(msg)
            del self.published[:-500]
            subs = list(self._subs)
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(q.put_nowait, msg)
            except RuntimeError:  # loop closed; the subscriber is gone
                pass

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        entry = (asyncio.get_running_loop(), q)
        with self._lock:
            self._subs.append(entry)
        try:
            while True:
                yield await q.get()
        finally:
            with self._lock:
                self._subs.remove(entry)

    def wake_fusion(self) -> None:
        self._wake.set()

    def wait_for_fusion_work(self, timeout_s: float) -> bool:
        woke = self._wake.wait(timeout_s)
        self._wake.clear()
        return woke


class RedisBus:
    def __init__(self, url: str) -> None:
        import redis

        self._url = url
        self._sync = redis.Redis.from_url(url)

    def publish(self, event_type: str, data: dict[str, Any]) -> None:
        try:
            self._sync.publish(LIVE_CHANNEL, json.dumps(envelope(event_type, data), default=str))
        except Exception as exc:  # realtime is best-effort; the DB is the record
            log.warning("bus.publish_failed", error=str(exc))

    async def subscribe(self) -> AsyncIterator[dict[str, Any]]:
        import redis.asyncio as aioredis

        client = aioredis.Redis.from_url(self._url)
        pubsub = client.pubsub()
        await pubsub.subscribe(LIVE_CHANNEL)
        try:
            async for msg in pubsub.listen():
                if msg.get("type") == "message":
                    yield json.loads(msg["data"])
        finally:
            await pubsub.unsubscribe(LIVE_CHANNEL)
            await client.aclose()

    def wake_fusion(self) -> None:
        try:
            pipe = self._sync.pipeline()
            pipe.lpush(FUSION_WAKE_KEY, "1")
            pipe.ltrim(FUSION_WAKE_KEY, 0, 0)  # one pending wake is as good as a thousand
            pipe.execute()
        except Exception as exc:
            log.warning("bus.wake_failed", error=str(exc))

    def wait_for_fusion_work(self, timeout_s: float) -> bool:
        try:
            return self._sync.blpop([FUSION_WAKE_KEY], timeout=max(1, int(timeout_s))) is not None
        except Exception:
            return False


_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_bus() -> EventBus:
    global _bus
    with _bus_lock:
        if _bus is None:
            url = get_settings().redis_url
            _bus = RedisBus(url) if url else MemoryBus()
        return _bus


def set_bus(bus: EventBus | None) -> None:
    """Test hook."""
    global _bus
    with _bus_lock:
        _bus = bus
