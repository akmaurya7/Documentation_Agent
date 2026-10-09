---
title: DocAgent usage and operations guide
owner: documentation-platform
last_verified_commit: d9542dd
status: draft
---

# DocAgent usage and operations guide

DocAgent is a fail-closed service that analyzes relevant GitHub changes and
prepares documentation-only pull requests. It authenticates webhook requests,
stores each run durably, places work on Redis, checks out the exact source
commit, gives a bounded tool surface to the configured language-model provider,
validates the resulting documentation, and publishes only approved files.

Human reviewers remain responsible for reviewing and merging documentation pull
requests. DocAgent does not approve or merge pull requests.

## 1. How the system works

The runtime has two processes and two infrastructure dependencies:

```text
GitHub webhook
    -> FastAPI authentication, filtering, and deduplication
    -> SQLite or PostgreSQL run record
    -> Redis queue
    -> worker
    -> exact-source checkout
    -> bounded agent and guarded tools
    -> deterministic documentation checks
    -> shadow result or documentation pull request
```

### Webhook/API process

The API process:

1. Reads the raw request body.
2. Rejects bodies over `DOCAGENT_MAX_WEBHOOK_BYTES`.
3. Verifies `X-Hub-Signature-256` with the configured webhook secret.
4. Parses the JSON payload and requires a delivery ID, repository, and head SHA.
5. Filters bot-authored events, `[skip-docs]` payloads, fork pull requests, and
   active kill-switch requests.
6. Creates one idempotent queued run using repository, head SHA, and event type.
7. Enqueues the persisted run in Redis.

The webhook returns quickly. Documentation work is never performed inline in
the webhook request.

### Worker process

The worker dequeues one run, changes it from `queued` to `running`, and then:

1. Uses the exact source SHA for a temporary detached checkout.
2. Computes the complete name-status diff against the trusted base SHA.
3. Ends as `noop` for an empty or documentation-only change.
4. Runs the bounded agent for code changes that may affect documentation.
5. Persists the sanitized report, including prompt hash and token totals.
6. Runs Markdown, link, Mermaid, front-matter, secret, and scope checks.
7. Publishes in shadow mode or creates/updates a documentation pull request.
8. Removes the temporary checkout in the cleanup path.

The worker returns `blocked` or `failed` rather than guessing when required
metadata, credentials, checks, or provider output are unavailable.

## 2. Run states

| State | Meaning |
| --- | --- |
| `queued` | Persisted and waiting for a worker. |
| `running` | Claimed by a worker. |
| `noop` | No documentation work was required or the checked result was empty. |
| `blocked` | A policy, configuration, safety, or verification gate prevented completion. |
| `failed` | Processing exhausted its retry bound or encountered a terminal failure. |
| `succeeded` | Documentation was successfully published. |

The normal state path is:

```text
queued -> running -> noop
                 -> blocked
                 -> failed
                 -> succeeded
```

Transient worker exceptions are retried up to `DOCAGENT_MAX_ATTEMPTS`. Each
retry is audited. Once the bound is reached, the run is marked `failed` and a
redacted dead-letter record is stored. An operator may explicitly re-run a
`failed`, `blocked`, or `noop` run through the authenticated admin endpoint.

## 3. Prerequisites

### Local development

- Windows, Linux, or macOS with Python 3.12+
- Git
- Redis for the worker queue when running the full stack
- Docker Desktop if using Compose

SQLite is suitable for single-process local development. PostgreSQL is the
production persistence target.

### Production integrations

Provide:

- A GitHub App installation on each target repository.
- Credentials for the selected model provider: an Anthropic API key, or an
  already authenticated Antigravity CLI account session.
- A webhook secret shared between GitHub and DocAgent.
- A private key file for the GitHub App.
- A strong admin token for operational endpoints.
- Durable PostgreSQL and Redis services.
- A trusted allowlist containing the SHA-256 hash of the policy prompt.

Create the GitHub App with only the permissions needed for this workflow:
metadata read, contents read/write, and pull-request write. Do not grant
administrator, Actions, secrets, or workflow permissions.

## 4. Install and run locally

From the repository root:

```powershell
Copy-Item .env.example .env
py -3.12 -m pip install -e ".[dev]"
$env:PYTHONPATH = "src"
```

Edit `.env` and set at least:

```text
DOCAGENT_WEBHOOK_SECRET=<random value of at least 16 characters>
ANTHROPIC_API_KEY=<provider key>
DOCAGENT_ADMIN_TOKEN=<strong random operator token>
```

### Antigravity CLI account login

Install the official `agy` CLI on the same host as the DocAgent worker and log
in interactively once:

```powershell
irm https://antigravity.google/cli/install.ps1 | iex
agy
```

Complete the browser sign-in, then configure:

```text
$env:DOCAGENT_PROVIDER = "antigravity_cli"
$env:DOCAGENT_MODEL_NAME = "gemini-3.8-flash-low"
$env:DOCAGENT_ANTIGRAVITY_COMMAND = "agy"
```

The provider invokes the documented Antigravity headless stream protocol in an
isolated temporary directory. It sends the existing sanitized conversation and
tool schemas to `agy`, accepts only a typed JSON result, and does not grant the
CLI access to the repository checkout. DocAgent never reads, copies, or stores
the Antigravity OAuth token; the CLI uses its own operating-system keyring
session. The official CLI documentation describes local keyring sign-in and
headless runs using cached credentials.

For Docker or a remote worker, the image must include a pinned `agy` binary and
the runtime identity must have its own authenticated keyring/profile. A host
login is not automatically visible inside a container. If that profile is
unavailable, DocAgent fails closed rather than falling back to an API key.

Start the API:

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m uvicorn docagent.main:app --reload
```

For local API-only checks:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/healthz
Invoke-RestMethod http://127.0.0.1:8000/readyz
Invoke-RestMethod http://127.0.0.1:8000/metrics
```

Run the worker in a second terminal. Redis must be available at
`DOCAGENT_QUEUE_URL`:

```powershell
$env:PYTHONPATH = "src"
py -3.12 -m docagent.worker_main
```

Without complete GitHub App configuration, the worker deliberately uses the
fail-closed handler and finishes queued runs as `blocked`.

## 5. Run the Docker Compose stack

Compose starts PostgreSQL, Redis, the API, and the worker:

```powershell
Copy-Item .env.example .env
# Edit .env and provide the required secrets and provider settings.
docker compose up --build
```

The Compose services override the database URL to use the local PostgreSQL
container. Before a production deployment, run the migrations against the
target database:

```powershell
py -3.12 -m alembic upgrade head
```

The API is available on port `8000`. The API and worker containers run as a
non-root user with a read-only filesystem and a temporary filesystem mounted at
`/tmp`.

## 6. Prompt integrity configuration

The worker will not run the real handler unless the configured policy prompt is
trusted. Compute the prompt hash from the exact file deployed with the service:

```powershell
(Get-FileHash prompts/docagent_system.md -Algorithm SHA256).Hash.ToLower()
```

Put that hash in the JSON list used by the settings loader:

```text
DOCAGENT_ALLOWED_PROMPT_HASHES=["paste-the-lowercase-sha256-here"]
```

If the file changes, update the allowlist deliberately, review the change, and
restart the worker. A hash mismatch blocks processing before model execution.

## 7. Configuration reference

| Setting | Purpose | Local default or example |
| --- | --- | --- |
| `DOCAGENT_WEBHOOK_SECRET` | HMAC secret for GitHub webhooks. | Required. |
| `ANTHROPIC_API_KEY` | Anthropic provider credential. | Required only when provider is `anthropic`. |
| `DOCAGENT_PROVIDER` | Provider adapter. | `anthropic` or `antigravity_cli` |
| `DOCAGENT_ANTIGRAVITY_COMMAND` | Antigravity CLI executable. | `agy` |
| `DOCAGENT_PROMPT_PATH` | Trusted policy prompt path. | `prompts/docagent_system.md` |
| `DOCAGENT_ALLOWED_PROMPT_HASHES` | Approved prompt SHA-256 values. | `[]` |
| `DOCAGENT_DATABASE_PATH` | SQLite file path. | `data/docagent.sqlite3` |
| `DOCAGENT_DATABASE_URL` | PostgreSQL or alternate SQLAlchemy URL. | Blank for local SQLite. |
| `DOCAGENT_QUEUE_URL` | Redis connection URL. | `<redis-url>` |
| `DOCAGENT_GITHUB_APP_ID` | GitHub App ID. | Required for real runs. |
| `DOCAGENT_GITHUB_PRIVATE_KEY_PATH` | Private-key file path. | Required for real runs. |
| `DOCAGENT_GITHUB_BOT_LOGIN` | Bot login filtered from loops. | `docagent[bot]` |
| `DOCAGENT_ADMIN_TOKEN` | Token for `/admin/*`. | Required for administration. |
| `DOCAGENT_SHADOW` | Store/report without branch publication. | `true` |
| `DOCAGENT_DOCS_ROOT` | Allowed documentation directory. | `docs` |
| `DOCAGENT_ALLOWED_EXTERNAL_DOMAINS` | Markdown domains allowed by checks. | Empty set. |
| `DOCAGENT_KILL_SWITCH` | Startup kill switch. | `false` |
| `DOCAGENT_MAX_ATTEMPTS` | Maximum worker attempts before dead-lettering. | `3` |
| `DOCAGENT_REVIEWERS` | Optional GitHub reviewer logins. | `[]` |
| `DOCAGENT_LABELS` | Labels applied to generated PRs. | `["documentation"]` |

Run limits are configured through `DOCAGENT_LIMITS` in settings deployments or
the validated `RunLimits` model: maximum files, input tokens, output tokens,
tool calls, and wall-clock seconds.

## 8. Configure the GitHub webhook

Set the GitHub App webhook URL to:

```text
https://<docagent-host>/webhooks/github
```

Use the same value as `DOCAGENT_WEBHOOK_SECRET`. Subscribe to the repository
events needed by the deployment, normally push and pull-request events.

GitHub must send these headers:

- `X-Hub-Signature-256`
- `X-GitHub-Event`
- `X-GitHub-Delivery`

A missing or invalid signature receives HTTP 401. A valid but filtered event
receives HTTP 202 with a reason. Replayed deliveries are deduplicated and do
not create a second persisted run.

## 9. Shadow mode and publication

Keep `DOCAGENT_SHADOW=true` for the first rollout. In shadow mode the system
still performs checkout, analysis, redaction, checks, report persistence, and
audit logging, but the publisher does not push a branch or create a pull
request.

Before enabling publication, verify:

1. The prompt hash is allowlisted.
2. The GitHub App is installed on a test repository.
3. The worker can clone the repository using its short-lived installation token.
4. Reports contain no secrets or personal data.
5. Documentation checks pass.
6. The base SHA and default branch policy are correct.
7. The kill switch has been tested.

Then set:

```text
DOCAGENT_SHADOW=false
```

The publisher creates a branch named `docs-agent/<run_id>`, stages only
`DOCAGENT_DOCS_ROOT`, verifies scope and protected paths, commits the change,
pushes the branch, and opens one documentation pull request. Existing
bot-created documentation PRs for the same source SHA are updated instead of
duplicated. Labels, reviewer requests, and the source-PR link are applied when
the corresponding settings and metadata are available.

## 10. Administration and monitoring

All admin calls require:

```text
X-DocAgent-Admin-Token: <DOCAGENT_ADMIN_TOKEN>
```

List recent runs:

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/admin/runs `
  -Headers @{"X-DocAgent-Admin-Token" = $env:DOCAGENT_ADMIN_TOKEN}
```

Enable the shared kill switch:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/admin/kill-switch `
  -Headers @{"X-DocAgent-Admin-Token" = $env:DOCAGENT_ADMIN_TOKEN} `
  -ContentType "application/json" `
  -Body '{"enabled":true}'
```

Disable it after the incident is understood:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/admin/kill-switch `
  -Headers @{"X-DocAgent-Admin-Token" = $env:DOCAGENT_ADMIN_TOKEN} `
  -ContentType "application/json" `
  -Body '{"enabled":false}'
```

Re-run a failed, blocked, or noop run:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri http://127.0.0.1:8000/admin/rerun `
  -Headers @{"X-DocAgent-Admin-Token" = $env:DOCAGENT_ADMIN_TOKEN} `
  -ContentType "application/json" `
  -Body '{"idempotency_key":"repository:sha:push"}'
```

`/metrics` currently exposes non-sensitive counts by persisted run state, for
example `docagent_runs_queued` and `docagent_runs_succeeded`. Protect this
endpoint at the network boundary.

## 11. Security behavior

DocAgent treats webhook payloads, repository content, diffs, commit messages,
filenames, Markdown, and logs as untrusted input. The system:

- Verifies webhook HMAC signatures using constant-time comparison.
- Rejects traversal, absolute paths, symlink escapes, and writes outside the
  configured documentation root.
- Protects workflow, CI, dependency, build, and other protected paths.
- Redacts credentials, tokens, private keys, and connection strings before
  reports, tool results, logs, and PR bodies.
- Rejects personal-data patterns in file-based log input.
- Rejects active HTML and non-allowlisted external Markdown URLs.
- Bounds files, model tokens, tool calls, and wall time.
- Uses temporary askpass transport for GitHub installation tokens so tokens do
  not appear in remotes, command arguments, reports, or logs.
- Requires a final typed `finalize` result before accepting an agent report.

Never place secrets in repository files, webhook payload examples, test output,
PR descriptions, or issue comments.

## 12. Troubleshooting

### `/healthz` succeeds but `/readyz` fails

Check the persisted kill switch and the database connection. A kill switch is
an intentional HTTP 503. Clear it through the authenticated admin endpoint
only after the incident is understood.

### Runs remain `queued`

Check that Redis is reachable from the worker, that the worker is running, and
that API and worker use the same `DOCAGENT_QUEUE_URL` and database. Inspect
`/admin/runs` and worker logs without copying secrets.

### Runs become `blocked` immediately

Check GitHub App ID, private-key path, prompt path, and prompt hash allowlist.
Also confirm that the run contains source URL, base SHA, and base branch.
Missing integration configuration intentionally fails closed.

### Runs become `failed`

Inspect the run audit events and dead-letter record. Transient exceptions are
retried up to `DOCAGENT_MAX_ATTEMPTS`. Correct the external failure, then use
`/admin/rerun` for the affected run.

### No pull request appears

Confirm `DOCAGENT_SHADOW=false`, the publisher checks passed, the GitHub App has
contents and pull-request write permissions, and the worker can push using its
installation token. A documentation-only or empty change correctly ends as
`noop`.

### A webhook is accepted but no new run is visible

The event may have been deduplicated, filtered as bot-authored, filtered by
`[skip-docs]`, rejected as a fork, or stopped by the kill switch. Check the
HTTP response reason and the audit trail.

## 13. Safe rollout and rollback

1. Run lint, type checks, tests, coverage, dependency audit, and the container
   build in CI.
2. Apply database migrations before starting multiple workers.
3. Deploy API and worker with shadow mode enabled.
4. Send a signed test webhook and inspect its report, state, and audit events.
5. Confirm Redis recovery, retry behavior, and dead-letter handling.
6. Enable publication for a small test repository.
7. Expand repository coverage only after reviewing generated PRs.

For an emergency rollback, enable the shared kill switch, stop publication by
setting `DOCAGENT_SHADOW=true`, and preserve run/audit identifiers for incident
review. Do not delete the database or Redis data while investigating.

## 14. Current limitations

The following deployment work remains explicit rather than silently assumed:

- Fork pull requests are currently rejected at webhook intake; the planned
  comment-only fork workflow is not yet enabled.
- Cost totals, daily repository/global budgets, and full latency/queue-depth
  metrics require deployment-specific accounting and are not represented by
  the current status-counter endpoint.
- Log mode has safe file ingestion primitives but is not yet a complete
  end-to-end incident-report workflow.
- Conflict regeneration, signed commits, and a complete adversarial/load-test
  suite are still release-gate work.
- Antigravity account login is supported for a native worker host. Container
  packaging and managed keyring provisioning remain deployment-specific.

Review [SECURITY.md](SECURITY.md), [RUNBOOK.md](RUNBOOK.md),
[ARCHITECTURE.md](ARCHITECTURE.md), [ASSUMPTIONS.md](ASSUMPTIONS.md), and
[DECISIONS.md](DECISIONS.md) before enabling production writes.
