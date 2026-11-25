"""Vision, replan gate, completion validation, and delivery workspace qualification."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ..capabilities.progress_review.context import external_progress_review_context
from ..capabilities.todo_replan_cadence.machine_defaults import (
    resolve_todo_replan_cadence_goal,
)
from ..control_plane.agents.delivery_workspace import (
    qualify_delivery_workspace_isolation,
)
from ..control_plane.coordination.local_authority import local_authority_is_promoted
from ..control_plane.goals.goal_frontier import latest_agent_vision_from_runs
from ..control_plane.goals.vision_checkpoint import (
    build_vision_checkpoint,
    prepare_vision_refresh,
)
from ..control_plane.quota.blocked_retry import require_blocked_retry_wait
from ..control_plane.quota.settlement import QuotaSettlementReadback, SettlementIdentity
from ..control_plane.runtime.time import chronology_key, now_local_iso
from ..control_plane.todos.active_state_todo_parser import parse_active_state_todos
from ..control_plane.todos.completion_validation_accountability import (
    require_accountable_completion_validation,
)
from ..control_plane.work_items.delivery_outcome import ACCOUNTABLE_DELIVERY_OUTCOMES
from ..control_plane.work_items.recommendation_source_io import (
    recommendation_source_context,
)
from ..control_plane.work_items.refresh_recommendation import (
    resolve_refresh_recommendation,
)
from ..control_plane.work_items.replan_history_codec import (
    effective_turn_cadence_context,
)
from ..control_plane.work_items.semantic_replan_writeback import (
    RefreshReplanQualification,
)
from ..history import RunIndexSnapshot
from .admission import RefreshGoalContext


def _dict_or_none(value: object) -> dict[str, object] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _dict_or_empty(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def _str_list_or_empty(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str)]


@dataclass(frozen=True)
class RefreshQualificationOutcome:
    """Computed vision, recommendation, replan qualification, and completion facts."""

    generated_at: str
    newest_first_runs: list[dict[str, object]]
    agent_vision: dict[str, object] | None
    existing_agent_vision: dict[str, object] | None
    recommendation_resolution: dict[str, object]
    action: str
    recommended_action_source: str
    requested_classification: str
    classification: str
    normalized_delivery_outcome: str | None
    repair_delta_contract: dict[str, object] | None
    replan_semantic_delta: dict[str, object] | None
    autonomous_replan_frontier_identity: str | None
    effective_autonomous_replan_recorded: bool
    blocked_retry: dict[str, object] | None
    vision_checkpoint: dict[str, object]
    parsed_fallback_todos: dict[str, object] | None


def qualify_vision_replan_and_completion(
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    goal_context: RefreshGoalContext,
    settlement_identity: SettlementIdentity | None,
    settlement_readback: QuotaSettlementReadback | None,
    checkpoint_supplement: bool,
    prior_writeback_run: dict[str, object] | None,
    recovering_writeback_path: str | None,
    source_admission: dict[str, object] | None,
    goal_ref: dict[str, str] | None,
    normalized_agent_id: str,
    recommended_action: str | None,
    normalized_next_action: str | None,
    next_action_basis: str | None,
    classification: str,
    normalized_delivery_outcome: str | None,
    normalized_delivery_boundary: str | None,
    autonomous_replan_recorded: bool,
    normalized_repair_delta_kinds: Sequence[str],
    agent_vision_packet: Mapping[str, object] | None,
    merge_agent_vision_patch: bool,
    vision_unchanged_reason: str | None,
    normalized_progress_observation: Mapping[str, object] | None,
    completion_todo_id: str | None,
    completion_turn_key: str | None,
    dry_run: bool,
    load_index_snapshot_fn: Callable[..., RunIndexSnapshot],
    now_local_fn: Callable[[], str],
    qualify_refresh_replan_writeback_fn: Callable[..., RefreshReplanQualification],
) -> RefreshQualificationOutcome:
    """Evaluate vision refresh, recommendation resolution, replan gate, and completion."""
    agent_vision: dict[str, object] | None = None
    existing_agent_vision: dict[str, object] | None = None
    newest_first_runs: list[dict[str, object]] = []
    if normalized_agent_id:
        existing_runs = load_index_snapshot_fn(
            runtime_root / "goals" / goal_id / "runs" / "index.jsonl",
            include_artifact_status=False,
        ).records
        newest_first_runs = [
            dict(run)
            for _, run in sorted(
                enumerate(existing_runs),
                key=lambda item: (
                    *chronology_key(item[1].get("generated_at")),
                    item[0],
                ),
                reverse=True,
            )
        ]
        raw_existing_vision: object = latest_agent_vision_from_runs(
            newest_first_runs,
            goal_id=goal_id,
            agent_id=normalized_agent_id,
        )
        existing_agent_vision = _dict_or_none(raw_existing_vision)
    effective_replan_goal = resolve_todo_replan_cadence_goal(
        goal_context.registry_goal or {"id": goal_id},
        runtime_root,
    )
    if agent_vision_packet is not None:
        agent_vision = prepare_vision_refresh(
            dict(agent_vision_packet),
            goal_id=goal_id,
            agent_id=normalized_agent_id or None,
            existing_agent_vision=existing_agent_vision,
            merge_patch=merge_agent_vision_patch,
            require_path_delta_for_durable_change=autonomous_replan_recorded,
        )
    generated_at = now_local_fn()
    recommendation_context = (
        recommendation_source_context(
            goal_context.registry_goal,
            goal_context.state_text,
            source_registry=goal_context.next_action_source_registry,
            todo_fields=goal_context.todo_fields,
        )
        if goal_context.registry_goal is not None
        else None
    )
    recommendation_resolution = resolve_refresh_recommendation(
        goal_context.state_text,
        todo_fields=goal_context.todo_fields,
        explicit_action=recommended_action,
        next_step=normalized_next_action,
        next_step_basis=next_action_basis,
        source_context=recommendation_context,
        runs=newest_first_runs,
        agent_id=normalized_agent_id or None,
        settlement_identity=(
            settlement_identity.as_dict() if settlement_identity is not None else None
        ),
        registry_goal=goal_context.registry_goal,
        state_path=goal_context.resolved_state_file,
        rollout_events=(
            goal_context.planning_events
            if normalized_agent_id or settlement_identity is not None
            else None
        ),
    )
    action = str(recommendation_resolution["recommended_action"])
    recommended_action_source = str(
        recommendation_resolution["recommended_action_source"]
    )
    requested_classification = classification
    settlement_replan_guard = (
        dict(settlement_readback.semantic_replan_guard)
        if settlement_readback is not None
        and settlement_readback.semantic_replan_guard is not None
        else {}
    )
    from ..capabilities.explore.research_frontier import prepare_research_replan_evidence

    capability_evidence = prepare_research_replan_evidence(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=normalized_agent_id,
        registry_goal=goal_context.registry_goal,
        state_text=goal_context.state_text,
        capability_guard=_dict_or_none(
            settlement_replan_guard.get("selected_capability_guard")
        ),
    )

    def read_explore_context(
        read_goal_id: str, read_agent_id: str | None
    ) -> dict[str, object] | None:
        from ..capabilities.explore.turn_context import read_replan_explore_context

        stride_decision = _dict_or_empty((agent_vision or {}).get("stride_decision"))
        explore_review = _dict_or_empty(stride_decision.get("explore_review"))
        return read_replan_explore_context(
            source_status={
                "registry": str(registry_path),
                "runtime_root": str(runtime_root),
            },
            goal_id=read_goal_id,
            agent_id=read_agent_id,
            review_pages=_str_list_or_empty(explore_review.get("result_pages"))
            or None,
        )

    selected_obligation = settlement_replan_guard.get("selected_obligation_id")
    admission_fallback = (
        (settlement_readback.heartbeat_receipt or {}).get("recorded_at")
        if settlement_readback is not None
        else None
    )
    replan_qualification = qualify_refresh_replan_writeback_fn(
        goal_ref=goal_ref,
        replan_explore_reader=read_explore_context,
        replan_source_route={
            "registry": str(registry_path),
            "runtime_root": str(runtime_root),
        },
        capability_evidence=capability_evidence,
        guard_capability=settlement_replan_guard.get("selected_capability_guard"),
        todo_fields=goal_context.todo_fields,
        autonomous_replan_recorded=autonomous_replan_recorded,
        requested_delta_kinds=list(normalized_repair_delta_kinds),
        active_state_next_action_update=None,
        agent_vision=agent_vision,
        existing_agent_vision=existing_agent_vision,
        agent_id=normalized_agent_id,
        dry_run=dry_run,
        settlement_todo_id=(
            settlement_identity.todo_id if settlement_identity is not None else None
        ),
        settlement_guard_scoped=(
            settlement_replan_guard.get("scope") == "turn_guard"
        ),
        receipt_bound_guard_recorded_at=(
            settlement_readback.admission_recorded_at
            if settlement_readback is not None
            and settlement_readback.admission_recorded_at
            else admission_fallback
        ),
        admission_event_id=(
            settlement_readback.admission_event_id
            if settlement_readback is not None
            else None
        ),
        receipt_bound_guard_turn_instance_id=(
            settlement_identity.turn_instance_id
            if settlement_identity is not None
            else None
        ),
        receipt_bound_replan_review=settlement_replan_guard.get(
            "selected_review_source"
        ),
        settlement_guard_semantic_replan_obligation_id=(
            selected_obligation if isinstance(selected_obligation, str) else None
        ),
        newest_first_runs=newest_first_runs,
        state_text=goal_context.state_text,
        goal_id=goal_id,
        progress_observation=(
            dict(normalized_progress_observation)
            if normalized_progress_observation is not None
            else None
        ),
        recovering_writeback_path=recovering_writeback_path,
        registry_goal=effective_replan_goal,
        effective_turn_cadence=effective_turn_cadence_context(
            effective_replan_goal,
            runtime_root,
            registry_path=registry_path,
            goal_ref=goal_ref,
            source_admission=source_admission,
        ),
        external_progress_review=external_progress_review_context(
            goal_context.registry_goal or {"id": goal_id},
            runtime_root,
        ),
        completion_todo_id=completion_todo_id,
        completion_turn_key=completion_turn_key,
        classification=classification,
        delivery_outcome=normalized_delivery_outcome,
    )
    resolved_classification = replan_qualification.classification
    resolved_delivery_outcome = replan_qualification.delivery_outcome
    effective_recorded = replan_qualification.autonomous_replan_recorded
    blocked_retry: dict[str, object] | None = None
    parsed_fallback_todos: dict[str, object] | None = None
    if checkpoint_supplement and prior_writeback_run is not None:
        blocked_retry = _dict_or_none(prior_writeback_run.get("blocked_retry"))
    elif (
        settlement_identity is not None
        and settlement_identity.binding_kind.value == "todo"
        and resolved_delivery_outcome == "outcome_gap"
        and normalized_progress_observation is not None
        and normalized_progress_observation.get("result_class") == "blocked"
    ):
        blocked_todo_fields = goal_context.todo_fields
        if blocked_todo_fields is None:
            parsed_fallback_todos = parse_active_state_todos(
                goal_context.state_text,
                goal=goal_context.registry_goal,
                state_path=goal_context.resolved_state_file,
                preferred_todo_ids={settlement_identity.todo_id or ""},
                rollout_events=goal_context.planning_events,
                item_limit=None,
            )
            blocked_todo_fields = parsed_fallback_todos
        blocked_retry = require_blocked_retry_wait(
            blocked_todo_fields,
            todo_id=settlement_identity.todo_id or "",
            observed_at=generated_at,
            allow_turn_settlement_retry=local_authority_is_promoted(
                runtime_root=runtime_root,
                goal_id=goal_id,
            ),
        )
    vision_checkpoint = build_vision_checkpoint(
        agent_id=normalized_agent_id or None,
        agent_vision=agent_vision,
        existing_agent_vision=existing_agent_vision,
        vision_unchanged_reason=vision_unchanged_reason,
        delivery_outcome=resolved_delivery_outcome,
        active_state_next_action_update=None,
        delivery_boundary=normalized_delivery_boundary,
        todo_id=(
            settlement_identity.todo_id if settlement_identity is not None else None
        ),
        completion_todo_id=completion_todo_id,
        autonomous_replan_recorded=effective_recorded,
        blocked_retry=blocked_retry,
    )
    if (
        resolved_delivery_outcome in ACCOUNTABLE_DELIVERY_OUTCOMES
        and not checkpoint_supplement
    ):
        require_accountable_completion_validation(
            goal_context.state_text,
            todo_fields=goal_context.todo_fields,
            todo_id=(
                settlement_identity.todo_id if settlement_identity is not None else None
            ),
            agent_id=normalized_agent_id or None,
            delivery_boundary=normalized_delivery_boundary,
            delivery_outcome=resolved_delivery_outcome,
            semantic_replan_recorded=effective_recorded,
            goal_id=goal_id,
            progress_writeback=(
                {
                    "goal_id": goal_id,
                    "agent_id": normalized_agent_id,
                    "todo_id": settlement_identity.todo_id,
                    "turn_instance_id": settlement_identity.turn_instance_id,
                    "settlement_identity": settlement_identity.as_dict(),
                    "delivery_outcome": resolved_delivery_outcome,
                    "vision_checkpoint": vision_checkpoint,
                    "agent_vision": agent_vision,
                    "completion_todo_id": completion_todo_id,
                }
                if settlement_identity is not None
                else None
            ),
        )
    if checkpoint_supplement and not vision_checkpoint.get("satisfied"):
        raise ValueError(
            "checkpoint supplement did not satisfy the missing decision; "
            "an unchanged reason requires an existing vision. Supply a valid vision "
            "patch on the same Turn; the original writeback and quota are unchanged."
        )
    return RefreshQualificationOutcome(
        generated_at=generated_at,
        newest_first_runs=newest_first_runs,
        agent_vision=agent_vision,
        existing_agent_vision=existing_agent_vision,
        recommendation_resolution=dict(recommendation_resolution),
        action=action,
        recommended_action_source=recommended_action_source,
        requested_classification=requested_classification,
        classification=resolved_classification,
        normalized_delivery_outcome=resolved_delivery_outcome,
        repair_delta_contract=_dict_or_none(
            replan_qualification.repair_delta_contract
        ),
        replan_semantic_delta=_dict_or_none(replan_qualification.semantic_delta),
        autonomous_replan_frontier_identity=replan_qualification.frontier_identity,
        effective_autonomous_replan_recorded=effective_recorded,
        blocked_retry=_dict_or_none(blocked_retry),
        vision_checkpoint=dict(vision_checkpoint),
        parsed_fallback_todos=parsed_fallback_todos,
    )


@dataclass(frozen=True)
class DeliveryWorkspaceResolution:
    """Resolved workspace snapshot and supplement-restored delivery attributes."""

    delivery_workspace: dict[str, object] | None
    delivery_outcome: str | None
    delivery_batch_scale: str | None
    progress_observation: dict[str, object] | None
    explore_result: dict[str, object] | None
    explore_result_recorded_at: str | None
    classification: str


def resolve_refresh_delivery_workspace(
    *,
    goal_id: str,
    goal_context: RefreshGoalContext,
    settlement_identity: SettlementIdentity | None,
    settlement_workspace_requirement: Mapping[str, object] | None,
    turn_scoped_settlement_qualified: bool,
    checkpoint_supplement: bool,
    prior_writeback_run: dict[str, object] | None,
    delivery_workspace_path: Path | None,
    blocked_retry: Mapping[str, object] | None,
    parsed_fallback_todos: Mapping[str, object] | None,
    classification: str,
    normalized_delivery_outcome: str | None,
    normalized_delivery_batch_scale: str | None,
    normalized_progress_observation: Mapping[str, object] | None,
    normalized_explore_result: Mapping[str, object] | None,
    capture_delivery_workspace_fn: Callable[..., dict[str, object] | None],
) -> DeliveryWorkspaceResolution:
    """Verify accountable workspace isolation or restore prior supplement delivery state."""
    delivery_workspace: dict[str, object] | None = None
    workspace_requirement = str(
        (settlement_workspace_requirement or {}).get("requirement") or "unknown"
    )
    if (
        delivery_workspace_path is not None
        and workspace_requirement == "not_required"
    ):
        raise ValueError(
            "--delivery-workspace-path conflicts with the original Todo's "
            "explicit non-delivery settlement contract"
        )
    if (
        turn_scoped_settlement_qualified
        and workspace_requirement != "not_required"
        and not checkpoint_supplement
        and not (
            isinstance(blocked_retry, Mapping)
            and blocked_retry.get("schema_version")
            in {"quota_monitor_unavailable_v0", "quota_blocked_lifecycle_v0"}
        )
    ):
        delivery_workspace = capture_delivery_workspace_fn(
            current_path=delivery_workspace_path,
            peer_independent_worktree_required=False,
            local_goal_id=goal_id,
            local_project_root=goal_context.resolved_project,
            repository_source=(
                "refresh_state.delivery_workspace_path"
                if delivery_workspace_path is not None
                else None
            ),
        )
        selected_contract: dict[str, object] = {}
        if settlement_identity is not None and settlement_identity.todo_id:
            workspace_todo_fields: Mapping[str, object] | None = (
                goal_context.todo_fields
                if goal_context.todo_fields is not None
                else parsed_fallback_todos
            )
            if workspace_todo_fields is None:
                workspace_todo_fields = parse_active_state_todos(
                    goal_context.state_text,
                    goal=goal_context.registry_goal,
                    state_path=goal_context.resolved_state_file,
                    preferred_todo_ids={settlement_identity.todo_id},
                    rollout_events=goal_context.planning_events,
                    item_limit=None,
                )
            agent_todos = _dict_or_empty(workspace_todo_fields.get("agent_todos"))
            items = agent_todos.get("items")
            for item in items if isinstance(items, list) else []:
                if (
                    isinstance(item, Mapping)
                    and item.get("todo_id") == settlement_identity.todo_id
                ):
                    selected_contract = dict(item)
                    break
        raw_task_repo = selected_contract.get("task_repository")
        task_repository = str(raw_task_repo) if isinstance(raw_task_repo, str) else None
        explicit_req = goal_context.explicit_peer_worktree_requirement
        delivery_workspace, peer_independent_worktree_required = (
            qualify_delivery_workspace_isolation(
                delivery_workspace,
                multi_agent_goal=goal_context.multi_agent_goal,
                explicit_peer_worktree_requirement=(
                    explicit_req if isinstance(explicit_req, bool) else None
                ),
                task_repository=task_repository,
            )
        )
        if peer_independent_worktree_required and (
            delivery_workspace is None
            or delivery_workspace.get("workspace_kind")
            != "independent_git_worktree"
        ):
            raise ValueError(
                "accountable peer delivery must be refreshed from the independent "
                "git worktree that produced it, or name that worktree with "
                "--delivery-workspace-path"
            )
        if delivery_workspace is None:
            raise ValueError(
                "delivery workspace could not be verified; registered local Goal "
                f"workspace: {str(goal_context.resolved_project)!r}. If this delivery belongs to "
                "that workspace, retry from it or pass it with --delivery-workspace-path. "
                "For a selected repository delivery, use the actual producing worktree; "
                "the registered Goal workspace does not replace that requirement."
            )
    explore_result_recorded_at: str | None = None
    outcome = normalized_delivery_outcome
    batch_scale = normalized_delivery_batch_scale
    progress_obs = (
        dict(normalized_progress_observation)
        if normalized_progress_observation is not None
        else None
    )
    explore = (
        dict(normalized_explore_result)
        if normalized_explore_result is not None
        else None
    )
    final_classification = classification
    if checkpoint_supplement:
        assert prior_writeback_run is not None
        delivery_workspace = _dict_or_none(prior_writeback_run.get("delivery_workspace"))
        raw_outcome = prior_writeback_run.get("delivery_outcome")
        outcome = str(raw_outcome) if isinstance(raw_outcome, str) else None
        raw_scale = prior_writeback_run.get("delivery_batch_scale")
        batch_scale = str(raw_scale) if isinstance(raw_scale, str) else None
        progress_obs = _dict_or_none(prior_writeback_run.get("progress_observation"))
        explore = _dict_or_none(prior_writeback_run.get("explore_result"))
        raw_recorded_at = prior_writeback_run.get(
            "explore_result_recorded_at", prior_writeback_run["generated_at"]
        )
        explore_result_recorded_at = (
            str(raw_recorded_at) if raw_recorded_at is not None else None
        )
        final_classification = str(prior_writeback_run["classification"])
    elif explore is not None:
        explore_result_recorded_at = now_local_iso(timespec="microseconds")
    return DeliveryWorkspaceResolution(
        delivery_workspace=delivery_workspace,
        delivery_outcome=outcome,
        delivery_batch_scale=batch_scale,
        progress_observation=progress_obs,
        explore_result=explore,
        explore_result_recorded_at=explore_result_recorded_at,
        classification=final_classification,
    )
