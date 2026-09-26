"""A delegated request stays open until its receiver returns a conclusion.

The existing inbox owns request/decision truth; this module owns only return
routing, receiver-authored replies, and publication receipts. No model polling.
"""

from __future__ import annotations

import logging
import re
import threading
from datetime import datetime, timezone, timedelta
from uuid import uuid4

from . import _root, _read, _write, _hash, authority
from .tracking import _entry, _now
from ...file_lock import exclusive_file_lock
from ...control_plane.collaboration import conversation_scope
from ...control_plane.collaboration.goal_instance_scope import (
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
)
from ...control_plane.projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
)
from ...presentation.public_safety import scan_public_boundary_text
from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result

from ...control_plane.collaboration.inbox import (
    _request_lock,
    needs_conclusion as needs_conclusion,
)

PHASES = ("decision", "conclusion")
DELIVERY_STATUSES = {
    "admitted",
    "queued",
    "retry_pending",
    "verification_required",
    "delivered",
    "superseded",
    "explicit_unverified",
}
EXACT_DELIVERY_ADMISSION_SECONDS = 60
DELIVERY_ERRORS = {
    "provider_delivery_unverified",
    "provider_locator_unavailable",
    "provider_verifier_unavailable",
    "provider_verification_unavailable",
    "provider_delivery_intent_conflict",
    "provider_message_missing",
    "provider_delivery_mismatch",
    "return_authorization_unavailable",
    "original_route_unavailable",
    "initial_delivery_receipt_unavailable",
    "original_route_or_return_delivery_unavailable",
    "delivery_state_unreadable",
}

# Bounded reasons why one return cannot be resolved at all. Adapters raise
# ``ReturnResolutionBlocked`` with one of these instead of relying on their prose
# being re-parsed, so delivery state never depends on message substrings.
RETURN_RESOLUTION_REASONS = frozenset(
    {
        "return_authorization_unavailable",
        "original_route_unavailable",
        "initial_delivery_receipt_unavailable",
    }
)


class ReturnResolutionBlocked(ValueError, RuntimeError):
    """A return cannot be resolved; ``reason`` is the provider-neutral code.

    Inheriting ``ValueError`` keeps existing adapter call sites and their
    handling unchanged while the typed reason is what the state machine reads.
    """

    def __init__(self, reason, message):
        if reason not in RETURN_RESOLUTION_REASONS:
            raise ValueError("unsupported return resolution reason")
        self.reason = reason
        super().__init__(message)


def _delivery_attempt(value):
    return dict(
        effect_runtime_result(
            "manager.return_delivery.normalize_attempt", {"attempt": value}
        )
    )


def _attempt_locator(value):
    """The provider locator a recorded attempt can be verified against.

    ``None`` covers both a record no normalization can read and the typed state
    where the provider accepted the write without reporting a message id. The
    attempt still proves a write happened; it just cannot name a readback
    target, and that is what stops the pump from treating the return as unsent.
    """

    if value is None:
        return None
    try:
        message_ref = _delivery_attempt(value).get("message_ref")
    except (ValueError, EffectRuntimeRejected):
        return None
    return message_ref if isinstance(message_ref, str) and message_ref.strip() else None


def _verification_decision(outcome):
    return dict(
        effect_runtime_result(
            "manager.return_delivery.classify_verification", {"outcome": outcome}
        )
    )


def _verification_exception_error(exc):
    reason = getattr(exc, "reason", None)
    if reason in RETURN_RESOLUTION_REASONS:
        return reason
    # Compatibility fallback for adapter text that still arrives as prose. New
    # adapter failures must raise ReturnResolutionBlocked with a typed reason.
    message = str(exc)
    if (
        "authorization" in message
        or "authorized" in message
        or "authority" in message
    ):
        return "return_authorization_unavailable"
    if any(token in message for token in ("conversation", "route", "binding", "target")):
        return "original_route_unavailable"
    if "initial reply" in message or "initial_receipt" in message:
        return "initial_delivery_receipt_unavailable"
    return None


def _register_unlocked(root, row, session, turn):
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
    if path.exists():
        old = _read(path)
        if any(old.get(key) != value for key, value in value.items()):
            raise ValueError("context return route conflict")
    else:
        _write(path, value | {"registered_at": _now()})


def register(root, row, session, turn, *, scope=None):
    """Called by trusted Chat delivery, never with model-authored routing."""
    path = _root(root) / "roundtrips" / (row["request_id"] + ".json")
    with _request_lock(
        root,
        row["request_id"],
        scope,
        path.with_suffix(".lock"),
    ):
        _register_unlocked(root, row, session, turn)


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
            raise ValueError("original Chat return route unavailable or ambiguous")
        register(root, row, *matches[0])
    value = _read(path)
    if value.get("kind") == "peer" and (row.get("source_kind") != "peer" or value.get("source_agent_id") != row.get("source_agent_id")):
        raise ValueError("peer return route identity mismatch")
    if any(
        value.get(k) != row.get(k)
        for k in ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")
        if k in row
    ):
        raise ValueError("context return route identity mismatch")
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
            )
    row = _entry(root, goal_id, agent_id, request_id, scope=scope)
    route = _route(root, row, scope=scope)
    if route.get("kind") == "peer" and phase != "conclusion":
        raise ValueError("peer replies require a conclusion; report a concrete result or blocker")
    if (route["channel_id"] != "peer" and not conversation_scope(route)["private_conversation"]
            and not scan_public_boundary_text(text)["ok"]):
        raise ValueError("reply contains private boundary material; write an audience-safe conclusion")
    from ...control_plane.collaboration.inbox import record_result
    return {
        **record_result(
            root,
            row,
            phase,
            text,
            scope=scope,
            route=route,
        ),
        "status": (
            "queued_for_requester"
            if route.get("kind") == "peer"
            else "queued_for_original_conversation"
        ),
    }


def reply_status(root, row):
    result = []
    for phase in PHASES:
        path = _root(root) / "replies" / row["request_id"] / (phase + ".json")
        if not path.exists():
            continue
        reply = _read(path)
        state_path = path.with_name(phase + ".delivery.json")
        state = _read(state_path) if state_path.exists() else {}
        status = state.get("status", "queued")
        error = state.get("error")
        if status not in DELIVERY_STATUSES:
            status, error = "explicit_unverified", "delivery_state_unreadable"
        elif error is not None and error not in DELIVERY_ERRORS:
            status, error = "explicit_unverified", "delivery_state_unreadable"
        delivered_at = state.get("delivered_at")
        if not isinstance(delivered_at, str) or len(delivered_at) > 80:
            delivered_at = None
        item = {
            "phase": phase,
            "status": status,
            "created_at": reply.get("created_at"),
            "delivered_at": delivered_at,
            "error": error,
        }
        if state.get("verification") == "reconciled_after_restart":
            item["verification"] = "reconciled_after_restart"
        result.append(item)
    return result


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
            if path.stem != request_id or not re.fullmatch(r"[a-f0-9]{64}", request_id):
                continue
            route_message_ids = {
                "handoff." + _hash([request_id, phase]) for phase in PHASES
            }
            if pending_message_ids.isdisjoint(route_message_ids):
                continue
            for item in reply_status(root, route):
                phase = item.get("phase")
                if phase not in PHASES:
                    continue
                message_id = "handoff." + _hash([request_id, phase])
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


def project_chat_session_snapshot(root, store, session_id):
    """Project return delivery state into one existing Chat snapshot."""

    snapshot = store.session_snapshot(session_id)
    snapshot["messages"] = project_chat_return_deliveries(
        root, session_id, snapshot["messages"]
    )
    from .presentation import project_collaboration
    snapshot["messages"] = project_collaboration(store, root, session_id, snapshot["messages"])
    return snapshot


def _initial_delivery_proved(row, route, turn):
    receipt = (turn.get("response") or {}).get("context_handoff_receipt") or {}
    return (
        turn.get("status") == "completed"
        and receipt.get("request_id") == row["request_id"]
        and receipt.get("goal_id") == row["goal_id"]
        and receipt.get("agent_id") == row["agent_id"]
        and receipt.get("goal_ref") == row["goal_ref"]
        and route.get("goal_ref") == row["goal_ref"]
    )


def _exact_return_scope(registry, reply):
    return collaboration_goal_scope(
        registry,
        goal_id=reply["goal_id"],
        agents=(),
        caller_goal_ref=reply["goal_ref"],
    )


def _exact_return_context(root, registry, store, path, state_path, now):
    reply = _read(path)
    if not isinstance(reply.get("goal_ref"), dict):
        raise ValueError("exact return reply is missing goal_ref")
    with _exact_return_scope(registry, reply) as scope:
        row = _entry(
            root,
            reply["goal_id"],
            reply["agent_id"],
            reply["request_id"],
            scope=scope,
        )
        if (
            path.parent.name != row["request_id"]
            or reply.get("phase") != path.stem
            or any(
                reply.get(key) != row.get(key)
                for key in ("source_id", "goal_ref")
            )
        ):
            raise ValueError("return_reply_identity_mismatch")
        route = _route(root, row, scope=scope)
        if route.get("kind") == "peer":
            return None
        session = store.load_session(route["session_id"])
        turn = store.turn_for_client(route["session_id"], route["client_turn_id"])
        if (
            not session
            or session.get("status") == "closed"
            or session.get("channel_id") != route["channel_id"]
            or not turn
        ):
            raise ValueError("original_conversation_unavailable")
        grant = authority(root, registry, session, turn)
        target = {key: row[key] for key in ("goal_id", "agent_id")}
        if (
            target not in grant["targets"]
            or grant.get("source_id") != row["source_id"]
        ):
            raise ValueError("return_authorization_unavailable")
        initial_delivery_proved = _initial_delivery_proved(row, route, turn)
        decide_collaboration_lifecycle(
            scope,
            operation="original_return_admit",
            record=row,
            route=route,
            initial_delivery_proved=initial_delivery_proved,
        )
        with _request_lock(
            root,
            row["request_id"],
            scope,
            path.with_suffix(".lock"),
        ):
            state = _read(state_path) if state_path.exists() else {}
            if state.get("status") in {
                "delivered",
                "superseded",
                "explicit_unverified",
            }:
                return None
            admission = state.get("admission")
            if (
                isinstance(admission, dict)
                and isinstance(admission.get("expires_at"), str)
                and now.isoformat() < admission["expires_at"]
            ):
                return None
            if (
                state.get("retry_at")
                and state.get("status") != "admitted"
                and now.isoformat() < state["retry_at"]
            ):
                return None
            if path.stem == "decision" and (
                path.parent / "conclusion.json"
            ).exists():
                _write(
                    state_path,
                    {
                        "status": "superseded",
                        "reason": "conclusion_ready",
                        "goal_ref": row["goal_ref"],
                    },
                )
                return None
            token = uuid4().hex
            prior_status = (
                admission.get("prior_status")
                if isinstance(admission, dict)
                else state.get("status", "queued")
            )
            admitted = {
                **state,
                "status": "admitted",
                "goal_ref": row["goal_ref"],
                "admission": {
                    "token": token,
                    "prior_status": prior_status,
                    "admitted_at": now.isoformat(),
                    "expires_at": (
                        now + timedelta(seconds=EXACT_DELIVERY_ADMISSION_SECONDS)
                    ).isoformat(),
                },
            }
            admitted.pop("retry_at", None)
            _write(state_path, admitted)
        return {
            "reply": reply,
            "row": row,
            "route": route,
            "session": session,
            "turn": turn,
            "store": store,
            "state_path": state_path,
            "state": admitted,
            "token": token,
        }


def _write_exact_return_state(
    root,
    registry,
    context,
    value,
    *,
    preserve_admission=False,
):
    reply = context["reply"]
    with _exact_return_scope(registry, reply) as scope:
        row = _entry(
            root,
            reply["goal_id"],
            reply["agent_id"],
            reply["request_id"],
            scope=scope,
        )
        route = _route(root, row, scope=scope)
        turn = context["store"].turn_for_client(
            route["session_id"],
            route["client_turn_id"],
        )
        decide_collaboration_lifecycle(
            scope,
            operation="original_return_settle",
            record=row,
            route=route,
            initial_delivery_proved=(
                isinstance(turn, dict)
                and _initial_delivery_proved(row, route, turn)
            ),
        )
        with _request_lock(
            root,
            row["request_id"],
            scope,
            context["state_path"].with_suffix(".lock"),
        ):
            current = _read(context["state_path"])
            admission = current.get("admission")
            if (
                not isinstance(admission, dict)
                or admission.get("token") != context["token"]
            ):
                raise ValueError("exact return delivery admission changed")
            result = {**value, "goal_ref": row["goal_ref"]}
            if preserve_admission:
                result["admission"] = admission
            else:
                result.pop("admission", None)
            _write(context["state_path"], result)


def _retry_state(state, now, *, error):
    attempts = int(state.get("attempts", 0)) + 1
    result = {
        key: value
        for key, value in state.items()
        if key not in {"admission", "delivered_at", "message_id", "verification"}
    }
    result.update(
        status="retry_pending",
        attempts=attempts,
        error=error,
        retry_at=(
            now + timedelta(seconds=min(300, 5 * 2 ** min(attempts, 6)))
        ).isoformat(),
    )
    return result


def _drain_exact(root, registry, store, external_sender, *, now, cancelled):
    processed = 0
    for path in sorted((_root(root) / "replies").glob("*/*.json")):
        if cancelled():
            break
        if path.stem not in PHASES:
            continue
        state_path = path.with_name(path.stem + ".delivery.json")
        try:
            context = _exact_return_context(
                root,
                registry,
                store,
                path,
                state_path,
                now,
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            logging.getLogger(__name__).warning(
                "Exact manager return admission unavailable"
            )
            continue
        if context is None:
            continue
        row = context["row"]
        route = context["route"]
        session = context["session"]
        turn = context["turn"]
        reply = context["reply"]
        state = context["state"]
        prefix = "处理结论" if path.stem == "conclusion" else "处理进展"
        text = (
            f"{prefix} · {row['agent_id']} · 委托 {row['request_id'][:8]}"
            f"\n\n{reply['text']}"
        )
        mid = "handoff." + _hash([row["request_id"], path.stem])
        try:
            if cancelled():
                return processed
            store.append_message(
                route["session_id"],
                role="agent",
                text=text,
                turn_id=turn["turn_id"],
                origin="manager_followup",
                message_id=mid,
            )
            if cancelled():
                return processed
            if conversation_scope(session)["private_conversation"]:
                _write_exact_return_state(
                    root,
                    registry,
                    context,
                    {
                        "status": "delivered",
                        "delivered_at": now.isoformat(),
                        "message_id": mid,
                    },
                )
                processed += 1
                continue

            prior_status = state["admission"]["prior_status"]
            if prior_status == "verification_required" or state.get("attempt") is not None:
                attempt = state.get("attempt")
                if attempt is None or _attempt_locator(attempt) is None:
                    _write_exact_return_state(
                        root,
                        registry,
                        context,
                        {
                            **({"attempt": attempt} if attempt is not None else {}),
                            "status": "explicit_unverified",
                            "error": "provider_locator_unavailable",
                        },
                    )
                    processed += 1
                    continue
                normalized_attempt = _delivery_attempt(attempt)
                verifier = getattr(external_sender, "verify", None)
                if not callable(verifier):
                    _write_exact_return_state(
                        root,
                        registry,
                        context,
                        {
                            "status": "explicit_unverified",
                            "error": "provider_verifier_unavailable",
                        },
                    )
                    processed += 1
                    continue
                try:
                    decision = _verification_decision(
                        verifier(
                            route,
                            session,
                            turn,
                            text,
                            normalized_attempt,
                        )
                    )
                except (
                    OSError,
                    ValueError,
                    KeyError,
                    TypeError,
                    RuntimeError,
                ) as exc:
                    error = _verification_exception_error(exc)
                    next_state = (
                        {
                            "status": "explicit_unverified",
                            "error": error,
                        }
                        if error
                        else _retry_state(
                            {"attempt": normalized_attempt, **state},
                            now,
                            error="provider_verification_unavailable",
                        )
                    )
                    _write_exact_return_state(
                        root,
                        registry,
                        context,
                        next_state,
                    )
                    processed += 1
                    continue
                if decision["status"] == "delivered":
                    next_state = {
                        "status": "delivered",
                        "delivered_at": now.isoformat(),
                        "message_id": mid,
                        "provider_receipt": normalized_attempt["provider_receipt"],
                        "reply_verified": True,
                        "verification": decision["verification"],
                    }
                elif decision["status"] == "explicit_unverified":
                    next_state = {
                        "status": "explicit_unverified",
                        "error": decision["error"],
                    }
                else:
                    next_state = _retry_state(
                        {"attempt": normalized_attempt, **state},
                        now,
                        error=decision["error"],
                    )
                _write_exact_return_state(
                    root,
                    registry,
                    context,
                    next_state,
                )
                processed += 1
                continue

            def record_attempt(value):
                nonlocal state
                attempt = _delivery_attempt(value)
                existing = state.get("attempt")
                if existing is not None and _delivery_attempt(existing) != attempt:
                    raise ValueError("manager return delivery attempt conflict")
                state = {**state, "attempt": attempt}
                _write_exact_return_state(
                    root,
                    registry,
                    context,
                    state,
                    preserve_admission=True,
                )

            sender = getattr(external_sender, "send_with_attempt", None)
            sent = (
                sender(route, session, turn, text, record_attempt)
                if callable(sender)
                else external_sender(route, session, turn, text)
            )
            if sent.get("reply_verified") is not True:
                if sent.get("external_write_performed") is True:
                    attempt = state.get("attempt")
                    next_state = (
                        {
                            "status": "verification_required",
                            "error": "provider_delivery_unverified",
                            "attempt": attempt,
                        }
                        if attempt is not None and _attempt_locator(attempt) is not None
                        else {
                            **({"attempt": attempt} if attempt is not None else {}),
                            "status": "explicit_unverified",
                            "error": "provider_locator_unavailable",
                        }
                    )
                    _write_exact_return_state(
                        root,
                        registry,
                        context,
                        next_state,
                    )
                    processed += 1
                    continue
                raise ValueError("return_transport_unavailable")
            _write_exact_return_state(
                root,
                registry,
                context,
                {
                    "status": "delivered",
                    "delivered_at": now.isoformat(),
                    "message_id": mid,
                    "provider_receipt": sent.get("idempotency_key"),
                    "reply_verified": True,
                },
            )
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            attempt = state.get("attempt")
            error = (
                _verification_exception_error(exc)
                if attempt is not None
                else None
            )
            if error:
                next_state = {
                    "status": "explicit_unverified",
                    "error": error,
                }
            elif attempt is not None and _attempt_locator(attempt) is None:
                next_state = {
                    "status": "explicit_unverified",
                    "error": "provider_locator_unavailable",
                    "attempt": attempt,
                }
            else:
                next_state = _retry_state(
                    state,
                    now,
                    error="original_route_or_return_delivery_unavailable",
                )
            try:
                _write_exact_return_state(
                    root,
                    registry,
                    context,
                    next_state,
                )
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                logging.getLogger(__name__).warning(
                    "Exact manager return settlement unavailable"
                )
        processed += 1
        if processed >= 20:
            break
    return processed


def drain(root, registry, store, external_sender, *, now=None, cancelled=lambda: False):
    """Restart-safe return delivery; transport retries never rerun the worker/model."""
    now = now or datetime.now(timezone.utc)
    try:
        profile_id = load_project_registry(registry).get("profile_id")
    except (OSError, ValueError, TypeError):
        profile_id = None
    if profile_id == SOURCE_SESSION_PROFILE_ID:
        return _drain_exact(
            root,
            registry,
            store,
            external_sender,
            now=now,
            cancelled=cancelled,
        )
    processed = 0
    for path in sorted((_root(root) / "replies").glob("*/*.json")):
        if cancelled():
            break
        if path.stem not in PHASES:
            continue
        state_path = path.with_name(path.stem + ".delivery.json")
        with exclusive_file_lock(path.with_suffix(".lock")):
            try:
                state = _read(state_path) if state_path.exists() else {}
            except (OSError, ValueError):
                # A damaged receipt has unknown delivery state: do not resend
                # it blindly, and do not starve unrelated pending returns.
                logging.getLogger(__name__).warning("Unreadable manager return receipt")
                continue
            if state.get("status") in {"delivered", "superseded", "explicit_unverified"}:
                continue
            if state.get("retry_at") and now.isoformat() < state["retry_at"]:
                continue
            try:
                reply = _read(path)
                row = _entry(
                    root, reply["goal_id"], reply["agent_id"], reply["request_id"]
                )
                if (
                    path.parent.name != row["request_id"]
                    or reply.get("phase") != path.stem
                    or reply.get("source_id") != row["source_id"]
                ):
                    raise ValueError("return_reply_identity_mismatch")
                route = _route(root, row)
                if route.get("kind") == "peer":
                    continue  # Delivered by the requester inbox, never a Chat audience.
                session = store.load_session(route["session_id"])
                turn = store.turn_for_client(
                    route["session_id"], route["client_turn_id"]
                )
                if (
                    not session
                    or session.get("status") == "closed"
                    or session.get("channel_id") != route["channel_id"]
                    or not turn
                ):
                    raise ValueError("original_conversation_unavailable")
                grant = authority(root, registry, session, turn)
                target = {k: row[k] for k in ("goal_id", "agent_id")}
                if (
                    target not in grant["targets"]
                    or grant.get("source_id") != row["source_id"]
                ):
                    raise ValueError("return_authorization_unavailable")
                if turn.get("status") != "completed":
                    raise ValueError("initial_receipt_not_completed")
                if (
                    path.stem == "decision"
                    and (path.parent / "conclusion.json").exists()
                ):
                    _write(
                        state_path,
                        {"status": "superseded", "reason": "conclusion_ready"},
                    )
                    continue
                prefix = "处理结论" if path.stem == "conclusion" else "处理进展"
                text = f"{prefix} · {row['agent_id']} · 委托 {row['request_id'][:8]}\n\n{reply['text']}"
                # Transcript writes are independently idempotent, including when
                # Lark is offline. Keep the original Turn and logical conversation.
                mid = "handoff." + _hash([row["request_id"], path.stem])
                if cancelled():
                    return processed
                store.append_message(
                    route["session_id"],
                    role="agent",
                    text=text,
                    turn_id=turn["turn_id"],
                    origin="manager_followup",
                    message_id=mid,
                )
                if cancelled():
                    return processed
                transport = {}
                if not conversation_scope(session)["private_conversation"]:
                    if state.get("status") == "verification_required":
                        if state.get("attempt") is None:
                            # A record written before locators were persisted has
                            # no trustworthy provider identity: never guess and
                            # never resend the conclusion.
                            _write(
                                state_path,
                                {
                                    "status": "explicit_unverified",
                                    "error": "provider_locator_unavailable",
                                },
                            )
                            continue
                        try:
                            attempt = _delivery_attempt(state.get("attempt"))
                        except (ValueError, EffectRuntimeRejected):
                            _write(
                                state_path,
                                {
                                    "status": "explicit_unverified",
                                    "error": "provider_locator_unavailable",
                                },
                            )
                            continue
                        if _attempt_locator(state.get("attempt")) is None:
                            # The attempt records a provider write that carried no
                            # locator, so no readback can prove it and the provider
                            # must not be called again. Converging here keeps the
                            # attempt as the evidence of that write while making
                            # the return terminal, instead of looping the pump
                            # through verification attempts forever.
                            _write(
                                state_path,
                                {
                                    **state,
                                    "status": "explicit_unverified",
                                    "error": "provider_locator_unavailable",
                                },
                            )
                            continue
                        verifier = getattr(external_sender, "verify", None)
                        if not callable(verifier):
                            _write(
                                state_path,
                                {
                                    "status": "explicit_unverified",
                                    "error": "provider_verifier_unavailable",
                                },
                            )
                            continue
                        try:
                            verified = verifier(route, session, turn, text, attempt)
                            decision = _verification_decision(verified)
                        except (
                            OSError,
                            ValueError,
                            KeyError,
                            TypeError,
                            RuntimeError,
                        ) as exc:
                            error = _verification_exception_error(exc)
                            if error:
                                _write(
                                    state_path,
                                    {"status": "explicit_unverified", "error": error},
                                )
                                continue
                            # An unclassified readback failure keeps the locator and
                            # stays retryable, but must never re-run the provider on
                            # every pump without backoff.
                            attempts = int(state.get("attempts", 0)) + 1
                            _write(
                                state_path,
                                {
                                    **state,
                                    "status": "verification_required",
                                    "attempts": attempts,
                                    "retry_at": (
                                        now
                                        + timedelta(
                                            seconds=min(300, 5 * 2 ** min(attempts, 6))
                                        )
                                    ).isoformat(),
                                },
                            )
                            continue
                        if decision["status"] == "delivered":
                            _write(
                                state_path,
                                {
                                    "status": "delivered",
                                    "delivered_at": now.isoformat(),
                                    "message_id": mid,
                                    "provider_receipt": attempt["provider_receipt"],
                                    "reply_verified": True,
                                    "verification": decision["verification"],
                                },
                            )
                            continue
                        if decision["status"] == "explicit_unverified":
                            _write(
                                state_path,
                                {
                                    "status": "explicit_unverified",
                                    "error": decision["error"],
                                },
                            )
                            continue
                        attempts = int(state.get("attempts", 0)) + 1
                        _write(
                            state_path,
                            {
                                **state,
                                "status": "verification_required",
                                "attempts": attempts,
                                "error": decision["error"],
                                "retry_at": (
                                    now
                                    + timedelta(
                                        seconds=min(300, 5 * 2 ** min(attempts, 6))
                                    )
                                ).isoformat(),
                            },
                        )
                        continue

                    def record_attempt(value):
                        attempt = _delivery_attempt(value)
                        current = _read(state_path) if state_path.exists() else {}
                        existing = current.get("attempt")
                        if existing is not None and _delivery_attempt(existing) != attempt:
                            raise ValueError("manager return delivery attempt conflict")
                        _write(
                            state_path,
                            {
                                "status": "verification_required",
                                "error": "provider_delivery_unverified",
                                "attempt": attempt,
                            },
                        )

                    sender = getattr(external_sender, "send_with_attempt", None)
                    sent = (
                        sender(route, session, turn, text, record_attempt)
                        if callable(sender)
                        else external_sender(route, session, turn, text)
                    )
                    if sent.get("reply_verified") is not True:
                        if sent.get("external_write_performed") is True:
                            current = _read(state_path) if state_path.exists() else {}
                            if (
                                current.get("attempt") is not None
                                and _attempt_locator(current.get("attempt")) is not None
                            ):
                                _write(
                                    state_path,
                                    {
                                        **current,
                                        "status": "verification_required",
                                        "error": "provider_delivery_unverified",
                                    },
                                )
                            else:
                                # Either nothing was recorded or the record says
                                # the provider took the write without a locator.
                                # Both leave no readback target, so the return is
                                # terminal rather than retryable: a retry would
                                # post the same text again.
                                _write(
                                    state_path,
                                    (
                                        {
                                            **current,
                                            "status": "explicit_unverified",
                                            "error": "provider_locator_unavailable",
                                        }
                                        if current.get("attempt") is not None
                                        else {
                                            "status": "explicit_unverified",
                                            "error": "provider_locator_unavailable",
                                        }
                                    ),
                                )
                            continue
                        raise ValueError("return_transport_unavailable")
                    transport = {
                        "provider_receipt": sent.get("idempotency_key"),
                        "reply_verified": True,
                    }
                _write(
                    state_path,
                    {
                        "status": "delivered",
                        "delivered_at": now.isoformat(),
                        "message_id": mid,
                        **transport,
                    },
                )
            except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
                current = _read(state_path) if state_path.exists() else {}
                if current.get("status") == "verification_required" and current.get(
                    "attempt"
                ) is not None:
                    error = _verification_exception_error(exc)
                    if error:
                        _write(
                            state_path,
                            {"status": "explicit_unverified", "error": error},
                        )
                    elif _attempt_locator(current.get("attempt")) is None:
                        # The record says the provider took the write and named
                        # no locator, so there is nothing left to verify and a
                        # retry would post the same text again. Converge now
                        # instead of leaving the return in a retryable state.
                        _write(
                            state_path,
                            {
                                **current,
                                "status": "explicit_unverified",
                                "error": "provider_locator_unavailable",
                            },
                        )
                    processed += 1
                    continue
                attempts = int(state.get("attempts", 0)) + 1
                _write(
                    state_path,
                    {
                        "status": "retry_pending",
                        "attempts": attempts,
                        "error": "original_route_or_return_delivery_unavailable",
                        "retry_at": (
                            now + timedelta(seconds=min(300, 5 * 2 ** min(attempts, 6)))
                        ).isoformat(),
                    },
                )
            processed += 1
            if processed >= 20:
                break
    return processed


class ReturnService:
    """Cheap local receipt pump hosted by the existing Chat server."""

    def __init__(self, root, registry, store, external_sender):
        self.args = (root, registry, store, external_sender)
        self.stop = threading.Event()
        self.thread = threading.Thread(
            target=self.run, daemon=True, name="loopx-manager-returns"
        )

    def start(self):
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                drain(*self.args, cancelled=self.stop.is_set)
            except (OSError, ValueError, KeyError, TypeError, RuntimeError):
                logging.getLogger(__name__).warning(
                    "Manager return receipts unavailable; retrying"
                )
            self.stop.wait(3)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
