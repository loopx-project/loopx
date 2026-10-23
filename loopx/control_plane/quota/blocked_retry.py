"""Bind typed blocked Turn closeout to a durable retry or causal wait."""

from __future__ import annotations

from typing import Any

from ..todos.contract import normalize_todo_id
from ..todos.resume_condition import (
    compact_todo_resume_items,
    evaluate_todo_resume_conditions,
    normalize_supported_todo_resume_when,
)
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..work_items.autonomous_replan_ack import qualify_settlement_history


def compact_settlement_wait_todos(
    items: list[dict[str, Any]], *, todo_id: str,
) -> list[dict[str, Any]]:
    """Transport all dependency facts and the bound wait's frozen observation.

    The TS blocked-wait owner needs the complete graph for cycle diagnosis,
    but only the bound Todo's resume proof and Monitor clocks. Keep those
    observations verbatim; prose and completion instructions are not inputs.
    """
    facts = compact_todo_resume_items(items)
    bound_fields = (
        "resume_condition", "last_checked_at", "next_due_at", "result_hash",
        "material_change", "cadence", "expires_at", "watch_only", "done",
    )
    for source, row in zip((item for item in items if item.get("todo_id")), facts):
        if source.get("todo_id") == todo_id:
            row.update({field: source[field] for field in bound_fields if field in source})
    return facts


def require_blocked_retry_wait(
    todo_fields: dict[str, Any] | None,
    *,
    todo_id: str,
    observed_at: str,
    allow_turn_settlement_retry: bool = False,
) -> dict[str, Any]:
    """Transport current Todo facts; TS owns wait qualification and receipts."""
    items = []
    for section in ("agent_todos", "user_todos"):
        summary = (todo_fields or {}).get(section)
        if isinstance(summary, dict) and isinstance(summary.get("items"), list):
            items.extend(summary["items"])
    try:
        result = effect_runtime_result("quota.settlement.read", {
            "schema_version": "loopx_quota_blocked_wait_request_v0",
            "todos": compact_settlement_wait_todos(items, todo_id=todo_id),
            "todo_id": todo_id,
            "observed_at": observed_at,
            "allow_turn_settlement_retry": allow_turn_settlement_retry,
        }, large_local_snapshot=True)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(result, dict):
        raise RuntimeError("TypeScript blocked wait result must be an object")
    return result


def overlay_active_turn_retries(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    observed_at: str,
    include_unclaimed: bool = True,
) -> dict[str, Any]:
    """Project a receipt-owned wait into selection without editing hard-lease Todos.

    The committed Run is the retry authority.  Its five-minute wait is scoped
    to the same Goal/Agent/Todo and expires without a maintenance write.  A
    newer work Run on that Todo supersedes it.  The canonical Todo remains
    open and keeps its completion validator and lease rules intact.
    """

    if not agent_id:
        return status_payload
    history = status_payload.get("run_history")
    goals = history.get("goals") if isinstance(history, dict) else None
    goal = (
        next(
            (
                value
                for value in goals
                if isinstance(value, dict) and value.get("id") == goal_id
            ),
            None,
        )
        if isinstance(goals, list)
        else None
    )
    semantic = goal.get("semantic_history") if isinstance(goal, dict) else None
    runs = (
        semantic.get("active_blocked_retry_runs")
        if isinstance(semantic, dict)
        and isinstance(semantic.get("active_blocked_retry_runs"), list)
        else goal.get("latest_runs")
        if isinstance(goal, dict)
        else None
    )
    if not isinstance(runs, list):
        return status_payload
    runs = [run for run in runs if isinstance(run, dict) and run.get("agent_id") == agent_id]
    qualification = qualify_settlement_history(runs, observed_at=observed_at)
    waits: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for index, run in enumerate(runs):
        todo_id = normalize_todo_id(run.get("todo_id"))
        if not todo_id or todo_id in seen:
            continue
        classification = str(run.get("classification") or "")
        if classification.startswith(("quota_slot_", "quota_scheduler_")):
            continue
        seen.add(todo_id)
        if index in qualification.active_turn_retries:
            retry = run["blocked_retry"]
            waits[todo_id] = {
                **retry, "goal_id": goal_id, "agent_id": agent_id,
                "turn_instance_id": run["turn_instance_id"],
            }
    if not waits:
        return status_payload
    queue = status_payload.get("attention_queue")
    items = queue.get("items") if isinstance(queue, dict) else None
    if not isinstance(items, list):
        return status_payload
    # Reuse the resume owner for complete clock proof. A bare satisfied=False
    # suppresses selection but cannot qualify an automatic Vision/host wait.
    retry_items = [
        {"todo_id": todo_id, "status": "open", "role": "agent",
         "task_class": "advancement_task", "claimed_by": agent_id,
         "resume_when": normalized}
        for todo_id, retry in waits.items()
        if (normalized := normalize_supported_todo_resume_when(retry["resume_when"]))
    ]
    normalized_clock = normalize_supported_todo_resume_when(f"resume_at:{observed_at}")
    resume_conditions = (
        evaluate_todo_resume_conditions(
            retry_items, source_items=retry_items,
            evaluated_at=normalized_clock.removeprefix("resume_at:"),
        ) if retry_items and normalized_clock else {}
    )
    projected_items = []
    changed = False

    def overlay_summary(summary: Any) -> tuple[Any, bool]:
        if not isinstance(summary, dict):
            return summary, False
        projected: dict[str, Any] = {}
        summary_changed = False
        for key, value in summary.items():
            if not isinstance(value, list):
                projected[key] = value
                continue
            projected_rows = []
            for todo in value:
                retry = (
                    waits.get(normalize_todo_id(todo.get("todo_id")))
                    if isinstance(todo, dict)
                    else None
                )
                if (
                    retry is not None
                    and todo.get("status") == "open"
                    and todo.get("task_class") == "advancement_task"
                    and not todo.get("resume_when")
                    and (todo.get("claimed_by") == agent_id or
                         include_unclaimed and todo.get("claimed_by") is None)
                ):
                    resume = retry["resume_when"]
                    projected_rows.append(
                        {
                            **todo,
                            "resume_when": resume,
                            "resume_ready": False,
                            # Read-only Run provenance; never persist these fields
                            # into the canonical Todo or treat them as admission.
                            "blocked_retry": retry,
                            "next_due_at": retry["due_at"],
                            "resume_condition": {
                                # Historical Python datetime spellings may not
                                # be valid authoring syntax. Keep their original
                                # selection fence without inventing clock proof.
                                **resume_conditions.get(normalize_todo_id(todo["todo_id"]), {
                                    "kind": "resume_at", "resume_when": resume,
                                    "satisfied": False,
                                }),
                                "blocked_retry": retry,
                            },
                        }
                    )
                    summary_changed = True
                else:
                    projected_rows.append(todo)
            projected[key] = projected_rows
        return (projected if summary_changed else summary), summary_changed

    for item in items:
        if not isinstance(item, dict) or item.get("goal_id") != goal_id:
            projected_items.append(item)
            continue
        summary = item.get("agent_todos")
        projected_summary, summary_changed = overlay_summary(summary)
        asset = item.get("project_asset")
        asset_summary, asset_changed = overlay_summary(
            asset.get("agent_todos") if isinstance(asset, dict) else None
        )
        if summary_changed or asset_changed:
            changed = True
            projected_items.append(
                {
                    **item,
                    "agent_todos": projected_summary,
                    **(
                        {"project_asset": {**asset, "agent_todos": asset_summary}}
                        if asset_changed
                        else {}
                    ),
                }
            )
        else:
            projected_items.append(item)
    return (
        {
            **status_payload,
            "attention_queue": {**queue, "items": projected_items},
        }
        if changed
        else status_payload
    )


def overlay_status_turn_retries(
    history: dict[str, Any], queue: dict[str, Any], *, observed_at: str
) -> dict[str, Any]:
    """Transport the existing per-agent retry projection into status readers.

    History is the uncompressed control-plane snapshot, not the display tail.
    Qualification, supersession and clocks stay with the existing retry owner.
    An unclaimed Todo is shared across lanes: only the requesting Agent's
    admission may overlay its own retry there, never an unscoped status read.
    No canonical Todo, receipt, scheduler or quota mutation is performed here.
    """
    payload = {"run_history": history, "attention_queue": queue}
    for goal in history.get("goals") or []:
        if not isinstance(goal, dict) or not goal.get("id"):
            continue
        semantic = goal.get("semantic_history")
        runs = (
            semantic.get("active_blocked_retry_runs")
            if isinstance(semantic, dict)
            and isinstance(semantic.get("active_blocked_retry_runs"), list)
            else goal.get("latest_runs") or []
        )
        agents = dict.fromkeys(
            run["agent_id"] for run in runs
            if isinstance(run, dict) and isinstance(run.get("agent_id"), str)
            and run["agent_id"]
        )
        for agent_id in agents:
            payload = overlay_active_turn_retries(
                payload, goal_id=goal["id"], agent_id=agent_id,
                observed_at=observed_at, include_unclaimed=False,
            )
    return payload["attention_queue"]
