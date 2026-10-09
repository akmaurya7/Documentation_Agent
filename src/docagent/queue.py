"""Queue adapters for durable production work and deterministic tests."""

from __future__ import annotations

import asyncio
import json
from typing import Protocol

from .store import Run


class RunQueue(Protocol):
    """Minimal queue contract used by the webhook receiver and worker."""

    async def enqueue(self, run: Run) -> None:
        """Put a run on the queue."""

    async def dequeue(self, timeout_seconds: int = 5) -> Run | None:
        """Get a run, or return none when no item is available."""


class LocalRunQueue:
    """In-memory queue for tests and single-process development."""

    def __init__(self) -> None:
        self._items: asyncio.Queue[Run] = asyncio.Queue()

    async def enqueue(self, run: Run) -> None:
        await self._items.put(run)

    async def dequeue(self, timeout_seconds: int = 5) -> Run | None:
        try:
            return await asyncio.wait_for(self._items.get(), timeout_seconds)
        except TimeoutError:
            return None


class RedisRunQueue:
    """Redis list queue. Redis is imported only when this adapter is used."""

    def __init__(self, url: str, name: str = "docagent:runs") -> None:
        self.url = url
        self.name = name

    async def enqueue(self, run: Run) -> None:
        from redis.asyncio import Redis

        client = Redis.from_url(self.url, decode_responses=True)
        try:
            await client.rpush(self.name, json.dumps(run.__dict__))
        finally:
            await client.aclose()

    async def dequeue(self, timeout_seconds: int = 5) -> Run | None:
        from redis.asyncio import Redis

        client = Redis.from_url(self.url, decode_responses=True)
        try:
            result = await client.blpop(self.name, timeout=timeout_seconds)
        finally:
            await client.aclose()
        if result is None:
            return None
        payload = json.loads(result[1])
        return Run(**payload)
