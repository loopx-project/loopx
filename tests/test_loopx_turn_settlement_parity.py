"""M7.1 parity fixtures for the fenced Turn settlement vertical slice.

The typed settlement contract (RFC M7.1,
``docs/architecture/rfcs/agent-loop-effect-interpreter-v0.md``) requires that
partial execution, retry, cancellation, permission denial, and budget rejection
stay distinguishable and that replay reuses committed receipts only for the
same settlement identity. The first five fault classes are already covered in
``tests/test_loopx_turn_executor.py`` (cancellation before writeback and
during scheduler, permission denial at host and spend, budget rejection after
writeback, explicit retry without duplicate effects, resume after partial
writeback). This module pins the missing parity:

- key/owner-mismatched replay must fail closed with
  ``SettlementFailureKind.IDENTITY_MISMATCH`` instead of re-attributing the
  committed validation/writeback/spend receipts to a different effect id while
  skipping its effects;
- a same-key replay stays idempotent under the original effect id;
- the executor wires journal provenance through
  ``_journal_committed_effect_id``, while legacy journals without a typed
  settlement plan skip the cross-check (opt-in seam).

Expectations are derived from the typed settlement contract and the aggregate
quota readback precedent (``read_heartbeat_settlement`` in
``tests/control_plane/test_quota_settlement.py``), not from implementation
output.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from loopx.control_plane.effect_program import (
    SettlementFailureKind,
    SettlementStepKind,
)
from loopx.control_plane.turn_driver import build_loopx_turn_plan
from loopx.control_plane.turn_driver.executor import _journal_committed_effect_id
from loopx.control_plane.turn_driver.settlement import execute_turn_driver_settlement
import loopx.control_plane.turn_driver.settlement as turn_settlement
from loopx.control_plane.turn_driver.transaction import TRANSACTION_PHASES

COMMITTED_PHASES = list(TRANSACTION_PHASES[:5])  # host_execute .. quota_spend
COMMITTED_PAYLOAD: dict[str, Any] = {"ok": True, "appended": True}


def _plan(
    todo_id: str = "todo_fixture0001",
    agent_id: str = "codex-fixture",
) -> dict[str, Any]:
    return build_loopx_turn_plan(
        {
            "ok": True,
            "schema_version": "loopx_turn_envelope_v0",
            "goal_id": "fixture-goal",
            "agent_id": agent_id,
            "should_run": True,
            "effective_action": "normal_run",
            "action": {
                "must_attempt": True,
                "delivery_allowed": True,
                "quiet_noop_allowed": False,
                "selected_todo": {
                    "todo_id": todo_id,
                    "text": "Advance one public fixture",
                },
            },
            "user": {
                "action_required": False,
                "open_count": 0,
                "notify": "DONT_NOTIFY",
            },
            "writeback": {"spend_after_validation": True},
            "scheduler": {"action": "run_now"},
            "action_signature": {
                "matches": True,
                "source_hash": "sha256:fixture",
                "envelope_hash": "sha256:fixture",
            },
            "compaction": {"within_budget": True},
        },
        host="generic-cli",
        execution_mode="isolated-headless",
    )


def _effect_id(plan: dict[str, Any]) -> str:
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    settlement_plan = transaction["settlement_plan"]
    assert isinstance(settlement_plan, dict)
    identity = settlement_plan["identity"]
    assert isinstance(identity, dict)
    effect_id = identity["effect_id"]
    assert isinstance(effect_id, str)
    return effect_id


def _settle(
    plan: dict[str, Any],
    *,
    committed_effect_id: str | None,
    calls: dict[str, int] | None = None,
):
    counters = {"writeback": 0, "spend": 0} if calls is None else calls
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)

    def writeback() -> dict[str, Any]:
        counters["writeback"] += 1
        return dict(COMMITTED_PAYLOAD)

    def spend() -> dict[str, Any]:
        counters["spend"] += 1
        return dict(COMMITTED_PAYLOAD)

    def checkpoint(
        _step_kind: SettlementStepKind,
        _payload: Any,
        _phases: tuple[str, ...],
    ) -> None:
        return None

    result = execute_turn_driver_settlement(
        transaction,
        transaction_phases=TRANSACTION_PHASES,
        completed_phases=COMMITTED_PHASES,
        writeback_payload=dict(COMMITTED_PAYLOAD),
        quota_spend_payload=dict(COMMITTED_PAYLOAD),
        writeback=writeback,
        spend=spend,
        checkpoint=checkpoint,
        committed_effect_id=committed_effect_id,
    )
    return result, counters


def test_key_mismatched_replay_fails_closed_with_identity_mismatch() -> None:
    plan_a = _plan(todo_id="todo_fixture0001")
    plan_b = _plan(todo_id="todo_fixture0002")
    committed_effect_id = _effect_id(plan_a)
    assert committed_effect_id != _effect_id(plan_b)

    result, calls = _settle(plan_b, committed_effect_id=committed_effect_id)

    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.IDENTITY_MISMATCH
    assert result.failure.step_kind is SettlementStepKind.VALIDATION
    assert committed_effect_id in result.failure.reason
    assert _effect_id(plan_b) in result.failure.reason
    # No committed receipt may be re-attributed to the mismatched identity.
    assert result.receipts == ()
    # No effect may be skipped by pretending it belonged to the other plan.
    assert calls == {"writeback": 0, "spend": 0}


def test_owner_mismatched_replay_fails_closed_with_identity_mismatch() -> None:
    plan_owner = _plan(agent_id="codex-fixture")
    plan_other = _plan(agent_id="codex-fixture-other")
    committed_effect_id = _effect_id(plan_owner)
    assert committed_effect_id != _effect_id(plan_other)

    result, calls = _settle(plan_other, committed_effect_id=committed_effect_id)

    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.IDENTITY_MISMATCH
    assert result.failure.step_kind is SettlementStepKind.VALIDATION
    assert calls == {"writeback": 0, "spend": 0}


def test_same_key_replay_stays_idempotent_under_original_effect_id() -> None:
    plan = _plan(todo_id="todo_fixture0002")
    committed_effect_id = _effect_id(plan)

    result, calls = _settle(plan, committed_effect_id=committed_effect_id)

    assert result.failure is None
    assert [receipt.step_kind for receipt in result.receipts] == [
        SettlementStepKind.VALIDATION,
        SettlementStepKind.DURABLE_WRITEBACK,
        SettlementStepKind.QUOTA_SPEND,
    ]
    assert all(receipt.effect_id == committed_effect_id for receipt in result.receipts)
    assert calls == {"writeback": 0, "spend": 0}


@pytest.mark.parametrize("committed_steps", [0, 1, 2])
@pytest.mark.parametrize("malformation", ["coerced_id", "binding_conflict", "unknown_schema"])
def test_invalid_identity_never_dispatches_or_replays_receipts(
    committed_steps: int, malformation: str
) -> None:
    transaction = _plan()["transaction"]
    identity = transaction["settlement_plan"]["identity"]
    if malformation == "coerced_id":
        identity["goal_id"] = 42
        identity["effect_id"] = identity["effect_id"].replace("fixture-goal:", "42:", 1)
    elif malformation == "binding_conflict":
        identity.update(binding_kind="autonomous_replan", binding_id="different-work")
    else:
        identity["schema_version"] = "unsupported_identity"
    calls: list[str] = []

    def effect(name: str):
        def execute():
            calls.append(name)
            return dict(COMMITTED_PAYLOAD)

        return execute

    result = execute_turn_driver_settlement(
        transaction,
        transaction_phases=TRANSACTION_PHASES,
        completed_phases=TRANSACTION_PHASES[: 3 + committed_steps],
        writeback_payload=dict(COMMITTED_PAYLOAD) if committed_steps >= 1 else None,
        quota_spend_payload=dict(COMMITTED_PAYLOAD) if committed_steps >= 2 else None,
        writeback=effect("writeback"),
        spend=effect("spend"),
        checkpoint=lambda *_: calls.append("checkpoint"),
        committed_effect_id=identity["effect_id"],
    )

    assert result.failure is not None
    assert result.failure.kind is SettlementFailureKind.INVALID_IDENTITY
    assert result.failure.step_kind is SettlementStepKind.VALIDATION
    assert result.receipts == ()
    assert calls == []


def test_mismatch_check_is_opt_in_for_callers_without_journal_provenance() -> None:
    plan = _plan(todo_id="todo_fixture0002")

    result, calls = _settle(plan, committed_effect_id=None)

    # Direct callers that cannot prove journal provenance keep the pre-fix
    # contract: receipts are rebuilt under the requested plan identity.
    assert result.failure is None
    assert all(receipt.effect_id == _effect_id(plan) for receipt in result.receipts)
    assert calls == {"writeback": 0, "spend": 0}


def test_journal_committed_effect_id_reads_typed_plan() -> None:
    plan = _plan(todo_id="todo_fixture0001")
    journal: dict[str, Any] = {"plan": dict(plan)}

    assert _journal_committed_effect_id(journal) == _effect_id(plan)


def test_journal_committed_effect_id_is_none_for_legacy_journal() -> None:
    plan = _plan(todo_id="todo_fixture0001")
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    legacy_transaction = {
        key: value for key, value in transaction.items() if key != "settlement_plan"
    }
    journal: dict[str, Any] = {"plan": {"transaction": legacy_transaction}}

    assert _journal_committed_effect_id(journal) is None


def test_new_turn_settlement_reduces_between_each_provider_effect() -> None:
    plan = _plan(todo_id="todo_fixture0002")
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    calls: list[str] = []
    original = turn_settlement.effect_runtime_result

    def observed(method: str, params: dict[str, Any]) -> Any:
        calls.append(method)
        return original(method, params)

    with patch.object(turn_settlement, "effect_runtime_result", observed):
        result = execute_turn_driver_settlement(
            transaction,
            transaction_phases=TRANSACTION_PHASES,
            completed_phases=TRANSACTION_PHASES[:3],
            writeback_payload=None,
            quota_spend_payload=None,
            writeback=lambda: dict(COMMITTED_PAYLOAD),
            spend=lambda: dict(COMMITTED_PAYLOAD),
            checkpoint=lambda _step, _payload, _phases: None,
        )

    assert result.failure is None
    assert calls == [
        "turn.settlement.reduce",
        "turn.settlement.reduce",
        "turn.settlement.reduce",
    ]


def test_replayed_turn_settlement_uses_one_runtime_request() -> None:
    plan = _plan(todo_id="todo_fixture0002")
    transaction = plan["transaction"]
    assert isinstance(transaction, dict)
    calls: list[str] = []
    original = turn_settlement.effect_runtime_result

    def observed(method: str, params: dict[str, Any]) -> Any:
        calls.append(method)
        return original(method, params)

    with patch.object(turn_settlement, "effect_runtime_result", observed):
        result = execute_turn_driver_settlement(
            transaction,
            transaction_phases=TRANSACTION_PHASES,
            completed_phases=COMMITTED_PHASES,
            writeback_payload=dict(COMMITTED_PAYLOAD),
            quota_spend_payload=dict(COMMITTED_PAYLOAD),
            writeback=lambda: (_ for _ in ()).throw(AssertionError("writeback ran")),
            spend=lambda: (_ for _ in ()).throw(AssertionError("spend ran")),
            checkpoint=lambda _step, _payload, _phases: None,
        )

    assert result.failure is None
    assert calls == ["turn.settlement.reduce"]
