"""Settlement lookup, binding validation, and run-index helpers for slot previews."""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TypeAlias, TypeVar, cast

from ..effect_program import (
    SettlementFailure,
    SettlementFailureKind,
    SettlementIdentity,
    SettlementResult,
    SettlementStepKind,
)
from ..todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_replan_obligation_id,
)
from ..work_items.delivery_outcome import (
    normalize_delivery_outcome,
    qualifies_turn_scoped_settlement,
)
from .ledger_readback import (
    QUOTA_SLOT_SPENT_CLASSIFICATION,
    QUOTA_SLOT_VOIDED_CLASSIFICATION,
)
from .monitor_poll import QUOTA_MONITOR_POLL_CLASSIFICATION
from .scheduler_ack import QUOTA_SCHEDULER_ACK_CLASSIFICATION
from .settlement import read_heartbeat_settlement
from .settlement_validation import completion_validation_spend_error
from .spend_sources import (
    DEFAULT_SLOT_SPEND_SOURCE,
    TURN_SCOPED_SLOT_SPEND_SOURCES,
    VISIBLE_GOAL_SLOT_SPEND_SOURCE,
)

_T = TypeVar("_T")

PreviewSettlementResult: TypeAlias = (
    SettlementResult[SettlementIdentity] | SettlementResult[dict[str, object]]
)

DELIVERY_COMPLETION_SPEND_STATES = frozenset(
    {"waiting", "focus_wait", "operator_gate", "eligible"}
)
TERMINAL_NO_FOLLOWUP_DECISION_STATE = "terminal_no_followup"


@dataclass(frozen=True, slots=True)
class PreviewSettlementResolution:
    """Typed settlement readback projection carried across preview admission."""

    identity: SettlementIdentity | None = None
    result: PreviewSettlementResult | None = None
    delivery_run: dict[str, object] | None = None
    delivery_workspace_causality: dict[str, str] | None = None
    reason: str | None = None
    writeback_committed: bool = False
    spend_committed: bool = False


def receipt_committed(
    result: SettlementResult[_T] | None,
    step_kind: SettlementStepKind,
) -> bool:
    """Report whether one settlement step already owns a committed receipt."""
    if result is None or result.failure is not None:
        return False
    return any(
        receipt.step_kind is step_kind and receipt.status == "committed"
        for receipt in result.receipts
    )


def _dict_or_empty(value: object) -> dict[str, object]:
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def todo_binding_error(
    *,
    source: str,
    status_payload: Mapping[str, object],
    goal_id: str,
    before: Mapping[str, object],
    requested_todo_id: str | None,
    requested_replan_obligation_id: str | None,
    agent_id: str | None,
    settlement_identity: SettlementIdentity | None = None,
    delivery_run: dict[str, object] | None = None,
) -> str | None:
    """Validate that a preview spend stays bound to its Todo or replan identity."""
    selected = _dict_or_empty(before.get("selected_todo"))
    status_dict = (
        cast(dict[str, object], status_payload)
        if isinstance(status_payload, dict)
        else dict(status_payload)
    )
    if settlement_identity is not None:
        if (
            requested_todo_id != settlement_identity.todo_id
            or requested_replan_obligation_id
            != settlement_identity.replan_obligation_id
        ):
            return (
                "quota spend binding does not match the original settlement "
                f"identity: expected {settlement_identity.binding_kind}="
                f"{settlement_identity.binding_id}"
            )
        return completion_validation_spend_error(
            status_dict,
            goal_id=goal_id,
            todo_id=requested_todo_id,
            agent_id=agent_id,
            selected_todo=selected,
            delivery_run=delivery_run,
        )
    selected_todo_id = normalize_todo_id(selected.get("todo_id"))
    if requested_todo_id and selected_todo_id and requested_todo_id != selected_todo_id:
        return (
            f"quota spend todo binding mismatch: selected todo is "
            f"{selected_todo_id} but --todo-id is {requested_todo_id}; "
            "complete or update the selected todo first, or record a replan"
        )
    if (
        source == DEFAULT_SLOT_SPEND_SOURCE
        and before.get("normal_delivery_allowed") is True
        and selected_todo_id
        and not requested_todo_id
    ):
        return (
            f"quota spend requires --todo-id {selected_todo_id} for heartbeat "
            "delivery so the accounted turn is bound to the selected todo"
        )
    return completion_validation_spend_error(
        status_dict,
        goal_id=goal_id,
        todo_id=requested_todo_id,
        agent_id=agent_id,
        selected_todo=selected,
        delivery_run=None,
    )


def resolve_preview_settlement(
    *,
    raw_runtime_root: object,
    source: str,
    goal_id: str,
    agent_id: str | None,
    todo_id: str | None,
    replan_obligation_id: str | None,
    turn_instance_id: str | None,
    registry_path: Path | None,
    goal_ref: Mapping[str, object] | None,
) -> PreviewSettlementResolution:
    """Resolve persisted Turn settlement state for turn-scoped preview spends."""
    if turn_instance_id and source not in TURN_SCOPED_SLOT_SPEND_SOURCES:
        failed_result: SettlementResult[SettlementIdentity] = SettlementResult.failed(
            kind=SettlementFailureKind.INVALID_IDENTITY,
            step_kind=SettlementStepKind.VALIDATION,
            reason=(
                "turn-scoped settlement is valid only for heartbeat or "
                "visible-goal spend"
            ),
        )
        return PreviewSettlementResolution(
            result=failed_result,
            reason=cast(SettlementFailure, failed_result.failure).reason,
        )
    if turn_instance_id and not raw_runtime_root:
        return PreviewSettlementResolution(
            reason="status payload does not include runtime_root"
        )
    if source not in TURN_SCOPED_SLOT_SPEND_SOURCES or not raw_runtime_root:
        return PreviewSettlementResolution()

    runtime_root = Path(str(raw_runtime_root)).expanduser()
    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=replan_obligation_id,
        infer_turn_instance_id=not bool(turn_instance_id),
        allow_unbound_binding=(
            source == VISIBLE_GOAL_SLOT_SPEND_SOURCE
            and not todo_id
            and not replan_obligation_id
        ),
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if readback is None:
        return PreviewSettlementResolution()
    if (
        isinstance(readback.progress, dict)
        and readback.progress.get("closeout_kind")
        in {"typed_blocked_writeback_no_spend", "capability_duty_retired_no_spend"}
    ):
        return PreviewSettlementResolution(
            identity=readback.identity.value,
            result=readback.settlement,
            delivery_run=readback.writeback_run,
            reason=(
                "this exact capability duty was retired and must not consume a quota slot; "
                "reassess the current frontier in a new Turn"
                if readback.progress.get("closeout_kind")
                == "capability_duty_retired_no_spend"
                else "this Turn already closed with an exact typed blocked writeback "
                "and must not consume a quota slot; retry the Todo only after "
                "its external blocker changes or a bounded backoff"
            ),
        )
    identity_result = readback.identity
    identity = identity_result.value if identity_result.failure is None else None
    result: PreviewSettlementResult = (
        readback.delivery if identity is not None else identity_result
    )
    return PreviewSettlementResolution(
        identity=identity,
        result=result,
        delivery_run=result.value if result.failure is None and isinstance(result.value, dict) else None,
        delivery_workspace_causality=readback.workspace_causality,
        reason=result.failure.reason if result.failure is not None else None,
        writeback_committed=receipt_committed(
            readback.writeback, SettlementStepKind.DURABLE_WRITEBACK
        ),
        spend_committed=receipt_committed(
            readback.spend, SettlementStepKind.QUOTA_SPEND
        ),
    )


def unbound_visible_goal_settlement_failure(
    *,
    source: str,
    before: Mapping[str, object],
    requested_todo_id: str | None,
    requested_replan_obligation_id: str | None,
    turn_instance_id: str | None,
) -> SettlementResult[SettlementIdentity] | None:
    """Reject unbound visible-goal previews when a selected Todo requires binding."""
    if (
        source != VISIBLE_GOAL_SLOT_SPEND_SOURCE
        or before.get("normal_delivery_allowed") is not True
    ):
        return None
    selected = _dict_or_empty(before.get("selected_todo"))
    selected_todo_id = normalize_todo_id(selected.get("todo_id"))
    if not selected_todo_id:
        return None
    if bool(requested_todo_id) == bool(requested_replan_obligation_id):
        reason = (
            "visible Goal settlement requires exactly one todo_id or "
            "replan_obligation_id binding"
        )
    elif not turn_instance_id:
        reason = (
            "visible Goal settlement for selected Todo "
            f"{selected_todo_id} requires turn_instance_id from quota should-run; "
            "rerun the guard with --begin-turn"
        )
    else:
        return None
    return SettlementResult.failed(
        kind=SettlementFailureKind.IDENTITY_MISMATCH,
        step_kind=SettlementStepKind.VALIDATION,
        reason=reason,
        details={
            "source": source,
            "selected_todo_id": selected_todo_id,
            "requested_todo_id": requested_todo_id,
            "requested_replan_obligation_id": requested_replan_obligation_id,
            "turn_instance_id": turn_instance_id,
        },
    )


def iter_reversed_goal_run_index_records(
    runtime_root: Path,
    goal_id: str,
) -> Iterator[dict[str, object]]:
    """Yield parsed dictionary records from ``runs/index.jsonl`` newest first."""
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    if not index_path.exists():
        return
    try:
        lines = index_path.read_text(encoding="utf-8").split("\n")
    except OSError:
        return
    for line in reversed(lines):
        if not line.strip():
            continue
        try:
            record: object = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            yield cast(dict[str, object], record)


def load_goal_run_index_records(
    runtime_root: Path,
    goal_id: str,
) -> list[dict[str, object]]:
    """Load parsed dictionary records from ``runs/index.jsonl`` in chronological order."""
    return list(reversed(list(iter_reversed_goal_run_index_records(runtime_root, goal_id))))


def is_quota_neutral_state_refresh(run: Mapping[str, object]) -> bool:
    """Recognize refresh-state provenance without relying on its classification."""
    if str(run.get("delivery_outcome") or "").strip():
        return False
    if str(run.get("classification") or "").strip() == "state_refreshed":
        return True
    return (
        isinstance(run.get("state"), dict)
        and isinstance(run.get("runtime_projection_route"), dict)
        and bool(str(run.get("recommended_action_source") or "").strip())
    )


def select_latest_unspent_turn_settlement_run(
    runtime_root: Path,
    goal_id: str,
    *,
    agent_id: str | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, object] | None:
    """Return the latest same-agent Turn settlement that still needs accounting."""
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    expected_goal_ref = dict(goal_ref) if goal_ref is not None else None
    for run in iter_reversed_goal_run_index_records(runtime_root, goal_id):
        if expected_goal_ref is None and "goal_ref" in run:
            continue
        if expected_goal_ref is not None and run.get("goal_ref") != expected_goal_ref:
            continue
        run_agent_id = normalize_todo_claimed_by(run.get("agent_id"))
        if safe_agent_id and run_agent_id and safe_agent_id != run_agent_id:
            continue
        classification = str(run.get("classification") or "").strip()
        if classification == QUOTA_SLOT_VOIDED_CLASSIFICATION:
            continue
        if classification == QUOTA_SLOT_SPENT_CLASSIFICATION:
            return None
        if (
            classification == QUOTA_MONITOR_POLL_CLASSIFICATION
            and run.get("material_change") is not True
        ):
            continue
        if classification == QUOTA_SCHEDULER_ACK_CLASSIFICATION:
            continue
        if is_quota_neutral_state_refresh(run):
            continue
        delivery_outcome = normalize_delivery_outcome(run.get("delivery_outcome"))
        raw_progress = run.get("progress_observation")
        progress_observation = (
            cast(dict[str, object], raw_progress)
            if isinstance(raw_progress, dict)
            else None
        )
        if qualifies_turn_scoped_settlement(
            delivery_outcome,
            progress_observation,
            work_item_id=normalize_todo_id(run.get("todo_id")),
            replan_obligation_id=normalize_todo_replan_obligation_id(
                run.get("replan_obligation_id")
            ),
        ):
            return run
        return None
    return None


def admits_delivery_completion_spend_state(
    *,
    before: Mapping[str, object],
    identity: SettlementIdentity | None,
    settlement: PreviewSettlementResolution,
) -> bool:
    """Admit the decision states one delivery-completion spend may settle from."""
    from ..effect_program import SettlementBindingKind

    state = str(before.get("state") or "")
    if state in DELIVERY_COMPLETION_SPEND_STATES:
        return True
    if state != TERMINAL_NO_FOLLOWUP_DECISION_STATE:
        return False
    return (
        identity is not None
        and identity.binding_kind is SettlementBindingKind.AUTONOMOUS_REPLAN
        and settlement.writeback_committed is True
        and settlement.spend_committed is not True
    )
