"""Complete-source integrity must survive every journal read/write boundary."""
from copy import deepcopy
import json
import sys

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.turn_driver.journal_store import (
    find_loopx_turn_key_by_settlement_identity, load_loopx_turn_plan_from_journal, turn_journal_path,
)
from loopx.control_plane.turn_driver.turn_journal_runtime import write_turn_journal
from tests.control_plane.canonical_authority_fixture import (
    initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos,
)
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _configure_read_only_todo, _run_cli,
    _spend_run_count, _write_fixture,
)


def _journal_plan(contract, *, legacy, goal_ref):
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan

    plan = build_loopx_turn_plan(build_turn_envelope(contract) if legacy else contract,
        host="generic-cli", execution_mode="isolated-headless", turn_instance_id="journal-source",
        goal_ref=goal_ref)
    if legacy:
        # Explicit recorded transitional format, independent of today's writer.
        assert plan["schema_version"] == "loopx_turn_plan_v0"
        plan["work_contract"] = contract
    else:
        assert plan["schema_version"] == "loopx_turn_plan_v1" and "turn_envelope" not in plan
    return plan


@pytest.fixture(params=[(provider, legacy) for provider in ("file", "sqlite") for legacy in (False, True)])
def complete_journal(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    state = _configure_read_only_todo(project)
    initialize_canonical_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode="soft_claim",
            todos=read_fixture_todos(state)), state_path=state, provider=request.param[0])
    code, plan = _run_cli(registry, runtime, "turn", "plan", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "journal-source",
        "--host", "generic-cli", "--execution-mode", "isolated-headless",
        "--include-transaction-detail", "--available-capability", "shell",
        "--available-capability", "filesystem_read", cwd=project)
    assert code == 0, plan
    assert plan["route"]["would_invoke_host"] is True
    assert plan["work_contract"]["schema_version"] == "loopx_turn_envelope_v1"
    # CLI inspection intentionally withholds executable settlement steps.
    # Use the ordinary executor planner to prepare the actual journal carrier.
    plan = _journal_plan(plan["work_contract"], legacy=request.param[1], goal_ref=plan.get("goal_ref"))
    key = plan["transaction"]["turn_key"]
    journal = {"schema_version": "loopx_turn_journal_v0", "goal_id": GOAL_ID,
        "turn_key": key, "status": "in_progress", "completed_phases": [],
        "host_attempt_count": 0, "plan": plan}
    before = state.read_bytes()
    yield project, runtime, registry, journal
    assert state.read_bytes() == before
    assert _spend_run_count(runtime) == 0


def _inspect(project, runtime, registry, key):
    code, result = _run_cli(registry, runtime, "turn", "inspect-journal", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-key", key, cwd=project)
    assert code == 0, result  # Invalid evidence is still a successful observation.
    return result


def test_complete_journal_readers_and_writer_refuse_source_drift(complete_journal):
    project, runtime, registry, original = complete_journal
    key = original["turn_key"]
    identity = original["plan"]["transaction"]["settlement_plan"]["identity"]
    path = turn_journal_path(runtime, goal_id=GOAL_ID, turn_key=key)
    path.parent.mkdir(parents=True, exist_ok=True)
    for mutation in ("content", "version", "null", "binding", "adapter", "interaction", "action", "reference"):
        journal = deepcopy(original)
        plan = journal["plan"]
        if mutation == "content":
            plan["work_contract"]["reason"] = "Changed complete source after admission"
        elif mutation == "version":
            plan["work_contract"]["schema_version"] = "future_turn_contract"
        elif mutation == "null":
            plan["work_contract"] = None
        elif mutation == "binding":
            plan["work_contract"].pop("contract_binding")
        elif mutation in {"interaction", "action", "reference"}:
            from tests.control_plane.test_complete_turn_decision import _resign_contract
            source = plan["work_contract"]
            if mutation == "interaction":
                source["interaction_contract"]["schema_version"] = "future_interaction"
            elif mutation == "action":
                source["effective_action"] = "future_action"
            else:
                source.setdefault("agent_lane_next_action", {}).pop("title", None)
                source["agent_lane_next_action"]["title_ref"] = "$.unavailable_original_requirement"
            _resign_contract(source)
        elif mutation == "adapter":
            # The adapter can be internally self-consistent yet differ from
            # the admitted source. Its own signature alone cannot qualify it.
            from tests.control_plane.test_complete_turn_decision import _canonical_digest
            from loopx.control_plane.quota.turn_envelope import turn_envelope_action_signature_document
            from loopx.control_plane.quota.turn_envelope import build_turn_envelope

            envelope = plan.setdefault("turn_envelope", build_turn_envelope(plan["work_contract"]))
            envelope["boundary"]["write_scope"] = ["different-artifact"]
            digest = _canonical_digest(turn_envelope_action_signature_document(envelope))
            envelope["action_signature"].update(source_hash=digest, envelope_hash=digest, matches=True)
        with pytest.raises(RuntimeError, match="work_contract_invalid"):
            write_turn_journal(str(path), journal, expected_effect_id=identity["effect_id"])
        assert not path.exists(), mutation
        # Corrupt or older files can bypass today's writer. Inspection and
        # settlement lookup must agree, with no repair or fresh admission.
        path.write_text(json.dumps(journal, ensure_ascii=False))
        before = path.read_bytes()
        result = _inspect(project, runtime, registry, key)
        assert result["journal_consistent"] is False, mutation
        assert "work_contract_invalid" in result["violations"], mutation
        assert result["recovery_decision"]["action"] == "blocked", mutation
        assert result["effects"] == []
        with pytest.raises((RuntimeError, ValueError)):
            load_loopx_turn_plan_from_journal(runtime, goal_id=GOAL_ID,
                agent_id=AGENT_ID, turn_key=key)
        with pytest.raises(ValueError, match="work_contract_invalid"):
            find_loopx_turn_key_by_settlement_identity(runtime, goal_id=GOAL_ID,
                agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id="journal-source")
        assert path.read_bytes() == before
        path.unlink()


def test_complete_journal_keeps_numeric_source_and_historical_decoder(complete_journal):
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope, compose_turn_decision
    from loopx.control_plane.turn_driver.decision_capture import prepare_turn_decision_capture
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan

    project, runtime, registry, journal = complete_journal
    plan = journal["plan"]
    prior = plan["work_contract"]
    source = {key: value for key, value in prior.items() if key not in {"schema_version", "contract_binding"}}
    source["future_measurements"] = [1.0, -0.0, 2**60 + 1]
    contract = compose_turn_decision(source)
    journal["plan"] = plan = _journal_plan(contract, legacy=plan["schema_version"] == "loopx_turn_plan_v0",
        goal_ref=plan.get("goal_ref"))
    key = journal["turn_key"]
    identity = plan["transaction"]["settlement_plan"]["identity"]
    path = turn_journal_path(runtime, goal_id=GOAL_ID, turn_key=key)
    path.parent.mkdir(parents=True, exist_ok=True)
    journal["plan"] = plan = prepare_turn_decision_capture(plan, journal_path=path,
        decision=contract, existing_journal=False)
    written = write_turn_journal(str(path), journal, expected_effect_id=identity["effect_id"])
    assert written["appended"] is True
    assert json.loads(path.read_text())["plan"]["work_contract"]["future_measurements"] == [1.0, -0.0, 2**60 + 1]
    assert load_loopx_turn_plan_from_journal(runtime, goal_id=GOAL_ID,
        agent_id=AGENT_ID, turn_key=key) == plan
    assert _inspect(project, runtime, registry, key)["journal_consistent"] is True
    assert find_loopx_turn_key_by_settlement_identity(runtime, goal_id=GOAL_ID,
        agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id="journal-source") == key
    replay = write_turn_journal(str(path), journal, expected_effect_id=identity["effect_id"])
    assert replay["replayed"] is True and replay["appended"] is False
    before = path.read_bytes()
    corrupted = json.loads(before)
    corrupted["plan"]["work_contract"]["future_measurements"][2] += 1
    path.write_text(json.dumps(corrupted))
    assert _inspect(project, runtime, registry, key)["journal_consistent"] is False
    path.write_bytes(before)
    assert _inspect(project, runtime, registry, key)["journal_consistent"] is True
    # Absence is the existing historical format, not a way for a supplied
    # null/unknown complete source to select that decoder.
    historical = deepcopy(journal)
    if plan["schema_version"] == "loopx_turn_plan_v1":
        # Missing source in a current plan is invalid, never a downgrade.
        historical["plan"].pop("work_contract")
        path.write_text(json.dumps(historical))
        assert _inspect(project, runtime, registry, key)["journal_consistent"] is False
        with pytest.raises(ValueError, match="complete Turn plan requires one source"):
            load_loopx_turn_plan_from_journal(runtime, goal_id=GOAL_ID,
                agent_id=AGENT_ID, turn_key=key)
        path.write_bytes(before)

    # A separate, genuine raw-source capture exercises the old decoder. Keep
    # the original complete observation bound to its own Turn.
    historical_plan = build_loopx_turn_plan(build_turn_envelope(source),
        host="generic-cli", execution_mode="isolated-headless", turn_instance_id="historical-journal-source",
        goal_ref=plan.get("goal_ref"))
    historical_key = historical_plan["transaction"]["turn_key"]
    historical_path = turn_journal_path(runtime, goal_id=GOAL_ID, turn_key=historical_key)
    historical["turn_key"] = historical_key
    historical["plan"] = prepare_turn_decision_capture(historical_plan, journal_path=historical_path,
        decision=source, existing_journal=False)
    assert "work_contract" not in historical["plan"]
    historical_path.write_text(json.dumps(historical))
    original_bytes = historical_path.read_bytes()
    assert load_loopx_turn_plan_from_journal(runtime, goal_id=GOAL_ID,
        agent_id=AGENT_ID, turn_key=historical_key) == historical["plan"]
    assert _inspect(project, runtime, registry, historical_key)["journal_consistent"] is True
    assert historical_path.read_bytes() == original_bytes
    assert path.read_bytes() == before


def test_cli_resume_refuses_changed_complete_source_without_rewriting_history(complete_journal):
    project, runtime, registry, journal = complete_journal
    journal["plan"]["work_contract"]["reason"] = "Changed original requirement after capture"
    key = journal["turn_key"]
    path = turn_journal_path(runtime, goal_id=GOAL_ID, turn_key=key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal))
    original_bytes = path.read_bytes()
    for command in (
        ("run-once", "--resume-turn-key", key, "--host", "generic-cli",
         "--execution-mode", "isolated-headless", "--project", str(project)),
        ("inspect-journal", "--turn-key", key, "--retry-failed-turn"),
    ):
        code, result = _run_cli(registry, runtime, "turn", *command,
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, cwd=project)
        assert code == 1 and result["ok"] is False, result
        assert path.read_bytes() == original_bytes


@pytest.mark.parametrize("complete_journal", [("file", True), ("sqlite", True)], indirect=True)
def test_legacy_journal_refuses_unknown_recorded_versions(complete_journal):
    from loopx.control_plane.turn_driver.transaction import TRANSACTION_PHASES

    project, runtime, registry, journal = complete_journal
    journal["plan"].pop("work_contract")
    journal.update(status="committed", completed_phases=list(TRANSACTION_PHASES))
    key = journal["turn_key"]
    path = turn_journal_path(runtime, goal_id=GOAL_ID, turn_key=key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal))
    resume = ("turn", "run-once", "--resume-turn-key", key,
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--host", "generic-cli",
        "--execution-mode", "isolated-headless", "--project", str(project),
        "--host-command-json", json.dumps([sys.executable, "-c", "raise SystemExit(99)"]))

    code, result = _run_cli(registry, runtime, *resume, cwd=project)
    assert code == 0 and result["status"] == "preview", result
    assert _inspect(project, runtime, registry, key)["journal_consistent"] is True

    for fields in (
        ("schema_version",),
        ("turn_envelope", "schema_version"),
        ("turn_envelope", "action_signature", "schema_version"),
        ("turn_envelope", "action_signature", "coverage"),
    ):
        invalid = deepcopy(journal)
        carrier = invalid["plan"]
        for field in fields[:-1]:
            carrier = carrier[field]
        carrier[fields[-1]] = "future_unsupported"
        path.write_text(json.dumps(invalid))
        saved = path.read_bytes()

        observed = _inspect(project, runtime, registry, key)
        assert observed["journal_consistent"] is False, fields
        assert "work_contract_invalid" in observed["violations"]
        assert observed["recovery_decision"]["action"] == "blocked"
        assert observed["effects"] == []
        for flags in ((), ("--execute",)):
            code, rejected = _run_cli(registry, runtime, *resume, *flags, cwd=project)
            assert code == 1 and rejected["ok"] is False, (fields, rejected)
            assert "unsupported Turn" in rejected["error"], rejected
        assert path.read_bytes() == saved


def test_managed_retry_reads_complete_source_with_original_numbers(complete_journal):
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    from loopx.control_plane.turn_driver.host_failure import build_host_failure_record
    from loopx.control_plane.turn_driver.managed_step import decide_managed_step
    from loopx.control_plane.turn_driver.transaction import validate_loopx_turn_receipt

    _, _, _, journal = complete_journal
    plan = journal["plan"]
    source = {key: value for key, value in plan["work_contract"].items()
              if key not in {"schema_version", "contract_binding"}}
    source["future_measurements"] = [1.0, -0.0, 2**60 + 1]
    journal["plan"] = plan = _journal_plan(compose_turn_decision(source),
        legacy=plan["schema_version"] == "loopx_turn_plan_v0", goal_ref=plan.get("goal_ref"))
    # The managed controller's current quota preview remains a distinct input;
    # never recreate a second carrier inside the current journal to supply it.
    quota = compose_turn_decision(source)
    result = {"schema_version": "loopx_turn_result_v0", "turn_key": journal["turn_key"],
        "result_kind": "host_failure", "completed_phases": [], "failed_phase": "host_execute"}
    journal.update(status="failed", result_kind="host_failure", host_attempt_count=1,
        host_failure=build_host_failure_record("provider_capacity", attempt=1),
        receipt=validate_loopx_turn_receipt(plan["transaction"], result))

    def decide():
        return decide_managed_step(journal, quota, goal_id=GOAL_ID,
            agent_id=AGENT_ID, turn_key=journal["turn_key"])

    original = deepcopy(journal)
    continuation = decide()
    assert continuation["disposition"] == "wait"
    assert continuation["retry_continuation"]["same_turn"] is True
    assert continuation["retry_continuation"]["retry_after_seconds"] == 30
    assert journal == original
    plan["work_contract"]["future_measurements"][2] += 1
    with pytest.raises(ValueError, match="work_contract_invalid"):
        decide()
