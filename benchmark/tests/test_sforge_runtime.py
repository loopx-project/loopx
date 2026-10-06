import subprocess
import tarfile
import io
import json

import pytest

from benchmark.runtime.source import archive_source, source_pins
from benchmark.runtime.scheduler import worker_command
from benchmark.runtime.connect_proxy import connect_target


@pytest.mark.parametrize("request_line", [
    b"CONNECT example.org:443 HTTP/1.1", b"CONNECT chatgpt.com:80 HTTP/1.1",
    b"GET https://chatgpt.com/ HTTP/1.1", b"CONNECT 127.0.0.1:443 HTTP/1.1",
    b"CONNECT chatgpt.com.evil.invalid:443 HTTP/1.1",
    b"CONNECT user@chatgpt.com:443 HTTP/1.1", b"invalid",
])
def test_proxy_rejects_non_api_egress(request_line):
    assert connect_target(request_line, frozenset({"chatgpt.com", "auth.openai.com"})) is None


def test_proxy_permits_only_exact_tls_api_endpoint():
    assert connect_target(b"CONNECT chatgpt.com:443 HTTP/1.1",
                          frozenset({"chatgpt.com"})) == "chatgpt.com:443"


def repository(path, marker):
    path.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()
    git("init", "-q")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    for name in ("loopx/product.py", "benchmark/runtime/worker.py"):
        target = path / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(marker)
    git("add", "loopx", "benchmark")
    git("commit", "-qm", "fixture")
    return git("rev-parse", "HEAD")


def test_split_revision_pins_preserve_main_product_and_exclude_local_state(tmp_path):
    product, runner = tmp_path / "main", tmp_path / "runner"
    main_rev, runner_rev = repository(product, "main"), repository(runner, "runner")
    (runner / "auth.json").write_text("private fixture: never archive untracked files")
    assert source_pins(product, runner, main_rev, runner_rev) == (main_rev, runner_rev)
    archive = tmp_path / "runner.tar"
    archive_source(runner, runner_rev, archive, ("benchmark/runtime",))
    with tarfile.open(archive) as source:
        assert "loopx/product.py" not in source.getnames()
        assert "auth.json" not in source.getnames()
        assert source.extractfile("benchmark/runtime/worker.py").read() == b"runner"
    with pytest.raises(ValueError, match="both revision pins"):
        source_pins(product, runner, main_rev, None)
    with pytest.raises(RuntimeError, match="expected revision"):
        source_pins(product, runner, main_rev, main_rev)
    (product / "loopx/product.py").write_text("dirty")
    with pytest.raises(subprocess.CalledProcessError):
        source_pins(product, runner, main_rev, runner_rev)


def test_scheduler_modes_do_not_add_an_outer_loop_to_native_goal():
    env = {"LOOPX_EXECUTION_MODE": "native-goal"}
    args = dict(python="/python", source="/source", state_file="/state", host_timeout=4700)
    assert worker_command(env, **args) == ["/python", "-m", "benchmark.runtime.worker"]
    env |= {"LOOPX_EXECUTION_MODE": "heartbeat", "LOOPX_CLI": "/loopx",
            "LOOPX_REGISTRY": "/registry", "LOOPX_RUNTIME_ROOT": "/runtime",
            "LOOPX_GOAL_ID": "goal", "LOOPX_AGENT_ID": "agent"}
    command = worker_command(env, **args)
    assert command[command.index("--wake-timeout-seconds") + 1] == "4850"
    assert command[command.index("--state-file") + 1] == "/state"


@pytest.mark.parametrize("profile,resumes", [
    ("official", True), ("single", False), ("native-goal", False),
    ("heartbeat-resume", True), ("heartbeat-explore", True),
])
def test_worker_completion_authority(profile, resumes, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    worker = SForgeWorker(SForgeConfig(agent_model="fixture-model", agent_effort="xhigh"),
                          profile=profile, cwd="/task")
    assert (worker.resume_cmd is not None) is resumes
    assert worker.timeout_seconds == 64800
    if profile in {"official", "single"}:
        command = worker.format_run_cmd("/task.md", internet=False)
        assert 'model_reasoning_effort="xhigh"' in command
        assert 'web_search="disabled"' in command


def test_invalid_worker_inputs_fail_before_install(monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    config = SForgeConfig(agent_model="fixture-model", agent_effort="xhigh")
    with pytest.raises(ValueError, match="Unknown"):
        SForgeWorker(config, profile="unknown", cwd="/task")
    with pytest.raises(ValueError, match="160s"):
        SForgeWorker(config, profile="single", cwd="/task", timeout_seconds=160)
    monkeypatch.delenv("CODEX_AUTH_JSON_PATH", raising=False)
    with pytest.raises(ValueError, match="credential"):
        SForgeWorker(config, profile="single", cwd="/task")


@pytest.mark.parametrize("profile,total,expected", [
    ("native-goal", 64800, 64640), ("native-goal", 1800, 1640),
    ("heartbeat-resume", 64800, 64640), ("heartbeat-explore", 64800, 64640),
])
@pytest.mark.parametrize("turns", [None, 3])
def test_native_goal_and_heartbeat_use_trial_budget_without_independent_wake_limit(
    tmp_path, monkeypatch, profile, total, expected, turns,
):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    async def installed(self, environment):
        pass  # Budget transport test; no container or solver launch.
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    config = SForgeConfig(agent_model="fixture", agent_effort="xhigh")
    if turns is not None and profile == "native-goal":
        with pytest.raises(ValueError, match="heartbeat profile"):
            SForgeWorker(config, profile=profile, cwd="/task", replan_after_turns=turns)
        return
    worker = SForgeWorker(config, profile=profile, cwd="/task", timeout_seconds=total,
                          replan_after_turns=turns)
    worker.install_stop_hook(None, None, tmp_path, None)
    env = worker.runtime._worker_env(cwd="/task")
    assert float(env["LOOPX_CODEX_TURN_TIMEOUT_SEC"]) == expected
    assert worker.runtime.scheduler_timeout == total
    expected_cadence = ({"replan_after_effective_turns": turns} if turns else
                        {"replan_after_completed_todos": 3})
    assert worker.runtime._replan_receipt() == expected_cadence
    receipt = json.loads((tmp_path / "worker-profile.json").read_text())
    if turns is not None:
        assert all(receipt.get(k) == v for k, v in expected_cadence.items())
    else:
        assert "replan_after_effective_turns" not in receipt
        assert "replan_after_completed_todos" not in receipt
    if profile == "native-goal":
        assert worker.resume_cmd is None
        assert worker_command(env, python="/python", source="/source",
                              state_file="/state", host_timeout=expected) == [
            "/python", "-m", "benchmark.runtime.worker"]


def test_artifact_collection_preserves_both_session_homes_without_auth(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    backend = object.__new__(RecordingDockerBackend)
    backend.log_dir = tmp_path / "artifacts"
    copied, cleaned = [], []
    def archive(handle, remote):
        copied.append(str(remote))
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode="w") as bundle:
            member = tarfile.TarInfo("session.jsonl")
            member.size = 2
            bundle.addfile(member, io.BytesIO(b"{}"))
        return payload.getvalue()
    monkeypatch.setattr(backend, "copy_from_container", archive)
    monkeypatch.setattr(DockerBackend, "cleanup_container",
                        lambda self, handle, logger: cleaned.append(handle))
    backend.cleanup_container("fixture-container")
    assert "/home/agent/.codex/sessions" in copied
    assert "/opt/loopx-benchmark/codex-home/sessions" in copied
    assert "/home/agent/.codex" not in copied
    assert "/opt/loopx-benchmark/codex-home" not in copied
    assert all("auth.json" not in path for path in copied)
    assert cleaned == ["fixture-container"]
    assert all(row["collected"] for row in json.loads(
        (backend.log_dir / "artifact-collection.json").read_text()))


@pytest.mark.parametrize("fail", [False, True])
def test_setup_proxy_is_cleared_before_worker_execution(tmp_path, monkeypatch, fail):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    monkeypatch.setenv("LOOPX_INSTALL_HTTPS_PROXY", "http://setup-proxy.invalid:8080")
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                          profile="heartbeat-resume", cwd="/task")
    def install(*args):
        assert worker.environment.setup_env["HTTPS_PROXY"] == "http://setup-proxy.invalid:8080"
        if fail:
            raise RuntimeError("installation failed")
    monkeypatch.setattr(worker, "_install_worker", install)
    if fail:
        with pytest.raises(RuntimeError, match="installation failed"):
            worker.install_stop_hook(None, None, tmp_path, None)
    else:
        worker.install_stop_hook(None, None, tmp_path, None)
    assert worker.environment.setup_env == {}


def test_blind_policy_removes_judge_route_and_credentials_native_is_unchanged(monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend, AllowedEndpoint
    backend = object.__new__(RecordingDockerBackend)
    backend.auth_ips = []
    backend.blind_api_endpoint = None
    judge = AllowedEndpoint(ip="192.0.2.1", port=8080, hostname="judge")
    api = AllowedEndpoint(ip="192.0.2.1", port=9090, hostname="api-proxy")
    monkeypatch.setattr(DockerBackend, "create_network_isolation",
                        lambda self, handle, allowed_endpoints, logger: allowed_endpoints)
    env = {"SFORGE_TOKEN": "fixture", "SFORGE_JUDGE_URL": "http://judge:8080",
           "HTTPS_PROXY": "http://api-proxy:9090", "SFORGE_PATCH_DIR": "/task"}
    assert backend._agent_environment(env) is env
    assert backend.create_network_isolation(None, [judge, api], None) == [judge, api]
    backend.blind_api_endpoint = ("192.0.2.1", 9090)
    assert backend.create_network_isolation(None, [judge, api], None) == [api]
    assert backend._agent_environment(env) == {
        "HTTPS_PROXY": "http://api-proxy:9090", "SFORGE_PATCH_DIR": "/task"}
    with pytest.raises(RuntimeError, match="admitted API-only endpoint"):
        backend.create_network_isolation(None, [judge], None)
    alias = AllowedEndpoint(ip="192.0.2.1", port=9090, hostname="judge-alias")
    with pytest.raises(RuntimeError, match="distinct from the judge"):
        backend.create_network_isolation(None, [alias, api], None)


@pytest.mark.parametrize("interrupted,started,runtime,status", [
    (True, True, 0, "cancelled"),
    (True, False, 0, "cancelled"),
    (False, False, 0, "launch_failed"),
    (False, True, 0, "runner_failed"),
    (False, True, 1, "terminal"),
])
def test_native_result_disposition_uses_signal_and_start_evidence(interrupted, started, runtime, status):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _result_status
    assert _result_status(interrupted=interrupted, started=started, runtime_seconds=runtime) == status


def test_native_swallowed_interrupt_is_observed_and_handler_restored():
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    import os
    import signal
    from benchmark.edgebench.run import _observe_run
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        def native_cancel():
            try:
                os.kill(os.getpid(), signal.SIGINT)
            except KeyboardInterrupt:
                return "native-cancelled-result"
        result, interrupted, elapsed = _observe_run(native_cancel)
        assert result == "native-cancelled-result" and interrupted and elapsed >= 0
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
        result, interrupted, _ = _observe_run(lambda: "completed")
        assert result == "completed" and not interrupted
        with pytest.raises(RuntimeError):
            _observe_run(lambda: (_ for _ in ()).throw(RuntimeError("fixture")))
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    finally:
        signal.signal(signal.SIGINT, previous)


@pytest.mark.parametrize("timed_out", [False, True])
def test_native_terminal_handoff_is_readable_by_visualizer(tmp_path, timed_out):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _write_native_final_result
    from sforge.harness.run_agent import RunResult
    from sforge.visualizer.scanner import _build_run
    trial = tmp_path / "run" / "case"
    trial.mkdir(parents=True)
    result = RunResult(best_score=0.0, best_pass_rate=1.0, best_round="auto-1",
                       total_rounds=1, auto_submissions=1, runtime_seconds=12.0,
                       timed_out=timed_out)
    _write_native_final_result(trial, result, status="terminal", agent="codex",
                              task="case", run_id="run", model="model", effort="xhigh")
    final = json.loads((trial / "final_result.json").read_text())
    assert all(final[k] == v for k, v in result.to_dict().items())
    assert not (trial / "final_result.json.tmp").exists()
    displayed = _build_run("run", trial)
    assert displayed.has_final and not displayed.aborted
    assert displayed.best_score == 0.0 and displayed.runtime_seconds == 12.0
    assert displayed.model == "model"


@pytest.mark.parametrize("status", ["cancelled", "launch_failed", "runner_failed"])
def test_native_failed_run_does_not_publish_completed_result(tmp_path, status):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _write_native_final_result
    from sforge.harness.run_agent import RunResult
    _write_native_final_result(tmp_path, RunResult(), status=status, agent="codex",
                              task="case", run_id="run", model="model", effort="xhigh")
    assert not (tmp_path / "final_result.json").exists()


@pytest.mark.parametrize("profile", ["official", "single", "native-goal"])
def test_planned_sforge_entry_rejects_unsupported_profiles(profile, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    with pytest.raises(ValueError, match="heartbeat profile"):
        SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                     profile=profile, cwd="/task", task_entry="loopx-planned")


@pytest.mark.parametrize("entry", ["seeded-todo", "loopx-planned"])
def test_sforge_command_preserves_native_timing_and_planning_boundary(tmp_path, monkeypatch, entry):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                          profile="heartbeat-explore", cwd="/task", task_entry=entry)
    worker.install_stop_hook(None, None, tmp_path, None)
    assert worker.runtime.execution.task_entry == entry
    worker.prepared = True  # Bootstrap is separately covered through real CLI fixtures.
    first = worker.format_run_cmd("/task.md")
    resumed = worker.format_run_cmd("/task.md", resume=True)
    assert first == resumed
    assert ("benchmark.runtime.sforge_entry" in first) is (entry == "loopx-planned")
    assert "test -f /opt/loopx-benchmark/control/phase-deadline ||" in first
    assert first.index("export LOOPX_PHASE_DEADLINE_EPOCH") < first.index("exec timeout")
    assert json.loads((tmp_path / "worker-profile.json").read_text())["task_entry"] == entry
@pytest.mark.parametrize('profile', ['heartbeat-resume', 'heartbeat-explore'])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('cadence', [None, 2])
def test_envelope_treatment_reaches_shared_worker_and_receipts(tmp_path, monkeypatch, profile, enabled, cadence):
    pytest.importorskip('sforge')
    pytest.importorskip('harbor')
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv('CODEX_AUTH_JSON_PATH', '/private-credential')
    async def installed(self, environment):
        pass  # Transport only; the real renderer/guard test runs without a solver.
    monkeypatch.setattr(BenchmarkCodex, 'install', installed)
    worker = SForgeWorker(SForgeConfig(agent_model='fixture', agent_effort='xhigh'),
                          profile=profile, cwd='/task', turn_envelope=enabled,
                          replan_after_turns=cadence)
    worker.install_stop_hook(None, None, tmp_path, None)
    env = worker.runtime._worker_env(cwd='/task')
    assert env.get('LOOPX_TURN_ENVELOPE') == ('1' if enabled else None)
    assert worker.runtime.execution.turn_envelope is enabled
    assert worker.runtime.replan_after_turns == cadence
    receipt = json.loads((tmp_path / 'worker-profile.json').read_text())
    assert receipt.get('turn_envelope') is (True if enabled else None)
    if not enabled:
        assert 'turn_envelope' not in receipt
    from types import SimpleNamespace
    context = SimpleNamespace()
    worker.runtime._populate_context(context)
    assert context.metadata.get('turn_envelope') is (True if enabled else None)
    if not enabled:
        assert 'turn_envelope' not in context.metadata
    # The opt-in only changes context transport, not resume or model settings.
    assert env['LOOPX_ITERATION_CONTEXT'] == 'resume'
    assert env['REASONING_EFFORT'] == 'xhigh'


@pytest.mark.parametrize('profile', ['official', 'single', 'native-goal'])
def test_envelope_rejects_incompatible_sforge_worker(profile):
    pytest.importorskip('sforge')
    pytest.importorskip('harbor')
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    with pytest.raises(ValueError, match='heartbeat worker'):
        SForgeWorker(SForgeConfig(agent_model='fixture', agent_effort='xhigh'),
                     profile=profile, cwd='/task', turn_envelope=True)


def test_edgebench_rejects_envelope_before_creating_trial(tmp_path):
    pytest.importorskip('sforge')
    pytest.importorskip('harbor')
    from benchmark.edgebench.run import main
    with pytest.raises(SystemExit) as error:
        main(['--task', 'fixture', '--tasks-dir', str(tmp_path), '--log-dir', str(tmp_path),
              '--run-id', 'invalid', '--worker', 'native-goal', '--model', 'fixture',
              '--effort', 'xhigh', '--judge-url', 'http://127.0.0.1:9999', '--turn-envelope'])
    assert error.value.code == 2
    assert not (tmp_path / 'runs').exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_edgebench_receipt_records_only_enabled_treatment(tmp_path, monkeypatch, enabled):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from benchmark.edgebench import run

    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    (tmp_path / "fixture.json").write_text("{}")
    monkeypatch.setattr(run, "source_pins", lambda *a: ("fixture", "fixture"))
    monkeypatch.setattr(run, "load_benchmark", lambda *a: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *a: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False))
    monkeypatch.setattr(run, "SForgeWorker", lambda *a, **k: SimpleNamespace(resume_cmd="resume"))
    monkeypatch.setattr(run, "RecordingDockerBackend", lambda **k: SimpleNamespace(image_exists=lambda image: True))
    def stop_before_solver(**kwargs):
        raise RuntimeError("synthetic launch failure")
    monkeypatch.setattr(run, "run_agent", stop_before_solver)
    args = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "receipt", "--worker", "heartbeat-resume", "--model", "fixture",
            "--effort", "xhigh", "--judge-url", "http://127.0.0.1:9999"]
    with pytest.raises(RuntimeError, match="synthetic launch failure"):
        run.main(args + (["--turn-envelope"] if enabled else []))
    receipt = json.loads((tmp_path / "runs/receipt/fixture/runtime-receipt.json").read_text())
    assert ("turn_envelope" in receipt) is enabled
    if enabled:
        assert receipt["turn_envelope"] is True
    assert receipt["status"] == "runner_failed"


@pytest.mark.parametrize("value", [0, 6, True, 2.5, "3"])
def test_effective_turn_cadence_rejects_invalid_values_before_install(tmp_path, monkeypatch, value):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    with pytest.raises(ValueError, match="replan_after_turns"):
        SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                     profile="heartbeat-explore", cwd="/task", replan_after_turns=value)
    with pytest.raises(ValueError, match="replan_after_turns"):
        BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", replan_after_turns=value)


def test_effective_turn_cadence_rejects_ambiguous_units(tmp_path):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    with pytest.raises(ValueError, match="not both"):
        BenchmarkCodex(logs_dir=tmp_path, model_name="fixture",
                       replan_after_turns=3, replan_after_todos=3)
