"""Guarded repository tools exposed to the language model."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from ..github.checkout import Checkout, CheckoutError
from ..guardrails import (
    GuardrailViolation,
    normalize_repo_path,
    verify_manual_blocks,
    verify_markdown,
)
from ..redaction import redact
from .models import FinalizeInput


class ToolError(RuntimeError):
    """Raised when a tool request is invalid or forbidden."""


class ReadFileArgs(BaseModel):
    path: str


class ListDirArgs(BaseModel):
    path: str = "."


class SearchCodeArgs(BaseModel):
    query: str = Field(min_length=1, max_length=200)
    path: str = "."


class WriteDocArgs(BaseModel):
    path: str
    content: str


class ToolContext:
    """Execution context that keeps the model away from raw filesystem access."""

    def __init__(
        self, checkout: Checkout, docs_root: str, allowed_domains: set[str] | None = None
    ) -> None:
        self.checkout = checkout
        self.docs_root = docs_root.rstrip("/")
        self.allowed_domains = allowed_domains or set()
        self.audit: list[dict[str, str]] = []

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Run one known tool and redact its result before returning it."""
        try:
            result = self._execute(name, arguments)
        except (CheckoutError, GuardrailViolation, ToolError, ValueError) as exc:
            self.audit.append({"tool": name, "result": "denied"})
            raise ToolError(str(exc)) from exc
        if name == "finalize":
            self.audit.append({"tool": name, "result": "ok"})
            return {"value": result, "security_hits": []}
        text, hits = redact(str(result))
        self.audit.append({"tool": name, "result": "redacted" if hits else "ok"})
        return {"value": text, "security_hits": [hit.kind for hit in hits]}

    def _execute(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "read_file":
            read_args = ReadFileArgs.model_validate(arguments)
            return self.checkout.read(read_args.path).decode("utf-8", errors="replace")
        if name == "list_dir":
            list_args = ListDirArgs.model_validate(arguments)
            path = normalize_repo_path(self.checkout.path, list_args.path)
            if not path.is_dir():
                raise ToolError("not a directory")
            return sorted(item.name for item in path.iterdir())
        if name == "search_code":
            search_args = SearchCodeArgs.model_validate(arguments)
            root = normalize_repo_path(self.checkout.path, search_args.path)
            if not root.is_dir():
                raise ToolError("search root is not a directory")
            matches: list[str] = []
            for file in root.rglob("*"):
                if file.is_file() and ".git" not in file.parts:
                    try:
                        content = file.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        continue
                    if re.search(search_args.query, content):
                        matches.append(file.relative_to(self.checkout.path).as_posix())
            return matches[:100]
        if name == "get_diff":
            return self.checkout.diff(arguments["base_sha"], arguments["head_sha"])
        if name == "write_doc_file":
            write_args = WriteDocArgs.model_validate(arguments)
            if write_args.path != self.docs_root and not write_args.path.startswith(
                self.docs_root + "/"
            ):
                raise ToolError("writes are restricted to docs_root")
            if Path(write_args.path).suffix.lower() not in {
                ".md", ".mdx", ".rst", ".adoc", ".yml", ".yaml"
            }:
                raise ToolError("file extension is not permitted")
            verify_markdown(write_args.content, self.allowed_domains)
            if redact(write_args.content)[1]:
                raise ToolError("secret detected in documentation")
            destination = normalize_repo_path(self.checkout.path, write_args.path)
            if destination.exists():
                verify_manual_blocks(
                    destination.read_text(encoding="utf-8"), write_args.content
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(write_args.content, encoding="utf-8", newline="\n")
            return {"path": write_args.path, "bytes": len(write_args.content.encode("utf-8"))}
        if name == "finalize":
            return FinalizeInput.model_validate(arguments).model_dump()
        raise ToolError("tool is not available in this run")


def tool_definitions() -> list[dict[str, Any]]:
    """Return provider-neutral JSON schemas for the supported tools."""
    return [
        {
            "name": "read_file",
            "description": "Read one repository file.",
            "input_schema": ReadFileArgs.model_json_schema(),
        },
        {
            "name": "list_dir",
            "description": "List one repository directory.",
            "input_schema": ListDirArgs.model_json_schema(),
        },
        {
            "name": "search_code",
            "description": "Search text in repository files.",
            "input_schema": SearchCodeArgs.model_json_schema(),
        },
        {
            "name": "get_diff",
            "description": "Read the complete source diff.",
            "input_schema": {
                "type": "object",
                "required": ["base_sha", "head_sha"],
            },
        },
        {
            "name": "write_doc_file",
            "description": "Write only an allowed documentation file.",
            "input_schema": WriteDocArgs.model_json_schema(),
        },
        {
            "name": "finalize",
            "description": "Finish the run with a validated report.",
            "input_schema": FinalizeInput.model_json_schema(),
        },
    ]
