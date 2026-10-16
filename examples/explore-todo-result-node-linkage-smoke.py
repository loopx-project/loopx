#!/usr/bin/env python3
"""Smoke-test explicit todo-to-Explore-node linkage and diagnostics."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.explore.todo_branch_plan import (  # noqa: E402
    build_explore_todo_branch_plan,
)
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402
from loopx.control_plane.todos.contract import format_todo_metadata_line  # noqa: E402


GOAL_ID = "explore-todo-linkage-smoke"
ORCHESTRATION = {
    "spawn_allowed": False,
    "max_children": 2,
    "explore_harness": {"enabled": True},
}


def run_loopx(registry: Path, *args: str) -> dict[str, object]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--format",
            "json",
            *args,
        ],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert completed.returncode == 0, (completed.stdout, completed.stderr)
    return json.loads(completed.stdout)


def write_fixture(root: Path, provider: str) -> tuple[Path, Path]:
    project = root / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "\n".join(
            [
                "# Explore Todo Linkage Smoke",
                "",
                "## User Todo / Owner Review Reading Queue",
                "",
                "## Agent Todo",
                "",
            ]
        ),
        encoding="utf-8",
    )
    registry = root / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "common_runtime_root": str(root / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(project),
                        "state_file": str(state_file),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    import_fixture_authority(registry, GOAL_ID, provider=provider)
    return registry, state_file


def check_todo_lifecycle(provider: str) -> None:
    try:
        format_todo_metadata_line(
            todo_id="todo_invalid123",
            explore_result_node_refs=["/private/result-node"],
        )
    except ValueError as exc:
        assert "public-safe Explore node ids" in str(exc), exc
    else:
        raise AssertionError("private path-shaped Explore node refs must fail closed")

    with tempfile.TemporaryDirectory(prefix="loopx-explore-todo-link-") as tmp:
        registry, state_file = write_fixture(Path(tmp), provider)
        added = run_loopx(
            registry,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "[P1] Follow explicit Explore evidence.",
            "--task-class",
            "advancement_task",
            "--explore-result-node-ref",
            "node_live",
            "--explore-result-node-ref",
            "node_dead",
        )
        todo_id = str(added["todo_id"])
        assert added["todo"]["explore_result_node_refs"] == ["node_live", "node_dead"], added

        listed = run_loopx(
            registry,
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
        )
        assert listed["matched"] is True, listed
        assert listed["todo_detail_projection"]["source_complete"] is True, listed
        item = listed["todo"]
        assert item["todo_id"] == todo_id, item
        assert item["explore_result_node_refs"] == ["node_live", "node_dead"], item

        completed = run_loopx(
            registry,
            "todo",
            "complete",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            todo_id,
            "--evidence",
            "typed evidence inspected",
            "--no-follow-up",
        )
        assert completed["status"] == "done", completed
        terminal = run_loopx(registry, "todo", "list", "--goal-id", GOAL_ID,
                             "--todo-id", todo_id)["todo"]
        assert terminal["status"] == "done"
        assert terminal["explore_result_node_refs"] == ["node_live", "node_dead"]
        state_text = state_file.read_text(encoding="utf-8")
        assert "explore_result_node_refs=node_live%2Cnode_dead" in state_text, state_text

        run_loopx(
            registry,
            "todo",
            "archive-completed",
            "--goal-id",
            GOAL_ID,
            "--max-active-done",
            "0",
            "--execute",
        )
        archived = state_file.read_text(encoding="utf-8")
        assert "Completed Work Archive" in archived, archived
        assert "explore_result_node_refs=node_live%2Cnode_dead" in archived, archived

        archived_item = run_loopx(registry, "todo", "list", "--goal-id", GOAL_ID,
                                  "--todo-id", todo_id)["todo"]
        assert archived_item["archive_state"] == "archive", archived_item
        assert archived_item["explore_result_node_refs"] == ["node_live", "node_dead"]

        second = run_loopx(
            registry,
            "todo",
            "add",
            "--goal-id",
            GOAL_ID,
            "--role",
            "agent",
            "--text",
            "[P2] Temporary evidence link.",
            "--explore-result-node-ref",
            "node_unknown",
        )
        run_loopx(
            registry,
            "todo",
            "update",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            str(second["todo_id"]),
            "--clear-explore-result-node-refs",
        )
        cleared = run_loopx(
            registry,
            "todo",
            "list",
            "--goal-id",
            GOAL_ID,
            "--todo-id",
            str(second["todo_id"]),
        )["todo"]
        assert cleared["explore_result_node_refs"] == [], cleared


def check_planner_diagnostics() -> None:
    projection = {
        "nodes": [
            {
                "node_id": "node_dead",
                "status": "dead_end",
                "node_kind": "hypothesis",
                "title": "Rejected route",
                "finding_count": 1,
            }
        ],
        "findings": [
            {
                "finding_id": "finding_refuted",
                "node_id": "node_dead",
                "status": "refuted",
                "confidence": 0.9,
                "finding": "The route does not satisfy the invariant.",
            }
        ],
        "edges": [
            {
                "edge_id": "edge_refutes",
                "from_node": "node_dead",
                "to_node": "node_baseline",
                "edge_type": "refutes",
                "confidence": 0.9,
            }
        ],
        "frontier": [],
        "stuck": [],
    }
    linked = {
        "todo_id": "todo_linked123",
        "index": 1,
        "status": "open",
        "text": "[P1] Evaluate a linked route.",
        "task_class": "advancement_task",
        "explore_result_node_refs": ["node_dead", "node_unknown"],
    }
    unlinked = {
        "todo_id": "todo_unlinked123",
        "index": 2,
        "status": "open",
        "text": "[P2] Keep ordinary behavior.",
        "task_class": "advancement_task",
    }
    plan = build_explore_todo_branch_plan(
        goal_id=GOAL_ID,
        todos=[linked, unlinked],
        projection=projection,
        orchestration=ORCHESTRATION,
        width=2,
    )
    candidates = {
        item["todo_id"]: item
        for item in [*plan["selected_branches"], *plan["rejected_candidates"]]
    }
    audit = candidates["todo_linked123"]["typed_evidence_audit"]
    assert audit["mode"] == "diagnostic_only" and audit["score_delta"] == 0.0, audit
    assert audit["unknown_node_refs"] == ["node_unknown"], audit
    assert set(audit["hazards"]) == {
        "unknown_result_node_ref",
        "linked_node_dead_end",
        "linked_finding_refuted",
        "refute_edge_present",
    }, audit
    assert audit["boundary"] == {
        "changes_score": False,
        "writes_state": False,
        "claims_todos": False,
        "acquires_leases": False,
        "starts_agents": False,
        "changes_quota": False,
    }, audit
    assert "typed_evidence_audit" not in candidates["todo_unlinked123"], candidates
    assert all(
        not item.get("suggested_commands")
        for item in [*plan["selected_branches"], *plan["rejected_candidates"]]
    ), plan


def main() -> int:
    for provider in ("file", "sqlite"):
        check_todo_lifecycle(provider)
    check_planner_diagnostics()
    print("explore-todo-result-node-linkage-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
