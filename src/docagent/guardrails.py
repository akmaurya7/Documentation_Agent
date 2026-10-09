"""Fail-closed deterministic guards for agent actions and generated output."""

from __future__ import annotations

import hashlib
import hmac
import posixpath
import re
from pathlib import Path
from urllib.parse import urlparse


class GuardrailViolation(ValueError):
    """Raised when an action cannot be proven safe."""


def verify_signature(body: bytes, signature: str | None, secret: str) -> bool:
    """Verify GitHub's SHA-256 signature before parsing webhook content."""
    if not signature or not signature.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def normalize_repo_path(repo_root: Path, raw_path: str, *, max_bytes: int = 1_000_000) -> Path:
    """Resolve a repository-relative path and reject traversal and symlink escapes."""
    if not raw_path or "\x00" in raw_path or "\\" in raw_path or Path(raw_path).is_absolute():
        raise GuardrailViolation("unsafe path")
    normalized = posixpath.normpath(raw_path)
    if normalized in {".", ".."} or normalized.startswith("../"):
        raise GuardrailViolation("path traversal")
    candidate = (repo_root / normalized).resolve(strict=False)
    root = repo_root.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise GuardrailViolation("path escapes repository") from exc
    if candidate.exists() and candidate.is_file() and candidate.stat().st_size > max_bytes:
        raise GuardrailViolation("file exceeds size limit")
    return candidate


def verify_scope(changed_paths: list[str], docs_root: str) -> None:
    """Ensure all changed paths are inside the configured documentation root."""
    root = docs_root.rstrip("/") + "/"
    if any(path != docs_root and not path.startswith(root) for path in changed_paths):
        raise GuardrailViolation("working tree changed outside docs_root")


def verify_branch(branch: str, run_id: str) -> None:
    """Allow writes only to the run's dedicated branch."""
    expected = f"docs-agent/{run_id}"
    if branch != expected:
        raise GuardrailViolation("writes are restricted to the run branch")


def verify_markdown(text: str, allowed_domains: set[str]) -> None:
    """Reject active HTML and untrusted external URLs in generated Markdown."""
    if re.search(r"(?is)<\s*(?:script|iframe)\b|javascript:", text):
        raise GuardrailViolation("active markdown content is forbidden")
    for match in re.finditer(r"(?:!?)\[[^]]*\]\((https?://[^)\s]+)", text):
        domain = (urlparse(match.group(1)).hostname or "").lower()
        if domain not in {item.lower() for item in allowed_domains}:
            raise GuardrailViolation("external markdown URL is not allowlisted")
