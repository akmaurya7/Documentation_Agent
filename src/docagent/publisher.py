"""Documentation-only Git publisher with an injected PR API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from .checks.docs import CheckReport
from .github.checkout import Checkout
from .guardrails import GuardrailViolation, verify_branch, verify_protected_paths, verify_scope
from .redaction import contains_secret


class PullRequestAPI(Protocol):
    """Minimum GitHub operations required by the publisher."""

    async def find_open_agent_pr(self, repo: str, head_sha: str) -> dict[str, Any] | None:
        """Find an existing open documentation PR."""

    async def create_agent_pr(
        self, repo: str, title: str, body: str, head: str, base: str
    ) -> dict[str, Any]:
        """Create one documentation PR."""


@dataclass(frozen=True)
class PublishResult:
    """Publisher outcome."""

    status: str
    branch: str | None = None
    pull_request_url: str | None = None
    reason: str | None = None


class Publisher:
    """Create a dedicated branch and PR, or report shadow-mode output."""

    def __init__(self, api: PullRequestAPI) -> None:
        self.api = api

    async def publish(
        self,
        checkout: Checkout,
        *,
        run_id: str,
        repo: str,
        head_sha: str,
        base_sha: str = "",
        base_branch: str,
        docs_root: str,
        title: str,
        body: str,
        checks: CheckReport,
        shadow: bool = True,
        source_pr_number: int | None = None,
        reviewers: list[str] | None = None,
        labels: list[str] | None = None,
    ) -> PublishResult:
        """Publish only a checked documentation diff."""
        if checks.failed:
            return PublishResult("blocked", reason="documentation checks failed")
        if contains_secret(body):
            return PublishResult("blocked", reason="secret detected in pull-request body")
        existing = await self.api.find_open_agent_pr(repo, head_sha)
        if existing is not None:
            update = getattr(self.api, "update_agent_pr", None)
            number = existing.get("number")
            if callable(update) and isinstance(number, int):
                updated = await update(repo, number, title, body)
                await self._notify_source(
                    repo, number, updated, source_pr_number, reviewers or [], labels or []
                )
                return PublishResult(
                    "updated", pull_request_url=str(updated.get("url", existing.get("url", "")))
                )
            return PublishResult("existing", pull_request_url=str(existing.get("url", "")))
        branch = f"docs-agent/{run_id}"
        try:
            verify_branch(branch, run_id)
            if shadow:
                return PublishResult("shadow", branch=branch)
            source_base = base_sha or base_branch
            if base_sha:
                checkout.run(["merge-base", "--is-ancestor", base_sha, base_branch])
            checkout.run(["switch", "--create", branch, source_base])
            checkout.run(["add", "--", docs_root])
            changed = checkout.run(["diff", "--cached", "--name-only"]).splitlines()
            verify_scope(changed, docs_root)
            verify_protected_paths(changed)
            if not changed:
                return PublishResult("noop", branch=branch)
            checkout.run(["commit", "-m", f"docs(agent): {title} [run {run_id}]"])
            checkout.run(["push", "--set-upstream", "origin", branch])
            pr = await self.api.create_agent_pr(repo, title, body, branch, base_branch)
            number = pr.get("number")
            if not isinstance(number, int):
                raise RuntimeError("GitHub returned a pull request without a number")
            await self._notify_source(
                repo, number, pr, source_pr_number, reviewers or [], labels or []
            )
            return PublishResult("published", branch=branch, pull_request_url=str(pr["url"]))
        except (GuardrailViolation, RuntimeError) as exc:
            return PublishResult("blocked", branch=branch, reason=str(exc))

    async def _notify_source(
        self,
        repo: str,
        doc_pr_number: int,
        pull_request: dict[str, Any],
        source_pr_number: int | None,
        reviewers: list[str],
        labels: list[str],
    ) -> None:
        """Apply review metadata and link the generated PR from its source."""
        add_labels = getattr(self.api, "add_labels", None)
        if callable(add_labels) and labels:
            await add_labels(repo, doc_pr_number, labels)
        request_reviewers = getattr(self.api, "request_reviewers", None)
        if callable(request_reviewers) and reviewers:
            await request_reviewers(repo, doc_pr_number, reviewers)
        comment = getattr(self.api, "comment_on_pr", None)
        url = str(pull_request.get("url", ""))
        if callable(comment) and source_pr_number is not None and url:
            await comment(repo, source_pr_number, f"DocAgent documentation PR: {url}")
