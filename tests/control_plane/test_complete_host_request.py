"""One complete Host wire; historical adapters stay version-bound and internal."""
from __future__ import annotations

from copy import deepcopy
import json

import pytest

from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan, turn_host_lineage
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture
from loopx.control_plane.turn_driver.host_candidate import (
    complete_host_input, extract_turn_authority, extract_turn_work_identity,
)
from tests.control_plane.test_turn_host_decision_capture import (
    complete_host_request as complete_host_request,
)


def _captured_request(original, tmp_path):
    request, source = original
    plan = build_loopx_turn_plan(request["turn_envelope"], work_contract=source,
        host="generic-cli", execution_mode="isolated-headless", turn_instance_id="complete-wire")
    plan = prepare_turn_decision_capture(plan, journal_path=tmp_path / "wire" / "journal.json",
        decision=source, existing_journal=False)
    return build_loopx_turn_host_request(plan)


def test_complete_wire_roundtrip_has_one_source_and_original_readers(complete_host_request, tmp_path):
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    internal = _captured_request(complete_host_request, tmp_path)
    legacy, _ = complete_host_request
    before = deepcopy(internal)
    wire = complete_host_input(internal)
    assert wire["schema_version"] == "loopx_turn_host_request_v1"
    assert "turn_envelope" not in wire and "turn_authority" not in wire
    assert wire["work_contract"] == internal["work_contract"]
    assert wire["turn_key"] == internal["turn_key"]
    assert wire["detail_ref"]["captured_decision"]["turn_key"] == wire["turn_key"]
    assert complete_host_input(wire) == wire
    assert turn_host_lineage(wire) == turn_host_lineage(legacy)
    assert extract_turn_work_identity(wire) == extract_turn_work_identity(legacy)
    assert extract_turn_authority(wire)["primary_action"] == extract_turn_authority(legacy)["primary_action"]
    rendered = json.loads(_prompt(wire).split("Turn request:\n", 1)[1])
    assert rendered == wire
    assert internal == before


@pytest.mark.parametrize("host", ["codex", "structured"])
@pytest.mark.parametrize("declared_audit", [True, False])
def test_open_goal_acceptance_judgment_keeps_its_read_audit(
    complete_host_request, tmp_path, host, declared_audit,
):
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.host_candidate import render_prompt
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision

    # This is ordinary admitted work, with no Goal-completion result requested.
    # Judging its acceptance must still read the original ordered audit first.
    _, original = complete_host_request
    source = deepcopy({key: value for key, value in original.items()
                       if key not in {"schema_version", "contract_binding"}})
    audit = {"required": True,
             "required_before_closeout": ["derive_requirements_from_active_vision_and_current_todo",
                                          "name_authoritative_evidence_for_each_requirement"],
             "vision_gap_judge": {"done": False, "decision": "continue"}}
    source.pop("vision_continuation_audit", None)
    source["interaction_contract"]["cli_channel"].pop("vision_continuation_audit", None)
    if declared_audit:
        source["vision_continuation_audit"] = audit
        source["interaction_contract"]["cli_channel"]["vision_continuation_audit"] = deepcopy(audit)
    contract = compose_turn_decision(source)
    plan = build_loopx_turn_plan(contract, host="generic-cli",
        execution_mode="isolated-headless", turn_instance_id="open-goal-acceptance")
    plan = prepare_turn_decision_capture(plan, journal_path=tmp_path / "audit" / "journal.json",
        decision=contract, existing_journal=False)
    request = build_loopx_turn_host_request(plan)
    before = deepcopy(request)
    wire = complete_host_input(request)
    assert wire["work_contract"]["should_run"] is True
    if declared_audit:
        assert wire["work_contract"]["vision_continuation_audit"] == audit
    else:
        assert "vision_continuation_audit" not in wire["work_contract"]
    prompt = (_prompt(request) if host == "codex"
              else render_prompt(extract_turn_authority(request), allow_completion=False))
    instructions, encoded = prompt.split("Turn request:\n", 1)
    if declared_audit:
        assert "Read vision_continuation_audit before judging Goal acceptance or proposing closeout" in instructions
        assert "ordered required_before_closeout" in instructions
        assert "evidence requirements" in instructions and "vision_gap_judge" in instructions
    else:
        assert "vision_continuation_audit" not in instructions
    assert "Do not write LoopX state" in instructions
    assert "owns those effects" in instructions
    assert json.JSONDecoder().raw_decode(encoded)[0] == wire
    assert request == before


@pytest.mark.parametrize("host", ["codex", "structured"])
def test_complete_host_keeps_notification_and_execution_boundaries(
    complete_host_request, tmp_path, host,
):
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.host_candidate import render_prompt

    request = _captured_request(complete_host_request, tmp_path)
    wire = complete_host_input(request)
    prompt = (_prompt(request) if host == "codex"
              else render_prompt(extract_turn_authority(request), allow_completion=False))
    instructions, encoded = prompt.split("Turn request:\n", 1)
    assert "User notification policy does not grant or cancel execution authority" in instructions
    for field in ("must_attempt", "delivery_allowed", "quiet_noop_allowed",
                  "execution_policy", "task_orchestration_contract"):
        assert field in instructions
    assert "alternatives and capability intents do not override admission or scope" in instructions
    assert "Do not write LoopX state" in instructions
    assert "owns those effects" in instructions
    assert json.JSONDecoder().raw_decode(encoded)[0] == wire


def test_private_plan_lineage_is_not_a_host_wire(complete_host_request):
    request, source = complete_host_request
    plan = build_loopx_turn_plan(request["turn_envelope"], work_contract=source,
        host="generic-cli", execution_mode="isolated-headless", turn_instance_id="private-plan")
    assert turn_host_lineage(plan) == turn_host_lineage(request)
    with pytest.raises(ValueError, match="host request schema"):
        complete_host_input(plan)


def test_complete_wire_rejects_downgrade_mixed_carriers_and_capture_drift(complete_host_request, tmp_path):
    original = _captured_request(complete_host_request, tmp_path)
    wire = complete_host_input(original)
    assert wire["schema_version"] == "loopx_turn_host_request_v1"
    malformed = []
    missing = deepcopy(wire)
    del missing["work_contract"]
    malformed.append(missing)
    for replacement in (None, {**wire["work_contract"], "reason": "changed after capture"},
                        {**wire["work_contract"], "schema_version": "unsupported_future_version"}):
        malformed.append({**wire, "work_contract": replacement})
    for replacement in (None, {}, complete_host_request[0]["turn_envelope"]):
        malformed.append({**wire, "turn_envelope": replacement})
    malformed.extend([{**wire, "schema_version": "unsupported_future_host"},
                      {**wire, "turn_key": "not-a-turn-key"},
                      {**wire, "source_decision_hash": "sha256:" + "0" * 64}])
    malformed.extend({**wire, "detail_ref": value} for value in (None, [], "invalid reference"))
    malformed.extend({**wire, "detail_ref": {**wire["detail_ref"], "captured_decision": value}}
                     for value in (None, {}, [], "invalid capture"))
    for key, value in (("turn_key", "sha256:" + "0" * 64), ("goal_id", "other-goal"),
                       ("agent_id", "other-agent"), ("source_hash_ref", "$.unverified")):
        changed = deepcopy(wire)
        changed["detail_ref"]["captured_decision"][key] = value
        malformed.append(changed)
    without_hash = deepcopy(wire)
    del without_hash["source_decision_hash"]
    malformed.append(without_hash)
    for changed in malformed:
        before = deepcopy(changed)
        for consume in (complete_host_input, extract_turn_authority, extract_turn_work_identity, turn_host_lineage):
            with pytest.raises((ValueError, RuntimeError)):
                consume(changed)
        assert changed == before


def test_historical_v0_wire_keeps_its_original_decoder(complete_host_request):
    internal, _ = complete_host_request
    historical = {key: deepcopy(value) for key, value in internal.items() if key != "work_contract"}
    assert historical["schema_version"] == "loopx_turn_host_request_v0"
    before = deepcopy(historical)
    assert complete_host_input(historical) is None
    assert extract_turn_work_identity(historical) == extract_turn_work_identity(internal)
    assert extract_turn_authority(historical)["primary_action"] == extract_turn_authority(internal)["primary_action"]
    assert historical == before


def test_complete_wire_keeps_original_numbers_through_capture_and_readback(complete_host_request, tmp_path):
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision, build_turn_envelope

    _, original = complete_host_request
    source = deepcopy({key: value for key, value in original.items()
                       if key not in {"schema_version", "contract_binding"}})
    source["observations"] = [1.0, -0.0, 1e-7, 2**60 + 1]
    contract = compose_turn_decision(source)
    request = _captured_request(({"turn_envelope": build_turn_envelope(contract)}, contract), tmp_path)
    wire = complete_host_input(request)
    assert wire["schema_version"] == "loopx_turn_host_request_v1"
    restored = extract_turn_authority(wire)
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    assert encode(restored["observations"]) == encode(source["observations"])
    assert encode(complete_host_input(wire)) == encode(wire)
