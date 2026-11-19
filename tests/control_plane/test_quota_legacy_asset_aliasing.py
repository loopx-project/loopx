"""Legacy source format and quota decisions cannot depend on object aliasing."""
from copy import deepcopy

import pytest

from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.todos import quota_summary
from loopx.quota import build_quota_should_run


def legacy_asset(*, next_only=False):
    counts = {"total": "3", "open": "1", "done": "2"}
    if next_only:
        return {**counts, "next": "Verify the synthetic result.", "next_index": "7"}
    return {**counts, "items": [{
        "todo_id": "todo_legacy_asset", "index": 7, "text": "Verify the synthetic result.",
        "status": "open", "priority": "P1", "task_class": "advancement_task",
    }]}


@pytest.mark.parametrize("role", ["user", "agent"])
@pytest.mark.parametrize("shared", [False, True])
@pytest.mark.parametrize("next_only", [False, True])
def test_composed_legacy_source_matches_independent_summary(role, shared, next_only):
    asset = legacy_asset(next_only=next_only)
    canonical = asset if shared else deepcopy(asset)
    before = deepcopy(asset)
    expected = quota_summary.select_quota_todo_summary(
        canonical, asset, filter_user_gate_blocks_agent=role == "user",
    )
    user, agent, _, _ = quota_summary.select_quota_todo_contexts(
        canonical if role == "user" else None, asset if role == "user" else None,
        canonical if role == "agent" else None, asset if role == "agent" else None,
        agent_identity=None, available_capabilities=None,
    )
    assert expected["total_count"] == "3" and expected["done_count"] == "2"
    assert (user if role == "user" else agent) == expected
    assert canonical == before and asset == before


def test_declared_canonical_source_keeps_precedence_over_legacy_asset():
    asset = legacy_asset()
    canonical = {**deepcopy(asset), "schema_version": "todo_summary_v0",
                 "source_section": "Agent Todo", "total_count": 9, "open_count": 1, "done_count": 8}
    expected = quota_summary.select_quota_todo_summary(canonical, asset)
    _, actual, _, _ = quota_summary.select_quota_todo_contexts(
        None, None, canonical, asset, agent_identity=None, available_capabilities=None,
    )
    assert actual == expected
    assert actual["total_count"] == 9 and actual["done_count"] == 8


@pytest.mark.parametrize("shared", [False, True])
def test_real_guard_preserves_legacy_counts_and_selected_work(shared, tmp_path):
    asset = legacy_asset()
    status = quota_status_payload(goal_id="legacy-asset-fixture", status="active",
        recommended_action="Verify the synthetic result.", agent_todos=asset,
        coordination={"registered_agents": ["agent-a"]})
    item = status["attention_queue"]["items"][0]
    item["agent_todos"] = asset if shared else deepcopy(asset)
    item["project_asset"]["agent_todos"] = asset
    result = build_quota_should_run(status, goal_id="legacy-asset-fixture", agent_id="agent-a",
        turn_instance_id="legacy-asset-turn", runtime_root=tmp_path)
    assert result["normal_delivery_allowed"] is True
    assert result["selected_todo"]["todo_id"] == "todo_legacy_asset"
    assert result["agent_todo_summary"]["total_count"] == "3"
    assert result["agent_todo_summary"]["done_count"] == "2"


@pytest.mark.parametrize("shared", [False, True])
def test_real_guard_refuses_legacy_next_without_a_todo_identity(shared, tmp_path):
    asset = legacy_asset(next_only=True)
    status = quota_status_payload(goal_id="legacy-asset-fixture", status="active",
        recommended_action="Verify the synthetic result.", agent_todos=asset,
        coordination={"registered_agents": ["agent-a"]})
    item = status["attention_queue"]["items"][0]
    item["agent_todos"] = asset if shared else deepcopy(asset)
    item["project_asset"]["agent_todos"] = asset
    with pytest.raises(ValueError, match="todo_id"):
        build_quota_should_run(status, goal_id="legacy-asset-fixture", agent_id="agent-a",
            turn_instance_id="legacy-asset-turn", runtime_root=tmp_path)
