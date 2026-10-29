"""One complete acceptance survives composition, display and actual admission."""
from copy import deepcopy
from functools import partial
import json

import pytest

from loopx.control_plane.quota.cli_projection import (
    _reference_context, compact_quota_should_run_cli_payload,
)
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope, compose_turn_decision, quota_action_signature_document,
    turn_envelope_action_signature_document,
)
from test_replan_host_context_projection import _quota_payload


@pytest.fixture(params=[partial(_reference_context, task_title=False,
    explore_guidance=False, goal_acceptance=True), compose_turn_decision],
                ids=["legacy-display", "complete-composition"])
def share(request):
    return request.param


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


@pytest.mark.parametrize("project", [partial(_reference_context, task_title=False,
    explore_guidance=False, goal_acceptance=True), compact_quota_should_run_cli_payload,
    compose_turn_decision], ids=["display", "cli", "complete"])
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
    # Reuse the independent source-value oracle from the legacy reader tests:
    # unequal numbers stay literal; later exact copies still share their owner.
    actual = result["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    assert json.dumps(actual["acceptance_contract"]) == json.dumps(second)
    assert "acceptance_contract_ref" not in actual
    assert result["agent_todo_summary"]["goal_acceptance_contract_ref"] == (
        "$.autonomous_replan_obligation.replan_context.core_goal.acceptance_contract")
    assert json.dumps(source, ensure_ascii=False) == original
    assert json.dumps(project(result)) == json.dumps(result)


@pytest.mark.parametrize("value", [1.0, -0.0, 2**60 + 1, float(2**60), 0.000001])
def test_complete_composition_keeps_original_numeric_facts_and_returns_only_edits(value, monkeypatch):
    from loopx.control_plane.quota import turn_envelope

    source = packet()
    facts = {"scalar": value, "nested": [{"number": value}], "body": "Original supporting material. " * 2048}
    source["future_facts"] = deepcopy(facts)
    original, calls = json.dumps(source), []
    real = turn_envelope.effect_runtime_result

    def observed(*args, **kwargs):
        result = real(*args, **kwargs)
        calls.append((args[1], result))
        return result

    monkeypatch.setattr(turn_envelope, "effect_runtime_result", observed)
    shared = compose_turn_decision(source)
    assert json.dumps(shared["future_facts"]) == json.dumps(facts)
    assert json.dumps(source) == original
    assert len(calls) == 1
    assert set(calls[0][1]) == {"changes"}
    assert facts["body"] not in json.dumps(calls[0][1])
    assert json.dumps(compose_turn_decision(shared)) == json.dumps(shared)


@pytest.mark.parametrize("left,right", [(1, 1.0), (-0.0, 0), (2**60, 2**60 + 1)])
def test_complete_summary_lanes_share_only_exact_original_numbers(left, right):
    source = packet()
    row = {"todo_id": "task-one", "text": "Keep the complete original task and evidence. " * 20}
    first, second = [{**row, "future": [left]}], [{**row, "future": [right]}]
    source["agent_todo_summary"] = {"schema_version": "todo_summary_v0",
        "first_executable_items": first, "backlog_items": second, "claimed_open_items": deepcopy(second)}
    shared = compose_turn_decision(source)
    summary = shared["agent_todo_summary"]
    assert json.dumps(summary["first_executable_items"]) == json.dumps(first)
    assert json.dumps(summary["backlog_items"]) == json.dumps(second)
    assert summary["claimed_open_items_ref"] == "$.agent_todo_summary.backlog_items"
    assert json.dumps(compose_turn_decision(shared)) == json.dumps(shared)


@pytest.mark.parametrize("with_context", [False, True])
def test_exact_copies_share_one_complete_source_without_changing_signed_action(with_context, share):
    source = packet(with_context)
    original = deepcopy(source)
    shared = share(source)
    assert source == original
    assert share(shared) == shared
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
def test_different_contracts_are_retained_in_full(change, share):
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
    shared = share(source)
    assert shared["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"] == changed
    assert "acceptance_contract_ref" not in shared["autonomous_replan_obligation"]["replan_context"]["core_goal"]


@pytest.mark.parametrize("missing", [None, {}, [], "unavailable"])
def test_missing_or_malformed_carriers_never_create_dangling_references(missing, share):
    source = packet()
    source["interaction_contract"]["agent_channel"]["work_context"] = missing
    source["planning_horizon"] = missing
    source["autonomous_replan_obligation"] = missing
    shared = share(source)
    assert shared["agent_todo_summary"]["goal_acceptance_contract"] == acceptance()
    assert "goal_acceptance_contract_ref" not in shared["agent_todo_summary"]


def test_unavailable_context_is_not_used_even_if_it_retains_old_content(share):
    source = packet()
    content = source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]
    content["status"] = "unavailable"
    shared = share(source)
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


@pytest.mark.parametrize("mutation", ["dangling", "cycle", "self", "unavailable"])
def test_complete_readers_reject_dangling_or_cyclic_acceptance_even_with_valid_hash(mutation):
    from test_complete_turn_decision import _resign_contract, _canonical_digest
    from loopx.control_plane.quota.turn_envelope import _turn_envelope_result
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    contract = compose_turn_decision(packet())
    envelope = build_turn_envelope(contract)
    summary = contract["agent_todo_summary"]
    if mutation == "dangling":
        summary["goal_acceptance_contract_ref"] = "$.interaction_contract.agent_channel.work_context.sources[9].content.goal_acceptance_contract"
    elif mutation == "self":
        summary["goal_acceptance_contract_ref"] = "$.agent_todo_summary.goal_acceptance_contract"
    elif mutation == "cycle":
        summary["goal_acceptance_contract_ref"] = "$.planning_horizon.core_goal.acceptance_contract"
        contract["planning_horizon"]["core_goal"]["acceptance_contract_ref"] = "$.agent_todo_summary.goal_acceptance_contract"
    else:
        sources = contract["interaction_contract"]["agent_channel"]["work_context"]["sources"]
        sources.append(deepcopy(sources[0]))
        sources[1]["content"]["status"] = "unavailable"
        summary["goal_acceptance_contract_ref"] = "$.interaction_contract.agent_channel.work_context.sources[1].content.goal_acceptance_contract"
    contract, _ = _resign_contract(contract)
    for read in (
        lambda: compose_turn_decision(contract),
        lambda: build_turn_envelope(contract),
        lambda: _turn_envelope_result("replan_view", payload=contract,
            source_hash=_canonical_digest(contract), stage="review"),
        lambda: _prompt({"work_contract": contract, "turn_envelope": envelope}),
    ):
        with pytest.raises(RuntimeError, match="acceptance reference"):
            read()


@pytest.mark.parametrize("status", [None, "loaded"])
def test_historical_reference_to_later_loaded_source_keeps_full_material(status):
    from test_complete_turn_decision import _canonical_digest
    from loopx.control_plane.quota.turn_envelope import _turn_envelope_result
    source = packet()
    sources = source["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    sources.append(deepcopy(sources[0]))
    sources[1]["content"]["status"] = status
    sources[1]["content"]["goal_acceptance_contract"]["revision"] = 7
    expected = deepcopy(sources[1]["content"]["goal_acceptance_contract"])
    core = source["autonomous_replan_obligation"]["replan_context"]["core_goal"]
    del core["acceptance_contract"]
    core["acceptance_contract_ref"] = "$.interaction_contract.agent_channel.work_context.sources[1].content.goal_acceptance_contract"
    contract = compose_turn_decision(source)
    view = _turn_envelope_result("replan_view", payload=contract,
        source_hash=_canonical_digest(contract), stage="review")
    # The staged review indexes requirements, retaining the complete captured
    # requirement source, revision and negative verification for exact readback.
    assert view["core_goal"]["acceptance_contract"]["revision"] == 7
    assert contract["interaction_contract"]["agent_channel"]["work_context"]["sources"][1]["content"]["goal_acceptance_contract"] == expected


def test_shared_contract_survives_warm_delta_and_missing_baseline_recovery():
    from loopx.control_plane.quota.turn_envelope import prepare_context_delivery, restore_context_delivery
    initial = compose_turn_decision(packet())
    changed = packet()
    changed["reason"] = "New feedback retains the original requirements and stops."
    current = compose_turn_decision(changed)
    scope = dict(goal_id="fixture", agent_id="worker", session_id="session", context_epoch="one")
    baseline = prepare_context_delivery(initial, scope=scope, capture_id="one", surface="complete_turn")
    full = prepare_context_delivery(current, scope=scope, capture_id="two", surface="complete_turn")
    delta = prepare_context_delivery(current, scope=scope, capture_id="two", surface="complete_turn",
        baseline=baseline, retained={"complete": True, "target": baseline["target"]})
    assert delta["mode"] == "delta"
    for retained in (baseline, None):
        restored = restore_context_delivery(delta, baseline=retained, current=full)["context"]
        assert restored == current
        assert compose_turn_decision(restored) == current
        assert restored["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["goal_acceptance_contract"] == acceptance()


@pytest.mark.parametrize("value", [{"revision": 1}, {"revision": 1.0}, {"value": -0.0},
    {"note": "短"}, {"passed": False}])
def test_small_identical_contracts_stay_inline_when_a_reference_would_cost_more(value, share):
    source = packet(False)
    source["planning_horizon"]["core_goal"]["acceptance_contract"] = deepcopy(value)
    source["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"] = deepcopy(value)
    source["agent_todo_summary"]["goal_acceptance_contract"] = deepcopy(value)
    shared = share(source)
    assert json.dumps(shared["agent_todo_summary"]["goal_acceptance_contract"]) == json.dumps(value)
    assert json.dumps(shared["autonomous_replan_obligation"]["replan_context"]["core_goal"]["acceptance_contract"]) == json.dumps(value)


@pytest.mark.parametrize("existing_owner", ["planning", "replan"])
def test_existing_reference_follows_a_relocated_complete_owner(existing_owner, share):
    source = packet()
    del source["agent_todo_summary"]["goal_acceptance_contract"]
    owner = ("$.planning_horizon.core_goal.acceptance_contract" if existing_owner == "planning"
             else "$.autonomous_replan_obligation.replan_context.core_goal.acceptance_contract")
    source["agent_todo_summary"]["goal_acceptance_contract_ref"] = owner
    shared = share(source)
    assert shared["agent_todo_summary"]["goal_acceptance_contract_ref"] == (
        "$.interaction_contract.agent_channel.work_context.sources[0].content.goal_acceptance_contract")
    assert compose_turn_decision(shared)["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["goal_acceptance_contract"] == acceptance()
