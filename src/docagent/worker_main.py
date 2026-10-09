"""Production worker entrypoint."""

from __future__ import annotations

import asyncio

from .agent.provider import AnthropicProvider
from .config import Settings
from .github.auth import GitHubAppAuth
from .github.client import GitHubClient
from .github.pr_api import GitHubPullRequestAPI
from .handler import DocumentationRunHandler
from .publisher import Publisher
from .queue import RedisRunQueue
from .store import RunStore
from .worker import FailClosedHandler, RunHandler, Worker


async def serve() -> None:
    """Consume runs continuously until the process is stopped."""
    settings = Settings()  # type: ignore[call-arg]
    worker = Worker(
        RunStore(settings.database_path),
        RedisRunQueue(settings.queue_url),
        build_handler(settings),
        kill_switch=settings.kill_switch,
    )
    while True:
        await worker.run_once(timeout_seconds=5)


def build_handler(settings: Settings) -> RunHandler:
    """Build the real handler only when every required integration setting exists."""
    if not settings.github_app_id or not settings.github_private_key_path:
        return FailClosedHandler()
    from pathlib import Path

    private_key = Path(settings.github_private_key_path).read_text(encoding="utf-8")
    auth = GitHubAppAuth(settings.github_app_id, private_key)
    client = GitHubClient(auth)
    api = GitHubPullRequestAPI(client, settings.github_app_id, settings.github_bot_login)
    return DocumentationRunHandler(
        AnthropicProvider(),
        Publisher(api),
        prompt_path=settings.prompt_path,
        allowed_prompt_hashes=settings.allowed_prompt_hashes,
        model=settings.model_name,
        docs_root=settings.docs_root,
        shadow=settings.shadow,
        allowed_domains=settings.allowed_external_domains,
        limits=settings.limits,
    )


if __name__ == "__main__":
    asyncio.run(serve())
