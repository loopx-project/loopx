"""Autonomous replan obligation composition and run-history entrypoints."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence, Set as AbstractSet
from typing import cast

from .autonomous_replan_policy import (
    AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION as AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION,
    AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID as AUTONOMOUS_REPLAN_DEFAULT_STOP_CONTRACT_ID,
    AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION as AUTONOMOUS_REPLAN_DIGEST_COMPATIBILITY_STOP_CONDITION,
    AUTONOMOUS_REPLAN_STALL_THRESHOLD as AUTONOMOUS_REPLAN_STALL_THRESHOLD,
    MAX_AUTONOMOUS_REPLAN_TRIGGERS as MAX_AUTONOMOUS_REPLAN_TRIGGERS,
    MONITOR_NO_CHANGE_STREAK_THRESHOLD as MONITOR_NO_CHANGE_STREAK_THRESHOLD,
    REPLAN_NOVELTY_GUIDANCE as REPLAN_NOVELTY_GUIDANCE,
    REPLAN_NOVELTY_POLICY_SCHEMA_VERSION as REPLAN_NOVELTY_POLICY_SCHEMA_VERSION,
    TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE as TODO_LIFECYCLE_SETTLEMENT_RESOLUTION_MODE,
    Payload as Payload,
    PayloadView as PayloadView,
    PublicSafeText as PublicSafeText,
    build_autonomous_replan_cli_actions as build_autonomous_replan_cli_actions,
    build_replan_novelty_policy as build_replan_novelty_policy,
    ensure_replan_novelty_policy as ensure_replan_novelty_policy,
    extra_fields_for_summary,
    project_todo_lifecycle_settlement_reentry as project_todo_lifecycle_settlement_reentry,
    recommended_action_for_summary,
    replan_obligation_id_from_packet as replan_obligation_id_from_packet,
    summarize_trigger_evidence,
    todo_actions_for_summary,
    todo_lifecycle_settlement_obligation as todo_lifecycle_settlement_obligation,
    with_replan_novelty_guidance as with_replan_novelty_guidance,
)
from .external_progress_review import external_progress_review_obligation
from .replan_history_codec import project_replan_history

AckRecorded = Callable[[dict[str, object]], bool]


def _single_public_agent_id(items: Sequence[PayloadView]) -> str | None:
    agent_ids = {
        text
        for item in items
        if isinstance(item, Mapping) and (text := str(item.get("agent_id") or "").strip())
    }
    return next(iter(agent_ids)) if len(agent_ids) == 1 else None


def run_history_agent_id(run: PayloadView) -> str | None:
    agent_id = str(run.get("agent_id") or "").strip()
    if agent_id:
        return agent_id
    monitor_target = run_history_monitor_target(run)
    if isinstance(monitor_target, Mapping):
        return str(monitor_target.get("agent_id") or "").strip() or None
    return None


def _external_review_agent_runs(
    latest_runs: Sequence[PayloadView] | None,
    *,
    neutral_classifications: AbstractSet[str],
    agent_id: str | None,
) -> list[Payload]:
    """Scope receipt joins to the same newest attributable lane as history."""

    runs = latest_runs or ()
    accountable_agent_id = str(agent_id or "").strip()
    if not accountable_agent_id:
        for run in runs:
            if not isinstance(run, Mapping):
                continue
            if str(run.get("classification") or "").strip() in neutral_classifications:
                continue
            if candidate := run_history_agent_id(run):
                accountable_agent_id = candidate
                break
    return [
        cast(Payload, run) if isinstance(run, dict) else dict(run)
        for run in runs
        if isinstance(run, Mapping)
        and (not accountable_agent_id or run_history_agent_id(run) in {None, accountable_agent_id})
    ]


def run_history_monitor_target(run: PayloadView) -> Payload | None:
    target = run.get("monitor_target")
    if isinstance(target, Mapping):
        return cast(Payload, target) if isinstance(target, dict) else dict(target)
    event = run.get("monitor_event")
    if isinstance(event, Mapping):
        nested = event.get("monitor_target")
        if isinstance(nested, Mapping):
            return cast(Payload, nested) if isinstance(nested, dict) else dict(nested)
    return None


def run_history_monitor_wait_already_acknowledged(
    latest_runs: Sequence[PayloadView] | None,
    *,
    signal_count: int,
    autonomous_replan_ack_recorded: AckRecorded,
    neutral_classifications: AbstractSet[str],
) -> bool:
    """Return true when newer monitor-poll stalls already have a compact ack run behind them."""

    runs = list(latest_runs or ())
    for run in runs[signal_count:]:
        if not isinstance(run, Mapping):
            continue
        run_dict = cast(Payload, run) if isinstance(run, dict) else dict(run)
        if autonomous_replan_ack_recorded(run_dict):
            return True
        classification = str(run.get("classification") or "").strip()
        if not classification or classification in neutral_classifications:
            continue
        if classification == "quota_monitor_poll":
            continue
        return False
    return False


def autonomous_replan_periodic_review_from_runs(
    latest_runs: Sequence[PayloadView] | None,
    *,
    agent_todos: PayloadView | None,
    autonomous_replan_ack_recorded: AckRecorded,
    neutral_classifications: AbstractSet[str],
    periodic_run_threshold: int,
    build_autonomous_replan_obligation: Callable[..., Payload | None],
) -> Payload | None:
    neutral_set = (
        neutral_classifications
        if isinstance(neutral_classifications, set)
        else set(neutral_classifications)
    )
    trigger = project_replan_history(
        latest_runs or (),
        operation="periodic",
        ack_recorded=autonomous_replan_ack_recorded,
        neutral_classifications=neutral_set,
        periodic_threshold=periodic_run_threshold,
    )
    return (
        build_autonomous_replan_obligation([trigger], agent_todos=agent_todos)
        if trigger
        else None
    )


def build_autonomous_replan_obligation(
    evidence: Sequence[PayloadView],
    *,
    agent_todos: PayloadView | None,
    public_safe_compact_text: PublicSafeText,
    autonomous_replan_schema_version: str,
    autonomous_replan_stall_threshold: int,
    dead_monitor_repeat_threshold: int,
    dead_monitor_repeat_schema_version: str,
) -> Payload | None:
    resolved_evidence: Sequence[PayloadView] = evidence
    if not resolved_evidence:
        monitor_evidence = project_replan_history(
            operation="monitor_streak",
            agent_todos=agent_todos,
            streak_threshold=MONITOR_NO_CHANGE_STREAK_THRESHOLD,
            monitor_schema=dead_monitor_repeat_schema_version,
        )
        if monitor_evidence:
            resolved_evidence = [monitor_evidence]
    if not resolved_evidence:
        return None

    summary = summarize_trigger_evidence(resolved_evidence)
    first_open: PayloadView = {}
    if isinstance(agent_todos, Mapping):
        open_items = agent_todos.get("first_open_items")
        if isinstance(open_items, list) and open_items and isinstance(open_items[0], Mapping):
            first_open = open_items[0]

    replan_agent_id = (
        next(iter(summary.agent_ids)) if len(summary.agent_ids) == 1 else None
    )
    if replan_agent_id is None and not summary.has_agent_attribution:
        replan_agent_id = str(first_open.get("claimed_by") or "").strip() or None

    guidance_actions = (
        ["set_watch_expiry", "write_blocker", "supersede_monitor", "create_successor"]
        if summary.dead_monitor
        else [
            "discover_safe_successor",
            "create_successor",
            "write_blocker",
            "record_coverage_terminal",
        ]
        if summary.blocked_successor
        else ["keep", "split", "add", "retire", "ask_decision"]
    )
    raw_threshold = (
        summary.dead_monitor.get("threshold") if summary.dead_monitor else None
    )
    stall_threshold = (
        (
            int(raw_threshold)
            if isinstance(raw_threshold, (int, float, str)) and raw_threshold
            else dead_monitor_repeat_threshold
        )
        if summary.dead_monitor
        else autonomous_replan_stall_threshold
    )
    result = build_autonomous_replan_obligation_payload(
        schema_version=autonomous_replan_schema_version,
        stall_threshold=stall_threshold,
        trigger_count=len(resolved_evidence),
        triggers=resolved_evidence,
        guidance_actions=guidance_actions,
        todo_actions=todo_actions_for_summary(
            summary,
            first_open=first_open,
            public_safe_compact_text=public_safe_compact_text,
        ),
        stop_condition=AUTONOMOUS_REPLAN_DEFAULT_STOP_CONDITION,
        recommended_action=recommended_action_for_summary(summary),
        agent_id=replan_agent_id,
        extra_fields=extra_fields_for_summary(summary),
    )
    if summary.dead_monitor:
        result["dead_monitor_detector"] = {
            "schema_version": dead_monitor_repeat_schema_version,
            "monitor_target_id": summary.dead_monitor.get("monitor_target_id"),
            "run_count": summary.dead_monitor.get("run_count"),
            "threshold": summary.dead_monitor.get("threshold"),
            "required_resolution": [
                "watch_lane_expiry",
                "blocker",
                "todo_supersede",
                "successor_runnable_todo",
            ],
        }
    return result


def build_autonomous_replan_obligation_payload(
    *,
    schema_version: str,
    stall_threshold: int,
    trigger_count: int,
    triggers: Sequence[PayloadView],
    guidance_actions: Sequence[str],
    todo_actions: Sequence[PayloadView],
    stop_condition: str,
    recommended_action: str,
    agent_id: str | None = None,
    include_agent_id: bool = False,
    rearmed_after_obligation_id: str | None = None,
    extra_fields: PayloadView | None = None,
) -> Payload:
    payload: Payload = {
        "schema_version": schema_version,
        "required": True,
        "stall_threshold": stall_threshold,
        "trigger_count": trigger_count,
        "triggers": triggers if isinstance(triggers, list) else list(triggers),
        "guidance_actions": (
            guidance_actions if isinstance(guidance_actions, list) else list(guidance_actions)
        ),
        "todo_actions": todo_actions if isinstance(todo_actions, list) else list(todo_actions),
        "stop_condition": stop_condition,
        "recommended_action": recommended_action,
    }
    if include_agent_id or agent_id is not None:
        payload["agent_id"] = agent_id
    if extra_fields:
        payload.update(extra_fields)
    if rearmed_after_obligation_id is not None:
        payload["rearmed_after_obligation_id"] = rearmed_after_obligation_id
    return ensure_replan_novelty_policy(payload)


def autonomous_replan_obligation_from_runs(
    latest_runs: Sequence[PayloadView] | None,
    *,
    agent_todos: PayloadView | None,
    agent_id: str | None = None,
    autonomous_replan_ack_recorded: AckRecorded,
    neutral_classifications: AbstractSet[str],
    build_autonomous_replan_obligation: Callable[..., Payload | None],
    autonomous_replan_stall_threshold: int,
    dead_monitor_repeat_threshold: int,
    dead_monitor_repeat_schema_version: str,
    periodic_run_threshold: int,
    external_progress_review: PayloadView | None = None,
    effective_turn_cadence: PayloadView | None = None,
) -> Payload | None:
    neutral_set = (
        neutral_classifications
        if isinstance(neutral_classifications, set)
        else set(neutral_classifications)
    )
    cadence_arg = (
        None
        if effective_turn_cadence is None
        else (
            cast(Payload, effective_turn_cadence)
            if isinstance(effective_turn_cadence, dict)
            else dict(effective_turn_cadence)
        )
    )
    trigger = project_replan_history(
        latest_runs or (),
        agent_todos=agent_todos,
        agent_id=agent_id,
        ack_recorded=autonomous_replan_ack_recorded,
        neutral_classifications=neutral_set,
        stall_threshold=autonomous_replan_stall_threshold,
        monitor_threshold=dead_monitor_repeat_threshold,
        monitor_schema=dead_monitor_repeat_schema_version,
        periodic_threshold=periodic_run_threshold,
        effective_turn_cadence=cadence_arg,
    )
    if trigger and trigger.get("kind") == "typed_progress_repeat":
        return build_autonomous_replan_obligation([trigger], agent_todos=agent_todos)

    # The TypeScript history owner keeps the established trigger rules. The
    # optional receipt source only takes precedence over monitor/periodic review
    # when a pinned assist policy has formed its own obligation.
    if external_progress_review is not None:
        todos_dict = (
            None
            if agent_todos is None
            else (cast(Payload, agent_todos) if isinstance(agent_todos, dict) else dict(agent_todos))
        )
        review_obligation = external_progress_review_obligation(
            _external_review_agent_runs(
                latest_runs,
                neutral_classifications=neutral_set,
                agent_id=agent_id,
            ),
            external_progress_review=external_progress_review,
            agent_id=agent_id,
            ack_recorded=autonomous_replan_ack_recorded,
            build_obligation=build_autonomous_replan_obligation,
            agent_todos=todos_dict,
            neutral_classifications=neutral_set,
        )
        if review_obligation:
            return review_obligation

    return (
        build_autonomous_replan_obligation([trigger], agent_todos=agent_todos)
        if trigger
        else None
    )
