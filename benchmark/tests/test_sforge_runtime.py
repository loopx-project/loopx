import subprocess
import tarfile

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
