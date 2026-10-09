"""Authenticated GitHub pull-request operations for the publisher."""

from __future__ import annotations

from typing import Any

from .client import GitHubClient


class GitHubPullRequestAPI:
    """Adapt the async GitHub client to the publisher contract."""

    def __init__(self, client: GitHubClient, installation_id: int, bot_login: str) -> None:
        self.client = client
        self.installation_id = installation_id
        self.bot_login = bot_login

    async def installation_token(self) -> str:
        """Return the short-lived token for Git transport authentication."""
        return (await self.client.installation_token(self.installation_id)).value

    async def find_open_agent_pr(self, repo: str, head_sha: str) -> dict[str, Any] | None:
        for pull_request in await self.client.open_pull_requests(self.installation_id, repo):
            body = str(pull_request.get("body", ""))
            user = str(pull_request.get("user", {}).get("login", ""))
            if user == self.bot_login and f"source: {head_sha}" in body:
                return pull_request
        return None

    async def create_agent_pr(
        self, repo: str, title: str, body: str, head: str, base: str
    ) -> dict[str, Any]:
        return await self.client.create_pull_request(
            self.installation_id, repo, title, body, head, base
        )

    async def update_agent_pr(
        self, repo: str, number: int, title: str, body: str
    ) -> dict[str, Any]:
        """Update the existing bot-created documentation PR."""
        return await self.client.update_pull_request(
            self.installation_id, repo, number, title, body
        )

    async def add_labels(self, repo: str, number: int, labels: list[str]) -> None:
        await self.client.add_labels(self.installation_id, repo, number, labels)

    async def request_reviewers(self, repo: str, number: int, reviewers: list[str]) -> None:
        await self.client.request_reviewers(self.installation_id, repo, number, reviewers)

    async def comment_on_pr(self, repo: str, number: int, body: str) -> None:
        await self.client.comment_on_pr(self.installation_id, repo, number, body)
