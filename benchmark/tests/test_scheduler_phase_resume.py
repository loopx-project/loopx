"""An authorized new phase preserves old stops and current conversation state."""
from __future__ import annotations

import json
from pathlib import Path
import shlex
import sys
import time

import pytest

from benchmark.runtime.scheduler import resume_state_file, worker_command


def _stopped(path):
    receipt = path.parent / "wakes" / "source" / "receipt.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(json.dumps(dict(budget_exhausted=True, host_invoked=False,
                                      mode="heartbeat", stage="execute")))
    path.write_text(json.dumps(dict(host_attempt=dict(status="stopped", scope="a" * 64,
        attempt=1, stop_exit_code=75), last_wake_rc=75, last_wake_status="wake_requested_stop")))
    path.with_name(path.name + ".host-attempt").write_text('{"pending":false}')


def test_budget_phase_retries_keep_the_same_state_and_original_receipts(tmp_path):
    old = tmp_path / "scheduler.json"
    _stopped(old)
    before = old.read_bytes()
    witness = old.with_name(old.name + ".host-attempt").read_bytes()
    end = time.time() + 1000
    new = resume_state_file(str(old), phase_deadline=end, agent_id="agent", wake_log_dir=str(tmp_path / "wakes"))
    assert new != str(old) and Path(new).parent == old.parent
    Path(new).write_text('{"host_attempt":{"status":"failed"}}')
    assert resume_state_file(str(old), phase_deadline=end, agent_id="agent", wake_log_dir=str(tmp_path / "wakes")) == new
    state = json.loads(old.read_bytes())
    state["reset_token"] = "new-todo-observation"
    old.write_text(json.dumps(state))
    assert resume_state_file(str(old), phase_deadline=end, agent_id="agent", wake_log_dir=str(tmp_path / "wakes")) == new
    assert json.loads(before)["host_attempt"] == json.loads(old.read_bytes())["host_attempt"]
    assert old.with_name(old.name + ".host-attempt").read_bytes() == witness
    assert json.loads(Path(new).read_bytes())["host_attempt"]["status"] == "failed"


@pytest.mark.parametrize("change", ["started", "provider_failure", "other_stop", "interrupted", "unknown_witness"])
def test_phase_continuation_does_not_clear_host_recovery_or_other_stops(tmp_path, change):
    old = tmp_path / "scheduler.json"
    _stopped(old)
    state = json.loads(old.read_bytes())
    if change == "started":
        state["host_attempt"]["status"] = "started"
    elif change == "provider_failure":
        state["host_attempt"].update(status="failed", host_failure={"kind": "unknown"})
    elif change == "other_stop":
        state["host_attempt"]["stop_exit_code"] = 76
    elif change == "interrupted":
        receipt = tmp_path / "wakes" / "source" / "receipt.json"
        receipt.write_text(json.dumps(dict(budget_exhausted=True, host_invoked=True,
                                          mode="heartbeat", stage="execute")))
        # The stopped transport snapshot follows this interrupted receipt.
        old.touch()
    else:
        old.with_name(old.name + ".host-attempt").write_text("incomplete")
    old.write_text(json.dumps(state))
    before = old.read_bytes()
    assert resume_state_file(str(old), phase_deadline=time.time() + 1000, agent_id="agent", wake_log_dir=str(tmp_path / "wakes")) == str(old)
    assert old.read_bytes() == before and not list(tmp_path.glob("*.phase-*.json"))


@pytest.mark.parametrize("end", [0, float("inf"), float("nan"), True])
def test_phase_selection_cannot_grant_an_expired_or_nonfinite_allowance(tmp_path, end):
    old = tmp_path / "scheduler.json"
    _stopped(old)
    before = old.read_bytes()
    with pytest.raises(ValueError, match="finite phase"):
        resume_state_file(str(old), phase_deadline=end, agent_id="agent", wake_log_dir=str(tmp_path / "wakes"))
    assert old.read_bytes() == before


def test_real_scheduler_resumes_original_native_session_after_budget_stop(tmp_path, monkeypatch):
    from benchmark.runtime import worker
    from benchmark.tests.test_worker_phase_budget import _phase_worker
    from tests import test_external_scheduler_worker as fixture

    env, project, _runtime, _source, goal_state, _clock = _phase_worker(
        tmp_path, monkeypatch, work_seconds=300, output="success")
    original_goal = goal_state.read_bytes()
    first = worker.run_once(env)
    assert first["ok"] and first["session"]["session_id"] == "original-native"
    entry = tmp_path / "native_worker.py"
    entry.write_text("from benchmark.runtime import worker\n"
                     "worker.heartbeat_body=lambda *a,**kw:'synthetic work'\n"
                     "raise SystemExit(worker.main())\n")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    args, _ = fixture._guarded_host(tmp_path / "scheduler")
    args.once = True
    args.registry = env["LOOPX_REGISTRY"]
    args.runtime_root = env["LOOPX_RUNTIME_ROOT"]
    args.goal_id = env["LOOPX_GOAL_ID"]
    args.agent_id = env["LOOPX_AGENT_ID"]
    args.wake_cmd = shlex.join([sys.executable, str(entry)])
    args.wake_stop_exit_code = 75
    args.wake_timeout_seconds = None
    monkeypatch.setattr(fixture.worker, "run_quota_should_run",
        lambda *a, **kw: fixture._hint_payload(should_run=True))
    monkeypatch.setenv("LOOPX_PHASE_DEADLINE_EPOCH", "1")
    assert fixture.worker.run_worker(args) == 0  # Actual worker returns budget stop.
    old = Path(args.state_file)
    end = time.time() + 1000
    monkeypatch.setenv("LOOPX_PHASE_DEADLINE_EPOCH", str(end))
    assert fixture.worker.run_worker(args) == 0  # Same old phase still cannot dispatch.
    old_bytes = old.read_bytes()
    args.state_file = resume_state_file(str(old), phase_deadline=end, agent_id="agent", wake_log_dir=env["LOOPX_WAKE_LOG_DIR"])
    assert fixture.worker.run_worker(args) == 0
    assert (project / "native-starts").read_text().splitlines() == ["native", "native"]
    receipts = [json.loads(path.read_bytes()) for path in Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json")]
    invoked = [row for row in receipts if row.get("host_invoked")]
    assert len(invoked) == 2 and all(row["session"]["session_id"] == "original-native" for row in invoked)
    assert old.read_bytes() == old_bytes and goal_state.read_bytes() == original_goal


def test_only_explicit_continuation_wraps_the_existing_scheduler():
    env = dict(LOOPX_EXECUTION_MODE="heartbeat", LOOPX_CLI="/cli", LOOPX_REGISTRY="/registry",
        LOOPX_RUNTIME_ROOT="/runtime", LOOPX_GOAL_ID="goal", LOOPX_AGENT_ID="agent")
    options = dict(python="/python", source="/source", state_file="/state", host_timeout=1000)
    ordinary = worker_command(env, **options)
    assert ordinary[1] == "/source/scripts/external_scheduler_worker.py"
    continued = worker_command(env, **options, resume_budget_stop=True)
    assert continued[1:3] == ["-m", "benchmark.runtime.scheduler"]
    assert worker_command({**env, "LOOPX_EXECUTION_MODE": "native-goal"}, **options,
                          resume_budget_stop=True) == ["/python", "-m", "benchmark.runtime.worker"]
