"""Explicit request-to-Core work links for any receiving Agent.

Python adapts receipt/Core reads; TypeScript owns receiver advice. Link writes
reuse inbox request and exact Goal-instance admission, never Todo authority.
"""

from __future__ import annotations

from ...control_plane.todos.list_readback import list_goal_todos
from ..runtime.public_safety import public_safe_compact_text
from ..todos.contract import TODO_ID_PATTERN
from ..content_digest import ENVELOPED_SHA256_PATTERN
from .inbox import _entry, _now, _receipt, _request_lock, _root, _write
from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle


def _core_todos(registry_path, root, goal_id, *, todo_ids=(), cached_todos=None):
    if cached_todos is None:
        result = list_goal_todos(
            registry_path=registry_path, runtime_root_arg=str(root), goal_id=goal_id
        )
        if result.get("ok") is not True:
            raise ValueError("Core Todo authority unavailable")
        cached_todos = {r["todo_id"]: r for r in result.get("todos", []) if r.get("todo_id")}
    for tid in todo_ids:
        if tid in cached_todos:
            continue
        # The queue omits completed history. Resolve only the explicit missing
        # links through Core's existing exact read, without exporting history.
        try:
            result = list_goal_todos(
                registry_path=registry_path, runtime_root_arg=str(root),
                goal_id=goal_id, todo_id=tid,
            )
            todo = result.get("todo") if result.get("ok") is True else None
            cached_todos[tid] = todo if isinstance(todo, dict) and todo.get("todo_id") == tid else None
        except (OSError, ValueError, RuntimeError):
            cached_todos[tid] = None
    return cached_todos


def read_linked_work(root, registry_path, item, todos_cache, *, include_core_details):
    """Read explicit links and current receiver-owned Core facts, never copied progress."""
    links, error = _receipt(root, "links", item)
    tids = links.get("todo_ids", [])
    if not include_core_details:
        # An audience-bound handoff grants access to its receipt, not the
        # receiver's private Core work. Keep references for separately scoped
        # inspection without reading or projecting private title/status.
        return {
            "linked_todos": [{"todo_id": tid} for tid in tids],
            "evidence_refs": links.get("evidence_ids", []),
            "warnings": [error] if error else [],
            "evidence_unavailable": bool(error),
        }
    gid, aid = item["goal_id"], item["agent_id"]
    goal_ref = item.get("goal_ref")
    cache_key = (gid, (goal_ref or {}).get("goal_instance_id"))
    core = todos_cache.get(cache_key)
    if tids and (cache_key not in todos_cache or
                 (core is not None and any(tid not in core for tid in tids))):
        try:
            with collaboration_goal_scope(
                registry_path, goal_id=gid, agents=(), caller_goal_ref=goal_ref
            ) as scope:
                admission = decide_collaboration_lifecycle(scope, operation="inbox_observe", record=item)
                todos_cache[cache_key] = (
                    None if admission.get("kind") == "omit"
                    else _core_todos(registry_path, root, gid, todo_ids=tids, cached_todos=core)
                )
        except (OSError, ValueError, RuntimeError):
            todos_cache[cache_key] = None
    core = todos_cache.get(cache_key)
    linked = []
    for tid in tids:
        todo = core.get(tid) if core is not None else None
        belongs = todo and aid in (todo.get("claimed_by"), todo.get("bound_agent"))
        linked.append({
            "todo_id": tid,
            "status": todo.get("status") if belongs else "unknown",
            "title": public_safe_compact_text(todo.get("text") or todo.get("title"), limit=420) if belongs else None,
            "source": "core_todo_current_read" if belongs else "core_todo_unavailable_or_owner_changed",
        })
    return {
        "linked_todos": linked,
        "evidence_refs": links.get("evidence_ids", []),
        "warnings": [error] if error else [],
        "evidence_unavailable": bool(error) or any(t["status"] == "unknown" for t in linked),
    }


def receiver_work_observations(root, registry_path, items):
    """Cache the active queue and exact linked reads within each Goal instance."""
    todos_cache, observations = {}, []
    for item in items:
        work = read_linked_work(root, registry_path, item, todos_cache, include_core_details=True)
        observations.append({
            "kind": item["inbox_state"], "recorded_decision": item["recorded_decision"],
            "linked_todos": work["linked_todos"], "evidence_refs": work["evidence_refs"],
            "evidence_unavailable": work["evidence_unavailable"],
        })
        if work["warnings"]:
            item.setdefault("warnings", []).extend(work["warnings"])
    return observations


def link(
    root,
    registry_path,
    goal_id,
    agent_id,
    request_id,
    todo_ids,
    evidence_ids,
    *,
    caller_goal_ref=None,
    scope=None,
):
    if not todo_ids and not evidence_ids:
        raise ValueError("at least one Core Todo or evidence reference required")
    if len(todo_ids) > 16 or len(evidence_ids) > 16:
        raise ValueError("too many context links")
    if any(not TODO_ID_PATTERN.fullmatch(x) for x in todo_ids):
        raise ValueError("invalid Core Todo id")
    if any(not ENVELOPED_SHA256_PATTERN.fullmatch(x) for x in evidence_ids):
        raise ValueError("evidence references must be opaque SHA256 identifiers")
    if scope is None:
        with collaboration_goal_scope(
            registry_path,
            goal_id=goal_id,
            agents=(agent_id,),
            caller_goal_ref=caller_goal_ref,
        ) as goal_scope:
            return link(
                root,
                registry_path,
                goal_id,
                agent_id,
                request_id,
                todo_ids,
                evidence_ids,
                scope=goal_scope,
            )
    row = _entry(root, goal_id, agent_id, request_id, scope=scope)
    decide_collaboration_lifecycle(
        scope,
        operation="artifact_link",
        record=row,
    )
    if todo_ids:
        rows = _core_todos(registry_path, root, goal_id, todo_ids=todo_ids)
        for tid in todo_ids:
            todo = rows.get(tid) or {}
            if todo.get("claimed_by") != agent_id and todo.get("bound_agent") != agent_id:
                raise ValueError("linked Todo must belong to the receiving Agent")
    path = _root(root) / "links" / (request_id + ".json")
    with _request_lock(root, request_id, scope, path.with_suffix(".lock")):
        old, error = _receipt(
            root,
            "links",
            row,
        )
        if error:
            raise ValueError(error)
        tids = sorted(set(old.get("todo_ids", [])) | set(todo_ids))
        refs = sorted(set(old.get("evidence_ids", [])) | set(evidence_ids))
        if len(tids) > 16 or len(refs) > 16:
            raise ValueError("too many context links")
        value = {
            key: row[key]
            for key in ("request_id", "goal_id", "agent_id", "goal_ref")
            if key in row
        }
        value.update(todo_ids=tids, evidence_ids=refs)
        if any(old.get(k) != v for k, v in value.items()):
            _write(path, value | {"updated_at": _now()})
    return {"ok": True, **value}
