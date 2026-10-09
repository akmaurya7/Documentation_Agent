"""Small typed GitHub App client with bounded retries."""

from __future__ import annotations

import asyncio
import secrets
from typing import Any, cast

import httpx

from .auth import GitHubAppAuth, InstallationToken


class GitHubClientError(RuntimeError):
    """Raised when GitHub cannot safely complete an operation."""


class GitHubClient:
    """GitHub REST client; tokens never leave this adapter."""

    def __init__(self, auth: GitHubAppAuth, base_url: str = "https://api.github.com") -> None:
        self.auth = auth
        self.base_url = base_url.rstrip("/")

    async def installation_token(self, installation_id: int) -> InstallationToken:
        """Get or create a short-lived installation token."""
        cached = self.auth.cached_token(installation_id)
        if cached is not None:
            return cached
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.auth.app_jwt()}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        data = await self._request(
            "POST", f"/app/installations/{installation_id}/access_tokens", headers=headers
        )
        token = InstallationToken(data["token"], _expiry(data["expires_at"]))
        self.auth.cache_token(installation_id, token)
        return token

    async def repository(self, installation_id: int, owner: str, name: str) -> dict[str, Any]:
        """Read repository metadata with an installation token."""
        token = await self.installation_token(installation_id)
        data = await self._request(
            "GET", f"/repos/{owner}/{name}", headers=self._token_headers(token)
        )
        if not isinstance(data, dict):
            raise GitHubClientError("GitHub returned invalid repository metadata")
        return data

    async def open_pull_requests(self, installation_id: int, repo: str) -> list[dict[str, Any]]:
        """List open pull requests for idempotency lookup."""
        token = await self.installation_token(installation_id)
        data = await self._request(
            "GET",
            f"/repos/{repo}/pulls?state=open&per_page=100",
            headers=self._token_headers(token),
        )
        if not isinstance(data, list):
            raise GitHubClientError("GitHub returned an invalid pull-request list")
        return [item for item in data if isinstance(item, dict)]

    async def create_pull_request(
        self,
        installation_id: int,
        repo: str,
        title: str,
        body: str,
        head: str,
        base: str,
    ) -> dict[str, Any]:
        """Create one pull request with the installation token."""
        token = await self.installation_token(installation_id)
        data = await self._request(
            "POST",
            f"/repos/{repo}/pulls",
            headers=self._token_headers(token),
            body={"title": title, "body": body, "head": head, "base": base},
        )
        if not isinstance(data, dict):
            raise GitHubClientError("GitHub returned an invalid pull-request response")
        return data

    async def _request(
        self, method: str, path: str, *, headers: dict[str, str], body: dict[str, Any] | None = None
    ) -> Any:
        for attempt in range(3):
            async with httpx.AsyncClient(timeout=20.0) as client:
                response = await client.request(
                    method, self.base_url + path, headers=headers, json=body
                )
            if response.status_code not in {429, 502, 503, 504}:
                if response.is_error:
                    raise GitHubClientError(f"GitHub request failed: {response.status_code}")
                return cast(dict[str, Any], response.json())
            if attempt < 2:
                await asyncio.sleep((2**attempt) + secrets.randbelow(1000) / 1000)
        raise GitHubClientError("GitHub rate limit or transient failure persisted")

    @staticmethod
    def _token_headers(token: InstallationToken) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token.value}",
            "X-GitHub-Api-Version": "2022-11-28",
        }


def _expiry(value: str) -> int:
    """Parse GitHub's ISO timestamp without exposing token data."""
    from datetime import datetime

    return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp())
