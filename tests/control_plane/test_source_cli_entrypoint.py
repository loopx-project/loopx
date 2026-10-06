"""Fresh source-module callers reuse the existing console/TS ownership boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
UNRELATED_OWNERS = (
    "loopx.cli_commands.benchmark_dispatch",
    "loopx.capabilities.content_ops.cli",
)


def run_entry(
    entry: str,
    argv: list[str],
    *,
    setup: str = "",
    assertions: str = "",
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    # runpy executes the actual module branch in a fresh interpreter. Give both
    # public entries the same program name so argparse output is byte-comparable.
    invocation = (
        'runpy.run_module("loopx.cli", run_name="__main__", alter_sys=False)'
        if entry == "module"
        else "__import__('loopx.entrypoint', fromlist=['main']).main()"
    )
    script = f"""
import runpy
import sys
sys.argv = ["loopx", *{argv!r}]
{setup}
try:
    code = {invocation}
except SystemExit as exc:
    code = exc.code
{assertions}
raise SystemExit(code)
"""
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "LOOPX_USAGE_PING": "0", **(env or {})},
        text=True,
        capture_output=True,
        check=False,
        timeout=45,
    )


@pytest.mark.parametrize(
    "argv",
    [["--version"], [], ["--help"], ["check", "--help"], ["status", "--help"],
     ["diagnose", "--help"], ["review-packet", "--help"], ["quota", "--help"],
     ["todo", "--help"], ["delegation", "--help"], ["turn", "--help"],
     ["turn", "run-once", "--help"],
     ["--registry=fixture", "--format=json", "quota", "--help"]],
)
def test_source_help_and_version_do_not_load_unrelated_owners(argv: list[str]) -> None:
    assertions = f"""
assert "loopx.cli" not in sys.modules
for owner in {UNRELATED_OWNERS!r}:
    assert owner not in sys.modules, owner
"""
    if argv == ["--version"]:
        assertions += '\nassert "loopx.cli_runtime" not in sys.modules\n'
    module = run_entry("module", argv, assertions=assertions)
    console = run_entry("console", argv, assertions=assertions)
    assert module.returncode == console.returncode == 0, module.stderr + console.stderr
    assert (module.stdout, module.stderr) == (console.stdout, console.stderr)


@pytest.mark.parametrize(
    "argv",
    [["version", "--format", "json"], ["status", "--unknown-option"],
     ["todo", "list"], ["quota", "unknown-command"], ["--format", "unknown", "status"],
     ["delegation", "inspect", "--unknown-option"], ["turn", "run-once", "--exec"],
     ["turn", "unknown-command"],
     ["--reg", "fixture", "status"]],
)
def test_source_full_fallback_keeps_canonical_results_and_diagnostics(argv: list[str]) -> None:
    module = run_entry("module", argv, assertions='assert "loopx.cli" in sys.modules')
    console = run_entry("console", argv)
    assert (module.returncode, module.stdout, module.stderr) == (
        console.returncode, console.stdout, console.stderr
    )


def test_imported_full_parser_remains_a_compatibility_api() -> None:
    result = run_entry(
        "module", ["--version"],
        assertions="""
from loopx.cli import build_parser, main
args = build_parser().parse_args(["todo", "list", "--goal-id", "fixture"])
assert args.command == "todo" and args.todo_command == "list"
assert callable(main)
""",
    )
    assert result.returncode == 0, result.stderr


def native_argv() -> list[str]:
    return [
        "--format", "json", "--runtime-root", "fixture-runtime", "quota",
        "scheduler-ack-current", "--goal-id", "fixture-goal", "--agent-id", "fixture-agent",
        "--scheduler-host-facts-chunk", "fixture-facts", "--turn-instance-id", "fixture-turn",
        "--execute",
    ]


@pytest.mark.parametrize("entry", ["module", "console"])
def test_receipt_bound_scheduler_dispatch_reuses_native_owner(entry: str) -> None:
    setup = """
import os
import shutil
shutil.which = lambda name: "/fixture/node" if name == "node" else None
seen = []
def replace_process(executable, argv):
    seen.append((executable, argv))
    raise SystemExit(73)
os.execv = replace_process
import subprocess
subprocess.call = lambda argv: replace_process(argv[0], argv)
"""
    result = run_entry(entry, native_argv(), setup=setup, assertions="""
assert len(seen) == 1
assert seen[0][0] == "/fixture/node"
assert seen[0][1][1:3] == ["--no-warnings", "--experimental-strip-types"]
from pathlib import Path
assert Path(seen[0][1][3]).parts[-2:] == ("scheduler", "heartbeat_followup_cli.ts")
assert seen[0][1][4:] == sys.argv[1:]
assert "loopx.cli_runtime" not in sys.modules
assert "loopx.cli" not in sys.modules
""")
    assert result.returncode == 73, result.stdout + result.stderr


@pytest.mark.parametrize("entry", ["module", "console"])
def test_native_scheduler_missing_node_fails_closed_without_python_fallback(entry: str) -> None:
    result = run_entry(
        entry, native_argv(), setup="import shutil; shutil.which = lambda name: None",
        assertions='assert "loopx.cli_runtime" not in sys.modules',
    )
    assert result.returncode == 2
    assert result.stdout == ""
    assert "requires Node.js 22.22.3 or newer" in result.stderr


@pytest.mark.parametrize("entry", ["module", "console"])
@pytest.mark.parametrize("argv", [native_argv(), [
    "todo", "update", "--goal-id", "fixture-goal", "--todo-id", "todo_fixture",
    "--agent-id", "fixture-agent", "--status", "done",
], [
    "turn", "run-once", "--goal-id", "fixture-goal", "--agent-id", "fixture-agent",
    "--host", "generic-cli", "--project", "fixture-project", "--execute",
]])
def test_outer_controller_write_guard_runs_before_native_or_provider_effects(
    tmp_path: Path, entry: str, argv: list[str],
) -> None:
    runtime = tmp_path / "runtime"
    args = [*argv]
    if "--runtime-root" in args:
        args[args.index("--runtime-root") + 1] = str(runtime)
    else:
        args = ["--runtime-root", str(runtime), *args]
    result = run_entry(
        entry, args,
        setup="""
import os
def replace_process(*args):
    raise AssertionError("outer-controlled writes cannot process-replace their guard")
os.execv = replace_process
""",
        assertions='assert "loopx.cli" not in sys.modules',
        env={"LOOPX_KUNLUNCODE_OUTER_CONTROLLER": "1",
             "LOOPX_KUNLUNCODE_OUTER_GOAL_ID": "fixture-goal",
             "LOOPX_KUNLUNCODE_OUTER_AGENT_ID": "fixture-agent"},
    )
    assert result.returncode == 2, result.stdout + result.stderr
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["error_code"] == "kunluncode_outer_controller_write_owned"
    assert payload["goal_id"] == "fixture-goal"
    assert not runtime.exists()


@pytest.mark.parametrize("entry", ["module", "console"])
def test_outer_controller_scope_preserves_reads_other_goals_and_recovery(
    tmp_path: Path, entry: str,
) -> None:
    from examples.control_plane.quota_plan_fixtures import SCOPED_AGENT_ID, write_cli_fixture
    from loopx.control_plane.scheduler.state import (
        APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY as state_key,
        load_scheduler_state,
    )

    registry, runtime, _project = write_cli_fixture(tmp_path / "fixture", scoped_agents=True)
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    env = {"CODEX_HOME": str(tmp_path / "codex-home"), "CODEX_THREAD_ID": "",
           "LOOPX_KUNLUNCODE_OUTER_CONTROLLER": "1",
           "LOOPX_KUNLUNCODE_OUTER_GOAL_ID": "needs-operator",
           "LOOPX_KUNLUNCODE_OUTER_AGENT_ID": SCOPED_AGENT_ID}

    def selected_ack(goal: str) -> list[str]:
        decision = run_entry(entry, [*prefix, "quota", "should-run", "--goal-id", goal,
                                    "--agent-id", SCOPED_AGENT_ID, "--codex-app",
                                    "--turn-instance-id", "scope-turn"], env=env)
        # An independent fixture without a Todo can return the ordinary health
        # failure (1); it must still pass the outer write guard and commit its
        # typed observation, rather than return that guard's rejection (2).
        assert decision.returncode in (0, 1), decision.stdout + decision.stderr
        payload = json.loads(decision.stdout)
        assert payload["mode"] == "should-run"
        assert payload["heartbeat_receipt"]["status"] == "committed"
        return [*prefix, *payload["scheduler_hint"]["app_automation"]
                ["ack_hint"]["cli_args"]]

    # A read for the controlled Goal remains allowed, but changing the actor
    # does not allow its bound write to escape Goal-scoped ownership.
    protected_ack = selected_ack("needs-operator")
    for agent in (SCOPED_AGENT_ID, "different-agent"):
        args = list(protected_ack)
        args[args.index("--agent-id") + 1] = agent
        blocked = run_entry(entry, args, env=env)
        assert blocked.returncode == 2, blocked.stdout + blocked.stderr
        assert json.loads(blocked.stderr)["error_code"] == "kunluncode_outer_controller_write_owned"
    assert load_scheduler_state(runtime, goal_id="needs-operator", agent_id=SCOPED_AGENT_ID,
                                state_key=state_key) is None

    # Add an independent Goal after the protected scope was exercised. The
    # original controller declaration must not silently become registry-wide.
    registered = json.loads(registry.read_text())
    future = dict(next(row for row in registered["goals"] if row["id"] == "full-speed"))
    future["id"] = "future-independent"
    future["state_file"] = ".codex/goals/future-independent/ACTIVE_GOAL_STATE.md"
    future_state = registry.parent.parent / future["state_file"]
    future_state.parent.mkdir(parents=True)
    future_state.write_text("---\nstatus: active\n---\n\n# Future independent Goal\n")
    registered["goals"].append(future)
    registry.write_text(json.dumps(registered))
    for goal in ("full-speed", "future-independent"):
        accepted = run_entry(entry, selected_ack(goal), env=env)
        assert accepted.returncode == 0, accepted.stdout + accepted.stderr
        payload = json.loads(accepted.stdout)
        assert payload["ok"] is True and payload["scheduler_state_mutated"] is True, payload
        assert load_scheduler_state(runtime, goal_id=goal, agent_id=SCOPED_AGENT_ID,
                                    state_key=state_key) is not None

    # Release by the existing controller owner restores real receipt-bound
    # progress, not merely a different blocker or a mocked success payload.
    recovered = run_entry(entry, protected_ack,
                          env={**env, "LOOPX_KUNLUNCODE_OUTER_CONTROLLER": "0"})
    assert recovered.returncode == 0, recovered.stdout + recovered.stderr
    assert json.loads(recovered.stdout)["scheduler_state_mutated"] is True
    assert load_scheduler_state(runtime, goal_id="needs-operator", agent_id=SCOPED_AGENT_ID,
                                state_key=state_key) is not None


def test_source_module_observes_usage_once_and_preserves_failure_result() -> None:
    setup = """
from loopx import usage_ping
calls = []
usage_ping.begin = lambda command: calls.append(("begin", command)) or ("generation", 0)
def finish(ticket, command, code, error):
    calls.append(("finish", command, code, type(error).__name__ if error else None))
usage_ping.finish = finish
"""
    result = run_entry("module", ["status", "--unknown-option"], setup=setup, assertions="""
assert calls == [("begin", "status"), ("finish", "status", 2, "SystemExit")], calls
""")
    assert result.returncode == 2, result.stderr
    assert "unrecognized arguments" in result.stderr


def test_source_module_usage_opt_out_has_no_machine_state_or_sender(tmp_path: Path) -> None:
    state = tmp_path / "machine"
    result = run_entry("module", ["version", "--format", "json"], setup=f"""
from pathlib import Path
from loopx import usage_ping
usage_ping.select_default_runtime_root = lambda: Path({str(state)!r})
sent = []
def unexpected_send(*args):
    sent.append(args)
usage_ping._detach = unexpected_send
""", assertions="assert sent == [], sent")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert result.stderr == ""
    assert not state.exists()


def test_source_first_usage_disclosure_keeps_json_pure_and_does_not_send(tmp_path: Path) -> None:
    state = tmp_path / "machine"
    result = run_entry("module", ["version", "--format", "json"], setup=f"""
import os
from pathlib import Path
from loopx import usage_ping
for key in ("CI", "DO_NOT_TRACK", "LOOPX_USAGE_PING", "LOOPX_USAGE_POLICY"):
    os.environ.pop(key, None)
os.environ["LOOPX_USAGE_PING_ENDPOINT"] = "http://127.0.0.1:1/v1/ping"
usage_ping.select_default_runtime_root = lambda: Path({str(state)!r})
sent = []
usage_ping._detach = lambda *args: sent.append(args)
""", assertions="assert sent == [], sent")
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True
    assert "random installation ID" in result.stderr
    stored = json.loads((state / "usage-ping.json").read_text())
    # Version 6 discloses the installation profile and overlapping runtime
    # clocks. Pin the public contract independently of the implementation.
    assert stored["notice"]["version"] == 6
    assert "last_attempt_day" not in stored and "counters" not in stored


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_source_read_uses_canonical_authority_not_missing_markdown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from loopx.todos import add_goal_todo

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
                            text="Read canonical work", claimed_by="agent-a", agent_id="agent-a")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    state.unlink()
    argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "todo", "list", "--goal-id", "goal-a", "--agent-id", "agent-a"]
    module = run_entry("module", argv, assertions='assert "loopx.cli" not in sys.modules')
    console = run_entry("console", argv)
    assert module.returncode == console.returncode == 0, module.stderr + console.stderr
    assert json.loads(module.stdout) == json.loads(console.stdout)
    result = json.loads(module.stdout)
    assert result["authority_read"]["source_authority"] == f"{provider}_v0"
    assert result["authority_read"]["legacy_fallback_used"] is False
    assert result["authority_read"]["provider_revision"] == before["provider_revision"]
    assert any(row["todo_id"] == created["todo_id"] for row in result["todos"])
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
    assert not state.exists()
