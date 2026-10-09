"""Deterministic, idempotent redaction for sensitive output."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SecurityHit:
    """A non-sensitive description of a detected secret."""

    kind: str


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "private_key",
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ),
    ("aws_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{20,}\b")),
    ("bearer_token", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
    ("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b")),
    ("connection_string", re.compile(r"(?i)\b(?:postgres|mysql|mongodb(?:\+srv)?|redis)://[^\s]+")),
)
_PLACEHOLDER = re.compile(r"<REDACTED:[A-Z0-9_]+>")


def _looks_like_secret(value: str) -> bool:
    """Detect long high-entropy tokens without flagging ordinary hashes."""
    if len(value) < 24 or _PLACEHOLDER.fullmatch(value):
        return False
    alphabet = len(set(value))
    if alphabet < 12:
        return False
    frequencies = [value.count(char) / len(value) for char in set(value)]
    entropy = -sum(freq * math.log2(freq) for freq in frequencies)
    return entropy >= 3.7 and bool(re.fullmatch(r"[A-Za-z0-9_+/=-]+", value))


def redact(text: str) -> tuple[str, list[SecurityHit]]:
    """Replace known secrets and high-entropy credentials with typed markers."""
    hits: list[SecurityHit] = []
    result = text
    for kind, pattern in _PATTERNS:
        marker = f"<REDACTED:{kind.upper()}>"
        if pattern.search(result):
            hits.append(SecurityHit(kind))
            result = pattern.sub(marker, result)
    def replace_entropy(match: re.Match[str]) -> str:
        value = match.group(0)
        if _looks_like_secret(value):
            hits.append(SecurityHit("high_entropy"))
            return "<REDACTED:HIGH_ENTROPY>"
        return value
    result = re.sub(r"\b[A-Za-z0-9_+/=-]{24,}\b", replace_entropy, result)
    unique = {hit.kind: hit for hit in hits}
    return result, list(unique.values())


def contains_secret(text: str) -> bool:
    """Return whether redaction would change the value."""
    redacted, _ = redact(text)
    return redacted != text
