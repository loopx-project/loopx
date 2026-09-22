from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Generic, TypeVar

from .effect_runtime import EffectRuntimeRejected, effect_runtime_result


# Identity remains stable while the plan and receipt versions advance with the
# typed terminal-closeout step shared by quota and Turn adapters.
SETTLEMENT_IDENTITY_SCHEMA_VERSION = "quota_settlement_identity_v0"
SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION = "quota_settlement_identity_v1"
SETTLEMENT_PLAN_SCHEMA_VERSION = "quota_settlement_plan_v1"
SETTLEMENT_RECEIPT_SCHEMA_VERSION = "quota_settlement_receipt_v1"


class ReceiptBoundMonitorPhase(StrEnum):
    POLL_DUE = "poll_due"
    SETTLEMENT_PENDING = "settlement_pending"
    SETTLED = "settled"


class ReceiptBoundReplayPhase(StrEnum):
    OPEN = "open"
    SETTLEMENT_PENDING = "settlement_pending"
    SETTLED = "settled"


# Compatibility alias for callers that predate ordinary-completion replay.
ReceiptBoundTerminalPhase = ReceiptBoundReplayPhase


def receipt_bound_monitor_phase(
    *,
    poll_present: bool,
    material_change: bool,
    durable_writeback_present: bool,
    quota_spend_present: bool,
) -> ReceiptBoundMonitorPhase | None:
    result = effect_runtime_result(
        "settlement.receipt_bound_monitor_phase",
        {
            "poll_present": poll_present,
            "material_change": material_change,
            "durable_writeback_present": durable_writeback_present,
            "quota_spend_present": quota_spend_present,
        },
    )
    if result is None:
        return None
    return ReceiptBoundMonitorPhase(str(result))


def receipt_bound_replay_phase(
    *,
    binding_kind: SettlementBindingKind | str | None = None,
    writeback_completes_binding: bool = False,
    completion_receipt_present: bool,
    durable_writeback_present: bool,
    quota_spend_present: bool,
) -> ReceiptBoundReplayPhase | None:
    params: dict[str, Any] = {
        "completion_receipt_present": completion_receipt_present,
        "durable_writeback_present": durable_writeback_present,
        "quota_spend_present": quota_spend_present,
    }
    if binding_kind is not None:
        params["binding_kind"] = str(binding_kind)
    if writeback_completes_binding:
        params["writeback_completes_binding"] = True
    result = effect_runtime_result(
        "settlement.receipt_bound_replay_phase",
        params,
    )
    if result is None:
        return None
    return ReceiptBoundReplayPhase(str(result))


def receipt_bound_terminal_phase(
    *,
    terminal_closeout_present: bool,
    durable_writeback_present: bool,
    quota_spend_present: bool,
) -> ReceiptBoundTerminalPhase | None:
    """Compatibility adapter for the pre-successor terminal-only contract."""

    return receipt_bound_replay_phase(
        completion_receipt_present=terminal_closeout_present,
        durable_writeback_present=durable_writeback_present,
        quota_spend_present=quota_spend_present,
    )


@dataclass(frozen=True)
class EffectRequest:
    kind: str
    source: str
    goal_id: str | None = None
    agent_id: str | None = None
    capabilities: tuple[str, ...] = ()
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectInterpretation:
    route: str
    obligation: str
    interaction_mode: str
    capability_action: str | None = None
    cadence_class: str | None = None


@dataclass(frozen=True)
class EffectObservation:
    decision: str
    should_run: bool
    effective_action: str | None
    recommended_action: str
    action_portfolio: Mapping[str, Any] | None = None
    planning_horizon: Mapping[str, Any] | None = None
    protocol_summary: str | None = None


@dataclass(frozen=True)
class EffectNext:
    cli_actions: tuple[str, ...] = ()
    execution_mode: str | None = None
    scheduler_action: str | None = None
    cadence_class: str | None = None
    ack_cli_args: tuple[str, ...] = ()
    failure_cli_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class EffectTurn:
    request: EffectRequest
    interpretation: EffectInterpretation
    observation: EffectObservation
    next_effect: EffectNext


@dataclass(frozen=True)
class EffectStep:
    step_id: str | None = None
    kind: str | None = None
    command: str | None = None
    purpose: str | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EffectProgram:
    steps: tuple[EffectStep, ...] = ()
    execution_mode: str | None = None


_TURN_TRANSACTION_CONTRACT_PATH = Path(__file__).with_name(
    "turn_transaction_contract.json"
)
_TURN_TRANSACTION_CONTRACT = json.loads(
    _TURN_TRANSACTION_CONTRACT_PATH.read_text(encoding="utf-8")
)
TURN_TRANSACTION_PHASES = tuple(_TURN_TRANSACTION_CONTRACT["phases"])


class SettlementStepKind(StrEnum):
    VALIDATION = "validation"
    DURABLE_WRITEBACK = "durable_writeback"
    QUOTA_SPEND = "quota_spend"
    TERMINAL_CLOSEOUT = "terminal_closeout"


class SettlementBindingKind(StrEnum):
    TODO = "todo"
    AUTONOMOUS_REPLAN = "autonomous_replan"
    UNBOUND = "unbound"


class SettlementFailureKind(StrEnum):
    INVALID_IDENTITY = "invalid_identity"
    RECEIPT_MISSING = "receipt_missing"
    RECEIPT_UNBOUND = "receipt_unbound"
    IDENTITY_MISMATCH = "identity_mismatch"
    WRITEBACK_MISSING = "writeback_missing"
    WRITEBACK_REJECTED = "writeback_rejected"
    QUOTA_SPEND_REJECTED = "quota_spend_rejected"
    TERMINAL_CLOSEOUT_REJECTED = "terminal_closeout_rejected"
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"
    BUDGET_REJECTED = "budget_rejected"
    EFFECT_OUTCOME_UNKNOWN = "effect_outcome_unknown"


@lru_cache(maxsize=4096)
def _settlement_identity_full(
    goal_id: str,
    agent_id: str,
    todo_id: str | None,
    turn_instance_id: str,
    replan_obligation_id: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        result = effect_runtime_result(
            "settlement.identity_full",
            {
                "goal_id": goal_id,
                "agent_id": agent_id,
                "todo_id": todo_id,
                "turn_instance_id": turn_instance_id,
                "replan_obligation_id": replan_obligation_id,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript settlement identity shape mismatch")
    identity = result.get("identity")
    payload = result.get("payload")
    if not isinstance(identity, Mapping) or not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript settlement identity shape mismatch")
    return dict(identity), dict(payload)


@dataclass(frozen=True, slots=True)
class SettlementIdentity:
    goal_id: str
    agent_id: str
    todo_id: str | None
    turn_instance_id: str
    replan_obligation_id: str | None = None
    _runtime_identity: Mapping[str, Any] = field(
        init=False,
        repr=False,
        compare=False,
    )
    _runtime_payload: Mapping[str, Any] = field(
        init=False,
        repr=False,
        compare=False,
    )

    def __post_init__(self) -> None:
        identity, payload = _settlement_identity_full(
            self.goal_id,
            self.agent_id,
            self.todo_id,
            self.turn_instance_id,
            self.replan_obligation_id,
        )
        object.__setattr__(self, "todo_id", identity.get("todo_id"))
        object.__setattr__(
            self, "replan_obligation_id", identity.get("replan_obligation_id")
        )
        object.__setattr__(self, "_runtime_identity", identity)
        object.__setattr__(self, "_runtime_payload", payload)

    @property
    def binding_kind(self) -> SettlementBindingKind:
        return SettlementBindingKind(str(self._runtime_identity["binding_kind"]))

    @property
    def binding_id(self) -> str:
        return str(self._runtime_identity["binding_id"])

    @property
    def effect_id(self) -> str:
        return str(self._runtime_identity["effect_id"])

    def as_dict(self) -> dict[str, str]:
        return {key: str(value) for key, value in self._runtime_payload.items()}

    @classmethod
    def from_runtime_payload(cls, payload: Any) -> SettlementIdentity:
        """Decode a TS-owned identity without issuing a second runtime request."""

        if not isinstance(payload, Mapping):
            raise RuntimeError("TypeScript settlement identity shape mismatch")
        schema_version = payload.get("schema_version")
        if schema_version not in {
            SETTLEMENT_IDENTITY_SCHEMA_VERSION,
            SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION,
        }:
            raise RuntimeError("TypeScript settlement identity shape mismatch")
        required = ("effect_id", "goal_id", "agent_id", "turn_instance_id")
        if any(
            not isinstance(payload.get(key), str) or not str(payload[key]).strip()
            for key in required
        ):
            raise RuntimeError("TypeScript settlement identity shape mismatch")
        todo_id = str(payload.get("todo_id") or "").strip() or None
        replan_obligation_id = (
            str(payload.get("replan_obligation_id") or "").strip() or None
        )
        if bool(todo_id) == bool(replan_obligation_id):
            raise RuntimeError("TypeScript settlement identity shape mismatch")
        binding_kind = (
            SettlementBindingKind.TODO
            if todo_id
            else SettlementBindingKind.AUTONOMOUS_REPLAN
        )
        binding_id = todo_id or replan_obligation_id
        if schema_version == SCOPED_SETTLEMENT_IDENTITY_SCHEMA_VERSION and (
            payload.get("binding_kind") != binding_kind.value
            or payload.get("binding_id") != binding_id
        ):
            raise RuntimeError("TypeScript settlement identity shape mismatch")
        instance = object.__new__(cls)
        object.__setattr__(instance, "goal_id", str(payload["goal_id"]))
        object.__setattr__(instance, "agent_id", str(payload["agent_id"]))
        object.__setattr__(instance, "todo_id", todo_id)
        object.__setattr__(
            instance,
            "turn_instance_id",
            str(payload["turn_instance_id"]),
        )
        object.__setattr__(instance, "replan_obligation_id", replan_obligation_id)
        object.__setattr__(
            instance,
            "_runtime_identity",
            {
                "goal_id": instance.goal_id,
                "agent_id": instance.agent_id,
                "todo_id": todo_id,
                "turn_instance_id": instance.turn_instance_id,
                "replan_obligation_id": replan_obligation_id,
                "binding_kind": binding_kind.value,
                "binding_id": str(binding_id),
                "effect_id": str(payload["effect_id"]),
            },
        )
        object.__setattr__(instance, "_runtime_payload", dict(payload))
        return instance


@dataclass(frozen=True, slots=True)
class SettlementReceipt:
    step_kind: SettlementStepKind
    status: str
    effect_id: str
    source_ref: str | None = None

    def as_dict(self) -> dict[str, str]:
        receipt = {
            "schema_version": SETTLEMENT_RECEIPT_SCHEMA_VERSION,
            "step_kind": self.step_kind.value,
            "status": self.status,
            "effect_id": self.effect_id,
        }
        if self.source_ref:
            receipt["source_ref"] = self.source_ref
        return receipt


@dataclass(frozen=True, slots=True)
class SettlementFailure:
    kind: SettlementFailureKind
    step_kind: SettlementStepKind
    reason: str
    details: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "kind": self.kind.value,
            "step_kind": self.step_kind.value,
            "reason": self.reason,
        }
        if self.details:
            result["details"] = dict(self.details)
        return result


def _runtime_receipt(receipt: SettlementReceipt) -> dict[str, Any]:
    return {
        "step_kind": receipt.step_kind.value,
        "status": receipt.status,
        "effect_id": receipt.effect_id,
        **({"source_ref": receipt.source_ref} if receipt.source_ref else {}),
    }


def _runtime_failure(failure: SettlementFailure | None) -> dict[str, Any] | None:
    if failure is None:
        return None
    return {
        "kind": failure.kind.value,
        "step_kind": failure.step_kind.value,
        "reason": failure.reason,
        **({"details": dict(failure.details)} if failure.details else {}),
    }


def _runtime_result(result: SettlementResult[Any]) -> dict[str, Any]:
    return {
        "value": None,
        "receipts": [_runtime_receipt(receipt) for receipt in result.receipts],
        "failure": _runtime_failure(result.failure),
    }


def _required_settlement_string(
    payload: Mapping[str, Any],
    key: str,
    *,
    shape: str,
) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"TypeScript settlement result {shape} shape mismatch")
    return value


_SettlementEnum = TypeVar("_SettlementEnum", bound=StrEnum)


def _required_settlement_enum(
    enum_type: type[_SettlementEnum],
    payload: Mapping[str, Any],
    key: str,
    *,
    shape: str,
) -> _SettlementEnum:
    try:
        return enum_type(_required_settlement_string(payload, key, shape=shape))
    except ValueError:
        raise RuntimeError(
            f"TypeScript settlement result {shape} shape mismatch"
        ) from None


def decode_settlement_receipt_payload(payload: Any) -> SettlementReceipt:
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript settlement result receipts shape mismatch")
    source_ref = payload.get("source_ref")
    if source_ref is not None and (
        not isinstance(source_ref, str) or not source_ref.strip()
    ):
        raise RuntimeError("TypeScript settlement result receipts shape mismatch")
    return SettlementReceipt(
        step_kind=_required_settlement_enum(
            SettlementStepKind,
            payload,
            "step_kind",
            shape="receipts",
        ),
        status=_required_settlement_string(payload, "status", shape="receipts"),
        effect_id=_required_settlement_string(
            payload,
            "effect_id",
            shape="receipts",
        ),
        source_ref=source_ref,
    )


def decode_settlement_result_payload(
    payload: Any,
) -> tuple[Any, tuple[SettlementReceipt, ...], SettlementFailure | None]:
    """Decode the shared TS settlement envelope at the Python boundary."""

    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript settlement result shape mismatch")
    if "value" not in payload or "failure" not in payload:
        raise RuntimeError("TypeScript settlement result shape mismatch")
    receipts_value = payload.get("receipts")
    if not isinstance(receipts_value, list):
        raise RuntimeError("TypeScript settlement result receipts shape mismatch")
    receipts = tuple(
        decode_settlement_receipt_payload(receipt) for receipt in receipts_value
    )
    failure_value = payload.get("failure")
    failure = None
    if failure_value is not None:
        if not isinstance(failure_value, Mapping):
            raise RuntimeError("TypeScript settlement result failure shape mismatch")
        if payload.get("value") is not None:
            raise RuntimeError("TypeScript settlement result shape mismatch")
        details = failure_value.get("details")
        if details is not None and not isinstance(details, Mapping):
            raise RuntimeError("TypeScript settlement result failure shape mismatch")
        failure = SettlementFailure(
            kind=_required_settlement_enum(
                SettlementFailureKind,
                failure_value,
                "kind",
                shape="failure",
            ),
            step_kind=_required_settlement_enum(
                SettlementStepKind,
                failure_value,
                "step_kind",
                shape="failure",
            ),
            reason=_required_settlement_string(
                failure_value,
                "reason",
                shape="failure",
            ),
            details=dict(details) if details is not None else None,
        )
    return payload.get("value"), receipts, failure


T = TypeVar("T")
U = TypeVar("U")


@dataclass(frozen=True, slots=True)
class SettlementResult(Generic[T]):
    value: T | None
    receipts: tuple[SettlementReceipt, ...] = ()
    failure: SettlementFailure | None = None
    _runtime_payload: Mapping[str, Any] | None = field(
        default=None,
        repr=False,
        compare=False,
    )

    @classmethod
    def pure(
        cls,
        value: T,
        *,
        receipts: tuple[SettlementReceipt, ...] = (),
    ) -> SettlementResult[T]:
        return cls(value=value, receipts=receipts)

    @classmethod
    def failed(
        cls,
        *,
        kind: SettlementFailureKind,
        step_kind: SettlementStepKind,
        reason: str,
        receipts: tuple[SettlementReceipt, ...] = (),
        details: Mapping[str, Any] | None = None,
    ) -> SettlementResult[T]:
        return cls(
            value=None,
            receipts=receipts,
            failure=SettlementFailure(
                kind=kind,
                step_kind=step_kind,
                reason=reason,
                details=details,
            ),
        )

    def bind(self, step: Callable[[T], SettlementResult[U]]) -> SettlementResult[U]:
        gate = effect_runtime_result(
            "settlement.bind_gate",
            {"result": _runtime_result(self)},
        )
        if not isinstance(gate, Mapping) or not isinstance(gate.get("execute"), bool):
            raise RuntimeError("TypeScript settlement bind gate shape mismatch")
        if gate["execute"] is False:
            return SettlementResult(
                value=None,
                receipts=self.receipts,
                failure=self.failure,
            )
        next_result = step(self.value)  # type: ignore[arg-type]
        reduced = effect_runtime_result(
            "settlement.bind_reduce",
            {
                "current": _runtime_result(self),
                "next": _runtime_result(next_result),
            },
        )
        _, receipts, failure = decode_settlement_result_payload(reduced)
        return SettlementResult(
            value=next_result.value,
            receipts=receipts,
            failure=failure,
        )


@dataclass(frozen=True, slots=True)
class SettlementStep:
    kind: SettlementStepKind
    owner: str
    precondition: str
    idempotency_key_ref: str
    expected_receipt: str
    command_template: str | None = None
    conditional: bool = False

    def as_dict(self) -> dict[str, Any]:
        step: dict[str, Any] = {
            "kind": self.kind.value,
            "owner": self.owner,
            "precondition": self.precondition,
            "idempotency_key_ref": self.idempotency_key_ref,
            "expected_receipt": self.expected_receipt,
        }
        if self.command_template:
            step["command_template"] = self.command_template
        if self.conditional:
            step["conditional"] = True
        return step


@dataclass(frozen=True, slots=True)
class SettlementPlan:
    identity: SettlementIdentity
    steps: tuple[SettlementStep, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = effect_runtime_result(
            "settlement.plan_payload",
            {
                "plan": {
                    "identity": self.identity.as_dict(),
                    "steps": [step.as_dict() for step in self.steps],
                }
            },
        )
        if not isinstance(payload, Mapping):
            raise RuntimeError("TypeScript settlement plan shape mismatch")
        return dict(payload)


def settlement_result_payload(result: SettlementResult[Any]) -> dict[str, Any]:
    if result._runtime_payload is not None:
        return dict(result._runtime_payload)
    payload = effect_runtime_result(
        "settlement.result_payload",
        {"result": _runtime_result(result)},
    )
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript settlement result payload shape mismatch")
    return dict(payload)


def _effect_turn_from_payload(payload: Any) -> EffectTurn:
    if not isinstance(payload, Mapping):
        raise RuntimeError("TypeScript Effect turn shape mismatch")
    request = payload.get("request")
    interpretation = payload.get("interpretation")
    observation = payload.get("observation")
    next_effect = payload.get("next_effect")
    if not all(
        isinstance(value, Mapping)
        for value in (request, interpretation, observation, next_effect)
    ):
        raise RuntimeError("TypeScript Effect turn shape mismatch")
    assert isinstance(request, Mapping)
    assert isinstance(interpretation, Mapping)
    assert isinstance(observation, Mapping)
    assert isinstance(next_effect, Mapping)
    context = request.get("context")
    normalized_context = dict(context) if isinstance(context, Mapping) else {}
    if isinstance(normalized_context.get("completed_phases"), list):
        normalized_context["completed_phases"] = tuple(
            str(phase) for phase in normalized_context["completed_phases"]
        )
    return EffectTurn(
        request=EffectRequest(
            kind=str(request.get("kind") or ""),
            source=str(request.get("source") or ""),
            goal_id=str(request.get("goal_id"))
            if request.get("goal_id") is not None
            else None,
            agent_id=str(request.get("agent_id"))
            if request.get("agent_id") is not None
            else None,
            capabilities=tuple(str(item) for item in request.get("capabilities", [])),
            context=normalized_context,
        ),
        interpretation=EffectInterpretation(
            route=str(interpretation.get("route") or ""),
            obligation=str(interpretation.get("obligation") or ""),
            interaction_mode=str(interpretation.get("interaction_mode") or ""),
            capability_action=(
                str(interpretation["capability_action"])
                if interpretation.get("capability_action") is not None
                else None
            ),
            cadence_class=(
                str(interpretation["cadence_class"])
                if interpretation.get("cadence_class") is not None
                else None
            ),
        ),
        observation=EffectObservation(
            decision=str(observation.get("decision") or ""),
            should_run=observation.get("should_run") is True,
            effective_action=(
                str(observation["effective_action"])
                if observation.get("effective_action") is not None
                else None
            ),
            recommended_action=str(observation.get("recommended_action") or ""),
            action_portfolio=(
                dict(observation["action_portfolio"])
                if isinstance(observation.get("action_portfolio"), Mapping)
                else None
            ),
            planning_horizon=(
                dict(observation["planning_horizon"])
                if isinstance(observation.get("planning_horizon"), Mapping)
                else None
            ),
            protocol_summary=(
                str(observation["protocol_summary"])
                if observation.get("protocol_summary") is not None
                else None
            ),
        ),
        next_effect=EffectNext(
            cli_actions=tuple(str(item) for item in next_effect.get("cli_actions", [])),
            execution_mode=(
                str(next_effect["execution_mode"])
                if next_effect.get("execution_mode") is not None
                else None
            ),
            scheduler_action=(
                str(next_effect["scheduler_action"])
                if next_effect.get("scheduler_action") is not None
                else None
            ),
            cadence_class=(
                str(next_effect["cadence_class"])
                if next_effect.get("cadence_class") is not None
                else None
            ),
            ack_cli_args=tuple(
                str(item) for item in next_effect.get("ack_cli_args", [])
            ),
            failure_cli_args=tuple(
                str(item) for item in next_effect.get("failure_cli_args", [])
            ),
        ),
    )


def effect_program_from_ordered_steps(
    ordered_steps: Sequence[Mapping[str, Any]],
    *,
    execution_mode: str | None = None,
) -> EffectProgram:
    """Map existing `guided_transaction.ordered_steps` onto an effect program."""

    payload = effect_runtime_result(
        "effect.program_from_ordered_steps",
        {
            "ordered_steps": [
                dict(step) if isinstance(step, Mapping) else step
                for step in ordered_steps
            ],
            "execution_mode": execution_mode,
        },
    )
    if not isinstance(payload, Mapping) or not isinstance(payload.get("steps"), list):
        raise RuntimeError("TypeScript Effect Program shape mismatch")
    steps = tuple(
        EffectStep(
            step_id=str(step["step_id"]) if step.get("step_id") is not None else None,
            kind=str(step["kind"]) if step.get("kind") is not None else None,
            command=str(step["command"]) if step.get("command") is not None else None,
            purpose=str(step["purpose"]) if step.get("purpose") is not None else None,
            raw=dict(step["raw"]) if isinstance(step.get("raw"), Mapping) else {},
        )
        for step in payload["steps"]
        if isinstance(step, Mapping)
    )
    mode = payload.get("execution_mode")
    return EffectProgram(
        steps=steps, execution_mode=str(mode) if mode is not None else None
    )


def interpret_quota_should_run_packet(
    packet: Mapping[str, Any],
    *,
    goal_id: str | None = None,
    agent_id: str | None = None,
    capabilities: Sequence[str] = (),
) -> EffectTurn:
    """Map an existing `quota should-run` packet onto canonical effect slots."""
    return _effect_turn_from_payload(
        effect_runtime_result(
            "effect.interpret_quota",
            {
                "packet": dict(packet),
                "identity": {
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                    "capabilities": list(capabilities),
                },
            },
        )
    )


def interpret_turn_result_packet(
    packet: Mapping[str, Any],
    *,
    goal_id: str | None = None,
    agent_id: str | None = None,
    capabilities: Sequence[str] = (),
) -> EffectTurn:
    """Project a Turn verdict; the TS owner emits no quota action (None)."""
    return _effect_turn_from_payload(
        effect_runtime_result(
            "effect.interpret_turn_result",
            {
                "packet": dict(packet),
                "identity": {
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                    "capabilities": list(capabilities),
                },
            },
        )
    )
