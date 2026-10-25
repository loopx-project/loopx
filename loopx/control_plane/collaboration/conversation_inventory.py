"""Read original conversation requests through existing Inbox/receipt owners.

Host IO verifies the source Session and Goal lifetime before projecting content.
The shared TypeScript agenda decides what remains unfinished; this is not a
second work ledger or an acknowledgement of receiver work.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json

from ..content_digest import BARE_SHA256_PATTERN
from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle
from .inbox import _entry, _now, _read, _receipt, _request_lock, _root, read_inbox_transaction, reply_status
from .links import read_linked_work
from .source_chat_observation import is_source_chat_host, original_delivery_facts, original_turn_reader
from .source_grant_observation import source_context_target_authority


def _last_event(values):
    dated = []
    for value in values:
        if not isinstance(value, str):
            continue
        try:
            stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
            if stamp.tzinfo is not None:
                dated.append((stamp, value))
        except ValueError:
            pass
    return max(dated, default=(None, None), key=lambda pair: pair[0])[1]


def read_conversation_requests(root, registry, store, session, *, goal_ids, core_goal_ids,
                               validate_source_session, history=False, offset=0, limit=12, search=""):
    """Read only this host's exact source Session, with fresh caller read scope.

    The caller supplies provider-validated Goal visibility and rechecks it after
    this read. A model/caller-supplied channel, path or source id is never used.
    """
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 12:
        raise ValueError("invalid request page")
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError("request search must be at most 200 characters")
    query = search.strip().casefold()
    paths = sorted((_root(root) / "entries").glob("*/*.json"))
    rows, unreadable, cache, turn_readers = [], 0, {}, {}
    for path in paths[:2000]:
        try:
            candidate = _read(path)
            if candidate.get("source_kind") == "peer":
                continue
            if candidate.get("source_channel") not in {None, session["channel_id"]}:
                continue
            if not isinstance(candidate.get("request_id"), str) or not BARE_SHA256_PATTERN.fullmatch(candidate["request_id"]):
                raise ValueError("invalid request identity")
            route = _read(_root(root) / "roundtrips" / (candidate["request_id"] + ".json"))
            if route.get("channel_id") != session["channel_id"]:
                continue
            if not is_source_chat_host(root, route, store):
                raise ValueError("original source host changed")
            source_session = store.load_session(route["session_id"])
            if not source_session or source_session.get("channel_id") != session["channel_id"]:
                raise ValueError("original source Session unavailable")
            validate_source_session(source_session)
            gid, aid = route["goal_id"], route["agent_id"]
            if goal_ids is not None and gid not in goal_ids:
                continue
            sid = source_session["session_id"]
            if sid not in turn_readers:
                turn_readers[sid] = original_turn_reader(store, sid)
            turn = turn_readers[sid](route["client_turn_id"])
            if turn is None:
                raise ValueError("original source Turn unavailable")
            with collaboration_goal_scope(registry, goal_id=gid, agents=(), caller_goal_ref=route.get("goal_ref")) as scope:
                with _request_lock(root, candidate["request_id"], scope, path.with_suffix(".lock")):
                    row = _entry(root, gid, aid, candidate["request_id"], scope=scope)
                    if row.get("source_kind") == "peer":
                        continue
                    grant = source_context_target_authority(root, source_session, turn, {"goal_id": gid, "agent_id": aid})
                    decide_collaboration_lifecycle(scope, operation="original_request_inspect", record=row, route=route,
                        initial_delivery=original_delivery_facts(row, route, turn, source_id=grant["source_id"]))
                    observed = read_inbox_transaction(root, {"operation": "receipt_states", "rows": [row], "include_agenda": True})
                    state, agenda = observed["items"][0], observed["agenda_items"][0]
                    controlled = state.get("readback")
                    read, read_error = _receipt(root, "reads", row)
                    decision, decision_error = _receipt(root, "decisions", row)
                    replies = reply_status(root, row)
            # Request/return observations are version-consistent. Core work is
            # a separate current observation; never hold its request lock over
            # another authority's IO.
            work = read_linked_work(root, registry, row, cache,
                include_core_details=core_goal_ids is None or gid in core_goal_ids)
            warnings = list(state.get("warnings", [])) + work["warnings"]
            warnings += [error for error in (read_error, decision_error) if error]
            if not history and agenda is None:
                rows.append({"request_id": row["request_id"], "unfinished": False})
                continue
            control_unavailable = "request_control" in row and controlled is None
            brief = controlled["brief"] if controlled else None if control_unavailable else row.get("brief")
            assessment = controlled["decision"] if controlled else {
                "status": "unavailable" if control_unavailable else decision.get("decision", "not_recorded"),
                "at": None if control_unavailable else decision.get("decided_at"),
                "reason": None if control_unavailable else decision.get("reason")}
            read_at = controlled["read"].get("at") if controlled else read.get("read_at")
            rows.append({"request_id": row["request_id"], "goal_id": gid, "agent_id": aid,
                "unfinished": agenda is not None, "agenda": agenda,
                "control_state": (state.get("request_control_view") or {}).get("control_state"),
                "brief": brief, "source_question": turn.get("message", ""),
                "request_revision": controlled["request_revision"] if controlled else None if control_unavailable else 1,
                "read_status": controlled["card_read_status"] if controlled else
                    "unavailable" if read_error or control_unavailable else "supplied" if read else "pending",
                "decision": assessment["status"], "decision_reason": assessment.get("reason"),
                "returns": replies, "linked_todos": work["linked_todos"], "evidence_refs": work["evidence_refs"],
                "warnings": warnings, "source_session_id": source_session["session_id"], "source_turn_id": turn["turn_id"],
                "last_event_at": _last_event([row.get("delivered_at"), read_at, assessment.get("at"),
                    *(reply.get(key) for reply in replies for key in ("created_at", "delivered_at"))])})
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
    rows = list({row["request_id"]: row for row in rows}.values())
    pending_count = sum(row["unfinished"] for row in rows)
    visible = [row for row in rows if history or row["unfinished"]]
    # Presentation-only search over already authorized, version-consistent reads.
    # The typed agenda above remains the sole owner of unfinished status.
    if query:
        visible = [row for row in visible if any(query in str(value or "").casefold() for value in (
            row["request_id"], row["goal_id"], row["agent_id"], row["source_question"],
            (row.get("brief") or {}).get("purpose"), (row.get("brief") or {}).get("context")))]
    visible.sort(key=lambda row: (row.get("last_event_at") or "", row["request_id"]), reverse=True)
    page = visible[offset:offset + limit]
    coverage = {"scan_complete": len(paths) <= 2000, "unreadable": unreadable}
    # A page fence over observations, not an atomic snapshot or a new ledger.
    snapshot_digest = hashlib.sha256(json.dumps([visible, coverage, query], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"ok": True, "session_id": session["session_id"], "rows": page,
        "pending_count": pending_count, "matched": len(visible),
        "snapshot_digest": snapshot_digest,
        "next_offset": offset + len(page) if offset + len(page) < len(visible) else None,
        "coverage": coverage,
        "observed_at": _now()}
