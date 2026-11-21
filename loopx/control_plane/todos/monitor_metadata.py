"""Monitor wire adaptation. State rules live in the TS Todo field planner."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


@dataclass(frozen=True)
class MonitorPollObservation:
    """One observation, reduced against the Todo state under its writer lock."""

    generated_at: str
    result_hash: str
    material_change: bool
    monitor_effect_id: str | None = None
    target_key: str | None = None
    cadence: str | None = None
    next_due_at: str | None = None


MonitorMetadataInput = dict[str, Any] | MonitorPollObservation | None


def monitor_metadata_intent(value: MonitorMetadataInput) -> dict[str, Any]:
    return (
        {"observation": asdict(value), "metadata": None}
        if isinstance(value, MonitorPollObservation)
        else {"observation": None, "metadata": value}
    )


def require_monitor_metadata_scope(
    *, monitor_metadata: dict[str, Any] | None, role: str,
    task_class: str | None, generated_at: str | None = None,
    resume_when: str | None = None, enforce_boundedness: bool = False,
) -> dict[str, Any]:
    """Create/line-codec adapter; update composes this owner in its field plan."""
    try:
        result = effect_runtime_result("todo.monitor_metadata.plan", {
            "schema_version": "loopx_todo_monitor_metadata_request_v0",
            "existing": {}, "metadata": monitor_metadata, "role": role,
            "task_class": task_class, "generated_at": generated_at,
            "resume_when": resume_when, "enforce_boundedness": enforce_boundedness,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if (not isinstance(result, dict)
        or result.get("schema_version") != "loopx_todo_monitor_metadata_result_v0"
        or not isinstance(result.get("metadata"), dict)):
        raise RuntimeError("TypeScript monitor metadata result shape mismatch")
    return result["metadata"]
