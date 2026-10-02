"""Provider/store observation for the typed context-recipient policy owner."""

from pathlib import Path

from ...agent_registry import registered_agent_ids_for_goal
from ..goals.activation import goal_is_stopped
from ..projects.registry_codec import load_project_registry
from . import conversation_scope
from .inbox import _hash, _read, _root
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

POLICY_SCHEMA = "loopx_manager_context_policy_v1"


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


def source_context_authority(
    runtime_root: Path, registry_path: Path, session: dict, turn: dict
) -> dict:
    """Return only a write-only recipient catalog; no cross-audience Goal evidence."""
    if registry_path is None:
        return {"mode": "unavailable", "targets": []}
    try:
        registry = load_project_registry(registry_path)
        if not isinstance(registry, dict):
            raise ValueError("invalid registry")
    except (OSError, ValueError, TypeError):
        return {"mode": "unavailable", "targets": []}
    observed = registered_context_recipients(registry)
    available = {(row["goal_id"], row["agent_id"]) for row in observed["available"]}
    scope = conversation_scope(session, origin=turn.get("origin", "unknown"))
    if scope["private_conversation"] and turn.get("origin") == "web":
        allowed = {target for target in available
                   if scope["goal_ids"] is None or target[0] in scope["goal_ids"]}
        source_id = "web:" + _hash([session["session_id"], turn["client_turn_id"]])
    else:
        if scope["kind"] != "external_audience":
            return {"mode": "unavailable", "targets": []}
        try:
            ingress = _read(
                _root(runtime_root)
                / "ingress"
                / (_hash([session["session_id"], turn["client_turn_id"]]) + ".json")
            )
            if (
                ingress["channel"] != session.get("channel_id")
                or ingress["message_digest"] != _hash(turn.get("message"))
                or turn.get("origin") != "lark"
            ):
                raise ValueError("source mismatch")
            policy = _read(_root(runtime_root) / "policy.json")
            if policy.get("schema_version") != POLICY_SCHEMA:
                raise ValueError("invalid policy")
            grants = policy.get("sources", {}).get(ingress["channel"], {})
            selected = effect_runtime_result("collaboration.source.recipients", {
                "source": grants, "sender_id": ingress["sender_id"],
                "available": observed["available"],
            })
            allowed = {(v["goal_id"], v["agent_id"]) for v in selected["targets"]}
            source_id = ingress["source_id"]
        except (OSError, ValueError, KeyError, TypeError, AttributeError, EffectRuntimeRejected):
            return {"mode": "unavailable", "targets": []}
    targets = [
        {"goal_id": g, "agent_id": a} for g, a in sorted(allowed & available)
    ]
    return {
        "mode": "context_only",
        "targets": targets,
        "source_id": source_id,
    }
