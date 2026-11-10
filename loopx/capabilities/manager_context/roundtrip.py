"""A delegated request stays open until its receiver returns a conclusion.

The existing inbox owns request/decision truth; this module owns only return
routing, receiver-authored replies, and publication receipts. No model polling.
"""

from __future__ import annotations

import logging
import json
import re
from hashlib import sha256
import threading
from itertools import islice
from contextlib import contextmanager, ExitStack, nullcontext
from urllib.parse import quote
from datetime import datetime, timezone

from ...extensions.background_runtime import HostedWorker

from . import _root, _read, _write, _hash, authority, target_authority
from .tracking import _entry, _now
from ...file_lock import (
    LockAcquisitionPolicy,
    LockAcquireTimeoutError,
    exclusive_file_lock,
)
from ...control_plane.collaboration import conversation_scope
from ...control_plane.collaboration.goal_instance_scope import (
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
)
from ...control_plane.collaboration.source_policy_fence import source_policy_fence
from ...control_plane.projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from ...presentation.public_safety import scan_public_boundary_text
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_request_scope, effect_runtime_result

from ...control_plane.collaboration.inbox import (
    _request_lock,
    iter_result_paths,
    DELIVERY_STATUSES as DELIVERY_STATUSES,
    DELIVERY_ERRORS as DELIVERY_ERRORS,
    reply_status as reply_status,
)
from ...control_plane.content_digest import BARE_SHA256_PATTERN
from ...control_plane.collaboration.source_chat_observation import original_delivery_facts
from ...chat import ChatMessagePayloadConflictError

class ReturnResolutionBlocked(ValueError, RuntimeError):
    """A return cannot be resolved; ``reason`` is the provider-neutral code.

    Inheriting ``ValueError`` keeps existing adapter call sites and their
    handling unchanged while the typed reason is what the state machine reads.
    """

    def __init__(self, reason, message):
        decision = _verification_decision({
            "verification_performed": False, "reply_verified": False, "blocker": reason,
        })
        if decision["status"] != "explicit_unverified":
            raise ValueError("unsupported return resolution reason")
        self.reason = reason
        super().__init__(message)


def _verification_decision(outcome):
    return dict(
        effect_runtime_result(
            "manager.return_delivery.classify_verification", {"outcome": outcome}
        )
    )


def _register_unlocked(root, row, session, turn, *, source_store=None):
    value = {
        key: row[key]
        for key in ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")
        if key in row
    }
    value.update(
        session_id=session["session_id"],
        client_turn_id=turn["client_turn_id"],
        channel_id=session["channel_id"],
    )
    path = _root(root) / "roundtrips" / (row["request_id"] + ".json")
    source_root = None
    if source_store is not None:
        from ...control_plane.collaboration.source_chat_observation import trusted_source_chat_root

        source_root = trusted_source_chat_root(source_store, value)
        if source_root != str(root.resolve()):
            value["source_chat_runtime_root"] = source_root
    if path.exists():
        old = _read(path)
        if (any(old.get(key) != item for key, item in value.items()
                if key != "source_chat_runtime_root")
                or (source_root is not None and old.get("source_chat_runtime_root")
                    not in (None, source_root))):
            raise ValueError("context return route conflict")
        if "source_chat_runtime_root" in value and "source_chat_runtime_root" not in old:
            _write(path, old | {"source_chat_runtime_root": source_root})
    else:
        _write(path, value | {"registered_at": _now()})
    saved = _read(path)
    if any(saved.get(key) != value for key, value in value.items()):
        raise ValueError("context return route readback failed")


def register(root, row, session, turn, *, scope=None, source_store=None):
    """Called by trusted Chat delivery, never with model-authored routing."""
    path = _root(root) / "roundtrips" / (row["request_id"] + ".json")
    with _request_lock(
        root,
        row["request_id"],
        scope,
        path.with_suffix(".lock"),
    ):
        _register_unlocked(root, row, session, turn, source_store=source_store)


def _route(root, row, *, scope=None):
    path = _root(root) / "roundtrips" / (row["request_id"] + ".json")
    if not path.exists():
        if scope is not None and scope.exact:
            raise ValueError("exact context return route unavailable")
        # Legacy opt-in only when the receiver actually publishes a reply. Recover
        # exact trusted Chat receipts, never infer a destination from Goal alone.
        from ...chat_store import ChatSessionStore

        store = ChatSessionStore(root)
        matches = []
        for session in store.list_sessions():
            for p in (store.root / "sessions" / session["session_id"] / "turns").glob(
                "*.json"
            ):
                try:
                    turn = _read(p)
                except (OSError, ValueError):
                    # An unrelated damaged/oversized historical turn cannot
                    # prevent recovery of this exact request's return receipt.
                    continue
                response = turn.get("response") or {}
                receipts = response.get("context_handoff_receipts")
                if receipts is None:
                    receipts = [response.get("context_handoff_receipt") or {}]
                if isinstance(receipts, list) and sum(
                    isinstance(receipt, dict) and receipt.get("request_id") == row["request_id"]
                    for receipt in receipts
                ) == 1:
                    matches.append((session, turn))
        if len(matches) != 1:
            raise ReturnResolutionBlocked(
                "original_route_unavailable", "original Chat return route unavailable or ambiguous"
            )
        register(root, row, *matches[0])
    value = _read(path)
    if value.get("kind") == "peer" and (row.get("source_kind") != "peer" or value.get("source_agent_id") != row.get("source_agent_id")):
        raise ReturnResolutionBlocked("original_route_unavailable", "peer return route identity mismatch")
    if any(
        value.get(k) != row.get(k)
        for k in ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")
        if k in row
    ):
        raise ReturnResolutionBlocked("original_route_unavailable", "context return route identity mismatch")
    return value


def report(
    root,
    goal_id,
    agent_id,
    request_id,
    phase,
    text,
    *,
    registry=None,
    caller_goal_ref=None,
    scope=None,
    update_id=None,
    attachment_refs=None,
    workspace=None,
    request_revision=None,
    remaining_work=None,
):
    """Chat audience adapter; the shared Inbox owns result validation/persistence."""
    if scope is None and registry is not None:
        from ...control_plane.collaboration.goal_instance_scope import (
            collaboration_goal_scope,
        )

        with collaboration_goal_scope(
            registry,
            goal_id=goal_id,
            agents=(),
            caller_goal_ref=caller_goal_ref,
        ) as goal_scope:
            return report(
                root,
                goal_id,
                agent_id,
                request_id,
                phase,
                text,
                scope=goal_scope,
                update_id=update_id,
                attachment_refs=attachment_refs,
                workspace=workspace,
                request_revision=request_revision,
                remaining_work=remaining_work,
            )
    row = _entry(root, goal_id, agent_id, request_id, scope=scope)
    route = _route(root, row, scope=scope)
    if route.get("kind") == "peer" and phase != "conclusion":
        raise ValueError("peer replies require a conclusion; report a concrete result or blocker")
    audience_text = text + ("\n" + json.dumps(remaining_work, ensure_ascii=False) if remaining_work is not None else "")
    if (route["channel_id"] != "peer" and not conversation_scope(route)["private_conversation"]
            and not scan_public_boundary_text(audience_text)["ok"]):
        raise ValueError("reply contains private boundary material; write an audience-safe conclusion")
    from ...control_plane.collaboration.inbox import record_result
    attachments = None
    if attachment_refs:
        # Only the independently verified bound-owner App is qualified here.
        # Group and local-Web file disclosure need their own qualified surface.
        if scope is None or not str(route.get("channel_id", "")).startswith("manager.external.native."):
            raise ValueError("result attachments require a registered Goal and bound-owner App return")
        from ...control_plane.collaboration.result_files import snapshot_result_files

        decide_collaboration_lifecycle(scope, operation="result_publish", record=row, route=route)
        attachments = snapshot_result_files(root, scope, attachment_refs, workspace=workspace)
    return {
        **record_result(
            root,
            row,
            phase,
            text,
            scope=scope,
            route=route,
            update_id=update_id,
            attachments=attachments,
            request_revision=request_revision,
            remaining_work=remaining_work,
        ),
        "status": (
            "queued_for_requester"
            if route.get("kind") == "peer"
            else "queued_for_original_conversation"
        ),
    }


def _chat_return_files(root, route, path, reply, message, store, registry):
    """Read the published result, never its mutable workspace source or sender."""
    if not reply.get("attachments") or store is None:
        return []
    session = store.load_session(route["session_id"])
    turn = store.load_turn(route["session_id"], message.get("turn_id")) if message.get("turn_id") else None
    if (not session or not turn or message.get("role") not in {"agent", "assistant"}
            or session.get("channel_id") != route.get("channel_id")
            or turn.get("client_turn_id") != route.get("client_turn_id")
            or turn.get("session_id") != session["session_id"]):
        return []
    if route.get("goal_ref") and registry is None:
        return []
    with (collaboration_goal_scope(registry, goal_id=route["goal_id"], agents=(),
            caller_goal_ref=route.get("goal_ref")) if route.get("goal_ref") else nullcontext(None)) as scope:
        row = _entry(root, route["goal_id"], route["agent_id"], route["request_id"], scope=scope)
        if any(row.get(key) != route.get(key) for key in ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")):
            return []
        from ...control_plane.collaboration.inbox import inspect_result_identity
        inspected = inspect_result_identity(reply, row, path)
        if inspected is None:
            return []
        attachments = inspected["value"].get("attachments") or []
    base = f"/api/chat/sessions/{quote(session['session_id'], safe='')}/messages/{quote(message['message_id'], safe='')}/files/"
    return [{"id": f"result-file.{item['sha256']}.{index}", "name": item["name"], "size": item["size"],
             "mime_type": "application/octet-stream", "data_url": base + f"{item['sha256']}.{index}"}
            for index, item in enumerate(attachments)]


def read_chat_return_file(root, store, session_id, message_id, file_id, *, registry):
    """Resolve one file through its original saved message, then verify bytes."""
    match = re.fullmatch(r"([a-f0-9]{64})\.([0-3])", file_id)
    if not match:
        raise KeyError("returned file unavailable")
    digest = match[1]
    message = next((item for item in store.messages(session_id, strict=True)
                    if item.get("message_id") == message_id and item.get("origin") == "manager_followup"), None)
    if message is None:
        raise KeyError("returned file unavailable")
    # Only files independently resolved from the immutable result can authorize
    # a download; persisted input attachments are not file-store locators.
    projected = project_chat_return_deliveries(root, session_id, [{**message, "attachments": []}],
        source_store=store, registry=registry)[0]
    attachment = next((item for item in projected.get("attachments", [])
                       if item["id"] == "result-file." + file_id), None)
    if attachment is None:
        raise KeyError("returned file unavailable")
    from ...control_plane.collaboration.result_files import read_result_file
    return attachment, read_result_file(root, {"sha256": digest, "size": attachment["size"]})


def project_chat_return_deliveries(root, session_id, messages, *, source_store=None, registry=None):
    """Attach public-safe delivery readback to returned Chat transcript rows."""

    from ...control_plane.collaboration.source_chat_observation import is_source_chat_host

    pending_message_ids = {
        message_id
        for message in messages
        if message.get("origin") == "manager_followup"
        and isinstance((message_id := message.get("message_id")), str)
        and message_id.startswith("handoff.")
    }
    if not pending_message_ids:
        return list(messages)
    statuses, files = {}, {}
    visible = {message.get("message_id"): message for message in messages}
    routes = _root(root) / "roundtrips"

    def candidate_paths():
        # The original Turn normally names its requests. Prefer those locators
        # over scanning every conversation's routes for every history page.
        # They are only hints: the route, source host and result identity below
        # still have to match. Lost/older receipts retain the exhaustive fallback.
        known = set()
        if source_store is not None:
            turn_ids = dict.fromkeys(
                message.get("turn_id") for message in messages
                if message.get("message_id") in pending_message_ids
                and isinstance(message.get("turn_id"), str) and message.get("turn_id")
            )
            for turn_id in turn_ids:
                try:
                    turn = source_store.load_turn(session_id, turn_id) or {}
                    if turn.get("session_id") != session_id or turn.get("turn_id") != turn_id:
                        continue
                    response = turn.get("response") or {}
                    if not isinstance(response, dict):
                        continue
                    receipts = response.get("context_handoff_receipts")
                    if receipts is None:
                        receipts = [response.get("context_handoff_receipt")]
                    for receipt in receipts if isinstance(receipts, list) else []:
                        request_id = receipt.get("request_id") if isinstance(receipt, dict) else None
                        if (isinstance(request_id, str) and BARE_SHA256_PATTERN.fullmatch(request_id)
                                and request_id not in known):
                            known.add(request_id)
                            yield routes / (request_id + ".json")
                except (OSError, ValueError, KeyError, TypeError):
                    continue
        # This part is lazy: a fully resolved page never enumerates the directory.
        for path in sorted(routes.glob("*.json")):
            if path.stem not in known:
                yield path

    for path in candidate_paths():
        try:
            route = _read(path)
            if (route.get("session_id") != session_id
                    or (source_store is not None and not is_source_chat_host(root, route, source_store))):
                continue
            request_id = str(route.get("request_id") or "")
            if path.stem != request_id or not BARE_SHA256_PATTERN.fullmatch(request_id):
                continue
            def observe_result(result_path, reply):
                mid = "handoff." + _hash([request_id, result_path.stem])
                try:
                    files[mid] = _chat_return_files(root, route, result_path, reply,
                        visible[mid], source_store, registry)
                except (OSError, ValueError, KeyError, TypeError):
                    # Preserve delivery history even when a file cannot be read.
                    files[mid] = []
            # A legacy/unmatched row can require the exhaustive route fallback.
            # Match immutable result keys before reading potentially long reply
            # bodies and delivery sidecars for off-page results. The existing
            # result owner still validates every selected status on this read.
            for item in reply_status(
                root, route,
                include_result_key=lambda key: "handoff." + _hash([request_id, key]) in pending_message_ids,
                observe_result=observe_result,
            ):
                message_id = "handoff." + _hash([request_id, item.get("result_key", item["phase"])])
                if message_id not in pending_message_ids:
                    continue
                statuses[message_id] = {
                    "schema_version": "manager_return_delivery_status_v0",
                    **item,
                }
                pending_message_ids.discard(message_id)
            if not pending_message_ids:
                break
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return [
        (
            {**message, "return_delivery": statuses[message.get("message_id")],
             **({"attachments": [*(item for item in (message.get("attachments") or [])
                                    if item.get("id") not in {file["id"] for file in files[message["message_id"]]}),
                                  *files[message["message_id"]]]}
                if files.get(message.get("message_id")) else {})}
            if message.get("message_id") in statuses
            else message
        )
        for message in messages
    ]


def project_chat_session_snapshot(root, store, session_id, *, registry,
                                  message_limit=None, message_cursor=None, turn_id=None,
                                  resume_from=None, project_history_messages=None):
    """Project return delivery state into one existing Chat snapshot."""

    strict = message_limit is not None or resume_from is not None
    snapshot = (store.session_snapshot(session_id, strict_messages=True) if strict
                else store.session_snapshot(session_id))
    if turn_id is not None:
        if message_limit is None:
            raise ValueError("a selected original Turn requires paged history")
        turn = store.load_turn(session_id, turn_id)
        if not turn or turn.get("session_id") != session_id or turn.get("turn_id") != turn_id:
            raise ValueError("original Turn is unavailable from this Session")
        # OriginalRequest already displays only this Turn. Select it before
        # paging/projection, retaining every message and the strict journal read.
        snapshot["messages"] = [row for row in snapshot["messages"] if row.get("turn_id") == turn_id]
    elif project_history_messages is not None:
        # Extension-owned, presentation-only rows must join the frozen page
        # prefix. A selected Turn only reads its canonical messages.
        snapshot["messages"] = project_history_messages(snapshot["messages"])
    if resume_from is not None and not 0 <= len(snapshot["messages"]) - resume_from <= 200:
        return None  # Bound the complete visible stream, including provider-only notices.
    from .chat_history import page_chat_messages
    page = (page_chat_messages(session_id, snapshot["messages"], limit=message_limit,
                              cursor=message_cursor, turn_id=turn_id) if message_limit is not None else None)
    context_messages = snapshot["messages"]
    if page is not None:
        context_messages = context_messages[:page["message_page"]["total"]]
        snapshot["messages"] = page["messages"]
    snapshot["messages"] = project_chat_return_deliveries(
        root, session_id, snapshot["messages"], source_store=store, registry=registry
    )
    from .presentation import project_collaboration
    # Use the frozen context to choose the carrier, projecting only this page.
    # A question and answer can straddle pages without duplicating a commitment
    # or re-reading every off-page Turn for each page.
    snapshot["messages"] = project_collaboration(store, root, session_id, snapshot["messages"],
        registry=registry, context_messages=context_messages)
    # Provider-owned history joins the selected page at the composition root.
    # Core capabilities never import an extension projection sink.
    if page is not None:
        snapshot["message_page"] = page["message_page"]
    return snapshot



def parse_chat_history_selection(query):
    """Validate one HTTP history selection before any recovery observation."""
    if (set(query) - {"message_limit", "message_cursor", "turn_id", "known_history"}
            or any(len(values) != 1 or not values[0] for values in query.values())
            or ("message_limit" not in query and set(query) & {"message_cursor", "turn_id", "known_history"})):
        raise ValueError("invalid history page arguments")
    limit = int(query["message_limit"][0]) if "message_limit" in query else None
    if limit is not None and not 1 <= limit <= 200:
        raise ValueError("message_limit must be between 1 and 200")
    basis = query.get("known_history", [None])[0]
    parsed_basis = None
    if basis is not None:
        if limit != 200 or "turn_id" in query or "message_cursor" in query:
            raise ValueError("invalid history resume arguments")
        count_text, _, proof = basis.partition(":")
        if not re.fullmatch(r"[1-9][0-9]{0,3}", count_text):
            raise ValueError("invalid known history proof")
        count = int(count_text)
        if count > 5000 or len(proof) != ((count + 199) // 200) * 64 or not re.fullmatch(r"[a-f0-9]+", proof):
            raise ValueError("invalid known history proof")
        parsed_basis = (basis, count, proof)
    return {"message_limit": limit, "message_cursor": query.get("message_cursor", [None])[0],
            "turn_id": query.get("turn_id", [None])[0], "known_history": parsed_basis}


def _visible_history_page_digest(session_id, offset, messages):
    # Bind the displayed rows, including fresh provider projections, to the source.
    body = json.dumps([session_id, offset, messages], sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    return sha256(body).hexdigest()


def read_chat_history_selection(root, store, session_id, *, registry, selection, project_provider,
                                project_history_messages=None):
    """Compose the same provider projections for page reads and suffix proof."""
    basis = selection["known_history"]
    if basis is None:
        snapshot = project_chat_session_snapshot(root, store, session_id, registry=registry,
            project_history_messages=project_history_messages,
            **{key: value for key, value in selection.items() if key != "known_history"})
        snapshot["messages"] = project_provider(snapshot["messages"])
        if selection["message_limit"] == 200 and selection["turn_id"] is None:
            page = snapshot["message_page"]
            if 1 <= page["total"] <= 5000:
                page["visible_page_sha256"] = _visible_history_page_digest(
                    session_id, page["offset"], snapshot["messages"])
        return snapshot

    basis, count, proof = basis
    resume = {"schema_version": "loopx_chat_history_resume_v1"}
    restart = {"ok": True, **resume, "status": "restart"}
    snapshot = project_chat_session_snapshot(root, store, session_id, registry=registry, resume_from=count,
        project_history_messages=project_history_messages)
    if snapshot is None or len(snapshot["messages"]) > 5000:
        return restart
    messages = project_provider(snapshot["messages"])
    current_prefix = "".join(
        _visible_history_page_digest(session_id, offset, messages[offset:min(offset + 200, count)])
        for offset in range(0, count, 200))
    if current_prefix != proof:
        return restart
    total = len(messages)
    # The old last page may have been short; earlier pages were just verified.
    unchanged = count if total == count else count - count % 200
    next_proof = proof[:((unchanged + 199) // 200) * 64] + "".join(
        _visible_history_page_digest(session_id, offset, messages[offset:offset + 200])
        for offset in range(unchanged, total, 200))
    return {**snapshot, "messages": messages[count:], "history_resume": {
        **resume, "base": basis, "next": f"{total}:{next_proof}",
    }}


RETURN_DELIVERY_TRANSACTION = "manager_return_delivery_transaction_v0"


def _hold_return_source_fence(root, route, effects):
    # Match the grant writer through transcript/provider publication. Owner-only
    # returns stay independent of source policy and create no policy storage.
    if conversation_scope(route)["kind"] == "external_audience":
        effects.enter_context(source_policy_fence(_root(root) / "policy.json"))


@contextmanager
def _return_source(host, path, frozen_route, original_grant=None):
    """Observe original Chat/provider sources under their existing lifetime fence."""
    root, registry, store, exact = (host[key] for key in ("root", "registry", "store", "exact"))
    with ExitStack() as guards:
        try:
            reply = _read(path)
            scope = guards.enter_context(collaboration_goal_scope(
                registry, goal_id=reply["goal_id"],
                agents=(reply["agent_id"],) if exact else (),
                caller_goal_ref=reply.get("goal_ref") if exact else None,
            ))
            if exact:
                guards.enter_context(_request_lock(root, reply["request_id"], scope, path.with_suffix(".lock")))
            row = _entry(root, reply["goal_id"], reply["agent_id"], reply["request_id"], scope=scope)
            route = _route(root, row, scope=scope)
            if route != frozen_route:
                raise ValueError("context return route changed during admission")
            session = store.load_session(route["session_id"])
            turn = store.turn_for_client(route["session_id"], route["client_turn_id"])
            grant = original_grant
            if grant is None and session and turn:
                grant = (target_authority(root, session=session, turn=turn,
                    target={key: row[key] for key in ("goal_id", "agent_id")})
                    if exact else authority(root, registry, session, turn))
            target = scope.target(reply["agent_id"])
            facts = {
                "entry": row,
                "entry_path": str(_root(root) / "entries" / _hash(target) / (reply["request_id"] + ".json")),
                "route": route, "session": session, "turn": turn,
                "grant": grant or {"targets": []},
                "scope": {"profile_id": scope.profile_id,
                    "caller_goal_ref": scope.caller_goal_ref,
                    "current_goal_ref": scope.current_goal_ref},
                "initial_delivery": original_delivery_facts(row, route, turn,
                    source_id=(grant or {}).get("source_id")) if turn else None,
            }
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            facts = {"error": exc.reason if isinstance(exc, ReturnResolutionBlocked) else "return_source_unavailable"}
        yield facts


def _return_transaction(host, path, phase, request):
    root, exact = (host[key] for key in ("root", "exact"))
    payload = {key: value for key, value in request.items() if key != "facts"}
    observed = dict(request["facts"])
    for field, keys in {
        "session": ("session_id", "goal_id", "channel_id", "status", "project_context", "steward_context"),
        "turn": ("turn_id", "client_turn_id", "status"),
        "grant": ("source_id", "targets"),
    }.items():
        if isinstance(observed.get(field), dict):
            observed[field] = {key: observed[field][key] for key in keys if key in observed[field]}
    result = effect_runtime_result("manager.return_delivery.transact", {
        "schema_version": RETURN_DELIVERY_TRANSACTION,
        "runtime_root": str(root.resolve()), "request_id": path.parent.name,
        "result_key": path.stem, "phase": phase, "now": host["clock"]().isoformat(),
        "exact": exact, "facts": observed, **payload,
    }, retry_safe=False)
    if (not isinstance(result, dict) or result.get("schema_version") != RETURN_DELIVERY_TRANSACTION
            or result.get("action") not in {"skip", "private", "verify", "send"}
            or type(result.get("processed")) is not int or result["processed"] not in {0, 1}):
        raise RuntimeError("return delivery transaction result shape mismatch")
    return result


def _return_provider(plan, source, sender, record_attempt):
    route, session, turn = (source[key] for key in ("route", "session", "turn"))
    # The typed transaction owns the canonical result identity. Transports may
    # verify a mirror of this message, never guess its identity from its body.
    route = {**route, "return_message_id": plan["message_id"]}
    attachments = plan.get("attachments") or []
    if attachments:
        if getattr(sender, "supports_result_files", False) is not True:
            raise ValueError("return_transport_unavailable")
        route = {**route, "result_attachments": attachments}
    if plan["action"] == "private":
        return {"kind": "private"}
    if plan["action"] == "verify":
        attempt = plan.get("attempt")
        if not attempt or attempt.get("message_ref") is None:
            return {"kind": "locator_unavailable"}
        verifier = getattr(sender, "verify", None)
        if not callable(verifier):
            return {"kind": "verify_unavailable"}
        return {"kind": "verified", "verification": verifier(route, session, turn, plan["text"], attempt)}
    provider = getattr(sender, "send_with_attempt", None)
    if callable(provider):
        sent = provider(route, session, turn, plan["text"], record_attempt)
    elif callable(sender):
        sent = sender(route, session, turn, plan["text"])
    else:
        return {"kind": "sender_unavailable"}
    if isinstance(sent.get("verification"), dict) and sent.get("external_write_performed") is False:
        # A provider outbox may already own publication. Its adapter records the
        # existing attempt before returning a readback, including unavailable
        # or mismatched results, through the ordinary verification state machine.
        return {"kind": "verified", "verification": sent["verification"]}
    return {"kind": "sent", "reply_verified": sent.get("reply_verified"),
            "external_write_performed": sent.get("external_write_performed"),
            "idempotency_key": sent.get("idempotency_key")}


def _deliver_return(host, path, sender, frozen_route):
    store, cancelled = host["store"], host["cancelled"]
    with _return_source(host, path, frozen_route) as facts:
        plan = _return_transaction(host, path, "prepare", {"facts": facts})
        source = dict(facts)
    if plan.get("source_unavailable") is True:
        logging.getLogger(__name__).warning("Exact manager return admission unavailable")
    if plan["action"] == "skip":
        return plan["processed"]
    if cancelled():
        return 0
    checkpoint = {key: plan[key] for key in ("revision", "token")}

    def transact(phase, **payload):
        with _return_source(host, path, frozen_route, source["grant"]) as current:
            return _return_transaction(host, path, phase,
                {"facts": current, **checkpoint, **payload})

    def record_attempt(attempt):
        saved = transact("attempt", attempt=dict(attempt))
        checkpoint.update({key: saved[key] for key in ("revision", "token")})

    try:
        store.append_message(source["route"]["session_id"], role="agent", text=plan["text"],
            turn_id=source["turn"]["turn_id"], origin="manager_followup", message_id=plan["message_id"])
        if cancelled():
            return 0
        outcome = _return_provider(plan, source, sender, record_attempt)
    except ChatMessagePayloadConflictError as exc:
        logging.getLogger(__name__).warning("Manager return payload conflict on %s: %s", plan["message_id"], exc.field)
        outcome = {"kind": "conflict"}
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        outcome = {"kind": "verification_error" if plan["action"] == "verify" else "error",
                   "blocker": exc.reason if isinstance(exc, ReturnResolutionBlocked) else None}
    settled = transact("settle", outcome=outcome, message_id=plan["message_id"])
    # The reply may already be visible while its provider receipt is queued.
    # Invalidate the existing session read hint after settlement so cached
    # conversations re-read the receipt without rewriting history or activity.
    try:
        store.update_session(source["route"]["session_id"])
    except (OSError, ValueError):
        # Publication is already committed. A presentation hint failure must
        # never reclassify delivery or cause the original reply to be resent.
        logging.getLogger(__name__).warning("Manager return session read hint unavailable")
    if settled.get("observe_verified"):
        from ...usage_ping import observe_verified_return
        observe_verified_return()
    return settled["processed"]


def _belongs_to_chat_host(root, store, path):
    from ...control_plane.collaboration.source_chat_observation import is_source_chat_host

    route_path = _root(root) / "roundtrips" / (path.parent.name + ".json")
    try:
        route = _read(route_path) if route_path.exists() else {}
        return route.get("kind") != "peer" and is_source_chat_host(root, route, store)
    except (OSError, ValueError, TypeError):
        logging.getLogger(__name__).warning("Manager return source provenance unavailable")
        return False


def _pending_return_paths(host, paths):
    root, scan_at, exact, cancelled = (host[key] for key in ("root", "scan_at", "exact", "cancelled"))
    paths = iter(paths)
    while not cancelled() and (batch := list(islice(paths, 128))):
        candidates = [{"request_id": path.parent.name, "result_key": path.stem} for path in batch]
        selected = effect_runtime_result("manager.return_delivery.transact", {
            "schema_version": RETURN_DELIVERY_TRANSACTION, "phase": "scan",
            "runtime_root": str(root.resolve()), "now": scan_at.isoformat(), "exact": exact,
            "candidates": candidates,
        })
        if not isinstance(selected, dict) or selected.get("schema_version") != RETURN_DELIVERY_TRANSACTION:
            raise RuntimeError("return delivery scan result shape mismatch")
        if selected.get("unavailable_count"):
            logging.getLogger(__name__).warning("Unreadable manager return receipt")
        ready = {(row["request_id"], row["result_key"]) for row in selected["candidates"]}
        yield from (path for path in batch if (path.parent.name, path.stem) in ready)


@effect_runtime_request_scope()
def drain(root, registry, store, external_sender, *, now=None, cancelled=lambda: False, progress=lambda: None):
    """Run the typed return transaction with the existing Chat/provider adapters."""
    if cancelled():
        return 0

    def clock() -> datetime:
        return datetime.now(timezone.utc) if now is None else now

    # Keep one eligibility instant for the scan. The per-return transaction
    # observes its own time after acquiring the route and source guards.
    scan_at = clock()
    try:
        exact = load_project_registry(registry).get("profile_id") == SOURCE_SESSION_PROFILE_ID
    except (OSError, ValueError, TypeError):
        exact = False
    processed = 0
    host = {
        "root": root, "registry": registry, "store": store,
        "scan_at": scan_at, "clock": clock, "exact": exact, "cancelled": cancelled,
    }
    paths = iter_result_paths(_root(root) / "replies")
    for path in _pending_return_paths(host, paths):
        progress()
        if cancelled():
            break
        if not _belongs_to_chat_host(root, store, path):
            continue
        lock_path = (_root(root) / "return-effect-locks" / path.parent.name / path.stem
                     if exact else path.with_suffix(".lock"))
        try:
            with exclusive_file_lock(lock_path, **({"policy": LockAcquisitionPolicy.SINGLE_FLIGHT,
                                     "operation": "manager_return_delivery"} if exact else {})), ExitStack() as effects:
                frozen_route = _read(_root(root) / "roundtrips" / (path.parent.name + ".json"))
                _hold_return_source_fence(root, frozen_route, effects)
                processed += _deliver_return(host, path, external_sender, frozen_route)
        except LockAcquireTimeoutError:
            continue
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            logging.getLogger(__name__).warning("Manager return transaction unavailable")
        if processed >= 20:
            break
    return processed


def recover_source_chat_provenance(root, registry, store, *, cancelled=lambda: False):
    """Backfill legacy addresses from this host's committed, authorized sources.

    Run once on host startup, including for already delivered returns. Never
    scan other Chat roots or rewrite a pinned source, grant, result or receipt.
    """
    from ...control_plane.collaboration.source_chat_observation import observe_source_chat

    if store.root.parent.resolve() == root.resolve():
        return 0  # Co-located legacy routes already have the right address.
    recovered = 0
    for path in sorted((_root(root) / "roundtrips").glob("*.json")):
        if cancelled():
            break
        try:
            route = _read(path)
            if route.get("kind") == "peer" or "source_chat_runtime_root" in route:
                continue
            if path.stem != route.get("request_id"):
                continue
            # Reject other hosts before taking a Goal lifetime lock or loading
            # its registry. This is one startup pass, not a polling-time scan.
            session, turn = observe_source_chat(store.root.parent, route)
            with collaboration_goal_scope(
                registry, goal_id=route["goal_id"], agents=(route["agent_id"],),
                caller_goal_ref=route.get("goal_ref"), require_active=True,
            ) as scope:
                row = _entry(root, scope.goal_id, route["agent_id"], route["request_id"], scope=scope)
                if any(route.get(key) != row.get(key) for key in
                       ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")):
                    continue
                grant = (target_authority(root, session=session, turn=turn,
                                          target={"goal_id": row["goal_id"], "agent_id": row["agent_id"]})
                         if scope.exact else authority(root, registry, session, turn))
                if {"goal_id": row["goal_id"], "agent_id": row["agent_id"]} not in grant["targets"]:
                    continue
                decide_collaboration_lifecycle(
                    scope, operation="original_return_admit", record=row, route=route,
                    initial_delivery=original_delivery_facts(row, route, turn,
                                                               source_id=grant.get("source_id")),
                )
                if cancelled():
                    break
                register(root, row, session, turn, scope=scope, source_store=store)
                recovered += 1
        except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRejected):
            # Unrelated unavailable/revoked sources cannot starve valid ones.
            # Preserve their records; peer admission still reports recovery.
            continue
    return recovered


class ReturnService:
    """Cheap local receipt pump hosted by the existing Chat server."""

    def __init__(self, root, registry, store, external_sender):
        self.args = (root, registry, store, external_sender)
        self.stop = threading.Event()
        self._execution_pump = None
        self._return_worker = HostedWorker(target=self.run, stop=self.stop,
                                          name="loopx-manager-returns", retry_seconds=3)
        self._execution_worker = HostedWorker(target=self.run_executions, stop=self.stop,
                                             name="loopx-manager-executions", retry_seconds=3)
        self.thread = self._return_worker.thread
        self.execution_thread = self._execution_worker.thread

    def start(self) -> None:
        self._return_worker.start()
        self._execution_worker.start()
        self.thread = self._return_worker.thread
        self.execution_thread = self._execution_worker.thread

    def run(self):
        try:
            recover_source_chat_provenance(*self.args[:3], cancelled=self.stop.is_set)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            logging.getLogger(__name__).warning("Source Chat provenance recovery unavailable")
        self._return_worker.repeat(
            self._return_step, interval_seconds=3,
            on_error=lambda: logging.getLogger(__name__).warning(
                "Manager return receipts unavailable; retrying"),
        )

    def _return_step(self) -> None:
        drain(*self.args, cancelled=self.stop.is_set, progress=self._return_worker.checkpoint)

    def run_executions(self) -> None:
        # Native previews may wait on a subprocess. Keep result publication
        # independent, using the same server lifetime and one serial probe.
        from .execution import QueuedSourcePump
        if self._execution_pump is None:
            self._execution_pump = QueuedSourcePump(*self.args[:3])
        execution_pump = self._execution_pump

        def step() -> None:
            execution_pump.tick(cancelled=self.stop.is_set)

        self._execution_worker.repeat(step, interval_seconds=3,
            on_error=lambda: logging.getLogger(__name__).warning(
                "Queued source execution unavailable; retrying"))

    def close(self) -> None:
        self.stop.set()
        self._return_worker.close(timeout=1.5)
        self._execution_worker.close(timeout=1.5)
