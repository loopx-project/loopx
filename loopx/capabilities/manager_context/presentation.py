"""Owner-local collaboration readback in the conversation that delegated it."""

from . import _read, _root
from .tracking import _entry, _receipt
from .roundtrip import reply_status
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
    result = []
    for message in messages:
        if (
            message.get("role") not in {"agent", "assistant"}
            or message.get("origin") == "manager_followup"
            or not message.get("turn_id")
        ):
            result.append(message)
            continue
        try:
            turn = store.load_turn(session_id, message["turn_id"]) or {}
            receipt = (turn.get("response") or {}).get("context_handoff_receipt") or {}
            if not receipt:
                result.append(message)
                continue
            # History belongs to the receipt's exact Goal instance, including
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
                decide_collaboration_lifecycle(scope, operation="history_inspect", record=row)
            route = _read(_root(root) / "roundtrips" / (row["request_id"] + ".json"))
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
