import subprocess
import tarfile
import io
import json
from types import SimpleNamespace

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
def test_native_goal_and_heartbeat_use_trial_budget_without_independent_wake_limit(
    tmp_path, monkeypatch, profile, total, expected,
):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker, BenchmarkCodex
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/private-credential")
    async def installed(self, environment):
        pass  # Budget transport test; no container or solver launch.
    monkeypatch.setattr(BenchmarkCodex, "install", installed)
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
                          profile=profile, cwd="/task", timeout_seconds=total)
    worker.install_stop_hook(None, None, tmp_path, None)
    env = worker.runtime._worker_env(cwd="/task")
    assert float(env["LOOPX_CODEX_TURN_TIMEOUT_SEC"]) == expected
    assert worker.runtime.scheduler_timeout == total
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
    judge = AllowedEndpoint(ip="172.17.0.1", port=8080, hostname="judge")
    api = AllowedEndpoint(ip="172.17.0.1", port=9090, hostname="api-proxy")
    monkeypatch.setattr(DockerBackend, "create_network_isolation",
                        lambda self, handle, allowed_endpoints, logger: allowed_endpoints)
    env = {"SFORGE_TOKEN": "fixture", "SFORGE_JUDGE_URL": "http://judge:8080",
           "HTTPS_PROXY": "http://api-proxy:9090", "SFORGE_PATCH_DIR": "/task"}
    assert backend._agent_environment(env) is env
    assert backend.create_network_isolation(None, [judge, api], None) == [judge, api]
    backend.blind_api_endpoint = ("172.17.0.1", 9090)
    assert backend.create_network_isolation(None, [judge, api], None) == [api]
    assert backend._agent_environment(env) == {
        "HTTPS_PROXY": "http://api-proxy:9090", "SFORGE_PATCH_DIR": "/task"}
    with pytest.raises(RuntimeError, match="admitted API-only endpoint"):
        backend.create_network_isolation(None, [judge], None)
    alias = AllowedEndpoint(ip="172.17.0.1", port=9090, hostname="judge-alias")
    with pytest.raises(RuntimeError, match="distinct from the judge"):
        backend.create_network_isolation(None, [alias, api], None)
