# Security model

DocAgent treats repository content, pull-request text, commit messages, filenames, and logs as untrusted data and potential prompt-injection input.

| Threat | Mitigation | Proof in this milestone |
| --- | --- | --- |
| Webhook replay or forgery | HMAC SHA-256 verification and idempotency key | `tests/test_core.py` |
| Path traversal and symlink escape | Normalization, resolution, and repository containment check | `tests/test_core.py` |
| Markdown exfiltration | Reject active HTML and non-allowlisted external URLs | `tests/test_core.py` |
| Secret leakage | Redaction before model/output boundaries; typed security hits | `tests/test_core.py` |
| Runaway work | Configured file, token, tool-call, wall-time, and cost limits are enforced before publication | Handler and agent-loop limit tests |

The GitHub App must use only metadata read, contents read/write, and pull-request write permissions. It must not receive administrator, actions, secrets, or workflow permissions.
