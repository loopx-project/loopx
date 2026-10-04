from __future__ import annotations

import json
import asyncio
import os
import shlex
import subprocess
import sys
import time
import tomllib
from types import SimpleNamespace
from pathlib import Path

import pytest

from benchmark.runtime.codex import Execution, prepare_codex_home
from benchmark.runtime.worker import run_once, turn_command


def settings(tmp_path, **changes):
    skills = tmp_path / "installed-skills"
    skills.mkdir(exist_ok=True)
    return (
        dict(
            home=tmp_path / "home",
            execution=Execution(),
            workspace=tmp_path,
            model="fixture-model",
            effort="high",
            base_url="http://localhost:8123/v1",
            api_key="fixture-key",
            wire_api="responses",
            skills=skills,
        )
        | changes
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"mode": "unknown"},
        {"context": "resume-if-available"},
        {"mode": "turn"},
        {"timeout_seconds": float("nan")},
        {"validation_command": ("true",)},
        {"mode": "turn", "validation_command": "true"},
        {"mode": "turn", "validation_command": {"command": "true"}},
        {"sandbox": "unrecognised"},
    ],
)
def test_invalid_execution_rejected_before_host(kwargs):
    with pytest.raises(ValueError):
        Execution(**kwargs)


def test_trial_home_preserves_sessions_and_fixes_nonconversation_inputs(tmp_path):
    args = settings(tmp_path)
    prepare_codex_home(**args)
    session = args["home"] / "sessions" / "old.jsonl"
    session.parent.mkdir()
    session.write_text("history\n")
    prepare_codex_home(**args)
    assert session.read_text() == "history\n"
    config = tomllib.loads((args["home"] / "config.toml").read_text())
    assert config["features"]["goals"] is False
    assert config["memories"] == {"generate_memories": False, "use_memories": False}
    assert config["model_providers"]["harbor"]["wire_api"] == "responses"
    assert not (args["home"] / "auth.json").exists()
    with pytest.raises(ValueError, match="settings changed"):
        prepare_codex_home(**(args | {"effort": "medium"}))


def test_baseline_isolated_from_loopx_skills(tmp_path):
    args = settings(tmp_path, execution=Execution(mode="native-goal"), skills=None)
    prepare_codex_home(**args)
    assert not (args["home"] / "skills").exists()
    assert tomllib.loads((args["home"] / "config.toml").read_text())["features"][
        "goals"
    ]
    (args["home"] / "skills").mkdir()
    with pytest.raises(ValueError, match="baseline"):
        prepare_codex_home(**args)


def worker_env(tmp_path):
    task = tmp_path / "task.md"
    task.write_text("Synthetic task.\n")
    binary = tmp_path / "codex"
    binary.write_text(
        f"#!{sys.executable}\n"
        + """
import json, os, pathlib, sys
home = pathlib.Path(os.environ["CODEX_HOME"])
sessions = home / "sessions"
sessions.mkdir(exist_ok=True)
number = len(list(sessions.iterdir()))
(sessions / f"session-{number}.jsonl").write_text("{}\\n")
print(json.dumps({"argv": sys.argv[1:], "home": str(home)}))
sys.stdin.read()
"""
    )
    binary.chmod(0o755)
    return dict(os.environ) | {
        "LOOPX_EXECUTION_MODE": "plain",
        "LOOPX_PROJECT": str(tmp_path),
        "LOOPX_TASK_DOC": str(task),
        "LOOPX_CODEX_HOME": str(tmp_path / "home"),
        "LOOPX_WAKE_LOG_DIR": str(tmp_path / "logs" / "wakes"),
        "CODEX_BIN": str(binary),
        "MODEL_NAME": "fixture",
        "REASONING_EFFORT": "high",
        "OPENAI_BASE_URL": "http://localhost:8123/v1",
        "OPENAI_API_KEY": "fixture-key",
    }


def test_fresh_wakes_share_environment_without_resuming_or_duplicate_session_copies(
    tmp_path,
):
    env = worker_env(tmp_path)
    for _ in range(2):
        assert run_once(env)["ok"]
    logs = tmp_path / "logs"
    assert len(list((logs / "sessions").glob("*.jsonl"))) == 2
    calls = [json.loads(p.read_text()) for p in (logs / "wakes").glob("*/stdout.jsonl")]
    assert len({p["home"] for p in calls}) == 1
    assert all("resume" not in p["argv"] for p in calls)
    assert not list((logs / "wakes").glob("*/sessions"))


def test_process_failure_is_not_reported_as_success(tmp_path):
    env = worker_env(tmp_path)
    Path(env["CODEX_BIN"]).write_text(f"#!{sys.executable}\nraise SystemExit(7)\n")
    receipt = run_once(env)
    assert receipt["ok"] is False
    assert receipt["return_code"] == 7


def test_timeout_keeps_receipt_and_reaps_child(tmp_path):
    env = worker_env(tmp_path)
    env["LOOPX_CODEX_TURN_TIMEOUT_SEC"] = "1"
    Path(env["CODEX_BIN"]).write_text(
        f"#!{sys.executable}\n"
        + """
import os,pathlib,time
pathlib.Path("pid").write_text(str(os.getpid()))
time.sleep(60)
"""
    )
    receipt = run_once(env)
    assert receipt["timed_out"] and not receipt["ok"]
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "pid").read_text()), 0)


@pytest.mark.skipif(os.name != "posix", reason="POSIX scheduler cancellation")
def test_scheduler_cancel_reaps_detached_worker_host_and_keeps_receipt(tmp_path):
    env = worker_env(tmp_path)
    source = Path(__file__).resolve().parents[2]
    env["PYTHONPATH"] = str(source)
    Path(env["CODEX_BIN"]).write_text(
        f"#!{sys.executable}\n"
        "import os,pathlib,signal,time\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "pathlib.Path('pid').write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    cli = tmp_path / "quota"
    payload = {
        "should_run": True,
        "effective_action": "run_now",
        "scheduler_hint": {
            "action": "run_now",
            "cadence_class": "active_work",
            "reason": "fixture",
            "reset_policy": {"reset_token": "fixture"},
            "cold_path_detail": {
                "local_scheduler": {
                    "recommended_interval_minutes": 1,
                    "example_progression_minutes": [1],
                    "unchanged_poll_limit": None,
                    "after_limit": "continue",
                }
            },
        },
    }
    cli.write_text(f"#!{sys.executable}\nprint({json.dumps(payload)!r})\n")
    cli.chmod(0o755)
    command = [
        sys.executable,
        str(source / "scripts/external_scheduler_worker.py"),
        "--cli-bin",
        str(cli),
        "--goal-id",
        "fixture",
        "--agent-id",
        "fixture",
        "--state-file",
        str(tmp_path / "scheduler.json"),
        "--wake-cmd",
        "exec " + shlex.join([sys.executable, "-m", "benchmark.runtime.worker"]),
    ]
    process = subprocess.Popen(
        command,
        env=env,
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "pid").exists() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert (tmp_path / "pid").exists()
        process.terminate()
        process.wait(timeout=15)
        with pytest.raises(ProcessLookupError):
            os.kill(int((tmp_path / "pid").read_text()), 0)
        receipt = json.loads(
            next((tmp_path / "logs/wakes").glob("*/receipt.json")).read_text()
        )
        assert receipt["error_kind"] == "KeyboardInterrupt"
        assert not receipt["ok"]
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_turn_uses_public_cli_and_core_session_policy(tmp_path):
    env = worker_env(tmp_path) | {
        "LOOPX_CLI": "loopx",
        "LOOPX_REGISTRY": "registry.json",
        "LOOPX_RUNTIME_ROOT": "runtime",
        "LOOPX_GOAL_ID": "goal",
        "LOOPX_AGENT_ID": "agent",
    }
    execution = Execution(
        mode="turn",
        context="resume",
        validation_command=("python", "trusted-validator.py"),
    )
    command = turn_command(env, execution, "wake-fixture")
    from loopx.cli import build_parser

    parsed = build_parser().parse_args(command[1:])
    assert parsed.iteration_context == "resume"
    assert parsed.session_scope == "agent"
    assert parsed.validation_command_json == '["python", "trusted-validator.py"]'
    assert parsed.codex_sandbox == "danger-full-access"


def test_turn_defaults_and_retired_context_rejection():
    from loopx.cli import build_parser

    parser = build_parser()
    argv = ["turn", "plan", "--goal-id", "fixture-goal", "--agent-id", "fixture-agent"]
    parsed = parser.parse_args(argv)
    assert parsed.iteration_context == "resume" and parsed.session_scope == "agent"
    with pytest.raises(SystemExit):
        parser.parse_args([*argv, "--iteration-context", "resume-if-available"])


def test_failed_turn_restarts_same_transaction_until_core_recovers(tmp_path):
    env = worker_env(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    cli = tmp_path / "loopx"
    cli.write_text(
        f"#!{sys.executable}\n"
        + """
import json, pathlib, sys
log = pathlib.Path("calls.jsonl")
first = not log.exists()
with log.open("a") as output:
    output.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps({"ok": not first, "resume_turn_key": "sha256:" + "a" * 64}))
sys.exit(1 if first else 0)
"""
    )
    cli.chmod(0o755)
    env.update(
        LOOPX_EXECUTION_MODE="turn",
        LOOPX_CLI=str(cli),
        LOOPX_SHARED_SKILLS=str(skills),
        LOOPX_REGISTRY=str(tmp_path / "registry.json"),
        LOOPX_RUNTIME_ROOT=str(tmp_path / "runtime"),
        LOOPX_GOAL_ID="fixture-goal",
        LOOPX_AGENT_ID="fixture-agent",
        LOOPX_VALIDATION_COMMAND_JSON='["trusted-validator"]',
    )
    assert not run_once(env)["ok"]
    pending = tmp_path / "runtime/benchmark-pending-turn.json"
    assert pending.exists()
    assert run_once(env)["ok"]
    assert not pending.exists()
    assert run_once(env)["ok"]
    calls = [
        json.loads(line) for line in (tmp_path / "calls.jsonl").read_text().splitlines()
    ]
    first, recovery, successor = calls
    assert "--turn-instance-id" in first
    assert "--turn-instance-id" not in recovery
    assert "--retry-failed-turn" in recovery
    assert recovery[recovery.index("--resume-turn-key") + 1] == "sha256:" + "a" * 64
    assert (
        successor[successor.index("--turn-instance-id") + 1]
        != first[first.index("--turn-instance-id") + 1]
    )


def test_harbor_imports_and_keeps_native_sessions_separate(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture")
    date = tmp_path / "sessions" / "2026" / "01" / "01"
    date.mkdir(parents=True)
    for name in ("first", "second"):
        (date / f"{name}.jsonl").write_text("{}\n")
    seen = []

    def convert(directory):
        seen.append([p.name for p in directory.glob("*.jsonl")])

    monkeypatch.setattr(agent, "_convert_events_to_trajectory", convert)
    agent._session_trajectories([tmp_path])
    assert sorted(seen) == [["first.jsonl"], ["second.jsonl"]]


def test_baseline_and_treatment_use_same_harbor_entry(tmp_path):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    modes = ("plain", "native-goal", "heartbeat", "loopx-goal", "turn")
    for mode in modes:
        kwargs = {"validation_command": ["trusted-validator"]} if mode == "turn" else {}
        agent = BenchmarkCodex(
            logs_dir=tmp_path / mode,
            model_name="openai/fixture",
            execution_mode=mode,
            **kwargs,
        )
        env = agent._worker_env(cwd="/workspace")
        assert env["LOOPX_EXECUTION_MODE"] == mode
        assert env["LOOPX_PROJECT"] == "/workspace"
        assert env["MODEL_NAME"] == "fixture"


@pytest.mark.parametrize("existing", [False, True])
def test_phase_bootstrap_uses_current_public_cli(tmp_path, monkeypatch, existing):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    from loopx.cli import build_parser

    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture")
    calls = []

    async def write_task(*args, **kwargs):
        pass

    async def registry_exists(*args):
        return existing

    async def cli(environment, args, **kwargs):
        # Parse the actual adapter command, so retired flags fail without
        # launching a model or mutating any active project.
        build_parser().parse_args(args)
        calls.append(args)
        return {"todo_id": "todo_fixture", "after": {"execution_profile": {"replan_after_completed_todos": 3}}}

    monkeypatch.setattr(agent, "_write_task_document", write_task)
    monkeypatch.setattr(agent, "_registry_exists", registry_exists)
    monkeypatch.setattr(agent, "_loopx", cli)
    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)

    asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Synthetic task", cwd=str(tmp_path)))
    assert any(args[:2] == ["todo", "add"] for args in calls)
    assert any(args[0] == "bootstrap" for args in calls) is not existing
    assert all("--clear-waiting-on" not in args for args in calls)


def test_staged_snapshot_keeps_observed_commit_when_branch_moves(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    import tarfile
    from benchmark.runtime import harbor

    source = tmp_path / "source"
    source.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-C", str(source), *args],
            check=True,
            capture_output=True,
            text=True,
        )

    git("init")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    marker = source / "revision.txt"
    marker.write_text("original")
    git("add", "revision.txt")
    git("commit", "-m", "original")
    original = git("rev-parse", "HEAD").stdout.strip()
    monkeypatch.setattr(harbor, "__file__", str(source / "benchmark/runtime/harbor.py"))
    monkeypatch.setenv("LOOPX_EXPECTED_COMMIT", original)
    run = subprocess.run

    def moving_head(argv, **kwargs):
        result = run(argv, **kwargs)
        if argv[-2:] == ["rev-parse", "HEAD"]:
            marker.write_text("successor")
            git("add", "revision.txt")
            git("commit", "-m", "successor")
        return result

    from benchmark.runtime import source as source_runtime
    monkeypatch.setattr(source_runtime.subprocess, "run", moving_head)
    uploaded = []

    class Environment:
        async def upload_file(self, path, target):
            with tarfile.open(path) as archive:
                uploaded.append(archive.extractfile("revision.txt").read())

    async def unpack(*args, **kwargs):
        pass

    agent = harbor.BenchmarkCodex(
        logs_dir=tmp_path / "logs", model_name="openai/fixture"
    )
    monkeypatch.setattr(agent, "exec_as_root", unpack)
    staged = asyncio.run(agent._stage_source(Environment(), source))
    assert staged == original
    assert marker.read_text() == "successor"
    assert uploaded == [b"original"]
