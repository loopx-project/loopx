"""Decision-table tests for the pure Turn Loop Controller transition.

Each row pairs one validated Turn receipt with one fresh quota/scheduler
decision and asserts exactly one typed disposition. The controller must never
launch a host, write state, or spend quota; every row also asserts those
markers.

Receipts are qualified from the public Turn execution shape. Material results
must prove the M7 validation/writeback/spend receipt sequence, durable effects,
and completed scheduler handoff; completion must also carry its Todo lifecycle
outcome. Phase strings alone are intentionally insufficient.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from loopx.control_plane.turn_driver import (
    LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
    LOOPX_TURN_RESULT_SCHEMA_VERSION,
    LoopXTurnResultKind,
    build_loopx_turn_transaction_plan,
    validate_loopx_turn_receipt,
)
from loopx.control_plane.turn_driver.loop_controller import (
    BOUNDED_TURN_BUDGET_SCHEMA_VERSION,
    LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION,
    VALIDATED_TURN_RECEIPT_SCHEMA_VERSION,
    BoundedTurnBudget,
    ValidatedTurnReceipt,
)
from loopx.control_plane.turn_driver.host_failure import build_host_failure_record
from loopx.control_plane.turn_driver.loop_controller import (
    decide_loop_disposition as _decide_loop_disposition,
)

_ALL_PHASES = [
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
    "scheduler_apply",
    "scheduler_ack",
]


def _lineage(
    *,
    goal_id: str = "goal-1",
    agent_id: str = "agent-1",
    todo_id: str = "todo-1",
) -> dict[str, str]:
    return {"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id}


def _plan(lineage: dict[str, str] | None = None) -> dict[str, object]:
    return build_loopx_turn_transaction_plan(
        planned=True,
        lineage=_lineage() if lineage is None else lineage,
        host="codex-cli",
        execution_mode="interactive-visible",
        session_action="resume",
    )


def _result(
    plan: dict[str, object],
    *,
    result_kind: LoopXTurnResultKind = LoopXTurnResultKind.VALIDATED_PROGRESS,
    completed_phases: list[str] | None = None,
    failed_phase: str | None = None,
) -> dict[str, Any]:
    result: dict[str, object] = {
        "schema_version": LOOPX_TURN_RESULT_SCHEMA_VERSION,
        "turn_key": plan["turn_key"],
        "result_kind": result_kind.value,
        "completed_phases": (
            completed_phases
            if completed_phases is not None
            else list(_ALL_PHASES)
        ),
    }
    if failed_phase:
        result["failed_phase"] = failed_phase
    return result


_FAILURE_PHASES = {
    LoopXTurnResultKind.HOST_FAILURE: ([], "host_execute"),
    LoopXTurnResultKind.VALIDATION_FAILED: (
        ["host_execute", "typed_result"],
        "validation",
    ),
    LoopXTurnResultKind.WRITEBACK_FAILED: (
        ["host_execute", "typed_result", "validation"],
        "durable_writeback",
    ),
    LoopXTurnResultKind.QUOTA_SPEND_FAILED: (
        ["host_execute", "typed_result", "validation", "durable_writeback"],
        "quota_spend",
    ),
}
_STOP_KINDS = {
    LoopXTurnResultKind.WAIT,
    LoopXTurnResultKind.USER_ACTION_REQUIRED,
    LoopXTurnResultKind.ITERATION_FAILED,
}


def _validated_receipt(
    *,
    result_kind: LoopXTurnResultKind = LoopXTurnResultKind.VALIDATED_PROGRESS,
    lineage: dict[str, str] | None = None,
    completed_phases: list[str] | None = None,
    failed_phase: str | None = None,
    continuation: str = "active_goal",
    successor_todo_ids: list[str] | None = None,
    host_failure: dict[str, object] | None = None,
) -> ValidatedTurnReceipt:
    return ValidatedTurnReceipt.from_execution(
        _execution(
            result_kind=result_kind,
            lineage=lineage,
            completed_phases=completed_phases,
            failed_phase=failed_phase,
            continuation=continuation,
            successor_todo_ids=successor_todo_ids,
            host_failure=host_failure,
        )
    )


def _execution(
    *,
    result_kind: LoopXTurnResultKind = LoopXTurnResultKind.VALIDATED_PROGRESS,
    lineage: dict[str, str] | None = None,
    completed_phases: list[str] | None = None,
    failed_phase: str | None = None,
    continuation: str = "active_goal",
    successor_todo_ids: list[str] | None = None,
    host_failure: dict[str, object] | None = None,
) -> dict[str, object]:
    plan = _plan(lineage=lineage)
    if completed_phases is None and result_kind in _FAILURE_PHASES:
        completed_phases, failed_phase = _FAILURE_PHASES[result_kind]
    elif completed_phases is None and result_kind in _STOP_KINDS:
        completed_phases = ["host_execute", "typed_result"]
    result = _result(
        plan,
        result_kind=result_kind,
        completed_phases=completed_phases,
        failed_phase=failed_phase,
    )
    validation = validate_loopx_turn_receipt(plan, result)
    assert validation["ok"] is True, validation
    material = result_kind in {
        LoopXTurnResultKind.VALIDATED_COMPLETION,
        LoopXTurnResultKind.VALIDATED_PROGRESS,
    }
    effect_id = str(validation.get("settlement_effect_id") or "")
    execution: dict[str, Any] = {
        "ok": validation["ok"],
        "schema_version": LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
        "status": "committed" if material else validation["status"],
        "result_kind": result_kind.value,
        "receipt": validation,
        "effects": {
            "state_written": material,
            "quota_spent": material,
        },
        "scheduler": {"completed": material},
    }
    if material:
        execution["settlement_result"] = {
            "ok": True,
            "failure": None,
            "receipts": [
                {
                    "step_kind": step_kind,
                    "status": "committed",
                    "effect_id": effect_id,
                }
                for step_kind in ("validation", "durable_writeback", "quota_spend")
            ],
        }
    if result_kind is LoopXTurnResultKind.VALIDATED_COMPLETION:
        execution["todo_completion"] = {
            "todo_id": str(validation["lineage"]["todo_id"]),
            "continuation": continuation,
            **(
                {"successor_todo_ids": successor_todo_ids}
                if successor_todo_ids is not None
                else {}
            ),
        }
    if host_failure is not None:
        execution["host_failure"] = host_failure
    return execution


def _envelope(
    *,
    should_run: bool,
    effective_action: str = "deliver",
    delivery_allowed: bool = True,
    must_attempt: bool = True,
    user_action_required: bool = False,
    quiet_noop_allowed: bool = False,
    lineage: dict[str, str] | None = None,
    signature_matches: bool = True,
    predecessor_turn_key: str | None = None,
    state: str | None = None,
    selected_todo_id: str | None = "from_lineage",
    adaptive_primary_todo_id: str | None = None,
) -> dict[str, object]:
    lin = _lineage() if lineage is None else lineage
    signature: dict[str, object] = {"matches": signature_matches}
    if signature_matches:
        signature["source_hash"] = "sha256:test"
        signature["envelope_hash"] = "sha256:test"
    selected_todo = (
        {"todo_id": lin["todo_id"]}
        if selected_todo_id == "from_lineage"
        else ({"todo_id": selected_todo_id} if selected_todo_id else None)
    )
    envelope: dict[str, object] = {
        "schema_version": "loopx_turn_envelope_v0",
        "goal_id": lin["goal_id"],
        "agent_id": lin["agent_id"],
        "should_run": should_run,
        "effective_action": effective_action,
        "action_signature": signature,
        "compaction": {"within_budget": True},
        "action": {
            "delivery_allowed": delivery_allowed,
            "must_attempt": must_attempt,
            "quiet_noop_allowed": quiet_noop_allowed,
            "selected_todo": selected_todo,
        },
        "user": {"action_required": user_action_required},
    }
    if state is not None:
        envelope["state"] = state
    if predecessor_turn_key is not None:
        envelope["predecessor_turn_key"] = predecessor_turn_key
    if adaptive_primary_todo_id is not None:
        envelope["task_orchestration_contract"] = {
            "schema_version": "task_orchestration_contract_v2",
            "mode": "adaptive",
            "primary_todo_id": adaptive_primary_todo_id,
        }
    return envelope


def _budget(
    *,
    max_turns: int = 3,
    completed_turns: int = 1,
    lineage: dict[str, str] | None = None,
) -> BoundedTurnBudget:
    return BoundedTurnBudget(
        lineage=_lineage() if lineage is None else lineage,
        max_turns=max_turns,
        completed_turns=completed_turns,
    )


def test_capability_handoff_after_progress_keeps_predecessor_and_actor_binding() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    envelope = _envelope(should_run=True, effective_action="governed_capability_intent",
                         selected_todo_id=None, predecessor_turn_key=receipt.turn_key)
    envelope["action"]["capability_intent"] = {
        "schema_version": "pending_capability_intent_projection_v0",
        "goal_id": envelope["goal_id"], "agent_id": envelope["agent_id"],
        "command": "fixture capability command",
    }
    result = decide_loop_disposition(turn_receipt=receipt, quota_decision=envelope)
    _assert_markers(result, "capability_action_required")
    envelope["predecessor_turn_key"] = "stale-predecessor"
    with pytest.raises(ValueError):
        decide_loop_disposition(turn_receipt=receipt, quota_decision=envelope)
    envelope["predecessor_turn_key"] = receipt.turn_key
    envelope["action"]["capability_intent"]["agent_id"] = "another-agent"
    with pytest.raises(ValueError):
        decide_loop_disposition(turn_receipt=receipt, quota_decision=envelope)


def decide_loop_disposition(
    *,
    turn_receipt: ValidatedTurnReceipt | None,
    quota_decision: dict[str, object],
    bounded_turn_budget: BoundedTurnBudget | None = None,
) -> dict[str, object]:
    """Test adapter that binds the fresh decision to its predecessor."""

    predecessor_turn_key = str(quota_decision.get("predecessor_turn_key") or "")
    return _decide_loop_disposition(
        turn_receipt=turn_receipt,
        quota_decision=quota_decision,
        predecessor_turn_key=predecessor_turn_key or None,
        bounded_turn_budget=bounded_turn_budget,
    )


def _assert_markers(payload: dict[str, object], disposition: str) -> None:
    assert payload["schema_version"] == LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION
    assert payload["disposition"] == disposition
    assert payload["spends_quota"] is False
    assert payload["launches_host"] is False
    assert payload["writes_state"] is False


def test_no_receipt_with_delivery_decision_runs_now() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(should_run=True),
    )
    _assert_markers(payload, "run_now")


def test_no_receipt_with_quiet_decision_waits_no_spend() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(should_run=False, quiet_noop_allowed=True),
    )
    _assert_markers(payload, "wait")


def test_no_receipt_user_gate_without_selected_todo_is_valid() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(
            should_run=False,
            user_action_required=True,
            selected_todo_id=None,
        ),
    )

    _assert_markers(payload, "user_action_required")
    assert payload["lineage"]["todo_id"] == ""


def test_no_receipt_quiet_wait_without_selected_todo_is_valid() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(
            should_run=False,
            quiet_noop_allowed=True,
            selected_todo_id=None,
        ),
    )

    _assert_markers(payload, "wait")
    assert payload["lineage"]["todo_id"] == ""


def test_no_receipt_delivery_without_selected_todo_fails_closed() -> None:
    with pytest.raises(ValueError, match="missing selected Todo"):
        decide_loop_disposition(
            turn_receipt=None,
            quota_decision=_envelope(should_run=True, selected_todo_id=None),
        )


def test_adaptive_primary_todo_owns_controller_lineage() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(
            should_run=True,
            selected_todo_id="todo-child",
            adaptive_primary_todo_id="todo-primary",
        ),
    )

    _assert_markers(payload, "run_now")
    assert payload["lineage"]["todo_id"] == "todo-primary"


def test_no_receipt_terminal_requires_fresh_goal_frontier_state() -> None:
    with pytest.raises(ValueError, match="Goal frontier state"):
        decide_loop_disposition(
            turn_receipt=None,
            quota_decision=_envelope(
                should_run=False,
                effective_action="terminal_no_followup",
            ),
        )


def test_validated_no_followup_is_terminal_with_fresh_goal_evidence() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation="no_followup",
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=False,
            effective_action="terminal_no_followup",
            predecessor_turn_key=receipt.turn_key,
            state="terminal_no_followup",
        ),
    )
    _assert_markers(payload, "terminal")


def test_validated_completion_runs_declared_successor() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation="successor",
        successor_todo_ids=["todo-2"],
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True,
            lineage=_lineage(todo_id="todo-2"),
            predecessor_turn_key=receipt.turn_key,
        ),
    )
    _assert_markers(payload, "run_now")


def test_validated_completion_continues_from_fresh_active_goal_frontier() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation="active_goal",
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True,
            lineage=_lineage(todo_id="todo-2"),
            predecessor_turn_key=receipt.turn_key,
        ),
    )
    _assert_markers(payload, "run_now")


def test_active_goal_completion_cannot_reselect_completed_todo() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation="active_goal",
    )
    with pytest.raises(ValueError, match="reselected the completed Todo"):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(
                should_run=True,
                predecessor_turn_key=receipt.turn_key,
            ),
        )


def test_validated_progress_with_budget_runs_now() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
        bounded_turn_budget=_budget(max_turns=3, completed_turns=1),
    )
    _assert_markers(payload, "run_now")


def test_validated_progress_with_exhausted_budget_requires_replan() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
        bounded_turn_budget=_budget(max_turns=3, completed_turns=3),
    )
    _assert_markers(payload, "replan")
    assert "budget" in str(payload["reason"])
    assert payload["replan_continuation"]["requires_bounded_delta"] is True


def test_validated_progress_without_delivery_decision_waits() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=False,
            quiet_noop_allowed=True,
            predecessor_turn_key=receipt.turn_key,
        ),
        bounded_turn_budget=_budget(max_turns=3, completed_turns=1),
    )
    _assert_markers(payload, "wait")


def test_validated_progress_without_bounded_budget_raises() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    with pytest.raises(ValueError, match="bounded turn budget"):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(
                should_run=True, predecessor_turn_key=receipt.turn_key
            ),
        )


def test_repair_receipt_routes_to_repair() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.REPAIR_REQUIRED)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )
    _assert_markers(payload, "repair")


def test_replan_receipt_requires_bounded_delta_before_successor() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.REPLAN_REQUIRED)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True,
            effective_action="autonomous_replan",
            predecessor_turn_key=receipt.turn_key,
        ),
    )
    _assert_markers(payload, "replan")
    continuation = payload["replan_continuation"]
    assert continuation["requires_bounded_delta"] is True
    assert continuation["stale_todo_rerun_allowed"] is False
    assert continuation["fresh_envelope_required"] is True
    assert "todo_delta" in continuation["delta_kinds"]


def test_replan_decision_without_receipt_also_requires_delta() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(
            should_run=True, effective_action="autonomous_replan_required"
        ),
    )
    _assert_markers(payload, "replan")
    assert payload["replan_continuation"]["stale_todo_rerun_allowed"] is False


def test_user_action_from_receipt_wins() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.USER_ACTION_REQUIRED
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )
    _assert_markers(payload, "user_action_required")


def test_user_action_from_decision_wins_even_with_receipt() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True,
            user_action_required=True,
            predecessor_turn_key=receipt.turn_key,
        ),
        bounded_turn_budget=_budget(max_turns=3, completed_turns=1),
    )
    _assert_markers(payload, "user_action_required")


def test_receipt_backed_user_gate_without_selected_todo_preserves_lineage() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=False,
            user_action_required=True,
            predecessor_turn_key=receipt.turn_key,
            selected_todo_id=None,
        ),
    )

    _assert_markers(payload, "user_action_required")
    assert payload["lineage"]["todo_id"] == receipt.lineage["todo_id"]


def test_validated_no_followup_wins_over_decision_user_action() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation="no_followup",
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=False,
            effective_action="terminal_no_followup",
            user_action_required=True,
            predecessor_turn_key=receipt.turn_key,
            state="terminal_no_followup",
        ),
    )
    _assert_markers(payload, "terminal")
    assert "terminal closure" in str(payload["reason"])


def test_wait_receipt_waits() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.WAIT)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )
    _assert_markers(payload, "wait")


def test_iteration_failure_stops_without_retry_or_successor() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.ITERATION_FAILED)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )

    _assert_markers(payload, "stop")
    assert payload["stop_scope"] == "iteration"
    assert payload["goal_terminal"] is False
    assert payload["continuation_required"] is False
    assert "retry_continuation" not in payload
    assert "replan_continuation" not in payload


@pytest.mark.parametrize(
    "failure_kind",
    ["host_failure", "validation_failed", "writeback_failed", "quota_spend_failed"],
)
def test_failure_receipts_route_to_repair(failure_kind: str) -> None:
    kind = LoopXTurnResultKind(failure_kind)
    receipt = _validated_receipt(result_kind=kind)
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )
    _assert_markers(payload, "repair")
    assert failure_kind in str(payload["reason"])


@pytest.mark.parametrize(
    ("failure_kind", "backoff_seconds"),
    [
        ("provider_capacity", 30),
        ("provider_overloaded", 30),
        ("rate_limited", 60),
    ],
)
def test_retryable_host_failure_waits_with_same_turn_backoff(
    failure_kind: str,
    backoff_seconds: int,
) -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.HOST_FAILURE,
        host_failure=build_host_failure_record(failure_kind, attempt=1),
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )

    _assert_markers(payload, "wait")
    assert payload["retry_continuation"] == {
        "same_turn": True,
        "retry_failed_turn": True,
        "strategy": "same_configuration",
        "retry_after_seconds": backoff_seconds,
        "attempt": 1,
        "max_attempts": 3,
        "fresh_envelope_required": True,
        "model_fallback_allowed": False,
    }


@pytest.mark.parametrize(
    "failure_kind",
    ["provider_capacity", "provider_overloaded", "rate_limited"],
)
def test_exhausted_host_retry_budget_routes_to_repair(failure_kind: str) -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.HOST_FAILURE,
        host_failure=build_host_failure_record(failure_kind, attempt=3),
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )

    _assert_markers(payload, "repair")
    assert "exhausted" in str(payload["reason"])


def test_exhausted_quota_failure_routes_to_repair_without_retry_metadata() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.HOST_FAILURE,
        host_failure={
            "schema_version": "loopx_turn_host_failure_v0",
            "kind": "quota_exhausted",
            "attempt": 1,
            "retryable": False,
        },
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True, predecessor_turn_key=receipt.turn_key
        ),
    )

    _assert_markers(payload, "repair")
    assert "retry_continuation" not in payload


def test_output_budget_failure_routes_to_repair_without_retry_metadata() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.HOST_FAILURE,
        host_failure=build_host_failure_record(
            "output_budget_exhausted",
            attempt=1,
        ),
    )
    payload = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True,
            predecessor_turn_key=receipt.turn_key,
        ),
    )

    _assert_markers(payload, "repair")
    assert "retry_continuation" not in payload


def test_stale_todo_receipt_raises_not_terminal() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        lineage=_lineage(todo_id="todo-old"),
        continuation="successor",
        successor_todo_ids=["todo-expected"],
    )
    with pytest.raises(ValueError, match="stale_receipt"):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(
                should_run=True,
                lineage=_lineage(todo_id="todo-new"),
                predecessor_turn_key=receipt.turn_key,
            ),
        )


def test_stale_agent_receipt_raises() -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS,
        lineage=_lineage(agent_id="agent-2"),
    )
    with pytest.raises(ValueError, match="stale_receipt"):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(
                should_run=True,
                lineage=_lineage(agent_id="agent-1"),
                predecessor_turn_key=receipt.turn_key,
            ),
        )


def test_forged_completion_mapping_cannot_construct_receipt() -> None:
    # A caller-authored result_kind + lineage mapping must not be enough to
    # prove completion.
    with pytest.raises(ValueError, match="schema_version"):
        ValidatedTurnReceipt.from_execution(
            {"result_kind": "validated_completion", "lineage": _lineage()}
        )


def test_receipt_requires_ok_true() -> None:
    with pytest.raises(ValueError, match="ok=true"):
        ValidatedTurnReceipt.from_execution(
            {
                "schema_version": LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
                "result_kind": "validated_progress",
                "receipt": {
                    "schema_version": "loopx_turn_receipt_validation_v0",
                    "ok": False,
                    "result_kind": "validated_progress",
                    "turn_key": "sha256:abc",
                },
            }
        )


def test_material_result_requires_committed_status() -> None:
    # A validation-only (3-phase) completion must not construct a receipt; the
    # loop must not terminate before writeback/spend/scheduler close the
    # run-once transaction.
    plan = _plan()
    result = _result(
        plan,
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        completed_phases=["host_execute", "typed_result", "validation"],
    )
    validation = validate_loopx_turn_receipt(plan, result)
    assert validation["ok"] is True
    assert validation["status"] == "validated"
    with pytest.raises(ValueError, match="committed"):
        ValidatedTurnReceipt.from_execution(
            {
                "schema_version": LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
                "status": "validated",
                "result_kind": "validated_completion",
                "receipt": validation,
            }
        )


def test_material_progress_requires_committed_status() -> None:
    plan = _plan()
    result = _result(
        plan,
        result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS,
        completed_phases=["host_execute", "typed_result", "validation"],
    )
    validation = validate_loopx_turn_receipt(plan, result)
    assert validation["ok"] is True
    with pytest.raises(ValueError, match="committed"):
        ValidatedTurnReceipt.from_execution(
            {
                "schema_version": LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
                "status": "validated",
                "result_kind": "validated_progress",
                "receipt": validation,
            }
        )


def test_phase_only_committed_mapping_cannot_prove_material_effect() -> None:
    execution = _execution(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    execution.pop("settlement_result")
    with pytest.raises(ValueError, match="settlement"):
        ValidatedTurnReceipt.from_execution(execution)


@pytest.mark.parametrize(
    "mutation,match",
    [
        (
            lambda execution: execution["settlement_result"]["receipts"].reverse(),
            "ordered validation/writeback/spend",
        ),
        (
            lambda execution: execution["settlement_result"]["receipts"][1].update(
                {"effect_id": "forged-effect"}
            ),
            "one effect identity",
        ),
        (
            lambda execution: execution["effects"].update({"quota_spent": False}),
            "durable effect evidence",
        ),
        (
            lambda execution: execution["scheduler"].update({"completed": False}),
            "scheduler handoff",
        ),
    ],
)
def test_material_execution_fails_closed_when_commit_evidence_is_broken(
    mutation: Callable[[dict[str, Any]], None],
    match: str,
) -> None:
    execution = _execution(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    mutation(execution)
    with pytest.raises(ValueError, match=match):
        ValidatedTurnReceipt.from_execution(execution)


def test_validated_completion_requires_durable_todo_outcome() -> None:
    execution = _execution(result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION)
    execution.pop("todo_completion")
    with pytest.raises((TypeError, ValueError), match="durable Todo outcome"):
        ValidatedTurnReceipt.from_execution(execution)


def test_successor_completion_requires_declared_successor_ids() -> None:
    execution = _execution(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation="successor",
    )
    with pytest.raises(ValueError, match="successor Todo ids"):
        ValidatedTurnReceipt.from_execution(execution)


def test_forged_ok_true_mapping_requires_turn_key() -> None:
    # A caller-authored mapping with ok=true and lineage but no turn_key must
    # not construct a receipt; the turn_key is the causal binding handle.
    with pytest.raises(ValueError, match="turn_key"):
        ValidatedTurnReceipt.from_execution(
            {
                "schema_version": LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
                "result_kind": "wait",
                "receipt": {
                    "schema_version": "loopx_turn_receipt_validation_v0",
                    "ok": True,
                    "result_kind": "wait",
                    "status": "stopped",
                    "lineage": _lineage(),
                },
            }
        )


def test_missing_predecessor_turn_key_raises() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    with pytest.raises(ValueError, match="predecessor_turn_key"):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(should_run=True),
            bounded_turn_budget=_budget(max_turns=3, completed_turns=1),
        )


def test_mismatched_predecessor_turn_key_raises() -> None:
    # An old receipt for the same todo cannot be replayed against a later
    # envelope that references a different predecessor turn.
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    with pytest.raises(ValueError, match="predecessor_turn_key"):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(
                should_run=True, predecessor_turn_key="sha256:some-other-turn"
            ),
            bounded_turn_budget=_budget(max_turns=3, completed_turns=1),
        )


def test_malformed_decision_raises() -> None:
    with pytest.raises(ValueError, match="envelope contract"):
        decide_loop_disposition(
            turn_receipt=None,
            quota_decision={"schema_version": "not_an_envelope"},
        )


def test_marker_only_signature_raises() -> None:
    envelope = _envelope(should_run=True)
    envelope["action_signature"] = {"matches": True}
    with pytest.raises(ValueError, match="envelope contract"):
        decide_loop_disposition(turn_receipt=None, quota_decision=envelope)


def test_mismatched_signature_hashes_raise() -> None:
    envelope = _envelope(should_run=True)
    envelope["action_signature"] = {
        "matches": True,
        "source_hash": "sha256:a",
        "envelope_hash": "sha256:b",
    }
    with pytest.raises(ValueError, match="envelope contract"):
        decide_loop_disposition(turn_receipt=None, quota_decision=envelope)


def test_over_budget_compaction_preserves_disposition() -> None:
    envelope = _envelope(should_run=True)
    envelope["compaction"] = {"within_budget": False}
    result = decide_loop_disposition(turn_receipt=None, quota_decision=envelope)
    _assert_markers(result, "run_now")


def test_mismatched_signature_raises() -> None:
    with pytest.raises(ValueError, match="envelope contract"):
        decide_loop_disposition(
            turn_receipt=None,
            quota_decision=_envelope(should_run=True, signature_matches=False),
        )


def test_repair_decision_routes_to_repair() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(
            should_run=True, effective_action="agent_workspace_repair"
        ),
    )
    _assert_markers(payload, "repair")


def test_delivery_blocked_decision_waits() -> None:
    payload = decide_loop_disposition(
        turn_receipt=None,
        quota_decision=_envelope(should_run=True, delivery_allowed=False),
    )
    _assert_markers(payload, "wait")


def test_disposition_enum_includes_explicit_capability_adapter_handoff() -> None:
    from loopx.control_plane.turn_driver.loop_controller import LoopDisposition

    assert {d.value for d in LoopDisposition} == {
        "run_now",
        "capability_action_required",
        "wait",
        "stop",
        "user_action_required",
        "repair",
        "replan",
        "terminal",
    }


def test_budget_rejects_booleans() -> None:
    with pytest.raises(ValueError, match="max_turns must be an int"):
        BoundedTurnBudget(lineage=_lineage(), max_turns=True, completed_turns=0)
    with pytest.raises(ValueError, match="completed_turns must be an int"):
        BoundedTurnBudget(lineage=_lineage(), max_turns=3, completed_turns=False)


@pytest.mark.parametrize(
    "max_turns,completed_turns",
    [
        (0, 0),
        (-1, 0),
        (3, -1),
        (3, 4),
    ],
)
def test_budget_rejects_invalid_ranges(max_turns: int, completed_turns: int) -> None:
    with pytest.raises(ValueError):
        BoundedTurnBudget(
            lineage=_lineage(),
            max_turns=max_turns,
            completed_turns=completed_turns,
        )


def test_budget_requires_lineage() -> None:
    with pytest.raises(ValueError, match="lineage"):
        BoundedTurnBudget(lineage={}, max_turns=3, completed_turns=0)


def test_stale_budget_lineage_raises() -> None:
    receipt = _validated_receipt(result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS)
    with pytest.raises(ValueError, match="stale_budget"):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(
                should_run=True, predecessor_turn_key=receipt.turn_key
            ),
            bounded_turn_budget=_budget(lineage=_lineage(agent_id="agent-2")),
        )


def test_validated_receipt_carries_turn_key() -> None:
    receipt = _validated_receipt(
        lineage=_lineage(goal_id="goal-x", agent_id="agent-y", todo_id="todo-z")
    )
    assert receipt.turn_key is not None
    assert receipt.status == "committed"


def test_budget_schema_version() -> None:
    assert (
        _budget().to_mapping()["schema_version"] == BOUNDED_TURN_BUDGET_SCHEMA_VERSION
    )


def test_validated_receipt_schema_version() -> None:
    assert (
        _validated_receipt().to_mapping()["schema_version"]
        == VALIDATED_TURN_RECEIPT_SCHEMA_VERSION
    )


# These expectations come from the continuation invariants, independently of
# the controller table: a user gate blocks work; causal/actor identity must be
# proved first; completion cannot manufacture a successor; exhaustion replans.
@pytest.mark.parametrize("kind", list(LoopXTurnResultKind))
def test_fresh_user_gate_outranks_every_nonterminal_result(kind) -> None:
    kwargs = {}
    if kind is LoopXTurnResultKind.TERMINAL_CLOSEOUT_FAILED:
        kwargs = {
            "completed_phases": _ALL_PHASES[:5],
            "failed_phase": "terminal_closeout",
        }
    receipt = _validated_receipt(result_kind=kind, **kwargs)
    # Completion needs a different Todo even when the fresh decision is a gate.
    todo = "todo-next" if kind is LoopXTurnResultKind.VALIDATED_COMPLETION else "todo-1"
    envelope = _envelope(
        should_run=True,
        selected_todo_id=todo,
        user_action_required=True,
        predecessor_turn_key=receipt.turn_key,
    )
    result = decide_loop_disposition(turn_receipt=receipt, quota_decision=envelope)
    _assert_markers(result, "user_action_required")
    assert "retry_continuation" not in result
    assert "replan_continuation" not in result


@pytest.mark.parametrize(
    "action,should_run,quiet",
    [
        ("deliver", True, False),
        ("agent_workspace_repair", True, False),
        ("autonomous_replan", True, False),
        ("deliver", False, True),
        ("deliver", False, False),
    ],
)
def test_progress_exhaustion_outranks_fresh_work_and_wait(
    action, should_run, quiet
) -> None:
    receipt = _validated_receipt()
    result = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=should_run,
            effective_action=action,
            quiet_noop_allowed=quiet,
            predecessor_turn_key=receipt.turn_key,
        ),
        bounded_turn_budget=_budget(max_turns=1, completed_turns=1),
    )
    _assert_markers(result, "replan")
    assert "exhausted" in result["reason"]


@pytest.mark.parametrize(
    "action,expected",
    [
        ("deliver", "wait"),
        ("agent_workspace_repair", "repair"),
        ("autonomous_replan", "replan"),
    ],
)
def test_host_failure_recovery_route_outranks_available_retry(action, expected) -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.HOST_FAILURE,
        host_failure=build_host_failure_record("provider_capacity", attempt=1),
    )
    result = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=True,
            effective_action=action,
            predecessor_turn_key=receipt.turn_key,
        ),
    )
    _assert_markers(result, expected)
    assert ("retry_continuation" in result) == (expected == "wait")


@pytest.mark.parametrize(
    "kind",
    [
        None,
        LoopXTurnResultKind.VALIDATED_PROGRESS,
        LoopXTurnResultKind.VALIDATED_COMPLETION,
    ],
)
def test_capability_handoff_needs_no_todo_or_progress_budget(kind) -> None:
    receipt = (
        None
        if kind is None
        else _validated_receipt(result_kind=kind, continuation="no_followup")
    )
    envelope = _envelope(
        should_run=True,
        effective_action="governed_capability_intent",
        selected_todo_id=None,
        predecessor_turn_key=receipt.turn_key if receipt else None,
    )
    envelope["action"]["capability_intent"] = {
        "schema_version": "pending_capability_intent_projection_v0",
        "goal_id": "goal-1",
        "agent_id": "agent-1",
        "command": "fixture command",
    }
    result = decide_loop_disposition(turn_receipt=receipt, quota_decision=envelope)
    _assert_markers(result, "capability_action_required")
    assert result["lineage"]["todo_id"] == ""


@pytest.mark.parametrize(
    "mutation,expected",
    [
        ("signature", "envelope contract"),
        ("actor_missing", "missing goal/agent"),
        ("predecessor_missing", "missing predecessor_turn_key"),
        ("predecessor_stale", "does not match the receipt turn_key"),
        ("actor_stale", "on agent_id"),
        ("todo_stale", "receipt Todo"),
    ],
)
def test_rejection_precedence_before_user_gate_and_missing_budget(
    mutation, expected
) -> None:
    receipt = _validated_receipt()
    envelope = _envelope(
        should_run=True,
        user_action_required=True,
        predecessor_turn_key=receipt.turn_key,
    )
    if mutation == "signature":
        envelope["action_signature"] = {}
        envelope["agent_id"] = ""
        envelope.pop("predecessor_turn_key")
    elif mutation == "actor_missing":
        envelope["agent_id"] = ""
        envelope.pop("predecessor_turn_key")
    elif mutation == "predecessor_missing":
        envelope.pop("predecessor_turn_key")
        envelope["agent_id"] = "stale-agent"
    elif mutation == "predecessor_stale":
        envelope["predecessor_turn_key"] = "stale-turn"
        envelope["agent_id"] = "stale-agent"
    elif mutation == "actor_stale":
        envelope["agent_id"] = "stale-agent"
        envelope["action"]["selected_todo"] = {"todo_id": "stale-todo"}
    else:
        envelope["action"]["selected_todo"] = {"todo_id": "stale-todo"}
    with pytest.raises(ValueError, match=expected):
        decide_loop_disposition(turn_receipt=receipt, quota_decision=envelope)


@pytest.mark.parametrize(
    "continuation,todo,match",
    [
        ("no_followup", None, "terminal Goal frontier evidence"),
        ("successor", None, "fresh selected Todo"),
        ("successor", "undeclared", "declared completion successor"),
        ("active_goal", "todo-1", "reselected the completed Todo"),
    ],
)
def test_completion_admission_precedes_user_gate(continuation, todo, match) -> None:
    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation=continuation,
        successor_todo_ids=["todo-next"],
    )
    with pytest.raises(ValueError, match=match):
        decide_loop_disposition(
            turn_receipt=receipt,
            quota_decision=_envelope(
                should_run=True,
                selected_todo_id=todo,
                user_action_required=True,
                predecessor_turn_key=receipt.turn_key,
            ),
        )


def test_wait_and_blocked_differ_for_missing_progress_todo() -> None:
    receipt = _validated_receipt()
    for quiet in (True, False):
        envelope = _envelope(
            should_run=False,
            quiet_noop_allowed=quiet,
            selected_todo_id=None,
            predecessor_turn_key=receipt.turn_key,
        )
        if quiet:
            result = decide_loop_disposition(
                turn_receipt=receipt,
                quota_decision=envelope,
                bounded_turn_budget=_budget(),
            )
            _assert_markers(result, "wait")
            assert result["lineage"] == receipt.lineage
        else:
            with pytest.raises(ValueError, match="receipt-backed executable"):
                decide_loop_disposition(
                    turn_receipt=receipt,
                    quota_decision=envelope,
                    bounded_turn_budget=_budget(),
                )


def test_completion_terminal_evidence_accepts_the_string_enum_owner() -> None:
    from loopx.control_plane.quota.effective_action import EffectiveAction

    receipt = _validated_receipt(
        result_kind=LoopXTurnResultKind.VALIDATED_COMPLETION,
        continuation="no_followup",
    )
    result = decide_loop_disposition(
        turn_receipt=receipt,
        quota_decision=_envelope(
            should_run=False,
            effective_action=EffectiveAction.TERMINAL_NO_FOLLOWUP,
            state="terminal_no_followup",
            predecessor_turn_key=receipt.turn_key,
        ),
    )
    _assert_markers(result, "terminal")
