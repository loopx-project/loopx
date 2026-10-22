#!/usr/bin/env python3
"""Smoke-test explicit project_asset todo projection gaps."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.status import (  # noqa: E402
    TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION,
    TODO_PROJECTION_VIEW_SCHEMA_VERSION,
    project_asset_todo_projection_gap,
    project_asset_todo_summary,
)


GOAL_ID = "project-asset-todo-gap-goal"


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active-read-only\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Project Asset Todo Gap Goal\n\n"
        "## Next Action\n\n"
        "- Run the first read-only adapter tick.\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "project-asset-gap-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "authority_sources": [],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path, runtime


def run_cli(
    registry_path: Path,
    runtime: Path,
    *args: str,
    output_format: str = "json",
    check: bool = True,
) -> dict | str:
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
            output_format,
            *args,
        ],
        cwd=REPO_ROOT,
        check=check,
        capture_output=True,
        text=True,
    )
    if output_format == "json":
        return json.loads(result.stdout)
    return result.stdout


def attention_item(payload: dict) -> dict:
    items = payload["attention_queue"]["items"]
    assert len(items) == 1, payload
    return items[0]


def main() -> int:
    empty_summary = project_asset_todo_summary(
        {
            "schema_version": "todo_summary_v0",
            "open_count": 0,
            "done_count": 0,
            "total_count": 0,
        }
    )
    assert empty_summary["schema_version"] == "todo_summary_v0", empty_summary
    assert empty_summary["source_section"] == "project_asset", empty_summary
    assert empty_summary["open"] == 0, empty_summary
    assert empty_summary["done"] == 0, empty_summary
    assert empty_summary["total"] == 0, empty_summary
    assert empty_summary["projection_view"]["schema_version"] == TODO_PROJECTION_VIEW_SCHEMA_VERSION, empty_summary
    assert empty_summary["projection_view"]["view"] == "project_asset_overview", empty_summary
    assert empty_summary["projection_view"]["truth"] == "derived", empty_summary
    assert (
        empty_summary["projection_view"]["canonical_source"]
        == "attention_queue.items[].{user_todos,agent_todos}"
    ), empty_summary
    assert empty_summary["detail_pointer"]["schema_version"] == TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION, empty_summary
    assert empty_summary["detail_pointer"]["full_list_included"] is False, empty_summary
    user_empty_summary = project_asset_todo_summary(empty_summary, role="user")
    assert user_empty_summary["projection_view"]["canonical_source"] == "attention_queue.items[].user_todos"
    agent_empty_summary = project_asset_todo_summary(empty_summary, role="agent")
    assert agent_empty_summary["projection_view"]["canonical_source"] == "attention_queue.items[].agent_todos"
    assert project_asset_todo_projection_gap(user_todos=empty_summary, agent_todos=empty_summary) is None
    gap = project_asset_todo_projection_gap(user_todos=None, agent_todos=empty_summary)
    assert gap and gap["missing_roles"] == ["user"], gap

    with tempfile.TemporaryDirectory(prefix="loopx-project-asset-todo-gap-") as raw_tmp:
        registry_path, runtime = write_fixture(Path(raw_tmp))
        incomplete = run_cli(registry_path, runtime, "status", check=False)
        assert incomplete["ok"] is False, incomplete
        assert any("missing required Todo section(s): Agent Todo" in error
                   for error in incomplete["contract_errors"]), incomplete
        state = registry_path.parent.parent / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
        state.write_text(state.read_text(encoding="utf-8") + "\n## Agent Todo\n",
                         encoding="utf-8")
        payload = run_cli(registry_path, runtime, "status")
        assert payload["ok"] is True, payload
        item = attention_item(payload)  # type: ignore[arg-type]
        project_asset = item["project_asset"]
        projected_gap = project_asset["todo_projection_gap"]
        assert projected_gap["kind"] == "project_asset_todo_projection_gap", projected_gap
        assert projected_gap["missing_roles"] == ["user"], projected_gap
        assert "user_todos" not in project_asset, project_asset
        assert project_asset["agent_todos"]["open"] == 0, project_asset
        assert item["todo_projection_gap"]["missing_roles"] == ["user"], item

        markdown = run_cli(registry_path, runtime, "status", output_format="markdown")
        assert "todo_projection_gap" in markdown, markdown
        assert "missing_roles=user" in markdown, markdown

    print("project-asset-todo-projection-gap-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
