"""Public Python value objects and transport for the typed Turn controller.

Receipt qualification and ordered continuation live in loop_controller.ts.
The exported classes retain their existing Python API; no model, scheduler or
settlement effect is executed by this boundary.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_program import SettlementIdentity
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..quota.turn_envelope import TurnControllerInput
from .driver import _historical_route_text
from .transaction import LoopXTurnResultKind
from .turn_contract_generated import LoopDisposition as LoopDisposition

LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION = "loop_turn_loop_disposition_v0"
BOUNDED_TURN_BUDGET_SCHEMA_VERSION = "loop_bounded_turn_budget_v0"
VALIDATED_TURN_RECEIPT_SCHEMA_VERSION = "loop_validated_turn_receipt_v0"


def _controller_result(operation: str, **facts: Any) -> dict[str, Any]:
    decision = facts.get("decision")
    if isinstance(decision, TurnControllerInput):
        decision = decision.envelope()
    if isinstance(decision, Mapping):
        facts["decision"] = dict(decision)
        # Preserve scalar spelling here; the shared typed owner decides routes.
        facts["historical_route_text"] = _historical_route_text(decision)
    # JSON numbers erase Python's int/float distinction and round large ints.
    # These are exact scalar codec facts, not precomputed admission decisions.
    source = facts.get({"receipt": "execution", "disposition": "receipt"}.get(operation, "journal"))
    if isinstance(source, Mapping):
        failure = source.get("host_failure")
        scalars = {"host_attempt_count": source.get("host_attempt_count"),
                   "failure_attempt": failure.get("attempt") if isinstance(failure, Mapping) else None,
                   "observed_attempt": facts.get("observed_attempt"),
                   "observed_max_attempts": facts.get("observed_max_attempts")}
        facts["integer_facts"] = {
            key: str(value) if type(value) is int else None for key, value in scalars.items()
        }
    request = {"operation": operation, **facts}
    try:
        payload = effect_runtime_result("turn.controller", request)
    except EffectRuntimeRejected as error:
        if error.diagnostic_code != "request_too_large":
            raise
        # The pure request was refused before sending. Preserve complete facts
        # through the existing bounded snapshot transport, as the envelope does.
        payload = effect_runtime_result("turn.controller", request, large_local_snapshot=True)
    if not isinstance(payload, dict):
        raise RuntimeError("TypeScript Turn controller returned an invalid response")
    if payload.get("ok") is False and isinstance(payload.get("message"), str):
        if payload.get("error_type") == "TypeError":
            raise TypeError(payload["message"])
        if payload.get("error_type") == "ValueError":
            raise ValueError(payload["message"])
    value = payload.get("value")
    if payload.get("ok") is not True or not isinstance(value, dict):
        raise RuntimeError("TypeScript Turn controller returned an invalid response")
    for record in (value, value.get("host_failure"), value.get("retry_continuation")):
        if isinstance(record, dict) and isinstance(record.get("attempt"), str):
            record["attempt"] = int(record["attempt"])
    return value


class ValidatedTurnReceipt:
    """A controller view qualified from one complete Turn execution.

    Material results are admitted only when the public execution proves the
    M7 typed settlement receipt sequence, one stable effect identity, durable
    writeback and spend, and a completed scheduler handoff. Todo completion
    additionally retains its durable continuation outcome so a local Todo
    completion cannot be confused with Goal terminal closure.
    """

    __slots__ = (
        "lineage",
        "host_failure",
        "task_failure",
        "result_kind",
        "settlement_effect_id",
        "status",
        "todo_completion",
        "turn_key",
    )

    def __init__(
        self,
        *,
        result_kind: LoopXTurnResultKind,
        status: str,
        lineage: Mapping[str, str],
        turn_key: str | None,
        host_failure: Mapping[str, Any] | None = None,
        task_failure: Mapping[str, Any] | None = None,
        settlement_effect_id: str | None = None,
        todo_completion: Mapping[str, Any] | None = None,
    ) -> None:
        self.result_kind = result_kind
        self.status = status
        self.lineage = {
            "goal_id": str(lineage.get("goal_id") or ""),
            "agent_id": str(lineage.get("agent_id") or ""),
            "todo_id": str(lineage.get("todo_id") or ""),
            **({"request_binding_id": lineage["request_binding_id"]} if lineage.get("request_binding_id") else {}),
        }
        self.turn_key = turn_key
        self.host_failure = dict(host_failure or {}) or None
        self.task_failure = dict(task_failure or {}) or None
        self.settlement_effect_id = settlement_effect_id
        self.todo_completion = dict(todo_completion or {}) or None

    @classmethod
    def from_execution(
        cls, execution_payload: Mapping[str, Any], *, request_identity: SettlementIdentity | None = None,
    ) -> ValidatedTurnReceipt:
        value = _controller_result("receipt", execution=(
            dict(execution_payload) if isinstance(execution_payload, Mapping) else {}
        ), **({"request_identity": request_identity.as_dict()} if request_identity is not None else {}))
        return cls(
            result_kind=LoopXTurnResultKind(value["result_kind"]),
            status=value["status"], lineage=value["lineage"], turn_key=value["turn_key"],
            settlement_effect_id=value["settlement_effect_id"],
            host_failure=value.get("host_failure"), task_failure=value.get("task_failure"),
            todo_completion=value.get("todo_completion"),
        )

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATED_TURN_RECEIPT_SCHEMA_VERSION,
            "result_kind": self.result_kind.value,
            "status": self.status,
            "lineage": dict(self.lineage),
            "turn_key": self.turn_key,
            "settlement_effect_id": self.settlement_effect_id,
            **({"task_failure": dict(self.task_failure)} if self.task_failure else {}),
            **(
                {"host_failure": dict(self.host_failure)}
                if self.host_failure
                else {}
            ),
            **(
                {"todo_completion": dict(self.todo_completion)}
                if self.todo_completion
                else {}
            ),
        }


class BoundedTurnBudget:
    """A provider-neutral bounded Turn budget tied to one lineage.

    Construction enforces strict integer domains (booleans are rejected) and a
    sane range, so the transition never guesses an unbounded continuation.
    """

    __slots__ = ("completed_turns", "lineage", "max_turns")

    def __init__(
        self,
        *,
        lineage: Mapping[str, str],
        max_turns: int,
        completed_turns: int,
    ) -> None:
        if type(max_turns) is not int:
            raise ValueError("bounded turn budget max_turns must be an int")
        if type(completed_turns) is not int:
            raise ValueError("bounded turn budget completed_turns must be an int")
        if max_turns <= 0:
            raise ValueError("bounded turn budget max_turns must be > 0")
        if completed_turns < 0:
            raise ValueError("bounded turn budget completed_turns must be >= 0")
        if completed_turns > max_turns:
            raise ValueError(
                "bounded turn budget completed_turns must be <= max_turns"
            )
        self.lineage = {
            "goal_id": str(lineage.get("goal_id") or ""),
            "agent_id": str(lineage.get("agent_id") or ""),
            "todo_id": str(lineage.get("todo_id") or ""),
        }
        if not all(self.lineage.values()):
            raise ValueError("bounded turn budget is missing goal/agent/todo lineage")
        self.max_turns = max_turns
        self.completed_turns = completed_turns

    def to_mapping(self) -> dict[str, Any]:
        return {
            "schema_version": BOUNDED_TURN_BUDGET_SCHEMA_VERSION,
            "lineage": dict(self.lineage),
            "max_turns": self.max_turns,
            "completed_turns": self.completed_turns,
        }

    @property
    def remaining(self) -> int:
        return self.max_turns - self.completed_turns


def decide_loop_disposition(
    *,
    turn_receipt: ValidatedTurnReceipt | None,
    quota_decision: Mapping[str, Any] | TurnControllerInput,
    predecessor_turn_key: str | None = None,
    bounded_turn_budget: BoundedTurnBudget | None = None,
) -> dict[str, Any]:
    """Transport complete facts; the TS owner preserves ordered early returns."""
    budget = bounded_turn_budget.to_mapping() if bounded_turn_budget is not None else None
    if budget is not None:
        budget = {**budget, "max_turns": str(budget["max_turns"]),
                  "completed_turns": str(budget["completed_turns"])}
    return _controller_result(
        "disposition", receipt=turn_receipt.to_mapping() if turn_receipt else None,
        decision=quota_decision, predecessor_turn_key=predecessor_turn_key,
        budget=budget,
    )
