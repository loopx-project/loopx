"""Failed native workers retain their last source, without granting recovery."""
from __future__ import annotations

import errno
import io
import json
import tarfile
import os
from types import SimpleNamespace

import pytest

from benchmark.edgebench.terminal_capture import FailurePreservation, terminal_components
from benchmark.tests import test_checkpoint_controller as native_fixtures
from benchmark.tests import test_checkpoint_worker as docker_fixtures

controllers = native_fixtures.controllers
writer = docker_fixtures.writer


def fixture_preservation(tmp_path, monkeypatch, fault=None):
    calls = []
    def close():
        calls.append("close")
        if fault == "barrier":
            raise RuntimeError("private transport details")
    container = SimpleNamespace(id="a" * 64, attrs={"State": {
        "Running": False, "Paused": False, "Restarting": False, "Pid": 0}}, reload=lambda: None)
    supervisor = SimpleNamespace(destination=tmp_path, controller_file=tmp_path / "controller.json",
        token="dummy-token", container=container, close=close,
        capture_phase_deadline_path=lambda: "/opt/loopx-benchmark/control/phase-deadline")
    def capture(*args, **kwargs):
        calls.append("capture")
        if fault == "capture":
            raise TimeoutError("dummy-token in remote message")
        assert "handoff" not in json.loads(kwargs["request_file"].read_text())
        return {"sha256": "b" * 64, "research_point_qualified": False}
    monkeypatch.setattr("benchmark.edgebench.checkpoint_worker.capture_worker", capture)
    return FailurePreservation(supervisor, [{"id": "workspace", "source_path": "/task"}],
                               tmp_path / "terminal-preservation.json"), calls


@pytest.mark.parametrize("fault", [None, "capture", "barrier"])
def test_failed_capture_preserves_source_and_never_replays(tmp_path, monkeypatch, fault):
    preservation, calls = fixture_preservation(tmp_path, monkeypatch, fault)
    execution = {"exit_code": 2, "execution_started": True, "timed_out": False}
    preservation.finish(execution)
    preservation.finish(execution)
    receipt = json.loads((tmp_path / "terminal-preservation.json").read_text())
    assert calls == (["close"] if fault == "barrier" else ["close", "capture"])
    assert receipt["execution"] == execution and receipt["restore_qualified"] is False
    assert receipt["capture_succeeded"] is (fault is None)
    assert preservation.retain_source and receipt["source_retained"] is True
    assert "private" not in json.dumps(receipt)
    box = SimpleNamespace(id="a" * 64, attrs={"State": {"Running": True, "Paused": False, "Restarting": False, "Pid": 17}})
    box.reload = lambda: None
    def stop(timeout):
        assert timeout == 15
        box.attrs["State"].update(Running=False, Pid=0)
    box.stop = stop
    preservation.stop_source(box)
    assert json.loads((tmp_path / "terminal-preservation.json").read_text())["source_stopped"] is True


@pytest.mark.parametrize("execution", [None, {"exit_code": 1, "execution_started": False}])
def test_failed_setup_or_unknown_transport_does_not_invent_phase(tmp_path, monkeypatch, execution):
    preservation, calls = fixture_preservation(tmp_path, monkeypatch)
    preservation.finish(execution)
    assert calls == ["close"] and preservation.retain_source
    assert preservation.receipt["capture_attempted"] is False


def test_success_retains_normal_cleanup_unless_writer_barrier_is_uncertain(tmp_path, monkeypatch):
    for fault in [None, "barrier"]:
        preservation, calls = fixture_preservation(tmp_path, monkeypatch, fault)
        preservation.finish({"exit_code": 0, "execution_started": True, "timed_out": False})
        assert preservation.retain_source is (fault == "barrier")
        assert "capture" not in calls


def test_component_declaration_captures_full_workspace_and_no_auth():
    original = {"components": [{"id": "submitted", "source_path": "/task/solution.py"},
                                {"id": "notes", "source_path": "/private-notes"}]}
    components = terminal_components(original, "/task")
    paths = [row["source_path"] for row in components]
    assert "/task" in paths and "/task/solution.py" not in paths and "/private-notes" in paths
    assert "/opt/loopx-benchmark/codex-home" not in paths
    assert not any(path.endswith("auth.json") for path in paths)
    assert original["components"][0]["source_path"] == "/task/solution.py"
    with pytest.raises(ValueError, match="credential home"):
        terminal_components({"components": [{"id": "unsafe", "source_path": "/opt/loopx-benchmark"}]}, "/task")
    with pytest.raises(ValueError, match="credential home"):
        terminal_components({"components": [{"id": "secret", "source_path": "/opt/loopx-benchmark/codex-home/auth.json"}]}, "/task")


@pytest.mark.parametrize("failure", [False, True])
def test_backend_terminal_failure_replaces_remove_with_verified_stop(tmp_path, monkeypatch, failure):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    backend = object.__new__(RecordingDockerBackend)
    events = []
    preservation = SimpleNamespace(retain_source=True, stop_source=lambda raw: events.append("stop"))
    def close():
        if failure:
            raise RuntimeError("uncertain observer barrier")
    backend.checkpoint = SimpleNamespace(close=close, preservation=preservation)
    backend.feedback = None
    backend.log_dir = tmp_path
    backend.research_sidecar = False
    monkeypatch.setattr(backend, "copy_from_container", lambda *args: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(DockerBackend, "cleanup_container", lambda *args: events.append("remove"))
    handle = SimpleNamespace(raw=SimpleNamespace(id="a" * 64))
    if failure:
        with pytest.raises(RuntimeError, match="uncertain"):
            backend.cleanup_container(handle)
    else:
        backend.cleanup_container(handle)
    assert events == ["stop", "stop"]


@pytest.mark.parametrize("enabled", [False, True])
def test_partial_observer_start_preserves_only_opted_in_failure(tmp_path, monkeypatch, enabled):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    events = []
    class Observer:
        preservation = SimpleNamespace(retain_source=False) if enabled else None
        def start(self, container):
            events.append("start")
            raise OSError("private setup text")
        def close(self):
            events.append("close")
        def finish_solver(self, execution):
            events.append("finish")
            assert execution == {"transport_error_kind": "OSError", "execution_started": None}
    backend = RecordingDockerBackend(log_dir=tmp_path / "collected", logger=None,
        oauth_proxy=True, checkpoint=Observer(), client=SimpleNamespace())
    backend.execution_command = "solver"
    monkeypatch.setattr(DockerBackend, "exec_run_with_timeout", lambda *args, **kwargs: pytest.fail("must not start solver"))
    with pytest.raises(OSError, match="private setup"):
        backend.exec_run_with_timeout(SimpleNamespace(raw="owned"), ["/bin/bash", "-c", "solver"])
    assert events == (["start", "finish"] if enabled else ["start"])
    assert (backend.execution_receipt is None) is not enabled


def test_unknown_stop_state_does_not_claim_released_compute(tmp_path, monkeypatch):
    preservation, _ = fixture_preservation(tmp_path, monkeypatch)
    preservation.finish({"exit_code": 2, "execution_started": True})
    box = SimpleNamespace(id="a" * 64, attrs={"State": {}}, reload=lambda: None)
    with pytest.raises(RuntimeError, match="not confirmed stopped"):
        preservation.stop_source(box)
    assert preservation.receipt["source_stopped"] is False


def test_receipt_disk_failure_keeps_solver_and_stopped_source_outcomes(tmp_path, monkeypatch, caplog):
    preservation, calls = fixture_preservation(tmp_path, monkeypatch)
    monkeypatch.setattr(preservation, "_write", lambda: (_ for _ in ()).throw(OSError(errno.ENOSPC, "private path")))
    execution = {"exit_code": 2, "execution_started": True}
    preservation.finish(execution)
    assert preservation.retain_source and calls == ["close", "capture"]
    assert preservation.receipt["execution"] == execution
    assert preservation.receipt["source_stopped"] and preservation.receipt["capture_succeeded"]
    assert preservation.receipt["receipt_write_error_kind"] == "OSError"
    assert "could not be saved" in caplog.text and "private path" not in caplog.text
    assert not preservation.receipt_path.exists()
    preservation.finish(execution)
    assert calls == ["close", "capture"]


def test_receipt_failure_never_masks_an_unconfirmed_stop(tmp_path, monkeypatch):
    preservation, _ = fixture_preservation(tmp_path, monkeypatch)
    preservation.supervisor.container.attrs["State"] = {}
    monkeypatch.setattr(preservation, "_write", lambda: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    with pytest.raises(RuntimeError, match="not confirmed stopped"):
        preservation.finish({"exit_code": 2, "execution_started": True})
    assert preservation.retain_source and preservation.receipt["source_stopped"] is False
    assert preservation.receipt["stop_error_kind"] == "RuntimeError"


def _read_retained_clock(container, name):
    with io.BytesIO(b"".join(container.get_archive("/opt/loopx-benchmark/control/" + name)[0])) as raw:
        with tarfile.open(fileobj=raw) as archive:
            return archive.extractfile(name).read()


@pytest.mark.parametrize("profile,feedback", [
    ("official", "best-only"), ("native-goal", "native"), ("heartbeat-explore", "native"),
])
def test_cli_preservation_rejects_unowned_native_feedback_and_checkpoint_routes(tmp_path, profile, feedback):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import main
    with pytest.raises(SystemExit) as error:
        main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
              "--run-id", "fixture", "--worker", profile, "--model", "fixture", "--effort", "xhigh",
              "--feedback", feedback, "--judge-url", "http://127.0.0.1:9999", "--preserve-failed-worker"])
    assert error.value.code == 2 and not (tmp_path / "runs").exists()


@pytest.mark.skipif(os.environ.get("LOOPX_EDGEBENCH_DOCKER_SMOKE") != "1",
                    reason="Requires isolated Docker and native HTTP checkpoint provider")
@pytest.mark.parametrize("capture_failure,receipt_failure,continued", [
    (False, False, False), (False, True, False), (True, False, False), (True, True, False),
    (False, False, True),
])
def test_real_failed_solver_preserves_tail_and_native_opportunity(
    controllers, writer, tmp_path, monkeypatch, capture_failure, receipt_failure, continued,
):
    from sforge.harness.backend.docker_backend import DockerContainerHandle
    from benchmark.runtime.sforge_backend import RecordingDockerBackend
    from benchmark.edgebench.checkpoint_supervisor import CheckpointSupervisor
    from benchmark.tests.test_checkpoint_controller import submit
    from sforge.harness.agent import Agent
    from sforge.harness.benchmark import load_benchmark
    from sforge.harness.task_spec import make_task_spec
    from dataclasses import replace
    import importlib
    native_runner = importlib.import_module("sforge.harness.run_agent")
    source, _, first, _, files, grades = controllers
    container, _ = writer
    script = '''from pathlib import Path
import sqlite3,time
root=Path('/opt/loopx-benchmark'); control=root/'control'; control.mkdir(parents=True)
(control/'phase-deadline').write_text('1')
(control/'continued-phase-deadline').write_text(str(int(time.time())+600))
(control/'registry.json').write_text('{"fixture":true}')
(control/'scheduler-state.json').write_text('{"host_attempt":{"status":"failed"}}')
(root/'state/runtime').mkdir(parents=True)
(root/'state/runtime/goal.json').write_text('{"todo":"open"}')
home=root/'codex-home'; (home/'sessions').mkdir(parents=True)
(home/'sessions/tail.jsonl').write_text('{"status":"failed","turn":"latest"}\\n')
(home/'config.toml').write_text('model="synthetic"\\n')
(home/'auth.json').write_text('PRIVATE-DO-NOT-EXPORT')
for name in ['state_5.sqlite','thread_history_1.sqlite']:
 db=sqlite3.connect(home/name); db.execute('CREATE TABLE retained (turn TEXT)'); db.execute("INSERT INTO retained VALUES ('latest')"); db.commit(); db.close()
Path('/logs/agent/wakes/latest').mkdir(parents=True)
Path('/logs/agent/wakes/latest/receipt.json').write_text('{"ok":false}')
Path('/data/candidate').write_text('newer than the last graded candidate')
'''
    assert container.exec_run(["python", "-c", script]).exit_code == 0
    components = terminal_components({"components": []}, "/data")
    if continued:
        from benchmark.tests.test_scheduler_phase_resume import _resume, _stopped
        from pathlib import Path
        import time

        # Keep both actual scheduler-selected names, including the phase that
        # failed after the second resume. The root alone has different evidence.
        root = tmp_path / "scheduler-state.json"
        _stopped(root)
        phase_one = time.time() + 500
        first_phase = _resume(root, phase_one)
        _stopped(first_phase)
        phase_two = time.time() + 600
        second_phase = _resume(root, phase_two)
        assert len({root, first_phase, second_phase}) == 3
        control = Path("/opt/loopx-benchmark/control")
        for path, content in [
            (first_phase, {"resume_from": str(control / root.name), "phase": "prior"}),
            (second_phase, {"resume_from": str(control / first_phase.name), "phase": "failed latest"}),
            (tmp_path / "unselected-private.json", {"unselected": True}),
        ]:
            assert container.exec_run(["python", "-c", "from pathlib import Path; "
                f"Path({str(control / path.name)!r}).write_text({json.dumps(content)!r})"]).exit_code == 0
        assert container.exec_run(["python", "-c", "from pathlib import Path; "
            f"Path({str(control / 'active-phase-deadline')!r}).write_text({str(phase_two)!r})"]).exit_code == 0
    if capture_failure:
        components.append({"id": "missing", "source_path": "/missing-participant"})
    supervisor = CheckpointSupervisor(files[0], tmp_path / "terminal-points",
        retention_enabled=False, terminal_components=components,
        terminal_receipt=tmp_path / "terminal-preservation.json",
        phase_deadline_path="/opt/loopx-benchmark/control/continued-phase-deadline")
    if receipt_failure:
        def fail_receipt():
            raise OSError(errno.ENOSPC, "synthetic receipt disk full")
        monkeypatch.setattr(supervisor.preservation, "_write", fail_receipt)
    config = source.state.judge.config
    trial = config.log_dir / "runs/source/fixture"
    backend = RecordingDockerBackend(log_dir=trial / "collected", logger=None,
                                     oauth_proxy=True, checkpoint=supervisor, client=container.client)
    backend.execution_command = "exit 2"
    handle = DockerContainerHandle(container)
    # Only setup uses a precreated test-owned box. Keep the production solver,
    # final-archive exception handling, HTTP drain, result publication and cleanup.
    monkeypatch.setattr(backend, "image_exists", lambda image: True)
    monkeypatch.setattr(backend, "remove_container_by_name", lambda name: None)
    monkeypatch.setattr(backend, "create_container", lambda *args, **kwargs: handle)
    monkeypatch.setattr(backend, "start_container", lambda handle: None)
    monkeypatch.setattr(native_runner, "_install_tools", lambda *args, **kwargs: None)
    monkeypatch.setattr("sforge.harness.network_isolation.cleanup_stale_chains", lambda *args: None)
    original_drain = native_runner._wait_for_grading
    drained = []
    def drain(*args, **kwargs):
        container.reload()
        assert container.attrs["State"]["Running"] is False
        value = original_drain(*args, **kwargs)
        drained.append(value.status)
        return value
    monkeypatch.setattr(native_runner, "_wait_for_grading", drain)
    class SyntheticAgent(Agent):
        name, run_cmd, api_key_env = "fixture", "exit 2", "SYNTHETIC_API_KEY"
        install_cmds = []
    def registered(token):
        backend.bind_registration(token)
        assert submit(first, token, 7).status_code == 200
    task = make_task_spec(config.tasks_dir / "fixture.json", load_benchmark(config.tasks_dir))
    result = native_runner.run_agent(replace(task, cwd="/data", submit_paths=["candidate"]),
        SyntheticAgent(config), config, backend, "source", timeout=60, judge_url=first,
        eval_interval=0, disable_stop_hook=True, disable_auto_eval=True, disable_auto_resume=True,
        internet=True, submission_cooldown=3600, host_registration_callback=registered)
    assert result.solver_execution["segments"][0]["native_exit_code"] == 2
    assert result.solver_execution["status"] == "nonzero_exit"
    assert result.best_score == 7 and result.agent_submissions == 1
    assert drained == ["complete"] and result.archive == b""
    assert json.loads((trial / "run_history.json").read_text())["best_score"] == 7
    container.reload()
    assert not container.attrs["State"]["Running"] and container.attrs["State"]["Pid"] == 0
    if receipt_failure:
        assert not (tmp_path / "terminal-preservation.json").exists()
        receipt = supervisor.preservation.receipt
        assert receipt["receipt_write_error_kind"] == "OSError"
    else:
        receipt = json.loads((tmp_path / "terminal-preservation.json").read_text())
    assert receipt["source_retained"] and receipt["source_stopped"]
    assert not receipt["restore_qualified"]
    assert receipt["capture_succeeded"] is not capture_failure
    assert len(grades) == 1  # Saving the tail did not create an evaluation opportunity.
    if not capture_failure:
        point = supervisor.destination / receipt["capture_id"]
        manifest = json.loads((point / "checkpoint.json").read_text())
        metadata = manifest["point_metadata"]["worker_file_snapshot"]
        assert metadata["native_cursor_qualified"] is False
        assert (point / "components/terminal_workspace/candidate").read_text() == 'newer than the last graded candidate'
        capsule = point / "components" / metadata["native_capsule_component_id"]
        assert (capsule / "components/host_phase_deadline").read_bytes() == _read_retained_clock(container, "continued-phase-deadline")
        assert _read_retained_clock(container, "phase-deadline") == b"1"
        native = json.loads((capsule / "checkpoint.json").read_text())["point_metadata"]["native_reference"]
        assert native["opportunity"]["next_agent"] == 2
        assert native["opportunity"]["remaining_cooldown_seconds"] > 3500
        assert not list(point.rglob("auth.json"))
        originals = json.loads((point / "components/terminal_scheduler").read_text())
        assert originals == {"host_attempt": {"status": "failed"}}
        captured_phases = sorted((point / "components").glob("terminal_scheduler_phase_*"))
        assert [json.loads(path.read_text())["phase"] for path in captured_phases] == (
            ["prior", "failed latest"] if continued else [])
        assert not list(point.rglob("unselected-private.json"))


@pytest.mark.parametrize("fault", [None, "cycle", "outside", "missing", "invalid_clock"])
def test_terminal_phase_discovery_follows_only_the_admitted_predecessors(tmp_path, monkeypatch, fault):
    from pathlib import PurePosixPath
    from benchmark.edgebench.terminal_capture import _scheduler_components_at_barrier
    from benchmark.runtime.layout import _ACTIVE_PHASE_DEADLINE, _CONTROL
    from benchmark.runtime.scheduler import phase_state_name

    root = PurePosixPath(_CONTROL, "scheduler-state.json")
    previous = root.with_name(phase_state_name(root, 1000.25))
    latest = root.with_name(phase_state_name(root, 2000.75))
    files = {_ACTIVE_PHASE_DEADLINE: b"2000.75", str(latest): json.dumps({"resume_from": str(previous)}).encode(),
        str(previous): json.dumps({"resume_from": str(root)}).encode()}
    if fault == "cycle":
        files[str(previous)] = json.dumps({"resume_from": str(latest)}).encode()
    elif fault == "outside":
        files[str(previous)] = b'{"resume_from":"/private/credential.json"}'
    elif fault == "missing":
        del files[str(previous)]
    elif fault == "invalid_clock":
        files[_ACTIVE_PHASE_DEADLINE] = b"nan"
    observed = []
    def archive(container, remote, destination, *, deadline):
        assert container == "paused-original" and deadline == 12
        observed.append(remote)
        if remote not in files:
            raise FileNotFoundError(remote)
        destination.write_bytes(files[remote])
        return destination
    monkeypatch.setattr("benchmark.edgebench.checkpoint_worker._archive", archive)
    components = terminal_components({"components": []}, "/data")
    if fault:
        with pytest.raises((ValueError, FileNotFoundError)):
            _scheduler_components_at_barrier("paused-original", components, tmp_path, deadline=12)
    else:
        selected = _scheduler_components_at_barrier("paused-original", components, tmp_path, deadline=12)
        assert selected[len(components):] == [
            {"id": "terminal_scheduler_phase_1", "source_path": str(previous)},
            {"id": "terminal_scheduler_phase_2", "source_path": str(latest)}]
    assert all(path == _ACTIVE_PHASE_DEADLINE or path in {str(previous), str(latest)} for path in observed)
