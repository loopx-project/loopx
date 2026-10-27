"""Explore instructions survive sharing, historical views and captured recovery."""
from copy import deepcopy

import pytest

from loopx.control_plane.quota.cli_projection import _reference_context
from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result, build_turn_envelope, compose_turn_decision,
    prepare_context_delivery, quota_action_signature_document, restore_context_delivery,
)
from test_complete_turn_decision import _canonical_digest, _resign_contract
from test_replan_host_context_projection import _quota_payload


def packet():
    source = _quota_payload()
    guidance = "Keep original requirements, lease ownership, negative evidence and stop conditions. " * 20
    source["interaction_contract"]["agent_channel"]["work_context"] = {"complete": True, "sources": [{
        "kind": "explore_turn_context", "source": "turn_start_capability_hook",
        "hook_id": "explore.turn_context", "capability_id": "explore",
        "content": {"graph": {"result_writeback_guidance": guidance,
            "writeback_results": [{"status": "refuted", "evidence": "fixture:negative"}]}}}]}
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = {
        "identity": {"turn_instance_id": "original-turn"}, "ordered_steps": [
            {"kind": "validation"}, {"kind": "durable_writeback", "optional_attachments": [
                {"capability_id": "explore", "guidance": guidance, "required": False}]}]}
    return source, guidance


def graph(source):
    return source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["content"]["graph"]


def attachment(source):
    return source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["optional_attachments"][0]


@pytest.fixture(params=[_reference_context, compose_turn_decision], ids=["legacy-display", "complete"])
def share(request):
    return request.param


@pytest.mark.parametrize("captured", [False, True])
def test_shared_guidance_preserves_material_and_settlement_across_readers(share, captured):
    source, guidance = packet()
    if captured:
        source["replan_read_path"] = "/synthetic/decision.json"
    original = deepcopy(source)
    shared = share(source)
    assert source == original
    assert share(shared) == shared
    assert graph(shared) == {"writeback_results": graph(source)["writeback_results"],
        "result_writeback_guidance_ref": "$.interaction_contract.cli_channel.settlement_plan.ordered_steps[1].optional_attachments[0].guidance"}
    assert shared["interaction_contract"]["cli_channel"] == source["interaction_contract"]["cli_channel"]
    if share is _reference_context and not captured:
        assert quota_action_signature_document(shared) == quota_action_signature_document(source)
    envelope = build_turn_envelope(shared)
    material = envelope["work_context"]["sources"][0]["content"]["graph"]
    assert material == graph(source)
    assert material["result_writeback_guidance"] == guidance
    assert envelope["action_signature"]["matches"] is True
    if captured:
        projected = _turn_envelope_result("replan_cli_view", payload=shared)
        assert graph(projected) == graph(source)
        writer = _turn_envelope_result("replan_view", payload=shared,
            source_hash=_canonical_digest(shared), stage="writeback")
        assert writer["writeback"]["settlement_plan"]["ordered_steps"][1]["optional_attachments"][0] == attachment(source)


@pytest.mark.parametrize("mutation", ["different", "wrong-step", "wrong-capability", "missing-owner", "short", "wrong-source"])
def test_nonidentical_or_unavailable_guidance_stays_inline(share, mutation):
    source, _ = packet()
    if mutation == "different":
        attachment(source)["guidance"] += " Extra stop."
    elif mutation == "wrong-step":
        source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["kind"] = "validation"
    elif mutation == "wrong-capability":
        attachment(source)["capability_id"] = "another"
    elif mutation == "missing-owner":
        attachment(source).pop("guidance")
    elif mutation == "short":
        attachment(source)["guidance"] = graph(source)["result_writeback_guidance"] = "Keep evidence."
    else:
        source["interaction_contract"]["agent_channel"]["work_context"]["sources"][0]["kind"] = "other_context"
    assert graph(share(source)) == graph(source)


@pytest.mark.parametrize("mutation", ["dangling", "wrong-step", "wrong-capability", "chain", "both", "malformed"])
def test_invalid_guidance_reference_is_refused_despite_valid_integrity(mutation):
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    source, _ = packet()
    contract = compose_turn_decision(source)
    envelope = build_turn_envelope(contract)
    if mutation == "dangling":
        graph(contract)["result_writeback_guidance_ref"] = graph(contract)["result_writeback_guidance_ref"].replace("[1]", "[99]")
    elif mutation == "wrong-step":
        contract["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["kind"] = "validation"
    elif mutation == "wrong-capability":
        attachment(contract)["capability_id"] = "another"
    elif mutation == "chain":
        attachment(contract)["guidance_ref"] = "$.unknown"
    elif mutation == "both":
        graph(contract)["result_writeback_guidance"] = "Conflicting instruction."
    else:
        graph(contract)["result_writeback_guidance_ref"] = {"path": "$.unknown"}
    contract, _ = _resign_contract(contract)
    for read in (lambda: compose_turn_decision(contract), lambda: build_turn_envelope(contract),
                 lambda: _prompt({"work_contract": contract, "turn_envelope": envelope}),
                 lambda: _turn_envelope_result("replan_view", payload=contract,
                     source_hash=_canonical_digest(contract), stage="writeback")):
        with pytest.raises(RuntimeError, match="Explore guidance reference"):
            read()


@pytest.mark.parametrize("change_guidance, complete", [(False, True), (True, True), (True, False)])
def test_guidance_restores_after_delta_or_full_fallback(change_guidance, complete):
    source, guidance = packet()
    source["interaction_contract"]["agent_channel"]["work_context"]["complete"] = complete
    before = compose_turn_decision(source)
    if change_guidance:
        guidance += " New authorization boundary."
        graph(source)["result_writeback_guidance"] = attachment(source)["guidance"] = guidance
    else:
        source["replan_action_packet"]["uncovered_frontier"] = ["A new observed gap"]
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
        assert build_turn_envelope(restored)["work_context"]["sources"][0]["content"]["graph"]["result_writeback_guidance"] == guidance
