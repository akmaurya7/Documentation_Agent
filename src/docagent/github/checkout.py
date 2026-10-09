"""Constrained Git checkout operations."""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import Self


class CheckoutError(RuntimeError):
    """Raised when a repository cannot be safely prepared."""


class Checkout:
    """Temporary detached checkout with hooks and submodules disabled."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def run(self, args: list[str], timeout: int = 60) -> str:
        """Run a fixed-argument git command without a shell."""
        result = subprocess.run(
            ["git", *args],
            cwd=self.path,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode != 0:
            raise CheckoutError("git command failed")
        return result.stdout

    def diff(self, base_sha: str, head_sha: str) -> str:
        """Return a complete patch between two commits."""
        return self.run(["-c", "core.hooksPath=/dev/null", "diff", base_sha, head_sha], timeout=120)

    def name_status(self, base_sha: str, head_sha: str) -> str:
        """Return machine-readable changed paths."""
        return self.run(["diff", "--name-status", base_sha, head_sha], timeout=120)

    def read(self, relative_path: str, max_bytes: int = 1_000_000) -> bytes:
        """Read only a repository-relative regular file."""
        from ..guardrails import normalize_repo_path

        path = normalize_repo_path(self.path, relative_path, max_bytes=max_bytes)
        if not path.is_file():
            raise CheckoutError("requested path is not a regular file")
        return path.read_bytes()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        cleanup_checkout(self)


def prepare_checkout(source: str, head_sha: str, *, timeout: int = 300) -> Checkout:
    """Clone a repository into a temporary directory and detach at a commit."""
    directory = Path(tempfile.mkdtemp(prefix="docagent-"))
    try:
        result = subprocess.run(
            [
                "git", "clone", "--no-checkout", "--no-tags", "--filter=blob:none",
                source, str(directory),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode != 0:
            raise CheckoutError("repository clone failed")
        checkout = Checkout(directory)
        checkout.run(
            [
                "-c", "core.hooksPath=/dev/null", "-c", "submodule.recurse=false",
                "checkout", "--detach", head_sha,
            ],
            timeout=timeout,
        )
        return checkout
    except Exception:
        _cleanup(directory)
        raise


def cleanup_checkout(checkout: Checkout) -> None:
    """Remove a checkout after the run has completed."""
    _cleanup(checkout.path)


def _cleanup(path: Path) -> None:
    def onerror(function: Callable[[str], object], target: str, _exc_info: object) -> None:
        os.chmod(target, stat.S_IWRITE)
        function(target)

    shutil.rmtree(path, onerror=onerror)
