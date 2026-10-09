import asyncio

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
