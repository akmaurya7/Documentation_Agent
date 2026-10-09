"""Pydantic contracts shared by providers, tools, and run reports."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ToolCall(BaseModel):
    """One model-requested tool invocation."""

    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class FinalizeInput(BaseModel):
    """Required final status and evidence summary."""

    status: Literal["success", "noop", "blocked", "failed"]
    files_changed: list[str] = Field(default_factory=list)
    unverified_claims_count: int = Field(default=0, ge=0)
    todo_human_items: list[str] = Field(default_factory=list)
    security_flags: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class RunReport(BaseModel):
    """Sanitized report emitted by a completed agent run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    status: Literal["success", "noop", "blocked", "failed"]
    event_type: str
    head_sha: str
    phases: list[str] = Field(default_factory=list)
    files_changed: list[str] = Field(default_factory=list)
    unverified_claims_count: int = 0
    todo_human_items: list[str] = Field(default_factory=list)
    security_flags: list[str] = Field(default_factory=list)
    checks_passed: list[str] = Field(default_factory=list)
    checks_failed: list[str] = Field(default_factory=list)
    checks_not_run: list[str] = Field(default_factory=list)
    limits_hit: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ProviderResponse(BaseModel):
    """Provider output normalized before the loop sees it."""

    tool_call: ToolCall | None = None
    final: FinalizeInput | None = None
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)

