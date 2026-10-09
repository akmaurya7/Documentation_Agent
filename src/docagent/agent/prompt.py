"""Prompt loading and integrity enforcement."""

from __future__ import annotations

import hashlib
from pathlib import Path


class PromptIntegrityError(RuntimeError):
    """Raised when the policy prompt is missing or not allowlisted."""


def load_prompt(path: str, allowed_hashes: set[str]) -> tuple[str, str]:
    """Load the policy prompt and return its content and SHA-256 hash."""
    prompt = Path(path).read_text(encoding="utf-8")
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if digest not in allowed_hashes:
        raise PromptIntegrityError("prompt hash is not allowlisted")
    return prompt, digest
