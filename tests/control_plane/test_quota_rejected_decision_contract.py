"""Rejected decisions share the complete transport without acquiring authority."""

from argparse import Namespace
from copy import deepcopy
import json

import pytest

from loopx.cli_commands.quota import _project_quota_cli_payload
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope,
    compose_turn_decision,
)
from loopx.control_plane.turn_driver.driver import build_loopx_turn_plan
from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
    read_fixture_todos,
)
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    _run_cli,
    _write_fixture,
    _heartbeat_receipt_count,
    _spend_run_count,
    _configure_selectable_alternative,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("profile", ["codex_app_heartbeat", "generic_cli"])
@pytest.mark.parametrize("rejection", ["missing", "capability", "reentry"])
def test_rejected_cli_decision_is_complete_and_keeps_original_effects(
    tmp_path, monkeypatch, provider, profile, rejection
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(
        tmp_path, required_capability="network" if rejection == "capability" else None
    )
    if rejection == "reentry":
        _configure_selectable_alternative(project)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    initialize_canonical_authority(
        runtime,
        GOAL_ID,
        build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode="soft_claim", todos=read_fixture_todos(state)
        ),
        state_path=state,
        provider=provider,
    )
    before = state.read_bytes()
    turn = "rejected-complete-contract"
    args = [
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--runtime-profile",
        profile,
        "--turn-instance-id",
        turn,
    ]
    original = None
    if rejection == "reentry":
        rc, original = _run_cli(registry, runtime, *args, cwd=project)
        assert rc == 0, original
    receipt_count = _heartbeat_receipt_count(runtime, turn)
    assert receipt_count == (1 if original else 0)
    for flag in ([], ["--turn-envelope"]):
        capture = tmp_path / ("flag" if flag else "ordinary")
        context = tmp_path / "context"
        rc, denied = _run_cli(
            registry,
            runtime,
            *args,
            *flag,
            "--todo-id",
            TODO_ID if rejection == "capability" else "todo_unavailable",
            "--decision-output-dir",
            str(capture),
            *(
                ["--context-delivery", "on", "--codex-context-root", str(context)]
                if profile == "codex_app_heartbeat"
                else []
            ),
            cwd=project,
        )
        assert rc == 1 and denied["ok"] is False and denied["should_run"] is False, (
            denied
        )
        assert denied["error_code"] == "quota_action_selection_rejected"
        captured = json.loads((capture / "decision.json").read_text())
        if profile == "codex_app_heartbeat":
            # Main's bounded refusal response points to the original complete
            # capture; it neither delivers work nor establishes a baseline.
            reference = denied["captured_decision"]
            assert reference["path"] == str(capture / "decision.json")
            assert denied["heartbeat_receipt"] == captured["heartbeat_receipt"]
            contents, offset = bytearray(), 0
            while True:
                read_rc, page = _run_cli(registry, runtime, "quota", "context-read",
                    "--capture-file", reference["path"], "--capture-sha256", reference["sha256"],
                    "--offset", str(offset), "--page-bytes", "64000", "--max-page-output-bytes", "64000")
                assert read_rc == 0 and page["sha256"] == reference["sha256"]
                assert page["offset"] == len(contents)
                contents.extend(page["content"].encode())
                if page["eof"]:
                    assert len(contents) == page["total_bytes"]
                    break
                offset = page["next_offset"]
            denied = json.loads(contents)
        assert denied["schema_version"] == "loopx_turn_envelope_v1"
        assert denied == captured
        assert "context_delivery" not in denied
        assert (
            not context.exists()
        )  # Refusal does not advance a host delivery baseline.
        assert denied["interaction_contract"]["agent_channel"]["must_attempt"] is False
        assert (
            denied["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
        )
        cli = denied["interaction_contract"]["cli_channel"]
        assert (
            cli["spend_allowed_now"] is False and cli["spend_after_validation"] is False
        )
        assert "settlement_plan" not in cli
        assert "selected_todo" not in denied
        assert (
            "selection_rejection" in denied
            or "action_selection_qualification" in denied
        )
        binding = denied["contract_binding"]["scheduler_execution_args"]
        from loopx.control_plane.quota.turn_envelope import _turn_envelope_result

        assert (
            _turn_envelope_result(
                "compose_complete", payload=denied, scheduler_execution_args=binding
            )
            == denied
        )
        envelope = build_turn_envelope(denied)
        plan = build_loopx_turn_plan(
            envelope,
            host="codex-cli",
            execution_mode="isolated-headless",
            turn_instance_id=turn,
        )
        assert plan["route"]["would_invoke_host"] is False
        if original:
            assert denied["heartbeat_receipt"]["status"] == "replayed"
            assert (
                denied["heartbeat_receipt"]["event_id"]
                == original["heartbeat_receipt"]["event_id"]
            )
            assert denied["heartbeat_receipt"].get("settlement_identity") == original[
                "heartbeat_receipt"
            ].get("settlement_identity")
        else:
            assert denied["heartbeat_receipt"]["status"] == "not_committed"
        assert _heartbeat_receipt_count(runtime, turn) == receipt_count
        assert _spend_run_count(runtime) == 0 and state.read_bytes() == before


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize(
    "code",
    [
        "QUOTA_VALIDATION_FAILED",
        "quota_unexpected_collection_error",
        "quota_agent_registry_roster_missing",
    ],
)
def test_predecision_failure_remains_diagnostic_without_legacy_view(flag, code):
    failure = {
        "ok": False,
        "mode": "should-run",
        "decision": "skip",
        "should_run": False,
        "error_code": code,
        "reason": "Original diagnosis",
        "recommended_action": "Original recovery",
        "diagnostic_evidence": {"unknown_fact": ["preserve", "ordered"]},
    }
    original = deepcopy(failure)
    result = _project_quota_cli_payload(
        failure,
        Namespace(quota_command="should-run", turn_envelope=flag),
        frozenset(),
        None,
    )
    assert result == original and failure == original
    assert "contract_binding" not in result and "interaction_contract" not in result
    with pytest.raises(RuntimeError, match="interaction_contract"):
        compose_turn_decision(result)


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("fault", [None, "action", "interaction"])
def test_unhealthy_paused_decision_keeps_stop_policy_in_complete_capture(
    tmp_path, flag, fault
):
    from loopx.cli_commands.quota import _emit_quota_result
    from loopx.quota import build_quota_should_run
    from test_quota_paused_precedence import (
        _paused_status,
        _assert_authoritatively_paused,
    )
    from test_quota_paused_precedence import (
        AGENT_ID as PAUSED_AGENT,
        GOAL_ID as PAUSED_GOAL,
    )
    from loopx.control_plane.scheduler.execution_context import (
        GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )

    status = _paused_status()
    status["contract"] = {"error_diagnostics": []}
    status["global_registry"] = {"ok": False}
    source = build_quota_should_run(
        status,
        goal_id=PAUSED_GOAL,
        agent_id=PAUSED_AGENT,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )
    assert source["ok"] is False and source["status_health_ok"] is False
    capture = tmp_path / "capture"
    capture.mkdir()
    emitted = []
    args = Namespace(
        quota_command="should-run",
        turn_envelope=flag,
        format="json",
        agent_id=PAUSED_AGENT,
    )

    def emit():
        return _emit_quota_result(
            source,
            args,
            context=None,
            registry_path=tmp_path / "registry.json",
            heartbeat_turn_id="paused-unhealthy",
            usage_quota_started=0,
            capture_directory=capture,
            detail_sections=frozenset(),
            print_payload=lambda value, *_: emitted.append(value),
        )

    if fault:
        if fault == "action":
            source["effective_action"] = "future_execution"
        else:
            source["interaction_contract"]["schema_version"] = "future_interaction"
        with pytest.raises(
            RuntimeError, match="effective_action|interaction_contract.schema_version"
        ):
            emit()
        assert emitted == [] and not (capture / "decision.json").exists()
        return
    rc = emit()
    assert rc == 1
    result = emitted[0]
    _assert_authoritatively_paused(result)
    assert result["schema_version"] == "loopx_turn_envelope_v1"
    assert result["ok"] is False and result["status_health_ok"] is False
    assert result == json.loads((capture / "decision.json").read_text())
    # Complete transport must not discard any health, authority or stop fact.
    assert {
        k: v
        for k, v in result.items()
        if k not in {"schema_version", "contract_binding"}
    } == source


@pytest.mark.parametrize("flag", [False, True])
def test_new_emission_cannot_fall_back_to_historical_views(monkeypatch, flag):
    from loopx.cli_commands import quota_presentation
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected
    from tests.test_turn_envelope import _full_decision

    source = _full_decision()
    original = deepcopy(source)
    complete = compose_turn_decision(source)
    args = Namespace(quota_command="should-run", turn_envelope=flag)

    def forbidden(*args, **kwargs):
        pytest.fail("new should-run emission must not call a historical renderer")

    monkeypatch.setattr(quota_presentation, "build_turn_envelope", forbidden)
    # Source and historical view can only enter through composition/recovery,
    # never silently become an alternative new model-facing contract.
    historical = build_turn_envelope(source)
    for uncomposed in (source, historical):
        with pytest.raises(EffectRuntimeRejected, match="requires a composed complete decision"):
            _project_quota_cli_payload(uncomposed, args, frozenset(), None)
    for detail in (frozenset(), frozenset({"vision", "agent-todos"})):
        result = _project_quota_cli_payload(complete, args, detail, None,
            captured_decision_path="/synthetic/decision.json")
        assert result is complete
        assert result["interaction_contract"] == original["interaction_contract"]
    assert source == original


@pytest.mark.parametrize("flag", [False, True])
@pytest.mark.parametrize("schema", [None, "loopx_turn_envelope_v1"])
def test_success_without_contract_is_rejected_before_capture_or_handoff(
    tmp_path, monkeypatch, flag, schema
):
    from loopx.cli_commands import quota
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected

    def forbidden(*args, **kwargs):
        pytest.fail("invalid decision must not reach capture, handoff or output")

    monkeypatch.setattr(quota, "capture_decision", forbidden)
    monkeypatch.setattr(quota, "handoff_decision", forbidden)
    with pytest.raises(EffectRuntimeRejected, match="requires a composed complete decision"):
        quota._emit_quota_result(
            {"ok": True, "should_run": True, "effective_action": "run",
                "schema_version": schema},
            Namespace(quota_command="should-run", turn_envelope=flag,
                format="json", agent_id="synthetic-agent"),
            context=None,
            registry_path=tmp_path / "registry.json",
            heartbeat_turn_id="missing-contract",
            usage_quota_started=0,
            capture_directory=tmp_path / "capture",
            detail_sections=frozenset(),
            print_payload=forbidden,
        )
