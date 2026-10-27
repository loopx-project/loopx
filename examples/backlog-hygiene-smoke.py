#!/usr/bin/env python3
"""Smoke-test Agent Todo hygiene warnings in status and quota should-run."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tests" / "control_plane"))
from canonical_authority_fixture import initialize_canonical_source_fixture  # noqa: E402

GOAL_ID = "backlog-hygiene-goal"


def run_cli(*args: str, registry_path: Path, runtime: Path) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def write_fixture(
    root: Path,
    *,
    include_agent_todo: bool,
    done_agent_todos: int = 0,
    archived_done_todos: int = 0,
) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"

    state_path.parent.mkdir(parents=True, exist_ok=True)
    agent_todo_lines = [
        f"- [x] Completed active work item {index}.\n" for index in range(1, done_agent_todos + 1)
    ]
    if include_agent_todo:
        agent_todo_lines.append(
            "- [ ] [P1] Mirror durable follow-up work into Agent Todo before scheduling.\n"
        )
    agent_todo_section = "\n## Agent Todo\n\n" + "".join(agent_todo_lines)
    archive_section = ""
    if archived_done_todos:
        archive_section = "\n## Completed Work Archive\n\n" + "".join(
            f"- [x] Archived completed work item {index}.\n"
            for index in range(1, archived_done_todos + 1)
        )
    state_path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Backlog Hygiene Fixture\n\n"
        "## Next Action\n\n"
        "- Add a backlog hygiene check for hidden long-running work.\n"
        "- Add a source-registry regression after the hygiene check lands.\n\n"
        "## Operating Lessons\n\n"
        "- Keep the sub-agent audit as an explicit follow-up todo.\n"
        f"{agent_todo_section}"
        f"{archive_section}",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "backlog-hygiene-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "authority_sources": [],
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "allowed_slots": 5,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    initialize_canonical_source_fixture(runtime, GOAL_ID, state_path=state_path, provider="sqlite")
    return registry_path, runtime


def attention_item(status_payload: dict) -> dict:
    items = status_payload.get("attention_queue", {}).get("items") or []
    assert len(items) == 1, status_payload
    return items[0]


def assert_hidden_backlog_warning() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-backlog-hygiene-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), include_agent_todo=False)
        status_payload = run_cli("status", registry_path=registry_path, runtime=runtime)
        item = attention_item(status_payload)
        warning = item["project_asset"]["backlog_hygiene_warning"]
        assert warning["kind"] == "hidden_backlog_without_agent_todo", warning
        assert warning["requires_agent_todo"] is True, warning
        assert warning["evidence_count"] == 3, warning
        assert warning["source_sections"] == ["Next Action", "Operating Lessons"], warning
        assert item["backlog_hygiene_warning"] == warning, item

        guard = run_cli("quota", "should-run", "--goal-id", GOAL_ID, registry_path=registry_path, runtime=runtime)
        assert guard["should_run"] is True, guard
        assert guard["backlog_hygiene_warning"] == warning, guard
        assert guard["agent_todo_summary"]["open_count"] == 0, guard
        assert guard["effective_action"] == "state_projection_gap_repair", guard
        assert guard["normal_delivery_allowed"] is False, guard
        assert guard["execution_obligation"]["must_attempt_work"] is True, guard


def assert_open_agent_todo_suppresses_warning() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-backlog-hygiene-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp), include_agent_todo=True)
        status_payload = run_cli("status", registry_path=registry_path, runtime=runtime)
        item = attention_item(status_payload)
        assert "backlog_hygiene_warning" not in item, item
        assert "backlog_hygiene_warning" not in item["project_asset"], item

        guard = run_cli("quota", "should-run", "--goal-id", GOAL_ID, registry_path=registry_path, runtime=runtime)
        assert "backlog_hygiene_warning" not in guard, guard
        assert guard["agent_todo_summary"]["open_count"] == 1, guard


def assert_completed_todo_archive_warning() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-archive-") as tmp:
        registry_path, runtime = write_fixture(
            Path(tmp),
            include_agent_todo=True,
            done_agent_todos=13,
        )
        status_payload = run_cli("status", registry_path=registry_path, runtime=runtime)
        item = attention_item(status_payload)
        warning = item["project_asset"]["completed_todo_archive_warning"]
        assert warning["kind"] == "completed_agent_todo_archive_required", warning
        assert warning["requires_archive"] is True, warning
        assert warning["active_done_count"] == 13, warning
        assert warning["active_open_count"] == 1, warning
        assert warning["max_active_done_count"] == 12, warning
        assert warning["default_archive_keep_count"] == 10, warning
        assert warning["archive_command_template"].endswith(
            "--max-active-done 10 --execute"
        ), warning
        assert item["completed_todo_archive_warning"] == warning, item

        guard = run_cli("quota", "should-run", "--goal-id", GOAL_ID, registry_path=registry_path, runtime=runtime)
        assert guard["should_run"] is True, guard
        assert guard["completed_todo_archive_warning"] == warning, guard
        assert guard["agent_todo_summary"]["open_count"] == 1, guard
        assert guard["agent_todo_summary"]["done_count"] == 13, guard


def assert_completed_archive_section_is_not_active_todo() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-todo-archive-") as tmp:
        registry_path, runtime = write_fixture(
            Path(tmp),
            include_agent_todo=True,
            done_agent_todos=12,
            archived_done_todos=20,
        )
        status_payload = run_cli("status", registry_path=registry_path, runtime=runtime)
        item = attention_item(status_payload)
        assert "completed_todo_archive_warning" not in item, item
        assert "completed_todo_archive_warning" not in item["project_asset"], item
        assert item["agent_todos"]["done_count"] == 12, item
        assert item["project_asset"]["agent_todos"]["done"] == 12, item

        guard = run_cli("quota", "should-run", "--goal-id", GOAL_ID, registry_path=registry_path, runtime=runtime)
        assert "completed_todo_archive_warning" not in guard, guard
        assert guard["agent_todo_summary"]["open_count"] == 1, guard
        assert guard["agent_todo_summary"]["done_count"] == 12, guard


def main() -> int:
    assert_hidden_backlog_warning()
    assert_open_agent_todo_suppresses_warning()
    assert_completed_todo_archive_warning()
    assert_completed_archive_section_is_not_active_todo()
    print("backlog-hygiene-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
