import subprocess
from pathlib import Path

from docagent.context import FileKind, classify_path, parse_name_status
from docagent.github.auth import GitHubAppAuth, InstallationToken
from docagent.github.checkout import cleanup_checkout, prepare_checkout


def test_app_jwt_and_token_cache() -> None:
    auth = GitHubAppAuth(123, "not-a-real-key")
    token = InstallationToken("secret-token", 9_999_999_999)
    auth.cache_token(1, token)
    assert auth.cached_token(1) == token
    assert auth.cached_token(2) is None


def test_context_classification_and_renames() -> None:
    changes = parse_name_status("M\tsrc/api.py\nR100\told.md\tdocs/new.md\nD\topenapi.yaml\n")
    assert changes[0].kind is FileKind.SOURCE
    assert changes[1].previous_path == "old.md"
    assert changes[1].kind is FileKind.DOCS
    assert classify_path(".github/workflows/ci.yml") is FileKind.CI
    assert changes[2].kind is FileKind.API


def test_context_handles_deletions_and_reverts() -> None:
    changes = parse_name_status("D\tdocs/old.md\nM\tsrc/reverted.py\n")
    assert changes[0].status == "D"
    assert changes[0].kind is FileKind.DOCS
    assert changes[1].status == "M"


def test_local_checkout_is_detached_and_cleaned(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", str(source)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "README.md").write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(source), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(source), "commit", "-m", "initial"], check=True, capture_output=True
    )
    sha = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    checkout = prepare_checkout(str(source), sha)
    try:
        assert checkout.read("README.md") == b"hello\n"
        assert checkout.run(["rev-parse", "--abbrev-ref", "HEAD"]).strip() == "HEAD"
    finally:
        path = checkout.path
        cleanup_checkout(checkout)
        assert not path.exists()
