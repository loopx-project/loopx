from __future__ import annotations

from ..todos.todo_semantics import todo_priority_rank

from typing import Iterable, Mapping

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


Payload = dict[str, object]
PayloadView = Mapping[str, object]


AGENT_MANAGEMENT_PROJECTION_SCHEMA_VERSION = "agent_management_projection_v0"
TODO_ROW_SCHEMA_VERSION = "todo_row_v0"
AGENT_MANAGEMENT_MODE = "read_only"
MAX_AGENT_ROWS = 24
MAX_AGENT_TODOS = 8
MAX_REFS = 1
MAX_SESSION_BINDING_CANDIDATES = 3
MAX_WORKSPACE_SCOPES = 4
STALE_CLAIM_THRESHOLD_HOURS = 36
MATERIAL_LIFECYCLE_CAPABILITY = "material_lifecycle"
LEASE_STATUS_UNAVAILABLE = "unavailable"

_TODO_GROUP_LIST_KEYS = tuple(dict.fromkeys((
    "items", *TODO_SUMMARY_SOURCE_KEYS,
    "executable_backlog_items", "deferred_items", "deferred_resume_candidates",
)))


def _compact(value: object, *, limit: int = 220) -> str | None:
    return public_safe_compact_text(value, limit=limit)


def _now_iso() -> str:
    return now_utc_iso()


def _as_dict(value: object) -> PayloadView:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _as_list(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _append_unique(target: list[str], value: str | None) -> None:
    if value and value not in target:
        target.append(value)


def _nonempty_dict(values: PayloadView) -> Payload:
    return {key: value for key, value in values.items() if value not in (None, "", [], {})}


def _string_list(value: object, *, limit: int = MAX_WORKSPACE_SCOPES) -> list[str]:
    items = value if isinstance(value, list) else [value] if value not in (None, "") else []
    compact: list[str] = []
    for item in items:
        _append_unique(compact, _compact(item, limit=140))
        if len(compact) >= limit:
            break
    return compact


def _todo_status(todo: PayloadView) -> str:
    status = str(todo.get("status") or "").strip().lower()
    return status or ("done" if todo.get("done") else "open")


def _is_done(todo: PayloadView) -> bool:
    # A deferred Todo can carry a checked legacy display flag. Explicit state
    # wins; the checkbox fallback belongs only to rows without a status.
    return _todo_status(todo) in {"done", "archive", "archived"}


def _index_rank(todo: PayloadView) -> int:
    raw = todo.get("index")
    if isinstance(raw, (bool, int, float, str)):
        try:
            return int(raw)
        except (TypeError, ValueError):
            pass
    return 999_999


def _todo_sort_key(todo: PayloadView) -> tuple[int, int, int, str]:
    done_rank = 1 if _is_done(todo) else 0
    return (done_rank, todo_priority_rank(todo), _index_rank(todo), str(todo.get("todo_id") or ""))


def _is_monitor_todo(todo: PayloadView) -> bool:
    return (
        todo.get("task_class") == "continuous_monitor"
        or "monitor" in str(todo.get("action_kind") or "").lower()
    )


def _is_agent_lane_next_action(todo: PayloadView) -> bool:
    return (
        todo.get("schema_version") == "agent_lane_next_action_v0"
        or str(todo.get("source") or "").endswith("agent_lane_next_action")
        or bool(todo.get("selected_by"))
    )


def _is_runnable_advancement_todo(todo: PayloadView) -> bool:
    return (
        _todo_status(todo) == "open"
        and todo.get("task_class") != "blocker"
        and not _is_monitor_todo(todo)
    )


def _current_todo_execution_rank(todo: PayloadView) -> int:
    if todo.get("task_class") == "blocker":
        return 0
    if _todo_status(todo) == "blocked":
        return 2
    return 3 if _is_monitor_todo(todo) else 1


def _current_todo_sort_key(todo: PayloadView) -> tuple[int, int, int, int, int, str]:
    return (
        0 if _is_runnable_advancement_todo(todo) else 1,
        0 if _is_agent_lane_next_action(todo) else 1,
        _current_todo_execution_rank(todo),
        todo_priority_rank(todo),
        _index_rank(todo),
        str(todo.get("todo_id") or ""),
    )


def _select_current_todo(todos: list[Payload]) -> Payload | None:
    open_todos = [todo for todo in todos if not _is_done(todo)]
    return sorted(open_todos, key=_current_todo_sort_key)[0] if open_todos else None


def _select_blocked_todo(todos: list[Payload]) -> Payload | None:
    blocked_todos = [
        todo for todo in todos
        if not _is_done(todo) and (_todo_status(todo) == "blocked" or todo.get("task_class") == "blocker")
    ]
    return sorted(blocked_todos, key=_todo_sort_key)[0] if blocked_todos else None


def _registered_agents(status_payload: PayloadView) -> dict[str, Payload]:
    rows: dict[str, Payload] = {}
    run_history = _as_dict(status_payload.get("run_history"))
    for goal in _as_list(run_history.get("goals")):
        if not isinstance(goal, dict):
            continue
        goal_id = _compact(goal.get("id"), limit=180)
        for raw_agent in _as_list(_as_dict(goal.get("coordination")).get("registered_agents")):
            agent_id = normalize_todo_claimed_by(raw_agent)
            if not agent_id:
                continue
            row = rows.setdefault(
                agent_id,
                {"agent_id": agent_id, "agent_model": "peer_v1", "_goal_ids": [], "_todos": []},
            )
            goal_ids = row["_goal_ids"]
            if isinstance(goal_ids, list):
                _append_unique(goal_ids, goal_id)
    return rows


def projected_agent_goals(
    status_payload: PayloadView,
) -> dict[str, dict[str, dict[str, list[str]]]]:
    """Return ``goal_id -> agent_id -> {spellings, open_todo_ids}`` for this view's agents.

    The execution facts collector reads one Turn lane per Goal and agent and the
    leases on open Todos, so it needs the same agents this projection rows: each
    Goal's registered agents and its open-Todo claimants. Registered ids keep
    their registry spelling next to the normalized one, because the Turn
    envelope names a lane with the former while Todo claims carry the latter.
    """

    goals: dict[str, dict[str, dict[str, list[str]]]] = {}

    def add(goal_id: object, raw_agent: object, *, todo_id: str | None = None) -> None:
        goal = str(goal_id or "").strip()
        agent_id = normalize_todo_claimed_by(raw_agent)
        if not goal or not agent_id:
            return
        work = goals.setdefault(goal, {}).setdefault(
            agent_id, {"spellings": [agent_id], "open_todo_ids": []}
        )
        _append_unique(work["spellings"], str(raw_agent or "").strip() or None)
        _append_unique(work["open_todo_ids"], todo_id)

    run_history = _as_dict(status_payload.get("run_history"))
    for goal in _as_list(run_history.get("goals")):
        if not isinstance(goal, dict):
            continue
        for raw_agent in _as_list(_as_dict(goal.get("coordination")).get("registered_agents")):
            add(goal.get("id"), raw_agent)
    for todo in _iter_status_todos(status_payload):
        if not _is_done(todo):
            add(todo.get("goal_id"), _todo_agent_id(todo), todo_id=normalize_todo_id(todo.get("todo_id")))
    return goals


def registered_agent_ids(status_payload: PayloadView) -> set[str]:
    """Return the registered Agent ids, ignoring the display cap.

    ``MAX_AGENT_ROWS`` bounds the published ``agents`` list; a scope question
    such as "is this caller a registered Agent of the Goal?" must not inherit
    that cap, so it reads this full set instead of the truncated rows. Only
    ``coordination.registered_agents`` proves registration here: a Todo claim
    is a work relationship that can outlive the registration, so claimant ids
    the diagnostic rows carry are not members of this set.
    """

    return set(_registered_agents(status_payload))


def _agent_material_frontiers(status_payload: PayloadView) -> dict[tuple[str, str], Payload]:
    raw_frontiers = status_payload.get("agent_material_frontiers")
    candidates = list(raw_frontiers.values()) if isinstance(raw_frontiers, dict) else _as_list(raw_frontiers)

    frontiers: dict[tuple[str, str], Payload] = {}
    for raw in candidates:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("schema_version") or "") != AGENT_MATERIAL_FRONTIER_SCHEMA_VERSION:
            continue
        goal_id = _compact(raw.get("goal_id"), limit=180)
        agent_id = normalize_todo_claimed_by(raw.get("agent_id"))
        if goal_id and agent_id:
            frontiers[(goal_id, agent_id)] = {str(k): v for k, v in raw.items()}
    return frontiers


def _agent_material_frontier_key(
    *, raw_row: PayloadView, current: PayloadView | None, agent_id: str, goal_filter: str | None,
) -> tuple[str, str] | None:
    row_goal_ids: list[str] = []
    for raw_goal_id in _as_list(raw_row.get("_goal_ids")):
        _append_unique(row_goal_ids, _compact(raw_goal_id, limit=180))
    current_goal_id = _compact(current.get("goal_id"), limit=180) if current else None

    if goal_filter:
        if (current_goal_id and current_goal_id != goal_filter) or (row_goal_ids and goal_filter not in row_goal_ids):
            return None
        return (goal_filter, agent_id)
    if current_goal_id:
        return None if (row_goal_ids and current_goal_id not in row_goal_ids) else (current_goal_id, agent_id)
    return (row_goal_ids[0], agent_id) if len(row_goal_ids) == 1 else None


def _iter_todo_group_items(group: PayloadView, *, goal_id: str | None, source: str) -> Iterable[Payload]:
    for key in _TODO_GROUP_LIST_KEYS:
        for todo in _as_list(group.get(key)):
            if isinstance(todo, dict):
                row: Payload = {str(k): v for k, v in todo.items()}
                row.setdefault("goal_id", goal_id)
                row.setdefault("source", source if key == "items" else f"{source}.{key}")
                yield row


def _iter_next_action_todo(owner: PayloadView, *, goal_id: str | None, source: str) -> Iterable[Payload]:
    todo = owner.get("agent_lane_next_action")
    if isinstance(todo, dict):
        row: Payload = {str(k): v for k, v in todo.items()}
        row.setdefault("goal_id", goal_id)
        row.setdefault("source", source)
        yield row


def _iter_status_todos(status_payload: PayloadView) -> Iterable[Payload]:
    queue = _as_dict(status_payload.get("attention_queue"))
    todo_index = _as_dict(status_payload.get("todo_index"))
    unavailable_goals = set(_as_list(todo_index.get("unavailable_goal_ids")))
    unavailable_goals.update(
        item.get("goal_id") for item in _as_list(queue.get("items"))
        if isinstance(item, dict) and item.get("todo_source") == "unavailable"
    )
    candidates: list[Payload] = []
    hints: list[Payload] = []
    for item in _as_list(queue.get("items")):
        if not isinstance(item, dict):
            continue
        goal_id = _compact(item.get("goal_id"), limit=180)
        for source_prefix, container in (
            ("attention_queue", item),
            ("project_asset", _as_dict(item.get("project_asset"))),
        ):
            hints.extend(_iter_next_action_todo(
                container, goal_id=goal_id, source=f"{source_prefix}.agent_lane_next_action",
            ))
            candidates.extend(_iter_todo_group_items(
                _as_dict(container.get("agent_todos")), goal_id=goal_id, source=f"{source_prefix}.agent_todos",
            ))

    for todo in _as_list(todo_index.get("items")):
        if (
            isinstance(todo, dict)
            and str(todo.get("role") or "") == "agent"
            and str(todo.get("source") or "") != "rollout_event_log"
        ):
            row: Payload = {str(k): v for k, v in todo.items()}
            row.setdefault("source", "todo_index")
            candidates.append(row)

    # Display text, ordinals and Next Action hints are not Task identity. Keep
    # current source rows before their shorter aliases and historical hints;
    # both management and its execution-facts reader consume this one view.
    selected = {_todo_identity(todo): todo for todo in hints}
    seen: set[tuple[str, str, str, str]] = set()
    for row in [*candidates, *hints]:
        if row.get("goal_id") in unavailable_goals:
            continue
        identity = _todo_identity(row)
        if identity in seen:
            continue
        seen.add(identity)
        hint = selected.get(identity)
        if hint and hint.get("selected_by"):
            row = dict(row)
            row.setdefault("selected_by", hint["selected_by"])
        yield row


def _todo_agent_id(todo: PayloadView) -> str | None:
    return (
        normalize_todo_claimed_by(todo.get("claimed_by"))
        or normalize_todo_claimed_by(todo.get("agent_id"))
    )


def _todo_identity(todo: PayloadView) -> tuple[str, str, str, str]:
    todo_id = str(todo.get("todo_id") or "").strip()
    return (
        str(todo.get("goal_id") or ""),
        todo_id,
        "" if todo_id else str(todo.get("index") or ""),
        "" if todo_id else str(todo.get("text") or todo.get("title") or ""),
    )


def _todo_row(todo: PayloadView) -> Payload:
    row: Payload = {
        "schema_version": TODO_ROW_SCHEMA_VERSION,
        "todo_id": normalize_todo_id(todo.get("todo_id")),
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
        "required_capabilities", "required_write_scopes", "target_capabilities",
        "blocks_agent", "excluded_agents", "unblocks_todo_id", "successor_todo_ids",
        "resume_when", "updated_at",
    ):
        if (value := todo.get(key)) not in (None, "", [], {}):
            row[key] = value
    return _nonempty_dict(row)


def _workspace_ref_from_todo(todo: PayloadView | None) -> Payload | None:
    if not todo:
        return None
    raw = _as_dict(todo.get("workspace_ref"))
    scopes = _string_list(
        raw.get("write_scope") or todo.get("required_write_scopes") or todo.get("required_write_scope")
    )
    if raw:
        workspace: Payload = {
            "kind": _compact(raw.get("kind") or "unknown", limit=80),
            "label": _compact(raw.get("label") or raw.get("branch") or raw.get("kind"), limit=140),
            "path_safe": raw.get("path_safe") is True,
        }
        if branch := _compact(raw.get("branch"), limit=120):
            workspace["branch"] = branch
        if scopes:
            workspace["write_scope"] = scopes
        return _nonempty_dict(workspace)

    policy = _compact(
        todo.get("worktree_policy") or todo.get("workspace_policy") or todo.get("workspace_kind"),
        limit=120,
    )
    if not (policy or scopes):
        return None
    kind = "worktree" if "worktree" in (policy or "").lower() else "unknown"
    return _nonempty_dict({
        "kind": kind, "label": policy or "workspace not projected",
        "path_safe": False, "write_scope": scopes,
    })


def _stale_claim_hint(todo: PayloadView | None, *, agent_id: str) -> Payload | None:
    if not todo or _is_done(todo) or todo.get("task_class") == "continuous_monitor":
        return None
    claimed_by = _todo_agent_id(todo) or agent_id
    if not claimed_by:
        return None
    last_activity = _compact(todo.get("updated_at") or todo.get("latest_event_at"), limit=80)
    if not last_activity:
        return {
            "state": "activity_missing", "claimed_by": claimed_by,
            "reason": "claimed open todo has no projected activity timestamp",
            "recommended_operator_action": "inspect evidence before considering reassignment",
        }
    parsed = parse_timestamp(last_activity)
    if not parsed:
        return {
            "state": "activity_unparsed", "claimed_by": claimed_by, "last_activity_at": last_activity,
            "reason": "projected activity timestamp could not be parsed",
            "recommended_operator_action": "inspect status/evidence before considering reassignment",
        }
    if (now_utc() - parsed).total_seconds() / 3600 <= STALE_CLAIM_THRESHOLD_HOURS:
        return None
    return {
        "state": "suspected_stale", "claimed_by": claimed_by, "last_activity_at": last_activity,
        "threshold_hours": STALE_CLAIM_THRESHOLD_HOURS,
        "reason": "last projected activity is older than the stale-claim warning threshold",
        "recommended_operator_action": "ask the same agent to resume or inspect evidence before manual handoff",
    }


def _refs_from_todo(todo: PayloadView) -> tuple[list[str], list[str]]:
    evidence_refs: list[str] = []
    handoff_refs: list[str] = []
    handoff = _as_dict(todo.get("handoff_note"))
    _append_unique(handoff_refs, _compact(handoff.get("handoff_id"), limit=140))
    for raw_ref in _as_list(handoff.get("evidence_refs")):
        _append_unique(evidence_refs, _compact(raw_ref, limit=180))
    todo_id = normalize_todo_id(todo.get("todo_id"))
    if todo.get("evidence") and todo_id:
        evidence_refs.append(f"todo:{todo_id}:evidence")
    if todo.get("note") and todo_id:
        evidence_refs.append(f"todo:{todo_id}:note")
    if (latest_event_kind := _compact(todo.get("latest_event_kind"), limit=80)) and todo_id:
        evidence_refs.append(f"rollout_event:{latest_event_kind}:{todo_id}")
    for material in _as_list(todo.get("review_materials")):
        if isinstance(material, dict) and (ref := _compact(material.get("label") or material.get("path"), limit=180)):
            evidence_refs.append(ref)
    return evidence_refs[:MAX_REFS], handoff_refs[:MAX_REFS]


def _agent_state(
    todos: list[Payload],
    *,
    current: PayloadView | None = None,
    has_session_binding: bool = False,
    execution: PayloadView | None = None,
) -> str:
    """Derive the worker lifecycle state from existing facts only.

    The state is a projection over registry membership, todo claims,
    session bindings and execution facts.  It does not introduce a second
    source of truth: every input is already owned by another contract
    (registry, todo, session binding, Turn lane, delegation lock, lease).

    State priority (highest first):
    1. blocked      — current todo is blocked or a blocker
    2. monitoring / waiting — monitor-only or non-open current work
    3. executing    — the Turn lane is live or a delegation worker holds its lock
    4. unknown      — the lane or lease cannot be checked here, or an active
                      lease has expired while nothing is live; consumers fail
                      closed on it
    5. bound        — has session binding and active todo
    6. launchable   — has active todo, no session binding
    7. addressable  — has session binding but no active todo
    8. registered   — registered in registry, no binding or todo

    A Todo timestamp never makes a worker `executing`: activity age stays in
    `last_activity_at` and `stale_claim_hint`, where it is a hint, not liveness.
    """
    open_todos = [todo for todo in todos if not _is_done(todo)]

    # Blocked takes priority: a blocked worker cannot launch or execute.
    # This only applies to the current todo, not all open todos, per the
    # protocol contract: "blocker remains visible without making the whole
    # peer appear blocked."
    if current and not _is_done(current):
        if _todo_status(current) == "blocked" or current.get("task_class") == "blocker":
            return WORKER_LIFECYCLE_STATE_BLOCKED
        if _is_monitor_todo(current):
            return "monitoring"
        if _todo_status(current) != "open":
            return "waiting"

    facts = execution or {}
    lane = str(facts.get("lane") or "")
    if lane == EXECUTION_LANE_LIVE or facts.get("delegation_worker_active") is True:
        return WORKER_LIFECYCLE_STATE_EXECUTING
    lease = _as_dict(facts.get("lease"))
    if lane in EXECUTION_LANE_UNKNOWN_STATES or (
        lease.get("status") == LEASE_STATUS_UNAVAILABLE
        or (lease.get("status") == "active" and lease.get("expired") is True)
    ):
        return WORKER_LIFECYCLE_STATE_UNKNOWN

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
# claims, session bindings and execution facts (Turn lane liveness, delegation
# worker locks, task leases) -- nothing else.
WORKER_LIFECYCLE_STATE_REGISTERED = "registered"
WORKER_LIFECYCLE_STATE_ADDRESSABLE = "addressable"
WORKER_LIFECYCLE_STATE_BOUND = "bound"
WORKER_LIFECYCLE_STATE_LAUNCHABLE = "launchable"
WORKER_LIFECYCLE_STATE_EXECUTING = "executing"
WORKER_LIFECYCLE_STATE_BLOCKED = "blocked"
WORKER_LIFECYCLE_STATE_UNKNOWN = "unknown"

# Execution-fact lane states this projection switches on. `live` is the only
# evidence of execution; the two unknowns are lanes whose holder this machine
# cannot vouch for, so the row must not read as idle either.
EXECUTION_LANE_LIVE = "live"
EXECUTION_LANE_UNKNOWN_STATES = frozenset({"foreign_host", "unreadable"})


def _execution_row(facts: object) -> Payload | None:
    """Project one agent's execution facts as read-only evidence for its state."""

    if not isinstance(facts, dict):
        return None
    row: Payload = {}
    if lane := _compact(facts.get("lane"), limit=40):
        row["lane"] = lane
    if holder := _as_dict(facts.get("lane_holder")):
        row["lane_holder"] = {
            key: holder[key]
            for key in ("host", "pid", "acquired_at")
            if holder.get(key) not in (None, "")
        }
    if facts.get("delegation_worker_active") is True:
        row["delegation_worker_active"] = True
    lease = _as_dict(facts.get("lease"))
    if lease_status := _compact(lease.get("status"), limit=40):
        lease_row: Payload = {"status": lease_status}
        if isinstance(lease.get("expired"), bool):
            lease_row["expired"] = lease["expired"]
        row["lease"] = lease_row
    return row or None


def _last_activity(todos: list[Payload]) -> str | None:
    candidates = [
        item for todo in todos
        if (item := _compact(todo.get("updated_at") or todo.get("latest_event_at"), limit=80))
    ]
    return max(candidates) if candidates else None


def _safe_next_action(todo: PayloadView | None) -> str:
    if not todo:
        return "Inspect status projection before taking work."
    todo_id = normalize_todo_id(todo.get("todo_id"))
    return f"Continue projected todo {todo_id}." if todo_id else "Inspect projected todo."


def _collect_session_binding_candidates(
    status_payload: PayloadView,
) -> dict[str, list[dict[str, str | None]]]:
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

    candidates: dict[str, list[dict[str, str | None]]] = {}
    run_history = _as_dict(status_payload.get("run_history"))
    for binding in collect_accepted_bindings(_as_list(run_history.get("goals"))):
        candidates.setdefault(binding["agent_id"], []).append({
            "thread_id": _compact(binding["thread_id"], limit=120),
            "host_surface": _compact(binding["host_surface"], limit=60),
        })
    return candidates


def _build_agent_row(
    *,
    agent_id: str,
    raw_row: PayloadView,
    session_binding_candidates: dict[str, list[dict[str, str | None]]],
    facts_by_agent: PayloadView,
    material_frontiers: dict[tuple[str, str], Payload],
    goal_filter: str | None,
) -> Payload:
    all_todos: list[Payload] = [
        {str(k): v for k, v in todo.items()}
        for todo in _as_list(raw_row.get("_todos"))
        if isinstance(todo, dict)
    ]
    current = _select_current_todo(all_todos)
    blocked = _select_blocked_todo(all_todos)
    todos = sorted(all_todos, key=_todo_sort_key)
    if current:
        current_identity = _todo_identity(current)
        todos = [current, *[todo for todo in todos if _todo_identity(todo) != current_identity]]
    todos = todos[:MAX_AGENT_TODOS]
    evidence_refs: list[str] = []
    handoff_refs: list[str] = []
    for todo in todos:
        todo_evidence, todo_handoffs = _refs_from_todo(todo)
        for ref in todo_evidence:
            _append_unique(evidence_refs, ref)
        for ref in todo_handoffs:
            _append_unique(handoff_refs, ref)
    execution = _execution_row(facts_by_agent.get(agent_id))
    binding_candidates = session_binding_candidates.get(agent_id)
    agent_row: Payload = {
        "agent_id": agent_id,
        "agent_model": raw_row.get("agent_model") or "unregistered",
        "state": _agent_state(
            all_todos, current=current, has_session_binding=bool(binding_candidates), execution=execution,
        ),
        "current_todo": _todo_row(current) if current else None,
        "next_action": _safe_next_action(current),
        "last_activity_at": _last_activity(todos),
        "execution": execution,
        "evidence_refs": evidence_refs[:MAX_REFS],
        "handoff_refs": handoff_refs[:MAX_REFS],
        "goal_ids": _as_list(raw_row.get("_goal_ids"))[:MAX_REFS],
    }
    if binding_candidates:
        agent_row["session_binding_candidates"] = binding_candidates[:MAX_SESSION_BINDING_CANDIDATES]
        agent_row["session_binding_count"] = len(binding_candidates)
    frontier_key = _agent_material_frontier_key(
        raw_row=raw_row, current=current, agent_id=agent_id, goal_filter=goal_filter,
    )
    if material_frontier := (material_frontiers.get(frontier_key) if frontier_key else None):
        agent_row["material_frontier"] = project_material_frontier_for_handoff(material_frontier)
        if handoff_note := (_as_dict(current.get("handoff_note")) if current else {}):
            agent_row["handoff_note"] = build_material_handoff_note_v1(handoff_note, material_frontier)
    if workspace_ref := _workspace_ref_from_todo(current):
        agent_row["workspace_ref"] = workspace_ref
    if blocked and (not current or _todo_identity(blocked) != _todo_identity(current)):
        agent_row["blocked_on"] = _todo_row(blocked)
    if stale_claim_hint := _stale_claim_hint(current, agent_id=agent_id):
        agent_row["stale_claim_hint"] = stale_claim_hint
    return _nonempty_dict(agent_row)


def build_agent_management_projection(
    status_payload: PayloadView,
    *,
    available_capabilities: object = None,
    execution_facts: object = None,
    after_agent_id: str | None = None,
) -> Payload:
    """Build a read-only agent management view from a status payload.

    This is a projection over existing LoopX status/todo/history state. It does
    not allocate tasks, dispatch agents, reclaim stale claims, or expose write
    actions.

    ``execution_facts`` maps agent id to the facts `collect_agent_execution_facts`
    reads (Turn lane liveness, delegation worker lock, task lease). Without them
    no row can be `executing` or `unknown`: the projection then says what the
    durable state proves and nothing more.

    ``after_agent_id`` selects one page of the projection's stable agent-id
    ordering: only Agents sorting strictly after it become the source set, so a
    caller whose rows are capped at ``MAX_AGENT_ROWS`` can continue past row 24.
    The cursor is a position, not a membership claim. Without a cursor the
    projection covers the whole registry exactly as before.

    ``source_summary`` separates the two kinds of row: ``registered_agent_count``
    counts the Goal's registrations, and ``diagnostic_agent_count`` (published
    only when it is non-zero) counts the rows this view carries for a work
    relationship alone. A Todo claimant is evidence, never a member, so it can
    never grow the registered total.
    """

    rows_by_agent = _registered_agents(status_payload)
    # Registration facts, remembered before Todo claims add their own rows: a
    # claim is a work relationship that can outlive a registration, so the
    # members of this Goal stay the ids the registry proves.
    registered_ids = set(rows_by_agent)
    material_projection_enabled = MATERIAL_LIFECYCLE_CAPABILITY in set(
        normalize_required_capabilities(available_capabilities)
    )
    material_frontiers = (
        _agent_material_frontiers(status_payload) if material_projection_enabled else {}
    )
    goal_filter = _compact(status_payload.get("goal_filter"), limit=180)

    # Session bindings come from run_history.coordination.thread_agent_bindings.
    # This is the only source for addressable/bound lifecycle states. One Agent
    # can hold several historical bindings, and an omitted one is not disproved:
    # keep a bounded candidate summary plus the full count, so no consumer can
    # read the surviving row as the only route to that peer.
    session_binding_candidates = _collect_session_binding_candidates(status_payload)
    facts_by_agent: PayloadView = (
        {str(k): v for k, v in execution_facts.items()} if isinstance(execution_facts, Mapping) else {}
    )

    for todo in _iter_status_todos(status_payload):
        if not (agent_id := _todo_agent_id(todo)):
            continue
        row = rows_by_agent.setdefault(
            agent_id, {"agent_id": agent_id, "role": "agent", "_goal_ids": [], "_todos": []},
        )
        goal_ids = row["_goal_ids"]
        if isinstance(goal_ids, list):
            _append_unique(goal_ids, _compact(todo.get("goal_id"), limit=180))
        row_todos = row["_todos"]
        if isinstance(row_todos, list):
            row_todos.append(todo)

    page_cursor = str(after_agent_id).strip() if after_agent_id else None
    agents: list[Payload] = []
    selected_agent_count = 0
    for agent_id, raw_row in sorted(rows_by_agent.items()):
        if page_cursor is not None and agent_id <= page_cursor:
            continue
        selected_agent_count += 1
        if len(agents) >= MAX_AGENT_ROWS:
            # Without a cursor this page stops at the cap; with one, keep
            # counting so a paging caller can tell whether more rows follow.
            if page_cursor is None:
                break
            continue
        agents.append(_build_agent_row(
            agent_id=agent_id,
            raw_row=raw_row,
            session_binding_candidates=session_binding_candidates,
            facts_by_agent=facts_by_agent,
            material_frontiers=material_frontiers,
            goal_filter=goal_filter,
        ))

    source_summary: Payload = {
        "registered_agent_count": len(registered_ids),
        "projected_agent_count": len(agents),
        "todo_source": (
            "status.attention_queue + authoritative status.todo_index rows; "
            "event-only rollout receipts are evidence, not runtime work"
        ),
    }
    diagnostic_agent_count = len(rows_by_agent) - len(registered_ids)
    if diagnostic_agent_count:
        # Rows carried only for a work relationship: visible evidence, never
        # members, so a retained claim cannot grow the Goal's Agent total.
        source_summary["diagnostic_agent_count"] = diagnostic_agent_count
    if page_cursor is not None:
        # The span this page reads, not the whole Goal: `registered_agent_count`
        # still counts every registered Agent, so a paging caller can separate
        # rows before the cursor from rows this page could not carry.
        source_summary["selected_agent_count"] = selected_agent_count
    if material_frontiers:
        source_summary["material_frontier_count"] = len(material_frontiers)
    if isinstance(execution_facts, dict):
        # One flag: readers only need to tell "facts collected, none found"
        # from "no facts collected"; each row carries its own evidence.
        source_summary["execution_facts_collected"] = True

    return _nonempty_dict({
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
    })
