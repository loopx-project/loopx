"""Transport retry must continue one replan, never authorize a replacement."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from tests.test_external_scheduler_worker import _guarded_host, _hint_payload, worker


def _replan(tmp_path):
    return {**_hint_payload(should_run=True),
            "effective_action": "autonomous_replan_required", "goal_id": "goal",
            "agent_identity": {"agent_id": "agent"}, "runtime_root": str(tmp_path),
            "autonomous_replan_obligation": {"required": True, "obligation_id": "replan-1111111111111111"}}


@pytest.mark.parametrize("recover_at,expected_calls,rc", [(2, 2, 0), (None, 3, 2)])
def test_bound_replan_retries_original_host_with_one_semantic_attempt(tmp_path, monkeypatch, recover_at, expected_calls, rc):
    args, count = _guarded_host(tmp_path, kind="provider_capacity", recover_at=recover_at)
    now, delays = [1000.0], []
    def sleep(seconds):
        delays.append(seconds)
        now[0] += seconds
    def quota(*a, **kw):
        if recover_at and count.exists() and int(count.read_text()) >= recover_at:
            return _hint_payload(should_run=False, local_scheduler_directive="stop")
        return _replan(tmp_path)
    monkeypatch.setattr(worker, "run_quota_should_run", quota)
    assert worker.run_worker(args, sleep=sleep, clock=lambda: now[0]) == rc
    assert count.read_text() == str(expected_calls)
    assert delays == ([30.0] if recover_at else [30.0, 60.0])
    state = json.loads(Path(args.state_file).read_text())
    assert len(state["replan_attempts"]) == 1
    attempt = state["replan_attempts"][0]
    assert attempt["obligation_id"] == "replan-1111111111111111"
    assert attempt["status"] == ("returned" if recover_at else "unknown")
    if recover_at:
        assert "host_attempt" not in state
        # Transport success is not semantic acknowledgement. The unchanged
        # obligation must still pause instead of starting a third conversation.
        monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: _replan(tmp_path))
        assert worker.run_worker(args, sleep=sleep, clock=lambda: now[0]) == 0
    else:
        assert state["host_attempt"]["attempt"] == 3
        assert worker.run_worker(args, sleep=sleep, clock=lambda: now[0]) == 2
    assert count.read_text() == str(expected_calls)


@pytest.mark.parametrize("change", ["unchanged", "legacy", "other_obligation", "normal_work", "other_lifetime",
                                    "ambiguous", "started", "disabled", "stopped"])
def test_restart_keeps_replan_binding_and_existing_holds(tmp_path, monkeypatch, change):
    args, count = _guarded_host(tmp_path, kind="provider_capacity", recover_at=2)
    args.once = True
    payload = _replan(tmp_path)
    monkeypatch.setattr(worker, "run_quota_should_run", lambda *a, **kw: payload)
    assert worker.run_worker(args, clock=lambda: 1000.0) == 1
    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text())
    assert state["host_attempt"]["replan_obligation_id"] == "replan-1111111111111111"
    if change == "legacy":
        state["host_attempt"].pop("replan_obligation_id")
    elif change == "other_obligation":
        payload["autonomous_replan_obligation"]["obligation_id"] = "replan-2222222222222222"
    elif change == "normal_work":
        payload["effective_action"] = "normal_run"
    elif change == "other_lifetime":
        payload["goal_ref"] = {"goal_id": "goal", "goal_instance_id": "new-lifetime"}
    elif change == "ambiguous":
        another = deepcopy(state["replan_attempts"][0])
        another["obligation_id"] = "replan-3333333333333333"
        state["replan_attempts"].append(another)
    elif change == "started":
        state["replan_attempts"][0]["status"] = "started"
    elif change == "disabled":
        args.wake_host_failure = False
    elif change == "stopped":
        payload = _hint_payload(should_run=False, local_scheduler_directive="stop")
    state_path.write_text(json.dumps(state))
    rc = worker.run_worker(args, clock=lambda: 1031.0)
    assert rc == (0 if change in {"unchanged", "stopped"} else 2)
    assert count.read_text() == ("2" if change == "unchanged" else "1")
    after = json.loads(state_path.read_text())
    assert len(after["replan_attempts"]) == len(state["replan_attempts"])
    if change != "unchanged":
        assert after["host_attempt"]["attempt"] == 1
        assert after["replan_attempts"] == state["replan_attempts"]
