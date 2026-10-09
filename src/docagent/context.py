"""Diff parsing and repository-content classification."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath


class FileKind(StrEnum):
    SOURCE = "source"
    TEST = "test"
    CONFIG = "config"
    CI = "ci"
    MIGRATION = "migration"
    API = "api"
    MANIFEST = "manifest"
    GENERATED = "generated"
    VENDORED = "vendored"
    BINARY = "binary"
    DOCS = "docs"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ChangedFile:
    """One changed path as reported by Git."""

    status: str
    path: str
    previous_path: str | None
    kind: FileKind


def parse_name_status(output: str) -> list[ChangedFile]:
    """Parse Git's name-status output, including rename records."""
    changes: list[ChangedFile] = []
    for line in output.splitlines():
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        if status.startswith("R") and len(parts) >= 3:
            previous, path = parts[1], parts[2]
        else:
            previous, path = None, parts[1]
        changes.append(ChangedFile(status, path, previous, classify_path(path)))
    return changes


def classify_path(path: str) -> FileKind:
    """Classify a path conservatively for context selection."""
    lower = path.lower()
    name = PurePosixPath(lower).name
    if ".github/workflows/" in lower or lower.startswith(".github/"):
        return FileKind.CI
    if "/vendor/" in f"/{lower}" or lower.startswith("vendor/"):
        return FileKind.VENDORED
    if any(part in {"generated", "dist", "build"} for part in PurePosixPath(lower).parts):
        return FileKind.GENERATED
    if name.endswith((".png", ".jpg", ".jpeg", ".gif", ".ico", ".pdf", ".zip")):
        return FileKind.BINARY
    if lower.endswith((".md", ".mdx", ".rst", ".adoc")):
        return FileKind.DOCS
    if "migration" in lower or "/migrations/" in lower:
        return FileKind.MIGRATION
    if name in {"openapi.yaml", "openapi.yml", "openapi.json", "schema.graphql"}:
        return FileKind.API
    if name in {"pyproject.toml", "package.json", "go.mod", "cargo.toml", "requirements.txt"}:
        return FileKind.MANIFEST
    if name.startswith("test_") or "/tests/" in lower or lower.endswith(("_test.py", ".spec.ts")):
        return FileKind.TEST
    if name in {".env", ".env.example", "config.yaml", "config.yml"} or lower.endswith(
        (".toml", ".ini")
    ):
        return FileKind.CONFIG
    if lower.endswith((".py", ".js", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".php")):
        return FileKind.SOURCE
    return FileKind.UNKNOWN
