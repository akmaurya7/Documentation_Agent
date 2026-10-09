# DocAgent

DocAgent is a fail-closed service for keeping GitHub repository documentation aligned with code changes. It authenticates GitHub webhooks, filters loops and fork writes, deduplicates deliveries, persists queued runs and audit events, runs a bounded provider/tool loop, checks documentation, and publishes documentation-only pull requests.

## Run locally

```powershell
Copy-Item .env.example .env
py -3.12 -m pip install -e ".[dev]"
$env:PYTHONPATH = "src"
py -3.12 -m uvicorn docagent.main:app --reload
```

Check `http://127.0.0.1:8000/healthz` and `/readyz`. The webhook endpoint is `POST /webhooks/github` and requires `X-Hub-Signature-256`, `X-GitHub-Event`, and `X-GitHub-Delivery` headers.

## Runtime modes

The worker is fail-closed unless all GitHub App settings and a valid prompt hash are configured. With those settings it performs checkout, bounded analysis, documentation checks, and publisher operations. Shadow mode is enabled by default and stores the result without pushing a branch.

The agent validates the policy prompt hash, exposes typed guarded tools, redacts tool results and final reports, restricts writes to `docs_root`, and requires the model to finish through `finalize`. Run limits cover files, tool calls, tokens, and wall time.

For the local stack, copy `.env.example` to `.env`, set a real webhook secret, and start Redis, the API, and the worker with `docker compose up --build`. Without GitHub App credentials, queued work intentionally finishes as `blocked`.

## Production prerequisites

- Create a GitHub App with metadata read, contents read/write, and pull-request write permissions only.
- Configure the webhook secret, app ID, private-key path, bot login, Anthropic credentials, prompt hash allowlist, and an admin token through the environment.
- Start with `DOCAGENT_SHADOW=true`; verify reports and checks before enabling publication.
- Put the SQLite database on durable storage for a single instance. PostgreSQL/Alembic migration is still required before multi-instance production.
- Keep Redis durable and protected; the worker treats queue failures as failures and records dead letters.
- Protect `/admin/*` and `/metrics` at the network boundary in addition to the admin token.

## Verification

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m ruff check .
py -3.12 -m mypy src
py -3.12 -m pytest -q
```

See [ARCHITECTURE.md](docs/ARCHITECTURE.md), [SECURITY.md](docs/SECURITY.md), [RUNBOOK.md](docs/RUNBOOK.md), [DECISIONS.md](docs/DECISIONS.md), and [ASSUMPTIONS.md](docs/ASSUMPTIONS.md).
