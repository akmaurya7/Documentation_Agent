from pathlib import Path

import pytest

from docagent.logs import FileLogSource, LogSafetyError, group_signatures


def test_file_log_source_redacts_and_groups(tmp_path: Path) -> None:
    log = tmp_path / "app.log"
    log.write_text(
        "error token=ghp_123456789012345678901234567890\nerror token=hidden\n",
        encoding="utf-8",
    )
    result = FileLogSource(tmp_path).read("app.log")
    assert "ghp_" not in result.text
    assert "github_token" in result.security_hits
    assert group_signatures(result)["error token=<REDACTED:GITHUB_TOKEN>"] == 1


def test_file_log_source_rejects_escape_and_invalid_utf8(tmp_path: Path) -> None:
    with pytest.raises(LogSafetyError):
        FileLogSource(tmp_path).read("../secret.log")
    bad = tmp_path / "bad.log"
    bad.write_bytes(b"\xff")
    with pytest.raises(LogSafetyError):
        FileLogSource(tmp_path).read("bad.log")
