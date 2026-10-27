"""A delegated request stays open until its receiver returns a conclusion.

The existing inbox owns request/decision truth; this module owns only return
routing, receiver-authored replies, and publication receipts. No model polling.
"""

from __future__ import annotations

import logging
import threading
from itertools import islice
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone

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
                receipt = (turn.get("response") or {}).get(
                    "context_handoff_receipt"
                ) or {}
                if receipt.get("request_id") == row["request_id"]:
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
            )
    row = _entry(root, goal_id, agent_id, request_id, scope=scope)
    route = _route(root, row, scope=scope)
    if route.get("kind") == "peer" and phase != "conclusion":
        raise ValueError("peer replies require a conclusion; report a concrete result or blocker")
    if (route["channel_id"] != "peer" and not conversation_scope(route)["private_conversation"]
            and not scan_public_boundary_text(text)["ok"]):
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
        ),
        "status": (
            "queued_for_requester"
            if route.get("kind") == "peer"
            else "queued_for_original_conversation"
        ),
    }


def project_chat_return_deliveries(root, session_id, messages):
    """Attach public-safe delivery readback to returned Chat transcript rows."""

    pending_message_ids = {
        message_id
        for message in messages
        if message.get("origin") == "manager_followup"
        and isinstance((message_id := message.get("message_id")), str)
        and message_id.startswith("handoff.")
    }
    if not pending_message_ids:
        return list(messages)
    statuses = {}
    paths = sorted((_root(root) / "roundtrips").glob("*.json"))
    for path in paths:
        try:
            route = _read(path)
            if route.get("session_id") != session_id:
                continue
            request_id = str(route.get("request_id") or "")
            if path.stem != request_id or not BARE_SHA256_PATTERN.fullmatch(request_id):
                continue
            for item in reply_status(root, route):
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
            {**message, "return_delivery": statuses[message.get("message_id")]}
            if message.get("message_id") in statuses
            else message
        )
        for message in messages
    ]


def project_chat_session_snapshot(root, store, session_id, *, registry,
                                  message_limit=None, message_cursor=None):
    """Project return delivery state into one existing Chat snapshot."""

    snapshot = (store.session_snapshot(session_id, strict_messages=True) if message_limit is not None
                else store.session_snapshot(session_id))
    from .chat_history import page_chat_messages
    page = (page_chat_messages(session_id, snapshot["messages"], limit=message_limit,
                              cursor=message_cursor) if message_limit is not None else None)
    context_messages = snapshot["messages"]
    if page is not None:
        context_messages = context_messages[:page["message_page"]["total"]]
        snapshot["messages"] = page["messages"]
    snapshot["messages"] = project_chat_return_deliveries(
        root, session_id, snapshot["messages"]
    )
    from .presentation import project_collaboration
    # Use the frozen context to choose the carrier, projecting only this page.
    # A question and answer can straddle pages without duplicating a commitment
    # or re-reading every off-page Turn for each page.
    snapshot["messages"] = project_collaboration(store, root, session_id, snapshot["messages"],
        registry=registry, context_messages=context_messages)
    if page is not None:
        snapshot["message_page"] = page["message_page"]
    return snapshot



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
    root, now, exact = (host[key] for key in ("root", "now", "exact"))
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
        "result_key": path.stem, "phase": phase, "now": now.isoformat(),
        "exact": exact, "facts": observed, **payload,
    }, retry_safe=False)
    if (not isinstance(result, dict) or result.get("schema_version") != RETURN_DELIVERY_TRANSACTION
            or result.get("action") not in {"skip", "private", "verify", "send"}
            or type(result.get("processed")) is not int or result["processed"] not in {0, 1}):
        raise RuntimeError("return delivery transaction result shape mismatch")
    return result


def _return_provider(plan, source, sender, record_attempt):
    route, session, turn = (source[key] for key in ("route", "session", "turn"))
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
    root, now, exact, cancelled = (host[key] for key in ("root", "now", "exact", "cancelled"))
    paths = iter(paths)
    while not cancelled() and (batch := list(islice(paths, 128))):
        candidates = [{"request_id": path.parent.name, "result_key": path.stem} for path in batch]
        selected = effect_runtime_result("manager.return_delivery.transact", {
            "schema_version": RETURN_DELIVERY_TRANSACTION, "phase": "scan",
            "runtime_root": str(root.resolve()), "now": now.isoformat(), "exact": exact,
            "candidates": candidates,
        })
        if not isinstance(selected, dict) or selected.get("schema_version") != RETURN_DELIVERY_TRANSACTION:
            raise RuntimeError("return delivery scan result shape mismatch")
        if selected.get("unavailable_count"):
            logging.getLogger(__name__).warning("Unreadable manager return receipt")
        ready = {(row["request_id"], row["result_key"]) for row in selected["candidates"]}
        yield from (path for path in batch if (path.parent.name, path.stem) in ready)


@effect_runtime_request_scope()
def drain(root, registry, store, external_sender, *, now=None, cancelled=lambda: False):
    """Run the typed return transaction with the existing Chat/provider adapters."""
    if cancelled():
        return 0
    now = now or datetime.now(timezone.utc)
    try:
        exact = load_project_registry(registry).get("profile_id") == SOURCE_SESSION_PROFILE_ID
    except (OSError, ValueError, TypeError):
        exact = False
    processed = 0
    host = {"root": root, "registry": registry, "store": store, "now": now, "exact": exact, "cancelled": cancelled}
    paths = iter_result_paths(_root(root) / "replies")
    for path in _pending_return_paths(host, paths):
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
        self.thread = threading.Thread(
            target=self.run, daemon=True, name="loopx-manager-returns"
        )

    def start(self) -> None:
        self.thread.start()

    def run(self):
        try:
            recover_source_chat_provenance(*self.args[:3], cancelled=self.stop.is_set)
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            logging.getLogger(__name__).warning("Source Chat provenance recovery unavailable")
        while not self.stop.is_set():
            try:
                drain(*self.args, cancelled=self.stop.is_set)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                logging.getLogger(__name__).warning(
                    "Manager return receipts unavailable; retrying"
                )
            self.stop.wait(3)

    def close(self) -> None:
        self.stop.set()
        if self.thread.ident is not None:
            self.thread.join(timeout=3)
