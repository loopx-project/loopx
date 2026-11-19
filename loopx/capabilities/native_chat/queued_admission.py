"""IO admission to the existing same-Session queue; Core owns source authority."""
import hashlib
import json
from pathlib import Path
from typing import Any

from ...chat_attachments import require_text_document_route
from ...attached_session import enqueue_attached_agent_turn
from ...chat_store import CHAT_SESSION_MODE_ATTACHED
from ...control_plane.collaboration import conversation_scope
from . import project_context as project_context_policy


def enqueue_turn(controller: Any, *, session_id: str, client_turn_id: str, message: str,
                 attachments: list[dict[str, Any]] | None, work_dir: Path | None, objective: str,
                 origin: str, external_agent_target: dict[str, Any] | None) -> tuple[dict[str, Any], bool]:
    session = controller.store.load_session(session_id)
    if session is None or session.get("status") == "closed":
        raise KeyError("chat session was not found")
    require_text_document_route(session, attachments, origin=origin)
    bound = session.get("steward_context") or session.get("project_context") or {}
    if bound.get("audience") in {"bound_owner", "bound_group"}:
        if external_agent_target is not None:
            raise ValueError("Agent target requires its original attached Session")
        if origin != "web" and work_dir is None:
            raise ValueError("the conversation's runtime context is required")
        return _enqueue_bound(controller, session, client_turn_id, message, attachments, origin)
    if work_dir is None:
        raise ValueError("the conversation's runtime context is required")
    if session.get("steward_context") is not None:
        if origin not in {"lark", "external"}:
            raise ValueError("bound steward Chat requires its external source admission")
        controller.project_contexts.session_context(session)
    if session.get("project_context") is not None:
        if conversation_scope(session, origin=origin)["kind"] != "project_workspace":
            raise ValueError("project grant does not authorize this external audience or source")
        context = controller.project_contexts.session_context(session)
        project_context_policy.validate_project_executor_scope(str(session["agent_id"]), session["project_context"], controller.capabilities)
        work_dir, objective = context["project"], context["objective"]
    if session.get("session_mode") == CHAT_SESSION_MODE_ATTACHED:
        if attachments:
            raise ValueError("attached host session queue does not yet accept attachments")
        turn, created = enqueue_attached_agent_turn(
            store=controller.store,
            registry_path=controller.registry_path,
            session_id=session_id,
            client_turn_id=client_turn_id,
            message=message,
            origin=origin,
            external_agent_target=external_agent_target,
        )
    else:
        if external_agent_target is not None:
            raise ValueError("Agent target requires its original attached Session")
        turn, created = controller.store.create_queued_turn(
            session_id,
            client_turn_id=client_turn_id,
            message=message,
            attachments=attachments,
            origin=origin,
        )
        controller.resume_session_queue(
            session_id=session_id,
            work_dir=work_dir,
            objective=objective,
        )
    return turn, created


def deliver_native_mention(
    controller, *, session_id: str, ingress_id: str, target_turn_id: str | None,
    binding_matches: bool, text_only: bool, may_deliver_new: bool,
    steering_text: str, queued_text: str, origin: str,
) -> dict[str, Any]:
    """Project Chat receipts through the shared mention ingress rule."""
    from ...control_plane.chat_turn_acceptance import plan_native_mention_ingress

    def plan():
        receipt = controller.store.steering_receipt(session_id, ingress_id) if binding_matches else None
        queued_turn_exists = (
            binding_matches and receipt is None
            and controller.store.turn_for_client(session_id, ingress_id) is not None
        )
        message = queued_text if (receipt or {}).get("mode") == "session_queue" else steering_text
        return plan_native_mention_ingress(
            mentioned=True, binding_matches=binding_matches, text_only=text_only, may_deliver_new=may_deliver_new,
            target_turn_id=target_turn_id, queued_turn_exists=queued_turn_exists,
            receipt=({key: receipt.get(key) for key in ("mode", "status", "expected_turn_id", "active_turn_id", "error_code")}
                     if receipt is not None else None),
            receipt_message_matches=receipt is not None and receipt.get("message") == message,
        )

    try:
        decision = plan()
        if decision["action"] == "enqueue":
            # Reserve the delivery mode before creating a FIFO Turn. Replays
            # use this mode even if a different Turn is now running.
            controller.store.create_ingress_receipt(
                session_id, client_ingress_id=ingress_id, mode="session_queue", message=queued_text,
            )
        elif decision["action"] in {"steer", "unresolved"}:
            receipt = controller.store.steering_receipt(session_id, ingress_id)
            if decision["action"] == "unresolved" and receipt is None:
                return decision
            try:
                controller.steer_active_turn(
                    session_id=session_id, client_ingress_id=ingress_id, message=steering_text,
                    expected_turn_id=decision.get("target_turn_id") or (receipt or {}).get("expected_turn_id"), origin=origin,
                )
            except Exception:
                # The native response can be lost after sending. The existing
                # receipt, rather than the exception, decides whether it landed.
                pass
            decision = plan()
            if decision["action"] == "steer":
                return {"action": "unresolved"}
        return decision
    except (OSError, ValueError):
        return {"action": "unresolved"}


def record_steering_delivery(
    store, *, session_id: str, turn_id: str, ingress_id: str,
    message: str, queued_turn_id: str | None, origin: str | None,
) -> None:
    """Persist transcript evidence before settling the native ingress receipt."""
    payload = {"client_ingress_id": ingress_id}
    if origin is not None:
        payload["origin"] = origin
    if queued_turn_id is None:
        message_id = "steer-" + hashlib.sha256(ingress_id.encode()).hexdigest()
        store.append_message(session_id, role="user", text=message, turn_id=turn_id, origin=origin,
                             message_id=message_id)
    else:
        payload["queued_turn_id"] = queued_turn_id
    if not any(event.get("kind") == "turn.steered" and event.get("payload") == payload
               for event in store.events_after(session_id, turn_id, None)):
        store.append_event(session_id, turn_id, kind="turn.steered", payload=payload)
    store.update_ingress_receipt(
        session_id, ingress_id, status="delivered", active_turn_id=turn_id, error_code=None,
    )


def send_native_steering(controller, adapter, *, session_id: str, active_turn_id: str,
                         upstream_turn_id: str, client_ingress_id: str, message: str,
                         queued_turn_id: str | None, origin: str | None) -> dict[str, Any]:
    """Persist native identity before IO and settle its original receipt afterward."""
    native = {
        "local_turn_id": active_turn_id, "thread_id": adapter.upstream_thread_id,
        "turn_id": upstream_turn_id, "client_message_id": "loopx-steer-" + hashlib.sha256(
            json.dumps([session_id, client_ingress_id]).encode()
        ).hexdigest(),
        "queued_turn_id": queued_turn_id, "origin": origin,
    }
    controller.store.update_ingress_receipt(session_id, client_ingress_id, native_steering=native)
    try:
        adapter.steer_turn(message, upstream_turn_id, native["client_message_id"])
    except Exception:
        controller.store.update_ingress_receipt(session_id, client_ingress_id,
            status="failed", error_code="live_steering_rejected")
        raise
    record_steering_delivery(controller.store, session_id=session_id, turn_id=active_turn_id,
        ingress_id=client_ingress_id, message=message, queued_turn_id=queued_turn_id, origin=origin)
    turn = controller.store.load_turn(session_id, active_turn_id)
    if turn is None:
        raise RuntimeError("live_steering_turn_missing")
    return turn


def reconcile_steering_delivery(controller, session, receipt) -> dict[str, Any] | None:
    """Read native evidence for the saved operation without sending its input."""
    from .codex_adapter import CodexAppServerAdapter
    from ...control_plane.effect_runtime import effect_runtime_result

    with controller.lock:
        adapter = controller.adapters.get(session["session_id"])
    if not isinstance(adapter, CodexAppServerAdapter) or not adapter.healthcheck():
        return None
    capture = receipt.get("native_steering")
    if not isinstance(capture, dict) or not isinstance(capture.get("local_turn_id"), str):
        return None
    turn = controller.store.load_turn(session["session_id"], capture["local_turn_id"])
    facts = {"receipt": receipt, "session_thread_id": session.get("upstream_thread_id"),
             "adapter_thread_id": adapter.upstream_thread_id, "turn": turn or {}}
    try:
        decision = effect_runtime_result("chat.queue.steering_readback", facts)
        if decision["action"] != "read":
            return None
        facts["items"] = adapter.steering_delivery_readback(decision["turn_id"])
        facts["inventory_complete"] = True
        if effect_runtime_result("chat.queue.steering_readback", facts)["action"] != "confirmed":
            return None
    except (OSError, RuntimeError, ValueError, KeyError):
        return None
    record_steering_delivery(controller.store, session_id=session["session_id"],
        turn_id=turn["turn_id"], ingress_id=receipt["client_ingress_id"], message=receipt["message"],
        queued_turn_id=capture.get("queued_turn_id"), origin=capture.get("origin"))
    return turn


def _enqueue_bound(controller: Any, session: dict[str, Any], client_turn_id: str, message: str,
                   attachments: list[dict[str, Any]] | None, origin: str) -> tuple[dict[str, Any], bool]:
    contexts = controller.project_contexts
    bindings = contexts.conversation_bindings
    if bindings is None:
        raise ValueError("bound conversation authority is unavailable")
    saved = session.get("steward_context") or session["project_context"]
    with bindings.input_context(saved, origin=origin, session=session, store=controller.store) as selected:
        fresh = controller.store.load_session(session["session_id"])
        if fresh is None or fresh.get("status") == "closed":
            raise KeyError("chat session was not found")
        for key in ("project_context", "steward_context", "channel_id", "goal_id", "agent_id"):
            if fresh.get(key) != session.get(key):
                raise ValueError("bound Session changed before input admission")
        context = contexts.runtime_context(fresh, selected)
        if fresh.get("project_context") is not None:
            project_context_policy.validate_project_executor_scope(str(fresh["agent_id"]),
                fresh["project_context"], controller.capabilities)
        elif origin == "web":
            bindings._ensure_delivery_scope(selected)  # input_context retains the binding fence
            _record_local_ingress(controller.coordination_runtime_root, fresh, client_turn_id, message)
        turn, created = controller.store.create_queued_turn(session["session_id"],
            client_turn_id=client_turn_id, message=message, attachments=attachments, origin=origin)
    # Dispatch after releasing the publication fence. The worker independently
    # rechecks current scope before effects, including revocation after enqueue.
    controller.resume_session_queue(session_id=session["session_id"],
        work_dir=context["project"], objective=context["objective"])
    return turn, created


def _record_local_ingress(runtime_root: Path, session: dict[str, Any],
                          client_turn_id: str, message: str) -> None:
    from ..manager_context import register_ingress
    from ...control_plane.collaboration.inbox import _hash, normalize_source_context

    try:
        normalize_source_context(message)
    except ValueError:
        # Chat keeps the full input. Oversized Inbox context is not an implicit
        # truncation or handoff grant, matching the existing provider boundary.
        return
    register_ingress(runtime_root, session_id=session["session_id"], client_turn_id=client_turn_id,
        channel=session["channel_id"], sender_id=session["steward_context"]["operator_ref"],
        message=message, source_id="web:" + _hash([session["session_id"], client_turn_id]), origin="web")
