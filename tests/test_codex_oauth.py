import base64
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

from docagent import codex_oauth
from docagent.agent.models import ProviderResponse
from docagent.agent.provider import (
    _parse_responses_stream,
    _responses_input,
    _responses_tools,
)
from docagent.codex_oauth import CodexCredentials, CodexCredentialStore, new_attempt


def test_oauth_url_contains_pkce_and_scope() -> None:
    attempt = new_attempt("dynamic_agent_client", "http://127.0.0.1:1455/auth/callback")
    assert "code_challenge_method=S256" in attempt.authorization_url("docagent")
    expected = base64.urlsafe_b64encode(
        hashlib.sha256(attempt.verifier.encode()).digest()
    ).decode().rstrip("=")
    assert f"code_challenge={expected}" in attempt.authorization_url("docagent")
    assert "chatgpt.tokens.use.direct" in attempt.authorization_url("docagent")


def test_credentials_are_encrypted_at_rest(tmp_path: Path) -> None:
    path = tmp_path / "credentials.enc"
    store = CodexCredentialStore(str(path), Fernet.generate_key().decode())
    credentials = CodexCredentials(
        client_id="client",
        ext_agent_host_id="urn:uuid:test",
        id_token="id-secret",  # noqa: S106
        access_token="access-secret",  # noqa: S106
        refresh_token="refresh-secret",  # noqa: S106
        expires_at=2_000_000_000,
        scopes=["chatgpt.tokens.use.direct"],
        subject="subject",
    )
    store.save(credentials)
    assert store.load() == credentials
    raw = path.read_bytes()
    assert b"access-secret" not in raw
    assert oct(path.stat().st_mode & 0o777) in {"0o600", "0o666"}


def test_credential_store_rejects_invalid_key_and_corrupt_file(tmp_path: Path) -> None:
    with pytest.raises(codex_oauth.CodexOAuthError, match="Fernet"):
        CodexCredentialStore(str(tmp_path / "x"), "invalid")
    path = tmp_path / "corrupt"
    path.write_bytes(b"not-encrypted")
    store = CodexCredentialStore(str(path), Fernet.generate_key().decode())
    with pytest.raises(codex_oauth.CodexOAuthError, match="safely read"):
        store.load()
    missing_store = CodexCredentialStore(str(tmp_path / "missing"), Fernet.generate_key().decode())
    assert missing_store.load() is None

    encrypted = store.fernet.encrypt(b"{}")
    path.write_bytes(encrypted)
    with pytest.raises(codex_oauth.CodexOAuthError, match="safely read"):
        store.load()


@pytest.mark.asyncio
async def test_exchange_and_refresh_build_valid_credentials(monkeypatch) -> None:
    claims = {"sub": "subject", "email": "user@example.com"}
    monkeypatch.setattr(codex_oauth, "_validate_id_token", lambda *args: claims)

    async def token_request(values):
        assert values["resource"] == codex_oauth.RESOURCE
        return {
            "access_token": "access",
            "refresh_token": "refresh",
            "id_token": "identity",
            "scope": "chatgpt.tokens.use.direct",
            "expires_in": 3600,
        }

    monkeypatch.setattr(codex_oauth, "_token_request", token_request)
    attempt = new_attempt("issued-client", "http://127.0.0.1/callback")
    credentials = await codex_oauth.exchange_code(attempt, "code", "issued-client")
    assert credentials.subject == "subject"
    refreshed = await codex_oauth.refresh_credentials(credentials)
    assert refreshed.refresh_token == "refresh"


@pytest.mark.asyncio
async def test_exchange_requires_issued_client_id() -> None:
    with pytest.raises(codex_oauth.CodexOAuthError, match="issued client"):
        await codex_oauth.exchange_code(
            new_attempt("dynamic_agent_client", "http://127.0.0.1/callback"), "code", None
        )


@pytest.mark.asyncio
async def test_token_request_rejects_non_object_response(monkeypatch) -> None:
    class Response:
        status_code = 200
        is_error = False

        def json(self):
            return []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return Response()

    monkeypatch.setattr(codex_oauth.httpx, "AsyncClient", lambda **kwargs: Client())
    with pytest.raises(codex_oauth.CodexOAuthError, match="invalid token data"):
        await codex_oauth._token_request({"grant_type": "test"})


def test_credentials_require_direct_plan_scope() -> None:
    with pytest.raises(codex_oauth.CodexOAuthError, match="plan-use"):
        codex_oauth._credentials(
            {"access_token": "a", "refresh_token": "r", "id_token": "i", "scope": "openid"},
            "client",
            new_attempt("client", "http://127.0.0.1/callback"),
            None,
        )


def test_responses_payload_conversion() -> None:
    messages = [
        {"role": "user", "content": "read this"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "call-1", "name": "read", "input": {"path": "x"}}
            ],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call-1", "content": "ok"}],
        },
    ]
    converted = _responses_input(messages)
    assert converted[1]["type"] == "function_call"
    assert converted[2]["type"] == "function_call_output"
    assert _responses_tools([{"name": "read", "description": "Read", "input_schema": {}}])[0][
        "type"
    ] == "function"


@pytest.mark.asyncio
async def test_responses_stream_normalizes_function_call() -> None:
    events = [
        "data: "
        + json.dumps(
            {
                "type": "response.output_item.done",
                "item": {
                    "type": "function_call",
                    "call_id": "call-1",
                    "name": "finalize",
                    "arguments": '{"status":"noop"}',
                },
            }
        ),
        "data: "
        + '{"type":"response.completed","response":{"usage":{"input_tokens":4,"output_tokens":2}}}',
    ]

    async def lines():
        for line in events:
            yield line

    response = await _parse_responses_stream(lines())
    assert isinstance(response, ProviderResponse)
    assert response.tool_call is not None
    assert response.tool_call.name == "finalize"
    assert response.input_tokens == 4
