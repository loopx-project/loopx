"""Acceptance holds come from canonical TS authority, including public CLI reads."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

from canonical_authority_fixture import initialize_canonical_authority

from loopx.control_plane.coordination.local_authority import read_canonical_todo_fields_if_promoted
from loopx.control_plane.coordination.local_authority_shadow_projection import canonical_bytes
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.todos.summary_item import todo_summary_source_items
from loopx.control_plane.todos.todo_semantics import todo_item_is_actionable_open
from loopx.quota import build_quota_should_run
from loopx.status import active_state_todo_fields


def seed(tmp_path: Path, *, enabled: bool, monitor: bool = False):
    state = tmp_path / "state.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n- [ ] Stale display work\n")
    runtime = tmp_path / "runtime"
    goal = {"id": "goal-a", "repo": str(tmp_path), "state_file": str(state),
            "domain": "software", "adapter": {"kind": "read_only_project_map_v0"}}
    records = [{"schema_version": "todo_item_v0", "todo_id": "todo_work", "index": 1,
                "role": "agent", "status": "open", "done": False, "text": "Configured work",
                "task_class": "advancement_task", "archive_state": "active", "source_section": "Agent Todo"}]
    if monitor:
        records.append({**records[0], "todo_id": "todo_monitor", "index": 2, "text": "Inspect current progress",
                        "task_class": "continuous_monitor", "next_due_at": "2026-01-01T00:00:00Z", "cadence": "1h"})
    projection = build_todo_runtime_shadow_projection(goal_id=goal["id"], todos=records)
    if enabled:
        document = {"objective": "Deliver a verified result", "non_goals": [], "bindings": [],
                    "criteria": [{"id": "criterion-a", "description": "The check passes", "validation_argv": ["true"],
                                  "validation_timeout_seconds": 5, "validation_files": []}]}
        projection["goal_acceptance"] = {"schema_version": "loopx_goal_acceptance_v0", "enabled": True,
            "revision": 1, "digest": hashlib.sha256(canonical_bytes(document)).hexdigest(),
            "document": document, "bindings": [], "verification": None}
    initialize_canonical_authority(runtime, goal["id"], projection, state_path=state)
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [goal]}))
    return goal, runtime, state, registry


def test_same_provider_snapshot_keeps_holds_visible_and_excludes_selection(tmp_path):
    goal, runtime, state, _ = seed(tmp_path, enabled=True, monitor=True)
    state.unlink()
    fields = read_canonical_todo_fields_if_promoted(runtime_root=runtime, goal_id=goal["id"])
    summary = fields["agent_todos"]
    assert summary["goal_acceptance_contract"]["status"] == "held"
    assert summary["first_executable_items"] == []
    assert summary["executable_backlog_items"] == []
    visible = {item["todo_id"]: item for item in todo_summary_source_items(summary)}
    held = visible["todo_work"]
    assert held["status"] == "open"
    assert held["goal_acceptance_guard"]["reason_code"] == "goal_acceptance_unbound"
    assert held["goal_acceptance_guard"]["reason"]
    assert not todo_item_is_actionable_open(held)
    assert todo_item_is_actionable_open(visible["todo_monitor"])
    assert "goal_acceptance_guard" not in visible["todo_monitor"]
    assert "validation_argv" not in json.dumps(fields)
    assert not state.exists()


def test_quota_cannot_select_unbound_work(tmp_path):
    goal, runtime, _, _ = seed(tmp_path, enabled=True)
    fields = active_state_todo_fields(goal, runtime_root=runtime)
    status = quota_status_payload(goal_id=goal["id"], status="ready", agent_todos=fields["agent_todos"],
                                  user_todos=fields["user_todos"], recommended_action="Configured work")
    decision = build_quota_should_run(status, goal_id=goal["id"])
    assert decision["should_run"] is False
    assert "goal_acceptance_unbound" in json.dumps(status)


def test_public_status_cli_retains_safe_contract_sidecar_without_extra_source(tmp_path):
    goal, runtime, state, registry = seed(tmp_path, enabled=True)
    state.unlink()
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--format", "json", "status", "--goal-id", goal["id"]], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    status = json.loads(result.stdout)
    items = status["attention_queue"]["items"]
    summaries = [item for item in items if item["goal_id"] == goal["id"] and "agent_todos" in item]
    assert len(summaries) == 1, items
    item = summaries[0]
    contract = item["agent_todos"]["goal_acceptance_contract"]
    assert contract["status"] == "held"
    assert contract["criteria"] == [{"id": "criterion-a", "description": "The check passes"}]
    assert item["agent_todos"]["first_executable_items"] == []
    assert "validation_argv" not in json.dumps(contract)
    assert any(row["goal_id"] == goal["id"] and row["status"] == "state_file_missing" for row in items)
    decision = build_quota_should_run(status, goal_id=goal["id"])
    assert decision["status_health_ok"] is False
    assert decision["should_run"] is False
    assert decision["normal_delivery_allowed"] is False
    assert not state.exists()


def test_absent_acceptance_does_not_change_summary_shape_or_executability(tmp_path):
    goal, runtime, _, _ = seed(tmp_path, enabled=False)
    summary = active_state_todo_fields(goal, runtime_root=runtime)["agent_todos"]
    assert "goal_acceptance_contract" not in summary
    assert summary["first_executable_items"][0]["todo_id"] == "todo_work"
    assert "goal_acceptance_guard" not in json.dumps(summary)


def test_public_todo_list_counts_and_selection_share_acceptance_guard(tmp_path):
    from loopx.todos import list_goal_todos
    from loopx.control_plane.todos.todo_semantics import todo_summary_open_task_counts

    goal, runtime, state, registry = seed(tmp_path, enabled=True, monitor=True)
    state.unlink()
    result = list_goal_todos(registry_path=registry, runtime_root_arg=str(runtime), goal_id=goal["id"])
    summary = result["agent_todos"]
    assert summary["first_executable_items"] == []
    assert todo_summary_open_task_counts(summary)["advancement"] == 0
    assert todo_summary_open_task_counts(summary)["monitor"] == 1
    assert "validation_argv" not in json.dumps(result)
    assert not state.exists()
