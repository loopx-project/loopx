"""An explicit idle handoff retains the failure, source and finite phase."""

from __future__ import annotations

from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import time

import pytest

from benchmark.edgebench.stopped_recovery import native_source_paths, prepare_stopped_recovery
from benchmark.runtime.scheduler import resume_state_file
from benchmark.runtime.worker import host_output_paths
from benchmark.tests.test_stopped_recovery import _source, _fake_container
from loopx.control_plane.turn_driver.host_failure import build_host_failure_record
from loopx.hosts import codex_checkpoint as native


def _idle_return(declaration):
    state = Path(declaration["scheduler_state"])
    state.write_text(
        json.dumps(
            {
                "host_attempt": {
                    "status": "failed",
                    "scope": "a" * 64,
                    "attempt": 1,
                    "host_failure": build_host_failure_record("unknown", attempt=1),
                    "session_id": None,
                    "not_before": 111,
                },
                "last_wake_status": "wake_failed",
                "last_wake_rc": 1,
            }
        )
    )
    os.utime(state, (111, 111))
    receipt = (
        Path(declaration["wake_log_dir"]) / declaration["wake_turn_id"] / "receipt.json"
    )
    value = json.loads(receipt.read_text())
    value.update(
        timed_out=True,
        error_kind="RuntimeError",
        error="Synthetic unknown Host failure",
    )
    value.pop("return_code", None)
    receipt.write_text(json.dumps(value))
    os.utime(receipt, (110, 110))
    for output in host_output_paths(receipt.parent):
        output.write_bytes(b"")
    return state, receipt



def _completed_return(declaration):
    state, receipt = _idle_return(declaration)
    attempt = {
        "goal_id": declaration["goal_id"], "agent_id": declaration["agent_id"],
        "registry": declaration["registry"], "runtime_root": declaration["runtime_root"],
        "obligation_id": "replan-0123456789abcdef", "status": "returned", "wake_rc": 0,
    }
    state.write_text(json.dumps({"last_wake_status": "wake_ok", "last_wake_rc": 0,
                                 "replan_attempts": [attempt]}))
    os.utime(state, (111, 111))
    state.with_name(state.name + ".host-attempt").write_text('{"pending": false}')
    value = json.loads(receipt.read_text())
    for key in ("budget_exhausted", "error_kind", "error"):
        value.pop(key, None)
    value.update(
        ok=True, return_code=0, timed_out=False,
        goal_id=declaration["goal_id"], agent_id=declaration["agent_id"],
        host_attempt_id="f" * 32, phase_deadline_epoch="200",
        native_process={
            "schema_version": "native_codex_process_observation_v1",
            "pid": 23, "started_at_unix": 101.1, "cleanup_trigger": "scope_completed",
            "before_cleanup": {"return_code": 0, "observed_at_unix": 108.9},
            "after_cleanup": {"return_code": 0, "observed_at_unix": 109.1},
        },
    )
    receipt.write_text(json.dumps(value))
    os.utime(receipt, (110, 110))
    Path(declaration["phase_clock"]).write_text("200.0")
    return state, receipt


@pytest.mark.parametrize("fault", [None, "default", "pending", "numeric_pending", "clock", "wrong_goal", "late_return", "later_wake"])
def test_completed_host_source_requires_the_last_exact_return(tmp_path, monkeypatch, fault):
    declaration = _source(tmp_path)
    state, receipt = _completed_return(declaration)
    box, client = _fake_container(monkeypatch)
    if fault == "pending":
        state.with_name(state.name + ".host-attempt").write_text('{"pending": true}')
    elif fault == "numeric_pending":
        state.with_name(state.name + ".host-attempt").write_text('{"pending": 0}')
    elif fault == "clock":
        Path(declaration["phase_clock"]).write_text("210")
    elif fault in {"wrong_goal", "late_return"}:
        value = json.loads(receipt.read_text())
        if fault == "wrong_goal":
            value["goal_id"] = "other-goal"
        else:
            value["native_process"]["after_cleanup"]["observed_at_unix"] = 111.5
        receipt.write_text(json.dumps(value))
        os.utime(receipt, (110, 110))
    elif fault == "later_wake":
        later = receipt.parent.with_name("later") / "receipt.json"
        later.parent.mkdir()
        later.write_bytes(receipt.read_bytes())
        os.utime(later, (111, 111))
    output = tmp_path / "proof.json"
    before = state.read_bytes()
    if fault:
        with pytest.raises(ValueError):
            prepare_stopped_recovery(client, container_id=box.id, declaration=declaration,
                                     output=output, idle_host_handoff=fault != "default")
        assert not output.exists()
    else:
        proof = prepare_stopped_recovery(client, container_id=box.id, declaration=declaration,
                                        output=output, idle_host_handoff=True)
        assert proof["completed_host"] is True
        assert proof["execution_authority_granted"] is False
        assert proof["process_started_at"] == 101.1
        assert proof["process_stopped_at"] == 109.1
        assert proof["source_phase_deadline_epoch"] == 200
    assert state.read_bytes() == before

def test_stopped_export_uses_output_created_by_the_real_worker(tmp_path, monkeypatch):
    from benchmark.runtime import worker
    from benchmark.tests.test_worker_phase_budget import _phase_worker

    env, _project, *_ = _phase_worker(tmp_path / "worker", monkeypatch)
    Path(env["CODEX_BIN"]).write_text(
        f"#!{sys.executable}\nimport signal,sys,time\n"
        "signal.signal(signal.SIGINT,lambda *_:sys.exit(0))\n"
        "signal.signal(signal.SIGTERM,lambda *_:sys.exit(0))\n"
        "sys.stdin.read()\ntime.sleep(60)\n"
    )
    with pytest.raises(RuntimeError, match="session identity"):
        worker.run_once(env)
    wakes = list(Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json"))
    assert len(wakes) == 1
    # Independent production-layout oracle: the worker, not a recovery fixture,
    # must create these physical streams before its identity-confirmation error.
    wake = wakes[0].parent
    assert (wake / "stdout.jsonl").read_bytes() == b""
    assert (wake / "stderr.log").read_bytes() == b""
    assert not (wake / "stderr.txt").exists()
    declaration = _source(tmp_path / "source")
    _state, receipt = _idle_return(declaration)
    for output in host_output_paths(receipt.parent):
        output.unlink()
    for output in (wake / "stdout.jsonl", wake / "stderr.log"):
        shutil.copy2(output, receipt.parent / output.name)
    box, client = _fake_container(monkeypatch)
    proof = prepare_stopped_recovery(client, container_id=box.id, declaration=declaration,
        output=tmp_path / "proof.json", idle_host_handoff=True)
    assert proof["idle_host_handoff"] is True
    assert not (receipt.parent / "stderr.txt").exists()


@pytest.mark.parametrize("extra", [None, "archived_sessions", "session_index.jsonl",
                                 "state_6.sqlite", "thread_history_2.sqlite"])
def test_idle_native_reader_never_observes_unsealed_store_participants(tmp_path, monkeypatch, extra):
    from benchmark.edgebench.stopped_recovery import native_source_paths

    home = tmp_path / "native"
    home.mkdir()
    sealed = native_source_paths({"native_home": str(home)})
    for path in map(Path, sealed.values()):
        path.mkdir() if path.name == "sessions" else path.write_bytes(b"retained fixture")
    if extra:
        target = home / extra
        target.mkdir() if extra == "archived_sessions" else target.write_bytes(b"unsealed")
    calls = []

    @contextmanager
    def observe(env, **kwargs):
        calls.append(env)
        assert Path(env["CODEX_HOME"]) != home
        yield "isolated reader"

    monkeypatch.setattr(native, "native_checkpoint_reader", observe)
    def read():
        with native._idle_native_reader({"CODEX_HOME": str(home)}, timeout=1,
                deadline=time.monotonic() + 1, sealed_store_paths=tuple(sealed.values())) as reader:
            assert reader == "isolated reader"
    if extra:
        with pytest.raises(ValueError, match="sealed native-store participants"):
            read()
        assert not calls
    else:
        read()
        assert len(calls) == 1


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "default",
        "live",
        "output",
        "stderr",
        "success",
        "quota",
        "modern",
        "wrong_goal",
        "not_budget",
        "later_receipt",
        "pending_cleared",
    ],
)
def test_idle_source_export_is_explicit_and_keeps_original_failure(
    tmp_path, monkeypatch, fault
):
    declaration = _source(tmp_path)
    state, receipt = _idle_return(declaration)
    box, client = _fake_container(monkeypatch)
    if fault == "live":
        box.attrs["State"].update(Status="running", Running=True, Pid=1)
    elif fault in ("output", "stderr"):
        (
            receipt.parent / ("stdout.jsonl" if fault == "output" else "stderr.log")
        ).write_bytes(b"unknown output")
    elif fault in ("success", "wrong_goal", "not_budget"):
        value = json.loads(receipt.read_text())
        value.update(
            {"ok": True}
            if fault == "success"
            else {"goal_id": "other"}
            if fault == "wrong_goal"
            else {"budget_exhausted": False}
        )
        receipt.write_text(json.dumps(value))
        os.utime(receipt, (110, 110))
    elif fault in ("quota", "modern"):
        value = json.loads(state.read_text())
        value["host_attempt"].update(
            {"host_failure": build_host_failure_record("quota_exhausted", attempt=1)}
            if fault == "quota"
            else {"attempt_id": "b" * 32}
        )
        state.write_text(json.dumps(value))
        os.utime(state, (111, 111))
    elif fault == "later_receipt":
        other = receipt.parent.with_name("later") / "receipt.json"
        other.parent.mkdir()
        other.write_text("{}")
        os.utime(other, (110.5, 110.5))
    elif fault == "pending_cleared":
        state.with_name(state.name + ".host-attempt").write_text('{"pending":false}')
    before = state.read_bytes(), receipt.read_bytes()
    if fault is not None:
        with pytest.raises(ValueError):
            prepare_stopped_recovery(
                client,
                container_id=box.id,
                declaration=declaration,
                output=tmp_path / "proof.json",
                idle_host_handoff=fault != "default",
            )
        assert not (tmp_path / "proof.json").exists()
    else:
        proof = prepare_stopped_recovery(
            client,
            container_id=box.id,
            declaration=declaration,
            output=tmp_path / "proof.json",
            idle_host_handoff=True,
        )
        assert (
            proof["idle_host_handoff"] is True
            and proof["execution_authority_granted"] is False
        )
        assert proof["process_started_at"] == 100 and proof["process_stopped_at"] == 110
        assert any(
            x["source_path"].endswith("state_5.sqlite-wal") for x in proof["components"]
        )
    assert (state.read_bytes(), receipt.read_bytes()) == before


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "running",
        "pending_effect",
        "new_work",
        "wrong_cwd",
        "wrong_session",
        "changed",
        "error",
        "empty",
    ],
)
def test_native_idle_readback_requires_complete_stable_history_before_attempt(
    tmp_path, monkeypatch, fault
):
    turns = [
        {
            "id": "retained",
            "status": "completed",
            "startedAt": 80,
            "completedAt": 90,
            "error": None,
            "items": [{"type": "commandExecution", "status": "completed"}],
        }
    ]
    if fault == "running":
        turns[-1]["status"] = "inProgress"
    elif fault == "pending_effect":
        turns[-1]["items"][0]["status"] = "inProgress"
    elif fault == "new_work":
        turns[-1]["completedAt"] = 101
    elif fault == "error":
        turns[-1]["error"] = {"message": "retained error"}
    elif fault == "empty":
        turns = []
    calls = []

    class Transport:
        def request(self, method, params):
            calls.append(method)
            if method == "thread/read":
                return {
                    "thread": {
                        "id": "other" if fault == "wrong_session" else "original",
                        "cwd": "/other" if fault == "wrong_cwd" else str(tmp_path),
                    }
                }
            rows = copy.deepcopy(turns)
            if fault == "changed" and calls.count("thread/turns/list") > 1:
                rows[0]["items"].append({"type": "agentMessage", "text": "later work"})
            return {"data": rows, "nextCursor": None}

    @contextmanager
    def reader(*args, **kwargs):
        yield Transport()

    monkeypatch.setattr(native, "_idle_native_reader", reader)
    if fault:
        with pytest.raises(ValueError):
            native.native_idle_turn(
                {"LOOPX_PROJECT": str(tmp_path)}, "original", process_started_at=100
            )
    else:
        result = native.native_idle_turn(
            {"LOOPX_PROJECT": str(tmp_path)}, "original", process_started_at=100
        )
        assert (
            result["status"] == "completed"
            and result["completed_at"] == 90
            and result["turn_count"] == 1
        )
    assert not set(calls) - {"thread/read", "thread/turns/list"}


@pytest.mark.parametrize(
    "fault",
    [
        None,
        "stop",
        "profile",
        "exhausted",
        "new_native_work",
        "changed_wal",
        "numeric_pending",
        "unrelated_lock",
    ],
)
@pytest.mark.parametrize("completed_host", [False, True], ids=["legacy-empty", "completed-host"])
def test_real_sqlite_idle_handoff_keeps_source_and_runs_the_retained_session(
    tmp_path, monkeypatch, fault, completed_host
):
    from benchmark.runtime import worker
    from benchmark.tests.test_worker_phase_budget import _phase_worker

    env, project, runtime, _registry, goal, _clock = _phase_worker(
        tmp_path, monkeypatch, output="success"
    )
    env["LOOPX_CODEX_HOME"] = str(tmp_path / "native-home")
    monkeypatch.setattr(worker, "time", time)
    monkeypatch.syspath_prepend(
        str(
            Path(__file__).resolve().parents[2]
            / "packages/loopx-codex-provider-routing/src"
        )
    )
    deadline = time.time() + 120
    env["LOOPX_PHASE_DEADLINE_EPOCH"] = str(deadline)
    # Establish the session through the real worker and canonical SQLite owner.
    assert worker.run_once(env)["ok"]
    home = Path(env["LOOPX_CODEX_HOME"])
    home.mkdir(exist_ok=True)
    for name in ("state_5.sqlite", "thread_history_1.sqlite"):
        (home / name).write_bytes(b"SQLite format 3\0synthetic native provider store")
    (home / "state_5.sqlite-wal").write_bytes(b"retained native WAL")
    (home / "sessions").mkdir(exist_ok=True)
    (home / "sessions/retained.jsonl").write_text("{}\n")
    assert (home / "config.toml").is_file()
    Path(env["CODEX_BIN"]).write_text(
        f"#!{sys.executable}\nIDLE_TEST_CONTROL={str(tmp_path / 'stopped-control')!r}\n"
        + """
import json,pathlib,sys,time
markers=pathlib.Path(IDLE_TEST_CONTROL)
if 'app-server' in sys.argv:
    for line in sys.stdin:
        request=json.loads(line)
        if 'id' not in request: continue
        method=request['method']
        if method=='initialize': value={}
        elif method=='account/read': value={'account':{'type':'chatgpt'}}
        elif method=='account/rateLimits/read': value={'rateLimits':{'primary':{'usedPercent':100 if (markers/'exhausted').exists() else 40}}}
        elif method=='thread/read': value={'thread':{'id':'original-native','cwd':str(pathlib.Path.cwd())}}
        elif method=='thread/turns/list':
            started,completed=(102,108) if (markers/'completed-host').exists() else (80,90)
            if (markers/'new-native-work').exists(): completed=int(time.time())+10
            value={'data':[{'id':'last-completed','status':'completed','startedAt':started,'completedAt':completed,'error':None,'items':[]}],'nextCursor':None}
        else: raise AssertionError(method)
        print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':value}),flush=True)
    sys.exit(0)
pathlib.Path('resumed-argv').write_text(json.dumps(sys.argv[1:]))
print(json.dumps({'type':'thread.started','thread_id':'original-native'}),flush=True)
sys.stdin.read()
pathlib.Path('continued-candidate').write_text('retained candidate improved')
print(json.dumps({'type':'turn.completed'}),flush=True)
"""
    )
    control = tmp_path / "stopped-control"
    control.mkdir()
    state = control / "scheduler.json"
    clock = control / "phase-deadline"
    clock.write_text(str(deadline - 60))
    wake = Path(env["LOOPX_WAKE_LOG_DIR"]) / "wake-100000000000-deadbeefabcd"
    wake.mkdir()
    (wake / "receipt.json").write_text(
        json.dumps(
            {
                "turn_id": wake.name,
                "host_invoked": True,
                "budget_exhausted": True,
                "mode": "heartbeat",
                "stage": "execute",
                "ok": False,
                "session": {"binding_scope": "agent", "session_id": "original-native"},
            }
        )
    )
    declaration = dict(
        goal_id=env["LOOPX_GOAL_ID"],
        agent_id=env["LOOPX_AGENT_ID"],
        workspace=str(project),
        runtime_root=str(runtime),
        native_home=str(home),
        registry=env["LOOPX_REGISTRY"],
        scheduler_state=str(state),
        wake_log_dir=env["LOOPX_WAKE_LOG_DIR"],
        phase_clock=str(clock),
        wake_turn_id=wake.name,
    )
    state.write_text("{}")
    state.with_name(state.name + ".host-attempt").write_text('{"pending":true}')
    if completed_host:
        (control / "completed-host").touch()
        state, returned = _completed_return(declaration)
    else:
        state, returned = _idle_return(declaration)
        clock.write_text("105")
    receipt = json.loads(returned.read_text())
    receipt["session"]["session_id"] = "original-native"
    returned.write_text(json.dumps(receipt))
    os.utime(returned, (110, 110))
    # The selected return must still be the latest source wake.
    for old in Path(env["LOOPX_WAKE_LOG_DIR"]).glob("*/receipt.json"):
        if old != returned:
            os.utime(old, (95, 95))
    original_uts = os.uname()
    unrelated_lock = project / "research.lock"
    unrelated_lock.write_text("retained work evidence")
    box, client = _fake_container(monkeypatch)
    monkeypatch.setattr(
        os,
        "uname",
        lambda: os.uname_result(
            (
                original_uts.sysname,
                box.id[:12],
                original_uts.release,
                original_uts.version,
                original_uts.machine,
            )
        ),
    )
    proof = control / "proof.json"
    prepare_stopped_recovery(
        client,
        container_id=box.id,
        declaration=declaration,
        output=proof,
        idle_host_handoff=True,
    )
    env["LOOPX_INTERRUPTED_SOURCE"] = str(proof)
    env["CODEX_HOME"] = str(home)
    original = state.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    if fault == "stop":
        from loopx.control_plane.goals.activation_service import (
            set_goal_activation_state,
        )

        assert set_goal_activation_state(
            registry_path=Path(env["LOOPX_REGISTRY"]),
            goal_id=env["LOOPX_GOAL_ID"],
            state="stopped",
            actor_kind="owner",
            reason="Synthetic owner Stop",
            execute=True,
        )["ok"]
    elif fault == "profile":
        env["MODEL_NAME"] = "different-model"
    elif fault == "exhausted":
        (control / "exhausted").touch()
    elif fault == "new_native_work":
        (control / "new-native-work").touch()
    elif fault == "changed_wal":
        (home / "state_5.sqlite-wal").write_bytes(b"changed WAL")
    elif fault == "numeric_pending":
        pending = state.with_name(state.name + ".host-attempt")
        pending.write_text('{"pending": 0}' if completed_host else '{"pending": 1}')
    elif fault == "unrelated_lock":
        read_idle = native.native_idle_turn

        def changed_source(*args, **kwargs):
            result = read_idle(*args, **kwargs)
            unrelated_lock.write_text("changed during native read")
            return result

        monkeypatch.setattr(native, "native_idle_turn", changed_source)

    def resume():
        return resume_state_file(
            str(state),
            phase_deadline=deadline,
            agent_id=env["LOOPX_AGENT_ID"],
            wake_log_dir=env["LOOPX_WAKE_LOG_DIR"],
            env=env,
            idle_host_state_sha256=digest,
        )

    if fault:
        with pytest.raises((ValueError, RuntimeError)):
            resume()
        assert (
            not list(control.glob("*.phase-*.json")) and state.read_bytes() == original
        )
        return
    goal_before = goal.read_bytes()
    phase = Path(resume())
    initial = phase.read_bytes()
    saved = json.loads(initial)
    assert saved["recovered_session_id"] == "original-native"
    recovery = saved["idle_host_recovery"]
    if completed_host:
        assert recovery["process_stopped_at"] == 109.1
        assert recovery["native"]["started_at"] == 102
        assert recovery["native"]["completed_at"] == 108
        assert "original_host_failure" not in recovery
        assert saved["replan_attempts"] == json.loads(original)["replan_attempts"]
    else:
        assert recovery["original_host_failure"]["kind"] == "unknown"
    assert recovery["phase_deadline_epoch"] == deadline
    assert resume() == str(phase) and phase.read_bytes() == initial
    for key in ("allowance", "native", "receipt_sha256", "session_profile_digest"):
        changed = copy.deepcopy(saved)
        del changed["idle_host_recovery"][key]
        phase.write_text(json.dumps(changed))
        with pytest.raises(ValueError):
            resume()
    phase.write_bytes(initial)
    if completed_host:
        pending = state.with_name(state.name + ".host-attempt")
        cleared = pending.read_bytes()
        from scripts.external_scheduler_worker import _host_pending

        for witness in ('{"pending": true}', '{"pending": 0}', '{"pending":false}'):
            pending.write_text(witness)
            if witness == '{"pending": 0}':
                assert _host_pending(state) is None
            with pytest.raises(ValueError, match="replay differs"):
                resume()
            assert phase.read_bytes() == initial
        pending.write_bytes(cleared)
        assert resume() == str(phase)
    assert worker.run_once(env)["ok"]
    assert (
        project / "continued-candidate"
    ).read_text() == "retained candidate improved"
    argv = json.loads((project / "resumed-argv").read_text())
    assert "resume" in argv and "original-native" in argv
    assert state.read_bytes() == original and goal.read_bytes() == goal_before
    # Reentry retains the same phase after useful work; another deadline cannot
    # reset the original failure into another qualified idle handoff.
    assert resume() == str(phase)
    with pytest.raises(ValueError):
        resume_state_file(
            str(state),
            phase_deadline=deadline + 30,
            agent_id=env["LOOPX_AGENT_ID"],
            wake_log_dir=env["LOOPX_WAKE_LOG_DIR"],
            env=env,
            idle_host_state_sha256=digest,
        )


@pytest.mark.skipif(
    not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
    reason="Explicit real native local-only fixture",
)
def test_actual_native_idle_readback_never_dispatches_another_turn(tmp_path):
    from tests.control_plane.codex_checkpoint_fixture import real_native_source
    from benchmark.edgebench.stopped_recovery import file_inventory

    project = tmp_path / "project"
    project.mkdir()
    with real_native_source(
        tmp_path / "provider",
        os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"],
        project,
        close_source=True,
    ) as (env, session, _cursor, calls):
        home = Path(env["CODEX_HOME"])

        def source_stores():
            return {
                str(p.relative_to(home)): file_inventory(p)
                for p in [
                    home / "sessions",
                    home / "config.toml",
                    *home.glob("state_*.sqlite*"),
                    *home.glob("thread_history_*.sqlite*"),
                ]
                if p.exists() and not p.name.endswith("-shm")
            }

        source = source_stores()
        before = len(calls)
        observed = native.native_idle_turn(
            env, session, process_started_at=time.time() + 2,
            sealed_store_paths=tuple(native_source_paths({"native_home": str(home)}).values()),
        )
        assert observed["status"] == "completed" and observed["turn_count"] >= 1
        assert len(calls) == before
        assert source_stores() == source
