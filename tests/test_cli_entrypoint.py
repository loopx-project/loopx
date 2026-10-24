from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_machine_json_keeps_values_capture_and_human_renderer(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copy import deepcopy

    from loopx.cli_runtime import print_payload
    from loopx import usage_ping

    payload = {
        "ok": False,
        "error": "保留空格、换行\n和 \\\" 引号",
        "future": {"unknown": [None, True, 3, {"projection_ref": "$.future"}]},
        "instruction": "Read before work. Continue when evidence is missing.",
    }
    original = deepcopy(payload)
    captured = []
    monkeypatch.setattr(usage_ping, "capture_result", lambda value: captured.append(deepcopy(value)))
    print_payload(payload, "json", lambda _: pytest.fail("JSON cannot use Markdown"))
    stdout = capsys.readouterr().out
    assert json.loads(stdout) == original
    assert stdout.count("\n") == 1  # Newlines in values remain JSON escapes.
    assert "保留空格" in stdout
    assert payload == original
    assert captured == [original]

    print_payload(payload, "markdown", lambda value: "Readable result\n" + value["error"])
    assert capsys.readouterr().out == "Readable result\n" + original["error"] + "\n"
    assert captured == [original, original]


def run_isolated_script(
    script: str, *, environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, **(environment or {}), "PYTHONPATH": str(REPO_ROOT)},
        text=True,
        capture_output=True,
        check=False,
    )


def run_cli_main(
	module: str,
	argv: list[str],
	*,
	forbidden_modules: tuple[str, ...] = (),
	environment: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
	script = f"""
import sys

sys.argv[0] = "loopx"
from {module} import main

try:
    exit_code = main({argv!r})
except SystemExit as exc:
    exit_code = exc.code
for forbidden_module in {forbidden_modules!r}:
    assert forbidden_module not in sys.modules, "unexpected module import: " + forbidden_module
raise SystemExit(exit_code)
"""
	return run_isolated_script(script, environment=environment)


def run_cli_batch(module: str, argv_cases: list[list[str]]) -> list[dict[str, object]]:
	script = f"""
import contextlib
import io
import json
import sys

from {module} import main

sys.argv[0] = "loopx"
results = []
for argv in {argv_cases!r}:
    stdout = io.StringIO()
    stderr = io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        try:
            exit_code = main(argv)
        except SystemExit as exc:
            exit_code = exc.code
    results.append(
        {{
            "returncode": 0 if exit_code is None else exit_code,
            "stdout": stdout.getvalue(),
            "stderr": stderr.getvalue(),
        }}
    )
print(json.dumps(results))
"""
	completed = run_isolated_script(script)

	assert completed.returncode == 0, completed.stderr
	return json.loads(completed.stdout)


def write_command_fixture(tmp_path: Path) -> tuple[Path, Path]:
	project = tmp_path / "project"
	runtime_root = tmp_path / "runtime"
	registry = project / ".loopx" / "registry.json"
	state_file = project / ".codex" / "goals" / "perf-goal" / "ACTIVE_GOAL_STATE.md"
	state_file.parent.mkdir(parents=True)
	state_file.write_text(
		"""---
status: active-read-only
owner_mode: goal
objective: "Exercise selected CLI dispatch."
updated_at: 2026-08-28T00:00:00+00:00
---

# CLI Dispatch Fixture

## Agent Todo

- [ ] [P0] Read one bounded command projection.
  <!-- loopx:todo todo_id=todo_cli_dispatch status=open task_class=advancement_task action_kind=read_projection claimed_by=perf-agent required_capabilities=filesystem_read -->
""",
		encoding="utf-8",
	)
	registry.parent.mkdir(parents=True)
	registry.write_text(
		json.dumps(
			{
				"schema_version": "0.1",
				"updated_at": "2026-08-28T00:00:00+00:00",
				"common_runtime_root": str(runtime_root),
				"goals": [
					{
						"id": "perf-goal",
						"domain": "cli-dispatch-test",
						"status": "active-read-only",
						"repo": str(project),
						"state_file": ".codex/goals/perf-goal/ACTIVE_GOAL_STATE.md",
						"adapter": {
							"kind": "read_only_project_map_v0",
							"status": "connected-read-only",
						},
						"coordination": {
							"registered_agents": ["perf-agent"],
							"agent_model": "peer_v1",
						},
						"authority_sources": [],
					},
				],
			},
			indent=2,
		)
		+ "\n",
		encoding="utf-8",
	)
	return registry, runtime_root


def test_version_flag_skips_full_cli_import() -> None:
    script = """
import contextlib
import io
import sys

from loopx.entrypoint import main

output = io.StringIO()
with contextlib.redirect_stdout(output):
    exit_code = main(["--version"])

assert exit_code == 0
assert output.getvalue().startswith("loopx ")
assert "loopx.cli_runtime" not in sys.modules
assert "loopx.cli" not in sys.modules
"""
    completed = run_isolated_script(script)

    assert completed.returncode == 0, completed.stderr


def test_other_arguments_delegate_to_full_cli() -> None:
    script = """
from loopx.entrypoint import main

raise SystemExit(main(["--format", "json", "version"]))
"""
    completed = run_isolated_script(script)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["schema_version"] == "loopx_version_v0"


def test_top_level_help_skips_full_cli_import() -> None:
	script = """
import contextlib
import io
import sys

from loopx.entrypoint import main

output = io.StringIO()
with contextlib.redirect_stdout(output):
    exit_code = main([])

assert exit_code == 0
assert output.getvalue().startswith("LoopX keeps long-running agent work moving")
assert "loopx.cli" not in sys.modules
"""
	completed = run_isolated_script(script)

	assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
def test_command_reference_does_not_require_default_runtime_route(
	tmp_path: Path, module: str,
) -> None:
	home = tmp_path / "home"
	for root in (home / ".loopx", home / ".codex" / "loopx"):
		(root / "owned-state").mkdir(parents=True)
	cwd = tmp_path / "cwd"
	cwd.mkdir()
	script = f"""
import contextlib
import io
import json
import os

os.environ["HOME"] = {str(home)!r}
os.environ["LOOPX_USAGE_PING"] = "0"
os.chdir({str(cwd)!r})

from {module} import main

output = io.StringIO()
with contextlib.redirect_stdout(output):
    exit_code = main(["commands", "--format", "json"])

assert exit_code == 0
payload = json.loads(output.getvalue())
assert payload["schema_version"] == "loopx_command_reference_v0"
assert payload["groups"]
"""
	completed = run_isolated_script(script)

	assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize(
	("argv", "registration_module", "handler_module"),
	[
		(["commands", "--help"], "loopx.help_surface", "loopx.help_surface"),
		(["doctor", "--help"], "loopx.cli_commands.doctor", "loopx.cli_commands.doctor"),
		(["authority-archive", "--help"], "loopx.cli_commands.authority_archive", "loopx.cli_commands.authority_archive"),
		(["extension", "--help"], "loopx.cli_commands.extension", "loopx.cli_commands.extension"),
		(["slash-commands", "--help"], "loopx.cli_commands.slash_commands", "loopx.cli_commands.slash_commands"),
		(["check", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["status", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["diagnose", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["review-packet", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["quota", "--help"], "loopx.cli_commands.quota_registration", "loopx.cli_commands.quota"),
		(["task-lease", "--help"], "loopx.cli_commands.task_lease", "loopx.cli_commands.task_lease"),
		(["handoff", "--help"], "loopx.cli_commands.todo_continuation", "loopx.cli_commands.todo_continuation"),
		(["handoff-mode", "--help"], "loopx.cli_commands.handoff_mode", "loopx.cli_commands.handoff_mode"),
		(["todo", "--help"], "loopx.cli_commands.todo_registration", "loopx.cli_commands.todo"),
		(["delegation", "--help"], "loopx.cli_commands.delegation", "loopx.cli_commands.delegation"),
		(["agent-context", "--help"], "loopx.cli_commands.agent_context", "loopx.cli_commands.agent_context"),
		(["agent-directory", "--help"], "loopx.cli_commands.agent_directory", "loopx.cli_commands.agent_directory"),
		(["agent-capabilities", "--help"], "loopx.cli_commands.agent_capabilities", "loopx.cli_commands.agent_capabilities"),
		(["goal-portfolio", "--help"], "loopx.cli_commands.summary_all_registration", "loopx.cli_commands.summary_all"),
		(["global-summary", "--help"], "loopx.cli_commands.summary_all_registration", "loopx.cli_commands.summary_all"),
		(["global-gates", "--help"], "loopx.cli_commands.summary_all_registration", "loopx.cli_commands.summary_all"),
		(["global-todos", "--help"], "loopx.cli_commands.summary_all_registration", "loopx.cli_commands.summary_all"),
		(["global-risks", "--help"], "loopx.cli_commands.summary_all_registration", "loopx.cli_commands.summary_all"),
		(["turn", "--help"], "loopx.cli_commands.turn_registration", "loopx.cli_commands.turn"),
		(["turn", "run-once", "--help"], "loopx.cli_commands.turn_registration", "loopx.cli_commands.turn"),
		*[( [command, "--help"], "loopx.cli_commands.project_lifecycle", "loopx.cli_commands.project_lifecycle")
		  for command in ("refresh-state", "checkpoint-context", "read-only-map", "reward", "operator-gate")],
	],
)
def test_common_command_help_loads_only_its_owner(
	argv: list[str],
	registration_module: str,
	handler_module: str,
) -> None:
	script = f"""
import contextlib
import io
import sys

from loopx.entrypoint import main

output = io.StringIO()
try:
    with contextlib.redirect_stdout(output):
        main({argv!r})
except SystemExit as exc:
    assert exc.code == 0
else:
    raise AssertionError("command help must exit through argparse")

assert output.getvalue().startswith("usage:")
assert "loopx.cli" not in sys.modules
assert {registration_module!r} in sys.modules
assert "loopx.cli_commands.benchmark_dispatch" not in sys.modules
assert "loopx.capabilities.content_ops.cli" not in sys.modules
assert "loopx.cli_commands.turn_run_once" not in sys.modules
if {registration_module!r} != {handler_module!r}:
    assert {handler_module!r} not in sys.modules
"""
	completed = run_isolated_script(script)

	assert completed.returncode == 0, completed.stderr


def test_command_submodule_import_does_not_expand_all_cli_commands() -> None:
	script = """
import sys

import loopx.cli_commands.status_registration

assert "loopx.cli_commands.status_registration" in sys.modules
assert "loopx.cli_commands.quota" not in sys.modules
assert "loopx.cli_commands.todo" not in sys.modules
assert "loopx.capabilities.content_ops.cli" not in sys.modules
"""
	completed = run_isolated_script(script)

	assert completed.returncode == 0, completed.stderr


def test_turn_handler_import_defers_optional_workflows() -> None:
	script = """
import sys

import loopx.cli_commands.turn

assert "loopx.capabilities.agent_turn_recall" not in sys.modules
assert "loopx.capabilities.reward_memory" not in sys.modules
assert "loopx.capabilities.issue_fix" not in sys.modules
assert "loopx.todos" not in sys.modules
assert "loopx.control_plane.todos.provider_update" not in sys.modules
assert "loopx.control_plane.todos.provider_create" not in sys.modules
assert "loopx.control_plane.turn_driver.executor" not in sys.modules
assert "loopx.dsh_goal_mode.turn_host_adapter" not in sys.modules
assert "loopx.cli_commands.turn_managed_step" not in sys.modules
assert "loopx.control_plane.quota.slot_accounting" not in sys.modules
assert "loopx.control_plane.quota.void_commit" not in sys.modules
"""
	completed = run_isolated_script(script)
	assert completed.returncode == 0, completed.stderr


def test_turn_facade_preserves_every_export_owner_identity() -> None:
	script = """
from importlib import import_module
import sys
import loopx.control_plane.turn_driver as facade

assert "loopx.control_plane.turn_driver.executor" not in sys.modules
for name in facade.__all__:
    value = getattr(facade, name)
    owner = import_module("." + facade._EXPORTS[name], facade.__name__)
    assert value is getattr(owner, name), name
    assert name in dir(facade)
from loopx import todos, state_refresh
from loopx.control_plane.todos.list_readback import list_goal_todos
from loopx.control_plane.goals.state_resolution import resolve_goal_state
assert todos.list_goal_todos is list_goal_todos
assert state_refresh.resolve_goal_state is resolve_goal_state
"""
	completed = run_isolated_script(script)
	assert completed.returncode == 0, completed.stderr


def test_quota_facade_lazy_exports_preserve_owner_identity() -> None:
	script = """
from importlib import import_module
import sys
import loopx.quota as facade
from loopx.control_plane.quota import ledger_readback

assert "loopx.control_plane.quota.slot_accounting" not in sys.modules
assert "loopx.control_plane.quota.void_commit" not in sys.modules
assert facade.quota_slot_contribution is ledger_readback.quota_slot_contribution
assert facade.net_quota_slot_spend is ledger_readback.net_quota_slot_spend
for name in facade._LAZY_PUBLIC_COMPAT_REEXPORTS:
    assert name in dir(facade)
    value = getattr(facade, name)
    owner = import_module(facade._PUBLIC_COMPAT_REEXPORTS[name])
    assert value is getattr(owner, name), name
    assert getattr(facade, name) is value, name
try:
    facade.nonexistent_quota_export
except AttributeError:
    pass
else:
    raise AssertionError("unknown quota exports must fail")
"""
	completed = run_isolated_script(script)
	assert completed.returncode == 0, completed.stderr


def test_selected_parser_matches_full_help_and_diagnostics() -> None:
	argv_cases = [
		*[[command, flag] for command in
		  ("refresh-state", "checkpoint-context", "read-only-map", "reward", "operator-gate")
		  for flag in ("--help", "--unknown-option")],
		["refresh-state", "--goal-id", "goal", "--delivery-outcome", "invalid"],
		["checkpoint-context", "--goal-id", "goal", "--agent-id", "agent", "--turn-instance-id", "turn",
		 "--todo-id", "todo_work", "--replan-obligation-id", "replan"],
		["commands", "--help"],
		["commands", "--format", "json"],
		["commands", "--format", "markdown"],
		["doctor", "--help"],
		["doctor", "--installation-only", "--agent-type", "codex-app"],
		["doctor", "--agent-type", "unknown-host"],
		["doctor", "--unknown-option"],
		["commands", "--unknown-option"],
		["authority-archive", "--help"],
		["authority-archive", "upgrade", "--help"],
		["authority-archive", "upgrade", "--unknown-option"],
		["authority-archive", "upgrade", "--execute", "--require-current"],
		["extension", "--help"],
		["extension", "doctor", "--help"],
		["extension", "doctor", "--unknown-option"],
		["extension", "install"],
		["slash-commands", "--help"],
		["slash-commands", "--install", "--help"],
		["slash-commands", "--unknown-option"],
		["slash-commands", "--surface", "unknown-host"],
		["check", "--help"],
		["status", "--help"],
		["diagnose", "--help"],
		["review-packet", "--help"],
		["todo", "--help"],
		["quota", "--help"],
		["task-lease", "--help"],
		["task-lease", "renew", "--help"],
		["task-lease", "unknown-action"],
		["task-lease", "renew"],
		["task-lease", "inspect", "--goal-id", "goal", "--todo-id", "todo", "--unknown-option"],
		["task-lease", "renew", "--goal-id", "goal", "--todo-id", "todo", "--expected-ver", "1"],
		["task-lease", "renew", "--goal-id", "goal", "--todo-id", "todo", "--ttl-seconds", "not-a-number"],
		["delegation", "--help"],
		["manager-inbox", "--help"],
		["manager-inbox", "unknown-action"],
		["manager-inbox", "report", "--unknown-option"],
		["manager-inbox", "report", "--request-revision", "not-an-integer"],
		["manager-inbox", "acknowledge", "--decision", "unknown-decision"],
		["turn", "--help"],
		["turn", "run-once", "--help"],
		["status", "--unknown-option"],
		["delegation", "inspect", "--unknown-option"],
		["turn", "run-once", "--exec"],
		["turn", "unknown-command"],
		["todo", "list"],
		["quota", "unknown-command"],
		["--format", "unknown", "status"],
	]
	for command in ("goal-portfolio", "global-summary", "global-gates", "global-todos", "global-risks"):
		argv_cases.extend([[command, "--help"], [command, "--unknown-option"], [command, "--limit", "invalid"]])
	argv_cases.append(["goal-portfolio", "--manager-view", "unknown"])
	for command in ("agent-context", "agent-directory", "agent-capabilities"):
		argv_cases.extend([[command, "--help"], [command], [command, "--unknown-option"],
			[command, "--goal-id", "goal", "--agent-i", "agent"]])
	argv_cases.append(["agent-context", "--goal-id", "goal", "--agent-id", "agent", "--phase", "unknown"])
	selected = run_cli_batch("loopx.entrypoint", argv_cases)
	full = run_cli_batch("loopx.cli", argv_cases)

	assert selected == full


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
@pytest.mark.parametrize("command", ["agent-context", "agent-directory", "agent-capabilities"])
def test_agent_commands_read_registered_peer_without_changing_goal(
	tmp_path: Path, module: str, command: str,
) -> None:
	from tests.control_plane.canonical_authority_fixture import (
		assert_todo_read_unchanged, initialize_canonical_source_fixture,
	)
	from loopx.control_plane.todos.list_readback import list_goal_todos

	registry, runtime_root = write_command_fixture(tmp_path)
	state_path = registry.parent.parent / ".codex/goals/perf-goal/ACTIVE_GOAL_STATE.md"
	initialize_canonical_source_fixture(runtime_root, "perf-goal", state_path=state_path)
	before = list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime_root), goal_id="perf-goal")
	registry_before = registry.read_bytes()
	argv = ["--registry", str(registry), "--runtime-root", str(runtime_root),
		"--format", "json", command, "--goal-id", "perf-goal", "--agent-id", "perf-agent"]
	if command == "agent-context":
		argv += ["--phase", "before_plan"]
	completed = run_cli_main(module, argv,
		forbidden_modules=("loopx.cli",) if module == "loopx.entrypoint" else ())
	assert completed.returncode == 0, completed.stderr
	payload = json.loads(completed.stdout)
	assert payload["ok"] is True
	if command == "agent-context":
		assert payload["read_only"] is True
		assert payload["agent_context"] is None
	elif command == "agent-directory":
		assert [row["agent_id"] for row in payload["rows"]] == ["perf-agent"]
		assert payload["scope"]["caller_membership"] == "registered_agent"
	else:
		assert payload["agent_capabilities"]["agent_id"] == "perf-agent"
		assert payload["agent_capabilities"]["goal_id"] == "perf-goal"
		preview = run_cli_main(module, [*argv, "--unavailable-capability", "network"])
		assert preview.returncode == 0, preview.stderr
		assert json.loads(preview.stdout)["agent_capabilities"]["proposed"]["unavailable"] == ["network"]
		assert not (runtime_root / "agent-runtime-capabilities").exists()
		written = run_cli_main(module, [*argv, "--unavailable-capability", "network", "--execute"])
		assert written.returncode == 0, written.stderr
		assert json.loads(written.stdout)["agent_capabilities"]["written"] is True
		other = "loopx.cli" if module == "loopx.entrypoint" else "loopx.entrypoint"
		readback = run_cli_main(other, argv)
		assert readback.returncode == 0, readback.stderr
		assert json.loads(readback.stdout)["agent_capabilities"]["unavailable"] == ["network"]
	assert registry.read_bytes() == registry_before
	assert_todo_read_unchanged(
		list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime_root), goal_id="perf-goal"), before,
	)


@pytest.mark.parametrize("arguments", [
	["acknowledge", "--request-id", "a" * 64, "--decision", "adopt", "--reason", "Checked the source"],
	["report", "--request-id", "a" * 64, "--reply-text", "The evidence check passed."],
])
def test_selected_manager_inbox_refusals_match_without_loading_full_cli(tmp_path, arguments):
	registry, runtime_root = write_command_fixture(tmp_path)
	argv = ["--registry", str(registry), "--runtime-root", str(runtime_root), "--format", "json",
		"manager-inbox", *arguments, "--goal-id", "perf-goal", "--agent-id", "perf-agent"]
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 1
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr
	assert json.loads(selected.stdout)["ok"] is False


@pytest.mark.parametrize("arguments", [
	["handoff", "inspect"],
	["handoff", "prepare", "--goal-id", "perf-goal"],
	["handoff", "inspect", "--format", "digest"],
	["handoff-mode", "show", "--goal-id", "perf-goal", "--mode", "hard_lease"],
	["handoff-mode", "migrate", "--goal-id", "perf-goal"],
])
def test_selected_handoff_refusals_match_without_loading_full_cli(tmp_path, arguments):
	registry, runtime_root = write_command_fixture(tmp_path)
	argv = ["--registry", str(registry), "--runtime-root", str(runtime_root), "--format", "json", *arguments]
	before = registry.read_bytes()
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 1
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr
	assert registry.read_bytes() == before


def test_selected_handoff_parser_diagnostics_match_full_cli():
	cases = [
		["handoff"], ["handoff", "unknown"], ["handoff-mode"], ["handoff-mode", "unknown"],
		["handoff", "inspect", "--format", "yaml"],
		["handoff-mode", "set", "--mode", "invalid"],
		["handoff-mode", "show", "--goal-id", "demo", "--unrecognized"],
	]
	assert run_cli_batch("loopx.entrypoint", cases) == run_cli_batch("loopx.cli", cases)


@pytest.mark.parametrize("input_format", ["json", "markdown"])
@pytest.mark.parametrize("format_name", ["json", "markdown"])
def test_selected_handoff_restore_preserves_content_without_authority(tmp_path, input_format, format_name):
	text = "保持完整要求。\n\nValidate the original Todo.\n"
	source = tmp_path / "handoff.txt"
	source.write_text(json.dumps({"ok": True, "handoff_text": text}) if input_format == "json" else text, encoding="utf-8")
	registry, runtime = tmp_path / "absent-registry.json", tmp_path / "absent-runtime"
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "handoff", "restore",
		"--input", str(source), "--input-format", input_format, "--format", format_name]
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 0
	assert selected.stdout == full.stdout
	assert (json.loads(selected.stdout)["handoff_text"] if format_name == "json" else selected.stdout) == text
	assert not registry.exists() and not runtime.exists()


@pytest.mark.parametrize("arguments", [
	["refresh-state", "--dry-run"],
	["checkpoint-context", "--agent-id", "perf-agent", "--turn-instance-id", "missing", "--todo-id", "todo_missing"],
	["read-only-map"],
	["reward", "--decision", "continue_route", "--reward", "positive", "--reason-summary", "Checked evidence", "--dry-run"],
	["operator-gate", "--decision", "approve", "--reason-summary", "Checked evidence", "--dry-run"],
])
def test_selected_lifecycle_refusals_match_without_full_cli(tmp_path, arguments):
	registry, runtime = write_command_fixture(tmp_path)
	before = registry.read_bytes()
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
		*arguments, "--goal-id", "missing-goal"]
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 1
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr
	assert json.loads(selected.stdout)["ok"] is False
	assert registry.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_selected_refresh_preview_keeps_canonical_state_without_full_cli(tmp_path, monkeypatch, provider):
	from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime
	from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted

	isolate_sqlite_runtime(tmp_path, monkeypatch)
	registry, runtime = write_command_fixture(tmp_path)
	state = registry.parent.parent / ".codex/goals/perf-goal/ACTIVE_GOAL_STATE.md"
	initialize_canonical_source_fixture(runtime, "perf-goal", state_path=state, provider=provider)
	before = state.read_bytes(), read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="perf-goal")
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
		"refresh-state", "--goal-id", "perf-goal", "--agent-id", "perf-agent",
		"--next-action", "Continue bounded fixture work.", "--no-global-sync", "--dry-run"]
	for module in ("loopx.entrypoint", "loopx.cli"):
		result = run_cli_main(module, argv, forbidden_modules=("loopx.cli",) if module == "loopx.entrypoint" else ())
		assert result.returncode == 0, result.stderr
		payload = json.loads(result.stdout)
		assert payload["ok"] and payload["dry_run"]
		assert (state.read_bytes(), read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="perf-goal")) == before


def test_selected_manager_result_matches_full_replay_without_loading_full_cli(tmp_path):
	from test_collaboration_goal_instance import _create_source_registry, _manager_request
	from loopx.control_plane.collaboration.inbox import _root, acknowledge

	registry = _create_source_registry(tmp_path)
	_, _, receipt = _manager_request(tmp_path, registry)
	rid = receipt["request_id"]
	acknowledge(tmp_path, "delivery", "builder", rid, "adopt", "Checked the source",
		registry=registry, caller_goal_ref=receipt["goal_ref"])
	argv = ["--registry", str(registry), "--runtime-root", str(tmp_path), "--format", "json",
		"manager-inbox", "report", "--goal-id", "delivery", "--agent-id", "builder",
		"--request-id", rid, "--reply-text", "The independent evidence check passed."]
	selected = run_cli_main("loopx.entrypoint", argv,
		forbidden_modules=("loopx.cli", "loopx.quota"))
	path = _root(tmp_path) / "replies" / rid / "conclusion.json"
	assert selected.returncode == 0, selected.stderr
	committed = path.read_bytes()
	full = run_cli_main("loopx.cli", argv)
	assert full.returncode == 0, full.stderr
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr
	assert json.loads(selected.stdout)["delivered"] is False
	assert path.read_bytes() == committed


@pytest.mark.parametrize("action", ["acquire", "renew", "transfer", "release", "inspect"])
def test_selected_task_lease_dispatch_preserves_owner_arguments(
	tmp_path: Path, action: str,
) -> None:
	"""Thin routing must preserve the existing lease handler, not reimplement it."""
	registry, runtime_root = write_command_fixture(tmp_path)
	argv = ["--registry", str(registry), "--runtime-root", str(runtime_root),
		"--format", "markdown", "task-lease", action, "--format", "json",
		"--goal-id", "perf-goal", "--todo-id", "todo_cli_dispatch"]
	expected = {"registry_path": str(registry), "runtime_root": str(runtime_root),
		"goal_id": "perf-goal", "todo_id": "todo_cli_dispatch"}
	if action != "inspect":
		argv += ["--owner", "perf-agent", "--idempotency-key", "original-execution", "--expected-version", "11"]
		expected.update(owner="perf-agent", idempotency_key="original-execution", expected_version=11)
	if action in {"acquire", "renew", "transfer"}:
		argv += ["--ttl-seconds", "20"]
		expected["ttl_seconds"] = 20
	if action == "acquire":
		argv += ["--write-scope", "loopx/**", "--write-worktree", str(tmp_path / "worktree")]
		expected.update(write_scopes=["loopx/**"], write_worktree=str(tmp_path / "worktree"))
	if action == "transfer":
		argv += ["--new-owner", "next-agent", "--new-idempotency-key", "next-execution", "--transfer-claim"]
		expected.update(new_owner="next-agent", new_idempotency_key="next-execution", transfer_claim=True)
	function = "execute_native_task_lease_acquire" if action == "acquire" else f"{action}_task_lease"
	results = []
	for module in ("loopx.entrypoint", "loopx.cli"):
		script = f"""
import contextlib
import io
import json
import sys
from pathlib import Path
import loopx.cli_commands.task_lease as owner
from {module} import main

observed = []
def invoke(**kwargs):
    observed.append({{key: str(value) if isinstance(value, Path) else value for key, value in kwargs.items()}})
    return {{"ok": True, "action": {action!r}, "schema_version": "task_lease_v0"}}
owner.{function} = invoke
output = io.StringIO()
with contextlib.redirect_stdout(output):
    code = main({argv!r})
assert code == 0
assert observed == [{expected!r}]
assert json.loads(output.getvalue()) == {{"ok": True, "action": {action!r}, "schema_version": "task_lease_v0"}}
if {module!r} == "loopx.entrypoint":
    assert "loopx.cli" not in sys.modules
    assert "loopx.cli_commands.benchmark_dispatch" not in sys.modules
    assert "loopx.capabilities.content_ops.cli" not in sys.modules
print(output.getvalue(), end="")
"""
		completed = run_isolated_script(script)
		assert completed.returncode == 0, completed.stderr
		results.append(completed.stdout)
	assert results[0] == results[1]


@pytest.mark.parametrize("arguments", [
	["renew"],
	["renew", "--owner", "perf-agent"],
	["inspect", "--owner", "perf-agent"],
	["release", "--owner", "perf-agent", "--idempotency-key", "original", "--ttl-seconds", "20"],
	["renew", "--write-worktree", "worktree"],
	["renew", "--transfer-claim"],
])
def test_selected_task_lease_rejections_match_full_cli(tmp_path: Path, arguments: list[str]) -> None:
	registry, runtime_root = write_command_fixture(tmp_path)
	argv = ["--registry", str(registry), "--runtime-root", str(runtime_root),
		"--format", "json", "task-lease", *arguments,
		"--goal-id", "perf-goal", "--todo-id", "todo_cli_dispatch"]
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 1
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	assert json.loads(selected.stdout)["ok"] is False
	assert not runtime_root.exists()


@pytest.mark.parametrize("command", ["authority-archive", "extension", "slash-commands"])
def test_selected_admin_preview_matches_full_cli_without_writes(
	tmp_path: Path, command: str,
) -> None:
	parent_environment = os.environ.copy()
	registry, runtime_root = write_command_fixture(tmp_path)
	runtime_root.mkdir()
	host_home = tmp_path / "host-home"
	host_home.mkdir()
	# Only the CLI subprocesses use this home. Existing worker threads must not
	# discover it through a temporary change to the pytest process environment.
	cli_environment = {
		"HOME": str(host_home),
		"USERPROFILE": str(host_home),
		"CODEX_HOME": str(host_home / ".codex"),
		"LOOPX_RUNTIME_ROOT": str(runtime_root),
		"LOOPX_REGISTRY": str(registry),
		"LOOPX_USAGE_PING": "0",
	}
	argv = ["--registry", str(registry), "--runtime-root", str(runtime_root),
		"--format", "json", command]
	if command == "authority-archive":
		argv += ["upgrade", "--require-current"]
	elif command == "extension":
		argv += ["doctor", "--all-enabled", "--state-file", str(runtime_root / "extensions.json")]
	else:
		argv += ["--install", "--dry-run", "--surface", "codex", "--codex-home", str(host_home / ".codex")]

	before = {str(path.relative_to(tmp_path)): path.read_bytes()
		for path in tmp_path.rglob("*") if path.is_file()}
	selected = run_cli_main(
		"loopx.entrypoint", argv, forbidden_modules=("loopx.cli",),
		environment=cli_environment,
	)
	full = run_cli_main("loopx.cli", argv, environment=cli_environment)

	assert selected.returncode == full.returncode == 0, (selected.stderr, full.stderr)
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	assert {key: os.environ.get(key) for key in cli_environment} == {
		key: parent_environment.get(key) for key in cli_environment
	}
	assert {str(path.relative_to(tmp_path)): path.read_bytes()
		for path in tmp_path.rglob("*") if path.is_file()} == before
	assert not list(host_home.iterdir())
	assert not list(runtime_root.iterdir())


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
@pytest.mark.parametrize("healthy", [True, False])
def test_doctor_dispatch_preserves_owner_flags_and_failure(
    module: str, healthy: bool, tmp_path: Path,
) -> None:
    registry = tmp_path / "registry.json"
    runtime_root = tmp_path / "runtime"
    script = f"""
import contextlib
import io
import json
from pathlib import Path

import loopx.cli_commands.doctor as owner
from {module} import main

observed = []
def collect(**kwargs):
    observed.append(kwargs)
    return {{"ok": {healthy!r}, "scope": "installation_only"}}
owner.collect_doctor = collect
output = io.StringIO()
with contextlib.redirect_stdout(output):
    code = main(["--registry", {str(registry)!r}, "--runtime-root", {str(runtime_root)!r},
                 "--format", "markdown", "doctor", "--format", "json",
                 "--deep", "--installation-only"])
assert code == {0 if healthy else 1}
assert observed == [{{"deep": True, "agent_type": None, "installation_only": True,
                    "registry_path": Path({str(registry)!r}),
                    "runtime_root_override": {str(runtime_root)!r}}}]
assert json.loads(output.getvalue()) == {{"ok": {healthy!r}, "scope": "installation_only"}}
"""
    completed = run_isolated_script(script)
    assert completed.returncode == 0, completed.stderr


def test_selected_todo_execution_matches_full_cli(tmp_path: Path) -> None:
	from tests.control_plane.canonical_authority_fixture import (
		assert_todo_read_unchanged, initialize_canonical_source_fixture,
	)

	registry, runtime_root = write_command_fixture(tmp_path)
	initialize_canonical_source_fixture(runtime_root, "perf-goal",
		state_path=registry.parent.parent / ".codex/goals/perf-goal/ACTIVE_GOAL_STATE.md")
	argv = [
		"--registry",
		str(registry),
		"--runtime-root",
		str(runtime_root),
		"--format",
		"json",
		"todo",
		"list",
		"--goal-id",
		"perf-goal",
		"--agent-id",
		"perf-agent",
	]

	selected = run_cli_main(
		"loopx.entrypoint",
		argv,
		forbidden_modules=("loopx.cli",),
	)
	full = run_cli_main("loopx.cli", argv)

	assert selected.returncode == full.returncode == 0
	assert_todo_read_unchanged(json.loads(selected.stdout), json.loads(full.stdout))
	assert selected.stderr == full.stderr == ""


def test_console_script_uses_lightweight_entrypoint() -> None:
	project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

	assert project["project"]["scripts"]["loopx"] == "loopx.entrypoint:main"


def test_release_launchers_use_lightweight_entrypoint() -> None:
	posix_launcher = (REPO_ROOT / "scripts" / "loopx").read_text(encoding="utf-8")
	windows_entry = (REPO_ROOT / "scripts" / "loopx_entry.py").read_text(encoding="utf-8")

	assert "LOOPX_MANAGED_RELEASE_LAUNCHER_V1" in posix_launcher
	assert "LOOPX_MANAGED_RELEASE_LAUNCHER_V1" in windows_entry
	assert '"loopx.entrypoint"' in posix_launcher
	assert '"loopx.entrypoint"' in windows_entry
	assert 'else "loopx.cli"' in posix_launcher
	assert 'else "loopx.cli"' in windows_entry


def test_console_entrypoint_selects_only_receipt_bound_native_scheduler_followup(
    monkeypatch,
) -> None:
    from loopx import entrypoint

    monkeypatch.setattr(
        shutil, "which", lambda value: "/fixture/node" if value == "node" else None
    )
    args = [
        "--format",
        "json",
        "--runtime-root",
        "runtime",
        "quota",
        "scheduler-ack-current",
        "--goal-id",
        "goal",
        "--agent-id",
        "agent",
        "--scheduler-host-facts-chunk",
        "facts",
        "--turn-instance-id",
        "turn",
        "--execute",
    ]

    native = entrypoint._native_scheduler_followup_argv(args)

    assert native is not None
    assert native[:3] == [
        "/fixture/node",
        "--no-warnings",
        "--experimental-strip-types",
    ]
    assert native[-len(args) :] == args
    assert (
        entrypoint._native_scheduler_followup_argv(
            [
                value
                for value in args
                if value != "facts" and value != "--scheduler-host-facts-chunk"
            ]
        )
        is None
    )
    assert (
        entrypoint._native_scheduler_followup_argv(
            [value for value in args if value not in {"turn", "--turn-instance-id"}]
        )
        is None
    )


@pytest.mark.parametrize("observation", [
    {"available": True, "rrule": "FREQ=MINUTELY;INTERVAL=3"},
    {"available": False},
])
def test_native_scheduler_reads_host_instead_of_the_applied_claim(monkeypatch, observation) -> None:
    from loopx import entrypoint, upgrade

    monkeypatch.setattr(shutil, "which", lambda value: "/fixture/node")
    seen = []
    monkeypatch.setattr(upgrade, "resolve_codex_app_automation_rrule", lambda **kw: seen.append(kw) or observation)
    args = ["quota", "scheduler-ack-current", "--goal-id", "goal", "--agent-id", "agent",
            "--scheduler-host-facts-chunk", "facts", "--turn-instance-id", "turn",
            "--host-match-observed", "--applied-rrule", "FREQ=MINUTELY;INTERVAL=30",
            "--app-automation-current-rrule", "FREQ=MINUTELY;INTERVAL=30"]
    native = entrypoint._native_scheduler_followup_argv(args)
    assert seen == [{"goal_id": "goal", "agent_id": "agent"}]
    assert native[-2:] == ["--app-automation-current-rrule", observation.get("rrule", "")]


def test_explicit_main_argv_remains_an_in_process_compatibility_call(
    monkeypatch,
) -> None:
    from loopx import entrypoint

    seen: list[list[str]] = []
    monkeypatch.setattr(
        entrypoint,
        "_native_scheduler_followup_argv",
        lambda _args: (_ for _ in ()).throw(AssertionError("must not process-replace")),
    )
    monkeypatch.setattr(
        "loopx.cli_runtime.main",
        lambda args: seen.append(list(args)) or 0,
    )

    assert entrypoint.main(["--format", "json", "version"]) == 0
    assert seen == [["--format", "json", "version"]]


def test_windows_native_scheduler_followup_preserves_child_exit_status(
    monkeypatch, capfd,
) -> None:
    from loopx import entrypoint

    native_argv = [
        sys.executable, "-c",
        "import sys; print('native refusal'); sys.exit(73)",
    ]
    with monkeypatch.context() as patch:
        patch.setattr(entrypoint.os, "name", "nt")
        patch.setattr(sys, "argv", ["loopx", "quota", "scheduler-ack-current"])
        patch.setattr(entrypoint, "_native_scheduler_followup_argv", lambda _args: native_argv)
        result = entrypoint.main()

    assert result == 73
    assert capfd.readouterr().out.strip() == "native refusal"


def test_console_native_scheduler_followup_fails_closed_without_node(
    monkeypatch, capsys
) -> None:
    from loopx import entrypoint

    monkeypatch.setattr(shutil, "which", lambda _value: None)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "loopx",
            "--runtime-root",
            "runtime",
            "quota",
            "scheduler-ack-current",
            "--scheduler-host-facts-chunk",
            "facts",
            "--turn-instance-id",
            "turn",
        ],
    )

    assert entrypoint.main() == 2
    assert "requires Node.js 22.22.3 or newer" in capsys.readouterr().err


@pytest.mark.parametrize("command", [
	"goal-portfolio", "global-summary", "global-gates", "global-todos", "global-risks",
])
def test_global_read_preserves_empty_coverage_without_loading_full_cli(tmp_path, command):
	registry, runtime = tmp_path / "registry.json", tmp_path / "runtime"
	runtime.mkdir()
	registry.write_text(json.dumps({"goals": [], "common_runtime_root": str(runtime)}))
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", command]
	before = registry.read_bytes()
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == 0, selected.stderr
	assert full.returncode == 0, full.stderr
	payload, original = json.loads(selected.stdout), json.loads(full.stdout)
	assert payload["ok"] is True
	for key in ("schema_version", "request", "summary", "coverage", "scope", "boundary", "omissions"):
		assert payload.get(key) == original.get(key)
	if command == "goal-portfolio":
		assert payload["coverage"]["discovered"] == 0
		assert payload["coverage"]["complete"] is False
		assert payload["goals"] == []
	assert registry.read_bytes() == before
