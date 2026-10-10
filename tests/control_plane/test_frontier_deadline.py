import json
import subprocess
import sys
from datetime import UTC, datetime

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

import pytest

from loopx.control_plane.todos.frontier_deadline import (
    build_frontier_recheck_plan,
    todo_summary_frontier_deadline,
)

NOW = datetime(2026, 10, 1, tzinfo=UTC)


def projected(due: str, identity: str) -> dict:
    return {"frontier_deadline": {
        "schema_version": "todo_frontier_deadline_v0", "next_due_at": due,
        "identity": identity, "source": "user_gate", "candidate_count": 1,
    }}


@pytest.mark.parametrize("agent_due,user_due", [
    ("2026-10-01T08:01:00+08:00", "2026-10-01T00:02:00Z"),
    ("2026-09-30T20:01:00-04:00", "2026-10-01T01:02:00+01:00"),
])
def test_recheck_compares_projected_deadlines_by_instant(agent_due, user_due):
    result = build_frontier_recheck_plan({
        "agent_todo_summary": projected(agent_due, "earlier"),
        "user_todo_summary": projected(user_due, "later"),
    }, current_time=NOW)
    assert result == {"frontier_recheck_after_seconds": 60,
        "frontier_recheck_source": "user_gate", "frontier_recheck_identity": "earlier",
        "frontier_recheck_candidate_count": 2}


def test_deadline_keeps_dedup_expiry_identity_and_source_compatibility():
    early = {"todo_id": "todo_early", "next_due_at": "2026-10-01T00:01:00.000001Z"}
    summary = {
        "current_agent_claimed_monitor_items": [
            {"todo_id": "expired", "expires_at": "2026-10-01T00:00:00Z",
             "next_due_at": "2026-10-01T00:00:01Z"},
            {"todo_id": "due", "next_due_at": "2026-10-01T00:00:00Z"}, early,
        ],
        "monitor_open_items": [early, None],
        "gate_open_items": [{"target_key": "later", "next_due_at": "2026-10-01T00:02:00Z"}],
        "deferred_resume_candidates": [{"index": 0, "next_due_at": "bad"}],
    }
    assert todo_summary_frontier_deadline(summary, current_time=NOW) == {
        "schema_version": "todo_frontier_deadline_v0", "identity": "todo_early",
        "source": "continuous_monitor", "next_due_at": "2026-10-01T00:01:00.000001+00:00",
        "candidate_count": 2,
    }
    assert build_frontier_recheck_plan({"agent_todo_summary": summary}, current_time=NOW)[
        "frontier_recheck_after_seconds"] == 61


@pytest.mark.parametrize("summary", [None, [], {}, {"monitor_open_items": "bad"}])
def test_absent_deadline_does_not_create_a_wakeup(summary):
    assert todo_summary_frontier_deadline(summary, current_time=NOW) is None
    assert build_frontier_recheck_plan({"agent_todo_summary": summary}, current_time=NOW) is None


def test_future_projection_preserves_extra_fields_and_stale_projection_falls_back():
    summary = projected("2026-10-01T00:01:00Z", "cached")
    summary["frontier_deadline"]["extension"] = {"retained": True}
    assert todo_summary_frontier_deadline(summary, current_time=NOW) == summary["frontier_deadline"]
    summary["frontier_deadline"]["next_due_at"] = "2026-09-30T00:01:00Z"
    summary["current_agent_handoff_gates"] = [
        {"index": 0, "task_class": "user_action", "next_due_at": "2026-10-01T00:02:00Z"}]
    assert todo_summary_frontier_deadline(summary, current_time=NOW)["identity"] == "0"
    assert todo_summary_frontier_deadline(summary, current_time=NOW)["source"] == "user_action"


@pytest.mark.parametrize("user_gate_scope", [False, True])
def test_quota_planning_keeps_gate_deadline_before_visibility_limits(monkeypatch, user_gate_scope):
    from loopx.control_plane.todos import quota_summary
    from loopx.control_plane.todos import quota_selection
    monkeypatch.setattr(quota_selection, "now_utc", lambda: NOW)
    gates = [{"todo_id": f"gate_{i}", "index": i + 1, "text": "Review the source",
              "status": "open", "task_class": "user_gate", "blocks_agent": "agent-a",
              "next_due_at": "2026-10-01T00:10:00Z"} for i in range(25)]
    gates[-1]["next_due_at"] = "2026-10-01T08:01:00.000001+08:00"
    gates.append({**gates[-1], "todo_id": "other_lane", "blocks_agent": "agent-b",
                  "next_due_at": "2026-10-01T00:00:01Z"})
    # Agent summaries use execution claims, User summaries use gate addressing.
    if not user_gate_scope:
        for gate in gates[:-1]:
            gate["claimed_by"] = "agent-a"
        gates[-1]["claimed_by"] = "agent-b"
    original = json.loads(json.dumps(gates))
    crossings = []
    invoke = quota_selection.effect_runtime_result
    def observed(method, params):
        crossings.append(method)
        return invoke(method, params)
    monkeypatch.setattr(quota_selection, "effect_runtime_result", observed)
    summary = quota_summary.summarize_user_todos_for_quota(
        {"schema_version": "todo_summary_v0", "items": gates, "total_count": 26,
         "open_count": 26, "done_count": 0, "source_section": "User Todo"},
        agent_identity={"agent_id": "agent-a"}, filter_user_gate_blocks_agent=user_gate_scope)
    assert len(summary["gate_open_items"]) == 3
    monkeypatch.setattr("loopx.control_plane.runtime.time.now_utc", lambda: NOW)
    compact = quota_summary.compact_quota_todo_summary_for_payload(summary)
    assert compact["frontier_deadline"] == {
        "schema_version": "todo_frontier_deadline_v0", "identity": "gate_24",
        "source": "user_gate", "next_due_at": "2026-10-01T00:01:00.000001+00:00",
        "candidate_count": 25}
    assert gates == original
    assert crossings == ["todo.quota_planning.project"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("role", ["agent", "user"])
def test_installed_cli_keeps_full_source_deadline_after_compaction(tmp_path, monkeypatch, provider, role):
    from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    state.write_text("---\nstatus: active-read-only\nowner_mode: goal\nobjective: Observe authorized sources.\n---\n\n# Goal\n\n## Agent Todo\n")
    records = [{"todo_id": f"todo_monitor_{index:02}", "schema_version": "todo_item_v0",
        "role": "agent", "text": "Observe the authorized source", "status": "open", "done": False,
        "archive_state": "active", "source_section": "Agent Todo", "index": index + 1,
        "task_class": "continuous_monitor", "claimed_by": "agent-a", "watch_only": True,
        "target_key": f"source-{index}", "cadence": "60m", "next_due_at": "2099-01-01T00:10:00Z",
    } for index in range(24)]
    records[-1]["next_due_at"] = "2099-01-01T08:01:00+08:00"
    records.append({"todo_id": "todo_work", "schema_version": "todo_item_v0", "role": "agent",
        "text": "Validate remaining authorized work.", "status": "open", "done": False,
        "archive_state": "active", "source_section": "Agent Todo", "task_class": "advancement_task",
        "claimed_by": "agent-a", "action_kind": "validate", "priority": "P2"})
    if role == "user":
        for record in records[:-1]:
            record.update(role="user", task_class="user_gate", source_section="User Todo",
                          blocks_agent="agent-a")
            record.pop("watch_only", None)
    projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=records, handoff_mode="soft_claim")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": "goal-a", "domain": "deadline-fixture", "repo": str(tmp_path), "state_file": state.name, "status": "active-read-only",
        "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
        "coordination": {"registered_agents": ["agent-a"], "handoff_mode": "soft_claim"},
    }]}))
    initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
    state_before = state.read_bytes()
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    child = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
        "quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
        "--runtime-profile", "ark_managed_agent_goal", "--include-detail", "scheduler"],
        capture_output=True, text=True, timeout=60)
    assert child.returncode == 0, child.stdout or child.stderr
    packet = json.loads(child.stdout)
    summary = packet[f"{role}_todo_summary"]
    assert summary["frontier_deadline"]["identity"] == "todo_monitor_23"
    assert summary["frontier_deadline"]["next_due_at"] == "2099-01-01T00:01:00+00:00"
    if role == "agent":
        assert "monitor_open_items" not in summary  # CLI omits this diagnostic lane entirely.
    else:
        assert all(row["todo_id"] != "todo_monitor_23" for row in summary["gate_open_items"])
    assert packet["scheduler_hint"]["cold_path_detail"]["frontier_recheck"][
        "frontier_recheck_identity"] == "todo_monitor_23"
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
    assert state.read_bytes() == state_before
