"""Pure Turn Loop Controller transition contract.

This module decides the next disposition of a governed loop from one validated
Turn receipt plus a fresh quota/scheduler decision. It is a pure function: it
never invokes a model, sleeps, mutates a host scheduler, writes state, or
spends quota. `loopx turn run-once` remains the only delivery transaction;
scheduler process management, host wake APIs, and operator presentation belong
to later adapters (see the Turn Loop Controller plan in docs/development/contributor-tasks).

The transition output includes host delivery, capability-adapter handoff,
waiting, stopping, user action, repair, replan, and terminal closure.

Input validity is enforced at the typed-input boundary, not encoded as an
separate error disposition. ``decide_loop_disposition`` raises ``ValueError`` when a
receipt, envelope, or budget cannot be proven against the shared Turn
contracts, so the caller is responsible for feeding only validated, fresh
inputs.
"""

from __future__ import annotations
# The generated data is the shared contract; Python alone evaluates decisions.
from .turn_contract_generated import (
    TURN_CONTROLLER_CONTRACT as _LOOP_CONTROLLER_CONTRACT,
    project_turn_route as _route_to_disposition,
)
from .turn_contract_generated import LoopDisposition  # compatibility re-export
from ..quota.effective_action import EffectiveAction

from collections.abc import Mapping
from typing import Any, Literal

from ..effect_program import SettlementStepKind
from .driver import LoopXTurnRoute, _typed_route, selected_turn_todo
from .host_failure import (
    host_failure_retry_available,
    normalize_host_failure_record,
)
from .transaction import (
    LOOPX_TURN_EXECUTION_SCHEMA_VERSION,
    LOOPX_TURN_RECEIPT_VALIDATION_SCHEMA_VERSION,
    LoopXTurnResultKind,
    require_loopx_turn_completion_outcome,
)

LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION = "loop_turn_loop_disposition_v0"
BOUNDED_TURN_BUDGET_SCHEMA_VERSION = "loop_bounded_turn_budget_v0"
VALIDATED_TURN_RECEIPT_SCHEMA_VERSION = "loop_validated_turn_receipt_v0"

# Material result kinds that prove a durable delivery effect and may therefore
# drive a terminal or progress transition. They require a fully committed
# transaction (all seven phases), not a validation-only intermediate.
_MATERIAL_PROGRESS_KINDS = {
    LoopXTurnResultKind.VALIDATED_COMPLETION,
    LoopXTurnResultKind.VALIDATED_PROGRESS,
}
_MATERIAL_RECEIPT_ORDER = (
    SettlementStepKind.VALIDATION.value,
    SettlementStepKind.DURABLE_WRITEBACK.value,
    SettlementStepKind.QUOTA_SPEND.value,
)



def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _disposition(
    disposition: LoopDisposition,
    *,
    reason: str,
    lineage: Mapping[str, str] | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": LOOP_CONTROLLER_DISPOSITION_SCHEMA_VERSION,
        "disposition": disposition.value,
        "reason": reason,
        "spends_quota": False,
        "launches_host": False,
        "writes_state": False,
    }
    if lineage:
        payload["lineage"] = dict(lineage)
    if extra:
        payload.update(dict(extra))
    return payload


def _decision_lineage(decision: Mapping[str, Any]) -> dict[str, str]:
    selected_todo = selected_turn_todo(decision)
    return {
        "goal_id": str(decision.get("goal_id") or ""),
        "agent_id": str(decision.get("agent_id") or ""),
        "todo_id": str(selected_todo.get("todo_id") or ""),
    }


def _envelope_route(decision: Mapping[str, Any]) -> LoopXTurnRoute:
    """Return the shared typed route for a fresh quota/scheduler decision.

    Reuses the Turn plan driver's ``_typed_route`` contract, which requires a
    matching action signature with non-empty equal hashes. Compaction budget
    warnings are diagnostic only. A projected user action outranks delivery, so it is resolved
    before the typed delivery route. Raises ``ValueError`` when the envelope
    fails the shared contract instead of accepting a forged or truncated
    decision.
    """

    route = _typed_route(decision)
    if route is LoopXTurnRoute.CONTRACT_ERROR:
        raise ValueError(
            "quota decision failed the shared envelope contract "
            "(schema or signature hashes)"
        )
    user = _mapping(decision.get("user"))
    if user.get("action_required") is True:
        return LoopXTurnRoute.USER_ACTION_REQUIRED
    return route




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
        }
        self.turn_key = turn_key
        self.host_failure = dict(host_failure or {}) or None
        self.task_failure = dict(task_failure or {}) or None
        self.settlement_effect_id = settlement_effect_id
        self.todo_completion = dict(todo_completion or {}) or None

    @classmethod
    def from_execution(
        cls, execution_payload: Mapping[str, Any]
    ) -> ValidatedTurnReceipt:
        execution = _mapping(execution_payload)
        if execution.get("schema_version") != LOOPX_TURN_EXECUTION_SCHEMA_VERSION:
            raise ValueError(
                "validated turn receipt requires schema_version="
                f"{LOOPX_TURN_EXECUTION_SCHEMA_VERSION}"
            )
        receipt = _mapping(execution.get("receipt"))
        if receipt.get("schema_version") != LOOPX_TURN_RECEIPT_VALIDATION_SCHEMA_VERSION:
            raise ValueError(
                "validated turn receipt requires schema_version="
                f"{LOOPX_TURN_RECEIPT_VALIDATION_SCHEMA_VERSION}"
            )
        if receipt.get("ok") is not True:
            raise ValueError("validated turn receipt requires ok=true")
        raw_kind = receipt.get("result_kind")
        try:
            result_kind = LoopXTurnResultKind(str(raw_kind or ""))
        except ValueError:
            raise ValueError(
                f"validated turn receipt has unsupported result_kind {raw_kind!r}"
            ) from None
        if execution.get("result_kind") != result_kind.value:
            raise ValueError("execution result_kind does not match its receipt")
        status = str(receipt.get("status") or "")
        if result_kind in _MATERIAL_PROGRESS_KINDS and (
            status != "committed" or execution.get("status") != "committed"
        ):
            raise ValueError(
                f"material result {result_kind.value} requires a fully committed "
                "Turn execution and receipt"
            )
        lineage = _mapping(receipt.get("lineage"))
        if not all(lineage.get(k) for k in ("goal_id", "agent_id", "todo_id")):
            raise ValueError(
                "validated turn receipt is missing goal/agent/todo lineage"
            )
        turn_key = str(receipt.get("turn_key") or "") or None
        if not turn_key:
            raise ValueError("validated turn receipt is missing turn_key")
        settlement_effect_id = str(receipt.get("settlement_effect_id") or "") or None
        todo_completion = _mapping(execution.get("todo_completion"))
        host_failure = None
        if "host_failure" in execution:
            if result_kind is not LoopXTurnResultKind.HOST_FAILURE:
                raise ValueError(
                    "only a host_failure receipt may carry host failure metadata"
                )
            host_failure = normalize_host_failure_record(execution.get("host_failure"))
        if result_kind in _MATERIAL_PROGRESS_KINDS:
            settlement_effect_id = _qualified_material_effect_id(
                execution,
                expected_effect_id=settlement_effect_id,
            )
        if result_kind is LoopXTurnResultKind.VALIDATED_COMPLETION:
            todo_completion = require_loopx_turn_completion_outcome(
                todo_completion,
                expected_todo_id=str(lineage["todo_id"]),
            )
        from ..effect_runtime import effect_runtime_result
        task_failure = effect_runtime_result("turn.task_validation_failure", {
            "result_kind": result_kind.value, "status": execution.get("status"),
            "receipt": dict(receipt), "validation": execution.get("validation"),
            **({"validation_stage": execution["validation_stage"]} if "validation_stage" in execution else {}),
        })["failure"] if result_kind is LoopXTurnResultKind.VALIDATION_FAILED and "validation_stage" in execution else None
        return cls(
            task_failure=task_failure,
            result_kind=result_kind,
            status=status,
            lineage=lineage,
            turn_key=turn_key,
            host_failure=host_failure,
            settlement_effect_id=settlement_effect_id,
            todo_completion=todo_completion,
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


def _qualified_material_effect_id(
    execution: Mapping[str, Any],
    *,
    expected_effect_id: str | None,
) -> str:
    settlement = _mapping(execution.get("settlement_result"))
    if settlement.get("ok") is not True or settlement.get("failure") is not None:
        raise ValueError("material Turn execution requires a successful settlement")
    raw_receipts = settlement.get("receipts")
    if not isinstance(raw_receipts, list):
        raise TypeError("material Turn execution is missing settlement receipts")
    receipts = [_mapping(item) for item in raw_receipts]
    if [str(item.get("step_kind") or "") for item in receipts] != list(
        _MATERIAL_RECEIPT_ORDER
    ):
        raise ValueError(
            "material Turn execution requires ordered validation/writeback/spend receipts"
        )
    effect_ids = {str(item.get("effect_id") or "") for item in receipts}
    if "" in effect_ids or len(effect_ids) != 1:
        raise ValueError("material Turn settlement receipts must share one effect identity")
    effect_id = next(iter(effect_ids))
    if not expected_effect_id or expected_effect_id != effect_id:
        raise ValueError(
            "material Turn settlement identity does not match the transaction receipt"
        )
    if any(item.get("status") != "committed" for item in receipts):
        raise ValueError("material Turn settlement receipts must be committed")
    effects = _mapping(execution.get("effects"))
    committed_replay = (execution.get("replayed") is True and execution.get("dry_run") is False
        and execution.get("status") == "committed" and execution.get("quota_slot_spend_count") == 1
        and effects.get("host_invoked") is False
        and effects.get("state_written") is False and effects.get("quota_spent") is False)
    # A replay has no new effects; the ordered committed receipts above prove
    # the original durable settlement under the same transaction identity.
    if not committed_replay and (effects.get("state_written") is not True or effects.get("quota_spent") is not True):
        raise ValueError("material Turn execution is missing durable effect evidence")
    scheduler = _mapping(execution.get("scheduler"))
    if scheduler.get("completed") is not True:
        raise ValueError("material Turn execution is missing completed scheduler handoff")
    return effect_id


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


def _assert_goal_agent_match(
    *,
    receipt_lineage: Mapping[str, str],
    decision_lineage: Mapping[str, str],
) -> None:
    for key in ("goal_id", "agent_id"):
        if receipt_lineage.get(key) != decision_lineage.get(key):
            raise ValueError(
                "stale_receipt: receipt lineage does not match the fresh decision "
                f"on {key} (receipt={receipt_lineage.get(key)!r}, "
                f"decision={decision_lineage.get(key)!r})"
            )


def _assert_same_todo(
    *,
    receipt_lineage: Mapping[str, str],
    decision_lineage: Mapping[str, str],
) -> None:
    if receipt_lineage.get("todo_id") != decision_lineage.get("todo_id"):
        raise ValueError(
            "stale_receipt: receipt Todo does not match the fresh decision "
            f"(receipt={receipt_lineage.get('todo_id')!r}, "
            f"decision={decision_lineage.get('todo_id')!r})"
        )


def _assert_budget_lineage_match(
    *,
    budget_lineage: Mapping[str, str],
    decision_lineage: Mapping[str, str],
) -> None:
    for key in ("goal_id", "agent_id", "todo_id"):
        if budget_lineage.get(key) != decision_lineage.get(key):
            raise ValueError(
                "stale_budget: budget lineage does not match the fresh decision "
                f"on {key} (budget={budget_lineage.get(key)!r}, "
                f"decision={decision_lineage.get(key)!r})"
            )


def _assert_predecessor_binding(
    *,
    receipt: ValidatedTurnReceipt,
    predecessor_turn_key: str | None,
) -> None:
    """Prove the fresh envelope causally succeeds the receipt.

    The outer adapter must bind the fresh decision to the receipt's
    ``turn_key``. The causal assertion stays beside the envelope instead of
    adding an unsigned field to ``loopx_turn_envelope_v0``.
    """

    predecessor = str(predecessor_turn_key or "")
    if not predecessor:
        raise ValueError(
            "stale_receipt: continuation is missing predecessor_turn_key; "
            "cannot prove causal succession from the receipt"
        )
    if predecessor != receipt.turn_key:
        raise ValueError(
            "stale_receipt: fresh decision predecessor_turn_key "
            f"({predecessor!r}) does not match the receipt turn_key "
            f"({receipt.turn_key!r})"
        )


class _ControllerInputs:
    """Lazy semantic partitions and the existing ordered admission checks.

    Facts are read only when a contract row reaches them: a user gate need not
    have a progress budget, and recovery routing need not inspect retry policy.
    Receipt/budget constructors still own their shape and evidence validation.
    No arbitrary predicates, callbacks or evaluation language are accepted.
    """

    def __init__(
        self,
        receipt: ValidatedTurnReceipt | None,
        decision: Mapping[str, Any],
        predecessor: str | None,
        budget: BoundedTurnBudget | None,
    ) -> None:
        self.receipt = receipt
        self.decision = decision
        self.predecessor = predecessor
        self.budget = budget
        self.route: LoopXTurnRoute
        self.decision_lineage: dict[str, str]
        self.effective_lineage: dict[str, str]
        self.retry_state: Literal[
            "available", "exhausted", "not_retryable", "not_applicable"
        ] = "not_applicable"

    def fact(self, name: str) -> str | bool:
        if name == "receipt_present":
            return self.receipt is not None
        if name == "validation_recovery":
            return str((self.receipt.task_failure or {}).get("recovery_kind") or "absent") if self.receipt else "absent"
        if name == "receipt_kind":
            return self.receipt.result_kind.value if self.receipt else "absent"
        if name == "route":
            return self.route.value
        if name == "terminal_action":
            return (
                str(self.decision.get("effective_action") or "")
                == EffectiveAction.TERMINAL_NO_FOLLOWUP.value
            )
        if name == "completion":
            if (
                not self.receipt
                or self.receipt.result_kind
                is not LoopXTurnResultKind.VALIDATED_COMPLETION
            ):
                return "not_applicable"
            return str((self.receipt.todo_completion or {}).get("continuation") or "")
        if name == "budget_state":
            if (
                not self.receipt
                or self.receipt.result_kind
                is not LoopXTurnResultKind.VALIDATED_PROGRESS
            ):
                return "not_applicable"
            if self.budget is None:
                return "absent"
            return "exhausted" if self.budget.remaining <= 0 else "available"
        if name == "host_failure_present":
            return bool(self.receipt and self.receipt.host_failure)
        if name == "retry_state":
            return self.retry_state
        raise ValueError(f"unsupported controller partition: {name}")

    def check(self, name: str) -> None:
        receipt = self.receipt
        if name == "envelope":
            self.route = _envelope_route(self.decision)
        elif name == "decision_actor":
            self.decision_lineage = _decision_lineage(self.decision)
            if (
                not self.decision_lineage["goal_id"]
                or not self.decision_lineage["agent_id"]
            ):
                raise ValueError("fresh quota decision is missing goal/agent lineage")
            self.effective_lineage = dict(self.decision_lineage)
        elif name == "receipt_binding":
            assert receipt is not None
            _assert_predecessor_binding(
                receipt=receipt, predecessor_turn_key=self.predecessor
            )
            _assert_goal_agent_match(
                receipt_lineage=receipt.lineage, decision_lineage=self.decision_lineage
            )
        elif name == "initial_terminal":
            if self.decision.get("state") != "terminal_no_followup":
                raise ValueError(
                    "terminal no-follow-up requires fresh Goal frontier state"
                )
        elif name == "initial_todo":
            if not self.decision_lineage["todo_id"] and _route_to_disposition(
                self.route
            ) not in {
                LoopDisposition.WAIT,
                LoopDisposition.USER_ACTION_REQUIRED,
            }:
                raise ValueError(
                    "executable quota decision is missing selected Todo lineage"
                )
        elif name == "completion_terminal":
            if (
                self.decision.get("effective_action")
                != EffectiveAction.TERMINAL_NO_FOLLOWUP.value
                or self.decision.get("state") != "terminal_no_followup"
            ):
                raise ValueError(
                    "no_followup completion requires fresh terminal Goal frontier evidence"
                )
        elif name == "completion_todo":
            assert receipt is not None
            selected = self.decision_lineage["todo_id"]
            if not selected:
                raise ValueError(
                    "Todo completion continuation requires a fresh selected Todo"
                )
            completion = receipt.todo_completion or {}
            if self.fact("completion") == "successor":
                successors = completion.get("successor_todo_ids")
                if not isinstance(successors, list) or selected not in successors:
                    raise ValueError(
                        "stale_receipt: fresh decision is not a declared completion successor"
                    )
            elif (
                self.fact("completion") == "active_goal"
                and selected == receipt.lineage["todo_id"]
            ):
                raise ValueError(
                    "stale_receipt: active Goal continuation reselected the completed Todo"
                )
        elif name == "receipt_todo":
            assert receipt is not None
            if self.decision_lineage["todo_id"]:
                _assert_same_todo(
                    receipt_lineage=receipt.lineage,
                    decision_lineage=self.decision_lineage,
                )
            elif (
                receipt.result_kind is LoopXTurnResultKind.VALIDATED_PROGRESS
                and self.route
                not in {
                    LoopXTurnRoute.USER_ACTION_REQUIRED,
                    LoopXTurnRoute.WAIT,
                }
            ):
                raise ValueError(
                    "receipt-backed executable decision is missing selected Todo lineage"
                )
            else:
                self.effective_lineage["todo_id"] = receipt.lineage["todo_id"]
        elif name == "progress_budget":
            if self.budget is None:
                raise ValueError(
                    "validated progress cannot continue without a proven bounded turn budget"
                )
            _assert_budget_lineage_match(
                budget_lineage=self.budget.lineage,
                decision_lineage=self.effective_lineage,
            )
        elif name == "host_retry":
            assert receipt is not None and receipt.host_failure
            if host_failure_retry_available(receipt.host_failure):
                self.retry_state = "available"
            elif receipt.host_failure.get("retryable") is True:
                self.retry_state = "exhausted"
            else:
                self.retry_state = "not_retryable"
        else:
            raise ValueError(f"unsupported controller check: {name}")

    def render(self, rule: Mapping[str, Any]) -> dict[str, Any]:
        disposition = (
            _route_to_disposition(self.route)
            if rule["disposition"] == "project_route"
            else LoopDisposition(rule["disposition"])
        )
        reason = rule["reason"]
        if isinstance(reason, Mapping):
            reason = reason.get(disposition.value, reason.get("default"))
        if not isinstance(reason, str):
            raise ValueError(
                f"controller rule {rule['id']} has no reason for {disposition.value}"
            )
        failure = (self.receipt.host_failure or {}) if self.receipt else {}
        reason = reason.format(
            failure_kind=failure.get("kind"), receipt_kind=self.fact("receipt_kind")
        )
        lineage = {
            "decision": self.decision_lineage,
            "effective": self.effective_lineage,
            "receipt": self.receipt.lineage if self.receipt else {},
        }[rule["lineage"]]
        if disposition is LoopDisposition.REPLAN:
            return _replan_disposition(reason=reason, decision_lineage=lineage)
        extra = None
        if rule.get("extra") == "capability":
            extra = {
                "capability_action": _mapping(
                    _mapping(self.decision.get("action")).get("capability_intent")
                )
            }
        elif rule.get("extra") == "iteration_stop":
            extra = {
                "stop_scope": "iteration",
                "goal_terminal": False,
                "continuation_required": False,
            }
        elif rule.get("extra") == "retry":
            retry = _mapping(failure.get("retry"))
            extra = {
                "retry_continuation": {
                    "same_turn": True,
                    "retry_failed_turn": True,
                    "strategy": retry["strategy"],
                    "retry_after_seconds": retry["backoff_seconds"],
                    "attempt": failure["attempt"],
                    "max_attempts": retry["max_attempts"],
                    "fresh_envelope_required": True,
                    "model_fallback_allowed": False,
                }
            }
        elif rule.get("extra") is not None:
            raise ValueError(f"unsupported controller continuation: {rule['extra']}")
        return _disposition(disposition, reason=reason, lineage=lineage, extra=extra)


def decide_loop_disposition(
    *,
    turn_receipt: ValidatedTurnReceipt | None,
    quota_decision: Mapping[str, Any],
    predecessor_turn_key: str | None = None,
    bounded_turn_budget: BoundedTurnBudget | None = None,
) -> dict[str, Any]:
    """Evaluate the bundled ordered controller contract without side effects.

    Receipt and budget admission remain :class:`ValidatedTurnReceipt` and
    :class:`BoundedTurnBudget`. The separate predecessor key proves causal
    succession without adding an unsigned field to the quota envelope.

    Each matching check must pass before evaluation proceeds; the first
    matching return wins. The finite partitions and their precedence are in
    ``turn_loop_controller_contract_v0.json``, including checks skipped by
    earlier returns. Invalid or stale inputs raise, never become dispositions.
    """

    inputs = _ControllerInputs(
        turn_receipt, quota_decision, predecessor_turn_key, bounded_turn_budget
    )
    for rule in _LOOP_CONTROLLER_CONTRACT["rules"]:
        if not all(
            inputs.fact(name) in values for name, values in rule["when"].items()
        ):
            continue
        if "check" in rule:
            inputs.check(rule["check"])
        else:
            return inputs.render(rule)
    raise ValueError("controller contract has no disposition for the qualified inputs")


def _replan_disposition(
    *, reason: str, decision_lineage: Mapping[str, str]
) -> dict[str, Any]:
    return _disposition(
        LoopDisposition.REPLAN,
        reason=reason,
        lineage=decision_lineage,
        extra={
            "replan_continuation": {
                "requires_bounded_delta": True,
                "delta_kinds": ["todo_delta", "vision_delta"],
                "stale_todo_rerun_allowed": False,
                "fresh_envelope_required": True,
            }
        },
    )
