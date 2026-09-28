"""Bind typed blocked Turn closeout to a durable retry or causal wait."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from ..todos.contract import normalize_todo_id
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

BLOCKED_RETRY_SCHEMA_VERSION = "quota_blocked_retry_v0"
MIN_RETRY_SECONDS = 60
MAX_RETRY_SECONDS = 30 * 60
TURN_SETTLEMENT_RETRY_SECONDS = 5 * 60


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
            "todos": items,
            "todo_id": todo_id,
            "observed_at": observed_at,
            "allow_turn_settlement_retry": allow_turn_settlement_retry,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from exc
    if not isinstance(result, dict):
        raise RuntimeError("TypeScript blocked wait result must be an object")
    return result


def active_turn_retry_for_run(
    run: dict[str, Any], *, observed_at: str
) -> dict[str, Any] | None:
    """Accept only the exact active, receipt-owned retry on a blocked work Run."""

    todo_id = normalize_todo_id(run.get("todo_id"))
    retry = run.get("blocked_retry")
    progress = run.get("progress_observation")
    if (
        not todo_id
        or not isinstance(retry, dict)
        or retry.get("schema_version") != BLOCKED_RETRY_SCHEMA_VERSION
        or retry.get("source") != "turn_settlement"
        or retry.get("todo_id") != todo_id
        or run.get("delivery_outcome") != "outcome_gap"
        or not isinstance(progress, dict)
        or progress.get("result_class") != "blocked"
        or not run.get("turn_instance_id")
    ):
        return None
    try:
        now = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        observed = datetime.fromisoformat(
            str(retry["observed_at"]).replace("Z", "+00:00")
        )
        due = datetime.fromisoformat(str(retry["due_at"]).replace("Z", "+00:00"))
        delay = (due - observed).total_seconds()
    except (KeyError, ValueError, TypeError):
        return None
    if (
        now.tzinfo is None
        or observed.tzinfo is None
        or due.tzinfo is None
        or not MIN_RETRY_SECONDS <= delay <= MAX_RETRY_SECONDS
        or due <= now
        or retry.get("resume_when") != f"resume_at:{retry['due_at']}"
    ):
        return None
    return retry


def overlay_active_turn_retries(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    observed_at: str,
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
    waits: dict[str, dict[str, Any]] = {}
    seen: set[str] = set()
    for run in runs:
        if not isinstance(run, dict) or run.get("agent_id") != agent_id:
            continue
        todo_id = normalize_todo_id(run.get("todo_id"))
        if not todo_id or todo_id in seen:
            continue
        classification = str(run.get("classification") or "")
        if classification.startswith(("quota_slot_", "quota_scheduler_")):
            continue
        seen.add(todo_id)
        retry = active_turn_retry_for_run(run, observed_at=observed_at)
        if retry is not None:
            waits[todo_id] = retry
    if not waits:
        return status_payload
    queue = status_payload.get("attention_queue")
    items = queue.get("items") if isinstance(queue, dict) else None
    if not isinstance(items, list):
        return status_payload
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
                ):
                    resume = retry["resume_when"]
                    projected_rows.append(
                        {
                            **todo,
                            "resume_when": resume,
                            "resume_ready": False,
                            "resume_condition": {
                                "kind": "resume_at",
                                "resume_when": resume,
                                "satisfied": False,
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
