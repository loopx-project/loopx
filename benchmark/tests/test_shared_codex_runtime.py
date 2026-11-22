from __future__ import annotations

import json
import asyncio
import os
import shlex
import subprocess
import sys
import time
import tomllib
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path

import pytest

from benchmark.runtime.codex import Execution, prepare_codex_home
from benchmark.runtime import worker
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
        {"mode": "plain", "official_stop_hook": "true"},
        {"mode": "native-goal", "official_stop_hook": True},
    ],
)
def test_invalid_execution_rejected_before_host(kwargs):
    with pytest.raises(ValueError):
        Execution(**kwargs)


@pytest.mark.parametrize("mode", ["plain", "native-goal", "heartbeat", "turn", "loopx-goal"])
def test_mode_default_context_preserves_explicit_fresh_and_transport_opt_ins(tmp_path, mode):
    extra = {"validation_command": ("true",)} if mode == "turn" else {}
    expected = "resume" if mode in {"heartbeat", "turn"} else "fresh"
    implicit = Execution(mode=mode, **extra)
    assert implicit.context == expected
    assert implicit.context_delivery == ("on" if mode == "heartbeat" else "off")
    assert implicit.turn_envelope is False
    assert Execution(mode=mode, context_delivery="off", **extra).context_delivery == "off"
    assert Execution(mode=mode, context="fresh", **extra).context == "fresh"
    if expected == "fresh":
        with pytest.raises(ValueError, match="resume requires"):
            Execution(mode=mode, context="resume", **extra)
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture",
                           execution_mode=mode, **extra)
    assert agent.execution.context == expected
    assert agent._worker_env(cwd="/task")["LOOPX_ITERATION_CONTEXT"] == expected


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


def test_catalog_staging_validates_host_bytes_but_renders_fixed_guest_path(tmp_path):
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"models": [{"slug": "fixture-model"}]}))
    args = settings(tmp_path, model_catalog=catalog,
                    model_catalog_destination=Path("/usr/local/share/codex/model-catalog.json"))
    prepare_codex_home(**args)
    config = tomllib.loads((args["home"] / "config.toml").read_text())
    assert config["model_catalog_json"] == "/usr/local/share/codex/model-catalog.json"
    with pytest.raises(ValueError, match="settings changed"):
        prepare_codex_home(**(args | {"model_catalog_destination": Path("/different/catalog.json")}))
    for invalid in ({"model_catalog": None}, {"model_catalog_destination": Path("relative.json")}):
        with pytest.raises(ValueError, match="catalog destination"):
            prepare_codex_home(**(args | invalid))
    catalog.write_text(json.dumps({"models": [{"slug": "other-model"}]}))
    with pytest.raises(ValueError, match="no entry"):
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
        # This executable models legacy sessions, not native hook discovery.
        "LOOPX_CONTEXT_DELIVERY": "off",
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


def test_resumed_worker_builds_native_command_without_collaboration_server(tmp_path):
    env = worker_env(tmp_path)
    execution = Execution(context_delivery="off")
    session = SimpleNamespace(session_id="retained-session")

    command = worker.native_command(env, execution, "execute", tmp_path, session)

    assert command[:2] == [env["CODEX_BIN"], "exec"]
    assert "resume" in command
    assert command[-2:] == [session.session_id, "-"]
    assert not any(argument.startswith("mcp_servers.") for argument in command)


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
         "import loopx.capabilities.benchmark_toolkit.native_codex_goal as goal; "
         "import loopx.capabilities.benchmark_toolkit.native_codex_process as process; "
         "from loopx.extensions.process_runtime import _darwin_owned_group_has_exited; "
         "import benchmark.runtime.codex_readiness; "
         "print(goal.__name__, process.__name__)"],
        cwd=tmp_path, env={"PATH": os.environ["PATH"]}, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "loopx.capabilities.benchmark_toolkit.native_codex_goal "
        "loopx.capabilities.benchmark_toolkit.native_codex_process"
    )


def test_native_goal_rounds_graceful_deadline_and_opt_in_continuation():
    from benchmark.runtime.worker import native_goal_rounds
    from loopx.capabilities.benchmark_toolkit.native_codex_goal import NativeGoalDeadlineExceeded
    clock = [0.0]
    def tick(seconds):
        clock[0] += seconds
    # Default: one Goal that declares completion ends the worker (no continuation).
    calls = []
    def one_round(suffix, timeout):
        calls.append((suffix, timeout))
        tick(100)
        return "complete"
    assert native_goal_rounds(one_round, budget_seconds=1000, continue_after_complete=False, clock=lambda: clock[0]) == (1, "complete")
    assert calls == [("", 1000)]
    # Opt-in: completed Goals are followed by continuation Goals until the reserve.
    clock[0] = 0
    calls.clear()
    def completing(suffix, timeout):
        calls.append(suffix)
        tick(400)
        return "complete"
    rounds, outcome = native_goal_rounds(completing, budget_seconds=1000, continue_after_complete=True, clock=lambda: clock[0], reserve_seconds=300)
    assert (rounds, outcome) == (2, "complete")
    assert calls[0] == "" and calls[1].startswith("\n\nContinuation 1")
    assert "600 seconds remain" in calls[1]
    assert "original task under its existing requirements and evaluation rules" in calls[1]
    assert "not by a prior improvement claim" in calls[1]
    assert "submit only if it improves" not in calls[1]
    # A Goal still active at the budget edge is normal exhaustion, not a failure.
    clock[0] = 0
    def never_ends(suffix, timeout):
        raise NativeGoalDeadlineExceeded("goal_timeout_before_terminal")
    assert native_goal_rounds(never_ends, budget_seconds=1000, continue_after_complete=True, clock=lambda: clock[0]) == (1, "budget_exhausted")
    # A non-complete terminal status stops without continuation.
    clock[0] = 0
    assert native_goal_rounds(lambda s, t: "cancelled", budget_seconds=1000, continue_after_complete=True, clock=lambda: clock[0]) == (1, "cancelled")


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


def _sqlite_heartbeat_env(tmp_path, monkeypatch):
    from loopx.global_registry import sync_project_registry_to_global
    from tests.control_plane import test_quota_settlement_cli as fixture
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.host_process_fixture import write_python_host_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[2]))
    project, runtime, source = fixture._write_fixture(tmp_path)
    fixture._initialize_fixture_authority(project, runtime, provider="sqlite")
    assert sync_project_registry_to_global(
        registry_path=source, runtime_root_override=str(runtime), goal_id=fixture.GOAL_ID
    )["ok"]
    env = worker_env(project)
    skills = tmp_path / "shared-skills"
    skills.mkdir()
    env.update(
        LOOPX_EXECUTION_MODE="heartbeat",
        LOOPX_ITERATION_CONTEXT="resume",
        LOOPX_TASK_ENTRY="seeded-todo",
        LOOPX_TASK_STAGE="execute",
        LOOPX_RUNTIME_ROOT=str(runtime),
        LOOPX_REGISTRY=str(runtime / "registry.global.json"),
        LOOPX_GOAL_ID=fixture.GOAL_ID,
        LOOPX_AGENT_ID=fixture.AGENT_ID,
        LOOPX_SHARED_SKILLS=str(skills),
        LOOPX_CODEX_TURN_TIMEOUT_SEC="90",
    )
    env["LOOPX_CLI"] = str(
        write_python_host_fixture(
            tmp_path / "loopx-cli",
            f"#!{sys.executable}\nfrom loopx.cli import main\nraise SystemExit(main())\n",
        )
    )
    return env


@pytest.mark.parametrize("code,expected", [("insufficient_quota", "quota_exhausted"),
                                           ("model_at_capacity", "provider_capacity"),
                                           ("unrecognized", "unknown")])
def test_heartbeat_real_native_process_preserves_content_free_host_failure(tmp_path, monkeypatch, code, expected):
    import benchmark.runtime.worker as worker

    env = _sqlite_heartbeat_env(tmp_path, monkeypatch)
    monkeypatch.setattr(worker, "heartbeat_body", lambda *a, **kw: "synthetic work")
    event = {"type": "turn.failed", "error": {"code": code, "message": "PRIVATE-SENTINEL"}}
    Path(env["CODEX_BIN"]).write_text(
        f"#!{sys.executable}\nimport json,sys\n"
        "print(json.dumps({'type':'thread.started','thread_id':'session-a'}))\n"
        f"print(json.dumps({event!r}))\n"
        "sys.stdin.read();sys.exit(1)\n")
    receipt = run_once(env)
    assert receipt["ok"] is False and receipt["return_code"] == 1
    assert receipt["host_failure"]["kind"] == expected
    assert "PRIVATE-SENTINEL" not in json.dumps(receipt)


def test_host_retry_session_mismatch_refuses_native_process(tmp_path, monkeypatch):
    import benchmark.runtime.worker as worker
    import benchmark.runtime.sessions as sessions

    env = worker_env(tmp_path)
    skills = tmp_path / "skills"
    skills.mkdir()
    env.update(LOOPX_EXECUTION_MODE="heartbeat", LOOPX_SHARED_SKILLS=str(skills),
               LOOPX_HOST_RETRY_SESSION_ID="original-session")
    monkeypatch.setattr(worker, "heartbeat_body", lambda *a, **kw: "synthetic work")
    class Session:
        session_id = "replacement-session"
        def __init__(self, *a):
            pass
        def observe(self, path):
            assert not path.exists()
    monkeypatch.setattr(sessions, "BenchmarkSessionWake", Session)
    with pytest.raises(ValueError, match="original Codex session"):
        run_once(env)
    assert not list((tmp_path / "logs/wakes").glob("*/stdout.jsonl"))


@pytest.mark.parametrize("context", ["fresh", "resume"])
def test_native_heartbeat_retry_uses_real_session_owner(tmp_path, monkeypatch, context):
    import benchmark.runtime.worker as worker

    env = _sqlite_heartbeat_env(tmp_path, monkeypatch)
    env["LOOPX_ITERATION_CONTEXT"] = context
    # This fake executable models session retries, not native hook discovery.
    env["LOOPX_CONTEXT_DELIVERY"] = "off"
    monkeypatch.setattr(worker, "heartbeat_body", lambda *a, **kw: "synthetic work")
    arguments = tmp_path / "native-calls.jsonl"
    Path(env["CODEX_BIN"]).write_text(f"#!{sys.executable}\n" + f"""
import json,pathlib,sys
with pathlib.Path({str(arguments)!r}).open('a') as log:
    log.write(json.dumps(sys.argv[1:])+'\\n')
print(json.dumps({{'type':'thread.started','thread_id':'original-native'}}))
resuming = 'resume' in sys.argv
print(json.dumps({{'type':'turn.completed'}} if resuming else
                 {{'type':'turn.failed','error':{{'code':'model_at_capacity'}}}}))
sys.stdin.read()
sys.exit(0 if resuming else 1)
""")
    first = run_once(env)
    assert first["host_failure"]["kind"] == "provider_capacity"
    assert first["session"]["session_id"] == "original-native"
    second = run_once({**env, "LOOPX_HOST_RETRY_SESSION_ID": "original-native"})
    assert second["ok"] and second["session"]["action"] == "resume"
    calls = [json.loads(line) for line in arguments.read_text().splitlines()]
    assert "resume" not in calls[0]
    assert calls[1][calls[1].index("resume") + 1:].count("original-native") == 1
    # A valid but different expected binding cannot fork or invoke the Host.
    with pytest.raises(ValueError, match="original Codex session"):
        run_once({**env, "LOOPX_HOST_RETRY_SESSION_ID": "different-native"})
    assert len(arguments.read_text().splitlines()) == 2


@pytest.mark.parametrize("tail", ['{"type":', '[]', '\ufffd', 'x' * 1_048_577])
def test_partial_native_exchange_stays_unknown_even_after_transient_error(tmp_path, tail):
    from benchmark.runtime.worker import heartbeat_host_failure
    event = {"type": "turn.failed", "error": {"code": "model_at_capacity"}}
    (tmp_path / "stdout.jsonl").write_text(json.dumps(event) + "\n" + tail)
    (tmp_path / "stderr.log").write_text("")
    failure = heartbeat_host_failure(tmp_path, {"ok": False})
    assert failure["kind"] == "unknown" and failure["retryable"] is False


def test_native_scheduler_enables_host_failure_guard_only_on_heartbeat():
    from benchmark.runtime.scheduler import worker_command
    base = {"LOOPX_CLI": "loopx", "LOOPX_REGISTRY": "/synthetic/registry.json",
            "LOOPX_RUNTIME_ROOT": "/synthetic/runtime", "LOOPX_GOAL_ID": "goal",
            "LOOPX_AGENT_ID": "agent"}
    for mode in ["plain", "native-goal", "turn", "heartbeat"]:
        args = worker_command({**base, "LOOPX_EXECUTION_MODE": mode}, python="python",
                              source="/synthetic/source", state_file="/synthetic/state.json", host_timeout=100)
        assert ("--wake-host-failure" in args) is (mode == "heartbeat")


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


def test_timeout_keeps_receipt_and_reaps_child(tmp_path, monkeypatch):
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
    child_pids = []
    original_child_process = worker.child_process

    @contextmanager
    def observe_child(*args, **kwargs):
        with original_child_process(*args, **kwargs) as process:
            # Observe the real OS launch even if the one-second deadline fires
            # before this child can write its fixture file under parallel load.
            child_pids.append(process.pid)
            yield process

    monkeypatch.setattr(worker, "child_process", observe_child)
    receipt = run_once(env)
    assert receipt["timed_out"] and not receipt["ok"]
    assert len(child_pids) == 1
    if (tmp_path / "pid").exists():
        assert int((tmp_path / "pid").read_text()) == child_pids[0]
    with pytest.raises(ProcessLookupError):
        os.kill(child_pids[0], 0)


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
        "--registry",
        str(tmp_path / "registry.json"),
        "--runtime-root",
        str(tmp_path / "runtime"),
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
    original = pending.read_bytes()
    from loopx.file_lock import try_exclusive_file_lock

    with try_exclusive_file_lock(pending) as lock:
        assert lock is not None
        with pytest.raises(RuntimeError, match="Another benchmark wake"):
            run_once(env)
    assert pending.read_bytes() == original
    # Old unbound markers cannot establish which validator checked the work.
    unbound = json.loads(original)
    del unbound["validation_argv_sha256"]
    pending.write_text(json.dumps(unbound))
    with pytest.raises(ValueError, match="original validation command"):
        run_once(env)
    assert json.loads(pending.read_text()) == unbound
    assert len((tmp_path / "calls.jsonl").read_text().splitlines()) == 1
    pending.write_bytes(original)
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


def test_harbor_owns_official_stop_hook_opt_in(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    from benchmark.runtime.scheduler import phase_work_reserve

    monkeypatch.setenv("LOOPX_OFFICIAL_STOP_HOOK", "1")
    for enabled in (False, True):
        agent = BenchmarkCodex(logs_dir=tmp_path, model_name="openai/fixture",
                               execution_mode="plain", **({"official_stop_hook": True} if enabled else {}))
        assert agent._worker_env(cwd="/workspace")["LOOPX_OFFICIAL_STOP_HOOK"] == str(int(enabled))
        reserve = phase_work_reserve(agent.execution.mode, official_stop_hook=agent.execution.official_stop_hook)
        assert (reserve == 0) is enabled


@pytest.mark.parametrize("blocked_method", ["initialize", "hooks/list"])
def test_official_stop_discovery_spends_the_same_phase_clock(tmp_path, blocked_method):
    env = worker_env(tmp_path)
    binary = Path(env["CODEX_BIN"])
    binary.write_text(f"#!{sys.executable}\n" + """
import json
import os
from pathlib import Path
import sys
import time

assert "app-server" in sys.argv
Path("discovery.pid").write_text(str(os.getpid()))
for line in sys.stdin:
    request = json.loads(line)
    if "id" not in request:
        continue
    method = request["method"]
    Path("last-method").write_text(method)
    if method == os.environ["BLOCKED_METHOD"]:
        time.sleep(60)
    time.sleep(.4)
    print(json.dumps({"id": request["id"], "result": {}}), flush=True)
""")
    env.update(LOOPX_OFFICIAL_STOP_HOOK="1", BLOCKED_METHOD=blocked_method)
    started = time.time()
    env["LOOPX_PHASE_DEADLINE_EPOCH"] = str(started + 2)
    result = run_once(env)
    assert time.time() - started < 5
    assert (tmp_path / "last-method").read_text() == blocked_method
    assert result["budget_exhausted"] and result["timed_out"]
    assert result["host_invoked"] is False and "official_stop_hook_verified" not in result
    assert not (tmp_path / "home" / "sessions").exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int((tmp_path / "discovery.pid").read_text()), 0)


def test_official_stop_discovery_preserves_a_protocol_rejection(tmp_path):
    from loopx.control_plane.turn_driver.codex_stdio import NativeGoalProtocolError

    env = worker_env(tmp_path)
    binary = Path(env["CODEX_BIN"])
    binary.write_text(f"#!{sys.executable}\n" + """
import json
import sys

assert "app-server" in sys.argv
request = json.loads(sys.stdin.readline())
assert request["method"] == "initialize"
print(json.dumps({"id": request["id"], "error": {"code": -32603, "message": "rejected"}}), flush=True)
""")
    env.update(LOOPX_OFFICIAL_STOP_HOOK="1", LOOPX_PHASE_DEADLINE_EPOCH=str(time.time() + 4))
    with pytest.raises(NativeGoalProtocolError, match="app_server_request_failed:initialize"):
        run_once(env)
    receipt, = (tmp_path / "logs" / "wakes").glob("*/receipt.json")
    result = json.loads(receipt.read_text())
    assert not result["timed_out"] and "budget_exhausted" not in result
    assert not (tmp_path / "home" / "sessions").exists()


@pytest.mark.parametrize("case", ["ok", "timeout_initialize", "timeout_hooks_list", "protocol_error", "expired"])
def test_official_stop_hook_supports_split_pinned_two_arg_transport(tmp_path, monkeypatch, case):
    from collections.abc import Mapping
    from loopx.control_plane.turn_driver import codex_stdio
    from loopx.control_plane.turn_driver.codex_stdio import NativeGoalProtocolError, StdioNativeGoalTransport

    class LegacyTwoArgTransport(StdioNativeGoalTransport):
        def request(self, method: str, params: Mapping[str, object]) -> Mapping[str, object]:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._send({"id": request_id, "method": method, "params": dict(params)})
            deadline = time.monotonic() + self.response_timeout_sec
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise NativeGoalProtocolError(f"app_server_response_timeout:{method}")
                message = self._next_message(timeout_sec=remaining)
                if message is None:
                    if self.process.poll() is not None:
                        raise NativeGoalProtocolError("app_server_exited_before_response")
                    continue
                if message.get("id") == request_id:
                    if message.get("error") is not None:
                        raise NativeGoalProtocolError(f"app_server_request_failed:{method}")
                    result = message.get("result")
                    return result if isinstance(result, Mapping) else {}
                if message.get("method"):
                    self._pending_events.append(message)

    monkeypatch.setattr(codex_stdio, "StdioNativeGoalTransport", LegacyTwoArgTransport)
    env = worker_env(tmp_path)
    binary = Path(env["CODEX_BIN"])
    binary.write_text(f"#!{sys.executable}\n" + """
import json, os, pathlib, sys, time

home = pathlib.Path(os.environ["CODEX_HOME"])
if "app-server" in sys.argv:
    pathlib.Path("spawned-discovery").write_text("1")
    for line in sys.stdin:
        req = json.loads(line)
        if "id" not in req:
            continue
        method = req["method"]
        pathlib.Path("last-method").write_text(method)
        case = os.environ["SPLIT_CASE"]
        if case == "protocol_error" and method == "initialize":
            print(json.dumps({"id": req["id"], "error": {"code": -32603, "message": "rejected"}}), flush=True)
            continue
        if case == f"timeout_{method.replace('/', '_')}":
            time.sleep(60)
        if method == "hooks/list":
            hook_path = str((home / "hooks.json").resolve())
            stop = json.loads((home / "hooks.json").read_text())["hooks"]["Stop"][0]["hooks"][0]
            print(json.dumps({"id": req["id"], "result": {"data": [{"hooks": [{
                "sourcePath": hook_path, "eventName": "stop", "handlerType": stop["type"],
                "command": stop["command"], "matcher": None, "timeoutSec": stop["timeout"],
                "async": False, "enabled": True, "key": "stop-0", "currentHash": "hash-0",
            }]}]}}), flush=True)
        else:
            print(json.dumps({"id": req["id"], "result": {}}), flush=True)
else:
    sessions = home / "sessions"
    sessions.mkdir(exist_ok=True)
    (sessions / "session-0.jsonl").write_text("{}\\n")
    print(json.dumps({"argv": sys.argv[1:]}))
""")
    env.update(LOOPX_OFFICIAL_STOP_HOOK="1", SPLIT_CASE=case)
    started = time.time()
    env["LOOPX_PHASE_DEADLINE_EPOCH"] = str(started + 2)
    if case == "protocol_error":
        with pytest.raises(NativeGoalProtocolError, match="app_server_request_failed:initialize"):
            run_once(env)
        return
    if case == "expired":
        from benchmark.runtime.native_hooks import prepare_official_stop_hook
        home = Path(env["LOOPX_CODEX_HOME"])
        home.mkdir(parents=True, exist_ok=True)
        with pytest.raises(subprocess.TimeoutExpired):
            prepare_official_stop_hook(env, home, deadline=time.monotonic() - 1)
        assert not (tmp_path / "spawned-discovery").exists()
        return
    result = run_once(env)
    if case == "ok":
        assert result["ok"] and result["official_stop_hook_verified"] is True
        assert result["return_code"] == 0 and "hooks.state=" in (
            tmp_path / "logs" / "wakes" / result["turn_id"] / "stdout.jsonl"
        ).read_text()
        return
    assert time.time() - started < 5
    assert result["budget_exhausted"] and result["timed_out"] and result["host_invoked"] is False
    assert (tmp_path / "last-method").read_text() == (
        "initialize" if case == "timeout_initialize" else "hooks/list"
    )


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                    reason="Explicit native Codex binary required; inference uses loopback only")
@pytest.mark.parametrize("enabled", [False, True])
def test_official_stop_hook_continues_one_real_native_session_until_timeout(tmp_path, enabled):
    from benchmark.runtime.native_hooks import OFFICIAL_STOP_HOOK_REASON
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider

    task = tmp_path / "task.md"
    task.write_text("Synthetic task. Propose an implementation.\n")
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update(HOME=str(tmp_path), TMPDIR=str(temporary),
               LOOPX_PROJECT=str(tmp_path), LOOPX_TASK_DOC=str(task),
               LOOPX_CODEX_HOME=str(tmp_path / "codex-home"),
               LOOPX_WAKE_LOG_DIR=str(tmp_path / "logs" / "wakes"),
               LOOPX_EXECUTION_MODE="plain", LOOPX_OFFICIAL_STOP_HOOK=str(int(enabled)),
               LOOPX_CODEX_TURN_TIMEOUT_SEC="10",
               CODEX_BIN=os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"],
               MODEL_NAME="fixture", REASONING_EFFORT="high", OPENAI_API_KEY="synthetic-local")

    def respond(request, number, message):
        if number >= 2:
            # Leave one continued provider request in flight at the runner deadline.
            time.sleep(11)
        return message

    with local_inference_provider(response_builder=respond) as (url, calls, requests):
        receipt = run_once(env | {"OPENAI_BASE_URL": url})
        assert len(list((tmp_path / "logs" / "sessions").rglob("*.jsonl"))) == 1
        assert receipt["timed_out"] is enabled
        assert receipt["ok"] is not enabled
        assert (tmp_path / "codex-home" / "hooks.json").exists() is enabled
        if enabled:
            assert receipt["official_stop_hook_verified"] is True
            assert len(calls) == 2
            assert OFFICIAL_STOP_HOOK_REASON in json.dumps(requests[1]["input"])
        else:
            assert len(calls) == 1
            assert "official_stop_hook" not in receipt


@pytest.mark.skipif(not os.environ.get("LOOPX_NATIVE_CHECKPOINT_BIN"),
                    reason="Explicit native Codex binary required; inference uses loopback only")
@pytest.mark.skipif(os.name != "posix", reason="Exercises POSIX task-controller cancellation")
def test_official_stop_hook_without_clock_retains_task_controller_cancellation(tmp_path):
    from threading import Event

    from benchmark.runtime.native_hooks import OFFICIAL_STOP_HOOK_REASON
    from tests.control_plane.codex_checkpoint_fixture import local_inference_provider

    task = tmp_path / "task.md"
    task.write_text("Synthetic task. Propose an implementation.\n")
    temporary = tmp_path / "temporary"
    temporary.mkdir()
    env = {key: value for key, value in os.environ.items()
           if key in {"PATH", "USER", "LOGNAME", "SHELL", "LANG", "LC_ALL", "SYSTEMROOT"}}
    env.update(HOME=str(tmp_path), TMPDIR=str(temporary),
               PYTHONPATH=str(Path(__file__).resolve().parents[2]),
               LOOPX_PROJECT=str(tmp_path), LOOPX_TASK_DOC=str(task),
               LOOPX_CODEX_HOME=str(tmp_path / "codex-home"),
               LOOPX_WAKE_LOG_DIR=str(tmp_path / "logs" / "wakes"),
               LOOPX_EXECUTION_MODE="plain", LOOPX_OFFICIAL_STOP_HOOK="1",
               CODEX_BIN=os.environ["LOOPX_NATIVE_CHECKPOINT_BIN"],
               MODEL_NAME="fixture", REASONING_EFFORT="high", OPENAI_API_KEY="synthetic-local")
    continued, release = Event(), Event()

    def respond(request, number, message):
        if number == 2:
            continued.set()
            release.wait(20)
        return message

    with local_inference_provider(response_builder=respond) as (url, calls, requests):
        process = subprocess.Popen([sys.executable, "-m", "benchmark.runtime.worker"],
            cwd=tmp_path, env=env | {"OPENAI_BASE_URL": url},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            assert continued.wait(20), "Native Stop hook did not continue the same conversation"
            # The task's final-submission controller may terminate its worker;
            # no independent worker/phase deadline is needed for that boundary.
            process.terminate()
            process.communicate(timeout=10)
        finally:
            release.set()
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=10)
        receipt, = (tmp_path / "logs" / "wakes").glob("*/receipt.json")
        result = json.loads(receipt.read_text())
        assert result["official_stop_hook_verified"] is True
        assert result["timed_out"] is False and result["ok"] is False
        assert len(calls) == 2
        assert OFFICIAL_STOP_HOOK_REASON in json.dumps(requests[1]["input"])
        assert len(list((tmp_path / "logs" / "sessions").rglob("*.jsonl"))) == 1


@pytest.mark.parametrize("option,mode,provider_env", [
    ("on", "heartbeat", {}),
    (1, "heartbeat", {}),
    (None, "heartbeat", {}),
    (True, "plain", {}),
    (True, "native-goal", {}),
    (True, "loopx-goal", {}),
    (True, "turn", {}),
    (True, "heartbeat", {"OPENAI_BASE_URL": ""}),
    (True, "heartbeat", {"OPENAI_API_KEY": ""}),
    (True, "heartbeat", {"CODEX_WIRE_API": "chat"}),
])
def test_harbor_provider_request_admission_rejects_unsupported_activation(
    tmp_path, option, mode, provider_env,
):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    with pytest.raises(ValueError, match="provider_request_admission"):
        BenchmarkCodex(
            logs_dir=tmp_path, model_name="fixture", execution_mode=mode,
            provider_request_admission=option,
            extra_env={"OPENAI_BASE_URL": "https://provider.invalid/v1",
                       "OPENAI_API_KEY": "synthetic-provider-key",
                       "CODEX_WIRE_API": "responses", **provider_env},
            **({"validation_command": ["true"]} if mode == "turn" else {}),
        )


def test_harbor_provider_request_admission_off_ignores_ambient_activation(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex

    monkeypatch.setenv("LOOPX_PROVIDER_REQUEST_ADMISSION", "on")
    kwargs = dict(logs_dir=tmp_path, model_name="fixture",
                  extra_env={"OPENAI_BASE_URL": "", "OPENAI_API_KEY": "", "CODEX_WIRE_API": "chat"})
    implicit = BenchmarkCodex(**kwargs)
    explicit = BenchmarkCodex(**kwargs, provider_request_admission=False)
    assert implicit._worker_env(cwd="/task") == explicit._worker_env(cwd="/task")
    assert "LOOPX_PROVIDER_REQUEST_ADMISSION" not in implicit._worker_env(cwd="/task")


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


@pytest.mark.parametrize("destination", [
    "/opt/loopx-benchmark/checkpoints", "/logs/checkpoints", "relative/checkpoints",
])
def test_fresh_replan_destination_rejected_before_native_setup(tmp_path, monkeypatch, destination):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex, CodexOffline

    def unexpected_setup(*args, **kwargs):
        pytest.fail("Invalid capture configuration reached native setup")

    monkeypatch.setattr(CodexOffline, "__init__", unexpected_setup)
    request = {"destination": destination,
        "components": [{"id": "workspace", "source_path": "/task"}],
        "point_metadata": {}, "trigger": "fresh_replan"}
    with pytest.raises(ValueError, match="fresh_replan destination"):
        BenchmarkCodex(logs_dir=tmp_path, model_name="fixture",
            checkpoint_request_path="/control/request.json",
            checkpoint_request=json.dumps(request).encode())


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
        return {"todo_id": "todo_fixture", "after": {"execution_profile": {field: value, "planning_horizon_goal": True}}}

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
    assert agent._replan_receipt() == {field: value, "planning_horizon_goal": True}
    assert all("--execution-planning-horizon-goal" in args for args in configured)


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
            "replan_after_effective_turns": 6, "stride_strategy": "off", "planning_horizon_goal": True,
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
    assert agent.execution.timeout_seconds is None
    assert "LOOPX_CODEX_TURN_TIMEOUT_SEC" not in agent._worker_env(cwd="/task")
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
    (False, "fresh_replan", None),
    (True, "fresh_replan", "explicit"),
])
@pytest.mark.parametrize("context_delivery", ["off", "on"])
@pytest.mark.parametrize("provider_request_admission", [False, True])
def test_harbor_install_receipt_keeps_capture_independent_from_display(tmp_path, monkeypatch, enabled, trigger, expected, context_delivery, provider_request_admission):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex, CodexOffline
    from benchmark.runtime.checkpoint_wake import CheckpointWake
    wake_root = tmp_path / "wakes"
    wake_root.mkdir()
    monkeypatch.setattr("benchmark.runtime.harbor._WAKE_LOG_DIR", str(wake_root))
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
    provider_env = {"OPENAI_BASE_URL": "https://provider.invalid/v1",
                    "OPENAI_API_KEY": "synthetic-provider-key", "CODEX_WIRE_API": "responses"}
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", turn_envelope=enabled,
                           context_delivery=context_delivery,
                           provider_request_admission=provider_request_admission,
                           extra_env=provider_env,
                           **checkpoint_args)
    with CheckpointWake({"LOOPX_WAKE_LOG_DIR": str(wake_root),
            **({"LOOPX_CHECKPOINT_REQUEST": str(request)} if trigger else {})},
            {"turn_id": "fixture", "mode": "heartbeat", "stage": "execute"}) as wake:
        # Real declaration parser and socket lifecycle, independent of the
        # metadata helper. Omitted trigger is legal after-wake capture.
        assert bool(wake.socket_path) is (trigger == "fresh_replan")
        assert enabled is (expected is not None)
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
    monkeypatch.setattr(agent, "_get_env", provider_env.get)
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
    assert receipt.get("context_delivery") == (context_delivery if context_delivery != "off" else None)
    assert agent._worker_env(cwd="/task")["LOOPX_CONTEXT_DELIVERY"] == context_delivery
    assert receipt.get("provider_request_admission") == ("on" if provider_request_admission else None)
    worker_env = agent._worker_env(cwd="/task")
    assert worker_env.get("LOOPX_PROVIDER_REQUEST_ADMISSION") == ("on" if provider_request_admission else None)
    assert worker_env["OPENAI_BASE_URL"] == provider_env["OPENAI_BASE_URL"]
    assert worker_env["OPENAI_API_KEY"] == provider_env["OPENAI_API_KEY"]
    assert provider_env["OPENAI_API_KEY"] not in json.dumps(receipt)
    if expected:
        assert receipt["turn_envelope"] is True


@pytest.mark.parametrize("stage", ["plan", "execute"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_short_wake_rejects_c1_before_host_preparation(tmp_path, monkeypatch, stage, provider):
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, GOAL_ID, _heartbeat_receipt_count, _write_fixture,
    )

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original_state = state.read_bytes()
    wakes = tmp_path / "wakes"
    home = tmp_path / "codex-home"
    source = Path(__file__).resolve().parents[2]
    env = dict(
        os.environ,
        PYTHONPATH=str(source), LOOPX_PYTHON=sys.executable, LOOPX_USAGE_PING="0",
        LOOPX_CLI=str(Path(sys.executable).parent / "loopx"),
        LOOPX_PROJECT=str(project), LOOPX_REGISTRY=str(registry), LOOPX_RUNTIME_ROOT=str(runtime),
        LOOPX_GOAL_ID=GOAL_ID, LOOPX_AGENT_ID=AGENT_ID, LOOPX_WAKE_LOG_DIR=str(wakes),
        LOOPX_CODEX_HOME=str(home), LOOPX_EXECUTION_MODE="heartbeat",
        LOOPX_SHARED_SKILLS=str(tmp_path / "shared-skills"),
        LOOPX_TASK_STAGE=stage, LOOPX_TURN_ENVELOPE="1", LOOPX_CONTEXT_DELIVERY="off",
        MODEL_NAME="fixture", REASONING_EFFORT="low",
    )

    def unexpected_host_preparation(*args, **kwargs):
        pytest.fail("A C1 response cannot start the Short envelope arm")

    monkeypatch.setattr(worker, "prepare_codex_home", unexpected_host_preparation)
    with pytest.raises(ValueError, match="Short envelope requires"):
        run_once(env)
    receipt_path, = wakes.glob("*/receipt.json")
    receipt = json.loads(receipt_path.read_text())
    assert receipt["ok"] is False and receipt["host_invoked"] is False
    assert receipt["quota_input_contract"] == {
        "expected_schema_version": "loopx_turn_envelope_v0",
        "observed_schema_version": "loopx_turn_envelope_v1",
        "eligible": False,
    }
    assert "turn_envelope_source" not in receipt
    assert _heartbeat_receipt_count(runtime, receipt["turn_id"]) == 0
    assert state.read_bytes() == original_state
    assert not home.exists()


@pytest.mark.parametrize("enabled", [False, True])
def test_wake_receipt_preserves_opt_out(tmp_path, monkeypatch, enabled):
    from benchmark.tests.test_resume_sessions import resume_env
    env = resume_env(tmp_path, monkeypatch)
    env["LOOPX_TURN_ENVELOPE"] = "1" if enabled else "0"
    original_run = subprocess.run
    previews = []

    def legacy_preview(argv, **kwargs):
        if argv[0] != "loopx" or "--dry-run" not in argv:
            return original_run(argv, **kwargs)
        previews.append(argv)
        return subprocess.CompletedProcess(argv, 0, json.dumps({
            "ok": True, "schema_version": "loopx_turn_envelope_v0",
        }), "")

    monkeypatch.setattr(worker.subprocess, "run", legacy_preview)
    receipt = run_once(env)
    assert receipt["ok"], receipt
    assert ("turn_envelope" in receipt) is enabled
    assert ("turn_envelope_source" in receipt) is enabled
    assert ("quota_input_contract" in receipt) is enabled
    assert len(previews) == int(enabled)
    if enabled:
        assert receipt["turn_envelope"] is True
        assert receipt["turn_envelope_source"] == "explicit"
        assert receipt["quota_input_contract"] == {
            "expected_schema_version": "loopx_turn_envelope_v0",
            "observed_schema_version": "loopx_turn_envelope_v0",
            "eligible": True,
        }
        assert "should-run" in previews[0] and "--turn-envelope" in previews[0]
        assert "--turn-instance-id" not in previews[0] and "--decision-output-root" not in previews[0]


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


@pytest.mark.parametrize("requested,actual", [(True, False), (False, True), (None, False), (None, None), (None, 1)])
def test_horizon_setting_must_match_restored_goal_before_work(tmp_path, monkeypatch, requested, actual):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    agent = BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", planning_horizon_goal=requested)
    async def prepared(*args, **kwargs):
        return True
    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)
    async def wrong_setting(*args, **kwargs):
        return {"after": {"execution_profile": {
            "replan_after_effective_turns": 6, "planning_horizon_goal": actual,
        }}}
    monkeypatch.setattr(agent, "_registry_exists", prepared)
    monkeypatch.setattr(agent, "_write_task_document", prepared)
    monkeypatch.setattr(agent, "_loopx", wrong_setting)
    with pytest.raises(RuntimeError, match="planning_horizon_goal readback mismatch"):
        asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Synthetic task", cwd=str(tmp_path)))


@pytest.mark.parametrize("settings", [
    {"planning_horizon_goal": "true"}, {"planning_horizon_goal": 1},
    {"execution_mode": "plain", "planning_horizon_goal": False},
])
def test_horizon_setting_rejects_non_boolean_and_non_loopx_mode(tmp_path, settings):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    with pytest.raises(ValueError, match="planning_horizon_goal"):
        BenchmarkCodex(logs_dir=tmp_path, model_name="fixture", **settings)


@pytest.mark.parametrize("mode", ["plain", "native-goal", "turn", "loopx-goal"])
def test_native_context_opt_in_is_only_a_heartbeat_treatment(mode):
    with pytest.raises(ValueError, match="context_delivery"):
        Execution(mode=mode, context_delivery="on")
    with pytest.raises(ValueError, match="context_delivery"):
        Execution(context_delivery="1")


@pytest.mark.parametrize("declared_mode", [None, "heartbeat"])
def test_context_preflight_defaults_on_and_exports_resolved_heartbeat_mode(tmp_path, monkeypatch, declared_mode):
    from benchmark.tests.test_resume_sessions import resume_env
    from loopx.control_plane.turn_driver.codex_context import context_delivery_activation

    env = resume_env(tmp_path, monkeypatch)
    if declared_mode is None:
        env.pop("LOOPX_EXECUTION_MODE")
    env.pop("LOOPX_CONTEXT_DELIVERY", None)
    monkeypatch.setattr("benchmark.runtime.context_delivery.prepare_context_delivery", lambda *args: None)

    def preflight(current, *_args, **_kwargs):
        activation = context_delivery_activation("on", runtime_profile="generic_cli",
            benchmark_mode=current.get("LOOPX_EXECUTION_MODE"))
        assert activation["scenario"] == "codex_benchmark_heartbeat"
        assert current["LOOPX_CONTEXT_DELIVERY"] == "on"
        return "Current Todo input."

    monkeypatch.setattr("benchmark.runtime.worker.heartbeat_body", preflight)
    result = run_once(env)
    assert result["ok"] and result["session"]["session_id"]
    assert env.get("LOOPX_EXECUTION_MODE") == declared_mode


def test_context_transport_unavailable_preserves_ordinary_worker(tmp_path, monkeypatch):
    from benchmark.tests.test_resume_sessions import resume_env
    env = resume_env(tmp_path, monkeypatch)
    env["LOOPX_CONTEXT_DELIVERY"] = "on"
    def unavailable(env, home):
        env["LOOPX_CODEX_CONTEXT_DIR"] = "not-ready"
        env["CODEX_ROLLOUT_TRACE_ROOT"] = "not-ready"
        raise ValueError("unsupported native host")
    monkeypatch.setattr("benchmark.runtime.context_delivery.prepare_context_delivery", unavailable)
    result = run_once(env)
    assert result["ok"], result
    assert result["context_delivery"] and result["context_transport"]["available"] is False
    assert result["session"]["session_id"]


def test_context_transport_off_never_installs_or_trusts_ambient_args(tmp_path, monkeypatch):
    from benchmark.tests.test_resume_sessions import resume_env
    env = resume_env(tmp_path, monkeypatch)
    env["LOOPX_CODEX_CONTEXT_DIR"] = "ambient-not-authority"
    env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"] = '["--invalid-untrusted-override"]'
    def unexpected(*args):
        pytest.fail("disabled transport must not stage hooks or diagnostics")
    monkeypatch.setattr("benchmark.runtime.context_delivery.prepare_context_delivery", unexpected)
    result = run_once(env)
    assert result["ok"] and "context_delivery" not in result and "context_transport" not in result
    assert not (Path(env["LOOPX_CODEX_HOME"]) / "hooks.json").exists()


@pytest.mark.parametrize("setting,mode", [("off", 0o664), ("on", 0o600)])
@pytest.mark.skipif(os.name != "posix", reason="Native POSIX file creation permissions")
def test_context_native_writer_is_private_without_changing_off_umask(tmp_path, setting, mode):
    from benchmark.runtime import worker

    original = os.umask(0o002)
    try:
        command = [sys.executable, "-c", "from pathlib import Path; Path('native-history').write_bytes(b'fixture')"]
        with worker.child_process(command, env={**os.environ, "LOOPX_PROJECT": str(tmp_path),
                "LOOPX_CONTEXT_DELIVERY": setting}, stdout=subprocess.PIPE, stderr=subprocess.PIPE) as child:
            child.communicate(timeout=10)
            assert child.returncode == 0
        assert (tmp_path / "native-history").stat().st_mode & 0o777 == mode
        assert os.umask(0o002) == 0o002
    finally:
        os.umask(original)


def test_context_selection_is_fixed_by_trial_home(tmp_path):
    args = settings(tmp_path, execution=Execution(mode="heartbeat", context_delivery="on"))
    prepare_codex_home(**args)
    with pytest.raises(ValueError, match="settings changed"):
        prepare_codex_home(**(args | {"execution": Execution(mode="heartbeat", context_delivery="off")}))


@pytest.mark.parametrize("kind", ["public_directory", "symlink_directory", "existing_hooks"])
def test_context_setup_preserves_unsafe_or_unowned_inputs(tmp_path, kind):
    from benchmark.runtime.context_delivery import prepare_context_delivery
    home = tmp_path / "home"
    home.mkdir()
    env = {"LOOPX_CLI": "loopx", "LOOPX_GOAL_ID": "fixture", "LOOPX_AGENT_ID": "agent"}
    if kind == "existing_hooks":
        protected = home / "hooks.json"
        protected.write_text('{"operator": "unchanged"}')
    else:
        protected = home / "loopx-context"
        if kind == "public_directory":
            protected.mkdir(mode=0o755)
            protected.chmod(0o755)
        else:
            target = tmp_path / "outside"
            target.mkdir()
            protected.symlink_to(target, target_is_directory=True)
    before = protected.read_bytes() if protected.is_file() else protected.lstat().st_mode
    with pytest.raises(ValueError, match="private|changed within"):
        prepare_context_delivery(env, home)
    assert (protected.read_bytes() if protected.is_file() else protected.lstat().st_mode) == before
    assert "LOOPX_CODEX_CONTEXT_TRUST_ARGS" not in env


@pytest.mark.parametrize("provider", ["openai", "operator-provider"])
@pytest.mark.parametrize("mutation", [None, "additional_generated_handler", "missing", "extra",
    "duplicate_handler", "duplicate_key", "event", "command", "matcher", "timeout", "async",
    "async_number", "type", "disabled", "missing_enabled", "source", "missing_key", "missing_hash",
    "malformed_key", "malformed_hash", "changed_file"])
def test_context_discovery_keeps_solver_configuration_and_unrelated_hooks(tmp_path, monkeypatch, provider, mutation):
    from contextlib import contextmanager
    from benchmark.runtime.context_delivery import prepare_context_delivery
    from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport

    home = tmp_path / "home"
    home.mkdir()
    config = home / "config.toml"
    original_config = (f'model_provider={json.dumps(provider)}\n'
        + '[features]\nplugins=true\nremote_plugin=true\nplugin_sharing=true\n').encode()
    config.write_bytes(original_config)
    methods, commands, discovered = [], [], []
    if mutation == "additional_generated_handler":
        from loopx.control_plane.turn_driver import codex_context
        original = codex_context.codex_context_configuration
        def extended(*args, **kwargs):
            configuration = original(*args, **kwargs)
            configuration["hooks"]["PostToolUse"][0]["hooks"].append(
                {"type": "command", "command": "fixture-other-handler", "timeout": 15, "async": True})
            return configuration
        monkeypatch.setattr(codex_context, "codex_context_configuration", extended)

    @contextmanager
    def discovery(argv, **kwargs):
        commands.append(argv)
        def request(method, params):
            methods.append(method)
            if method == "initialize":
                return {}
            assert method == "hooks/list", "discovery must not start model work"
            path = home / "hooks.json"
            configured = json.loads(path.read_text())["hooks"]
            events = {"SessionStart": "sessionStart", "PreCompact": "preCompact", "PostCompact": "postCompact",
                      "PostToolUse": "postToolUse", "Interrupt": "interrupt"}
            assert set(configured) == set(events)
            own = [{"sourcePath": str(path.resolve()), "eventName": events[event],
                    "handlerType": handler["type"], "command": handler["command"],
                    "timeoutSec": handler["timeout"], "matcher": group.get("matcher"),
                    "async": handler.get("async", False), "enabled": True}
                   for event, groups in configured.items() for group in groups for handler in group["hooks"]]
            for i, hook in enumerate(own):
                hook.update(key=f"own-{i}", currentHash=f"native-hash-{i}")
            discovered.extend(dict(hook) for hook in own)
            if mutation == "missing":
                own.pop()
            elif mutation == "extra":
                own.append(own[-1] | {"key": "extra"})
            elif mutation == "duplicate_handler":
                own[-1] = own[0] | {"key": own[-1]["key"]}
            elif mutation == "duplicate_key":
                own[-1]["key"] = own[0]["key"]
            elif mutation == "changed_file":
                path.write_text('{}')
            else:
                changes = {"event": ("eventName", "preToolUse"), "command": ("command", "other"),
                    "matcher": ("matcher", "Bash"), "timeout": ("timeoutSec", 999), "async": ("async", True),
                    "async_number": ("async", 0), "type": ("handlerType", "prompt"), "disabled": ("enabled", False),
                    "missing_enabled": ("enabled", None), "source": ("sourcePath", str(tmp_path / "wrong.json")),
                    "missing_key": ("key", None), "missing_hash": ("currentHash", None),
                    "malformed_key": ("key", 1), "malformed_hash": ("currentHash", [])}
                if mutation in changes:
                    field, value = changes[mutation]
                    own[0][field] = value
            # Native discovery has its own ordering, unrelated to config order.
            return {"data": [{"hooks": [*reversed(own), {"sourcePath": str(tmp_path / "other-hooks.json"),
                "key": "unrelated", "currentHash": "never-trust-this"}]}]}
        yield SimpleNamespace(request=request, notify=lambda *args: None)

    monkeypatch.setattr(StdioNativeGoalTransport, "spawn", discovery)
    env = {"LOOPX_CLI": "fixture-loopx", "LOOPX_GOAL_ID": "fixture", "LOOPX_AGENT_ID": "agent",
           "CODEX_BIN": "native-codex", "LOOPX_PROJECT": str(tmp_path)}
    if mutation in {None, "additional_generated_handler"}:
        prepare_context_delivery(env, home)
    else:
        with pytest.raises(ValueError, match="exact trial context hooks|trust identity"):
            prepare_context_delivery(env, home)
        assert "LOOPX_CODEX_CONTEXT_TRUST_ARGS" not in env
        assert config.read_bytes() == original_config
        return
    assert methods == ["initialize", "hooks/list"]
    assignments = {commands[0][i + 1] for i, value in enumerate(commands[0]) if value == "-c"}
    assert assignments == {"features.plugins=false", "features.remote_plugin=false", "features.plugin_sharing=false"}
    trusted = json.loads(env["LOOPX_CODEX_CONTEXT_TRUST_ARGS"])
    assert all(hook["key"] in trusted[1] and hook["currentHash"] in trusted[1] for hook in discovered)
    assert "unrelated" not in trusted[1] and "never-trust-this" not in trusted[1]
    assert all("features.plugins" not in value and "features.remote_plugin" not in value
               and "features.plugin_sharing" not in value for value in trusted)
    assert config.read_bytes() == original_config
    overrides = {trusted[i + 1].split("=", 1)[0]: trusted[i + 1].split("=", 1)[1]
                 for i, value in enumerate(trusted) if value == "-c"}
    assert not any(key.startswith("model_provider") for key in overrides)
    assert overrides["tool_output_token_limit"] == "20000"
    assert json.loads(overrides["shell_environment_policy.set.LOOPX_CONTEXT_PAGE_OUTPUT_BYTES"]) == "64000"
    assert "CODEX_ROLLOUT_TRACE_ROOT" not in env
    assert not (home / "context-traces").exists()


@pytest.mark.parametrize("value", [True, False, "1", "codex_benchmark_heartbeat", "codex_app_heartbeat", "all"])
def test_context_benchmark_setting_rejects_broad_or_other_host_activation(value):
    with pytest.raises(ValueError, match="context_delivery"):
        Execution(context_delivery=value)


def test_h1_opt_in_execution_guardrails_and_codex_settings_parity(tmp_path):
    from benchmark.runtime.codex import codex_settings

    default_exec = Execution()
    assert default_exec.compact_direct_read is False
    assert default_exec.quiet_turn is False
    assert default_exec.epoch_prefetch_json is None

    opt_in_exec = Execution(
        mode="heartbeat",
        context_delivery="on",
        compact_direct_read=True,
        quiet_turn=True,
        epoch_prefetch_json='{"b": 2, "a": 1}',
    )
    assert opt_in_exec.epoch_prefetch_json == '{"a":1,"b":2}'
    base_cfg = codex_settings(execution=default_exec, workspace=tmp_path, model="fixture", effort="low",
                              base_url="", wire_api="responses")
    opt_cfg = codex_settings(execution=opt_in_exec, workspace=tmp_path, model="fixture", effort="low",
                             base_url="", wire_api="responses")
    # WarmStart seals restore-config.toml digest; opt-ins never mutate config.toml.
    assert base_cfg == opt_cfg

    for kwargs in (
        {"mode": "plain", "compact_direct_read": True},
        {"mode": "heartbeat", "context_delivery": "off", "compact_direct_read": True},
        {"mode": "heartbeat", "context_delivery": "off", "quiet_turn": True},
        {"mode": "heartbeat", "context_delivery": "off", "epoch_prefetch_json": '{"enabled":true}'},
        {"mode": "native-goal", "quiet_turn": True},
        {"mode": "loopx-goal", "epoch_prefetch_json": '{"enabled":true}'},
        {"mode": "turn", "validation_command": ("true",), "epoch_prefetch_json": "[1,2]"},
        {"mode": "heartbeat", "compact_direct_read": "1"},
        {"mode": "heartbeat", "quiet_turn": 1},
    ):
        with pytest.raises(ValueError):
            Execution(**kwargs)


def test_prepare_context_delivery_opt_ins_default_off_split_pin_and_hooks_json(tmp_path, monkeypatch):
    from benchmark.runtime.context_delivery import ContextOptInConfigurationError, prepare_context_delivery
    from loopx.control_plane.turn_driver import codex_context
    from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport

    captured_calls = []
    real_config = codex_context.codex_context_configuration

    @contextmanager
    def fake_discovery(argv, **kwargs):
        def request(method, params):
            if method == "initialize":
                return {}
            assert method == "hooks/list"
            path = Path(kwargs["env"]["LOOPX_CODEX_HOME"]) / "hooks.json" if "LOOPX_CODEX_HOME" in kwargs.get("env", {}) else home_ref["path"]
            configured = json.loads(path.read_text())["hooks"]
            return {"data": [{"hooks": [
                {"sourcePath": str(path.resolve()), "eventName": event[0].lower() + event[1:],
                 "handlerType": h["type"], "command": h["command"], "timeoutSec": h["timeout"],
                 "matcher": g.get("matcher"), "async": False, "enabled": True,
                 "key": f"{event}-{i}", "currentHash": f"h-{event}-{i}"}
                for event, groups in configured.items() for g in groups for i, h in enumerate(g["hooks"])
            ]}]}
        yield SimpleNamespace(request=request, notify=lambda *args: None)

    monkeypatch.setattr(StdioNativeGoalTransport, "spawn", fake_discovery)

    # Simulate split-pinned origin/main codex_context_configuration lacking new kwargs:
    # default-off must succeed without passing any new kwargs.
    def legacy_config(directory, *, goal_id, agent_id, cli_argv, scenario, session_root=False,
                      inherit_machine=False, progressive_enabled=True):
        captured_calls.append("legacy")
        return real_config(directory, goal_id=goal_id, agent_id=agent_id, cli_argv=cli_argv,
                           scenario=scenario, session_root=session_root,
                           inherit_machine=inherit_machine, progressive_enabled=progressive_enabled)

    monkeypatch.setattr(codex_context, "codex_context_configuration", legacy_config)
    home_off = tmp_path / "home-off"
    home_off.mkdir()
    home_ref = {"path": home_off / "hooks.json"}
    env_off = {"LOOPX_CLI": "loopx", "LOOPX_GOAL_ID": "g", "LOOPX_AGENT_ID": "a",
               "CODEX_BIN": "codex", "LOOPX_PROJECT": str(tmp_path)}
    prepare_context_delivery(env_off, home_off)
    assert captured_calls == ["legacy"]
    off_hooks = (home_off / "hooks.json").read_text()
    assert "--compact-direct-read" not in off_hooks
    assert "--quiet-turn" not in off_hooks
    assert "--epoch-prefetch-json" not in off_hooks

    # Explicit H1 opt-in fails closed against legacy signature lacking support.
    home_fail = tmp_path / "home-fail"
    home_fail.mkdir()
    with pytest.raises(ContextOptInConfigurationError, match="compact_direct_read"):
        prepare_context_delivery({**env_off, "LOOPX_COMPACT_DIRECT_READ": "1"}, home_fail)

    # Restore real codex_context_configuration -> verifies all 3 opt-in flags reach hooks.json.
    monkeypatch.setattr(codex_context, "codex_context_configuration", real_config)
    home_on = tmp_path / "home-on"
    home_on.mkdir()
    home_ref["path"] = home_on / "hooks.json"
    env_on = {
        **env_off,
        "LOOPX_COMPACT_DIRECT_READ": "1",
        "LOOPX_QUIET_TURN": "1",
        "LOOPX_EPOCH_PREFETCH_JSON": '{"prefetch_command": ["git", "status", "--short"], "enabled": true}',
    }
    prepare_context_delivery(env_on, home_on)
    hooks_json = json.loads((home_on / "hooks.json").read_text())["hooks"]
    start_cmd = hooks_json["SessionStart"][0]["hooks"][0]["command"]
    assert "--compact-direct-read" in start_cmd
    assert "--quiet-turn" in start_cmd
    assert "--epoch-prefetch-json" in start_cmd
    assert '{"enabled":true,"prefetch_command":["git","status","--short"]}' in start_cmd


def test_worker_turn_command_and_run_once_forward_h1_opt_ins_default_off(tmp_path, monkeypatch):
    from benchmark.tests.test_resume_sessions import resume_env
    from loopx.control_plane.turn_driver.codex_stdio import StdioNativeGoalTransport

    env_turn = {"LOOPX_CLI": "loopx", "LOOPX_REGISTRY": "/r", "LOOPX_RUNTIME_ROOT": "/rt",
                "LOOPX_PROJECT": "/p", "LOOPX_GOAL_ID": "g", "LOOPX_AGENT_ID": "a",
                "REASONING_EFFORT": "low", "CODEX_BIN": "codex", "MODEL_NAME": "m"}
    cmd_off = turn_command(env_turn, Execution(mode="turn", validation_command=("true",)), "w1")
    assert "--quiet-turn" not in cmd_off
    assert "--epoch-prefetch-json" not in cmd_off

    cmd_on = turn_command(
        env_turn,
        Execution(mode="turn", validation_command=("true",), quiet_turn=True,
                  epoch_prefetch_json={"enabled": True, "timeout_ms": 500}),
        "w2",
    )
    assert "--quiet-turn" in cmd_on
    assert cmd_on[cmd_on.index("--epoch-prefetch-json") + 1] == '{"enabled":true,"timeout_ms":500}'

    # Real run_once(heartbeat) end-to-end verification (default-off vs opt-in hooks.json + receipt).
    @contextmanager
    def fake_spawn(argv, **kwargs):
        home_p = Path(kwargs["env"]["LOOPX_CODEX_HOME"])
        def req(method, params):
            if method == "initialize":
                return {}
            path = home_p / "hooks.json"
            configured = json.loads(path.read_text())["hooks"]
            return {"data": [{"hooks": [
                {"sourcePath": str(path.resolve()), "eventName": e[0].lower() + e[1:],
                 "handlerType": h["type"], "command": h["command"], "timeoutSec": h["timeout"],
                 "matcher": g.get("matcher"), "async": False, "enabled": True,
                 "key": f"{e}-{i}", "currentHash": f"h-{e}-{i}"}
                for e, groups in configured.items() for g in groups for i, h in enumerate(g["hooks"])
            ]}]}
        yield SimpleNamespace(request=req, notify=lambda *args: None)

    monkeypatch.setattr(StdioNativeGoalTransport, "spawn", fake_spawn)
    monkeypatch.setattr("benchmark.runtime.worker.heartbeat_body", lambda *a, **kw: "Task body.")

    off_tmp = tmp_path / "off"
    off_tmp.mkdir()
    e_off = resume_env(off_tmp, monkeypatch)
    e_off["LOOPX_CONTEXT_DELIVERY"] = "on"
    res_off = run_once(e_off)
    assert res_off["ok"]
    assert "compact_direct_read" not in res_off
    assert "quiet_turn" not in res_off
    assert "epoch_prefetch" not in res_off
    assert "--compact-direct-read" not in (Path(e_off["LOOPX_CODEX_HOME"]) / "hooks.json").read_text()

    on_tmp = tmp_path / "on"
    on_tmp.mkdir()
    e_on = resume_env(on_tmp, monkeypatch)
    e_on.update({
        "LOOPX_CONTEXT_DELIVERY": "on",
        "LOOPX_COMPACT_DIRECT_READ": "1",
        "LOOPX_QUIET_TURN": "1",
        "LOOPX_EPOCH_PREFETCH_JSON": '{"enabled": true}',
    })
    res_on = run_once(e_on)
    assert res_on["ok"]
    assert res_on["compact_direct_read"] is True
    assert res_on["quiet_turn"] is True
    assert res_on["epoch_prefetch"] is True
    assert res_on["epoch_prefetch_configured"] is True
    hooks_text = (Path(e_on["LOOPX_CODEX_HOME"]) / "hooks.json").read_text()
    assert "--compact-direct-read" in hooks_text
    assert "--quiet-turn" in hooks_text
    assert "--epoch-prefetch-json" in hooks_text

    # Explicitly disabled epoch_prefetch_json must NOT set epoch_prefetch=True in receipt.
    dis_tmp = tmp_path / "dis"
    dis_tmp.mkdir()
    e_dis = resume_env(dis_tmp, monkeypatch)
    e_dis.update({"LOOPX_CONTEXT_DELIVERY": "on", "LOOPX_EPOCH_PREFETCH_JSON": '{"enabled": false}'})
    res_dis = run_once(e_dis)
    assert res_dis["ok"]
    assert "epoch_prefetch" not in res_dis
    assert res_dis["epoch_prefetch_configured"] is False


def test_harbor_h1_h2_opt_in_worker_env_and_warm_start_yardstick(tmp_path, monkeypatch):
    pytest.importorskip("harbor")
    from benchmark.runtime.harbor import BenchmarkCodex
    from loopx.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("LOOPX_COMPACT_DIRECT_READ", "1")
    monkeypatch.setenv("LOOPX_QUIET_TURN", "1")
    monkeypatch.setenv("LOOPX_EPOCH_PREFETCH_JSON", '{"enabled":true}')
    implicit = BenchmarkCodex(logs_dir=tmp_path / "imp", model_name="openai/fixture")
    explicit = BenchmarkCodex(logs_dir=tmp_path / "exp", model_name="openai/fixture",
                              compact_direct_read=False, quiet_turn=False,
                              epoch_prefetch_json=None, execution_yardstick_enabled=None)
    assert implicit._worker_env(cwd="/task") == explicit._worker_env(cwd="/task")
    assert implicit._worker_env(cwd="/task")["LOOPX_COMPACT_DIRECT_READ"] == "0"
    assert implicit._worker_env(cwd="/task")["LOOPX_QUIET_TURN"] == "0"
    assert implicit._worker_env(cwd="/task")["LOOPX_EPOCH_PREFETCH_JSON"] == ""
    assert "yardstick_enabled" not in implicit._replan_receipt()

    # Invalid execution_yardstick_enabled rejections.
    for bad in ({"execution_yardstick_enabled": "yes"},
                {"execution_mode": "plain", "execution_yardstick_enabled": True}):
        with pytest.raises(ValueError, match="execution_yardstick_enabled"):
            BenchmarkCodex(logs_dir=tmp_path / "bad", model_name="openai/fixture", **bad)

    # Cold Harbor H2 without active Explore Graph must fail closed instead of accepting inert yardstick=True.
    reg = tmp_path / "reg.json"
    rt = tmp_path / "rt"
    agent = BenchmarkCodex(
        logs_dir=tmp_path / "h2", model_name="openai/fixture",
        task_entry="seeded-todo", compact_direct_read=True, quiet_turn=True,
        epoch_prefetch_json={"enabled": True}, execution_yardstick_enabled=True,
    )
    assert agent._replan_receipt()["yardstick_enabled"] is True
    assert agent._turn_envelope_receipt()["compact_direct_read"] is True
    assert agent._turn_envelope_receipt()["quiet_turn"] is True
    assert agent._turn_envelope_receipt()["epoch_prefetch"] is True

    calls = []
    async def cli(environment, args, **kwargs):
        import contextlib
        import io
        calls.append(args)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--format", "json", "--registry", str(reg), "--runtime-root", str(rt), *args])
        assert code == 0, buf.getvalue()
        return json.loads(buf.getvalue())

    async def no_pending(**kwargs):
        return SimpleNamespace(return_code=1)

    async def noop(*a, **kw):
        return None

    async def reg_exists(*a, **kw):
        return reg.is_file() and reg.stat().st_size > 0

    monkeypatch.setattr(agent, "_loopx", cli)
    monkeypatch.setattr(agent, "_write_task_document", noop)
    monkeypatch.setattr(agent, "_registry_exists", reg_exists)
    with pytest.raises(RuntimeError, match="active Explore evidence graph"):
        asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Task 1", cwd=str(tmp_path)))

    # Enabling Explore Graph on the Goal allows cold/existing-registry and WarmStart H2 readback to pass,
    # verifying real `explore turn-context` exposes `graph_enabled` and `work_yardstick_capture`.
    asyncio.run(cli(None, ["configure-goal", "--goal-id", agent.goal_id, "--explore-graph-enabled", "--execute"]))
    asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Task 1", cwd=str(tmp_path)))
    turn_ctx = asyncio.run(cli(None, ["explore", "turn-context", "--goal-id", agent.goal_id, "--agent-id", agent.agent_id]))
    assert turn_ctx["graph_enabled"] is True
    assert isinstance(turn_ctx["graph"]["work_yardstick_capture"], dict)

    # Reset yardstick externally, then simulate WarmStart / existing registry Phase 2 -> must re-apply!
    asyncio.run(cli(None, ["configure-goal", "--goal-id", agent.goal_id, "--no-execution-yardstick-enabled", "--execute"]))
    asyncio.run(agent._prepare_phase(SimpleNamespace(exec=no_pending), "Task 2", cwd=str(tmp_path)))
    readback = asyncio.run(cli(None, ["configure-goal", "--goal-id", agent.goal_id]))
    assert readback["after"]["execution_profile"]["yardstick_enabled"] is True
    assert any("--execution-yardstick-enabled" in c for c in calls)

    # Explicit execution_yardstick_enabled=False persists cleanly without requiring Explore.
    agent_off = BenchmarkCodex(
        logs_dir=tmp_path / "h2-off", model_name="openai/fixture",
        task_entry="seeded-todo", execution_yardstick_enabled=False,
    )
    monkeypatch.setattr(agent_off, "_loopx", cli)
    monkeypatch.setattr(agent_off, "_write_task_document", noop)
    monkeypatch.setattr(agent_off, "_registry_exists", reg_exists)
    asyncio.run(agent_off._prepare_phase(SimpleNamespace(exec=no_pending), "Task off", cwd=str(tmp_path)))
    off_readback = asyncio.run(cli(None, ["configure-goal", "--goal-id", agent_off.goal_id]))
    assert off_readback["after"]["execution_profile"]["yardstick_enabled"] is False

    # Readback mismatch must fail closed.
    async def stale_cli(environment, args, **kwargs):
        return {"after": {"execution_profile": {
            "replan_after_effective_turns": 6, "planning_horizon_goal": True,
            "stride_strategy": "off", "yardstick_enabled": False,
        }}}
    monkeypatch.setattr(agent, "_loopx", stale_cli)
    with pytest.raises(RuntimeError, match="execution_yardstick_enabled readback mismatch"):
        asyncio.run(agent._verify_phase_configuration(SimpleNamespace(), cwd=str(tmp_path)))


def test_sforge_worker_h2_warm_start_and_cold_explore_yardstick(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker

    monkeypatch.chdir(tmp_path)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(auth_file))
    monkeypatch.setattr("benchmark.runtime.harbor.BenchmarkCodex.install", lambda self, env: asyncio.sleep(0))
    monkeypatch.setattr(SForgeWorker, "_warm_command", lambda *a, **kw: None)

    worker = SForgeWorker(
        SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile="heartbeat-explore",
        cwd=str(tmp_path),
        execution_yardstick_enabled=True,
    )
    reg = tmp_path / "sforge-reg.json"
    rt = tmp_path / "sforge-rt"
    calls = []

    async def cli(environment, args, **kwargs):
        import contextlib
        import io
        from loopx.cli import main
        calls.append(args)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--format", "json", "--registry", str(reg), "--runtime-root", str(rt), *args])
        assert code == 0, buf.getvalue()
        return json.loads(buf.getvalue())

    async def noop(*a, **kw):
        return None

    async def reg_exists(*a, **kw):
        return reg.is_file() and reg.stat().st_size > 0

    class DummyEnv:
        async def exec(self, command, **_kw):
            if command.startswith("cat "):
                return SimpleNamespace(return_code=0, stdout="Solve the task.\n")
            return SimpleNamespace(return_code=1, stdout="")

        async def upload_file(self, *a, **kw):
            return None

    worker.backend = SimpleNamespace(
        exec_run=lambda *a, **kw: SimpleNamespace(exit_code=0, output=""),
        exec_run_with_exit_code=lambda *a, **kw: SimpleNamespace(timed_out=False, exit_code=0, output=""),
        copy_to_container=lambda *a: None,
    )
    logs = tmp_path / "logs"
    logs.mkdir()
    worker.install_stop_hook(worker.backend, None, logs, None)
    worker.runtime.environment = DummyEnv()
    worker.environment = DummyEnv()
    monkeypatch.setattr(worker.runtime, "_loopx", cli)
    monkeypatch.setattr(worker.runtime, "_write_task_document", noop)
    monkeypatch.setattr(worker.runtime, "_registry_exists", reg_exists)
    worker.format_run_cmd(str(tmp_path / "prompt.md"), model="fixture", cwd=str(tmp_path))
    assert (logs / "explore-activation.json").is_file()
    readback = asyncio.run(cli(None, ["configure-goal", "--goal-id", worker.runtime.goal_id]))
    assert readback["after"]["execution_profile"]["yardstick_enabled"] is True

    # Simulate warm_start forwarding --execution-yardstick-enabled on restore.
    asyncio.run(cli(None, ["configure-goal", "--goal-id", worker.runtime.goal_id, "--no-execution-yardstick-enabled", "--execute"]))
    warm = SimpleNamespace(
        goal_id=worker.runtime.goal_id,
        agent_id=worker.runtime.agent_id,
        stage=lambda *a, **kw: asyncio.sleep(0, result=()),
        research_sidecar=False,
        native_child_limit=None,
        deadline_epoch=time.time() + 600,
        verify=lambda _cwd: None,
        source_context_dir=lambda: tmp_path / "src-ctx",
    )
    (tmp_path / "src-ctx").mkdir()
    worker_warm = SForgeWorker(
        SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile="heartbeat-explore",
        cwd=str(tmp_path),
        task_entry="loopx-planned",
        warm_start=warm,
        execution_yardstick_enabled=True,
    )
    worker_warm.backend = worker.backend
    logs_warm = tmp_path / "logs-warm"
    logs_warm.mkdir()
    worker_warm.install_stop_hook(worker_warm.backend, None, logs_warm, None)
    worker_warm.environment = DummyEnv()
    worker_warm.runtime.environment = DummyEnv()
    monkeypatch.setattr(worker_warm.runtime, "_loopx", cli)
    monkeypatch.setattr(worker_warm.runtime, "_write_task_document", noop)
    monkeypatch.setattr(worker_warm.runtime, "_registry_exists", reg_exists)
    worker_warm.format_run_cmd(str(tmp_path / "prompt.md"), model="fixture", cwd=str(tmp_path))
    readback2 = asyncio.run(cli(None, ["configure-goal", "--goal-id", worker_warm.runtime.goal_id]))
    assert readback2["after"]["execution_profile"]["yardstick_enabled"] is True


def test_sforge_worker_default_off_opt_ins_and_base_verification_before_explore(tmp_path, monkeypatch):
    pytest.importorskip("sforge")
    pytest.importorskip("harbor")
    from sforge.harness.config import SForgeConfig
    from benchmark.runtime.sforge import SForgeWorker
    from loopx.cli import main

    monkeypatch.chdir(tmp_path)
    auth_file = tmp_path / "auth.json"
    auth_file.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("CODEX_AUTH_JSON_PATH", str(auth_file))
    monkeypatch.setenv("LOOPX_COMPACT_DIRECT_READ", "1")
    monkeypatch.setenv("LOOPX_QUIET_TURN", "1")
    monkeypatch.setenv("LOOPX_EPOCH_PREFETCH_JSON", '{"enabled":true}')
    monkeypatch.setattr("benchmark.runtime.harbor.BenchmarkCodex.install", lambda self, env: asyncio.sleep(0))
    monkeypatch.setattr(SForgeWorker, "_warm_command", lambda *a, **kw: None)

    class DummyEnv:
        async def exec(self, command, **_kw):
            if command.startswith("cat "):
                return SimpleNamespace(return_code=0, stdout="Solve the task.\n")
            return SimpleNamespace(return_code=1, stdout="")

        async def upload_file(self, *a, **kw):
            return None

    async def noop(*a, **kw):
        return None

    dummy_backend = SimpleNamespace(
        exec_run=lambda *a, **kw: SimpleNamespace(exit_code=0, output=""),
        exec_run_with_exit_code=lambda *a, **kw: SimpleNamespace(timed_out=False, exit_code=0, output=""),
        copy_to_container=lambda *a: None,
    )

    # 1. Default-off SForgeWorker ignores ambient H1 host env vars and never enables H1/H2/Explore by default.
    off_dir = tmp_path / "off"
    off_dir.mkdir()
    reg_off = tmp_path / "off-reg.json"
    rt_off = tmp_path / "off-rt"
    logs_off = tmp_path / "off-logs"
    logs_off.mkdir()
    w_off = SForgeWorker(SForgeConfig(agent_model="fixture", agent_effort="xhigh"), profile="heartbeat-resume", cwd=str(off_dir))
    assert w_off.execution.compact_direct_read is False
    assert w_off.execution.quiet_turn is False
    assert w_off.execution.epoch_prefetch_json is None
    assert w_off.execution_yardstick_enabled is None
    w_off.backend = dummy_backend
    w_off.install_stop_hook(w_off.backend, None, logs_off, None)
    assert w_off.runtime._worker_env(cwd=str(off_dir))["LOOPX_COMPACT_DIRECT_READ"] == "0"
    assert w_off.runtime._worker_env(cwd=str(off_dir))["LOOPX_QUIET_TURN"] == "0"
    assert w_off.runtime._worker_env(cwd=str(off_dir))["LOOPX_EPOCH_PREFETCH_JSON"] == ""
    assert "yardstick_enabled" not in w_off.runtime._replan_receipt()
    turn_receipt = w_off.runtime._turn_envelope_receipt()
    assert "compact_direct_read" not in turn_receipt
    assert "quiet_turn" not in turn_receipt
    assert "epoch_prefetch" not in turn_receipt

    calls_off = []

    async def cli_off(environment, args, **kwargs):
        import contextlib
        import io
        calls_off.append(list(args))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--format", "json", "--registry", str(reg_off), "--runtime-root", str(rt_off), *args])
        assert code == 0, buf.getvalue()
        return json.loads(buf.getvalue())

    monkeypatch.chdir(off_dir)
    w_off.environment = DummyEnv()
    w_off.runtime.environment = DummyEnv()
    monkeypatch.setattr(w_off.runtime, "_loopx", cli_off)
    monkeypatch.setattr(w_off.runtime, "_write_task_document", noop)
    monkeypatch.setattr(w_off.runtime, "_registry_exists", lambda *a, **kw: asyncio.sleep(0, result=reg_off.is_file()))
    w_off.format_run_cmd(str(tmp_path / "prompt.md"), model="fixture", cwd=str(off_dir))
    assert not (logs_off / "explore-activation.json").exists()
    off_state = asyncio.run(cli_off(None, ["configure-goal", "--goal-id", w_off.runtime.goal_id]))
    assert off_state["after"]["execution_profile"].get("yardstick_enabled") in (None, False)
    assert not any("--execution-yardstick-enabled" in call or "--explore-graph-enabled" in call for call in calls_off)

    # 2. Stale base replan configuration on cold heartbeat-explore MUST fail BEFORE activating Explore or writing explore-activation.json.
    stale_dir = tmp_path / "stale"
    stale_dir.mkdir()
    monkeypatch.chdir(stale_dir)
    reg_stale = tmp_path / "stale-reg.json"
    rt_stale = tmp_path / "stale-rt"
    logs_stale = tmp_path / "stale-logs"
    logs_stale.mkdir()
    w_stale = SForgeWorker(
        SForgeConfig(agent_model="fixture", agent_effort="xhigh"),
        profile="heartbeat-explore",
        cwd=str(stale_dir),
        execution_yardstick_enabled=True,
    )
    w_stale.backend = dummy_backend
    w_stale.install_stop_hook(w_stale.backend, None, logs_stale, None)
    w_stale.environment = DummyEnv()
    w_stale.runtime.environment = DummyEnv()

    seen_args = []

    async def stale_base_cli(environment, args, **kwargs):
        import contextlib
        import io
        seen_args.append(list(args))
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--format", "json", "--registry", str(reg_stale), "--runtime-root", str(rt_stale), *args])
        assert code == 0, buf.getvalue()
        payload = json.loads(buf.getvalue())
        if args[:2] == ["configure-goal", "--goal-id"] and "--execute" not in args:
            payload = {
                **payload,
                "after": {
                    **payload.get("after", {}),
                    "execution_profile": {
                        **payload.get("after", {}).get("execution_profile", {}),
                        "replan_after_effective_turns": 99,
                    },
                },
            }
        return payload

    monkeypatch.setattr(w_stale.runtime, "_loopx", stale_base_cli)
    monkeypatch.setattr(w_stale.runtime, "_write_task_document", noop)
    monkeypatch.setattr(w_stale.runtime, "_registry_exists", lambda *a, **kw: asyncio.sleep(0, result=reg_stale.is_file()))
    with pytest.raises(RuntimeError, match="replan cadence readback mismatch"):
        w_stale.format_run_cmd(str(tmp_path / "prompt.md"), model="fixture", cwd=str(stale_dir))
    assert not any("--explore-graph-enabled" in call for call in seen_args)
    assert not (logs_stale / "explore-activation.json").exists()
