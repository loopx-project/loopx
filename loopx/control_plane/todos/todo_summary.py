from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import re
from typing import Any, Callable, Optional

from ..goals.goal_vision_wait_projection import attach_active_vision_waits
from .contract import (
    TODO_STATUS_DONE,
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_ADVANCEMENT,
    TODO_TASK_CLASS_USER_ACTION,
    build_todo_id,
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_explore_result_node_refs,
    normalize_target_capabilities,
    normalize_todo_action_kind,
    normalize_todo_capability_binding_ref,
    normalize_todo_task_repository,
    normalize_todo_blocks_agent,
    normalize_todo_bound_agent,
    normalize_todo_claimed_by,
    normalize_todo_continuation_policy,
    normalize_todo_decision_outcome,
    normalize_todo_decision_scope,
    normalize_todo_decision_scope_outcomes,
    normalize_todo_excluded_agents,
    normalize_todo_global_gate,
    normalize_todo_generation,
    normalize_todo_goal_bound,
    normalize_todo_id,
    normalize_todo_no_followup,
    normalize_removed_todo_continuation_policy,
    normalize_todo_required_decision_scopes,
    normalize_todo_resume_when,
    normalize_todo_status,
    normalize_todo_task_domain,
    normalize_todo_task_class,
    todo_done_for_status,
)
from .completion_validation_projection import project_completion_validation_authority
from .frontier_revision import attach_advancement_frontier_revision_index
from .handoff_gate import build_todo_handoff_gate_states
from .handoff_note import attach_todo_handoff_note
from .todo_semantics import (
    todo_claimed_visibility_items as projection_todo_claimed_visibility_items,
    todo_item_is_actionable_open as projection_todo_item_is_actionable_open,
    todo_item_is_deferred as projection_todo_item_is_deferred,
    todo_item_is_due_monitor as projection_todo_item_is_due_monitor,
    todo_item_is_watch_only_monitor as projection_todo_item_is_watch_only_monitor,
    todo_item_missing_monitor_schedule as projection_todo_item_missing_monitor_schedule,
    todo_item_task_class as projection_todo_item_task_class,
    todo_item_next_due_at as projection_todo_item_next_due_at,
    todo_item_expires_at as projection_todo_item_expires_at,
    todo_priority_parts as projection_todo_priority_parts,
    todo_priority_rank as projection_todo_priority_rank,
    todo_presentation_sort_key as projection_todo_presentation_sort_key,
    todo_projection_sort_key as projection_todo_projection_sort_key,
)
from .succession_warning import (
    TODO_SUCCESSION_WARNING_REASON_CODE,
    TODO_SUCCESSION_WARNING_SCHEMA_VERSION,
)
from .resume_condition import evaluate_todo_resume_conditions
from ..runtime.time import now_utc, now_utc_iso
from ..work_items.project_asset import build_project_asset_todo_summary
from .user_gate import open_user_gate_todo_items
from ..coordination.coordination_state_contract import (
    TODO_CANONICAL_READ_RECORD_FIELDS,
    TODO_CANONICAL_READ_RECORD_SCHEMA_VERSION as TODO_CANONICAL_READ_RECORD_SCHEMA_VERSION,
    TODO_CANONICAL_REQUIRED_READ_FIELDS,
    TODO_ITEM_SCHEMA_VERSION,
    canonical_record_fields,
)


MAX_STATUS_TODOS_PER_ROLE = 12
MAX_PROJECT_ASSET_TODO_ITEMS = 3
MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS = 8
MAX_TODO_VISIBILITY_LANE_ITEMS = 16
MAX_DEFERRED_TODO_VISIBILITY_ITEMS = 8
MAX_MONITOR_DUE_ITEMS = 1
MAX_DEPENDENCY_BLOCKERS = 4
MAX_COMPLETED_SUCCESSION_WARNING_ITEMS = 5
MAX_RECENT_COMPLETED_ADVANCEMENT_ITEMS = MAX_TODO_VISIBILITY_LANE_ITEMS

TASK_ORCHESTRATION_AUTHORITY_SCHEMA_VERSION = "task_orchestration_authority_v0"
TODO_ARCHIVE_STATE_ACTIVE = "active"
AttentionItemBuilder = Callable[..., dict[str, Any]]
GoalLifecycleFields = Callable[[dict[str, Any], Optional[dict[str, Any]]], dict[str, Any]]
PublicSafeText = Callable[..., Optional[str]]
TodoOpenCount = Callable[[Optional[dict[str, Any]]], int]
FirstOpenTodoText = Callable[[Optional[dict[str, Any]]], Optional[str]]


@dataclass(frozen=True)
class _TodoGroupLanes:
    open_items: list[dict[str, Any]]
    terminal_items: list[dict[str, Any]]
    deferred_items: list[dict[str, Any]]
    done_items: list[dict[str, Any]]
    projected_open_items: list[dict[str, Any]]
    projected_deferred_items: list[dict[str, Any]]
    budgeted_items: list[dict[str, Any]]
    claimed_open_items: list[dict[str, Any]]
    unclaimed_open_items: list[dict[str, Any]]
    executable_items: list[dict[str, Any]]
    blocker_items: list[dict[str, Any]]
    resume_blocked_items: list[dict[str, Any]]
    monitor_items: list[dict[str, Any]]
    monitor_due_items: list[dict[str, Any]]
    watch_only_monitor_items: list[dict[str, Any]]
    watch_only_monitor_due_items: list[dict[str, Any]]
    non_watch_only_monitor_due_items: list[dict[str, Any]]
    convergent_open_items: list[dict[str, Any]]
    monitor_schedule_gap_items: list[dict[str, Any]]
    claimed_advancement_items: list[dict[str, Any]]
    claimed_monitor_items: list[dict[str, Any]]
    active_next_action_items: list[dict[str, Any]]
    active_next_action_executable_items: list[dict[str, Any]]
TASK_ORCHESTRATION_CANDIDATE_FIELDS = (
    "todo_id",
    "status",
    "done",
    "task_class",
    "action_kind",
    "task_domain",
    "task_repository",
    "required_write_scopes",
    "required_capabilities",
    "claimed_by",
    "excluded_agents",
    "resume_when",
    "resume_ready",
    "continuation_policy",
    "target_key",
    "completion_validation_required",
    "title",
    "text",
)
TASK_ORCHESTRATION_USER_BLOCKER_FIELDS = (
    "todo_id",
    "status",
    "done",
    "task_class",
    "unblocks_todo_id",
)


def normalize_todo_text(text: str, *, limit: int = 500) -> str:
    compact = " ".join(str(text or "").strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def todo_item_status(item: dict[str, Any]) -> str:
    """Return one Todo's explicit status with marker compatibility."""

    status = normalize_todo_status(item.get("status"))
    if status:
        return status
    return TODO_STATUS_DONE if item.get("done") else TODO_STATUS_OPEN


def todo_archive_state(item: dict[str, Any]) -> str:
    value = str(item.get("archive_state") or TODO_ARCHIVE_STATE_ACTIVE).strip()
    return value or TODO_ARCHIVE_STATE_ACTIVE


def active_state_todo_attention_item(
    goal: dict[str, Any],
    fields: dict[str, Any],
    current_run: dict[str, Any] | None,
    *,
    public_safe_compact_text: PublicSafeText,
    first_open_todo_text: FirstOpenTodoText,
    todo_summary_open_count: TodoOpenCount,
    goal_lifecycle_fields: GoalLifecycleFields,
    attention_item: AttentionItemBuilder,
) -> dict[str, Any] | None:
    """Surface active-state todos even when the latest run classification is passive."""

    user_todos = fields.get("user_todos") if isinstance(fields.get("user_todos"), dict) else None
    agent_todos = fields.get("agent_todos") if isinstance(fields.get("agent_todos"), dict) else None
    active_next_action = public_safe_compact_text(
        fields.get("active_state_next_action"),
        limit=320,
    )
    user_gate_items = open_user_gate_todo_items(user_todos)
    user_gate_action = public_safe_compact_text(
        user_gate_items[0].get("text") if user_gate_items else None,
        limit=320,
    )
    user_action = public_safe_compact_text(first_open_todo_text(user_todos), limit=320)
    agent_action = public_safe_compact_text(first_open_todo_text(agent_todos), limit=320)
    agent_has_open = bool(agent_action or todo_summary_open_count(agent_todos) > 0)
    lifecycle_fields = goal_lifecycle_fields(goal, current_run)
    goal_id = str(goal.get("id") or "unknown-goal")

    if user_gate_action or user_gate_items:
        return attention_item(
            goal_id=goal_id,
            status="active_state_user_gate",
            waiting_on="controller",
            severity="action",
            recommended_action=(
                user_gate_action
                or active_next_action
                or "resolve the open user_gate todo from the active goal state"
            ),
            source="active_state",
            **lifecycle_fields,
        )

    if user_action or todo_summary_open_count(user_todos) > 0:
        user_items = [
            item
            for item in (user_todos.get("first_open_items") if user_todos else []) or []
            if isinstance(item, dict) and item.get("done") is not True
        ]
        explicit_user_actions_only = bool(user_items) and all(
            str(item.get("task_class") or "").strip()
            and projection_todo_item_task_class(item) == TODO_TASK_CLASS_USER_ACTION
            for item in user_items
        )
        if not explicit_user_actions_only:
            return attention_item(
                goal_id=goal_id,
                status="active_state_user_todo",
                waiting_on="controller",
                severity="action",
                recommended_action=(
                    user_action
                    or active_next_action
                    or "resolve the open user todo from the active goal state"
                ),
                source="active_state",
                **lifecycle_fields,
            )

    if agent_has_open:
        return attention_item(
            goal_id=goal_id,
            status="active_state_agent_todo",
            waiting_on="codex",
            severity="action",
            recommended_action=(
                agent_action
                or active_next_action
                or "run the open agent todo from the active goal state"
            ),
            source="active_state",
            **lifecycle_fields,
        )

    projection_gap = fields.get("state_projection_gap")
    if isinstance(projection_gap, dict):
        return attention_item(
            goal_id=goal_id,
            status="state_projection_gap",
            waiting_on="codex",
            severity="action",
            recommended_action=str(
                projection_gap.get("recommended_action")
                or "expand the active-state Next Action into parseable todos"
            ),
            source="active_state",
            **lifecycle_fields,
        )

    return None


def sync_connected_attention_action_from_todos(
    item: dict[str, Any],
    *,
    first_open_todo_text: FirstOpenTodoText,
) -> None:
    if item.get("status") != "connected_without_run":
        return
    agent_lane_action = (
        item.get("agent_lane_next_action")
        if isinstance(item.get("agent_lane_next_action"), dict)
        else None
    )
    if agent_lane_action is None and isinstance(item.get("project_asset"), dict):
        project_asset = item["project_asset"]
        agent_lane_action = (
            project_asset.get("agent_lane_next_action")
            if isinstance(project_asset.get("agent_lane_next_action"), dict)
            else None
        )
    agent_action = normalize_todo_text(agent_lane_action.get("text")) if agent_lane_action else None
    if not agent_action:
        agent_action = first_open_todo_text(
            item.get("agent_todos") if isinstance(item.get("agent_todos"), dict) else None
        )
    if not agent_action:
        return
    item["recommended_action"] = agent_action
    project_asset = item.get("project_asset")
    if isinstance(project_asset, dict):
        project_asset["next_action"] = agent_action


def todo_priority_parts(text: str) -> tuple[str | None, str]:
    return projection_todo_priority_parts(text)


def structured_todo_item(
    item: dict[str, Any],
    *,
    role: str | None,
    source_section: str | None,
    archive_state: str = "active",
) -> dict[str, Any]:
    text = normalize_todo_text(str(item.get("text") or ""))
    priority, title = todo_priority_parts(text)
    index = item.get("index")
    explicit_status = normalize_todo_status(item.get("status"))
    status = explicit_status or ("done" if item.get("done") else "open")
    done = todo_done_for_status(status) if explicit_status else bool(item.get("done"))
    todo_id = item.get("todo_id") or build_todo_id(
        role=role,
        source_section=source_section,
        index=index,
        text=text,
    )
    normalized = project_completion_validation_authority(item)
    normalized.update(
        {
            "schema_version": TODO_ITEM_SCHEMA_VERSION,
            "todo_id": todo_id,
            "role": role,
            "status": status,
            "done": done,
            "archive_state": archive_state,
            "source_section": source_section,
            "text": text,
            "task_class": normalize_todo_task_class(
                item.get("task_class"),
                text=text,
                action_kind=item.get("action_kind"),
            ),
        }
    )
    action_kind = normalize_todo_action_kind(item.get("action_kind"))
    if action_kind:
        normalized["action_kind"] = action_kind
    task_domain = normalize_todo_task_domain(item.get("task_domain"))
    if task_domain:
        normalized["task_domain"] = task_domain
    capability_binding_ref = normalize_todo_capability_binding_ref(
        item.get("capability_binding_ref")
    )
    if capability_binding_ref:
        normalized["capability_binding_ref"] = capability_binding_ref
    task_repository = normalize_todo_task_repository(item.get("task_repository"))
    if task_repository:
        normalized["task_repository"] = task_repository
    continuation_policy = normalize_todo_continuation_policy(
        item.get("continuation_policy")
    )
    if continuation_policy:
        normalized["continuation_policy"] = continuation_policy
    removed_continuation_policy = normalize_removed_todo_continuation_policy(
        item.get("removed_continuation_policy")
    )
    if removed_continuation_policy:
        normalized["removed_continuation_policy"] = removed_continuation_policy
    required_write_scopes = normalize_required_write_scopes(item.get("required_write_scopes"))
    if required_write_scopes:
        normalized["required_write_scopes"] = required_write_scopes
    required_capabilities = normalize_required_capabilities(item.get("required_capabilities"))
    if required_capabilities:
        normalized["required_capabilities"] = required_capabilities
    target_capabilities = normalize_target_capabilities(item.get("target_capabilities"))
    if target_capabilities:
        normalized["target_capabilities"] = target_capabilities
    explore_result_node_refs = normalize_explore_result_node_refs(
        item.get("explore_result_node_refs")
    )
    if explore_result_node_refs:
        normalized["explore_result_node_refs"] = explore_result_node_refs
    decision_scope = normalize_todo_decision_scope(item.get("decision_scope"))
    if decision_scope:
        normalized["decision_scope"] = decision_scope
    required_decision_scopes = normalize_todo_required_decision_scopes(
        item.get("required_decision_scopes")
    )
    if required_decision_scopes:
        normalized["required_decision_scopes"] = required_decision_scopes
    decision_outcome = normalize_todo_decision_outcome(item.get("decision_outcome"))
    if decision_outcome:
        normalized["decision_outcome"] = decision_outcome
    decision_scope_outcomes = normalize_todo_decision_scope_outcomes(
        item.get("decision_scope_outcomes")
    )
    if decision_scope_outcomes:
        normalized["decision_scope_outcomes"] = decision_scope_outcomes
    claimed_by = normalize_todo_claimed_by(item.get("claimed_by"))
    if claimed_by:
        normalized["claimed_by"] = claimed_by
    bound_agent = normalize_todo_bound_agent(item.get("bound_agent"))
    if bound_agent:
        normalized["bound_agent"] = bound_agent
    goal_bound = normalize_todo_goal_bound(item.get("goal_bound"))
    if goal_bound is not None:
        normalized["goal_bound"] = goal_bound
    blocks_agent = normalize_todo_blocks_agent(item.get("blocks_agent"))
    if blocks_agent:
        normalized["blocks_agent"] = blocks_agent
    excluded_agents = normalize_todo_excluded_agents(item.get("excluded_agents"))
    if excluded_agents:
        normalized["excluded_agents"] = excluded_agents
    global_gate = normalize_todo_global_gate(item.get("global_gate"))
    if global_gate is not None:
        normalized["global_gate"] = global_gate
    unblocks_todo_id = normalize_todo_id(item.get("unblocks_todo_id"))
    if unblocks_todo_id:
        normalized["unblocks_todo_id"] = unblocks_todo_id
    resume_when = normalize_todo_resume_when(item.get("resume_when"))
    if resume_when:
        normalized["resume_when"] = resume_when
    resume_monitor_generation = normalize_todo_generation(
        item.get("resume_monitor_generation")
    )
    if resume_monitor_generation is not None:
        normalized["resume_monitor_generation"] = resume_monitor_generation
    material_change_generation = normalize_todo_generation(
        item.get("material_change_generation")
    )
    if material_change_generation is not None:
        normalized["material_change_generation"] = material_change_generation
    no_followup = normalize_todo_no_followup(item.get("no_followup"))
    if no_followup is not None:
        normalized["no_followup"] = no_followup
    if priority:
        normalized["priority"] = priority
        normalized["title"] = normalize_todo_text(title)
    return normalized


def compact_todo_item(item: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {
        "index": item.get("index"),
        "done": bool(item.get("done")),
        "text": item.get("text"),
    }
    for key in TODO_CANONICAL_READ_RECORD_FIELDS:
        if key in compact:
            continue
        if item.get(key) is not None:
            compact[key] = item.get(key)
    if isinstance(item.get("goal_acceptance_guard"), dict):
        compact["goal_acceptance_guard"] = item["goal_acceptance_guard"]
    attach_todo_handoff_note(compact)
    return compact


def canonical_todo_read_record(
    item: dict[str, Any],
    *,
    reject_unknown: bool = False,
) -> dict[str, Any]:
    """Copy one already-normalized Todo consumer record without re-deriving it."""

    # Read-policy evaluations are recomputed from a complete source. They must
    # never enter shadow capture, provider records or the durable source digest.
    record = canonical_record_fields(
        {key: value for key, value in item.items() if key != "succession_evaluation"},
        fields=TODO_CANONICAL_READ_RECORD_FIELDS,
        required_fields=TODO_CANONICAL_REQUIRED_READ_FIELDS,
        label="canonical Todo read record",
        reject_unknown=reject_unknown,
    )
    if (
        record["schema_version"] != TODO_ITEM_SCHEMA_VERSION
        or not isinstance(record["role"], str)
        or record["role"] not in {"user", "agent"}
        or not isinstance(record["status"], str)
        or not record["status"]
        or not isinstance(record["done"], bool)
        or not isinstance(record["text"], str)
        or not isinstance(record["archive_state"], str)
        or not record["archive_state"]
        or not isinstance(record["source_section"], str)
        or not record["source_section"]
    ):
        raise ValueError("canonical Todo read record has invalid required semantics")
    return record


def _task_orchestration_authority(
    lanes: _TodoGroupLanes,
    *,
    role: str | None,
) -> dict[str, Any]:
    candidate_items = (
        [
            {
                key: compact[key]
                for key in TASK_ORCHESTRATION_CANDIDATE_FIELDS
                if key in compact
            }
            for item in lanes.projected_open_items
            if todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
            for compact in [compact_todo_item(item)]
        ]
        if role == "agent"
        else []
    )
    user_blocker_items = (
        [
            {
                key: compact[key]
                for key in TASK_ORCHESTRATION_USER_BLOCKER_FIELDS
                if key in compact
            }
            for item in lanes.projected_open_items
            if normalize_todo_id(item.get("unblocks_todo_id"))
            for compact in [compact_todo_item(item)]
        ]
        if role == "user"
        else []
    )
    return {
        "schema_version": TASK_ORCHESTRATION_AUTHORITY_SCHEMA_VERSION,
        "role": role,
        "candidate_items": candidate_items,
        "user_blocker_items": user_blocker_items,
    }


def compact_active_next_action_todo_item(item: dict[str, Any]) -> dict[str, Any]:
    compact = compact_todo_item(item)
    for key in (
        "note",
        "evidence",
        "reason",
        "completed_at",
        "updated_at",
        "superseded_by",
    ):
        compact.pop(key, None)
    return compact


def todo_item_task_class(item: dict[str, Any]) -> str:
    return projection_todo_item_task_class(item)


def count_advancement_todos(items: list[dict[str, Any]]) -> int:
    return sum(
        1 for item in items if todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
    )


def todo_item_is_actionable_open(item: dict[str, Any]) -> bool:
    return projection_todo_item_is_actionable_open(item)


def todo_item_next_due_at(item: dict[str, Any]) -> datetime | None:
    return projection_todo_item_next_due_at(item)


def todo_item_expires_at(item: dict[str, Any]) -> datetime | None:
    return projection_todo_item_expires_at(item)


def todo_item_is_due_monitor(
    item: dict[str, Any], *, now: datetime | None = None
) -> bool:
    return projection_todo_item_is_due_monitor(item, now=now, task_text_keys=("text",))


def todo_item_missing_monitor_schedule(
    item: dict[str, Any], *, now: datetime | None = None
) -> bool:
    return projection_todo_item_missing_monitor_schedule(item, now=now, task_text_keys=("text",))


def todo_priority_rank(priority: Any) -> int:
    return projection_todo_priority_rank(priority)


def todo_projection_sort_key(item: dict[str, Any]) -> tuple[int, int]:
    return projection_todo_projection_sort_key(item, text_mode="prefix")


def claimed_visibility_items(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return projection_todo_claimed_visibility_items(items, limit=limit)


def todo_item_is_deferred(item: dict[str, Any]) -> bool:
    return projection_todo_item_is_deferred(item)


def open_todo_items(
    todos: dict[str, Any] | None,
    *,
    limit: int = MAX_PROJECT_ASSET_TODO_ITEMS,
    text_limit: int = 220,
    source_keys: tuple[str, ...] = ("first_open_items", "items"),
) -> list[dict[str, Any]]:
    if not isinstance(todos, dict):
        return []
    result: list[dict[str, Any]] = []
    seen: set[tuple[Any, str]] = set()
    for source_key in source_keys:
        source_items = todos.get(source_key)
        if not isinstance(source_items, list):
            continue
        for item in source_items:
            if not isinstance(item, dict) or item.get("done"):
                continue
            text = normalize_todo_text(str(item.get("text") or ""), limit=text_limit)
            if not text:
                continue
            key = (item.get("index"), text)
            if key in seen:
                continue
            seen.add(key)
            compact = compact_todo_item(item)
            compact["done"] = False
            compact["text"] = text
            result.append(compact)
            if len(result) >= limit:
                return sorted(result, key=projection_todo_presentation_sort_key)
    return sorted(result, key=projection_todo_presentation_sort_key)


def todo_lane_items(
    todos: dict[str, Any] | None,
    lane: str,
    *,
    limit: int = MAX_STATUS_TODOS_PER_ROLE,
    text_limit: int = 220,
) -> list[dict[str, Any]]:
    return open_todo_items(
        todos,
        limit=limit,
        text_limit=text_limit,
        source_keys=(lane,),
    )


def first_open_todo_text(
    todos: dict[str, Any] | None,
    *,
    item_limit: int = 220,
) -> str | None:
    items = open_todo_items(todos, limit=1, text_limit=item_limit)
    if not items:
        return None
    return str(items[0].get("text") or "") or None


def first_open_todo_item(
    todos: dict[str, Any] | None,
    *,
    item_limit: int = MAX_PROJECT_ASSET_TODO_ITEMS,
    text_limit: int = 220,
) -> dict[str, Any] | None:
    for todo in open_todo_items(todos, limit=item_limit, text_limit=text_limit):
        if not isinstance(todo, dict) or todo.get("done"):
            continue
        return todo
    return None


def project_asset_todo_summary(
    todos: dict[str, Any] | None,
    *,
    role: str | None = None,
    item_limit: int = MAX_PROJECT_ASSET_TODO_ITEMS,
    deferred_item_limit: int = MAX_DEFERRED_TODO_VISIBILITY_ITEMS,
    advancement_task_class: str = TODO_TASK_CLASS_ADVANCEMENT,
) -> dict[str, Any] | None:
    return build_project_asset_todo_summary(
        todos,
        role=role,
        item_limit=item_limit,
        deferred_item_limit=deferred_item_limit,
        advancement_task_class=advancement_task_class,
        open_todo_items=lambda value, **kwargs: open_todo_items(
            value,
            limit=kwargs.get("limit", item_limit),
            text_limit=kwargs.get("text_limit", 220),
            source_keys=kwargs.get("source_keys", ("first_open_items", "items")),
        ),
        compact_todo_item=compact_todo_item,
        todo_lane_items=lambda value, lane, **kwargs: todo_lane_items(
            value,
            lane,
            limit=kwargs.get("limit", MAX_STATUS_TODOS_PER_ROLE),
            text_limit=kwargs.get("text_limit", 220),
        ),
        todo_item_is_actionable_open=todo_item_is_actionable_open,
        todo_item_task_class=todo_item_task_class,
    )


def dependency_blocker_summary(
    items: list[dict[str, Any]],
    *,
    current_goal_id: str,
    limit: int = MAX_DEPENDENCY_BLOCKERS,
) -> dict[str, Any] | None:
    blockers: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        goal_id = str(item.get("goal_id") or "")
        if not goal_id or goal_id == current_goal_id:
            continue
        raw_user_todos = item.get("user_todos")
        user_todos = raw_user_todos if isinstance(raw_user_todos, dict) else {}
        for todo in user_todos.get("items") or []:
            if not isinstance(todo, dict) or todo.get("done"):
                continue
            text = normalize_todo_text(str(todo.get("text") or ""), limit=220)
            if not text:
                continue
            blockers.append(
                {
                    "goal_id": goal_id,
                    "status": item.get("status"),
                    "waiting_on": item.get("waiting_on"),
                    "severity": item.get("severity"),
                    "index": todo.get("index"),
                    "text": text,
                    "source": "user_todos",
                }
            )
    if not blockers:
        return None
    return {
        "source": "attention_queue.user_todos",
        "open_count": len(blockers),
        "items": blockers[:limit],
    }


def attach_dependency_blockers(
    items: list[dict[str, Any]],
    *,
    limit: int = MAX_DEPENDENCY_BLOCKERS,
) -> None:
    for item in items:
        if not isinstance(item, dict):
            continue
        goal_id = str(item.get("goal_id") or "")
        if not goal_id:
            continue
        blockers = dependency_blocker_summary(items, current_goal_id=goal_id, limit=limit)
        if blockers:
            item["dependency_blockers"] = blockers


def apply_resume_conditions(
    items: list[dict[str, Any]],
    *,
    resume_source_items: list[dict[str, Any]] | None = None,
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
    evaluated_at: str | None = None,
) -> None:
    resume_items = [
        item
        for item in items
        if normalize_todo_resume_when(item.get("resume_when"))
    ]
    if not resume_items:
        return
    source_items = [*(resume_source_items or []), *items]
    conditions = evaluate_todo_resume_conditions(
        resume_items,
        source_items=source_items,
        rollout_events=rollout_events,
        available_capabilities=available_capabilities,
        evaluated_at=evaluated_at or now_utc_iso(),
    )
    for item in items:
        resume_when = normalize_todo_resume_when(item.get("resume_when"))
        if not resume_when:
            continue
        todo_id = normalize_todo_id(item.get("todo_id"))
        condition = conditions.get(todo_id or "")
        if condition is None:
            condition = {
                "schema_version": "todo_resume_condition_v0",
                "resume_when": resume_when,
                "satisfied": False,
                "unsupported": True,
            }
        item["resume_condition"] = condition
        item["resume_ready"] = bool(condition.get("satisfied"))


def active_next_action_todo_ids(value: Any) -> set[str]:
    todo_ids: set[str] = set()
    for match in re.findall(r"\btodo_[A-Za-z0-9_-]+\b", str(value or "")):
        todo_id = normalize_todo_id(match)
        if todo_id:
            todo_ids.add(todo_id)
    return todo_ids


def todo_successor_todo_ids(item: dict[str, Any], *, items: list[dict[str, Any]]) -> list[str]:
    """Compatibility call site for repair-delta; the typed graph owns links."""
    from .succession_warning import evaluate_succession

    selected = dict(item)
    evaluation = evaluate_succession([selected], items)[0]
    return list(evaluation["successor_todo_ids"])


def todo_item_is_succession_tracked_completion(item: dict[str, Any]) -> bool:
    from .succession_warning import project_succession

    return project_succession([item])[0]["tracked_completion"] is True


def _completed_succession_sort_key(item: dict[str, Any]) -> tuple[str, int]:
    raw_index = item.get("index")
    try:
        index = int(raw_index) if raw_index is not None else 0
    except (TypeError, ValueError):
        index = 0
    timestamp = str(item.get("updated_at") or item.get("completed_at") or "")
    return (timestamp, index)


def completed_without_successor_items(
    items: list[dict[str, Any]], *, evaluations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    gap_items = []
    for item, evaluation in zip(items, evaluations, strict=True):
        if not evaluation["successor_gap"]:
            continue
        compact = compact_todo_item(item)
        for key in ("note", "evidence", "reason"):
            compact.pop(key, None)
        compact["succession_tracked"] = True
        compact["recommended_action"] = "record no_followup=true or add/link a successor todo"
        gap_items.append(compact)
    return sorted(gap_items, key=_completed_succession_sort_key, reverse=True)


def _structured_todo_group_items(
    items: list[dict[str, Any]],
    *,
    source_section: str | None,
    role: str | None,
) -> list[dict[str, Any]]:
    return [
        structured_todo_item(
            item,
            role=role,
            source_section=source_section,
            archive_state=todo_archive_state(item),
        )
        if isinstance(item, dict)
        else item
        for item in items
    ]


def _structured_resume_source_items(
    items: list[dict[str, Any]] | None,
    *,
    source_section: str | None,
) -> list[dict[str, Any]]:
    return [
        structured_todo_item(
            item,
            role=item.get("role") if isinstance(item.get("role"), str) else None,
            source_section=(
                item.get("source_section")
                if isinstance(item.get("source_section"), str)
                else source_section
            ),
            archive_state=(
                str(item.get("archive_state"))
                if item.get("archive_state") is not None
                else TODO_ARCHIVE_STATE_ACTIVE
            ),
        )
        for item in (items or [])
        if isinstance(item, dict)
    ]


def _project_summary_lanes(items: list[dict[str, Any]], preferred_todo_ids: set[str] | None) -> dict[str, Any]:
    """Adapt legacy facts and read batch ordinals from the typed lane owner."""
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    rows = []
    for item in items:
        resume = normalize_todo_resume_when(item.get("resume_when"))
        condition = item.get("resume_condition")
        evaluated = (isinstance(condition, dict)
            and condition.get("schema_version") == "todo_resume_condition_v0"
            and condition.get("resume_when") == resume
            and isinstance(condition.get("satisfied"), bool)
            and item.get("resume_ready") is condition.get("satisfied"))
        due = projection_todo_item_next_due_at(item)
        expires = projection_todo_item_expires_at(item)
        guard = item.get("goal_acceptance_guard")
        rows.append({"status": item.get("status") or ("done" if item.get("done") else "open"),
            "done": bool(item.get("done")), "task_class": projection_todo_item_task_class(item),
            "has_resume": bool(resume), "resume_ready": item.get("resume_ready"),
            "resume_evaluated": evaluated, "acceptance_blocked": isinstance(guard, dict) and guard.get("allowed") is False,
            "claimed": bool(item.get("claimed_by")), "preferred": item.get("todo_id") in (preferred_todo_ids or set()),
            "watch_only": projection_todo_item_is_watch_only_monitor(item),
            "due_at": due.timestamp() if due else None, "expires_at": expires.timestamp() if expires else None,
            "sort": list(projection_todo_presentation_sort_key(item))})
    try:
        result = effect_runtime_result("todo.summary_lanes.project", {
            "schema_version": "todo_summary_lanes_request_v0", "rows": rows, "observed_at": now_utc().timestamp(),
        })
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(result, dict) or result.get("schema_version") != "todo_summary_lanes_v0":
        raise ValueError("invalid typed Todo summary lanes")
    lanes = result["lanes"]
    if not isinstance(lanes, dict) or any(
        not isinstance(indices, list) or any(type(index) is not int or not 0 <= index < len(items) for index in indices)
        for indices in lanes.values()
    ):
        raise ValueError("invalid Todo summary source ordinal")
    return {"lanes": {key: [items[index] for index in indices] for key, indices in lanes.items()},
            "work_counts": result["work_counts"]}


def compact_todo_group(
    items: list[dict[str, Any]],
    *,
    source_section: str | None,
    role: str | None = None,
    include_empty_source: bool = False,
    preferred_todo_ids: set[str] | None = None,
    resume_source_items: list[dict[str, Any]] | None = None,
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
    item_limit: int | None = MAX_STATUS_TODOS_PER_ROLE,
    include_task_orchestration_authority: bool = False,
    vision_runs: list[dict[str, Any]] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any] | None:
    if not items and not include_empty_source:
        return None
    items = _structured_todo_group_items(
        items,
        source_section=source_section,
        role=role,
    )
    apply_resume_conditions(
        items,
        resume_source_items=_structured_resume_source_items(
            resume_source_items,
            source_section=source_section,
        ),
        rollout_events=rollout_events,
        available_capabilities=available_capabilities,
        evaluated_at=evaluated_at,
    )
    from .succession_warning import evaluate_succession

    evaluate_succession(items, resume_source_items)
    return compact_evaluated_todo_group(
        items, source_section=source_section, role=role,
        include_empty_source=include_empty_source, preferred_todo_ids=preferred_todo_ids,
        item_limit=item_limit, include_task_orchestration_authority=include_task_orchestration_authority,
        vision_runs=vision_runs, lineage_items=resume_source_items,
    )



def compact_evaluated_todo_group(
    items: list[dict[str, Any]],
    *,
    source_section: str | None,
    role: str | None = None,
    include_empty_source: bool = False,
    preferred_todo_ids: set[str] | None = None,
    item_limit: int | None = MAX_STATUS_TODOS_PER_ROLE,
    include_task_orchestration_authority: bool = False,
    vision_runs: list[dict[str, Any]] | None = None,
    lineage_items: list[dict[str, Any]] | None = None,
    full_selection: bool = True,
) -> dict[str, Any] | None:
    """Filter/display an already evaluated snapshot, never re-evaluate topology.

    Callers must come from the full-source parser or canonical summary, not
    persisted derived fields. Missing or mismatched evaluations fail closed.
    """
    if not items and not include_empty_source:
        return None
    projected = _project_summary_lanes(items, preferred_todo_ids)
    lanes = _TodoGroupLanes(**projected["lanes"])
    from .succession_warning import project_succession

    succession = project_succession(items, reuse=True)
    successor_gap_items = completed_without_successor_items(items, evaluations=succession)
    recent_completed_advancement_items = [
        compact_todo_item(item)
        for item in sorted(
            (
                item
                for item in lanes.done_items
                if todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
                and str(item.get("completed_at") or "").strip()
            ),
            key=_completed_succession_sort_key,
            reverse=True,
        )[:MAX_RECENT_COMPLETED_ADVANCEMENT_ITEMS]
    ]
    for item in recent_completed_advancement_items:
        for key in ("note", "evidence", "reason"):
            item.pop(key, None)
    handoff_gates = build_todo_handoff_gate_states(items, evaluations=succession)
    watch_only_monitor_items = lanes.watch_only_monitor_items
    watch_only_monitor_due_items = lanes.watch_only_monitor_due_items
    convergent_open_items = lanes.convergent_open_items
    summary: dict[str, Any] = {
        "schema_version": "todo_summary_v0",
        "source_section": source_section,
        "total_count": len(items),
        "work_counts": projected["work_counts"],
        "open_count": len(lanes.open_items),
        "done_count": len(lanes.terminal_items),
        "advancement_done_count": count_advancement_todos(lanes.done_items),
        "deferred_count": len(lanes.deferred_items),
        "first_open_items": [
            compact_todo_item(item) for item in lanes.projected_open_items[:3]
        ],
        "first_executable_items": [
            compact_todo_item(item) for item in lanes.executable_items[:3]
        ],
        "monitor_open_items": [
            compact_todo_item(item) for item in lanes.monitor_items
        ],
        "monitor_due_count": len(lanes.monitor_due_items),
        "monitor_due_items": [
            compact_todo_item(item)
            for item in lanes.monitor_due_items[:MAX_MONITOR_DUE_ITEMS]
        ],
        "monitor_schedule_gap_count": len(lanes.monitor_schedule_gap_items),
        "monitor_schedule_gap_items": [
            compact_todo_item(item)
            for item in lanes.monitor_schedule_gap_items[:MAX_MONITOR_DUE_ITEMS]
        ],
        "unclaimed_priority_open_items": [
            compact_todo_item(item)
            for item in lanes.unclaimed_open_items[:MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS]
        ],
        "claimed_open_items": [
            compact_todo_item(item)
            for item in claimed_visibility_items(
                lanes.claimed_open_items,
                limit=MAX_TODO_VISIBILITY_LANE_ITEMS,
            )
        ],
        "claimed_advancement_open_items": [
            compact_todo_item(item)
            for item in claimed_visibility_items(
                lanes.claimed_advancement_items,
                limit=MAX_TODO_VISIBILITY_LANE_ITEMS,
            )
        ],
        "claimed_monitor_open_items": [
            compact_todo_item(item)
            for item in claimed_visibility_items(
                lanes.claimed_monitor_items,
                limit=MAX_TODO_VISIBILITY_LANE_ITEMS,
            )
        ],
        "backlog_items": [
            compact_todo_item(item)
            for item in lanes.projected_open_items[:MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS]
        ],
        "executable_backlog_items": [
            compact_todo_item(item)
            for item in lanes.executable_items[:MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS]
        ],
        "deferred_items": [
            compact_todo_item(item)
            for item in lanes.projected_deferred_items[:MAX_DEFERRED_TODO_VISIBILITY_ITEMS]
        ],
        "deferred_resume_candidates": [
            compact_todo_item(item)
            for item in lanes.projected_deferred_items
            if item.get("resume_ready") is True
        ][:MAX_DEFERRED_TODO_VISIBILITY_ITEMS],
        "items": lanes.budgeted_items if item_limit is None else lanes.budgeted_items[:item_limit],
    }
    attach_advancement_frontier_revision_index(summary, items, role=role)
    attach_active_vision_waits(
        summary, vision_runs, role=role, items=items,
        lineage_items=lineage_items,
    )
    if watch_only_monitor_items:
        summary["watch_only_monitor_count"] = len(watch_only_monitor_items)
        summary["watch_only_monitor_due_count"] = len(watch_only_monitor_due_items)
        summary["convergence_open_count"] = len(convergent_open_items)
    if recent_completed_advancement_items:
        summary["recent_completed_advancement_items"] = recent_completed_advancement_items
    if include_task_orchestration_authority:
        summary["task_orchestration_authority"] = _task_orchestration_authority(
            lanes,
            role=role,
        )
    if lanes.blocker_items:
        summary["blocker_open_count"] = len(lanes.blocker_items)
        summary["blocker_items"] = [
            compact_todo_item(item) for item in lanes.blocker_items
        ]
    from ..effect_runtime import effect_runtime_result

    replan_gates = {gate.get("todo_id") for gate in handoff_gates
        if gate.get("route_continuation_replan_required") is True}
    closure = effect_runtime_result("todo.succession.closure", {
        "schema_version": "todo_closure_request_v0", "role": role,
        "source_section": source_section, "full_selection": full_selection,
        "rows": [{"status": item.get("status") or ("done" if item.get("done") else "open"),
            "watch_only": projection_todo_item_is_watch_only_monitor(item),
            "no_followup": normalize_todo_no_followup(item.get("no_followup")) is True,
            "successor_gap": evaluation["successor_gap"], "handoff_state": evaluation["handoff_state"],
            "replan": item.get("route_continuation_replan_required") is True or item.get("todo_id") in replan_gates}
            for item, evaluation in zip(items, succession, strict=True)],
    })
    if not isinstance(closure, dict):
        raise ValueError("invalid typed Todo closure projection")
    summary.update(closure)
    if lanes.resume_blocked_items:
        summary["resume_blocked_count"] = len(lanes.resume_blocked_items)
        summary["resume_blocked_items"] = [
            compact_todo_item(item)
            for item in lanes.resume_blocked_items[:MAX_DEFERRED_TODO_VISIBILITY_ITEMS]
        ]
    if handoff_gates:
        summary["handoff_gates"] = handoff_gates
    if successor_gap_items:
        compact_gap_items = successor_gap_items[:MAX_COMPLETED_SUCCESSION_WARNING_ITEMS]
        summary["completed_without_successor_count"] = len(successor_gap_items)
        summary["completed_without_successor_items"] = compact_gap_items
        summary["todo_succession_warning"] = {
            "schema_version": TODO_SUCCESSION_WARNING_SCHEMA_VERSION,
            "reason_code": TODO_SUCCESSION_WARNING_REASON_CODE,
            "count": len(successor_gap_items),
            "items": compact_gap_items,
            "recommended_action": (
                "run loopx todo complete --no-follow-up for the completed Todo, "
                "or add/link a successor Todo before closing the slice; do not "
                "invent a user gate"
            ),
        }
    if lanes.active_next_action_items:
        summary["active_next_action_items"] = [
            compact_active_next_action_todo_item(item)
            for item in lanes.active_next_action_items
        ]
    if lanes.active_next_action_executable_items:
        summary["active_next_action_executable_items"] = [
            compact_active_next_action_todo_item(item)
            for item in lanes.active_next_action_executable_items
        ]
    if lanes.claimed_open_items:
        summary["claimed_open_count"] = len(lanes.claimed_open_items)
        summary["unclaimed_open_count"] = len(lanes.open_items) - len(lanes.claimed_open_items)
        summary["claimed_advancement_open_count"] = len(lanes.claimed_advancement_items)
        summary["claimed_monitor_open_count"] = len(lanes.claimed_monitor_items)
    return summary
