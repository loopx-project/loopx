"""Addressed machine-manager conversations, including group-root messages."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any
from pathlib import Path

from ...control_plane.collaboration import conversation_trigger
from ...chat_manager import (
    manager_channel,
    manager_connection_executor_endpoint,
    manager_executor_endpoint_default,
    steward_machine_defaults,
)
from ..external_connector_runtime import project_external_connector_status
from .goal_channel_contracts import bindings_for_goal
from .goal_channel_targets import goal_channel_target_for_name
from .goal_channel_transport import CHAT_ID_PATTERN, MESSAGE_ID_PATTERN
from .goal_topic_routing import is_event_addressed_to_bot


class ManagerAuthorityMode(str, Enum):
    """The only authority states a manager route may enter."""

    CONTEXT_ONLY = "context_only"
    TURN_AUTHORIZED = "turn_authorized"


def manager_turn_executor(runtime_controller: Any) -> str:
    """Resolve the manager executor from this machine's current selection."""

    return manager_executor_endpoint_default(
        machine_defaults=steward_machine_defaults(runtime_controller)
    )


def manager_session_requires_executor_rebind(
    session: Mapping[str, Any] | None,
    *,
    expected_channel: str,
    agent_id: str,
) -> bool:
    """Identify a live manager Session left on the machine's old executor."""

    return bool(
        session is not None
        and session.get("channel_id") == expected_channel
        and session.get("agent_id") != agent_id
        and session.get("status") != "closed"
    )


def parse_manager_authority_mode(value: object) -> ManagerAuthorityMode | None:
    """Parse a persisted route mode without coercing unknown values."""

    if not isinstance(value, str):
        return None
    try:
        return ManagerAuthorityMode(value)
    except ValueError:
        return None


def invalid_manager_authority_result(
    route: Mapping[str, Any], *, inbox_config_ref: str
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "invalid_manager_authority_mode",
        "goal_id": route["goal_id"],
        "inbox_config_ref": inbox_config_ref,
        "turn_authorized": False,
        "model_invoked": False,
        "external_write_performed": False,
        "source_acknowledged": False,
    }


def unavailable_manager_context_result(
    route: Mapping[str, Any], *, inbox_config_ref: str
) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "context_materials_unavailable",
        "goal_id": route["goal_id"],
        "inbox_config_ref": inbox_config_ref,
        "source_acknowledged": False,
    }


def has_manager_binding(payloads: Mapping[str, Any], target_ref: str) -> bool:
    return any(
        item.get("enabled") is True
        and item.get("target_ref") == target_ref
        and (item.get("routing") or {}).get("conversation_kind") == "manager"
        for goal_id, payload in payloads.items()
        if isinstance(payload, Mapping)
        for item in bindings_for_goal(payload, str(goal_id))
    )


def decide_manager_event(
    *,
    target_payload: Mapping[str, Any],
    binding_payloads: Mapping[str, Any],
    event: Mapping[str, Any],
    runtime_root: str | Path | None = None,
) -> dict[str, Any] | None:
    """A manager receives addressed messages; exact worker Topics keep priority.

    The route carries the executor this machine selected for its manager
    channel rather than the one the connection recorded when it was created, so
    a machine that changes its steward executor does not keep answering on the
    endpoint that happened to be the default on the day of the connection.
    """
    chat_id, message_id = (
        str(event.get("chat_id") or ""),
        str(event.get("message_id") or ""),
    )
    if not CHAT_ID_PATTERN.fullmatch(chat_id) or not MESSAGE_ID_PATTERN.fullmatch(
        message_id
    ):
        return None
    root = str(event.get("root_id") or "")
    candidates = []
    for goal_id, payload in binding_payloads.items():
        for binding in bindings_for_goal(payload, goal_id):
            if binding.get("enabled") is not True:
                continue
            target = (
                goal_channel_target_for_name(
                    target_payload, str(binding.get("target_ref") or "")
                )
                or {}
            )
            if (target.get("channel") or {}).get("chat_id") != chat_id:
                continue
            routing = binding.get("routing") or {}
            topic_root = str(
                (binding.get("topic") or {}).get("root_message_id")
                or (binding.get("channel") or {}).get("pinned_message_id")
                or ""
            )
            if routing.get("conversation_kind") != "manager":
                if root and root == topic_root:
                    return None
                continue
            candidates.append((goal_id, binding, target, routing, topic_root))
    if not candidates:
        return None

    def ignored(reason: str) -> dict[str, Any]:
        return {"matched": False, "reason": reason, "route": None}

    if len(candidates) != 1:
        return ignored("route_ambiguous")
    goal_id, binding, target, routing, topic_root = candidates[0]
    identity = target.get("identity") or {}
    if str(event.get("sender_id") or "") in {
        str(identity.get("bot_open_id") or "__unset__"),
        str(identity.get("bot_app_id") or "__unset__"),
    }:
        return ignored("self_message")
    connector = binding.get("connector")
    if not _valid_manager_binding(goal_id, binding, routing):
        return ignored("invalid_routing_state")
    profile = str(identity.get("sender_profile") or "default")
    try:
        trigger = conversation_trigger(
            routing.get("turn_trigger"),
            addressed=is_event_addressed_to_bot(event, identity),
            bot_message=event.get("sender_type") == "app",
            human=event.get("sender_type") == "user" and bool(event.get("sender_id")),
            historical=event.get("historical_context_only") is True,
        )
    except ValueError:
        return ignored("invalid_routing_state")
    turn_authorized = trigger["authorized"]
    executor_endpoint_id, executor_endpoint_source = (
        manager_connection_executor_endpoint(runtime_root)
    )
    return {
        "matched": True,
        "reason": "matched" if turn_authorized else "context_only",
        "route": {
            "goal_id": goal_id,
            "connection_id": binding["connection_id"],
            "agent_id": binding["agent_id"],
            "session_id": binding["session_id"],
            "conversation_kind": "manager",
            "executor_endpoint_id": executor_endpoint_id,
            "executor_endpoint_source": executor_endpoint_source,
            "manager_channel_id": manager_channel(
                provider="lark", audience=f"{profile}\0{chat_id}"
            ),
            "app_ref": profile,
            "target_ref": binding["target_ref"],
            "message_id": message_id,
            "event_id": str(event.get("event_id") or message_id),
            "topic_root_message_id": topic_root,
            # Provider evidence is adapted to the shared conversation rule.
            # Capture is not admission; the explicit trigger selects live input.
            "turn_trigger": trigger["mode"],
            "trigger_reason": trigger["reason"],
            "capture_scope": "configured_chat_all",
            "authority_mode": (
                ManagerAuthorityMode.TURN_AUTHORIZED.value
                if turn_authorized
                else ManagerAuthorityMode.CONTEXT_ONLY.value
            ),
            "ingress_mode": "session_queue",
            "reply_mode": "topic_reply",
            "connector": dict(connector),
        },
    }


def _valid_manager_binding(goal_id, binding, routing) -> bool:
    connector = binding.get("connector")
    try:
        status = project_external_connector_status(connector)
        return bool(
            routing.get("ingress_mode") == "session_queue"
            and binding.get("session_id")
            and status["goal_ref"] == goal_id
            and status["agent_ref"] == binding.get("agent_id")
            and status["ingress_policy"] == "session_queue"
            and connector.get("session_ref") == binding.get("session_id")
        )
    except (TypeError, ValueError, AttributeError):
        return False


def authorized_manager_goal_ids(
    snapshot: Mapping[str, Any], session: Mapping[str, Any], *, runtime_root: Path | None = None
) -> list[str]:
    """Resolve current external read authority; a session's old Goal is not a grant."""
    candidates = []
    targets = snapshot.get("target_payload") or {}
    for goal_id, payload in (snapshot.get("binding_payloads") or {}).items():
        for binding in bindings_for_goal(payload, goal_id):
            routing = binding.get("routing") or {}
            if (
                binding.get("enabled") is not True
                or routing.get("conversation_kind") != "manager"
            ):
                continue
            target = (
                goal_channel_target_for_name(
                    targets, str(binding.get("target_ref") or "")
                )
                or {}
            )
            if target.get("enabled") is not True:
                continue
            profile = str(
                (target.get("identity") or {}).get("sender_profile") or "default"
            )
            chat_id = str((target.get("channel") or {}).get("chat_id") or "")
            channel = manager_channel(provider="lark", audience=f"{profile}\0{chat_id}")
            if channel != session.get("channel_id") or not CHAT_ID_PATTERN.fullmatch(
                chat_id
            ):
                continue
            candidates.append((goal_id, binding, routing))
    if len(candidates) != 1:
        return []
    goal_id, binding, routing = candidates[0]
    if (
        binding.get("session_id") != session.get("session_id")
        or manager_connection_executor_endpoint(runtime_root)[0]
        != session.get("agent_id")
        or not _valid_manager_binding(goal_id, binding, routing)
    ):
        return []
    if runtime_root is not None:
        from ...capabilities.manager_context import evidence_goal_scope
        grant = evidence_goal_scope(runtime_root, str(session.get("channel_id") or ""))
        if grant is not None:
            return grant
    return [goal_id]
