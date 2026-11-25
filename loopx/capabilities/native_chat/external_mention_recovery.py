"""Recover only a selected external mention with a proved pre-dispatch failure."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...chat.ingress import resolve_external_admission_source
from ...chat_store import TERMINAL_TURN_STATES, _atomic_write_json
from ...file_lock import exclusive_file_lock
from .file_discovery import external_source_route_path

if TYPE_CHECKING:
    from .external_conversations import ChatExternalConversations


def requeue_rejected_mention(core: ChatExternalConversations, request_ref: str, *, original_turn_id: str,
                             source_preflight: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Admit an explicitly selected, independently verified private source once."""
    from ...chat import require_matching_replay
    from ...chat_manager_context import manager_authorization_scope_is_current

    if not re.fullmatch(r"[a-f0-9]{24}", request_ref) or not callable(source_preflight):
        raise ValueError("invalid original mention replay")
    path = core.root / f"{request_ref}.json"
    error = "live_steering_session_not_attached"
    with exclusive_file_lock(path, operation="admit_external_chat_request"):
        row = core.read_request(request_ref)
        replay = row.get("rejected_mention_replay")
        if replay is not None:
            if replay["target_turn_id"] != original_turn_id:
                raise ValueError("the original mention replay target changed")
            if row["status"] == "accepted":
                return row  # Repair transport correlation after a committed admission.
        sid = row["session_id"]
        capture = row.get("mention_capture") or {}
        if (row["status"] != "command_completed" or row.get("delivery_verified") is not True
                or row.get("origin") != "lark" or row.get("command") or row.get("agent_target")
                or row.get("attachments") or row.get("native_mention") is not True
                or row["source"].get("private_human_message") is not True
                or row.get("mention_delivery") != {"action": "reject", "error_code": error}
                or row["turn_id"] != original_turn_id or capture.get("target_turn_id") != original_turn_id
                or capture.get("session_id") != sid):
            raise ValueError("the original mention lacks a pre-dispatch rejection")
        route_lock = external_source_route_path(core.root, row["binding_id"], row["source"]["source_ref"])
        with exclusive_file_lock(route_lock, operation="route_external_chat_request"):
            store = core.controller.store
            client = f"external-{request_ref}"
            session = store.load_session(sid)
            prior = store.load_turn(sid, original_turn_id)
            receipt = store.steering_receipt(sid, client) or {}
            if (receipt.get("mode") != "live_steering" or receipt.get("status") != "failed"
                    or receipt.get("error_code") != error or receipt.get("expected_turn_id") != original_turn_id
                    or receipt.get("active_turn_id") is not None or receipt.get("native_steering") is not None
                    or receipt.get("message") != row["message"]):
                raise ValueError("the original steering receipt does not prove non-delivery")
            turn = store.turn_for_client(sid, client)
            if turn is not None:
                if replay is None:
                    raise ValueError("the original client identity already has a Turn")
                require_matching_replay(
                    {**turn, "message": turn.get("_queue_original_message", turn.get("message"))},
                    identity="client_turn_id", request={"message": row["message"], "attachments": None,
                        "origin": "lark", "external_agent_target": None},
                )
            else:
                if (not session or session.get("status") == "closed" or not session.get("steward_context")
                        or not prior or prior["status"] not in TERMINAL_TURN_STATES
                        or core._source_stop_observed(row, prior, store.events_after(sid, original_turn_id, None))):
                    raise ValueError("the original mention replay is not eligible")
                selected = resolve_external_admission_source(
                    core.bindings, row["binding_id"], row["source"], source_preflight, session=session,
                )
                if (selected["owner_manager_audience"] is not True
                        or selected["channel_id"] != capture.get("channel_id")
                        or not manager_authorization_scope_is_current(core.controller.registry_path,
                            core.controller.manager_scope_resolver, session, session.get("manager_authorization_scope_id"),
                            runtime_root=core.controller.coordination_runtime_root)):
                    raise ValueError("the original steward source or authority changed")
                if replay is None:
                    row["rejected_mention_replay"] = {"target_turn_id": original_turn_id, "original_response": row["response"]}
                    _atomic_write_json(path, row)
                core._record_steward_ingress(row, selected, session, client)
                turn, _ = core.controller.enqueue_turn(session_id=sid, client_turn_id=client,
                    message=row["message"], attachments=None, work_dir=Path("."), objective="", origin="lark")
            row.update(status="accepted", turn_id=turn["turn_id"], response=None, delivery_verified=False)
            _atomic_write_json(path, row)
            return row
