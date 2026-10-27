"""Replan authoring shares complete settlement instructions without losing stops."""
from copy import deepcopy

import pytest

from loopx.control_plane.quota.cli_projection import _reference_inline_replan_authoring
from loopx.control_plane.quota.turn_envelope import (
    _turn_envelope_result, build_turn_envelope, compose_turn_decision,
    quota_action_signature_document, prepare_context_delivery, restore_context_delivery,
)
from test_complete_turn_decision import _canonical_digest, _resign_contract
from test_replan_host_context_projection import _quota_payload


def packet():
    source = _quota_payload()
    authoring = {"schema_version": "goal_vision_replan_contract_v0",
        "fields": {"vision_patch": {"acceptance_summary": 420}},
        "rules": ["Keep all original requirements and cite source evidence. " * 12,
                  "A result does not grant publication or spending authority."],
        "minimal_example": {"state": "vision_patch_proposed"},
        "provenance": {"ref": "message:original", "revision": 3}, "required": False}
    source["replan_action_packet"]["writeback_contract"]["vision_authoring"] = deepcopy(authoring)
    steps = [{"kind": "validation"}, {"kind": "durable_writeback"}]
    source["interaction_contract"]["cli_channel"]["settlement_plan"] = {
        "identity": {"turn_instance_id": "original-turn"}, "ordered_steps": steps}
    index = next(i for i, step in enumerate(steps) if step["kind"] == "durable_writeback")
    steps[index]["vision_authoring"] = authoring
    return source, authoring, index


def legacy(source):
    return {**source, "replan_action_packet": _reference_inline_replan_authoring(
        source["replan_action_packet"], payload=source)}


@pytest.fixture(params=[legacy, compose_turn_decision], ids=["legacy-display", "complete"])
def share(request):
    return request.param


def test_exact_authoring_keeps_single_complete_owner_and_original_settlement(share):
    source, authoring, index = packet()
    original = deepcopy(source)
    shared = share(source)
    assert source == original
    assert share(shared) == shared
    writeback = shared["replan_action_packet"]["writeback_contract"]
    assert "vision_authoring" not in writeback
    assert writeback["vision_authoring_ref"] == (
        f"$.interaction_contract.cli_channel.settlement_plan.ordered_steps[{index}].vision_authoring")
    assert shared["interaction_contract"]["cli_channel"]["settlement_plan"] == original["interaction_contract"]["cli_channel"]["settlement_plan"]
    if share is legacy:
        assert quota_action_signature_document(shared) == quota_action_signature_document(source)
    envelope = build_turn_envelope(shared)
    assert envelope["action_signature"]["matches"] is True
    assert envelope["replan_action_packet"]["writeback_contract"]["vision_authoring"] == authoring
    writer = _turn_envelope_result("replan_view", payload=shared,
        source_hash=envelope["action_signature"]["source_decision_hash"], stage="writeback")
    expected = {k: v for k, v in authoring.items() if k != "minimal_example"}
    assert writer["writeback_contract"]["vision_authoring"] == expected
    assert "vision_authoring_ref" not in writer["writeback_contract"]


@pytest.mark.parametrize("mutation", ["different-rule", "revision", "type", "wrong-step", "short"])
def test_distinct_or_unavailable_authoring_is_not_replaced(mutation, share):
    source, _, index = packet()
    owner = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][index]
    if mutation == "different-rule":
        owner["vision_authoring"]["rules"].append("Retain an additional stop.")
    elif mutation == "revision":
        owner["vision_authoring"]["provenance"]["revision"] = 4
    elif mutation == "type":
        owner["vision_authoring"]["required"] = 0
    elif mutation == "wrong-step":
        owner["kind"] = "validation"
    else:
        owner["vision_authoring"] = {"required": True}
        source["replan_action_packet"]["writeback_contract"]["vision_authoring"] = {"required": True}
    original = deepcopy(source["replan_action_packet"]["writeback_contract"]["vision_authoring"])
    shared = share(source)
    assert shared["replan_action_packet"]["writeback_contract"]["vision_authoring"] == original
    assert "vision_authoring_ref" not in shared["replan_action_packet"]["writeback_contract"]


@pytest.mark.parametrize("mutation", ["dangling", "wrong-step", "chain", "both", "malformed"])
def test_invalid_authoring_reference_is_rejected_even_with_valid_content_hash(mutation):
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    source, _, index = packet()
    contract = compose_turn_decision(source)
    envelope = build_turn_envelope(contract)
    writeback = contract["replan_action_packet"]["writeback_contract"]
    writeback.pop("vision_authoring", None)
    reference = f"$.interaction_contract.cli_channel.settlement_plan.ordered_steps[{index}].vision_authoring"
    writeback["vision_authoring_ref"] = reference
    owner = contract["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][index]
    if mutation == "dangling":
        writeback["vision_authoring_ref"] = reference.replace(f"[{index}]", "[999]")
    elif mutation == "wrong-step":
        owner["kind"] = "validation"
    elif mutation == "chain":
        owner.pop("vision_authoring")
        owner["vision_authoring_ref"] = "$.replan_action_packet.writeback_contract.vision_authoring"
    elif mutation == "both":
        writeback["vision_authoring"] = {"rules": ["Conflicting authority"]}
    else:
        writeback["vision_authoring_ref"] = {"path": reference}
    contract, _ = _resign_contract(contract)
    for read in (lambda: compose_turn_decision(contract), lambda: build_turn_envelope(contract),
                 lambda: _prompt({"work_contract": contract, "turn_envelope": envelope}),
                 lambda: _turn_envelope_result("replan_view", payload=contract,
                     source_hash=_canonical_digest(contract), stage="writeback")):
        with pytest.raises(RuntimeError, match="authoring reference"):
            read()


def test_full_delta_and_lost_baseline_restore_authoring_without_guard_reentry():
    # Use the production delivery/restore owner; independent material is checked
    # after reconstruction rather than only comparing signatures.
    source, authoring, _ = packet()
    before = compose_turn_decision(source)
    source["replan_action_packet"]["uncovered_frontier"] = ["A newly observed gap"]
    after = compose_turn_decision(source)
    scope = dict(goal_id="fixture", agent_id="worker", session_id="session", context_epoch="one")
    baseline = prepare_context_delivery(before, scope=scope, capture_id="one", surface="complete_turn")
    full = prepare_context_delivery(after, scope=scope, capture_id="two", surface="complete_turn")
    delta = prepare_context_delivery(after, scope=scope, capture_id="two", surface="complete_turn",
        baseline=baseline, retained={"complete": True, "target": baseline["target"]})
    assert delta["mode"] == "delta"
    for retained in (baseline, None):
        restored = restore_context_delivery(delta, baseline=retained, current=full)["context"]
        assert restored == after
        assert build_turn_envelope(restored)["replan_action_packet"]["writeback_contract"]["vision_authoring"] == authoring


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_capture_shares_authoring_and_refuses_invalid_recovery_without_effects(tmp_path, monkeypatch, provider):
    import json
    import subprocess
    import sys
    from pathlib import Path
    from test_turn_replan_writeback import _fixture
    from test_quota_settlement_cli import _run_cli, _spend_run_count, GOAL_ID, AGENT_ID, SELECTED_REPLAN_TODO_ID
    project, runtime, registry, state, turn = _fixture(tmp_path, monkeypatch, provider)
    capture = tmp_path / "captured"
    code, source = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", SELECTED_REPLAN_TODO_ID,
        "--turn-instance-id", turn, "--decision-output-dir", str(capture), cwd=project)
    assert code == 0, source
    assert json.loads((capture / "decision.json").read_text()) == source
    contract = source["replan_action_packet"]["writeback_contract"]
    assert "vision_authoring" not in contract
    assert contract["vision_authoring_ref"] == "$.interaction_contract.cli_channel.settlement_plan.ordered_steps[1].vision_authoring"
    original = source["interaction_contract"]["cli_channel"]["settlement_plan"]["ordered_steps"][1]["vision_authoring"]
    envelope = build_turn_envelope(source)
    import shlex
    read = subprocess.run(shlex.split(envelope["replan_action_packet"]["writeback"]),
        capture_output=True, text=True, check=True)
    assert json.loads(read.stdout)["writeback_contract"]["vision_authoring"] == {
        key: value for key, value in original.items() if key != "minimal_example"}
    state_before = state.read_bytes()
    invalid = deepcopy(source)
    invalid["replan_action_packet"]["writeback_contract"]["vision_authoring_ref"] = "$.interaction_contract.cli_channel.settlement_plan.ordered_steps[99].vision_authoring"
    invalid, _ = _resign_contract(invalid)
    saved = tmp_path / "invalid.json"
    saved.write_text(json.dumps(invalid))
    result = subprocess.run([str(Path(sys.executable).with_name("loopx")), "--format", "json", "quota", "replan-view",
        "--decision-file", str(saved), "--source-hash", _canonical_digest(invalid), "--replan-stage", "writeback"],
        capture_output=True, text=True)
    assert result.returncode != 0
    assert "authoring reference" in result.stdout + result.stderr
    assert state.read_bytes() == state_before
    assert _spend_run_count(runtime) == 0
