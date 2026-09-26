"""Receipt projection and explicit Core links for manager context handoffs.

Receipts describe transport/consumption, never a second mutable work status.
"""

from __future__ import annotations

import re

from . import _read, _root, _write
from ...control_plane.collaboration.inbox import (
    _now as _now, _entry as _entry, _receipt as _receipt, record_read as record_read,
)
from ...file_lock import exclusive_file_lock
from ...todos import list_goal_todos
from ...chat_manager_details import _text


def _core_todos(registry_path, root, goal_id):
    result = list_goal_todos(
        registry_path=registry_path, runtime_root_arg=str(root), goal_id=goal_id
    )
    if result.get("ok") is not True:
        raise ValueError("Core Todo authority unavailable")
    return {r["todo_id"]: r for r in result.get("todos", []) if r.get("todo_id")}


def link(root, registry_path, goal_id, agent_id, request_id, todo_ids, evidence_ids):
    _entry(root, goal_id, agent_id, request_id)
    if not todo_ids and not evidence_ids:
        raise ValueError("at least one Core Todo or evidence reference required")
    if len(todo_ids) > 16 or len(evidence_ids) > 16:
        raise ValueError("too many context links")
    if any(not re.fullmatch(r"todo_[a-f0-9]{12}", x) for x in todo_ids):
        raise ValueError("invalid Core Todo id")
    if any(not re.fullmatch(r"sha256:[a-f0-9]{64}", x) for x in evidence_ids):
        raise ValueError("evidence references must be opaque SHA256 identifiers")
    if todo_ids:
        rows = _core_todos(registry_path, root, goal_id)
        for tid in todo_ids:
            row = rows.get(tid, {})
            if row.get("claimed_by") != agent_id and row.get("bound_agent") != agent_id:
                raise ValueError("linked Todo must belong to the receiving Agent")
    path = _root(root) / "links" / (request_id + ".json")
    with exclusive_file_lock(path.with_suffix(".lock")):
        old, error = _receipt(
            root,
            "links",
            {"request_id": request_id, "goal_id": goal_id, "agent_id": agent_id},
        )
        if error:
            raise ValueError(error)
        tids = sorted(set(old.get("todo_ids", [])) | set(todo_ids))
        refs = sorted(set(old.get("evidence_ids", [])) | set(evidence_ids))
        if len(tids) > 16 or len(refs) > 16:
            raise ValueError("too many context links")
        value = dict(
            request_id=request_id,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_ids=tids,
            evidence_ids=refs,
        )
        if any(old.get(k) != v for k, v in value.items()):
            _write(path, value | {"updated_at": _now()})
    return {"ok": True, **value}


def query(
    root,
    registry_path,
    *,
    goal_ids,
    owner_scope,
    channel_id=None,
    request_id=None,
    agent_id=None,
    offset=0,
    limit=8,
):
    """External callers see only requests from their exact audience, never raw text."""
    if request_id is not None and not re.fullmatch(r"[a-f0-9]{64}", request_id):
        raise ValueError("invalid context request id")
    if not owner_scope and not channel_id:
        raise ValueError("handoff audience required")
    if (
        type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 12
    ):
        raise ValueError("invalid context page")
    # Legacy entries have no channel. Recover only provider-owned provenance,
    # never infer audience from Goal identity or a model-authored field.
    legacy_channels = {}
    ingress_paths = sorted((_root(root) / "ingress").glob("*.json"))
    for path in ingress_paths[:2000] if not owner_scope else []:
        try:
            item = _read(path)
            legacy_channels.setdefault(item.get("source_id"), set()).add(
                item.get("channel")
            )
        except (OSError, ValueError, TypeError):
            continue
    rows, unreadable = [], 0
    paths = sorted(
        (_root(root) / "entries").glob(
            "*/" + (request_id + ".json" if request_id else "*.json")
        )
    )
    for path in paths[:2000]:
        try:
            row = _read(path)
            if row.get("goal_id") not in goal_ids:
                continue
            if agent_id is not None and row.get("agent_id") != agent_id:
                continue
            if request_id and row.get("request_id") != request_id:
                continue
            if not owner_scope:
                audience = row.get("source_channel")
                if audience != channel_id and not (
                    audience is None
                    and len(ingress_paths) <= 2000
                    and legacy_channels.get(row.get("source_id")) == {channel_id}
                ):
                    continue
            _entry(root, row["goal_id"], row["agent_id"], row["request_id"])
            rows.append(row)
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
    rows.sort(
        key=lambda row: (row.get("delivered_at") or "", row["request_id"]), reverse=True
    )
    projected, todos_cache = [], {}
    for row in rows[offset : offset + limit]:
        read, read_error = _receipt(root, "reads", row)
        decision, decision_error = _receipt(root, "decisions", row)
        links, link_error = _receipt(root, "links", row)
        warnings = [x for x in (read_error, decision_error, link_error) if x]
        tids = links.get("todo_ids", [])
        linked = []
        if tids:
            gid = row["goal_id"]
            if gid not in todos_cache:
                try:
                    todos_cache[gid] = _core_todos(registry_path, root, gid)
                except (OSError, ValueError, RuntimeError):
                    todos_cache[gid] = {}
            for tid in tids:
                todo = todos_cache[gid].get(tid)
                linked.append(
                    {
                        "todo_id": tid,
                        "status": todo.get("status") if todo else "unknown",
                        "title": _text(todo.get("text") or todo.get("title"))
                        if todo
                        else None,
                        "source": "core_todo_current_read"
                        if todo
                        else "core_todo_unavailable_or_not_found",
                    }
                )
        item = {k: row[k] for k in ("request_id", "goal_id", "agent_id", "source_id")}
        if row.get("source_kind") == "peer":
            item.update(source_kind="peer", source_agent_id=row["source_agent_id"], parent_request_id=row.get("parent_request_id"))
        if owner_scope and row.get("brief"):
            item["brief"] = row["brief"]
        item.update(
            delivery={"status": "delivered", "at": row.get("delivered_at")},
            read={
                "status": "supplied_to_receiver"
                if read
                else "decision_exists_read_receipt_missing"
                if decision
                else "not_recorded",
                "at": read.get("read_at"),
            },
            decision={
                "status": decision.get("decision", "not_recorded"),
                "at": decision.get("decided_at"),
            },
            linked_todos=linked,
            evidence_refs=links.get("evidence_ids", []),
            evidence_verification="receiver_linked_reference_not_independent_verification",
            warnings=warnings,
        )
        from .roundtrip import reply_status
        item["return_replies"] = reply_status(root, row)
        if owner_scope:
            item["decision"]["reason"] = decision.get("reason")
        else:
            item["decision"]["reason_visibility"] = "owner_only"
        projected.append(item)
    return {
        "source": "manager_context_receipts_and_core_todos",
        "observed_at": _now(),
        "rows": projected,
        "matched": len(rows),
        "coverage": {
            "scan_complete": len(paths) <= 2000,
            "legacy_audience_scan_complete": owner_scope or len(ingress_paths) <= 2000,
            "unreadable": unreadable,
        },
        "limitations": [
            "Missing historical timestamps remain unknown; file mtime is not an event time.",
            "Read receipt proves CLI provision, not model comprehension.",
            "Adoption and linked Todo completion do not establish the overall request outcome.",
        ],
    }
