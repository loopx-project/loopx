from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import timedelta
from typing import Any

from .time import now_utc

EVENT_LEDGER_CLASSES = (
    "accounting",
    "decision",
    "evidence",
    "state",
    "work",
)
EVENT_LEDGER_PROXY_NOTE = "append-only run-history projection; compact event-class counts only"

ParseTimestamp = Callable[[Any], Any]
RunPredicate = Callable[[dict[str, Any]], bool]


def blank_event_class_counts(
    event_classes: Iterable[str] = EVENT_LEDGER_CLASSES,
) -> dict[str, int]:
    return {event_class: 0 for event_class in event_classes}


def blank_event_ledger_goal(
    goal_id: str,
    *,
    event_classes: Iterable[str] = EVENT_LEDGER_CLASSES,
) -> dict[str, Any]:
    return {
        "goal_id": goal_id,
        "events_24h": 0,
        "events_7d": 0,
        "by_class_24h": blank_event_class_counts(event_classes),
        "by_class_7d": blank_event_class_counts(event_classes),
        "latest_event_class": None,
        "latest_event_at": None,
    }


def event_ledger_event_class(
    run: dict[str, Any],
    *,
    run_has_external_evidence_watch_signal: RunPredicate,
    decision_classifications: set[str],
    evidence_classifications: set[str],
    state_classifications: set[str],
) -> str:
    classification = str(run.get("classification") or "").lower()
    if classification == "quota_slot_spent" or isinstance(run.get("quota_event"), dict):
        return "accounting"
    if (
        classification in decision_classifications
        or "operator_gate" in classification
        or "human_reward" in classification
        or "reward" in classification
        or isinstance(run.get("human_reward"), dict)
        or isinstance(run.get("operator_gate"), dict)
        or isinstance(run.get("operator_gate_resume_contract"), dict)
    ):
        return "decision"
    if classification in evidence_classifications:
        return "evidence"
    if run_has_external_evidence_watch_signal(run):
        return "evidence"
    if any(
        key in run
        for key in (
            "active_priorities",
            "active_task_count",
            "artifact",
            "artifacts",
            "cache_check",
            "controller_readiness",
            "project_map",
        )
    ):
        return "evidence"
    if classification in state_classifications or classification.endswith("_refreshed"):
        return "state"
    return "work"


def build_event_ledger_summary(
    history: dict[str, Any],
    *,
    parse_timestamp: ParseTimestamp,
    event_class_for_run: Callable[[dict[str, Any]], str],
    event_classes: tuple[str, ...] = EVENT_LEDGER_CLASSES,
    proxy_note: str = EVENT_LEDGER_PROXY_NOTE,
) -> dict[str, Any]:
    now = now_utc()
    cutoff_24h = now - timedelta(hours=24)
    cutoff_7d = now - timedelta(days=7)
    totals = {
        "events_24h": 0,
        "events_7d": 0,
        "by_class_24h": blank_event_class_counts(event_classes),
        "by_class_7d": blank_event_class_counts(event_classes),
    }
    goals: dict[str, dict[str, Any]] = {}
    sample_count = 0

    for run in history.get("runs") or []:
        if not isinstance(run, dict):
            continue
        sample_count += 1
        generated_at = parse_timestamp(run.get("generated_at"))
        if generated_at is None:
            continue
        event_class = event_class_for_run(run)
        goal_id = str(run.get("goal_id") or "unknown-goal")
        goal = goals.setdefault(
            goal_id,
            blank_event_ledger_goal(goal_id, event_classes=event_classes),
        )
        latest_event_at = parse_timestamp(goal.get("latest_event_at"))
        if latest_event_at is None or generated_at > latest_event_at:
            goal["latest_event_class"] = event_class
            goal["latest_event_at"] = generated_at.isoformat()
        if generated_at >= cutoff_7d:
            totals["events_7d"] += 1
            totals["by_class_7d"][event_class] += 1
            goal["events_7d"] += 1
            goal["by_class_7d"][event_class] += 1
        if generated_at >= cutoff_24h:
            totals["events_24h"] += 1
            totals["by_class_24h"][event_class] += 1
            goal["events_24h"] += 1
            goal["by_class_24h"][event_class] += 1

    goal_rows = sorted(
        goals.values(),
        key=lambda item: (
            item["events_24h"],
            item["events_7d"],
            item["goal_id"],
        ),
        reverse=True,
    )
    return {
        "available": True,
        "source": "run_history",
        "generated_at": now.isoformat(),
        "sample_run_count": sample_count,
        "proxy_note": proxy_note,
        "event_classes": list(event_classes),
        "totals": totals,
        "goals": goal_rows,
    }
