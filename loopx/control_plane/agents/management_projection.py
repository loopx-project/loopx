from __future__ import annotations

from ..todos.todo_semantics import todo_priority_rank

from typing import Any, Iterable

from ..runtime.public_safety import public_safe_compact_text
from ..runtime.time import now_utc, now_utc_iso, parse_timestamp
from ..todos.contract import (
    normalize_required_capabilities,
    normalize_todo_claimed_by,
    normalize_todo_id,
)
from ..todos.summary_item import TODO_SUMMARY_SOURCE_KEYS
from ...thread_agent_binding import collect_accepted_bindings
from .material_frontier import AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION
from .material_handoff import (
    build_material_handoff_note_v1,
    project_material_frontier_for_handoff,
)


AGENT_MANAGEMENT_PROJECTION_SCHEMA_VERSION = "agent_management_projection_v0"
TODO_ROW_SCHEMA_VERSION = "todo_row_v0"
AGENT_MANAGEMENT_MODE = "read_only"
MAX_AGENT_ROWS = 24
MAX_AGENT_TODOS = 8
MAX_REFS = 1
MAX_SESSION_BINDING_CANDIDATES = 3
MAX_WORKSPACE_SCOPES = 4
STALE_CLAIM_THRESHOLD_HOURS = 36
EXECUTING_ACTIVITY_THRESHOLD_HOURS = 8
MATERIAL_LIFECYCLE_CAPABILITY = "material_lifecycle"

_TODO_GROUP_LIST_KEYS = tuple(
    dict.fromkeys(
        (
            *TODO_SUMMARY_SOURCE_KEYS,
            "executable_backlog_items",
            "deferred_items",
            "deferred_resume_candidates",
        )
    )
)



def _compact(value: Any, *, limit: int = 220) -> str | None:
    return public_safe_compact_text(value, limit=limit)


def _now_iso() -> str:
    return now_utc_iso()


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _string_list(value: Any, *, limit: int = MAX_WORKSPACE_SCOPES) -> list[str]:
    items = value if isinstance(value, list) else [value] if value not in (None, "") else []
    compact: list[str] = []
    for item in items:
        text = _compact(item, limit=140)
        if text and text not in compact:
            compact.append(text)
        if len(compact) >= limit:
            break
    return compact


def _todo_status(todo: dict[str, Any]) -> str:
    status = str(todo.get("status") or "").strip().lower()
    if status:
        return status
    return "done" if todo.get("done") else "open"


def _is_done(todo: dict[str, Any]) -> bool:
    return bool(todo.get("done")) or _todo_status(todo) in {"done", "archive", "archived"}




def _index_rank(todo: dict[str, Any]) -> int:
    try:
        return int(todo.get("index"))
    except (TypeError, ValueError):
        return 999_999


def _todo_sort_key(todo: dict[str, Any]) -> tuple[int, int, int, str]:
    done_rank = 1 if _is_done(todo) else 0
    return (done_rank, todo_priority_rank(todo), _index_rank(todo), str(todo.get("todo_id") or ""))


def _is_monitor_todo(todo: dict[str, Any]) -> bool:
    return (
        todo.get("task_class") == "continuous_monitor"
        or "monitor" in str(todo.get("action_kind") or "").lower()
    )


def _is_agent_lane_next_action(todo: dict[str, Any]) -> bool:
    return (
        todo.get("schema_version") == "agent_lane_next_action_v0"
        or str(todo.get("source") or "").endswith("agent_lane_next_action")
        or bool(todo.get("selected_by"))
    )


def _is_runnable_advancement_todo(todo: dict[str, Any]) -> bool:
    return (
        _todo_status(todo) == "open"
        and todo.get("task_class") != "blocker"
        and not _is_monitor_todo(todo)
    )


def _current_todo_execution_rank(todo: dict[str, Any]) -> int:
    if todo.get("task_class") == "blocker":
        return 0
    if _todo_status(todo) == "blocked":
        return 2
    if _is_monitor_todo(todo):
        return 3
    return 1


def _current_todo_sort_key(todo: dict[str, Any]) -> tuple[int, int, int, int, int, str]:
    runnable_advancement_rank = 0 if _is_runnable_advancement_todo(todo) else 1
    selected_rank = 0 if _is_agent_lane_next_action(todo) else 1
    return (
        runnable_advancement_rank,
        selected_rank,
        _current_todo_execution_rank(todo),
        todo_priority_rank(todo),
        _index_rank(todo),
        str(todo.get("todo_id") or ""),
    )


def _select_current_todo(todos: list[dict[str, Any]]) -> dict[str, Any] | None:
    open_todos = [todo for todo in todos if not _is_done(todo)]
    if not open_todos:
        return None
    return sorted(open_todos, key=_current_todo_sort_key)[0]


def _select_blocked_todo(todos: list[dict[str, Any]]) -> dict[str, Any] | None:
    blocked_todos = [
        todo
        for todo in todos
        if not _is_done(todo)
        and (_todo_status(todo) == "blocked" or todo.get("task_class") == "blocker")
    ]
    if not blocked_todos:
        return None
    return sorted(blocked_todos, key=_todo_sort_key)[0]


def _registered_agents(status_payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    run_history = _as_dict(status_payload.get("run_history"))
    for goal in _as_list(run_history.get("goals")):
        if not isinstance(goal, dict):
            continue
        goal_id = _compact(goal.get("id"), limit=180)
        coordination = _as_dict(goal.get("coordination"))
        for raw_agent in _as_list(coordination.get("registered_agents")):
            agent_id = normalize_todo_claimed_by(raw_agent)
            if not agent_id:
                continue
            row = rows.setdefault(
                agent_id,
                {
                    "agent_id": agent_id,
                    "agent_model": "peer_v1",
                    "_goal_ids": [],
                    "_todos": [],
                },
            )
            if goal_id and goal_id not in row["_goal_ids"]:
                row["_goal_ids"].append(goal_id)
    return rows


def _agent_material_frontiers(
    status_payload: dict[str, Any],
) -> dict[tuple[str, str], dict[str, Any]]:
    raw_frontiers = status_payload.get("agent_material_frontiers")
    if isinstance(raw_frontiers, dict):
        candidates = list(raw_frontiers.values())
    else:
        candidates = _as_list(raw_frontiers)

    frontiers: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("schema_version") or "") != AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION:
            continue
        goal_id = _compact(raw.get("goal_id"), limit=180)
        agent_id = normalize_todo_claimed_by(raw.get("agent_id"))
        if goal_id and agent_id:
            frontiers[(goal_id, agent_id)] = raw
    return frontiers


def _agent_material_frontier_key(
    *,
    raw_row: dict[str, Any],
    current: dict[str, Any] | None,
    agent_id: str,
    goal_filter: str | None,
) -> tuple[str, str] | None:
    row_goal_ids: list[str] = []
    for raw_goal_id in _as_list(raw_row.get("_goal_ids")):
        row_goal_id = _compact(raw_goal_id, limit=180)
        if row_goal_id and row_goal_id not in row_goal_ids:
            row_goal_ids.append(row_goal_id)
    current_goal_id = _compact(current.get("goal_id"), limit=180) if current else None

    if goal_filter:
        if current_goal_id and current_goal_id != goal_filter:
            return None
        if row_goal_ids and goal_filter not in row_goal_ids:
            return None
        return (goal_filter, agent_id)
    if current_goal_id:
        if row_goal_ids and current_goal_id not in row_goal_ids:
            return None
        return (current_goal_id, agent_id)
    if len(row_goal_ids) == 1:
        return (row_goal_ids[0], agent_id)
    return None


def _iter_todo_group_items(
    group: dict[str, Any],
    *,
    goal_id: str | None,
    source: str,
) -> Iterable[dict[str, Any]]:
    for key in _TODO_GROUP_LIST_KEYS:
        for todo in _as_list(group.get(key)):
            if isinstance(todo, dict):
                row = dict(todo)
                row.setdefault("goal_id", goal_id)
                row.setdefault("source", source if key == "items" else f"{source}.{key}")
                yield row


def _iter_next_action_todo(
    owner: dict[str, Any],
    *,
    goal_id: str | None,
    source: str,
) -> Iterable[dict[str, Any]]:
    todo = owner.get("agent_lane_next_action")
    if isinstance(todo, dict):
        row = dict(todo)
        row.setdefault("goal_id", goal_id)
        row.setdefault("source", source)
        yield row


def _iter_status_todos(status_payload: dict[str, Any]) -> Iterable[dict[str, Any]]:
    queue = _as_dict(status_payload.get("attention_queue"))
    for item in _as_list(queue.get("items")):
        if not isinstance(item, dict):
            continue
        goal_id = _compact(item.get("goal_id"), limit=180)
        yield from _iter_next_action_todo(
            item,
            goal_id=goal_id,
            source="attention_queue.agent_lane_next_action",
        )
        group = _as_dict(item.get("agent_todos"))
        yield from _iter_todo_group_items(
            group,
            goal_id=goal_id,
            source="attention_queue.agent_todos",
        )
        project_asset = _as_dict(item.get("project_asset"))
        yield from _iter_next_action_todo(
            project_asset,
            goal_id=goal_id,
            source="project_asset.agent_lane_next_action",
        )
        group = _as_dict(project_asset.get("agent_todos"))
        yield from _iter_todo_group_items(
            group,
            goal_id=goal_id,
            source="project_asset.agent_todos",
        )

    todo_index = _as_dict(status_payload.get("todo_index"))
    for todo in _as_list(todo_index.get("items")):
        if (
            isinstance(todo, dict)
            and str(todo.get("role") or "") == "agent"
            and str(todo.get("source") or "") != "rollout_event_log"
        ):
            row = dict(todo)
            row.setdefault("source", "todo_index")
            yield row


def _todo_agent_id(todo: dict[str, Any]) -> str | None:
    return (
        normalize_todo_claimed_by(todo.get("claimed_by"))
        or normalize_todo_claimed_by(todo.get("agent_id"))
    )


def _todo_identity(todo: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(todo.get("goal_id") or ""),
        str(todo.get("todo_id") or ""),
        str(todo.get("index") or ""),
        str(todo.get("text") or todo.get("title") or ""),
    )


def _todo_row(todo: dict[str, Any]) -> dict[str, Any]:
    todo_id = normalize_todo_id(todo.get("todo_id"))
    row: dict[str, Any] = {
        "schema_version": TODO_ROW_SCHEMA_VERSION,
        "todo_id": todo_id,
        "goal_id": _compact(todo.get("goal_id"), limit=180),
        "role": "agent",
        "status": _todo_status(todo),
        "priority": _compact(todo.get("priority"), limit=40),
        "title": _compact(todo.get("title") or todo.get("text"), limit=96),
        "task_class": _compact(todo.get("task_class"), limit=80),
        "action_kind": _compact(todo.get("action_kind"), limit=100),
        "task_domain": _compact(todo.get("task_domain"), limit=100),
        "claimed_by": normalize_todo_claimed_by(todo.get("claimed_by")),
    }
    for key in (
        "required_capabilities",
        "required_write_scopes",
        "target_capabilities",
        "blocks_agent",
        "excluded_agents",
        "unblocks_todo_id",
        "successor_todo_ids",
        "resume_when",
        "updated_at",
    ):
        value = todo.get(key)
        if value not in (None, "", [], {}):
            row[key] = value
    return {key: value for key, value in row.items() if value not in (None, "", [], {})}


def _workspace_ref_from_todo(todo: dict[str, Any] | None) -> dict[str, Any] | None:
    if not todo:
        return None
    raw = _as_dict(todo.get("workspace_ref"))
    scopes = _string_list(
        raw.get("write_scope")
        or todo.get("required_write_scopes")
        or todo.get("required_write_scope")
    )
    if raw:
        workspace: dict[str, Any] = {
            "kind": _compact(raw.get("kind") or "unknown", limit=80),
            "label": _compact(raw.get("label") or raw.get("branch") or raw.get("kind"), limit=140),
            "path_safe": raw.get("path_safe") is True,
        }
        branch = _compact(raw.get("branch"), limit=120)
        if branch:
            workspace["branch"] = branch
        if scopes:
            workspace["write_scope"] = scopes
        return {key: value for key, value in workspace.items() if value not in (None, "", [], {})}

    policy = _compact(
        todo.get("worktree_policy")
        or todo.get("workspace_policy")
        or todo.get("workspace_kind"),
        limit=120,
    )
    if not (policy or scopes):
        return None
    policy_lower = (policy or "").lower()
    kind = "worktree" if "worktree" in policy_lower else "unknown"
    workspace = {
        "kind": kind,
        "label": policy or "workspace not projected",
        "path_safe": False,
        "write_scope": scopes,
    }
    return {key: value for key, value in workspace.items() if value not in (None, "", [], {})}


def _stale_claim_hint(todo: dict[str, Any] | None, *, agent_id: str) -> dict[str, Any] | None:
    if not todo or _is_done(todo):
        return None
    if todo.get("task_class") == "continuous_monitor":
        return None
    claimed_by = (
        normalize_todo_claimed_by(todo.get("claimed_by"))
        or normalize_todo_claimed_by(todo.get("agent_id"))
        or agent_id
    )
    if not claimed_by:
        return None
    last_activity = _compact(todo.get("updated_at") or todo.get("latest_event_at"), limit=80)
    if not last_activity:
        return {
            "state": "activity_missing",
            "claimed_by": claimed_by,
            "reason": "claimed open todo has no projected activity timestamp",
            "recommended_operator_action": "inspect evidence before considering reassignment",
        }
    parsed = parse_timestamp(last_activity)
    if not parsed:
        return {
            "state": "activity_unparsed",
            "claimed_by": claimed_by,
            "last_activity_at": last_activity,
            "reason": "projected activity timestamp could not be parsed",
            "recommended_operator_action": "inspect status/evidence before considering reassignment",
        }
    age_hours = (now_utc() - parsed).total_seconds() / 3600
    if age_hours <= STALE_CLAIM_THRESHOLD_HOURS:
        return None
    return {
        "state": "suspected_stale",
        "claimed_by": claimed_by,
        "last_activity_at": last_activity,
        "threshold_hours": STALE_CLAIM_THRESHOLD_HOURS,
        "reason": "last projected activity is older than the stale-claim warning threshold",
        "recommended_operator_action": "ask the same agent to resume or inspect evidence before manual handoff",
    }


def _refs_from_todo(todo: dict[str, Any]) -> tuple[list[str], list[str]]:
    evidence_refs: list[str] = []
    handoff_refs: list[str] = []
    handoff = _as_dict(todo.get("handoff_note"))
    handoff_id = _compact(handoff.get("handoff_id"), limit=140)
    if handoff_id:
        handoff_refs.append(handoff_id)
    for raw_ref in _as_list(handoff.get("evidence_refs")):
        ref = _compact(raw_ref, limit=180)
        if ref and ref not in evidence_refs:
            evidence_refs.append(ref)
    todo_id = normalize_todo_id(todo.get("todo_id"))
    if todo.get("evidence") and todo_id:
        evidence_refs.append(f"todo:{todo_id}:evidence")
    if todo.get("note") and todo_id:
        evidence_refs.append(f"todo:{todo_id}:note")
    latest_event_kind = _compact(todo.get("latest_event_kind"), limit=80)
    if latest_event_kind and todo_id:
        evidence_refs.append(f"rollout_event:{latest_event_kind}:{todo_id}")
    for material in _as_list(todo.get("review_materials")):
        if isinstance(material, dict):
            ref = _compact(material.get("label") or material.get("path"), limit=180)
            if ref:
                evidence_refs.append(ref)
    return evidence_refs[:MAX_REFS], handoff_refs[:MAX_REFS]


def _agent_state(
    todos: list[dict[str, Any]],
    *,
    current: dict[str, Any] | None = None,
    has_session_binding: bool = False,
    last_activity_at: str | None = None,
) -> str:
    """Derive the worker lifecycle state from existing facts only.

    The state is a projection over registry membership, todo claims,
    session bindings, and activity timestamps.  It does not introduce a
    second source of truth: every input is already owned by another
    contract (registry, todo, session binding, or run history).

    State priority (highest first):
    1. blocked      — current todo is blocked or a blocker
    2. monitoring / waiting — monitor-only or non-open current work
    3. executing    — current open work updated within the activity threshold
    4. bound        — has session binding and active todo
    5. launchable   — has active todo, no session binding
    6. addressable  — has session binding but no active todo
    7. registered   — registered in registry, no binding or todo
    """
    open_todos = [todo for todo in todos if not _is_done(todo)]

    # Blocked takes priority: a blocked worker cannot launch or execute.
    # This only applies to the current todo, not all open todos, per the
    # protocol contract: "blocker remains visible without making the whole
    # peer appear blocked."
    if current and not _is_done(current):
        if _todo_status(current) == "blocked" or current.get("task_class") == "blocker":
            return WORKER_LIFECYCLE_STATE_BLOCKED

    if current and not _is_done(current):
        if _is_monitor_todo(current):
            return "monitoring"
        if _todo_status(current) != "open":
            return "waiting"

    # Activity describes the selected work, not updates to unrelated todos.
    if current and not _is_done(current) and last_activity_at:
        parsed = parse_timestamp(last_activity_at)
        if parsed:
            age_hours = (now_utc() - parsed).total_seconds() / 3600
            if 0 <= age_hours <= EXECUTING_ACTIVITY_THRESHOLD_HOURS:
                return WORKER_LIFECYCLE_STATE_EXECUTING

    # Bound: has session binding and active todo.
    if current and not _is_done(current) and has_session_binding:
        return WORKER_LIFECYCLE_STATE_BOUND

    # Launchable: has active todo, no session binding.
    if open_todos:
        return WORKER_LIFECYCLE_STATE_LAUNCHABLE

    # Addressable: has session binding but no active todo.
    if has_session_binding:
        return WORKER_LIFECYCLE_STATE_ADDRESSABLE

    # Registered: in registry, no binding or todo.
    return WORKER_LIFECYCLE_STATE_REGISTERED


# Worker lifecycle state vocabulary for R2 small-team execution qualification.
# These states are derived from existing facts only; they do not introduce a
# second source of truth.  The projection reads registry membership, todo
# claims, session bindings, and activity timestamps — nothing else.
WORKER_LIFECYCLE_STATE_REGISTERED = "registered"
WORKER_LIFECYCLE_STATE_ADDRESSABLE = "addressable"
WORKER_LIFECYCLE_STATE_BOUND = "bound"
WORKER_LIFECYCLE_STATE_LAUNCHABLE = "launchable"
WORKER_LIFECYCLE_STATE_EXECUTING = "executing"
WORKER_LIFECYCLE_STATE_BLOCKED = "blocked"


def _last_activity(todos: list[dict[str, Any]]) -> str | None:
    candidates = [
        _compact(todo.get("updated_at") or todo.get("latest_event_at"), limit=80)
        for todo in todos
    ]
    return sorted([item for item in candidates if item], reverse=True)[0] if any(candidates) else None


def _safe_next_action(todo: dict[str, Any] | None) -> str:
    if not todo:
        return "Inspect status projection before taking work."
    todo_id = normalize_todo_id(todo.get("todo_id"))
    if todo_id:
        return f"Continue projected todo {todo_id}."
    return "Inspect projected todo."


def _collect_session_binding_candidates(
    status_payload: dict[str, Any],
) -> dict[str, list[dict[str, str]]]:
    """Group one Agent's accepted session bindings by the owner's agent identity.

    Session bindings come from run_history.coordination.thread_agent_bindings
    and are the only source for addressable/bound lifecycle states. They are
    read through the binding owner's `collect_accepted_bindings`, so this row
    counts the same bindings the peer directory publishes as routes instead of
    normalising a second time here; that also keys the candidates by the same
    agent identity this projection uses for its rows. The display budget is not
    an identity key: the owner accepts thread identifiers to 128 characters and
    host surfaces to 64, both wider than what this projection shows, so two
    accepted bindings that share a visible prefix are still two routes.
    Identity stays whole, and `_compact` only renders it.
    """

    candidates: dict[str, list[dict[str, str]]] = {}
    run_history = _as_dict(status_payload.get("run_history"))
    for binding in collect_accepted_bindings(_as_list(run_history.get("goals"))):
        candidates.setdefault(binding["agent_id"], []).append(
            {
                "thread_id": _compact(binding["thread_id"], limit=120),
                "host_surface": _compact(binding["host_surface"], limit=60),
            }
        )
    return candidates


def build_agent_management_projection(
    status_payload: dict[str, Any],
    *,
    available_capabilities: Any = None,
) -> dict[str, Any]:
    """Build a read-only agent management view from a status payload.

    This is a projection over existing LoopX status/todo/history state. It does
    not allocate tasks, dispatch agents, reclaim stale claims, or expose write
    actions.
    """

    rows_by_agent = _registered_agents(status_payload)
    material_projection_enabled = MATERIAL_LIFECYCLE_CAPABILITY in set(
        normalize_required_capabilities(available_capabilities)
    )
    material_frontiers = (
        _agent_material_frontiers(status_payload)
        if material_projection_enabled
        else {}
    )
    goal_filter = _compact(status_payload.get("goal_filter"), limit=180)

    # Session bindings come from run_history.coordination.thread_agent_bindings.
    # This is the only source for addressable/bound lifecycle states. One Agent
    # can hold several historical bindings, and an omitted one is not disproved:
    # keep a bounded candidate summary plus the full count, so no consumer can
    # read the surviving row as the only route to that peer.
    session_binding_candidates = _collect_session_binding_candidates(status_payload)

    seen_todos: set[tuple[str, str, str, str]] = set()
    for todo in _iter_status_todos(status_payload):
        agent_id = _todo_agent_id(todo)
        if not agent_id:
            continue
        identity = _todo_identity(todo)
        if identity in seen_todos:
            continue
        seen_todos.add(identity)
        row = rows_by_agent.setdefault(
            agent_id,
            {
                "agent_id": agent_id,
                "role": "agent",
                "_goal_ids": [],
                "_todos": [],
            },
        )
        goal_id = _compact(todo.get("goal_id"), limit=180)
        if goal_id and goal_id not in row["_goal_ids"]:
            row["_goal_ids"].append(goal_id)
        row["_todos"].append(todo)

    agents: list[dict[str, Any]] = []
    for agent_id, raw_row in sorted(rows_by_agent.items()):
        all_todos = _as_list(raw_row.get("_todos"))
        current = _select_current_todo(all_todos)
        blocked = _select_blocked_todo(all_todos)
        todos = sorted(all_todos, key=_todo_sort_key)
        if current:
            current_identity = _todo_identity(current)
            todos = [
                current,
                *[todo for todo in todos if _todo_identity(todo) != current_identity],
            ]
        todos = todos[:MAX_AGENT_TODOS]
        evidence_refs: list[str] = []
        handoff_refs: list[str] = []
        for todo in todos:
            todo_evidence, todo_handoffs = _refs_from_todo(todo)
            for ref in todo_evidence:
                if ref not in evidence_refs:
                    evidence_refs.append(ref)
            for ref in todo_handoffs:
                if ref not in handoff_refs:
                    handoff_refs.append(ref)
        last_activity = _last_activity(todos)
        agent_state = _agent_state(
            all_todos,
            current=current,
            has_session_binding=agent_id in session_binding_candidates,
            last_activity_at=_last_activity([current]) if current else None,
        )
        agent_row: dict[str, Any] = {
            "agent_id": agent_id,
            "agent_model": raw_row.get("agent_model") or "unregistered",
            "state": agent_state,
            "current_todo": _todo_row(current) if current else None,
            "next_action": _safe_next_action(current),
            "last_activity_at": last_activity,
            "evidence_refs": evidence_refs[:MAX_REFS],
            "handoff_refs": handoff_refs[:MAX_REFS],
            "goal_ids": _as_list(raw_row.get("_goal_ids"))[:MAX_REFS],
        }
        binding_candidates = session_binding_candidates.get(agent_id)
        if binding_candidates:
            agent_row["session_binding_candidates"] = binding_candidates[
                :MAX_SESSION_BINDING_CANDIDATES
            ]
            agent_row["session_binding_count"] = len(binding_candidates)
        material_frontier_key = _agent_material_frontier_key(
            raw_row=raw_row,
            current=current,
            agent_id=agent_id,
            goal_filter=goal_filter,
        )
        material_frontier = (
            material_frontiers.get(material_frontier_key)
            if material_frontier_key
            else None
        )
        if material_frontier:
            agent_row["material_frontier"] = project_material_frontier_for_handoff(
                material_frontier
            )
            handoff_note = _as_dict(current.get("handoff_note")) if current else {}
            if handoff_note:
                agent_row["handoff_note"] = build_material_handoff_note_v1(
                    handoff_note,
                    material_frontier,
                )
        workspace_ref = _workspace_ref_from_todo(current)
        if workspace_ref:
            agent_row["workspace_ref"] = workspace_ref
        if blocked and (not current or _todo_identity(blocked) != _todo_identity(current)):
            agent_row["blocked_on"] = _todo_row(blocked)
        stale_claim_hint = _stale_claim_hint(current, agent_id=agent_id)
        if stale_claim_hint:
            agent_row["stale_claim_hint"] = stale_claim_hint
        agents.append(
            {
                key: value
                for key, value in agent_row.items()
                if value not in (None, "", [], {})
            }
        )
        if len(agents) >= MAX_AGENT_ROWS:
            break

    source_summary: dict[str, Any] = {
        "registered_agent_count": len(rows_by_agent),
        "projected_agent_count": len(agents),
        "todo_source": (
            "status.attention_queue + authoritative status.todo_index rows; "
            "event-only rollout receipts are evidence, not runtime work"
        ),
    }
    if material_frontiers:
        source_summary["material_frontier_count"] = len(material_frontiers)

    projection: dict[str, Any] = {
        "schema_version": AGENT_MANAGEMENT_PROJECTION_SCHEMA_VERSION,
        "mode": AGENT_MANAGEMENT_MODE,
        "goal_id": goal_filter,
        "generated_at": _now_iso(),
        "truth_contract": {
            "todo_is_runtime_work_item": True,
            "projection_is_writable": False,
            "introduces_task_runtime": False,
            "write_api": False,
        },
        "source_summary": source_summary,
        "agents": agents,
    }
    return {
        key: value
        for key, value in projection.items()
        if value not in (None, "", [], {})
    }
