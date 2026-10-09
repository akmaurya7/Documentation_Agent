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


def test_retry_is_bounded_and_dead_letters_only_at_limit(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    assert store.enqueue_once("k", "d", "acme/app", "sha", "push")
    store.transition("k", RunStatus.RUNNING)
    first = store.retry_or_fail("k", "temporary", max_attempts=2)
    assert first.status is RunStatus.QUEUED
    assert first.attempts == 1
    store.transition("k", RunStatus.RUNNING)
    second = store.retry_or_fail("k", "permanent", max_attempts=2)
    assert second.status is RunStatus.FAILED
    assert second.attempts == 2
    assert store.audit_events("k")[-1][0] == "run.dead_lettered"


def test_retry_rejects_invalid_bounds_and_non_running_runs(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    assert store.enqueue_once("k", "d", "acme/app", "sha", "push")
    with pytest.raises(ValueError):
        store.retry_or_fail("k", "temporary", max_attempts=0)
    with pytest.raises(ValueError):
        store.retry_or_fail("k", "temporary", max_attempts=2)


def test_store_accepts_sqlalchemy_database_url(tmp_path):
    database = tmp_path / "url.db"
    store = RunStore(str(tmp_path / "unused.db"), f"sqlite:///{database.as_posix()}")
    assert store.enqueue_once("url", "d", "acme/app", "sha", "push")
    assert store.get("url") is not None


def test_kill_switch_is_shared_in_persistence(tmp_path):
    store = RunStore(str(tmp_path / "runs.db"))
    assert store.kill_switch_enabled() is False
    store.set_kill_switch(True)
    assert RunStore(str(tmp_path / "runs.db")).kill_switch_enabled() is True
