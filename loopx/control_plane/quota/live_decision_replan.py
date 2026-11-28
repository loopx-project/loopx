from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import cast

from ..agent_context import project_agent_context, project_goal_agent_context
from ..goals.activation import goal_owner_resume_id
from ..goals.goal_frontier.fallback_source import (
    FallbackTodoSource,
    live_fallback_authority_items,
)
from ..runtime.time import now_utc_iso
from ..scheduler.execution_context import SchedulerExecutionContextResolution
from ..work_items.interaction_contract import build_interaction_contract
from .blocked_retry import overlay_active_turn_retries
from .effect_program import ReceiptBoundReplayPhase
from .effective_action import EffectiveAction
from .live_decision_contracts import (
    AttachSettlementProgressFn,
    BuildInteractionContractFn,
    HostObservationResolver,
    ProgressReviewContextReader,
    ReceiptBoundGuardKwargs,
    as_json_map,
    as_object_list,
    mapping_or_empty,
    require_mapping,
)
from .settlement import QuotaSettlementReadback, attach_settlement_progress
from .unsettled_host_turn import restore_receipt_bound_todo_source


def _split_receipt_bound_guard_fields(
    readback: QuotaSettlementReadback | None,
    obligation_id: str | None,
) -> tuple[ReceiptBoundGuardKwargs, str | None]:
    """Build typed replan-guard kwargs together with the admission event id."""

    guard = getattr(readback, "semantic_replan_guard", None)
    raw_receipt = getattr(readback, "heartbeat_receipt", None)
    receipt = require_mapping(raw_receipt, label="heartbeat_receipt") if raw_receipt else {}
    identity = readback.identity.value if readback is not None else None
    guard_scoped = bool(
        obligation_id
        and isinstance(guard, Mapping)
        and guard.get("scope") == "turn_guard"
        and guard.get("selected_obligation_id") == obligation_id
    )
    # The effective receipt stays the latest content. The admission clock is the
    # first bound receipt already selected by the GoalRef-filtered readback.
    admission_recorded_at = getattr(readback, "admission_recorded_at", None)
    raw_recorded = admission_recorded_at or receipt.get("recorded_at")
    recorded_at = raw_recorded if isinstance(raw_recorded, str) else None
    raw_admission_event_id = getattr(readback, "admission_event_id", None)
    admission_event_id = (
        raw_admission_event_id if isinstance(raw_admission_event_id, str) else None
    )
    raw_review = (
        guard.get("selected_review_source") if isinstance(guard, Mapping) else None
    )
    kwargs: ReceiptBoundGuardKwargs = {
        "receipt_bound_replan_guard_scoped": guard_scoped,
        "receipt_bound_replan_execution_pending": bool(
            guard_scoped and readback and readback.replan_execution_pending
        ),
        "receipt_bound_guard_recorded_at": recorded_at,
        "receipt_bound_guard_turn_instance_id": (
            identity.turn_instance_id if identity is not None else None
        ),
        "receipt_bound_replan_review": (
            mapping_or_empty(raw_review) if isinstance(raw_review, Mapping) else None
        ),
        "receipt_bound_successor_replan": bool(
            getattr(readback, "successor_replan_pending", False)
        ),
    }
    return kwargs, admission_event_id


def _receipt_bound_guard_fields(
    readback: QuotaSettlementReadback | None,
    obligation_id: str | None,
) -> dict[str, object]:
    """Adapt the recorded guard identity and time for the shared decision owner."""

    guard_kwargs, admission_event_id = _split_receipt_bound_guard_fields(
        readback, obligation_id
    )
    return {
        **guard_kwargs,
        "receipt_bound_admission_event_id": admission_event_id,
    }


def _historical_ack_writeback_required(payload: Mapping[str, object]) -> bool:
    """Recognize the exact historical ACK closeout before durable writeback."""

    packet = payload.get("replan_action_packet")
    return (
        isinstance(packet, Mapping)
        and packet.get("settlement_only") is True
        and packet.get("decision") == "historical_ack_recorded"
    )


def _prepared_execution_needs_readback(
    payload: Mapping[str, object],
    readback: QuotaSettlementReadback | None,
    receipt_todo_id: str | None,
) -> bool:
    """Ask the receipt owner to check a prepared generation missing from the current projection."""

    if readback is None or readback.replay_phase is not ReceiptBoundReplayPhase.OPEN:
        return False
    prepared = readback.prepared_lease
    if not prepared or prepared.get("prepared_lease_epoch") is None:
        return False
    raw_current = payload.get("execution_preparation")
    current = (
        require_mapping(raw_current, label="execution_preparation")
        if raw_current
        else {}
    )
    return (
        current.get("todo_id") != receipt_todo_id
        or current.get("lease_epoch") != prepared["prepared_lease_epoch"]
    )


def _original_replan_settlement_required(
    payload: Mapping[str, object],
    readback: QuotaSettlementReadback | None,
) -> bool:
    if _historical_ack_writeback_required(payload):
        return True
    if (
        readback is None
        or readback.replay_phase is not ReceiptBoundReplayPhase.SETTLEMENT_PENDING
    ):
        return False
    packet = payload.get("replan_action_packet")
    identity = readback.identity.value
    progress = mapping_or_empty(readback.progress)
    return (
        isinstance(packet, Mapping)
        and packet.get("settlement_only") is True
        and packet.get("decision") == readback.replay_phase.value
        and identity is not None
        and identity.replan_obligation_id is not None
        and identity.replan_obligation_id == packet.get("obligation_id")
        and (
            progress.get("next_step") == "quota_spend"
            or progress.get("checkpoint_recovery_required") is True
        )
        and payload.get("should_run") is True
        and payload.get("effective_action")
        == EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value
    )


def _project_receipt_replan_settlement(
    payload: dict[str, object],
    readback: QuotaSettlementReadback | None,
    *,
    available_capabilities: list[str] | None,
    scheduler_execution_context: (
        Mapping[str, object] | SchedulerExecutionContextResolution | None
    ),
    turn_instance_id: str | None,
    registry_path: Path,
    runtime_root: Path,
    goal_ref: Mapping[str, object] | None,
    attach_settlement_fn: AttachSettlementProgressFn = attach_settlement_progress,
    build_interaction_contract_fn: BuildInteractionContractFn = build_interaction_contract,
) -> None:
    """Project closeout from the original readback, never the current selection."""

    if not readback or not _original_replan_settlement_required(payload, readback):
        return
    attach_settlement_fn(
        payload,
        readback,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_ref=goal_ref,
    )
    identity = readback.identity.value
    if identity is None:
        raise ValueError("original replan settlement identity is unavailable")
    payload["heartbeat_receipt"] = {
        **mapping_or_empty(readback.heartbeat_receipt),
        "settlement_identity": identity.as_dict(),
    }
    if mapping_or_empty(readback.progress).get("checkpoint_recovery_required") is True:
        reason = (
            "Read checkpoint-context for the original blocked writeback. Reassess its evidence and submit "
            "only the original checkpoint supplement. Preserve its delivery fields and quota."
        )
        packet = {
            **require_mapping(
                payload["replan_action_packet"], label="replan_action_packet"
            ),
            "decision": "vision_outcome_checkpoint_required",
            "required_outcome": "durable_writeback_receipt",
            "writeback_contract": {"rule": reason},
        }
        obligation = require_mapping(
            payload["autonomous_replan_obligation"],
            label="autonomous_replan_obligation",
        )
        payload["autonomous_replan_obligation"] = {
            **obligation,
            "recommended_action": reason,
            "guidance_actions": [reason],
            "settlement_action_packet": packet,
        }
        payload.update(replan_action_packet=packet, recommended_action=reason)
    payload["interaction_contract"] = build_interaction_contract_fn(
        payload,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=turn_instance_id,
        registry_path=str(registry_path),
        runtime_root=str(runtime_root),
    )


def _receipt_bound_recovery_todo(
    payload: Mapping[str, object],
    receipt_bound_todo_id: str | None,
    *,
    agent_id: str | None,
    turn_instance_id: str | None,
    replay_phase: ReceiptBoundReplayPhase | None,
) -> str | None:
    """Keep finished/replan-only Turns out of generic Todo wait recovery."""

    if (
        not receipt_bound_todo_id
        or not agent_id
        or not turn_instance_id
        or replay_phase is ReceiptBoundReplayPhase.SETTLED
        or _historical_ack_writeback_required(payload)
        or payload.get("effective_action")
        == EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value
    ):
        return None
    if replay_phase is ReceiptBoundReplayPhase.SETTLEMENT_PENDING:
        return receipt_bound_todo_id
    selected = mapping_or_empty(payload.get("selected_todo"))
    if (
        selected.get("todo_id") != receipt_bound_todo_id
        or payload.get("normal_delivery_allowed") is not True
    ):
        return receipt_bound_todo_id
    wait_kind = str(selected.get("resume_when") or "").split(":", 1)[0]
    return (
        receipt_bound_todo_id
        if wait_kind not in ("", "monitor_changed", "todo_done")
        else None
    )


def _restore_replan_obligation_without_foreign_acks(
    status_payload: dict[str, object],
    *,
    goal_id: str,
    agent_id: str,
    goal_ref: Mapping[str, object] | None,
    registry_goal: Mapping[str, object] | None,
    registry_path: Path,
    runtime_root: Path,
    progress_review_context_reader: ProgressReviewContextReader | None,
) -> None:
    """Recompute the open review after removing another instance's ACK rows."""

    from ..status.autonomous_replan_projection import autonomous_replan_obligation_from_runs
    from ..work_items.autonomous_replan_ack import drop_unowned_recorded_replan_acks
    from ..work_items.project_asset import attach_active_state_project_asset_fields
    from ..work_items.replan_history_codec import effective_turn_cadence_context

    run_history = as_json_map(status_payload.get("run_history"))
    goals = as_object_list(run_history.get("goals")) if run_history is not None else None
    goal: dict[str, object] | None = None
    if goals is not None:
        for entry in goals:
            entry_map = as_json_map(entry)
            if entry_map is not None and str(entry_map.get("id") or "") == goal_id:
                goal = entry_map
                break
    if goal is None:
        return
    latest_runs_raw = as_object_list(goal.get("latest_runs"))
    if latest_runs_raw is None:
        return
    filtered, changed = drop_unowned_recorded_replan_acks(
        cast(list[dict[str, object]], latest_runs_raw),
        goal_id=goal_id,
        goal_ref=goal_ref,
    )
    if not changed:
        return
    goal["latest_runs"] = filtered
    kept_ack_times = {
        run.get("generated_at")
        for run in filtered
        if isinstance(run.get("autonomous_replan_ack"), dict)
    }
    semantic = as_json_map(goal.get("semantic_history"))
    agents = as_object_list(semantic.get("agents")) if semantic is not None else None
    if agents is not None:
        for context_item in agents:
            context = as_json_map(context_item)
            if context is None:
                continue
            ack_run = as_json_map(context.get("latest_autonomous_replan_ack_run"))
            if (
                ack_run is not None
                and ack_run.get("generated_at") not in kept_ack_times
            ):
                context.pop("latest_autonomous_replan_ack_run", None)
    queue = as_json_map(status_payload.get("attention_queue"))
    items = as_object_list(queue.get("items")) if queue is not None else None
    item: dict[str, object] | None = None
    if items is not None:
        for entry in items:
            entry_map = as_json_map(entry)
            if entry_map is not None and entry_map.get("goal_id") == goal_id:
                item = entry_map
                break
    if item is None:
        return
    for target in (item, as_json_map(item.get("project_asset"))):
        if target is None:
            continue
        target.pop("autonomous_replan_obligation", None)
        target.pop("autonomous_replan_obligations_by_agent", None)
        target.pop("autonomous_replan_ack", None)
    source_goal: dict[str, object] = (
        dict(registry_goal) if isinstance(registry_goal, Mapping) else {"id": goal_id}
    )
    source_goal.setdefault("id", goal_id)
    review = (
        progress_review_context_reader(source_goal, runtime_root)
        if progress_review_context_reader is not None
        else None
    )
    cadence_goal_ref = (
        {
            str(key): value
            for key, value in goal_ref.items()
            if isinstance(value, str)
        }
        if isinstance(goal_ref, Mapping)
        and all(isinstance(value, str) for value in goal_ref.values())
        else None
    )
    attach_active_state_project_asset_fields(
        item,
        latest_runs=filtered,
        autonomous_replan_obligation_from_runs=autonomous_replan_obligation_from_runs,
        external_progress_review=review,
        effective_turn_cadence=effective_turn_cadence_context(
            source_goal,
            runtime_root,
            registry_path=registry_path,
            goal_ref=cadence_goal_ref,
        ),
        agent_lane_id=agent_id,
    )


def resolve_host_rrule_and_automation(
    *,
    codex_app_host: bool,
    codex_app_current_rrule: str | None,
    host_observation_resolver: HostObservationResolver | None,
    goal_id: str,
    agent_id: str | None,
) -> tuple[str, str]:
    """Resolve observed Codex App RRULE and automation id from inputs or host."""

    observed_rrule = str(codex_app_current_rrule or "").strip()
    observed_automation_id = ""
    if (
        codex_app_host
        and not observed_rrule
        and host_observation_resolver is not None
    ):
        observation = host_observation_resolver(goal_id=goal_id, agent_id=agent_id)
        if observation.get("available") is True:
            observed_rrule = str(observation.get("rrule") or "")
            observed_automation_id = str(
                observation.get("automation_id") or ""
            ).strip()
    return observed_rrule, observed_automation_id


def attach_goal_todos_to_attention_queue(
    decision_status_payload: dict[str, object],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
) -> None:
    """Hydrate complete Goal Todos into the matching attention-queue item."""

    from ..todos.list_readback import list_goal_todos

    source = list_goal_todos(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
    )
    todo_fields: dict[str, object] = {
        key: source[key] for key in ("user_todos", "agent_todos")
    }
    raw_queue = decision_status_payload.get("attention_queue")
    queue = require_mapping(raw_queue, label="attention_queue") if raw_queue else {}
    items = queue.get("items") or []
    if not isinstance(items, list):
        raise TypeError(
            f"Expected list for attention_queue.items, got {type(items).__name__}"
        )
    updated_items: list[object] = []
    for item in items:
        item_map = require_mapping(item, label="attention_queue.item")
        updated_items.append(
            {**item_map, **todo_fields} if item_map.get("goal_id") == goal_id else item
        )
    decision_status_payload["attention_queue"] = {
        **queue,
        "items": updated_items,
    }


def prepare_live_decision_status_payload(
    decision_status_payload: dict[str, object],
    *,
    goal_id: str,
    agent_id: str | None,
    goal_ref: Mapping[str, object] | None,
    registered_goal: dict[str, object] | None,
    registry_path: Path,
    runtime_root: Path,
    guard_kwargs: ReceiptBoundGuardKwargs,
    admission_event_id: str | None,
    receipt_bound_replay_phase: ReceiptBoundReplayPhase | None,
    receipt_bound_todo_id: str | None,
    requested_action_todo_id: str | None,
    retained_action_selection_todo_id: str | None,
    receipt_bound_replan_obligation_id: str | None,
    progress_review_context_reader: ProgressReviewContextReader | None,
) -> tuple[dict[str, object], FallbackTodoSource]:
    """Hydrate status payload with replan ACK cleanup, Todo snapshots, retries and fallbacks."""

    if (
        guard_kwargs["receipt_bound_replan_guard_scoped"]
        and receipt_bound_replay_phase is ReceiptBoundReplayPhase.OPEN
        and receipt_bound_replan_obligation_id
        and agent_id
    ):
        from ..work_items.autonomous_replan_ack import load_receipt_replan_ack_candidates

        decision_status_payload["receipt_bound_replan_ack_candidates"] = (
            load_receipt_replan_ack_candidates(
                runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                obligation_id=receipt_bound_replan_obligation_id,
                guard_recorded_at=guard_kwargs["receipt_bound_guard_recorded_at"],
                guard_turn_instance_id=guard_kwargs[
                    "receipt_bound_guard_turn_instance_id"
                ],
                goal_ref=goal_ref,
                admission_event_id=admission_event_id,
            )
        )
        _restore_replan_obligation_without_foreign_acks(
            decision_status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
            goal_ref=goal_ref,
            registry_goal=registered_goal,
            registry_path=registry_path,
            runtime_root=runtime_root,
            progress_review_context_reader=progress_review_context_reader,
        )
    if (
        requested_action_todo_id or retained_action_selection_todo_id
    ) and not receipt_bound_todo_id:
        attach_goal_todos_to_attention_queue(
            decision_status_payload,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
    decision_status_payload = restore_receipt_bound_todo_source(
        decision_status_payload,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=receipt_bound_todo_id,
        replay_phase=receipt_bound_replay_phase,
        replan_guard_scoped=guard_kwargs["receipt_bound_replan_guard_scoped"],
    )
    decision_status_payload = overlay_active_turn_retries(
        decision_status_payload,
        goal_id=goal_id,
        agent_id=agent_id,
        observed_at=now_utc_iso(),
    )
    authoritative_fallback_todo_items = live_fallback_authority_items(
        decision_status_payload,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
    )
    return decision_status_payload, authoritative_fallback_todo_items


def project_before_plan_agent_context(
    payload: dict[str, object],
    *,
    goal_id: str,
    agent_id: str,
    registered_goal: dict[str, object] | None,
    orchestration: Mapping[str, object],
    registry_path: Path,
    runtime_root: Path,
) -> None:
    """Attach the agent capability-improvement context to an active interaction contract."""

    interaction = payload.get("interaction_contract")
    if not isinstance(interaction, dict):
        return
    raw_selected = payload.get("selected_todo")
    selected_todo = (
        require_mapping(raw_selected, label="selected_todo") if raw_selected else {}
    )
    scope: dict[str, object] = {
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": selected_todo.get("todo_id"),
    }
    if registered_goal is not None:
        context = project_goal_agent_context(
            phase="before_plan",
            scope=scope,
            goal=registered_goal,
            registry_path=registry_path,
            runtime_root=runtime_root,
            observations={
                "capability_improvement": {
                    "trigger": (
                        "replan"
                        if payload.get("replan_action_packet")
                        else "before_plan"
                    ),
                }
            },
        )
    else:
        context = project_agent_context(
            phase="before_plan",
            scope=scope,
            orchestration=dict(orchestration),
        )
    if context is not None:
        interaction["agent_context"] = context


def owner_resume_action_id(registered_goal: dict[str, object] | None) -> str | None:
    """Return owner-resume action id from the registered goal."""

    return goal_owner_resume_id(registered_goal)
