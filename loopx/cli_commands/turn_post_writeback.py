"""Optional Turn hooks read original receipts, never the current Goal frontier."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..capabilities.periodic_report.post_writeback_hook import (
    build_periodic_report_post_writeback_projection,
    periodic_report_post_writeback_hooks_for_goal,
)
from ..capabilities.periodic_report.todo_source import read_report_source_history
from ..control_plane.effect_runtime import (
    CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS,
    effect_runtime_result,
)
from ..control_plane.quota.settlement import SettlementIdentity, read_heartbeat_settlement
from .post_writeback import (
    dispatch_committed_cli_post_writeback_hooks,
    post_writeback_source_failure,
)


def drain_committed_turn_post_writeback_hooks(
    *,
    registry_path: Path,
    runtime_root: Path,
    identity: SettlementIdentity,
    goal_ref: Mapping[str, object] | None,
    available_capabilities: list[str] | None,
) -> dict[str, Any]:
    """Drain committed sources on first return and replay without primary effects."""
    try:
        hooks = periodic_report_post_writeback_hooks_for_goal(
            registry_path=registry_path, goal_id=identity.goal_id, runtime_root=runtime_root
        )
    except Exception:  # Optional subscription reads grant no primary failure.
        return {}
    if not hooks:
        return {}
    try:
        readback = read_heartbeat_settlement(
            runtime_root, goal_id=identity.goal_id, agent_id=identity.agent_id,
            todo_id=identity.todo_id, turn_instance_id=identity.turn_instance_id,
            replan_obligation_id=identity.replan_obligation_id,
            registry_path=registry_path, goal_ref=goal_ref,
        )
        run = readback.writeback_run if readback is not None else None
        if run is None:
            return {}
        source_runs = read_report_source_history(
            runtime_root=runtime_root, goal_id=identity.goal_id, run=run,
        )
    except Exception:  # Optional source recovery must preserve primary settlement.
        return {"post_writeback_hooks": post_writeback_source_failure(hooks)}

    def dispatch(event_kind: str, request: dict[str, Any]) -> dict[str, Any] | None:
        try:
            source = effect_runtime_result(
                "coordination.local_authority.todo_source",
                {
                    "schema_version": "loopx_local_coordination_todo_source_request_v0",
                    "runtime_root": str(runtime_root), "goal_id": identity.goal_id,
                    **request,
                },
                timeout=CANONICAL_AUTHORITY_READ_TIMEOUT_SECONDS,
                large_local_snapshot=True,
            )
            if source.get("status") == "missing":
                return None
            if source.get("status") != "loaded":
                raise ValueError("original canonical Todo source is unavailable")
            completion = source.get("completion") or {}
            committed_at = str(
                completion.get("completed_at") if event_kind == "todo_complete"
                else run.get("generated_at")
            )
            provider_source = source["source"]
            state_version = (
                str(provider_source["provider_revision"])
                if event_kind == "todo_complete" else committed_at
            )

            def projection_builder(**kwargs: Any) -> Mapping[str, object]:
                return build_periodic_report_post_writeback_projection(
                    **kwargs, source_todos=source, source_runs=source_runs,
                )

            return dispatch_committed_cli_post_writeback_hooks(
                payload={**run, "available_capabilities": available_capabilities},
                registry_path=registry_path, runtime_root_arg=str(runtime_root),
                goal_id=identity.goal_id, event_kind=event_kind,
                identity={
                    "agent_id": identity.agent_id, "todo_id": identity.todo_id,
                    "turn_instance_id": identity.turn_instance_id,
                    "effect_id": (
                        provider_source["operation_id"]
                        if event_kind == "todo_complete" else identity.effect_id
                    ),
                },
                state_version=state_version, committed_at=committed_at,
                receipt_id=completion.get("completion_receipt_id"),
                hooks=hooks, projection_builder=projection_builder,
            )
        except Exception:  # No retry may repeat a primary write or spend.
            return post_writeback_source_failure(hooks)

    refresh = (
        dispatch("refresh_state", {"source": run["todo_source"]})
        if isinstance(run.get("todo_source"), Mapping)
        else post_writeback_source_failure(hooks)
    )
    result = {"post_writeback_hooks": refresh}
    if identity.todo_id and isinstance(run.get("todo_source"), Mapping):
        terminal = dispatch("todo_complete", {"terminal": {
            "todo_id": identity.todo_id, "agent_id": identity.agent_id,
            "completion_turn_key": identity.turn_instance_id,
            "source_authority": run["todo_source"].get("source_authority"),
            "store_identity": run["todo_source"].get("store_identity"),
        }})
        if terminal is not None:
            result["terminal_post_writeback_hooks"] = terminal
    return result
