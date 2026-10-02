"""Observe existing parent records and source grants for typed peer admission."""

from .inbox import _entry, _read, _root
from . import conversation_scope
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


def require_parent_context_access(root, registry, parent, target_agent_id, *, scope):
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
            return  # An independent local peer request has no source context.
        original = _entry(root, scope.goal_id, original["source_agent_id"],
                          ancestor_id, scope=scope)

    conversation = {
        "channel_id": original.get("source_channel"), "goal_id": scope.goal_id,
        "origin": "web" if str(original.get("source_id", "")).startswith("web:") else "unknown",
    }
    grant = None
    if not conversation_scope(conversation, origin=conversation["origin"])["private_conversation"]:
        # Use the original provider/store provenance and the existing source
        # authority owner. Reading must not recover or rewrite a return route.
        from ...chat_store import ChatSessionStore
        from .source_grants import source_context_authority

        try:
            route = _read(_root(root) / "roundtrips" / (original["request_id"] + ".json"))
            if any(route.get(key) != original.get(key) for key in
                   ("request_id", "goal_id", "agent_id", "source_id", "goal_ref")):
                raise ValueError("original source route identity mismatch")
            store = ChatSessionStore(root)
            session = store.load_session(route["session_id"])
            turn = store.turn_for_client(route["session_id"], route["client_turn_id"])
            if (not session or session.get("status") == "closed" or not turn
                    or session.get("channel_id") != route.get("channel_id")
                    or session.get("channel_id") != original.get("source_channel")):
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
