import asyncio
import subprocess
from pathlib import Path

from docagent.checks.docs import run_doc_checks
from docagent.github.checkout import Checkout
from docagent.publisher import Publisher


class FakePRAPI:
    def __init__(self) -> None:
        self.created = False

    async def find_open_agent_pr(self, repo: str, head_sha: str):
        del repo, head_sha
        return None

    async def create_agent_pr(self, repo: str, title: str, body: str, head: str, base: str):
        del repo, title, body, head, base
        self.created = True
        return {"url": "https://github.example/pr/1"}


def test_checks_reject_missing_front_matter_and_external_url(tmp_path: Path) -> None:
    root = tmp_path / "docs"
    root.mkdir()
    (root / "guide.md").write_text(
        "# Guide\n\n![x](https://evil.example/a.png)\n", encoding="utf-8"
    )
    report = run_doc_checks(Checkout(tmp_path), "docs")
    assert "markdown" in report.failed
    assert "front_matter" in report.failed


def test_shadow_publisher_does_not_push(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    api = FakePRAPI()
    checkout = Checkout(repo)
    checks = type("Checks", (), {"failed": []})()
    result = asyncio.run(
        Publisher(api).publish(
            checkout,
            run_id="r1",
            repo="acme/app",
            head_sha="abc",
            base_branch="main",
            docs_root="docs",
            title="update guide",
            body="body",
            checks=checks,
            shadow=True,
        )
    )
    assert result.status == "shadow"
    assert api.created is False


def test_publisher_rejects_secret_in_pull_request_body(tmp_path: Path) -> None:
    api = FakePRAPI()
    result = asyncio.run(
        Publisher(api).publish(
            Checkout(tmp_path),
            run_id="r1",
            repo="acme/app",
            head_sha="abc",
            base_branch="main",
            docs_root="docs",
            title="update guide",
            body="token=ghp_123456789012345678901234567890",
            checks=type("Checks", (), {"failed": []})(),
            shadow=True,
        )
    )
    assert result.status == "blocked"
    assert api.created is False


def test_publisher_updates_existing_pr_when_supported(tmp_path: Path) -> None:
    class UpdatingAPI(FakePRAPI):
        async def find_open_agent_pr(self, repo: str, head_sha: str):
            return {"number": 7, "url": "https://github.example/pr/7"}

        async def update_agent_pr(self, repo: str, number: int, title: str, body: str):
            assert number == 7
            return {"url": "https://github.example/pr/7"}

    result = asyncio.run(
        Publisher(UpdatingAPI()).publish(
            Checkout(tmp_path), run_id="r1", repo="acme/app", head_sha="abc",
            base_branch="main", docs_root="docs", title="update guide", body="body",
            checks=type("Checks", (), {"failed": []})(), shadow=False,
        )
    )
    assert result.status == "updated"
