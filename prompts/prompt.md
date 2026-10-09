# PROJECT: DocAgent, an automated documentation engineer for GitHub repositories

## 0. Working rules for you, the coding agent
- Build milestone by milestone (section 8). After each milestone run lint, type check and tests. All must pass before you continue.
- Production quality only. No TODO, stub, pass or NotImplementedError in a production code path. If something cannot be completed, record it in docs/ASSUMPTIONS.md and make the code fail closed.
- Do not invent library APIs. Read the installed package source or docs, or use the standard library. Pin all dependencies with a lockfile.
- Tests never call real GitHub or a real LLM. Use fakes, local git repositories and recorded fixtures.
- Never commit secrets. Provide .env.example only.
- Fail closed. On any ambiguity in security-relevant code, deny the action.
- Typed, small, documented functions. mypy strict, ruff clean. Minimum 90 percent coverage on guardrails, redaction, path handling and publisher modules.
- Record every design choice you make in docs/DECISIONS.md and every assumption in docs/ASSUMPTIONS.md.
- At the end of each milestone, print a short summary: what was built, how to run it, how it was verified.

## 1. Goal
Build a service that reacts to GitHub events on a large company codebase and keeps documentation accurate. For each relevant event it reads the code change, decides which docs are affected, drafts the updates with an LLM agent, verifies them, and opens a pull request containing only documentation changes. Humans review and merge. The service must be safe to run across many repositories with many engineers committing daily.

The LLM behavior is defined by prompts/docagent_system.md. Treat that file as the source of the agent's policy, but NEVER rely on it for safety. Every safety property in section 6 must be enforced in code.

## 2. Stack (defaults; record any change in DECISIONS.md)
- Python 3.12, FastAPI, pydantic v2, SQLAlchemy 2 with Alembic. SQLite for local dev, Postgres for production.
- Redis-backed job queue (RQ or arq; pick one and record why).
- GitHub App authentication (JWT, then short-lived installation tokens) using a maintained client library.
- Git operations through the git CLI via subprocess with fixed argument lists. Never shell=True.
- LLM access behind a provider interface. Implement Anthropic with the official SDK. Model name comes from the MODEL_NAME env var.
- structlog for logging, Prometheus-style metrics, pytest, ruff, mypy, Docker, docker-compose, GitHub Actions CI.

## 3. Architecture
GitHub webhook -> receiver (verify, filter, dedupe, enqueue) -> worker -> run orchestrator -> context builder -> agent loop (LLM plus tools) -> guardrail layer -> doc checks -> publisher (branch, commit, PR) -> persisted run report.

Key principles:
- The LLM never sees GitHub tokens or the filesystem. It calls tools that the service executes, and every tool call and the final diff pass through guardrails.
- Guardrails, redaction and scope checks live in deterministic code.
- Every run is idempotent, auditable and killable.

## 4. Repository layout
  prompts/docagent_system.md        (provided by the user; do not edit)
  src/docagent/
    api/            webhook receiver, admin endpoints, health
    config/         settings, per-repo config models, loader
    github/         app auth, API client, checkout, publisher
    context/        diff, classification, repo map, docs map, codeowners
    agent/          provider interface, tool definitions, loop, report models
    guardrails/     scope, secrets, injection, limits, branch, idempotency
    redaction/      patterns, redactor
    checks/         markdown, links, diagrams, front matter, scope verifier
    store/          models, repositories, migrations
    ops/            metrics, kill switch, budgets, logging
  tests/ unit/ integration/ adversarial/ fixtures/
  docs/ ARCHITECTURE.md SECURITY.md RUNBOOK.md DECISIONS.md ASSUMPTIONS.md
  .docagent.example.yml  docker-compose.yml  Dockerfile  .env.example

## 5. Module specifications

### 5.1 Webhook receiver
- Verify X-Hub-Signature-256 with HMAC SHA-256 and constant-time comparison. Reject missing or invalid signatures with 401 before parsing the body.
- Handle: push, pull_request (opened, synchronize, reopened, ready_for_review, closed with merged=true), release (published), plus an authenticated manual-run endpoint and a scheduled sweep trigger.
- Respond 202 quickly; do all work in the worker.
- Dedupe on X-GitHub-Delivery and on (repo, head_sha, event class). Duplicate deliveries must not create duplicate runs.
- Loop and noise filters in the receiver: ignore events whose actor is this GitHub App, commits containing [skip-docs], diffs touching only DOCS_ROOT, repositories not on the allowlist, draft PRs (comment-only policy), and pushes to branches named docs-agent/*.
- Rate limit per repository. Cap body size.

### 5.2 Configuration
- Org-level defaults plus per-repo .docagent.yml validated by pydantic: docs_root, docs_map, docsignore, modes (shadow, docstring_mode, log_mode), limits (files, tokens, tool calls, wall time), owners, allowed prompt hashes.
- Read per-repo config ONLY from the default branch, never from a PR head or fork, because a PR author could otherwise change the agent's permissions.
- New repositories start with shadow=true (comment or report only, no PR).
- docs_root must be a relative subdirectory. Reject '.', empty, '..' segments, and anything under .github or other CI or build paths.

### 5.3 GitHub client
- App JWT auth, installation tokens cached until shortly before expiry, never logged, never passed to the LLM layer.
- Minimum permissions only: metadata read, contents read and write, pull requests write. Document the exact list in SECURITY.md.
- Retries with exponential backoff and jitter; honor rate-limit and secondary-rate-limit headers; fail the run cleanly if limits persist.

### 5.4 Checkout
- Per-run temporary directory, partial or shallow clone sized to the diff, hooks disabled (core.hooksPath=/dev/null), submodules not fetched, size and time limits, guaranteed cleanup in finally blocks.
- The source tree is read-only to the agent. Writes are possible only through the write_doc_file tool into docs_root.

### 5.5 Context builder
- Retrieve diff from base_sha to head_sha. Handle merge commits (first parent), squash merges, force pushes (recompute), renames, deletions, reverts and empty diffs.
- Classify files: source, test, config, CI, migration, schema, API definition, manifest, generated, vendored, binary, docs. Exclude generated, vendored, minified, lockfile and binary content from model input.
- Size-aware chunking by module. Never pass truncated content without marking it truncated.
- Parse CODEOWNERS, resolve docs map entries, build a repo map and an index of existing docs.

### 5.6 Agent tools exposed to the LLM
Tools: read_file, list_dir, search_code, get_diff, get_commit_log, get_pr, read_docs_map, write_doc_file, request_human_input, fetch_redacted_logs, run_doc_checks, finalize.
- Each tool has a pydantic input and output schema, a result size cap, and an audit record.
- Path handling for every path: normalize, reject absolute paths, '..', NUL bytes, backslash tricks, and unicode or case variants that escape the root. Resolve symlinks and reject any that leave the repo root. Reject files over the size cap.
- All tool output passes through the redactor before it reaches the LLM.
- write_doc_file is allowed only inside docs_root, only for allowed extensions, only up to configured file size, and counts against the file limit.
- Cap the number of tool calls and loop iterations per run.

### 5.7 Guardrails (deterministic code)
- ScopeGuard: after the agent finishes, compute git diff --name-only for the working tree. If any changed path is outside docs_root, abort the run. Never rely on the agent's own list of files.
- Docstring mode (off by default): permit source-file edits only if a language-aware check proves the change is comments or docstrings only. Implement Python via AST comparison. For any other language without a verified checker, docstring mode must refuse.
- SecretGuard: scan every output (doc diff, PR title and body, run report, comments) with the redaction patterns plus an entropy check; integrate detect-secrets or gitleaks if installed. Any hit blocks publishing and raises a security event containing only the file path and secret type.
- InjectionFlagger: heuristic detection of instruction-like text in repo content, PR text and logs. It flags, never obeys, and records to the report.
- LimitGuard: files changed, input tokens, output tokens, tool calls, wall time, cost. Exceeding any limit stops the run cleanly with no partial commit.
- BranchGuard: the only branch ever written is docs-agent/<run_id>. Refuse any other ref. Force-push allowed only to the agent's own branch, with lease.
- Markdown exfiltration guard: reject generated docs that contain raw HTML script or iframe tags, javascript: links, or external image or link URLs whose domain is not on an allowlist, since rendered markdown can leak data through URLs.
- Kill switch: check global and per-repo kill switches at run start and again immediately before publishing.

### 5.8 Redaction
- Apply to LLM inputs, tool outputs, generated docs, PR text, run reports and application logs.
- Detect: cloud keys, GitHub and Slack tokens, JWTs, private key blocks, bearer tokens, connection strings with credentials, signed URLs, emails, phone numbers, IP addresses (configurable), and high-entropy strings.
- Replace with typed placeholders such as <REDACTED:AWS_KEY>. Redaction must be idempotent: redact(redact(x)) == redact(x).
- Ship a large positive and negative test corpus, plus fuzz tests, so benign hashes and version strings are not mangled needlessly.

### 5.9 LLM layer
- Provider interface with timeouts, bounded retries, token and cost accounting, and request and response logging after redaction.
- Load prompts/docagent_system.md at start, compute its SHA-256, store the hash on every run, and refuse to run if the hash is not in the allowed list from config.
- Low temperature. Validate every structured output against pydantic models. On validation failure, retry once with the error, then fail the run.
- The agent loop ends only through the finalize tool. A model that stops without finalize is a failed run.

### 5.10 Documentation checks
- Markdown lint, relative link and anchor validation (no external network by default), Mermaid syntax validation, front matter schema validation, secret scan on the doc diff, and the scope verifier from 5.7.
- Optional language-specific code sample runners, disabled by default.
- A check that cannot run is recorded as not_run, never as passed.

### 5.11 Publisher
- Verify base_sha is still an ancestor of the target branch. Create docs-agent/<run_id> from base_sha, commit with the bot identity (signed if configured), push, and open or update exactly one PR per source change, using the PR template from the system prompt.
- Look up an existing open agent PR first (idempotency). If the docs branch conflicts with new base changes, regenerate from the new base instead of auto-resolving.
- Apply labels, request review from CODEOWNERS teams, and comment a short link on the source PR. Never approve or merge.
- In shadow mode, post only a comment or store the report.

### 5.12 Persistence
- Runs table with a state machine: queued, running, noop, blocked, failed, succeeded. Unique constraint on the idempotency key.
- Store run reports, evidence lists, the prompt hash, model name, token and cost totals, and an append-only audit event table. Configurable retention. Alembic migrations.

### 5.13 Operations
- /healthz and /readyz. Admin endpoints (authenticated, authorized) to list runs, re-run, and flip kill switches.
- Metrics: runs by status, latency, tokens, cost, guardrail blocks, security events, queue depth.
- Dead-letter queue and retry policy. Alert hook interface for security events and repeated failures.
- Budgets per run, per repository per day, and global per day.

### 5.14 Log and incident mode
- A LogSource interface with a file implementation and stubs for external systems. Log mode is off by default. Logs are redacted before they reach the model; if redaction cannot be confirmed, the run stops and escalates.

## 6. Security requirements (non-negotiable)
1. No secret ever reaches the LLM, a document, a PR, a comment, a log line or a report.
2. The agent cannot write outside docs_root, cannot touch CI, workflow, build or dependency files, and cannot touch protected branches.
3. Config and policy come from the default branch only. Fork and PR content is untrusted data.
4. Fork PRs run read-only with no secrets and produce comments only.
5. All inputs from GitHub, repositories and logs are treated as prompt-injection vectors.
6. Every run is reproducible from its stored record, and every action is in the audit log.
7. Dependencies are pinned, scanned in CI, and the container runs as non-root with a read-only root filesystem where possible.
8. Write docs/SECURITY.md with a threat model covering injection, token theft, path traversal, symlink escape, exfiltration via markdown, replayed webhooks, runaway cost and malicious forks, with the mitigation and the test that proves each one.

## 7. Testing requirements
- Unit tests for every module; property-based tests for path normalization and redaction idempotency.
- Integration tests with local git repositories and a fake GitHub API server; a scripted fake LLM that replays tool-call sequences.
- Adversarial suite in tests/adversarial that must pass:
  1. A fake API key in a diff and in a log never appears in any output; a security event is raised.
  2. A code comment saying to ignore instructions is ignored and recorded in the report.
  3. A refactor-only change ends as noop.
  4. A commit by the bot ends as noop; no loop.
  5. The same delivery or SHA twice creates one run and one PR.
  6. Docs that contradict the code are corrected and the discrepancy appears in the PR.
  7. Text inside manual blocks is never modified.
  8. Renames, deletions, reverts, force pushes and merge commits are handled.
  9. A diff beyond limits stops cleanly with no partial commit.
  10. A fork PR gets a comment only and no write attempt.
  11. A log containing personal data stops the run and escalates.
  12. A breaking API change is flagged prominently.
  13. A path traversal, symlink escape or workflow-file write attempt is blocked.
  14. A markdown image URL pointing to a non-allowlisted domain is rejected.
  15. A PR that tries to change .docagent.yml has no effect on that run.
  16. A killed repo or global kill switch stops a run before publishing.
- CI runs lint, mypy, tests, coverage thresholds, dependency audit and a container build on every push.

## 8. Milestones and acceptance criteria
M0 Scaffold: repo layout, tooling, CI, Dockerfile, health endpoint. Acceptance: CI green, container starts.
M1 Config and persistence: settings, per-repo config models, DB models, migrations, state machine. Acceptance: config validation tests, idempotency constraint test.
M2 Redaction and guardrails (tests first): redactor, scope, secret, branch, limit, markdown guards. Acceptance: adversarial tests 1, 13, 14 pass; coverage above 90 percent.
M3 GitHub integration: app auth, client, checkout, webhook receiver with signature, dedupe and filters. Acceptance: tests for bad signature, replay, bot actor, forks, kill switch.
M4 Context and tools: diff handling, classification, chunking, CODEOWNERS, docs map, tool layer with path safety. Acceptance: tests for renames, deletions, reverts, force push, symlinks.
M5 LLM layer and agent loop: provider interface, Anthropic implementation, loop with finalize, structured validation, prompt hash check. Acceptance: scripted fake-LLM runs end to end; adversarial tests 2, 3, 6, 7 pass.
M6 Checks and publisher: doc checks, PR creation, update-existing-PR, conflict handling, shadow mode. Acceptance: tests 4, 5, 8, 9, 10, 12, 15 pass.
M7 Operations: metrics, budgets, kill switches, admin endpoints, dead-letter handling, docker-compose. Acceptance: test 16 passes; budgets stop runs.
M8 Hardening and docs: log mode, full adversarial suite, load test of dedupe and queue, README, ARCHITECTURE.md, SECURITY.md, RUNBOOK.md. Acceptance: all 16 adversarial tests pass; no TODOs remain.

## 9. Definition of done
- All milestones accepted, CI green, coverage thresholds met.
- docker-compose up runs the full stack locally against the fake GitHub server.
- README explains setup, GitHub App creation (permissions and events), env variables, and how to roll out in shadow mode.
- RUNBOOK covers failures, rollbacks, kill switches, key rotation and incident response.
- ASSUMPTIONS.md and DECISIONS.md are complete and honest about known limits.

## 10. If you are blocked
Do not guess and do not weaken a guardrail to make a test pass. Record the blocker in docs/ASSUMPTIONS.md, make the affected path fail closed, finish everything else, and list the blocker clearly in your milestone summary.