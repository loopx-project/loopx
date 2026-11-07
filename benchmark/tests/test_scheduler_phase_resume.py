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
    receipt = path.parent / "wakes" / path.stem / "receipt.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(dict(budget_exhausted=True, host_invoked=False,
                                      mode="heartbeat", stage="execute")))
    state = json.loads(path.read_text()) if path.exists() else {}
    state.update(host_attempt=dict(status="stopped", scope="a" * 64,
        attempt=1, stop_exit_code=75), last_wake_rc=75, last_wake_status="wake_requested_stop")
    path.write_text(json.dumps(state))
    path.with_name(path.name + ".host-attempt").write_text('{"pending":false}')


def _resume(path, end):
    return Path(resume_state_file(str(path), phase_deadline=end, agent_id="agent",
                                  wake_log_dir=str(path.parent / "wakes")))


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


def test_repeated_budget_stops_advance_from_the_actual_predecessor(tmp_path):
    root = tmp_path / "scheduler.json"
    _stopped(root)
    second = _resume(root, time.time() + 1000)
    _stopped(second)
    third = _resume(root, time.time() + 2000)
    assert third not in {root, second}
    assert json.loads(third.read_text())["resume_from"] == str(second)


@pytest.mark.parametrize("change", ["hard_failure", "unknown", "started", "unreadable", "interrupted"])
def test_later_phase_cannot_skip_the_latest_host_attempt(tmp_path, change):
    from loopx.control_plane.turn_driver.host_failure import build_host_failure_record

    root = tmp_path / "scheduler.json"
    _stopped(root)
    second = _resume(root, time.time() + 1000)
    _stopped(second)
    state = json.loads(second.read_text())
    if change == "hard_failure":
        state["host_attempt"] = {
            "scope": "a" * 64, "status": "failed", "attempt": 1,
            "host_failure": build_host_failure_record("quota_exhausted", attempt=1),
            "not_before": time.time() + 100,
        }
        state["last_wake_rc"] = 1
        state["last_wake_status"] = "wake_failed"
    elif change == "unknown":
        state["host_attempt"] = {
            "scope": "a" * 64, "status": "failed", "attempt": 1,
            "host_failure": build_host_failure_record("unknown", attempt=1),
            "not_before": time.time() + 100,
        }
        state["last_wake_rc"] = 1
        state["last_wake_status"] = "wake_failed"
    elif change == "started":
        state["host_attempt"]["status"] = "started"
    elif change == "unreadable":
        second.write_text("broken-state")
    else:
        receipt = tmp_path / "wakes" / second.stem / "receipt.json"
        receipt.write_text(json.dumps(dict(budget_exhausted=True, host_invoked=True,
                                          mode="heartbeat", stage="execute")))
        second.touch()
    if change != "unreadable":
        second.write_text(json.dumps(state))
    before = second.read_bytes()
    later = _resume(root, time.time() + 2000)
    assert later == second
    assert second.read_bytes() == before
    assert not list(tmp_path.glob("scheduler.phase-*.json"))[1:]


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
        receipt = tmp_path / "wakes" / old.stem / "receipt.json"
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


def test_later_authorized_phase_can_bind_a_new_handoff_without_rewriting_prior(tmp_path, monkeypatch):
    import hashlib
    from benchmark.runtime import interruption_recovery
    root = tmp_path / 'scheduler.json'
    _stopped(root)
    source = json.loads(root.read_bytes())
    old_deadline = time.time() - 10
    source.update(interruption_recovery={'phase_deadline_epoch': old_deadline},
                  recovery_handoff={'path': '/old/handoff', 'sha256': 'a' * 64})
    root.write_text(json.dumps(source))
    old = root.read_bytes()
    brief = tmp_path / 'new-handoff.json'
    brief.write_text(json.dumps({'ok': True, 'handoff_text': 'New phase, same objective and opportunity.'}))
    env = dict(LOOPX_RECOVERY_HANDOFF=str(brief), LOOPX_RECOVERY_HANDOFF_SHA256=hashlib.sha256(brief.read_bytes()).hexdigest())
    monkeypatch.setattr(interruption_recovery, 'read_interrupted_host',
        lambda *a, **kw: {'native': {'session_id': 'original'}})
    deadline = time.time() + 100
    selected = Path(resume_state_file(str(root),phase_deadline=deadline,agent_id='agent',wake_log_dir=str(tmp_path/'wakes'),env=env))
    assert selected != root and root.read_bytes() == old
    assert json.loads(selected.read_bytes())['recovery_handoff']['sha256'] == env['LOOPX_RECOVERY_HANDOFF_SHA256']


def test_pre_dispatch_continuation_binds_and_replays_its_optional_handoff(tmp_path):
    import hashlib
    root = tmp_path / 'scheduler.json'
    _stopped(root)
    brief = tmp_path / 'handoff.json'
    brief.write_text(json.dumps({'ok': True, 'handoff_text': 'Keep the original goal.'}))
    env = dict(LOOPX_RECOVERY_HANDOFF=str(brief), LOOPX_RECOVERY_HANDOFF_SHA256=hashlib.sha256(brief.read_bytes()).hexdigest())
    deadline = time.time() + 100
    def select():
        return Path(resume_state_file(str(root),phase_deadline=deadline,agent_id='agent',wake_log_dir=str(tmp_path/'wakes'),env=env))
    phase = select()
    assert json.loads(phase.read_bytes())['recovery_handoff']['sha256'] == env['LOOPX_RECOVERY_HANDOFF_SHA256']
    assert select() == phase
    brief.write_text(json.dumps({'ok': True, 'handoff_text': 'Different material.'}))
    env['LOOPX_RECOVERY_HANDOFF_SHA256'] = hashlib.sha256(brief.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match='immutable'): select()
