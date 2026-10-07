"""Legacy fact codec for the single typed quota planning read boundary."""

from __future__ import annotations

from typing import Any

from ..agents.profile import agent_profile_candidate_rank
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from .contract import (
    normalize_todo_claimed_by, normalize_todo_bound_agent, normalize_todo_blocks_agent,
    normalize_todo_excluded_agents, normalize_todo_global_gate,
    normalize_required_capabilities, normalize_target_capabilities,
)
from .todo_semantics import (
    todo_item_has_removed_continuation_policy, todo_item_is_actionable_open,
    todo_item_is_due_monitor, todo_item_is_watch_only_monitor,
    todo_item_task_class, todo_projection_sort_key,
    todo_summary_monitor_writeback_supported,
)
from .resume_planning import build_todo_resume_planning_request
from .summary_item import compact_todo_summary_item
from .user_gate import is_user_gate_todo_item


def _closure_source_facts(value: dict[str, Any]) -> dict[str, Any]:
    # JSON erases Python's int/float distinction. Retain the strict integer
    # evidence type before transport; the typed owner decides validity.
    def integer(raw: Any) -> int | None:
        return raw if type(raw) is int else None

    def proof(key: str, fields: tuple[str, ...], counts: dict[str, int | None]) -> Any:
        raw = value.get(key)
        if not isinstance(raw, dict):
            return None
        return {**{name: raw.get(name) for name in fields},
                **{name: integer(raw.get(name, default)) for name, default in counts.items()}}

    def rows(key: str, *, absent_empty: bool = False) -> Any:
        raw = value.get(key)
        if raw is None and absent_empty:
            raw = []
        if not isinstance(raw, list):
            return None
        return [{"status": item.get("status"), "done": item.get("done"),
                 "watch_only": todo_item_is_watch_only_monitor(item),
                 "route_continuation_replan_required": item.get("route_continuation_replan_required")}
                if isinstance(item, dict) else None for item in raw]

    return {
        **{key: value.get(key) for key in ("schema_version", "source_section")},
        **{key: integer(value.get(key)) for key in ("total_count", "open_count", "done_count", "deferred_count")},
        "convergence_open_count": integer(value.get("convergence_open_count", value.get("open_count"))),
        **{key: integer(value.get(key, 0)) for key in ("completed_without_successor_count", "route_continuation_replan_count")},
        "source_proof": proof("source_proof", ("schema_version", "role", "derived"), {"item_count": None}),
        "terminal_closure_proof": proof("terminal_closure_proof",
            ("schema_version", "role", "source_section", "all_todos_done", "all_convergent_todos_done", "derived"),
            {"item_count": None, "monitor_open_count": None, "watch_only_monitor_count": 0,
             "successor_gap_count": None, "route_replan_count": None, "no_followup_count": None}),
        "closure_intent": proof("closure_intent", ("schema_version", "kind", "derived"), {"count": None}),
        "items": rows("items"), "monitor_open_items": rows("monitor_open_items", absent_empty=True),
        "deferred_item_count": len(value["deferred_items"]) if isinstance(value.get("deferred_items"), list) else None,
        "deferred_resume_count": len(value["deferred_resume_candidates"]) if isinstance(value.get("deferred_resume_candidates"), list) else None,
    }


def project_quota_planning(
    value: dict[str, Any], *, all_open_items: list[dict[str, Any]],
    source_open_count: Any, agent_identity: dict[str, Any] | None,
    filter_user_gate_blocks_agent: bool, available_capabilities: Any,
    resolve_capacity: bool = False,
) -> dict[str, Any]:
    identity = agent_identity if isinstance(agent_identity, dict) else {}
    profile = identity.get("agent_profile")
    profile = profile if isinstance(profile, dict) and profile else None
    agent = normalize_todo_claimed_by(identity.get("agent_id"))

    def encode(item: dict[str, Any]) -> dict[str, Any]:
        priority, index = todo_projection_sort_key(item)
        display = compact_todo_summary_item(item, text=str(item.get("text") or "").strip())
        return {
            "payload": item, **({"display": display} if display != item else {}),
            "claim": normalize_todo_claimed_by(item.get("claimed_by")),
            "bound": normalize_todo_bound_agent(item.get("bound_agent")),
            "blocks": normalize_todo_blocks_agent(item.get("blocks_agent")),
            "excluded": normalize_todo_excluded_agents(item.get("excluded_agents")),
            "global": bool(normalize_todo_global_gate(item.get("global_gate"))),
            "gate": is_user_gate_todo_item(item),
            "removed": todo_item_has_removed_continuation_policy(item),
            "actionable": todo_item_is_actionable_open(item),
            "due": todo_item_is_due_monitor(item),
            "watch_only": todo_item_is_watch_only_monitor(item),
            "task_class": todo_item_task_class(item),
            "priority": priority, "index": index,
            "profile_rank": agent_profile_candidate_rank(item, agent_profile=profile),
            "required": normalize_required_capabilities(item.get("required_capabilities")),
            "targets": normalize_target_capabilities(item.get("target_capabilities")),
            "raw_claimed": bool(item.get("claimed_by")),
        }

    def active(key: str) -> list[dict[str, Any]]:
        raw = value.get(key)
        return [encode(item) for item in raw if isinstance(item, dict)] if isinstance(raw, list) else []

    try:
        result = effect_runtime_result("todo.quota_planning.project", {
            "schema_version": "todo_quota_planning_request_v2",
            "source_contract": _closure_source_facts(value),
            "resume": build_todo_resume_planning_request(value, agent_id=agent, item_limit=8,
                available_capabilities=(available_capabilities or []) if resolve_capacity else None),
            "selection": {
                "available": normalize_required_capabilities(available_capabilities),
                "items": [encode(item) for item in all_open_items],
                "active_items": active("active_next_action_items"),
                "active_executable_items": active("active_next_action_executable_items"),
                "agent_id": agent, "profile": profile,
                "user_gate_scope": filter_user_gate_blocks_agent,
                "monitor_supported": todo_summary_monitor_writeback_supported(value),
                "source_open_count": source_open_count,
                "source_complete": (value.get("work_counts") or {}).get("complete", True),
                "frontier_revision_index": value.get("advancement_frontier_revision_index"),
                "diagnostic_limit": 3, "backlog_limit": 8, "visibility_limit": 16,
            },
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, dict) or result.get("schema_version") != "todo_quota_planning_v0":
        raise RuntimeError("TypeScript Todo quota planning shape mismatch")
    if not isinstance(result.get("source_completeness"), dict) or "closure_intent" not in result:
        raise RuntimeError("TypeScript Todo quota planning source contract missing")
    if isinstance(result["closure_intent"], dict):
        result["closure_intent"] = {**value["closure_intent"], **result["closure_intent"]}
    elif result["closure_intent"] is not None:
        raise RuntimeError("TypeScript Todo quota planning closure intent shape mismatch")
    return result
