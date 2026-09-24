from __future__ import annotations
from .effective_action import EffectiveAction

import json
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

from ..agents.workspace_guard import (
    build_delivery_workspace_guard,
    delivery_workspace_identity,
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
from .monitor_poll import QUOTA_MONITOR_POLL_CLASSIFICATION
from .scheduler_ack import QUOTA_SCHEDULER_ACK_CLASSIFICATION
from ..effect_program import SettlementBindingKind
from .settlement import (
    SettlementFailureKind,
    SettlementIdentity,
    SettlementResult,
    SettlementStepKind,
    read_heartbeat_settlement,
    settlement_result_payload,
)
from .settlement_workspace_causality import (
    LEGACY_SETTLEMENT_RECEIPT_EVIDENCE_SCHEMA_VERSION,
    completed_todo_workspace_causality,
    missing_delivery_workspace_resolution,
    resolve_settlement_workspace_requirement,
)
from .settlement_validation import completion_validation_spend_error
from .spend_sources import (
    DEFAULT_SLOT_SPEND_SOURCE,
    TURN_SCOPED_SLOT_SPEND_SOURCES,
    VISIBLE_GOAL_SLOT_SPEND_SOURCE,
)
from .spend_commit import (
    build_quota_slot_spend_event as build_quota_slot_spend_event,
    record_quota_slot_spend_from_preview as record_quota_slot_spend_from_preview,
)
from .void_commit import (
    build_quota_slot_void_event as build_quota_slot_void_event,
    build_quota_slot_void_preview_for_decision as build_quota_slot_void_preview_for_decision,
    record_quota_slot_void_from_preview as record_quota_slot_void_from_preview,
)

QUOTA_SLOT_SPENT_CLASSIFICATION = "quota_slot_spent"
QUOTA_SLOT_VOIDED_CLASSIFICATION = "quota_slot_voided"

QuotaDecisionBuilder = Callable[[dict[str, Any]], dict[str, Any]]
QuotaStatusBuilder = Callable[..., dict[str, Any]]


def _todo_binding_error(
    *,
    source: str,
    status_payload: dict[str, Any],
    goal_id: str,
    before: dict[str, Any],
    requested_todo_id: str | None,
    requested_replan_obligation_id: str | None,
    agent_id: str | None,
    settlement_identity: SettlementIdentity | None = None,
    delivery_run: dict[str, Any] | None = None,
) -> str | None:
    selected = (
        before.get("selected_todo")
        if isinstance(before.get("selected_todo"), dict)
        else {}
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
            status_payload,
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
        status_payload,
        goal_id=goal_id,
        todo_id=requested_todo_id,
        agent_id=agent_id,
        selected_todo=selected,
        delivery_run=None,
    )


def _receipt_committed(
    result: SettlementResult[Any] | None,
    step_kind: SettlementStepKind,
) -> bool:
    """Report whether one settlement step already owns a committed receipt."""

    if result is None or result.failure is not None:
        return False
    return any(
        receipt.step_kind is step_kind and receipt.status == "committed"
        for receipt in result.receipts
    )


def _resolve_preview_settlement(
    *,
    raw_runtime_root: Any,
    source: str,
    goal_id: str,
    agent_id: str | None,
    todo_id: str | None,
    replan_obligation_id: str | None,
    turn_instance_id: str | None,
) -> dict[str, Any]:
    if turn_instance_id and source not in TURN_SCOPED_SLOT_SPEND_SOURCES:
        result = SettlementResult.failed(
            kind=SettlementFailureKind.INVALID_IDENTITY,
            step_kind=SettlementStepKind.VALIDATION,
            reason=(
                "turn-scoped settlement is valid only for heartbeat or "
                "visible-goal spend"
            ),
        )
        return {"reason": result.failure.reason, "result": result}
    if turn_instance_id and not raw_runtime_root:
        return {"reason": "status payload does not include runtime_root"}
    if source not in TURN_SCOPED_SLOT_SPEND_SOURCES or not raw_runtime_root:
        return {}

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
    )
    if readback is None:
        return {}
    if (
        isinstance(readback.progress, dict)
        and readback.progress.get("closeout_kind")
        == "typed_blocked_writeback_no_spend"
    ):
        return {
            "identity": readback.identity.value,
            "result": readback.settlement,
            "delivery_run": readback.writeback_run,
            "reason": (
                "this Turn already closed with an exact typed blocked writeback "
                "and must not consume a quota slot; retry the Todo only after "
                "its external blocker changes or a bounded backoff"
            ),
        }
    result = readback.identity
    identity = result.value if result.failure is None else None
    if identity is not None:
        result = readback.delivery
    return {
        "identity": identity,
        "result": result,
        "delivery_run": result.value if result.failure is None else None,
        "delivery_workspace_causality": readback.workspace_causality,
        "reason": result.failure.reason if result.failure is not None else None,
        "writeback_committed": _receipt_committed(
            readback.writeback, SettlementStepKind.DURABLE_WRITEBACK
        ),
        "spend_committed": _receipt_committed(
            readback.spend, SettlementStepKind.QUOTA_SPEND
        ),
    }


def _unbound_visible_goal_settlement_failure(
    *,
    source: str,
    before: dict[str, Any],
    requested_todo_id: str | None,
    requested_replan_obligation_id: str | None,
    turn_instance_id: str | None,
) -> SettlementResult[SettlementIdentity] | None:
    if (
        source != VISIBLE_GOAL_SLOT_SPEND_SOURCE
        or before.get("normal_delivery_allowed") is not True
    ):
        return None
    selected = (
        before.get("selected_todo")
        if isinstance(before.get("selected_todo"), dict)
        else {}
    )
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


def _repair_settlement_workspace_causality(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    settlement_identity: SettlementIdentity | None,
    causality: dict[str, Any] | None,
) -> dict[str, Any] | None:
    requirement = str((causality or {}).get("requirement") or "")
    if settlement_identity is None or requirement not in {"", "unknown"}:
        return causality
    repaired = completed_todo_workspace_causality(
        status_payload,
        goal_id=goal_id,
        todo_id=settlement_identity.todo_id,
        source=(
            "current_todo_contract_repair"
            if requirement == "unknown"
            else "completed_todo_contract_fallback"
        ),
    )
    if str((repaired or {}).get("requirement") or "") == "unknown":
        return causality
    return repaired or causality


def _validate_goal_id_path_segment(goal_id: str) -> str:
    value = goal_id.strip()
    if not value:
        raise ValueError("goal id is required")
    if value in {".", ".."} or "/" in value or "\\" in value:
        raise ValueError("goal id must be a single path segment")
    if Path(value).name != value:
        raise ValueError("goal id must not include path traversal")
    return value


def _int_number(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def _queue_item_for_goal(status_payload: dict[str, Any], *, goal_id: str) -> dict[str, Any]:
    queue = status_payload.get("attention_queue") if isinstance(status_payload.get("attention_queue"), dict) else {}
    queue_items = queue.get("items") if isinstance(queue.get("items"), list) else []
    return next(
        (
            item
            for item in queue_items
            if isinstance(item, dict) and str(item.get("goal_id") or "") == goal_id
        ),
        {},
    )


def _set_quota_for_goal(status_payload: dict[str, Any], *, goal_id: str, quota: dict[str, Any]) -> None:
    run_history = status_payload.get("run_history") if isinstance(status_payload.get("run_history"), dict) else {}
    run_goals = run_history.get("goals") if isinstance(run_history.get("goals"), list) else []
    for goal in run_goals:
        if isinstance(goal, dict) and str(goal.get("id") or "") == goal_id:
            goal["quota"] = dict(quota)

    queue = status_payload.get("attention_queue") if isinstance(status_payload.get("attention_queue"), dict) else {}
    queue_items = queue.get("items") if isinstance(queue.get("items"), list) else []
    for item in queue_items:
        if isinstance(item, dict) and str(item.get("goal_id") or "") == goal_id:
            item["quota"] = dict(quota)
            project_asset = item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}
            if project_asset:
                project_asset["quota"] = dict(quota)


def _load_goal_run_index_records(runtime_root: Path, goal_id: str) -> list[dict[str, Any]]:
    index_path = runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
    if not index_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        lines = index_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _is_quota_neutral_state_refresh(run: dict[str, Any]) -> bool:
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


def _latest_unspent_turn_settlement_run(
    runtime_root: Path,
    goal_id: str,
    *,
    agent_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the latest same-agent Turn settlement that still needs accounting.

    Unchanged monitor polls, scheduler acknowledgements, and plain state
    refreshes are quota-neutral. They may occur after validation and before
    accounting, so they must not hide the accountable run. A state refresh
    with a non-settling delivery outcome and other non-delivery events remain
    fail closed. A typed blocked ``outcome_gap`` settles the Turn without being
    reclassified as delivery progress.
    """

    safe_agent_id = normalize_todo_claimed_by(agent_id)
    for run in reversed(_load_goal_run_index_records(runtime_root, goal_id)):
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
        if _is_quota_neutral_state_refresh(run):
            continue
        delivery_outcome = normalize_delivery_outcome(run.get("delivery_outcome"))
        if qualifies_turn_scoped_settlement(
            delivery_outcome,
            run.get("progress_observation")
            if isinstance(run.get("progress_observation"), dict)
            else None,
            work_item_id=normalize_todo_id(run.get("todo_id")),
            replan_obligation_id=normalize_todo_replan_obligation_id(
                run.get("replan_obligation_id")
            ),
        ):
            return run
        return None
    return None


def _settlement_workspace_requirement(
    delivery_workspace_causality: dict[str, Any] | None,
    settlement_identity: SettlementIdentity | None,
    settlement_result: SettlementResult[dict[str, Any]] | None,
    delivery_completion_run: dict[str, Any] | None,
) -> dict[str, str] | None:
    if settlement_identity is None:
        return None
    return resolve_settlement_workspace_requirement(
        delivery_workspace_causality,
        settlement_binding_kind=settlement_identity.binding_kind.value,
        legacy_settlement_evidence=(
            {
                "schema_version": (
                    LEGACY_SETTLEMENT_RECEIPT_EVIDENCE_SCHEMA_VERSION
                ),
                "settlement_effect_id": settlement_identity.effect_id,
                "delivery_workspace_present": bool(
                    isinstance(delivery_completion_run, dict)
                    and delivery_completion_run.get("delivery_workspace") is not None
                ),
                "receipts": [
                    {
                        "step_kind": receipt.step_kind.value,
                        "status": receipt.status,
                        "effect_id": receipt.effect_id,
                    }
                    for receipt in settlement_result.receipts
                ],
            }
            if settlement_result is not None
            and settlement_result.failure is None
            else None
        ),
    )


def _workspace_requirement_value(
    settlement_workspace_requirement: dict[str, Any] | None,
    delivery_workspace_causality: dict[str, Any] | None,
) -> str:
    return str(
        (settlement_workspace_requirement or {}).get("requirement")
        or (delivery_workspace_causality or {}).get("requirement")
        or ""
    )


def _missing_delivery_workspace_preview(
    *,
    delivery_workspace_causality: dict[str, Any] | None,
    settlement_workspace_requirement: dict[str, Any] | None,
    delivery_workspace: dict[str, Any] | None,
    goal_id: str,
    slots: int,
    agent_id: str | None,
    before: dict[str, Any],
) -> dict[str, Any] | None:
    if delivery_workspace_identity(delivery_workspace):
        return None
    requirement = _workspace_requirement_value(
        settlement_workspace_requirement, delivery_workspace_causality
    )
    if not requirement or requirement == "not_required":
        return None
    resolution = missing_delivery_workspace_resolution(
        delivery_workspace_causality
    )
    if resolution is not None and resolution["decision"] == "omit_snapshot":
        return None
    reason = (
        "quota spend requires a valid delivery workspace snapshot for "
        f"settlement causality requirement {requirement}"
        if resolution is not None
        and resolution["decision"] == "require_snapshot"
        else (
            "quota spend requires an explicit Todo delivery contract; declare "
            "repository/write requirements or mark the Todo as explicit "
            "non-delivery, then retry the same settlement"
        )
    )
    return {
        "ok": False,
        "mode": "spend-slot",
        "dry_run": True,
        "goal_id": goal_id,
        "slots": slots,
        "agent_id": agent_id,
        "appended": False,
        "registry_mutated": False,
        "reason": reason,
        "delivery_workspace": delivery_workspace,
        "delivery_workspace_causality": delivery_workspace_causality,
        "settlement_workspace_requirement": settlement_workspace_requirement,
        "delivery_workspace_resolution": resolution,
        "delivery_workspace_validated": False,
        "before": before,
        "after": None,
    }


DELIVERY_COMPLETION_SPEND_STATES = frozenset(
    {"waiting", "focus_wait", "operator_gate", "eligible"}
)
TERMINAL_NO_FOLLOWUP_DECISION_STATE = "terminal_no_followup"


def _admits_delivery_completion_spend_state(
    *,
    before: Mapping[str, Any],
    identity: SettlementIdentity | None,
    settlement: Mapping[str, Any],
) -> bool:
    """Admit the decision states one delivery-completion spend may settle from.

    ``terminal_no_followup`` is admitted only for the autonomous-replan
    settlement whose own durable writeback derived that frontier and which has
    not yet recorded its spend. The terminal guard therefore stays strict for
    every new, unrelated, or already-accounted spend, while the remaining step
    of the settlement that produced the terminal frontier is no longer
    stranded. See the ``terminal_settlement_ordering_gap`` repair pattern.
    """

    state = str(before.get("state") or "")
    if state in DELIVERY_COMPLETION_SPEND_STATES:
        return True
    if state != TERMINAL_NO_FOLLOWUP_DECISION_STATE:
        return False
    return (
        identity is not None
        and identity.binding_kind is SettlementBindingKind.AUTONOMOUS_REPLAN
        and settlement.get("writeback_committed") is True
        and settlement.get("spend_committed") is not True
    )


def build_quota_slot_preview_for_decision(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    before: dict[str, Any],
    after_decision: QuotaDecisionBuilder,
    quota_status_builder: QuotaStatusBuilder,
    self_repair_spend_actions: set[str] | frozenset[str],
    slots: int = 1,
    agent_id: str | None = None,
    workspace_path: Path | None = None,
    todo_id: str | None = None,
    replan_obligation_id: str | None = None,
    turn_instance_id: str | None = None,
    source: str = DEFAULT_SLOT_SPEND_SOURCE,
) -> dict[str, Any]:
    safe_goal_id = _validate_goal_id_path_segment(str(goal_id or ""))
    safe_slots = max(1, _int_number(slots, default=1))
    safe_requested_agent_id = normalize_todo_claimed_by(agent_id)
    normalized_todo_id = normalize_todo_id(todo_id) if todo_id else None
    normalized_replan_obligation_id = normalize_todo_replan_obligation_id(
        replan_obligation_id
    )
    unbound_visible_goal_failure = _unbound_visible_goal_settlement_failure(
        source=source,
        before=before,
        requested_todo_id=normalized_todo_id,
        requested_replan_obligation_id=normalized_replan_obligation_id,
        turn_instance_id=turn_instance_id,
    )
    if unbound_visible_goal_failure is not None:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "reason": unbound_visible_goal_failure.failure.reason,
            "settlement_result": settlement_result_payload(
                unbound_visible_goal_failure
            ),
            "delivery_workspace": None,
            "delivery_workspace_causality": None,
            "delivery_workspace_validated": False,
            "before": before,
            "after": None,
        }
    raw_runtime_root = status_payload.get("runtime_root")
    settlement_identity = None
    settlement_result = None
    delivery_completion_run = None
    delivery_workspace_causality = None
    settlement = _resolve_preview_settlement(
        raw_runtime_root=raw_runtime_root,
        source=source,
        goal_id=safe_goal_id,
        agent_id=safe_requested_agent_id,
        todo_id=normalized_todo_id,
        replan_obligation_id=normalized_replan_obligation_id,
        turn_instance_id=turn_instance_id,
    )
    settlement_identity = settlement.get("identity")
    settlement_result = settlement.get("result")
    delivery_completion_run = settlement.get("delivery_run")
    delivery_workspace_causality = settlement.get(
        "delivery_workspace_causality"
    )
    if settlement.get("reason"):
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "reason": settlement["reason"],
            "settlement_result": (
                settlement_result_payload(settlement_result)
                if settlement_result is not None
                else None
            ),
            "before": before,
            "after": None,
        }
    binding_error = _todo_binding_error(
        source=source,
        status_payload=status_payload,
        goal_id=safe_goal_id,
        before=before,
        requested_todo_id=normalized_todo_id,
        requested_replan_obligation_id=normalized_replan_obligation_id,
        agent_id=safe_requested_agent_id,
        settlement_identity=settlement_identity,
        delivery_run=(
            delivery_completion_run if settlement_identity is not None else None
        ),
    )
    if binding_error:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "reason": binding_error,
            "before": before,
            "after": None,
        }
    safe_bypass_requested = (
        (
            before.get("state") == "operator_gate"
            or before.get("recovery_delivery_allowed") is True
            or before.get("effective_action") == EffectiveAction.OUTCOME_FLOOR_RECOVERY.value
        )
        and before.get("safe_bypass_allowed") is True
    )
    self_repair_spend = before.get("effective_action") in self_repair_spend_actions
    capability_repair_spend = (
        before.get("effective_action") == EffectiveAction.CAPABILITY_BRIDGE_REPAIR.value
        and before.get("capability_repair_allowed") is True
    )
    delivery_completion_run = delivery_completion_run or (
        _latest_unspent_turn_settlement_run(
            Path(str(raw_runtime_root)).expanduser(),
            safe_goal_id,
            agent_id=safe_requested_agent_id,
        )
        if raw_runtime_root
        else None
    )
    delivery_workspace_causality = _repair_settlement_workspace_causality(
        status_payload,
        goal_id=safe_goal_id,
        settlement_identity=settlement_identity,
        causality=delivery_workspace_causality,
    )
    settlement_workspace_requirement = _settlement_workspace_requirement(
        delivery_workspace_causality,
        settlement_identity,
        settlement_result,
        delivery_completion_run,
    )
    safe_bypass_without_delivery = (
        safe_bypass_requested and delivery_completion_run is None
    )
    if safe_bypass_without_delivery:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "reason": (
                "safe-bypass quota spend requires a latest "
                "unspent Turn settlement writeback"
            ),
            "before": before,
            "after": None,
        }
    safe_bypass_spend = safe_bypass_requested
    raw_delivery_workspace = (
        delivery_completion_run.get("delivery_workspace")
        if isinstance(delivery_completion_run, dict)
        and isinstance(delivery_completion_run.get("delivery_workspace"), dict)
        else None
    )
    missing_delivery_workspace_preview = _missing_delivery_workspace_preview(
        delivery_workspace_causality=delivery_workspace_causality,
        settlement_workspace_requirement=settlement_workspace_requirement,
        delivery_workspace=raw_delivery_workspace,
        goal_id=safe_goal_id,
        slots=safe_slots,
        agent_id=safe_requested_agent_id,
        before=before,
    )
    if missing_delivery_workspace_preview:
        return missing_delivery_workspace_preview
    workspace_requirement = _workspace_requirement_value(
        settlement_workspace_requirement, delivery_workspace_causality
    )
    raw_delivery_workspace_identity = delivery_workspace_identity(
        raw_delivery_workspace
    )
    delivery_workspace = (
        raw_delivery_workspace
        if raw_delivery_workspace_identity
        and workspace_requirement != "not_required"
        else None
    )
    delivery_workspace_guard = (
        build_delivery_workspace_guard(
            delivery_completion_run,
            agent_id=safe_requested_agent_id,
            current_path=workspace_path,
        )
        if delivery_completion_run and delivery_workspace
        else None
    )
    if delivery_workspace_guard:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "reason": delivery_workspace_guard["reason"],
            "workspace_guard": delivery_workspace_guard,
            "delivery_workspace": delivery_workspace,
            "delivery_workspace_causality": delivery_workspace_causality,
            "delivery_workspace_validated": False,
            "before": before,
            "after": None,
        }
    delivery_workspace_validated = bool(delivery_workspace)
    workspace_repair_no_spend = (
        before.get("effective_action") == EffectiveAction.AGENT_WORKSPACE_REPAIR.value
        and before.get("workspace_repair_allowed") is True
        and not delivery_workspace_validated
    )
    if workspace_repair_no_spend:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "delivery_workspace": delivery_workspace,
            "delivery_workspace_causality": delivery_workspace_causality,
            "delivery_workspace_validated": False,
            "reason": (
                "agent workspace guard requires moving to an independent "
                "worktree and rerunning quota should-run before quota spend"
            ),
            "before": before,
            "after": None,
        }
    delivery_completion_spend = (
        delivery_completion_run is not None
        and before.get("ok")
        and (
            settlement_identity is not None
            or not before.get("should_run")
            or before.get("effective_action") == EffectiveAction.EXTERNAL_EVIDENCE_OBSERVE.value
            or (
                before.get("effective_action") == EffectiveAction.AGENT_WORKSPACE_REPAIR.value
                and delivery_workspace_validated
            )
        )
        and before.get("effective_action") != EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED.value
        and not safe_bypass_spend
        and _admits_delivery_completion_spend_state(
            before=before,
            identity=settlement_identity,
            settlement=settlement,
        )
    )
    if delivery_completion_spend:
        capability_repair_spend = False
    if not before.get("ok") or (
        not before.get("should_run")
        and not safe_bypass_spend
        and not capability_repair_spend
        and not delivery_completion_spend
    ):
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "reason": before.get("reason") or "goal is not eligible for quota accounting preview",
            "before": before,
            "after": None,
        }
    before_quota = before.get("quota") if isinstance(before.get("quota"), dict) else {}
    if not before_quota:
        return {
            "ok": False,
            "mode": "spend-slot",
            "dry_run": True,
            "goal_id": safe_goal_id,
            "slots": safe_slots,
            "agent_id": safe_requested_agent_id,
            "appended": False,
            "registry_mutated": False,
            "reason": "goal has no quota payload to preview",
            "before": before,
            "after": None,
        }

    queue_item = _queue_item_for_goal(status_payload, goal_id=safe_goal_id)
    after_status = deepcopy(status_payload)
    after_goal = {
        "quota": {
            **before_quota,
            "spent_slots": _int_number(before_quota.get("spent_slots"), default=0) + safe_slots,
        }
    }
    after_quota = quota_status_builder(
        after_goal,
        waiting_on=str(before.get("waiting_on") or ""),
        severity=str(queue_item.get("severity") or ""),
        lifecycle_phase=before.get("lifecycle_phase") or queue_item.get("lifecycle_phase"),
        lifecycle_flags=before.get("lifecycle_flags") or queue_item.get("lifecycle_flags"),
        status=before.get("status") or queue_item.get("status"),
    )
    _set_quota_for_goal(after_status, goal_id=safe_goal_id, quota=after_quota)
    after = after_decision(after_status)

    return {
        "ok": True,
        "mode": "spend-slot",
        "dry_run": True,
        "goal_id": safe_goal_id,
        "slots": safe_slots,
        "agent_id": safe_requested_agent_id,
        "appended": False,
        "registry_mutated": False,
        "before": before,
        "after": after,
        "would_throttle": after.get("state") == "throttled",
        "reason": (
            f"dry-run preview: spending {safe_slots} slot(s) accounts for latest "
            f"validated delivery {delivery_completion_run.get('classification')} "
            f"after current {before.get('state')} guard"
            if delivery_completion_spend and delivery_completion_run
            else (
                f"dry-run preview: spending {safe_slots} slot(s) would move "
                f"{safe_goal_id} from {before.get('state')} to {after.get('state')}"
            )
        ),
        "accounting_projection": {
            "schema_version": "quota_slot_accounting_projection_v0",
            "settlement_event_semantics": "append_only",
            "spent_slots_semantics": "rolling_window_aggregate",
            "before_after_semantics": "same_status_payload_projection",
            "window_hours": _int_number(before_quota.get("window_hours"), default=0),
        },
        "rolling_window_note": (
            "before -> after is a same-status-payload projection. Later quota status "
            "recomputes spent_slots from quota_slot_spent events still inside window_hours, "
            "so the visible total can stay flat or decrease as older spends expire; that "
            "does not replay or undo the appended settlement event."
        ),
        "todo_id": normalized_todo_id,
        "replan_obligation_id": normalized_replan_obligation_id,
        "turn_instance_id": (
            settlement_identity.turn_instance_id
            if settlement_identity is not None
            else None
        ),
        "settlement_identity": (
            settlement_identity.as_dict()
            if settlement_identity is not None
            else None
        ),
        "settlement_result": (
            settlement_result_payload(settlement_result)
            if settlement_result is not None
            else None
        ),
        "safe_bypass_spend": safe_bypass_spend,
        "self_repair_spend": self_repair_spend,
        "capability_repair_spend": capability_repair_spend,
        "delivery_completion_spend": delivery_completion_spend,
        "delivery_run_generated_at": delivery_completion_run.get("generated_at")
        if delivery_completion_run
        else None,
        "delivery_run_classification": delivery_completion_run.get("classification")
        if delivery_completion_run
        else None,
        "delivery_run_agent_id": normalize_todo_claimed_by(delivery_completion_run.get("agent_id"))
        if delivery_completion_run
        else None,
        "delivery_run_recommended_action": delivery_completion_run.get("recommended_action")
        if delivery_completion_run
        else None,
        "delivery_workspace": delivery_workspace,
        "delivery_workspace_causality": delivery_workspace_causality,
        "settlement_workspace_requirement": settlement_workspace_requirement,
        "delivery_workspace_validated": delivery_workspace_validated,
    }


def load_quota_event_from_run(run: dict[str, Any]) -> dict[str, Any] | None:
    if str(run.get("classification") or "") not in {
        QUOTA_SLOT_SPENT_CLASSIFICATION,
        QUOTA_SLOT_VOIDED_CLASSIFICATION,
    }:
        return None
    event = run.get("quota_event") if isinstance(run.get("quota_event"), dict) else None
    if event:
        return event

    raw_json_path = str(run.get("json_path") or "")
    if not raw_json_path:
        return None
    json_path = Path(raw_json_path).expanduser()
    if not json_path.exists():
        return None
    try:
        record = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    event = record.get("quota_event") if isinstance(record.get("quota_event"), dict) else None
    return event


def quota_slot_contribution(run: dict[str, Any]) -> tuple[str, str, int] | None:
    """Classify one run's contribution to the rolling-window slot ledger.

    ``goal_quota_with_spend_ledger`` enforces quota from this rule and the
    usage summary reports from it, so both read an event the same way: the
    quota event's ``event_type`` decides, a spend is keyed by the run it was
    recorded against, and a void by the run it targets. A run with no usable
    event contributes no slot rather than a default one, which is what the
    ledger already assumed.
    """

    event = load_quota_event_from_run(run)
    if not event:
        return None
    slots = max(0, _int_number(event.get("slots"), default=0))
    if slots <= 0:
        return None
    event_type = str(event.get("event_type") or "")
    if event_type == QUOTA_SLOT_SPENT_CLASSIFICATION:
        run_key = str(event.get("run_generated_at") or run.get("generated_at") or "")
        if not run_key:
            return None
        return ("spent", run_key, slots)
    if event_type == QUOTA_SLOT_VOIDED_CLASSIFICATION:
        voided_run_generated_at = str(event.get("voided_run_generated_at") or "")
        if not voided_run_generated_at:
            return None
        return ("voided", voided_run_generated_at, slots)
    return None


def net_quota_slot_spend(
    contributions: Iterable[tuple[Any, str, int]],
) -> dict[Any, int]:
    """Clamp each spend bucket against the voids that target it.

    A void only cancels the spend recorded against the key it names, so a
    window that no longer holds that spend is never pushed negative and a void
    never cancels an unrelated spend.
    """

    spent: dict[Any, int] = {}
    voided: dict[Any, int] = {}
    for bucket, kind, slots in contributions:
        target = spent if kind == "spent" else voided
        target[bucket] = target.get(bucket, 0) + slots
    return {
        bucket: max(0, slots - voided.get(bucket, 0))
        for bucket, slots in spent.items()
    }
