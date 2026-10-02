from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
import subprocess

from ...control_plane.runtime.runtime_projection_route import (
    resolve_goal_source_runtime_route,
)
from ...control_plane.projects.registry_codec import load_registry
from ...paths import registry_project_root
from ...quota import build_quota_should_run
from ...status import collect_status
from ..runtime import default_extension_state_file, resolve_extension_activation
from . import LARK_EXTENSION_ID, LARK_GOAL_CHANNEL_PERMISSION
from .goal_channel_contracts import (
    binding_for_goal,
    blocked_notice_auto_notify_enabled,
    blocked_notice_auto_notify_marker_enabled,
    blocked_notice_auto_notify_marker_path,
    default_goal_channel_binding_path,
    human_gate_auto_notify_enabled,
    human_gate_auto_notify_marker_enabled,
    human_gate_auto_notify_marker_path,
    notification_request_snapshot,
    read_goal_channel_binding,
)
from .goal_channel_runtime import auto_notify_lark_goal_channel_gate
from .goal_channel_blocked_notice import deliver_blocked_notices
from .goal_channel_message_delivery import delivery_send_failure
from .identity_shapes import LARK_MESSAGE_ID_SEARCH as MESSAGE_ID_PATTERN
from .goal_channel_transport import (
    call,
    find_first_string,
    json_payload,
)
from .goal_channel_targets import (
    default_goal_channel_target_path,
    goal_channel_target_for_name,
    read_goal_channel_targets,
)
from .presentation.kanban import (
    CommandRunner,
    default_subprocess_runner,
)

FailureStage = Literal[
    "lifecycle",
    "extension_activation",
    "binding_resolution",
    "gate_selection",
    "provider_preflight",
    "provider_send",
    "provider_readback",
    "receipt_write",
]
ExternalWriteStatus = Literal["not_attempted", "not_performed", "performed", "unknown"]


def _exception_reason(error: Exception | None) -> str:
    # Never project exception messages, provider output, argv or local paths.
    if isinstance(error, (TimeoutError, subprocess.TimeoutExpired)):
        return "timeout"
    if isinstance(error, FileNotFoundError):
        return "runtime_unavailable"
    if isinstance(error, PermissionError):
        return "permission_denied"
    if isinstance(error, OSError):
        return "io_error"
    if isinstance(error, ValueError):
        return "invalid_input_or_config"
    if isinstance(error, subprocess.SubprocessError):
        return "provider_process_failed"
    return "unexpected_failure"


def _with_failure(
    result: dict[str, Any],
    *,
    stage: FailureStage,
    reason: str,
    write_status: ExternalWriteStatus = "not_attempted",
) -> dict[str, Any]:
    if result.get("enabled") and result.get("ok") is False:
        result["failure"] = {
            "schema_version": "loopx_goal_channel_gate_failure_v0",
            "stage": stage,
            "reason_code": reason,
            "external_write_status": write_status,
        }
        result["failure_summary"] = (
            f"Goal Channel notification failed at {stage} ({reason}); "
            "preserve the primary writeback and original Turn identity"
        )
    return result


class _DeliveryObservation:
    """Transport facts only; notification admission and TS recovery stay owned.

    Observing a command never retries it or changes its semantic identity.
    Use the existing send classifier, including its ambiguous-outcome rule.
    """

    def __init__(self, runner: CommandRunner) -> None:
        self.runner = runner
        self.stage: FailureStage = "binding_resolution"
        self.reason: str | None = None
        self.write_status: ExternalWriteStatus = "not_attempted"

    def __call__(
        self,
        args: list[str],
        cwd: Path | None,
        timeout: float | None,
    ) -> dict[str, Any]:
        # Match command position, never a token inside the private message.
        offset = 3 if args[1:2] == ["--profile"] else 1
        command = args[offset : offset + 2]
        sending = command == ["im", "+messages-send"]
        reading = command == ["im", "+messages-mget"]
        self.stage = (
            "provider_send"
            if sending
            else "provider_readback"
            if reading
            else "provider_preflight"
        )
        self.reason = None
        if sending:
            self.write_status = "unknown"
        result = call(
            lambda argv, _cwd, _timeout: self.runner(argv, cwd, timeout),
            args,
        )
        if result.get("timed_out") is True:
            self.reason = "timeout"
        elif result.get("spawn_failed") is True:
            self.reason = "runtime_unavailable"
        elif result.get("returncode") != 0:
            self.reason = "provider_api_failed"
        if sending:
            message_id = find_first_string(
                json_payload(result),
                {"message_id"},
                MESSAGE_ID_PATTERN,
            )
            if result.get("returncode") == 0 and message_id:
                self.write_status = "performed"
            else:
                failure = delivery_send_failure(result)
                self.write_status = (
                    "unknown"
                    if failure.external_write_performed is None
                    else "not_performed"
                )
                if not (result.get("timed_out") or result.get("spawn_failed")):
                    self.reason = failure.blocker
        elif reading:
            # A subsequent exception is local receipt persistence, not send.
            self.stage = "receipt_write"
        return dict(result)


def _inactive_result(status: str) -> dict[str, Any]:
    return {
        "schema_version": "loopx_goal_channel_gate_auto_delivery_v0",
        "ok": True,
        "enabled": False,
        "status": status,
        "external_write_performed": False,
        "readback_verified": False,
        "delivery_postcondition": {
            "satisfied": True,
            "blocks_delivery": False,
        },
    }


def _extension_unavailable_result(*, configured: bool) -> dict[str, Any]:
    if configured:
        return {
            "schema_version": "loopx_goal_channel_gate_auto_delivery_v0",
            "ok": False,
            "enabled": True,
            "status": "extension_unavailable",
            "external_write_performed": False,
            "readback_verified": False,
            "blocker": "extension_unavailable",
            "delivery_postcondition": {
                "satisfied": False,
                "blocks_delivery": True,
            },
        }
    return {
        **_inactive_result("extension_unavailable"),
        "enabled": False,
    }


def goal_channel_gate_sync_failure(
    *,
    registry_path: Path,
    goal_id: str,
    status: str = "failed",
    blocker: str = "goal_channel_gate_sync_failed",
    exception: Exception | None = None,
    configured: bool | None = None,
) -> dict[str, Any]:
    if configured is None:
        configured = False
        try:
            invoked_registry = load_registry(registry_path)
            source_route = resolve_goal_source_runtime_route(
                registry_path=registry_path,
                goal_id=goal_id,
                registry=invoked_registry,
            )
            source_registry_path = Path(str(source_route["source_registry"]))
            if source_registry_path.parent.name == ".loopx":
                binding_path = default_goal_channel_binding_path(source_registry_path)
                configured = human_gate_auto_notify_marker_enabled(
                    human_gate_auto_notify_marker_path(binding_path, goal_id)
                )
        except (OSError, ValueError):
            configured = False
    result = {
        "schema_version": "loopx_goal_channel_gate_auto_delivery_v0",
        "ok": not configured,
        "enabled": configured,
        "status": status if configured else "not_configured",
        "external_write_performed": False,
        "readback_verified": False,
        **({"blocker": blocker} if configured else {}),
        "delivery_postcondition": {
            "satisfied": not configured,
            "blocks_delivery": configured,
        },
    }
    return (
        _with_failure(
            result,
            stage="lifecycle",
            reason=_exception_reason(exception),
            write_status="unknown",
        )
        if exception is not None
        else result
    )


def sync_human_gate_after_refresh(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    agent_id: str | None,
    external_sink_delivery_authorized: bool,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    invoked_registry = load_registry(registry_path)
    source_route = resolve_goal_source_runtime_route(
        registry_path=registry_path,
        goal_id=goal_id,
        registry=invoked_registry,
    )
    source_registry_path = Path(str(source_route["source_registry"]))
    source_registry = (
        invoked_registry
        if source_registry_path.expanduser().resolve()
        == registry_path.expanduser().resolve()
        else load_registry(source_registry_path)
    )
    if source_registry_path.parent.name != ".loopx":
        return _inactive_result("project_binding_unavailable")
    runtime_root = (
        Path(runtime_root_override).expanduser().resolve()
        if runtime_root_override
        else Path(str(source_route["source_runtime_root"]))
    )
    binding_path = default_goal_channel_binding_path(source_registry_path)
    try:
        binding_payload = read_goal_channel_binding(binding_path)
        raw_binding = binding_for_goal(binding_payload, goal_id)
        target_name = str((raw_binding or {}).get("target_ref") or "")
        provider_target = (
            goal_channel_target_for_name(
                read_goal_channel_targets(
                    default_goal_channel_target_path(runtime_root)
                ),
                target_name,
            )
            if target_name
            else None
        )
    except (OSError, ValueError) as error:
        return _with_failure(
            goal_channel_gate_sync_failure(
                registry_path=registry_path,
                goal_id=goal_id,
                blocker="invalid_goal_channel_binding",
            ),
            stage="binding_resolution",
            reason=_exception_reason(error),
        )
    if target_name and provider_target is None:
        return _with_failure(
            {
                "schema_version": "loopx_goal_channel_gate_auto_delivery_v0",
                "ok": False,
                "enabled": True,
                "status": "provider_target_missing",
                "external_write_performed": False,
                "readback_verified": False,
                "blocker": "provider_target_missing",
                "delivery_postcondition": {
                    "satisfied": False,
                    "blocks_delivery": True,
                },
            },
            stage="binding_resolution",
            reason="provider_target_missing",
        )
    binding = binding_for_goal(
        binding_payload,
        goal_id,
        provider_target=provider_target,
    )
    quota_packet: dict[str, Any] = {}
    if human_gate_auto_notify_enabled(binding) and external_sink_delivery_authorized:
        try:
            status = collect_status(
                registry_path=source_registry_path,
                runtime_root_override=str(runtime_root),
                scan_roots=[registry_project_root(source_registry_path)],
                limit=20,
                goal_id=goal_id,
                # Delivery needs Goal/quota state, not a publication audit.
                include_public_boundary_scan=False,
            )
            quota_packet = build_quota_should_run(
                status,
                goal_id=goal_id,
                agent_id=agent_id,
            )
            quota_packet["request_snapshot"] = notification_request_snapshot(status, goal_id)
        except Exception as error:
            return _with_failure(
                goal_channel_gate_sync_failure(
                    registry_path=registry_path,
                    goal_id=goal_id,
                    configured=True,
                ),
                stage="gate_selection",
                reason=_exception_reason(error),
            )

    observation = _DeliveryObservation(runner)
    activation: dict[str, Any] | None = None

    def admit_delivery() -> None:
        # The existing notification owner first selects, suppresses and deduplicates.
        # Require the extension only when it is about to use provider transport.
        nonlocal activation
        observation.stage = "extension_activation"
        activation = resolve_extension_activation(
            LARK_EXTENSION_ID,
            state_file=default_extension_state_file(runtime_root),
            required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,),
        )

    try:
        result = auto_notify_lark_goal_channel_gate(
            registry=source_registry,
            goal_id=goal_id,
            binding_path=binding_path,
            quota_packet=quota_packet,
            provider_target=provider_target,
            external_sink_delivery_authorized=external_sink_delivery_authorized,
            runner=observation,
            admit_delivery=admit_delivery,
        )
    except Exception as error:
        result = (
            _extension_unavailable_result(configured=True)
            if observation.stage == "extension_activation"
            else goal_channel_gate_sync_failure(
                registry_path=registry_path,
                goal_id=goal_id,
                configured=human_gate_auto_notify_enabled(binding),
            )
        )
        result["external_write_performed"] = observation.write_status == "performed"
        _with_failure(
            result,
            stage=observation.stage,
            reason=_exception_reason(error),
            write_status=observation.write_status,
        )
    else:
        notification = result.get("notification") or {}
        blocker = str(notification.get("blocker") or "")
        failure_causes: dict[str, tuple[FailureStage, str]] = {
            "provider_identity_unverified": (
                "provider_preflight",
                "provider_identity_unverified",
            ),
            "channel_membership_unverified": (
                "provider_preflight",
                "channel_membership_unverified",
            ),
            "provider_api_failed": ("provider_send", "provider_api_failed"),
            "readback_mismatch": ("provider_readback", "readback_mismatch"),
            "channel_binding_missing": (
                "binding_resolution",
                "channel_binding_missing",
            ),
            "channel_binding_incomplete": (
                "binding_resolution",
                "channel_binding_incomplete",
            ),
            "provider_target_missing": (
                "binding_resolution",
                "provider_target_missing",
            ),
        }
        stage, reason = failure_causes.get(blocker, ("lifecycle", "unexpected_failure"))
        _with_failure(
            result,
            stage=stage,
            reason=observation.reason or reason,
            write_status=observation.write_status,
        )
    if activation is not None:
        result["extension_activation"] = activation
    return result


def blocked_notice_sync_failure(
    *, registry_path: Path, goal_id: str, exception: Exception
) -> dict[str, Any]:
    configured = False
    try:
        registry = load_registry(registry_path)
        route = resolve_goal_source_runtime_route(
            registry_path=registry_path, goal_id=goal_id, registry=registry
        )
        source_registry = Path(str(route["source_registry"]))
        if source_registry.parent.name == ".loopx":
            binding_path = default_goal_channel_binding_path(source_registry)
            configured = blocked_notice_auto_notify_enabled(
                binding_for_goal(read_goal_channel_binding(binding_path), goal_id)
            ) or blocked_notice_auto_notify_marker_enabled(
                blocked_notice_auto_notify_marker_path(binding_path, goal_id)
            )
    except (OSError, ValueError):
        pass
    return {
        "schema_version": "loopx_goal_channel_blocked_notice_delivery_v0",
        "ok": not configured,
        "enabled": configured,
        "status": "failed" if configured else "not_configured",
        "external_write_performed": False,
        "external_write_status": "unknown" if configured else "not_attempted",
        "readback_verified": False,
        "blocker": "blocked_notice_sync_failed" if configured else None,
        "failure_reason": _exception_reason(exception) if configured else None,
        "delivery_postcondition": {
            "satisfied": not configured,
            "blocks_delivery": False,
        },
    }


def sync_blocked_notice_after_refresh(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    agent_id: str | None,
    external_sink_delivery_authorized: bool,
    runner: CommandRunner = default_subprocess_runner,
) -> dict[str, Any]:
    invoked_registry = load_registry(registry_path)
    source_route = resolve_goal_source_runtime_route(
        registry_path=registry_path, goal_id=goal_id, registry=invoked_registry
    )
    source_registry_path = Path(str(source_route["source_registry"]))
    if source_registry_path.parent.name != ".loopx":
        return {
            "schema_version": "loopx_goal_channel_blocked_notice_delivery_v0",
            "ok": True,
            "enabled": False,
            "status": "project_binding_unavailable",
            "external_write_performed": False,
            "readback_verified": False,
            "delivery_postcondition": {"satisfied": True, "blocks_delivery": False},
        }
    runtime_root = (
        Path(runtime_root_override).expanduser().resolve()
        if runtime_root_override
        else Path(str(source_route["source_runtime_root"]))
    )
    binding_path = default_goal_channel_binding_path(source_registry_path)
    payload = read_goal_channel_binding(binding_path)
    raw_binding = binding_for_goal(payload, goal_id)
    if not blocked_notice_auto_notify_enabled(raw_binding):
        return {
            "schema_version": "loopx_goal_channel_blocked_notice_delivery_v0",
            "ok": True,
            "enabled": False,
            "status": "not_configured" if raw_binding is None else "disabled",
            "external_write_performed": False,
            "readback_verified": False,
            "delivery_postcondition": {"satisfied": True, "blocks_delivery": False},
        }
    if blocked_notice_auto_notify_enabled(raw_binding):
        resolve_extension_activation(
            LARK_EXTENSION_ID,
            state_file=default_extension_state_file(runtime_root),
            required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,),
        )
    target_name = str((raw_binding or {}).get("target_ref") or "")
    target = (
        goal_channel_target_for_name(
            read_goal_channel_targets(default_goal_channel_target_path(runtime_root)),
            target_name,
        )
        if target_name
        else None
    )
    status = collect_status(
        registry_path=source_registry_path,
        runtime_root_override=str(runtime_root),
        scan_roots=[registry_project_root(source_registry_path)],
        limit=20,
        goal_id=goal_id,
        include_public_boundary_scan=False,
    )
    quota_packet = build_quota_should_run(status, goal_id=goal_id, agent_id=agent_id)
    return deliver_blocked_notices(
        goal_id=goal_id,
        binding_path=binding_path,
        status=status,
        quota_packet=quota_packet,
        provider_target=target,
        external_sink_delivery_authorized=external_sink_delivery_authorized,
        runner=runner,
    )
