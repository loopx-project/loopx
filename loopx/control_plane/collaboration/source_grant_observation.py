"""Provider/store observation for the typed context-recipient policy owner."""

from pathlib import Path
from typing import Any

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
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

POLICY_SCHEMA = "loopx_manager_context_policy_v1"


def external_source_policy(runtime_root: Path, session: dict[str, Any], turn: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """One provider provenance check for context and explicit execution grants."""
    ingress = _read(_root(runtime_root) / "ingress" /
                    (_hash([session["session_id"], turn["client_turn_id"]]) + ".json"))
    if (ingress["channel"] != session.get("channel_id")
            or ingress["message_digest"] != _hash(turn.get("message"))
            or turn.get("origin") != "lark"):
        raise ValueError("source mismatch")
    policy = _read(_root(runtime_root) / "policy.json")
    if policy.get("schema_version") != POLICY_SCHEMA:
        raise ValueError("invalid policy")
    return ingress, policy.get("sources", {}).get(ingress["channel"], {})


def registered_context_recipients(registry: dict) -> dict:
    """Observe active Goal membership, ignoring unreadable activation rows."""
    active_goals = []
    available = []
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
    session: dict,
    turn: dict,
    available_rows: list[dict],
) -> dict:
    available = {
        (row["goal_id"], row["agent_id"])
        for row in available_rows
        if isinstance(row, dict)
        and isinstance(row.get("goal_id"), str)
        and isinstance(row.get("agent_id"), str)
    }
    scope = conversation_scope(session, origin=turn.get("origin", "unknown"))
    if scope["private_conversation"] and turn.get("origin") == "web":
        allowed = {
            target
            for target in available
            if scope["goal_ids"] is None or target[0] in scope["goal_ids"]
        }
        source_id = "web:" + _hash([session["session_id"], turn["client_turn_id"]])
    else:
        if scope["kind"] != "external_audience":
            return {"mode": "unavailable", "targets": []}
        try:
            ingress, grants = external_source_policy(runtime_root, session, turn)
            selected = effect_runtime_result(
                "collaboration.source.recipients",
                {
                    "source": grants,
                    "sender_id": ingress["sender_id"],
                    "available": available_rows,
                },
            )
            allowed = {
                (value["goal_id"], value["agent_id"])
                for value in selected["targets"]
            }
            source_id = ingress["source_id"]
        except (
            OSError,
            ValueError,
            KeyError,
            TypeError,
            AttributeError,
            EffectRuntimeRejected,
        ):
            return {"mode": "unavailable", "targets": []}
    targets = [
        {"goal_id": goal_id, "agent_id": agent_id}
        for goal_id, agent_id in sorted(allowed & available)
    ]
    return {
        "mode": "context_only",
        "targets": targets,
        "source_id": source_id,
    }


def source_context_target_authority(
    runtime_root: Path,
    session: dict,
    turn: dict,
    target: dict,
) -> dict:
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
    runtime_root: Path, registry_path: Path | None, session: dict[str, Any], turn: dict[str, Any]
) -> dict[str, Any]:
    """Observe one compatible registry; TS owns sender and exact binding grants."""
    if conversation_scope(session, origin=turn.get("origin", "unknown"))["kind"] != "external_audience":
        return {"bindings": []}
    observed = _source_registry_recipients(registry_path)
    ingress, source = external_source_policy(runtime_root, session, turn)
    result: dict[str, Any] = effect_runtime_result("collaboration.source.execution_bindings", {
        "source": source, "sender_id": ingress["sender_id"], "available": observed["available"],
    })
    return result


def source_context_authority(
    runtime_root: Path, registry_path: Path, session: dict, turn: dict
) -> dict:
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
