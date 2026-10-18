"""IO admission to the existing same-Session queue; Core owns source authority."""
from pathlib import Path
from typing import Any

from ...attached_session import enqueue_attached_agent_turn
from ...chat_store import CHAT_SESSION_MODE_ATTACHED
from ...control_plane.collaboration import conversation_scope
from . import project_context as project_context_policy


def enqueue_turn(controller, *, session_id: str, client_turn_id: str, message: str,
                 attachments: list[dict[str, Any]] | None, work_dir: Path | None, objective: str,
                 origin: str, external_agent_target: dict | None):
    session = controller.store.load_session(session_id)
    if session is None or session.get("status") == "closed":
        raise KeyError("chat session was not found")
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


def _enqueue_bound(controller, session, client_turn_id, message, attachments, origin):
    contexts = controller.project_contexts
    bindings = contexts.conversation_bindings
    if bindings is None:
        raise ValueError("bound conversation authority is unavailable")
    saved = session.get("steward_context") or session["project_context"]
    with bindings.input_context(saved, origin=origin) as selected:
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
            bindings.ensure_delivery_scope(selected)
            _record_local_ingress(controller.coordination_runtime_root, fresh, client_turn_id, message)
        turn, created = controller.store.create_queued_turn(session["session_id"],
            client_turn_id=client_turn_id, message=message, attachments=attachments, origin=origin)
    # Dispatch after releasing the publication fence. The worker independently
    # rechecks current scope before effects, including revocation after enqueue.
    controller.resume_session_queue(session_id=session["session_id"],
        work_dir=context["project"], objective=context["objective"])
    return turn, created


def _record_local_ingress(runtime_root, session, client_turn_id, message):
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
