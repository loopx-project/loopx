#!/usr/bin/env python3
"""Smoke-test todo/quota help and structured validation errors."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "LOOPX_USAGE_PING": "0"},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def assert_contains(text: str, needle: str, label: str) -> None:
    # Help wrapping can split words at hyphens; only whitespace is incidental here.
    if "".join(needle.split()) not in "".join(text.split()):
        raise AssertionError(f"{label} missing {needle!r}")


def assert_help_surfaces() -> None:
    todo_help = run_cli("todo", "--help")
    assert todo_help.returncode == 0, todo_help.stderr
    assert_contains(todo_help.stdout, "archive-completed", "todo help")
    assert_contains(todo_help.stdout, "--self-merged", "todo help")
    assert_contains(todo_help.stdout, "--next-agent-todo", "todo help")

    complete_help = run_cli("todo", "complete", "--help")
    assert complete_help.returncode == 0, complete_help.stderr
    assert_contains(
        complete_help.stdout,
        "The options below are the union for every todo command",
        "todo complete help",
    )
    assert_contains(
        complete_help.stdout,
        "lifecycle actor; registered multi-agent goals require",
        "todo complete agent-id help",
    )
    assert_contains(
        complete_help.stdout,
        "exact linked user_gate decision_scope",
        "todo complete controller override help",
    )
    assert_contains(
        complete_help.stdout,
        "todo add intentionally does not accept this option",
        "todo agent-id help",
    )
    assert_contains(
        complete_help.stdout,
        "use --claimed-by to assign execution",
        "todo agent-id recovery help",
    )

    quota_help = run_cli("quota", "--help")
    assert quota_help.returncode == 0, quota_help.stderr
    assert_contains(quota_help.stdout, "should-run", "quota help")
    assert_contains(quota_help.stdout, "spend-slot", "quota help")
    assert_contains(quota_help.stdout, "--available-capability", "quota help")


def assert_todo_error_payload() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-cli-todo-modular-smoke-") as tmp:
        result = run_cli(
            "--format",
            "json",
            "--registry",
            str(Path(tmp) / "missing-registry.json"),
            "--runtime-root",
            tmp,
            "todo",
            "add",
            "--goal-id",
            "cli-modular-smoke",
            "--text",
            "Synthetic todo missing role.",
            "--dry-run",
        )
    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False, payload
    assert payload["goal_id"] == "cli-modular-smoke", payload
    assert payload["error"] == "todo add requires --role", payload

    with tempfile.TemporaryDirectory(prefix="loopx-cli-todo-agent-id-smoke-") as tmp:
        result = run_cli(
            "--format",
            "json",
            "--registry",
            str(Path(tmp) / "missing-registry.json"),
            "--runtime-root",
            tmp,
            "todo",
            "complete",
            "--goal-id",
            "cli-modular-smoke",
            "--agent-id",
            "codex-side-bypass",
        )
    assert result.returncode == 1, result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is False, payload
    assert payload["error"] == "todo complete requires --todo-id", payload


def assert_todo_markdown_error_payload() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-cli-todo-markdown-smoke-") as tmp:
        result = run_cli(
            "--registry",
            str(Path(tmp) / "missing-registry.json"),
            "--runtime-root",
            tmp,
            "todo",
            "add",
            "--goal-id",
            "cli-modular-smoke",
            "--text",
            "Synthetic todo missing role.",
            "--dry-run",
        )
    assert result.returncode == 1, result.stdout
    assert_contains(result.stdout, "# LoopX Todo", "todo markdown error")
    assert_contains(result.stdout, "- ok: `False`", "todo markdown error")
    assert_contains(result.stdout, "- error: todo add requires --role", "todo markdown error")


def assert_history_reader_route() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-cli-history-smoke-") as tmp:
        script = f"""
import contextlib
import io
import json
import sys
from loopx.entrypoint import main
output = io.StringIO()
with contextlib.redirect_stdout(output):
    result = main(["--registry", {str(Path(tmp) / 'absent.json')!r},
                   "--format", "json", "history", "--agent-id", "fixture-agent"])
assert result == 1
assert json.loads(output.getvalue()) == {{"ok": False,
    "error": "--agent-id requires --goal-id without a history action"}}
for module in ("loopx.cli", "loopx.chat_server", "loopx.cli_commands.benchmark_dispatch"):
    assert module not in sys.modules, module
"""
        result = subprocess.run([sys.executable, "-c", script], cwd=REPO_ROOT,
                                text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr


def main() -> int:
    assert_help_surfaces()
    assert_todo_error_payload()
    assert_todo_markdown_error_payload()
    assert_history_reader_route()
    print("cli-control-plane-command-modularization-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
