"""TypeScript-owned admission planning for managed Chat turns."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from typing import Any

from .control_plane.effect_runtime import effect_runtime_result


CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA = "loopx_chat_turn_acceptance_request_v0"
CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA = "loopx_chat_turn_acceptance_result_v0"
CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA = "loopx_chat_turn_acceptance_v0"


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


def _text_digest(value: Any) -> str:
    if not isinstance(value, str):
        return "invalid"
    return _sha256(value)


def _json_digest(value: Any) -> str:
    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
            sort_keys=True,
        )
    except (TypeError, ValueError):
        return "invalid"
    return _sha256(encoded)


def _acceptance_facts(turn: dict[str, Any]) -> dict[str, Any] | None:
    acceptance = turn.get("_acceptance")
    if acceptance is None:
        return None
    if not isinstance(acceptance, dict):
        return {"schema_version": None}
    return {
        "schema_version": acceptance.get("schema_version"),
        "phase": acceptance.get("phase"),
        "request_sha256": acceptance.get("request_sha256"),
        "message_id": acceptance.get("message_id"),
        "display_message_sha256": _text_digest(
            acceptance.get("display_message")
        ),
        "attachments_sha256": _json_digest(
            acceptance.get("attachments") or None
        ),
    }


def _turn_facts(turn: dict[str, Any] | None) -> dict[str, Any] | None:
    if turn is None:
        return None
    return {
        "turn_id": turn.get("turn_id"),
        "client_turn_id": turn.get("client_turn_id"),
        "status": turn.get("status"),
        "execution_message_sha256": _text_digest(turn.get("message")),
        "origin": turn.get("origin"),
        "loopx_execution": turn.get("loopx_execution", False),
        "loopx_request_sha256": _json_digest(turn.get("loopx_request")),
        "acceptance": _acceptance_facts(turn),
    }


def _transcript_facts(
    messages: list[dict[str, Any]],
    turn_id: str | None,
) -> dict[str, Any]:
    if turn_id is None:
        return {"count": 0}
    matching = [
        row
        for row in messages
        if row.get("role") == "user" and row.get("turn_id") == turn_id
    ]
    if len(matching) != 1:
        return {"count": len(matching)}
    message = matching[0]
    return {
        "count": 1,
        "message_id": message.get("message_id"),
        "display_message_sha256": _text_digest(message.get("text")),
        "attachments_sha256": _json_digest(
            message.get("attachments") or None
        ),
        "origin": message.get("origin"),
    }


def _queued_event_facts(events: list[dict[str, Any]]) -> dict[str, Any]:
    queued = [event for event in events if event.get("kind") == "turn.queued"]
    if len(queued) != 1:
        return {"count": len(queued)}
    return {
        "count": 1,
        "payload_sha256": _json_digest(queued[0].get("payload")),
    }


def _prepared_turn_facts(
    turns: list[dict[str, Any]],
) -> dict[str, Any]:
    prepared = [turn for turn in turns if "_acceptance" in turn]
    if len(prepared) != 1:
        return {"count": len(prepared)}
    return {
        "count": 1,
        "turn_id": prepared[0].get("turn_id"),
        "client_turn_id": prepared[0].get("client_turn_id"),
    }


@dataclass(frozen=True)
class ManagedTurnAcceptanceWrites:
    prepare_turn: bool
    activate_session: bool
    append_message: bool
    append_queued_event: bool
    settle_turn: bool


@dataclass(frozen=True)
class ManagedTurnAcceptancePlan:
    disposition: str
    turn_id: str
    message_id: str
    request_sha256: str
    created: bool
    writes: ManagedTurnAcceptanceWrites
    dispatch_required: bool
    dispatch_reason: str | None


@dataclass(frozen=True)
class AcceptedManagedTurn:
    turn: dict[str, Any]
    created: bool
    dispatch_required: bool
    dispatch_reason: str | None


def _required_string(payload: dict[str, Any], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"chat turn acceptance result has invalid {field}")
    return value


def _required_bool(payload: dict[str, Any], field: str) -> bool:
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"chat turn acceptance result has invalid {field}")
    return value


def _raise_rejection(result: dict[str, Any]) -> None:
    code = result.get("code")
    if code in {"session_not_found", "session_closed"}:
        raise KeyError("chat session was not found")
    if code == "request_conflict":
        raise ValueError(
            "client_turn_id already belongs to a different request"
        )
    if code == "active_turn_conflict":
        active_turn_id = result.get("active_turn_id")
        raise RuntimeError(
            str(active_turn_id or "chat session already has an active turn")
        )
    if code == "original_request_unavailable":
        raise ValueError("client_turn_id original request is unavailable")
    if code == "durable_state_conflict":
        raise ValueError("chat turn acceptance state is inconsistent")
    raise ValueError("chat turn acceptance returned an unsupported rejection")


def _decode_plan(value: Any) -> ManagedTurnAcceptancePlan:
    if not isinstance(value, dict):
        raise ValueError("chat turn acceptance result must be an object")
    if value.get("schema_version") != CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA:
        raise ValueError("chat turn acceptance result schema is unsupported")
    if value.get("kind") == "rejected":
        _raise_rejection(value)
    if value.get("kind") != "accepted":
        raise ValueError("chat turn acceptance result kind is unsupported")
    disposition = _required_string(value, "disposition")
    if disposition not in {"created", "repaired", "replayed"}:
        raise ValueError("chat turn acceptance disposition is unsupported")
    writes = value.get("writes")
    dispatch = value.get("dispatch")
    if not isinstance(writes, dict) or not isinstance(dispatch, dict):
        raise ValueError("chat turn acceptance result is incomplete")
    dispatch_kind = dispatch.get("kind")
    if dispatch_kind not in {"required", "not_required"}:
        raise ValueError("chat turn acceptance dispatch kind is unsupported")
    dispatch_reason = dispatch.get("reason")
    if dispatch_kind == "not_required":
        if dispatch_reason not in {
            "already_started",
            "completion_in_progress",
            "terminal",
        }:
            raise ValueError("chat turn acceptance dispatch reason is unsupported")
    elif dispatch_reason is not None:
        raise ValueError("chat turn acceptance dispatch reason is unsupported")
    return ManagedTurnAcceptancePlan(
        disposition=disposition,
        turn_id=_required_string(value, "turn_id"),
        message_id=_required_string(value, "message_id"),
        request_sha256=_required_string(value, "request_sha256"),
        created=_required_bool(value, "created"),
        writes=ManagedTurnAcceptanceWrites(
            prepare_turn=_required_bool(writes, "prepare_turn"),
            activate_session=_required_bool(writes, "activate_session"),
            append_message=_required_bool(writes, "append_message"),
            append_queued_event=_required_bool(
                writes,
                "append_queued_event",
            ),
            settle_turn=_required_bool(writes, "settle_turn"),
        ),
        dispatch_required=dispatch_kind == "required",
        dispatch_reason=(
            str(dispatch_reason)
            if dispatch_kind == "not_required"
            else None
        ),
    )


def plan_managed_turn_acceptance(
    *,
    session_id: str,
    client_turn_id: str,
    message: str,
    display_message: str,
    attachments: list[dict[str, Any]] | None,
    origin: str,
    loopx_execution: bool,
    loopx_request: dict[str, object] | None,
    candidate_turn_id: str,
    candidate_message_id: str,
    accepted_at: str,
    session: dict[str, Any] | None,
    active_turn: dict[str, Any] | None,
    matching_turn: dict[str, Any] | None,
    turns: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    queued_events: list[dict[str, Any]],
) -> ManagedTurnAcceptancePlan:
    request = {
        "schema_version": CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA,
        "request": {
            "session_id": session_id,
            "client_turn_id": client_turn_id,
            "execution_message_sha256": _text_digest(message),
            "display_message_sha256": _text_digest(display_message),
            "attachments_sha256": _json_digest(attachments or None),
            "origin": origin,
            "loopx_execution": loopx_execution,
            "loopx_request_sha256": _json_digest(loopx_request),
        },
        "candidate": {
            "turn_id": candidate_turn_id,
            "message_id": candidate_message_id,
            "accepted_at": accepted_at,
        },
        "session": (
            {
                "status": session.get("status"),
                "active_turn_id": session.get("active_turn_id"),
            }
            if session is not None
            else None
        ),
        "active_turn": (
            {
                "turn_id": active_turn.get("turn_id"),
                "status": active_turn.get("status"),
            }
            if active_turn is not None
            else None
        ),
        "matching_turn": _turn_facts(matching_turn),
        "transcript": _transcript_facts(
            messages,
            (
                str(matching_turn.get("turn_id"))
                if matching_turn is not None
                else None
            ),
        ),
        "queued_event": _queued_event_facts(queued_events),
        "prepared_turn": _prepared_turn_facts(turns),
    }
    return _decode_plan(effect_runtime_result("chat.turn.accept", request))
