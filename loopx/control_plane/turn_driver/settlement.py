"""Turn-driver adapter for the shared typed settlement receipt-chain driver."""

from __future__ import annotations

import inspect
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from ..effect_program import (
    SettlementResult,
    SettlementStepKind,
    TurnProviderStepKind,
    require_turn_provider_step_kind,
    settlement_result_payload,
)
from ..effect_runtime import effect_runtime_result
from ..goals.source_session_turn_effects import (
    SOURCE_TURN_EFFECT_HOLD_SCHEMA_VERSION,
)
from ..settlement_driver import decode_settlement_result
from .driver import selected_turn_todo
from .transaction import (
    LoopXTurnResultKind,
    require_loopx_turn_completion_outcome,
)


TurnEffect = Callable[..., Mapping[str, Any]]
TurnEffectResolver = Callable[[str], Mapping[str, Any]]
ResultEffect = Callable[..., Mapping[str, Any]]
TurnSettlementPrepare = Callable[[SettlementStepKind, str], None]
TurnSettlementAbort = Callable[[SettlementStepKind, str], None]
TurnSettlementCheckpoint = Callable[
    [SettlementStepKind, Mapping[str, Any], tuple[str, ...]],
    None,
]

TerminalCloseoutCheckpoint = Callable[[Mapping[str, Any]], None]
CompletionIntent = Callable[[Mapping[str, Any]], Mapping[str, Any]]
SourceJournalPersist = Callable[[Mapping[str, Any]], None]


class TurnSettlementEffectAdmission(Protocol):
    def prepare(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
        persist_journal: SourceJournalPersist,
    ) -> None: ...

    def hold(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
        persist_journal: SourceJournalPersist,
    ) -> None: ...

    def release(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
        persist_journal: SourceJournalPersist,
    ) -> None: ...

    def allows_absent_reexecute(
        self,
        step_kind: TurnProviderStepKind,
        effect_ref: str,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class TurnSettlementState:
    completed_phases: tuple[str, ...]
    writeback: Mapping[str, Any] | None = None
    quota_spend: Mapping[str, Any] | None = None


TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION = "loopx_turn_settlement_transaction_v1"
TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION = "loopx_turn_settlement_reduction_v1"


def _invoke_turn_effect(effect: TurnEffect, effect_ref: str) -> Mapping[str, Any]:
    """Invoke a provider with a stable ref while retaining zero-arg callbacks."""

    try:
        signature = inspect.signature(effect)
    except (TypeError, ValueError):
        return effect(effect_ref)
    try:
        signature.bind(effect_ref=effect_ref)
    except TypeError:
        pass
    else:
        return effect(effect_ref=effect_ref)
    try:
        signature.bind()
    except TypeError:
        signature.bind(effect_ref)
        return effect(effect_ref)
    return effect()


def invoke_result_effect(
    effect: ResultEffect,
    result: Mapping[str, Any],
    effect_ref: str,
) -> Mapping[str, Any]:
    """Invoke a result provider with optional stable-ref compatibility."""

    try:
        signature = inspect.signature(effect)
    except (TypeError, ValueError):
        return effect(result, effect_ref)
    try:
        signature.bind(result, effect_ref=effect_ref)
    except TypeError:
        pass
    else:
        return effect(result, effect_ref=effect_ref)
    try:
        signature.bind(result)
    except TypeError:
        signature.bind(result, effect_ref)
        return effect(result, effect_ref)
    return effect(result)


def turn_effect_resolvers(
    *,
    writeback: TurnEffectResolver | None,
    spend: TurnEffectResolver | None,
    terminal_closeout: TurnEffectResolver | None,
) -> dict[SettlementStepKind, TurnEffectResolver]:
    """Collect configured provider readbacks under their typed steps."""

    return {
        step: resolver
        for step, resolver in (
            (SettlementStepKind.DURABLE_WRITEBACK, writeback),
            (SettlementStepKind.QUOTA_SPEND, spend),
            (SettlementStepKind.TERMINAL_CLOSEOUT, terminal_closeout),
        )
        if resolver is not None
    }


@dataclass(frozen=True, slots=True)
class TurnSettlementJournalAdapter:
    """Mechanically persist prepared effects and committed provider payloads."""

    journal: dict[str, Any]
    effects: dict[str, bool]
    persist: Callable[[], None]
    compact_payload: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    source_effects: TurnSettlementEffectAdmission | None = None
    persist_source: SourceJournalPersist | None = None
    deferred_release_step: SettlementStepKind | None = None

    @property
    def effect_attempts(self) -> Mapping[str, Mapping[str, Any]]:
        attempts = self.journal.get("effect_attempts")
        return attempts if isinstance(attempts, Mapping) else {}

    def prepare(self, step_kind: SettlementStepKind, effect_ref: str) -> None:
        attempts = self.journal.setdefault("effect_attempts", {})
        if not isinstance(attempts, dict):
            raise RuntimeError("Turn journal effect_attempts shape mismatch")
        attempts[step_kind.value] = {
            "status": "prepared",
            "effect_ref": effect_ref,
        }
        if self.source_effects is None:
            self.persist()
            return
        self.source_effects.prepare(
            require_turn_provider_step_kind(step_kind),
            effect_ref,
            self._require_source_persist(),
        )

    def abort(self, step_kind: SettlementStepKind, effect_ref: str) -> None:
        self._forget(step_kind, effect_ref)
        if self.source_effects is None:
            self.persist()
            return
        self.source_effects.release(
            require_turn_provider_step_kind(step_kind),
            effect_ref,
            self._require_source_persist(),
        )

    def checkpoint(
        self,
        step_kind: SettlementStepKind,
        payload: Mapping[str, Any],
        phases: tuple[str, ...],
    ) -> None:
        effect_ref = self._effect_ref(step_kind, payload)
        if step_kind is SettlementStepKind.DURABLE_WRITEBACK:
            self.effects["state_written"] = True
            self.journal["writeback"] = self._completion_payload(payload)
        elif step_kind is SettlementStepKind.QUOTA_SPEND:
            self.effects["quota_spent"] = True
            self.journal["quota_spend"] = dict(self.compact_payload(payload))
        self.journal["completed_phases"] = list(phases)
        self._forget(step_kind, effect_ref)
        if self.source_effects is None:
            self.persist()
            return
        self._checkpoint_source_effect(step_kind, effect_ref)

    def checkpoint_terminal(self, payload: Mapping[str, Any]) -> None:
        step_kind = SettlementStepKind.TERMINAL_CLOSEOUT
        effect_ref = self._effect_ref(step_kind, payload)
        compact = self._completion_payload(payload)
        self.journal["terminal_closeout"] = compact
        writeback = self.journal.get("writeback")
        self.journal["writeback"] = {
            **(dict(writeback) if isinstance(writeback, Mapping) else {}),
            "completion": compact.get("completion"),
        }
        self._forget(step_kind, effect_ref)
        if self.source_effects is None:
            self.persist()
            return
        self._checkpoint_source_effect(step_kind, effect_ref)

    def allows_absent_reexecute(
        self,
        step_kind: SettlementStepKind,
        effect_ref: str,
    ) -> bool:
        if self.source_effects is None:
            return True
        return self.source_effects.allows_absent_reexecute(
            require_turn_provider_step_kind(step_kind),
            effect_ref,
        )

    def hold_tail(
        self,
        step_kind: SettlementStepKind,
        effect_ref: str,
    ) -> None:
        if self.source_effects is None:
            raise RuntimeError("source Turn effect admission is unavailable")
        self._set_tail_hold(step_kind, effect_ref)
        self.source_effects.hold(
            require_turn_provider_step_kind(step_kind),
            effect_ref,
            self._require_source_persist(),
        )

    def release_tail(
        self,
        step_kind: SettlementStepKind,
        effect_ref: str,
    ) -> None:
        expected = self._tail_hold(step_kind, effect_ref)
        if self.journal.get("source_effect_hold") != expected:
            raise RuntimeError("source Turn effect hold identity changed")
        self.journal.pop("source_effect_hold")
        if self.source_effects is None:
            raise RuntimeError("source Turn effect admission is unavailable")
        self.source_effects.release(
            require_turn_provider_step_kind(step_kind),
            effect_ref,
            self._require_source_persist(),
        )

    def _checkpoint_source_effect(
        self,
        step_kind: SettlementStepKind,
        effect_ref: str,
    ) -> None:
        if self.source_effects is None:
            raise RuntimeError("source Turn effect admission is unavailable")
        persist = self._require_source_persist()
        if step_kind is self.deferred_release_step:
            self._set_tail_hold(step_kind, effect_ref)
            self.source_effects.hold(
                require_turn_provider_step_kind(step_kind),
                effect_ref,
                persist,
            )
            return
        self.source_effects.release(
            require_turn_provider_step_kind(step_kind),
            effect_ref,
            persist,
        )

    @staticmethod
    def _tail_hold(
        step_kind: SettlementStepKind,
        effect_ref: str,
    ) -> dict[str, str]:
        return {
            "schema_version": SOURCE_TURN_EFFECT_HOLD_SCHEMA_VERSION,
            "status": "held",
            "step_kind": step_kind.value,
            "effect_ref": effect_ref,
        }

    def _set_tail_hold(
        self,
        step_kind: SettlementStepKind,
        effect_ref: str,
    ) -> None:
        expected = self._tail_hold(step_kind, effect_ref)
        existing = self.journal.get("source_effect_hold")
        if existing is not None and existing != expected:
            raise RuntimeError("source Turn effect hold identity changed")
        self.journal["source_effect_hold"] = expected

    def _completion_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        return {
            **dict(self.compact_payload(payload)),
            **(
                {"completion": dict(payload["completion"])}
                if isinstance(payload.get("completion"), Mapping)
                else {}
            ),
        }

    def _effect_ref(
        self,
        step_kind: SettlementStepKind,
        payload: Mapping[str, Any],
    ) -> str:
        payload_ref = str(payload.get("effect_ref") or "")
        attempt = self.effect_attempts.get(step_kind.value)
        attempt_ref = (
            str(attempt.get("effect_ref") or "") if isinstance(attempt, Mapping) else ""
        )
        if attempt_ref:
            if payload_ref and payload_ref != attempt_ref:
                raise RuntimeError("Turn provider effect ref changed")
            return attempt_ref
        if payload_ref:
            return payload_ref
        raise RuntimeError("Turn journal prepared effect ref is missing")

    def _require_source_persist(self) -> SourceJournalPersist:
        if self.persist_source is None:
            raise RuntimeError("source Turn effect persistence is unavailable")
        return self.persist_source

    def _forget(
        self,
        step_kind: SettlementStepKind,
        effect_ref: str,
        *,
        strict: bool = True,
    ) -> None:
        attempts = self.journal.get("effect_attempts")
        if not isinstance(attempts, dict):
            return
        attempt = attempts.get(step_kind.value)
        if (
            isinstance(attempt, Mapping)
            and effect_ref
            and (attempt.get("effect_ref") != effect_ref)
        ):
            if strict:
                raise RuntimeError("Turn journal prepared effect ref changed")
            return
        attempts.pop(step_kind.value, None)
        if not attempts:
            self.journal.pop("effect_attempts", None)


def terminal_closeout_requirement(
    *,
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    journal: Mapping[str, Any],
    completion_intent: CompletionIntent,
) -> tuple[bool, str | None]:
    """Classify final no-followup without mutating the Todo frontier."""

    if result.get("result_kind") != LoopXTurnResultKind.VALIDATED_COMPLETION.value:
        return False, None
    stored_terminal = journal.get("terminal_closeout")
    stored_writeback = journal.get("writeback")
    terminal_payload = stored_terminal if isinstance(stored_terminal, Mapping) else {}
    writeback_payload = (
        stored_writeback if isinstance(stored_writeback, Mapping) else {}
    )
    observed_completion = terminal_payload.get("completion") or writeback_payload.get(
        "completion"
    )
    try:
        if not isinstance(observed_completion, Mapping):
            observed_completion = completion_intent(result)
        envelope = plan.get("turn_envelope")
        selected = selected_turn_todo(envelope) if isinstance(envelope, Mapping) else {}
        outcome = require_loopx_turn_completion_outcome(
            observed_completion,
            expected_todo_id=str(selected.get("todo_id") or ""),
        )
    except (TypeError, ValueError) as exc:
        return False, str(exc)
    return outcome["continuation"] == "no_followup", None


def _read_prepared_effect(
    resolvers: Mapping[SettlementStepKind, TurnEffectResolver],
    step_kind: SettlementStepKind,
    effect_ref: str,
) -> Mapping[str, Any]:
    """Transport provider evidence; TypeScript decides whether it permits IO."""

    resolver = resolvers.get(step_kind)
    if resolver is None:
        return {"kind": "unknown", "reason": "provider readback is unavailable"}
    try:
        return dict(resolver(effect_ref))
    except Exception as exc:
        return {
            "kind": "unknown",
            "reason": f"provider readback raised {type(exc).__name__}",
        }


def execute_turn_driver_settlement(
    transaction_plan: Mapping[str, Any],
    *,
    transaction_phases: tuple[str, ...],
    completed_phases: Sequence[str],
    writeback_payload: Mapping[str, Any] | None,
    quota_spend_payload: Mapping[str, Any] | None,
    writeback: TurnEffect,
    spend: TurnEffect,
    checkpoint: TurnSettlementCheckpoint,
    committed_effect_id: str | None = None,
    terminal_closeout_required: bool = False,
    terminal_closeout_payload: Mapping[str, Any] | None = None,
    terminal_closeout: TurnEffect | None = None,
    terminal_checkpoint: TerminalCloseoutCheckpoint | None = None,
    prepare: TurnSettlementPrepare | None = None,
    resume_prepare: TurnSettlementPrepare | None = None,
    abort: TurnSettlementAbort | None = None,
    allow_absent_reexecute: Callable[[SettlementStepKind, str], bool] | None = None,
    effect_attempts: Mapping[str, Mapping[str, Any]] | None = None,
    effect_resolvers: Mapping[SettlementStepKind, TurnEffectResolver] | None = None,
    turn_result_kind: str | None = None,
) -> SettlementResult[TurnSettlementState]:
    """Run external effect providers and reduce one complete Turn settlement.

    TypeScript first validates identity, replay, and the committed journal
    prefix, then authorizes the still-Python providers in order. Python transports
    provider returns and readbacks to TypeScript before executing its checkpoint
    or retry decision. Only a persisted checkpoint advances the next provider. A replay
    with no pending provider completes in the first reduction.
    """
    phases = tuple(str(phase) for phase in completed_phases)
    writeback_value = writeback_payload
    spend_value = quota_spend_payload
    terminal_value = terminal_closeout_payload
    failed_attempt: tuple[SettlementStepKind, Mapping[str, Any]] | None = None
    returned_attempt: Mapping[str, Any] | None = None
    attempts = {
        (step.value if isinstance(step, SettlementStepKind) else str(step)): dict(
            attempt
        )
        for step, attempt in (effect_attempts or {}).items()
    }
    observations: dict[str, Mapping[str, Any]] = {}
    resolvers = dict(effect_resolvers or {})

    def reduce() -> Mapping[str, Any]:
        payload = effect_runtime_result(
            "turn.settlement.reduce",
            {
                "schema_version": TURN_SETTLEMENT_TRANSACTION_SCHEMA_VERSION,
                "transaction_plan": dict(transaction_plan),
                "transaction_phases": list(transaction_phases),
                "completed_phases": list(phases),
                "committed_effect_id": committed_effect_id,
                "writeback_payload": (
                    dict(writeback_value)
                    if isinstance(writeback_value, Mapping)
                    else None
                ),
                "quota_spend_payload": (
                    dict(spend_value) if isinstance(spend_value, Mapping) else None
                ),
                "terminal_closeout_required": terminal_closeout_required,
                "terminal_closeout_payload": (
                    dict(terminal_value)
                    if isinstance(terminal_value, Mapping)
                    else None
                ),
                "failed_provider_attempt": (
                    {
                        "step_kind": failed_attempt[0].value,
                        "payload": dict(failed_attempt[1]),
                    }
                    if failed_attempt is not None
                    else None
                ),
                "returned_provider_attempt": returned_attempt,
                "effect_attempts": attempts,
                "provider_observations": observations,
                "turn_result_kind": turn_result_kind,
            },
        )
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema_version") != TURN_SETTLEMENT_REDUCTION_SCHEMA_VERSION
        ):
            raise RuntimeError("TypeScript Turn settlement reduction shape mismatch")
        return payload

    reduction = reduce()
    decision = str(reduction.get("decision") or "")
    while decision == "execute":
        provider_effects = reduction.get("provider_effects")
        if not isinstance(provider_effects, list) or not provider_effects:
            raise RuntimeError("TypeScript Turn settlement provider plan is empty")
        providers = {
            SettlementStepKind.DURABLE_WRITEBACK: writeback,
            SettlementStepKind.QUOTA_SPEND: spend,
        }
        for raw_effect in provider_effects:
            if not isinstance(raw_effect, Mapping):
                raise RuntimeError("TypeScript Turn settlement provider shape mismatch")
            step_kind = SettlementStepKind(str(raw_effect.get("step_kind") or ""))
            action = str(raw_effect.get("action") or "")
            effect_ref = str(raw_effect.get("effect_ref") or "")
            if not effect_ref:
                raise RuntimeError("TypeScript Turn settlement effect ref is empty")
            if action == "resolve_prepared":
                if resume_prepare is not None:
                    resume_prepare(step_kind, effect_ref)
                observations[step_kind.value] = _read_prepared_effect(
                    resolvers, step_kind, effect_ref
                )
                continue
            if action in {"prepare_and_execute", "execute_prepared"}:
                if action == "prepare_and_execute":
                    if prepare is not None:
                        prepare(step_kind, effect_ref)
                    attempts[step_kind.value] = {
                        "status": "prepared",
                        "effect_ref": effect_ref,
                    }
                elif (
                    allow_absent_reexecute is not None
                    and not allow_absent_reexecute(step_kind, effect_ref)
                ):
                    returned_attempt = {
                        "step_kind": step_kind.value,
                        "payload": {
                            "ok": False,
                            "appended": False,
                            "reason": "Goal retirement closed effect re-execution",
                        },
                    }
                    observations.pop(step_kind.value, None)
                    continue
                provider = (
                    terminal_closeout
                    if step_kind is SettlementStepKind.TERMINAL_CLOSEOUT
                    else providers[step_kind]
                )
                if provider is None:
                    raise ValueError("terminal closeout requires an effect provider")
                returned_attempt = {
                    "step_kind": step_kind.value,
                    "payload": dict(_invoke_turn_effect(provider, effect_ref)),
                }
                observations.pop(step_kind.value, None)
                continue
            if action not in {"checkpoint", "abort_prepared"}:
                raise RuntimeError("TypeScript Turn settlement action mismatch")
            observed = raw_effect.get("payload")
            if not isinstance(observed, Mapping):
                raise RuntimeError("TypeScript Turn settlement payload shape mismatch")
            if action == "abort_prepared":
                if abort is not None:
                    abort(step_kind, effect_ref)
                failed_attempt = (step_kind, observed)
            elif step_kind is SettlementStepKind.TERMINAL_CLOSEOUT:
                if terminal_checkpoint is None:
                    raise ValueError("terminal closeout requires a checkpoint")
                terminal_checkpoint(observed)
                terminal_value = observed
            else:
                raw_phases = raw_effect.get("completed_phases")
                if not isinstance(raw_phases, list):
                    raise RuntimeError(
                        "TypeScript Turn settlement phases shape mismatch"
                    )
                authorized_phases = tuple(str(phase) for phase in raw_phases)
                checkpoint(step_kind, observed, authorized_phases)
                phases = authorized_phases
                if step_kind is SettlementStepKind.DURABLE_WRITEBACK:
                    writeback_value = observed
                else:
                    spend_value = observed
            attempts.pop(step_kind.value, None)
            observations.pop(step_kind.value, None)
            returned_attempt = None
        reduction = reduce()
        decision = str(reduction.get("decision") or "")

    if decision not in {"complete", "failed"}:
        raise RuntimeError("TypeScript Turn settlement did not reach an outcome")

    def decode_state(value: Any) -> TurnSettlementState:
        if not isinstance(value, Mapping):
            raise RuntimeError("TypeScript Turn settlement state shape mismatch")
        completed = value.get("completed_phases")
        if not isinstance(completed, list):
            raise RuntimeError("TypeScript Turn settlement phases shape mismatch")
        raw_writeback = value.get("writeback")
        raw_spend = value.get("quota_spend")
        return TurnSettlementState(
            completed_phases=tuple(str(phase) for phase in completed),
            writeback=(
                dict(raw_writeback) if isinstance(raw_writeback, Mapping) else None
            ),
            quota_spend=(dict(raw_spend) if isinstance(raw_spend, Mapping) else None),
        )

    projection = reduction.get("settlement_result")
    return decode_settlement_result(
        reduction.get("result"),
        value_decoder=decode_state,
        projection_payload=(projection if isinstance(projection, Mapping) else None),
    )


def turn_settlement_outcome(
    result: SettlementResult[TurnSettlementState],
) -> Mapping[str, Any] | None:
    """Read the optional canonical Turn projection from the TS reduction."""

    projection = settlement_result_payload(result)
    outcome = projection.get("turn_outcome")
    if not isinstance(outcome, Mapping):
        return None
    phases = outcome.get("completed_phases")
    failed_phase = outcome.get("failed_phase")
    if (
        outcome.get("schema_version") != "loopx_turn_settlement_outcome_v0"
        or not isinstance(outcome.get("result_kind"), str)
        or not isinstance(phases, list)
        or (failed_phase is not None and not isinstance(failed_phase, str))
    ):
        raise RuntimeError("TypeScript Turn settlement outcome shape mismatch")
    return dict(outcome)


def turn_settlement_failure_outcome(
    result: SettlementResult[TurnSettlementState],
) -> tuple[LoopXTurnResultKind, tuple[str, ...], str]:
    """Decode a required TypeScript failure outcome for legacy projection."""

    outcome = turn_settlement_outcome(result)
    if outcome is None:
        raise RuntimeError(
            "TypeScript Turn settlement omitted its canonical failure outcome"
        )
    failed_phase = str(outcome.get("failed_phase") or "")
    if not failed_phase:
        raise RuntimeError("TypeScript Turn settlement failure omitted failed_phase")
    try:
        result_kind = LoopXTurnResultKind(str(outcome["result_kind"]))
    except ValueError as exc:
        raise RuntimeError(
            "TypeScript Turn settlement failure has unsupported result_kind"
        ) from exc
    return (
        result_kind,
        tuple(str(phase) for phase in outcome["completed_phases"]),
        failed_phase,
    )
