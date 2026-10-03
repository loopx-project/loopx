"""Receipt projection and explicit Core links for manager context handoffs.

Receipts describe transport/consumption, never a second mutable work status.
"""

from __future__ import annotations

from . import _read, _root
from ...control_plane.collaboration.inbox import (
    _entry as _entry,
    _now as _now,
    _receipt as _receipt,
    record_read as record_read,
)
from ...control_plane.collaboration.goal_instance_scope import (
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
)
from ...control_plane.collaboration.links import read_linked_work
from ...control_plane.content_digest import (
    BARE_SHA256_PATTERN,
)



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
    if request_id is not None and not BARE_SHA256_PATTERN.fullmatch(request_id):
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
            with collaboration_goal_scope(
                registry_path,
                goal_id=row["goal_id"],
                agents=(),
                caller_goal_ref=row.get("goal_ref"),
            ) as goal_scope:
                entry = _entry(
                    root,
                    row["goal_id"],
                    row["agent_id"],
                    row["request_id"],
                    scope=goal_scope,
                )
                decide_collaboration_lifecycle(
                    goal_scope,
                    operation="history_inspect",
                    record=entry,
                )
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
        work = read_linked_work(root, registry_path, row, todos_cache, include_core_details=owner_scope)
        warnings = [x for x in (read_error, decision_error) if x] + work["warnings"]
        item = {
            key: row[key]
            for key in ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")
            if key in row
        }
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
            linked_todos=work["linked_todos"],
            evidence_refs=work["evidence_refs"],
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
