from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority
from examples.control_plane.quota_plan_fixtures import write_cli_fixture
from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_node_event,
    explore_result_log_path,
)
from loopx.capabilities.issue_fix.explore_projection import (
    project_issue_fix_explore_graph,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def _make_runtime_paths_project_relative(
    *,
    registry_path: Path,
    runtime_root: Path,
    project: Path,
) -> Path:
    target_runtime = project / ".loopx" / "runtime"
    runtime_root.rename(target_runtime)

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    registry["common_runtime_root"] = ".loopx/runtime"
    registry_path.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    for index_path in target_runtime.glob("goals/*/runs/index.jsonl"):
        rows = []
        for line in index_path.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            for field in ("json_path", "markdown_path"):
                raw_path = Path(str(row[field]))
                moved_path = target_runtime / raw_path.relative_to(runtime_root)
                row[field] = str(moved_path.relative_to(project))
            rows.append(json.dumps(row, ensure_ascii=False))
        index_path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return target_runtime


def _run_cli(*args: str, cwd: Path) -> dict:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        value for value in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if value
    )
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *args],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_history_and_quota_anchor_relative_runtime_to_registry_project(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(tmp_path / "fixture")
    target_runtime = _make_runtime_paths_project_relative(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
    )
    independent_worktree = tmp_path / "independent-worktree"
    independent_worktree.mkdir()

    history = _run_cli(
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "history",
        "--goal-id",
        "half-speed",
        "--limit",
        "1",
        cwd=independent_worktree,
    )

    assert Path(history["runtime_root"]) == target_runtime
    assert history["run_count"] == 1
    latest = history["goals"][0]["latest_status_run"]
    assert latest["json_exists"] is True
    assert latest["markdown_exists"] is True

    decision = _run_cli(
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "quota",
        "should-run",
        "--goal-id",
        "half-speed",
        cwd=independent_worktree,
    )

    assert decision["should_run"] is True
    assert decision["normal_delivery_allowed"] is True
    assert decision["decision"] == "run"


def test_todo_list_anchors_promoted_authority_to_registry_project(
    tmp_path: Path,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(tmp_path / "fixture")
    target_runtime = _make_runtime_paths_project_relative(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
    )
    state_path = (
        project / ".codex" / "goals" / "half-speed" / "ACTIVE_GOAL_STATE.md"
    )
    state_path.write_text(
        "# half-speed\n\n"
        "## Agent Todo\n\n"
        "- [ ] Stale Markdown work\n"
        "  <!-- loopx:todo todo_id=todo_stale status=open -->\n",
        encoding="utf-8",
    )
    projection = build_todo_runtime_shadow_projection(
        goal_id="half-speed",
        todos=[
            {
                "schema_version": "todo_item_v0",
                "todo_id": "todo_canonical",
                "index": 1,
                "role": "agent",
                "status": "open",
                "done": False,
                "text": "Canonical provider work",
                "archive_state": "active",
                "source_section": "Agent Todo",
            }
        ],
    )
    initialize_canonical_authority(
        target_runtime,
        "half-speed",
        projection,
        state_path=state_path,
    )
    independent_worktree = tmp_path / "independent-worktree"
    independent_worktree.mkdir()

    payload = _run_cli(
        "--format",
        "json",
        "--registry",
        str(registry_path),
        "todo",
        "list",
        "--goal-id",
        "half-speed",
        cwd=independent_worktree,
    )

    assert [todo["todo_id"] for todo in payload["todos"]] == ["todo_canonical"]
    assert payload["authority_read"]["decision_read_from_provider"] is True
    assert payload["authority_read"]["legacy_fallback_used"] is False
    assert not (independent_worktree / ".loopx" / "runtime").exists()


def test_issue_fix_explore_projection_anchors_relative_runtime_to_registry_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    registry_path, runtime_root, project = write_cli_fixture(tmp_path / "fixture")
    target_runtime = _make_runtime_paths_project_relative(
        registry_path=registry_path,
        runtime_root=runtime_root,
        project=project,
    )
    result_log = explore_result_log_path(target_runtime, "half-speed")
    append_explore_result_event(
        result_log,
        build_explore_node_event(
            goal_id="half-speed",
            node_id="canonical_source_node",
            title="Canonical source node",
        ),
    )
    independent_worktree = tmp_path / "independent-worktree"
    independent_worktree.mkdir()
    monkeypatch.chdir(independent_worktree)

    projection = project_issue_fix_explore_graph(
        registry_path=registry_path,
        goal_id="half-speed",
    )

    assert projection["material_event_count"] == 0
    assert {
        node["node_id"] for node in projection["projection"]["nodes"]
    } == {"canonical_source_node"}
