#!/usr/bin/env python3
"""Smoke-test force bootstrap warnings and todo-preserving reconnects."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "bootstrap-preserve-fixture"


def run_cli(*args: str, check: bool = True) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=REPO_ROOT,
        check=check,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def goal_from_registry(project: Path) -> dict:
    registry = project / ".loopx" / "registry.json"
    payload = json.loads(registry.read_text(encoding="utf-8"))
    return payload["goals"][0]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-bootstrap-preserve-") as tmp:
        project = Path(tmp) / "project"
        project.mkdir()

        initial = run_cli(
            "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--objective",
            "Exercise force reconnect preservation.",
            "--no-global-sync",
        )
        assert initial["ok"] is True, initial
        assert initial["state_action"] == "created", initial
        state_file = Path(initial["state_file"])
        assert state_file == (project / ".loopx/goals/bootstrap-preserve-fixture/ACTIVE_GOAL_STATE.md").resolve()

        todo_state = "# Active Goal State\n\n## Agent Todo\n\n- [ ] Preserve this during reconnect.\n"
        state_file.write_text(todo_state, encoding="utf-8")

        preserved = run_cli(
            "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--objective",
            "Exercise force reconnect preservation.",
            "--write-scope",
            "src/**",
            "--force",
            "--preserve-todos",
            "--no-global-sync",
        )
        assert preserved["ok"] is True, preserved
        assert preserved["state_action"] == "kept-existing-preserve-todos", preserved
        warning = preserved["force_bootstrap_warning"]
        assert warning["will_replace_active_state"] is False, warning
        assert warning["preserve_todos_requested"] is True, warning
        assert "configure-goal --write-scope" in warning["recommended_scope_migration"], warning
        migrated_state = state_file.read_text(encoding="utf-8")
        assert "- [ ] Preserve this during reconnect." in migrated_state
        assert "## User Todo / Owner Review Reading Queue" in migrated_state
        assert preserved["todo_source_migration"] == {
            "schema_version": "todo_source_section_migration_v0",
            "added_roles": ["user"],
            "applied": True,
        }, preserved
        assert goal_from_registry(project)["coordination"]["write_scope"] == ["src/**"]

        replaced = run_cli(
            "bootstrap",
            "--project",
            str(project),
            "--goal-id",
            GOAL_ID,
            "--objective",
            "Exercise force reconnect replacement warning.",
            "--write-scope",
            "docs/**",
            "--force",
            "--no-global-sync",
        )
        assert replaced["ok"] is True, replaced
        assert replaced["state_action"] == "replaced", replaced
        warning = replaced["force_bootstrap_warning"]
        assert warning["will_replace_active_state"] is True, warning
        assert warning["preserve_todos_requested"] is False, warning
        assert "Preserve this during reconnect." not in state_file.read_text(encoding="utf-8")
        assert goal_from_registry(project)["coordination"]["write_scope"] == ["docs/**"]

    print("bootstrap-force-preserve-todos-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
