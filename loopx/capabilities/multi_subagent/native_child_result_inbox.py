"""Thin Python adapter for the TypeScript native-child result inbox owner."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ...control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ingest_native_child_result(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    turn_instance_id: str, operation_id: str, outcome: str, text: str,
    goal_ref: Mapping[str, Any] | None = None,
    terminal_event_id: str,
) -> dict[str, Any]:
    try:
        return effect_runtime_result("multi_subagent.native_result_inbox", {
            "operation": "ingest", "runtime_root": str(runtime_root.resolve()),
            "goal_id": goal_id, "agent_id": agent_id,
            "goal_ref": dict(goal_ref) if goal_ref is not None else None,
            "turn_instance_id": turn_instance_id, "operation_id": operation_id,
            "outcome": outcome, "text": text, "terminal_event_id": terminal_event_id, "observed_at": _now(),
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc


def acknowledge_native_child_result(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    turn_instance_id: str, result_id: str, disposition: str,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    try:
        return effect_runtime_result("multi_subagent.native_result_inbox", {
            "operation": "acknowledge", "runtime_root": str(runtime_root.resolve()),
            "goal_id": goal_id, "agent_id": agent_id,
            "goal_ref": dict(goal_ref) if goal_ref is not None else None,
            "turn_instance_id": turn_instance_id,
            "result_id": result_id, "disposition": disposition,
            "observed_at": _now(),
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
