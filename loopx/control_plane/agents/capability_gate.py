from __future__ import annotations

from functools import lru_cache
from typing import Any

from ..todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    normalize_required_capabilities,
    normalize_target_capabilities,
    normalize_todo_claimed_by,
)
from ..todos.todo_semantics import (
    todo_index_rank,
    todo_item_is_actionable_open,
    todo_item_task_class,
    todo_priority_rank,
)
from ..todos.summary_item import compact_todo_summary_items
from .agent_scope import agent_scope_item_claimed_by
from .profile import agent_profile_candidate_rank


CAPABILITY_OWNER_GATE_HINTS = {
    "credentials",
    "production_access",
}


def runtime_capabilities_for_cli_projection(value: Any) -> list[str]:
    """Return observed runtime capabilities, never owner-held authority gates."""

    return [
        capability
        for capability in normalize_required_capabilities(value)
        if capability not in CAPABILITY_OWNER_GATE_HINTS
    ]


def _evaluate(operation: str, **facts: Any) -> Any:
    from ..effect_runtime import effect_runtime_result

    response = effect_runtime_result("agent.capability_gate.evaluate", {
        "schema_version": "capability_gate_request_v0", "operation": operation, **facts,
    })
    if not isinstance(response, dict) or response.get("schema_version") != "capability_gate_result_v0":
        raise TypeError("invalid typed capability gate result")
    return response["result"]


@lru_cache(maxsize=512)
def _missing(required: tuple[str, ...], targets: tuple[str, ...],
             available: tuple[str, ...]) -> tuple[str, ...]:
    # Cache pure normalized requirement values, never Todo identity/state or authority.
    result = _evaluate("missing", items=[{"required": list(required), "targets": list(targets)}],
                       available=list(available))
    return tuple(result[0])


def missing_required_capabilities(item: dict[str, Any], *, available_capabilities: Any) -> list[str]:
    required = tuple(normalize_required_capabilities(item.get("required_capabilities")))
    if not required:
        return []
    return list(_missing(required, tuple(normalize_target_capabilities(item.get("target_capabilities"))),
                         tuple(normalize_required_capabilities(available_capabilities))))


def _capability_item_identity(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("todo_id") or ""),
        "" if item.get("todo_id") else str(item.get("text") or "").strip(),
    )


def _agent_lane_candidate_sort_key(
    raw_item: dict[str, Any],
    *,
    agent_id: str | None,
    preferred_todo_ids: set[str] | None = None,
    agent_profile: dict[str, Any] | None = None,
) -> tuple[int, int, int, int, int, int]:
    preferred_todo_ids = preferred_todo_ids or set()
    todo_id = str(raw_item.get("todo_id") or "").strip()
    active_next_rank = 0 if todo_id and todo_id in preferred_todo_ids else 1
    claimed_by = agent_scope_item_claimed_by(raw_item)
    claim_rank = 0 if agent_id and claimed_by == agent_id else 1
    repair_rank = 0 if raw_item.get("capability_repair_mode") is True else 1
    # Durable Next Action is a steering hint inside the selected peer/profile
    # priority bucket, not permission to cross an explicit todo priority boundary.
    return (
        claim_rank,
        agent_profile_candidate_rank(raw_item, agent_profile=agent_profile),
        todo_priority_rank(raw_item),
        active_next_rank,
        repair_rank,
        todo_index_rank(raw_item),
    )


def _select_advancement_candidate_source(
    agent_todo_summary: dict[str, Any],
) -> tuple[list[Any], str]:
    active_next_items = agent_todo_summary.get("active_next_action_executable_items")
    backlog_items = agent_todo_summary.get("executable_backlog_items")
    first_executable_items = agent_todo_summary.get("first_executable_items")
    if isinstance(active_next_items, list) and active_next_items:
        return (
            [
                *active_next_items,
                *(backlog_items if isinstance(backlog_items, list) else []),
            ],
            "agent_todo_summary.active_next_action_executable_items",
        )
    if isinstance(backlog_items, list):
        return backlog_items, "agent_todo_summary.executable_backlog_items"
    if isinstance(first_executable_items, list) and first_executable_items:
        return first_executable_items, "agent_todo_summary.first_executable_items"
    return [], "agent_todo_summary.executable_backlog_items"


def _collect_capability_gate_candidates(
    agent_todo_summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], str]:
    raw_items, source = _select_advancement_candidate_source(agent_todo_summary)
    raw_due_monitor_items = agent_todo_summary.get("monitor_due_items")
    due_monitor_items = (
        raw_due_monitor_items if isinstance(raw_due_monitor_items, list) else []
    )
    raw_blocked_due_monitor_items = agent_todo_summary.get(
        "monitor_capability_blocked_due_items"
    )
    blocked_due_monitor_items = (
        raw_blocked_due_monitor_items
        if isinstance(raw_blocked_due_monitor_items, list)
        else []
    )
    monitor_sources: list[str] = []
    if due_monitor_items:
        monitor_sources.append("agent_todo_summary.monitor_due_items")
    if blocked_due_monitor_items:
        monitor_sources.append(
            "agent_todo_summary.monitor_capability_blocked_due_items"
        )
    if monitor_sources:
        source_parts = [source, *monitor_sources] if raw_items else monitor_sources
        source = "+".join(source_parts)
        raw_items = [*raw_items, *due_monitor_items, *blocked_due_monitor_items]

    deduped_items: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        identity = _capability_item_identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        deduped_items.append(item)

    due_monitor_identities = {
        _capability_item_identity(item)
        for item in [*due_monitor_items, *blocked_due_monitor_items]
        if isinstance(item, dict)
    }
    return (
        [
            item
            for item in deduped_items
            if todo_item_is_actionable_open(item)
            and (
                todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
                or _capability_item_identity(item) in due_monitor_identities
            )
        ],
        source,
    )


def build_capability_gate(
    agent_todo_summary: dict[str, Any] | None, *,
    available_capabilities: list[str], agent_identity: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not isinstance(agent_todo_summary, dict):
        return None
    candidates, source = _collect_capability_gate_candidates(agent_todo_summary)
    if not candidates:
        return None
    identity = agent_identity if isinstance(agent_identity, dict) else {}
    agent = normalize_todo_claimed_by(identity.get("agent_id"))
    profile = identity.get("agent_profile")
    profile = profile if isinstance(profile, dict) else None
    policy = ("claim_then_profile_then_priority_then_active_next_then_repair" if profile else
              "claim_then_priority_then_active_next_then_repair") if agent else None
    displays = compact_todo_summary_items(candidates, strip_text=True)
    return _evaluate("project", source=source,
                     available=normalize_required_capabilities(available_capabilities),
                     candidate_order_policy=policy, candidates=[{
                         "payload": display,
                         "required": normalize_required_capabilities(item.get("required_capabilities")),
                         "targets": normalize_target_capabilities(item.get("target_capabilities")),
                         "rank": list(_agent_lane_candidate_sort_key(item, agent_id=agent, agent_profile=profile)),
                     } for item, display in zip(candidates, displays, strict=True)])
