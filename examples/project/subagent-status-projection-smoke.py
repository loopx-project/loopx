#!/usr/bin/env python3
"""Smoke-test compact sub-agent run-history/status projection."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx.presentation.renderers.status_markdown import render_status_markdown  # noqa: E402
from loopx.status import (  # noqa: E402
    collect_status,
    project_asset_summary_is_public_safe,
)


GOAL_ID = "multi-subagent-controller"
PRIVATE_LOCAL_PATH = "/" + "Users/example/private.txt"


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    registry_path = project / ".loopx" / "registry.json"
    run_dir = runtime / "goals" / GOAL_ID / "runs"

    (project / Path(state_file).parent).mkdir(parents=True, exist_ok=True)
    (project / state_file).write_text(
        "---\n"
        "status: active-read-only\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Multi Subagent Controller\n\n"
        "## Agent Todo\n\n"
        "- [ ] Observe child run projection before launching more children.\n",
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
                        "domain": "subagent-smoke",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "harness_self_improvement",
                            "status": "connected-read-only",
                        },
                        "spawn_policy": {
                            "mode": "multi_subagent",
                            "allowed": True,
                            "max_children": 3,
                            "allowed_domains": ["docs", "validation", "implementation"],
                        },
                        "quota": {"compute": 1.0},
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

    run_dir.mkdir(parents=True, exist_ok=True)
    runs = [
        {
            "generated_at": "2026-01-01T00:02:00+00:00",
            "run_id": "controller-run-001",
            "goal_id": GOAL_ID,
            "classification": "ready_for_parallel_probe",
            "recommended_action": "merge child evidence, then choose one bounded implementation slice",
            "subagents": [
                {
                    "run_id": "docs-map-001",
                    "goal_id": "docs-map-subagent",
                    "parent_run_id": "controller-run-001",
                    "spawned_by_goal_id": GOAL_ID,
                    "agent_role": "explorer",
                    "work_scope": ["docs/**"],
                    "touched_paths": ["docs/status-data-contract.md", PRIVATE_LOCAL_PATH],
                    "result_status": "completed",
                    "quota_slots": 1,
                    "handoff_summary": "Mapped status docs and found the projection surface.",
                },
                {
                    "run_id": "validation-001",
                    "goal_id": "validation-subagent",
                    "parent_run_id": "controller-run-001",
                    "spawned_by_goal_id": GOAL_ID,
                    "agent_role": "validator",
                    "work_scope": ["examples/**"],
                    "result_status": "running",
                    "quota_event": {"slots": 1},
                    "handoff_summary": "Running focused smoke coverage.",
                },
            ],
            "merge_decision": "accepted docs-map evidence; validator still running",
        },
        {
            "generated_at": "2026-01-01T00:01:00+00:00",
            "run_id": "implementation-001",
            "goal_id": GOAL_ID,
            "parent_run_id": "controller-run-001",
            "spawned_by_goal_id": GOAL_ID,
            "agent_role": "implementation",
            "classification": "subagent_progress",
            "result_status": "queued",
            "work_scope": ["loopx/status.py"],
            "quota_spend": 2,
            "handoff_summary": "Waiting for controller merge decision before writing.",
        },
    ]
    with (run_dir / "index.jsonl").open("w", encoding="utf-8") as f:
        for run in runs:
            f.write(json.dumps(run, ensure_ascii=False, sort_keys=True) + "\n")
    return registry_path, runtime


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-subagent-status-") as tmp:
        registry_path, runtime = write_fixture(Path(tmp))
        payload = collect_status(
            registry_path=registry_path,
            runtime_root_override=str(runtime),
            limit=5,
            include_goal_subagent_configuration=True,
        )
    markdown = render_status_markdown(payload)
    goal = payload["run_history"]["goals"][0]
    activity = goal["subagent_activity"]
    queue_item = payload["attention_queue"]["items"][0]
    project_asset = queue_item["project_asset"]
    asset_activity = project_asset["subagent_activity"]
    latest = goal["latest_runs"][0]

    assert payload["ok"] is True, payload
    assert goal["spawn_policy"] == {
        "mode": "multi_subagent",
        "spawn_allowed": True,
        "max_children": 3,
        "allowed_domains": ["docs", "validation", "implementation"],
    }, goal
    assert activity["source"] == "run_history", activity
    assert activity["parent_goal_id"] == GOAL_ID, activity
    assert activity["child_count"] == 3, activity
    assert activity["visible_child_count"] == 3, activity
    assert activity["completed_count"] == 1, activity
    assert activity["active_count"] == 2, activity
    assert activity["quota_spend_slots"] == 4, activity
    assert asset_activity == activity, (asset_activity, activity)
    assert project_asset_summary_is_public_safe(project_asset), project_asset
    assert latest["subagent_count"] == 2, latest
    assert latest["subagents"][0]["run_id"] == "docs-map-001", latest
    assert latest["subagents"][0]["parent_run_id"] == "controller-run-001", latest
    assert latest["subagents"][0]["touched_paths"] == ["docs/status-data-contract.md"], latest
    assert PRIVATE_LOCAL_PATH not in json.dumps(activity, ensure_ascii=False), activity
    assert "subagent_activity: children=3 visible=3 active=2 completed=1 quota_slots=4" in markdown, markdown
    assert "child_run: role=explorer state=completed run_id=docs-map-001 parent_run_id=controller-run-001" in markdown, markdown
    assert "child_run: role=implementation state=queued run_id=implementation-001 parent_run_id=controller-run-001" in markdown, markdown

    print("subagent-status-projection-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
