from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import re
from typing import Any

from .control_plane.work_items.replan_history_codec import (
    REPLAN_HISTORY_NEUTRAL_CLASSIFICATIONS as _REPLAN_HISTORY_NEUTRAL_CLASSIFICATIONS,
)

from .control_plane import compact_control_plane_policy
from .control_plane.effect_runtime import effect_runtime_request_scope
from .control_plane.status.collection import (
    StatusCollectionContext,
    collect_status as _collect_status_read_model,
)
from .control_plane.status.active_state_projection import (
    STATE_EVENT_LOG_BASENAME as STATE_EVENT_LOG_BASENAME,
)
from .control_plane.status.contract_projection import (
    STATUS_CONTRACT_RELOAD_HINT as STATUS_CONTRACT_RELOAD_HINT,
)
from .control_plane.status.goal_attention_projection import (
    PLANNED_CONTROLLER_OPT_IN_RECOMMENDED_ACTION as PLANNED_CONTROLLER_OPT_IN_RECOMMENDED_ACTION,
)
# Refs #4447: one definition for this vocabulary. The control_plane projection
# owns it because it feeds the monitor/attention read models; this module keeps
# re-exporting each name for existing callers instead of restating its value.
from .control_plane.status.monitor_display_projection import (
    MONITOR_DISPLAY_FALLBACK_ACTION as MONITOR_DISPLAY_FALLBACK_ACTION,
    MONITOR_DISPLAY_STOP_CONDITION as MONITOR_DISPLAY_STOP_CONDITION,
    MONITOR_SIGNAL_WAITING_ON,
)
from .control_plane.status.registry_health_projection import (
    SOURCE_REGISTRY_SHADOW_FINDINGS,
)
from .control_plane.status.runtime_summaries import (
    StatusRuntimeSummaryContext,
    build_status_runtime_summaries as _build_status_runtime_summaries_read_model,
)
from .contract import check_contract
from .doctor import (
    PROMOTION_READINESS_CLASSIFICATIONS,
    PROMOTION_READINESS_FRESHNESS_HOURS,
    add_promotion_readiness_freshness,
    latest_promotion_readiness_event,
)
from .execution_profile import (
    compact_execution_profile,
    execution_profile_outcome_floor,
)
from .control_plane.goals.goal_channel_projection import build_goal_channel_projection
from .extensions.lark.goal_channel_notification import (
    build_goal_channel_notification_projection,
)
from .handoff_budget import handoff_budget_contract
from .history import collect_status_history, load_registry
from .history import STATUS_NEUTRAL_CLASSIFICATIONS as HISTORY_STATUS_NEUTRAL_CLASSIFICATIONS
from .interface_budget import interface_budget_cadence_for_runs
from .long_task_cadence import build_long_task_cadence_hint
from .orchestration import compact_orchestration_policy
from .capabilities.multi_subagent.native_child_receipts import (
    latest_native_child_activity,
    load_native_child_activity,
)
from .paths import resolve_runtime_root
from .control_plane.work_items.task_graph import (
    build_task_graph_projection as _build_task_graph_projection_read_model,
)
from .control_plane.work_items.project_asset import (
    TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION as TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION,
    TODO_PROJECTION_VIEW_SCHEMA_VERSION as TODO_PROJECTION_VIEW_SCHEMA_VERSION,
    attach_active_state_project_asset_fields as _attach_active_state_project_asset_fields,
    build_project_asset,
    enrich_project_asset as _enrich_project_asset_read_model,
    project_asset_handoff_check_projection,
    project_asset_latest_validation,
    project_asset_quota_state,
    project_asset_quota_summary,
    project_asset_summary_is_public_safe as project_asset_summary_is_public_safe,
    project_asset_todo_projection_gap,
    project_asset_user_todo_open_count,
)
from .control_plane.todos.completed_archive import completed_todo_archive_warning
from .control_plane.handoff.project_handoff import (
    project_asset_handoff_readiness as _project_asset_handoff_readiness_read_model,
    project_asset_handoff_state as _project_asset_handoff_state_read_model,
)
from .control_plane.work_items.autonomous_candidates import (
    MAX_AUTONOMOUS_TODO_CANDIDATES as _MAX_AUTONOMOUS_TODO_CANDIDATES,
)
from .control_plane.goals.active_state_metadata import (
    parse_state_frontmatter,
)
from .control_plane.todos.active_state_todos import (
    MONITOR_WRITEBACK_CONTRACT_SCHEMA_VERSION as _MONITOR_WRITEBACK_CONTRACT_SCHEMA_VERSION,
    active_state_todo_fields as _active_state_todo_fields_read_model,
)
from .control_plane.todos.active_state_todo_parser import (
    parse_active_state_todos,
)
from .control_plane.work_items.attention_item import (
    attention_item as _attention_item_read_model,
)
from .control_plane.work_items.attention_queue import (
    AttentionQueueContext,
    build_attention_queue as _build_attention_queue_read_model,
)
from .control_plane.work_items.autonomous_replan_ack import (
    AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW,
    AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK as _AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK,
    compact_autonomous_replan_ack,
)
from .control_plane.work_items.autonomous_replan_obligation import (
    AUTONOMOUS_REPLAN_STALL_THRESHOLD as _AUTONOMOUS_REPLAN_STALL_THRESHOLD_READ_MODEL,
    MAX_AUTONOMOUS_REPLAN_TRIGGERS as _MAX_AUTONOMOUS_REPLAN_TRIGGERS_READ_MODEL,
)
from .control_plane.work_items.backlog_hygiene import (
    MAX_BACKLOG_HYGIENE_EVIDENCE_ITEMS as _MAX_BACKLOG_HYGIENE_EVIDENCE_ITEMS_READ_MODEL,
)
from .control_plane.work_items.delivery_history import compact_delivery_binding, project_delivery_history
from .control_plane.runtime.run_compaction import (
    RUN_BASE_COMPACT_FIELDS,
    attach_run_summary_projections as _attach_run_summary_projections_read_model,
    compact_controller_readiness,
    compact_human_reward,
    compact_operator_gate,
    compact_operator_gate_resume_contract,
    compact_run_base as _compact_run_base_read_model,
)
from .control_plane.runtime.status_classifications import (
    BLOCKING_CLASSIFICATIONS,
    CODEX_READY_CLASSIFICATIONS,
    DREAMING_ADVISORY_CLASSIFICATIONS,  # noqa: F401
    HANDOFF_READY_CLASSIFICATIONS,
    USER_OR_CONTROLLER_CLASSIFICATIONS,
)
from .control_plane.runtime.public_safety import (
    public_safe_compact_text,
)
from .control_plane.runtime.time import parse_timestamp
from .control_plane.runtime.decision_freshness import (
    DECISION_FRESHNESS_CLASSIFICATION_PREFIXES,
    DECISION_FRESHNESS_ITEM_LIMIT,
    DECISION_FRESHNESS_PROXY_NOTE,
    DECISION_FRESHNESS_WINDOW_DAYS,
)
from .control_plane.runtime.promotion_readiness import (
    PROMOTION_READINESS_PROXY_NOTE,
)
from .control_plane.handoff.handoff_runs import (
    is_custom_post_handoff_work_run as _is_custom_post_handoff_work_run_read_model,
    is_handoff_ready_run as _is_handoff_ready_run_read_model,
    run_has_external_evidence_watch_signal as _run_has_external_evidence_watch_signal_read_model,
)
from .control_plane.goals.global_registry_shadow import (
    attach_global_registry_shadow_finding,
)
from .control_plane.goals.path_resolution import resolve_goal_local_path
from .control_plane.goals.goal_channel import (
    attach_goal_channel_projection as _attach_goal_channel_projection_read_model,
)
from .control_plane.goals.goal_vision import (
    compact_goal_vision_packet as _compact_goal_vision_packet_read_model,
)
from .control_plane.runtime.session_runtime import (
    compact_session_runtime_projection_from_run,
)
from .control_plane.agents.subagent_activity import (
    MAX_SUBAGENT_ACTIVITY_ITEMS,
    compact_subagent_run,
    subagent_activity_for_goal,
)
from .control_plane.agents.management_projection import (
    build_agent_management_projection as _build_agent_management_projection_read_model,
)
from .control_plane.runtime.agent_scoped_evidence_log import (
    MAX_PROJECTED_READ_RECEIPTS,
    project_evidence_log_read_receipts,
)
from .control_plane.runtime.stale_latest_run import (
    stale_latest_run_projection_warning as _stale_latest_run_projection_warning_read_model,
)
from .control_plane.todos.todo_summary import (
    MAX_DEFERRED_TODO_VISIBILITY_ITEMS as _TODO_SUMMARY_MAX_DEFERRED_TODO_VISIBILITY_ITEMS,
    MAX_DEPENDENCY_BLOCKERS as _TODO_SUMMARY_MAX_DEPENDENCY_BLOCKERS,
    MAX_MONITOR_DUE_ITEMS as _TODO_SUMMARY_MAX_MONITOR_DUE_ITEMS,
    MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS as _TODO_SUMMARY_MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS,
    MAX_PROJECT_ASSET_TODO_ITEMS as _TODO_SUMMARY_MAX_PROJECT_ASSET_TODO_ITEMS,
    MAX_STATUS_TODOS_PER_ROLE as _TODO_SUMMARY_MAX_STATUS_TODOS_PER_ROLE,
    MAX_TODO_VISIBILITY_LANE_ITEMS as _TODO_SUMMARY_MAX_TODO_VISIBILITY_LANE_ITEMS,
    active_state_todo_attention_item as _active_state_todo_attention_item_read_model,
    active_next_action_todo_ids,
    attach_dependency_blockers,
    compact_todo_group as compact_todo_group,
    compact_todo_item as compact_todo_item,
    first_open_todo_text,
    open_todo_items,
    project_asset_todo_summary,
    sync_connected_attention_action_from_todos as _sync_connected_attention_action_from_todos_read_model,
    todo_lane_items as todo_lane_items,
    todo_item_is_deferred as todo_item_is_deferred,
    todo_item_is_due_monitor as todo_item_is_due_monitor,
    todo_item_missing_monitor_schedule as todo_item_missing_monitor_schedule,
    todo_item_next_due_at as todo_item_next_due_at,
    todo_item_task_class,
    todo_projection_sort_key as todo_projection_sort_key,
)
from .control_plane.todos.todo_index import (
    EventsForGoal,
    MAX_TODO_INDEX_ITEMS,
    MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
)
from .promotion_gate import build_promotion_gate
from .quota import quota_status, quota_with_handoff_outcome_floor
from .rollout_event_log import load_rollout_events, rollout_event_log_path
from .state_projection import (
    active_state_next_action_entries,
    next_action_projection_warning,
    state_projection_gap_warning,
)
from .control_plane.todos.contract import (
    TODO_STATUS_OPEN,
    TODO_TASK_CLASS_USER_GATE,
    normalize_todo_status,
    normalize_todo_task_class as normalize_todo_task_class,
    todo_done_for_status,
)
from .control_plane.todos.todo_semantics import (
    todo_item_is_expired_monitor as todo_item_is_expired_monitor,
)


_PUBLIC_COMPAT_REEXPORTS = {
    "DREAMING_ADVISORY_CLASSIFICATIONS": "loopx.control_plane.runtime.status_classifications",
    "TODO_PROJECTION_DETAIL_POINTER_SCHEMA_VERSION": "loopx.control_plane.work_items.project_asset",
    "TODO_PROJECTION_VIEW_SCHEMA_VERSION": "loopx.control_plane.work_items.project_asset",
    "project_asset_summary_is_public_safe": "loopx.control_plane.work_items.project_asset",
    "compact_todo_group": "loopx.control_plane.todos.todo_summary",
    "compact_todo_item": "loopx.control_plane.todos.todo_summary",
    "todo_lane_items": "loopx.control_plane.todos.todo_summary",
    "todo_item_is_deferred": "loopx.control_plane.todos.todo_summary",
    "todo_item_is_due_monitor": "loopx.control_plane.todos.todo_summary",
    "todo_item_missing_monitor_schedule": "loopx.control_plane.todos.todo_summary",
    "todo_item_next_due_at": "loopx.control_plane.todos.todo_summary",
    "todo_projection_sort_key": "loopx.control_plane.todos.todo_summary",
    "normalize_todo_task_class": "loopx.control_plane.todos.contract",
    "todo_item_is_expired_monitor": "loopx.control_plane.todos.todo_semantics",
    # Refs #4447: single-sourced monitor/status vocabulary. Each name was already
    # defined here and again in the control_plane projection that owns it; these
    # entries keep the facade exporting one definition instead of a second copy.
    "MONITOR_DISPLAY_STOP_CONDITION": "loopx.control_plane.status.monitor_display_projection",
    "MONITOR_DISPLAY_FALLBACK_ACTION": "loopx.control_plane.status.monitor_display_projection",
    "STATUS_CONTRACT_RELOAD_HINT": "loopx.control_plane.status.contract_projection",
    "STATE_EVENT_LOG_BASENAME": "loopx.control_plane.status.active_state_projection",
    "PLANNED_CONTROLLER_OPT_IN_RECOMMENDED_ACTION": "loopx.control_plane.status.goal_attention_projection",
}


STATUS_NEUTRAL_CLASSIFICATIONS = HISTORY_STATUS_NEUTRAL_CLASSIFICATIONS
STATUS_CONTROL_PLANE_CONTEXT_LIMIT = 20
AGENT_LANE_PROGRESS_SCOPE = "agent_lane"
REGISTRY_WAITING_ON_OVERRIDES = {
    "user_or_controller",
    "controller",
    "codex",
    "external_evidence",
}
LEGACY_EXTERNAL_EVIDENCE_CLASSIFICATION_PREFIXES = (
    "await_",
    "external_evidence_observation_",
)
MONITOR_DISPLAY_SCHEMA_VERSION = "monitor_quiet_display_v0"
STATUS_CONTRACT_SCHEMA_VERSION = 2
MINIMUM_DASHBOARD_STATUS_CONTRACT_SCHEMA_VERSION = 2
STATUS_CONTRACT_SIGNAL_LIMIT = 3
MONITOR_WRITEBACK_CONTRACT_SCHEMA_VERSION = _MONITOR_WRITEBACK_CONTRACT_SCHEMA_VERSION
EVENT_LEDGER_DECISION_CLASSIFICATIONS = USER_OR_CONTROLLER_CLASSIFICATIONS | {
    "operator_gate_approved",
}
EVENT_LEDGER_STATE_CLASSIFICATIONS = {
    "state_refreshed",
    "public_harness_healthy",
}
EVENT_LEDGER_EVIDENCE_CLASSIFICATIONS = {
    "inspect_eval_result",
    "inspect_result",
    "needs_more_read_only_evidence",
    "read_only_project_map",
}
EVENT_LEDGER_EVIDENCE_HINTS = (
    "artifact",
    "blocker",
    "ci",
    "data",
    "deploy",
    "done",
    "eval",
    "evidence",
    "failure",
    "fail",
    "metric",
    "monitor",
    "validation",
)


CONNECTED_ADAPTER_STATUSES = {
    "connected",
    "connected-read-only",
    "pre-tick-runnable",
}
CONNECTED_DELIVERY_ADAPTER_STATUSES = {
    "connected-delivery",
}
RUN_COMPACT_FIELDS = RUN_BASE_COMPACT_FIELDS
LIFECYCLE_PRIORITY = (
    "controller_ready",
    "reward_judged",
    "operator_approved",
    "controller_gated",
    "operator_gated",
    "adapter_inspected",
    "mapped",
    "refreshed",
    "connected",
    "registered",
    "planned",
    "run_recorded",
)
SECTION_HEADING_PATTERN = re.compile(r"^##+\s+(.+?)\s*$")
MAX_STATUS_TODOS_PER_ROLE = _TODO_SUMMARY_MAX_STATUS_TODOS_PER_ROLE
MAX_ACTIVE_DONE_TODOS_BEFORE_ARCHIVE = MAX_STATUS_TODOS_PER_ROLE
MAX_PROJECT_ASSET_TODO_ITEMS = _TODO_SUMMARY_MAX_PROJECT_ASSET_TODO_ITEMS
MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS = _TODO_SUMMARY_MAX_PROJECT_ASSET_TODO_BACKLOG_ITEMS
MAX_TODO_VISIBILITY_LANE_ITEMS = _TODO_SUMMARY_MAX_TODO_VISIBILITY_LANE_ITEMS
MAX_DEFERRED_TODO_VISIBILITY_ITEMS = _TODO_SUMMARY_MAX_DEFERRED_TODO_VISIBILITY_ITEMS
MAX_MONITOR_DUE_ITEMS = _TODO_SUMMARY_MAX_MONITOR_DUE_ITEMS
MAX_DEPENDENCY_BLOCKERS = _TODO_SUMMARY_MAX_DEPENDENCY_BLOCKERS
MAX_AUTONOMOUS_BACKLOG_CANDIDATES = _MAX_AUTONOMOUS_TODO_CANDIDATES
MAX_BACKLOG_HYGIENE_EVIDENCE_ITEMS = _MAX_BACKLOG_HYGIENE_EVIDENCE_ITEMS_READ_MODEL
MAX_AUTONOMOUS_REPLAN_TRIGGERS = _MAX_AUTONOMOUS_REPLAN_TRIGGERS_READ_MODEL
AUTONOMOUS_REPLAN_STALL_THRESHOLD = _AUTONOMOUS_REPLAN_STALL_THRESHOLD_READ_MODEL
DEAD_MONITOR_REPEAT_THRESHOLD = 6
AUTONOMOUS_REPLAN_PERIODIC_RUN_THRESHOLD = AUTONOMOUS_REPLAN_ACK_MATERIAL_RUN_WINDOW
AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK = _AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK
BACKLOG_HYGIENE_SECTION_HEADINGS = ("Next Action", "Operating Lessons")
BACKLOG_HYGIENE_BULLET_PATTERN = re.compile(r"^\s*(?:[-*]|\d+[.)])\s+(.+?)\s*$")
BACKLOG_HYGIENE_HINT_PATTERN = re.compile(
    r"(?i)(?:\[p[0-4]\]|todo|backlog|follow[- ]?up|queue|audit|regression|smoke|cadence|mirror|monitor|sub-?agent|待办|回归|审计|修复|检查|推进)"
)
AUTONOMOUS_REPLAN_SCHEMA_VERSION = "autonomous_replan_obligation_v0"
DEAD_MONITOR_REPEAT_SCHEMA_VERSION = "dead_monitor_repeat_v0"
# Refs #4447: one definition for this vocabulary, owned by the control-plane
# codec that now feeds the replan history policy across status and quota. The
# facade keeps exporting the established public name for existing callers as an
# identity alias instead of restating the values, which also keeps the audited
# import-only export allowlist unchanged.
AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS = (
    _REPLAN_HISTORY_NEUTRAL_CLASSIFICATIONS
)




def state_event_log_candidates(goal: dict[str, Any], *, state_path: Path) -> list[Path]:
    from .control_plane.status.active_state_projection import (
        state_event_log_candidates as _state_event_log_candidates,
    )

    return _state_event_log_candidates(goal, state_path=state_path)


def active_state_event_projection_fields(
    goal: dict[str, Any],
    *,
    state_path: Path,
    preferred_todo_ids: set[str] | None = None,
    rollout_events: list[dict[str, Any]] | None = None,
    item_limit: int | None = MAX_STATUS_TODOS_PER_ROLE,
) -> dict[str, Any]:
    from .control_plane.status.active_state_projection import (
        active_state_event_projection_fields as _active_state_event_projection_fields,
    )

    return _active_state_event_projection_fields(
        goal,
        state_path=state_path,
        preferred_todo_ids=preferred_todo_ids,
        rollout_events=rollout_events,
        item_limit=item_limit,
    )


def active_state_sections(state_text: str, headings: tuple[str, ...]) -> dict[str, list[str]]:
    from .control_plane.status.active_state_projection import (
        active_state_sections as _active_state_sections,
    )

    return _active_state_sections(state_text, headings)


def parse_issue_meta_surface(state_text: str) -> dict[str, Any] | None:
    from .control_plane.status.active_state_projection import (
        parse_issue_meta_surface as _parse_issue_meta_surface,
    )

    return _parse_issue_meta_surface(state_text)


def active_state_section_entries(lines: list[str]) -> list[str]:
    from .control_plane.status.active_state_projection import (
        active_state_section_entries as _active_state_section_entries,
    )

    return _active_state_section_entries(lines)


def backlog_hygiene_warning(state_text: str, *, agent_todos: dict[str, Any] | None) -> dict[str, Any] | None:
    from .control_plane.status.active_state_projection import (
        backlog_hygiene_warning as _backlog_hygiene_warning,
    )

    return _backlog_hygiene_warning(state_text, agent_todos=agent_todos)


def build_autonomous_replan_obligation(
    evidence: list[dict[str, Any]],
    *,
    agent_todos: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from .control_plane.status.autonomous_replan_projection import (
        build_autonomous_replan_obligation as _build_autonomous_replan_obligation,
    )

    return _build_autonomous_replan_obligation(
        evidence,
        agent_todos=agent_todos,
    )


def run_history_monitor_wait_already_acknowledged(
    latest_runs: list[dict[str, Any]] | None,
    *,
    signal_count: int,
) -> bool:
    from .control_plane.status.autonomous_replan_projection import (
        run_history_monitor_wait_already_acknowledged as _run_history_monitor_wait_already_acknowledged,
    )

    return _run_history_monitor_wait_already_acknowledged(
        latest_runs,
        signal_count=signal_count,
    )


def latest_autonomous_replan_ack_for_projection(
    latest_runs: list[dict[str, Any]] | None,
) -> dict[str, Any] | None:
    from .control_plane.status.autonomous_replan_projection import (
        latest_autonomous_replan_ack_for_projection as _latest_autonomous_replan_ack_for_projection,
    )

    return _latest_autonomous_replan_ack_for_projection(latest_runs)


def autonomous_replan_periodic_review_from_runs(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_todos: dict[str, Any] | None,
) -> dict[str, Any] | None:
    from .control_plane.status.autonomous_replan_projection import (
        autonomous_replan_periodic_review_from_runs as _autonomous_replan_periodic_review_from_runs,
    )

    return _autonomous_replan_periodic_review_from_runs(
        latest_runs,
        agent_todos=agent_todos,
    )


def autonomous_replan_obligation_from_runs(
    latest_runs: list[dict[str, Any]] | None,
    *,
    agent_todos: dict[str, Any] | None,
    agent_id: str | None = None,
    external_progress_review: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    from .control_plane.status.autonomous_replan_projection import (
        autonomous_replan_obligation_from_runs as _autonomous_replan_obligation_from_runs,
    )

    return _autonomous_replan_obligation_from_runs(
        latest_runs,
        agent_todos=agent_todos,
        agent_id=agent_id,
        external_progress_review=external_progress_review,
    )


def external_progress_review_context(
    goal: dict[str, Any],
    runtime_root: Path | None,
) -> dict[str, Any] | None:
    """Status reads the sentinel context through the capability-owned loader."""

    from .capabilities.progress_review.context import (
        external_progress_review_context as _load_external_progress_review_context,
    )

    return _load_external_progress_review_context(goal, runtime_root)


def autonomous_backlog_candidates(
    items: list[dict[str, Any]],
    *,
    limit: int = MAX_AUTONOMOUS_BACKLOG_CANDIDATES,
) -> dict[str, Any] | None:
    from .control_plane.status.attention_projection import (
        autonomous_backlog_candidates as _autonomous_backlog_candidates,
    )

    return _autonomous_backlog_candidates(items, limit=limit)


def autonomous_monitor_candidates(
    items: list[dict[str, Any]],
    *,
    limit: int = MAX_AUTONOMOUS_BACKLOG_CANDIDATES,
) -> dict[str, Any] | None:
    from .control_plane.status.attention_projection import (
        autonomous_monitor_candidates as _autonomous_monitor_candidates,
    )

    return _autonomous_monitor_candidates(items, limit=limit)


def build_attention_queue_projection(
    *,
    items: list[dict[str, Any]],
    goal_id_filter: str | None,
    autonomous_backlog_candidates: dict[str, Any] | None,
    autonomous_monitor_candidates: dict[str, Any] | None,
) -> dict[str, Any]:
    from .control_plane.status.attention_projection import (
        build_attention_queue_projection as _build_attention_queue_projection,
    )

    return _build_attention_queue_projection(
        items=items,
        goal_id_filter=goal_id_filter,
        autonomous_backlog_candidates=autonomous_backlog_candidates,
        autonomous_monitor_candidates=autonomous_monitor_candidates,
    )


def active_state_projection_warning(goal: dict[str, Any], current_run: dict[str, Any] | None) -> dict[str, Any] | None:
    return _stale_latest_run_projection_warning_read_model(
        goal,
        current_run,
        agent_lane_progress_scope=AGENT_LANE_PROGRESS_SCOPE,
        resolve_goal_local_path=lambda raw, goal_value: resolve_goal_local_path(
            raw,
            goal_value,
            fallback_base=Path.cwd(),
        ),
        parse_state_frontmatter=parse_state_frontmatter,
        parse_timestamp=parse_timestamp,
    )


def is_handoff_ready_run(run: dict[str, Any]) -> bool:
    return _is_handoff_ready_run_read_model(
        run,
        handoff_ready_classifications=HANDOFF_READY_CLASSIFICATIONS,
        compact_operator_gate=compact_operator_gate,
    )


def run_has_external_evidence_watch_signal(run: dict[str, Any]) -> bool:
    """Return true only for explicit external-evidence watch state.

    Feature names may legitimately start with words such as "monitor"; routing
    to an external-evidence wait must come from structured state or explicit
    legacy external-evidence classifications, not broad classification prefixes.
    """

    return _run_has_external_evidence_watch_signal_read_model(
        run,
        legacy_external_evidence_classification_prefixes=LEGACY_EXTERNAL_EVIDENCE_CLASSIFICATION_PREFIXES,
    )


def is_custom_post_handoff_work_run(run: dict[str, Any]) -> bool:
    return _is_custom_post_handoff_work_run_read_model(
        run,
        is_status_neutral_run=is_status_neutral_run,
        is_handoff_ready_run=is_handoff_ready_run,
        run_has_external_evidence_watch_signal=run_has_external_evidence_watch_signal,
        codex_ready_classifications=CODEX_READY_CLASSIFICATIONS,
        user_or_controller_classifications=USER_OR_CONTROLLER_CLASSIFICATIONS,
        blocking_classifications=BLOCKING_CLASSIFICATIONS,
    )


def compact_post_handoff_run(run: dict[str, Any], profile: dict[str, Any] | None = None) -> dict[str, Any]:
    return project_post_handoff_history([run], profile)["post_handoff_latest_run"]


def project_post_handoff_history(
    runs: list[dict[str, Any]], profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not runs:
        return {}
    floor = execution_profile_outcome_floor(profile)
    floor_configured = bool(floor.get("outcome_markers") or floor.get("surface_only_hints"))
    projection = project_delivery_history(runs, outcome_floor_configured=floor_configured)
    compact_runs = []
    for run, signal in zip(runs, projection["runs"], strict=True):
        compact = {field: run[field] for field in (
            "generated_at", "classification", "health_check", "json_exists", "markdown_exists",
        ) if field in run}
        compact.update({field: signal[field] for field in ("delivery_batch_scale", "delivery_turn_kind")})
        if signal.get("delivery_claim_conflicts"):
            compact["delivery_claim_conflicts"] = signal["delivery_claim_conflicts"]
        if signal["delivery_turn_kind"] == "blocker_writeback":
            compact.update(compact_delivery_binding(run))
        if signal["delivery_outcome"] != "not_configured":
            compact["delivery_outcome"] = signal["delivery_outcome"]
        compact_runs.append(_attach_run_summary_projections_read_model(
            compact, run,
            compact_session_runtime_projection_from_run=compact_session_runtime_projection_from_run,
        ))
    result = {
        "post_handoff_latest_run": compact_runs[0],
        "post_handoff_recent_runs": compact_runs,
        "post_handoff_small_scale_streak": projection["small_scale_streak"],
    }
    if floor_configured:
        result["post_handoff_outcome_gap_streak"] = projection["outcome_gap_streak"]
    return result


def project_asset_handoff_state(
    *,
    ready: bool,
    project_asset: dict[str, Any],
    latest_runs: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    return _project_asset_handoff_state_read_model(
        ready=ready,
        project_asset=project_asset,
        latest_runs=latest_runs,
        compact_execution_profile=compact_execution_profile,
        parse_timestamp=parse_timestamp,
        is_handoff_ready_run=is_handoff_ready_run,
        is_custom_post_handoff_work_run=is_custom_post_handoff_work_run,
        is_status_neutral_run=is_status_neutral_run,
        project_delivery_history=project_post_handoff_history,
    )


def project_asset_handoff_readiness(
    item: dict[str, Any],
    *,
    latest_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    return _project_asset_handoff_readiness_read_model(
        item,
        latest_runs=latest_runs,
        project_asset_handoff_check_projection=project_asset_handoff_check_projection,
        handoff_budget_contract=handoff_budget_contract,
        project_asset_handoff_state=project_asset_handoff_state,
    )


def enrich_project_asset(
    item: dict[str, Any],
    *,
    user_todos: dict[str, Any] | None = None,
    agent_todos: dict[str, Any] | None = None,
    quota: dict[str, Any] | None = None,
    latest_validation: dict[str, Any] | None = None,
    latest_runs: list[dict[str, Any]] | None = None,
    execution_profile: dict[str, Any] | None = None,
    orchestration: dict[str, Any] | None = None,
    subagent_activity: dict[str, Any] | None = None,
    interface_budget_cadence: dict[str, Any] | None = None,
) -> None:
    _enrich_project_asset_read_model(
        item,
        user_todos=user_todos,
        agent_todos=agent_todos,
        quota=quota,
        latest_validation=latest_validation,
        latest_runs=latest_runs,
        execution_profile=execution_profile,
        orchestration=orchestration,
        subagent_activity=subagent_activity,
        interface_budget_cadence=interface_budget_cadence,
        project_asset_todo_summary=project_asset_todo_summary,
        project_asset_todo_projection_gap=project_asset_todo_projection_gap,
        project_asset_quota_summary=project_asset_quota_summary,
        compact_execution_profile=compact_execution_profile,
        compact_orchestration_policy=compact_orchestration_policy,
        project_asset_handoff_readiness=project_asset_handoff_readiness,
        project_asset_quota_state=project_asset_quota_state,
        project_asset_user_todo_open_count=project_asset_user_todo_open_count,
        build_long_task_cadence_hint=build_long_task_cadence_hint,
    )


def active_state_todo_fields(
    goal: dict[str, Any],
    *,
    runtime_root: Path | None = None,
    rollout_events: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    return _active_state_todo_fields_read_model(
        goal,
        runtime_root=runtime_root,
        rollout_events=rollout_events,
        resolve_goal_local_path=resolve_goal_local_path,
        active_state_next_action_entries=active_state_next_action_entries,
        active_next_action_todo_ids=active_next_action_todo_ids,
        load_rollout_events=load_rollout_events,
        rollout_event_log_path=rollout_event_log_path,
        max_todo_index_rollout_events_per_goal=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
        active_state_event_projection_fields=active_state_event_projection_fields,
        parse_active_state_todos=parse_active_state_todos,
        parse_issue_meta_surface=parse_issue_meta_surface,
        backlog_hygiene_warning=backlog_hygiene_warning,
        completed_todo_archive_warning=completed_todo_archive_warning,
        state_projection_gap_warning=state_projection_gap_warning,
    )


def attention_item(
    *,
    goal_id: str,
    status: str,
    waiting_on: str,
    severity: str,
    recommended_action: str,
    source: str,
    operator_question: str | None = None,
    agent_command: str | None = None,
    controller_stage: str | None = None,
    missing_gates: list[str] | None = None,
    next_handoff_condition: str | None = None,
    lifecycle_phase: str | None = None,
    lifecycle_flags: list[str] | None = None,
    user_todos: dict[str, Any] | None = None,
    agent_todos: dict[str, Any] | None = None,
    todo_state_file: str | None = None,
    dreaming_proposal: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return _attention_item_read_model(
        goal_id=goal_id,
        status=status,
        waiting_on=waiting_on,
        severity=severity,
        recommended_action=recommended_action,
        source=source,
        build_project_asset=build_project_asset,
        compact_dreaming_lane_badge=compact_dreaming_lane_badge,
        operator_question=operator_question,
        agent_command=agent_command,
        controller_stage=controller_stage,
        missing_gates=missing_gates,
        next_handoff_condition=next_handoff_condition,
        lifecycle_phase=lifecycle_phase,
        lifecycle_flags=lifecycle_flags,
        user_todos=user_todos,
        agent_todos=agent_todos,
        todo_state_file=todo_state_file,
        dreaming_proposal=dreaming_proposal,
    )


def sync_connected_attention_action_from_todos(item: dict[str, Any]) -> None:
    _sync_connected_attention_action_from_todos_read_model(
        item,
        first_open_todo_text=first_open_todo_text,
    )


def active_state_todo_attention_item(
    goal: dict[str, Any],
    fields: dict[str, Any],
    current_run: dict[str, Any] | None,
) -> dict[str, Any] | None:
    return _active_state_todo_attention_item_read_model(
        goal,
        fields,
        current_run,
        public_safe_compact_text=public_safe_compact_text,
        first_open_todo_text=first_open_todo_text,
        todo_summary_open_count=todo_summary_open_count,
        goal_lifecycle_fields=goal_lifecycle_fields,
        attention_item=attention_item,
    )


def todo_summary_open_count(summary: dict[str, Any] | None) -> int:
    from .control_plane.status.monitor_display_projection import (
        todo_summary_open_count as _todo_summary_open_count,
    )

    return _todo_summary_open_count(summary)


def todo_summary_lane_items(summary: dict[str, Any] | None, lane: str) -> list[dict[str, Any]]:
    from .control_plane.status.monitor_display_projection import (
        todo_summary_lane_items as _todo_summary_lane_items,
    )

    return _todo_summary_lane_items(summary, lane)


def attention_item_is_monitor_quiet_display_candidate(item: dict[str, Any]) -> bool:
    from .control_plane.status.monitor_display_projection import (
        attention_item_is_monitor_quiet_display_candidate as _attention_item_is_monitor_quiet_display_candidate,
    )

    return _attention_item_is_monitor_quiet_display_candidate(item)


def quiet_monitor_display_action(raw_action: str | None) -> str:
    from .control_plane.status.monitor_display_projection import (
        quiet_monitor_display_action as _quiet_monitor_display_action,
    )

    return _quiet_monitor_display_action(raw_action)


def normalize_monitor_quiet_attention_display(item: dict[str, Any]) -> None:
    from .control_plane.status.monitor_display_projection import (
        normalize_monitor_quiet_attention_display as _normalize_monitor_quiet_attention_display,
    )

    _normalize_monitor_quiet_attention_display(item)


def merge_global_registry_attention_findings(
    *,
    health_items: list[dict[str, Any]],
    history_items: list[dict[str, Any]],
    findings: list[Any],
    goal_id_filter: str | None,
) -> None:
    from .control_plane.status.registry_health_projection import (
        merge_global_registry_attention_findings as _merge_global_registry_attention_findings,
    )

    _merge_global_registry_attention_findings(
        health_items=health_items,
        history_items=history_items,
        findings=findings,
        goal_id_filter=goal_id_filter,
    )


def collect_global_registry_health(
    *,
    registry_path: Path,
    runtime_root: Path,
    current_registry: dict[str, Any],
) -> dict[str, Any]:
    from .control_plane.status.registry_health_projection import (
        collect_global_registry_health as _collect_global_registry_health,
    )

    return _collect_global_registry_health(
        registry_path=registry_path,
        runtime_root=runtime_root,
        current_registry=current_registry,
    )


def is_status_neutral_run(run: dict[str, Any]) -> bool:
    from .control_plane.status.run_projection import (
        is_status_neutral_run as _is_status_neutral_run,
    )

    return _is_status_neutral_run(run)


def latest_agent_lane_run(goal: dict[str, Any]) -> dict[str, Any] | None:
    from .control_plane.status.run_projection import (
        latest_agent_lane_run as _latest_agent_lane_run,
    )

    return _latest_agent_lane_run(goal)


def compact_agent_lane_recommendation(run: dict[str, Any] | None) -> dict[str, Any] | None:
    from .control_plane.status.run_projection import (
        compact_agent_lane_recommendation as _compact_agent_lane_recommendation,
    )

    return _compact_agent_lane_recommendation(run)


def latest_run_recommended_action_for_projection(
    *,
    current_status_run: dict[str, Any] | None,
    agent_lane_recommendation: dict[str, Any] | None,
    active_state_next_action: Any = None,
    preferred_agent_id: str | None = None,
    limit: int = 320,
) -> tuple[str | None, str | None]:
    from .control_plane.status.run_projection import (
        latest_run_recommended_action_for_projection as _latest_run_recommended_action_for_projection,
    )

    return _latest_run_recommended_action_for_projection(
        current_status_run=current_status_run,
        agent_lane_recommendation=agent_lane_recommendation,
        active_state_next_action=active_state_next_action,
        preferred_agent_id=preferred_agent_id,
        limit=limit,
    )


def latest_run(goal: dict[str, Any]) -> dict[str, Any] | None:
    from .control_plane.status.run_projection import (
        latest_run as _latest_run,
    )

    return _latest_run(goal)


def ordered_lifecycle_flags(flags: list[str]) -> list[str]:
    from .control_plane.status.lifecycle_projection import (
        ordered_lifecycle_flags as _ordered_lifecycle_flags,
    )

    return _ordered_lifecycle_flags(flags)


def primary_lifecycle_phase(flags: list[str], fallback: str = "registered") -> str:
    from .control_plane.status.lifecycle_projection import (
        primary_lifecycle_phase as _primary_lifecycle_phase,
    )

    return _primary_lifecycle_phase(flags, fallback=fallback)


def run_lifecycle_flags(run: dict[str, Any] | None) -> list[str]:
    from .control_plane.status.lifecycle_projection import (
        run_lifecycle_flags as _run_lifecycle_flags,
    )

    return _run_lifecycle_flags(run)


def run_lifecycle_phase(run: dict[str, Any] | None) -> str:
    from .control_plane.status.lifecycle_projection import (
        run_lifecycle_phase as _run_lifecycle_phase,
    )

    return _run_lifecycle_phase(run)


def goal_lifecycle_fields(goal: dict[str, Any], current_run: dict[str, Any] | None) -> dict[str, Any]:
    from .control_plane.status.lifecycle_projection import (
        goal_lifecycle_fields as _goal_lifecycle_fields,
    )

    return _goal_lifecycle_fields(goal, current_run)


def readiness_attention_fields(run: dict[str, Any] | None) -> dict[str, Any]:
    from .control_plane.status.lifecycle_projection import (
        readiness_attention_fields as _readiness_attention_fields,
    )

    return _readiness_attention_fields(run)


def operator_gate_attention_fields(run: dict[str, Any] | None) -> dict[str, Any]:
    from .control_plane.status.lifecycle_projection import (
        operator_gate_attention_fields as _operator_gate_attention_fields,
    )

    return _operator_gate_attention_fields(run)


def compact_server_planning_contract(value: Any) -> dict[str, Any]:
    from .control_plane.status.dreaming_projection import (
        compact_server_planning_contract as _compact_server_planning_contract,
    )

    return _compact_server_planning_contract(value)


def compact_dreaming_proposal(run: dict[str, Any] | None) -> dict[str, Any] | None:
    from .control_plane.status.dreaming_projection import (
        compact_dreaming_proposal as _compact_dreaming_proposal,
    )

    return _compact_dreaming_proposal(run)


def compact_dreaming_lane_badge(proposal: dict[str, Any] | None) -> dict[str, Any] | None:
    from .control_plane.status.dreaming_projection import (
        compact_dreaming_lane_badge as _compact_dreaming_lane_badge,
    )

    return _compact_dreaming_lane_badge(proposal)


def dreaming_attention_fields(run: dict[str, Any] | None) -> dict[str, Any]:
    from .control_plane.status.dreaming_projection import (
        dreaming_attention_fields as _dreaming_attention_fields,
    )

    return _dreaming_attention_fields(run)


def legacy_runtime_goal_attention(
    goal: dict[str, Any],
    current_run: dict[str, Any] | None,
    readiness_fields: dict[str, Any],
) -> dict[str, Any] | None:
    from .control_plane.status.goal_attention_projection import (
        legacy_runtime_goal_attention as _legacy_runtime_goal_attention,
    )

    return _legacy_runtime_goal_attention(
        goal,
        current_run,
        readiness_fields,
    )


def goal_attention(goal: dict[str, Any]) -> dict[str, Any] | None:
    from .control_plane.status.goal_attention_projection import (
        goal_attention as _goal_attention,
    )

    return _goal_attention(goal)


def build_task_graph_projection(
    item: dict[str, Any],
    *,
    goal: dict[str, Any],
    goal_latest_runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    return _build_task_graph_projection_read_model(
        item,
        goal=goal,
        goal_latest_runs=goal_latest_runs,
        public_safe_compact_text=public_safe_compact_text,
        normalize_todo_status=normalize_todo_status,
        todo_done_for_status=todo_done_for_status,
        todo_status_open=TODO_STATUS_OPEN,
        open_todo_items=open_todo_items,
        max_status_todos_per_role=MAX_STATUS_TODOS_PER_ROLE,
        todo_item_task_class=todo_item_task_class,
        user_gate_task_class=TODO_TASK_CLASS_USER_GATE,
        todo_summary_open_count=todo_summary_open_count,
        latest_run=latest_run,
    )

def attach_goal_channel_projection(
    item: dict[str, Any],
    *,
    goal: dict[str, Any],
    goal_latest_runs: list[dict[str, Any]],
    runtime_root: Path | None = None,
) -> None:
    _attach_goal_channel_projection_read_model(
        item,
        goal=goal,
        goal_latest_runs=goal_latest_runs,
        build_goal_channel_projection=build_goal_channel_projection,
        runtime_root=runtime_root,
    )


def build_attention_queue(
    *,
    contract: dict[str, Any],
    history: dict[str, Any],
    global_registry: dict[str, Any],
    runtime_root: Path | None = None,
    include_task_graph: bool = False,
    goal_id_filter: str | None = None,
    include_stopped_goal_context: bool = False,
    events_for_goal: EventsForGoal | None = None,
) -> dict[str, Any]:
    def request_active_state_todo_fields(
        goal: dict[str, Any],
        *,
        runtime_root: Path | None = None,
    ) -> dict[str, Any]:
        goal_id = str(goal.get("id") or "").strip()
        supplied_events = (
            events_for_goal(
                goal_id,
                limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
            )
            if events_for_goal is not None and runtime_root is not None and goal_id
            else None
        )
        return active_state_todo_fields(
            goal,
            runtime_root=runtime_root,
            rollout_events=supplied_events,
        )

    queue = _build_attention_queue_read_model(
        contract=contract,
        history=history,
        global_registry=global_registry,
        context=AttentionQueueContext(
            active_state_todo_fields=request_active_state_todo_fields,
            active_state_todo_attention_item=active_state_todo_attention_item,
            latest_run=latest_run,
            goal_attention=goal_attention,
            compact_agent_lane_recommendation=compact_agent_lane_recommendation,
            latest_agent_lane_run=latest_agent_lane_run,
            latest_run_recommended_action_for_projection=latest_run_recommended_action_for_projection,
            compact_autonomous_replan_ack=compact_autonomous_replan_ack,
            latest_autonomous_replan_ack_for_projection=latest_autonomous_replan_ack_for_projection,
            compact_control_plane_policy=compact_control_plane_policy,
            subagent_activity_for_goal=subagent_activity_for_goal,
            interface_budget_cadence_for_runs=interface_budget_cadence_for_runs,
            active_state_projection_warning=active_state_projection_warning,
            enrich_project_asset=enrich_project_asset,
            project_asset_latest_validation=project_asset_latest_validation,
            attach_active_state_project_asset_fields=_attach_active_state_project_asset_fields,
            sync_connected_attention_action_from_todos=sync_connected_attention_action_from_todos,
            quota_status=quota_status,
            quota_with_handoff_outcome_floor=quota_with_handoff_outcome_floor,
            normalize_monitor_quiet_attention_display=normalize_monitor_quiet_attention_display,
            build_task_graph_projection=build_task_graph_projection,
            attach_goal_channel_projection=attach_goal_channel_projection,
            attach_dependency_blockers=attach_dependency_blockers,
            autonomous_backlog_candidates=autonomous_backlog_candidates,
            autonomous_monitor_candidates=autonomous_monitor_candidates,
            attention_item=attention_item,
            attach_global_registry_shadow_finding=attach_global_registry_shadow_finding,
            next_action_projection_warning=next_action_projection_warning,
            autonomous_replan_obligation_from_runs=autonomous_replan_obligation_from_runs,
            source_registry_shadow_findings=SOURCE_REGISTRY_SHADOW_FINDINGS,
            monitor_signal_waiting_on=MONITOR_SIGNAL_WAITING_ON,
            external_progress_review_context=external_progress_review_context,
        ),
        runtime_root=runtime_root,
        include_task_graph=include_task_graph,
        goal_id_filter=goal_id_filter,
        include_stopped_goal_context=include_stopped_goal_context,
    )
    if runtime_root is not None:
        for item in queue.get("items") or []:
            if not isinstance(item, dict):
                continue
            goal_id = str(item.get("goal_id") or "").strip()
            if not goal_id:
                continue
            events = (
                events_for_goal(
                    goal_id,
                    limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
                )
                if events_for_goal is not None
                else load_rollout_events(
                    rollout_event_log_path(runtime_root, goal_id),
                    limit=MAX_TODO_INDEX_ROLLOUT_EVENTS_PER_GOAL,
                )
            )
            receipts = project_evidence_log_read_receipts(
                events,
                limit=MAX_PROJECTED_READ_RECEIPTS,
            )
            if receipts:
                item["evidence_log_read_receipts"] = receipts
            project_asset = item.get("project_asset")
            if not isinstance(project_asset, dict):
                continue
            orchestration = project_asset.get("orchestration")
            if not isinstance(orchestration, dict) or not (
                orchestration.get("mode") == "multi_subagent"
                and orchestration.get("spawn_allowed") is True
                and int(orchestration.get("max_children") or 0) > 0
            ):
                continue
            native_activity = latest_native_child_activity(
                events, goal_id=goal_id,
                configured_limit=int(orchestration["max_children"]),
            )
            if native_activity:
                # The shared status snapshot is bounded for Todo work. Once it
                # reveals a native event, read its exact Turn before showing
                # counts, so older stages falling outside that window cannot
                # silently undercount the activity.
                project_asset["native_child_activity"] = load_native_child_activity(
                    runtime_root, goal_id=goal_id,
                    agent_id=native_activity["agent_id"],
                    turn_instance_id=native_activity["turn_instance_id"],
                    configured_limit=int(orchestration["max_children"]),
                )
    return queue


def compact_run(run: dict[str, Any]) -> dict[str, Any]:
    compact = _compact_run_base_read_model(
        run,
        run_compact_fields=RUN_COMPACT_FIELDS,
        run_lifecycle_flags=run_lifecycle_flags,
        primary_lifecycle_phase=primary_lifecycle_phase,
        compact_human_reward=compact_human_reward,
        compact_operator_gate=compact_operator_gate,
        compact_autonomous_replan_ack=compact_autonomous_replan_ack,
        compact_operator_gate_resume_contract=compact_operator_gate_resume_contract,
        compact_controller_readiness=compact_controller_readiness,
        public_safe_compact_text=public_safe_compact_text,
        compact_subagent_run=compact_subagent_run,
        max_subagent_activity_items=MAX_SUBAGENT_ACTIVITY_ITEMS,
        compact_agent_vision=_compact_goal_vision_packet_read_model,
    )
    return _attach_run_summary_projections_read_model(
        compact,
        run,
        compact_session_runtime_projection_from_run=compact_session_runtime_projection_from_run,
    )


def build_status_contract() -> dict[str, Any]:
    from .control_plane.status.contract_projection import (
        build_status_contract as _build_status_contract,
    )

    return _build_status_contract()


def build_contract_health_projection(contract: dict[str, Any]) -> dict[str, Any]:
    from .control_plane.status.contract_projection import (
        build_contract_health_projection as _build_contract_health_projection,
    )

    return _build_contract_health_projection(contract)


def build_status_runtime_summary_context() -> StatusRuntimeSummaryContext:
    return StatusRuntimeSummaryContext(
        latest_run=latest_run,
        goal_lifecycle_fields=goal_lifecycle_fields,
        subagent_activity_for_goal=subagent_activity_for_goal,
        compact_run=compact_run,
        quota_status=quota_status,
        parse_timestamp=parse_timestamp,
        run_has_external_evidence_watch_signal=run_has_external_evidence_watch_signal,
        decision_classifications=EVENT_LEDGER_DECISION_CLASSIFICATIONS,
        evidence_classifications=EVENT_LEDGER_EVIDENCE_CLASSIFICATIONS,
        evidence_hints=EVENT_LEDGER_EVIDENCE_HINTS,
        state_classifications=EVENT_LEDGER_STATE_CLASSIFICATIONS,
        promotion_readiness_classifications=PROMOTION_READINESS_CLASSIFICATIONS,
        add_promotion_readiness_freshness=add_promotion_readiness_freshness,
        latest_promotion_readiness_event=latest_promotion_readiness_event,
        promotion_readiness_freshness_hours=PROMOTION_READINESS_FRESHNESS_HOURS,
        promotion_readiness_proxy_note=PROMOTION_READINESS_PROXY_NOTE,
        public_safe_compact_text=public_safe_compact_text,
        decision_freshness_classification_prefixes=DECISION_FRESHNESS_CLASSIFICATION_PREFIXES,
        decision_freshness_window_days=DECISION_FRESHNESS_WINDOW_DAYS,
        decision_freshness_item_limit=DECISION_FRESHNESS_ITEM_LIMIT,
        decision_freshness_proxy_note=DECISION_FRESHNESS_PROXY_NOTE,
    )


def build_status_runtime_summaries(
    *,
    history: dict[str, Any],
    queue: dict[str, Any],
    runtime_root: Path,
    goal_id_filter: str | None,
    display_limit: int,
    todo_index_limit: int,
    recent_run_limit: int | None = None,
    include_goal_subagent_configuration: bool = False,
    events_for_goal: EventsForGoal | None = None,
) -> dict[str, Any]:
    return _build_status_runtime_summaries_read_model(
        history=history,
        queue=queue,
        runtime_root=runtime_root,
        goal_id_filter=goal_id_filter,
        display_limit=display_limit,
        todo_index_limit=todo_index_limit,
        recent_run_limit=recent_run_limit,
        include_goal_subagent_configuration=(
            include_goal_subagent_configuration
        ),
        events_for_goal=events_for_goal,
        context=build_status_runtime_summary_context(),
    )


def build_status_collection_context() -> StatusCollectionContext:
    return StatusCollectionContext(
        load_registry=load_registry,
        resolve_runtime_root=resolve_runtime_root,
        collect_global_registry_health=collect_global_registry_health,
        collect_status_history=collect_status_history,
        check_contract=check_contract,
        build_attention_queue=build_attention_queue,
        build_runtime_summaries=build_status_runtime_summaries,
        build_promotion_gate=build_promotion_gate,
        build_status_contract=build_status_contract,
        build_contract_health_projection=build_contract_health_projection,
        build_agent_management_projection=_build_agent_management_projection_read_model,
        build_goal_channel_notification_projection=build_goal_channel_notification_projection,
        status_control_plane_context_limit=STATUS_CONTROL_PLANE_CONTEXT_LIMIT,
        max_todo_index_items=MAX_TODO_INDEX_ITEMS,
    )


def collect_status(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    limit: int,
    include_task_graph: bool = False,
    goal_id: str | None = None,
    available_capabilities: Any = None,
    include_public_boundary_scan: bool = True,
    recent_run_limit: int | None = None,
    include_goal_subagent_configuration: bool = False,
    activation_state_filter: str | None = None,
    agent_lane_id: str | None = None,
) -> dict[str, Any]:
    with effect_runtime_request_scope():
        return _collect_status_read_model(
            registry_path=registry_path,
            runtime_root_override=runtime_root_override,
            scan_roots=scan_roots,
            limit=limit,
            include_task_graph=include_task_graph,
            goal_id=goal_id,
            available_capabilities=available_capabilities,
            include_public_boundary_scan=include_public_boundary_scan,
            recent_run_limit=recent_run_limit,
            include_goal_subagent_configuration=(
                include_goal_subagent_configuration
            ),
            activation_state_filter=activation_state_filter,
            agent_lane_id=agent_lane_id,
            context=build_status_collection_context(),
        )
