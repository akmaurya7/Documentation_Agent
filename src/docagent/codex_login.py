"""Interactive local Sign in with ChatGPT login for the Codex provider."""

from __future__ import annotations

import asyncio
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

from .codex_oauth import CodexCredentialStore, CodexOAuthError, exchange_code, new_attempt


def _env(name: str, default: str | None = None) -> str:
    value = os.environ.get(name, default)
    if not value:
        raise CodexOAuthError(f"{name} is required")
    return value


async def login() -> None:
    """Open the browser, receive one loopback callback, and save encrypted credentials."""
    token_path = os.environ.get("DOCAGENT_CODEX_TOKEN_PATH", "data/codex_credentials.enc")
    key = _env("DOCAGENT_CODEX_TOKEN_KEY")
    port = int(os.environ.get("DOCAGENT_CODEX_REDIRECT_PORT", "1455"))
    redirect_uri = f"http://127.0.0.1:{port}/auth/callback"
    store = CodexCredentialStore(token_path, key)
    previous = store.load()
    attempt = new_attempt(
        previous.client_id if previous else "dynamic_agent_client",
        redirect_uri,
        previous.ext_agent_host_id if previous else None,
    )
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            query = parse_qs(urlparse(self.path).query)
            result.update({key: values[0] for key, values in query.items() if values})
            self.send_response(200 if result.get("state") == attempt.state else 400)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"<h1>DocAgent login received.</h1><p>You may close this window.</p>")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = HTTPServer(("127.0.0.1", port), CallbackHandler)
    try:
        webbrowser.open(attempt.authorization_url(os.environ.get("DOCAGENT_APP_NAME", "docagent")))
        await asyncio.to_thread(server.handle_request)
    finally:
        server.server_close()
    if result.get("state") != attempt.state:
        raise CodexOAuthError("OAuth callback state did not match")
    if result.get("error"):
        raise CodexOAuthError("OAuth authorization was declined")
    if not result.get("code"):
        raise CodexOAuthError("OAuth callback did not include an authorization code")
    credentials = await exchange_code(attempt, result["code"], result.get("client_id"))
    store.save(credentials)
    print(f"Codex OAuth credentials saved to {token_path}")


if __name__ == "__main__":
    asyncio.run(login())
