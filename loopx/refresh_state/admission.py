"""Turn settlement admission, Explore attachment bridge, and Goal context loading."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, cast

from ..control_plane.goals.checkpoint_context_io import inspect_checkpoint_replay
from ..control_plane.goals.state_resolution import resolve_goal_state
from ..control_plane.quota.heartbeat_receipt import (
    find_heartbeat_receipt,
    heartbeat_receipt_request_subject,
)
from ..control_plane.quota.refresh_external_delivery import refresh_recovery_payload
from ..control_plane.quota.settlement import QuotaSettlementReadback, SettlementIdentity
from ..control_plane.quota.settlement_workspace_causality import (
    resolve_settlement_workspace_requirement,
)
from ..control_plane.runtime.runtime_projection_route import (
    resolve_runtime_projection_route,
)
from ..control_plane.work_items.delivery_outcome import TURN_SCOPED_SETTLEMENT_REQUIREMENT
from ..control_plane.work_items.recommendation_source_io import (
    load_recommendation_source_goal,
)
from ..control_plane.todos.resume_condition import TodoResumeRolloutEvents
from ..control_plane.work_items.refresh_recommendation import RefreshPlanningSource
from ..file_lock import exclusive_file_lock
from ..paths import resolve_runtime_root


class _ExploreResultWritebackApi(Protocol):
    def normalize_result_attachment(self, attachment: object) -> dict[str, object]: ...

    def prepare_result_attachment(
        self,
        attachment: object,
        *,
        registry_path: Path,
        runtime_root: Path,
        goal_id: str,
        agent_id: str,
        todo_id: str | None,
        turn_instance_id: str | None,
        _prepared_source: dict[str, object] | None = None,
    ) -> dict[str, object]: ...


def normalize_explore_attachment(attachment: Mapping[str, object]) -> dict[str, object]:
    """Normalize an Explore result attachment through the typed capability boundary."""
    from ..capabilities.explore import result_writeback as _explore_result_writeback

    api = cast(_ExploreResultWritebackApi, _explore_result_writeback)
    return dict(api.normalize_result_attachment(dict(attachment)))


def prepare_explore_attachment(
    attachment: Mapping[str, object],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str | None,
    turn_instance_id: str | None,
    prepared_source: dict[str, object],
) -> dict[str, object]:
    """Validate and bind an Explore result attachment prior to refresh persistence."""
    from ..capabilities.explore import result_writeback as _explore_result_writeback

    api = cast(_ExploreResultWritebackApi, _explore_result_writeback)
    return dict(
        api.prepare_result_attachment(
            dict(attachment),
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            _prepared_source=prepared_source,
        )
    )


def _dict_or_none(value: object) -> dict[str, object] | None:
    return dict(value) if isinstance(value, Mapping) else None


def _dict_or_empty(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, Mapping) else {}


def resolve_registry_and_request_work_id(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    agent_id: str,
    todo_id: str | None,
    replan_obligation_id: str | None,
    turn_instance_id: str | None,
    goal_ref: dict[str, str] | None,
    load_registry_fn: Callable[[Path], dict[str, object]],
) -> tuple[dict[str, object] | None, Path | None, str | None]:
    """Resolve optional request-subject work_id while caching loaded registry state."""
    if todo_id or replan_obligation_id or not turn_instance_id or not agent_id:
        return None, None, None
    registry = load_registry_fn(registry_path)
    runtime_root = resolve_runtime_root(
        registry,
        runtime_root_override,
        registry_path=registry_path,
    )
    request_receipt = find_heartbeat_receipt(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        turn_instance_id=turn_instance_id,
        goal_ref=goal_ref,
    )
    request_subject = heartbeat_receipt_request_subject(request_receipt)
    request_work_id: str | None = None
    if request_subject is not None:
        request_work_id = SettlementIdentity(
            goal_id=goal_id,
            agent_id=agent_id,
            todo_id=None,
            turn_instance_id=turn_instance_id,
            request_subject=request_subject,
        ).binding_id
    return registry, runtime_root, request_work_id


@dataclass(frozen=True)
class TurnSettlementAdmission:
    """Admitted Turn settlement state or short-circuit recovery payload."""

    settlement_identity: SettlementIdentity | None = None
    settlement_readback: QuotaSettlementReadback | None = None
    delivery_workspace_causality: dict[str, str] | None = None
    settlement_workspace_requirement: dict[str, object] | None = None
    refresh_recovery: dict[str, object] | None = None
    prior_writeback_run: dict[str, object] | None = None
    checkpoint_supplement: bool = False
    recovering_writeback_path: str | None = None
    refresh_retry_request: dict[str, object] | None = None
    recovery_payload: dict[str, object] | None = None


def admit_or_recover_turn_settlement(
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    todo_id: str | None,
    turn_instance_id: str | None,
    replan_obligation_id: str | None,
    goal_ref: dict[str, str] | None,
    source_admission: dict[str, object] | None,
    turn_scoped_settlement_qualified: bool,
    turn_scoped_settlement_gap: str,
    checkpoint_read_context_id: str | None,
    external_delivery: Mapping[str, object] | None,
    agent_vision_packet: Mapping[str, object] | None,
    vision_unchanged_reason: str | None,
    merge_agent_vision_patch: bool,
    workspace_requested: bool,
    normalized_explore_result: Mapping[str, object] | None,
    next_action: str | None,
    next_action_basis: str | None,
    autonomous_replan_recorded: bool,
    repair_delta_kinds: Sequence[str] | None,
    usage_measurement: Mapping[str, object] | None,
    usage_codex_session: Path | None,
    normalized_delivery_outcome: str | None,
    normalized_delivery_batch_scale: str | None,
    normalized_delivery_boundary: str | None,
    normalized_progress_observation: Mapping[str, object] | None,
    dry_run: bool,
    require_complete_checkpoint_index_fn: Callable[[Path], None],
    read_heartbeat_settlement_fn: Callable[..., QuotaSettlementReadback | None],
) -> TurnSettlementAdmission:
    """Validate and admit a Turn-scoped refresh, or emit a replay/repair payload."""
    if not (todo_id or replan_obligation_id or turn_instance_id):
        return TurnSettlementAdmission()
    if checkpoint_read_context_id or agent_vision_packet or vision_unchanged_reason:
        require_complete_checkpoint_index_fn(
            runtime_root / "goals" / goal_id / "runs" / "index.jsonl"
        )
    if not turn_scoped_settlement_qualified:
        raise ValueError(
            TURN_SCOPED_SETTLEMENT_REQUIREMENT + ": " + turn_scoped_settlement_gap
        )
    mutation: dict[str, object] = {
        "next_action": next_action,
        "autonomous_replan_recorded": autonomous_replan_recorded,
        "repair_delta_kinds": (
            list(repair_delta_kinds) if repair_delta_kinds is not None else None
        ),
        "usage_measurement": (
            dict(usage_measurement) if usage_measurement is not None else None
        ),
        "usage_codex_session": str(usage_codex_session) if usage_codex_session else None,
    }
    if normalized_explore_result is not None:
        mutation["explore_result"] = dict(normalized_explore_result)
    if next_action_basis:
        mutation["next_action_basis"] = next_action_basis
    refresh_retry_request: dict[str, object] = {
        "checkpoint_read_context_id": checkpoint_read_context_id,
        "external_delivery": (
            dict(external_delivery) if external_delivery is not None else None
        ),
        "vision": (
            dict(agent_vision_packet) if agent_vision_packet is not None else None
        ),
        "unchanged_reason": vision_unchanged_reason,
        "merge_patch": bool(merge_agent_vision_patch),
        "workspace_requested": workspace_requested,
        "mutation": mutation,
        "delivery_outcome": normalized_delivery_outcome,
        "delivery_batch_scale": normalized_delivery_batch_scale,
        "delivery_boundary": normalized_delivery_boundary,
        "progress_observation": (
            dict(normalized_progress_observation)
            if normalized_progress_observation is not None
            else None
        ),
    }
    settlement_readback = read_heartbeat_settlement_fn(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id or None,
        todo_id=todo_id,
        turn_instance_id=turn_instance_id,
        replan_obligation_id=replan_obligation_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
        source_admission=source_admission,
        borrow_source_admission=source_admission is not None,
        resolve_original_binding=True,
        refresh_retry=refresh_retry_request,
    )
    if settlement_readback is None:
        raise RuntimeError("exact settlement readback unexpectedly returned not-found")
    settlement_result = settlement_readback.identity
    if settlement_result.failure is not None:
        raise ValueError(settlement_result.failure.reason)
    settlement_identity = settlement_result.value
    if settlement_identity is None:
        raise ValueError("turn-scoped refresh-state has no settlement identity")
    refresh_recovery = _dict_or_none(settlement_readback.refresh_recovery)
    if not refresh_recovery:
        raise RuntimeError("settlement readback omitted refresh recovery admission")
    prior_writeback_run = _dict_or_none(settlement_readback.writeback_run)
    checkpoint_supplement = refresh_recovery["decision"] == "supplement_checkpoint"
    recovering_writeback_path: str | None = None
    if (
        checkpoint_supplement
        and refresh_recovery.get("reason")
        == "complete_missing_replan_qualification_on_original_turn"
    ):
        raw_path = (prior_writeback_run or {}).get("json_path")
        if not isinstance(raw_path, str) or not raw_path:
            raise ValueError(
                "the original blocked writeback has no indexed artifact path"
            )
        recovering_writeback_path = raw_path
    checkpoint_replay: dict[str, object] | None = None
    if (
        refresh_recovery.get("decision") in {"replay", "repair_receipt"}
        and prior_writeback_run is not None
    ):
        checkpoint_replay = inspect_checkpoint_replay(
            runtime_root,
            goal_id,
            prior_writeback_run,
        )
    recovery_payload = refresh_recovery_payload(
        settlement_readback,
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        dry_run=dry_run,
        goal_ref=goal_ref,
    )
    if recovery_payload is not None:
        if checkpoint_replay is not None:
            recovery_payload.update(
                {
                    key: checkpoint_replay[key]
                    for key in ("json_path", "markdown_path")
                }
            )
        return TurnSettlementAdmission(
            settlement_identity=settlement_identity,
            settlement_readback=settlement_readback,
            recovery_payload=recovery_payload,
        )
    if checkpoint_read_context_id and not checkpoint_supplement:
        raise ValueError(
            "--checkpoint-read-context applies only to a missing-checkpoint supplement"
        )
    settlement_workspace_requirement = resolve_settlement_workspace_requirement(
        settlement_readback.workspace_causality,
        settlement_binding_kind=settlement_identity.binding_kind.value,
    )
    return TurnSettlementAdmission(
        settlement_identity=settlement_identity,
        settlement_readback=settlement_readback,
        delivery_workspace_causality=settlement_readback.workspace_causality,
        settlement_workspace_requirement=dict(settlement_workspace_requirement),
        refresh_recovery=refresh_recovery,
        prior_writeback_run=prior_writeback_run,
        checkpoint_supplement=checkpoint_supplement,
        recovering_writeback_path=recovering_writeback_path,
        refresh_retry_request=refresh_retry_request,
    )


@dataclass(frozen=True)
class RefreshGoalContext:
    """Resolved registry route, planning snapshot, and actor policy metadata."""

    runtime_projection_route: dict[str, object]
    route_status: str
    shared_runtime_root: Path | None
    global_sync_runtime_root: Path | None
    next_action_source_registry: Path
    registry_goal: dict[str, object] | None
    resolved_project: Path | None
    resolved_state_file: Path
    planning_source: RefreshPlanningSource
    state_text: str
    planning_events: list[dict[str, object]]
    todo_fields: dict[str, object]
    multi_agent_goal: bool
    explicit_peer_worktree_requirement: object


def load_and_validate_goal_context(
    *,
    registry: dict[str, object],
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    project: Path | None,
    state_file: Path | None,
    sync_global: bool,
    next_action: str | None,
    normalized_next_action: str | None,
    normalized_agent_id: str,
    settlement_readback: QuotaSettlementReadback | None,
    goal_acceptance_preview: Mapping[str, object] | None,
    research_write_guard: ExitStack,
    load_refresh_planning_source_fn: Callable[..., RefreshPlanningSource],
    registered_agents_fn: Callable[[dict[str, object] | None], list[str]],
) -> RefreshGoalContext:
    """Load Goal route and canonical planning snapshot, validating actor membership."""
    runtime_projection_route = resolve_runtime_projection_route(
        registry_path=registry_path,
        goal_id=goal_id,
        source_runtime_root=runtime_root,
    )
    route_status = str(runtime_projection_route.get("status") or "missing")
    route_target_text = str(
        runtime_projection_route.get("target_runtime_root") or ""
    ).strip()
    route_target_root = Path(route_target_text) if route_target_text else None
    shared_runtime_root = (
        route_target_root if sync_global and route_status == "resolved" else None
    )
    global_sync_runtime_root = (
        route_target_root
        if sync_global and route_status in {"resolved", "single_runtime"}
        else None
    )
    next_action_source_registry = registry_path.resolve()
    state_registry: dict[str, object] = registry
    if next_action:
        next_action_source_registry, source_goal = load_recommendation_source_goal(
            registry_path,
            goal_id,
            registry=registry,
        )
        state_registry = {
            **registry,
            "goals": [source_goal] if source_goal is not None else [],
        }
    registry_goal, resolved_project, resolved_state_file = resolve_goal_state(
        registry=state_registry,
        goal_id=goal_id,
        project_override=project,
        state_file_override=state_file,
    )
    spawn_policy = _dict_or_empty((registry_goal or {}).get("spawn_policy"))
    harness = _dict_or_empty(spawn_policy.get("explore_harness"))
    capability_guard = (
        (settlement_readback.semantic_replan_guard or {}).get("selected_capability_guard")
        if settlement_readback is not None
        else None
    )
    if (
        harness.get("enabled") is True
        and harness.get("composition_mode") == "explicit_only"
    ) or capability_guard is not None:
        from ..capabilities.explore.result_log import explore_result_log_path

        research_write_guard.enter_context(
            exclusive_file_lock(
                explore_result_log_path(runtime_root, goal_id),
                agent_id=normalized_agent_id or None,
                operation="research-writeback",
            )
        )
    planning_source = load_refresh_planning_source_fn(
        runtime_root,
        goal_id,
        resolved_state_file,
        require_display=False,
        registry_path=next_action_source_registry,
    )
    todo_fields = dict(planning_source.todo_fields)
    if goal_acceptance_preview is not None:
        raw_agent_todos = todo_fields.get("agent_todos")
        if not isinstance(raw_agent_todos, Mapping):
            raise ValueError("outcome preview requires a canonical planning snapshot")
        todo_fields = {
            **todo_fields,
            "agent_todos": {
                **dict(raw_agent_todos),
                "goal_acceptance_contract": dict(goal_acceptance_preview),
            },
        }
    if normalized_next_action and registry_goal is None:
        raise ValueError(
            "--next-action requires a registry Goal; register its state route first"
        )
    known_agents = {agent for agent in registered_agents_fn(registry_goal) if agent}
    multi_agent_goal = len(known_agents) > 1
    raw_registry_goal: object = registry_goal
    workspace_guard_policy = _dict_or_empty(
        raw_registry_goal.get("workspace_guard_policy")
        if isinstance(raw_registry_goal, Mapping)
        else None
    )
    if normalized_agent_id and known_agents and normalized_agent_id not in known_agents:
        raise ValueError(
            f"agent_id {normalized_agent_id!r} is not registered for goal {goal_id!r}"
        )
    if multi_agent_goal and not normalized_agent_id:
        raise ValueError(
            "multi-agent refresh-state requires --agent-id; text inference is disabled"
        )
    return RefreshGoalContext(
        runtime_projection_route=dict(runtime_projection_route),
        route_status=route_status,
        shared_runtime_root=shared_runtime_root,
        global_sync_runtime_root=global_sync_runtime_root,
        next_action_source_registry=next_action_source_registry,
        registry_goal=_dict_or_none(raw_registry_goal),
        resolved_project=resolved_project,
        resolved_state_file=resolved_state_file,
        planning_source=planning_source,
        state_text=planning_source.state_text,
        planning_events=(
            TodoResumeRolloutEvents(
                planning_source.events,
                runtime_root=Path(planning_source.events.source["runtime_root"]),
                goal_id=str(planning_source.events.source["goal_id"]),
            )
            if isinstance(planning_source.events, TodoResumeRolloutEvents)
            else [dict(item) for item in planning_source.events]
        ),
        todo_fields=todo_fields,
        multi_agent_goal=multi_agent_goal,
        explicit_peer_worktree_requirement=workspace_guard_policy.get(
            "peer_independent_worktree_required"
        ),
    )
