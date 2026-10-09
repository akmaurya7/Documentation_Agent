# Decisions

## 2026-10-09: Python service foundation

DocAgent uses Python 3.12+, FastAPI, Pydantic v2, and SQLAlchemy with SQLite for local development and PostgreSQL for production. Alembic owns the production schema migration path without changing webhook behavior.

## 2026-10-09: Queue boundary

Webhook handling persists an idempotency record and returns quickly. A worker boundary consumes queued records; no external queue is silently emulated in the request handler.

## 2026-10-09: Redis queue adapter

The production queue adapter uses Redis lists through the pinned `redis` async client. An in-memory queue is retained only for tests and local deterministic runs. The worker never treats an unavailable production queue as success.

## 2026-10-09: Git operations

Git operations run through fixed argument lists with `shell=False`, disabled hooks, disabled submodule recursion, no terminal prompts, and temporary detached checkouts. Cleanup is explicit and also happens when the checkout context manager exits.

## 2026-10-09: Provider boundary

The agent loop depends on a provider protocol. It supports the official
Anthropic SDK and an Antigravity CLI adapter. The CLI adapter uses its
documented stream protocol and cached account session, runs in an isolated
temporary directory, and returns only Pydantic-validated provider output; it
never reads OAuth token storage. Tests use scripted/fake providers and the
real Antigravity smoke check is performed outside CI on an authenticated host.

## 2026-10-09: Pull-request idempotency

The publisher searches open pull requests created by the configured bot and matches the source SHA in the PR body before creating anything. This avoids duplicate documentation PRs without trusting an LLM-provided file list.

## 2026-10-09: End-to-end handler

The worker handler treats missing source metadata, checkout errors, prompt-policy errors, tool failures, and publisher failures as blocked runs. It cleans temporary checkouts in a `finally` path and does not attempt partial publication.

## 2026-10-09: Fail-closed adapters

GitHub and LLM integrations are not guessed or enabled in this slice. Their interfaces will be added only after the maintained client and provider contracts are pinned and tested with fakes.
