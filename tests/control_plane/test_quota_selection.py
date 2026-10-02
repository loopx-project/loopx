"""Quota scope is distinct from execution ownership; all data are synthetic."""
import pytest
import json
from pathlib import Path

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

from loopx.control_plane.todos.quota_summary import summarize_user_todos_for_quota


def summary(items):
    return {"schema_version": "todo_summary_v0", "source_section": "User Todo",
            "items": items, "first_open_items": items, "total_count": len(items),
            "open_count": len(items), "done_count": 0, "deferred_count": 0}


def item(todo_id, **fields):
    return {"todo_id": todo_id, "text": "Synthetic scoped work", "index": 1,
            "priority": "P1", "status": "open", "task_class": "user_gate", **fields}


@pytest.mark.parametrize("scope", [{"global_gate": True}, {"blocks_agent": "agent-b"}])
@pytest.mark.parametrize("exclusions", [[], ["agent-b"]])
def test_explicit_user_gate_scope_is_not_erased_by_executor_claim(scope, exclusions):
    gate = item("todo_gate", claimed_by="agent-a", excluded_agents=exclusions, **scope)
    result = summarize_user_todos_for_quota(summary([gate]),
        agent_identity={"agent_id": "agent-b"}, filter_user_gate_blocks_agent=True)
    assert [row["todo_id"] for row in result["gate_open_items"]] == ["todo_gate"]
    assert result["open_count"] == 1
    assert result["first_executable_items"] == []


def test_scope_precedence_keeps_other_lane_gate_diagnostic():
    gate = item("todo_gate", claimed_by="agent-b", blocks_agent="agent-a")
    result = summarize_user_todos_for_quota(summary([gate]),
        agent_identity={"agent_id": "agent-b"}, filter_user_gate_blocks_agent=True)
    assert result["gate_open_items"] == []
    assert result["other_agent_scoped_open_count"] == 1
    assert result["open_count"] == 0


def test_agent_execution_still_respects_claim_and_exclusion():
    rows = [item("todo_peer", task_class="advancement_task", claimed_by="agent-a"),
            item("todo_excluded", task_class="advancement_task", excluded_agents=["agent-b"]),
            item("todo_free", task_class="advancement_task"),
            item("todo_owned", task_class="advancement_task", claimed_by="agent-b")]
    result = summarize_user_todos_for_quota(summary(rows), agent_identity={"agent_id": "agent-b"})
    assert [row["todo_id"] for row in result["first_executable_items"]] == ["todo_owned", "todo_free"]
    assert result["claim_scope"]["executor_excluded_self_count"] == 1
    assert result["claim_scope"]["other_agent_claimed_open_count"] == 1


@pytest.mark.parametrize("display", ["legacy", "missing", "stale"])
@pytest.mark.parametrize("scope", ["global_gate=true", "blocks_agent=agent-b"])
def test_public_quota_keeps_gate_from_real_canonical_provider(tmp_path: Path, display: str, scope: str):
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    history = "\n".join(f"- [x] [P2] Completed synthetic work {i}.\n"
        f"  <!-- loopx:todo todo_id=todo_history_{i} status=done task_class=advancement_task -->" for i in range(12))
    # A goal-wide gate cannot also bind continuation through a legacy agent claim.
    claim = " claimed_by=agent-a" if scope == "blocks_agent=agent-b" else ""
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nDeliver a checked change.\n\n"
        "## Agent Todo\n" + history + "\n\n## User Todo\n"
        "- [ ] [P0] Owner approval is required.\n"
        f"  <!-- loopx:todo todo_id=todo_gate task_class=user_gate status=open{claim} {scope} -->\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-scope", domain="quota-scope", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    if display != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        fields = parse_active_state_todos(state.read_text(), goal=goal, item_limit=None)
        projection = build_todo_runtime_shadow_projection(goal_id="goal-scope",
            todos=fields["agent_todos"]["items"] + fields["user_todos"]["items"], handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, "goal-scope", projection, state_path=state)
        if display == "missing":
            state.unlink()
        else:
            state.write_text("# Stale projection\n## User Todo\n- [x] Old approval.\n")
    before = state.read_bytes() if state.exists() else None
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-scope",
        "--agent-id", "agent-b", "--scan-path", str(tmp_path), registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    assert packet["requires_user_action"] is True
    gates = packet["user_todo_summary"]["gate_open_items"]
    assert [row["todo_id"] for row in gates] == ["todo_gate"]
    assert not packet["user_todo_summary"].get("claim_scope")
    assert (state.read_bytes() if state.exists() else None) == before
