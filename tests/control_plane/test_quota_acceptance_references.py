"""Display sharing preserves the complete acceptance and executable decisions."""
from copy import deepcopy
import json

import pytest

from loopx.control_plane.quota.cli_projection import (
    _reference_inline_goal_acceptance, compact_quota_should_run_cli_payload,
)
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope, quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from test_replan_host_context_projection import _quota_payload


def acceptance():
    return {"revision": 3, "scope": {"kind": "all_advancement"}, "status": "failed",
        "requirements": [{"id": "original", "text": "Keep the complete original requirement. " * 20},
                         {"id": "peer", "text": "Preserve another agent's requirement and stop."}],
        "sources": [{"ref": "message:original", "summary": "Original owner instruction"}],
        "verification": {"passed": False, "failures": ["Recovery is still unproven."]}}


def packet(with_context=True):
    source = _quota_payload()
    contract = acceptance()
    source["planning_horizon"] = {"core_goal": {"objective": "Deliver the whole outcome",
                                               "acceptance_contract": deepcopy(contract)}}
    source["autonomous_replan_obligation"]["replan_context"]["core_goal"] = {
        "objective": "Deliver the whole outcome", "acceptance_contract": deepcopy(contract)}
    source["agent_todo_summary"]["goal_acceptance_contract"] = deepcopy(contract)
    if with_context:
        source["interaction_contract"]["agent_channel"]["work_context"] = {"complete": True,
            "sources": [{"source": "goal_acceptance", "content": {"status": "loaded",
                "provider_revision": "synthetic:3", "goal_acceptance_contract": deepcopy(contract)}}]}
    return source


@pytest.mark.parametrize("with_context", [False, True])
def test_exact_copies_share_one_complete_source_without_changing_signed_action(with_context):
    source = packet(with_context)
    original = deepcopy(source)
    shared = _reference_inline_goal_acceptance(source)
    assert source == original
    assert _reference_inline_goal_acceptance(shared) == shared
    ref = ("$.interaction_contract.agent_channel.work_context.sources[0].content.goal_acceptance_contract"
           if with_context else "$.planning_horizon.core_goal.acceptance_contract")
    assert shared["agent_todo_summary"]["goal_acceptance_contract_ref"] == ref
    core = shared["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert core == {"objective": "Deliver the whole outcome", "acceptance_contract_ref": ref}
    if with_context:
        assert shared["planning_horizon"]["core_goal"]["acceptance_contract_ref"] == ref
        retained = shared["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["goal_acceptance_contract"]
    else:
        retained = shared["planning_horizon"]["core_goal"]["acceptance_contract"]
    assert retained == acceptance()
    assert len(json.dumps(shared)) < len(json.dumps(source)) - len(json.dumps(retained))
    assert quota_action_signature_document(shared) == quota_action_signature_document(source)
    assert turn_envelope_action_signature_document(build_turn_envelope(shared)) == quota_action_signature_document(source)
    # Default CLI really invokes sharing, including with diagnostic detail flags.
    for options in ({}, {"include_todo_summary_detail": True, "include_vision_detail": True}):
        assert compact_quota_should_run_cli_payload(source, **options)["agent_todo_summary"]["goal_acceptance_contract_ref"] == ref


@pytest.mark.parametrize("change", ["revision", "scope", "failure", "type", "source"])
def test_different_contracts_are_retained_in_full(change):
    source = packet(False)
    changed = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"]
    if change == "revision":
        changed["revision"] = 4
    if change == "scope":
        changed["scope"] = {"kind": "selected_work"}
    if change == "failure":
        changed["verification"]["failures"] += ["New failure"]
    if change == "type":
        changed["verification"]["passed"] = 0  # False == 0 in Python
    if change == "source":
        changed["sources"][0]["ref"] = "message:corrected"
    shared = _reference_inline_goal_acceptance(source)
    assert shared["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"] == changed
    assert "acceptance_contract_ref" not in shared["autonomous_replan_obligation"]["replan_context"]["core_goal"]


@pytest.mark.parametrize("missing", [None, {}, [], "unavailable"])
def test_missing_or_malformed_carriers_never_create_dangling_references(missing):
    source = packet()
    source["interaction_contract"]["agent_channel"]["work_context"] = missing
    source["planning_horizon"] = missing
    source["autonomous_replan_obligation"] = missing
    shared = _reference_inline_goal_acceptance(source)
    assert shared["agent_todo_summary"]["goal_acceptance_contract"] == acceptance()
    assert "goal_acceptance_contract_ref" not in shared["agent_todo_summary"]


def test_unavailable_context_is_not_used_even_if_it_retains_old_content():
    source = packet()
    content = source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]
    content["status"] = "unavailable"
    shared = _reference_inline_goal_acceptance(source)
    assert shared["planning_horizon"]["core_goal"]["acceptance_contract"] == acceptance()
    assert shared["agent_todo_summary"]["goal_acceptance_contract_ref"] == "$.planning_horizon.core_goal.acceptance_contract"
    assert build_turn_envelope(shared)["action"]["planning_horizon"]["core_goal"]["acceptance_contract"] == acceptance()


@pytest.mark.parametrize("project", [_reference_inline_goal_acceptance, compact_quota_should_run_cli_payload])
@pytest.mark.parametrize("left,right", [(1, 1.0), (False, 0), (-0.0, 0),
    (2**60, 2**60 + 1), (2**60, float(2**60))])
def test_reference_transport_keeps_distinct_original_numbers_and_later_exact_owner(left, right, project):
    source = packet(False)
    first = source["planning_horizon"]["core_goal"]["acceptance_contract"]
    second = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"]
    third = source["agent_todo_summary"]["goal_acceptance_contract"]
    first["future"] = {"observation": left}
    second["future"] = {"observation": right}
    third["future"] = {"observation": right}
    original = json.dumps(source, ensure_ascii=False)
    result = project(source)
    # JS equality is insufficient to replace the second source. The third may
    # still share its exact later owner, including original numeric spelling.
    actual = result["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert json.dumps(actual["acceptance_contract"]) == json.dumps(second)
    assert "acceptance_contract_ref" not in actual
    assert result["agent_todo_summary"]["goal_acceptance_contract_ref"] == (
        "$.autonomous_replan_obligation.replan_context.core_goal.acceptance_contract")
    assert json.dumps(source, ensure_ascii=False) == original
    assert json.dumps(project(result)) == json.dumps(result)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_cli_reuses_complete_acceptance_for_selection_and_replan(tmp_path, monkeypatch, provider):
    from test_replan_successor_guard_reentry import _fixture, AGENT, GOAL
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, future_monitor=False)
    registry_path = tmp_path / "registry.json"
    registry = json.loads(registry_path.read_text())
    registry["goals"][0]["execution_profile"] = {"planning_horizon_goal": True}
    registry_path.write_text(json.dumps(registry))
    document = tmp_path / "requirements.json"
    document.write_text(json.dumps({"outcome_id": "export", "owner_agent_id": AGENT,
        "objective": "Deliver the complete export", "requirements": [
            {"id": "content", "description": "Preserve every original section."},
            {"id": "recovery", "description": "Keep the tail requirement and the publication stop."}],
        "source": {"ref": "message:owner", "summary": "The owner requested a complete export."}}))
    before = call("goal-acceptance", "inspect", "--goal-id", GOAL)
    call("goal-acceptance", "refine", "--goal-id", GOAL, "--agent-id", AGENT,
         "--document", str(document), "--expected-provider-revision", before["provider_revision"],
         "--operation-id", "source-requirements", "--execute")
    expected = call("goal-acceptance", "inspect", "--goal-id", GOAL)["goal_acceptance_contract"]
    guard = call("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
                 "--turn-instance-id", "turn-acceptance-sharing")
    assert guard["effective_action"] == "autonomous_replan_required"
    ref = "$.interaction_contract.agent_channel.work_context.sources[0].content.goal_acceptance_contract"
    assert guard["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract_ref"] == ref
    assert guard["agent_todo_summary"]["goal_acceptance_contract_ref"] == ref
    assert guard["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["goal_acceptance_contract"] == expected
    assert build_turn_envelope(guard)["action_signature"]["matches"] is True

    # Normal selected delivery must use the exact inline acceptance read and
    # relocate its reference when a host consumes the CLI as a TurnEnvelope.
    index.write_text("")
    added = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
                 "--text", "Validate the complete export", "--task-class", "advancement_task",
                 "--action-kind", "validate")
    call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
         "--text", "Validate independent recovery", "--task-class", "advancement_task",
         "--action-kind", "validate")
    linked = json.loads(document.read_text())
    linked["todo_ids"] = [added["todo_id"]]
    document.write_text(json.dumps(linked))
    revision = call("goal-acceptance", "inspect", "--goal-id", GOAL)["provider_revision"]
    call("goal-acceptance", "refine", "--goal-id", GOAL, "--agent-id", AGENT,
         "--document", str(document), "--expected-provider-revision", revision,
         "--operation-id", "link-export", "--execute")
    expected = call("goal-acceptance", "inspect", "--goal-id", GOAL)["goal_acceptance_contract"]
    planning = call("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
                    "--turn-instance-id", "turn-export-planning")
    assert planning["planning_horizon"]["core_goal"]["acceptance_contract"] == expected
    assert planning["agent_todo_summary"]["goal_acceptance_contract_ref"] == "$.planning_horizon.core_goal.acceptance_contract"
    selected = call("quota", "should-run", "--codex-app", "--goal-id", GOAL, "--agent-id", AGENT,
                    "--todo-id", added["todo_id"], "--turn-instance-id", "turn-export-delivery")
    assert selected["normal_delivery_allowed"] is True
    sources = selected["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    source_index = next(i for i, row in enumerate(sources) if row["source"] == "goal_acceptance")
    retained = sources[source_index]["content"]["goal_acceptance_contract"]
    assert retained == expected
    assert selected["agent_todo_summary"]["goal_acceptance_contract_ref"] == (
        f"$.interaction_contract.agent_channel.work_context.sources[{source_index}].content.goal_acceptance_contract")
    envelope = build_turn_envelope(selected)
    assert envelope["work_context"]["sources"][source_index]["content"]["goal_acceptance_contract"] == expected
    assert envelope["action_signature"]["matches"] is True
