# Assumptions and limitations

- The current implementation includes the webhook, queue, bounded agent, checks, and publisher paths. Publication remains shadow-only until `DOCAGENT_SHADOW=false` is explicitly configured.
- PostgreSQL is supported through `DOCAGENT_DATABASE_URL`; run the Alembic migration before a production rollout. SQLite remains for single-instance local use.
- A configured webhook secret is mandatory. The application refuses to start without one.
- GitHub and Anthropic adapters are enabled only when their credentials and prompt hash allowlist are configured; missing configuration remains fail-closed.
- Queue execution is intentionally not performed inline as documentation work. The webhook persists the run and enqueues its idempotency record; a worker is required for end-to-end processing.
- The current worker selects a fail-closed handler when required GitHub App settings are absent; otherwise it builds the real checkout/agent/check/publisher handler.
- The agent tool layer exposes only the core read/search/diff/write/finalize tools. PR metadata and publisher operations stay outside the model tool surface.
- Reports persist prompt hashes and input/output token totals. Monetary cost remains deployment-specific because provider pricing and currency are not configured in this repository; publication must not treat an absent cost as zero.
- Git checkout and push use a temporary askpass transport for the short-lived installation token; the token is not written to a remote URL or report.
- The webhook stores the repository clone URL supplied by GitHub. Fork and private-repository transport policy still requires the deployment to provide an authenticated Git remote before enabling non-shadow publishing.
- Source pull-request numbers are retained when GitHub supplies them; labels, reviewer requests, and source-PR comments are optional adapter actions controlled by deployment settings.
