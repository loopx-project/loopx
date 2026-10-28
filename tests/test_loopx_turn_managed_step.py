"""Typed tests for the managed-step consumer of same-Turn continuation.

Each test drives ``decide_managed_step`` off a journal shaped exactly as
``loopx turn run-once`` leaves one after a retryable host failure, paired with a
fresh ``loopx_turn_envelope_v0`` decision. The reader must stay pure: it grants
no execution authority, never spends or writes, and treats the Turn journal as
the sole authority for the attempt budget.
"""

from __future__ import annotations

from typing import Any

import pytest

from loopx.control_plane.turn_driver import (
    LOOPX_TURN_RESULT_SCHEMA_VERSION,
    LoopXTurnResultKind,
    build_loopx_turn_transaction_plan,
    validate_loopx_turn_receipt,
)
from loopx.control_plane.turn_driver.host_failure import build_host_failure_record
from loopx.control_plane.turn_driver.managed_step import (
    LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION,
    decide_managed_step,
    managed_step_receipt_from_journal,
    reconcile_observed_attempt,
)

GOAL_ID = "goal-managed-step"
AGENT_ID = "agent-managed-step"
TODO_ID = "todo-managed-step"


def _lineage() -> dict[str, str]:
    return {"goal_id": GOAL_ID, "agent_id": AGENT_ID, "todo_id": TODO_ID}


def _plan() -> dict[str, Any]:
    """A stored Turn plan shaped as ``build_loopx_turn_plan`` returns it."""

    transaction = build_loopx_turn_transaction_plan(
        planned=True,
        lineage=_lineage(),
        host="dsh",
        execution_mode="interactive-visible",
        session_action="resume",
    )
    return {"transaction": transaction}


def _failed_journal(
    *,
    kind: str = "provider_capacity",
    attempt: int = 1,
    result_kind: LoopXTurnResultKind = LoopXTurnResultKind.HOST_FAILURE,
    status: str = "failed",
    lineage: dict[str, str] | None = None,
) -> dict[str, Any]:
    transaction = build_loopx_turn_transaction_plan(
        planned=True,
        lineage=_lineage() if lineage is None else lineage,
        host="dsh",
        execution_mode="interactive-visible",
        session_action="resume",
    )
    plan = {"transaction": transaction, "turn_envelope": {
        **(_lineage() if lineage is None else lineage),
        "action": {"selected_todo": {"todo_id": (_lineage() if lineage is None else lineage)["todo_id"]}},
    }}
    failure_kind = result_kind in {
        LoopXTurnResultKind.HOST_FAILURE,
        LoopXTurnResultKind.VALIDATION_FAILED,
        LoopXTurnResultKind.WRITEBACK_FAILED,
        LoopXTurnResultKind.QUOTA_SPEND_FAILED,
    }
    if failure_kind:
        completed: list[str] = []
        result: dict[str, Any] = {
            "schema_version": LOOPX_TURN_RESULT_SCHEMA_VERSION,
            "turn_key": transaction["turn_key"],
            "result_kind": result_kind.value,
            "completed_phases": completed,
            "failed_phase": "host_execute",
        }
    else:
        completed = ["host_execute", "typed_result", "validation"]
        result = {
            "schema_version": LOOPX_TURN_RESULT_SCHEMA_VERSION,
            "turn_key": transaction["turn_key"],
            "result_kind": result_kind.value,
            "completed_phases": completed,
        }
    receipt = validate_loopx_turn_receipt(transaction, result)
    assert receipt["ok"] is True, receipt
    journal: dict[str, Any] = {
        "schema_version": "loopx_turn_journal_v0",
        "goal_id": (_lineage() if lineage is None else lineage)["goal_id"],
        "status": status,
        "turn_key": transaction["turn_key"],
        "result_kind": result_kind.value,
        "plan": plan,
        "receipt": receipt,
        "completed_phases": completed,
        "host_attempt_count": attempt,
    }
    if result_kind is LoopXTurnResultKind.HOST_FAILURE:
        journal["host_failure"] = build_host_failure_record(kind, attempt=attempt)
    return journal


def _envelope(
    *,
    should_run: bool = True,
    effective_action: str = "deliver",
    selected_todo_id: str | None = TODO_ID,
    user_action_required: bool = False,
    lineage: dict[str, str] | None = None,
    predecessor_turn_key: str | None = None,
) -> dict[str, Any]:
    lin = _lineage() if lineage is None else lineage
    envelope: dict[str, Any] = {
        "schema_version": "loopx_turn_envelope_v0",
        "goal_id": lin["goal_id"],
        "agent_id": lin["agent_id"],
        "should_run": should_run,
        "effective_action": effective_action,
        "action_signature": {"matches": True, "source_hash": "sha256:test", "envelope_hash": "sha256:test"},
        "compaction": {"within_budget": True},
        "action": {
            "delivery_allowed": True,
            "must_attempt": True,
            "quiet_noop_allowed": False,
            "selected_todo": (
                {"todo_id": selected_todo_id} if selected_todo_id else None
            ),
        },
        "user": {"action_required": user_action_required},
    }
    if predecessor_turn_key is not None:
        envelope["predecessor_turn_key"] = predecessor_turn_key
    return envelope


def _decide(
    journal: dict[str, Any],
    *,
    envelope: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Decide one managed step, binding the fresh decision to the receipt."""

    return decide_managed_step(
        journal,
        (
            _envelope(predecessor_turn_key=str(journal["turn_key"]))
            if envelope is None
            else envelope
        ),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_key=str(journal["turn_key"]),
        **kwargs,
    )


def test_retryable_failure_with_budget_returns_wait_with_typed_continuation() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)

    payload = _decide(journal)

    assert payload["schema_version"] == LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION
    assert payload["disposition"] == "wait"
    assert payload["turn_key"] == journal["turn_key"]
    assert payload["attempt"] == 1
    continuation = payload["retry_continuation"]
    assert continuation == {
        "same_turn": True,
        "retry_failed_turn": True,
        "strategy": "same_configuration",
        "retry_after_seconds": 30,
        "attempt": 1,
        "max_attempts": 3,
        "fresh_envelope_required": True,
        "model_fallback_allowed": False,
    }
    assert payload["max_attempts"] == 3


def test_exhausted_budget_requests_repair_instead_of_waiting() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=3)

    payload = _decide(journal)

    assert payload["disposition"] == "repair"
    assert "retry_continuation" not in payload
    assert payload["attempt"] == 3


def test_non_retryable_failure_is_refused_before_the_controller() -> None:
    journal = _failed_journal(kind="auth_failed", attempt=1)

    with pytest.raises(ValueError, match="not retryable"):
        _decide(journal)


def test_committed_journal_is_not_a_managed_step_input() -> None:
    journal = _failed_journal(
        result_kind=LoopXTurnResultKind.VALIDATED_PROGRESS,
        status="committed",
    )

    with pytest.raises(ValueError, match="failed Turn journal"):
        _decide(journal)


def test_forged_observed_attempt_is_refused() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)

    with pytest.raises(ValueError, match="observed_attempt disagrees"):
        _decide(journal, observed_attempt=99)


def test_forged_observed_max_attempts_is_refused() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)

    with pytest.raises(ValueError, match="observed_max_attempts disagrees"):
        _decide(journal, observed_max_attempts=99)


def test_matching_observation_is_accepted() -> None:
    journal = _failed_journal(kind="rate_limited", attempt=2)

    payload = _decide(journal, observed_attempt=2, observed_max_attempts=3)

    assert payload["disposition"] == "wait"
    # Backoff doubles per attempt off the policy base (60s for rate_limited).
    assert payload["retry_continuation"]["retry_after_seconds"] == 120


def test_observation_must_be_a_positive_integer() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)

    with pytest.raises(ValueError, match="positive integer"):
        _decide(journal, observed_attempt=0)
    with pytest.raises(ValueError, match="positive integer"):
        _decide(journal, observed_attempt=True)


def test_journal_attempt_divergence_is_refused() -> None:
    """The two persisted attempt fields must agree, in both directions.

    The executor increments ``host_attempt_count`` while the controller reads
    ``host_failure.attempt`` for the retry ceiling. A journal that reports one
    value at the top level and another inside the typed failure would present
    ``3/3`` while still authorizing a retry, so it must fail closed.
    """

    consumed = _failed_journal(kind="provider_capacity", attempt=3)
    consumed["host_failure"] = build_host_failure_record("provider_capacity", attempt=1)

    with pytest.raises(ValueError, match="host_failure attempt disagrees"):
        _decide(consumed)

    # The opposite direction is refused the same way: a nested attempt ahead of
    # the journal's own counter.
    ahead = _failed_journal(kind="provider_capacity", attempt=1)
    ahead["host_failure"] = build_host_failure_record("provider_capacity", attempt=3)

    with pytest.raises(ValueError, match="host_failure attempt disagrees"):
        _decide(ahead)

    # An agreeing journal still decides normally, so the binding is not a blanket
    # rejection of the retryable path.
    agreeing = _failed_journal(kind="provider_capacity", attempt=1)
    decided = _decide(agreeing)
    assert decided["disposition"] == "wait"
    assert decided["attempt"] == 1


def test_foreign_lineage_is_refused() -> None:
    journal = _failed_journal(
        lineage={"goal_id": "other-goal", "agent_id": AGENT_ID, "todo_id": TODO_ID}
    )

    with pytest.raises(ValueError, match="lineage does not match"):
        _decide(journal)


def test_historical_recovery_audit_does_not_override_current_journal() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)
    journal["recovery_audit"] = {
        "schema_version": "loopx_turn_recovery_audit_v0",
        "planned": {
            "schema_version": "loopx_turn_recovery_decision_v0",
            "action": "blocked",
            "can_continue": False,
            "resume_from": None,
            "reinvoke_host": False,
            "reason": "journal consistency violation",
            "retry_failed": True,
            "checks": [],
        },
        "actual": {
            "status": "finished",
            "journal_status": "failed",
            "completed_phases": [],
            "host_invoked": True,
        },
    }

    assert _decide(journal)["disposition"] == "wait"
    # The inverse matters too: a previous success audit cannot admit a now
    # inconsistent journal. The TS owner independently defines phase order.
    journal["recovery_audit"]["planned"]["action"] = "continue"
    journal["completed_phases"] = ["quota_spend"]
    with pytest.raises(ValueError, match="completed_phases_not_ordered_prefix"):
        _decide(journal)


@pytest.mark.parametrize("mutation", ["goal", "receipt_key", "settlement"])
def test_current_journal_uses_canonical_identity_checks(mutation):
    journal = _failed_journal()
    if mutation == "goal":
        journal["goal_id"] = "other-goal"
    elif mutation == "receipt_key":
        journal["receipt"]["turn_key"] = "sha256:" + "0" * 64
    else:
        journal["plan"]["transaction"]["settlement_plan"]["identity"]["effect_id"] = "invalid"
    with pytest.raises(ValueError, match="replay is blocked"):
        _decide(journal)


def test_fresh_decision_must_agree_on_goal_and_agent_lineage() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)
    foreign = _envelope(
        lineage={"goal_id": "other-goal", "agent_id": AGENT_ID, "todo_id": TODO_ID}
    )

    with pytest.raises(ValueError, match="fresh decision goal_id"):
        _decide(journal, envelope=foreign)


def test_managed_step_never_spends_or_writes() -> None:
    """The reader is pure: no effects, no host invocation, no quota spend."""

    journal = _failed_journal(kind="provider_capacity", attempt=1)

    payload = _decide(journal)

    for forbidden in ("effects", "quota_slot_spend_count", "host_invoked", "writeback"):
        assert forbidden not in payload
    # A wait decision must not carry any field that could be mistaken for
    # authority to execute now.
    assert "accepted_turn_keys" not in payload
    assert payload["disposition"] == "wait"


def test_reconcile_accepts_omitted_observations() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)

    assert reconcile_observed_attempt(
        journal, observed_attempt=None, observed_max_attempts=None
    ) is None


def test_receipt_from_journal_projects_lineage_and_kind() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)

    receipt = managed_step_receipt_from_journal(
        journal,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_key=str(journal["turn_key"]),
    )

    assert receipt.result_kind is LoopXTurnResultKind.HOST_FAILURE
    assert receipt.lineage["todo_id"] == TODO_ID
    assert receipt.host_failure is not None
    assert receipt.host_failure["kind"] == "provider_capacity"


def test_journal_without_stored_plan_is_refused() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)
    journal.pop("plan")

    with pytest.raises(TypeError, match="no stored plan"):
        _decide(journal)


def test_journal_turn_key_mismatch_is_refused() -> None:
    journal = _failed_journal(kind="provider_capacity", attempt=1)
    journal["turn_key"] = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="turn_key"):
        _decide(journal)


def test_shared_decision_owner_binds_both_turn_owners_to_one_set_of_inputs(monkeypatch) -> None:
    """``run-once`` and ``managed-step`` must resolve through the shared owner.

    A registry that declares ``common_runtime_root`` while the command omits
    ``--runtime-root`` passes ``None`` as the raw argument, so the activation
    check must receive the resolved root instead. Deriving the status, the
    scheduler context or the capability hooks per command would additionally
    let the two owners disagree about the current governing decision.
    """

    import argparse
    from pathlib import Path

    from loopx.cli_commands import turn_decision

    roots: list[object] = []
    captured: list[dict[str, Any]] = []
    status_payload = {
        "ok": True,
        "attention_queue": {"items": []},
        "run_history": {"goals": []},
    }

    def _projector(**_kwargs: object) -> dict[str, object]:
        return {"schema_version": "lark_event_inbox_urgency_v0"}

    def _record_projector(*, runtime_root_arg):
        roots.append(runtime_root_arg)
        return _projector

    def _record_decision(payload, **kwargs):
        captured.append({"status_payload": payload, **kwargs})
        return {"selected_todo": None}

    monkeypatch.setattr(
        turn_decision, "build_lark_operator_inbox_urgency_projector", _record_projector
    )
    monkeypatch.setattr(
        turn_decision, "build_live_quota_should_run_decision", _record_decision
    )
    monkeypatch.setattr(
        turn_decision,
        "collect_turn_status_payload",
        lambda *_args, **_kwargs: status_payload,
    )
    args = argparse.Namespace(
        goal_id=GOAL_ID,
        agent_id="agent-a",
        host="codex-cli",
        execution_mode=None,
        scheduler_owner=None,
        available_capabilities=[],
    )
    resolved_root = Path("/tmp/registry-scoped-runtime-root")
    registry_path = Path("/tmp/registry.json")
    owner = turn_decision.build_fresh_turn_decision_owner(
        args,
        registry_path=registry_path,
        runtime_root=resolved_root,
        runtime_root_arg=None,
    )

    assert owner.resolve() == {"selected_todo": None}
    assert captured[-1]["status_payload"] is status_payload
    assert (
        captured[-1]["scheduler_execution_context"] is owner.scheduler_execution_context
    )
    assert (
        captured[-1]["operator_inbox_urgency_projector"]
        is owner.operator_inbox_urgency_projector
    )
    assert captured[-1]["route_source"] == turn_decision.TURN_DECISION_ROUTE_SOURCE

    envelopes: list[object] = []

    def _record_envelope(decision, *, scheduler_execution_context):
        envelopes.append((decision, scheduler_execution_context))
        return {"schema_version": "loopx_turn_envelope_v0"}

    monkeypatch.setattr(turn_decision, "build_turn_controller_input", _record_envelope)
    turn_decision.build_fresh_envelope_for_managed_step(
        args,
        registry_path=registry_path,
        runtime_root=resolved_root,
        runtime_root_arg=None,
    )
    assert captured[-1]["status_payload"] is status_payload
    assert len(envelopes) == 1
    signed_decision, signed_context = envelopes[0]
    assert signed_decision == {"selected_todo": None}
    assert signed_context == captured[-1]["scheduler_execution_context"]
    assert roots == [resolved_root, resolved_root], (
        "both Turn owners must receive the resolved runtime root, not the raw "
        f"argument that is None when the registry declares common_runtime_root: {roots}"
    )
