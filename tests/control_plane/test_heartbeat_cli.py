"""Heartbeat CLI routing preserves the public grammar, context and recovery."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from test_cli_entrypoint import run_cli_batch, run_cli_main, run_isolated_script, write_command_fixture


PARSER_CASES = [
    (["heartbeat-prompt", "--help"], 0),
    (["--format=json", "heartbeat-prompt", "--help"], 0),
    (["heartbeat-prompt"], 2),
    (["heartbeat-prompt", "--goal-id"], 2),
    (["heartbeat-prompt", "--goal", "fixture"], 2),
    (["heartbeat-prompt", "--goal-id=fixture", "--unknown"], 2),
    (["heartbeat-prompt", "--goal-id=fixture", "--full", "--thin"], 2),
    (["heartbeat-prompt", "--goal-id=fixture", "--runtime-profile=unknown"], 2),
    (["heartbeat-prompt", "--goal-id=fixture", "--context-delivery=invalid"], 2),
    (["heartbeat-prompt", "--goal-id=fixture", "--codex-context-dir=a", "--codex-context-root=b"], 2),
    (["heartbeat-prompt", "--goal-id=fixture", "--format=invalid"], 2),
    (["--format=invalid", "heartbeat-prompt", "--help"], 2),
]


def test_heartbeat_parser_and_full_diagnostics_match():
    cases = [args for args, _ in PARSER_CASES]
    full = run_cli_batch("loopx.cli", cases)
    entry = run_cli_batch("loopx.entrypoint", cases)
    assert [row["returncode"] for row in full] == [code for _, code in PARSER_CASES]
    assert entry == full


def heartbeat_args(registry: Path, runtime: Path) -> list[str]:
    return ["--registry", str(registry), "--runtime-root", str(runtime),
            "heartbeat-prompt", "--goal-id=perf-goal", "--agent-id=perf-agent"]


@pytest.mark.parametrize("global_format,local_format", [("json", None), ("markdown", None), ("markdown", "json")])
def test_heartbeat_output_and_format_precedence(tmp_path, global_format, local_format):
    registry, runtime = write_command_fixture(tmp_path)
    base = ["--format", global_format, *heartbeat_args(registry, runtime), "--codex-app"]
    if local_format:
        base += ["--format", local_format]
    cases = [base + mode for mode in ([], ["--thin"], ["--brief"], ["--compact"], ["--full"], ["--bootstrap"])]
    full = run_cli_batch("loopx.cli", cases)
    entry = run_cli_batch("loopx.entrypoint", cases)
    assert entry == full
    assert all(row["returncode"] == 0 for row in entry)
    if (local_format or global_format) == "json":
        for row in entry:
            payload = json.loads(row["stdout"])
            assert payload["ok"] is True
            assert payload["agent_id"] == "perf-agent"
            assert "--registry" in payload["task_body"]
            assert str(registry) in payload["task_body"]
            assert "--runtime-root" in payload["task_body"]


def test_heartbeat_semantic_errors_keep_recovery_and_exit_status(tmp_path):
    registry, runtime = write_command_fixture(tmp_path)
    base = ["--format=json", *heartbeat_args(registry, runtime)]
    cases = [base + options for options in (
        ["--codex-app", "--trae_app"],
        ["--codex-app", "--runtime-profile=codex_app_heartbeat"],
        ["--runtime-profile=codex_app_heartbeat", "--host-surface=codex_app"],
        ["--agent-id=unregistered"],
        ["--goal-id=missing"],
        ["--context-delivery=on", "--trae_app"],
        ["--turn-envelope"],
        ["--decision-handoff-socket=missing"],
    )]
    full = run_cli_batch("loopx.cli", cases)
    entry = run_cli_batch("loopx.entrypoint", cases)
    assert entry == full
    assert all(row["returncode"] == 1 for row in entry)
    for row in entry:
        payload = json.loads(row["stdout"])
        assert payload["ok"] is False
        assert payload.get("error") or payload.get("task_body")


def test_heartbeat_explicit_missing_registry_retains_fallback_contract(tmp_path):
    registry, runtime = write_command_fixture(tmp_path)
    # Explicitly missing registry and an absent Goal have different recovery;
    # compare both without supplying a real user's global registry.
    cases = [["--format=json", *heartbeat_args(registry.with_name("absent.json"), runtime)],
             ["--format=json", *heartbeat_args(registry, runtime), "--goal-id=absent"]]
    assert run_cli_batch("loopx.entrypoint", cases) == run_cli_batch("loopx.cli", cases)
    assert not registry.with_name("absent.json").exists()


def test_heartbeat_read_only_prompt_without_node(tmp_path):
    registry, runtime = write_command_fixture(tmp_path)
    args = ["--format=json", *heartbeat_args(registry, runtime), "--codex-app", "--context-delivery=off"]
    env = {"PATH": str(tmp_path / "no-executables")}
    full = run_cli_main("loopx.cli", args, environment=env)
    entry = run_cli_main("loopx.entrypoint", args, environment=env)
    assert (entry.returncode, entry.stdout, entry.stderr) == (full.returncode, full.stdout, full.stderr)
    assert entry.returncode == 0, entry.stderr
    assert json.loads(entry.stdout)["ok"] is True


@pytest.mark.parametrize("mode", ["help", "thin", "bootstrap"])
def test_cold_heartbeat_avoids_unrelated_modules_and_external_io(tmp_path, mode):
    registry, runtime = write_command_fixture(tmp_path)
    args = (["heartbeat-prompt", "--help"] if mode == "help" else
            ["--format=json", *heartbeat_args(registry, runtime), "--codex-app",
             "--context-delivery=off", "--" + mode])
    script = f"""
import contextlib
import io
import json
import sys

events = []
def audit(event, args):
    if event in {{"subprocess.Popen", "os.system", "os.posix_spawn"}} or event.startswith("socket."):
        events.append(event)
        raise AssertionError("unexpected external IO: " + event)
sys.addaudithook(audit)

from loopx.entrypoint import main
output = io.StringIO()
with contextlib.redirect_stdout(output):
    try:
        code = main({args!r})
    except SystemExit as exc:
        code = exc.code
assert code == 0, output.getvalue()
assert not events, events
if {mode!r} != "help":
    assert json.loads(output.getvalue())["ok"] is True
for module in ("loopx.cli", "loopx.cli_commands.support_control", "loopx.chat_server",
               "loopx.dashboard_launcher", "loopx.status_server"):
    assert module not in sys.modules, "unexpected module import: " + module
if {mode!r} == "help":
    assert "loopx.cli_commands.support_control_heartbeat" not in sys.modules
"""
    result = run_isolated_script(script, environment={"LOOPX_USAGE_PING": "0"})
    assert result.returncode == 0, result.stderr
