"""Portable durable persistence for SQLite development and PostgreSQL production."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine


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
    report_json: str = ""


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
    """Persist idempotency keys, reports, audit events, and dead letters."""

    def __init__(self, database_path: str, database_url: str | None = None) -> None:
        self.database_path = database_path
        url = database_url or _sqlite_url(database_path)
        if url.startswith("postgres://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgres://")
        elif url.startswith("postgresql://"):
            url = "postgresql+psycopg://" + url.removeprefix("postgresql://")
        self.engine: Engine = create_engine(url, pool_pre_ping=True)
        self._postgres = self.engine.dialect.name == "postgresql"
        if not self._postgres:
            Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        identity = (
            "BIGSERIAL PRIMARY KEY" if self._postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
        )
        with self.engine.begin() as connection:
            connection.execute(text("""CREATE TABLE IF NOT EXISTS runs (
                idempotency_key VARCHAR(512) PRIMARY KEY,
                delivery_id VARCHAR(512) NOT NULL,
                repo VARCHAR(512) NOT NULL,
                head_sha VARCHAR(256) NOT NULL,
                event_type VARCHAR(128) NOT NULL,
                status VARCHAR(32) NOT NULL,
                source_url TEXT NOT NULL DEFAULT '',
                base_sha VARCHAR(256) NOT NULL DEFAULT '',
                base_branch VARCHAR(256) NOT NULL DEFAULT '',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""))
            connection.execute(text(f"""CREATE TABLE IF NOT EXISTS audit_events (
                id {identity},
                idempotency_key VARCHAR(512) NOT NULL,
                event_type VARCHAR(128) NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{{}}',
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""))
            connection.execute(text("""CREATE TABLE IF NOT EXISTS run_reports (
                idempotency_key VARCHAR(512) PRIMARY KEY,
                report_json TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""))
            connection.execute(text(f"""CREATE TABLE IF NOT EXISTS dead_letters (
                id {identity},
                idempotency_key VARCHAR(512) NOT NULL,
                error TEXT NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""))
            connection.execute(text("""CREATE TABLE IF NOT EXISTS control_flags (
                name VARCHAR(128) PRIMARY KEY,
                enabled BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
            )"""))
            if not self._postgres:
                columns = {
                    str(row[1])
                    for row in connection.exec_driver_sql("PRAGMA table_info(runs)").all()
                }
                for name in ("source_url", "base_sha", "base_branch"):
                    if name not in columns:
                        connection.execute(
                            text(f"ALTER TABLE runs ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
                        )

    def enqueue_once(
        self, key: str, delivery_id: str, repo: str, head_sha: str, event_type: str,
        source_url: str = "", base_sha: str = "", base_branch: str = "",
    ) -> bool:
        """Insert a queued run once; return false for replayed events."""
        with self.engine.begin() as connection:
            result = connection.execute(
                text("""INSERT INTO runs
                (idempotency_key, delivery_id, repo, head_sha, event_type, status,
                 source_url, base_sha, base_branch)
                VALUES (:key, :delivery, :repo, :head, :event, 'queued', :source, :base, :branch)
                ON CONFLICT (idempotency_key) DO NOTHING"""),
                {"key": key, "delivery": delivery_id, "repo": repo, "head": head_sha,
                 "event": event_type, "source": source_url, "base": base_sha,
                 "branch": base_branch},
            )
        if result.rowcount != 1:
            return False
        self.audit(key, "run.queued", {"event_type": event_type})
        return True

    def get(self, key: str) -> Run | None:
        """Read one run by its idempotency key."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text("""SELECT idempotency_key, delivery_id, repo, head_sha, event_type,
                status, source_url, base_sha, base_branch FROM runs
                WHERE idempotency_key = :key"""), {"key": key}
            ).mappings().first()
        if row is None:
            return None
        return _run(row, self.report(key))

    def report(self, key: str) -> str:
        """Return the stored sanitized report JSON, or an empty string."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT report_json FROM run_reports WHERE idempotency_key = :key"),
                {"key": key},
            ).mappings().first()
        return "" if row is None else str(row["report_json"])

    def save_report(self, key: str, report: dict[str, object]) -> None:
        """Persist one JSON report and reject non-JSON values."""
        encoded = json.dumps(report, sort_keys=True, separators=(",", ":"))
        with self.engine.begin() as connection:
            if connection.execute(
                text("SELECT 1 FROM runs WHERE idempotency_key = :key"), {"key": key}
            ).first() is None:
                raise KeyError(key)
            connection.execute(
                text("""INSERT INTO run_reports(idempotency_key, report_json) VALUES (:key, :report)
                ON CONFLICT(idempotency_key) DO UPDATE SET report_json = :report"""),
                {"key": key, "report": encoded},
            )

    def audit(self, key: str, event_type: str, details: dict[str, object] | None = None) -> None:
        """Append one audit event; callers must provide already-redacted details."""
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO audit_events(idempotency_key, event_type, details_json) "
                     "VALUES (:key, :event, :details)"),
                {"key": key, "event": event_type,
                 "details": json.dumps(details or {}, sort_keys=True, separators=(",", ":"))},
            )

    def audit_events(self, key: str) -> list[tuple[str, str]]:
        """Return audit event type and JSON details in append order."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT event_type, details_json FROM audit_events "
                     "WHERE idempotency_key = :key ORDER BY id"), {"key": key}
            ).mappings().all()
        return [(str(row["event_type"]), str(row["details_json"])) for row in rows]

    def list_runs(self, limit: int = 100) -> list[Run]:
        """Return recent runs without exposing stored report contents."""
        if not 1 <= limit <= 1000:
            raise ValueError("limit must be between 1 and 1000")
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("""SELECT idempotency_key, delivery_id, repo, head_sha, event_type,
                status, source_url, base_sha, base_branch FROM runs
                ORDER BY created_at DESC LIMIT :limit"""), {"limit": limit}
            ).mappings().all()
        return [_run(row) for row in rows]

    def status_counts(self) -> dict[str, int]:
        """Return counts by persisted run state for metrics and operations."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                text("SELECT status, COUNT(*) AS count FROM runs GROUP BY status")
            ).mappings().all()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def dead_letter(self, key: str, error: str) -> None:
        """Persist a failure marker without retaining arbitrary exception data."""
        from .redaction import redact

        safe_error, _ = redact(error[:500])
        with self.engine.begin() as connection:
            connection.execute(
                text("INSERT INTO dead_letters(idempotency_key, error) VALUES (:key, :error)"),
                {"key": key, "error": safe_error},
            )

    def set_kill_switch(self, enabled: bool) -> None:
        """Persist the shared worker/API kill switch."""
        with self.engine.begin() as connection:
            connection.execute(
                text("""INSERT INTO control_flags(name, enabled) VALUES ('kill_switch', :enabled)
                ON CONFLICT(name) DO UPDATE SET enabled = :enabled,
                updated_at = CURRENT_TIMESTAMP"""),
                {"enabled": enabled},
            )

    def kill_switch_enabled(self) -> bool:
        """Read the shared kill switch, defaulting to disabled."""
        with self.engine.connect() as connection:
            row = connection.execute(
                text("SELECT enabled FROM control_flags WHERE name = 'kill_switch'")
            ).first()
        return bool(row[0]) if row is not None else False

    def transition(self, key: str, target: RunStatus) -> Run:
        """Apply one legal state transition atomically."""
        current = self.get(key)
        if current is None:
            raise KeyError(key)
        if target not in _TRANSITIONS[current.status]:
            raise ValueError(f"invalid run transition: {current.status} -> {target}")
        with self.engine.begin() as connection:
            connection.execute(
                text("UPDATE runs SET status = :target WHERE idempotency_key = :key"),
                {"target": target.value, "key": key},
            )
        self.audit(key, "run.transition", {"from": current.status.value, "to": target.value})
        updated = self.get(key)
        if updated is None:
            raise RuntimeError("run disappeared during transition")
        return updated

    def requeue(self, key: str) -> Run:
        """Queue a failed, blocked, or noop run for an explicit admin retry."""
        retryable = {RunStatus.FAILED, RunStatus.BLOCKED, RunStatus.NOOP}
        current = self.get(key)
        if current is None:
            raise KeyError(key)
        if current.status not in retryable:
            raise ValueError(f"run is not safely rerunnable: {current.status}")
        with self.engine.begin() as connection:
            result = connection.execute(
                text("UPDATE runs SET status = 'queued' WHERE idempotency_key = :key "
                     "AND status = :status"),
                {"key": key, "status": current.status.value},
            )
            if result.rowcount != 1:
                raise ValueError("run changed before it could be requeued")
            connection.execute(
                text("DELETE FROM run_reports WHERE idempotency_key = :key"), {"key": key}
            )
        self.audit(key, "run.requeued", {"from": current.status.value})
        updated = self.get(key)
        if updated is None:
            raise RuntimeError("run disappeared during requeue")
        return updated


def _sqlite_url(path: str) -> str:
    """Build a SQLAlchemy SQLite URL from the existing path setting."""
    return "sqlite:///" + str(Path(path).resolve()).replace("\\", "/")


def _run(row: object, report_json: str = "") -> Run:
    """Convert a SQLAlchemy row mapping to the existing domain value."""
    values = row  # SQLAlchemy mapping supports string keys at runtime.
    return Run(
        idempotency_key=str(values["idempotency_key"]),  # type: ignore[index]
        delivery_id=str(values["delivery_id"]),  # type: ignore[index]
        repo=str(values["repo"]),  # type: ignore[index]
        head_sha=str(values["head_sha"]),  # type: ignore[index]
        event_type=str(values["event_type"]),  # type: ignore[index]
        status=RunStatus(str(values["status"])),  # type: ignore[index]
        source_url=str(values["source_url"]),  # type: ignore[index]
        base_sha=str(values["base_sha"]),  # type: ignore[index]
        base_branch=str(values["base_branch"]),  # type: ignore[index]
        report_json=report_json,
    )
