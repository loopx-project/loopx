from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from ...state_projection import next_action_resolution_trace
from ..todos.contract import TODO_TASK_CLASS_ADVANCEMENT
from ..todos.todo_semantics import todo_item_is_actionable_open, todo_item_task_class
from .autonomous_replan_obligation import todo_lifecycle_settlement_obligation


def protocol_action_text(value: Any, *, limit: int = 220) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    if not text:
        return None
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def protocol_action_label(value: Any, *, limit: int = 80) -> str | None:
    text = protocol_action_text(value)
    if not text:
        return None
    head, separator, _tail = text.partition(":")
    if separator and "[" in head and "]" in head and 8 <= len(head) <= limit:
        return head.strip()
    return protocol_action_text(text, limit=limit)


def primary_action_instruction_facts(payload: dict[str, Any]) -> dict[str, Any]:
    """Bound each list to its first selectable view; the typed owner selects the instruction."""
    def obj(value: Any) -> dict[str, Any]:
        return value if isinstance(value, dict) else {}

    def rows(value: Any) -> list[dict[str, Any]]:
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    def first(views: Iterable[dict[str, Any]], eligible: Callable[[dict[str, Any]], bool]) -> list[dict[str, Any]]:
        return next(([view] for view in views if eligible(view)), [])

    def scalar(value: Any) -> str | None:
        return value if isinstance(value, str) else None

    def address(item: dict[str, Any], *, limit: int = 80) -> dict[str, Any]:
        return {"todo_id": str(item.get("todo_id") or "").strip(),
                "label": protocol_action_label(item.get("text"), limit=limit)}

    goal_id = str(payload.get("goal_id") or "")
    lane = obj(payload.get("agent_lane_next_action"))
    work = obj(payload.get("work_lane_contract"))
    gate = obj(payload.get("capability_gate"))
    selected = obj(obj(payload.get("scoped_user_gate_fallback")).get("selected_executable"))
    todos = obj(payload.get("agent_todo_summary"))
    backlog = obj(payload.get("autonomous_backlog_candidates"))
    packet = obj(payload.get("replan_action_packet"))
    writeback = obj(packet.get("writeback_contract"))
    contract = obj(payload.get("task_orchestration_contract"))
    lifecycle = todo_lifecycle_settlement_obligation(payload)
    return {
        "goal_id": goal_id,
        "agent_lane": {**address(lane), "next_step_truthy": bool(lane.get("next_step")),
                       "next_step_label": protocol_action_label(lane.get("next_step"))},
        "work_lane": {
            "monitor_kind": scalar(work.get("monitor_kind")),
            "obligation": scalar(work.get("obligation")),
            "monitor_due_items": first((address(item) for item in rows(work.get("monitor_due_items"))),
                                       lambda view: bool(view["label"])),
            "selected_todo_id": str(work.get("selected_todo_id") or "").strip(),
            "action_label": protocol_action_label(work.get("action"), limit=160),
            "action_text": protocol_action_text(work.get("action")),
            "due_action_text": protocol_action_text(work.get("action"), limit=260),
            "obligation_text": protocol_action_text(work.get("obligation")),
            "reason_codes": [item for item in work.get("reason_codes", []) if isinstance(item, str)]
                if isinstance(work.get("reason_codes"), list) else [],
        },
        "capability_gate": {"action": scalar(gate.get("action")),
                            "runnable_count": len(gate["runnable_candidates"])
                            if isinstance(gate.get("runnable_candidates"), list) else 0},
        "scoped_fallback": {**address(selected), "fallback_kind": scalar(selected.get("fallback_kind"))},
        "agent_todos": {key: first(({"label": protocol_action_label(item.get("text")),
                               "actionable": todo_item_is_actionable_open(item),
                               "advancement": todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT} for item in rows(todos.get(key))),
                                  lambda view: bool(view["actionable"] and view["advancement"] and view["label"]))
                        for key in ("first_executable_items", "first_open_items")},
        "backlog": first(({"goal_id": str(item.get("goal_id") or ""),
                           "label": protocol_action_label(item.get("text"))} for item in rows(backlog.get("items"))),
                         lambda view: bool(view["label"] and (not goal_id or view["goal_id"] == goal_id))),
        "recommended_action": protocol_action_text(payload.get("recommended_action")),
        "pending_intent": protocol_action_text(obj(payload.get("pending_capability_intent")).get("action_summary"), limit=320),
        "observation_target": protocol_action_text(obj(payload.get("external_evidence_observation")).get("observation_target")),
        "lifecycle": {"present": lifecycle is not None,
                      "action": protocol_action_text(lifecycle.get("recommended_action"), limit=360)
                      if lifecycle is not None else None},
        "replan_packet": {"settlement_only": packet.get("settlement_only") is True,
                          "rule": protocol_action_text(writeback.get("rule")),
                          "has_required_outcome": bool(obj(packet.get("uncovered_frontier")).get("required_any_of")),
                          "has_successor_command": bool(writeback.get("successor_command"))},
        "frontier_action": protocol_action_text(obj(payload.get("agent_scope_frontier")).get("recommended_action"), limit=260),
        "orchestration": {"mode": scalar(contract.get("mode")),
                          "child_count": len(contract["eligible_child_lanes"])
                          if isinstance(contract.get("eligible_child_lanes"), list) else None,
                          "peer_count": len(contract["eligible_peer_lanes"])
                          if isinstance(contract.get("eligible_peer_lanes"), list) else 0},
        "contract_obligation": str(obj(payload.get("execution_obligation"))["contract_obligation"])
            if obj(payload.get("execution_obligation")).get("contract_obligation") else None,
    }


def protocol_monitor_action(payload: dict[str, Any]) -> str | None:
    goal_id = str(payload.get("goal_id") or "")
    work_lane = (
        payload.get("work_lane_contract")
        if isinstance(payload.get("work_lane_contract"), dict)
        else {}
    )
    if work_lane.get("obligation") == "quiet_until_material_monitor_transition":
        return "quiet until a material monitor transition, regression, or concrete blocker appears"
    monitors = (
        payload.get("autonomous_monitor_candidates")
        if isinstance(payload.get("autonomous_monitor_candidates"), dict)
        else {}
    )
    items = monitors.get("items") if isinstance(monitors.get("items"), list) else []
    for item in items:
        if not isinstance(item, dict):
            continue
        if goal_id and str(item.get("goal_id") or "") != goal_id:
            continue
        text = protocol_action_label(item.get("text"), limit=160)
        if text:
            return text
    return None


def build_primary_action_projection(
    payload: dict[str, Any], *, mode: str, primary_action: str | None,
) -> dict[str, Any]:
    projection: dict[str, Any] = {"primary_action": primary_action}
    resolution_trace = next_action_resolution_trace(
        primary_action=primary_action,
        mode=mode,
        active_state_next_action=payload.get("active_state_next_action"),
        latest_run_recommended_action=payload.get("latest_run_recommended_action"),
        selected_recommended_action=payload.get("recommended_action"),
        agent_lane_next_action=payload.get("agent_lane_next_action"),
    )
    if resolution_trace:
        projection["resolution_trace"] = resolution_trace
    return projection
