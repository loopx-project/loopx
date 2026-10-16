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


@pytest.mark.parametrize("mode", ["plain", "native-goal", "heartbeat", "turn", "loopx-goal"])
def test_trial_shell_tools_disable_usage_ping_for_every_execution_mode(tmp_path, mode):
    execution = Execution(mode=mode, **({"validation_command": ("true",)} if mode == "turn" else {}))
    args = settings(tmp_path, execution=execution,
                    **({"skills": None} if not execution.uses_loopx else {}))
    prepare_codex_home(**args)
    config = tomllib.loads((args["home"] / "config.toml").read_text())
    assert config["shell_environment_policy"]["set"]["LOOPX_USAGE_PING"] == "0"


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


def test_non_heartbeat_wakes_do_not_import_checkpoint_control_plane(tmp_path, monkeypatch):
    # plain/native-goal workers stage no LoopX control plane; a checkpoint import
    # there turned a 90-minute trial into a 0.7s ModuleNotFoundError exit.
    monkeypatch.setitem(sys.modules, "benchmark.runtime.checkpoint_wake", None)
    env = worker_env(tmp_path)
    receipt = run_once(env)
    assert receipt["ok"], receipt
    assert receipt.get("error_kind") is None and "checkpoint" not in receipt


def test_native_goal_staged_source_is_importable_without_loopx_control_plane(tmp_path):
    # Non-LoopX workers receive only NATIVE_GOAL_SOURCE_PATHS from `git archive`;
    # a new loopx-internal import in native_codex_goal.py must be staged too,
    # or a native-goal trial dies with ModuleNotFoundError before the model runs.
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import NATIVE_GOAL_SOURCE_PATHS
    from benchmark.runtime.source import archive_source
    repo = Path(__file__).resolve().parents[2]
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
    archive = tmp_path / "source.tar"
    archive_source(repo, head, archive, NATIVE_GOAL_SOURCE_PATHS)
    staged = tmp_path / "staged"
    staged.mkdir()
    subprocess.run(["tar", "-xf", str(archive), "-C", str(staged)], check=True)
    # -I -S: no site-packages, so the editable checkout cannot mask a missing staged file.
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-c",
         f"import sys; sys.path.insert(0, {str(staged)!r}); "
         "import loopx.capabilities.benchmark_toolkit.native_codex_goal as m; print(m.__name__)"],
        cwd=tmp_path, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "loopx.capabilities.benchmark_toolkit.native_codex_goal"


def test_fresh_wakes_share_environment_without_resuming_or_duplicate_session_copies(
    tmp_path,
):
    env = worker_env(tmp_path)
    for _ in range(2):
        receipt = run_once(env)
        assert receipt["ok"]
        assert "turn_envelope" not in receipt
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


@pytest.mark.skipif(os.name != "posix", reason="POSIX worker resource inheritance")
@pytest.mark.parametrize("mode,checkpoint,expected", [
    ("heartbeat", True, 0), ("heartbeat", False, 4 * 1024 * 1024),
    ("plain", True, 4 * 1024 * 1024),
])
def test_checkpoint_cli_worker_bounds_crash_dumps_without_changing_parent_or_files(
    tmp_path, mode, checkpoint, expected,
):
    import resource

    parent_limits = resource.getrlimit(resource.RLIMIT_CORE)
    retained = tmp_path / "core"
    retained.write_bytes(b"existing participant must remain exact")
    script = r'''
import json, os, resource, subprocess, sys
from benchmark.runtime import worker
_, hard = resource.getrlimit(resource.RLIMIT_CORE)
resource.setrlimit(resource.RLIMIT_CORE, (4194304 if hard < 0 else min(4194304, hard), hard))
def probe(env):
    command = [sys.executable, "-c", "import json, resource; print(json.dumps(list(resource.getrlimit(resource.RLIMIT_CORE))))"]
    with worker.child_process(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
        out, err = child.communicate()
        assert child.returncode == 0, err
    print(out.strip())
    assert resource.getrlimit(resource.RLIMIT_CORE)[1] == hard
    return {"ok": True, "timed_out": False, "mode": env["LOOPX_EXECUTION_MODE"]}
worker.run_once = probe
raise SystemExit(worker.main())
'''
    env = dict(os.environ, LOOPX_EXECUTION_MODE=mode, LOOPX_PROJECT=str(tmp_path))
    env.pop("LOOPX_CHECKPOINT_REQUEST", None)
    if checkpoint:
        env["LOOPX_CHECKPOINT_REQUEST"] = "/control/explicit-request.json"
    result = subprocess.run([sys.executable, "-c", script], env=env,
                            text=True, capture_output=True, check=True)
    inherited, receipt = map(json.loads, result.stdout.splitlines())
    assert inherited[0] == (expected if parent_limits[1] < 0 else min(expected, parent_limits[1]))
    assert inherited[1] == parent_limits[1]
    assert receipt["ok"]
    assert resource.getrlimit(resource.RLIMIT_CORE) == parent_limits
    assert retained.read_bytes() == b"existing participant must remain exact"


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
        assert agent.replan_after_turns == (6 if agent.execution.uses_loopx else None)


def test_checkpoint_request_is_explicit_and_reaches_the_existing_worker_env(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    monkeypatch.setenv("LOOPX_CHECKPOINT_REQUEST", "/ambient-request.json")
    off = BenchmarkCodex(logs_dir=tmp_path / "off", model_name="openai/fixture")
    assert "LOOPX_CHECKPOINT_REQUEST" not in off._worker_env(cwd="/workspace")
    on = BenchmarkCodex(logs_dir=tmp_path / "on", model_name="openai/fixture",
                        checkpoint_request_path="/control/checkpoint-request.json")
    assert on._worker_env(cwd="/workspace")["LOOPX_CHECKPOINT_REQUEST"] == "/control/checkpoint-request.json"
    for settings in ({"execution_mode": "plain", "checkpoint_request_path": "/control/request.json"},
                     {"checkpoint_request_path": "relative"}, {"checkpoint_request_path": "/control/../request.json"}):
        with pytest.raises(ValueError, match="checkpoint_request_path"):
            BenchmarkCodex(logs_dir=tmp_path / "invalid", model_name="openai/fixture", **settings)


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("settings,field,value", [
    ({}, "replan_after_effective_turns", 6),
    ({"replan_after_turns": 2}, "replan_after_effective_turns", 2),
    ({"replan_after_todos": 3}, "replan_after_completed_todos", 3),
])
def test_seeded_phase_bootstrap_records_default_turns_or_explicit_cadence(tmp_path, monkeypatch, existing, settings, field, value):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    from loopx.cli import build_parser

    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture",
                           **settings, task_entry="seeded-todo")
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
        return {"todo_id": "todo_fixture", "after": {"execution_profile": {field: value}}}

    monkeypatch.setattr(agent, "_write_task_document", write_task)
    monkeypatch.setattr(agent, "_registry_exists", registry_exists)
    monkeypatch.setattr(agent, "_loopx", cli)
    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)

    asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Synthetic task", cwd=str(tmp_path)))
    assert any(args[:2] == ["todo", "add"] for args in calls)
    assert any(args[0] == "bootstrap" for args in calls) is not existing
    assert all("--clear-waiting-on" not in args for args in calls)
    option = ("--execution-replan-after-turns" if field == "replan_after_effective_turns"
              else "--execution-replan-after-todos")
    configured = [args for args in calls if option in args]
    assert configured and all(args[args.index(option) + 1] == str(value)
                              for args in configured)
    assert agent._replan_receipt() == {field: value}


def test_requested_stride_requires_configuration_readback_before_execution(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture",
                           stride_strategy="evidence_conditioned_v1")

    async def prepared(*args, **kwargs):
        return True

    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)

    async def wrong_strategy(*args, **kwargs):
        return {"after": {"execution_profile": {
            "replan_after_effective_turns": 6, "stride_strategy": "off",
        }}}

    monkeypatch.setattr(agent, "_registry_exists", prepared)
    monkeypatch.setattr(agent, "_write_task_document", prepared)
    monkeypatch.setattr(agent, "_loopx", wrong_strategy)
    with pytest.raises(RuntimeError, match="stride strategy readback mismatch"):
        asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Synthetic task", cwd=str(tmp_path)))


@pytest.mark.parametrize("settings", [
    {"stride_strategy": "future_policy"},
    {"stride_strategy": True},
    {"execution_mode": "plain", "stride_strategy": "off"},
])
def test_stride_rejects_unknown_version_and_incompatible_mode(tmp_path, settings):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    with pytest.raises(ValueError, match="stride_strategy"):
        BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", **settings)


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


@pytest.mark.parametrize("total", [1800, 64800])
def test_harbor_default_execution_budget_tracks_total_trial(tmp_path, total):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture",
                           scheduler_timeout_sec=total)
    assert agent.execution.timeout_seconds == total - 160
    assert agent.scheduler_timeout == total


def test_default_execution_has_no_independent_turn_deadline(tmp_path):
    execution = Execution(mode="turn", validation_command=("true",))
    env = worker_env(tmp_path) | {"LOOPX_CLI": "loopx", "LOOPX_GOAL_ID": "fixture", "LOOPX_AGENT_ID": "worker", "LOOPX_REGISTRY": "registry", "LOOPX_RUNTIME_ROOT": "runtime"}
    assert execution.timeout_seconds is None
    assert "--timeout-seconds" not in turn_command(env, execution, "wake-default")


@pytest.mark.parametrize("enabled,trigger,expected", [
    (False, None, None),
    (True, None, "explicit"),
    (False, "after_wake", None),
    (False, "fresh_replan", "checkpoint_handoff"),
    (True, "fresh_replan", "explicit"),
])
def test_harbor_install_receipt_matches_actual_checkpoint_trigger(tmp_path, monkeypatch, enabled, trigger, expected):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex, CodexOffline
    from benchmark.runtime.checkpoint_wake import CheckpointWake
    wake_root = tmp_path / "wakes"
    wake_root.mkdir()
    request = tmp_path / "request.json"
    checkpoint_args = {}
    if trigger is not None:
        declaration = {"destination": str(tmp_path / "checkpoints"),
            "components": [{"id": "workspace", "source_path": str(tmp_path)}],
            "point_metadata": {}}
        if trigger == "fresh_replan":
            declaration["trigger"] = trigger
        request.write_text(json.dumps(declaration))
        checkpoint_args = {"checkpoint_request_path": str(request),
                           "checkpoint_request": request.read_bytes()}
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", turn_envelope=enabled,
                           **checkpoint_args)
    with CheckpointWake({"LOOPX_WAKE_LOG_DIR": str(wake_root),
            **({"LOOPX_CHECKPOINT_REQUEST": str(request)} if trigger else {})},
            {"turn_id": "fixture", "mode": "heartbeat", "stage": "execute"}) as wake:
        # Real declaration parser and socket lifecycle, independent of the
        # metadata helper. Omitted trigger is legal after-wake capture.
        assert bool(wake.socket_path) is (trigger == "fresh_replan")
        assert bool(enabled or wake.socket_path) is (expected is not None)
    commands = []
    command_envs = {}
    async def no_op(*args, **kwargs):
        return None
    async def stage(*args, **kwargs):
        agent._runner_commit = "fixture"
        return "fixture"
    async def execute(*args, **kwargs):
        commands.append(kwargs.get("command", ""))
        command_envs[kwargs.get("command", "")] = kwargs.get("env", {})
        return SimpleNamespace(stdout='{"ok": true}', stderr="", return_code=0)
    for key in ("LOOPX_SRC_DIR", "LOOPX_PORTABLE_PYTHON", "LOOPX_NODE_DIR"):
        monkeypatch.setenv(key, str(tmp_path))
    monkeypatch.setattr(CodexOffline, "install", no_op)
    monkeypatch.setattr(agent, "_stage_source", stage)
    monkeypatch.setattr(agent, "exec_as_root", execute)
    monkeypatch.setattr(agent, "exec_as_agent", execute)
    monkeypatch.setattr(agent, "_get_env", lambda key: None)
    async def task_exec(**kwargs):
        assert kwargs == {"command": 'printf "%s" "$PATH"', "timeout_sec": 30}
        return SimpleNamespace(stdout="/task/toolchain:/usr/bin:/bin", return_code=0)
    asyncio.run(agent.install(SimpleNamespace(
        default_user="fixture", upload_dir=no_op, exec=task_exec,
    )))
    assert agent._task_path == "/task/toolchain:/usr/bin:/bin"
    assert "/task/toolchain" in agent._shell_path_restore()
    command = next(c for c in commands if "> /logs/agent/loopx-install.json" in c)
    receipt = json.loads(shlex.split(command)[2])
    assert receipt["loopx_usage_ping_enabled"] is False
    for command in commands:
        if "install-local.sh" in command or " doctor " in command:
            assert command_envs[command]["LOOPX_USAGE_PING"] == "0"
    assert agent._worker_env(cwd="/task")["LOOPX_USAGE_PING"] == "0"
    assert ("turn_envelope" in receipt) is (expected is not None)
    assert receipt.get("turn_envelope_source") == expected
    if expected:
        assert receipt["turn_envelope"] is True


@pytest.mark.parametrize("enabled", [False, True])
def test_wake_receipt_preserves_opt_out(tmp_path, monkeypatch, enabled):
    from benchmark.tests.test_resume_sessions import resume_env
    env = resume_env(tmp_path, monkeypatch)
    env["LOOPX_TURN_ENVELOPE"] = "1" if enabled else "0"
    receipt = run_once(env)
    assert receipt["ok"], receipt
    assert ("turn_envelope" in receipt) is enabled
    assert ("turn_envelope_source" in receipt) is enabled
    if enabled:
        assert receipt["turn_envelope"] is True
        assert receipt["turn_envelope_source"] == "explicit"


@pytest.mark.parametrize("observed", [
    {"replan_after_completed_todos": 2},
    {"replan_after_effective_turns": 3},
    {},
])
def test_turn_cadence_readback_rejects_wrong_unit_or_value(tmp_path, monkeypatch, observed):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", replan_after_turns=2)
    async def no_work(*args, **kwargs):
        return True
    async def cli(*args, **kwargs):
        return {"after": {"execution_profile": observed}}
    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)
    monkeypatch.setattr(agent, "_write_task_document", no_work)
    monkeypatch.setattr(agent, "_registry_exists", no_work)
    monkeypatch.setattr(agent, "_seed_phase", no_work)
    monkeypatch.setattr(agent, "_loopx", cli)
    with pytest.raises(RuntimeError, match="readback mismatch"):
        asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Fixture", cwd=str(tmp_path)))


@pytest.mark.parametrize("mode", ["plain", "native-goal", "heartbeat"])
def test_task_toolchain_survives_profile_and_login_shell(tmp_path, monkeypatch, mode):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", execution_mode=mode)
    # Shell metacharacters in an image path must remain literal, not execute.
    tool_dir = tmp_path / "tools '$(touch injected)'"
    tool_dir.mkdir()
    tool = tool_dir / "task-compiler"
    tool.write_text("#!/bin/sh\nprintf 'task-toolchain'\n")
    tool.chmod(0o755)
    agent._task_path = f"{tool_dir}:/usr/bin:/bin"
    monkeypatch.setenv("PATH", "/operator/private/bin")
    monkeypatch.setenv("OPERATOR_SECRET", "must-not-propagate")
    profile = agent._profile_env()
    assert "/operator/private/bin" not in profile["PATH"]
    assert "OPERATOR_SECRET" not in profile
    # BASH_ENV runs after login files; emulate a login profile resetting PATH.
    restore = tmp_path / "bash-env"
    restore.write_text(agent._shell_path_restore() + "\n")
    for login in [False, True]:
        result = subprocess.run(
            ["/bin/bash", "-lc" if login else "-c", "task-compiler"],
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "BASH_ENV": str(restore)},
            cwd=tmp_path, text=True, capture_output=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == "task-toolchain"
    assert not (tmp_path / "injected").exists()
    if not agent.execution.uses_loopx:
        assert "/opt/loopx-benchmark/node" not in agent._shell_path_restore()


@pytest.mark.parametrize("stdout,return_code", [("", 0), ("/task/bin", 1)])
def test_missing_task_path_fails_before_install(tmp_path, monkeypatch, stdout, return_code):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex, CodexOffline
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture")
    async def task_exec(**kwargs):
        return SimpleNamespace(stdout=stdout, return_code=return_code)
    async def unexpected(*args):
        pytest.fail("installation must not start without the task PATH")
    monkeypatch.setattr(CodexOffline, "install", unexpected)
    with pytest.raises(RuntimeError, match="task container PATH"):
        asyncio.run(agent.install(SimpleNamespace(exec=task_exec)))


def test_trial_home_stages_model_catalog_and_honors_web_search(tmp_path):
    catalog = tmp_path / "model-catalog.json"
    catalog.write_text(json.dumps({"models": [{"slug": "fixture-model"}]}))
    args = settings(tmp_path) | {"web_search": "live", "model_catalog": catalog}
    prepare_codex_home(**args)
    config = tomllib.loads((args["home"] / "config.toml").read_text())
    assert config["model_catalog_json"] == str(catalog.resolve())
    assert config["web_search"] == "live"
    defaults = settings(tmp_path) | {"home": tmp_path / "default-home"}
    prepare_codex_home(**defaults)
    rendered = tomllib.loads((defaults["home"] / "config.toml").read_text())
    assert rendered["web_search"] == "disabled"
    assert "model_catalog_json" not in rendered
    with pytest.raises(ValueError, match="web_search"):
        prepare_codex_home(**(settings(tmp_path) | {
            "home": tmp_path / "ws-home", "web_search": "on"}))
    with pytest.raises(ValueError, match="model catalog missing"):
        prepare_codex_home(**(settings(tmp_path) | {
            "home": tmp_path / "missing-home",
            "model_catalog": tmp_path / "absent.json"}))


def test_chat_bundle_revision_survives_missing_git_binary(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "chat_bundle_script", Path(__file__).resolve().parents[2] / "scripts/chat_bundle.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setenv("LOOPX_RESOLVED_SOURCE_GIT_COMMIT", "feedbeef")

    def missing(*args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(module.subprocess, "run", missing)
    assert module._source_revision() == "feedbeef"

    class Failed:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Failed())
    assert module._source_revision() == "feedbeef"

    class Resolved:
        returncode = 0
        stdout = "abc123\n"

    monkeypatch.setattr(module.subprocess, "run", lambda *a, **k: Resolved())
    assert module._source_revision() == "abc123"


@pytest.mark.parametrize(
    "catalog_body, message",
    [
        ("not json", "not valid JSON"),
        (json.dumps({"models": "nope"}), '"models" list'),
        (json.dumps({"models": [{"slug": "someone-else"}]}), "no entry for"),
        (json.dumps({"models": [{"slug": "fixture-model",
                                 "tool_mode": "code_mode_only"}]}), "tool_mode"),
    ],
)
def test_model_catalog_counterexamples_fail_before_a_trial(tmp_path, catalog_body, message):
    catalog = tmp_path / "model-catalog.json"
    catalog.write_text(catalog_body)
    with pytest.raises(ValueError, match=message):
        prepare_codex_home(**(settings(tmp_path) | {"model_catalog": catalog}))


def test_model_catalog_admits_capability_fields_and_model_messages(tmp_path):
    # model_messages is prompt content; it is disclosed, not rejected, and the
    # qualification boundary below is the check that actual tools survive.
    catalog = tmp_path / "model-catalog.json"
    catalog.write_text(json.dumps({"models": [{
        "slug": "fixture-model", "tool_mode": None,
        "supports_search_tool": True, "model_messages": ["be terse"],
    }]}))
    prepare_codex_home(**(settings(tmp_path) | {"model_catalog": catalog}))


def test_toolface_qualification_captures_first_request_and_rejects_empty(tmp_path):
    from benchmark.runtime import toolface

    fake = tmp_path / "codex"
    fake.write_text("""#!/usr/bin/env python3
import json, os, re, sys, urllib.request
if sys.argv[1:2] == ["--version"]:
    print("codex-cli 0.0.0-fake"); raise SystemExit(0)
if os.environ.get("FAKE_DIE"):
    print("error: model catalog entry is missing field display_name", file=sys.stderr)
    raise SystemExit(2)
home = os.environ["CODEX_HOME"]
config = open(os.path.join(home, "config.toml")).read()
base = re.search(r'base_url = "([^"]+)"', config).group(1)
model = re.search(r'model = "([^"]+)"', config).group(1)
tools = json.loads(os.environ.get("FAKE_TOOLS", "[]"))
namespaces = [] if os.environ.get("FAKE_NO_NAMESPACE") else [
    {"name": "functions", "tools": [{"name": "exec"}]}]
body = {"model": model, "tools": tools,
        "input": [{"type": "additional_tools", "tools": namespaces}]}
request = urllib.request.Request(base + "/responses",
    data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
try:
    urllib.request.urlopen(request, timeout=10)
except Exception:
    pass
raise SystemExit(1)
""")
    fake.chmod(0o755)
    receipt = toolface.qualify_toolface(
        {"web_search", "apply_patch", "exec"},
        codex_bin=fake, model="fixture-model", timeout_sec=30,
        extra_env={"FAKE_TOOLS": json.dumps(
            [{"type": "web_search"}, {"name": "apply_patch"}])},
    )
    assert receipt["binary_version"] == "codex-cli 0.0.0-fake"
    assert receipt["tools"] == ["apply_patch", "functions.exec", "web_search"]
    with pytest.raises(RuntimeError, match="missing"):
        toolface.qualify_toolface(
            {"web_search"}, codex_bin=fake, model="fixture-model",
            timeout_sec=30, extra_env={"FAKE_TOOLS": json.dumps(
                [{"name": "apply_patch"}])})
    with pytest.raises(RuntimeError, match="empty tool list"):
        toolface.qualify_toolface(
            set(), codex_bin=fake, model="fixture-model", timeout_sec=30,
            extra_env={"FAKE_TOOLS": "[]", "FAKE_NO_NAMESPACE": "1"})
    # A client that cannot parse its catalog dies before any request; the
    # qualification must surface the client's own words, not a timeout.
    with pytest.raises(RuntimeError, match="missing field display_name"):
        toolface.qualify_toolface(
            set(), codex_bin=fake, model="fixture-model", timeout_sec=30,
            extra_env={"FAKE_DIE": "1"})
