# Runbook

## Start locally

1. Copy `.env.example` to `.env` and set a random webhook secret with at least 16 characters.
2. Install the package with `pip install -e ".[dev]"`.
3. Run `uvicorn docagent.main:app --reload`.
4. Check `/healthz` and `/readyz`.

## Worker and queue

Run `python -m docagent.worker_main` in a separate process. Redis is required
for the production queue. A missing GitHub App configuration intentionally
selects the fail-closed handler. Inspect `/metrics` and the authenticated
`/admin/runs` endpoint for state; failed worker attempts are recorded as dead
letters after the configured `DOCAGENT_MAX_ATTEMPTS` bound. Earlier transient
exceptions are requeued automatically and each retry is audited.

## Safe rollout

1. Deploy with shadow mode enabled.
2. Send a test webhook and confirm one queued run, one audit trail, and a
   report with no security flags.
3. Review the generated documentation and checks.
4. Enable publication only after the GitHub App permissions and branch policy
   are verified.
5. Keep the kill switch available to operators and test it before rollout.

## Stop publishing

Set `DOCAGENT_KILL_SWITCH=true` and restart the service. Readiness will fail and no new webhook will be accepted for processing.

The authenticated `POST /admin/kill-switch` endpoint toggles the shared
persisted switch without a restart. A restart is still required when changing
the environment variable.

To retry a failed, blocked, or noop run, call the authenticated
`POST /admin/rerun` endpoint with its `idempotency_key`. Successful and queued
runs are rejected; the old report is cleared, an audit event is written, and
the run is enqueued again.

## Incident response

Preserve the run id and audit record. Do not copy secrets or raw personal data into tickets. Rotate the affected credential through the owning platform and review the security event metadata.
