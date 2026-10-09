import asyncio
import hashlib
import hmac
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from docagent.agent.models import RunReport
from docagent.agent.tools import ToolContext, ToolError
from docagent.api import create_app
from docagent.checks.docs import run_doc_checks
from docagent.config import Settings
from docagent.github.auth import GitHubAppAuth, InstallationToken
from docagent.github.checkout import Checkout
from docagent.github.client import GitHubClient
from docagent.github.pr_api import GitHubPullRequestAPI
from docagent.publisher import Publisher
from docagent.queue import LocalRunQueue, RedisRunQueue
from docagent.redaction import redact
from docagent.store import RunStatus, RunStore
from docagent.worker import Worker


def test_redactor_covers_supported_credential_shapes() -> None:
    value = " ".join(
        [
            "AKIA1234567890ABCDEF",
            "xoxb-123456789012345678901234",
            "Bearer abcdefghijklmnopqrstuvwx",
            "eyJabcdefghijk.eyJabcdefghijk.eyJabcdefghijk",
            "postgres://user:password@db.example/app",
            "-----BEGIN PRIVATE KEY-----secret-----END PRIVATE KEY-----",
        ]
    )
    safe, hits = redact(value)
    assert len(hits) == 6
    assert "password" not in safe
    noisy = "Ab3dEf5gHi7jKl9mNp1qRs3tUv5wXy7z"
    assert redact(noisy)[0] == "<REDACTED:HIGH_ENTROPY>"
    assert redact("ordinary text")[0] == "ordinary text"


def test_guarded_tools_cover_read_search_write_diff_and_finalize(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")
    (tmp_path / "code").mkdir()
    (tmp_path / "code" / "source.py").write_text("needle = True\n", encoding="utf-8")
    checkout = Checkout(tmp_path)
    context = ToolContext(checkout, "docs", {"docs.example"})
    assert context.execute("list_dir", {"path": "docs"})["value"] == "['guide.md']"
    assert "Guide" in context.execute("read_file", {"path": "docs/guide.md"})["value"]
    assert context.execute("search_code", {"query": "needle", "path": "code"})["value"] == (
        "['code/source.py']"
    )
    checkout.diff = lambda base, head: f"{base}:{head}"  # type: ignore[method-assign]
    assert context.execute("get_diff", {"base_sha": "a", "head_sha": "b"})["value"] == "a:b"
    context.execute("write_doc_file", {"path": "docs/new.md", "content": "# New\n"})
    assert (tmp_path / "docs" / "new.md").exists()
    assert context.execute("finalize", {"status": "success"})["value"]["status"] == "success"
    with pytest.raises(ToolError):
        context.execute("write_doc_file", {"path": "src/x.py", "content": "x"})


def test_documentation_checks_cover_success_and_link_failures(tmp_path: Path) -> None:
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "---\ntitle: Guide\nowner: team\nlast_verified_commit: sha\nstatus: active\n---\n"
        "See [missing](missing.md).\n```mermaid\ngraph TD\n```\n",
        encoding="utf-8",
    )
    report = run_doc_checks(Checkout(tmp_path), "docs")
    assert "front_matter" in report.passed
    assert "mermaid" in report.passed
    assert "links" in report.failed


def test_redisc_queue_round_trip(monkeypatch) -> None:
    import redis.asyncio
    from docagent.store import Run

    items = []

    class FakeRedis:
        @classmethod
        def from_url(cls, url, decode_responses=True):
            del url, decode_responses
            return cls()

        async def rpush(self, name, payload):
            items.append((name, payload))

        async def blpop(self, name, timeout):
            del timeout
            return items.pop(0) if items else None

        async def aclose(self):
            return None

    monkeypatch.setattr(redis.asyncio, "Redis", FakeRedis)
    queue = RedisRunQueue("redis://example")
    run = Run("k", "d", "r", "sha", "push", RunStatus.QUEUED)
    async def round_trip():
        await queue.enqueue(run)
        return await queue.dequeue(1)
    assert asyncio.run(round_trip()) == run


def test_publisher_blocks_failed_checks_and_reuses_existing_pr(tmp_path: Path) -> None:

    class ExistingAPI:
        async def find_open_agent_pr(self, repo, head_sha):
            return {"url": "https://github.example/pr/1"}

        async def create_agent_pr(self, *args):
            raise AssertionError("must not create duplicate")

    checks = type("Checks", (), {"failed": ["markdown"]})()
    result = asyncio.run(
        Publisher(ExistingAPI()).publish(
            Checkout(tmp_path), run_id="k", repo="r", head_sha="sha", base_branch="main",
            docs_root="docs", title="t", body="b", checks=checks, shadow=False,
        )
    )
    assert result.status == "blocked"
    checks.failed = []
    result = asyncio.run(
        Publisher(ExistingAPI()).publish(
            Checkout(tmp_path), run_id="k", repo="r", head_sha="sha", base_branch="main",
            docs_root="docs", title="t", body="b", checks=checks, shadow=False,
        )
    )
    assert result.status == "existing"


def test_publisher_commits_and_pushes_documentation_branch(tmp_path: Path) -> None:
    import subprocess

    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    subprocess.run(["git", "init", "-b", "main", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    (repo / "docs").mkdir()
    (repo / "docs" / "guide.md").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "docs"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "initial"], check=True, capture_output=True
    )
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin", str(remote)], check=True)
    (repo / "docs" / "guide.md").write_text("new\n", encoding="utf-8")

    class API:
        async def find_open_agent_pr(self, repo, head_sha):
            return None

        async def create_agent_pr(self, *args):
            return {"url": "https://github.example/pr/2"}

    result = asyncio.run(
        __import__("docagent.publisher", fromlist=["Publisher"]).Publisher(API()).publish(
            Checkout(repo), run_id="run", repo="acme/app", head_sha="sha", base_branch="main",
            docs_root="docs", title="update", body="safe", checks=type("C", (), {"failed": []})(),
            shadow=False,
        )
    )
    assert result.status == "published"


def test_local_queue_and_worker_failure_are_safe(tmp_path: Path) -> None:
    async def exercise() -> None:
        store = RunStore(str(tmp_path / "runs.db"))
        queue = LocalRunQueue()
        assert store.enqueue_once("k", "d", "r", "sha", "push")

        class FailingHandler:
            async def process(self, run):
                raise RuntimeError("provider failure")

        await queue.enqueue(store.get("k"))  # type: ignore[arg-type]
        result = await Worker(store, queue, FailingHandler()).run_once()
        assert result is not None and result.status is RunStatus.FAILED
        assert await queue.dequeue(timeout_seconds=0) is None

    asyncio.run(exercise())


def test_health_readiness_and_webhook_filters(tmp_path: Path) -> None:
    secret = "a" * 32
    settings = Settings(webhook_secret=secret, database_path=str(tmp_path / "runs.db"))
    client = TestClient(create_app(settings, queue=LocalRunQueue()))
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/readyz").json() == {"status": "ready"}
    payload = {"after": "sha", "repository": {"full_name": "r"}, "sender": {"login": "docagent"}}
    body = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    response = client.post(
        "/webhooks/github",
        content=body,
        headers={
            "x-hub-signature-256": signature,
            "x-github-event": "push",
            "x-github-delivery": "d",
        },
    )
    assert response.json() == {"accepted": False, "reason": "filtered"}


def test_auth_token_margin_and_expiry() -> None:
    token = InstallationToken("secret", 4_000_000_000)
    assert token.usable(1) is True
    assert InstallationToken("expired", 100).usable(0) is False
    auth = GitHubAppAuth(1, "key")
    auth.cache_token(1, token)
    assert auth.cached_token(1) is token
    assert auth.cached_token(2) is None


@pytest.mark.asyncio
async def test_handler_runs_agent_checks_and_publisher(monkeypatch, tmp_path: Path) -> None:
    import docagent.handler as handler_module
    from docagent.handler import DocumentationRunHandler
    from docagent.store import Run

    class FakeCheckout:
        path = tmp_path

        def name_status(self, base, head):
            return "M\tsrc/app.py\n"

    class Publisher:
        async def publish(self, *args, **kwargs):
            return type("Result", (), {"status": "published"})()

    async def fake_agent(**kwargs):
        return RunReport(run_id="k", status="success", event_type="push", head_sha="head")

    monkeypatch.setattr(handler_module, "prepare_checkout", lambda *args: FakeCheckout())
    monkeypatch.setattr(handler_module, "cleanup_checkout", lambda checkout: None)
    monkeypatch.setattr(handler_module, "run_agent", fake_agent)
    monkeypatch.setattr(
        handler_module,
        "run_doc_checks",
        lambda *args: type("Checks", (), {"failed": [], "passed": ["markdown"]})(),
    )
    handler = DocumentationRunHandler(
        provider=object(),  # type: ignore[arg-type]
        publisher=Publisher(),  # type: ignore[arg-type]
        prompt_path="prompt",
        allowed_prompt_hashes=set(),
        model="fake",
        docs_root="docs",
        shadow=False,
    )
    run = Run("k", "d", "acme/app", "head", "push", RunStatus.RUNNING, "source", "base", "main")
    assert await handler.process(run) is RunStatus.SUCCEEDED


@pytest.mark.asyncio
async def test_github_adapters_cover_authenticated_requests(monkeypatch) -> None:
    class Response:
        status_code = 200
        is_error = False

        def __init__(self, data):
            self.data = data

        def json(self):
            return self.data

    class AsyncClient:
        def __init__(self, **kwargs):
            del kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def request(self, method, url, **kwargs):
            if method == "POST" and "access_tokens" in url:
                return Response({"token": "installation", "expires_at": "2030-01-01T00:00:00Z"})
            if url.endswith("/pulls?state=open&per_page=100"):
                return Response([])
            if url.endswith("/pulls"):
                return Response({"url": "https://github.example/pr/1"})
            return Response({"full_name": "acme/app"})

    monkeypatch.setattr(httpx, "AsyncClient", AsyncClient)
    auth = GitHubAppAuth(1, "key")
    auth.app_jwt = lambda now=None: "jwt"  # type: ignore[method-assign]
    client = GitHubClient(auth, "https://github.example")
    assert (await client.installation_token(1)).value == "installation"
    assert (await client.repository(1, "acme", "app"))["full_name"] == "acme/app"
    assert await client.open_pull_requests(1, "acme/app") == []
    assert (await client.create_pull_request(1, "acme/app", "t", "b", "h", "main"))["url"]
    adapter = GitHubPullRequestAPI(client, 1, "docagent[bot]")
    assert await adapter.find_open_agent_pr("acme/app", "sha") is None
    assert await adapter.installation_token() == "installation"
