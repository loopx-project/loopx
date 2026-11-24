from __future__ import annotations

from collections.abc import Callable, Mapping
from importlib import import_module
from pathlib import Path

from ..extensions.lark.goal_channel_lifecycle import (
    blocked_notice_sync_failure,
    goal_channel_gate_sync_failure,
    sync_blocked_notice_after_refresh,
    sync_human_gate_after_refresh,
)
from ..extensions.runtime import (
    default_extension_state_file,
    resolve_extension_activation,
)
from ..history import load_registry
from ..paths import resolve_runtime_root


def lark_explore_graph_syncer(
    runtime_root_arg: str | None,
    *,
    registry_path: Path,
) -> Callable[..., Mapping[str, object]]:
    extension_runtime_root = resolve_runtime_root(
        load_registry(registry_path), runtime_root_arg
    )

    def sync(**kwargs: object) -> Mapping[str, object]:
        implementation = import_module(
            "loopx.extensions.lark.presentation.explore_results"
        )
        preview_kwargs = dict(kwargs)
        preview_kwargs["execute"] = False
        preview = dict(
            implementation.sync_issue_fix_explore_on_material_change(
                **preview_kwargs
            )
        )
        if preview.get("status") in {"not_applicable", "not_configured"}:
            return preview

        provider = import_module("loopx.extensions.lark")
        activation = resolve_extension_activation(
            str(provider.LARK_EXTENSION_ID),
            state_file=default_extension_state_file(extension_runtime_root),
            required_permissions=(str(provider.LARK_PROJECTION_SINK_PERMISSION),),
        )
        result = (
            dict(implementation.sync_issue_fix_explore_on_material_change(**kwargs))
            if kwargs.get("execute")
            else preview
        )
        result["extension_activation"] = activation
        return result

    return sync


def apply_external_sink_postcondition(
    payload: dict[str, object],
    *,
    sink_result: Mapping[str, object],
    warning: str,
    error: str,
) -> None:
    postcondition = (
        sink_result.get("delivery_postcondition")
        if isinstance(sink_result.get("delivery_postcondition"), Mapping)
        else {}
    )
    if not sink_result.get("enabled") or postcondition.get("satisfied"):
        return
    payload.setdefault("warnings", []).append(warning)
    if postcondition.get("blocks_delivery"):
        payload["ok"] = False
        payload["error"] = error


def sync_goal_channel_notifications_after_refresh(
    payload: dict[str, object],
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_id: str,
    agent_id: str | None,
) -> None:
    try:
        gate_sync = sync_human_gate_after_refresh(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            goal_id=goal_id,
            agent_id=agent_id,
            external_sink_delivery_authorized=payload["external_sink_delivery_authorized"] is True,
        )
    except Exception as error:
        gate_sync = goal_channel_gate_sync_failure(
            registry_path=registry_path,
            goal_id=goal_id,
            exception=error,
        )
    try:
        blocked_sync = sync_blocked_notice_after_refresh(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
            goal_id=goal_id,
            agent_id=agent_id,
            external_sink_delivery_authorized=payload[
                "external_sink_delivery_authorized"
            ]
            is True,
        )
    except Exception as error:
        blocked_sync = blocked_notice_sync_failure(
            registry_path=registry_path,
            goal_id=goal_id,
            exception=error,
        )
    payload["goal_channel_blocked_notice_sync"] = blocked_sync
    payload["goal_channel_gate_sync"] = gate_sync
    apply_external_sink_postcondition(
        payload,
        sink_result=gate_sync,
        warning=(
            "enabled Goal Channel human-gate delivery postcondition is "
            "unsatisfied; the notification remains retryable"
        ),
        error=(
            "enabled Goal Channel human-gate notification/readback failed "
            "after the refresh; retry it before delivery"
            + (
                "; " + gate_sync["failure_summary"]
                if gate_sync.get("failure_summary") else ""
            )
        ),
    )
