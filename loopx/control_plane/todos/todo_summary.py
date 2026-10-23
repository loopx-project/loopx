from __future__ import annotations

from datetime import datetime
import re
from typing import Any, Callable, Optional

from ..goals.goal_vision_wait_projection import attach_active_vision_waits
from .contract import (
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
from .summary_item import todo_text_content_revision
from .completion_validation_projection import project_completion_validation_authority
from .handoff_note import attach_todo_handoff_notes
from .todo_semantics import (
    todo_item_is_actionable_open as projection_todo_item_is_actionable_open,
    todo_item_is_deferred as projection_todo_item_is_deferred,
    todo_item_is_due_monitor as projection_todo_item_is_due_monitor,
    todo_item_missing_monitor_schedule as projection_todo_item_missing_monitor_schedule,
    todo_item_task_class as projection_todo_item_task_class,
    todo_item_next_due_at as projection_todo_item_next_due_at,
    todo_item_expires_at as projection_todo_item_expires_at,
    todo_priority_parts as projection_todo_priority_parts,
    todo_priority_rank as projection_todo_priority_rank,
    todo_presentation_sort_key as projection_todo_presentation_sort_key,
    todo_projection_sort_key as projection_todo_projection_sort_key,
)
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

TASK_ORCHESTRATION_AUTHORITY_SCHEMA_VERSION = "task_orchestration_authority_v0"
TODO_ARCHIVE_STATE_ACTIVE = "active"

AttentionItemBuilder = Callable[..., dict[str, Any]]
GoalLifecycleFields = Callable[[dict[str, Any], Optional[dict[str, Any]]], dict[str, Any]]
PublicSafeText = Callable[..., Optional[str]]
TodoOpenCount = Callable[[Optional[dict[str, Any]]], int]
FirstOpenTodoText = Callable[[Optional[dict[str, Any]]], Optional[str]]


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


def normalize_todo_text(text: str, *, limit: int | None = 500) -> str:
    """Normalize whitespace; source codecs explicitly opt out of display limits."""
    compact = " ".join(str(text or "").strip().split())
    if limit is None or len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def _structured_todo_text_fields(
    item: dict[str, Any],
    *,
    text_limit: int | None,
    include_content_revision: bool,
) -> tuple[str, str | None]:
    source_text = str(item.get("text") or "")
    content_revision = (
        todo_text_content_revision(source_text)
        if include_content_revision else None
    )
    return normalize_todo_text(source_text, limit=text_limit), content_revision


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

    state_incomplete = fields.get("state_incomplete")
    if isinstance(state_incomplete, dict):
        item = attention_item(
            goal_id=goal_id,
            status="state_incomplete",
            waiting_on="user_or_controller",
            severity="high",
            recommended_action=str(
                state_incomplete.get("recommended_action")
                or "restore the missing active-state Agent Todo section"
            ),
            source="active_state",
            **lifecycle_fields,
        )
        item["state_incomplete"] = state_incomplete
        return item

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


def _structured_todo_agent_fields(item: dict[str, Any]) -> dict[str, str]:
    """Keep request authorship, execution claim and recipient as distinct actors."""
    actors = {}
    for field, normalize in (("created_by", normalize_todo_claimed_by),
                             ("claimed_by", normalize_todo_claimed_by),
                             ("bound_agent", normalize_todo_bound_agent)):
        actor = normalize(item.get(field))
        if actor:
            actors[field] = actor
    return actors


def structured_todo_item(
    item: dict[str, Any],
    *,
    role: str | None,
    source_section: str | None,
    archive_state: str = "active",
    text_limit: int | None = 500,
    include_content_revision: bool = False,
) -> dict[str, Any]:
    text, content_revision = _structured_todo_text_fields(
        item,
        text_limit=text_limit,
        include_content_revision=include_content_revision,
    )
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
    if content_revision:
        normalized["content_revision"] = content_revision
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
    normalized.update(_structured_todo_agent_fields(item))
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
        normalized["title"] = normalize_todo_text(title, limit=text_limit)
    return normalized


def _compact_todo_item_record(item: dict[str, Any]) -> dict[str, Any]:
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
    if item.get("content_revision"):
        compact["content_revision"] = item["content_revision"]
    if isinstance(item.get("goal_acceptance_guard"), dict):
        compact["goal_acceptance_guard"] = item["goal_acceptance_guard"]
    return compact


def compact_todo_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return attach_todo_handoff_notes([_compact_todo_item_record(item) for item in items])


def compact_todo_item(item: dict[str, Any]) -> dict[str, Any]:
    """Singleton adapter; whole-source consumers use the batch owner."""
    return compact_todo_items([item])[0]


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


def _task_orchestration_authority(lanes: dict[str, list[dict[str, Any]]], *, role: str | None) -> dict[str, Any]:
    """Materialize the typed selection using the existing public field allowlist."""
    return {"schema_version": TASK_ORCHESTRATION_AUTHORITY_SCHEMA_VERSION, "role": role,
        **{name: [{key: compact[key] for key in fields if key in compact}
                  for compact in lanes[name]]
           for name, fields in (("candidate_items", TASK_ORCHESTRATION_CANDIDATE_FIELDS),
                                ("user_blocker_items", TASK_ORCHESTRATION_USER_BLOCKER_FIELDS))}}


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
            compact = _compact_todo_item_record(item)
            compact["done"] = False
            compact["text"] = text
            result.append(compact)
            if len(result) >= limit:
                return sorted(attach_todo_handoff_notes(result), key=projection_todo_presentation_sort_key)
    return sorted(attach_todo_handoff_notes(result), key=projection_todo_presentation_sort_key)


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


def _structured_todo_group_items(
    items: list[dict[str, Any]],
    *,
    source_section: str | None,
    role: str | None,
    text_limit: int | None,
    include_content_revision: bool,
) -> list[dict[str, Any]]:
    return [
        structured_todo_item(
            item,
            role=role,
            source_section=source_section,
            archive_state=todo_archive_state(item),
            text_limit=text_limit,
            include_content_revision=include_content_revision,
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
            include_content_revision=True,
        )
        for item in (items or [])
        if isinstance(item, dict)
    ]








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
    text_limit: int | None = 500,
    include_content_revision: bool = True,
    include_task_orchestration_authority: bool = False,
    vision_runs: list[dict[str, Any]] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, Any] | None:
    from .goal_todo_projection import project_todo_read_group

    if not items and not include_empty_source:
        return None
    items = _structured_todo_group_items(items, source_section=source_section, role=role,
        text_limit=text_limit, include_content_revision=include_content_revision)
    resume_lineage = (_structured_resume_source_items(resume_source_items, source_section=source_section)
        if any(normalize_todo_resume_when(item.get("resume_when")) for item in items) else None)
    projected = project_todo_read_group(items, source_section=source_section, role=role,
        preferred_todo_ids=preferred_todo_ids, lineage=resume_source_items,
        resume_lineage=resume_lineage, item_limit=item_limit,
        rollout_events=rollout_events, available_capabilities=available_capabilities,
        evaluated_at=evaluated_at, include_full_source=bool(vision_runs),
        include_task_orchestration_authority=include_task_orchestration_authority)
    items = projected.get("items", projected["summary"]["items"])
    if not projected["selected_count"] and not include_empty_source:
        return None
    summary = projected["summary"]
    attach_active_vision_waits(summary, vision_runs, role=role, items=items,
        lineage_items=resume_source_items)
    if include_task_orchestration_authority:
        summary["task_orchestration_authority"] = _task_orchestration_authority(
            projected["orchestration"], role=role)
    return summary



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
    selection: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from .goal_todo_projection import project_todo_read_group

    if not items and not include_empty_source:
        return None
    lineage = lineage_items
    projected = project_todo_read_group(items, source_section=source_section, role=role,
        preferred_todo_ids=preferred_todo_ids, lineage=lineage, item_limit=item_limit,
        evaluated=True, selection=selection, full_selection=full_selection,
        include_full_source=bool(vision_runs), include_task_orchestration_authority=include_task_orchestration_authority)
    items = projected.get("items", projected["summary"]["items"])
    if not projected["selected_count"] and not include_empty_source:
        return None
    summary = projected["summary"]
    attach_active_vision_waits(summary, vision_runs, role=role, items=items,
        lineage_items=lineage_items)
    if include_task_orchestration_authority:
        summary["task_orchestration_authority"] = _task_orchestration_authority(
            projected["orchestration"], role=role)
    return summary
