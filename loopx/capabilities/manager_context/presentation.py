"""Owner-local collaboration readback in the conversation that delegated it."""

from . import _read, _root
from .tracking import _entry, _receipt
from .roundtrip import _original_delivery_facts, reply_status
from ...chat import redact_local_paths
from ...control_plane.collaboration import conversation_scope
from ...control_plane.collaboration.goal_instance_scope import (
    collaboration_goal_scope,
    decide_collaboration_lifecycle,
)


def project_collaboration(store, root, session_id, messages, *, registry):
    session = store.load_session(session_id)
    if not session or not conversation_scope(session)["private_conversation"]:
        return messages
    # Prefer the saved answer; when it was lost, keep the commitment on the
    # original user message. Never append a synthetic answer or mutate Chat.
    carriers, turns = {}, {}
    for message in messages:
        tid = message.get("turn_id")
        if not tid or message.get("origin") == "manager_followup":
            continue
        if message.get("role") in {"agent", "assistant"} or tid not in carriers:
            carriers[tid] = message.get("message_id")
        if tid not in turns:
            turns[tid] = store.load_turn(session_id, tid) or {}
    missing_clients = {
        turn.get("client_turn_id") for turn in turns.values()
        if turn.get("response") is None
    }
    recovered = {}
    # Existing route records contain only delivery identity. The original
    # request is read below only after matching this owner-private conversation.
    # Keep the same bounded historical discovery as the existing Inbox readback.
    for path in sorted((_root(root) / "roundtrips").glob("*.json"))[:2000] if missing_clients else []:
        try:
            route = _read(path)
            client = route.get("client_turn_id")
            if route.get("session_id") != session_id or client not in missing_clients:
                continue
            recovered.setdefault(client, []).append(route)
        except (OSError, ValueError, TypeError):
            continue
    result = []
    for message in messages:
        tid = message.get("turn_id")
        if not tid or carriers.get(tid) != message.get("message_id"):
            result.append(message)
            continue
        try:
            turn = turns[tid]
            receipt = (turn.get("response") or {}).get("context_handoff_receipt")
            if not receipt:
                routes = recovered.get(turn.get("client_turn_id"), [])
                if len(routes) != 1:
                    result.append(message)
                    continue
                receipt = routes[0]
            # History belongs to the committed request's exact Goal instance, including
            # after the alias is recreated. Reuse the typed history decision;
            # a current alias alone must never redirect this read.
            with collaboration_goal_scope(
                registry,
                goal_id=receipt["goal_id"],
                agents=(),
                caller_goal_ref=receipt.get("goal_ref"),
            ) as scope:
                row = _entry(
                    root, receipt["goal_id"], receipt["agent_id"], receipt["request_id"],
                    scope=scope,
                )
                route = _read(_root(root) / "roundtrips" / (row["request_id"] + ".json"))
                decide_collaboration_lifecycle(
                    scope, operation="original_request_inspect", record=row, route=route,
                    initial_delivery=_original_delivery_facts(row, route, turn, source_id=row["source_id"]),
                )
            if (
                route.get("session_id") != session_id
                or route.get("client_turn_id") != turn.get("client_turn_id")
                or route.get("goal_ref") != row.get("goal_ref")
                or receipt.get("goal_ref") != row.get("goal_ref")
                or not row.get("brief")
            ):
                result.append(message)
                continue
            decision, error = _receipt(root, "decisions", row)
            read, read_error = _receipt(root, "reads", row)

            # Only display data is redacted. The receiver's immutable original
            # context is retained in the owner-private store.
            def safe(value):
                if isinstance(value, str):
                    return redact_local_paths(value)
                if isinstance(value, list):
                    return [safe(item) for item in value]
                if isinstance(value, dict):
                    return {key: safe(item) for key, item in value.items()}
                return value

            result.append(
                {
                    **message,
                    "collaboration": {
                        "schema_version": "collaboration_request_readback_v0",
                        "request_id": row["request_id"],
                        "agent_id": row["agent_id"],
                        "goal_id": row["goal_id"],
                        "brief": safe(row["brief"]),
                        "read_status": "unavailable"
                        if read_error
                        else "supplied"
                        if read
                        else "pending",
                        "decision": "unavailable"
                        if error
                        else decision.get("decision", "pending"),
                        "decision_reason": safe(decision.get("reason", "")) if not error else "",
                        "returns": reply_status(root, row),
                    },
                }
            )
        except (OSError, ValueError, KeyError, TypeError):
            # Missing readback is not a change to the saved conversation.
            result.append(message)
    return result
