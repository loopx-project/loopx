"""Closure is a property of the source graph, not the selected display rows."""
from __future__ import annotations

import pytest

from loopx.control_plane.todos.todo_summary import compact_todo_group
from loopx.control_plane.todos.goal_todo_projection import filtered_todo_summary
from loopx.control_plane.todos.handoff_gate import build_todo_handoff_gate_states


def work(todo_id, **fields):
    return {"todo_id": todo_id, "text": "Deliver a verified result", "role": "agent",
            "status": "done", "done": True, "archive_state": "active",
            "task_class": "advancement_task", "claimed_by": "agent-a", **fields}


def summary(items, **options):
    return compact_todo_group(items, source_section="Agent Todo", role="agent", item_limit=None, **options)


@pytest.mark.parametrize("successors", [["todo_missing"], ["todo_source"]])
def test_unresolved_or_self_reference_does_not_certify_closure(successors):
    result = summary([work("todo_source", successor_todo_ids=successors)])
    assert result.get("completed_without_successor_count") == 1
    assert "terminal_closure_proof" not in result


def test_filtered_done_source_keeps_its_inferred_open_successor():
    result = summary([work("todo_source"), work("todo_next", status="open", done=False,
        resume_when="todo_done:todo_source")])
    assert not result.get("completed_without_successor_count")
    selected = filtered_todo_summary(result, role="agent", todo_id="todo_source")
    assert not selected.get("completed_without_successor_count")


def test_archived_successor_remains_relationship_evidence():
    source = work("todo_source")
    archived = work("todo_next", archive_state="archive", resume_when="todo_done:todo_source", no_followup=True)
    result = summary([source], resume_source_items=[source, archived])
    assert not result.get("completed_without_successor_count")


def test_explicit_successor_is_shared_by_handoff_and_completion():
    gate = work("todo_gate", excluded_agents=["agent-b"], unblocks_todo_id="todo_work",
                successor_todo_ids=["todo_next"])
    next_item = work("todo_next", status="open", done=False)
    result = build_todo_handoff_gate_states([gate, next_item])
    assert result[0]["gate_state"] == "cleared_with_successor"
    assert result[0]["successor_todo_ids"] == ["todo_next"]


def test_missing_supersession_does_not_clear_blocking_handoff():
    gate = work("todo_gate", status="open", done=False, excluded_agents=["agent-b"],
                unblocks_todo_id="todo_work", superseded_by="todo_missing")
    assert build_todo_handoff_gate_states([gate])[0]["gate_state"] == "blocking"


def test_query_subset_does_not_certify_goal_closure():
    result = summary([work("todo_closed", no_followup=True), work("todo_open", status="open", done=False)])
    selected = filtered_todo_summary(result, role="agent", status="done")
    assert selected["total_count"] == 1
    assert "terminal_closure_proof" not in selected
    assert "source_proof" not in selected


def test_selection_after_warning_display_cap_keeps_exact_gap():
    records = [work(f"todo_work_{index:03}", index=index) for index in range(80)]
    result = summary(records)
    assert result["completed_without_successor_count"] == 80
    assert len(result["completed_without_successor_items"]) < 80
    selected = filtered_todo_summary(result, role="agent", todo_id="todo_work_000")
    assert selected["completed_without_successor_count"] == 1


def test_changed_item_cannot_reuse_old_graph_evaluation():
    result = summary([work("todo_closed", no_followup=True)])
    result["items"][0]["no_followup"] = False
    with pytest.raises(Exception, match="matching full-source"):
        filtered_todo_summary(result, role="agent", todo_id="todo_closed")


def test_explicit_route_flag_is_not_overridden_by_legacy_prose_hint():
    gate = work("todo_gate", status="open", done=False, text="stale handoff closeout",
        excluded_agents=["agent-b"], unblocks_todo_id="todo_work", route_continuation_replan_required=False)
    result = build_todo_handoff_gate_states([gate])[0]
    assert result["route_continuation_replan_required"] is False
    assert result["gate_state"] == "blocking"


def test_archived_identity_can_be_recreated_but_duplicate_active_authority_rejects():
    source = work("todo_source", no_followup=True)
    result = summary([source], resume_source_items=[source, {**source, "archive_state": "archive"}])
    assert result["terminal_closure_proof"]["all_todos_done"] is True
    with pytest.raises(Exception, match="duplicate succession identity"):
        summary([source], resume_source_items=[source, dict(source)])


def test_public_summary_drops_internal_evaluation_without_mutating_source():
    from loopx.control_plane.todos.succession_warning import public_todo_summary

    source = summary([work("todo_source", no_followup=True)])
    public = public_todo_summary(source)
    assert "succession_evaluation" not in public["items"][0]
    assert "succession_evaluation" not in source["items"][0]
    assert filtered_todo_summary(source, role="agent", todo_id="todo_source")["total_count"] == 1
    assert public["terminal_closure_proof"] == source["terminal_closure_proof"]


def test_public_parser_rows_do_not_carry_internal_evaluations():
    from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

    state = "## Agent Todo\n\n- [ ] Work\n  <!-- loopx:todo todo_id=todo_source status=open -->\n"
    fields = parse_active_state_todos(state, item_limit=None)
    assert fields["agent_todos"]["items"]
    assert all("succession_evaluation" not in row for row in fields["agent_todos"]["items"])


def test_evaluation_does_not_mutate_or_expand_input_rows():
    import json
    from loopx.control_plane.todos.succession_warning import evaluate_succession

    original = work("todo_source", no_followup=True)
    rows = [original]
    before = json.dumps(rows, sort_keys=True)
    evaluate_succession(rows)
    assert rows[0] is not original
    assert json.dumps(rows, sort_keys=True) == before
    assert "succession_evaluation" not in original


@pytest.mark.parametrize("mutation", ["columns", "row_width", "cardinality"])
def test_succession_wire_response_cannot_silently_rebind_fields(monkeypatch, mutation):
    from loopx.control_plane.todos import succession_warning as owner
    columns = list(owner.SUCCESSION_EVALUATION_COLUMNS)
    value = ["todo_succession_evaluation_v0", "hash", [], [], True, True, None]
    response = {"schema_version": "todo_succession_result_v1", "evaluation_columns": columns,
                "evaluations": [value]}
    if mutation == "columns":
        response["evaluation_columns"] = list(reversed(columns))
    elif mutation == "row_width":
        response["evaluations"] = [value[:-1]]
    else:
        response["evaluations"] = []
    monkeypatch.setattr(owner, "effect_runtime_result", lambda *a, **kw: response)
    with pytest.raises(ValueError, match="typed Todo succession"):
        owner.project_succession([work("todo_source")])
