"""Receipt-bound status polling and terminal readback preservation for botmux."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from .botmux_binding_store import (
    _TERMINAL_DISPATCH_STATES,
    AtomicJsonWriter,
    BotmuxApiError,
    BotmuxDispatchState,
    BotmuxTransportError,
    JsonRequester,
    NowIsoFn,
    Payload,
    PayloadView,
    _active_dispatch_receipt,
    _as_mapping,
    _coerce_binding_section,
    _dispatch_state,
    _now_iso,
    _operation_packet,
    _persist_dispatch_receipt,
    botmux_binding_for_goal,
    read_botmux_runtime_binding,
)
from .botmux_lifecycle import _receipt_id
from .botmux_transport import (
    _dashboard_auth_for_binding,
    _validated_endpoint,
    _validated_private_id,
)

_PROVIDER_OBSERVABLE_STATES = frozenset(
    {
        BotmuxDispatchState.RUNNING,
        BotmuxDispatchState.COMPLETED,
        BotmuxDispatchState.FAILED,
        BotmuxDispatchState.NOT_FOUND,
    }
)


def _resolve_unaccepted_dispatch_status(
    *,
    binding_path: Path,
    goal_id: str,
    binding: Payload,
    execute: bool,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn,
) -> Payload:
    active = _active_dispatch_receipt(binding)
    if active is not None:
        active_key, receipt = active
        initial_state = _dispatch_state(receipt.get("state"))
        receipt_state = initial_state
        if receipt_state == BotmuxDispatchState.ATTEMPTING:
            receipt_state = BotmuxDispatchState.UNKNOWN
            if execute:
                _persist_dispatch_receipt(
                    binding_path=binding_path,
                    goal_id=goal_id,
                    binding=binding,
                    dispatch_key=active_key,
                    state=BotmuxDispatchState.UNKNOWN,
                    timestamp_field="unknown_at",
                    updates={"unknown_reason": "unresolved_attempt"},
                    writer=writer,
                    now_iso=now_iso,
                )
        if receipt_state in {
            BotmuxDispatchState.UNKNOWN,
            BotmuxDispatchState.REJECTED,
        }:
            public_state = receipt_state.value
            return _operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="runtime_status",
                execute=execute,
                status=public_state,
                blocker=(
                    "botmux_dispatch_outcome_unknown"
                    if receipt_state == BotmuxDispatchState.UNKNOWN
                    else "botmux_api_rejected"
                ),
                public_summary=(
                    "the botmux runtime dispatch outcome is unknown"
                    if receipt_state == BotmuxDispatchState.UNKNOWN
                    else "the botmux runtime dispatch was rejected"
                ),
                idempotency_key=active_key,
                receipt_id=_receipt_id(active_key),
                details={
                    "dispatch_state": public_state,
                    "output_available": False,
                    "local_receipt_updated": bool(
                        execute and initial_state == BotmuxDispatchState.ATTEMPTING
                    ),
                },
            )
    return _operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="runtime_status",
        execute=execute,
        status="idle",
        public_summary="no botmux runtime turn has been dispatched for this goal",
        details={"dispatch_state": "idle", "output_available": False},
    )


def _record_status_transport_failure(
    *,
    binding_path: Path,
    goal_id: str,
    binding: Payload,
    dispatch_key: str,
    execute: bool,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn,
) -> Payload:
    if execute:
        receipts = _coerce_binding_section(binding.get("receipts"), field="receipts")
        receipt = _as_mapping(receipts.get(dispatch_key))
        current_state = _dispatch_state(
            receipt.get("state") if receipt is not None else None
        )
        if current_state in {
            BotmuxDispatchState.QUEUED,
            BotmuxDispatchState.RUNNING,
            BotmuxDispatchState.UNKNOWN,
        }:
            _persist_dispatch_receipt(
                binding_path=binding_path,
                goal_id=goal_id,
                binding=binding,
                dispatch_key=dispatch_key,
                state=BotmuxDispatchState.UNKNOWN,
                timestamp_field="unknown_at",
                updates={"unknown_reason": "status_transport_failure"},
                writer=writer,
                now_iso=now_iso,
            )
    return _operation_packet(
        ok=False,
        goal_id=goal_id,
        operation="runtime_status",
        execute=execute,
        status="unknown",
        blocker="botmux_unreachable",
        public_summary="the botmux runtime status is temporarily unknown",
    )


def _query_pinned_trigger_status(
    *,
    binding_path: Path,
    goal_id: str,
    binding: Payload,
    session_id: str,
    dispatch_key: str,
    pinned_trigger_id: str | None,
    execute: bool,
    requester: JsonRequester,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn,
) -> tuple[PayloadView | None, bool, Payload | None]:
    if pinned_trigger_id is None:
        return {"state": BotmuxDispatchState.UNKNOWN.value}, False, None
    try:
        result = requester(
            endpoint=_validated_endpoint(str(binding.get("endpoint") or "")),
            path=(
                "/api/sessions/"
                f"{quote(_validated_private_id(session_id, 'session id'), safe='')}"
                f"/trigger-result?triggerId={quote(pinned_trigger_id, safe='')}"
            ),
            dashboard_auth=_dashboard_auth_for_binding(binding),
        )
        return result, False, None
    except ValueError:
        return (
            None,
            False,
            _operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="runtime_status",
                execute=execute,
                status="blocked",
                blocker="botmux_auth_unavailable",
                public_summary="the botmux runtime status cannot be read without auth",
            ),
        )
    except BotmuxApiError as exc:
        if exc.status == 404 and (
            exc.payload.get("error") == "unknown_session"
            or exc.payload.get("state") == "not_found"
        ):
            not_found_result: Payload = {"state": "not_found"}
            echoed_trigger_id = str(exc.payload.get("triggerId") or "").strip()
            if echoed_trigger_id:
                not_found_result["triggerId"] = echoed_trigger_id
            # Only an explicit "this session is gone" that names no turn cannot
            # be attributed to another one.
            session_level_missing = (
                exc.payload.get("error") == "unknown_session"
                and not echoed_trigger_id
            )
            return not_found_result, session_level_missing, None
        return (
            None,
            False,
            _operation_packet(
                ok=False,
                goal_id=goal_id,
                operation="runtime_status",
                execute=execute,
                status="blocked",
                blocker="botmux_api_rejected",
                public_summary="botmux rejected the runtime status read",
            ),
        )
    except BotmuxTransportError:
        return (
            None,
            False,
            _record_status_transport_failure(
                binding_path=binding_path,
                goal_id=goal_id,
                binding=binding,
                dispatch_key=dispatch_key,
                execute=execute,
                writer=writer,
                now_iso=now_iso,
            ),
        )


def _resolve_observed_state(
    *,
    local_state: BotmuxDispatchState | None,
    provider_state: BotmuxDispatchState,
) -> tuple[BotmuxDispatchState, bool]:
    if (
        local_state is not None
        and local_state in _TERMINAL_DISPATCH_STATES
        and provider_state != local_state
    ):
        return local_state, True
    return provider_state, False


def _status_botmux_runtime(
    *,
    goal_id: str,
    binding_path: Path,
    execute: bool = False,
    requester: JsonRequester,
    writer: AtomicJsonWriter,
    now_iso: NowIsoFn = _now_iso,
) -> Payload:
    payload = read_botmux_runtime_binding(binding_path)
    binding = botmux_binding_for_goal(payload, goal_id)
    if binding is None:
        return _operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="runtime_status",
            execute=execute,
            status="blocked",
            blocker="botmux_binding_missing",
            public_summary="configure a botmux runtime binding for this goal",
        )
    session = _coerce_binding_section(binding.get("session"), field="session")
    session_id = str(session.get("session_id") or "").strip()
    dispatch_key = str(session.get("active_dispatch_key") or "").strip()
    local_receipts = _coerce_binding_section(binding.get("receipts"), field="receipts")
    local_receipt = _as_mapping(local_receipts.get(dispatch_key)) if dispatch_key else None
    local_state = _dispatch_state(
        local_receipt.get("state") if local_receipt is not None else None
    )
    unaccepted_dispatch = bool(dispatch_key) and (
        local_state in {BotmuxDispatchState.ATTEMPTING, BotmuxDispatchState.REJECTED}
        or (
            local_state == BotmuxDispatchState.UNKNOWN
            and local_receipt is not None
            and not local_receipt.get("queued_at")
        )
    )
    if not session_id or not dispatch_key or unaccepted_dispatch:
        return _resolve_unaccepted_dispatch_status(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            execute=execute,
            writer=writer,
            now_iso=now_iso,
        )
    # Ask for the trigger id this receipt was queued under, and promote nothing
    # the provider does not attribute to it.
    stored_trigger_id = (
        str(local_receipt.get("trigger_id") or "").strip()
        if local_receipt is not None
        else ""
    )
    try:
        pinned_trigger_id: str | None = _validated_private_id(
            stored_trigger_id, "trigger id"
        )
    except ValueError:
        pinned_trigger_id = None
    result, session_level_missing, error_packet = _query_pinned_trigger_status(
        binding_path=binding_path,
        goal_id=goal_id,
        binding=binding,
        session_id=session_id,
        dispatch_key=dispatch_key,
        pinned_trigger_id=pinned_trigger_id,
        execute=execute,
        requester=requester,
        writer=writer,
        now_iso=now_iso,
    )
    if error_packet is not None or result is None:
        assert error_packet is not None
        return error_packet
    raw_provider_state = _dispatch_state(result.get("state"))
    provider_state = (
        raw_provider_state
        if raw_provider_state in _PROVIDER_OBSERVABLE_STATES
        else BotmuxDispatchState.UNKNOWN
    )
    identity_unverified = pinned_trigger_id is None
    if (
        pinned_trigger_id is not None
        and not session_level_missing
        and str(result.get("triggerId") or "").strip() != pinned_trigger_id
    ):
        provider_state = BotmuxDispatchState.UNKNOWN
        identity_unverified = True
    latest_payload = read_botmux_runtime_binding(binding_path)
    latest_binding = botmux_binding_for_goal(latest_payload, goal_id) or binding
    latest_receipts = _coerce_binding_section(
        latest_binding.get("receipts"), field="receipts"
    )
    latest_receipt = _as_mapping(latest_receipts.get(dispatch_key))
    latest_local_state = _dispatch_state(
        latest_receipt.get("state") if latest_receipt is not None else None
    )
    state, provider_observation_ignored = _resolve_observed_state(
        local_state=latest_local_state,
        provider_state=provider_state,
    )
    output = _as_mapping(result.get("output"))
    output_available = not identity_unverified and bool(
        output is not None and str(output.get("content") or "").strip()
    )
    local_receipt_updated = execute
    if execute:
        updates: Payload | None = None
        if identity_unverified and state == BotmuxDispatchState.UNKNOWN:
            updates = {"unknown_reason": "trigger_identity_unverified"}
        if provider_observation_ignored:
            updates = {
                "provider_observation": provider_state.value,
                "provider_observation_ignored": True,
            }
        _persist_dispatch_receipt(
            binding_path=binding_path,
            goal_id=goal_id,
            binding=binding,
            dispatch_key=dispatch_key,
            state=state,
            timestamp_field="status_checked_at",
            updates=updates,
            writer=writer,
            now_iso=now_iso,
        )
    details: Payload = {
        "dispatch_state": state.value,
        "output_available": output_available,
        "local_receipt_updated": local_receipt_updated,
    }
    if provider_observation_ignored:
        details.update(
            {
                "provider_observation": provider_state.value,
                "provider_observation_ignored": True,
            }
        )
    return _operation_packet(
        ok=state != BotmuxDispatchState.UNKNOWN,
        goal_id=goal_id,
        operation="runtime_status",
        execute=execute,
        status=state.value,
        blocker=(
            "botmux_status_unknown" if state == BotmuxDispatchState.UNKNOWN else None
        ),
        public_summary=f"the botmux runtime turn is {state.value}",
        readback_verified=state == BotmuxDispatchState.COMPLETED,
        idempotency_key=dispatch_key,
        receipt_id=_receipt_id(dispatch_key),
        details=details,
    )
