import asyncio

from docagent.config import RunLimits
from docagent.handler import DocumentationRunHandler
from docagent.store import Run, RunStatus


class NeverPublisher:
    async def publish(self, *args, **kwargs):
        raise AssertionError("publisher must not run without required metadata")


def test_handler_blocks_missing_source_metadata() -> None:
    run = Run("k", "d", "acme/app", "head", "push", RunStatus.RUNNING)
    handler = DocumentationRunHandler(
        provider=object(),  # type: ignore[arg-type]
        publisher=NeverPublisher(),  # type: ignore[arg-type]
        prompt_path="missing",
        allowed_prompt_hashes=set(),
        model="fake",
        docs_root="docs",
        shadow=True,
    )
    assert asyncio.run(handler.process(run)) is RunStatus.BLOCKED


def test_handler_accepts_explicit_limits() -> None:
    handler = DocumentationRunHandler(
        provider=object(),  # type: ignore[arg-type]
        publisher=NeverPublisher(),  # type: ignore[arg-type]
        prompt_path="missing",
        allowed_prompt_hashes=set(),
        model="fake",
        docs_root="docs",
        shadow=True,
        limits=RunLimits(files=1),
    )
    assert handler.limits.files == 1


def test_handler_defaults_to_unauthenticated_checkout() -> None:
    handler = DocumentationRunHandler(
        provider=object(),  # type: ignore[arg-type]
        publisher=NeverPublisher(),  # type: ignore[arg-type]
        prompt_path="missing",
        allowed_prompt_hashes=set(),
        model="fake",
        docs_root="docs",
        shadow=True,
    )
    assert handler.token_provider is None
