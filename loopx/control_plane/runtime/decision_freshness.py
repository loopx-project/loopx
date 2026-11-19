from __future__ import annotations

from typing import Any, Callable, cast

from datetime import datetime, timedelta

from .time import now_utc

DECISION_FRESHNESS_WINDOW_DAYS = 7
DECISION_FRESHNESS_ITEM_LIMIT = 12
DECISION_FRESHNESS_WARNING_ITEM_LIMIT = 3
DECISION_FRESHNESS_PROXY_NOTE = (
    "checkpointed decision freshness projection; rebase old decisions at the decision point before reuse"
)
DECISION_FRESHNESS_WARNING_MESSAGE = (
    "decision-point rebase required before reusing sampled reward/gate state; "
    "refresh registry, ACTIVE_GOAL_STATE, quota, policy, and run status first"
)
DECISION_FRESHNESS_CLASSIFICATION_PREFIXES = (
    "human_reward",
    "reward_overlay",
)

ParseTimestamp = Callable[[Any], Any]
DecisionKinds = Callable[[dict[str, Any]], list[str]]
EventClassForRun = Callable[[dict[str, Any]], str]
BlankEventClassCounts = Callable[[], dict[str, int]]


def decision_event_kinds(
    run: dict[str, Any],
    *,
    decision_classifications: set[str],
    classification_prefixes: tuple[str, ...] = DECISION_FRESHNESS_CLASSIFICATION_PREFIXES,
) -> list[str]:
    kinds: list[str] = []
    if isinstance(run.get("human_reward"), dict):
        kinds.append("human_reward")
    if isinstance(run.get("operator_gate"), dict):
        kinds.append("operator_gate")
    if isinstance(run.get("operator_gate_resume_contract"), dict):
        kinds.append("operator_gate_resume_contract")

    classification = str(run.get("classification") or "").lower()
    if not kinds and (
        classification in decision_classifications
        or classification.startswith(classification_prefixes)
    ):
        kinds.append("decision_classification")
    return kinds


def decision_freshness_reason(*, stale_by_age: bool, newer_event_count: int) -> str:
    if stale_by_age and newer_event_count:
        return "decision older than freshness window and newer sampled events exist; rebase at decision point"
    if stale_by_age:
        return "decision older than freshness window; rebase at decision point"
    if newer_event_count:
        return "newer sampled events exist after decision; rebase at decision point"
    return "no newer sampled events inside freshness window"


def build_decision_freshness_summary(
    history: dict[str, Any],
    *,
    parse_timestamp: ParseTimestamp,
    decision_event_kinds: DecisionKinds,
    event_class_for_run: EventClassForRun,
    blank_event_class_counts: BlankEventClassCounts,
    window_days: int = DECISION_FRESHNESS_WINDOW_DAYS,
    item_limit: int = DECISION_FRESHNESS_ITEM_LIMIT,
    proxy_note: str = DECISION_FRESHNESS_PROXY_NOTE,
) -> dict[str, Any]:
    now = now_utc()
    cutoff = now - timedelta(days=window_days)
    runs = [run for run in history.get("runs") or [] if isinstance(run, dict)]
    items: list[dict[str, Any]] = []
    stale_count = 0
    rebase_required_count = 0
    fresh_count = 0

    indexed_runs: list[tuple[dict[str, Any], datetime, str]] = []
    for run in runs:
        generated_at = parse_timestamp(run.get("generated_at"))
        if generated_at is None:
            continue
        indexed_runs.append((run, generated_at, str(run.get("goal_id") or "unknown-goal")))

    for run, decision_at, goal_id in indexed_runs:
        for decision_kind in decision_event_kinds(run):
            newer_class_counts = blank_event_class_counts()
            newer_event_count = 0
            for other_run, other_at, other_goal_id in indexed_runs:
                if other_goal_id != goal_id or other_at <= decision_at or other_at < cutoff:
                    continue
                newer_event_count += 1
                newer_class_counts[event_class_for_run(other_run)] += 1

            stale_by_age = decision_at < cutoff
            if stale_by_age:
                stale_count += 1
            requires_rebase = stale_by_age or newer_event_count > 0
            if requires_rebase:
                rebase_required_count += 1
            else:
                fresh_count += 1
            if stale_by_age:
                freshness_state = "stale_rebase_required"
            elif newer_event_count:
                freshness_state = "rebase_required"
            else:
                freshness_state = "fresh"

            items.append(
                {
                    "goal_id": goal_id,
                    "decision_kind": decision_kind,
                    "decision_at": decision_at.isoformat(),
                    "classification": run.get("classification"),
                    "age_days": round(max(0.0, (now - decision_at).total_seconds() / 86400), 2),
                    "stale_by_age": stale_by_age,
                    "newer_event_count_7d": newer_event_count,
                    "newer_event_classes_7d": newer_class_counts,
                    "freshness_state": freshness_state,
                    "requires_decision_point_rebase": requires_rebase,
                    "reason": decision_freshness_reason(
                        stale_by_age=stale_by_age,
                        newer_event_count=newer_event_count,
                    ),
                }
            )

    items.sort(
        key=lambda item: (
            1 if item["requires_decision_point_rebase"] else 0,
            item["age_days"],
            item["newer_event_count_7d"],
            item["decision_at"],
        ),
        reverse=True,
    )
    return {
        "available": True,
        "source": "run_history",
        "generated_at": now.isoformat(),
        "sample_run_count": len(runs),
        "window_days": window_days,
        "proxy_note": proxy_note,
        "summary": {
            "decision_count": len(items),
            "stale_count": stale_count,
            "rebase_required_count": rebase_required_count,
            "fresh_count": fresh_count,
        },
        "items": items[:item_limit],
    }


def decision_freshness_warning(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    item_limit: int = DECISION_FRESHNESS_WARNING_ITEM_LIMIT,
    message: str = DECISION_FRESHNESS_WARNING_MESSAGE,
) -> dict[str, Any] | None:
    freshness = (
        cast(dict[str, Any], status_payload.get("decision_freshness_summary"))
        if isinstance(status_payload.get("decision_freshness_summary"), dict)
        else {}
    )
    raw_items = cast(list[Any], freshness.get("items")) if isinstance(freshness.get("items"), list) else []
    items: list[dict[str, Any]] = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        if str(item.get("goal_id") or "") != goal_id:
            continue
        if item.get("requires_decision_point_rebase") is not True:
            continue
        items.append(
            {
                "goal_id": item.get("goal_id"),
                "decision_kind": item.get("decision_kind"),
                "freshness_state": item.get("freshness_state"),
                "decision_at": item.get("decision_at"),
                "classification": item.get("classification"),
                "age_days": item.get("age_days"),
                "newer_event_count_7d": item.get("newer_event_count_7d"),
                "reason": item.get("reason"),
            }
        )

    if not items:
        return None
    summary = cast(dict[str, Any], freshness.get("summary")) if isinstance(freshness.get("summary"), dict) else {}
    return {
        "source": freshness.get("source") or "run_history",
        "window_days": freshness.get("window_days"),
        "message": message,
        "rebase_required_count": len(items),
        "global_rebase_required_count": summary.get("rebase_required_count"),
        "global_stale_count": summary.get("stale_count"),
        "items": items[:item_limit],
    }
