# Architecture

```text
GitHub webhook -> signature/filter/dedupe -> SQLite/Postgres run record -> Redis queue -> worker
                                                                               |
                                      context + tools <- agent -> guardrails -> checks -> publisher
```

The webhook boundary authenticates the raw request before parsing it, applies noise filters, records an idempotency key, and enqueues the durable run. The worker claims legal state transitions and fails closed when the handler is unavailable. Future handlers will construct evidence-backed context and expose only typed tools to the language model. Deterministic guardrails remain outside the model and must validate every write and final diff.

The configured handler now carries source URL, base SHA, base branch, and head SHA through persistence. It prepares a temporary checkout, skips empty or documentation-only changes, runs the guarded agent, validates generated docs, and sends the result to the publisher. The default deployment remains shadow mode unless explicitly configured otherwise.
