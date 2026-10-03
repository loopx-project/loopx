from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from .history import load_registry
from .paths import resolve_runtime_root
from .rollout_event_log import (
    append_rollout_event,
    append_rollout_event_once,
    build_rollout_event,
    rollout_event_log_path,
)


def append_cli_rollout_event(
    payload: dict[str, object],
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    event_kind: str,
    goal_ref: Mapping[str, object] | None = None,
    agent_id: str | None = None,
    todo_id: str | None = None,
    case_id: str | None = None,
    run_id: str | None = None,
    status: str | None = None,
    summary: str | None = None,
    labels: list[str] | None = None,
    artifact_refs: list[str] | None = None,
    details: dict[str, object] | None = None,
    allow_failed: bool = False,
    idempotency_fields: list[str] | None = None,
) -> dict[str, object]:
    """Append a compact rollout event for core CLI lifecycle commands.

    Rollout logging is intentionally best-effort so the diagnostic log cannot
    turn a successful state transition into a failed CLI command. Failures are
    surfaced in the command payload as compact metadata.
    """

    if not payload.get("ok") and not allow_failed:
        return payload
    goal_id = str(payload.get("goal_id") or "").strip()
    if not goal_id:
        return payload
    try:
        runtime_root_value = payload.get("runtime_root")
        if runtime_root_value:
            runtime_root = Path(str(runtime_root_value)).expanduser()
        else:
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(registry, runtime_root_arg, registry_path=registry_path)
        event = build_rollout_event(
            goal_id=goal_id,
            event_kind=event_kind,
            goal_ref=goal_ref,
            agent_id=agent_id or str(payload.get("agent_id") or "").strip() or None,
            todo_id=todo_id or str(payload.get("todo_id") or "").strip() or None,
            case_id=case_id,
            run_id=run_id,
            status=status,
            classification=str(payload.get("classification") or "").strip() or None,
            delivery_outcome=str(payload.get("delivery_outcome") or "").strip() or None,
            labels=labels,
            summary=summary,
            artifact_refs=artifact_refs,
            details=details,
        )
        log_path = rollout_event_log_path(runtime_root, goal_id)
        if idempotency_fields:
            appended, newly_appended = append_rollout_event_once(
                log_path,
                event,
                identity_fields=idempotency_fields,
            )
        else:
            appended = append_rollout_event(log_path, event)
            newly_appended = True
        rollout_event_view = {
            "schema_version": appended["schema_version"],
            "event_id": appended["event_id"],
            "event_kind": appended["event_kind"],
            "recorded_at": appended["recorded_at"],
            "status": appended.get("status"),
        }
        if idempotency_fields:
            rollout_event_view["appended"] = newly_appended
        payload["rollout_event"] = rollout_event_view
    except Exception as exc:
        payload["rollout_event_log_error"] = {
            "recorded": False,
            "error_type": type(exc).__name__,
            "message": "rollout event append failed; primary command payload remains authoritative",
        }
    return payload
