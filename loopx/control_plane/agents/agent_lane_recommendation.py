from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from ..effect_program import ReceiptBoundMonitorPhase
from ..todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_MONITOR,
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from ..todos.todo_semantics import todo_item_is_due_monitor
from ..todos.summary_item import compact_todo_summary_item
from ..work_items.primary_action import protocol_action_text
from ..work_items.work_lane import (
    work_lane_contract_is_due_monitor_attempt,
    work_lane_contract_is_lark_inbox_reply_due,
    work_lane_contract_is_operator_inbox_material_review_due,
)
from .agent_scope import (
    _todo_item_is_actionable_open,
    _todo_task_class,
    agent_scope_item_claimed_by,
    agent_scope_item_claimed_by_agent_or_unclaimed,
)
from .capability_gate import (
    _agent_lane_candidate_sort_key,
    missing_required_capabilities,
)

PublicSafeText = Callable[..., str | None]
ActionAlignment = Callable[[Any, Any], bool]
TimestampParser = Callable[[Any], Any]
AGENT_LANE_NEXT_ACTION_SCHEMA_VERSION = "agent_lane_next_action_v0"
AGENT_LANE_PROGRESS_SCOPE = "agent_lane"


def build_explicit_advancement_next_action(
    *,
    agent_identity: dict[str, Any] | None,
    agent_todo_items: list[dict[str, Any]],
    available_capabilities: Any,
    todo_id: str | None,
    selection_binding: str,
) -> dict[str, Any] | None:
    """Project an exact eligible advancement from the authoritative Todo set."""

    if not isinstance(agent_identity, dict):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    normalized_todo_id = normalize_todo_id(todo_id)
    if not agent_id or not normalized_todo_id:
        return None
    for item in agent_todo_items:
        if normalize_todo_id(item.get("todo_id")) != normalized_todo_id:
            continue
        if (
            not _todo_item_is_actionable_open(item)
            or _todo_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT
            or missing_required_capabilities(
                item,
                available_capabilities=available_capabilities,
            )
            or not agent_scope_item_claimed_by_agent_or_unclaimed(
                item,
                agent_id=agent_id,
            )
        ):
            return None
        text = protocol_action_text(item.get("text"), limit=500)
        if not text:
            return None
        payload = compact_todo_summary_item(item, text=text)
        payload.update(
            {
                "schema_version": AGENT_LANE_NEXT_ACTION_SCHEMA_VERSION,
                "agent_id": agent_id,
                "source": "authoritative_agent_todo",
                "selected_by": "current_agent_claimed_todo",
                "confidence": "selected",
                "preserves_goal_next_action": True,
                "selection_binding": selection_binding,
            }
        )
        if not agent_scope_item_claimed_by(item):
            payload["selected_by"] = "unclaimed_todo"
            payload["claim_required_before_work"] = True
        return payload
    return None


def build_receipt_bound_advancement_next_action(
    *,
    agent_identity: dict[str, Any] | None,
    agent_todo_items: list[dict[str, Any]],
    available_capabilities: Any,
    receipt_bound_todo_id: str | None,
) -> dict[str, Any] | None:
    """Replay a committed advancement omitted by bounded display projections."""

    candidate = build_explicit_advancement_next_action(
        agent_identity=agent_identity,
        agent_todo_items=agent_todo_items,
        available_capabilities=available_capabilities,
        todo_id=receipt_bound_todo_id,
        selection_binding="heartbeat_receipt",
    )
    if candidate is not None:
        candidate["source"] = "heartbeat_receipt.agent_todo"
    return candidate


def build_receipt_bound_monitor_next_action(
    *,
    agent_identity: dict[str, Any] | None,
    agent_todo_items: list[dict[str, Any]],
    available_capabilities: Any,
    receipt_bound_todo_id: str | None,
    receipt_bound_monitor_phase: ReceiptBoundMonitorPhase | None = None,
) -> dict[str, Any] | None:
    """Recover an exact receipt-bound monitor omitted by compact hot lanes.

    A monitor remains the same-turn settlement identity after a successful poll
    reschedules it into the future.  That replay must preserve the parent Todo
    without asking the agent to observe the target again.
    """

    if not isinstance(agent_identity, dict):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    todo_id = normalize_todo_id(receipt_bound_todo_id)
    if not agent_id or not todo_id:
        return None
    for item in agent_todo_items:
        if normalize_todo_id(item.get("todo_id")) != todo_id:
            continue
        monitor_due = todo_item_is_due_monitor(item)
        if (
            not _todo_item_is_actionable_open(item)
            or _todo_task_class(item) != TODO_TASK_CLASS_MONITOR
            or (
                monitor_due
                and missing_required_capabilities(
                    item,
                    available_capabilities=available_capabilities,
                )
            )
            or not agent_scope_item_claimed_by_agent_or_unclaimed(
                item,
                agent_id=agent_id,
            )
        ):
            return None
        text = protocol_action_text(item.get("text"), limit=500)
        if not text:
            return None
        payload = compact_todo_summary_item(item, text=text)
        monitor_phase = receipt_bound_monitor_phase or (
            ReceiptBoundMonitorPhase.POLL_DUE
            if monitor_due
            else ReceiptBoundMonitorPhase.SETTLEMENT_PENDING
        )
        payload.update(
            {
                "schema_version": AGENT_LANE_NEXT_ACTION_SCHEMA_VERSION,
                "agent_id": agent_id,
                "source": "heartbeat_receipt.monitor_todo",
                "selected_by": "current_agent_claimed_todo",
                "confidence": "selected",
                "preserves_goal_next_action": True,
                "selection_binding": "heartbeat_receipt",
                "receipt_bound_monitor_phase": monitor_phase.value,
            }
        )
        if not agent_scope_item_claimed_by(item):
            payload["selected_by"] = "unclaimed_todo"
            payload["claim_required_before_work"] = True
        return payload
    return None


def is_status_neutral_run(
    run: dict[str, Any],
    *,
    status_neutral_classifications: set[str],
    agent_lane_progress_scope: str,
) -> bool:
    return (
        str(run.get("classification") or "") in status_neutral_classifications
        or str(run.get("progress_scope") or "") == agent_lane_progress_scope
    )


def latest_agent_lane_run(
    goal: dict[str, Any],
    *,
    agent_lane_progress_scope: str,
    preferred_agent_id: str | None = None,
) -> dict[str, Any] | None:
    runs = goal.get("latest_runs")
    if not isinstance(runs, list):
        return None
    preferred_agent = normalize_todo_claimed_by(preferred_agent_id)
    for run in runs:
        if not isinstance(run, dict):
            continue
        if str(run.get("progress_scope") or "") != agent_lane_progress_scope:
            continue
        if (
            preferred_agent
            and normalize_todo_claimed_by(run.get("agent_id")) != preferred_agent
        ):
            continue
        return run
    return None


def compact_agent_lane_recommendation(
    run: dict[str, Any] | None,
    *,
    agent_lane_progress_scope: str,
    public_safe_compact_text: PublicSafeText,
) -> dict[str, Any] | None:
    if not isinstance(run, dict):
        return None
    action = public_safe_compact_text(run.get("recommended_action"), limit=220)
    if not action:
        return None
    compact: dict[str, Any] = {
        "schema_version": "agent_lane_recommendation_v0",
        "progress_scope": agent_lane_progress_scope,
        "recommended_action": action,
    }
    for field in (
        "agent_id",
        "agent_lane",
        "classification",
        "generated_at",
        "delivery_batch_scale",
        "delivery_outcome",
    ):
        if run.get(field) is not None:
            compact[field] = run.get(field)
    return compact


def scope_status_item_to_agent_lane(
    item: dict[str, Any],
    *,
    latest_runs: list[dict[str, Any]],
    agent_id: str | None,
    public_safe_compact_text: PublicSafeText,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    scoped_item = dict(item)
    project_asset = (
        dict(scoped_item.get("project_asset"))
        if isinstance(scoped_item.get("project_asset"), dict)
        else {}
    )
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not safe_agent_id:
        return scoped_item, project_asset, None
    item_had_recommendation = isinstance(
        scoped_item.get("agent_lane_recommendation"), dict
    )
    item_had_latest_action = bool(
        scoped_item.get("latest_run_recommended_action")
        or scoped_item.get("latest_run_recommended_action_source")
    )
    asset_had_recommendation = isinstance(
        project_asset.get("agent_lane_recommendation"), dict
    )
    asset_had_latest_action = bool(
        project_asset.get("latest_run_recommended_action")
        or project_asset.get("latest_run_recommended_action_source")
    )
    for field in (
        "agent_lane_recommendation",
        "latest_run_recommended_action",
        "latest_run_recommended_action_source",
    ):
        scoped_item.pop(field, None)
        project_asset.pop(field, None)

    lane_run = latest_agent_lane_run(
        {"latest_runs": latest_runs},
        agent_lane_progress_scope=AGENT_LANE_PROGRESS_SCOPE,
        preferred_agent_id=safe_agent_id,
    )
    recommendation = compact_agent_lane_recommendation(
        lane_run,
        agent_lane_progress_scope=AGENT_LANE_PROGRESS_SCOPE,
        public_safe_compact_text=public_safe_compact_text,
    )
    lane_action = public_safe_compact_text(
        recommendation.get("recommended_action")
        if isinstance(recommendation, dict)
        else None,
        limit=320,
    )
    if recommendation and lane_action:
        if item_had_recommendation:
            scoped_item["agent_lane_recommendation"] = recommendation
        if item_had_latest_action:
            scoped_item["latest_run_recommended_action"] = lane_action
            scoped_item["latest_run_recommended_action_source"] = (
                "agent_lane_recommendation"
            )
        if asset_had_recommendation:
            project_asset["agent_lane_recommendation"] = recommendation
        if asset_had_latest_action:
            project_asset["latest_run_recommended_action"] = lane_action
            project_asset["latest_run_recommended_action_source"] = (
                "agent_lane_recommendation"
            )

    if project_asset:
        scoped_item["project_asset"] = project_asset
    return scoped_item, project_asset, recommendation


def latest_run_recommended_action_for_projection(
    *,
    current_status_run: dict[str, Any] | None,
    agent_lane_recommendation: dict[str, Any] | None,
    active_state_next_action: Any = None,
    preferred_agent_id: str | None = None,
    limit: int = 320,
    public_safe_compact_text: PublicSafeText,
    actions_are_projection_aligned: ActionAlignment,
    parse_timestamp: TimestampParser,
) -> tuple[str | None, str | None]:
    latest_action = public_safe_compact_text(
        current_status_run.get("recommended_action")
        if isinstance(current_status_run, dict)
        else None,
        limit=limit,
    )
    if not isinstance(agent_lane_recommendation, dict):
        return latest_action, "latest_status_run" if latest_action else None

    lane_action = public_safe_compact_text(
        agent_lane_recommendation.get("recommended_action"),
        limit=limit,
    )
    if not lane_action:
        return latest_action, "latest_status_run" if latest_action else None
    lane_dt = parse_timestamp(agent_lane_recommendation.get("generated_at"))
    latest_dt = parse_timestamp(
        current_status_run.get("generated_at")
        if isinstance(current_status_run, dict)
        else None
    )
    lane_agent_id = str(agent_lane_recommendation.get("agent_id") or "").strip()
    preferred_agent = str(preferred_agent_id or "").strip()
    lane_matches_preferred_agent = bool(
        preferred_agent and lane_agent_id and lane_agent_id == preferred_agent
    )
    lane_is_newer = bool(lane_dt and latest_dt and lane_dt >= latest_dt)
    if lane_is_newer and lane_matches_preferred_agent:
        return lane_action, "agent_lane_recommendation"
    if not active_state_next_action or not actions_are_projection_aligned(
        active_state_next_action,
        lane_action,
    ):
        return latest_action, "latest_status_run" if latest_action else None

    latest_aligned = bool(
        latest_action
        and actions_are_projection_aligned(active_state_next_action, latest_action)
    )
    if not latest_action or not latest_aligned:
        return lane_action, "agent_lane_recommendation"
    return latest_action, "latest_status_run"


def _first_executable_todo_text(agent_todo_summary: dict[str, Any] | None) -> str | None:
    if not isinstance(agent_todo_summary, dict):
        return None
    items = (
        agent_todo_summary.get("first_executable_items")
        if isinstance(agent_todo_summary.get("first_executable_items"), list)
        else []
    )
    for item in items:
        if not isinstance(item, dict):
            continue
        if not _todo_item_is_actionable_open(item):
            continue
        if _todo_task_class(item) != TODO_TASK_CLASS_ADVANCEMENT:
            continue
        text = protocol_action_text(item.get("text"), limit=320)
        if text:
            return text
    return None


def _todo_ids_from_action(value: Any) -> set[str]:
    text = str(value or "")
    if not text:
        return set()
    return set(re.findall(r"\btodo_[A-Za-z0-9_]+\b", text))


def selected_recommended_action_from_work_lane(
    item: dict[str, Any],
    *,
    agent_todo_summary: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    agent_lane_recommendation: dict[str, Any] | None = None,
    prefer_agent_lane_recommendation: bool = False,
) -> Any:
    raw_action = item.get("recommended_action")
    if prefer_agent_lane_recommendation:
        if isinstance(agent_lane_recommendation, dict):
            lane_action = agent_lane_recommendation.get("recommended_action")
            if lane_action:
                return lane_action
        if isinstance(work_lane_contract, dict):
            return work_lane_contract.get("action")
        return None
    if work_lane_contract_is_lark_inbox_reply_due(
        work_lane_contract
    ) or work_lane_contract_is_operator_inbox_material_review_due(
        work_lane_contract
    ):
        return work_lane_contract.get("action") or raw_action
    if work_lane_contract_is_due_monitor_attempt(work_lane_contract):
        due_items = (
            work_lane_contract.get("monitor_due_items")
            if isinstance(work_lane_contract.get("monitor_due_items"), list)
            else []
        )
        for due_item in due_items:
            if not isinstance(due_item, dict):
                continue
            text = protocol_action_text(due_item.get("text"), limit=320)
            if text:
                return text
        return raw_action
    if (
        isinstance(work_lane_contract, dict)
        and work_lane_contract.get("lane") == "advancement_task"
        and "open_agent_todo"
        in (
            work_lane_contract.get("reason_codes")
            if isinstance(work_lane_contract.get("reason_codes"), list)
            else []
        )
    ):
        return _first_executable_todo_text(agent_todo_summary) or raw_action
    return raw_action


def build_agent_lane_next_action(
    *,
    agent_identity: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    capability_gate: dict[str, Any] | None,
    active_next_action: Any = None,
    scoped_user_gate_fallback: dict[str, Any] | None = None,
    receipt_bound_todo_id: str | None = None,
    selected_todo_override: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(agent_identity, dict):
        return None
    agent_id = normalize_todo_claimed_by(agent_identity.get("agent_id"))
    if not agent_id or not isinstance(agent_todo_summary, dict):
        return None
    agent_profile = (
        agent_identity.get("agent_profile")
        if isinstance(agent_identity.get("agent_profile"), dict)
        else None
    )

    if isinstance(scoped_user_gate_fallback, dict):
        selected = scoped_user_gate_fallback.get("selected_executable")
        if isinstance(selected, dict):
            text = protocol_action_text(selected.get("text"), limit=500)
            claimed_by = agent_scope_item_claimed_by(selected)
            if (
                text
                and _todo_item_is_actionable_open(selected)
                and _todo_task_class(selected) == TODO_TASK_CLASS_ADVANCEMENT
                and agent_scope_item_claimed_by_agent_or_unclaimed(
                    selected,
                    agent_id=agent_id,
                )
            ):
                payload = dict(selected)
                payload.update(
                    {
                        "schema_version": AGENT_LANE_NEXT_ACTION_SCHEMA_VERSION,
                        "agent_id": agent_id,
                        "source": "scoped_user_gate_fallback.selected_executable",
                        "selected_by": "scoped_user_gate_fallback",
                        "confidence": "selected",
                        "preserves_goal_next_action": False,
                        "replaces_gated_goal_next_action": True,
                    }
                )
                if not claimed_by:
                    payload["claim_required_before_work"] = True
                return payload

    candidate_sources: list[tuple[str, list[Any]]] = []
    # An empty projected list is authoritative; falling back would bypass the gate.
    capability_candidates = (
        capability_gate.get("runnable_candidates")
        if isinstance(capability_gate, dict)
        else None
    )
    if isinstance(capability_candidates, list):
        candidate_sources.append(
            (
                "capability_gate.runnable_candidates",
                capability_candidates,
            )
        )
    else:
        candidate_sources.append(
            (
                "agent_todo_summary.active_next_action_executable_items",
                agent_todo_summary.get("active_next_action_executable_items")
                if isinstance(
                    agent_todo_summary.get("active_next_action_executable_items"), list
                )
                else [],
            )
        )
        candidate_sources.append(
            (
                "agent_todo_summary.first_executable_items",
                agent_todo_summary.get("first_executable_items")
                if isinstance(
                    agent_todo_summary.get("first_executable_items"), list
                )
                else [],
            )
        )
        candidate_sources.append(
            (
                "agent_todo_summary.executable_backlog_items",
                agent_todo_summary.get("executable_backlog_items")
                if isinstance(
                    agent_todo_summary.get("executable_backlog_items"), list
                )
                else [],
            )
        )

    preferred_todo_ids = _todo_ids_from_action(active_next_action)
    receipt_todo_id = normalize_todo_id(receipt_bound_todo_id)
    override_todo_id = (
        normalize_todo_id(selected_todo_override.get("todo_id"))
        if isinstance(selected_todo_override, dict)
        else None
    )
    active_next_action_items = (
        agent_todo_summary.get("active_next_action_executable_items")
        if isinstance(agent_todo_summary.get("active_next_action_executable_items"), list)
        else []
    )
    active_next_action_todo_ids = {
        normalize_todo_id(item.get("todo_id"))
        for item in active_next_action_items
        if isinstance(item, dict)
    }

    seen: set[tuple[str, str]] = set()
    for source, raw_items in candidate_sources:
        source_candidates: list[dict[str, Any]] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            if not _todo_item_is_actionable_open(raw_item):
                continue
            if _todo_task_class(raw_item) != TODO_TASK_CLASS_ADVANCEMENT:
                continue
            text = protocol_action_text(raw_item.get("text"), limit=500)
            if not text:
                continue
            identity = (str(raw_item.get("todo_id") or ""), text)
            if identity in seen:
                continue
            if not agent_scope_item_claimed_by_agent_or_unclaimed(
                raw_item,
                agent_id=agent_id,
            ):
                continue
            seen.add(identity)
            source_candidates.append(raw_item)
        for raw_item in sorted(
            source_candidates,
            key=lambda candidate: (
                0
                if receipt_todo_id
                and normalize_todo_id(candidate.get("todo_id")) == receipt_todo_id
                else 1,
                0
                if override_todo_id
                and normalize_todo_id(candidate.get("todo_id")) == override_todo_id
                else 1,
                _agent_lane_candidate_sort_key(
                    candidate,
                    agent_id=agent_id,
                    preferred_todo_ids=preferred_todo_ids,
                    agent_profile=agent_profile,
                ),
            ),
        ):
            text = protocol_action_text(raw_item.get("text"), limit=500)
            claimed_by = agent_scope_item_claimed_by(raw_item)
            todo_id = str(raw_item.get("todo_id") or "").strip()
            override_selected = bool(
                override_todo_id
                and normalize_todo_id(todo_id) == override_todo_id
            )
            selected_by = (
                str(selected_todo_override.get("selected_by") or "selected_todo_override")
                if override_selected and isinstance(selected_todo_override, dict)
                else "active_next_action_todo"
                if todo_id and todo_id in preferred_todo_ids
                else "current_agent_claimed_todo"
                if claimed_by == agent_id
                else "unclaimed_todo"
            )
            payload = compact_todo_summary_item(raw_item, text=text)
            if selected_by == "unclaimed_todo":
                payload["claim_required_before_work"] = True
            if receipt_todo_id and normalize_todo_id(todo_id) == receipt_todo_id:
                payload["selection_binding"] = "heartbeat_receipt"
            lineage_source = source
            if override_selected and isinstance(selected_todo_override, dict):
                lineage_source = str(
                    selected_todo_override.get("source") or lineage_source
                )
                if selected_todo_override.get("selection_reason") is not None:
                    payload["selection_reason"] = selected_todo_override[
                        "selection_reason"
                    ]
            if (
                source == "capability_gate.runnable_candidates"
                and selected_by == "active_next_action_todo"
                and normalize_todo_id(todo_id) in active_next_action_todo_ids
            ):
                lineage_source = "agent_todo_summary.active_next_action_executable_items"
            unblocks_todo_id = normalize_todo_id(raw_item.get("unblocks_todo_id"))
            if unblocks_todo_id:
                payload["dependency_handoff"] = {
                    "unblocks_todo_id": unblocks_todo_id,
                }
            for key in (
                "missing_capabilities",
                "missing_target_capabilities",
                "capability_action",
                "capability_repair_mode",
            ):
                if raw_item.get(key) is not None:
                    payload[key] = raw_item.get(key)
            payload.update(
                {
                    "schema_version": AGENT_LANE_NEXT_ACTION_SCHEMA_VERSION,
                    "agent_id": agent_id,
                    "source": lineage_source,
                    "selected_by": selected_by,
                    "confidence": (
                        "selected"
                        if selected_by
                        in {
                            "in_flight_todo",
                            "active_next_action_todo",
                            "current_agent_claimed_todo",
                        }
                        else "candidate"
                    ),
                    "preserves_goal_next_action": True,
                }
            )
            return payload
    return None


def selected_action_with_agent_lane(
    selected_action: Any,
    *,
    agent_lane_next_action: dict[str, Any] | None,
) -> Any:
    if not isinstance(agent_lane_next_action, dict):
        return selected_action
    if agent_lane_next_action.get("source") not in {
        "capability_gate.runnable_candidates",
        "agent_todo_summary.active_next_action_executable_items",
        "delivery_continuity.latest_accountable_delivery",
        "authoritative_agent_todo",
    }:
        return selected_action
    selected_by = agent_lane_next_action.get("selected_by")
    confidence = agent_lane_next_action.get("confidence")
    if selected_by not in {
        "active_next_action_todo",
        "in_flight_todo",
        "current_agent_claimed_todo",
        "unclaimed_todo",
    }:
        return selected_action
    if confidence not in {"selected", "candidate"}:
        return selected_action
    lane_text = str(agent_lane_next_action.get("text") or "").strip()
    return lane_text or selected_action
