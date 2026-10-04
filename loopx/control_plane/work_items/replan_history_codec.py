"""Historical codecs for the TypeScript-owned replan history policy.

The wire input contains consumed facts, never full run records or Todo prose.
Persisted observation fingerprints and legacy timestamp parsing keep their
existing Python codecs; trigger selection and history windows have one owner.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from collections.abc import Callable, Iterable, Mapping
from typing import Any, Literal

from ..effect_runtime import MAX_REQUEST_BYTES, EffectRuntimeRejected, effect_runtime_result
from ..runtime.time import parse_timestamp
from ..todos.contract import normalize_todo_claimed_by, normalize_todo_id, normalize_todo_id_list
from ..todos.resume_planning import build_todo_resume_planning_request

REPLAN_HISTORY_NEUTRAL_CLASSIFICATIONS = {
    "quota_slot_spent", "quota_slot_voided", "delivery_completion_spend_accounted_v0",
}


def _timestamp(value: Any) -> float | None:
    parsed = parse_timestamp(value)
    return parsed.timestamp() if parsed is not None else None


def _run_fact(run: Mapping[str, Any], ack_recorded: Callable[..., bool]) -> dict[str, Any]:
    # Lazy imports keep the existing public codecs callable without an import cycle.
    from .autonomous_replan_obligation import run_history_agent_id, run_history_monitor_target
    from .progress_observation import _progress_turn_instance_id, progress_observation_from_run

    target = run_history_monitor_target(run) or {}
    event = run.get("monitor_event")
    event = event if isinstance(event, dict) else {}
    return {
        "agent_id": run_history_agent_id(run),
        "monitor_agent_id": normalize_todo_claimed_by(run_history_agent_id(run)),
        "public_agent_id": str(run.get("agent_id") or "").strip() or None,
        "classification": str(run.get("classification") or "").strip(),
        "generated_at": str(run.get("generated_at") or ""),
        "observed_at": _timestamp(run.get("generated_at")),
        "turn_id": _progress_turn_instance_id(run),
        "accepted_ack": ack_recorded(run),
        "progress": progress_observation_from_run(run),
        "monitor": {
            "target_id": str(target.get("target_id") or "").strip() or None,
            "mode": str(target.get("monitor_mode") or "").strip() or None,
            "material_change": (
                event.get("material_change")
                if isinstance(event.get("material_change"), bool) else None
            ),
            "frontier": str(target.get("frontier_identity") or "") or None,
            "todo_id": str(run.get("todo_id") or event.get("todo_id") or "").strip() or None,
            "target_key": str(run.get("target_key") or event.get("target_key") or "").strip() or None,
        },
    }


def _todo_facts(value: Any, *, include_resume: bool) -> dict[str, Any]:
    value = value if isinstance(value, dict) else {}
    raw_monitors = value.get("monitor_open_items")
    monitors = []
    for item in raw_monitors if isinstance(raw_monitors, list) else []:
        if not isinstance(item, dict):
            continue
        try:
            count = int(str(item.get("consecutive_no_change") or "0"))
        except ValueError:
            count = 0
        monitors.append({
            "id": normalize_todo_id(item.get("todo_id")),
            "claim": normalize_todo_claimed_by(item.get("claimed_by")),
            "public_claim": str(item.get("claimed_by") or "").strip() or None,
            "target": str(item.get("target_key") or item.get("todo_id") or "monitor").strip(),
            "no_change_count": count,
            "due_at": _timestamp(item.get("next_due_at")),
            "expires_at": _timestamp(item.get("expires_at")),
        })
    raw_advancements = value.get("executable_backlog_items")
    if not isinstance(raw_advancements, list):
        raw_advancements = value.get("items")
    advancements = [{
        "status": str(item.get("status") or "").strip().lower(),
        "task_class": str(item.get("task_class") or "").strip(),
        "claim": str(item.get("claimed_by") or "").strip() or None,
    } for item in (raw_advancements or []) if isinstance(item, dict)]
    # The existing resume codec is shared. TS composes the planner in-process;
    # Python must not make a second RPC or decide which monitor blocks replan.
    resume = build_todo_resume_planning_request(value) if include_resume else None
    if resume:
        for source in resume["sources"].values():
            for entry in source:
                payload = entry["payload"]
                # Resume planning tests truthiness of text, not its content.
                payload["text"] = "item" if payload.get("text") else ""
                payload["claimed_by"] = normalize_todo_claimed_by(payload.get("claimed_by"))
                condition = payload.get("resume_condition")
                condition = condition if isinstance(condition, dict) else {}
                payload["blocking_monitor_todo_id"] = normalize_todo_id(
                    payload.get("blocking_monitor_todo_id")
                    or condition.get("target_todo_id") or condition.get("target"))
                payload["successor_todo_ids"] = normalize_todo_id_list(payload.get("successor_todo_ids"))
                entry["payload"] = {key: payload[key] for key in (
                    "index", "text", "task_class", "todo_id", "claimed_by",
                    "resume_when", "resume_ready", "resume_condition",
                    "blocking_monitor_todo_id", "successor_todo_ids",
                ) if key in payload}
    return {"monitors": monitors, "advancements": advancements, "resume": resume}


def project_replan_history(
    runs: Iterable[Mapping[str, Any]] = (), *,
    operation: Literal["all", "progress", "periodic", "monitor_streak"] = "all",
    agent_id: str | None = None, agent_todos: Any = None,
    ack_recorded: Callable[..., bool] | None = None,
    neutral_classifications: set[str] | None = None,
    stall_threshold: int = 2, periodic_threshold: int = 20,
    monitor_threshold: int = 6, streak_threshold: int = 5,
    monitor_schema: str = "dead_monitor_repeat_v0",
) -> dict[str, Any] | None:
    if ack_recorded is None:
        from .autonomous_replan_ack import autonomous_replan_ack_recorded
        ack_recorded = autonomous_replan_ack_recorded
    facts = [_run_fact(row, ack_recorded) for row in runs if isinstance(row, Mapping)]
    needs_resume = operation == "all" and any(
        row["monitor"]["mode"] == "blocked_successor_wait_without_material_transition"
        for row in facts)
    params = {
        "schema_version": "replan_history_request_v0", "operation": operation,
        "runs": facts,
        "agent_id": str(agent_id or "").strip() or None,
        "monitor_agent_id": normalize_todo_claimed_by(agent_id),
        "neutral_classifications": sorted(REPLAN_HISTORY_NEUTRAL_CLASSIFICATIONS
            if neutral_classifications is None else neutral_classifications),
        "stall_threshold": max(2, int(stall_threshold)),
        "periodic_threshold": periodic_threshold, "monitor_threshold": monitor_threshold,
        "streak_threshold": streak_threshold, "monitor_schema": monitor_schema,
        "todos": _todo_facts(agent_todos, include_resume=needs_resume),
    }
    try:
        result = project_replan_request(params)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, dict) or result.get("schema_version") != "replan_history_result_v0":
        raise RuntimeError("TypeScript replan history shape mismatch")
    trigger = result.get("trigger")
    if trigger is not None and not isinstance(trigger, dict):
        raise RuntimeError("TypeScript replan history trigger mismatch")
    return trigger


def project_replan_request(params: dict[str, Any], *, method: str = "work_item.replan_history") -> Any:
    # Same serialization as the bridge. Reserve envelope overhead; the limit is
    # a wire budget, not permission to drop older evidence or duplicate TS policy.
    encoded = json.dumps(params, separators=(",", ":")).encode()
    if len(encoded) <= MAX_REQUEST_BYTES // 2:
        return effect_runtime_result(f"{method}.project", params)
    # Local same-UID runtime only. The private directory survives runtime retries
    # and is removed on success/rejection. This snapshot is never durable state.
    with TemporaryDirectory(prefix="loopx-replan-history-") as directory:
        path = Path(directory) / "request.json"
        with path.open("xb") as handle:
            path.chmod(0o600)
            handle.write(encoded)
        return effect_runtime_result(f"{method}.project_snapshot", {
            "schema_version": "replan_history_snapshot_v0",
            "path": str(path), "byte_count": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        })
