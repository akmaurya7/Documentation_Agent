# DocAgent Implementation Phase Plan

This plan is the execution checklist for completing DocAgent from the current
foundation to a production-ready, fail-closed documentation agent. Work in
order. A phase is complete only when its exit criteria pass and its evidence is
recorded in the phase summary.

## Global rules

1. Preserve fail-closed behavior at every trust boundary.
2. Treat GitHub payloads, repository content, diffs, commit messages, PR text,
   filenames, and logs as untrusted input.
3. Never send secrets or personal data to the model, documents, PRs, comments,
   logs, or reports.
4. Do not modify CI, workflow, dependency, build, or protected-branch content.
5. Configuration and policy are read from the default branch, never from the
   PR being analyzed.
6. Every phase must run:

   ```powershell
   $env:PYTHONPATH = "src"
   py -3.12 -m ruff check .
   py -3.12 -m mypy src
   py -3.12 -m pytest -q
   ```

7. Add or update tests with every behavior change. Do not weaken a guardrail to
   make a test pass.
8. Record unresolved external assumptions in `docs/ASSUMPTIONS.md` and make
   the affected path return `blocked`.

## Current baseline

The repository currently has the M0 service foundation, deterministic
guardrails, persistence and queue boundary, plus a tested agent-library
boundary. The worker still uses a fail-closed handler; it cannot yet perform a
full GitHub checkout-to-documentation-PR run. The existing test suite passes,
but that is not acceptance of all phases.

## Phase 0 — Baseline and project contract

### Goal

Freeze the starting point and make the acceptance contract executable.

### Implement

- Confirm the package layout, supported Python version, environment variables,
  Docker files, and current state machine.
- Define one canonical run report schema and status vocabulary:
  `queued`, `running`, `noop`, `blocked`, `failed`, `succeeded`.
- Add a CI workflow that runs lint, type checking, tests, coverage, dependency
  audit, and container build.
- Add coverage configuration and the minimum project test markers.
- Keep the current health and readiness endpoints working.

### Tests and evidence

- CI runs successfully on a clean checkout.
- Container starts and `/healthz` responds successfully.
- `/readyz` reports unavailable dependencies instead of claiming readiness.
- A phase summary records the exact commands and results.

### Exit criteria

M0 is accepted only when CI is green and the container starts.

## Phase 1 — Configuration and durable persistence

### Goal

Make configuration, run state, idempotency, reports, and audit history durable
and safe for multiple workers.

### Implement

- Add validated application settings and per-repository configuration models.
- Define repository policy fields: docs root, default branch, shadow mode,
  limits, allowed hosts, kill switches, and bot identity.
- Keep SQLite for deterministic local development, but define the persistence
  interface needed by PostgreSQL.
- Add PostgreSQL models and Alembic migrations before production deployment.
- Add the run table with legal state transitions and timestamps.
- Add a unique idempotency constraint for delivery/repository/source SHA.
- Store prompt hash, model name, token/cost totals, evidence references,
  checks, files changed, limits hit, and redacted notes.
- Add append-only audit events and configurable retention.

### Tests and evidence

- Invalid settings fail at startup.
- Illegal state transitions are rejected.
- Duplicate delivery creates one run.
- Concurrent duplicate inserts resolve to one durable run.
- Persistence tests run against SQLite and the supported PostgreSQL test
  service or equivalent integration fixture.

### Exit criteria

Configuration validation and idempotency tests pass, migrations are repeatable,
and no run report can contain secret values.

## Phase 2 — Redaction and deterministic guardrails

### Goal

Protect every input/output boundary before integrations are enabled.

### Implement

- Redact API keys, tokens, private keys, credentials, connection strings, and
  configured secret patterns.
- Make redaction idempotent and preserve a typed security-event record.
- Enforce repository/docs-root containment after normalization and resolution.
- Reject symlink escapes, traversal, absolute paths, and unsafe write targets.
- Protect default/protected branches and disallow CI, workflow, build, and
  dependency file writes.
- Enforce diff size, file count, token, time, and cost limits.
- Reject active HTML and non-allowlisted Markdown URLs.
- Validate manual blocks and preserve their contents byte-for-byte.

### Tests and evidence

- Property tests cover path normalization and redaction idempotency.
- Adversarial tests 1, 13, and 14 pass.
- Secret matches never appear in tool output, reports, comments, or logs.
- A failed guard returns `blocked` with a safe reason and audit event.

### Exit criteria

Coverage for guardrails is at least 90 percent and all trust-boundary tests
pass.

## Phase 3 — GitHub authentication and webhook intake

### Goal

Receive only authentic, relevant GitHub events and enqueue each event once.

### Implement

- Implement GitHub App authentication and short-lived installation tokens.
- Implement HMAC SHA-256 webhook verification using constant-time comparison.
- Validate required event and delivery headers.
- Filter unsupported events, bot-authored commits, ref-only noise, and killed
  repositories before queueing.
- Handle forks as read-only/comment-only runs with no write token.
- Deduplicate replayed deliveries and source SHAs.
- Persist the event before returning from the webhook handler.
- Enqueue the idempotency key through the configured queue adapter.

### Tests and evidence

- Bad signature is rejected.
- Replayed delivery creates no second run.
- Bot-authored event becomes `noop`.
- Fork event cannot perform a write.
- Repository and global kill switches stop the run before checkout.
- Installation token expiry and refresh behavior are tested with a fake GitHub
  server.

### Exit criteria

Webhook, authentication, dedupe, fork, bot, and kill-switch tests pass without
network calls to real GitHub.

## Phase 4 — Checkout, diff context, and guarded tools

### Goal

Build a reproducible, read-only source context and safe documentation tool
surface.

### Implement

- Create temporary detached checkouts at the exact source SHA.
- Disable hooks, prompts, submodule recursion, and shell interpretation.
- Clean up checkouts on success, failure, cancellation, and timeout.
- Parse additions, deletions, renames, reverts, force pushes, and merge
  commits.
- Classify documentation-relevant versus refactor-only changes.
- Read CODEOWNERS and documentation maps from the trusted default branch.
- Chunk context within token and file limits.
- Expose only guarded read, search, diff, write, and finalize tools.
- Return redacted tool results and prevent writes outside `docs_root`.

### Tests and evidence

- Local git fixtures cover renames, deletions, reverts, force pushes, merge
  commits, and empty/refactor-only changes.
- Symlink and traversal writes are rejected.
- Tool results are redacted before the model sees them.
- Context is reproducible from repository and source SHA.

### Exit criteria

All checkout and context fixtures pass, and no tool can mutate an out-of-scope
path.

## Phase 5 — LLM provider and agent loop

### Goal

Run a bounded, policy-controlled documentation agent with deterministic test
replays.

### Implement

- Define the provider protocol and pinned Anthropic adapter.
- Load and hash the trusted policy prompt.
- Reject prompt-hash mismatch before model execution.
- Validate provider responses with typed models.
- Enforce tool-call count, token, time, and cost budgets.
- Redact all tool results and provider errors.
- Require `finalize` before accepting a successful report.
- Record evidence, changed files, unresolved questions, security flags, and
  checks in the run report.
- Treat malformed output, unavailable providers, tool errors, and budget hits as
  `blocked` or `failed` according to the state policy.

### Tests and evidence

- Scripted fake provider runs the complete loop without external API calls.
- Adversarial tests 2, 3, 6, and 7 pass.
- Prompt injection in code comments is ignored and recorded.
- Refactor-only changes end as `noop`.
- Contradictory docs are corrected only when evidence supports the change.
- Manual blocks remain unchanged.

### Exit criteria

The agent can produce a validated, reproducible report and cannot finish
without the required finalization step.

## Phase 6 — Checks and documentation publisher

### Goal

Publish only verified documentation changes, with safe PR idempotency.

### Implement

- Run Markdown, links, Mermaid, front matter, secrets, and scope checks.
- Make unavailable checks fail closed; never report an unavailable check as
  passed.
- Create a documentation branch from the trusted default branch.
- Commit only approved documentation files with a deterministic message.
- Reuse an existing bot-created PR for the same source SHA.
- Handle existing PR updates, conflicts, empty changes, and retryable GitHub
  failures.
- Support shadow mode by storing the report and commenting without publishing.
- Use installation-token transport and never persist tokens in remotes or logs.
- Request CODEOWNERS review and add the report link to the source PR.
- Never approve or merge.

### Tests and evidence

- Adversarial tests 4, 5, 8, 9, 10, 12, and 15 pass.
- Bot commit cannot cause a loop.
- Duplicate source SHA produces one documentation PR.
- Diff-limit failure produces no partial commit.
- Fork runs comment only.
- Breaking API changes are prominent in the report and PR.
- `.docagent.yml` changes in the PR do not affect that run.

### Exit criteria

Publisher integration tests pass against a fake GitHub API and no secret or
out-of-scope file reaches a commit, PR, or comment.

## Phase 7 — Operations and production controls

### Goal

Operate the service safely under retries, failures, load, and emergency stop
conditions.

### Implement

- Add metrics for status, latency, queue depth, tokens, cost, guardrail blocks,
  and security events.
- Enforce per-run, per-repository daily, and global daily budgets.
- Add authenticated and authorized admin endpoints to list runs, re-run safe
  inputs, and flip kill switches.
- Add retry policy and a dead-letter queue with bounded attempts.
- Add alert hooks for security events and repeated failures.
- Complete Redis-backed queue startup, readiness, worker shutdown, and recovery.
- Update Docker Compose for API, worker, Redis, and health checks.
- Add log correlation IDs and structured redacted logging.

### Tests and evidence

- Adversarial test 16 passes.
- Budget exhaustion stops work before publishing.
- Queue outage does not produce false success.
- Dead-letter behavior is deterministic.
- Unauthorized admin requests are rejected.
- Worker restart does not lose or duplicate a run.

### Exit criteria

The local stack runs end-to-end with the fake GitHub server and all operational
failure paths are observable and fail closed.

## Phase 8 — Log mode, hardening, and documentation

### Goal

Finish the product, validate the full threat model, and document operation.

### Implement

- Add a `LogSource` interface and file implementation; leave external sources
  behind explicit adapters.
- Keep log mode off by default.
- Redact logs before model access and stop if redaction cannot be confirmed.
- Produce sourced incident reports with UTC timelines, impact, detection,
  contributing factors, resolution, and follow-ups.
- Group recurring errors by signature and record first/last seen times.
- Run the full adversarial suite and dedupe/queue load tests.
- Complete `ARCHITECTURE.md`, `SECURITY.md`, `RUNBOOK.md`,
  `ASSUMPTIONS.md`, and `DECISIONS.md`.
- Remove stale TODOs, stubs, `pass`, and `NotImplementedError` from production
  paths, or document a fail-closed limitation explicitly.
- Verify dependency pins, vulnerability scanning, non-root container use, and
  read-only filesystem behavior where supported.

### Tests and evidence

- All 16 adversarial tests pass.
- Log personal-data input stops and escalates.
- Load tests show bounded dedupe and queue behavior.
- A clean checkout can follow the README to run the full local stack.
- Security documentation maps each required threat to mitigation and test.

### Exit criteria

M8 is accepted only when the full adversarial suite passes, no production TODOs
remain, and the operational documentation is complete.

## Final release gate

Release DocAgent only when every item below is true:

- M0 through M8 exit criteria are signed off.
- Lint, mypy, tests, coverage, dependency audit, and container build are green.
- The full stack starts with `docker compose up --build`.
- A fake GitHub event can complete the checkout, analysis, checks, and safe PR
  publication path.
- Shadow mode has been exercised before enabling write mode.
- Secrets, personal data, and untrusted PR configuration are blocked at every
  boundary.
- `README.md` documents setup, GitHub App permissions/events, environment
  variables, shadow rollout, and rollback.
- `RUNBOOK.md` covers failures, retries, rollbacks, kill switches, key rotation,
  dead letters, and incident response.
- `docs/ASSUMPTIONS.md` lists only real remaining deployment-specific limits.

## Phase summary template

Use this after every phase:

```text
Phase: M< number > — <name>
Status: accepted | blocked
Implemented: <short list>
Tests: <commands and result>
Security checks: <result>
Artifacts: <files, migration IDs, dashboards, or PRs>
Open assumptions: <none or exact items>
Next phase: <name>
```

## Deferred items

Do not add multi-provider LLM support, a plugin system, a UI, automatic merge,
or additional integrations until the release gate passes. Those features are
outside the requested completion path and would increase the security and
operational surface.
