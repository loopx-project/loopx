"""Opt-in recovery reaches the native entry without changing research policy."""
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tarfile
from types import SimpleNamespace

import pytest

pytest.importorskip("sforge")
pytest.importorskip("harbor")

from sforge.harness.config import SForgeConfig
from sforge.harness.evolve_scripts import generate_submit_script

from benchmark.runtime.sforge import BenchmarkCodex, SForgeWorker, SUBMISSION_RECOVERY_ROOT


@pytest.mark.parametrize("profile", ["heartbeat-resume", "heartbeat-explore"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("custom_endpoint", [False, True])
def test_native_install_keeps_authentication_and_research_policy(
        tmp_path, monkeypatch, profile, enabled, custom_endpoint):
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    config = SForgeConfig(agent_model="fixture", agent_effort="high",
        **({"agent_api_base_url": "https://model.example.invalid/v1",
            "agent_api_key": "synthetic-token"} if custom_endpoint else {}))
    installed, commands, starts = {}, [], []
    native = generate_submit_script()

    async def install_runtime(self, environment):
        pass  # Source staging is covered separately; exercise the adapter hook.

    monkeypatch.setattr(BenchmarkCodex, "install", install_runtime)

    def execute(_handle, command, **kwargs):
        commands.append(command)
        return SimpleNamespace(exit_code=0,
            output=native if command == ["cat", "/usr/local/bin/sforge-submit"] else "")

    def copy(_handle, source, destination):
        installed[str(destination)] = source.read_text()

    publisher = SimpleNamespace(start=lambda *args: starts.append(args))
    backend = SimpleNamespace(exec_run=execute, copy_to_container=copy,
                              submission_receipts=publisher if enabled else None)
    worker = SForgeWorker(config, profile=profile, cwd="/task", submission_recovery=enabled,
                          stride_strategy="off")
    worker.install_stop_hook(backend, "synthetic-container", tmp_path, None)
    env = worker.runtime._worker_env(cwd="/task")
    assert worker.runtime.stride_strategy == "off"
    assert worker.runtime.research_loop is False
    assert "LOOPX_RESEARCH_LOOP" not in env
    assert "LOOPX_RESEARCH_FEEDBACK_ROOT" not in env
    receipt = json.loads((tmp_path / "worker-profile.json").read_text())
    assert receipt.get("submission_recovery", False) is enabled
    assert "research_loop" not in receipt
    worker._execution_command("synthetic worker")
    assert len(starts) == int(enabled)
    if enabled:
        script = installed["/usr/local/bin/sforge-submit"]
        assert "benchmark.runtime.submission_recovery" in script
        assert SUBMISSION_RECOVERY_ROOT in script
        assert "--wait-todo" not in script
        assert "record_observer_result_attachment" not in str(commands)
    else:
        assert not installed and not commands


@pytest.mark.parametrize("options", [
    {"profile": "official"}, {"profile": "single"}, {"profile": "native-goal"},
    {"feedback": "blind"}, {"feedback": "best-only"}, {"warm_start": object()},
    {"checkpoint_request": b"{}"}, {"checkpoint_request_path": "/control/request.json"},
])
def test_recovery_rejects_unsupported_worker_before_install(monkeypatch, options):
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    with pytest.raises(ValueError, match="submission_recovery"):
        SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="high"),
            **{"profile": "heartbeat-explore", "cwd": "/task", "submission_recovery": True, **options})


@pytest.mark.parametrize("options", [
    ["--worker", "official"], ["--feedback", "blind"],
    ["--restore-checkpoint", "synthetic-point"], ["--checkpoint-request", "missing.json"],
])
def test_recovery_cli_rejects_before_creating_an_attempt(tmp_path, monkeypatch, options):
    from benchmark.edgebench.run import main

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    with pytest.raises(SystemExit) as failure:
        main(["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
              "--run-id", "synthetic-run", "--worker", "heartbeat-explore", "--model", "fixture",
              "--effort", "high", "--judge-url", "http://127.0.0.1:9999",
              "--submission-recovery", *options])
    assert failure.value.code == 2
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("enabled,game_mode", [(False, False), (True, False), (True, True)])
def test_recovery_cli_handoff_preserves_native_limits(tmp_path, monkeypatch, enabled, game_mode):
    from benchmark.edgebench import run

    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    monkeypatch.setenv("LOOPX_SRC_DIR", str(tmp_path))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", "synthetic")
    (tmp_path / "fixture.json").write_text("{}")
    monkeypatch.setattr(run, "source_pins", lambda *args: ("synthetic", "synthetic"))
    monkeypatch.setattr(run, "load_benchmark", lambda *args: None)
    monkeypatch.setattr(run, "make_task_spec", lambda *args: SimpleNamespace(
        cwd="/task", work_image_key="work", judge_image_key="judge", internet=False,
        game_mode=game_mode, submit_paths=["candidate.py"],
        work=SimpleNamespace(agent_query="Synthetic task"),
        judge=SimpleNamespace(selection="score_first", score_direction="maximize")))
    constructed = {}

    def backend(**kwargs):
        constructed.update(kwargs)
        return SimpleNamespace(image_exists=lambda _image: True)

    def handoff(**kwargs):
        assert kwargs["agent"].submission_recovery is enabled
        assert kwargs["agent"].stride_strategy == "off"
        assert kwargs["eval_interval"] == 0
        assert kwargs["submission_cooldown"] == 3600
        assert kwargs["max_submissions"] is None
        assert kwargs["disable_auto_eval"] is False
        assert ("submission_receipts" in constructed) is enabled
        raise RuntimeError("synthetic launch boundary")

    monkeypatch.setattr(run, "RecordingDockerBackend", backend)
    monkeypatch.setattr(run, "run_agent", handoff)
    argv = ["--task", "fixture", "--tasks-dir", str(tmp_path), "--log-dir", str(tmp_path),
            "--run-id", "synthetic-run", "--worker", "heartbeat-explore", "--model", "fixture",
            "--effort", "high", "--judge-url", "http://127.0.0.1:9999", "--stride-strategy", "off",
            *(["--submission-recovery"] if enabled else [])]
    if game_mode:
        with pytest.raises(ValueError, match="non-game task"):
            run.main(argv)
        assert not constructed, "unsupported tasks fail before Docker or native registration"
        return
    with pytest.raises(RuntimeError, match="synthetic launch boundary"):
        run.main(argv)
    receipt = json.loads((tmp_path / "runs/synthetic-run/fixture/runtime-receipt.json").read_text())
    assert receipt.get("submission_recovery", False) is enabled
    assert "research_loop" not in receipt


def test_unsupported_native_script_keeps_the_original_entry(monkeypatch):
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", "/synthetic-credential")
    worker = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="high"),
                          profile="heartbeat-explore", cwd="/task", submission_recovery=True)
    copied = []
    backend = SimpleNamespace(submission_receipts=object(),
        exec_run=lambda *args, **kwargs: SimpleNamespace(exit_code=0, output="unsupported native script"),
        copy_to_container=lambda _handle, _source, target: copied.append(PurePosixPath(target)))
    with pytest.raises(ValueError, match="unsupported_native_submit_script_boundary"):
        worker._install_submission_recovery(backend, None)
    assert not copied


def test_recovery_helpers_stage_with_the_existing_runtime_overlay(tmp_path):
    from benchmark.runtime.source import archive_source

    source = Path(__file__).resolve().parents[2]
    head = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    archive = tmp_path / "runner.tar"
    archive_source(source, head, archive, ("benchmark/runtime",))
    guest = tmp_path / "guest"
    guest.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(guest, filter="data")
    # This is the same committed runtime overlay used with a separate main
    # product pin. Neither helper requires the deleted EdgeBench observer.
    command = [sys.executable, "-c",
        "from pathlib import Path; "
        "from benchmark.runtime import submission_recovery as recovery, submission_receipts as receipts; "
        "assert Path(recovery.__file__).is_relative_to(Path.cwd()); "
        "assert Path(receipts.__file__).is_relative_to(Path.cwd()); "
        "recovery.main(['--help'])"]
    result = subprocess.run(command, cwd=guest,
        env={**os.environ, "PYTHONPATH": str(guest)}, capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert "--root" in result.stdout
