"""Validated application and repository configuration."""

from __future__ import annotations

from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class RunLimits(BaseModel):
    """Hard limits used by the deterministic guardrail layer."""

    files: int = Field(default=20, ge=1, le=100)
    input_tokens: int = Field(default=40_000, ge=1_000, le=1_000_000)
    output_tokens: int = Field(default=12_000, ge=1_000, le=500_000)
    tool_calls: int = Field(default=80, ge=1, le=1_000)
    wall_time_seconds: int = Field(default=600, ge=10, le=3_600)


class RepositoryConfig(BaseModel):
    """Policy loaded from a repository's default branch only."""

    model_config = ConfigDict(extra="forbid")

    docs_root: str = "docs"
    docs_map: str | None = None
    docsignore: list[str] = Field(default_factory=list)
    shadow: bool = True
    docstring_mode: bool = False
    log_mode: bool = False
    owners: list[str] = Field(default_factory=list)
    allowed_prompt_hashes: list[str] = Field(default_factory=list)
    limits: RunLimits = Field(default_factory=RunLimits)

    @field_validator("docs_root")
    @classmethod
    def validate_docs_root(cls, value: str) -> str:
        """Require a safe relative documentation directory."""
        if not value or "\\" in value or value.startswith("/"):
            raise ValueError("docs_root must be a relative POSIX path")
        path = PurePosixPath(value)
        if value in {".", ".."} or ".." in path.parts or path.parts[0] in {".github", ".git"}:
            raise ValueError("docs_root is outside the permitted documentation scope")
        return value.rstrip("/")


class Settings(BaseSettings):
    """Process settings. Secrets are never included in representations or logs."""

    model_config = SettingsConfigDict(env_prefix="DOCAGENT_", env_file=".env", extra="ignore")

    app_name: str = "docagent"
    webhook_secret: str = Field(min_length=16, repr=False)
    model_name: str = "claude-3-5-sonnet-latest"
    prompt_path: str = "prompts/docagent_system.md"
    database_path: str = "data/docagent.sqlite3"
    queue_url: str = "redis://localhost:6379/0"
    github_app_id: int | None = None
    github_private_key_path: str | None = None
    github_bot_login: str = "docagent[bot]"
    admin_token: str | None = Field(default=None, repr=False)
    shadow: bool = True
    allowed_prompt_hashes: set[str] = Field(default_factory=set)
    docs_root: str = "docs"
    allowed_external_domains: set[str] = Field(default_factory=set)
    kill_switch: bool = False
    max_webhook_bytes: int = Field(default=1_000_000, ge=1_024)
