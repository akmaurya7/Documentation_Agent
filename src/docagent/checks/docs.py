"""Markdown, link, Mermaid, front matter, secret, and scope checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from ..github.checkout import Checkout
from ..guardrails import GuardrailViolation, verify_markdown, verify_scope
from ..redaction import contains_secret


@dataclass(frozen=True)
class CheckResult:
    """One check outcome."""

    name: str
    status: str
    details: str = ""


@dataclass(frozen=True)
class CheckReport:
    """Aggregate check result."""

    results: tuple[CheckResult, ...]

    @property
    def passed(self) -> list[str]:
        return [result.name for result in self.results if result.status == "passed"]

    @property
    def failed(self) -> list[str]:
        return [result.name for result in self.results if result.status == "failed"]


def run_doc_checks(
    checkout: Checkout, docs_root: str, allowed_domains: set[str] | None = None
) -> CheckReport:
    """Run all local checks and never call an unavailable check passed."""
    results: list[CheckResult] = []
    files = _doc_files(checkout.path / docs_root)
    results.append(_check_markdown(files, allowed_domains or set()))
    results.append(_check_links(files))
    results.append(_check_mermaid(files))
    results.append(_check_front_matter(files))
    results.append(_check_secrets(files))
    results.append(_check_scope(checkout, docs_root))
    return CheckReport(tuple(results))


def _doc_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [
        path
        for path in root.rglob("*")
        if path.is_file() and path.suffix.lower() in {".md", ".mdx", ".rst", ".adoc"}
    ]


def _check_markdown(files: list[Path], domains: set[str]) -> CheckResult:
    try:
        for path in files:
            verify_markdown(path.read_text(encoding="utf-8"), domains)
        return CheckResult("markdown", "passed")
    except (UnicodeDecodeError, GuardrailViolation) as exc:
        return CheckResult("markdown", "failed", str(exc))


def _check_links(files: list[Path]) -> CheckResult:
    for path in files:
        text = path.read_text(encoding="utf-8")
        for target in re.findall(r"!?\[[^]]*\]\(([^)\s]+)", text):
            if urlparse(target).scheme:
                continue
            target_path = (path.parent / target.split("#", 1)[0]).resolve()
            if target.split("#", 1)[0] and not target_path.exists():
                return CheckResult("links", "failed", f"missing link: {target}")
    return CheckResult("links", "passed")


def _check_mermaid(files: list[Path]) -> CheckResult:
    for path in files:
        text = path.read_text(encoding="utf-8")
        openings = len(re.findall(r"```mermaid\s*\n", text))
        closed = len(re.findall(r"```mermaid\s*\n[\s\S]*?\n```", text))
        if openings != closed:
            return CheckResult("mermaid", "failed", f"unbalanced Mermaid fence: {path.name}")
    return CheckResult("mermaid", "passed")


def _check_front_matter(files: list[Path]) -> CheckResult:
    required = ("title:", "owner:", "last_verified_commit:", "status:")
    for path in files:
        text = path.read_text(encoding="utf-8")
        parts = text.split("---\n", 2)
        metadata = parts[1].splitlines() if len(parts) >= 3 else []
        if not text.startswith("---\n") or len(parts) < 3 or not all(
            any(line.startswith(prefix) for line in metadata) for prefix in required
        ):
            return CheckResult(
                "front_matter", "failed", f"missing required front matter: {path.name}"
            )
    return CheckResult("front_matter", "passed")


def _check_secrets(files: list[Path]) -> CheckResult:
    for path in files:
        if contains_secret(path.read_text(encoding="utf-8")):
            return CheckResult("secrets", "failed", f"secret detected in: {path.name}")
    return CheckResult("secrets", "passed")


def _check_scope(checkout: Checkout, docs_root: str) -> CheckResult:
    try:
        changed = checkout.run(["status", "--porcelain"]).splitlines()
        paths = [line[3:] for line in changed if len(line) >= 4]
        verify_scope(paths, docs_root)
        return CheckResult("scope", "passed")
    except (GuardrailViolation, RuntimeError) as exc:
        return CheckResult("scope", "failed", str(exc))
