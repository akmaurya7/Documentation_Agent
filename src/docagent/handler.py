"""End-to-end documentation run handler."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from .agent.loop import run_agent
from .agent.provider import Provider
from .agent.tools import ToolContext
from .checks.docs import run_doc_checks
from .config import RunLimits
from .context import FileKind, parse_name_status
from .github.checkout import (
    CheckoutError,
    cleanup_checkout,
    prepare_authenticated_checkout,
    prepare_checkout,
)
from .publisher import Publisher
from .store import Run, RunStatus, RunStore


class DocumentationRunHandler:
    """Run checkout, agent, checks, and publisher in one fail-closed workflow."""

    def __init__(
        self,
        provider: Provider,
        publisher: Publisher,
        *,
        prompt_path: str,
        allowed_prompt_hashes: set[str],
        model: str,
        docs_root: str,
        shadow: bool,
        allowed_domains: set[str] | None = None,
        limits: RunLimits | None = None,
        token_provider: Callable[[], Awaitable[str]] | None = None,
        store: RunStore | None = None,
    ) -> None:
        self.provider = provider
        self.publisher = publisher
        self.prompt_path = prompt_path
        self.allowed_prompt_hashes = allowed_prompt_hashes
        self.model = model
        self.docs_root = docs_root
        self.shadow = shadow
        self.allowed_domains = allowed_domains or set()
        self.limits = limits or RunLimits()
        self.token_provider = token_provider
        self.store = store

    async def process(self, run: Run) -> RunStatus:
        """Process one persisted run and return a terminal state."""
        if not run.source_url or not run.base_sha or not run.base_branch:
            return RunStatus.BLOCKED
        checkout = None
        try:
            if self.token_provider is None:
                checkout = await asyncio.to_thread(prepare_checkout, run.source_url, run.head_sha)
            else:
                token = await self.token_provider()
                checkout = await asyncio.to_thread(
                    prepare_authenticated_checkout, run.source_url, run.head_sha, token
                )
            changes = parse_name_status(checkout.name_status(run.base_sha, run.head_sha))
            if len(changes) > self.limits.files:
                return RunStatus.BLOCKED
            if not changes or all(change.kind is FileKind.DOCS for change in changes):
                return RunStatus.NOOP
            context = ToolContext(checkout, self.docs_root, self.allowed_domains)
            report = await asyncio.wait_for(
                run_agent(
                    provider=self.provider,
                    context=context,
                    prompt_path=self.prompt_path,
                    allowed_prompt_hashes=self.allowed_prompt_hashes,
                    model=self.model,
                    run_id=run.idempotency_key,
                    event_type=run.event_type,
                    head_sha=run.head_sha,
                    max_tool_calls=self.limits.tool_calls,
                    max_input_tokens=self.limits.input_tokens,
                    max_output_tokens=self.limits.output_tokens,
                ),
                timeout=self.limits.wall_time_seconds,
            )
            if self.store is not None:
                self.store.save_report(run.idempotency_key, report.model_dump(mode="json"))
            if report.status in {"blocked", "failed", "noop"}:
                return RunStatus(report.status)
            checks = await asyncio.to_thread(
                run_doc_checks, checkout, self.docs_root, self.allowed_domains
            )
            body = _pr_body(run, report, checks)
            published = await self.publisher.publish(
                checkout,
                run_id=run.idempotency_key,
                repo=run.repo,
                head_sha=run.head_sha,
                base_branch=run.base_branch,
                docs_root=self.docs_root,
                title=f"docs: update documentation (source: {run.head_sha[:8]})",
                body=body,
                checks=checks,
                shadow=self.shadow,
            )
            if published.status in {"blocked", "noop"}:
                return RunStatus.BLOCKED if published.status == "blocked" else RunStatus.NOOP
            return RunStatus.SUCCEEDED
        except (CheckoutError, RuntimeError, ValueError):
            return RunStatus.BLOCKED
        finally:
            if checkout is not None:
                await asyncio.to_thread(cleanup_checkout, checkout)


def _pr_body(run: Run, report: object, checks: object) -> str:
    """Create a minimal source-linked PR body from validated values."""
    report_notes = getattr(report, "notes", [])
    passed = getattr(checks, "passed", [])
    return "\n".join(
        [
            "## Summary",
            "Documentation generated from an evidence-backed source change.",
            "",
            "## Why these docs changed",
            f"Source repository: `{run.repo}`",
            f"Source SHA: `{run.head_sha}`",
            "",
            "## Checks run and results",
            ", ".join(passed) or "No checks passed",
            "",
            "## Notes",
            *[f"- {note}" for note in report_notes],
            "",
            "## Reviewer checklist",
            "- [ ] Verify claims against the source change.",
            "- [ ] Confirm no sensitive information is present.",
        ]
    )
