"""Retained interrupted work resumes through ordinary admission, never replay."""
from __future__ import annotations

from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import shlex
import sys
import time

import pytest

from benchmark.runtime.interruption_recovery import _process_window
from benchmark.runtime.scheduler import resume_state_file
from loopx.hosts import codex_checkpoint as native


def _history(monkeypatch, tmp_path, *, change=None):
    tail = dict(id="last", status="interrupted", error=None, startedAt=110, completedAt=120,
                items=[dict(type="commandExecution", status="failed")])
    turns = [dict(id="first", status="completed", items=[]), tail]
    if change == "in_progress":
        tail["status"] = "inProgress"
    elif change == "pending_effect":
        tail["items"][0]["status"] = "inProgress"
    elif change == "outside_window":
        tail["startedAt"] = 90
    elif change == "completed":
        tail["status"] = "completed"
    elif change == "duplicate":
        turns[0]["id"] = "last"
    calls = []

    class Transport:
        def request(self, method, params):
            calls.append(method)
            if method == "thread/read":
                return {"thread": {"id": "other" if change == "wrong_session" else "session",
                                   "cwd": str(tmp_path)}}
            assert method == "thread/turns/list"  # No native resume/interrupt/model method.
            result = copy.deepcopy(turns)
            if change == "changed_history" and calls.count(method) > 1:
                result[0]["items"] = [dict(type="agentMessage", text="later")]
            return dict(data=result, nextCursor=None)

    @contextmanager
    def reader(*args, **kwargs):
        yield Transport()

    monkeypatch.setattr(native, "native_checkpoint_reader", reader)
    return calls


@pytest.mark.parametrize("change", ["in_progress", "pending_effect", "outside_window", "completed",
                                    "duplicate", "wrong_session", "changed_history"])
def test_native_interruption_rejects_unproven_or_changed_tail(monkeypatch, tmp_path, change):
    _history(monkeypatch, tmp_path, change=change)
    with pytest.raises(ValueError):
        native.native_interrupted_turn({"LOOPX_PROJECT": str(tmp_path)}, "session",
            process_started_at=100, process_stopped_at=125)


def test_interruption_readback_does_not_relax_completed_checkpoint(monkeypatch, tmp_path):
    calls = _history(monkeypatch, tmp_path)
    result = native.native_interrupted_turn({"LOOPX_PROJECT": str(tmp_path)}, "session",
        process_started_at=100, process_stopped_at=125)
    assert result["turn_count"] == 2 and result["turn_id"] == "last"
    assert calls == ["thread/read", "thread/turns/list", "thread/turns/list"]
    with native.native_checkpoint_reader({}, timeout=10) as transport:
        with pytest.raises(ValueError, match="completed final cursor"):
            native._complete_history(transport.request, "session")


@pytest.mark.skipif(os.name != "posix", reason="Process-group termination requires POSIX")
def test_live_process_group_cannot_be_settled():
    evidence = dict(native_process=dict(schema_version="native_codex_process_observation_v1",
        pid=os.getpgrp(), started_at_unix=100, after_cleanup=dict(return_code=0, observed_at_unix=110)))
    with pytest.raises(ValueError, match="still alive"):
        _process_window(evidence)


@pytest.mark.skipif(os.name != "posix", reason="Owned process-group cleanup requires POSIX")
@pytest.mark.parametrize("fault", [None, "profile", "stop", "wrong_session", "duplicate_wake"])
def test_real_sqlite_scheduler_retains_interrupted_session_then_admits_next_turn(tmp_path, monkeypatch, fault):
    from benchmark.runtime import worker
    from benchmark.tests.test_worker_phase_budget import _phase_worker
    from tests import test_external_scheduler_worker as scheduler_fixture

    env, project, _runtime, _source, goal_state, _clock = _phase_worker(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "time", time)
    binary = Path(env["CODEX_BIN"])
    binary.write_text(f"#!{sys.executable}\n" + '''
import json, os, pathlib, signal, sys, time
history=pathlib.Path('native-history.json')
if 'app-server' in sys.argv:
    for line in sys.stdin:
        message=json.loads(line)
        if 'id' not in message: continue
        method=message['method']
        if method=='initialize': result={}
        elif method=='thread/read': result={'thread':{'id':'original-native','cwd':str(pathlib.Path.cwd())}}
        elif method=='thread/turns/list': result={'data':[json.loads(history.read_text())],'nextCursor':None}
        else: raise AssertionError(method)
        print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
    sys.exit(0)
with pathlib.Path('native-argv').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')
print(json.dumps({'type':'thread.started','thread_id':'original-native'}),flush=True)
start=int(time.time())
def interrupted(*args):
    history.write_text(json.dumps({'id':'interrupted-work','status':'interrupted','error':None,
        'startedAt':start,'completedAt':int(time.time()),
        'items':[{'type':'commandExecution','status':'failed'}]}))
    sys.exit(130)
signal.signal(signal.SIGINT,interrupted)
payload=sys.stdin.read()
with pathlib.Path('native-inputs').open('a') as f:f.write(json.dumps(payload)+'\\n')
if pathlib.Path('continue').exists():
    print(json.dumps({'type':'turn.completed'}),flush=True)
else:
    time.sleep(60)
''')
    entry = tmp_path / "worker-entry.py"
    entry.write_text("from benchmark.runtime import worker\n"
                     "from benchmark.runtime.interruption_recovery import recovery_handoff\n"
                     "worker.heartbeat_body=lambda env,*a,**kw:'synthetic authorized work'+recovery_handoff(env)[1]\n"
                     "raise SystemExit(worker.main())\n")
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    args, _ = scheduler_fixture._guarded_host(tmp_path / "scheduler")
    args.once = True
    args.registry, args.runtime_root = env["LOOPX_REGISTRY"], env["LOOPX_RUNTIME_ROOT"]
    args.goal_id, args.agent_id = env["LOOPX_GOAL_ID"], env["LOOPX_AGENT_ID"]
    args.wake_cmd = shlex.join([sys.executable, str(entry)])
    args.wake_stop_exit_code, args.wake_timeout_seconds = 75, 30
    monkeypatch.setattr(scheduler_fixture.worker, "run_quota_should_run",
        lambda *a, **kw: scheduler_fixture._hint_payload(should_run=True))
    initial_goal = goal_state.read_bytes()
    monkeypatch.setenv("LOOPX_PHASE_DEADLINE_EPOCH", str(time.time() + 8))
    assert scheduler_fixture.worker.run_worker(args) == 0
    original = Path(args.state_file)
    old_bytes = original.read_bytes()
    old = json.loads(old_bytes)
    assert old["host_attempt"]["status"] == "stopped"
    receipts = list(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json"))
    assert len(receipts) == 1
    first = json.loads(receipts[0].read_bytes())
    assert first["host_invoked"] and first["budget_exhausted"]
    assert first["host_attempt_id"] == old["host_attempt"]["attempt_id"]
    assert first["native_process"]["after_cleanup"]["return_code"] is not None
    deadline = time.time() + 60
    monkeypatch.setenv("LOOPX_PHASE_DEADLINE_EPOCH", str(deadline))
    if fault == "profile":
        monkeypatch.setenv("MODEL_NAME", "different-model")
    elif fault == "stop":
        from loopx.control_plane.goals.activation_service import set_goal_activation_state
        result = set_goal_activation_state(registry_path=Path(args.registry), goal_id=args.goal_id,
            state="stopped", reason="Owner stopped this work.", actor_kind="owner", execute=True)
        assert result["ok"]
    elif fault == "wrong_session":
        first["session"]["session_id"] = "another-native"
        receipts[0].write_text(json.dumps(first))
    elif fault == "duplicate_wake":
        second_receipt = receipts[0].parent.with_name("duplicate") / "receipt.json"
        second_receipt.parent.mkdir()
        second_receipt.write_bytes(receipts[0].read_bytes())
    if fault is None:
        import hashlib
        brief = tmp_path / 'recovery-handoff.json'
        brief.write_text(json.dumps({'ok': True, 'handoff_text': 'Retained submission old-7 is now restored-7; read the inherited history. Goal and opportunities are unchanged.'}))
        monkeypatch.setenv('LOOPX_RECOVERY_HANDOFF', str(brief))
        monkeypatch.setenv('LOOPX_RECOVERY_HANDOFF_SHA256', hashlib.sha256(brief.read_bytes()).hexdigest())
    new = resume_state_file(str(original), phase_deadline=deadline, agent_id=args.agent_id,
        wake_log_dir=env["LOOPX_WAKE_LOG_DIR"], env=dict(os.environ))
    if fault:
        assert new == str(original) and original.read_bytes() == old_bytes
        assert len((project / "native-argv").read_text().splitlines()) == 1
        return
    assert new != str(original)
    settlement = json.loads(Path(new).read_bytes())
    assert settlement["recovered_session_id"] == "original-native"
    assert settlement["interruption_recovery"]["native"]["turn_id"] == "interrupted-work"
    assert resume_state_file(str(original), phase_deadline=deadline, agent_id=args.agent_id,
        wake_log_dir=env["LOOPX_WAKE_LOG_DIR"], env=dict(os.environ)) == new
    with pytest.raises(ValueError, match="immutable"):
        resume_state_file(str(original), phase_deadline=deadline + 60, agent_id=args.agent_id,
            wake_log_dir=env["LOOPX_WAKE_LOG_DIR"], env=dict(os.environ))
    (project / "continue").touch()
    args.state_file = new
    assert scheduler_fixture.worker.run_worker(args) == 0
    argv = [json.loads(line) for line in (project / "native-argv").read_text().splitlines()]
    assert len(argv) == 2 and "resume" in argv[1] and "original-native" in argv[1]
    assert original.read_bytes() == old_bytes and goal_state.read_bytes() == initial_goal
    assert json.loads(Path(new).read_bytes())["recovery_handoff_delivered"] is True
    assert scheduler_fixture.worker.run_worker(args) == 0
    inputs = [json.loads(line) for line in (project / 'native-inputs').read_text().splitlines()]
    assert len(inputs) == 3
    assert 'restored-7' in inputs[1] and 'restored-7' not in inputs[2]



def test_recovery_handoff_missing_changed_and_default_parity(tmp_path):
    import hashlib
    from benchmark.runtime.interruption_recovery import recovery_handoff
    assert recovery_handoff({}) == (None, '')
    path = tmp_path / 'handoff.json'
    raw = json.dumps({'ok': True, 'handoff_text': 'Read retained history; no ownership change.'}).encode()
    env = dict(LOOPX_RECOVERY_HANDOFF=str(path), LOOPX_RECOVERY_HANDOFF_SHA256=hashlib.sha256(raw).hexdigest())
    with pytest.raises(FileNotFoundError): recovery_handoff(env)
    path.write_bytes(raw)
    first = recovery_handoff(env)
    assert recovery_handoff(env) == first
    path.write_bytes(raw + b' ')
    with pytest.raises(ValueError, match='bytes changed'): recovery_handoff(env)
    with pytest.raises(ValueError): recovery_handoff({'LOOPX_RECOVERY_HANDOFF': str(path)})


@pytest.mark.parametrize('status', ['started', 'unknown', 'returned'])
@pytest.mark.parametrize("failed_recovery", [False, True])
def test_new_transport_phase_does_not_clear_unsettled_semantic_replan(tmp_path, monkeypatch, status, failed_recovery):
    from tests.test_external_scheduler_worker import _guarded_host, worker
    from tests.test_external_scheduler_replan_retry import _replan
    from benchmark.runtime.scheduler import _create_resume_state
    args, count = _guarded_host(tmp_path, kind='provider_capacity', recover_at=2)
    args.once = True
    monkeypatch.setattr(worker, 'run_quota_should_run', lambda *a, **kw: _replan(tmp_path))
    assert worker.run_worker(args, clock=lambda: 1000.0) == 1
    old = Path(args.state_file)
    state = json.loads(old.read_bytes())
    attempts = state['replan_attempts']
    attempts[0]['status'] = status
    target = tmp_path / 'continued.json'
    _create_resume_state(target, old, recovered={'native': {'session_id': 'same-session'},
        'host_failure': {'attempt': 1}}, replan_attempts=attempts, failed_recovery=failed_recovery)
    args.state_file = str(target)
    assert worker.run_worker(args, clock=lambda: 1031.0) in (0, 2)
    assert count.read_text() == '1'
    assert json.loads(target.read_bytes())['replan_attempts'] == attempts


@pytest.mark.skipif(not hasattr(os, 'mkfifo'), reason='FIFO boundary requires POSIX')
def test_recovery_handoff_rejects_fifo_without_waiting_for_a_writer(tmp_path):
    from benchmark.runtime.interruption_recovery import recovery_handoff
    pipe = tmp_path / 'handoff.fifo'
    os.mkfifo(pipe)
    with pytest.raises(ValueError, match='regular file'):
        recovery_handoff(dict(LOOPX_RECOVERY_HANDOFF=str(pipe), LOOPX_RECOVERY_HANDOFF_SHA256='a' * 64))
