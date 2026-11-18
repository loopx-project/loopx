from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime

from ..effect_program import ReceiptBoundMonitorPhase
from ..progress_scope import AGENT_LANE_PROGRESS_SCOPE
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

Payload = dict[str, object]
PayloadView = Mapping[str, object]
PublicSafeText = Callable[..., str | None]
ActionAlignment = Callable[[object, object], bool]
TimestampParser = Callable[[object], datetime | None]
AGENT_LANE_NEXT_ACTION_SCHEMA_VERSION = "agent_lane_next_action_v0"
_SCOPED_LANE_FIELDS = (
    "agent_lane_recommendation",
    "latest_run_recommended_action",
    "latest_run_recommended_action_source",
)
_LANE_RECOMMENDATION_FIELDS = (
    "agent_id",
    "agent_lane",
    "classification",
    "generated_at",
    "delivery_batch_scale",
    "delivery_outcome",
)
_CAPABILITY_GATE_FIELDS = (
    "missing_capabilities",
    "missing_target_capabilities",
    "capability_action",
    "capability_repair_mode",
)


def _as_dict(value: object) -> Payload | None:
    return value if isinstance(value, dict) else None


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _copy_non_none_fields(
    target: Payload,
    source: PayloadView,
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        value = source.get(field)
        if value is not None:
            target[field] = value


def _is_eligible_todo(
    item: Payload,
    *,
    task_class: str = TODO_TASK_CLASS_ADVANCEMENT,
    agent_id: str | None = None,
) -> bool:
    return (
        _todo_item_is_actionable_open(item)
        and _todo_task_class(item) == task_class
        and (
            agent_id is None
            or agent_scope_item_claimed_by_agent_or_unclaimed(
                item,
                agent_id=agent_id,
            )
        )
    )


def _build_exact_todo_next_action(
    *,
    agent_identity: PayloadView | None,
    agent_todo_items: Sequence[PayloadView],
    available_capabilities: object,
    todo_id: str | None,
    task_class: str,
    source: str,
    selection_binding: str,
    receipt_bound_monitor_phase: ReceiptBoundMonitorPhase | None = None,
) -> Payload | None:
    identity = _as_dict(agent_identity)
    agent_id = normalize_todo_claimed_by(identity.get("agent_id")) if identity else None
    normalized_todo_id = normalize_todo_id(todo_id)
    if not agent_id or not normalized_todo_id:
        return None
    for raw_item in agent_todo_items:
        item = _as_dict(raw_item)
        if item is None or normalize_todo_id(item.get("todo_id")) != normalized_todo_id:
            continue
        if not _is_eligible_todo(item, task_class=task_class, agent_id=agent_id):
            return None
        monitor_due = (
            todo_item_is_due_monitor(item)
            if task_class == TODO_TASK_CLASS_MONITOR
            else True
        )
        if monitor_due and missing_required_capabilities(
            item,
            available_capabilities=available_capabilities,
        ):
            return None
        text = protocol_action_text(item.get("text"), limit=500)
        if not text:
            return None
        payload: Payload = compact_todo_summary_item(item, text=text)
        updates: Payload = {
            "schema_version": AGENT_LANE_NEXT_ACTION_SCHEMA_VERSION,
            "agent_id": agent_id,
            "source": source,
            "selected_by": "current_agent_claimed_todo",
            "confidence": "selected",
            "preserves_goal_next_action": True,
            "selection_binding": selection_binding,
        }
        if task_class == TODO_TASK_CLASS_MONITOR:
            monitor_phase = receipt_bound_monitor_phase or (
                ReceiptBoundMonitorPhase.POLL_DUE
                if monitor_due
                else ReceiptBoundMonitorPhase.SETTLEMENT_PENDING
            )
            updates["receipt_bound_monitor_phase"] = monitor_phase.value
        payload.update(updates)
        if not agent_scope_item_claimed_by(item):
            payload["selected_by"] = "unclaimed_todo"
            payload["claim_required_before_work"] = True
        return payload
    return None


def build_explicit_advancement_next_action(
    *,
    agent_identity: PayloadView | None,
    agent_todo_items: Sequence[PayloadView],
    available_capabilities: object,
    todo_id: str | None,
    selection_binding: str,
) -> Payload | None:
    """Project an exact eligible advancement from the authoritative Todo set."""

    return _build_exact_todo_next_action(
        agent_identity=agent_identity,
        agent_todo_items=agent_todo_items,
        available_capabilities=available_capabilities,
        todo_id=todo_id,
        task_class=TODO_TASK_CLASS_ADVANCEMENT,
        source="authoritative_agent_todo",
        selection_binding=selection_binding,
    )


def build_receipt_bound_advancement_next_action(
    *,
    agent_identity: PayloadView | None,
    agent_todo_items: Sequence[PayloadView],
    available_capabilities: object,
    receipt_bound_todo_id: str | None,
) -> Payload | None:
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
    agent_identity: PayloadView | None,
    agent_todo_items: Sequence[PayloadView],
    available_capabilities: object,
    receipt_bound_todo_id: str | None,
    receipt_bound_monitor_phase: ReceiptBoundMonitorPhase | None = None,
) -> Payload | None:
    """Recover an exact receipt-bound monitor omitted by compact hot lanes.

    A monitor remains the same-turn settlement identity after a successful poll
    reschedules it into the future.  That replay must preserve the parent Todo
    without asking the agent to observe the target again.
    """

    return _build_exact_todo_next_action(
        agent_identity=agent_identity,
        agent_todo_items=agent_todo_items,
        available_capabilities=available_capabilities,
        todo_id=receipt_bound_todo_id,
        task_class=TODO_TASK_CLASS_MONITOR,
        source="heartbeat_receipt.monitor_todo",
        selection_binding="heartbeat_receipt",
        receipt_bound_monitor_phase=receipt_bound_monitor_phase,
    )


def is_status_neutral_run(
    run: PayloadView,
    *,
    status_neutral_classifications: set[str],
    agent_lane_progress_scope: str,
) -> bool:
    return (
        str(run.get("classification") or "") in status_neutral_classifications
        or str(run.get("progress_scope") or "") == agent_lane_progress_scope
    )


def latest_agent_lane_run(
    goal: PayloadView,
    *,
    agent_lane_progress_scope: str,
    preferred_agent_id: str | None = None,
) -> Payload | None:
    runs = goal.get("latest_runs")
    if not isinstance(runs, list):
        return None
    preferred_agent = normalize_todo_claimed_by(preferred_agent_id)
    for raw_run in runs:
        run = _as_dict(raw_run)
        if run is None:
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
    run: PayloadView | None,
    *,
    agent_lane_progress_scope: str,
    public_safe_compact_text: PublicSafeText,
) -> Payload | None:
    run_dict = _as_dict(run)
    if run_dict is None:
        return None
    action = public_safe_compact_text(run_dict.get("recommended_action"), limit=220)
    if not action:
        return None
    compact: Payload = {
        "schema_version": "agent_lane_recommendation_v0",
        "progress_scope": agent_lane_progress_scope,
        "recommended_action": action,
    }
    _copy_non_none_fields(compact, run_dict, _LANE_RECOMMENDATION_FIELDS)
    return compact


def scope_status_item_to_agent_lane(
    item: PayloadView,
    *,
    latest_runs: Sequence[PayloadView],
    agent_id: str | None,
    public_safe_compact_text: PublicSafeText,
) -> tuple[Payload, Payload, Payload | None]:
    scoped_item: Payload = dict(item)
    raw_asset = _as_dict(scoped_item.get("project_asset"))
    project_asset: Payload = dict(raw_asset) if raw_asset is not None else {}
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not safe_agent_id:
        return scoped_item, project_asset, None
    targets: list[tuple[Payload, bool, bool]] = []
    for target in (scoped_item, project_asset):
        had_recommendation = isinstance(target.get("agent_lane_recommendation"), dict)
        had_latest_action = bool(
            target.get("latest_run_recommended_action")
            or target.get("latest_run_recommended_action_source")
        )
        for field in _SCOPED_LANE_FIELDS:
            target.pop(field, None)
        targets.append((target, had_recommendation, had_latest_action))

    lane_run = latest_agent_lane_run(
        {"latest_runs": latest_runs if isinstance(latest_runs, list) else list(latest_runs)},
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
        if recommendation is not None
        else None,
        limit=320,
    )
    if recommendation and lane_action:
        for target, had_recommendation, had_latest_action in targets:
            if had_recommendation:
                target["agent_lane_recommendation"] = recommendation
            if had_latest_action:
                target["latest_run_recommended_action"] = lane_action
                target["latest_run_recommended_action_source"] = (
                    "agent_lane_recommendation"
                )

    if project_asset:
        scoped_item["project_asset"] = project_asset
    return scoped_item, project_asset, recommendation


def latest_run_recommended_action_for_projection(
    *,
    current_status_run: PayloadView | None,
    agent_lane_recommendation: PayloadView | None,
    active_state_next_action: object = None,
    preferred_agent_id: str | None = None,
    limit: int = 320,
    public_safe_compact_text: PublicSafeText,
    actions_are_projection_aligned: ActionAlignment,
    parse_timestamp: TimestampParser,
) -> tuple[str | None, str | None]:
    status_run = _as_dict(current_status_run)
    lane_rec = _as_dict(agent_lane_recommendation)
    latest_action = public_safe_compact_text(
        status_run.get("recommended_action") if status_run is not None else None,
        limit=limit,
    )
    lane_action = (
        public_safe_compact_text(lane_rec.get("recommended_action"), limit=limit)
        if lane_rec is not None
        else None
    )
    if lane_rec is None or not lane_action:
        return latest_action, "latest_status_run" if latest_action else None
    lane_dt = parse_timestamp(lane_rec.get("generated_at"))
    latest_dt = parse_timestamp(
        status_run.get("generated_at") if status_run is not None else None
    )
    lane_agent_id = str(lane_rec.get("agent_id") or "").strip()
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


def _first_executable_todo_text(agent_todo_summary: PayloadView | None) -> str | None:
    summary = _as_dict(agent_todo_summary)
    if summary is None:
        return None
    for raw_item in _as_list(summary.get("first_executable_items")):
        item = _as_dict(raw_item)
        if item is None or not _is_eligible_todo(item):
            continue
        text = protocol_action_text(item.get("text"), limit=320)
        if text:
            return text
    return None


def selected_recommended_action_from_work_lane(
    item: PayloadView,
    *,
    agent_todo_summary: PayloadView | None,
    work_lane_contract: PayloadView | None,
    agent_lane_recommendation: PayloadView | None = None,
    prefer_agent_lane_recommendation: bool = False,
) -> object:
    raw_action = item.get("recommended_action")
    contract = _as_dict(work_lane_contract)
    lane_rec = _as_dict(agent_lane_recommendation)
    if prefer_agent_lane_recommendation:
        if lane_rec is not None:
            lane_action = lane_rec.get("recommended_action")
            if lane_action:
                return lane_action
        return contract.get("action") if contract is not None else None
    if work_lane_contract_is_lark_inbox_reply_due(
        contract
    ) or work_lane_contract_is_operator_inbox_material_review_due(
        contract
    ):
        return (contract.get("action") if contract is not None else None) or raw_action
    if work_lane_contract_is_due_monitor_attempt(contract):
        for raw_due_item in _as_list(contract.get("monitor_due_items")):
            due_item = _as_dict(raw_due_item)
            if due_item is None:
                continue
            text = protocol_action_text(due_item.get("text"), limit=320)
            if text:
                return text
        return raw_action
    if (
        contract is not None
        and contract.get("lane") == "advancement_task"
        and "open_agent_todo" in _as_list(contract.get("reason_codes"))
    ):
        return _first_executable_todo_text(agent_todo_summary) or raw_action
    return raw_action


def build_agent_lane_next_action(
    *,
    agent_identity: PayloadView | None,
    agent_todo_summary: PayloadView | None,
    capability_gate: PayloadView | None,
    active_next_action: object = None,
    scoped_user_gate_fallback: PayloadView | None = None,
    receipt_bound_todo_id: str | None = None,
    selected_todo_override: PayloadView | None = None,
) -> Payload | None:
    identity = _as_dict(agent_identity)
    summary = _as_dict(agent_todo_summary)
    agent_id = normalize_todo_claimed_by(identity.get("agent_id")) if identity else None
    if not agent_id or summary is None:
        return None
    assert identity is not None
    agent_profile = _as_dict(identity.get("agent_profile"))

    fallback = _as_dict(scoped_user_gate_fallback)
    if fallback is not None:
        selected = _as_dict(fallback.get("selected_executable"))
        if selected is not None:
            text = protocol_action_text(selected.get("text"), limit=500)
            claimed_by = agent_scope_item_claimed_by(selected)
            if text and _is_eligible_todo(selected, agent_id=agent_id):
                payload: Payload = dict(selected)
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
        # The scoped gate selector is authoritative for this crossing. A
        # ready deferred successor intentionally yields no delivery action
        # so the frontier can project successor_replan_required. Falling
        # through here could reselect the exact Todo covered by the gate.
        return None

    gate = _as_dict(capability_gate)
    # An empty projected list is authoritative; falling back would bypass the gate.
    capability_candidates = gate.get("runnable_candidates") if gate is not None else None
    if isinstance(capability_candidates, list):
        candidate_sources: list[tuple[str, list[object]]] = [
            ("capability_gate.runnable_candidates", capability_candidates),
        ]
    else:
        candidate_sources = [
            (f"agent_todo_summary.{key}", _as_list(summary.get(key)))
            for key in (
                "active_next_action_executable_items",
                "first_executable_items",
                "executable_backlog_items",
            )
        ]

    preferred_todo_ids: set[str] = set()
    receipt_todo_id = normalize_todo_id(receipt_bound_todo_id)
    override = _as_dict(selected_todo_override)
    override_todo_id = (
        normalize_todo_id(override.get("todo_id")) if override is not None else None
    )
    active_next_action_todo_ids = {
        normalize_todo_id(item.get("todo_id"))
        for raw_item in _as_list(summary.get("active_next_action_executable_items"))
        if (item := _as_dict(raw_item)) is not None
    }

    seen: set[tuple[str, str]] = set()
    for source, raw_items in candidate_sources:
        source_candidates: list[Payload] = []
        for raw_item in raw_items:
            item = _as_dict(raw_item)
            if item is None or not _is_eligible_todo(item, agent_id=agent_id):
                continue
            text = protocol_action_text(item.get("text"), limit=500)
            if not text:
                continue
            identity_key = (str(item.get("todo_id") or ""), text)
            if identity_key in seen:
                continue
            seen.add(identity_key)
            source_candidates.append(item)
        for item in sorted(
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
            text = protocol_action_text(item.get("text"), limit=500)
            claimed_by = agent_scope_item_claimed_by(item)
            todo_id = str(item.get("todo_id") or "").strip()
            normalized_todo_id = normalize_todo_id(todo_id)
            override_selected = bool(
                override_todo_id and normalized_todo_id == override_todo_id
            )
            selected_by = (
                str(override.get("selected_by") or "selected_todo_override")
                if override_selected and override is not None
                else "active_next_action_todo"
                if todo_id and todo_id in preferred_todo_ids
                else "current_agent_claimed_todo"
                if claimed_by == agent_id
                else "unclaimed_todo"
            )
            payload = compact_todo_summary_item(item, text=text)
            if selected_by == "unclaimed_todo":
                payload["claim_required_before_work"] = True
            if receipt_todo_id and normalized_todo_id == receipt_todo_id:
                payload["selection_binding"] = "heartbeat_receipt"
            lineage_source = source
            if override_selected and override is not None:
                lineage_source = str(override.get("source") or lineage_source)
                if override.get("selection_reason") is not None:
                    payload["selection_reason"] = override["selection_reason"]
            if (
                source == "capability_gate.runnable_candidates"
                and selected_by == "active_next_action_todo"
                and normalized_todo_id in active_next_action_todo_ids
            ):
                lineage_source = "agent_todo_summary.active_next_action_executable_items"
            unblocks_todo_id = normalize_todo_id(item.get("unblocks_todo_id"))
            if unblocks_todo_id:
                payload["dependency_handoff"] = {
                    "unblocks_todo_id": unblocks_todo_id,
                }
            _copy_non_none_fields(payload, item, _CAPABILITY_GATE_FIELDS)
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
    selected_action: object,
    *,
    agent_lane_next_action: PayloadView | None,
) -> object:
    lane_next = _as_dict(agent_lane_next_action)
    if lane_next is None:
        return selected_action
    if lane_next.get("next_step"):
        return lane_next["next_step"]
    if lane_next.get("source") not in {
        "capability_gate.runnable_candidates",
        "agent_todo_summary.active_next_action_executable_items",
        "delivery_continuity.latest_accountable_delivery",
        "authoritative_agent_todo",
    }:
        return selected_action
    selected_by = lane_next.get("selected_by")
    confidence = lane_next.get("confidence")
    if selected_by not in {
        "active_next_action_todo",
        "in_flight_todo",
        "current_agent_claimed_todo",
        "unclaimed_todo",
    }:
        return selected_action
    if confidence not in {"selected", "candidate"}:
        return selected_action
    lane_text = str(
        lane_next.get("next_step") or lane_next.get("text") or ""
    ).strip()
    return lane_text or selected_action
