import json

import pytest

from docagent.store import RunStatus, RunStore


def test_reports_and_audit_events_are_durable(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    assert store.enqueue_once("k", "d", "acme/app", "sha", "push")
    report = {"run_id": "k", "status": "blocked", "security_flags": []}
    store.save_report("k", report)
    store.audit("k", "run.queued", {"reason": "webhook"})

    reopened = RunStore(str(tmp_path / "runs.db"))
    run = reopened.get("k")
    assert run is not None
    assert json.loads(run.report_json) == report
    assert reopened.audit_events("k")[-1] == ("run.queued", '{"reason":"webhook"}')
    assert reopened.audit_events("k")[0] == ("run.queued", '{"event_type":"push"}')


def test_report_requires_existing_run(tmp_path):
    with pytest.raises(KeyError):
        RunStore(str(tmp_path / "runs.db")).save_report("missing", {})


def test_run_state_transitions_are_terminal(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    store.enqueue_once("k", "d", "acme/app", "sha", "push")
    store.transition("k", RunStatus.RUNNING)
    store.transition("k", RunStatus.BLOCKED)
    with pytest.raises(ValueError):
        store.transition("k", RunStatus.RUNNING)


def test_dead_letter_redacts_failure_details(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    assert store.enqueue_once("k", "d", "acme/app", "sha", "push")
    store.dead_letter("k", "token=ghp_123456789012345678901234567890")
