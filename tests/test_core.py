from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from docagent.api import create_app
from docagent.config import RepositoryConfig, Settings
from docagent.guardrails import (
    GuardrailViolation,
    normalize_repo_path,
    verify_markdown,
    verify_protected_paths,
)
from docagent.queue import LocalRunQueue
from docagent.redaction import redact
from docagent.store import RunStatus, RunStore
from docagent.worker import FailClosedHandler, Worker


def test_redaction_is_idempotent() -> None:
    value = "token=ghp_123456789012345678901234567890"
    once, hits = redact(value)
    twice, _ = redact(once)
    assert once == twice
    assert hits
    assert "ghp_" not in once


def test_path_guard_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(GuardrailViolation):
        normalize_repo_path(tmp_path, "../secret")


def test_markdown_guard_rejects_untrusted_url() -> None:
    with pytest.raises(GuardrailViolation):
        verify_markdown("![x](https://evil.example/x.png)", {"docs.example"})


def test_protected_path_guard_rejects_control_plane_files() -> None:
    with pytest.raises(GuardrailViolation):
        verify_protected_paths([".github/workflows/ci.yml"])
    with pytest.raises(GuardrailViolation):
        verify_protected_paths(["pyproject.toml"])


def test_markdown_guard_rejects_active_html() -> None:
    with pytest.raises(GuardrailViolation):
        verify_markdown("<script>alert(1)</script>", set())


def test_repository_config_rejects_ci_scope() -> None:
    with pytest.raises(ValueError):
        RepositoryConfig(docs_root=".github/workflows")


def test_webhook_signature_and_dedupe(tmp_path: Path) -> None:
    import hashlib
    import hmac
    import json

    secret = "a" * 32
    settings = Settings(webhook_secret=secret, database_path=str(tmp_path / "runs.db"))
    store = RunStore(settings.database_path)
    client = TestClient(create_app(settings, store, LocalRunQueue()))
    payload = {
        "after": "abc123",
        "repository": {"full_name": "acme/app"},
        "sender": {"login": "dev"},
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "x-hub-signature-256": signature,
        "x-github-event": "push",
        "x-github-delivery": "d1",
    }
    assert client.post("/webhooks/github", content=body, headers=headers).status_code == 202
    replay = client.post("/webhooks/github", content=body, headers=headers)
    assert replay.json()["accepted"] is False
    assert replay.json()["queued"] is True


def test_invalid_webhook_signature_is_rejected(tmp_path: Path) -> None:
    settings = Settings(webhook_secret="a" * 32, database_path=str(tmp_path / "runs.db"))
    client = TestClient(create_app(settings, queue=LocalRunQueue()))
    response = client.post(
        "/webhooks/github",
        content=b"{}",
        headers={"x-hub-signature-256": "sha256=bad"},
    )
    assert response.status_code == 401


def test_webhook_rejects_bad_json_and_honors_kill_switch(tmp_path: Path) -> None:
    import hashlib
    import hmac

    secret = "a" * 32
    settings = Settings(
        webhook_secret=secret, database_path=str(tmp_path / "runs.db"), kill_switch=True
    )
    client = TestClient(create_app(settings, queue=LocalRunQueue()))
    body = b"not-json"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"x-hub-signature-256": signature, "x-github-event": "push"},
    )
    assert response.status_code == 400

    body = b"{}"
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={"x-hub-signature-256": signature, "x-github-event": "push"},
    )
    assert response.status_code == 202
    assert response.json()["reason"] == "kill switch is active"


def test_fork_pull_request_is_comment_only(tmp_path: Path) -> None:
    import hashlib
    import hmac
    import json

    secret = "a" * 32
    settings = Settings(webhook_secret=secret, database_path=str(tmp_path / "runs.db"))
    client = TestClient(create_app(settings, queue=LocalRunQueue()))
    payload = {
        "pull_request": {
            "head": {"sha": "abc", "repo": {"full_name": "fork/app"}},
            "base": {"sha": "base", "ref": "main"},
        },
        "repository": {"full_name": "acme/app"},
        "sender": {"login": "dev"},
    }
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": "pull_request",
            "x-github-delivery": "d1",
        },
    )
    assert response.status_code == 202
    assert response.json() == {"accepted": False, "reason": "fork"}


def test_worker_fail_closes_and_is_idempotent(tmp_path: Path) -> None:
    import asyncio

    store = RunStore(str(tmp_path / "runs.db"))
    queue = LocalRunQueue()
    assert store.enqueue_once("k", "d", "acme/app", "sha", "push")
    run = store.get("k")
    assert run is not None
    asyncio.run(queue.enqueue(run))
    result = asyncio.run(Worker(store, queue, FailClosedHandler()).run_once())
    assert result is not None
    assert result.status is RunStatus.BLOCKED
    assert asyncio.run(Worker(store, queue, FailClosedHandler()).run_once()) is None


def test_admin_endpoints_require_token_and_control_kill_switch(tmp_path: Path) -> None:
    settings = Settings(
        webhook_secret="a" * 32,
        admin_token="b" * 24,
        database_path=str(tmp_path / "runs.db"),
    )
    store = RunStore(settings.database_path)
    store.enqueue_once("k", "d", "acme/app", "sha", "push")
    client = TestClient(create_app(settings, store, LocalRunQueue()))
    assert client.get("/admin/runs").status_code == 401
    headers = {"x-docagent-admin-token": "b" * 24}
    response = client.get("/admin/runs", headers=headers)
    assert response.status_code == 200
    assert response.json()[0]["status"] == "queued"
    response = client.post(
        "/admin/kill-switch", headers=headers, json={"enabled": True}
    )
    assert response.json() == {"enabled": True}


def test_metrics_expose_only_run_counts(tmp_path: Path) -> None:
    settings = Settings(webhook_secret="a" * 32, database_path=str(tmp_path / "runs.db"))
    store = RunStore(settings.database_path)
    store.enqueue_once("k", "d", "acme/app", "sha", "push")
    response = TestClient(create_app(settings, store, LocalRunQueue())).get("/metrics")
    assert response.json() == {"docagent_runs_queued": 1}
