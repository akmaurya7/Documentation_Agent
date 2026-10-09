# DocAgent

DocAgent is a fail-closed service foundation for keeping GitHub repository documentation aligned with code changes. It authenticates GitHub webhooks, filters loops and noise, deduplicates deliveries, persists queued runs, and provides deterministic guards for secrets, paths, scope, branches, and Markdown output.

## Run locally

```powershell
Copy-Item .env.example .env
py -3.12 -m pip install -e ".[dev]"
$env:PYTHONPATH = "src"
py -3.12 -m uvicorn docagent.main:app --reload
```

Check `http://127.0.0.1:8000/healthz` and `/readyz`. The webhook endpoint is `POST /webhooks/github` and requires `X-Hub-Signature-256`, `X-GitHub-Event`, and `X-GitHub-Delivery` headers.

## Current delivery boundary

This release implements the M0 foundation, deterministic guardrails, durable run states, and a Redis-backed queue boundary. The default worker uses a fail-closed handler until the GitHub checkout and LLM orchestration adapters are implemented. It cannot publish a pull request yet.

The agent layer is now available as a tested library boundary. It validates the policy prompt hash, exposes typed guarded tools, redacts tool results, restricts writes to `docs_root`, and requires the model to finish through `finalize`. The queue worker does not enable it automatically until a full GitHub run handler and publisher are installed.

For the local stack, start Redis, the API, and the fail-closed worker with `docker compose up --build`. A queued event will be persisted and then finish as `blocked` until the next integration phase supplies a real run handler.

## Verification

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m ruff check .
py -3.12 -m mypy src
py -3.12 -m pytest -q
```

See [ARCHITECTURE.md](docs/ARCHITECTURE.md), [SECURITY.md](docs/SECURITY.md), [RUNBOOK.md](docs/RUNBOOK.md), [DECISIONS.md](docs/DECISIONS.md), and [ASSUMPTIONS.md](docs/ASSUMPTIONS.md).
