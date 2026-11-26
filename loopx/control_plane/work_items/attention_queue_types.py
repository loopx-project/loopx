"""Typed contracts and dependency context for the Work Items attention queue."""
from __future__ import annotations

from collections.abc import Callable, Set as AbstractSet
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, TypedDict

AttentionWaitingOn = Literal[
    "user_or_controller",
    "controller",
    "codex",
    "external_evidence",
]
GlobalRegistryFindingSeverity = Literal["high", "action"]

USER_OR_CONTROLLER_WAIT_STATES: frozenset[str] = frozenset(
    {"user_or_controller", "controller"}
)
ACTIONABLE_REGISTRY_SEVERITIES: frozenset[str] = frozenset({"high", "action"})


class AttentionQueueCounts(TypedDict):
    """Bucket counts emitted alongside attention queue items."""

    item_count: int
    needs_user_or_controller: int
    needs_controller: int
    needs_codex: int
    watching_external_evidence: int
    watching_monitor: int


class AttentionQueueProjection(AttentionQueueCounts, total=False):
    """Structured shape of the serialized ``attention_queue`` projection."""

    available: bool
    items: list[dict[str, object]]
    goal_filter: str
    goal_filter_applied: bool
    autonomous_backlog_candidates: dict[str, object]
    autonomous_monitor_candidates: dict[str, object]


class AttentionItemBuilder(Protocol):
    def __call__(
        self,
        *,
        goal_id: str,
        status: str,
        waiting_on: str,
        severity: str,
        recommended_action: str,
        source: str,
    ) -> dict[str, object]: ...


class GlobalRegistryShadowAttacher(Protocol):
    def __call__(
        self,
        item: dict[str, object],
        finding: dict[str, object],
        /,
    ) -> None: ...


class ActiveStateTodoFieldsBuilder(Protocol):
    def __call__(
        self,
        goal: dict[str, object],
        /,
        *,
        runtime_root: Path | None = None,
    ) -> dict[str, object]: ...


class ActiveStateTodoAttentionBuilder(Protocol):
    def __call__(
        self,
        goal: dict[str, object],
        fields: dict[str, object],
        current_run: dict[str, object] | None,
        /,
    ) -> dict[str, object] | None: ...


class LatestRunRecommendedActionBuilder(Protocol):
    def __call__(
        self,
        *,
        current_status_run: dict[str, object] | None,
        agent_lane_recommendation: dict[str, object] | None,
        active_state_next_action: object = None,
        preferred_agent_id: str | None = None,
    ) -> tuple[str | None, str | None]: ...


class EnrichProjectAssetFn(Protocol):
    def __call__(
        self,
        item: dict[str, object],
        /,
        *,
        user_todos: dict[str, object] | None = None,
        agent_todos: dict[str, object] | None = None,
        quota: dict[str, object] | None = None,
        latest_validation: dict[str, object] | None = None,
        latest_runs: list[dict[str, object]] | None = None,
        execution_profile: dict[str, object] | None = None,
        orchestration: dict[str, object] | None = None,
        subagent_activity: dict[str, object] | None = None,
        interface_budget_cadence: dict[str, object] | None = None,
    ) -> None: ...


class ActiveStateProjectAssetAttacher(Protocol):
    def __call__(
        self,
        item: dict[str, object],
        /,
        *,
        latest_runs: list[dict[str, object]] | None = None,
        next_action_projection_warning: Callable[..., dict[str, object] | None],
        autonomous_replan_obligation_from_runs: Callable[
            ..., dict[str, object] | None
        ],
        external_progress_review: dict[str, object] | None = None,
        effective_turn_cadence: dict[str, object] | None = None,
        agent_lane_id: str | None = None,
    ) -> object: ...


class QuotaStatusBuilder(Protocol):
    def __call__(
        self,
        goal: dict[str, object],
        /,
        *,
        waiting_on: str,
        severity: str,
        lifecycle_phase: object = None,
        lifecycle_flags: object = None,
        status: object = None,
    ) -> dict[str, object]: ...


class QuotaHandoffFloorBuilder(Protocol):
    def __call__(
        self,
        quota: dict[str, object],
        /,
        *,
        waiting_on: str,
        project_asset: dict[str, object] | None = None,
        handoff_readiness: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


class TaskGraphProjectionBuilder(Protocol):
    def __call__(
        self,
        item: dict[str, object],
        /,
        *,
        goal: dict[str, object],
        goal_latest_runs: list[dict[str, object]] | None = None,
    ) -> dict[str, object] | None: ...


class GoalChannelProjectionAttacher(Protocol):
    def __call__(
        self,
        item: dict[str, object],
        /,
        *,
        goal: dict[str, object],
        goal_latest_runs: list[dict[str, object]],
        runtime_root: Path | None = None,
    ) -> None: ...


class ExternalProgressReviewLoader(Protocol):
    def __call__(
        self,
        goal: dict[str, object],
        runtime_root: Path | None,
        /,
    ) -> dict[str, object] | None: ...


@dataclass(frozen=True)
class AttentionQueueContext:
    active_state_todo_fields: ActiveStateTodoFieldsBuilder
    active_state_todo_attention_item: ActiveStateTodoAttentionBuilder
    latest_run: Callable[[dict[str, object]], dict[str, object] | None]
    goal_attention: Callable[[dict[str, object]], dict[str, object] | None]
    compact_agent_lane_recommendation: Callable[
        [dict[str, object] | None], dict[str, object] | None
    ]
    latest_agent_lane_run: Callable[[dict[str, object]], dict[str, object] | None]
    latest_run_recommended_action_for_projection: LatestRunRecommendedActionBuilder
    compact_autonomous_replan_ack: Callable[
        [dict[str, object] | None], dict[str, object] | None
    ]
    latest_autonomous_replan_ack_for_projection: Callable[
        [list[dict[str, object]] | None], dict[str, object] | None
    ]
    compact_control_plane_policy: Callable[[object], dict[str, object]]
    subagent_activity_for_goal: Callable[[dict[str, object]], dict[str, object] | None]
    interface_budget_cadence_for_runs: Callable[
        [list[dict[str, object]]], dict[str, object] | None
    ]
    active_state_projection_warning: Callable[
        [dict[str, object], dict[str, object] | None], dict[str, object] | None
    ]
    enrich_project_asset: EnrichProjectAssetFn
    project_asset_latest_validation: Callable[
        [dict[str, object] | None], dict[str, object] | None
    ]
    attach_active_state_project_asset_fields: ActiveStateProjectAssetAttacher
    sync_connected_attention_action_from_todos: Callable[[dict[str, object]], None]
    quota_status: QuotaStatusBuilder
    quota_with_handoff_outcome_floor: QuotaHandoffFloorBuilder
    normalize_monitor_quiet_attention_display: Callable[[dict[str, object]], None]
    build_task_graph_projection: TaskGraphProjectionBuilder
    attach_goal_channel_projection: GoalChannelProjectionAttacher
    attach_dependency_blockers: Callable[[list[dict[str, object]]], None]
    autonomous_backlog_candidates: Callable[
        [list[dict[str, object]]], dict[str, object] | None
    ]
    autonomous_monitor_candidates: Callable[
        [list[dict[str, object]]], dict[str, object] | None
    ]
    attention_item: AttentionItemBuilder
    attach_global_registry_shadow_finding: GlobalRegistryShadowAttacher
    next_action_projection_warning: Callable[..., dict[str, object] | None]
    autonomous_replan_obligation_from_runs: Callable[..., dict[str, object] | None]
    source_registry_shadow_findings: AbstractSet[str]
    monitor_signal_waiting_on: str
    registry_path: Path | None = None
    external_progress_review_context: ExternalProgressReviewLoader | None = None
    tolerate_malformed_settlement_history: bool = False
