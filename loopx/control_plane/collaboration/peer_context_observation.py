"""Observe existing parent records and source grants for typed peer admission."""

from pathlib import Path
from typing import Any

from .goal_instance_scope import CollaborationGoalScope
from .inbox import _entry, _read, _root
from . import conversation_scope
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


def require_parent_context_access(root: Path, registry: Path, parent: dict[str, Any], target_agent_id: str,
                                  *, scope: CollaborationGoalScope) -> dict[str, Any]:
    # Follow references, not recursively copied context or model-authored claims.
    # Every row is loaded under the same Goal instance and addressed recipient.
    original = parent
    agents = {target_agent_id}
    seen = set()
    while True:
        if original["request_id"] in seen:
            raise ValueError("peer parent lineage contains a cycle")
        seen.add(original["request_id"])
        agents.add(original["agent_id"])
        if original.get("source_kind") != "peer":
            break
        ancestor_id = original.get("parent_request_id")
        if not ancestor_id:
            # An independent local peer request has no inherited source grant.
            return {"local_source": True, "source_id": original["source_id"]}
        original = _entry(root, scope.goal_id, original["source_agent_id"],
                          ancestor_id, scope=scope)

    conversation = {
        "channel_id": original.get("source_channel"), "goal_id": scope.goal_id,
        "origin": "web" if str(original.get("source_id", "")).startswith("web:") else "unknown",
    }
    grant = None
    local_source = conversation_scope(conversation, origin=conversation["origin"])["private_conversation"]
    if not local_source:
        # Use the original provider/store provenance and the existing source
        # authority owner. Reading must not recover or rewrite a return route.
        from .source_chat_observation import observe_source_chat
        from .source_grant_observation import source_context_authority

        try:
            route = _read(_root(root) / "roundtrips" / (original["request_id"] + ".json"))
            if any(route.get(key) != original.get(key) for key in
                   ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")):
                raise ValueError("original source route identity mismatch")
            session, turn = observe_source_chat(root, route)
            if session.get("channel_id") != original.get("source_channel"):
                raise ValueError("original source conversation unavailable for peer context forwarding")
        except (OSError, KeyError) as exc:
            raise ValueError("original source conversation unavailable for peer context forwarding") from exc
        grant = source_context_authority(root, registry, session, turn)

    try:
        effect_runtime_result("collaboration.peer.context_access", {
            "conversation": conversation, "source_id": original.get("source_id"),
            "goal_id": scope.goal_id, "agent_ids": sorted(agents), "grant": grant,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    return {"local_source": local_source, "source_id": original["source_id"]}
