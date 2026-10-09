"""Small SQLite persistence layer for webhook deduplication and run state."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class RunStatus(StrEnum):
    """Persisted lifecycle states for a documentation run."""

    QUEUED = "queued"
    RUNNING = "running"
    NOOP = "noop"
    BLOCKED = "blocked"
    FAILED = "failed"
    SUCCEEDED = "succeeded"


@dataclass(frozen=True)
class Run:
    """Immutable run data handed to a worker."""

    idempotency_key: str
    delivery_id: str
    repo: str
    head_sha: str
    event_type: str
    status: RunStatus
    source_url: str = ""
    base_sha: str = ""
    base_branch: str = ""


_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING}),
    RunStatus.RUNNING: frozenset(
        {RunStatus.NOOP, RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.SUCCEEDED}
    ),
    RunStatus.NOOP: frozenset(),
    RunStatus.BLOCKED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.SUCCEEDED: frozenset(),
}


class RunStore:
    """Persist idempotency keys and auditable run states."""

    def __init__(self, database_path: str) -> None:
        self.database_path = database_path
        Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("""CREATE TABLE IF NOT EXISTS runs (
                idempotency_key TEXT PRIMARY KEY,
                delivery_id TEXT NOT NULL,
                repo TEXT NOT NULL,
                head_sha TEXT NOT NULL,
                event_type TEXT NOT NULL,
                status TEXT NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                base_sha TEXT NOT NULL DEFAULT '',
                base_branch TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )""")
            columns = {row[1] for row in connection.execute("PRAGMA table_info(runs)")}
            for name in ("source_url", "base_sha", "base_branch"):
                if name not in columns:
                    connection.execute(
                        f"ALTER TABLE runs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''"
                    )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        return connection

    def enqueue_once(
        self,
        key: str,
        delivery_id: str,
        repo: str,
        head_sha: str,
        event_type: str,
        source_url: str = "",
        base_sha: str = "",
        base_branch: str = "",
    ) -> bool:
        """Insert a queued run once; return false for replayed events."""
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO runs(idempotency_key, delivery_id, repo, head_sha, event_type, "
                    "status, source_url, base_sha, base_branch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        key, delivery_id, repo, head_sha, event_type, "queued",
                        source_url, base_sha, base_branch,
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get(self, key: str) -> Run | None:
        """Read one run by its idempotency key."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT idempotency_key, delivery_id, repo, head_sha, event_type, status, "
                "source_url, base_sha, base_branch "
                "FROM runs WHERE idempotency_key = ?",
                (key,),
            ).fetchone()
        if row is None:
            return None
        return Run(
            idempotency_key=row["idempotency_key"],
            delivery_id=row["delivery_id"],
            repo=row["repo"],
            head_sha=row["head_sha"],
            event_type=row["event_type"],
            status=RunStatus(row["status"]),
            source_url=row["source_url"],
            base_sha=row["base_sha"],
            base_branch=row["base_branch"],
        )

    def transition(self, key: str, target: RunStatus) -> Run:
        """Apply one legal state transition atomically."""
        current = self.get(key)
        if current is None:
            raise KeyError(key)
        if target not in _TRANSITIONS[current.status]:
            raise ValueError(f"invalid run transition: {current.status} -> {target}")
        with self._connect() as connection:
            connection.execute(
                "UPDATE runs SET status = ? WHERE idempotency_key = ?", (target, key)
            )
        updated = self.get(key)
        if updated is None:
            raise RuntimeError("run disappeared during transition")
        return updated
