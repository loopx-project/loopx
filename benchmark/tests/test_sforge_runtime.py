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
    ("heartbeat-resume", False), ("heartbeat-explore", False),
])
def test_only_official_delegates_continuation_to_sforge(profile, resumes, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    worker = SForgeWorker(SForgeConfig(agent_model="fixture-model", agent_effort="xhigh"),
                          profile=profile, cwd="/task")
    assert (worker.resume_cmd is not None) is resumes
    assert worker.timeout_seconds == 64800
    assert worker.task_entry == ("loopx-planned" if profile.startswith("heartbeat-") else "seeded-todo")
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


@pytest.mark.parametrize("effort", ["max", "xhigh", "high"])
@pytest.mark.parametrize("profile", ["official", "single", "heartbeat-explore"])
def test_explicit_effort_survives_native_adapter_and_install(tmp_path, monkeypatch, effort, profile):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex, CodexOffline
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    monkeypatch.setattr(CodexOffline, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort=effort),
                          profile=profile, cwd="/task")
    if profile.startswith("heartbeat-"):
        worker.install_stop_hook(None, None, tmp_path, None)
        assert worker.runtime._worker_env(cwd="/task")["REASONING_EFFORT"] == effort
        assert json.loads((tmp_path / "worker-profile.json").read_text())["reasoning_effort"] == effort
    else:
        for resume in (False, True):
            command = worker.format_run_cmd("/task.md", internet=False, resume=resume)
            assert f'model_reasoning_effort="{effort}"' in command


@pytest.mark.parametrize("effort", ["max", "ultra", "high"])
def test_edgebench_accepts_canonical_effort_before_provider_admission(tmp_path, effort, monkeypatch, capsys):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench import run
    monkeypatch.setenv("OPENAI_BASE_URL", "http://example.invalid/v1")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(SystemExit):
        run.main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
                  "--run-id", "fixture", "--worker", "official", "--model", "fixture",
                  "--effort", effort, "--judge-url", "http://example.invalid:8080"])
    assert "OPENAI_BASE_URL requires OPENAI_API_KEY" in capsys.readouterr().err
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("with_catalog", [False, True])
def test_official_installer_binds_staged_catalog_without_leaking_host_path(tmp_path, monkeypatch, with_catalog):
    from types import SimpleNamespace
    import tomllib
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.agent.codex import CodexAgent
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, CodexOffline
    from benchmark.runtime.codex_offline import MODEL_CATALOG_REMOTE
    catalog = tmp_path / "host-catalog.json"
    catalog.write_text(json.dumps({"models": [{"slug": "fixture"}]}))
    async def installed(self, environment):
        self._model_catalog_staged = with_catalog
        self._model_catalog_source = catalog if with_catalog else None
    monkeypatch.setattr(CodexOffline, "install", installed)
    monkeypatch.setattr(CodexAgent, "install_stop_hook", lambda *args: None)
    observed = {}
    def copy(handle, source, target):
        if source.name == "config.toml":
            observed.update(tomllib.loads(source.read_text()))
    backend = SimpleNamespace(copy_to_container=copy,
        exec_run=lambda *args, **kwargs: SimpleNamespace(exit_code=0))
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="max",
        agent_api_base_url="http://example.invalid/v1", agent_api_key="fixture"),
        profile="official", cwd="/task")
    worker.install_stop_hook(backend, None, tmp_path, None)
    assert observed["model_reasoning_effort"] == "max"
    assert observed.get("model_catalog_json") == (MODEL_CATALOG_REMOTE if with_catalog else None)
    assert str(tmp_path) not in json.dumps(observed)


@pytest.mark.parametrize("profile", ["official", "single", "native-goal", "heartbeat-resume", "heartbeat-explore"])
@pytest.mark.parametrize("unreadable", [False, True])
def test_best_only_install_requires_ordinary_worker_hook_readback(tmp_path, monkeypatch, profile, unreadable):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from sforge.harness.agent.codex import CodexAgent
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex, CodexOffline
    credential = tmp_path / "synthetic-auth.json"
    credential.write_text("{}")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(credential))
    async def installed(self, environment):
        pass  # Exercise the native installation seam without installing a model.
    monkeypatch.setattr(CodexOffline, "install", installed)
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    monkeypatch.setattr(CodexAgent, "install_stop_hook", lambda *args: None)
    staged = []
    async def stage_python(environment, destination):
        staged.append(destination)
    monkeypatch.setattr("benchmark.runtime.sforge.stage_portable_python", stage_python)
    ordinary_reads = []
    def command(handle, cmd, **kwargs):
        if kwargs.get("user") == "agent" and isinstance(cmd, list) and "read_bytes" in cmd[-1]:
            ordinary_reads.append(cmd)
            return SimpleNamespace(exit_code=int(unreadable), output="")
        return SimpleNamespace(exit_code=0, output="")
    backend = SimpleNamespace(exec_run=command, copy_to_container=lambda *args: None)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile=profile, cwd="/task", feedback="best-only", feedback_prompt="Synthetic local task")
    if unreadable:
        with pytest.raises(RuntimeError, match="unreadable by worker"):
            worker.install_stop_hook(backend, None, tmp_path, None)
        assert not (tmp_path / "worker-profile.json").exists()
    else:
        worker.install_stop_hook(backend, None, tmp_path, None)
        assert json.loads((tmp_path / "worker-profile.json").read_text())["feedback_delivery"] == "codex_hooks"
        assert json.loads((tmp_path / "worker-profile.json").read_text())["feedback_payload"] == "official-result"
        assert json.loads((tmp_path / "worker-profile.json").read_text())["loopx_usage_ping_enabled"] is False
    assert len(ordinary_reads) == 1
    assert ordinary_reads[0][0] == "/opt/loopx-benchmark/python/bin/python3"
    assert len(staged) == int(profile in {"official", "single"})


@pytest.mark.parametrize("capture_path", [None, "/control/checkpoint-request.json"])
@pytest.mark.parametrize("context_delivery", ["off", "on"])
def test_checkpoint_request_reaches_sforge_worker_without_changing_default_profile(tmp_path, monkeypatch, capture_path, context_delivery):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    async def installed(self, environment):
        pass  # Exercise the real adapter handoff without starting a trial.
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    config = SForgeConfig(agent_model="fixture", agent_effort="xhigh")
    worker = SForgeWorker(config, profile="heartbeat-explore", cwd="/task", checkpoint_request_path=capture_path,
                         context_delivery=context_delivery)
    worker.install_stop_hook(None, None, tmp_path, None)
    env = worker.runtime._worker_env(cwd="/task")
    profile = json.loads((tmp_path / "worker-profile.json").read_text())
    assert env.get("LOOPX_CHECKPOINT_REQUEST") == capture_path
    assert ("checkpoint_capture_enabled" in profile) is (capture_path is not None)
    # A guest path is configuration, not evidence of a fresh-replan trigger.
    # Actual implicit activation is still recorded by the worker's wake.
    assert "turn_envelope" not in profile
    assert "turn_envelope_source" not in profile
    assert "LOOPX_TURN_ENVELOPE" not in env
    assert env.get("LOOPX_CONTEXT_DELIVERY") == ("on" if context_delivery != "off" else None)
    assert profile.get("context_delivery") == (context_delivery if context_delivery != "off" else None)
    assert profile.get("checkpoint_capture_enabled") is (True if capture_path else None)
    with pytest.raises(ValueError, match="checkpoint_request_path"):
        SForgeWorker(config, profile="official", cwd="/task", checkpoint_request_path="/control/request.json")


@pytest.mark.parametrize("research_paths", [(), ("/tmp/private-data.json",), ("/tmp/private-research",)])
@pytest.mark.parametrize("refuse_transfer", [False, True])
def test_warm_receiver_owns_only_selected_research_before_preparation(
        tmp_path, monkeypatch, research_paths, refuse_transfer):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import BenchmarkCodex, SForgeWorker

    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    async def stage(environment, workspace):
        return list(research_paths)  # Verified selection is exercised by WarmStart staging tests.
    start = SimpleNamespace(goal_id="receiver", agent_id="worker", stage=stage,
                            verify=lambda workspace: None)
    owned = set()
    commands = []
    def execute(handle, command, **kwargs):
        commands.append(command)
        if command[:4] == ["chown", "-R", "-P", "agent:agent"]:
            assert kwargs["user"] == "root"
            if refuse_transfer:
                return SimpleNamespace(exit_code=1)
            owned.update(command[4:])
        return SimpleNamespace(exit_code=0, output="")
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile="heartbeat-explore", cwd="/task", warm_start=start)
    restored = []
    monkeypatch.setattr(worker, "_warm_command", lambda action, **kwargs: restored.append(action))
    backend = SimpleNamespace(exec_run=execute, copy_to_container=lambda *args: None)
    if refuse_transfer:
        with pytest.raises(RuntimeError, match="receiver") as failure:
            worker.install_stop_hook(backend, None, tmp_path, None)
        if not research_paths:
            assert str(failure.value) == "Restored workspace is not writable by its receiver"
        assert not (tmp_path / "worker-profile.json").exists()
    else:
        worker.install_stop_hook(backend, None, tmp_path, None)
        assert owned == {"/task", *research_paths}
        assert "/tmp/unselected-research" not in owned
        assert not any(command[0] == "chmod" for command in commands)
    assert restored == ["workspace"]


@pytest.mark.parametrize("explicit,trigger,expected", [
    (False, None, None),
    (False, "fresh_replan", None),
    (True, None, "explicit"),
    (True, "fresh_replan", "explicit"),
])
def test_sforge_profile_does_not_enable_envelope_for_checkpoint(tmp_path, monkeypatch, explicit, trigger, expected):
    from types import SimpleNamespace
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    declaration = {"destination": "/logs/agent/checkpoints",
        "components": [{"id": "workspace", "source_path": "/task"}], "point_metadata": {}}
    if trigger is not None:
        declaration["trigger"] = trigger
    backend = SimpleNamespace(copy_to_container=lambda *args: None,
        exec_run=lambda *args, **kwargs: SimpleNamespace(exit_code=0))
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile="heartbeat-resume", cwd="/task", turn_envelope=explicit,
        checkpoint_request=json.dumps(declaration).encode())
    worker.install_stop_hook(backend, None, tmp_path, None)
    profile = json.loads((tmp_path / "worker-profile.json").read_text())
    assert profile.get("turn_envelope_source") == expected
    assert profile.get("turn_envelope") is (True if expected else None)


@pytest.mark.parametrize("enabled", [False, True])
def test_native_goal_continue_is_a_native_goal_opt_in(tmp_path, monkeypatch, enabled):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    config = SForgeConfig(agent_model="fixture", agent_effort="xhigh")
    worker = SForgeWorker(config, profile="native-goal", cwd="/task", native_goal_continue=enabled)
    worker.install_stop_hook(None, None, tmp_path, None)
    env = worker.runtime._worker_env(cwd="/task")
    profile = json.loads((tmp_path / "worker-profile.json").read_text())
    assert env.get("LOOPX_NATIVE_GOAL_CONTINUE") == ("1" if enabled else None)
    assert profile.get("native_goal_continue") is (True if enabled else None)
    with pytest.raises(ValueError, match="native-goal"):
        SForgeWorker(config, profile="heartbeat-resume", cwd="/task", native_goal_continue=True)
    with pytest.raises(ValueError, match="context_delivery"):
        SForgeWorker(config, profile="native-goal", cwd="/task", native_goal_continue="1")


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
    expected_cadence = ({"replan_after_effective_turns": turns or 6}
                        if profile.startswith("heartbeat-") else
                        {"replan_after_completed_todos": 3})
    assert worker.runtime._replan_receipt() == {**expected_cadence,
        **({"planning_horizon_goal": True} if profile.startswith("heartbeat-") else {})}
    receipt = json.loads((tmp_path / "worker-profile.json").read_text())
    if profile.startswith("heartbeat-"):
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
    backend.checkpoint = None
    backend.log_dir = tmp_path / "artifacts"
    backend.feedback = None
    copied, cleaned = [], []
    def archive(handle, remote):
        copied.append(str(remote))
        payload = io.BytesIO()
        event = json.dumps({"type": "event_msg", "payload": {"type": "task_complete",
            "error": {"message": "PRIVATE-SENTINEL", "codex_error_info": {
                "response_too_many_failed_attempts": {"http_status_code": 429}}}}}).encode() + b"\n"
        with tarfile.open(fileobj=payload, mode="w") as bundle:
            member = tarfile.TarInfo("sessions/fixture/session.jsonl")
            member.size = len(event)
            bundle.addfile(member, io.BytesIO(event))
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
    assert backend.provider_failure_receipt == {
        "schema_version": "codex_terminal_provider_failure_v0",
        "kind": "response_too_many_failed_attempts",
        "http_status_code": 429,
    }
    assert "PRIVATE-SENTINEL" not in json.dumps(backend.provider_failure_receipt)


def test_provider_observation_failure_cannot_skip_native_cleanup(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    backend = object.__new__(RecordingDockerBackend)
    backend.checkpoint = None
    backend.log_dir = tmp_path / "artifacts"
    backend.feedback = None
    backend.provider_failure_receipt = None
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w"):
        pass
    monkeypatch.setattr(backend, "copy_from_container", lambda *args: payload.getvalue())
    monkeypatch.setattr("benchmark.runtime.sforge_backend.observe_terminal_provider_failure",
                        lambda *args: (_ for _ in ()).throw(TypeError("unknown event shape")))
    cleaned = []
    monkeypatch.setattr(DockerBackend, "cleanup_container",
                        lambda self, handle, logger: cleaned.append(handle))

    backend.cleanup_container("fixture-container")

    assert cleaned == ["fixture-container"]
    assert backend.provider_failure_receipt is None
    observations = json.loads((backend.log_dir / "artifact-collection.json").read_text())
    assert {"artifact": "provider-failure-observation", "collected": False,
            "error_kind": "TypeError"} in observations


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
    backend.checkpoint = None
    backend.auth_ips = []
    backend.blind_api_endpoint = None
    judge = AllowedEndpoint(ip="192.0.2.1", port=8080, hostname="judge")
    api = AllowedEndpoint(ip="192.0.2.1", port=9090, hostname="api-proxy")
    monkeypatch.setattr(DockerBackend, "create_network_isolation",
                        lambda self, handle, allowed_endpoints, logger: allowed_endpoints)
    env = {"SFORGE_TOKEN": "fixture", "SFORGE_JUDGE_URL": "http://judge:8080",
           "SFORGE_FEEDBACK_URL": "http://judge:8080/api/v1/feedback/fixture",
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


@pytest.mark.parametrize("interrupted,started,runtime,exit_code,timed_out,status", [
    (True, True, 0, None, False, "cancelled"),
    (True, False, 0, None, False, "cancelled"),
    (False, False, 0, None, False, "launch_failed"),
    (False, True, 0, 0, False, "runner_failed"),
    (False, True, 1, 0, False, "terminal"),
    (False, True, 310, 1, False, "runner_failed"),
    (False, True, 310, None, False, "runner_failed"),
    (False, True, 43200, -1, True, "terminal"),
    (True, True, 43200, -1, True, "cancelled"),
])
def test_native_result_disposition_requires_successful_exit_or_budget_timeout(
    interrupted, started, runtime, exit_code, timed_out, status,
):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _result_status
    assert _result_status(interrupted=interrupted, started=started, runtime_seconds=runtime,
                          exit_code=exit_code, timed_out=timed_out) == status


@pytest.mark.parametrize("timed_out,exit_code", [(True, -1), (True, None), (False, 0)])
def test_planning_without_execution_entry_never_emits_completed_result(timed_out, exit_code):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _result_status
    assert _result_status(interrupted=False, started=True, runtime_seconds=300,
        exit_code=exit_code, timed_out=timed_out, execution_started=False) == "runner_failed"


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
@pytest.mark.parametrize("horizon", [None, False, True])
def test_sforge_command_preserves_native_timing_and_planning_boundary(tmp_path, monkeypatch, entry, horizon):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                          profile="heartbeat-explore", cwd="/task", task_entry=entry, planning_horizon_goal=horizon)
    worker.install_stop_hook(None, None, tmp_path, None)
    assert worker.runtime.execution.task_entry == entry
    expected_horizon = True if horizon is None else horizon
    assert worker.runtime.planning_horizon_goal is expected_horizon
    assert worker.runtime.execution.turn_envelope is False
    worker.prepared = True  # Bootstrap is separately covered through real CLI fixtures.
    first = worker.format_run_cmd("/task.md")
    resumed = worker.format_run_cmd("/task.md", resume=True)
    assert first == resumed
    assert ("benchmark.runtime.sforge_entry" in first) is (entry == "loopx-planned")
    assert "test -f /opt/loopx-benchmark/control/phase-deadline ||" in first
    assert first.index("export LOOPX_PHASE_DEADLINE_EPOCH") < first.index("timeout --signal")
    profile = json.loads((tmp_path / "worker-profile.json").read_text())
    assert profile["task_entry"] == entry
    assert profile["planning_horizon_goal"] is expected_horizon
@pytest.mark.parametrize('profile', ['heartbeat-resume', 'heartbeat-explore'])
@pytest.mark.parametrize('enabled', [False, True])
@pytest.mark.parametrize('cadence', [None, 2, 6])
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
    assert worker.runtime.replan_after_turns == (cadence or 6)
    receipt = json.loads((tmp_path / 'worker-profile.json').read_text())
    assert receipt['outer_resume'] is False
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


@pytest.mark.parametrize("profile,options", [
    ("native-goal", ["--turn-envelope"]),
    ("native-goal", ["--context-delivery"]),
    ("official", ["--replan-after-turns", "3"]),
    ("single", ["--replan-after-todos", "3"]),
    ("native-goal", ["--replan-after-todos", "3"]),
    ("heartbeat-resume", ["--replan-after-turns", "3", "--replan-after-todos", "3"]),
    ("heartbeat-explore", ["--replan-after-todos", "0"]),
    ("official", ["--stride-strategy", "off"]),
    ("native-goal", ["--stride-strategy", "evidence_conditioned_v1"]),
    ("heartbeat-resume", ["--stride-strategy", "future_policy"]),
    ("official", ["--checkpoint-request", "missing.json"]),
    ("native-goal", ["--checkpoint-request", "missing.json"]),
    ("heartbeat-explore", ["--checkpoint-retention-policy", "policy.json"]),
    ("heartbeat-explore", ["--no-checkpoint-retention"]),
    ("heartbeat-explore", ["--checkpoint-export-root", "/unused-export"]),
    ("heartbeat-explore", ["--no-checkpoint-retention", "--checkpoint-retention-policy", "policy.json"]),
])
def test_edgebench_rejects_invalid_profile_settings_before_creating_trial(tmp_path, profile, options):
    pytest.importorskip('sforge')
    pytest.importorskip('harbor')
    from benchmark.edgebench.run import main
    with pytest.raises(SystemExit) as error:
        main(['--task', 'fixture', '--tasks-dir', str(tmp_path), '--log-dir', str(tmp_path),
              '--run-id', 'invalid', '--worker', profile, '--model', 'fixture',
              '--effort', 'xhigh', '--judge-url', 'http://127.0.0.1:9999', *options])
    assert error.value.code == 2
    assert not (tmp_path / 'runs').exists()


@pytest.mark.parametrize("missing", ["controller", "declaration", "native_feedback"])
def test_controller_handoff_cannot_be_partially_enabled(tmp_path, missing):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import main
    request = tmp_path / "checkpoint.json"
    request.write_text(json.dumps({"destination": "/logs/agent/checkpoints", "point_metadata": {},
        "components": [{"id": "code", "source_path": "/task/code"}], "trigger": "fresh_replan",
        "native_session": True, "loopx_state": True, "evaluation_opportunity": True,
        "controller_handoff": True}))
    options = [] if missing == "controller" else ["--checkpoint-controller-file", str(tmp_path / "private.json")]
    if missing != "declaration":
        options.extend(["--checkpoint-request", str(request)])
    if missing == "native_feedback":
        options.extend(["--feedback", "best-only"])
    with pytest.raises(SystemExit) as error:
        main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
              "--run-id", "invalid", "--worker", "heartbeat-resume", "--model", "fixture",
              "--effort", "xhigh", "--judge-url", "http://127.0.0.1:9999", *options])
    assert error.value.code == 2 and not (tmp_path / "runs").exists()


@pytest.mark.parametrize("host_callback", [False, True])
def test_checkpoint_supervisor_is_bound_to_container_and_only_solver_execution(tmp_path, monkeypatch, host_callback):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    calls = []
    controller = SimpleNamespace(bind=lambda env: calls.append(("bind", env["SFORGE_TOKEN"])),
        start=lambda raw: calls.append(("start", raw)), close=lambda: calls.append(("close",)))
    monkeypatch.setattr("docker.from_env", lambda: pytest.fail("Unit transport must use its explicit client"))
    backend = RecordingDockerBackend(log_dir=tmp_path / "collected", logger=None,
                                    oauth_proxy=True, checkpoint=controller, client=SimpleNamespace())
    handle = SimpleNamespace(raw="exact-container")
    monkeypatch.setattr(DockerBackend, "create_container", lambda *a, **kw: handle)
    def current_provider(*, host_registration_callback=None):
        pass
    def legacy_provider():
        pass
    provider = current_provider if host_callback else legacy_provider
    options = backend.registration_options(provider)
    env = {"SFORGE_FEEDBACK_URL": "http://judge/api/v1/feedback/synthetic-relay"}
    if host_callback:
        options["host_registration_callback"]("synthetic")
    else:
        assert options == {}
        env["SFORGE_TOKEN"] = "synthetic"
    original_env = dict(env)
    backend.create_container("image", "name", environment=env)
    assert env == original_env, "binding must not inject the host token into worker env"
    backend.execution_command = "solver"
    monkeypatch.setattr(DockerBackend, "exec_run_with_timeout", lambda *a, **kw:
        SimpleNamespace(exit_code=0, timed_out=False, elapsed_seconds=1))
    backend.exec_run_with_timeout(handle, ["setup"])
    assert calls == [("bind", "synthetic")]
    backend.exec_run_with_timeout(handle, ["/bin/bash", "-c", "solver"])
    assert calls == [("bind", "synthetic"), ("start", "exact-container"), ("close",)]
    with pytest.raises(ValueError, match="one native host registration"):
        backend.bind_registration("another-registration")


def test_host_registration_is_opt_in_shared_and_requires_nonempty_token(tmp_path):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from benchmark.runtime.sforge_backend import RecordingDockerBackend
    backend = RecordingDockerBackend(log_dir=tmp_path, logger=None, oauth_proxy=True,
                                     client=SimpleNamespace())
    def provider(*, host_registration_callback=None):
        pass
    assert backend.registration_options(provider) == {}, "ordinary official runs retain native behavior"
    calls = []
    backend.feedback = SimpleNamespace(bind=lambda env: calls.append(("feedback", env["SFORGE_TOKEN"])))
    backend.checkpoint = SimpleNamespace(bind=lambda env: calls.append(("checkpoint", env["SFORGE_TOKEN"])))
    callback = backend.registration_options(provider)["host_registration_callback"]
    for invalid in (None, "", 1, {}):
        with pytest.raises(ValueError):
            callback(invalid)
    assert calls == [] and not backend.registration_bound
    callback("synthetic")
    assert calls == [("feedback", "synthetic"), ("checkpoint", "synthetic")]


@pytest.mark.parametrize("protect_fails", [False, True])
def test_host_checkpoint_declaration_is_staged_verbatim_and_protected(tmp_path, monkeypatch, protect_fails):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    async def installed(self, environment):
        pass  # Exercise the installation transport; no model or evaluator.
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    staged, commands = [], []
    def copy(handle, source, target):
        staged.append((str(target), source.read_bytes()))
    def command(handle, cmd, **kwargs):
        commands.append((cmd, kwargs))
        return SimpleNamespace(exit_code=int(protect_fails))
    backend = SimpleNamespace(copy_to_container=copy, exec_run=command)
    declaration = b'{"destination":"/logs/agent/checkpoints","trigger":"fresh_replan", "native_session":true,"components":[{"id":"code","source_path":"/task"}],"point_metadata":{}}'
    config = SForgeConfig(agent_model="fixture", agent_effort="xhigh")
    worker = SForgeWorker(config, profile="heartbeat-resume", cwd="/task", checkpoint_request=declaration)
    if protect_fails:
        with pytest.raises(RuntimeError, match="protect the explicit checkpoint declaration"):
            worker.install_stop_hook(backend, None, tmp_path, None)
        assert not (tmp_path / "worker-profile.json").exists()
    else:
        worker.install_stop_hook(backend, None, tmp_path, None)
        assert json.loads((tmp_path / "worker-profile.json").read_text())["checkpoint_capture_enabled"]
    guest_path = "/opt/loopx-benchmark/control/checkpoint-request.json"
    assert staged == [(guest_path, declaration)]
    assert worker.runtime._worker_env(cwd="/task")["LOOPX_CHECKPOINT_REQUEST"] == guest_path
    assert len(commands) == 1 and commands[0][1]["user"] == "root"
    assert "chmod 0600" in commands[0][0][-1]
    with pytest.raises(ValueError, match="no guest-path override"):
        SForgeWorker(config, profile="heartbeat-resume", cwd="/task", checkpoint_request=declaration,
                     checkpoint_request_path="/other")
    with pytest.raises(ValueError, match="requires destination"):
        SForgeWorker(config, profile="heartbeat-resume", cwd="/task", checkpoint_request=b'{}')


def test_invalid_checkpoint_declaration_rejects_before_trial_registration(tmp_path):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import main
    declaration = tmp_path / "request.json"
    declaration.write_text('{"components":[]}')
    with pytest.raises(ValueError, match="requires destination"):
        main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
              "--run-id", "invalid", "--worker", "heartbeat-resume", "--model", "fixture",
              "--effort", "xhigh", "--judge-url", "http://127.0.0.1:9999",
              "--checkpoint-request", str(declaration)])
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("cadence_args,field,count", [
    ([], "replan_after_effective_turns", 6),
    (["--planning-horizon-goal"], "replan_after_effective_turns", 6),
    (["--no-planning-horizon-goal"], "replan_after_effective_turns", 6),
    (["--replan-after-turns", "2"], "replan_after_effective_turns", 2),
    (["--replan-after-todos", "3"], "replan_after_completed_todos", 3),
])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("entry", [None, "seeded-todo"])
@pytest.mark.parametrize("task,timeout_args,expected,interval", [
    ("fixture", [], 64800, 0),
    ("portfolio_risk_calibration", [], 43200, 0),
    ("lean_analysis_proofs", [], 43200, 0),
    ("portfolio_risk_calibration", ["--timeout", "1800", "--eval-interval", "60"], 1800, 60),
    ("lean_analysis_proofs", ["--eval-interval", "0"], 43200, 0)])
def test_edgebench_native_goal_preserves_task_with_resolved_entry_and_treatment(tmp_path, monkeypatch, enabled, task, timeout_args, expected, interval, entry, cadence_args, field, count):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from benchmark.edgebench import run

    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    (tmp_path / f"{task}.json").write_text("{}")
    monkeypatch.setattr(run, "source_pins", lambda *a: ("fixture", "fixture"))
    monkeypatch.setattr(run, "load_benchmark", lambda *a: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *a: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False,
        game_mode=False, submit_paths=["solver.py"],
        work=SimpleNamespace(agent_query="fixture task"),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    monkeypatch.setattr(run, "RecordingDockerBackend", lambda **k: SimpleNamespace(image_exists=lambda image: True))
    def stop_before_solver(**kwargs):
        # Exercise the real adapter at the CLI handoff, including both context
        # modes. SForge must not resume an exited LoopX scheduler.
        assert kwargs["disable_auto_resume"] is True
        assert kwargs["agent"].resume_cmd is None
        assert kwargs["timeout"] == kwargs["config"].agent_timeout == expected
        assert kwargs["eval_interval"] == interval
        agent = kwargs["agent"]
        horizon = (True if "--planning-horizon-goal" in cadence_args else
                   False if "--no-planning-horizon-goal" in cadence_args else True)
        assert agent.planning_horizon_goal is horizon
        assert "total trial budget" not in agent.workspace_instructions
        assert agent.task_objective == "fixture task"
        assert agent.task_objective in agent.feedback_prompt
        assert agent.task_objective not in agent.workspace_instructions
        assert "The task defines candidate validity and acceptance" in agent.workspace_instructions
        assert "continue seeking useful improvements" in agent.workspace_instructions
        assert agent.feedback_prompt.startswith(agent.workspace_instructions)
        raise RuntimeError("synthetic launch failure")
    monkeypatch.setattr(run, "run_agent", stop_before_solver)
    args = ["--task", task, "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "receipt", "--worker", "heartbeat-resume", "--model", "fixture",
            "--effort", "xhigh", "--judge-url", "http://127.0.0.1:9999", "--feedback", "native"]
    if entry:
        args += ["--task-entry", entry]
    with pytest.raises(RuntimeError, match="synthetic launch failure"):
        run.main(args + timeout_args + cadence_args + (["--turn-envelope"] if enabled else []))
    receipt = json.loads((tmp_path / f"runs/receipt/{task}/runtime-receipt.json").read_text())
    assert receipt["task_entry"] == (entry or "loopx-planned")
    assert receipt["timeout_seconds"] == expected
    assert receipt["eval_interval"] == interval
    assert receipt[field] == count
    horizon = (True if "--planning-horizon-goal" in cadence_args else
               False if "--no-planning-horizon-goal" in cadence_args else True)
    assert receipt["planning_horizon_goal"] is horizon
    other = ("replan_after_completed_todos" if field == "replan_after_effective_turns"
             else "replan_after_effective_turns")
    assert other not in receipt
    assert ("turn_envelope" in receipt) is enabled
    if enabled:
        assert receipt["turn_envelope"] is True
    assert receipt["status"] == "runner_failed"


@pytest.mark.parametrize("value", [0, 7, True, 2.5, "3"])
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


@pytest.mark.parametrize("task,explicit,expected", [
    ("portfolio_risk_calibration", None, 43200),
    ("lean_analysis_proofs", None, 43200),
    ("lean_analysis_proofs", 86400, 86400),
    ("unknown-future-task", None, 64800),
    ("portfolio_risk_calibration", 1800, 1800),
    ("portfolio_risk_calibration", 64800, 64800),
    ("portfolio_risk_calibration", 0, 0),
])
def test_edgebench_task_timeout_precedence(task, explicit, expected):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _task_default
    assert _task_default(task, "timeout_seconds", explicit, 64800) == expected


@pytest.mark.parametrize("task,explicit,expected", [
    ("portfolio_risk_calibration", None, 300),
    ("lean_analysis_proofs", None, 300),
    ("unknown-future-task", None, 300),
    ("lean_analysis_proofs", 60, 60),
    ("portfolio_risk_calibration", 1800, 1800),
    ("lean_analysis_proofs", 0, 0),
])
def test_edgebench_five_minute_sampling_default_and_explicit_override(task, explicit, expected):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench.run import _task_default
    assert _task_default(task, "eval_interval_seconds", explicit, 300) == expected


@pytest.mark.parametrize("value", [0, -1, True, 2.5, "300"])
def test_edgebench_task_default_rejects_invalid_file_values(monkeypatch, value):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench import run
    monkeypatch.setattr(run.json, "loads", lambda _: {"fixture": {"eval_interval_seconds": value}})
    with pytest.raises(ValueError, match="Invalid EdgeBench eval_interval_seconds"):
        run._task_default("fixture", "eval_interval_seconds", None, 300)


@pytest.mark.parametrize("profile", ["heartbeat-resume", "heartbeat-explore"])
def test_sforge_explicit_todo_cadence_reaches_worker_and_receipt(tmp_path, monkeypatch, profile):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")

    async def installed(self, environment):
        pass

    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                          profile=profile, cwd="/task", replan_after_todos=3)
    worker.install_stop_hook(None, None, tmp_path, None)
    assert worker.replan_after_turns is None
    assert worker.runtime._replan_receipt() == {"replan_after_completed_todos": 3, "planning_horizon_goal": True}
    receipt = json.loads((tmp_path / "worker-profile.json").read_text())
    assert receipt["replan_after_completed_todos"] == 3
    assert "replan_after_effective_turns" not in receipt


@pytest.mark.parametrize("profile", ["heartbeat-resume", "heartbeat-explore"])
def test_sforge_planning_has_no_independent_cap_or_extra_trial_budget(tmp_path, monkeypatch, profile):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    async def installed(self, environment):
        pass
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile=profile, cwd="/task", timeout_seconds=1800)
    worker.install_stop_hook(None, None, tmp_path, None)
    assert worker.runtime.scheduler_timeout == 1800
    assert worker.turn_timeout == 1640
    receipt = json.loads((tmp_path / "worker-profile.json").read_text())
    assert receipt["planning_timeout_seconds"] is None
    assert receipt["timeout_seconds"] == 1800
    worker.prepared = True
    command = worker.format_run_cmd("/task.md")
    assert "LOOPX_PLANNING_TIMEOUT_SEC" not in command
    assert " + 1800 " in command


def test_recorded_solver_exit_does_not_take_status_from_output(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from benchmark.runtime.sforge_backend import RecordingDockerBackend, DockerBackend
    backend = object.__new__(RecordingDockerBackend)
    backend.checkpoint = None
    backend.log_dir = tmp_path / "collected"
    backend.blind_api_endpoint = None
    backend.feedback = None
    backend.execution_command = "synthetic solver"
    monkeypatch.setattr(DockerBackend, "exec_run_with_timeout", lambda *a, **k:
        SimpleNamespace(exit_code=1, timed_out=False, elapsed_seconds=310,
                        output="done: successful looking text"))
    backend.exec_run_with_timeout(None, ["/bin/bash", "-c", backend.execution_command])
    assert backend.execution_receipt == {"exit_code": 1, "execution_started": True,
                                         "timed_out": False, "elapsed_seconds": 310}
    assert json.loads((tmp_path / "execution-receipt.json").read_text()) == backend.execution_receipt


@pytest.mark.parametrize("profile", [
    "official", "single", "native-goal", "heartbeat-resume", "heartbeat-explore",
])
@pytest.mark.parametrize("feedback,prompt", [
    ("native", None), ("native", "Synthetic native wrapper"), ("blind", "Synthetic local task"),
])
def test_install_preserves_mode_owned_submission_access(tmp_path, monkeypatch, profile, feedback, prompt):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from sforge.harness.agent.codex import CodexAgent
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, CodexOffline, BenchmarkCodex
    credential = tmp_path / "synthetic-auth.json"
    credential.write_text("{}")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(credential))
    monkeypatch.delenv("LOOPX_PORTABLE_PYTHON", raising=False)
    async def installed(self, environment):
        pass
    async def forbidden(*args):
        raise AssertionError("Plain native/blind worker staged extra runtime")
    monkeypatch.setattr(CodexOffline, "install", installed)
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    monkeypatch.setattr("benchmark.runtime.sforge.stage_portable_python", forbidden)
    monkeypatch.setattr(CodexAgent, "install_stop_hook", lambda *args: None)
    commands = []
    def exec_run(handle, command, **kwargs):
        commands.append(command)
        return SimpleNamespace(exit_code=0)
    backend = SimpleNamespace(exec_run=exec_run,
                              copy_to_container=lambda *args: None)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile=profile, cwd="/task", feedback=feedback,
        feedback_prompt=prompt)
    worker.install_stop_hook(backend, None, tmp_path, None)
    assert (["rm", "-f", "/usr/local/bin/sforge-submit"] in commands) is (feedback != "native")
    assert json.loads((tmp_path / "worker-profile.json").read_text())["feedback"] == feedback


def test_warm_context_receipt_is_separate_from_native_stderr(tmp_path):
    pytest.importorskip("sforge")
    from types import SimpleNamespace
    from benchmark.runtime.sforge import SForgeWorker

    calls = []
    async def execute(command, **kwargs):
        calls.append(command)
        if command.startswith("cat "):
            return SimpleNamespace(return_code=0, stdout='{"model_calls": 0}')
        assert " > " in command
        return SimpleNamespace(return_code=0, stdout="native CLI diagnostic\n")
    worker = object.__new__(SForgeWorker)
    worker.runtime = SimpleNamespace(_worker_env=lambda **kwargs: {})
    worker.environment = SimpleNamespace(exec=execute)
    worker.cwd = "/task"
    worker.log_dir = tmp_path
    worker._warm_command("context")
    assert json.loads((tmp_path / "checkpoint-context.json").read_text()) == {"model_calls": 0}
    assert len(calls) == 2


@pytest.mark.parametrize('profile', ['official', 'single', 'native-goal', 'heartbeat-resume'])
def test_custom_provider_uses_environment_credential_without_copying_login(tmp_path, monkeypatch, profile):
    pytest.importorskip('sforge')
    pytest.importorskip('harbor')
    import tomllib
    from types import SimpleNamespace
    from sforge.harness.agent.codex import CodexAgent
    from sforge.harness.config import SForgeConfig
    from sforge.harness.run_agent import _build_agent_env
    from benchmark.runtime.sforge import SForgeWorker, CodexOffline, BenchmarkCodex

    # An ambient, unusable login must neither be needed nor staged on this route.
    monkeypatch.setenv('CODEX_AUTH_JSON_PATH', str(tmp_path / 'must-not-read-auth.json'))
    monkeypatch.setenv('OPENAI_API_KEY', 'wrong-ambient-key')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://wrong.example/v1')
    async def installed(self, environment):
        pass
    monkeypatch.setattr(CodexOffline, 'install', installed)
    monkeypatch.setattr(BenchmarkCodex, 'install', installed)
    monkeypatch.setattr(CodexAgent, 'install_stop_hook', lambda *args: None)
    copied = {}
    def copy(handle, source, target):
        copied[str(target)] = source.read_text()
    backend = SimpleNamespace(exec_run=lambda *a, **k: SimpleNamespace(exit_code=0),
                              copy_to_container=copy)
    config = SForgeConfig(agent_model='fixture', agent_effort='xhigh',
                         agent_api_base_url='http://127.0.0.1:9876/v1',
                         agent_api_key='synthetic-provider-secret')
    worker = SForgeWorker(config, profile=profile, cwd='/task')
    worker.install_stop_hook(backend, None, tmp_path, None)
    native_env = _build_agent_env(worker)
    assert native_env['OPENAI_API_KEY'] == config.agent_api_key
    assert native_env['OPENAI_BASE_URL'] == config.agent_api_base_url
    assert not any(name.endswith('/auth.json') for name in copied)
    if profile in {'official', 'single'}:
        settings = tomllib.loads(copied['/home/agent/.codex/config.toml'])
        assert settings['model_provider'] == 'harbor'
        assert settings['model_providers']['harbor']['env_key'] == 'OPENAI_API_KEY'
        assert settings['model_providers']['harbor']['base_url'] == config.agent_api_base_url
    else:
        assert worker.runtime._get_env('CODEX_AUTH_JSON_PATH') == ''
        env = worker.runtime._worker_env(cwd='/task')
        assert env['OPENAI_API_KEY'] == config.agent_api_key
        assert env['OPENAI_BASE_URL'] == config.agent_api_base_url
        worker.prepared = True
    command = worker.format_run_cmd('/task/prompt.md')
    assert config.agent_api_key not in command
    assert all(config.agent_api_key not in contents for contents in copied.values())
    config.agent_api_key = None
    with pytest.raises(ValueError, match='API credential'):
        SForgeWorker(config, profile=profile, cwd='/task')


def test_custom_provider_without_key_fails_before_creating_attempt(tmp_path, monkeypatch):
    pytest.importorskip('sforge')
    pytest.importorskip('harbor')
    from benchmark.edgebench.run import main
    monkeypatch.setenv('OPENAI_BASE_URL', 'http://127.0.0.1:9876/v1')
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    with pytest.raises(SystemExit, match='2'):
        main(['--task', 'fixture', '--tasks-dir', str(tmp_path), '--log-dir', str(tmp_path),
              '--run-id', 'missing-key', '--worker', 'official', '--model', 'fixture',
              '--effort', 'xhigh', '--judge-url', 'http://127.0.0.1:9999'])
    assert not (tmp_path / 'runs').exists()


@pytest.mark.parametrize('custom', [False, True])
def test_edgebench_cli_merges_sforge_env_with_explicit_runner_config(tmp_path, monkeypatch, custom):
    pytest.importorskip('sforge')
    pytest.importorskip('harbor')
    from types import SimpleNamespace
    from benchmark.edgebench import run
    monkeypatch.setenv('LOOPX_SRC_DIR', str(tmp_path))
    monkeypatch.setenv('LOOPX_EXPECTED_COMMIT', 'fixture')
    monkeypatch.setenv('CODEX_AUTH_JSON_PATH', '/synthetic-login')
    monkeypatch.setenv('OPENAI_API_KEY', 'synthetic-provider-secret')
    attestation_key = tmp_path / 'runtime-attestation.key'
    attestation_key.write_text('synthetic-attestation-key')
    monkeypatch.setenv('SFORGE_RUNTIME_ATTESTATION_KEY_FILE', str(attestation_key))
    monkeypatch.setenv('SFORGE_RUNNER_SOURCE_REVISION', 'a' * 40)
    monkeypatch.setenv('SFORGE_WORK_CPU_LIMIT', '9')
    if custom:
        monkeypatch.setenv('OPENAI_BASE_URL', 'http://127.0.0.1:9876/v1')
    else:
        monkeypatch.delenv('OPENAI_BASE_URL', raising=False)
    (tmp_path / 'fixture.json').write_text('{}')
    monkeypatch.setattr(run, 'source_pins', lambda *a: ('fixture', 'fixture'))
    monkeypatch.setattr(run, 'load_benchmark', lambda *a: None)
    monkeypatch.setattr(run, 'make_task_spec', lambda *a: SimpleNamespace(
        cwd='/task', work_image_key='work', judge_image_key='judge', internet=False,
        game_mode=False, submit_paths=['solver.py'], work=SimpleNamespace(agent_query='fixture task'),
        judge=SimpleNamespace(selection='score_first', score_direction='maximize')))
    def backend(**kwargs):
        assert kwargs['oauth_proxy'] is custom
        return SimpleNamespace(image_exists=lambda image: True)
    monkeypatch.setattr(run, 'RecordingDockerBackend', backend)
    def stop_before_solver(**kwargs):
        config = kwargs['config']
        assert config.agent_api_base_url == ('http://127.0.0.1:9876/v1' if custom else None)
        assert config.agent_api_key == ('synthetic-provider-secret' if custom else None)
        assert config.runtime_attestation_key_file == attestation_key
        assert config.runner_source_revision == 'a' * 40
        assert config.work_cpu_limit == 4
        raise RuntimeError('synthetic launch boundary')
    monkeypatch.setattr(run, 'run_agent', stop_before_solver)
    with pytest.raises(RuntimeError, match='synthetic launch boundary'):
        run.main(['--task', 'fixture', '--tasks-dir', str(tmp_path), '--log-dir', str(tmp_path),
                  '--run-id', 'route', '--worker', 'official', '--model', 'fixture',
                  '--effort', 'xhigh', '--judge-url', 'http://127.0.0.1:9999'])
    receipt = (tmp_path / 'runs/route/fixture/runtime-receipt.json').read_text()
    assert 'synthetic-provider-secret' not in receipt


@pytest.mark.parametrize("capture", [False, True])
@pytest.mark.parametrize("context_delivery", ["off", "on"])
def test_native_checkpoint_storage_guidance_reaches_both_prompt_surfaces(tmp_path, monkeypatch, capture, context_delivery):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from types import SimpleNamespace
    from benchmark.edgebench import run
    from benchmark.edgebench.prompts import native_task_prompt, native_task_instructions

    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "fixture")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-unused-credential")
    (tmp_path / "fixture.json").write_text("{}")
    monkeypatch.setattr(run, "source_pins", lambda *a: ("fixture", "fixture"))
    monkeypatch.setattr(run, "load_benchmark", lambda *a: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *a: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False,
        game_mode=False, submit_paths=["solver.py"], work=SimpleNamespace(agent_query="Original acceptance."),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    monkeypatch.setattr(run, "RecordingDockerBackend", lambda **k: SimpleNamespace(image_exists=lambda image: True))
    def before_solver(**kwargs):
        worker = kwargs["agent"]
        assert worker.context_delivery is context_delivery
        assert worker.task_objective == "Original acceptance."
        assert worker.feedback_prompt.startswith(worker.workspace_instructions)
        assert worker.feedback_prompt.endswith("---\n\nOriginal acceptance.\n")
        assert ("### Durable Research Artifacts" in worker.workspace_instructions) is capture
        if not capture:
            policy = dict(submission_cooldown=3600, eval_interval=0, internet=False,
                          selection="score_first", score_direction="maximize")
            assert worker.workspace_instructions == native_task_instructions(["solver.py"], **policy)
            assert worker.feedback_prompt == native_task_prompt("Original acceptance.", ["solver.py"], **policy)
        raise RuntimeError("validated before model execution")
    monkeypatch.setattr(run, "run_agent", before_solver)
    args = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "storage", "--worker", "heartbeat-explore", "--model", "fixture",
            "--effort", "xhigh", "--judge-url", "http://127.0.0.1:9999", "--feedback", "native"]
    if context_delivery != "off":
        args += ["--context-delivery", "on"]
    if capture:
        declaration = tmp_path / "request.json"
        declaration.write_text(json.dumps({"destination": "/logs/agent/checkpoints",
            "components": [{"id": "workspace", "source_path": "/task"}], "point_metadata": {}}))
        args += ["--checkpoint-request", str(declaration)]
    with pytest.raises(RuntimeError, match="validated before model execution"):
        run.main(args)
    receipt = json.loads((tmp_path / "runs/storage/fixture/runtime-receipt.json").read_text())
    assert receipt.get("context_delivery") == (context_delivery if context_delivery != "off" else None)
