from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
@pytest.mark.parametrize("command", ["auto-research", "multi-agent"])
def test_retired_demo_commands_reject_execution_without_writing_state(
    tmp_path: Path, module: str, command: str,
) -> None:
    registry, runtime = write_command_fixture(tmp_path)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = run_cli_main(module, [
        "--registry", str(registry), "--runtime-root", str(runtime),
        command, "start" if command == "auto-research" else "launch", "--execute",
    ], environment={"LOOPX_USAGE_PING": "0"})
    assert result.returncode == 2, result.stderr
    assert "invalid choice" in result.stderr and command in result.stderr
    assert not result.stdout
    assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_command_reference_omits_retired_demo_launchers() -> None:
    result = run_cli_main("loopx.entrypoint", ["commands", "--format", "json"])
    assert result.returncode == 0, result.stderr
    reference = json.loads(result.stdout)
    assert reference["groups"]
    assert "loopx auto-research" not in result.stdout
    assert "loopx multi-agent" not in result.stdout


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


@pytest.mark.parametrize("command", ["registry", "registry-boundary"])
@pytest.mark.parametrize("format_name", ["json", "markdown"])
@pytest.mark.parametrize("missing", [False, True])
def test_registry_inspection_keeps_full_cli_result_and_read_only_fixture(
	tmp_path: Path, command: str, format_name: str, missing: bool,
) -> None:
	registry, runtime = write_command_fixture(tmp_path)
	selected = registry.with_name("missing.json") if missing else registry
	argv = ["--registry", str(selected), "--runtime-root", str(runtime),
		"--format", format_name, command]
	before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
	legacy = run_cli_main("loopx.cli", argv)
	current = run_cli_main("loopx.entrypoint", argv, forbidden_modules=(
		"loopx.cli", "loopx.cli_commands.support_control", "loopx.chat_server",
		"loopx.dashboard_launcher", "loopx.cli_commands.benchmark_dispatch",
	))
	assert current.returncode == legacy.returncode == (1 if missing else 0), current.stderr
	assert current.stdout == legacy.stdout
	assert current.stderr == legacy.stderr == ""
	if format_name == "json":
		payload = json.loads(current.stdout)
		assert payload["ok"] is not missing
		if missing:
			assert payload["error"] == "registry file does not exist"
		elif command == "registry":
			assert payload["goal_count"] == 1
			assert payload["goals"][0]["id"] == "perf-goal"
			assert payload["goals"][0]["state_file_exists"]
		else:
			assert payload["schema_version"] == "loopx_registry_boundary_v0"
			assert payload["should_be_gitignored"] and not payload["github_push_allowed"]
	assert {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


@pytest.mark.parametrize("argv", [
	["registry", "--format", "json"], ["registry", "--unknown"],
	["registry-boundary", "--path"], ["registry-boundary", "--require-not-tr"],
	["--format", "invalid", "registry"], ["--format", "", "registry"],
	["--reg", "unused", "registry"], ["--registry"],
])
def test_registry_inspection_reuses_canonical_rejection(argv: list[str]) -> None:
	legacy = run_cli_main("loopx.cli", argv)
	current = run_cli_main("loopx.entrypoint", argv)
	assert current.returncode == legacy.returncode == 2
	assert current.stdout == legacy.stdout == ""
	assert current.stderr == legacy.stderr


@pytest.mark.parametrize("ok", [False, True])
def test_full_registry_inspection_keeps_support_patch_points(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, ok: bool,
) -> None:
	from loopx.cli import main
	from loopx.cli_commands import support_control
	registry, runtime = write_command_fixture(tmp_path)
	calls = []
	payload = {"ok": ok, "future": {"unknown": [None, True]}}
	monkeypatch.setattr(support_control, "inspect_registry", lambda path: calls.append(path) or payload)
	printed = []
	monkeypatch.setattr(support_control, "render_registry_markdown", lambda value: "preserved renderer")
	# The full CLI supplies its print callback; preserve that boundary as well.
	monkeypatch.setattr("loopx.cli.print_payload", lambda value, fmt, render: printed.append(
		(value, fmt, render(value))))
	assert main(["--registry", str(registry), "--runtime-root", str(runtime), "registry"]) == (0 if ok else 1)
	assert calls == [registry]
	assert printed == [(payload, "markdown", "preserved renderer")]


@pytest.mark.parametrize("git, require_not_tracked, require_gitignored, push_allowed, should_ignore, ok", [
	({"inside_worktree": True, "probe_status": "git_unavailable"}, True, False, False, True, False),
	({"worktree_discovery_unknown": True, "probe_status": "git_unavailable"}, True, False, False, True, False),
	({"inside_worktree": False, "probe_status": "git_unavailable"}, True, False, False, True, True),
	({"inside_worktree": True, "probe_status": "git_unavailable"}, True, False, True, False, True),
	({"inside_worktree": True, "ignored": False, "tracked": False}, False, True, False, True, False),
	({"inside_worktree": True, "ignored": True, "tracked": False}, False, True, False, True, True),
	({"inside_worktree": True, "ignored": False, "tracked": True}, False, True, False, True, False),
	({"inside_worktree": True, "ignored": False, "tracked": False}, False, True, True, False, True),
])
def test_registry_inspection_preserves_git_requirement_precedence(
	tmp_path: Path, monkeypatch: pytest.MonkeyPatch, git: dict[str, object],
	require_not_tracked: bool, require_gitignored: bool,
	push_allowed: bool, should_ignore: bool, ok: bool,
) -> None:
	import argparse
	from loopx.cli_commands import support_control
	registry = tmp_path / "registry.json"
	selected = tmp_path / "selected.json"
	calls = []
	owner_rejected = bool(git.get("tracked") and not push_allowed)
	payload = {"ok": not owner_rejected, "git": git, "github_push_allowed": push_allowed,
		"should_be_gitignored": should_ignore, "future": {"retained": None},
		"risks": ["registry_tracked_but_not_push_allowed"] if owner_rejected else []}
	monkeypatch.setattr(support_control, "inspect_registry_boundary", lambda path: calls.append(path) or payload)
	printed = []
	args = argparse.Namespace(command="registry-boundary", path=str(selected), format="json",
		require_not_tracked=require_not_tracked, require_gitignored=require_gitignored)
	result = support_control.handle_support_control_command(args, registry_path=registry,
		registry_was_supplied=True, print_payload=lambda value, fmt, render: printed.append((value, fmt)),
		output_format=lambda args: args.format)
	assert result == (0 if ok else 1)
	assert calls == [selected]
	assert len(printed) == 1 and printed[0][1] == "json"
	assert printed[0][0]["ok"] is ok
	assert printed[0][0]["future"] == {"retained": None}
	if not ok and require_not_tracked:
		assert printed[0][0]["error"] == "Could not determine whether the registry is tracked by Git."
	if not ok and require_gitignored and not owner_rejected:
		assert "registry_should_be_gitignored" in printed[0][0]["risks"]


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


@pytest.mark.parametrize("arguments", [
    ["version"],
    ["--format", "json", "version"],
    ["version", "--format", "json"],
    ["--format=markdown", "version", "--format=json"],
    ["--format=json", "version", "--format=markdown"],
    ["--registry", "missing-registry.json", "--runtime-root", "missing-runtime",
     "--cli-bin", "fixture-loopx", "version", "--format", "json"],
    ["--format", "markdown", "--format", "json", "version"],
])
def test_version_command_uses_its_selected_owner(arguments: list[str]) -> None:
    completed = run_cli_main(
        "loopx.entrypoint", arguments,
        forbidden_modules=("loopx.cli", "loopx.chat_manager", "loopx.control_plane.effect_runtime"),
        environment={"LOOPX_USAGE_PING": "0"},
    )
    assert completed.returncode == 0, completed.stderr
    from loopx import __version__

    if arguments[-1] == "--format=markdown" or arguments == ["version"]:
        assert completed.stdout == f"loopx {__version__}\n\n"
    else:
        assert json.loads(completed.stdout) == {
            "ok": True, "schema_version": "loopx_version_v0",
            "name": "loopx", "version": __version__,
        }


def test_version_command_retains_full_cli_diagnostics() -> None:
    arguments = [
        ["version", "--help"], ["version", "-h"],
        ["version", "--unknown-option"], ["version", "--format", "yaml"],
        ["version", "--format"], ["version", "--format="],
        ["--format", "yaml", "version"], ["--format", "", "version"],
        ["--format", "version"], ["--format", "json", "version", "extra"],
        ["--cli-bin", "", "version"], ["--registry", "version"],
        ["--for", "json", "version"], ["version", "--for", "json"],
        ["version", "--registry", "missing.json"], ["version", "--version"],
        ["--version", "version"], ["--", "version"],
    ]
    assert run_cli_batch("loopx.entrypoint", arguments) == run_cli_batch("loopx.cli", arguments)


def test_version_command_retains_explicit_full_cli_patch_points() -> None:
    script = """
from loopx import cli
from loopx.entrypoint import main
from loopx.cli_commands.version import register_version_command

assert cli.register_version_command is register_version_command
seen = []
def patched(args, **kwargs):
    seen.append(args.command)
    return 37
cli.handle_version_command = patched
assert main(["version"]) == 37
assert seen == ["version"]
"""
    completed = run_isolated_script(script, environment={"LOOPX_USAGE_PING": "0"})
    assert completed.returncode == 0, completed.stderr


def test_version_command_retains_explicit_package_export_patch_points() -> None:
    script = """
from loopx import cli_commands
from loopx.cli_commands import handle_version_command, register_version_command
from loopx.entrypoint import main

assert cli_commands.handle_version_command is handle_version_command
seen = []
def register(*args, **kwargs):
    seen.append("register")
    return register_version_command(*args, **kwargs)
def patched(args, **kwargs):
    seen.append(args.command)
    return 37
cli_commands.register_version_command = register
cli_commands.handle_version_command = patched
assert main(["version"]) == 37
assert seen == ["register", "version"]
"""
    completed = run_isolated_script(script, environment={"LOOPX_USAGE_PING": "0"})
    assert completed.returncode == 0, completed.stderr


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
		(["registry", "--help"], "loopx.cli_runtime", "loopx.registry"),
		(["registry-boundary", "--help"], "loopx.cli_runtime", "loopx.registry"),
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
		(["goal-acceptance", "--help"], "loopx.cli_commands.goal_acceptance", "loopx.cli_commands.goal_acceptance"),
		(["host-mode-plan", "--help"], "loopx.cli_commands.host_mode_plan", "loopx.cli_commands.host_mode_plan"),
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


def test_steward_endpoint_vocabulary_is_read_without_the_chat_layer() -> None:
	script = """
import sys
from loopx.capabilities.steward_executor import (
    normalize_steward_executor_machine_defaults,
    steward_executor_endpoints,
)

expected_kinds = {"codex": "individual", "dsh": "managed", "antigravity": "individual"}
assert steward_executor_endpoints() == frozenset(expected_kinds)
for endpoint in expected_kinds:
    normalized = normalize_steward_executor_machine_defaults(
        {"schema_version": "steward_executor_machine_defaults_v0", "executor_endpoint": endpoint}
    )
    assert normalized["executor_endpoint"] == endpoint
try:
    normalize_steward_executor_machine_defaults(
        {"schema_version": "steward_executor_machine_defaults_v0", "executor_endpoint": "unknown"}
    )
except ValueError:
    pass
else:
    raise AssertionError("an unknown executor endpoint must be rejected")
for name in ("loopx.chat_manager", "loopx.chat_agent", "loopx.chat_store"):
    assert name not in sys.modules, name

import loopx.chat_manager as manager
from loopx.capabilities.steward_executor import machine_defaults
for name in (
    "MANAGER_ENDPOINT_INDIVIDUAL", "MANAGER_ENDPOINT_MANAGED", "MANAGER_ENDPOINT_KINDS",
    "MANAGER_EXECUTOR_KIND_INDIVIDUAL", "MANAGER_EXECUTOR_KIND_MANAGED",
):
    assert getattr(manager, name) is getattr(machine_defaults, name), name
assert manager.MANAGER_ENDPOINT_KINDS == expected_kinds
"""
	completed = run_isolated_script(script)
	assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("command", [
	["heartbeat-prompt", "--thin", "--codex-app", "--goal-id", "perf-goal", "--agent-id", "perf-agent",
		"--agent-scope", "fixture", "--available-capability", "shell"],
	["quota", "should-run", "--goal-id", "perf-goal", "--agent-id", "perf-agent",
		"--available-capability", "shell", "--codex-app", "--turn-instance-id", "2026-10-31T00:00:00Z"],
	["status", "--goal-id", "perf-goal"],
	["refresh-state", "--goal-id", "perf-goal", "--agent-id", "perf-agent",
		"--next-action", "Continue bounded fixture work.", "--no-global-sync", "--dry-run"],
], ids=["heartbeat-prompt", "should-run", "status", "refresh-state"])
def test_heartbeat_chain_routes_do_not_load_the_chat_layer(tmp_path, monkeypatch, command):
	from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime

	isolate_sqlite_runtime(tmp_path, monkeypatch)
	registry, runtime = write_command_fixture(tmp_path)
	state = registry.parent.parent / ".codex/goals/perf-goal/ACTIVE_GOAL_STATE.md"
	initialize_canonical_source_fixture(runtime, "perf-goal", state_path=state, provider="sqlite")
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *command]
	result = run_cli_main("loopx.entrypoint", argv,
		forbidden_modules=("loopx.cli", "loopx.chat_manager", "loopx.chat_agent", "loopx.chat_store"))
	assert result.returncode == 0, result.stderr
	assert json.loads(result.stdout)["ok"] is True


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


def test_capability_hook_imports_defer_unselected_facade_exports() -> None:
	script = """
from importlib import import_module
import sys

import loopx.capabilities.issue_fix.pr_wait_hook
import loopx.capabilities.reward_memory.turn_hooks

for name in (
    "issue_fix.periodic_report",
    "issue_fix.pr_description",
    "issue_fix.reward_memory",
    "reward_memory.codex_app_outcome",
    "reward_memory.dogfood",
    "reward_memory.evaluation",
):
    assert "loopx.capabilities." + name not in sys.modules, name

from loopx.capabilities import issue_fix, reward_memory
for facade in (issue_fix, reward_memory):
    assert set(facade._EXPORTS) == set(facade.__all__)
    for name in facade.__all__:
        value = getattr(facade, name)
        owner = import_module("." + facade._EXPORTS[name], facade.__name__)
        assert value is getattr(owner, name), name
        assert name in dir(facade)
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


QUOTA_AUTHORITY_AND_COMPOSITION_OWNERS = (
	"loopx.status",
	"loopx.quota",
	"loopx.upgrade",
	"loopx.cli_commands.lark_inbox",
	"loopx.cli_commands.quota_context",
	"loopx.control_plane.quota.live_decision",
	"loopx.control_plane.quota.cli_projection",
	"loopx.capabilities.explore",
	"loopx.capabilities.periodic_report",
	"loopx.capabilities.repository_change_window",
	"loopx.capabilities.reward_memory",
	"loopx.capabilities.turn_context",
)
# Interaction hooks, the live decision, host observation, and recall exist only
# for should-run; accounting routes must not compose them.
QUOTA_SHOULD_RUN_ONLY_OWNERS = (
	"loopx.cli",
	"loopx.upgrade",
	"loopx.control_plane.quota.live_decision",
	"loopx.capabilities.periodic_report.pending_intent",
	"loopx.capabilities.repository_change_window",
	"loopx.capabilities.turn_context",
)


def test_quota_handler_import_defers_owners_and_preserves_export_identity() -> None:
	script = f"""
from importlib import import_module
import sys
import loopx.cli_commands.quota as command

for owner in {QUOTA_AUTHORITY_AND_COMPOSITION_OWNERS!r}:
    assert owner not in sys.modules, owner
for name, (module, attribute) in command._LAZY_EXPORTS.items():
    assert name in dir(command), name
    value = getattr(command, name)
    assert value is getattr(import_module(module), attribute), name
    assert getattr(command, name) is value, name
try:
    command.nonexistent_quota_handler_export
except AttributeError:
    pass
else:
    raise AssertionError("unknown quota handler exports must fail")
"""
	completed = run_isolated_script(script)
	assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("route", ["context-read", "replan-view"])
def test_quota_pre_authority_routes_skip_authority_owners(tmp_path: Path, route: str) -> None:
	registry, runtime = write_command_fixture(tmp_path)
	capture = tmp_path / "decision.json"
	capture.write_text("{\"ok\": true, \"goal_id\": \"perf-goal\"}\n", encoding="utf-8")
	digest = hashlib.sha256(capture.read_bytes()).hexdigest()
	arguments = {
		"context-read": ["--capture-file", str(capture), "--capture-sha256", digest],
		"replan-view": ["--decision-file", str(tmp_path / "missing.json"),
			"--outcome-ref", "outcome", "--source-hash", digest],
	}[route]
	before = registry.read_bytes()
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
		"quota", route, *arguments]
	selected = run_cli_main("loopx.entrypoint", argv,
		forbidden_modules=("loopx.cli", *QUOTA_AUTHORITY_AND_COMPOSITION_OWNERS))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == (0 if route == "context-read" else 1)
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr
	assert registry.read_bytes() == before
	assert not runtime.exists()


def test_quota_accounting_routes_skip_should_run_composition(tmp_path, monkeypatch) -> None:
	from tests.control_plane.canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime

	isolate_sqlite_runtime(tmp_path, monkeypatch)
	registry, runtime = write_command_fixture(tmp_path)
	state = registry.parent.parent / ".codex/goals/perf-goal/ACTIVE_GOAL_STATE.md"
	initialize_canonical_source_fixture(runtime, "perf-goal", state_path=state, provider="file")
	base = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", "quota"]
	identity = ["--goal-id", "perf-goal", "--agent-id", "perf-agent"]
	cases = [
		(["spend-slot", "--slots", "1", "--source", "heartbeat", "--todo-id", "todo_cli_dispatch"], 0),
		(["void-slot", "--void-generated-at", "2026-08-28T00:00:00+00:00",
			"--reason-summary", "Checked fixture"], 1),
		(["monitor-poll"], 1),
	]
	for arguments, expected_code in cases:
		result = run_cli_main("loopx.entrypoint", [*base, *arguments, *identity],
			forbidden_modules=QUOTA_SHOULD_RUN_ONLY_OWNERS)
		assert result.returncode == expected_code, result.stderr
		payload = json.loads(result.stdout)
		assert payload["mode"] == arguments[0]
		assert payload["ok"] is (expected_code == 0)
		assert payload.get("appended") is not True


def test_selected_parser_matches_full_help_and_diagnostics() -> None:
	argv_cases = [
		*[[command, flag] for command in ("bootstrap", "connect", "configure-goal")
		  for flag in ("--help", "--unknown-option")],
		["connect", "--max-children", "invalid"],
		["heartbeat-prequota", "--help"],
		["heartbeat-prequota"],
		["heartbeat-prequota", "-g", "goal"],
		["heartbeat-prequota", "-a", "agent"],
		["heartbeat-prequota", "-g", "goal", "-a", "agent", "--unknown-option"],
		["heartbeat-prequota", "-g", "goal", "-a", "agent", "--fetch-timeout-seconds", "bad"],
		["heartbeat-prequota", "-g", "goal", "-a", "agent", "--fetch-timeout-seconds"],
		["heartbeat-prequota", "-g", "goal", "-a", "agent", "--format", "bad"],
		["heartbeat-prequota", "--goal-i", "goal", "-a", "agent"],
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
	argv_cases.extend([["goal-acceptance", "--help"], ["goal-acceptance", "inspect"],
		["goal-acceptance", "unknown-action", "--goal-id", "goal"],
		["goal-acceptance", "inspect", "--goal-id", "goal", "--unknown-option"]])
	selected = run_cli_batch("loopx.entrypoint", argv_cases)
	full = run_cli_batch("loopx.cli", argv_cases)

	assert selected == full


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
@pytest.mark.parametrize("registry_mode", ["supplied", "equals", "empty", "omitted", "environment"])
@pytest.mark.parametrize("format_args, expected_format", [
	([], "markdown"), (["--format", "json"], "json"),
	(["--format", "markdown"], "markdown"),
])
def test_prequota_preserves_transport_patch_points_and_opaque_payload(
	tmp_path, module, registry_mode, format_args, expected_format,
):
	registry, runtime = write_command_fixture(tmp_path)
	global_registry = runtime / "registry.global.json"
	prefix = ["--runtime-root", str(runtime), "--format", "json"]
	if registry_mode == "supplied":
		prefix += ["--registry", str(registry)]
	elif registry_mode == "equals":
		prefix += ["--registry=" + str(registry)]
	elif registry_mode == "empty":
		prefix += ["--registry", ""]
	argv = prefix + ["heartbeat-prequota", "-g", "", "-a", "perf-agent", *format_args]
	# Global format applies when the subcommand does not override it.
	format_name = expected_format if format_args else "json"
	expected_registry = Path(".") if registry_mode == "empty" else (
		registry if registry_mode in {"supplied", "equals"} else global_registry
	)
	script = f"""
import json, sys
from pathlib import Path
from {module} import main
from loopx import heartbeat_prequota
from loopx.cli_commands import support_control
calls = []
payload = {{"ok": False, "degraded": True, "continue_to_quota": True,
    "quota_spend_required": False, "future": {{"opaque": [None, True, "保留\\n空 格"]}}}}
def runner(**kwargs):
    calls.append(kwargs)
    return payload
def renderer(value):
    assert value is payload
    return "CUSTOM-MARKDOWN"
def resolver(runtime_arg):
    assert runtime_arg == {str(runtime)!r}
    return Path({str(global_registry)!r})
support_control.run_heartbeat_pre_quota = heartbeat_prequota.run_heartbeat_pre_quota = runner
support_control.render_heartbeat_pre_quota_markdown = heartbeat_prequota.render_heartbeat_pre_quota_markdown = renderer
from loopx.cli_commands import support_control_registry
support_control.explicit_global_registry = support_control_registry.explicit_global_registry = resolver
sys.argv[0] = "loopx"
assert main({argv!r}) == 0
assert calls == [{{"registry_path": Path({str(expected_registry)!r}), "runtime_root_arg": {str(runtime)!r},
    "goal_id": "", "agent_id": "perf-agent", "fetch_timeout_seconds": 10}}]
"""
	result = run_isolated_script(script, environment={
		"LOOPX_REGISTRY": str(registry) if registry_mode == "environment" else "",
		"LOOPX_USAGE_PING": "0",
	})
	assert result.returncode == 0, result.stderr
	if format_name == "json":
		assert json.loads(result.stdout) == {
			"ok": False, "degraded": True, "continue_to_quota": True,
			"quota_spend_required": False, "future": {"opaque": [None, True, "保留\n空 格"]},
		}
	else:
		assert result.stdout == "CUSTOM-MARKDOWN\n"


@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
@pytest.mark.parametrize("timeout", [0, -1, 17])
def test_prequota_forwards_timeout_and_preserves_uncaught_exception(tmp_path, module, timeout):
	registry, runtime = write_command_fixture(tmp_path)
	argv = ["--registry", str(registry), "--runtime-root", str(runtime),
		"heartbeat-prequota", "-g", "perf-goal", "-a", "perf-agent",
		"--fetch-timeout-seconds", str(timeout)]
	script = f"""
from {module} import main
from loopx import heartbeat_prequota
from loopx.cli_commands import support_control
def runner(**kwargs):
    assert kwargs["fetch_timeout_seconds"] == {timeout}
    raise ValueError("synthetic-prequota-failure")
support_control.run_heartbeat_pre_quota = heartbeat_prequota.run_heartbeat_pre_quota = runner
try:
    main({argv!r})
except ValueError as exc:
    assert str(exc) == "synthetic-prequota-failure"
else:
    raise AssertionError("prequota must preserve its exception path")
"""
	result = run_isolated_script(script, environment={"LOOPX_USAGE_PING": "0"})
	assert result.returncode == 0, result.stderr
	assert result.stdout == ""


@pytest.mark.parametrize("flag", ["--help", "-h"])
def test_prequota_help_keeps_reconciliation_and_product_layers_unloaded(flag):
	selected = run_cli_main("loopx.entrypoint", ["heartbeat-prequota", flag],
		forbidden_modules=("loopx.cli", "loopx.cli_commands.support_control",
			"loopx.heartbeat_prequota", "loopx.chat_server", "loopx.dashboard_launcher"))
	full = run_cli_main("loopx.cli", ["heartbeat-prequota", flag])
	assert selected.returncode == full.returncode == 0
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("module", ["loopx.entrypoint", "loopx.cli"])
@pytest.mark.parametrize("observation", ["off", "merged", "degraded"])
def test_prequota_real_authority_observation_and_zero_quota(
	tmp_path, monkeypatch, capsys, provider, module, observation,
):
	import importlib
	from loopx.capabilities.issue_fix import pr_lifecycle
	from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
	from loopx.todos import add_goal_todo, list_goal_todos
	from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

	isolate_sqlite_runtime(tmp_path, monkeypatch)
	registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
	config = json.loads(registry.read_text())
	config["goals"][0]["quota"] = {"compute": 0}
	if observation != "off":
		config["goals"][0]["available_capabilities"] = ["external_evidence_poll"]
	registry.write_text(json.dumps(config))
	text = "保留原始工作\n与 opaque data"
	todo_id = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
		claimed_by="agent-a", text=text, resume_when="pr_merged:owner/repo#42")["todo_id"]
	before = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
	calls = []

	def fetch(reference, **kwargs):
		calls.append(reference)
		if observation == "off":
			pytest.fail("an unconfigured observation must not fetch metadata")
		if observation == "degraded":
			raise subprocess.TimeoutExpired("synthetic-provider", 1)
		return {"state": "MERGED", "mergedAt": "2026-10-01T00:00:00Z",
			"url": "https://github.com/owner/repo/pull/42"}

	monkeypatch.setattr(pr_lifecycle, "fetch_github_pr_lifecycle_payload", fetch)
	monkeypatch.setenv("LOOPX_KUNLUNCODE_OUTER_CONTROLLER", "1")
	monkeypatch.setenv("LOOPX_KUNLUNCODE_OUTER_GOAL_ID", "goal-a")
	main = importlib.import_module(module).main
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
		"heartbeat-prequota", "-g", "goal-a", "-a", "agent-a"]
	assert main(argv) == 0
	payload = json.loads(capsys.readouterr().out)
	assert payload["ok"] is True
	assert payload["continue_to_quota"] is True
	assert payload["quota_spend_required"] is False
	assert payload["degraded"] is (observation == "degraded")
	assert len(calls) == (0 if observation == "off" else 1)
	after = list_goal_todos(registry_path=registry, goal_id="goal-a", todo_id=todo_id)["todo"]
	for field in ("text", "status", "claimed_by", "resume_when", "required_write_scopes"):
		assert after.get(field) == before.get(field)
	assert after.get("resume_ready", False) is (observation == "merged")
	events = load_rollout_events(rollout_event_log_path(runtime, "goal-a"))
	assert not any(e["event_kind"] in {"quota_spend", "todo_complete"} for e in events)
	assert main(argv) == 0
	replay = json.loads(capsys.readouterr().out)
	assert replay["continue_to_quota"] is True and replay["quota_spend_required"] is False
	assert len(calls) == (0 if observation == "off" else 1)


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


@pytest.mark.parametrize("arguments", [
	["inspect"],
	["inspect-proposal", "--proposal-id", "missing-proposal"],
	["verify", "--proposal-id", "missing-proposal"],
])
def test_selected_goal_acceptance_refusals_match_without_full_cli(tmp_path, arguments):
	registry, runtime = write_command_fixture(tmp_path)
	before = registry.read_bytes()
	argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
		"goal-acceptance", *arguments, "--goal-id", "missing-goal"]
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 1
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr
	assert json.loads(selected.stdout)["ok"] is False
	assert registry.read_bytes() == before


@pytest.mark.parametrize("format_name", ["json", "markdown"])
@pytest.mark.parametrize("arguments, expected_code", [
	(["--intent", "watch_each_turn", "--host-capability", "visible_session",
	  "--host-identity", "codex-cli", "--registered-agent", "fixture-agent"], 0),
	(["--intent", "watch_each_turn", "--host-capability", "visible_session"], 0),
	(["--intent", "unsupported-intent"], 1),
	(["--host-capability", "unsupported-capability"], 1),
])
def test_selected_host_mode_plan_preserves_results_without_activation(
	tmp_path, format_name, arguments, expected_code,
):
	registry, runtime = tmp_path / "absent.json", tmp_path / "runtime"
	argv = ["--registry", str(registry), "--runtime-root", str(runtime),
		"--format", "markdown", "host-mode-plan", "--goal-id", "fixture-goal",
		"--cli-bin", "fixture-loopx", "--format", format_name, *arguments]
	# An explicit absent registry must not turn a read-only selector into an
	# authority read. Its preview is not permission to launch the named host.
	environment = {"LOOPX_USAGE_PING": "0"}
	selected = run_cli_main("loopx.entrypoint", argv,
		forbidden_modules=("loopx.cli", "loopx.chat_server", "loopx.chat_runtime",
			"loopx.cli_commands.benchmark_dispatch", "loopx.capabilities.content_ops.cli"),
		environment=environment)
	full = run_cli_main("loopx.cli", argv, environment=environment)
	assert selected.returncode == full.returncode == expected_code, (selected.stderr, full.stderr)
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	assert not registry.exists() and not runtime.exists()
	if format_name == "json" and expected_code == 0:
		payload = json.loads(selected.stdout)
		assert payload["schema_version"] == "host_mode_plan_v0"
		assert payload["goal_id"] == "fixture-goal"
		if "--host-identity" in arguments:
			assert payload["agent_id"] == "fixture-agent"
			assert payload["selected_connector_id"] == "codex_cli_tui"
		else:
			# A valid preview may expose missing readiness; it must not silently
			# select or activate a default host to make the plan executable.
			assert payload["selected_connector_id"] is None
			assert payload["selected_capability_ready"] is False


def test_selected_host_mode_plan_diagnostics_keep_full_parser_fallback():
	cases = [
		["host-mode-plan"], ["host-mode-plan", "--help"],
		["host-mode-plan", "--goal-id", "fixture-goal", "--unknown"],
		["host-mode-plan", "--goal-id", "fixture-goal", "--format", "yaml"],
		["host-mode-plan", "--goal-id", "fixture-goal", "--host-identity", "unknown"],
		["host-mode-plan", "--goal-id", "fixture-goal", "--host-cap"],
	]
	assert run_cli_batch("loopx.entrypoint", cases) == run_cli_batch("loopx.cli", cases)


def test_selected_host_mode_plan_does_not_start_processes_or_use_network(tmp_path):
	script = f"""
import os
import sys
os.environ["LOOPX_USAGE_PING"] = "0"
sys.dont_write_bytecode = True
os.chdir({str(tmp_path)!r})

def reject_activation(event, args):
    assert event not in ("subprocess.Popen", "os.system", "os.posix_spawn", "socket.connect"), event
    if event == "open":
        mode, flags = args[1:3]
        assert not (isinstance(mode, str) and any(c in mode for c in "wax+")), args
        assert not (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)), args

sys.addaudithook(reject_activation)
from loopx.entrypoint import main
assert main(["host-mode-plan", "--goal-id", "fixture-goal", "--intent", "watch_each_turn",
             "--host-capability", "visible_session", "--host-identity", "codex-cli",
             "--agent-id", "fixture-agent", "--format", "json"]) == 0
assert "loopx.cli" not in sys.modules
"""
	result = run_isolated_script(script)
	assert result.returncode == 0, result.stderr
	assert json.loads(result.stdout)["selected_connector_id"] == "codex_cli_tui"
	assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("arguments", [
	["list"], ["show", "performance-diagnosis"],
	["--help"], ["list", "--help"], ["show", "--help"],
])
def test_capability_discovery_skips_unselected_layers(tmp_path, arguments):
	result = run_cli_main("loopx.entrypoint",
		["--registry", str(tmp_path / "absent.json"), "--runtime-root", str(tmp_path / "runtime"),
		 "capability", *arguments],
		forbidden_modules=("loopx.cli", "loopx.chat_server", "loopx.chat_runtime",
			"loopx.cli_commands.benchmark_dispatch", "loopx.capabilities.content_ops.cli"),
		environment={"LOOPX_USAGE_PING": "0"})
	assert result.returncode == 0, result.stderr
	assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("format_name", ["json", "markdown"])
@pytest.mark.parametrize("extension_mode", ["absent", "declared", "disabled"])
@pytest.mark.parametrize("command", ["list", "show"])
def test_capability_discovery_preserves_catalog_and_default_off(
	tmp_path, format_name, extension_mode, command,
):
	from tests.capabilities.test_capability_extension_registry import _write_manifest
	from loopx.extensions.runtime import default_extension_state_file, disable_extension, install_extension

	registry, runtime = write_command_fixture(tmp_path)
	declared_extension = extension_mode != "absent"
	arguments = [command]
	if command == "show":
		arguments.append("sample-report" if declared_extension else "performance-diagnosis")
	if declared_extension:
		provider = None
		if extension_mode == "disabled":
			provider = tmp_path / "provider"
			provider.write_text(f"#!{sys.executable}\nraise SystemExit(0)\n", encoding="utf-8")
			provider.chmod(0o755)
		manifest = _write_manifest(tmp_path / "extension.toml", entrypoint=provider)
		arguments += ["--extension-manifest", str(manifest)]
		if extension_mode == "disabled":
			state_file = default_extension_state_file(runtime)
			install_extension(manifest, state_file=state_file, execute=True)
			disable_extension("sample-extension", state_file=state_file, execute=True)
	argv = ["--registry", str(registry), "--runtime-root", str(runtime),
		"--format", "markdown", "capability", *arguments, "--format", format_name]
	before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
	selected = run_cli_main("loopx.entrypoint", argv, environment={"LOOPX_USAGE_PING": "0"})
	full = run_cli_main("loopx.cli", argv, environment={"LOOPX_USAGE_PING": "0"})
	assert selected.returncode == full.returncode == 0, (selected.stderr, full.stderr)
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
	assert runtime.exists() == (extension_mode == "disabled")
	if format_name == "json":
		payload = json.loads(selected.stdout)
		assert payload["schema_version"] == (
			"loopx_capability_catalog_v0" if command == "list" else "loopx_capability_detail_v0")
		entries = payload["capabilities"] if command == "list" else [payload["capability"]]
		assert any(item["id"] == ("sample-report" if declared_extension else "performance-diagnosis")
			for item in entries)
		assert all(item["id"] != "sample-internal-helper" for item in entries)
		if declared_extension:
			entry = next(item for item in entries if item["id"] == "sample-report")
			assert entry["origin"] == "extension"
			assert entry["provider_state"] == {
				"declared": True, "installed": extension_mode == "disabled", "enabled": False, "ready": False}


def test_capability_discovery_rejections_keep_canonical_diagnostics(tmp_path):
	manifest = tmp_path / "invalid.toml"
	manifest.write_text("not valid TOML [", encoding="utf-8")
	prefix = ["--registry", str(tmp_path / "absent.json"),
		"--runtime-root", str(tmp_path / "runtime"), "capability"]
	cases = [["list", "--unknown"], ["show"], ["show", "absent-capability"],
		["list", "--format", "yaml"], ["list", "--extension-manifest"],
		["list", "--extension-manifest", str(tmp_path / "absent.toml")],
		["list", "--extension-manifest", str(manifest)], ["list", "--ext"]]
	assert run_cli_batch("loopx.entrypoint", [prefix + case for case in cases]) == (
		run_cli_batch("loopx.cli", [prefix + case for case in cases]))
	assert not (tmp_path / "runtime").exists()


@pytest.mark.parametrize("command", ["inspect", "bind", "invoke"])
def test_capability_non_discovery_retains_full_cli_patch_point(tmp_path, command):
	arguments = ["inspect", "--goal-id", "fixture-goal"] if command == "inspect" else (
		[command, "sample-report", "--goal-id", "fixture-goal", "--operation", "read", "--execute"])
	if command == "invoke":
		arguments += ["--input-json", str(tmp_path / "input.json")]
	script = f"""
import os
os.environ["LOOPX_USAGE_PING"] = "0"
import loopx.cli as full
from loopx.entrypoint import main
seen = []
def original_path(args, **kwargs):
    seen.append((args.capability_command, args.goal_id, getattr(args, "execute", False)))
    return 73
full.handle_capability_command = original_path
assert main(["--registry", {str(tmp_path / "absent.json")!r}, "--runtime-root", {str(tmp_path / "runtime")!r},
             "capability", *{arguments!r}]) == 73
assert seen == [({command!r}, "fixture-goal", {command != 'inspect'!r})]
"""
	result = run_isolated_script(script)
	assert result.returncode == 0, result.stderr
	assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("arguments", [["list"], ["show", "performance-diagnosis"]])
def test_capability_discovery_does_not_activate_providers(tmp_path, arguments):
	from tests.capabilities.test_capability_extension_registry import _write_manifest

	provider = tmp_path / "provider.py"
	provider.write_text("raise AssertionError('discovery cannot run a provider')\n", encoding="utf-8")
	manifest = _write_manifest(tmp_path / "extension.toml", entrypoint=provider)
	script = f"""
import os
import sys
os.environ["LOOPX_USAGE_PING"] = "0"
sys.dont_write_bytecode = True
os.chdir({str(tmp_path)!r})
def reject_activation(event, args):
    assert event not in ("subprocess.Popen", "os.system", "os.posix_spawn", "socket.connect"), event
    if event == "open":
        mode, flags = args[1:3]
        assert not (isinstance(mode, str) and any(c in mode for c in "wax+")), args
        assert not (isinstance(flags, int) and flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT)), args
sys.addaudithook(reject_activation)
from loopx.entrypoint import main
assert main(["--registry", {str(tmp_path / "absent.json")!r}, "--runtime-root", {str(tmp_path / "runtime")!r},
             "--format", "json", "capability", *{arguments!r}, "--extension-manifest", {str(manifest)!r}]) == 0
"""
	before = {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
	result = run_isolated_script(script, environment={"PATH": str(tmp_path / "no-node")})
	assert result.returncode == 0, result.stderr
	assert {str(path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()} == before
	assert not (tmp_path / "runtime").exists()


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


@pytest.mark.parametrize("arguments", [
	["--help"], ["--evidence-ref", "absent", "--help"],
	["repair-index-duplicates", "--help"],
])
def test_history_help_loads_only_its_reader(arguments):
	result = run_cli_main("loopx.entrypoint", ["history", *arguments],
		forbidden_modules=("loopx.cli", "loopx.chat_server",
			"loopx.cli_commands.benchmark_dispatch"),
		environment={"PATH": ""})
	assert result.returncode == 0, result.stderr
	assert "--evidence-ref" in result.stdout
	assert "--agent-id" in result.stdout
	assert result.stderr == ""


@pytest.mark.parametrize("format_name", ["json", "markdown"])
@pytest.mark.parametrize("limit", [-1, 0, 1, 10])
def test_history_reader_preserves_scope_chronology_and_limits(tmp_path, format_name, limit):
	registry, runtime = write_command_fixture(tmp_path)
	index = runtime / "goals/perf-goal/runs/index.jsonl"
	index.parent.mkdir(parents=True)
	rows = [
		{"goal_id": "perf-goal", "agent_id": "perf-agent",
		 "generated_at": "2026-08-19T00:30:00+08:00", "classification": "offset-earlier"},
		{"goal_id": "perf-goal", "agent_id": "perf-agent",
		 "generated_at": "2026-08-18T17:00:00Z", "classification": "utc-later"},
		{"goal_id": "perf-goal", "agent_id": "other-agent",
		 "generated_at": "2026-08-18T18:00:00Z", "classification": "other-lane"},
	]
	index.write_text("".join(json.dumps(row) + "\n" for row in rows))
	before = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
	argv = ["--registry=" + str(registry), "--runtime-root=" + str(runtime),
		"--cli-bin", "loopx", "--format", format_name, "history",
		"--goal-id", "perf-goal", "--agent-id", "perf-agent", "--limit", str(limit)]
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 0, (selected.stderr, full.stderr)
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	assert "other-lane" not in selected.stdout
	if format_name == "json":
		payload = json.loads(selected.stdout)
		expected = ["utc-later", "offset-earlier"][:max(0, limit)]
		assert [row["classification"] for row in payload["runs"]] == expected
		assert [row["classification"] for row in payload["goals"][0]["latest_runs"]] == expected
	assert {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before


def test_history_reader_retains_required_native_runtime_failure(tmp_path):
	registry, runtime = write_command_fixture(tmp_path)
	index = runtime / "goals/perf-goal/runs/index.jsonl"
	index.parent.mkdir(parents=True)
	index.write_text(json.dumps({"goal_id": "perf-goal", "agent_id": "perf-agent",
		"generated_at": "2026-08-18T17:00:00Z", "classification": "bounded_probe"}) + "\n")
	argv = ["--registry", str(registry), "--runtime-root", str(runtime),
		"--format", "json", "history", "--goal-id", "perf-goal"]
	temporary = tmp_path / "no-node-runtime"
	temporary.mkdir()
	environment = {"PATH": "", **{key: str(temporary) for key in ("TMPDIR", "TEMP", "TMP")}}
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",),
		environment=environment)
	full = run_cli_main("loopx.cli", argv, environment=environment)
	assert selected.returncode == full.returncode == 1
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	assert json.loads(selected.stdout)["error"] == "LoopX Effect runtime requires Node.js 22.22.3 or newer on PATH"


@pytest.mark.parametrize("case", ["current", "other-agent", "stale", "no-node"])
def test_history_reader_preserves_native_evidence_reference_window(tmp_path, case):
	from loopx.control_plane.work_items.replan_context_codec import project_replan_context

	registry, runtime = write_command_fixture(tmp_path)
	index = runtime / "goals/perf-goal/runs/index.jsonl"
	index.parent.mkdir(parents=True)
	observation = "Keep independent failure evidence.\nPreserve the next decision's constraint."
	index.write_text(json.dumps({"goal_id": "perf-goal", "agent_id": "perf-agent",
		"generated_at": "2026-08-18T17:00:00Z", "classification": "bounded_probe",
		"health_check": observation}) + "\n")
	context = project_replan_context(goal_id="perf-goal", agent_id="perf-agent", runs=(),
		source_status={"registry": str(registry), "runtime_root": str(runtime),
			"run_history": {"goals": []}})
	argv = ["--registry", str(registry), "--runtime-root", str(runtime),
		"--format", "json", "history", "--goal-id", "perf-goal", "--agent-id",
		"other-agent" if case == "other-agent" else "perf-agent",
		"--evidence-ref", context["evidence"][0]["evidence_ref"], "--limit", "0"]
	if case == "stale":
		index.write_text("")
	before = index.read_bytes()
	environment = None
	if case == "no-node":
		temporary = tmp_path / "no-node-runtime"
		temporary.mkdir()
		environment = {"PATH": "", **{key: str(temporary) for key in ("TMPDIR", "TEMP", "TMP")}}
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",),
		environment=environment)
	full = run_cli_main("loopx.cli", argv, environment=environment)
	assert selected.returncode == full.returncode == (0 if case == "current" else 1)
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	payload = json.loads(selected.stdout)
	if case == "current":
		assert payload["evidence"]["health_check"] == observation
	elif case == "no-node":
		assert "requires Node.js" in payload["error"]
	else:
		assert "unavailable" in payload["error"]
	assert index.read_bytes() == before


def test_history_reader_keeps_complete_parser_diagnostics():
	cases = [
		["history", "unknown-action"], ["history", "--unknown"],
		["history", "--agent-i", "agent"], ["history", "--evidence-ref"],
		["history", "--limit", "invalid"], ["history", "--format", "json"],
		["--format", "unknown", "history"], ["--cli-bin", "", "history"],
		["--registry", "history"], ["--runtime-root", "history"],
	]
	selected, full = run_cli_batch("loopx.entrypoint", cases), run_cli_batch("loopx.cli", cases)
	assert selected == full
	assert all(result["returncode"] == 2 and not result["stdout"] for result in selected)


@pytest.mark.parametrize("arguments,error", [
	(["--agent-id", "perf-agent"], "--agent-id requires --goal-id without a history action"),
	(["--evidence-ref", "absent", "--goal-id", "perf-goal"],
	 "--evidence-ref requires --goal-id and --agent-id without a history action"),
])
def test_history_reader_validation_refusals_match_full_cli(tmp_path, arguments, error):
	argv = ["--registry", str(tmp_path / "missing.json"), "--format", "json", "history", *arguments]
	selected = run_cli_main("loopx.entrypoint", argv, forbidden_modules=("loopx.cli",))
	full = run_cli_main("loopx.cli", argv)
	assert selected.returncode == full.returncode == 1
	assert selected.stdout == full.stdout
	assert selected.stderr == full.stderr == ""
	assert json.loads(selected.stdout) == {"ok": False, "error": error}
	assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("action", [
	None, "inspect-index-duplicates", "repair-index-duplicates",
	"rebuild-index-collisions", "trajectory-hygiene",
])
def test_history_maintenance_and_explicit_full_cli_keep_patch_points(tmp_path, action):
	arguments = ["history", *([action] if action else []), "--goal-id", "perf-goal"]
	# Direct full-CLI bindings retain their handler even for ordinary reads.
	module = "loopx.entrypoint" if action else "loopx.cli"
	script = f"""
import loopx.cli as full
from {module} import main
seen = []
def original_path(args, **kwargs):
    seen.append((args.history_action, args.goal_id))
    return 73
full.handle_history_command = original_path
assert main(["--registry", {str(tmp_path / 'absent.json')!r}, *{arguments!r}]) == 73
assert seen == [({action!r}, "perf-goal")]
"""
	result = run_isolated_script(script)
	assert result.returncode == 0, result.stderr
	assert not list(tmp_path.iterdir())


def test_history_entrypoint_retains_preloaded_full_cli_handler(tmp_path):
	script = f"""
import loopx.cli as full
from loopx.entrypoint import main
seen = []
def original_path(args, **kwargs):
    seen.append((args.history_action, args.goal_id))
    return 73
full.handle_history_command = original_path
assert main(["--registry", {str(tmp_path / 'absent.json')!r},
             "history", "--goal-id", "perf-goal"]) == 73
assert seen == [(None, "perf-goal")]
"""
	result = run_isolated_script(script)
	assert result.returncode == 0, result.stderr
	assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("command", ["bootstrap", "connect"])
def test_selected_connect_preview_keeps_full_cli_result_without_workspace(tmp_path, command):
    registry = tmp_path / "project/.loopx/registry.json"
    argv = ["--registry", str(registry), "--runtime-root", str(tmp_path / "runtime"),
            "--format", "json", command, "--project", str(tmp_path / "project"),
            "--objective", "Write and validate one artifact.", "--goal-id", "preview-goal",
            "--no-global-sync", "--dry-run"]
    selected = run_cli_main("loopx.entrypoint", argv,
        forbidden_modules=("loopx.cli", "loopx.chat", "loopx.canary"))
    ordinary = run_cli_main("loopx.cli", argv)
    assert selected.returncode == ordinary.returncode == 0, (selected.stderr, ordinary.stderr)
    assert json.loads(selected.stdout) == json.loads(ordinary.stdout)
    assert not registry.exists()
