"""Explicit receiver setup recovery cannot retry inference or renew an allowance."""
import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchmark.runtime import codex_readiness
from benchmark.tests.test_codex_readiness import native_fixture


@pytest.mark.parametrize("mode,ok,count", [
    ("account_timeout_once", True, 2), ("usage_timeout_once", True, 2),
    ("account_timeout", False, 3), ("initialize_timeout", False, 1),
    ("invalid_account", False, 1), ("forged_timeout", False, 1),
    ("disconnect_once", False, 1), ("empty_usage", False, 1), ("account/read", False, 1),
])
def test_real_stdio_explicit_account_timeout_recovery(tmp_path, mode, ok, count):
    env, trace, home, pending = native_fixture(tmp_path, mode)
    env.update(SFORGE_AGENT_REMAINING_SECONDS="30", SFORGE_AGENT_SEGMENT_STARTED_AT=str(time.time()))
    original = home.joinpath("auth.json").read_bytes(), pending.read_bytes()
    result = subprocess.run([sys.executable, "-m", "benchmark.runtime.codex_readiness",
                             "--total-seconds", "4.5", "--attempt-seconds", "1"],
                            env=env, text=True, capture_output=True, timeout=10)
    receipt = json.loads(result.stdout)
    assert result.returncode == (0 if ok else 1)
    assert receipt["ok"] is ok and receipt["inference_invoked"] is False
    assert len(receipt["attempts"]) == count
    assert receipt["attempts"][0]["account_timeout"] is (
        mode in {"account_timeout_once", "usage_timeout_once", "account_timeout"})
    assert int(Path(env["PROBE_ATTEMPTS"]).read_text()) == count
    methods = [json.loads(line)["method"] for line in trace.read_text().splitlines()]
    assert set(methods) <= {"initialize", "initialized", "account/read", "account/rateLimits/read"}
    assert original == (home.joinpath("auth.json").read_bytes(), pending.read_bytes())
    assert not (home / "skills").exists()
    assert "private" not in result.stdout + result.stderr
    if mode in {"account_timeout_once", "usage_timeout_once", "account_timeout", "initialize_timeout"}:
        assert receipt["attempts"][0]["error_kind"] == "timeout"
        assert receipt["elapsed_seconds"] >= 1  # Includes actual process cleanup.


def test_real_stdio_total_clipped_to_existing_outer_allowance(tmp_path):
    env, trace, _, _ = native_fixture(tmp_path, "account_timeout_once")
    # Two seconds have already been spent out of the original 3.5s allowance.
    env.update(SFORGE_AGENT_REMAINING_SECONDS="3.5",
               SFORGE_AGENT_SEGMENT_STARTED_AT=str(time.time() - 2))
    result = codex_readiness.check_native_receiver(env, total_seconds=75, attempt_seconds=25)
    assert result["ok"] is False and result["inference_invoked"] is False
    assert len(result["attempts"]) == 1
    assert 1 <= result["elapsed_seconds"] < 3.5
    assert [json.loads(line)["method"] for line in trace.read_text().splitlines()] == [
        "initialize", "initialized", "account/read"]


@pytest.mark.parametrize("remaining,age", [(None, 0), ("nan", 0), ("30", -10), ("1", 2)])
def test_missing_invalid_or_expired_outer_budget_never_opens_receiver(tmp_path, remaining, age):
    env, trace, _, _ = native_fixture(tmp_path, "success")
    env.pop("SFORGE_AGENT_REMAINING_SECONDS", None)
    if remaining is not None:
        env["SFORGE_AGENT_REMAINING_SECONDS"] = remaining
    env["SFORGE_AGENT_SEGMENT_STARTED_AT"] = str(time.time() - age)
    receipt = codex_readiness.check_native_receiver(env, total_seconds=75)
    assert receipt["ok"] is False and receipt["inference_invoked"] is False
    assert receipt["attempts"][0]["stage"] == "outer_budget"
    assert receipt["attempts"][0]["retryable"] is False
    assert not trace.exists()


@pytest.mark.parametrize("stop", [KeyboardInterrupt, SystemExit])
def test_stop_unwinds_observer_without_retry(tmp_path, monkeypatch, stop):
    env, _, _, _ = native_fixture(tmp_path, "success")
    env.update(SFORGE_AGENT_REMAINING_SECONDS="30", SFORGE_AGENT_SEGMENT_STARTED_AT=str(time.time()))
    calls = []

    @contextmanager
    def observer(*args, **kwargs):
        calls.append("open")
        try:
            yield SimpleNamespace(request=lambda *args: (_ for _ in ()).throw(stop()))
        finally:
            calls.append("cleanup")

    monkeypatch.setattr(codex_readiness, "native_checkpoint_reader", observer)
    with pytest.raises(stop):
        codex_readiness.check_native_receiver(env, total_seconds=75)
    assert calls == ["open", "cleanup"]


@pytest.mark.parametrize("total,attempt", [(None, 1), (76, 25), (75, 26), (0, 1),
                                          (True, 1), (75, float("nan"))])
def test_invalid_explicit_budgets_are_not_receiver_authority(total, attempt):
    with pytest.raises(ValueError):
        codex_readiness.check_native_receiver({}, total_seconds=total, attempt_seconds=attempt)


@pytest.mark.parametrize("profile,custom,warm", [
    ("official", False, None), ("single", False, None),
    ("heartbeat-resume", True, None), ("heartbeat-resume", False, object()),
])
def test_explicit_setup_budget_refuses_other_execution_routes(profile, custom, warm):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    config = SForgeConfig(agent_model="fixture", agent_effort="xhigh",
                         agent_api_base_url="https://provider.invalid/v1" if custom else None,
                         agent_api_key="synthetic" if custom else None)
    with pytest.raises(ValueError, match="fresh native Goal or heartbeat worker"):
        SForgeWorker(config, profile=profile, cwd="/task", warm_start=warm,
                     native_readiness_total_seconds=75)


@pytest.mark.parametrize("selected", [False, True])
def test_edgebench_runner_passes_selected_setup_budget_before_model(tmp_path, monkeypatch, selected):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from benchmark.edgebench import run
    from benchmark.runtime.harbor import BenchmarkCodex

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
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
    backend = SimpleNamespace(image_exists=lambda image: True,
                              exec_run=lambda *a, **k: SimpleNamespace(exit_code=0))
    monkeypatch.setattr(run, "RecordingDockerBackend", lambda **k: backend)

    async def installed(*args):
        pass

    monkeypatch.setattr(BenchmarkCodex, "install", installed)

    def before_model(**kwargs):
        worker = kwargs["agent"]
        assert worker.native_readiness_budget == ((75, 25) if selected else None)
        worker.install_stop_hook(backend, None, tmp_path, None)
        worker.prepared = True
        worker.instructions_installed = worker.prompt_installed = True
        command = worker.format_run_cmd("/task.md")
        assert ("--total-seconds 75.0 --attempt-seconds 25.0" in command) is selected
        raise RuntimeError("isolated before model")

    monkeypatch.setattr(run, "run_agent", before_model)
    args = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "startup", "--worker", "heartbeat-resume", "--model", "fixture",
            "--effort", "xhigh", "--judge-url", "http://127.0.0.1:9999"]
    if selected:
        args += ["--native-readiness-total-seconds", "75", "--native-readiness-attempt-seconds", "25"]
    with pytest.raises(RuntimeError, match="isolated before model"):
        run.main(args)
