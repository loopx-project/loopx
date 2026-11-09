"""Complete-source session lookup uses the existing identity and session owners."""
from copy import deepcopy
import json
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver import codex_sessions
from loopx.control_plane.turn_driver.driver import turn_host_lineage
from tests.control_plane.test_turn_host_decision_capture import (
    complete_host_request as complete_host_request,
)


@pytest.mark.parametrize("host", ["codex-cli", "dsh"])
@pytest.mark.parametrize("scope", ["todo", "agent"])
def test_source_only_lookup_reuses_the_original_native_session(
    complete_host_request, tmp_path, host, scope, monkeypatch,
):
    request, contract = complete_host_request
    runtime = tmp_path / "session-runtime"
    lineage = turn_host_lineage(request)
    codex_sessions._store_codex_cli_session(runtime, lineage=lineage, session_scope=scope,
        host=host, session_id="original-session")
    expected = {"schema_version": "loopx_turn_session_binding_v0", **lineage}
    assert codex_sessions.codex_cli_session_binding(runtime, request["turn_envelope"],
        work_contract=contract, session_scope=scope, host=host) == expected
    before = {p: p.read_bytes() for p in runtime.rglob("*.json")}
    from loopx.control_plane.quota import turn_envelope
    invoke = turn_envelope._turn_envelope_result
    observed = []

    def record(operation, **kwargs):
        observed.append((operation, kwargs))
        return invoke(operation, **kwargs)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", record)
    for _ in range(2):
        assert codex_sessions.codex_cli_session_binding(runtime, work_contract=contract,
            session_scope=scope, host=host) == expected
    assert len(observed) == 2
    assert all(operation == "complete_host_lineage" and set(options) == {"payload"}
               for operation, options in observed)
    assert codex_sessions.select_codex_cli_session(runtime, lineage=lineage,
        session_scope=scope, host=host)["session_id"] == "original-session"
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("replacement", [None, False, {}, "invalid", "corrupted"])
def test_source_only_lookup_refuses_bad_source_without_a_legacy_fallback(
    complete_host_request, tmp_path, replacement,
):
    request, contract = complete_host_request
    runtime = tmp_path / "session-runtime"
    lineage = turn_host_lineage(request)
    codex_sessions._store_codex_cli_session(runtime, lineage=lineage,
        session_id="original-session")
    damaged = deepcopy(contract)
    damaged["reason"] = "changed after admission"
    value = damaged if replacement == "corrupted" else replacement
    before = {p: p.read_bytes() for p in runtime.rglob("*.json")}
    with pytest.raises((ValueError, RuntimeError, TypeError)):
        codex_sessions.codex_cli_session_binding(runtime, work_contract=value)
    assert {p: p.read_bytes() for p in before} == before
    # The existing historical API and its exact session namespace still work.
    assert codex_sessions.codex_cli_session_binding(runtime,
        request["turn_envelope"])["todo_id"] == lineage["todo_id"]


@pytest.mark.parametrize("adapter", [None, {}, False, "wrong-goal"])
def test_explicit_adapter_still_has_to_match_the_complete_source(complete_host_request, tmp_path, adapter):
    request, contract = complete_host_request
    invalid = deepcopy(request["turn_envelope"])
    invalid["goal_id"] = "other-goal"
    if adapter != "wrong-goal":
        invalid = adapter
    before = json.dumps(request, ensure_ascii=False, sort_keys=True)
    with pytest.raises((ValueError, RuntimeError, TypeError)):
        codex_sessions.codex_cli_session_binding(tmp_path, invalid, work_contract=contract)
    assert json.dumps(request, ensure_ascii=False, sort_keys=True) == before


def test_real_cli_session_lookup_uses_only_current_complete_source(
    complete_host_request, monkeypatch, capsys,
):
    from loopx.cli import main
    from loopx.control_plane import turn_driver
    from loopx.control_plane.turn_driver import codex_cli

    request, contract = complete_host_request
    runtime = Path(contract["runtime_root"])
    lineage = turn_host_lineage(request)
    codex_sessions._store_codex_cli_session(runtime, lineage=lineage,
        session_id="original-session", goal_ref=request.get("goal_ref"))
    before = {}
    for path in runtime.rglob("*.json"):
        value = json.loads(path.read_text())
        if isinstance(value, dict) and value.get("schema_version") == "loopx_codex_cli_session_v1":
            before[path] = path.read_bytes()
    assert len(before) == 1
    registry = json.loads(Path(contract["registry"]).read_text())
    goal = next(row for row in registry["goals"] if row.get("id", row.get("goal_id")) == lineage["goal_id"])
    # Resolve the lazy export before patching its owner, so later recovery
    # consumers cannot retain this test's transport probe on first import.
    original = turn_driver.codex_cli_session_binding
    assert original is codex_cli.codex_cli_session_binding
    observed = []
    bindings = []

    def lookup(*args, **kwargs):
        observed.append((args, kwargs))
        assert len(args) == 1 and "turn_envelope" not in kwargs
        binding = original(*args, **kwargs)
        bindings.append(binding)
        return binding

    monkeypatch.setattr(codex_cli, "codex_cli_session_binding", lookup)
    code = main(["--registry", contract["registry"], "--runtime-root", str(runtime),
        "--format", "json", "turn", "run-once", "--goal-id", lineage["goal_id"],
        "--agent-id", lineage["agent_id"], "--host", "codex-cli",
        "--project", goal["repo"],
        "--session-scope", "todo",
        "--execution-mode", "isolated-headless", "--iteration-context", "resume"])
    result = json.loads(capsys.readouterr().out)
    assert code == 0 and result["ok"], result
    assert turn_driver.codex_cli_session_binding is original
    assert len(observed) == 1
    assert observed[0][1]["work_contract"]["goal_id"] == lineage["goal_id"]
    assert bindings == [{"schema_version": "loopx_turn_session_binding_v0", **lineage}]
    assert result["dry_run"] and result["status"] == "preview"
    assert {p: p.read_bytes() for p in before} == before


def _failed_session_retry(original, tmp_path, *, historical=False):
    from loopx.control_plane.quota.turn_envelope import build_turn_envelope
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    from loopx.control_plane.turn_driver.executor import BuiltInHostError
    from loopx.control_plane.turn_driver.host_candidate import build_result
    from loopx.control_plane.turn_driver.journal_store import turn_journal_path

    _, contract = original
    plan = build_loopx_turn_plan(build_turn_envelope(contract) if historical else contract,
        host="generic-cli", execution_mode="isolated-headless",
        turn_instance_id="failed-session-source")
    goal, agent = contract["goal_id"], contract["agent_identity"]["agent_id"]
    todo = contract["selected_todo"]["todo_id"]
    runtime = tmp_path / "retry-runtime"
    artifact = tmp_path / "retry-result.txt"
    calls = dict(host=0, writeback=0, spend=0, scheduler=0)
    sources, actions = [], []

    def session_binding(source):
        sources.append(deepcopy(source))
        identity = source.get("agent_identity") if source.get("schema_version") == "loopx_turn_envelope_v1" else source
        if source.get("goal_id") != goal or not isinstance(identity, dict) or identity.get("agent_id") != agent:
            raise ValueError("the original Session belongs to another actor")
        return {"schema_version": "loopx_turn_session_binding_v0",
                "goal_id": goal, "agent_id": agent, "todo_id": todo}

    def host(request):
        calls["host"] += 1
        actions.append(request["session"]["action"])
        if not historical:
            assert request["work_contract"] == contract and "turn_envelope" not in request
        if calls["host"] == 1:
            raise BuiltInHostError("codex_cli_timeout", recovery_kind="resume_session")
        artifact.write_text("ready", encoding="utf-8")
        return build_result(request, {"result_kind": "validated_progress",
            "classification": "fixture_recovered", "summary": "The original work resumed.",
            "next_action": "Continue the original work.", "delivery_batch_scale": "implementation",
            "delivery_outcome": "outcome_progress", "vision_unchanged_reason": "The original task remains open.",
            "work_evidence": ["retry-result.txt contains ready"],
            "validation_evidence": ["The independent artifact check passed"]})

    def validate(_plan, _result):
        assert artifact.read_text(encoding="utf-8") == "ready"
        return {"status": "passed", "validator_kind": "fixture",
                "summary": "The resumed work produced the expected artifact."}

    def writeback(_result):
        calls["writeback"] += 1
        return {"ok": True, "appended": True, "classification": "fixture_recovered"}

    def spend():
        calls["spend"] += 1
        return {"ok": True, "appended": True, "slots": 1}

    def scheduler(_spend):
        calls["scheduler"] += 1
        return {"completed": True, "acknowledged": False, "disposition": "not_required"}

    common = dict(host_runner=host, session_binding_resolver=session_binding,
        project=tmp_path / "project", runtime_root=runtime, goal_id=goal,
        timeout_seconds=5, execute=True, task_validator=validate,
        writeback=writeback, spend=spend, scheduler=scheduler,
        **({} if historical else {"decision_capture_payload": contract, "require_decision_capture": True}))
    journal = turn_journal_path(runtime, goal_id=goal, turn_key=plan["transaction"]["turn_key"])
    return plan, common, calls, sources, actions, journal, artifact


@pytest.mark.parametrize(("historical", "refresh_source"), [(False, False), (False, True), (True, False)])
def test_failed_session_retry_delivers_original_source_and_settles_once(
    complete_host_request, tmp_path, historical, refresh_source,
):
    from loopx.control_plane.capability_hooks import TurnContextHookRegistration, dispatch_turn_context_hooks
    from loopx.control_plane.quota.turn_envelope import compose_turn_decision
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once, inspect_loopx_turn_journal
    from loopx.control_plane.turn_driver.journal_store import load_loopx_turn_plan_from_journal

    plan, common, calls, sources, actions, journal, artifact = _failed_session_retry(
        complete_host_request, tmp_path, historical=historical)
    contract = complete_host_request[1]
    before = deepcopy(contract)
    observed_advice = []
    if refresh_source:
        def contribute(candidate, text):
            dispatch_turn_context_hooks(candidate, (TurnContextHookRegistration(
                "fixture.context", "fixture", ("fixture_readback",),
                lambda: {"context_fields": {"fixture_context": text}, "instructions": [],
                         "result_fields": {}, "readback": {"fixture_readback": text}},
            ),))

        contribute(plan, "Advice for the original source.")
        run_host = common["host_runner"]

        def observe_host(request):
            observed_advice.append(request.get("fixture_context"))
            return run_host(request)

        common["host_runner"] = observe_host

    failed = run_loopx_turn_once(plan, **common)
    assert failed["status"] == "failed" and not artifact.exists()
    assert calls == dict(host=1, writeback=0, spend=0, scheduler=0)
    saved = load_loopx_turn_plan_from_journal(common["runtime_root"], goal_id=common["goal_id"],
        turn_key=plan["transaction"]["turn_key"])
    original_journal = journal.read_bytes()
    original_capture = {}
    if not historical:
        capture = Path(saved["detail_ref"]["captured_decision"]["path"])
        assert json.loads(capture.read_bytes()) == contract
        original_capture[capture] = capture.read_bytes()
    inspected = inspect_loopx_turn_journal(common["runtime_root"], goal_id=common["goal_id"],
        agent_id=contract["agent_identity"]["agent_id"], turn_key=plan["transaction"]["turn_key"],
        retry_failed=True, session_binding_resolver=common["session_binding_resolver"])
    assert inspected["recovery_decision"]["can_continue"] is True
    assert journal.read_bytes() == original_journal
    retry_plan = saved
    if refresh_source:
        changed = {key: deepcopy(value) for key, value in contract.items()
                   if key not in {"schema_version", "contract_binding"}}
        changed["selected_todo"]["required_capabilities"] = ["filesystem_read", "network"]
        refreshed = compose_turn_decision(changed)
        assert refreshed["contract_binding"]["content_hash"] != contract["contract_binding"]["content_hash"]
        retry_plan = build_loopx_turn_plan(refreshed, host="generic-cli",
            execution_mode="isolated-headless", turn_instance_id="failed-session-source")
        assert retry_plan["transaction"]["turn_key"] == saved["transaction"]["turn_key"]
        contribute(retry_plan, "Advice for the refreshed source.")
    recovered = run_loopx_turn_once(retry_plan, retry_failed=True, **common)
    assert recovered["status"] == "committed"
    assert actions == ["start_new", "resume"]
    expected = plan["turn_envelope"] if historical else contract
    if refresh_source:
        assert observed_advice == ["Advice for the original source."] * 2
    assert sources == [expected, expected]
    assert run_loopx_turn_once(saved, **common)["replayed"] is True
    assert calls == dict(host=2, writeback=1, spend=1, scheduler=1)
    assert artifact.read_text(encoding="utf-8") == "ready"
    assert contract == before
    assert all(path.read_bytes() == data for path, data in original_capture.items())


@pytest.mark.parametrize("field", ["goal_id", "agent_id", "todo_id"])
def test_complete_failed_retry_refuses_identity_drift_without_effects(
    complete_host_request, tmp_path, field,
):
    from loopx.control_plane.turn_driver.executor import run_loopx_turn_once, inspect_loopx_turn_journal
    from loopx.control_plane.turn_driver.recovery import TurnRecoveryBlockedError

    plan, common, calls, sources, _actions, journal, artifact = _failed_session_retry(complete_host_request, tmp_path)
    run_loopx_turn_once(plan, **common)
    original = journal.read_bytes()
    resolver = common["session_binding_resolver"]

    def drifted(source):
        return {**resolver(source), field: "another-identity"}

    common["session_binding_resolver"] = drifted
    inspected = inspect_loopx_turn_journal(common["runtime_root"], goal_id=common["goal_id"],
        agent_id=complete_host_request[1]["agent_identity"]["agent_id"],
        turn_key=plan["transaction"]["turn_key"], retry_failed=True, session_binding_resolver=drifted)
    assert inspected["recovery_decision"]["reason"] == "session_binding_identity_mismatch"
    with pytest.raises(TurnRecoveryBlockedError):
        run_loopx_turn_once(plan, retry_failed=True, **common)
    assert sources == [complete_host_request[1], complete_host_request[1]]
    assert journal.read_bytes() == original and not artifact.exists()
    assert calls == dict(host=1, writeback=0, spend=0, scheduler=0)


@pytest.mark.parametrize("fault", ["missing", "null", "changed", "mixed", "future"])
def test_failed_retry_never_resolves_an_invalid_current_source(complete_host_request, fault):
    from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
    from loopx.control_plane.turn_driver.executor import build_loopx_turn_host_request
    from loopx.control_plane.turn_driver.session_recovery import assess_failed_turn_retry_request, build_host_recovery_record

    legacy, contract = complete_host_request
    plan = build_loopx_turn_plan(contract, host="generic-cli", execution_mode="isolated-headless")
    request = build_loopx_turn_host_request(plan)
    if fault == "missing":
        request.pop("work_contract")
    elif fault == "null":
        request["work_contract"] = None
    elif fault == "changed":
        request["work_contract"] = {**contract, "reason": "changed after capture"}
    elif fault == "mixed":
        request["turn_envelope"] = legacy["turn_envelope"]
    else:
        request["schema_version"] = "loopx_turn_host_request_v999"
    journal = {"host_recovery": build_host_recovery_record("resume_session"),
               "receipt": {"failed_phase": "host_execute"}}
    before = deepcopy((request, journal))
    observed = []
    _resolved, check, error = assess_failed_turn_retry_request(request, journal,
        session_binding_resolver=lambda source: observed.append(source))
    assert error is not None and check["reason"] == "binding_resolution_failed"
    assert observed == [] and (request, journal) == before


def test_historical_retry_callback_keeps_its_recorded_envelope(complete_host_request):
    from loopx.control_plane.turn_driver.session_recovery import assess_failed_turn_retry_request, build_host_recovery_record

    legacy, _contract = complete_host_request
    assert legacy["schema_version"] == "loopx_turn_host_request_v0" and "work_contract" in legacy
    observed = []

    def resolver(source):
        observed.append(deepcopy(source))
        return {"schema_version": "loopx_turn_session_binding_v0",
            "goal_id": source["goal_id"], "agent_id": source["agent_id"],
            "todo_id": source["action"]["selected_todo"]["todo_id"]}

    before = deepcopy(legacy)
    journal = {"host_recovery": build_host_recovery_record("resume_session"),
               "receipt": {"failed_phase": "host_execute"}}
    recovered, check, error = assess_failed_turn_retry_request(legacy, journal,
        session_binding_resolver=resolver)
    assert error is None and check["outcome"] == "passed"
    assert observed == [legacy["turn_envelope"]]
    assert recovered["session"]["action"] == "resume" and legacy == before
