"""A new successor may close only the exact frontier from which it was created."""
from copy import deepcopy

import pytest
from todo_frontier_fixture import summary_frontier_index

from loopx.control_plane.goals.goal_frontier.ack_policy import replan_successor_transition_ack
from loopx.control_plane.goals.goal_frontier.long_todo_chain import evaluate_long_todo_chain
from loopx.control_plane.work_items.autonomous_replan_obligation import ensure_replan_novelty_policy


def obligation(rows):
    observation, _ = evaluate_long_todo_chain(
        agent_todo_summary={"current_agent_claimed_open_count": len(rows)},
        agent_counts={}, frontier_counts={"current_agent_claimed_advancement_count": len(rows)},
        agent_id="worker-a", agent_todo_source_items=rows)
    assert observation is not None
    return ensure_replan_novelty_policy({"schema_version": "autonomous_replan_obligation_v0",
        "agent_id": "worker-a", "triggers": [observation.trigger]})


@pytest.mark.parametrize("indexed", [False, True])
@pytest.mark.parametrize("mutation", [None, "wrong-origin", "material-edit", "stale", "ambiguous", "truncated"])
def test_successor_proves_exact_predecessor_before_closing(indexed, mutation):
    rows = [{"todo_id": f"todo_owned_{i:03}", "text": "Synthetic work", "status": "open",
             "done": False, "task_class": "advancement_task", "claimed_by": "worker-a",
             "updated_at": "2026-08-01T00:00:00Z"} for i in range(15)]
    origin = obligation(rows)
    successor = {**rows[0], "todo_id": "todo_successor", "action_kind": "validate",
                 "target_key": "artifact-adoption", "updated_at": "2026-08-02T00:00:00Z",
                 "replan_obligation_id": origin["obligation_id"]}
    rows.append(successor)
    if mutation == "wrong-origin":
        successor["replan_obligation_id"] = "replan-0123456789abcdef"
    elif mutation == "material-edit":
        rows[0]["text"] = "A different acceptance condition"
    elif mutation == "stale":
        rows[0]["updated_at"] = "2026-08-03T00:00:00Z"
    elif mutation == "ambiguous":
        rows.append({**successor, "todo_id": "todo_other_successor"})
    current = obligation(rows)
    summary = {"advancement_frontier_revision_index": summary_frontier_index(rows)} if indexed else {}
    source = deepcopy(rows)
    if mutation == "truncated":
        source.pop(0)
    ack = replan_successor_transition_ack(summary, agent_id="worker-a",
        replan_obligation=current, agent_todo_items=source)
    if mutation:
        assert ack is None
    else:
        delta = ack["semantic_delta"]
        assert delta["obligation_id"] == current["obligation_id"]
        assert delta["successor_origin_obligation_id"] == origin["obligation_id"]
        assert delta["successor_todo_id"] == successor["todo_id"]
        assert delta["trigger_checkpoints"][0]["frontier_owned_identity"]


def test_planning_compaction_preserves_material_frontier_bytes():
    from loopx.control_plane.todos.frontier_revision import frontier_source_facts
    from loopx.control_plane.todos.summary_item import todo_planning_source_items
    rows = [{"todo_id": "todo_with_dependency", "text": "Validate adoption", "done": False,
             "status": "open", "task_class": "advancement_task", "claimed_by": "worker-a",
             "depends_on_todo_ids": ["todo_dependency"], "updated_at": "2026-08-01T00:00:00Z"}]
    planned = todo_planning_source_items({"items": rows}, include_terminal=True)
    assert frontier_source_facts(planned) == frontier_source_facts(rows)
