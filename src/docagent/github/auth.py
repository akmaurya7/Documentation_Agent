"""GitHub App JWT and installation-token authentication."""

from __future__ import annotations

import time
from dataclasses import dataclass

import jwt


@dataclass(frozen=True)
class InstallationToken:
    """Short-lived token metadata kept out of logs and model input."""

    value: str
    expires_at: int

    def usable(self, now: int | None = None) -> bool:
        """Require a safety margin before token expiry."""
        return self.expires_at - (now or int(time.time())) > 120


class GitHubAppAuth:
    """Create app JWTs and cache installation tokens in memory."""

    def __init__(self, app_id: int, private_key: str) -> None:
        self.app_id = app_id
        self.private_key = private_key
        self._tokens: dict[int, InstallationToken] = {}

    def app_jwt(self, now: int | None = None) -> str:
        """Create a ten-minute GitHub App JWT."""
        issued = now or int(time.time())
        payload = {"iat": issued - 60, "exp": issued + 540, "iss": str(self.app_id)}
        return str(jwt.encode(payload, self.private_key, algorithm="RS256"))

    def cached_token(self, installation_id: int) -> InstallationToken | None:
        """Return a token only while its safety margin remains valid."""
        token = self._tokens.get(installation_id)
        return token if token and token.usable() else None

    def cache_token(self, installation_id: int, token: InstallationToken) -> None:
        """Cache a token without logging or serializing its value."""
        self._tokens[installation_id] = token
