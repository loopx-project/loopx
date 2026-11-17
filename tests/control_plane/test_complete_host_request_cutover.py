"""Current Host producers share one input; recovery retains the original work."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
from loopx.control_plane.turn_driver.decision_capture import stage_turn_decision_capture
from loopx.control_plane.turn_driver.driver import (
    FailedTurnSessionRecoveryError,
    build_loopx_turn_plan,
    reconcile_failed_turn_session_request,
    turn_host_lineage,
)
from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
from loopx.control_plane.turn_driver.host_candidate import complete_host_input
from tests.control_plane.host_process_fixture import assert_host_task_context
from tests.control_plane.test_turn_host_decision_capture import (
    complete_host_request as complete_host_request,
)


def _plan(contract, *, scope="todo", turn="complete-request-cutover"):
    return build_loopx_turn_plan(contract,
        host="codex-cli" if scope == "agent" else "generic-cli",
        execution_mode="isolated-headless", session_scope=scope,
        turn_instance_id=turn)


def test_complete_source_can_plan_without_producing_a_legacy_adapter(complete_host_request):
    legacy, contract = complete_host_request
    before = deepcopy(contract)
    plan = build_loopx_turn_plan(contract, host="generic-cli",
        execution_mode="isolated-headless", session_scope="todo",
        turn_instance_id="source-only-plan")
    assert plan["ok"] is True
    assert plan["schema_version"] == "loopx_turn_plan_v1"
    assert plan["work_contract"] == contract and "turn_envelope" not in plan
    historical = _plan(contract, turn="source-only-plan")
    assert plan["route"] == historical["route"]
    assert plan["transaction"] == historical["transaction"]
    assert plan["session"] == historical["session"]
    request = build_loopx_turn_host_request(plan)
    assert request["schema_version"] == "loopx_turn_host_request_v1"
    assert request["work_contract"] == contract and "turn_envelope" not in request
    expected_lineage = turn_host_lineage(legacy)
    assert turn_host_lineage(request) == expected_lineage
    assert turn_host_lineage({"work_contract": contract}) == expected_lineage
    for invalid in ({"work_contract": {**contract, "goal_id": "other-goal"}},
                    {"work_contract": contract, "turn_envelope": None},
                    {"work_contract": contract, "turn_envelope": {}}):
        with pytest.raises((ValueError, RuntimeError)):
            turn_host_lineage(invalid)
    assert contract == before


def test_complete_plan_capture_and_journal_keep_one_original_source(
    complete_host_request, tmp_path,
):
    from loopx.control_plane.turn_driver.journal_store import (
        find_loopx_turn_key_by_settlement_identity, load_loopx_turn_plan_from_journal,
        turn_journal_path,
    )
    from loopx.control_plane.turn_driver.turn_journal_runtime import write_turn_journal

    _legacy, contract = complete_host_request
    runtime = Path(contract["runtime_root"])
    plan = build_loopx_turn_plan(contract, host="generic-cli",
        execution_mode="isolated-headless", session_scope="todo",
        turn_instance_id="source-plan-journal")
    assert plan["ok"] is True and "turn_envelope" not in plan
    key = plan["transaction"]["turn_key"]
    identity = plan["transaction"]["settlement_plan"]["identity"]
    path = turn_journal_path(runtime, goal_id=contract["goal_id"], turn_key=key)
    staged = stage_turn_decision_capture(plan, journal_path=path,
        decision=contract, existing_journal=False)
    published, wire = staged.publish_for_host(build_loopx_turn_host_request(plan))
    assert published["schema_version"] == "loopx_turn_plan_v1"
    assert "turn_envelope" not in published and "turn_envelope" not in wire
    assert published["work_contract"] == wire["work_contract"] == contract
    assert wire["detail_ref"]["captured_decision"]["turn_key"] == key
    journal = {"schema_version": "loopx_turn_journal_v0", "goal_id": contract["goal_id"],
        "turn_key": key, "status": "in_progress", "completed_phases": [],
        "host_attempt_count": 0, "plan": published}
    path.parent.mkdir(parents=True, exist_ok=True)
    assert write_turn_journal(str(path), journal, expected_effect_id=identity["effect_id"])["appended"]
    original = path.read_bytes()
    assert "turn_envelope" not in json.loads(original)["plan"]
    assert load_loopx_turn_plan_from_journal(runtime, goal_id=contract["goal_id"], turn_key=key) == published
    assert find_loopx_turn_key_by_settlement_identity(runtime, goal_id=identity["goal_id"],
        agent_id=identity["agent_id"], todo_id=identity["todo_id"],
        turn_instance_id=identity["turn_instance_id"]) == key
    assert write_turn_journal(str(path), journal, expected_effect_id=identity["effect_id"])["replayed"]
    assert path.read_bytes() == original
    assert json.loads(staged.path.read_text()) == contract


@pytest.mark.parametrize("mutation", ["missing", "null", "mixed", "future", "source_hash", "capture_turn"])
def test_current_plan_invalid_carrier_cannot_dispatch_or_recover(complete_host_request, tmp_path, mutation):
    from loopx.control_plane.turn_driver.driver import read_turn_execution_context, read_turn_plan_work
    from loopx.control_plane.turn_driver.lane_fence import turn_lane_agent_id

    _legacy, contract = complete_host_request
    plan = _plan(contract)
    if mutation == "missing":
        plan.pop("work_contract")
    elif mutation == "null":
        plan["work_contract"] = None
    elif mutation == "mixed":
        plan["turn_envelope"] = build_turn_envelope(contract)
    elif mutation == "future":
        plan["schema_version"] = "future_turn_plan"
    elif mutation == "source_hash":
        plan["source_decision_hash"] = "sha256:" + "0" * 64
    else:
        plan = stage_turn_decision_capture(plan, journal_path=tmp_path / "journal.json",
            decision=contract, existing_journal=False).plan
        plan["detail_ref"]["captured_decision"]["turn_key"] = "sha256:" + "0" * 64
    for reader in (build_loopx_turn_host_request, turn_host_lineage, read_turn_execution_context,
                   read_turn_plan_work, turn_lane_agent_id):
        with pytest.raises((ValueError, RuntimeError)):
            reader(plan)
    with pytest.raises(FailedTurnSessionRecoveryError):
        reconcile_failed_turn_session_request(plan, session_binding=None)
    assert not (tmp_path / "decisions").exists()  # staging is read-only


def test_current_host_request_producer_delivers_one_complete_source(complete_host_request):
    _legacy, contract = complete_host_request
    plan = _plan(contract)
    before = deepcopy(plan)
    request = build_loopx_turn_host_request(plan)
    assert request["schema_version"] == "loopx_turn_host_request_v1"
    assert "turn_envelope" not in request and "turn_authority" not in request
    assert request["work_contract"] == contract
    assert request["turn_key"] == plan["transaction"]["turn_key"]
    assert turn_host_lineage(request) == turn_host_lineage(plan)
    assert complete_host_input(request) == request
    assert plan == before


def test_disabled_memory_does_not_read_or_activate_complete_plan(complete_host_request, monkeypatch):
    from loopx.control_plane.reward_memory import reward_memory_automation_enabled

    _legacy, contract = complete_host_request
    plan = _plan(contract)
    monkeypatch.setattr("loopx.control_plane.quota.turn_envelope.complete_turn_host_source",
        lambda *_a, **_kw: pytest.fail("disabled capability must not read its source or provider"))
    for recall in ({}, {"experiment": {"enabled": False}}, {"experiment": {
        "enabled": True, "available": False, "configured_for_agent": True, "automatic_ingest": True}}):
        assert reward_memory_automation_enabled({**plan, "reward_memory_recall": recall},
                                                operation="automatic_ingest") is False


@pytest.mark.parametrize("damage", ["body", "identity", "mixed-adapter"])
def test_memory_participation_rejects_damaged_complete_source(complete_host_request, damage):
    from loopx.capabilities.reward_memory.turn_hooks import memory_context_result

    _, contract = complete_host_request
    source = deepcopy({key: value for key, value in contract.items()
                       if key not in {"schema_version", "contract_binding"}})
    policies = {"automatic_recall": True, "automatic_ingest": True}
    source.setdefault("goal_boundary", {})["capabilities"] = {"reward_memory": {
        "enabled": True, "configured_for_agent": True, "experiment_available": True, **policies,
    }}
    contract = compose_turn_decision(source)
    plan = _plan(contract)
    recall = {"experiment": {"goal_id": source["goal_id"],
        "agent_id": source["agent_identity"]["agent_id"],
        "enabled": True, "available": True, "configured_for_agent": True, **policies},
        "context": {"guidance": [{"content_summary": "Use the independent test evidence."}]}}
    intact = memory_context_result(plan, recall)
    assert intact["context_fields"]["reward_memory_recall"]["context"] == recall["context"]
    assert "reward_memory_reflection_json" in intact["result_fields"]

    damaged = deepcopy(plan)
    if damage == "body":
        damaged["work_contract"]["goal_boundary"]["write_scope"] = ["another-scope"]
    elif damage == "identity":
        damaged["source_decision_hash"] = "sha256:" + "0" * 64
    else:
        damaged["turn_envelope"] = build_turn_envelope(contract)
    outcome = memory_context_result(damaged, recall)
    assert outcome["context_fields"] == {} and outcome["instructions"] == []
    assert outcome["result_fields"] == {}
    assert outcome["readback"]["reward_memory_recall"]["context"] is None


def test_original_capture_publication_keeps_the_complete_request_version(
    complete_host_request, tmp_path,
):
    _legacy, contract = complete_host_request
    plan = _plan(contract)
    # Explicit conversion keeps the input to this boundary identical on both
    # old/new products, independently of the producer assertion above.
    incoming = complete_host_input(build_loopx_turn_host_request(plan))
    original = deepcopy(incoming)
    staged = stage_turn_decision_capture(plan, journal_path=tmp_path / "journal.json",
        decision=contract, existing_journal=False)
    published, request = staged.publish_for_host(incoming)
    assert request["schema_version"] == "loopx_turn_host_request_v1"
    assert "turn_envelope" not in request
    assert request == complete_host_input(build_loopx_turn_host_request(published))
    assert request["work_contract"] == contract and incoming == original
    detail = request["detail_ref"]["captured_decision"]
    assert detail["turn_key"] == request["turn_key"]
    assert detail["source_hash_ref"] == "$.source_decision_hash"
    assert json.loads(staged.path.read_text()) == contract


def test_original_historical_capture_restores_its_version_without_a_current_source(
    complete_host_request, tmp_path,
):
    _legacy, contract = complete_host_request
    incoming = complete_host_input(build_loopx_turn_host_request(_plan(contract, turn="historical-capture")))
    historical = {key: value for key, value in contract.items()
                  if key not in {"schema_version", "contract_binding"}}
    plan = build_loopx_turn_plan(build_turn_envelope(historical), host="generic-cli",
        execution_mode="isolated-headless", session_scope="todo", turn_instance_id="historical-capture")
    assert incoming["turn_key"] == plan["transaction"]["turn_key"]
    staged = stage_turn_decision_capture(plan, journal_path=tmp_path / "journal.json",
        decision=historical, existing_journal=False)
    _published, restored = staged.publish_for_host(incoming)
    assert restored["schema_version"] == "loopx_turn_host_request_v0"
    assert "work_contract" not in restored and "source_decision_hash" not in restored
    assert "detail_ref" not in restored
    assert restored["turn_envelope"] == staged.plan["turn_envelope"]
    assert json.loads(staged.path.read_text()) == historical


@pytest.mark.parametrize("scope", ["todo", "agent"])
def test_failed_complete_request_resumes_the_same_native_session(
    complete_host_request, tmp_path, scope,
):
    from loopx.control_plane.turn_driver import codex_sessions

    _legacy, contract = complete_host_request
    request = complete_host_input(build_loopx_turn_host_request(_plan(contract, scope=scope)))
    before = deepcopy(request)
    lineage = turn_host_lineage(request)
    binding = {"schema_version": "loopx_turn_session_binding_v0", **lineage,
               "host_session_id": "original-session"}
    runtime = tmp_path / "recovery-sessions"
    codex_sessions._store_codex_cli_session(runtime, lineage=lineage, session_scope=scope,
        session_id="original-session")
    stored = {p: p.read_bytes() for p in runtime.rglob("*.json")}
    recovered = reconcile_failed_turn_session_request(request, session_binding=binding)
    assert recovered["session"]["action"] == "resume"
    assert recovered["session"]["binding_status"] == "failed_turn_recovery"
    assert recovered["turn_key"] == request["turn_key"]
    assert codex_sessions.select_codex_cli_session(runtime, lineage=turn_host_lineage(recovered),
        session_scope=scope)["session_id"] == "original-session"
    assert "turn_envelope" not in recovered and recovered["work_contract"] == contract
    assert reconcile_failed_turn_session_request(recovered, session_binding=binding) == recovered
    assert request == before
    assert {p: p.read_bytes() for p in runtime.rglob("*.json")} == stored
    with pytest.raises(FailedTurnSessionRecoveryError):
        reconcile_failed_turn_session_request(request, session_binding={**binding, "agent_id": "other-agent"})
    if scope == "todo":
        with pytest.raises(FailedTurnSessionRecoveryError):
            reconcile_failed_turn_session_request(request, session_binding={**binding, "todo_id": "other-todo"})
    for bad in (None, {**contract, "reason": "source changed before recovery"}):
        with pytest.raises(FailedTurnSessionRecoveryError):
            reconcile_failed_turn_session_request({**request, "work_contract": bad}, session_binding=binding)


@pytest.mark.parametrize("request_scope", [False, True], ids=["replan", "request"])
def test_complete_todoless_scoped_recovery_preserves_its_original_identity(
    complete_host_request, tmp_path, request_scope,
):
    from loopx.control_plane.quota.settlement import build_turn_scoped_cli_settlement_plan
    from loopx.control_plane.turn_driver import codex_sessions
    from loopx.control_plane.quota.turn_envelope import complete_turn_host_source

    _legacy, original = complete_host_request
    goal, agent, obligation = original["goal_id"], original["agent_identity"]["agent_id"], "replan-" + "a" * 16
    settlement = build_turn_scoped_cli_settlement_plan(goal_id=goal, agent_id=agent,
        replan_obligation_id=None if request_scope else obligation,
        request_subject={"request_id": "a" * 64, "request_revision": 1,
            "request_digest": "b" * 64, "execution_scope_sha256": "c" * 64} if request_scope else None,
        turn_instance_id="fixture.replan", command_prefix="loopx",
        scoped_cli_args="", lifecycle_actor_args="").as_dict()
    contract = compose_turn_decision({"ok": True, "goal_id": goal,
        "agent_identity": {"agent_id": agent}, "decision": "run", "selected_todo": None,
        "should_run": True, "effective_action": "normal_run" if request_scope else "autonomous_replan_required",
        **({} if request_scope else {"replan_action_packet": {"schema_version": "replan_action_packet_v0",
            "decision": "replan_required", "obligation_id": obligation}}),
        "interaction_contract": {"schema_version": "loopx_interaction_contract_v0",
            "mode": "bounded_delivery" if request_scope else "autonomous_replan",
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {"must_attempt": True, "delivery_allowed": request_scope, "quiet_noop_allowed": False},
            "cli_channel": {"spend_after_validation": True, "settlement_plan": settlement}}})
    plan = _plan(contract, scope="agent", turn="fixture.replan")
    assert plan["transaction"]["settlement_plan"]["identity"] == settlement["identity"]
    wire = complete_host_input(build_loopx_turn_host_request(plan))
    binding = {"schema_version": "loopx_turn_session_binding_v0", "goal_id": goal,
               "agent_id": agent, "host_session_id": "original-replan-session"}
    runtime = tmp_path / "replan-sessions"
    codex_sessions._store_codex_cli_session(runtime, lineage=turn_host_lineage(wire),
        session_scope="agent", session_id="original-replan-session")
    stored = {p: p.read_bytes() for p in runtime.rglob("*.json")}
    assert complete_turn_host_source(wire, operation="driver_lineage")["scoped_settlement_identity"] == settlement["identity"]
    recovered = reconcile_failed_turn_session_request(wire, session_binding=binding)
    assert recovered["session"]["action"] == "resume"
    assert codex_sessions.select_codex_cli_session(runtime, lineage=turn_host_lineage(recovered),
        session_scope="agent")["session_id"] == "original-replan-session"
    assert recovered["work_contract"] == contract and "turn_envelope" not in recovered
    assert reconcile_failed_turn_session_request(recovered, session_binding=binding) == recovered
    assert {p: p.read_bytes() for p in runtime.rglob("*.json")} == stored


@pytest.mark.parametrize("adaptive", [False, True])
def test_complete_planning_retains_the_original_turn_key_codec(complete_host_request, adaptive):
    _legacy, original = complete_host_request
    source = deepcopy({key: value for key, value in original.items()
                       if key not in {"schema_version", "contract_binding"}})
    source["selected_todo"]["future_numeric_context"] = [1.0, -0.0, 1e-7, 2**60 + 1]
    source["selected_todo"]["future_text_context"] = "验收"
    if adaptive:
        source["task_orchestration_contract"] = {"schema_version": "task_orchestration_contract_v2",
            "mode": "adaptive", "primary_todo_id": source["selected_todo"]["todo_id"]}
    contract = compose_turn_decision(source)
    options = dict(host="generic-cli", execution_mode="isolated-headless", session_scope="todo",
                   turn_instance_id="numeric-codec")
    # The historical envelope path still supplies the persisted key preimage.
    original_plan = build_loopx_turn_plan(build_turn_envelope(contract), **options)
    current_plan = build_loopx_turn_plan(contract, **options)
    assert original_plan["ok"] and current_plan["ok"]
    assert current_plan["transaction"] == original_plan["transaction"]
    assert "turn_envelope" not in current_plan


def test_transitional_plan_keeps_original_quota_read_reference_without_a_fabricated_journal_key(
    complete_host_request, tmp_path,
):
    from loopx.control_plane.turn_driver.decision_capture import write_decision_capture
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority

    _legacy, contract = complete_host_request
    path = tmp_path / "original-source.json"
    write_decision_capture(path, contract)
    original = path.read_bytes()
    envelope = build_turn_envelope(contract, captured_decision_path=str(path))
    historical = build_loopx_turn_plan(envelope, host="generic-cli",
        execution_mode="isolated-headless", session_scope="todo", turn_instance_id="source-reader")
    # Read the recorded transitional shape, where the quota capture
    # predates the Host journal and carries no transaction key.
    request = build_loopx_turn_host_request({**historical, "work_contract": contract})
    wire = complete_host_input(request)
    capture = wire["detail_ref"]["captured_decision"]
    assert "turn_key" not in capture
    assert capture["turn_instance_id"] == contract.get("heartbeat_receipt", {}).get("turn_instance_id")
    assert capture["path"] == str(path) and wire["work_contract"] == contract
    model_input = extract_turn_authority(wire)["_model_input"]
    assert_host_task_context(model_input, contract)
    assert model_input["detail_ref"] == wire["detail_ref"]
    assert model_input["turn_key"] == wire["turn_key"]
    for field, value in (("goal_id", "other-goal"), ("agent_id", "other-agent"),
                         ("turn_instance_id", "other-turn"), ("path", "bad\0path"),
                         ("source_hash_ref", "$.unverified")):
        changed = deepcopy(wire)
        changed["detail_ref"]["captured_decision"][field] = value
        with pytest.raises((RuntimeError, ValueError)):
            extract_turn_authority(changed)
    assert path.read_bytes() == original


def test_real_callback_receives_published_complete_input_and_replay_has_no_effects(
    complete_host_request,
):
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once
    from loopx.control_plane.turn_driver.host_candidate import build_result

    _legacy, contract = complete_host_request
    plan = _plan(contract)
    project = Path(contract["registry"]).parent.parent
    artifact = project / "docs/turn-e2e-marker.txt"
    effects = []

    def host(request):
        effects.append("host")
        assert request["schema_version"] == "loopx_turn_host_request_v1"
        assert "turn_envelope" not in request and request["work_contract"] == contract
        original = Path(request["detail_ref"]["captured_decision"]["path"])
        assert json.loads(original.read_text()) == contract
        artifact.parent.mkdir(exist_ok=True)
        artifact.write_text("loopx-turn-real-e2e-step-1\n")
        return build_result(request, {"result_kind": "validated_progress",
            "classification": "complete_callback_source", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "next_action": "Continue the original synthetic task.",
            "work_evidence": ["artifact:synthetic-step-1"],
            "validation_evidence": ["validation:independent-marker-check"]})

    def validate(_plan, _result):
        effects.append("validation")
        assert artifact.read_text().strip() == "loopx-turn-real-e2e-step-1"
        return {"status": "progress", "validator_kind": "fixture", "summary": "Marker step is valid.", "exit_code": 10}

    def writeback(_result):
        effects.append("writeback")
        return {"ok": True, "appended": True}

    def spend():
        effects.append("spend")
        return {"ok": True, "appended": True, "slots": 1}

    def scheduler(_spend):
        effects.append("scheduler")
        return {"disposition": "outer_controller_owned", "completed": True,
                "acknowledged": False, "apply_needed": False}

    options = dict(host_runner=host, project=project, runtime_root=Path(contract["runtime_root"]),
        goal_id=contract["goal_id"], timeout_seconds=10, execute=True, task_validator=validate,
        writeback=writeback, spend=spend, scheduler=scheduler,
        require_decision_capture=True, decision_capture_payload=contract)
    result = run_loopx_turn_once(plan, **options)
    assert result["status"] == "committed" and result["quota_slot_spend_count"] == 1
    assert effects == ["host", "validation", "writeback", "spend", "scheduler"]
    replay = run_loopx_turn_once(plan, **options)
    assert replay["replayed"] and replay["status"] == "committed"
    assert effects == ["host", "validation", "writeback", "spend", "scheduler"]
