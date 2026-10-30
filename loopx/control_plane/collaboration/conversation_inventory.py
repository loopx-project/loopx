"""Read original conversation requests through existing Inbox/receipt owners.

Host IO verifies the source Session and Goal lifetime before projecting content.
The shared TypeScript agenda decides what remains unfinished; this is not a
second work ledger or an acknowledgement of receiver work.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime
import hashlib
import json

from ..content_digest import BARE_SHA256_PATTERN
from .goal_instance_scope import CollaborationLifecycleRejected, collaboration_goal_scope, decide_collaboration_lifecycle
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


@contextmanager
def _locked_request_batch(root, scope, requests):
    """Retain each request version for one page-sized typed observation."""
    available, lock_keys = set(), set()
    with ExitStack() as locks:
        for path, _, request_id in sorted(requests, key=lambda item: (item[2], str(item[0]))):
            # Exact requests share their guard across legacy entry locations.
            # Legacy records still use their individual entry lock.
            key = (request_id, None if scope.exact else path)
            if key not in lock_keys:
                try:
                    locks.enter_context(_request_lock(root, request_id, scope, path.with_suffix(".lock")))
                except (OSError, ValueError, KeyError, TypeError):
                    continue
                lock_keys.add(key)
            available.add(path)
        yield available


def _read_receipt_batch(root, rows):
    if not rows:
        return []
    try:
        observed = read_inbox_transaction(root, {"operation": "receipt_states", "rows": rows, "include_agenda": True})
        states = list(zip(observed["items"], observed["agenda_items"], strict=True))
        if len(states) != len(rows):
            raise ValueError("Inbox receipt count does not match requests")
        return states
    except (OSError, ValueError, KeyError, TypeError):
        # A failed batch is only a read. Retry its rows independently while
        # all their guards remain held; one unavailable record must not hide
        # its readable neighbors or become an empty successful observation.
        return [None] if len(rows) == 1 else [_read_receipt_batch(root, [row])[0] for row in rows]


def _project_request_observations(root, registry, observations, *, core_goal_ids, history):
    """Combine captured receipt facts with current linked Core observations."""
    rows, unreadable, cache = [], 0, {}
    for row, source_session_id, turn, state, agenda, controlled, read, read_error, decision, decision_error, replies in observations:
        try:
            gid, aid = row["goal_id"], row["agent_id"]
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
                "warnings": warnings, "source_session_id": source_session_id, "source_turn_id": turn["turn_id"],
                "last_event_at": _last_event([row.get("delivered_at"), read_at, assessment.get("at"),
                    *(reply.get(key) for reply in replies for key in ("created_at", "delivered_at"))])})
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
    return rows, unreadable


def _request_groups(root, store, session, goal_ids, validate_source_session):
    """Discover source-validated routes before acquiring any Goal guard."""
    paths = sorted((_root(root) / "entries").glob("*/*.json"))
    unreadable, groups = 0, {}
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
            if not isinstance(gid, str) or not isinstance(aid, str):
                raise ValueError("invalid request target")
            key = (gid, json.dumps(route.get("goal_ref"), sort_keys=True))
            groups.setdefault(key, []).append((path, route, candidate["request_id"]))
        except (OSError, ValueError, KeyError, TypeError):
            unreadable += 1
    return groups, unreadable, len(paths) <= 2000


def read_conversation_requests(root, registry, store, session, *, goal_ids, core_goal_ids,
                               validate_source_session, history=False, offset=0, limit=12, search=""):
    """Read only this host's exact source Session, with fresh caller read scope.

    The caller supplies provider-validated Goal visibility and rechecks it after
    this read. A model/caller-supplied channel, path or source id is never used.
    """
    # Response windows may restore four loaded pages; authority observations
    # below still retain at most one normal page of request guards at a time.
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 48:
        raise ValueError("invalid request page")
    if not isinstance(search, str) or len(search) > 200:
        raise ValueError("request search must be at most 200 characters")
    query = search.strip().casefold()
    groups, unreadable, scan_complete = _request_groups(root, store, session, goal_ids, validate_source_session)
    turn_readers, diagnostics, work_reads = {}, [], []
    # Reuse only the held lifetime guard, never an authorization or request
    # snapshot. Resolve sources outside it first: their provider checks can
    # read another authority. Each source/Turn and request is reread below.
    # Keep each guard bounded to a page-sized batch so a long inventory does
    # not exclude Goal writers for the entire history scan.
    batch_size = 12
    batches = [(key, items[start:start + batch_size]) for key, items in groups.items()
               for start in range(0, len(items), batch_size)]
    for (gid, _), requests in batches:
        start, failed = len(work_reads), 0
        try:
            with (
                collaboration_goal_scope(registry, goal_id=gid, agents=(),
                                         caller_goal_ref=requests[0][1].get("goal_ref")) as scope,
                _locked_request_batch(root, scope, requests) as available,
            ):
                prepared = []
                for path, route, request_id in requests:
                    if path not in available:
                        failed += 1
                        continue
                    try:
                        source_session = store.load_session(route["session_id"])
                        if not source_session or source_session.get("channel_id") != session["channel_id"]:
                            raise ValueError("original source Session unavailable")
                        validate_source_session(source_session)
                        sid = source_session["session_id"]
                        if sid not in turn_readers:
                            turn_readers[sid] = original_turn_reader(store, sid)
                        turn = turn_readers[sid](route["client_turn_id"])
                        if turn is None:
                            raise ValueError("original source Turn unavailable")
                        row = _entry(root, gid, route["agent_id"], request_id, scope=scope)
                        if row.get("source_kind") == "peer":
                            continue
                        grant = source_context_target_authority(root, source_session, turn,
                            {"goal_id": gid, "agent_id": route["agent_id"]})
                        try:
                            decide_collaboration_lifecycle(scope, operation="original_request_inspect", record=row, route=route,
                                initial_delivery=original_delivery_facts(row, route, turn, source_id=grant["source_id"]))
                        except CollaborationLifecycleRejected as exc:
                            # Only this typed refusal proves the route and source
                            # matched before the conflicting receipt was observed.
                            # Withhold the brief, work and completion state as before.
                            if exc.decision.get("delivery_proof_reason") == "handoff_receipt_conflict" and len(diagnostics) < 12:
                                diagnostics.append({"request_id": row["request_id"], "goal_id": gid, "agent_id": row["agent_id"],
                                    "code": exc.decision["code"], "reason": exc.decision["delivery_proof_reason"]})
                            raise
                        # Do not retain full model responses while waiting for
                        # linked-work IO; only the displayed question is used.
                        source_turn = {"turn_id": turn["turn_id"], "message": turn.get("message", "")}
                        prepared.append((row, source_session["session_id"], source_turn))
                    except (OSError, ValueError, KeyError, TypeError):
                        failed += 1
                observations = _read_receipt_batch(root, [row for row, _, _ in prepared])
                for (row, source_session_id, source_turn), observed in zip(prepared, observations, strict=True):
                    if observed is None:
                        failed += 1
                        continue
                    try:
                        state, agenda = observed
                        controlled = state.get("readback")
                        read, read_error = _receipt(root, "reads", row)
                        decision, decision_error = _receipt(root, "decisions", row)
                        replies = reply_status(root, row)
                        work_reads.append((row, source_session_id, source_turn, state, agenda, controlled,
                                           read, read_error, decision, decision_error, replies))
                    except (OSError, ValueError, KeyError, TypeError):
                        failed += 1
        except (OSError, ValueError, KeyError, TypeError):
            del work_reads[start:]
            unreadable += len(requests)
        else:
            unreadable += failed
    # Request/return observations are version-consistent. Core work is a
    # separate observation: release all lifetime/request guards before its IO.
    rows, projection_failures = _project_request_observations(root, registry, work_reads,
        core_goal_ids=core_goal_ids, history=history)
    unreadable += projection_failures
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
    coverage = {"scan_complete": scan_complete, "unreadable": unreadable, "diagnostics": diagnostics}
    # A page fence over observations, not an atomic snapshot or a new ledger.
    snapshot_digest = hashlib.sha256(json.dumps([visible, coverage, query], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return {"ok": True, "session_id": session["session_id"], "rows": page,
        "pending_count": pending_count, "matched": len(visible),
        "snapshot_digest": snapshot_digest,
        "next_offset": offset + len(page) if offset + len(page) < len(visible) else None,
        "coverage": coverage,
        "observed_at": _now()}
