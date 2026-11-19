"""Provider/store observation for the typed context-recipient policy owner."""

from contextlib import nullcontext
from pathlib import Path
from typing import Any, cast

from ...agent_registry import registered_agent_ids_for_goal
from ..goals.activation import goal_is_stopped
from ..goals.goal_ref_validation import exact_goal_ref
from ..projects.registry_codec import (
    SOURCE_SESSION_PROFILE_ID,
    load_project_registry,
    require_runtime_compatible_project_registry,
)
from . import conversation_scope
from .inbox import _hash, _read, _root
from .source_policy_fence import source_policy_fence
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

POLICY_SCHEMA = "loopx_manager_context_policy_v1"


def _source_ingress(runtime_root: Path, session: dict[str, Any], turn: dict[str, Any]) -> dict[str, Any]:
    return _read(_root(runtime_root) / "ingress" /
                 (_hash([session["session_id"], turn["client_turn_id"]]) + ".json"))


def _source_policy(runtime_root: Path, ingress: dict[str, Any]) -> dict[str, Any]:
    policy = _read(_root(runtime_root) / "policy.json")
    if policy.get("schema_version") != POLICY_SCHEMA:
        raise ValueError("invalid policy")
    return cast(dict[str, Any], policy.get("sources", {}).get(ingress["channel"], {}))


def external_source_policy(runtime_root: Path, session: dict[str, Any], turn: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Observe host provenance for the shared typed source-policy owner."""
    ingress = _source_ingress(runtime_root, session, turn)
    try:
        effect_runtime_result("collaboration.source.ingress", {
            "session": {key: session.get(key) for key in ("session_id", "goal_id", "channel_id", "steward_context")},
            "turn": {"client_turn_id": turn.get("client_turn_id"), "origin": turn.get("origin"),
                     "message_digest": _hash(turn.get("message"))},
            "ingress": {key: ingress[key] for key in ("session_id", "client_turn_id", "channel", "message_digest",
                                                      "sender_id", "source_id", "origin") if key in ingress},
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc

    return ingress, _source_policy(runtime_root, ingress)


def registered_context_recipients(registry: dict[str, Any]) -> dict[str, Any]:
    """Observe active Goal membership, ignoring unreadable activation rows."""
    active_goals = []
    available: list[dict[str, Any]] = []
    for goal in registry.get("goals", []):
        if not isinstance(goal, dict) or not goal.get("id"):
            continue
        try:
            if goal_is_stopped(goal):
                continue
        except ValueError:
            continue
        active_goals.append(goal["id"])
        available.extend({"goal_id": goal["id"], "agent_id": agent}
                         for agent in registered_agent_ids_for_goal(goal))
    return {"active_goal_ids": active_goals, "available": available}


def _source_context_grant(
    runtime_root: Path,
    session: dict[str, Any],
    turn: dict[str, Any],
    available_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    # The channel selects stored ingress IO, never delivery authority. Core
    # classifies the canonical conversation and decides the complete grant.
    source = None
    ingress = None
    source_id = None
    channel = session.get("channel_id")
    if isinstance(channel, str) and channel.startswith("manager.external."):
        try:
            ingress = _source_ingress(runtime_root, session, turn)
            source = _source_policy(runtime_root, ingress)
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
        ):
            source = None
    elif turn.get("origin") == "web" and "session_id" in session and "client_turn_id" in turn:
        source_id = "web:" + _hash([session["session_id"], turn["client_turn_id"]])
    return cast(dict[str, Any], effect_runtime_result(
        "collaboration.source.context_authority",
        {
            "conversation": {key: session[key] for key in (
                "session_id", "goal_id", "channel_id", "project_context", "steward_context",
            ) if key in session} | {"origin": turn.get("origin", "unknown")},
            "turn": {"client_turn_id": turn.get("client_turn_id"), "origin": turn.get("origin"),
                     "message_digest": _hash(turn.get("message"))} if ingress is not None else None,
            "ingress": {key: ingress[key] for key in (
                "session_id", "client_turn_id", "channel", "message_digest", "sender_id", "source_id", "origin",
            ) if key in ingress} if ingress is not None else None,
            "available": available_rows,
            "source": source,
            "source_id": source_id,
        },
    ))


def source_context_target_authority(
    runtime_root: Path,
    session: dict[str, Any],
    turn: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    """Authorize one target whose exact Goal scope was already validated."""
    return _source_context_grant(runtime_root, session, turn, [target])


def _source_registry_recipients(registry_path: Path | None, *, context_only: bool = False) -> dict[str, Any]:
    if registry_path is None:
        raise ValueError("context source registry unavailable")
    registry = load_project_registry(registry_path)
    if not isinstance(registry, dict):
        raise ValueError("invalid registry")
    if context_only and registry.get("profile_id") == SOURCE_SESSION_PROFILE_ID:
        goals = registry.get("goals")
        if not isinstance(goals, list):
            raise ValueError("source-session registry has no Goal list")
        # This is observation for context handoff, not execution admission.
        # Enumerate only instance-bound Goals; the handoff's own Goal scope
        # still rechecks the selected exact GoalRef before it commits.
        instantiated = []
        for goal in goals:
            if not isinstance(goal, dict):
                continue
            goal_id = goal.get("id")
            instance_id = goal.get("goal_instance_id")
            if not isinstance(goal_id, str) or not isinstance(instance_id, str):
                continue
            try:
                exact_goal_ref(goal_id, instance_id)
            except ValueError:
                continue
            instantiated.append(goal)
        if not instantiated:
            raise ValueError("source-session registry has no instantiated Goal")
        registry = {**registry, "goals": instantiated}
    else:
        require_runtime_compatible_project_registry(
            registry, operation="context source recipient observation"
        )
    return registered_context_recipients(registry)


def source_execution_bindings(
    runtime_root: Path, registry_path: Path | None, session: dict[str, Any], turn: dict[str, Any],
    *, required_binding: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Observe one compatible registry; TS owns sender and exact binding grants."""
    if conversation_scope(session, origin=turn.get("origin", "unknown"))["kind"] != "external_audience":
        if required_binding is not None:
            raise ValueError("original source execution grant requires verified external provenance")
        return {"bindings": []}
    observed = _source_registry_recipients(registry_path)
    ingress, source = external_source_policy(runtime_root, session, turn)
    result: dict[str, Any] = effect_runtime_result("collaboration.source.execution_bindings", {
        "source": source, "sender_id": ingress["sender_id"], "available": observed["available"],
        **({"required_binding": required_binding} if required_binding is not None else {}),
    })
    return result


def require_delegation_source_execution(runtime_root: Path, registry_path: Path, *,
        goal_id: str, requester_agent_id: str, source_request_id: str,
        binding: dict[str, Any], caller_goal_ref: dict[str, Any] | None,
        policy_locked: bool = False) -> dict[str, str]:
    """Reobserve the original Chat source; the existing TS owner decides its grant.

    A return address only locates frozen evidence. It never grants execution,
    and observing it must not recover or mutate the original Chat store.
    """
    from .goal_instance_scope import collaboration_goal_scope
    from .inbox import _entry
    from .source_chat_observation import observe_source_chat

    try:
        policy_guard = (
            nullcontext()
            if policy_locked
            else source_policy_fence(_root(runtime_root) / "policy.json")
        )
        with policy_guard, collaboration_goal_scope(
            registry_path, goal_id=goal_id, agents=(requester_agent_id, binding["agent_id"]),
            caller_goal_ref=caller_goal_ref, require_active=True,
        ) as scope:
            original = _entry(runtime_root, goal_id, binding["agent_id"], source_request_id, scope=scope)
            route = _read(_root(runtime_root) / "roundtrips" / (source_request_id + ".json"))
            if (original.get("execution_binding_id") != binding["id"]
                    or any(route.get(key) != original.get(key) for key in
                           ("request_id", "goal_id", "agent_id", "source_id", "goal_ref"))):
                raise ValueError("original source execution identity changed")
            session, turn = observe_source_chat(runtime_root, route)
            target = {"goal_id": goal_id, "agent_id": binding["agent_id"]}
            grant = source_context_target_authority(runtime_root, session, turn, target)
            if grant.get("source_id") != original["source_id"] or target not in grant.get("targets", []):
                raise ValueError("original source execution context is no longer authorized")
            source_execution_bindings(runtime_root, registry_path, session, turn,
                required_binding={**target, "requester_agent_id": requester_agent_id, "binding_id": binding["id"]})
            return {"session_id": session["session_id"], "turn_id": turn["turn_id"]}
    except (OSError, ValueError, KeyError, TypeError, EffectRuntimeRejected) as exc:
        raise ValueError("original source execution authority is unavailable or revoked") from exc


def verify_delegation_source_execution(service, binding_id: str, operation_id: str, *,
        source_request_id: str, brief: dict, conversation: dict,
        policy_locked: bool = False) -> None:
    """Reobserve exact source/peer work before replay or worker-owned effects.

    This performs no request publication, worker submission or result adoption.
    Legacy sources retain their legacy lifetime profile; they are not promoted.
    """
    from .goal_instance_scope import collaboration_goal_scope, decide_collaboration_lifecycle
    from .inbox import _entry
    from .peers import _operation_address, require_operation_id

    require_operation_id(operation_id)
    runtime_root, registry_path = service.root, service.registry
    goal_id, requester_agent_id = service.goal_id, service.agent_id
    caller_goal_ref = service._caller_goal_ref()
    binding = service.binding(binding_id, require_active=True)
    source_conversation = require_delegation_source_execution(runtime_root, registry_path, goal_id=goal_id,
        requester_agent_id=requester_agent_id, source_request_id=source_request_id,
        binding=binding, caller_goal_ref=caller_goal_ref, policy_locked=policy_locked)
    record = _read(service.path(operation_id))
    with collaboration_goal_scope(registry_path, goal_id=goal_id,
            agents=(requester_agent_id, binding["agent_id"]),
            caller_goal_ref=caller_goal_ref, require_active=True) as scope:
        original = _entry(runtime_root, goal_id, binding["agent_id"], source_request_id, scope=scope)
        peer = _entry(runtime_root, goal_id, binding["agent_id"], record["identity"]["request_id"], scope=scope)
        _, operation_path = _operation_address(runtime_root, scope, requester_agent_id, operation_id)
        for entry in (original, peer):
            decide_collaboration_lifecycle(scope, operation="history_inspect", record=entry)
        try:
            effect_runtime_result("collaboration.delegation.source_replay", {
                "config": _read(service.config), "binding_id": binding["id"], "agent_id": requester_agent_id,
                "goal_id": goal_id, "goal_ref": scope.record_identity().get("goal_ref"), "record": record,
                "operation_id": operation_id, "source_request_id": source_request_id,
                "source_request": original, "peer_request": peer, "peer_operation": _read(operation_path),
                "brief": brief, "conversation": conversation, "source_conversation": source_conversation,
            })
        except EffectRuntimeRejected as exc:
            raise ValueError("delegation operation identity conflict in original source execution") from exc


def read_delegation_source_execution(service, binding_id: str, operation_id: str, *,
        source_request_id: str, brief: dict, conversation: dict) -> dict:
    verify_delegation_source_execution(service, binding_id, operation_id,
        source_request_id=source_request_id, brief=brief, conversation=conversation)
    return service.read(operation_id)


def source_context_authority(
    runtime_root: Path, registry_path: Path, session: dict[str, Any], turn: dict[str, Any]
) -> dict[str, Any]:
    """Return only a write-only recipient catalog; no cross-audience Goal evidence."""
    if registry_path is None:
        return {"mode": "unavailable", "targets": []}
    try:
        observed = _source_registry_recipients(registry_path, context_only=True)

    except (OSError, ValueError, TypeError):
        return {"mode": "unavailable", "targets": []}
    return _source_context_grant(
        runtime_root,
        session,
        turn,
        observed["available"],
    )
