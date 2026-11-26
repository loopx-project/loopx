"""Status runtime summaries inside the `status` bounded context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

from ..runtime.decision_freshness import (
    DECISION_FRESHNESS_CLASSIFICATION_PREFIXES,
    DECISION_FRESHNESS_ITEM_LIMIT,
    DECISION_FRESHNESS_PROXY_NOTE,
    DECISION_FRESHNESS_WINDOW_DAYS,
    build_decision_freshness_summary,
    decision_event_kinds as _decision_event_kinds_read_model,
)
from ..runtime.event_ledger import (
    blank_event_class_counts,
    build_event_ledger_summary,
    event_ledger_event_class as _event_ledger_event_class_read_model,
)
from ..runtime.promotion_readiness import build_promotion_readiness_summary
from ..runtime.run_history import build_run_history
from ..quota.usage_summary import build_usage_summary
from ..todos.todo_index import EventsForGoal, build_todo_index


StatusCallback = Callable[..., Any]


@dataclass(frozen=True)
class StatusRuntimeSummaryContext:
    latest_run: StatusCallback
    goal_lifecycle_fields: StatusCallback
    subagent_activity_for_goal: StatusCallback
    compact_run: StatusCallback
    quota_status: StatusCallback
    parse_timestamp: StatusCallback
    run_has_external_evidence_watch_signal: StatusCallback
    decision_classifications: set[str]
    evidence_classifications: set[str]
    state_classifications: set[str]
    promotion_readiness_classifications: Iterable[str]
    add_promotion_readiness_freshness: StatusCallback
    latest_promotion_readiness_event: StatusCallback
    promotion_readiness_freshness_hours: int
    promotion_readiness_proxy_note: str
    public_safe_compact_text: StatusCallback
    decision_freshness_classification_prefixes: tuple[str, ...] = DECISION_FRESHNESS_CLASSIFICATION_PREFIXES
    decision_freshness_window_days: int = DECISION_FRESHNESS_WINDOW_DAYS
    decision_freshness_item_limit: int = DECISION_FRESHNESS_ITEM_LIMIT
    decision_freshness_proxy_note: str = DECISION_FRESHNESS_PROXY_NOTE


def event_ledger_event_class(
    run: dict[str, Any],
    *,
    context: StatusRuntimeSummaryContext,
) -> str:
    return _event_ledger_event_class_read_model(
        run,
        run_has_external_evidence_watch_signal=context.run_has_external_evidence_watch_signal,
        decision_classifications=context.decision_classifications,
        evidence_classifications=context.evidence_classifications,
        state_classifications=context.state_classifications,
    )


def decision_event_kinds(
    run: dict[str, Any],
    *,
    context: StatusRuntimeSummaryContext,
) -> list[str]:
    return _decision_event_kinds_read_model(
        run,
        decision_classifications=context.decision_classifications,
        classification_prefixes=context.decision_freshness_classification_prefixes,
    )


def build_status_runtime_summaries(
    *,
    history: dict[str, Any],
    queue: dict[str, Any],
    runtime_root: Path,
    goal_id_filter: str | None,
    display_limit: int,
    todo_index_limit: int,
    context: StatusRuntimeSummaryContext,
    events_for_goal: EventsForGoal | None = None,
    recent_run_limit: int | None = None,
    include_goal_subagent_configuration: bool = False,
) -> dict[str, Any]:
    def event_class_for_run(run: dict[str, Any]) -> str:
        return event_ledger_event_class(run, context=context)

    def decision_kinds(run: dict[str, Any]) -> list[str]:
        return decision_event_kinds(run, context=context)

    return {
        "run_history": build_run_history(
            history,
            latest_run=context.latest_run,
            goal_lifecycle_fields=context.goal_lifecycle_fields,
            subagent_activity_for_goal=context.subagent_activity_for_goal,
            compact_run=context.compact_run,
            quota_status=context.quota_status,
            display_limit=display_limit,
            recent_run_limit=recent_run_limit,
            include_goal_subagent_configuration=(
                include_goal_subagent_configuration
            ),
        ),
        "event_ledger_summary": build_event_ledger_summary(
            history,
            parse_timestamp=context.parse_timestamp,
            event_class_for_run=event_class_for_run,
        ),
        "promotion_readiness_summary": build_promotion_readiness_summary(
            history,
            parse_timestamp=context.parse_timestamp,
            readiness_classifications=context.promotion_readiness_classifications,
            add_promotion_readiness_freshness=context.add_promotion_readiness_freshness,
            latest_promotion_readiness_event=lambda root: context.latest_promotion_readiness_event(
                root,
                goal_id=goal_id_filter,
            ),
            freshness_hours=context.promotion_readiness_freshness_hours,
            runtime_root=runtime_root,
            proxy_note=context.promotion_readiness_proxy_note,
        ),
        "decision_freshness_summary": build_decision_freshness_summary(
            history,
            parse_timestamp=context.parse_timestamp,
            decision_event_kinds=decision_kinds,
            event_class_for_run=event_class_for_run,
            blank_event_class_counts=blank_event_class_counts,
            window_days=context.decision_freshness_window_days,
            item_limit=context.decision_freshness_item_limit,
            proxy_note=context.decision_freshness_proxy_note,
        ),
        "usage_summary": build_usage_summary(
            history,
            parse_timestamp=context.parse_timestamp,
            runtime_root=runtime_root,
        ),
        "todo_index": build_todo_index(
            queue=queue,
            history=history,
            runtime_root=runtime_root,
            public_safe_compact_text=context.public_safe_compact_text,
            limit=todo_index_limit,
            events_for_goal=events_for_goal,
        ),
    }
