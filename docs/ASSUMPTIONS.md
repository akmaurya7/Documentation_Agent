# Assumptions and limitations

- The current implementation is the M0 foundation plus the deterministic guardrail core. It does not publish GitHub pull requests.
- The production deployment must replace SQLite with PostgreSQL and add Alembic migrations before multi-instance operation.
- A configured webhook secret is mandatory. The application refuses to start without one.
- External GitHub and LLM clients remain disabled until their official SDK versions and authentication contracts are pinned.
- Queue execution is intentionally not performed inline as documentation work. The webhook persists the run and enqueues its idempotency record; a worker is required for end-to-end processing.
- The current worker uses a fail-closed handler. A queued run is blocked until the context, LLM, checks, and publisher handler is installed.
- The agent tool layer currently exposes the core read/search/diff/write/finalize tools. PR metadata, redacted log retrieval, documentation checks, and publisher tools remain intentionally unavailable and fail closed.
- The Git publisher assumes the checkout remote is already configured with a short-lived installation-token transport. Token injection into Git remotes is not implemented in this phase.
- The webhook stores the repository clone URL supplied by GitHub. Fork and private-repository transport policy still requires the deployment to provide an authenticated Git remote before enabling non-shadow publishing.
