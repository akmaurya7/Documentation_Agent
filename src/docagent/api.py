"""FastAPI webhook and health surface."""

from __future__ import annotations

import hmac
import json

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .guardrails import verify_signature
from .queue import RedisRunQueue, RunQueue
from .store import RunStore


def create_app(
    settings: Settings | None = None,
    store: RunStore | None = None,
    queue: RunQueue | None = None,
) -> FastAPI:
    """Create an application with explicit dependencies for deterministic tests."""
    config = settings or Settings()  # type: ignore[call-arg]
    run_store = store or RunStore(config.database_path, config.database_url)
    run_queue = queue or RedisRunQueue(config.queue_url)
    app = FastAPI(title="DocAgent", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> dict[str, str]:
        if config.kill_switch or run_store.kill_switch_enabled():
            raise HTTPException(status_code=503, detail="kill switch is active")
        return {"status": "ready"}

    @app.get("/metrics", response_class=JSONResponse)
    async def metrics() -> dict[str, int]:
        """Expose non-sensitive run counters for a metrics scraper."""
        return {f"docagent_runs_{key}": value for key, value in run_store.status_counts().items()}

    def require_admin(request: Request) -> None:
        supplied = request.headers.get("x-docagent-admin-token", "")
        if not config.admin_token or not hmac.compare_digest(supplied, config.admin_token):
            raise HTTPException(status_code=401, detail="admin authentication required")

    @app.get("/admin/runs")
    async def admin_runs(request: Request, limit: int = 100) -> list[dict[str, str]]:
        require_admin(request)
        return [
            {
                "idempotency_key": run.idempotency_key,
                "delivery_id": run.delivery_id,
                "repo": run.repo,
                "head_sha": run.head_sha,
                "event_type": run.event_type,
                "status": run.status.value,
            }
            for run in run_store.list_runs(limit)
        ]

    @app.post("/admin/kill-switch")
    async def admin_kill_switch(request: Request) -> dict[str, bool]:
        require_admin(request)
        payload = await request.json()
        if not isinstance(payload, dict) or not isinstance(payload.get("enabled"), bool):
            raise HTTPException(status_code=400, detail="enabled boolean is required")
        config.kill_switch = payload["enabled"]
        run_store.set_kill_switch(config.kill_switch)
        return {"enabled": config.kill_switch}

    @app.post("/admin/rerun")
    async def admin_rerun(request: Request) -> dict[str, str]:
        require_admin(request)
        payload = await request.json()
        key = payload.get("idempotency_key") if isinstance(payload, dict) else None
        if not isinstance(key, str) or not key:
            raise HTTPException(status_code=400, detail="idempotency_key is required")
        try:
            rerun = run_store.requeue(key)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="run not found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        try:
            await run_queue.enqueue(rerun)
        except Exception as exc:
            raise HTTPException(status_code=503, detail="queue unavailable") from exc
        return {"idempotency_key": key, "status": rerun.status.value}

    @app.post("/webhooks/github")
    async def github_webhook(request: Request) -> JSONResponse:
        body = await request.body()
        if len(body) > config.max_webhook_bytes:
            raise HTTPException(status_code=413, detail="webhook body too large")
        signature = request.headers.get("x-hub-signature-256")
        if not verify_signature(body, signature, config.webhook_secret):
            raise HTTPException(status_code=401, detail="invalid signature")
        event = request.headers.get("x-github-event", "")
        delivery = request.headers.get("x-github-delivery", "")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="invalid JSON payload") from exc
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="webhook payload must be an object")
        if config.kill_switch or run_store.kill_switch_enabled():
            return JSONResponse(
                {"accepted": False, "reason": "kill switch is active"}, status_code=202
            )
        repo = str(payload.get("repository", {}).get("full_name", ""))
        pull_request = payload.get("pull_request", {})
        source_pr_number = pull_request.get("number")
        if not isinstance(source_pr_number, int):
            source_pr_number = None
        head_sha = str(payload.get("after") or pull_request.get("head", {}).get("sha", ""))
        base_sha = str(pull_request.get("base", {}).get("sha", ""))
        base_branch = str(pull_request.get("base", {}).get("ref", ""))
        if not base_sha and event == "push":
            base_sha = str(payload.get("before", ""))
        if not base_branch:
            base_branch = str(payload.get("ref", "")).removeprefix("refs/heads/")
        source_url = str(payload.get("repository", {}).get("clone_url", ""))
        actor = str(payload.get("sender", {}).get("login", ""))
        if not delivery or not repo or not head_sha:
            raise HTTPException(status_code=400, detail="missing delivery, repository, or head sha")
        if actor in {config.app_name, config.github_bot_login} or "[skip-docs]" in str(payload):
            return JSONResponse({"accepted": False, "reason": "filtered"}, status_code=202)
        head_repo = str(pull_request.get("head", {}).get("repo", {}).get("full_name", ""))
        if event == "pull_request" and head_repo and head_repo != repo:
            return JSONResponse({"accepted": False, "reason": "fork"}, status_code=202)
        key = f"{repo}:{head_sha}:{event}"
        accepted = run_store.enqueue_once(
            key, delivery, repo, head_sha, event, source_url, base_sha, base_branch,
            source_pr_number,
        )
        queued_run = run_store.get(key)
        if queued_run is None:
            raise HTTPException(status_code=503, detail="run could not be persisted")
        if accepted or queued_run.status.value == "queued":
            try:
                await run_queue.enqueue(queued_run)
            except Exception as exc:
                raise HTTPException(status_code=503, detail="queue unavailable") from exc
        return JSONResponse({"accepted": accepted, "queued": True}, status_code=202)

    return app
