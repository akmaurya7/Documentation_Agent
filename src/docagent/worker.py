"""Fail-closed worker orchestration for persisted documentation runs."""

from __future__ import annotations

from typing import Protocol

from .queue import RunQueue
from .store import Run, RunStatus, RunStore


class RunHandler(Protocol):
    """Contract for the future GitHub/context/LLM orchestration layer."""

    async def process(self, run: Run) -> RunStatus:
        """Process a run and return a terminal state."""


class FailClosedHandler:
    """Safe default until all external adapters are configured."""

    async def process(self, run: Run) -> RunStatus:
        del run
        return RunStatus.BLOCKED


class Worker:
    """Claim, process, and finalize one queued run at a time."""

    def __init__(
        self,
        store: RunStore,
        queue: RunQueue,
        handler: RunHandler,
        kill_switch: bool = False,
        max_attempts: int = 3,
    ) -> None:
        self.store = store
        self.queue = queue
        self.handler = handler
        self.kill_switch = kill_switch
        self.max_attempts = max_attempts

    async def run_once(self, timeout_seconds: int = 1) -> Run | None:
        """Process one queue item; return none when the queue is empty or killed."""
        if self.kill_switch or self.store.kill_switch_enabled():
            return None
        queued = await self.queue.dequeue(timeout_seconds)
        if queued is None:
            return None
        current = self.store.get(queued.idempotency_key)
        if current is None or current.status is not RunStatus.QUEUED:
            return current
        running = self.store.transition(current.idempotency_key, RunStatus.RUNNING)
        if self.kill_switch or self.store.kill_switch_enabled():
            self.store.transition(running.idempotency_key, RunStatus.BLOCKED)
            return self.store.get(running.idempotency_key)
        try:
            target = await self.handler.process(running)
            terminal = {RunStatus.NOOP, RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.SUCCEEDED}
            if target not in terminal:
                raise ValueError("handler returned a non-terminal state")
            return self.store.transition(running.idempotency_key, target)
        except Exception as exc:
            retry = self.store.retry_or_fail(running.idempotency_key, str(exc), self.max_attempts)
            if retry.status is RunStatus.QUEUED:
                try:
                    await self.queue.enqueue(retry)
                except Exception as queue_error:
                    self.store.audit(
                        retry.idempotency_key,
                        "queue.enqueue_failed",
                        {"reason": str(queue_error)[:200]},
                    )
            return retry
