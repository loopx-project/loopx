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


def run_isolated_script(script: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        text=True,
        capture_output=True,
        check=False,
    )


def run_cli_main(
	module: str,
	argv: list[str],
	*,
	forbidden_modules: tuple[str, ...] = (),
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
    assert forbidden_module not in sys.modules
raise SystemExit(exit_code)
"""
	return run_isolated_script(script)


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


@pytest.mark.parametrize(
	("argv", "registration_module", "handler_module"),
	[
		(["check", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["status", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["diagnose", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["review-packet", "--help"], "loopx.cli_commands.status_registration", "loopx.cli_commands.status"),
		(["quota", "--help"], "loopx.cli_commands.quota_registration", "loopx.cli_commands.quota"),
		(["todo", "--help"], "loopx.cli_commands.todo_registration", "loopx.cli_commands.todo"),
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


def test_selected_parser_matches_full_help_and_diagnostics() -> None:
	argv_cases = [
		["check", "--help"],
		["status", "--help"],
		["diagnose", "--help"],
		["review-packet", "--help"],
		["todo", "--help"],
		["quota", "--help"],
		["status", "--unknown-option"],
		["todo", "list"],
		["quota", "unknown-command"],
		["--format", "unknown", "status"],
	]
	selected = run_cli_batch("loopx.entrypoint", argv_cases)
	full = run_cli_batch("loopx.cli", argv_cases)

	assert selected == full


def test_selected_todo_execution_matches_full_cli(tmp_path: Path) -> None:
	registry, runtime_root = write_command_fixture(tmp_path)
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
	assert selected.stdout == full.stdout
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
