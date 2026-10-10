from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from ..todos.contract import normalize_todo_id_list
from .public_safety import public_safe_compact_text
from .time import chronology_key, parse_timestamp


SCHEMA_VERSION = "agent_scoped_evidence_log_v0"
READ_RECEIPT_SCHEMA_VERSION = "evidence_log_read_receipt_v0"
MAX_PROJECTED_READ_RECEIPTS = 12


def _compact_text(value: Any, *, limit: int = 220) -> str | None:
    return public_safe_compact_text(value, limit=limit)


def _normalize_event_kind(value: str) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def _safe_rollout_event_row(event: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source": "rollout_event_log",
        "recorded_at": event.get("recorded_at"),
        "event_id": event.get("event_id"),
        "event_kind": event.get("event_kind"),
    }
    for key in (
        "status",
        "agent_id",
        "todo_id",
        "classification",
        "delivery_outcome",
        "case_id",
        "run_id",
    ):
        safe = _compact_text(event.get(key), limit=180)
        if safe:
            row[key] = safe
    summary = _compact_text(event.get("summary"), limit=360)
    if summary:
        row["summary"] = summary
    for key in ("lane", "state_transition", "causality", "code_refs", "handoff"):
        value = event.get(key)
        if isinstance(value, dict):
            row[key] = value
    return row


def _safe_run_history_row(run: Mapping[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "source": "run_history",
        "recorded_at": run.get("generated_at"),
        "run_ref": run.get("generated_at"),
    }
    for key in (
        "goal_id",
        "agent_id",
        "agent_lane",
        "classification",
        "delivery_outcome",
        "delivery_batch_scale",
        "progress_scope",
        "health_check",
    ):
        safe = _compact_text(run.get(key), limit=260 if key == "health_check" else 180)
        if safe:
            row[key] = safe
    action = _compact_text(run.get("recommended_action"), limit=360)
    if action:
        row["recommended_action"] = action
    return row


def _event_matches(
    event: Mapping[str, Any],
    *,
    agent_id: str,
    todo_id: str | None,
    event_kinds: set[str],
    since: datetime | None,
) -> bool:
    if str(event.get("agent_id") or "") != agent_id:
        return False
    if todo_id and str(event.get("todo_id") or "") != todo_id:
        return False
    normalized_event_kind = _normalize_event_kind(str(event.get("event_kind") or ""))
    if not event_kinds and normalized_event_kind == "evidence_log_read":
        return False
    if event_kinds and normalized_event_kind not in event_kinds:
        return False
    if since is not None:
        recorded_at = parse_timestamp(event.get("recorded_at"))
        if recorded_at is None or recorded_at < since:
            return False
    return True


def evidence_log_read_receipt(
    event: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Project one durable public-safe evidence-log read receipt."""

    if _normalize_event_kind(str(event.get("event_kind") or "")) != (
        "evidence_log_read"
    ):
        return None
    status = str(event.get("status") or "").strip()
    if status not in {"completed", "failed"}:
        return None
    goal_id = _compact_text(event.get("goal_id"), limit=180)
    agent_id = _compact_text(event.get("agent_id"), limit=180)
    event_id = _compact_text(event.get("event_id"), limit=180)
    recorded_at = _compact_text(event.get("recorded_at"), limit=80)
    details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
    command = _compact_text(details.get("command"), limit=500)
    if not all((goal_id, agent_id, event_id, recorded_at, command)):
        return None
    read_window: dict[str, Any] = {
        "mode": _compact_text(details.get("mode"), limit=40) or "thin",
    }
    for key in ("limit", "history_limit", "rollout_limit"):
        value = details.get(key)
        if isinstance(value, int) and value >= 0:
            read_window[key] = value
    since = _compact_text(details.get("since"), limit=80)
    if since:
        read_window["since"] = since
    event_kinds = _compact_text(details.get("event_kinds"), limit=240)
    if event_kinds:
        read_window["event_kinds"] = [
            item for item in event_kinds.split(",") if item
        ]
    receipt: dict[str, Any] = {
        "schema_version": READ_RECEIPT_SCHEMA_VERSION,
        "event_id": event_id,
        "goal_id": goal_id,
        "agent_id": agent_id,
        "status": status,
        "recorded_at": recorded_at,
        "command": command,
        "read_window": read_window,
    }
    if status == "failed":
        error = _compact_text(details.get("error"), limit=240)
        if error:
            receipt["error"] = error
    required_read_id = _compact_text(details.get("required_read_id"), limit=180)
    if required_read_id:
        receipt["required_read_id"] = required_read_id
    todo_id = _compact_text(event.get("todo_id"), limit=180)
    if todo_id:
        receipt["todo_id"] = todo_id
    return receipt


def project_evidence_log_read_receipts(
    events: Iterable[Mapping[str, Any]],
    *,
    limit: int = MAX_PROJECTED_READ_RECEIPTS,
) -> list[dict[str, Any]]:
    receipts = [
        receipt
        for event in events
        if (receipt := evidence_log_read_receipt(event)) is not None
    ]
    return sorted(
        receipts,
        key=_sort_key,
        reverse=True,
    )[: max(0, int(limit))]


def _run_mentions_todo(run: Mapping[str, Any], todo_id: str) -> bool:
    structured_todo_id = str(run.get("todo_id") or "").strip()
    if structured_todo_id:
        return structured_todo_id == todo_id
    needles = (
        run.get("classification"),
        run.get("recommended_action"),
        run.get("health_check"),
    )
    return any(todo_id in normalize_todo_id_list(value) for value in needles)


def _run_matches(
    run: Mapping[str, Any],
    *,
    goal_id: str,
    agent_id: str,
    todo_id: str | None,
    since: datetime | None,
) -> bool:
    if str(run.get("goal_id") or goal_id) != goal_id:
        return False
    if str(run.get("agent_id") or "") != agent_id:
        return False
    if todo_id and not _run_mentions_todo(run, todo_id):
        return False
    if since is not None:
        recorded_at = parse_timestamp(run.get("generated_at"))
        if recorded_at is None or recorded_at < since:
            return False
    return True


def _sort_key(row: Mapping[str, Any]) -> tuple[int, datetime, str, str]:
    rank, recorded_at, raw = chronology_key(row.get("recorded_at"))
    return (rank, recorded_at, raw, str(row.get("source") or ""))


def goal_history_runs(
    history_payload: Mapping[str, Any],
    goal_id: str,
) -> list[dict[str, Any]]:
    """Select compact history rows for one goal from either supported history shape."""

    if history_payload.get("ok") is False:
        raise ValueError("history source failed")
    if "goals" in history_payload:
        goals = history_payload["goals"]
        if not isinstance(goals, list) or any(not isinstance(goal, Mapping) for goal in goals):
            raise ValueError("history.goals must be an array of objects")
        for goal in goals:
            if str(goal.get("id") or "") == goal_id:
                runs = goal.get("latest_runs")
                break
        else:
            return []
    elif "runs" in history_payload:
        runs = history_payload["runs"]
    else:
        raise ValueError("history source is missing goals/runs")
    if not isinstance(runs, list) or any(not isinstance(row, Mapping) for row in runs):
        raise ValueError("history runs must be an array of objects")
    return [dict(row) for row in runs if row.get("goal_id", goal_id) == goal_id]


def _other_agent_frontier(
    runs: Iterable[Mapping[str, Any]],
    *,
    goal_id: str,
    agent_id: str,
    limit: int,
) -> dict[str, Any]:
    latest_by_agent: dict[str, dict[str, Any]] = {}
    for run in runs:
        if str(run.get("goal_id") or goal_id) != goal_id:
            continue
        other_agent = _compact_text(run.get("agent_id"), limit=120)
        if not other_agent or other_agent == agent_id:
            continue
        current = latest_by_agent.get(other_agent)
        is_newer = current is None or chronology_key(
            run.get("generated_at")
        ) > chronology_key(current.get("recorded_at"))
        if is_newer:
            row = _safe_run_history_row(run)
            row["agent_id"] = other_agent
            latest_by_agent[other_agent] = row
    rows = sorted(latest_by_agent.values(), key=_sort_key, reverse=True)[: max(0, limit)]
    return {
        "schema_version": "other_agent_frontier_v0",
        "policy": "goal_frontier_only",
        "item_count": len(rows),
        "items": rows,
    }


def build_agent_scoped_evidence_log(
    *,
    goal_id: str,
    agent_id: str,
    rollout_events: Iterable[Mapping[str, Any]],
    history_runs: Iterable[Mapping[str, Any]],
    todo_id: str | None = None,
    since: str | None = None,
    event_kinds: Iterable[str] | None = None,
    limit: int = 24,
) -> dict[str, Any]:
    """Build a public-safe, agent-scoped ledger for replan and handoff reads."""

    safe_goal_id = _compact_text(goal_id, limit=180)
    safe_agent_id = _compact_text(agent_id, limit=180)
    if not safe_goal_id:
        raise ValueError("goal_id is required")
    if not safe_agent_id:
        raise ValueError("agent_id is required")
    safe_todo_id = _compact_text(todo_id, limit=180) if todo_id else None
    since_dt = parse_timestamp(since)
    if since and since_dt is None:
        raise ValueError(f"invalid --since timestamp: {since}")
    normalized_kinds = {
        _normalize_event_kind(kind)
        for kind in event_kinds or []
        if _normalize_event_kind(kind)
    }
    max_rows = max(0, int(limit))

    event_rows = [
        _safe_rollout_event_row(event)
        for event in rollout_events
        if _event_matches(
            event,
            agent_id=safe_agent_id,
            todo_id=safe_todo_id,
            event_kinds=normalized_kinds,
            since=since_dt,
        )
    ]
    run_list = [dict(run) for run in history_runs]
    run_rows = [
        _safe_run_history_row(run)
        for run in run_list
        if _run_matches(
            run,
            goal_id=safe_goal_id,
            agent_id=safe_agent_id,
            todo_id=safe_todo_id,
            since=since_dt,
        )
    ]
    matched_rows = [*event_rows, *run_rows]
    ledger_rows = sorted(matched_rows, key=_sort_key, reverse=True)[:max_rows]

    return {
        "ok": True,
        "schema_version": SCHEMA_VERSION,
        "mode": "thin",
        "goal_id": safe_goal_id,
        "agent_id": safe_agent_id,
        "todo_id": safe_todo_id,
        "since": since if since_dt else None,
        "event_kinds": sorted(normalized_kinds),
        "limit": max_rows,
        "matched_count": len(matched_rows),
        "ledger_count": len(ledger_rows),
        "truncated": len(matched_rows) > len(ledger_rows),
        "source_refs": [
            "rollout_event_log.public_safe_view",
            "compact_run_history.public_refs",
        ],
        "rollout_event_count": len(event_rows),
        "run_history_ref_count": len(run_rows),
        "ledger": ledger_rows,
        "other_agent_frontier": _other_agent_frontier(
            run_list,
            goal_id=safe_goal_id,
            agent_id=safe_agent_id,
            limit=3,
        ),
        "boundary": {
            "raw_task_text_recorded": False,
            "raw_logs_recorded": False,
            "raw_trajectory_recorded": False,
            "raw_session_transcript_recorded": False,
            "credential_values_recorded": False,
            "absolute_paths_recorded": False,
            "other_agent_event_stream_expanded": False,
        },
    }
