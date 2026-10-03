from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from .goal_channel_contracts import (
    binding_for_goal,
    blocked_notice_auto_notify_enabled,
    blocked_notice_auto_notify_marker_path,
    clear_blocked_notice_auto_notify_marker,
    write_blocked_notice_auto_notify_marker,
    clear_human_gate_auto_notify_marker,
    _gate_notice_projection,
    goal_from_registry,
    goal_objective,
    human_gate_auto_notify_enabled,
    human_gate_auto_notify_marker_path,
    now_iso,
    operation_packet,
    provider_idempotency_key,
    quota_human_gate_identity,
    quota_human_gate_notification_suppressed,
    quota_human_gate_reminder_generation,
    quota_human_gate_state_generation,
    quota_selects_human_gate,
    read_goal_channel_binding,
    save_goal_binding,
    serialize_goal_binding_mutation,
    semantic_key,
    write_human_gate_auto_notify_marker,
)
from ...capabilities.manager_context.goal_notice import NoticeSynthesizer
from ...control_plane.runtime.public_safety import public_safe_compact_text
from ...control_plane.quota.blocked_transition_notice import collect_blocked_transition_notices
from .goal_channel_notice import render_channel_notice
from .identity_shapes import (
    LARK_CHAT_ID_SEARCH as CHAT_ID_PATTERN,
    LARK_MESSAGE_ID_SEARCH as MESSAGE_ID_PATTERN,
)
from .goal_channel_transport import (
    APP_ID_PATTERN,
    auth_verified,
    call,
    chat_verified,
    find_first_string,
    json_payload,
    lark_args,
    message_readback_verified,
    payload_contains_exact,
    pin_readback_verified,
    verified_app_id,
)
from .presentation.kanban import (
    DEFAULT_CLI_BIN,
    CommandRunner,
    LarkKanbanConfig,
    build_record_list_command,
    default_lark_kanban_config_path,
    default_subprocess_runner,
    lark_kanban_config_from_payload,
    lark_kanban_doctor,
    read_lark_kanban_local_config,
    sync_loopx_todos_to_lark_kanban,
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _resolve_kanban_config_path(
    *,
    registry_path: Path,
    configured_path: Any,
) -> Path:
    raw_path = str(configured_path or "").strip()
    if not raw_path:
        return default_lark_kanban_config_path(registry_path).expanduser().resolve()
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    source_registry = registry_path.expanduser().resolve()
    project_root = (
        source_registry.parent.parent
        if source_registry.parent.name == ".loopx"
        else source_registry.parent
    )
    return project_root / path


@serialize_goal_binding_mutation
def configure_lark_goal_channel_automation(
    *,
    registry: Mapping[str, Any],
    goal_id: str,
    binding_path: Path,
    human_gate_auto_notify: bool | None = None,
    blocked_notice_auto_notify: bool | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    goal_from_registry(registry, goal_id)
    payload = read_goal_channel_binding(binding_path)
    binding = binding_for_goal(payload, goal_id)
    if binding is None:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="configure",
            execute=execute,
            status="blocked",
            blocker="channel_binding_missing",
            public_summary="configure the Goal Channel before enabling automation",
        )
    if human_gate_auto_notify is None and blocked_notice_auto_notify is None:
        raise ValueError("choose an automation setting")
    if (
        human_gate_auto_notify is True or blocked_notice_auto_notify is True
    ) and binding.get("enabled") is not True:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="configure",
            execute=execute,
            status="blocked",
            blocker="channel_binding_incomplete",
            public_summary="complete Goal Channel setup before enabling automation",
        )
    current_human = human_gate_auto_notify_enabled(binding)
    current_blocked = blocked_notice_auto_notify_enabled(binding)
    next_human = (
        current_human if human_gate_auto_notify is None else human_gate_auto_notify
    )
    next_blocked = (
        current_blocked
        if blocked_notice_auto_notify is None
        else blocked_notice_auto_notify
    )
    changed = current_human != next_human or current_blocked != next_blocked
    if execute and changed:
        mutable_binding = dict(binding)
        automation = _mapping(binding.get("automation"))
        if human_gate_auto_notify is not None:
            automation["human_gate_auto_notify_enabled"] = next_human
        if blocked_notice_auto_notify is not None:
            automation["blocked_notice_auto_notify_enabled"] = next_blocked
        mutable_binding["automation"] = automation
        save_goal_binding(
            binding_path=binding_path,
            payload=payload,
            goal_id=goal_id,
            binding=mutable_binding,
        )
    if execute and human_gate_auto_notify is not None:
        human_marker = human_gate_auto_notify_marker_path(binding_path, goal_id)
        if next_human:
            write_human_gate_auto_notify_marker(human_marker)
        else:
            clear_human_gate_auto_notify_marker(human_marker)
    if execute and blocked_notice_auto_notify is not None:
        blocked_marker = blocked_notice_auto_notify_marker_path(binding_path, goal_id)
        if next_blocked:
            write_blocked_notice_auto_notify_marker(blocked_marker)
        else:
            clear_blocked_notice_auto_notify_marker(blocked_marker)
    saved = (
        binding_for_goal(read_goal_channel_binding(binding_path), goal_id)
        if execute
        else None
    )
    return operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="configure",
        execute=execute,
        status="configured" if execute else "preview_ready",
        public_summary=(
            "updated Goal Channel automatic notification settings"
            if execute
            else "previewed the Goal Channel automation change"
        ),
        readback_verified=bool(
            execute
            and (
                human_gate_auto_notify is None
                or human_gate_auto_notify_enabled(saved) == next_human
            )
            and (
                blocked_notice_auto_notify is None
                or blocked_notice_auto_notify_enabled(saved) == next_blocked
            )
        ),
        details={
            "changed": changed,
            "human_gate_auto_notify_enabled": next_human,
            "blocked_notice_auto_notify_enabled": next_blocked,
        },
    )


def auto_notify_lark_goal_channel_gate(
    *,
    registry: Mapping[str, Any],
    goal_id: str,
    binding_path: Path,
    quota_packet: Mapping[str, Any],
    provider_target: Mapping[str, Any] | None = None,
    external_sink_delivery_authorized: bool,
    runner: CommandRunner = default_subprocess_runner,
    admit_delivery: Callable[[], None] | None = None,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
    synthesizer: NoticeSynthesizer | None = None,
    before_receipt_write: Callable[[], None] | None = None,
) -> dict[str, Any]:
    goal_from_registry(registry, goal_id)
    payload = read_goal_channel_binding(binding_path)
    binding = binding_for_goal(
        payload,
        goal_id,
        provider_target=provider_target,
    )
    enabled = human_gate_auto_notify_enabled(binding)
    result: dict[str, Any] = {
        "schema_version": "loopx_goal_channel_gate_auto_delivery_v0",
        "ok": True,
        "enabled": enabled,
        "status": "not_configured" if binding is None else "disabled",
        "external_write_performed": False,
        "readback_verified": False,
        "delivery_postcondition": {
            "satisfied": True,
            "blocks_delivery": False,
        },
    }
    if not enabled:
        return result
    if not external_sink_delivery_authorized:
        result["status"] = "external_sink_suppressed"
        return result

    notification = notify_lark_goal_channel_gate(
        registry=registry,
        goal_id=goal_id,
        binding_path=binding_path,
        quota_packet=quota_packet,
        provider_target=provider_target,
        execute=True,
        runner=runner,
        admit_delivery=admit_delivery,
        registry_path=registry_path, runtime_root=runtime_root, synthesizer=synthesizer,
        before_receipt_write=before_receipt_write,
    )
    result["notification"] = notification
    blocker = str(notification.get("blocker") or "")
    result["status"] = (
        "not_selected"
        if blocker == "state_transition_rejected"
        else "cooldown"
        if blocker == "notification_cooldown_active"
        else str(notification.get("status") or "failed")
    )
    result["external_write_performed"] = bool(
        notification.get("external_write_performed")
    )
    result["readback_verified"] = bool(notification.get("readback_verified"))
    safe_noop = blocker in {
        "notification_cooldown_active",
        "state_transition_rejected",
    }
    satisfied = bool(notification.get("ok")) or safe_noop
    result["ok"] = satisfied
    result["delivery_postcondition"] = {
        "satisfied": satisfied,
        "blocks_delivery": not satisfied,
    }
    return result


def doctor_lark_goal_channel(
    *,
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    provider_target: Mapping[str, Any] | None = None,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    goal_from_registry(registry, goal_id)
    payload = read_goal_channel_binding(binding_path)
    binding = binding_for_goal(payload, goal_id, provider_target=provider_target)
    if binding is None:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="doctor",
            execute=False,
            status="blocked",
            blocker="channel_binding_missing",
            public_summary="no local-private Goal Channel binding exists",
        )
    binding_enabled = binding.get("enabled") is True
    channel = _mapping(binding.get("channel"))
    identity_config = _mapping(binding.get("identity"))
    kanban = _mapping(binding.get("kanban"))
    chat_id = str(channel.get("chat_id") or "")
    pinned_message_id = str(channel.get("pinned_message_id") or "")
    cli_bin = str(identity_config.get("cli_bin") or DEFAULT_CLI_BIN)
    identity = str(identity_config.get("sender_identity") or "")
    profile = str(identity_config.get("sender_profile") or "") or None
    configured_app_id = str(identity_config.get("bot_app_id") or "")
    auth_ok = bool(
        identity == "bot"
        and APP_ID_PATTERN.fullmatch(configured_app_id)
        and auth_verified(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
            identity="bot",
            expected_bot_name=str(identity_config.get("bot_display_name") or "")
            or None,
        )
        and verified_app_id(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
        )
        == configured_app_id
    )
    channel_ok = bool(
        auth_ok
        and CHAT_ID_PATTERN.fullmatch(chat_id)
        and chat_verified(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
            identity="bot",
            chat_id=chat_id,
        )
    )
    control_message_ok = bool(
        channel_ok
        and MESSAGE_ID_PATTERN.fullmatch(pinned_message_id)
        and message_readback_verified(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
            identity="bot",
            message_id=pinned_message_id,
            expected_text=f"LoopX Goal: {goal_id}",
        )
        and pin_readback_verified(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
            identity="bot",
            chat_id=chat_id,
            message_id=pinned_message_id,
        )
    )
    kanban_path = _resolve_kanban_config_path(
        registry_path=registry_path,
        configured_path=kanban.get("config_path"),
    )
    board_payload = read_lark_kanban_local_config(kanban_path)
    board_config = lark_kanban_config_from_payload(board_payload)
    kanban_result = lark_kanban_doctor(
        config_path=kanban_path,
        cli_bin=board_config.cli_bin if board_config else cli_bin,
        identity=board_config.identity if board_config else "user",
        require_board=True,
        runner=runner,
    )
    blocker = None
    if not binding_enabled:
        blocker = "channel_binding_incomplete"
    elif not auth_ok:
        blocker = "provider_identity_unverified"
    elif not channel_ok:
        blocker = "channel_membership_unverified"
    elif not control_message_ok:
        blocker = "readback_mismatch"
    elif not kanban_result.get("ok"):
        blocker = "kanban_binding_missing"
    ok = blocker is None
    return operation_packet(
        ok=ok,
        goal_id=goal_id,
        operation="doctor",
        execute=False,
        status="ready" if ok else "blocked",
        blocker=blocker,
        public_summary=(
            "the Lark Goal Channel binding is ready"
            if ok
            else "the Lark Goal Channel binding needs attention"
        ),
        readback_verified=ok,
        details={
            "binding_present": True,
            "binding_enabled": binding_enabled,
            "identity_verified": auth_ok,
            "channel_membership_verified": channel_ok,
            "control_message_verified": control_message_ok,
            "kanban_ready": bool(kanban_result.get("ok")),
        },
    )


def sync_lark_goal_channel(
    *,
    registry: Mapping[str, Any],
    registry_path: Path,
    goal_id: str,
    binding_path: Path,
    provider_target: Mapping[str, Any] | None = None,
    agent_id: str | None = None,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    goal_from_registry(registry, goal_id)
    payload = read_goal_channel_binding(binding_path)
    binding = binding_for_goal(payload, goal_id, provider_target=provider_target)
    if binding is None:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="sync",
            execute=execute,
            status="blocked",
            blocker="channel_binding_missing",
            public_summary="configure the Goal Channel before syncing it",
        )
    if binding.get("enabled") is not True:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="sync",
            execute=execute,
            status="blocked",
            blocker="channel_binding_incomplete",
            public_summary="complete Goal Channel setup before syncing it",
        )
    kanban = _mapping(binding.get("kanban"))
    kanban_path = _resolve_kanban_config_path(
        registry_path=registry_path,
        configured_path=kanban.get("config_path"),
    )
    board_payload = read_lark_kanban_local_config(kanban_path)
    config = lark_kanban_config_from_payload(board_payload)
    if config is None:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="sync",
            execute=execute,
            status="blocked",
            blocker="kanban_binding_missing",
            public_summary="the Goal Channel Kanban binding is missing",
        )
    result = sync_loopx_todos_to_lark_kanban(
        config,
        registry_path=registry_path,
        goal_id=goal_id,
        agent_id=agent_id,
        config_path=kanban_path,
        execute=execute,
        runner=runner,
    )
    ok = bool(result.get("ok"))
    provider_write_ok = ok
    successful_write_count = int(result.get("successful_write_count") or 0)
    external_write_performed = bool(result.get("external_write_performed"))
    records = [
        record for record in result.get("records") or [] if isinstance(record, Mapping)
    ]
    readback_verified = False
    if execute and ok:
        readback_config = LarkKanbanConfig(
            base_token=config.base_token,
            table_id=config.table_id,
            view_id=None,
            cli_bin=config.cli_bin,
            identity=config.identity,
        )
        readback = call(runner, build_record_list_command(readback_config))
        expected_record_ids = {
            str(record.get("record_id") or "")
            for record in records
            if str(record.get("record_id") or "")
        }
        readback_payload = json_payload(readback)
        readback_verified = bool(
            readback.get("returncode") == 0
            and len(expected_record_ids) == len(records)
            and all(
                payload_contains_exact(readback_payload, record_id)
                for record_id in expected_record_ids
            )
        )
        if not readback_verified:
            ok = False
    return operation_packet(
        ok=ok,
        goal_id=goal_id,
        operation="sync",
        execute=execute,
        status="synced" if execute and ok else "preview_ready" if ok else "failed",
        blocker=(
            None
            if ok
            else "readback_mismatch"
            if execute and provider_write_ok
            else "provider_api_failed"
        ),
        public_summary=(
            "synced accepted LoopX todos to the configured Goal Channel Kanban"
            if execute and ok
            else "previewed the Goal Channel Kanban sync"
            if ok
            else "the Goal Channel Kanban sync failed readback"
            if execute and provider_write_ok
            else "the Goal Channel Kanban sync failed after partial provider writes"
            if execute and external_write_performed
            else "the Goal Channel Kanban sync failed"
        ),
        external_write_performed=external_write_performed,
        readback_verified=readback_verified,
        details={
            "kanban_ready": True,
            "todo_count": int(result.get("todo_count") or 0),
            "record_count": len(records),
            "successful_write_count": successful_write_count,
        },
    )


@serialize_goal_binding_mutation
def notify_lark_goal_channel_gate(
    *,
    registry: Mapping[str, Any],
    goal_id: str,
    binding_path: Path,
    quota_packet: Mapping[str, Any],
    provider_target: Mapping[str, Any] | None = None,
    execute: bool = False,
    runner: CommandRunner = default_subprocess_runner,
    admit_delivery: Callable[[], None] | None = None,
    registry_path: Path | None = None,
    runtime_root: Path | None = None,
    synthesizer: NoticeSynthesizer | None = None,
    before_receipt_write: Callable[[], None] | None = None,
) -> dict[str, Any]:
    goal = goal_from_registry(registry, goal_id)
    payload = read_goal_channel_binding(binding_path)
    raw_binding = binding_for_goal(payload, goal_id)
    if raw_binding is None:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="blocked",
            blocker="channel_binding_missing",
            public_summary="configure the Goal Channel before notifying a human gate",
        )
    target_ref = str(raw_binding.get("target_ref") or "")
    if target_ref and provider_target is None:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="blocked",
            blocker="provider_target_missing",
            public_summary="configure the referenced shared provider target first",
        )
    binding = binding_for_goal(payload, goal_id, provider_target=provider_target)
    if binding is None:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="blocked",
            blocker="channel_binding_incomplete",
            public_summary="complete Goal Channel setup before notifying a human gate",
        )
    if binding.get("enabled") is not True:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="blocked",
            blocker="channel_binding_incomplete",
            public_summary="complete Goal Channel setup before notifying a human gate",
        )
    if quota_human_gate_notification_suppressed(quota_packet):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="suppressed",
            blocker="notification_cooldown_active",
            public_summary="the current human gate notification is in cooldown",
        )
    if not quota_selects_human_gate(quota_packet):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="rejected",
            blocker="state_transition_rejected",
            public_summary="LoopX has not selected a human gate notification",
        )
    channel = _mapping(binding.get("channel"))
    identity_config = _mapping(binding.get("identity"))
    kanban = _mapping(binding.get("kanban"))
    chat_id = str(channel.get("chat_id") or "")
    cli_bin = str(identity_config.get("cli_bin") or DEFAULT_CLI_BIN)
    identity = str(identity_config.get("sender_identity") or "")
    profile = str(identity_config.get("sender_profile") or "") or None
    question = public_safe_compact_text(quota_packet.get("gate_prompt")
        or quota_packet.get("operator_question") or quota_packet.get("reason")
        or "A human decision is required.", limit=900)
    facts = {"goal_id": goal_id,
        "objective": public_safe_compact_text(goal_objective(goal), limit=500),
        "decision_notice": _gate_notice_projection(goal_id=goal_id, quota_packet=quota_packet),
        "continuation": quota_packet.get("blocked_priority_fallback") or {},
        "kanban_url": str(kanban.get("base_url") or "")}
    requests = (quota_packet.get("request_snapshot") or {}).get("items", [])
    blockers, _ = collect_blocked_transition_notices({"attention_queue": {"items": [{
        "goal_id": goal_id, "user_todos": {"items": requests}}]}}, goal_id, quota_packet,
        fallback_assessed=bool(quota_packet.get("blocked_priority_fallback")))
    request_ids = {r["request_id"] for r in facts["decision_notice"]["items"]}
    facts["blockers"] = [b for b in blockers if b["task"]["todo_id"] in request_ids]
    covered_blockers = {b["blocker_identity"]: b["blocker_revision"] for b in facts["blockers"]}
    gate_identity = quota_human_gate_identity(quota_packet)
    state_generation = quota_human_gate_state_generation(quota_packet)
    reminder_generation = quota_human_gate_reminder_generation(quota_packet)
    delivery_generation = reminder_generation or "material_state"
    target_generation = semantic_key("lark_goal_channel_target_v0", chat_id)
    key = semantic_key(
        goal_id,
        "lark",
        "notify_gate",
        gate_identity,
        state_generation,
        delivery_generation,
        chat_id,
    )
    receipts = _mapping(binding.get("receipts"))
    existing_receipt = _mapping(receipts.get(key))
    if existing_receipt and existing_receipt.get("message_id"):
        return operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="already_sent",
            public_summary="the same human gate generation was already sent",
            readback_verified=existing_receipt.get("readback_verified") is not False,
            idempotency_key=key,
            receipt_id=f"receipt_{key.removeprefix('sha256:')[:16]}",
        )
    legacy_key = semantic_key(
        goal_id,
        "lark",
        "notify_gate",
        gate_identity,
        question,
        chat_id,
    )
    legacy_receipt = _mapping(receipts.get(legacy_key))
    if legacy_receipt and not legacy_receipt.get("state_generation"):
        return operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="already_sent",
            public_summary="the exact legacy human gate delivery was already sent",
            readback_verified=legacy_receipt.get("readback_verified") is not False,
            idempotency_key=key,
            receipt_id=f"receipt_{legacy_key.removeprefix('sha256:')[:16]}",
        )
    same_gate_receipts = [
        receipt
        for receipt in receipts.values()
        if isinstance(receipt, Mapping)
        and receipt.get("kind") == "gate_notification"
        and receipt.get("message_id")
        and str(receipt.get("gate_identity") or "") == gate_identity
        and str(receipt.get("target_generation") or "") == target_generation
    ]
    same_state_receipts = [
        receipt
        for receipt in same_gate_receipts
        if str(receipt.get("state_generation") or "") == state_generation
    ]
    if same_state_receipts and reminder_generation is None:
        matched_receipt = same_state_receipts[-1]
        return operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="notify_gate",
            execute=execute,
            status="already_sent",
            public_summary="the unchanged human gate generation was already sent",
            readback_verified=matched_receipt.get("readback_verified") is not False,
            idempotency_key=key,
        )
    if not execute:
        return operation_packet(
            ok=True,
            goal_id=goal_id,
            operation="notify_gate",
            execute=False,
            status="preview_ready",
            public_summary="previewed one bounded human gate notification",
            idempotency_key=key,
            receipt_id=f"receipt_{key.removeprefix('sha256:')[:16]}",
        )
    if admit_delivery is not None:
        admit_delivery()
    configured_app_id = str(identity_config.get("bot_app_id") or "")
    if not (
        identity == "bot"
        and APP_ID_PATTERN.fullmatch(configured_app_id)
        and auth_verified(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
            identity="bot",
            expected_bot_name=str(identity_config.get("bot_display_name") or "")
            or None,
        )
        and verified_app_id(
            runner=runner,
            cli_bin=cli_bin,
            profile=profile,
        )
        == configured_app_id
    ):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=True,
            status="gate_required",
            blocker="provider_identity_unverified",
            public_summary="the configured Lark sender identity could not be verified",
            idempotency_key=key,
        )
    if not CHAT_ID_PATTERN.fullmatch(chat_id) or not chat_verified(
        runner=runner,
        cli_bin=cli_bin,
        profile=profile,
        identity="bot",
        chat_id=chat_id,
    ):
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=True,
            status="gate_required",
            blocker="channel_membership_unverified",
            public_summary="the configured Lark channel is not reachable by the sender",
            idempotency_key=key,
        )
    try:
        message = render_channel_notice(
            cached_text=existing_receipt.get("delivery_text"),
            facts=facts, synthesizer=synthesizer,
            registry_path=registry_path, runtime_root=runtime_root,
            binding_path=binding_path, goal_id=goal_id, chat_id=chat_id,
            provider_target=provider_target,
        )
    except (ValueError, RuntimeError, OSError, TimeoutError):
        receipts[key] = {**existing_receipt, "kind": "gate_notification", "gate_identity": gate_identity,
            "target_generation": target_generation, "state_generation": state_generation,
            "delivery_generation": delivery_generation, "readback_verified": False,
            "failure_code": "steward_notice_unavailable"}
        if before_receipt_write is not None:
            before_receipt_write()
        save_goal_binding(binding_path=binding_path, payload=payload, goal_id=goal_id,
                          binding={**raw_binding, "receipts": receipts})
        return operation_packet(ok=False, goal_id=goal_id, operation="notify_gate",
            execute=True, status="blocked", blocker="steward_notice_unavailable",
            public_summary="the steward could not prepare a current notification; retry after recovery",
            idempotency_key=key)
    # Keep the generated body with the existing effect receipt before transport.
    # Provider retries use identical text under the same idempotency key.
    receipts[key] = {"kind": "gate_notification", "gate_identity": gate_identity,
        "target_generation": target_generation, "state_generation": state_generation,
        "delivery_generation": delivery_generation, "delivery_text": message,
        "covered_blockers": covered_blockers,
        "readback_verified": False}
    if before_receipt_write is not None:
        before_receipt_write()
    save_goal_binding(binding_path=binding_path, payload=payload, goal_id=goal_id,
                      binding={**raw_binding, "receipts": receipts})
    send = call(
        runner,
        lark_args(
            cli_bin=cli_bin,
            profile=profile,
            tail=[
                "im",
                "+messages-send",
                "--chat-id",
                chat_id,
                "--text",
                message,
                "--idempotency-key",
                provider_idempotency_key(key),
                "--as",
                "bot",
                "--format",
                "json",
            ],
        ),
    )
    message_id = (
        find_first_string(
            json_payload(send),
            {"message_id"},
            MESSAGE_ID_PATTERN,
        )
        or ""
    )
    if send.get("returncode") != 0 or not message_id:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=True,
            status="failed",
            blocker="provider_api_failed",
            public_summary="the human gate notification could not be sent",
            idempotency_key=key,
        )
    verified = message_readback_verified(
        runner=runner,
        cli_bin=cli_bin,
        profile=profile,
        identity="bot",
        message_id=message_id,
        expected_text=message,
    )
    mutable_receipts = dict(receipts)
    mutable_receipts[key] = {
        "kind": "gate_notification",
        "delivery_text": message,
        "covered_blockers": covered_blockers,
        "gate_identity": gate_identity,
        "target_generation": target_generation,
        "state_generation": state_generation,
        "delivery_generation": delivery_generation,
        "message_id": message_id,
        "verified_at": now_iso(),
        "readback_verified": verified,
    }
    mutable_binding = dict(raw_binding)
    mutable_binding["receipts"] = mutable_receipts
    save_goal_binding(
        binding_path=binding_path,
        payload=payload,
        goal_id=goal_id,
        binding=mutable_binding,
    )
    if not verified:
        return operation_packet(
            ok=False,
            goal_id=goal_id,
            operation="notify_gate",
            execute=True,
            status="sent_unverified",
            blocker="readback_mismatch",
            public_summary="the human gate notification was sent but not verified",
            external_write_performed=True,
            idempotency_key=key,
        )
    return operation_packet(
        ok=True,
        goal_id=goal_id,
        operation="notify_gate",
        execute=True,
        status="sent_verified",
        public_summary="sent one verified human gate notification",
        external_write_performed=True,
        readback_verified=True,
        idempotency_key=key,
        receipt_id=f"receipt_{key.removeprefix('sha256:')[:16]}",
    )
