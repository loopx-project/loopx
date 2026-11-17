"""One complete Host wire; historical adapters stay version-bound and internal."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan, turn_host_lineage
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture
from loopx.control_plane.turn_driver.host_candidate import (
    complete_host_input, extract_turn_authority, extract_turn_work_identity,
)
from tests.control_plane.host_process_fixture import assert_host_task_context
from tests.control_plane.test_turn_host_decision_capture import (
    complete_host_request as complete_host_request,
)


def _captured_request(source, tmp_path):
    plan = build_loopx_turn_plan(source,
        host="generic-cli", execution_mode="isolated-headless", turn_instance_id="complete-wire")
    plan = prepare_turn_decision_capture(plan, journal_path=tmp_path / "wire" / "journal.json",
        decision=source, existing_journal=False)
    return build_loopx_turn_host_request(plan)


@pytest.mark.parametrize("field", ["work_contract", "source_decision_hash", "fixture_context"])
@pytest.mark.parametrize("location", ["context_fields", "result_fields"])
def test_optional_context_preserves_the_complete_host_source(
    complete_host_request, field, location,
):
    from loopx.control_plane.capability_hooks import (
        TurnContextHookRegistration, dispatch_turn_context_hooks, project_turn_context,
    )

    _, contract = complete_host_request
    plan = build_loopx_turn_plan(contract, host="generic-cli",
        execution_mode="isolated-headless", turn_instance_id="context-source-boundary")
    ordinary = build_loopx_turn_host_request(plan)
    result = {"readback": {"fixture_readback": {}}, "context_fields": {},
              "instructions": [], "result_fields": {}}
    result[location] = {field: ("optional advice" if location == "context_fields"
                               else {"type": "string", "maxLength": 240})}
    registration = TurnContextHookRegistration(
        "fixture.context", "fixture", ("fixture_readback",), lambda: deepcopy(result),
    )
    dispatch_turn_context_hooks(plan, (registration,))
    request = build_loopx_turn_host_request(plan)
    assert request["work_contract"] == ordinary["work_contract"]
    assert request["source_decision_hash"] == ordinary["source_decision_hash"]
    assert request["turn_key"] == ordinary["turn_key"]
    if field == "fixture_context":
        projected = project_turn_context(plan)
        if location == "context_fields":
            assert field in projected
        else:
            assert field in projected["capability_result_fields"]
        assert "turn_context_hook_failures" not in plan
    else:
        assert plan["turn_context_hook_failures"] == [
            {"hook_id": "fixture.context", "error_code": "context_hook_failed"}]
        assert project_turn_context(plan) == {}


def test_complete_wire_roundtrip_has_one_source_and_original_readers(complete_host_request, tmp_path):
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    internal = _captured_request(complete_host_request[1], tmp_path)
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
    assert_host_task_context(rendered, wire["work_contract"])
    assert rendered["detail_ref"] == wire["detail_ref"]
    assert rendered["turn_key"] == wire["turn_key"]
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
    model_input = json.JSONDecoder().raw_decode(encoded)[0]
    assert_host_task_context(model_input, wire["work_contract"])
    assert model_input["detail_ref"] == wire["detail_ref"]
    assert request == before


@pytest.mark.parametrize("host", ["codex", "structured"])
def test_complete_host_keeps_notification_and_execution_boundaries(
    complete_host_request, tmp_path, host,
):
    from loopx.control_plane.turn_driver.codex_cli import _prompt
    from loopx.control_plane.turn_driver.host_candidate import render_prompt

    request = _captured_request(complete_host_request[1], tmp_path)
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
    model_input = json.JSONDecoder().raw_decode(encoded)[0]
    assert_host_task_context(model_input, wire["work_contract"])
    assert model_input["detail_ref"] == wire["detail_ref"]


def test_private_plan_lineage_is_not_a_host_wire(complete_host_request):
    request, source = complete_host_request
    plan = build_loopx_turn_plan(source,
        host="generic-cli", execution_mode="isolated-headless", turn_instance_id="private-plan")
    assert turn_host_lineage(plan) == turn_host_lineage(request)
    with pytest.raises(ValueError, match="host request schema"):
        complete_host_input(plan)


@pytest.mark.parametrize("source_valid", [True, False])
@pytest.mark.parametrize("with_advice", [False, True])
def test_status_readback_is_independent_of_executable_host_source(
    complete_host_request, source_valid, with_advice,
):
    from loopx.control_plane.capability_hooks import (
        TurnContextHookRegistration, dispatch_turn_context_hooks,
    )

    _, source = complete_host_request
    plan = build_loopx_turn_plan(source, host="generic-cli",
        execution_mode="isolated-headless", turn_instance_id="optional-status")
    if not source_valid:
        plan["work_contract"]["goal_id"] = "another-goal"
    calls = []
    observation = {"status": "unavailable"}

    def produce():
        calls.append("observed")
        return {"readback": {"fixture_readback": observation},
                "context_fields": {"fixture_context": "Bound advice."} if with_advice else {},
                "instructions": [], "result_fields": {}}

    dispatch_turn_context_hooks(plan, (TurnContextHookRegistration(
        "fixture.status", "fixture", ("fixture_readback",), produce,
    ),))
    assert calls == ["observed"]
    if source_valid or not with_advice:
        assert plan["fixture_readback"] == observation
        assert "turn_context_hook_failures" not in plan
    else:
        assert "fixture_readback" not in plan
        assert plan["turn_context_hook_failures"] == [
            {"hook_id": "fixture.status", "error_code": "context_hook_failed"}]
    if not with_advice or not source_valid:
        assert "turn_context_contributions" not in plan
    if source_valid:
        request = build_loopx_turn_host_request(plan)
        assert request["work_contract"] == source
        assert request.get("fixture_context") == ("Bound advice." if with_advice else None)
    else:
        # An observation of provider status cannot authorize a Host or advice.
        with pytest.raises((ValueError, RuntimeError)):
            build_loopx_turn_host_request(plan)


def test_preparation_reads_checked_identity_without_an_executor_adapter(complete_host_request):
    legacy, source = complete_host_request
    before = deepcopy(source)
    request = {"work_contract": source}
    assert turn_host_lineage(request) == turn_host_lineage(legacy)
    # Identity facts do not make this internal preparation argument a Host wire.
    with pytest.raises((ValueError, RuntimeError)):
        complete_host_input(request)
    for damaged in (None, {}, {**source, "reason": "changed after capture"},
                    {**source, "schema_version": "unsupported_future_version"}):
        with pytest.raises((ValueError, RuntimeError)):
            turn_host_lineage({"work_contract": damaged})
    for adapter in (None, {}):
        with pytest.raises((ValueError, RuntimeError)):
            turn_host_lineage({**request, "turn_envelope": adapter})
    assert source == before


@pytest.mark.parametrize("change", ["capability", "stop", "turn"])
def test_complete_host_optional_context_requires_original_turn_source(
    complete_host_request, tmp_path, change,
):
    from loopx.control_plane.capability_hooks import (
        TurnContextHookRegistration, dispatch_turn_context_hooks,
    )
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision

    _, original = complete_host_request
    source = deepcopy({key: value for key, value in original.items()
                       if key not in {"schema_version", "contract_binding"}})
    source["observations"] = [1.0, -0.0, 2**60 + 1]
    contract = compose_turn_decision(source)
    turn_id = "capability-source"
    plan = build_loopx_turn_plan(contract, host="generic-cli",
        execution_mode="isolated-headless", turn_instance_id=turn_id)
    result = {"readback": {"fixture_readback": "observed"},
        "context_fields": {"fixture_context": "Advisory context."},
        "instructions": ["Use the associated observation."],
        "result_fields": {"fixture_result": {"type": "string", "maxLength": 128}}}
    dispatch_turn_context_hooks(plan, (TurnContextHookRegistration(
        "fixture.context", "fixture", ("fixture_readback",), lambda: deepcopy(result),
    ),))
    initial = build_loopx_turn_host_request(plan)
    captured = prepare_turn_decision_capture(plan, journal_path=tmp_path / "capability" / "journal.json",
        decision=contract, existing_journal=False)
    bound = build_loopx_turn_host_request(captured)
    assert bound["detail_ref"]["captured_decision"]["turn_key"] == initial["turn_key"]
    for request in (initial, bound):
        assert request["fixture_context"] == result["context_fields"]["fixture_context"]
        assert request["capability_instructions"] == result["instructions"]
        assert request["capability_result_fields"] == result["result_fields"]

    if change == "capability":
        source["selected_todo"]["required_capabilities"] = ["filesystem_read", "network"]
    elif change == "stop":
        source["goal_boundary"]["stop_condition"] = "Stop before the fixture external action."
    else:
        turn_id = "another-capability-source"
    candidate = compose_turn_decision(source)
    changed = build_loopx_turn_plan(candidate, host="generic-cli",
        execution_mode="isolated-headless", turn_instance_id=turn_id)
    changed.update({key: deepcopy(plan[key])
                    for key in ("fixture_readback", "turn_context_contributions")})
    wire = build_loopx_turn_host_request(changed)
    assert wire["work_contract"] == candidate
    assert not {"fixture_context", "capability_instructions", "capability_result_fields"} & wire.keys()


@pytest.mark.parametrize("refresh_source", [False, True], ids=["original", "refreshed-caller"])
def test_recorded_complete_result_recovers_previous_optional_schema(complete_host_request, tmp_path, refresh_source):
    from loopx.control_plane.capability_hooks import (
        TurnContextHookRegistration, turn_context_source,
    )
    from loopx.control_plane.effect_runtime import effect_runtime_result
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    from loopx.control_plane.turn_driver.executor import (
        run_loopx_turn_once, validate_loopx_turn_host_result,
    )
    from loopx.control_plane.turn_driver.host_candidate import build_result
    from loopx.control_plane.turn_driver.journal_store import (
        LOOPX_TURN_JOURNAL_SCHEMA_VERSION, load_loopx_turn_plan_from_journal, turn_journal_path,
        write_turn_journal_checkpoint,
    )

    _, contract = complete_host_request
    source = deepcopy({key: value for key, value in contract.items()
                       if key not in {"schema_version", "contract_binding"}})
    source["selected_todo"]["required_capabilities"] = ["filesystem_read", "network"]
    contract = compose_turn_decision(source)
    plan = build_loopx_turn_plan(contract, host="generic-cli",
        execution_mode="isolated-headless", turn_instance_id="recorded-capability")
    goal_id, turn_key = contract["goal_id"], plan["transaction"]["turn_key"]
    runtime = Path(contract["runtime_root"])
    journal = turn_journal_path(runtime, goal_id=goal_id, turn_key=turn_key)
    plan = prepare_turn_decision_capture(plan, journal_path=journal,
        decision=contract, existing_journal=False)
    capture = Path(plan["detail_ref"]["captured_decision"]["path"])
    original = capture.read_bytes()
    result_fields = {"fixture_result": {"type": "string", "maxLength": 128}}
    output = {"readback": {"fixture_readback": "observed"},
        "context_fields": {"fixture_context": "Historical advice."},
        "instructions": ["Use the original observation."], "result_fields": result_fields}
    registration = TurnContextHookRegistration(
        "fixture.context", "fixture", ("fixture_readback",), lambda: output,
    )
    # Reproduce the released writer's basis using the historical governing view.
    contribution = effect_runtime_result("capability_hook.turn_context.validate", {
        "registration": registration.contract(), "result": output, "source": turn_context_source(plan),
    })
    plan.update(contribution.pop("readback"), turn_context_contributions=[contribution])
    request = build_loopx_turn_host_request(plan)
    assert "fixture_context" not in request and "capability_result_fields" not in request
    candidate = build_result({**request, "capability_result_fields": result_fields},
        {"result_kind": "validated_progress", "classification": "recorded_fixture_progress",
         "next_action": "Continue the original task.", "delivery_batch_scale": "implementation",
         "delivery_outcome": "outcome_progress", "work_evidence": ["fixture:artifact"],
         "validation_evidence": ["fixture:checked"], "fixture_result": "Observed evidence."})
    assert candidate["fixture_result"] == "Observed evidence."
    assert not validate_loopx_turn_host_result(plan, candidate)["ok"]
    for patch in ({"source_decision_hash": "sha256:" + "0" * 64}, {"turn_envelope": {}}):
        assert not validate_loopx_turn_host_result({**plan, **patch}, candidate, from_journal=True)["ok"]
    started = {
        "schema_version": LOOPX_TURN_JOURNAL_SCHEMA_VERSION, "status": "in_progress",
        "goal_id": goal_id, "turn_key": turn_key, "plan": plan, "host_attempt_count": 1,
        "completed_phases": [],
    }
    write_turn_journal_checkpoint(journal, started)
    write_turn_journal_checkpoint(journal, {
        **started, "completed_phases": candidate["completed_phases"], "host_result": candidate,
    })
    recovered = load_loopx_turn_plan_from_journal(runtime, goal_id=goal_id, turn_key=turn_key)
    calls = []
    checked_sources = []
    artifact_ready = not refresh_source

    def forbidden_host(_request):
        pytest.fail("a recorded typed result must not invoke the Host")

    def validate(validation_plan, result):
        assert result["fixture_result"] == candidate["fixture_result"]
        calls.append("validate")
        validation_source = validation_plan["work_contract"]
        checked_sources.append(validation_source["contract_binding"]["content_hash"])
        if "network" in validation_source["selected_todo"]["required_capabilities"] and not artifact_ready:
            return {"status": "failed", "validator_kind": "fixture",
                    "summary": "The original required artifact is absent."}
        return {"status": "progress", "validator_kind": "fixture",
                "summary": "Result retained.", "exit_code": 10}

    def writeback(result):
        assert result["fixture_result"] == candidate["fixture_result"]
        calls.append("writeback")
        return {"ok": True, "appended": True}

    def spend():
        calls.append("spend")
        return {"ok": True, "appended": True, "slots": 1}

    options = dict(host_runner=forbidden_host, project=tmp_path, runtime_root=runtime,
        goal_id=goal_id, timeout_seconds=10, execute=True, task_validator=validate,
        writeback=writeback, spend=spend, scheduler=lambda _: {"completed": True},
        require_decision_capture=True)
    retry_plan = recovered
    if refresh_source:
        first = run_loopx_turn_once(recovered, **options)
        assert first["status"] == "failed" and calls == ["validate"]
        source["selected_todo"]["required_capabilities"] = ["filesystem_read"]
        refreshed = compose_turn_decision(source)
        assert refreshed["contract_binding"]["content_hash"] != contract["contract_binding"]["content_hash"]
        retry_plan = build_loopx_turn_plan(refreshed, host="generic-cli",
            execution_mode="isolated-headless", turn_instance_id="recorded-capability")
        assert retry_plan["transaction"]["turn_key"] == turn_key
        failed = run_loopx_turn_once(retry_plan, retry_failed=True, **options)
        assert failed["status"] == "failed" and calls == ["validate"] * 2
        artifact_ready = True
    outcome = run_loopx_turn_once(retry_plan, retry_failed=refresh_source, **options)
    assert outcome["status"] == "committed", outcome.get("reason") or outcome
    expected_calls = ["validate"] * (3 if refresh_source else 1) + ["writeback", "spend"]
    assert calls == expected_calls
    assert checked_sources == [contract["contract_binding"]["content_hash"]] * calls.count("validate")
    assert run_loopx_turn_once(retry_plan, **options)["replayed"] is True
    assert calls == expected_calls
    assert capture.read_bytes() == original
    assert json.loads(journal.read_text())["plan"] == plan


def test_complete_wire_rejects_downgrade_mixed_carriers_and_capture_drift(complete_host_request, tmp_path):
    original = _captured_request(complete_host_request[1], tmp_path)
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
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision

    _, original = complete_host_request
    source = deepcopy({key: value for key, value in original.items()
                       if key not in {"schema_version", "contract_binding"}})
    source["observations"] = [1.0, -0.0, 1e-7, 2**60 + 1]
    contract = compose_turn_decision(source)
    request = _captured_request(contract, tmp_path)
    wire = complete_host_input(request)
    assert wire["schema_version"] == "loopx_turn_host_request_v1"
    restored = extract_turn_authority(wire)
    def encode(value):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    assert encode(restored["observations"]) == encode(source["observations"])
    assert encode(complete_host_input(wire)) == encode(wire)
