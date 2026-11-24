"""Receipt projection and explicit Core links for manager context handoffs.

Receipts describe transport/consumption, never a second mutable work status.
"""

from __future__ import annotations

import json
from typing import Any

from . import _read, _root, evidence_goal_scope
from ...control_plane.collaboration.inbox import (
    _entry as _entry,
    _now as _now,
    _receipt as _receipt,
    record_read as record_read,
    controlled_request_readback,
    read_inbox_transaction,
)
from ...control_plane.collaboration.goal_instance_scope import (
    CollaborationGoalScope,
    CollaborationLifecycleRejected,
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
    scope_for_caller_goal_ref,
)
from ...control_plane.collaboration.links import read_linked_work
from ...control_plane.content_digest import (
    BARE_SHA256_PATTERN,
)
from ...control_plane.effect_runtime import effect_runtime_request_scope

_LifecycleDecisionCache = dict[
    tuple[Any, str, str, str], tuple[bool, dict[str, Any] | None]
]


def _lifecycle_cache_key(
    scope: Any,
    record: dict[str, Any],
) -> tuple[Any, str, str, str] | None:
    """Build a canonical cache key for pure `history_inspect` lifecycle checks."""
    if not isinstance(scope, CollaborationGoalScope):
        return None
    try:
        caller_key = json.dumps(
            scope.caller_goal_ref,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        current_key = json.dumps(
            scope.current_goal_ref,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
        record_key = json.dumps(
            record.get("goal_ref") if isinstance(record, dict) else None,
            ensure_ascii=False,
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return None
    return (scope.profile_id, caller_key, current_key, record_key)


def _decide_history_inspect(
    scope: Any,
    record: dict[str, Any],
    cache: _LifecycleDecisionCache,
) -> None:
    """Reuse `history_inspect` decisions across entries sharing exact Goal refs."""
    # TS `collaboration.goal_instance.decide` for `history_inspect` depends solely
    # on `(profile_id, caller_goal_ref, current_goal_ref, record_goal_ref)`.
    key = _lifecycle_cache_key(scope, record)
    if key is not None and key in cache:
        allowed, rejected_decision = cache[key]
        if not allowed:
            assert rejected_decision is not None
            raise CollaborationLifecycleRejected(rejected_decision)
        return
    try:
        decide_collaboration_lifecycle(
            scope,
            operation="history_inspect",
            record=record,
        )
    except CollaborationLifecycleRejected as exc:
        if key is not None:
            cache[key] = (False, exc.decision)
        raise
    if key is not None:
        cache[key] = (True, None)


def _verify_goal_group(
    root: Any,
    registry_path: Any,
    gid: str,
    group_rows: list[tuple[int, dict[str, Any]]],
    lifecycle_cache: _LifecycleDecisionCache,
) -> tuple[list[tuple[int, dict[str, Any]]], int]:
    """Validate matching entries for one `goal_id` under one Goal lifetime fence."""
    verified: list[tuple[int, dict[str, Any]]] = []
    unreadable = 0
    try:
        with collaboration_goal_scope(
            registry_path,
            goal_id=gid,
            agents=(),
            caller_goal_ref=None,
        ) as base_scope:
            for scan_index, row in group_rows:
                try:
                    goal_scope = scope_for_caller_goal_ref(
                        base_scope,
                        row.get("goal_ref"),
                    )
                    entry = _entry(
                        root,
                        row["goal_id"],
                        row["agent_id"],
                        row["request_id"],
                        scope=goal_scope,
                    )
                    _decide_history_inspect(goal_scope, entry, lifecycle_cache)
                    verified.append((scan_index, row))
                except (OSError, ValueError, KeyError, TypeError):
                    unreadable += 1
    except (OSError, ValueError, KeyError, TypeError):
        return [], len(group_rows)
    return verified, unreadable


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
    ingress_paths = (
        [] if owner_scope else sorted((_root(root) / "ingress").glob("*.json"))
    )
    legacy_channels: dict[Any, set[Any]] | None = None

    def _legacy_channels() -> dict[Any, set[Any]]:
        nonlocal legacy_channels
        if legacy_channels is None:
            loaded: dict[Any, set[Any]] = {}
            for ingress_path in ingress_paths[:2000]:
                try:
                    item = _read(ingress_path)
                    loaded.setdefault(item.get("source_id"), set()).add(
                        item.get("channel")
                    )
                except (OSError, ValueError, TypeError):
                    continue
            legacy_channels = loaded
        return legacy_channels

    unreadable = 0
    candidates_by_goal: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    paths = sorted(
        (_root(root) / "entries").glob(
            "*/" + (request_id + ".json" if request_id else "*.json")
        )
    )
    for scan_index, path in enumerate(paths[:2000]):
        try:
            row = _read(path)
            gid = row.get("goal_id")
            if gid not in goal_ids:
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
                    and _legacy_channels().get(row.get("source_id")) == {channel_id}
                ):
                    continue
            if not isinstance(gid, str):
                raise TypeError("invalid context goal id")
            candidates_by_goal.setdefault(gid, []).append((scan_index, row))
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1

    matched_rows: list[tuple[int, dict[str, Any]]] = []
    lifecycle_cache: _LifecycleDecisionCache = {}
    with effect_runtime_request_scope():
        # Hold each Goal's lifetime fence once per matched Goal and reuse pure
        # history_inspect decisions so lock-hold time stays bounded to O(unique refs).
        for gid, group_rows in candidates_by_goal.items():
            verified, group_unreadable = _verify_goal_group(
                root, registry_path, gid, group_rows, lifecycle_cache
            )
            matched_rows.extend(verified)
            unreadable += group_unreadable
        matched_rows.sort(key=lambda item: item[0])
        rows = [row for _, row in matched_rows]
        rows.sort(
            key=lambda row: (row.get("delivered_at") or "", row["request_id"]), reverse=True
        )
        projected, todos_cache = [], {}
        core_read_goals = set(evidence_goal_scope(root, channel_id, registry_path=registry_path) or []) if not owner_scope else set()
        page = rows[offset : offset + limit]
        returns = []
        if page:
            returns = read_inbox_transaction(root, {
                "operation": "receipt_states",
                "rows": [{"request_id": row["request_id"], "source_kind": row.get("source_kind")} for row in page],
                "return_statuses_only": True,
            })["return_items"]
        for row, return_statuses in zip(page, returns, strict=True):
            read, read_error = _receipt(root, "reads", row)
            decision, decision_error = _receipt(root, "decisions", row)
            work = read_linked_work(root, registry_path, row, todos_cache,
                                    include_core_details=owner_scope or row["goal_id"] in core_read_goals)
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
            item["return_replies"] = return_statuses
            if owner_scope:
                item["decision"]["reason"] = decision.get("reason")
            else:
                item["decision"]["reason_visibility"] = "owner_only"
            controlled = controlled_request_readback(root, row)
            if controlled is not None:
                item["warnings"] += controlled["warnings"]
                current = controlled.get("readback")
                if current is not None:
                    item.update(request_revision=current["request_revision"], read=current["read"], decision=dict(current["decision"]))
                    if owner_scope:
                        item["brief"] = current["brief"]
                        item["request_control"] = controlled["request_control_view"]
                    else:
                        item["decision"].pop("reason", None)
                        item["decision"]["reason_visibility"] = "owner_only"
                        item["request_control"] = {"control_state": controlled["request_control_view"]["control_state"]}
                else:
                    item["request_control"] = None
                    item["read"] = {"status": "unavailable", "at": None}
                    item["decision"] = {"status": "unavailable", "at": None}
                revisions = controlled.get("result_revisions")
                for reply in item["return_replies"]:
                    key = reply.get("result_key", reply["phase"])
                    reply["request_revision"] = next((value["request_revision"] for value in revisions or [] if value["result_key"] == key), None)
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
