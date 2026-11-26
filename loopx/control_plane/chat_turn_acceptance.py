"""TypeScript-owned admission planning for managed Chat turns."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Sized
from dataclasses import dataclass
import hashlib
import json
from typing import TypedDict

from .effect_runtime import effect_runtime_result


CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA = "loopx_chat_turn_acceptance_request_v0"
CHAT_TURN_ACCEPTANCE_RESULT_SCHEMA = "loopx_chat_turn_acceptance_result_v0"
CHAT_TURN_ACCEPTANCE_CAPSULE_SCHEMA = "loopx_chat_turn_acceptance_v0"
TERMINAL_TURN_STATUSES = {"completed", "interrupted", "timed_out", "failed"}
_STEERING_RECEIPT_FIELDS = (
    "status",
    "mode",
    "expected_turn_id",
    "active_turn_id",
    "error_code",
)
_DISPATCH_NOT_REQUIRED_REASONS = {"already_started", "completion_in_progress", "terminal"}


class ManagedTurnReplayConflictError(ValueError):
    """A replay conflicts with the durably accepted turn identity or state."""


def _sha256(value: str) -> str:
    return f"sha256:{hashlib.sha256(value.encode('utf-8')).hexdigest()}"


_EMPTY_TEXT_DIGEST = _sha256("")
_NULL_JSON_DIGEST = _sha256("null")


def _text_digest(value: object) -> str:
    if not isinstance(value, str):
        return "invalid"
    return _EMPTY_TEXT_DIGEST if not value else _sha256(value)


def _json_digest(value: object) -> str:
    if value is None:
        return _NULL_JSON_DIGEST
    payload: object = value
    if isinstance(value, Mapping) and not isinstance(value, dict):
        payload = {str(key): item for key, item in value.items()}
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        if not isinstance(value, list) or any(
            isinstance(item, Mapping) and not isinstance(item, dict) for item in value
        ):
            payload = [
                {str(k): v for k, v in item.items()}
                if isinstance(item, Mapping) and not isinstance(item, dict)
                else item
                for item in value
            ]
    try:
        encoded = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True
        )
    except (TypeError, ValueError):
        return "invalid"
    return _sha256(encoded)


def _as_object_dict(value: object, *, label: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} result must be an object")
    return {str(key): item for key, item in value.items()}


def _session_projection(session: Mapping[str, object] | None) -> dict[str, object] | None:
    if session is None:
        return None
    return {"status": session.get("status"), "active_turn_id": session.get("active_turn_id")}


def _turn_status_projection(turn: Mapping[str, object] | None) -> dict[str, object] | None:
    if turn is None:
        return None
    return {"turn_id": turn.get("turn_id"), "status": turn.get("status")}


def _queued_turn_base(turn: Mapping[str, object]) -> dict[str, object]:
    return {
        "turn_id": turn["turn_id"],
        "status": turn["status"],
        "origin": turn.get("origin"),
        "loopx_execution": turn.get("loopx_execution", False),
        "has_collaboration_request": turn.get("collaboration_request") is not None,
        "has_external_agent_target": turn.get("external_agent_target") is not None,
        "reservation": turn.get("_queue_steering"),
        "mutation": turn.get("_queue_mutation"),
    }


def _single_match(
    rows: Sequence[Mapping[str, object]],
    predicate: Callable[[Mapping[str, object]], bool],
) -> tuple[int, Mapping[str, object] | None]:
    count = 0
    first: Mapping[str, object] | None = None
    for row in rows:
        if predicate(row):
            count += 1
            if count == 1:
                first = row
    return count, first


def _acceptance_facts(turn: Mapping[str, object]) -> dict[str, object] | None:
    acceptance = turn.get("_acceptance")
    if acceptance is None:
        return None
    if not isinstance(acceptance, Mapping):
        return {"schema_version": None}
    return {
        "schema_version": acceptance.get("schema_version"),
        "phase": acceptance.get("phase"),
        "request_sha256": acceptance.get("request_sha256"),
        "message_id": acceptance.get("message_id"),
        "display_message_sha256": _text_digest(acceptance.get("display_message")),
        "attachments_sha256": _json_digest(acceptance.get("attachments") or None),
    }


def _turn_facts(turn: Mapping[str, object] | None) -> dict[str, object] | None:
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


def _transcript_facts(messages: Sequence[Mapping[str, object]], turn_id: str | None) -> dict[str, object]:
    if turn_id is None:
        return {"count": 0}
    count, msg = _single_match(messages, lambda row: row.get("role") == "user" and row.get("turn_id") == turn_id)
    if count != 1 or msg is None:
        return {"count": count}
    return {
        "count": 1,
        "message_id": msg.get("message_id"),
        "display_message_sha256": _text_digest(msg.get("text")),
        "attachments_sha256": _json_digest(msg.get("attachments") or None),
        "origin": msg.get("origin"),
    }


def _queued_event_facts(events: Sequence[Mapping[str, object]]) -> dict[str, object]:
    count, event = _single_match(events, lambda row: row.get("kind") == "turn.queued")
    return {"count": count} if count != 1 or event is None else {"count": 1, "payload_sha256": _json_digest(event.get("payload"))}


def _prepared_turn_facts(turns: Sequence[Mapping[str, object]]) -> dict[str, object]:
    count, turn = _single_match(turns, lambda row: "_acceptance" in row and row.get("status") not in TERMINAL_TURN_STATUSES)
    if count != 1 or turn is None:
        return {"count": count}
    return {"count": 1, "turn_id": turn.get("turn_id"), "client_turn_id": turn.get("client_turn_id")}


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
    turn: dict[str, object]
    created: bool
    dispatch_required: bool
    dispatch_reason: str | None


class QueuedChatMutationEvent(TypedDict):
    """Event emitted by TypeScript queue mutation planning."""

    kind: str
    payload: dict[str, object]


class QueuedChatMutationPlan(TypedDict, total=False):
    """Typed plan returned by `chat.queue.mutate` for storage application."""

    action: str
    error_code: str
    receipt: dict[str, object]
    message_patch: dict[str, object]
    turn_patch: dict[str, object]
    event: QueuedChatMutationEvent


def _required_string(payload: Mapping[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ValueError(f"chat turn acceptance result has invalid {field}")
    return value


def _required_bool(payload: object, field: str) -> bool:
    if not isinstance(payload, Mapping):
        raise ValueError("chat turn acceptance result must be an object")
    value = payload.get(field)
    if not isinstance(value, bool):
        raise ValueError(f"chat turn acceptance result has invalid {field}")
    return value


def queued_chat_turn_ids(
    turns: Sequence[Mapping[str, object]], active_turn_id: str | None, *,
    include_reserved: bool = False, capacity_at: str | None = None,
) -> list[str]:
    """Project storage observations to the typed queue/Turn owner."""
    raw_result: object = effect_runtime_result("chat.queue.select", {
        "active_turn_id": active_turn_id, "include_reserved": include_reserved, "capacity_at": capacity_at,
        "turns": [{"turn_id": t["turn_id"], "status": t["status"], "expires_at": t.get("expires_at"),
                   "reservation": t.get("_queue_steering"), "mutation": t.get("_queue_mutation")} for t in turns],
    })
    if not isinstance(raw_result, Mapping) or not isinstance(raw_ids := raw_result.get("turn_ids"), list):
        raise ValueError("chat queue selection result has invalid turn_ids")
    turn_ids: list[str] = []
    for item in raw_ids:
        if not isinstance(item, str):
            raise ValueError("chat queue selection result has invalid turn_ids")
        turn_ids.append(item)
    return turn_ids


def plan_native_mention_ingress(**facts: object) -> dict[str, object]:
    """Ask the shared queue/steering owner to select one exact ingress."""
    return _as_object_dict(effect_runtime_result("chat.queue.mention", facts), label="chat mention ingress")


def _steering_attachment_count(attachments: object) -> int:
    """Count queued attachments for text-only steering, failing closed on unexpected shapes."""
    if attachments is None:
        return 0
    if isinstance(attachments, Sequence) and not isinstance(attachments, (str, bytes, bytearray, memoryview)):
        return len(attachments)
    if isinstance(attachments, Sized) and len(attachments) > 0:
        return len(attachments)
    raise ValueError("queued turn attachments must be null or a sequence")


def plan_queued_chat_steering(
    session: Mapping[str, object], turn: Mapping[str, object], active: Mapping[str, object] | None,
    receipt: Mapping[str, object] | None, *, ingress_id: str, operation_id: str, target: str, phase: str,
    web_origin_only: bool = False,
) -> dict[str, object]:
    session_payload = _session_projection(session) or {}
    session_payload["web_origin_only"] = web_origin_only
    turn_payload = _queued_turn_base(turn)
    turn_payload["attachment_count"] = _steering_attachment_count(turn.get("attachments"))
    raw_result: object = effect_runtime_result("chat.queue.steer", {
        "phase": phase,
        "request": {"client_ingress_id": ingress_id, "operation_id": operation_id, "target_turn_id": target},
        "session": session_payload,
        "active_status": active.get("status") if active is not None else None,
        "turn": turn_payload,
        "receipt": ({k: receipt.get(k) for k in _STEERING_RECEIPT_FIELDS} if receipt is not None else None),
        "receipt_message_matches": receipt is not None and receipt.get("message") == turn.get("message"),
    })
    return _as_object_dict(raw_result, label="chat queued steering")


def _decode_mutation_event(value: object) -> QueuedChatMutationEvent:
    raw_event = _as_object_dict(value, label="chat queued mutation event")
    kind = raw_event.get("kind")
    if not isinstance(kind, str) or not kind:
        raise ValueError("chat queued mutation event kind must be a string")
    return {"kind": kind, "payload": _as_object_dict(raw_event.get("payload"), label="chat queued mutation event payload")}


def plan_queued_chat_mutation(
    session: Mapping[str, object], turn: Mapping[str, object], request: Mapping[str, object]
) -> QueuedChatMutationPlan:
    turn_payload = _queued_turn_base(turn)
    turn_payload.update({"has_acceptance": "_acceptance" in turn, "input_revision": turn.get("input_revision", 0), "operations": turn.get("_queue_operations", [])})
    decoded = _as_object_dict(
        effect_runtime_result("chat.queue.mutate", {
            "session": _session_projection(session),
            "turn": turn_payload,
            "request": request if isinstance(request, dict) else _as_object_dict(request, label="request"),
        }),
        label="chat queued mutation",
    )
    plan: QueuedChatMutationPlan = {}
    if isinstance(action := decoded.get("action"), str):
        plan["action"] = action
    if isinstance(error_code := decoded.get("error_code"), str):
        plan["error_code"] = error_code
    if decoded.get("receipt") is not None:
        plan["receipt"] = _as_object_dict(decoded.get("receipt"), label="chat queued mutation receipt")
    if decoded.get("message_patch") is not None:
        plan["message_patch"] = _as_object_dict(decoded.get("message_patch"), label="chat queued mutation message_patch")
    if decoded.get("turn_patch") is not None:
        plan["turn_patch"] = _as_object_dict(decoded.get("turn_patch"), label="chat queued mutation turn_patch")
    if decoded.get("event") is not None:
        plan["event"] = _decode_mutation_event(decoded.get("event"))
    return plan


def managed_turn_execution_allowed(session: Mapping[str, object] | None, turn: Mapping[str, object] | None) -> bool:
    """Ask the existing typed Turn owner about the current durable claim."""
    return _required_bool(effect_runtime_result("chat.turn.execution_allowed", {"session": _session_projection(session), "turn": _turn_status_projection(turn)}), "allowed")


def managed_turn_recovery_allowed(
    session: Mapping[str, object] | None, turn: Mapping[str, object] | None, *, managed: bool, owner_dead: bool
) -> bool:
    """Project process observations to the same typed Turn lifecycle owner."""
    return _required_bool(
        effect_runtime_result("chat.turn.recovery_allowed", {"session": _session_projection(session), "turn": _turn_status_projection(turn), "managed": managed, "owner_dead": owner_dead}),
        "allowed",
    )


def _raise_rejection(result: Mapping[str, object]) -> None:
    code = result.get("code")
    if code in {"session_not_found", "session_closed"}:
        raise KeyError("chat session was not found")
    if code == "request_conflict":
        raise ManagedTurnReplayConflictError("client_turn_id already belongs to a different request")
    if code == "active_turn_conflict":
        raise RuntimeError(str(result.get("active_turn_id") or "chat session already has an active turn"))
    if code == "original_request_unavailable":
        raise ValueError("client_turn_id original request is unavailable")
    if code == "durable_state_conflict":
        raise ManagedTurnReplayConflictError("chat turn acceptance state is inconsistent")
    raise ValueError("chat turn acceptance returned an unsupported rejection")


def _decode_plan(value: object) -> ManagedTurnAcceptancePlan:
    if not isinstance(value, Mapping):
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
    writes, dispatch = value.get("writes"), value.get("dispatch")
    if not isinstance(writes, Mapping) or not isinstance(dispatch, Mapping):
        raise ValueError("chat turn acceptance result is incomplete")
    dispatch_kind, dispatch_reason = dispatch.get("kind"), dispatch.get("reason")
    if dispatch_kind not in {"required", "not_required"}:
        raise ValueError("chat turn acceptance dispatch kind is unsupported")
    if (dispatch_kind == "not_required" and dispatch_reason not in _DISPATCH_NOT_REQUIRED_REASONS) or (
        dispatch_kind == "required" and dispatch_reason is not None
    ):
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
            append_queued_event=_required_bool(writes, "append_queued_event"),
            settle_turn=_required_bool(writes, "settle_turn"),
        ),
        dispatch_required=dispatch_kind == "required",
        dispatch_reason=str(dispatch_reason) if dispatch_kind == "not_required" else None,
    )


def plan_managed_turn_acceptance(
    *, session_id: str, client_turn_id: str, message: str, display_message: str,
    attachments: Sequence[Mapping[str, object]] | None, origin: str, loopx_execution: bool,
    loopx_request: Mapping[str, object] | None, candidate_turn_id: str, candidate_message_id: str,
    accepted_at: str, session: Mapping[str, object] | None, active_turn: Mapping[str, object] | None,
    matching_turn: Mapping[str, object] | None, turns: Sequence[Mapping[str, object]],
    messages: Sequence[Mapping[str, object]], queued_events: Sequence[Mapping[str, object]],
) -> ManagedTurnAcceptancePlan:
    request = {
        "schema_version": CHAT_TURN_ACCEPTANCE_REQUEST_SCHEMA,
        "request": {
            "session_id": session_id, "client_turn_id": client_turn_id,
            "execution_message_sha256": _text_digest(message), "display_message_sha256": _text_digest(display_message),
            "attachments_sha256": _json_digest(attachments or None), "origin": origin,
            "loopx_execution": loopx_execution, "loopx_request_sha256": _json_digest(loopx_request),
        },
        "candidate": {"turn_id": candidate_turn_id, "message_id": candidate_message_id, "accepted_at": accepted_at},
        "session": _session_projection(session),
        "active_turn": _turn_status_projection(active_turn),
        "matching_turn": _turn_facts(matching_turn),
        "transcript": _transcript_facts(messages, str(matching_turn.get("turn_id")) if matching_turn is not None else None),
        "queued_event": _queued_event_facts(queued_events),
        "prepared_turn": _prepared_turn_facts(turns),
    }
    return _decode_plan(effect_runtime_result("chat.turn.accept", request))
