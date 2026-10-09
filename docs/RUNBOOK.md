# Runbook

## Start locally

1. Copy `.env.example` to `.env` and set a random webhook secret with at least 16 characters.
2. Install the package with `pip install -e ".[dev]"`.
3. Run `uvicorn docagent.main:app --reload`.
4. Check `/healthz` and `/readyz`.

## Stop publishing

Set `DOCAGENT_KILL_SWITCH=true` and restart the service. Readiness will fail and no new webhook will be accepted for processing.

## Incident response

Preserve the run id and audit record. Do not copy secrets or raw personal data into tickets. Rotate the affected credential through the owning platform and review the security event metadata.
