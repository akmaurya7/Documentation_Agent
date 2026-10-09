"""Fail-closed log ingestion for the optional incident-documentation mode."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .redaction import SecurityHit, contains_secret, redact


class LogSafetyError(ValueError):
    """Raised when a log cannot be safely bounded and redacted."""


@dataclass(frozen=True)
class RedactedLog:
    """Safe log content and non-sensitive redaction metadata."""

    text: str
    security_hits: tuple[str, ...]


class FileLogSource:
    """Read one explicitly supplied file without allowing path escape."""

    def __init__(self, root: Path, max_bytes: int = 5_000_000) -> None:
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.root = root.resolve()
        self.max_bytes = max_bytes

    def read(self, relative_path: str) -> RedactedLog:
        """Read, redact, and verify one UTF-8 log file."""
        if not relative_path or "\x00" in relative_path or "\\" in relative_path:
            raise LogSafetyError("unsafe log path")
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise LogSafetyError("log path escapes source root") from exc
        if not path.is_file() or path.stat().st_size > self.max_bytes:
            raise LogSafetyError("log is missing or exceeds the size limit")
        try:
            raw = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise LogSafetyError("log cannot be safely read") from exc
        if re.search(r"(?i)\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b|\b(?:\+?\d[\d ()-]{8,}\d)\b", raw):
            raise LogSafetyError("personal data detected in log")
        safe, hits = redact(raw)
        if contains_secret(safe):
            raise LogSafetyError("log redaction could not be verified")
        return RedactedLog(safe, tuple(hit.kind for hit in hits))


def group_signatures(log: RedactedLog) -> dict[str, int]:
    """Count repeated non-sensitive log lines for incident summaries."""
    counts: dict[str, int] = {}
    for line in log.text.splitlines():
        if line.strip():
            counts[line] = counts.get(line, 0) + 1
    return counts


def security_hits(log: RedactedLog) -> tuple[SecurityHit, ...]:
    """Expose typed hit metadata without exposing matched values."""
    return tuple(SecurityHit(kind) for kind in log.security_hits)
