from __future__ import annotations

import json
import hashlib
import re
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .event_collector import (
    _executable_prefix,
    _jq_projection,
    load_lark_event_collector_config,
)
from .event_inbox import (
    MESSAGE_ID_PATTERN,
    _event_attention_kind,
    ingest_lark_event_inbox,
)
from .goal_channel_operation import (
    handle_goal_channel_operation_callback,
    recover_goal_channel_operation_results,
    recover_goal_channel_simulation_claims,
)
from .private_json import write_private_json_atomic

APP_ID_PATTERN = re.compile(r"cli_[A-Za-z0-9_-]+")
EVENT_READY_PREFIX = "[event] ready "
EVENT_DIAGNOSTIC_PREFIX = "[event] "
_CALLBACK_FAILURE_CODES = {
    "collector Bot application identity is unverified": "collector_app_identity_unverified",
    "operation callback event type is unsupported": "callback_event_type_unsupported",
    "operation callback must come from a button": "callback_action_not_button",
    "operation callback action_value is invalid": "callback_action_value_invalid",
    "operation callback action is incomplete": "callback_action_incomplete",
    "operation callback action schema is unsupported": "callback_action_schema_unsupported",
    "operation callback decision is unsupported": "callback_decision_unsupported",
    "operation callback update token is invalid": "callback_update_token_invalid",
    "operation callback event_id is invalid": "callback_event_id_invalid",
    "operation callback message_id is invalid": "callback_message_id_invalid",
    "operation callback chat_id is invalid": "callback_chat_id_invalid",
    "operation callback operator_id is invalid": "callback_operator_id_invalid",
    "operation callback host is unsupported": "callback_host_unsupported",
    "operation callback card content is unavailable": "callback_card_content_unavailable",
    "operation callback proposal was not found": "callback_proposal_not_found",
    "typed operation proposal is unavailable": "callback_operation_unavailable",
    "typed operation envelope is unavailable": "callback_operation_envelope_unavailable",
    "operation review plan is unavailable": "callback_review_plan_unavailable",
    "operation review frame is unavailable": "callback_review_frame_unavailable",
    "operation projection is unavailable": "callback_projection_unavailable",
    "operation projection fields are unavailable": "callback_projection_fields_unavailable",
    "operation callback timestamp is invalid": "callback_timestamp_invalid",
    "operation confirmation has unsupported or missing fields": "callback_confirmation_invalid",
    "operation timestamps require a timezone": "callback_timestamp_timezone_missing",
    "claimed operation disappeared before dispatch": "callback_claim_disappeared",
    "operation executor outcome does not match the consumed claim": "callback_executor_outcome_invalid",
    "operation disappeared before result delivery": "callback_operation_disappeared",
    "operation card delivery was not recorded": "delivery_not_recorded",
    "operation callback digest drifted": "confirmation_digest_drifted",
    "recorded operation card digest drifted": "recorded_card_digest_drifted",
    "operation callback card content drifted": "callback_card_projection_drifted",
    "operation callback app identity drifted": "callback_app_identity_drifted",
    "principal is not authorized for this operation": "principal_not_authorized",
    "operation callback tenant membership is unverified": "membership_unverified",
    "operation callback does not match the delivered request": "delivery_binding_mismatch",
    "operation is not awaiting confirmation": "operation_not_awaiting_confirmation",
    "operation confirmation arrived after expiry": "operation_expired",
    "operation callback result delivery was not verified": "result_delivery_unverified",
}
_CALLBACK_FAILURE_STAGES = {
    "_callback_action": "parse_action",
    "_callback_timestamp": "validate_timestamp",
    "callback_timestamp": "validate_timestamp",
    "_callback_card_content_matches": "verify_card_content",
    "callback_card_content_matches": "verify_card_content",
    "_operator_membership_verified": "verify_operator_membership",
    "operator_membership_verified": "verify_operator_membership",
    "decide_operation": "claim_operation",
    "_execute_claimed_operation": "execute_operation",
    "_update_callback_card": "deliver_result",
    "update_callback_card": "deliver_result",
}
CommandRunner = Callable[..., subprocess.CompletedProcess[str]]
Sleeper = Callable[[float], None]


def _operation_callback_failure_code(exc: BaseException) -> str:
    return _CALLBACK_FAILURE_CODES.get(str(exc), "callback_rejected")


def _operation_callback_failure_stage(exc: BaseException) -> str:
    """Return a value-free processing stage for a rejected callback."""

    stage = "handle_callback"
    traceback = exc.__traceback__
    while traceback is not None:
        stage = _CALLBACK_FAILURE_STAGES.get(
            traceback.tb_frame.f_code.co_name,
            stage,
        )
        traceback = traceback.tb_next
    return stage


def _callback_event_shape(payload: Mapping[str, Any]) -> dict[str, object]:
    """Return a value-free diagnostic projection for a rejected callback."""

    action_value = payload.get("action_value")
    try:
        action = (
            json.loads(action_value) if isinstance(action_value, str) else action_value
        )
    except json.JSONDecodeError:
        action = None
    card_content = payload.get("card_content")
    card_shape = "missing"
    if isinstance(card_content, str):
        if not card_content:
            card_shape = "empty"
        else:
            try:
                parsed_card = json.loads(card_content)
            except json.JSONDecodeError:
                card_shape = "text"
            else:
                card_shape = (
                    "json_object" if isinstance(parsed_card, Mapping) else "json_other"
                )
    elif isinstance(card_content, Mapping):
        card_shape = "object"
    elif card_content is not None:
        card_shape = type(card_content).__name__
    timestamp = str(payload.get("timestamp") or "")
    return {
        "type_supported": payload.get("type") == "card.action.trigger",
        "action_is_button": payload.get("action_tag") == "button",
        "action_is_object": isinstance(action, Mapping),
        "action_field_count": len(action) if isinstance(action, Mapping) else 0,
        "event_id_valid": bool(
            re.fullmatch(r"[A-Za-z0-9._:-]{1,240}", str(payload.get("event_id") or ""))
        ),
        "timestamp_is_digits": timestamp.isdigit(),
        "timestamp_digit_count": len(timestamp),
        "operator_id_present": bool(payload.get("operator_id")),
        "message_id_present": bool(payload.get("message_id")),
        "chat_id_present": bool(payload.get("chat_id")),
        "host_supported": payload.get("host") == "im_message",
        "token_present": bool(payload.get("token")),
        "card_content_shape": card_shape,
        "shape_digest": hashlib.sha256(
            "\0".join(sorted(str(key) for key in payload)).encode()
        ).hexdigest()[:16],
    }


def _run_json(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    timeout_seconds: float = 30,
) -> object:
    try:
        result = runner(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}
    try:
        return json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError):
        return {}


def _run_json_with_status(
    runner: CommandRunner,
    argv: Sequence[str],
    *,
    timeout_seconds: float = 30,
) -> tuple[object, str]:
    try:
        result = runner(
            list(argv),
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return {}, "message_context_lookup_failed"
    except (OSError, subprocess.SubprocessError):
        return {}, "message_context_lookup_failed"
    payloads: list[object] = []
    for raw in (result.stdout, result.stderr):
        try:
            payloads.append(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            continue
    payload = payloads[0] if payloads else {}

    def provider_error_code(candidate: object) -> str:
        if not isinstance(candidate, Mapping):
            return ""
        direct = str(candidate.get("code") or "")
        if direct:
            return direct
        error = candidate.get("error")
        return str(error.get("code") or "") if isinstance(error, Mapping) else ""

    if result.returncode != 0:
        if any(provider_error_code(candidate) == "230027" for candidate in payloads):
            return {}, "message_context_permission_required"
        return {}, "message_context_lookup_failed"
    return payload, "message_context_available"


def _find_string_by_key(value: object, keys: set[str]) -> str | None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            if str(key) in keys and isinstance(child, str) and child.strip():
                return child.strip()
        for child in value.values():
            if found := _find_string_by_key(child, keys):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _find_string_by_key(child, keys):
                return found
    return None


def _profile_app_id(
    *,
    runner: CommandRunner,
    command_prefix: Sequence[str],
    profile: str,
) -> str | None:
    payload = _run_json(
        runner,
        [*command_prefix, "--profile", profile, "whoami", "--as", "bot"],
    )
    app_id = _find_string_by_key(payload, {"appId", "app_id"})
    return app_id if app_id and APP_ID_PATTERN.fullmatch(app_id) else None


def _find_message(value: object, message_id: str) -> Mapping[str, Any] | None:
    if isinstance(value, Mapping):
        if str(value.get("message_id") or "") == message_id:
            return value
        for child in value.values():
            if found := _find_message(child, message_id):
                return found
    elif isinstance(value, list):
        for child in value:
            if found := _find_message(child, message_id):
                return found
    return None


def _read_message(
    *,
    runner: CommandRunner,
    command_prefix: Sequence[str],
    profile: str,
    message_id: str,
    attempts: int,
    sleeper: Sleeper,
) -> Mapping[str, Any] | None:
    for attempt in range(max(1, attempts)):
        payload = _run_json(
            runner,
            [
                *command_prefix,
                "--profile",
                profile,
                "im",
                "+messages-mget",
                "--message-ids",
                message_id,
                "--as",
                "bot",
                "--no-reactions",
                "--format",
                "json",
            ],
        )
        if message := _find_message(payload, message_id):
            return message
        if attempt + 1 < max(1, attempts):
            sleeper(0.5 * (attempt + 1))
    return None


def _read_message_with_status(
    *,
    runner: CommandRunner,
    command_prefix: Sequence[str],
    profile: str,
    message_id: str,
    attempts: int,
    sleeper: Sleeper,
) -> tuple[Mapping[str, Any] | None, str]:
    status = "message_context_unavailable"
    for attempt in range(max(1, attempts)):
        payload, current_status = _run_json_with_status(
            runner,
            [
                *command_prefix,
                "--profile",
                profile,
                "im",
                "+messages-mget",
                "--message-ids",
                message_id,
                "--as",
                "bot",
                "--no-reactions",
                "--format",
                "json",
            ],
        )
        if current_status == "message_context_permission_required":
            return None, current_status
        if current_status == "message_context_lookup_failed":
            status = current_status
        if message := _find_message(payload, message_id):
            return message, "message_context_verified"
        if attempt + 1 < max(1, attempts):
            sleeper(0.5 * (attempt + 1))
    return None, status


def _sender_identity(message: Mapping[str, Any]) -> tuple[str, str]:
    sender = message.get("sender")
    sender = sender if isinstance(sender, Mapping) else {}
    sender_type = str(
        sender.get("sender_type") or message.get("sender_type") or ""
    ).strip()
    sender_id = str(
        sender.get("id") or sender.get("sender_id") or message.get("sender_id") or ""
    ).strip()
    return sender_type, sender_id


def _is_profile_self_message(
    message: Mapping[str, Any], *, profile_app_id: str | None
) -> bool:
    """Match only a provider-typed app sender to the verified profile app id."""

    if profile_app_id is None:
        return False
    sender_type, sender_id = _sender_identity(message)
    return sender_type == "app" and sender_id == profile_app_id


def enrich_lark_event_reply_context(
    event: Mapping[str, Any],
    *,
    runner: CommandRunner,
    command_prefix: Sequence[str],
    profile: str,
    profile_app_id: str,
    configured_chat_id: str,
    attempts: int = 3,
    sleeper: Sleeper = time.sleep,
) -> dict[str, Any]:
    """Verify whether an event structurally replies to this profile's bot."""

    enriched = dict(event)
    enriched["reply_context_verified"] = False
    enriched["reply_to_bot"] = False
    enriched["message_context_status"] = "message_context_unavailable"
    message_id = str(event.get("message_id") or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(message_id):
        return enriched
    current, current_status = _read_message_with_status(
        runner=runner,
        command_prefix=command_prefix,
        profile=profile,
        message_id=message_id,
        attempts=attempts,
        sleeper=sleeper,
    )
    enriched["message_context_status"] = current_status
    if current is None or str(current.get("chat_id") or "") != configured_chat_id:
        return enriched

    # The compact lark-cli event stream intentionally carries only stable event
    # envelope fields. Message-level routing fields live on the message lookup
    # response, so copy them into the canonical event before deciding whether
    # this bot was addressed and which Goal Topic owns the message.
    content = current.get("content")
    if content not in (None, ""):
        enriched["content"] = content
    for field in ("mentions", "mentioned"):
        if field in current:
            enriched[field] = current[field]
    current_sender_type, current_sender_id = _sender_identity(current)
    if current_sender_type:
        enriched["sender_type"] = current_sender_type
    if current_sender_id:
        enriched["sender_id"] = current_sender_id

    parent_id = str(current.get("parent_id") or "").strip()
    root_id = str(current.get("root_id") or "").strip()
    if MESSAGE_ID_PATTERN.fullmatch(root_id):
        enriched["root_id"] = root_id
    if not MESSAGE_ID_PATTERN.fullmatch(parent_id):
        enriched["reply_context_verified"] = True
        enriched["message_context_status"] = "message_context_verified"
        return enriched
    enriched["parent_id"] = parent_id

    parent, parent_status = _read_message_with_status(
        runner=runner,
        command_prefix=command_prefix,
        profile=profile,
        message_id=parent_id,
        attempts=attempts,
        sleeper=sleeper,
    )
    if parent_status != "message_context_verified":
        enriched["message_context_status"] = parent_status
    if parent is None or str(parent.get("chat_id") or "") != configured_chat_id:
        return enriched
    parent_sender_type, parent_sender_id = _sender_identity(parent)
    enriched["reply_context_verified"] = True
    enriched["message_context_status"] = "message_context_verified"
    enriched["reply_to_bot"] = bool(
        current_sender_type == "user"
        and parent_sender_type == "app"
        and parent_sender_id == profile_app_id
    )
    return enriched


def _consume_argv(
    config: Mapping[str, Any], command_prefix: Sequence[str]
) -> list[str]:
    chat_ids = [str(route["chat_id"]) for route in config["routes"]]
    return [
        *command_prefix,
        "--profile",
        str(config["profile"]),
        "event",
        "consume",
        str(config["event_key"]),
        "--as",
        str(config["identity"]),
        "--timeout",
        str(config["consume_timeout"]),
        "--jq",
        _jq_projection(chat_ids),
        "--quiet",
    ]


def _operation_callback_consume_argv(
    config: Mapping[str, Any], command_prefix: Sequence[str]
) -> list[str]:
    chat_ids = [str(route["chat_id"]) for route in config["routes"]]
    chat_filter = " or ".join(
        f".chat_id == {json.dumps(chat_id, ensure_ascii=False)}" for chat_id in chat_ids
    )
    return [
        *command_prefix,
        "--profile",
        str(config["profile"]),
        "event",
        "consume",
        "card.action.trigger",
        "--as",
        str(config["identity"]),
        "--timeout",
        str(config["consume_timeout"]),
        "--jq",
        f"select({chat_filter})",
    ]


def _operation_callback_status_path(project: str | Path) -> Path:
    return (
        Path(project).expanduser().resolve()
        / ".loopx"
        / "runtime"
        / "lark-collector"
        / "operation-callback-status.json"
    )


def _read_operation_callback_status(project: str | Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            _operation_callback_status_path(project).read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _write_operation_callback_status(
    project: str | Path,
    *,
    listener_active: bool,
    listener_ready: bool | None = None,
    callback_delivery_verified: bool | None = None,
    failure_kind: str | None = None,
    failure_code: str | None = None,
    failure_stage: str | None = None,
    failure_event_shape: Mapping[str, object] | None = None,
    consumer_returncode: int | None = None,
    recovered_result_count_delta: int = 0,
    result_delivery_failure_count_delta: int = 0,
    recovered_simulation_count_delta: int = 0,
    simulation_recovery_failure_count_delta: int = 0,
) -> dict[str, Any]:
    prior = _read_operation_callback_status(project)
    now = datetime.now(timezone.utc).isoformat()
    verified_count = int(prior.get("verified_callback_count") or 0)
    failure_count = int(prior.get("failed_callback_count") or 0)
    recovered_result_count = int(prior.get("recovered_result_count") or 0)
    result_delivery_failure_count = int(prior.get("result_delivery_failure_count") or 0)
    recovered_simulation_count = int(prior.get("recovered_simulation_count") or 0)
    simulation_recovery_failure_count = int(
        prior.get("simulation_recovery_failure_count") or 0
    )
    if callback_delivery_verified is True:
        verified_count += 1
    if failure_kind:
        failure_count += 1
    payload = {
        "schema_version": "lark_operation_callback_listener_status_v1",
        "listener_active": listener_active,
        "listener_ready": (
            bool(listener_ready)
            if listener_ready is not None
            else bool(prior.get("listener_ready") is True)
        ),
        "callback_delivery_verified": bool(
            prior.get("callback_delivery_verified") is True
            or callback_delivery_verified is True
        ),
        "verified_callback_count": verified_count,
        "failed_callback_count": failure_count,
        "recovered_result_count": (
            recovered_result_count + recovered_result_count_delta
        ),
        "result_delivery_failure_count": (
            result_delivery_failure_count + result_delivery_failure_count_delta
        ),
        "recovered_simulation_count": (
            recovered_simulation_count + recovered_simulation_count_delta
        ),
        "simulation_recovery_failure_count": (
            simulation_recovery_failure_count + simulation_recovery_failure_count_delta
        ),
        "last_verified_callback_at": (
            now
            if callback_delivery_verified is True
            else prior.get("last_verified_callback_at")
        ),
        "last_failure_kind": failure_kind or prior.get("last_failure_kind"),
        "last_failure_code": failure_code or prior.get("last_failure_code"),
        "last_failure_stage": failure_stage or prior.get("last_failure_stage"),
        "last_failure_event_shape": (
            dict(failure_event_shape)
            if failure_event_shape is not None
            else prior.get("last_failure_event_shape")
        ),
        "consumer_returncode": consumer_returncode,
        "updated_at": now,
        "private_content_returned": False,
    }
    write_private_json_atomic(_operation_callback_status_path(project), payload)
    return payload


def _operation_transport_runner(
    runner: CommandRunner,
    *,
    command_prefix: Sequence[str],
) -> Callable[[list[str], Path | None, float | None], dict[str, Any]]:
    def run(argv: list[str], cwd: Path | None, timeout: float | None) -> dict[str, Any]:
        effective_argv = list(argv)
        if (
            len(command_prefix) > 1
            and effective_argv
            and effective_argv[0] == command_prefix[-1]
        ):
            effective_argv = [*command_prefix, *effective_argv[1:]]
        try:
            result = runner(
                effective_argv,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return {"returncode": 1, "stdout": "", "stderr": ""}
        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
        }

    return run


def lark_event_requires_reply_context_lookup(
    event: Mapping[str, Any], *, bot_display_name: str
) -> bool:
    """Require provider context unless the stream carries a typed Bot mention."""

    provider_fields = {
        key: event[key] for key in ("mentioned", "mentions") if key in event
    }
    return (
        _event_attention_kind(
            provider_fields,
            bot_display_name=bot_display_name,
            capture_scope="configured_chat_all",
        )
        is None
    )


def _create_lark_event_received_reaction(
    event: Mapping[str, Any],
    *,
    runner: CommandRunner,
    command_prefix: Sequence[str],
    profile: str,
    emoji_type: str,
) -> str | None:
    message_id = str(event.get("message_id") or "").strip()
    if not MESSAGE_ID_PATTERN.fullmatch(message_id) or not emoji_type:
        return None
    payload = _run_json(
        runner,
        [
            *command_prefix,
            "--profile",
            profile,
            "im",
            "reactions",
            "create",
            "--message-id",
            message_id,
            "--data",
            json.dumps(
                {"reaction_type": {"emoji_type": emoji_type}},
                separators=(",", ":"),
            ),
            "--as",
            "bot",
            "--format",
            "json",
        ],
        timeout_seconds=5,
    )
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        return None
    return _find_string_by_key(payload, {"reaction_id"})


def add_lark_event_received_reaction(
    event: Mapping[str, Any],
    *,
    runner: CommandRunner,
    command_prefix: Sequence[str],
    profile: str,
    emoji_type: str,
) -> bool:
    return (
        _create_lark_event_received_reaction(
            event,
            runner=runner,
            command_prefix=command_prefix,
            profile=profile,
            emoji_type=emoji_type,
        )
        is not None
    )


def _delete_lark_event_reaction(
    *,
    runner: CommandRunner,
    command_prefix: Sequence[str],
    profile: str,
    message_id: str,
    reaction_id: str,
) -> bool:
    payload = _run_json(
        runner,
        [
            *command_prefix,
            "--profile",
            profile,
            "im",
            "reactions",
            "delete",
            "--message-id",
            message_id,
            "--reaction-id",
            reaction_id,
            "--as",
            "bot",
            "--format",
            "json",
        ],
        timeout_seconds=5,
    )
    return bool(isinstance(payload, Mapping) and payload.get("ok") is True)


def run_lark_event_collector(
    *,
    project: str | Path,
    config_path: str | Path,
    lark_cli_executable: str,
    runtime_root: str | Path | None = None,
    node_executable: str | None = None,
    runner: CommandRunner = subprocess.run,
) -> dict[str, Any]:
    config = load_lark_event_collector_config(
        project=project,
        config_path=config_path,
    )
    command_prefix = (
        [node_executable, lark_cli_executable]
        if node_executable
        else _executable_prefix(lark_cli_executable)
    )
    callbacks_enabled = config["operation_callbacks"]["enabled"] is True
    if callbacks_enabled and runtime_root is None:
        raise ValueError(
            "operation callback collection requires the pinned runtime root"
        )
    routes_by_chat = {str(route["chat_id"]): route for route in config["routes"]}
    resolved_runtime_root = (
        Path(str(runtime_root)).expanduser().resolve()
        if runtime_root is not None
        else None
    )
    process = subprocess.Popen(
        _consume_argv(config, command_prefix),
        stdout=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        bufsize=1,
    )
    callback_process: subprocess.Popen[str] | None = None
    callback_thread: threading.Thread | None = None
    result_recovery_thread: threading.Thread | None = None
    result_recovery_stop = threading.Event()
    callback_stats = {
        "ready": 0,
        "received": 0,
        "verified": 0,
        "failed": 0,
    }
    result_recovery_stats = {"attempted": 0, "delivered": 0, "failed": 0}
    simulation_recovery_stats = {"attempted": 0, "observed": 0, "failed": 0}
    profile_app_id: str | None = None
    profile_identity_checked = False
    if callbacks_enabled:
        profile_app_id = _profile_app_id(
            runner=runner,
            command_prefix=command_prefix,
            profile=str(config["profile"]),
        )
        profile_identity_checked = True
        callback_process = subprocess.Popen(
            _operation_callback_consume_argv(config, command_prefix),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
        _write_operation_callback_status(
            config["project"],
            listener_active=True,
            listener_ready=False,
        )

        def consume_operation_callbacks() -> None:
            assert callback_process is not None
            assert callback_process.stdout is not None
            transport_runner = _operation_transport_runner(
                runner,
                command_prefix=command_prefix,
            )
            try:
                for line in callback_process.stdout:
                    stripped = line.strip()
                    if stripped.startswith(EVENT_READY_PREFIX):
                        callback_stats["ready"] = 1
                        _write_operation_callback_status(
                            config["project"],
                            listener_active=True,
                            listener_ready=True,
                        )
                        continue
                    if stripped.startswith(EVENT_DIAGNOSTIC_PREFIX):
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(payload, Mapping):
                        continue
                    if payload.get("type") != "card.action.trigger":
                        # stderr is intentionally merged so provider startup
                        # diagnostics remain observable.  Some diagnostics are
                        # JSON objects, but they do not prove that the typed
                        # callback route is receiving events.
                        continue
                    if not callback_stats.get("ready"):
                        # A real typed event is stronger readiness evidence than
                        # the provider diagnostic marker.
                        callback_stats["ready"] = 1
                        _write_operation_callback_status(
                            config["project"],
                            listener_active=True,
                            listener_ready=True,
                        )
                    callback_stats["received"] += 1
                    try:
                        if profile_app_id is None:
                            raise ValueError(
                                "collector Bot application identity is unverified"
                            )
                        receipt = handle_goal_channel_operation_callback(
                            payload,
                            runtime_root=resolved_runtime_root,
                            action_store_root=resolved_runtime_root
                            / "chat"
                            / "actions",
                            profile_app_id=profile_app_id,
                            cli_bin=lark_cli_executable,
                            profile=str(config["profile"]),
                            runner=transport_runner,
                        )
                        if receipt.get("ok") is not True:
                            raise RuntimeError(
                                "operation callback result delivery was not verified"
                            )
                    except Exception as exc:  # noqa: BLE001
                        callback_stats["failed"] += 1
                        _write_operation_callback_status(
                            config["project"],
                            listener_active=True,
                            listener_ready=True,
                            failure_kind=type(exc).__name__,
                            failure_code=_operation_callback_failure_code(exc),
                            failure_stage=_operation_callback_failure_stage(exc),
                            failure_event_shape=_callback_event_shape(payload),
                        )
                        continue
                    callback_stats["verified"] += 1
                    _write_operation_callback_status(
                        config["project"],
                        listener_active=True,
                        listener_ready=True,
                        callback_delivery_verified=True,
                    )
            finally:
                callback_returncode = callback_process.wait()
                _write_operation_callback_status(
                    config["project"],
                    listener_active=False,
                    listener_ready=False,
                    consumer_returncode=callback_returncode,
                )

        callback_thread = threading.Thread(
            target=consume_operation_callbacks,
            name="loopx-lark-operation-callbacks",
            daemon=True,
        )
        callback_thread.start()

        def recover_operation_results() -> None:
            assert resolved_runtime_root is not None
            while not result_recovery_stop.is_set():
                try:
                    simulation_result = recover_goal_channel_simulation_claims(
                        action_store_root=resolved_runtime_root / "chat" / "actions",
                        runtime_root=resolved_runtime_root,
                    )
                except Exception:  # noqa: BLE001
                    simulation_result = {"attempted": 1, "observed": 0, "failed": 1}
                try:
                    result = recover_goal_channel_operation_results(
                        action_store_root=resolved_runtime_root / "chat" / "actions",
                        profile_app_id=str(profile_app_id or ""),
                        allowed_chat_ids=set(routes_by_chat),
                        cli_bin=lark_cli_executable,
                        profile=str(config["profile"]),
                        runner=_operation_transport_runner(
                            runner,
                            command_prefix=command_prefix,
                        ),
                    )
                except Exception:  # noqa: BLE001
                    result = {"attempted": 1, "delivered": 0, "failed": 1}
                for key in simulation_recovery_stats:
                    simulation_recovery_stats[key] += int(
                        simulation_result.get(key) or 0
                    )
                for key in result_recovery_stats:
                    result_recovery_stats[key] += int(result.get(key) or 0)
                if (
                    result.get("delivered")
                    or result.get("failed")
                    or simulation_result.get("observed")
                    or simulation_result.get("failed")
                ):
                    _write_operation_callback_status(
                        config["project"],
                        listener_active=True,
                        recovered_result_count_delta=int(result.get("delivered") or 0),
                        result_delivery_failure_count_delta=int(
                            result.get("failed") or 0
                        ),
                        recovered_simulation_count_delta=int(
                            simulation_result.get("observed") or 0
                        ),
                        simulation_recovery_failure_count_delta=int(
                            simulation_result.get("failed") or 0
                        ),
                    )
                result_recovery_stop.wait(3)

        result_recovery_thread = threading.Thread(
            target=recover_operation_results,
            name="loopx-lark-operation-result-recovery",
            daemon=True,
        )
        result_recovery_thread.start()
    previous_handlers: dict[signal.Signals, Any] = {}

    def forward_signal(signum: int, _: object) -> None:
        for child in (process, callback_process):
            if child is not None and child.poll() is None:
                child.send_signal(signum)

    for signum in (signal.SIGTERM, signal.SIGINT):
        previous_handlers[signum] = signal.signal(signum, forward_signal)
    captured_count = 0
    verified_count = 0
    reply_to_bot_count = 0
    self_message_skipped_count = 0
    routed_chat_ids: set[str] = set()
    try:
        assert process.stdout is not None
        for line in process.stdout:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, Mapping):
                continue
            chat_id = str(payload.get("chat_id") or "").strip()
            route = routes_by_chat.get(chat_id)
            if route is None:
                continue
            inbox = route["inbox"]
            sender_type, _sender_id = _sender_identity(payload)
            if sender_type == "app" and not profile_identity_checked:
                profile_app_id = _profile_app_id(
                    runner=runner,
                    command_prefix=command_prefix,
                    profile=str(config["profile"]),
                )
                profile_identity_checked = True
            if _is_profile_self_message(
                payload,
                profile_app_id=profile_app_id,
            ):
                self_message_skipped_count += 1
                continue
            needs_reply_lookup = lark_event_requires_reply_context_lookup(
                payload,
                bot_display_name=str(inbox["reply"].get("bot_display_name") or ""),
            )
            if not needs_reply_lookup:
                enriched = {
                    **payload,
                    "reply_context_verified": False,
                    "reply_to_bot": False,
                }
            else:
                if not profile_identity_checked:
                    profile_app_id = _profile_app_id(
                        runner=runner,
                        command_prefix=command_prefix,
                        profile=str(config["profile"]),
                    )
                    profile_identity_checked = True
                enriched = (
                    enrich_lark_event_reply_context(
                        payload,
                        runner=runner,
                        command_prefix=command_prefix,
                        profile=str(config["profile"]),
                        profile_app_id=profile_app_id,
                        configured_chat_id=chat_id,
                    )
                    if profile_app_id is not None
                    else {
                        **payload,
                        "reply_context_verified": False,
                        "reply_to_bot": False,
                    }
                )
            if _is_profile_self_message(
                enriched,
                profile_app_id=profile_app_id,
            ):
                self_message_skipped_count += 1
                continue
            message_id = str(enriched.get("message_id") or "")
            if not MESSAGE_ID_PATTERN.fullmatch(message_id):
                continue
            result = ingest_lark_event_inbox(
                project=config["project"],
                config_path=route["event_inbox_config_ref"],
                events=[
                    {
                        "schema_version": "lark_event_inbox_event_v0",
                        **enriched,
                        "route_key": route["route_key"],
                    }
                ],
                execute=True,
            )
            if int(result.get("accepted_count") or 0) == 0:
                continue
            captured_count += 1
            routed_chat_ids.add(chat_id)
            verified_count += int(enriched.get("reply_context_verified") is True)
            reply_to_bot_count += int(enriched.get("reply_to_bot") is True)
        returncode = process.wait()
    finally:
        result_recovery_stop.set()
        for child in (process, callback_process):
            if child is None or child.poll() is not None:
                continue
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if callback_thread is not None:
            callback_thread.join(timeout=10)
        if result_recovery_thread is not None:
            result_recovery_thread.join(timeout=10)
        if callbacks_enabled:
            _write_operation_callback_status(
                config["project"],
                listener_active=False,
                listener_ready=False,
                consumer_returncode=(
                    callback_process.returncode
                    if callback_process is not None
                    else None
                ),
            )
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
    callback_returncode = (
        callback_process.returncode if callback_process is not None else 0
    )
    result = {
        "ok": returncode == 0 and callback_returncode == 0,
        "schema_version": (
            "lark_event_collector_run_v1"
            if config["schema_version"] == "lark_event_collector_config_v1"
            else "lark_event_collector_run_v0"
        ),
        "status": (
            "completed"
            if returncode == 0 and callback_returncode == 0
            else "consumer_failed"
        ),
        "captured_count": captured_count,
        "route_count": len(config["routes"]),
        "routed_route_count": len(routed_chat_ids),
        "multi_chat_routing": len(config["routes"]) > 1,
        "reply_context_verified_count": verified_count,
        "reply_to_bot_count": reply_to_bot_count,
        # Retain the collector receipt shape while making its no-write
        # boundary explicit. Read acknowledgements are emitted by turn-start.
        "received_reaction_count": 0,
        "received_reaction_failure_count": 0,
        "self_message_skipped_count": self_message_skipped_count,
        # Collection proves durable capture only.  Provider acknowledgement is
        # owned by the Agent's next turn-start read boundary.
        "external_writes_performed": False,
        "profile_identity_checked": profile_identity_checked,
        "profile_identity_verified": profile_app_id is not None,
        "chat_ids_returned": False,
        "local_paths_returned": False,
        "private_content_returned": False,
    }
    if callbacks_enabled:
        result.update(
            {
                "operation_callback_listener_started": True,
                "operation_callback_listener_ready": bool(callback_stats.get("ready")),
                "operation_callback_received_count": callback_stats["received"],
                "operation_callback_verified_count": callback_stats["verified"],
                "operation_callback_failure_count": callback_stats["failed"],
                "operation_callback_consumer_succeeded": callback_returncode == 0,
                "operation_callback_console_configuration_preflighted": False,
                "operation_result_recovery_attempt_count": result_recovery_stats[
                    "attempted"
                ],
                "operation_result_recovery_verified_count": result_recovery_stats[
                    "delivered"
                ],
                "operation_result_recovery_failure_count": result_recovery_stats[
                    "failed"
                ],
                "operation_simulation_recovery_attempt_count": simulation_recovery_stats[
                    "attempted"
                ],
                "operation_simulation_recovery_observed_count": simulation_recovery_stats[
                    "observed"
                ],
                "operation_simulation_recovery_failure_count": simulation_recovery_stats[
                    "failed"
                ],
            }
        )
    return result
