"""Full current-Task requirements survive composition, reference validation and recovery."""
from copy import deepcopy

import pytest

from loopx.control_plane.quota.cli_projection import _reference_context
from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result, build_turn_envelope, compose_turn_decision,
    prepare_context_delivery, restore_context_delivery,
)
from test_complete_turn_decision import _source_digest, _resign_contract
from test_replan_host_context_projection import _quota_payload


def packet():
    source = _quota_payload()
    body = "Keep the original acceptance, evidence and authorization. 完整要求🙂\n" * 100 + "Stop before publication."
    source["selected_todo"] = {"todo_id": "current-task"}
    source["agent_lane_next_action"] = {"todo_id": "current-task", "text": "Bounded label…", "title": body}
    source["interaction_contract"]["agent_channel"]["work_context"] = {
        "complete": True, "selected_todo_text_verified": True, "sources": [
            {"source": "selected_todo", "content": {"ok": True, "matched": True,
                "read_complete": True, "todo": {"todo_id": "current-task", "status": "open", "text": body}}}]}
    return source


def context(source):
    return source["interaction_contract"]["agent_channel"]["work_context"]


@pytest.mark.parametrize("field", ["text", "title"])
def test_exact_title_sharing_preserves_entire_task_in_complete_and_historical_readers(field):
    source = packet()
    body = source["agent_lane_next_action"]["title"]
    if field == "title":
        context(source)["sources"][0]["content"]["todo"].update(text="[P0] " + body, title=body)
    original = deepcopy(source)
    contract = compose_turn_decision(source)
    expected = {"todo_id": "current-task", "text": "Bounded label…", "title_ref":
        f"$.interaction_contract.agent_channel.work_context.sources[0].content.todo.{field}"}
    assert contract["agent_lane_next_action"] == expected
    assert _reference_context(source)["agent_lane_next_action"] == expected
    assert compose_turn_decision(contract) == contract and source == original
    assert contract["interaction_contract"] == source["interaction_contract"]
    assert contract["selected_todo"] == source["selected_todo"]
    envelope = build_turn_envelope(contract)
    assert envelope["work_context"]["sources"] == context(source)["sources"]
    assert envelope["action_signature"]["matches"] is True
    assert "agent_lane_next_action" not in envelope  # Historical view retains the full declaration in work context.


@pytest.mark.parametrize("mutation", ["dangling", "malformed", "both", "chain", "wrong-source", "duplicate",
    "wrong-id", "wrong-selection", "boolean-id", "incomplete", "unverified", "unmatched", "ambiguous",
    "partial", "failed", "archived", "done", "nonstring", "empty"])
def test_invalid_task_reference_is_refused_before_consumption_even_with_valid_integrity(mutation):
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    contract = compose_turn_decision(packet())
    envelope = build_turn_envelope(contract)
    lane = contract["agent_lane_next_action"]
    ctx = context(contract)
    src = ctx["sources"][0]
    content = src["content"]
    todo = content["todo"]
    if mutation == "dangling":
        lane["title_ref"] = lane["title_ref"].replace("[0]", "[99]")
    elif mutation == "malformed":
        lane["title_ref"] = {"path": lane["title_ref"]}
    elif mutation == "both":
        lane["title"] = todo["text"]
    elif mutation == "chain":
        todo["text_ref"] = "$.another"
    elif mutation == "wrong-source":
        src["source"] = "historical_todo"
    elif mutation == "duplicate":
        ctx["sources"].append(deepcopy(src))
    elif mutation == "wrong-id":
        todo["todo_id"] = "other-task"
    elif mutation == "wrong-selection":
        contract["selected_todo"]["todo_id"] = "other-task"
    elif mutation == "boolean-id":
        lane["todo_id"] = todo["todo_id"] = contract["selected_todo"]["todo_id"] = True
    elif mutation in {"incomplete", "unverified"}:
        ctx["complete" if mutation == "incomplete" else "selected_todo_text_verified"] = False
    elif mutation in {"unmatched", "partial", "failed"}:
        content[{"unmatched": "matched", "partial": "read_complete", "failed": "ok"}[mutation]] = False
    elif mutation == "ambiguous":
        content["ambiguous"] = True
    elif mutation == "archived":
        todo["archive_state"] = "archive"
    elif mutation == "done":
        todo["status"] = "done"
    else:
        todo["text"] = "" if mutation == "empty" else True
    contract, _ = _resign_contract(contract)
    for read in (lambda: compose_turn_decision(contract), lambda: build_turn_envelope(contract),
                 lambda: _prompt({"work_contract": contract, "turn_envelope": envelope}),
                 lambda: _turn_envelope_result("replan_view", payload=contract,
                     source_hash=_source_digest(contract), stage="review")):
        with pytest.raises(RuntimeError, match="Current Task title reference"):
            read()


@pytest.mark.parametrize("complete", [False, True])
def test_new_requirements_survive_delta_and_lost_baseline_recovery(complete):
    source = packet()
    before = compose_turn_decision(source)
    body = source["agent_lane_next_action"]["title"] + " New requirement: preserve failed evidence."
    source["agent_lane_next_action"]["title"] = body
    context(source)["sources"][0]["content"]["todo"]["text"] = body
    context(source)["complete"] = complete
    after = compose_turn_decision(source)
    scope = dict(goal_id="fixture", agent_id="worker", session_id="session", context_epoch="one")
    baseline = prepare_context_delivery(before, scope=scope, capture_id="one", surface="complete_turn")
    full = prepare_context_delivery(after, scope=scope, capture_id="two", surface="complete_turn")
    delta = prepare_context_delivery(after, scope=scope, capture_id="two", surface="complete_turn",
        baseline=baseline, retained={"complete": True, "target": baseline["target"]})
    assert delta["mode"] == ("delta" if complete else "full")
    for retained in (baseline, None):
        restored = restore_context_delivery(delta, baseline=retained, current=full)["context"]
        assert restored == after
        assert context(restored)["sources"][0]["content"]["todo"]["text"] == body
        assert build_turn_envelope(restored)["work_context"]["sources"] == context(source)["sources"]
