"""Sign in with ChatGPT credential storage and token renewal for Codex usage."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlencode

import httpx
import jwt
from cryptography.fernet import Fernet, InvalidToken
from pydantic import BaseModel, Field

AUTHORIZATION_URL = "https://auth.openai.com/api/accounts/authorize"
TOKEN_URL = "https://auth.openai.com/api/accounts/oauth/token"
RESOURCE = "https://api.openai.com/v1"
ISSUER = "https://auth.openai.com"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"


class CodexOAuthError(RuntimeError):
    """Raised when a Codex OAuth session cannot be created or renewed."""


class CodexCredentials(BaseModel):
    """Encrypted-at-rest credentials; token values never enter logs or reports."""

    client_id: str
    ext_agent_host_id: str
    id_token: str = Field(repr=False)
    access_token: str = Field(repr=False)
    refresh_token: str = Field(repr=False)
    expires_at: int
    scopes: list[str] = Field(default_factory=list)
    subject: str
    email: str | None = None


@dataclass(frozen=True)
class OAuthAttempt:
    """Ephemeral state held only until one browser callback completes."""

    client_id: str
    ext_agent_host_id: str
    redirect_uri: str
    state: str
    nonce: str
    verifier: str

    @property
    def challenge(self) -> str:
        digest = hashlib.sha256(self.verifier.encode()).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def authorization_url(self, app_name: str) -> str:
        values = {
            "client_id": self.client_id,
            "response_type": "code",
            "redirect_uri": self.redirect_uri,
            "scope": SCOPES,
            "resource": RESOURCE,
            "state": self.state,
            "nonce": self.nonce,
            "code_challenge_method": "S256",
            "code_challenge": self.challenge,
            "ext_agent_host_id": self.ext_agent_host_id,
        }
        if self.client_id == "dynamic_agent_client":
            values["agent_name_hint"] = app_name
        return f"{AUTHORIZATION_URL}?{urlencode(values)}"


class CodexCredentialStore:
    """Atomically store one encrypted credential record with a Fernet key."""

    def __init__(self, path: str, encryption_key: str) -> None:
        self.path = Path(path)
        try:
            self.fernet = Fernet(encryption_key.encode())
        except (ValueError, TypeError) as exc:
            raise CodexOAuthError("DOCAGENT_CODEX_TOKEN_KEY is not a valid Fernet key") from exc

    def load(self) -> CodexCredentials | None:
        if not self.path.exists():
            return None
        try:
            data = self.fernet.decrypt(self.path.read_bytes())
            return CodexCredentials.model_validate_json(data)
        except (InvalidToken, OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise CodexOAuthError("Codex credential file cannot be safely read") from exc

    def save(self, credentials: CodexCredentials) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        encrypted = self.fernet.encrypt(credentials.model_dump_json().encode())
        temporary.write_bytes(encrypted)
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        temporary.replace(self.path)


def new_attempt(
    client_id: str, redirect_uri: str, ext_agent_host_id: str | None = None
) -> OAuthAttempt:
    """Create fresh PKCE, state, nonce, and host values for a browser attempt."""
    return OAuthAttempt(
        client_id=client_id,
        ext_agent_host_id=ext_agent_host_id or ("urn:uuid:" + str(uuid.uuid4())),
        redirect_uri=redirect_uri,
        state=secrets.token_urlsafe(32),
        nonce=secrets.token_urlsafe(32),
        verifier=secrets.token_urlsafe(64),
    )


async def exchange_code(
    attempt: OAuthAttempt, code: str, issued_client_id: str | None
) -> CodexCredentials:
    """Exchange a callback code and validate its identity and plan-use scope."""
    client_id = issued_client_id or attempt.client_id
    if client_id == "dynamic_agent_client":
        raise CodexOAuthError("OAuth callback did not return an issued client ID")
    response = await _token_request(
        {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "code": code,
            "code_verifier": attempt.verifier,
            "redirect_uri": attempt.redirect_uri,
            "resource": RESOURCE,
        }
    )
    return _credentials(response, client_id, attempt, attempt.nonce)


async def refresh_credentials(credentials: CodexCredentials) -> CodexCredentials:
    """Rotate the access and refresh tokens before the access token expires."""
    response = await _token_request(
        {
            "grant_type": "refresh_token",
            "client_id": credentials.client_id,
            "refresh_token": credentials.refresh_token,
            "resource": RESOURCE,
        }
    )
    return _credentials(
        response,
        credentials.client_id,
        OAuthAttempt(
            credentials.client_id,
            credentials.ext_agent_host_id,
            "",
            "",
            "",
            "",
        ),
        None,
        prior=credentials,
    )


async def _token_request(values: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(TOKEN_URL, data=values)
    if response.is_error:
        raise CodexOAuthError(f"Codex OAuth token request failed: HTTP {response.status_code}")
    data = response.json()
    if not isinstance(data, dict):
        raise CodexOAuthError("Codex OAuth returned invalid token data")
    return data


def _credentials(
    data: dict[str, Any],
    client_id: str,
    attempt: OAuthAttempt,
    nonce: str | None,
    prior: CodexCredentials | None = None,
) -> CodexCredentials:
    access_value = data.get("access_token")
    refresh_value = data.get("refresh_token") or (prior.refresh_token if prior else None)
    id_token_value = data.get("id_token") or (prior.id_token if prior else None)
    if not all(
        isinstance(value, str) and value for value in (access_value, refresh_value, id_token_value)
    ):
        raise CodexOAuthError("Codex OAuth response omitted required credentials")
    access = cast(str, access_value)
    refresh = cast(str, refresh_value)
    id_token = cast(str, id_token_value)
    scopes = str(data.get("scope", " ".join(prior.scopes if prior else []))).split()
    if "chatgpt.tokens.use.direct" not in scopes:
        raise CodexOAuthError("Codex OAuth grant lacks ChatGPT plan-use permission")
    claims = _validate_id_token(id_token, client_id, nonce)
    return CodexCredentials(
        client_id=client_id,
        ext_agent_host_id=attempt.ext_agent_host_id or (prior.ext_agent_host_id if prior else ""),
        id_token=id_token,
        access_token=access,
        refresh_token=refresh,
        expires_at=int(time.time()) + int(data.get("expires_in", 3600)),
        scopes=scopes,
        subject=str(claims["sub"]),
        email=str(claims["email"]) if claims.get("email") else None,
    )


def _validate_id_token(token: str, client_id: str, nonce: str | None) -> dict[str, Any]:
    """Validate issuer, audience, expiry, and nonce against OpenAI JWKS."""
    try:
        key = jwt.PyJWKClient(f"{ISSUER}/.well-known/jwks.json").get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=client_id, issuer=ISSUER)
    except (jwt.PyJWTError, OSError, ValueError) as exc:
        raise CodexOAuthError("Codex ID token validation failed") from exc
    if nonce is not None and claims.get("nonce") != nonce:
        raise CodexOAuthError("Codex ID token nonce did not match the OAuth attempt")
    if not isinstance(claims.get("sub"), str):
        raise CodexOAuthError("Codex ID token has no subject")
    return claims
