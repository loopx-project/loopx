"""Idempotent LoopX turn dispatch for a configured botmux Goal Channel binding."""

from __future__ import annotations

from pathlib import Path

from .botmux_binding_store import (
    AtomicJsonWriter,
    BotmuxApiError,
    BotmuxDispatchState,
    BotmuxTransportError,
    JsonRequester,
    NowIsoFn,
    Payload,
    PayloadView,
    _as_mapping,
    _as_mutable_dict,
    _dispatch_state,
    _now_iso,
    _operation_packet,
    _persist_dispatch_receipt,
    botmux_binding_for_goal,
    read_botmux_runtime_binding,
)
from .botmux_lifecycle import (
    _dispatch_key,
    _provider_turn_key,
    _receipt_id,
    build_botmux_loopx_instruction,
    goal_runtime_revision,
)
from .botmux_transport import (
    _dashboard_auth_for_binding,
    _validated_endpoint,
    _validated_private_id,
)

_ALREADY_DISPATCHED_STATES = frozenset(
    {
        BotmuxDispatchState.ATTEMPTING,
        BotmuxDispatchState.QUEUED,
        BotmuxDispatchState.RUNNING,
        BotmuxDispatchState.COMPLETED,
        BotmuxDispatchState.FAILED,
        BotmuxDispatchState.NOT_FOUND,
        BotmuxDispatchState.UNKNOWN,
    }
)


def _submit_trigger_attempt(
    *,
    binding_path: Path,
    goal_id: str,
    binding: Payload,
    dispatch_key: str,
    endpoint: str,
    dashboard_auth: str,
    target: PayloadView,
    options: PayloadView,
    instruction: str,
    existing_session_id: str,
    requester: JsonRequester,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn,
) -> Payload:
    _persist_dispatch_receipt(
        binding_path=binding_path,
        goal_id=goal_id,
        binding=binding,
        dispatch_key=dispatch_key,
        state=BotmuxDispatchState.ATTEMPTING,
        timestamp_field="attempted_at",
        updates={"session_reused": bool(existing_session_id)},
        session={
            **({"session_id": existing_session_id} if existing_session_id else {}),
            "active_dispatch_key": dispatch_key,
            "updated_at": now_iso(),
        },
        writer=writer,
        now_iso=now_iso,
    )
    try:
        response = requester(
            endpoint=endpoint,
            path="/api/trigger",
            method="POST",
            dashboard_auth=dashboard_auth,
            payload={
                "source": {"type": "webhook"},
                "target": dict(target),
                "instruction": instruction,
                "envelope": {
                    "format": "json",
                    "sourceName": "loopx",
                    "trusted": False,
                },
                "options": dict(options),
            },
        )
    except BotmuxTransportError:
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=BotmuxDispatchState.UNKNOWN,
            timestamp_field="unknown_at",
            updates={"unknown_reason": "transport_failure"},
            writer=writer,
            now_iso=now_iso,
        )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=True,
            status="dispatch_unknown",
            blocker="botmux_dispatch_outcome_unknown",
            public_summary=(
                "botmux dispatch outcome is unknown; inspect the existing receipt "
                "before retrying"
            ),
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
        )
    except BotmuxApiError as exc:
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=BotmuxDispatchState.REJECTED,
            timestamp_field="rejected_at",
            updates={"http_status": exc.status},
            writer=writer,
            now_iso=now_iso,
        )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=True,
            status="rejected",
            blocker="botmux_api_rejected",
            public_summary="botmux rejected the LoopX runtime turn",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
        )
    response_target = _as_mapping(response.get("target")) or {}
    async_payload = _as_mapping(response.get("async")) or {}
    session_id = str(
        response_target.get("sessionId") or async_payload.get("sessionId") or ""
    ).strip()
    if response.get("ok") is not True or not session_id:
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=BotmuxDispatchState.UNKNOWN,
            timestamp_field="unknown_at",
            updates={"unknown_reason": "invalid_provider_receipt"},
            writer=writer,
            now_iso=now_iso,
        )
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=True,
            status="dispatch_unknown",
            blocker="botmux_dispatch_receipt_invalid",
            public_summary="botmux did not return a verifiable runtime session receipt",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
        )
    _persist_dispatch_receipt(
        binding_path=binding_path,
        goal_id=goal_id,
        binding=binding,
        dispatch_key=dispatch_key,
        state=BotmuxDispatchState.QUEUED,
        timestamp_field="queued_at",
        updates={
            "session_id": session_id,
            "trigger_id": str(response.get("triggerId") or ""),
        },
        session={
            "session_id": session_id,
            "active_dispatch_key": dispatch_key,
            "updated_at": now_iso(),
        },
        writer=writer,
        now_iso=now_iso,
    )
    return _operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="runtime_trigger",
        execute=True,
        status="queued",
        public_summary="queued one LoopX turn through the botmux runtime",
        external_write_performed=True,
        idempotency_key=dispatch_key,
        receipt_id=_receipt_id(dispatch_key),
        details={"dispatch_state": "queued", "session_reused": bool(existing_session_id)},
    )


def _trigger_botmux_runtime(
    *,
    registry: PayloadView,
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    instruction: str | None = None,
    turn_key: str | None = None,
    execute: bool = False,
    requester: JsonRequester,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn = _now_iso,
) -> Payload:
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, goal_id)
    if binding is None or binding.get("enabled") is not True:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=execute,
            status="blocked",
            blocker="botmux_binding_missing",
            public_summary="configure a botmux runtime binding for this goal",
        )
    selected_instruction = str(
        instruction or build_botmux_loopx_instruction(goal_id)
    ).strip()
    if not selected_instruction:
        raise ValueError("botmux runtime instruction must not be empty")
    revision = str(
        turn_key
        or goal_runtime_revision(
            registry=registry,
            registry_path=registry_path,
            goal_id=goal_id,
        )
    )
    dispatch_key = _dispatch_key(
        goal_id=goal_id,
        revision=revision,
        instruction=selected_instruction,
    )
    receipts = _as_mutable_dict(binding.get("receipts"))
    prior = _as_mapping(receipts.get(dispatch_key))
    prior_state = _dispatch_state(prior.get("state") if prior is not None else None)
    if prior_state in _ALREADY_DISPATCHED_STATES:
        return _operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=execute,
            status="already_dispatched",
            public_summary="the same botmux runtime turn was already dispatched",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
            details={
                "dispatch_state": prior_state.value,
                "session_reused": True,
            },
        )
    session = _as_mutable_dict(binding.get("session"))
    existing_session_id = str(session.get("session_id") or "").strip()
    if not execute:
        return _operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="runtime_trigger",
            execute=False,
            status="preview_ready",
            public_summary="the next LoopX turn is ready for botmux dispatch",
            idempotency_key=dispatch_key,
            receipt_id=_receipt_id(dispatch_key),
            details={
                "dispatch_state": "preview",
                "session_reused": bool(existing_session_id),
            },
        )
    endpoint = _validated_endpoint(str(binding.get("endpoint") or ""))
    bot_id = _validated_private_id(str(binding.get("bot_id") or ""), "bot id")
    dashboard_auth = _dashboard_auth_for_binding(binding)
    target: Payload = {"kind": "turn", "botId": bot_id}
    options: Payload = {"asyncReturnSessionId": True}
    if existing_session_id:
        target["sessionId"] = _validated_private_id(existing_session_id, "session id")
        options["turnIdempotencyKey"] = _provider_turn_key(dispatch_key)
    else:
        target["chatId"] = _validated_private_id(
            str(binding.get("chat_id") or ""),
            "chat id",
        )
    return _submit_trigger_attempt(
        binding_path=binding_path,
        goal_id=goal_id,
        binding=binding,
        dispatch_key=dispatch_key,
        endpoint=endpoint,
        dashboard_auth=dashboard_auth,
        target=target,
        options=options,
        instruction=selected_instruction,
        existing_session_id=existing_session_id,
        requester=requester,
        writer=writer,
        now_iso=now_iso,
    )
