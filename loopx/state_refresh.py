"""Run-history ``refresh-state`` orchestration, record projection, and public exports."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path

from .agent_registry import registered_agent_ids_for_goal
from .control_plane.agents.workspace_guard import (
    capture_delivery_workspace as capture_delivery_workspace,
)
from .control_plane.effect_runtime import effect_runtime_request_scope
from .control_plane.goals.checkpoint_context_io import (
    checkpoint_commit_guard,
    require_complete_checkpoint_index as require_complete_checkpoint_index,
)
from .control_plane.goals.state_resolution import resolve_goal_state as resolve_goal_state
from .control_plane.progress_scope import AGENT_LANE_PROGRESS_SCOPE
from .control_plane.quota.accounting_admission import quota_accounting_admission
from .control_plane.quota.codex_session_usage import (
    book_codex_session_usage as book_codex_session_usage,
)
from .control_plane.quota.execution_preparation import guard_first_writeback_execution
from .control_plane.quota.refresh_external_delivery import (
    finish_external_delivery_refresh,
    persist_external_delivery_pause,
)
from .control_plane.quota.settlement import (
    attach_settlement_progress,
    read_heartbeat_settlement as read_heartbeat_settlement,
)
from .control_plane.quota.usage_collector import (
    ingest_usage_into_run_record as ingest_usage_into_run_record,
)
from .control_plane.runtime.run_artifacts import run_file_stem as run_file_stem
from .control_plane.runtime.time import now_local_iso as now_local_iso
from .control_plane.todos.contract import normalize_todo_replan_obligation_id
from .control_plane.todos.provider_projection import recover_refresh_todo_projection
from .control_plane.turn_driver.delivery_continuity import normalize_delivery_boundary
from .control_plane.work_items.delivery_batch_scale import (
    DELIVERY_BATCH_SCALE_CHOICES as DELIVERY_BATCH_SCALE_CHOICES,
    require_delivery_batch_scale,
)
from .control_plane.work_items.delivery_history import require_consistent_delivery_claim
from .control_plane.work_items.delivery_outcome import (
    DELIVERY_OUTCOME_CHOICES as DELIVERY_OUTCOME_CHOICES,
    TURN_SCOPED_SETTLEMENT_GAP_FALLBACK,
    explain_turn_scoped_settlement_gap,
    qualifies_turn_scoped_settlement,
    require_delivery_outcome,
)
from .control_plane.work_items.progress_observation import normalize_progress_observation
from .control_plane.work_items.recommendation_source_io import (
    recommendation_source_context,
    recommendation_source_guard,
)
from .control_plane.work_items.refresh_recommendation import (
    DEFAULT_REFRESH_ACTION as DEFAULT_REFRESH_ACTION,
    RECOMMENDED_ACTION_SOURCE_ACTIVE_NEXT_ACTION as RECOMMENDED_ACTION_SOURCE_ACTIVE_NEXT_ACTION,
    RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO as RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO,
    RECOMMENDED_ACTION_SOURCE_AGENT_TODO_FALLBACK as RECOMMENDED_ACTION_SOURCE_AGENT_TODO_FALLBACK,
    RECOMMENDED_ACTION_SOURCE_DEFAULT as RECOMMENDED_ACTION_SOURCE_DEFAULT,
    RECOMMENDED_ACTION_SOURCE_EXPLICIT as RECOMMENDED_ACTION_SOURCE_EXPLICIT,
    RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO as RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO,
    derive_recommended_action as derive_recommended_action,
    derive_recommended_action_with_source as derive_recommended_action_with_source,
    load_refresh_planning_source as load_refresh_planning_source,
    resolve_refresh_recommendation,
)
from .control_plane.work_items.repair_delta import (
    REPAIR_DELTA_CONTRACT_SCHEMA_VERSION as REPAIR_DELTA_CONTRACT_SCHEMA_VERSION,
    REPAIR_DELTA_KIND_CHOICES as REPAIR_DELTA_KIND_CHOICES,
    normalize_repair_delta_kinds,
)
from .control_plane.work_items.semantic_replan_writeback import (
    qualify_refresh_replan_writeback as qualify_refresh_replan_writeback,
)
from .feedback import validate_public_safe_text
from .history import (
    append_run_index_record,
    load_index_snapshot as load_index_snapshot,
    load_registry as load_registry,
    reserve_unique_run_paths as reserve_unique_run_paths,
    unique_run_paths,
)
from .paths import resolve_runtime_root
from .presentation.renderers.state_refresh_markdown import (
    render_state_refresh_markdown as render_state_refresh_markdown,
)
from .refresh_state.admission import (
    admit_or_recover_turn_settlement,
    load_and_validate_goal_context,
    normalize_explore_attachment,
    prepare_explore_attachment,
    resolve_registry_and_request_work_id,
)
from .refresh_state.post_commit import (
    REPAIR_NOOP_SCHEMA_VERSION as REPAIR_NOOP_SCHEMA_VERSION,
    attach_post_record_qualification_metadata,
    attach_refresh_usage_to_projections,
    build_refresh_dry_run_packet,
    commit_checkpoint_supplement_run,
    sync_refresh_global_and_shared_projection,
)
from .refresh_state.qualification import (
    qualify_vision_replan_and_completion,
    resolve_refresh_delivery_workspace,
)
from .refresh_state.records import build_state_refresh_record as build_state_refresh_record
from .refresh_state.text_parsing import (
    BULLET_PREFIX_RE as BULLET_PREFIX_RE,
    CHECKBOX_PREFIX_RE as CHECKBOX_PREFIX_RE,
    clean_action_line as clean_action_line,
    extract_section_lines as extract_section_lines,
    first_action_item as first_action_item,
    is_bullet_line as is_bullet_line,
    normalize_next_action_text as normalize_next_action_text,
    parse_frontmatter as parse_frontmatter,
    replace_updated_at as replace_updated_at,
    section_list_items as section_list_items,
)
from .registry import (
    registry_goals as registry_goals,
    resolve_state_file as resolve_state_file,
)
from .runtime import validate_goal_id_path_segment

DEFAULT_REFRESH_CLASSIFICATION = "state_refreshed"
GOAL_PROGRESS_SCOPE = "goal"
PROGRESS_SCOPE_CHOICES = (GOAL_PROGRESS_SCOPE, AGENT_LANE_PROGRESS_SCOPE)


def now_local() -> str:
    return now_local_iso()


def registered_agents_for_goal(registry_goal: Mapping[str, object] | None) -> list[str]:
    return registered_agent_ids_for_goal(
        dict(registry_goal) if registry_goal is not None else None
    )


def normalize_progress_scope(value: str | None) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    validate_public_safe_text("progress_scope", normalized)
    if normalized not in PROGRESS_SCOPE_CHOICES:
        raise ValueError(
            "--progress-scope must be one of: " + ", ".join(PROGRESS_SCOPE_CHOICES)
        )
    return normalized


def _build_state_refresh_output_projections(
    *,
    record: Mapping[str, object],
    registry_path: Path,
    runtime_root: Path,
    project: Path | None,
    json_path: Path,
    markdown_path: Path,
    index_path: Path,
    dry_run: bool,
    autonomous_replan_recorded_requested: bool,
) -> tuple[dict[str, object], dict[str, object]]:
    """Project one refresh record into its compact index and CLI response."""
    raw_state = record.get("state")
    record_state = dict(raw_state) if isinstance(raw_state, Mapping) else {}
    raw_frontmatter = record_state.get("frontmatter")
    record_frontmatter = (
        dict(raw_frontmatter) if isinstance(raw_frontmatter, Mapping) else {}
    )
    index_record: dict[str, object] = {
        field: record[field]
        for field in (
            "generated_at",
            "goal_id",
            "classification",
            "recommended_action",
            "recommended_action_source",
            "health_check",
        )
    }
    index_record.update(
        {
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "state": {
                "sha256_16": record_state.get("sha256_16"),
                "frontmatter": {"updated_at": record_frontmatter.get("updated_at")},
            },
            "runtime_projection_route": record["runtime_projection_route"],
        }
    )
    for field in (
        "recommended_action_resolution",
        "delivery_batch_scale",
        "delivery_outcome",
        "delivery_workspace",
        "settlement_identity",
        "todo_source",
        "blocked_retry",
        "refresh_recovery",
        "turn_instance_id",
        "todo_id",
        "replan_obligation_id",
    ):
        if field in record:
            index_record[field] = record[field]

    raw_ack = record.get("autonomous_replan_ack")
    replan_ack = dict(raw_ack) if isinstance(raw_ack, Mapping) else {}
    if autonomous_replan_recorded_requested or replan_ack.get("recorded") is True:
        index_record["autonomous_replan_ack"] = replan_ack
        if replan_ack.get("requested_classification"):
            index_record["requested_classification"] = replan_ack[
                "requested_classification"
            ]

    agent_vision = record.get("agent_vision")
    if isinstance(agent_vision, Mapping):
        projected_vision: dict[str, object] = {
            field: agent_vision.get(field)
            for field in (
                "schema_version",
                "agent_id",
                "state",
                "vision_patch",
                "todo_delta",
                "fallback_declarations",
                "stride_decision",
                "vision_budget",
            )
        }
        path_delta = agent_vision.get("path_delta")
        if isinstance(path_delta, Mapping):
            projected_vision["path_delta"] = dict(path_delta)
        index_record["agent_vision"] = projected_vision

    for field in (
        "vision_checkpoint",
        "progress_observation",
        "explore_result",
        "explore_result_recorded_at",
        "explore_result_source_snapshot",
        "progress_scope",
        "agent_id",
        "agent_lane",
    ):
        if field in record:
            index_record[field] = record[field]

    payload: dict[str, object] = {
        "ok": True,
        "dry_run": dry_run,
        "appended": not dry_run,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "project": str(project) if project else None,
    }
    payload.update(
        {
            field: record.get(field)
            for field in (
                "goal_id",
                "classification",
                "progress_scope",
                "agent_id",
                "agent_lane",
            )
        }
    )
    payload.update(
        {
            "autonomous_replan_recorded": bool(replan_ack.get("recorded")),
            "autonomous_replan_recorded_requested": autonomous_replan_recorded_requested,
            "repair_delta_contract": replan_ack.get("delta_contract"),
            "json_path": str(json_path),
            "markdown_path": str(markdown_path),
            "index_path": str(index_path),
        }
    )
    payload.update(
        {
            field: record.get(field)
            for field in (
                "agent_vision",
                "vision_checkpoint",
                "recommended_action",
                "recommended_action_source",
                "generated_at",
                "health_check",
            )
        }
    )
    payload.update(record)
    return index_record, payload


@effect_runtime_request_scope()
def refresh_state_run(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    goal_id: str,
    project: Path | None,
    state_file: Path | None,
    classification: str,
    recommended_action: str | None,
    next_action: str | None = None,
    next_action_basis: str | None = None,
    delivery_batch_scale: str | None = None,
    delivery_outcome: str | None = None,
    delivery_boundary: str | None = None,
    delivery_workspace_path: Path | None = None,
    todo_id: str | None = None,
    turn_instance_id: str | None = None,
    replan_obligation_id: str | None = None,
    agent_id: str | None = None,
    agent_lane: str | None = None,
    progress_scope: str | None = None,
    autonomous_replan_recorded: bool = False,
    repair_delta_kinds: Sequence[str] | None = None,
    agent_vision_packet: Mapping[str, object] | None = None,
    merge_agent_vision_patch: bool = False,
    vision_unchanged_reason: str | None = None,
    checkpoint_read_context_id: str | None = None,
    progress_observation: Mapping[str, object] | None = None,
    explore_result: Mapping[str, object] | None = None,
    completion_todo_id: str | None = None,
    completion_turn_key: str | None = None,
    usage_measurement: Mapping[str, object] | None = None,
    usage_codex_session: Path | None = None,
    dry_run: bool,
    sync_global: bool = True,
    external_delivery: Mapping[str, object] | None = None,
    goal_ref: dict[str, str] | None = None,
    goal_acceptance_preview: Mapping[str, object] | None = None,
    task_lease_proof: Mapping[str, object] | None = None,
    allow_same_generation_lease_renewal: bool = False,
    validation_recovery_turn_instance_id: str | None = None,
) -> dict[str, object]:
    """Validate, qualify, and persist (or preview) one ``refresh-state`` run."""
    safe_goal_id = validate_goal_id_path_segment(goal_id)
    if goal_acceptance_preview is not None and not dry_run:
        raise ValueError("prospective acceptance facts are valid only for dry-run planning")
    if next_action_basis and not next_action:
        raise ValueError("--next-action-basis requires --next-action")
    if checkpoint_read_context_id and not turn_instance_id:
        raise ValueError("--checkpoint-read-context requires the original Turn identity")
    validate_public_safe_text("classification", classification)
    if usage_measurement is not None and usage_codex_session is not None:
        raise ValueError("--usage-json cannot be combined with --usage-codex-session")
    normalized_agent_id = (agent_id or "").strip()
    normalized_agent_lane = (agent_lane or "").strip()
    normalized_replan_obligation_id = normalize_todo_replan_obligation_id(
        replan_obligation_id
    )
    if replan_obligation_id and not normalized_replan_obligation_id:
        raise ValueError("--replan-obligation-id is not a valid typed obligation id")
    if todo_id and normalized_replan_obligation_id:
        raise ValueError("--replan-obligation-id cannot be combined with --todo-id")
    if normalized_agent_id:
        validate_public_safe_text("agent_id", normalized_agent_id)
    if normalized_agent_lane:
        validate_public_safe_text("agent_lane", normalized_agent_lane)
    if normalized_agent_lane and not normalized_agent_id:
        raise ValueError("--agent-lane requires --agent-id so the lane has an owner")
    normalized_progress_scope = normalize_progress_scope(progress_scope)
    normalized_delivery_batch_scale = (
        require_delivery_batch_scale(delivery_batch_scale).value if delivery_batch_scale else None
    )
    normalized_delivery_outcome = (
        require_delivery_outcome(delivery_outcome).value if delivery_outcome else None
    )
    normalized_delivery_boundary = normalize_delivery_boundary(delivery_boundary)
    normalized_repair_delta_kinds = normalize_repair_delta_kinds(
        list(repair_delta_kinds) if repair_delta_kinds is not None else None
    )
    normalized_explore_result = (
        normalize_explore_attachment(explore_result) if explore_result is not None else None
    )
    prepared_explore_source: dict[str, object] = {}
    preloaded_registry, preloaded_runtime, request_work_id = (
        resolve_registry_and_request_work_id(
            registry_path=registry_path, runtime_root_override=runtime_root_override,
            goal_id=safe_goal_id, agent_id=normalized_agent_id, todo_id=todo_id,
            replan_obligation_id=normalized_replan_obligation_id,
            turn_instance_id=turn_instance_id, goal_ref=goal_ref,
            load_registry_fn=load_registry,
        )
    )
    normalized_progress_observation: dict[str, object] | None = (
        dict(
            normalize_progress_observation(
                dict(progress_observation),
                work_item_id=todo_id or normalized_replan_obligation_id or request_work_id,
            )
        )
        if progress_observation is not None else None
    )
    require_consistent_delivery_claim({
        "delivery_outcome": normalized_delivery_outcome,
        "progress_observation": normalized_progress_observation,
    })
    turn_scoped_settlement_qualified = qualifies_turn_scoped_settlement(
        normalized_delivery_outcome, normalized_progress_observation,
        work_item_id=todo_id or request_work_id,
        replan_obligation_id=normalized_replan_obligation_id,
    )
    turn_scoped_settlement_gap = (
        TURN_SCOPED_SETTLEMENT_GAP_FALLBACK
        if turn_scoped_settlement_qualified
        else explain_turn_scoped_settlement_gap(
            normalized_delivery_outcome, normalized_progress_observation,
            work_item_id=todo_id or request_work_id,
            replan_obligation_id=normalized_replan_obligation_id,
        ) or TURN_SCOPED_SETTLEMENT_GAP_FALLBACK
    )
    if delivery_workspace_path is not None and not turn_scoped_settlement_qualified:
        raise ValueError(
            "--delivery-workspace-path requires a progress outcome or a typed "
            "blocked outcome_gap settlement: " + turn_scoped_settlement_gap
        )
    registry = preloaded_registry if preloaded_registry is not None else dict(load_registry(registry_path))
    runtime_root = (
        preloaded_runtime if preloaded_runtime is not None
        else resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    )
    with quota_accounting_admission(
        runtime_root=runtime_root, registry_path=registry_path, goal_id=safe_goal_id,
        goal_ref=goal_ref, operation="refresh-state",
        lock_legacy_index=not dry_run, handoff_legacy_index=not dry_run,
    ) as source_admission, ExitStack() as research_write_guard:
        admission = admit_or_recover_turn_settlement(
            runtime_root=runtime_root, registry_path=registry_path, goal_id=safe_goal_id,
            agent_id=normalized_agent_id, todo_id=todo_id, turn_instance_id=turn_instance_id,
            replan_obligation_id=normalized_replan_obligation_id, goal_ref=goal_ref,
            source_admission=source_admission,
            turn_scoped_settlement_qualified=turn_scoped_settlement_qualified,
            turn_scoped_settlement_gap=turn_scoped_settlement_gap,
            checkpoint_read_context_id=checkpoint_read_context_id,
            external_delivery=external_delivery, agent_vision_packet=agent_vision_packet,
            vision_unchanged_reason=vision_unchanged_reason,
            merge_agent_vision_patch=merge_agent_vision_patch,
            workspace_requested=delivery_workspace_path is not None,
            normalized_explore_result=normalized_explore_result,
            next_action=next_action, next_action_basis=next_action_basis,
            autonomous_replan_recorded=autonomous_replan_recorded,
            repair_delta_kinds=repair_delta_kinds, usage_measurement=usage_measurement,
            usage_codex_session=usage_codex_session,
            normalized_delivery_outcome=normalized_delivery_outcome,
            normalized_delivery_batch_scale=normalized_delivery_batch_scale,
            normalized_delivery_boundary=normalized_delivery_boundary,
            normalized_progress_observation=normalized_progress_observation, dry_run=dry_run,
            require_complete_checkpoint_index_fn=require_complete_checkpoint_index,
            read_heartbeat_settlement_fn=read_heartbeat_settlement,
        )
        if admission.recovery_payload is not None:
            return dict(
                recover_refresh_todo_projection(
                    admission.recovery_payload, registry_path=registry_path, runtime_root=runtime_root,
                    goal_id=safe_goal_id, project=project, state_file=state_file, registry_data=registry,
                )
            )
        if normalized_explore_result is not None:
            normalized_explore_result = prepare_explore_attachment(
                normalized_explore_result, registry_path=registry_path, runtime_root=runtime_root,
                goal_id=goal_id, agent_id=normalized_agent_id, todo_id=todo_id,
                turn_instance_id=turn_instance_id, prepared_source=prepared_explore_source,
            )
        normalized_next_action = normalize_next_action_text(next_action) if next_action else None
        goal_context = load_and_validate_goal_context(
            registry=registry, registry_path=registry_path, runtime_root=runtime_root,
            goal_id=safe_goal_id, project=project, state_file=state_file, sync_global=sync_global,
            next_action=next_action, normalized_next_action=normalized_next_action,
            normalized_agent_id=normalized_agent_id, settlement_readback=admission.settlement_readback,
            goal_acceptance_preview=goal_acceptance_preview, research_write_guard=research_write_guard,
            load_refresh_planning_source_fn=load_refresh_planning_source,
            registered_agents_fn=registered_agents_for_goal,
        )
        if not normalized_progress_scope:
            normalized_progress_scope = AGENT_LANE_PROGRESS_SCOPE if normalized_agent_id else GOAL_PROGRESS_SCOPE
        if normalized_progress_scope == AGENT_LANE_PROGRESS_SCOPE and not normalized_agent_id:
            raise ValueError("--progress-scope agent_lane requires --agent-id")
        if normalized_progress_scope == GOAL_PROGRESS_SCOPE and normalized_agent_lane:
            raise ValueError("--agent-lane requires --progress-scope agent_lane")
        if (agent_vision_packet is not None or vision_unchanged_reason) and not normalized_agent_id:
            raise ValueError("vision writeback requires --agent-id")
        qualification = qualify_vision_replan_and_completion(
            runtime_root=runtime_root, registry_path=registry_path, goal_id=safe_goal_id,
            goal_context=goal_context, settlement_identity=admission.settlement_identity,
            settlement_readback=admission.settlement_readback,
            checkpoint_supplement=admission.checkpoint_supplement,
            prior_writeback_run=admission.prior_writeback_run,
            recovering_writeback_path=admission.recovering_writeback_path,
            source_admission=source_admission, goal_ref=goal_ref,
            normalized_agent_id=normalized_agent_id, recommended_action=recommended_action,
            normalized_next_action=normalized_next_action, next_action_basis=next_action_basis,
            classification=classification, normalized_delivery_outcome=normalized_delivery_outcome,
            normalized_delivery_boundary=normalized_delivery_boundary,
            autonomous_replan_recorded=autonomous_replan_recorded,
            normalized_repair_delta_kinds=normalized_repair_delta_kinds,
            agent_vision_packet=agent_vision_packet, merge_agent_vision_patch=merge_agent_vision_patch,
            vision_unchanged_reason=vision_unchanged_reason,
            normalized_progress_observation=normalized_progress_observation,
            completion_todo_id=completion_todo_id, completion_turn_key=completion_turn_key,
            dry_run=dry_run, load_index_snapshot_fn=load_index_snapshot,
            now_local_fn=now_local,
            qualify_refresh_replan_writeback_fn=qualify_refresh_replan_writeback,
        )
        workspace = resolve_refresh_delivery_workspace(
            goal_id=safe_goal_id, goal_context=goal_context,
            settlement_identity=admission.settlement_identity,
            settlement_workspace_requirement=admission.settlement_workspace_requirement,
            turn_scoped_settlement_qualified=turn_scoped_settlement_qualified,
            checkpoint_supplement=admission.checkpoint_supplement,
            prior_writeback_run=admission.prior_writeback_run,
            delivery_workspace_path=delivery_workspace_path, blocked_retry=qualification.blocked_retry,
            parsed_fallback_todos=qualification.parsed_fallback_todos,
            classification=qualification.classification,
            normalized_delivery_outcome=qualification.normalized_delivery_outcome,
            normalized_delivery_batch_scale=normalized_delivery_batch_scale,
            normalized_progress_observation=normalized_progress_observation,
            normalized_explore_result=normalized_explore_result,
            capture_delivery_workspace_fn=capture_delivery_workspace,
        )
        raw_snapshot = prepared_explore_source.get("source_snapshot")
        raw_prior_snap = (
            admission.prior_writeback_run.get("explore_result_source_snapshot")
            if admission.checkpoint_supplement and admission.prior_writeback_run is not None
            else None
        )
        explore_source_snapshot = (
            dict(raw_snapshot)
            if isinstance(raw_snapshot, Mapping)
            and not (admission.checkpoint_supplement and admission.prior_writeback_run)
            else (dict(raw_prior_snap) if isinstance(raw_prior_snap, Mapping) else None)
        )
        record = build_state_refresh_record(
            goal_id=safe_goal_id, state_file=goal_context.resolved_state_file,
            state_text=goal_context.state_text, classification=workspace.classification,
            recommended_action=qualification.action,
            recommended_action_source=qualification.recommended_action_source,
            recommended_action_resolution=qualification.recommendation_resolution,
            generated_at=qualification.generated_at, registry_goal=goal_context.registry_goal,
            delivery_batch_scale=workspace.delivery_batch_scale,
            delivery_outcome=workspace.delivery_outcome, progress_scope=normalized_progress_scope,
            agent_id=normalized_agent_id or None, agent_lane=normalized_agent_lane or None,
            autonomous_replan_recorded=qualification.effective_autonomous_replan_recorded,
            repair_delta_contract=qualification.repair_delta_contract,
            autonomous_replan_frontier_identity=qualification.autonomous_replan_frontier_identity,
            agent_vision=qualification.agent_vision, vision_checkpoint=qualification.vision_checkpoint,
            progress_observation=workspace.progress_observation, explore_result=workspace.explore_result,
            explore_result_recorded_at=workspace.explore_result_recorded_at,
            explore_result_source_snapshot=explore_source_snapshot,
            delivery_workspace=workspace.delivery_workspace,
            settlement_identity=admission.settlement_identity, todo_fields=goal_context.todo_fields,
        )
        compact_route = attach_post_record_qualification_metadata(
            record=record, goal_id=safe_goal_id, normalized_agent_id=normalized_agent_id,
            agent_vision=qualification.agent_vision, existing_agent_vision=qualification.existing_agent_vision,
            vision_checkpoint=qualification.vision_checkpoint, checkpoint_supplement=admission.checkpoint_supplement,
            prior_writeback_run=admission.prior_writeback_run,
            canonical_snapshot=goal_context.planning_source.canonical_snapshot,
            blocked_retry=qualification.blocked_retry,
            delivery_workspace_causality=admission.delivery_workspace_causality,
            refresh_recovery=admission.refresh_recovery,
            settlement_workspace_requirement=admission.settlement_workspace_requirement,
            autonomous_replan_recorded=autonomous_replan_recorded,
            repair_delta_contract=qualification.repair_delta_contract,
            autonomous_replan_frontier_identity=qualification.autonomous_replan_frontier_identity,
            requested_classification=qualification.requested_classification,
            classification=workspace.classification, replan_semantic_delta=qualification.replan_semantic_delta,
            runtime_projection_route=goal_context.runtime_projection_route, sync_global=sync_global,
        )
        runs_dir = runtime_root / "goals" / safe_goal_id / "runs"
        json_path, markdown_path = unique_run_paths(runs_dir, qualification.generated_at)
        index_path = runs_dir / "index.jsonl"
        index_record, payload = _build_state_refresh_output_projections(
            record=record, registry_path=registry_path, runtime_root=runtime_root,
            project=goal_context.resolved_project, json_path=json_path,
            markdown_path=markdown_path, index_path=index_path, dry_run=dry_run,
            autonomous_replan_recorded_requested=bool(autonomous_replan_recorded),
        )
        if goal_ref is not None:
            for projection in (record, index_record, payload):
                projection["goal_ref"] = dict(goal_ref)
        with ExitStack() as usage_booking_guard:
            current_goal: dict[str, object] | None = None
            if normalized_next_action:
                current_goal, current_text = usage_booking_guard.enter_context(
                    recommendation_source_guard(
                        registry_path, goal_context.resolved_state_file, safe_goal_id,
                        source_registry=goal_context.next_action_source_registry,
                    )
                )
                if current_goal is None:
                    raise ValueError("Next Action source Goal disappeared; reread status")
                current_planning = load_refresh_planning_source(
                    runtime_root, safe_goal_id, goal_context.resolved_state_file,
                    require_display=False, registry_path=goal_context.next_action_source_registry,
                    state_text=current_text, oracle_registry_is_source=True,
                )
                raw_basis = qualification.recommendation_resolution.get("read_basis")
                resolve_refresh_recommendation(
                    current_text, todo_fields=current_planning.todo_fields,
                    agent_id=normalized_agent_id or None,
                    settlement_identity=(
                        admission.settlement_identity.as_dict() if admission.settlement_identity else None
                    ),
                    registry_goal=current_goal, state_path=goal_context.resolved_state_file,
                    rollout_events=current_planning.events, next_step=normalized_next_action,
                    next_step_basis=str(raw_basis) if isinstance(raw_basis, str) else None,
                    source_context=recommendation_source_context(
                        current_goal, current_text,
                        source_registry=goal_context.next_action_source_registry,
                        todo_fields=current_planning.todo_fields,
                    ),
                    runs=qualification.newest_first_runs,
                )
            if admission.checkpoint_supplement:
                assert admission.settlement_identity is not None
                context = usage_booking_guard.enter_context(
                    checkpoint_commit_guard(
                        runtime_root=runtime_root, registry_path=registry_path,
                        state_file=goal_context.resolved_state_file,
                        identity=admission.settlement_identity,
                        read_context_id=checkpoint_read_context_id, goal_ref=goal_ref,
                    )
                )
                for projection in (record, index_record, payload):
                    existing_vc = projection.get("vision_checkpoint")
                    vc_dict = dict(existing_vc) if isinstance(existing_vc, Mapping) else {}
                    projection["vision_checkpoint"] = {**vc_dict, "read_context": context}
            attach_refresh_usage_to_projections(
                usage_booking_guard=usage_booking_guard,
                record=record, index_record=index_record, payload=payload,
                runs_dir=runs_dir, index_path=index_path,
                normalized_agent_id=normalized_agent_id, dry_run=dry_run,
                usage_codex_session=usage_codex_session, usage_measurement=usage_measurement,
                book_codex_session_usage_fn=book_codex_session_usage,
                ingest_usage_into_run_record_fn=ingest_usage_into_run_record,
            )
            if dry_run:
                payload["local_state_write_correctness"] = build_refresh_dry_run_packet(
                    goal_id=safe_goal_id, writer_id=normalized_agent_id or "loopx.refresh-state",
                    classification=workspace.classification, state_text=goal_context.state_text,
                    sync_global=sync_global, route_status=goal_context.route_status,
                    shared_runtime_root=goal_context.shared_runtime_root,
                )
            if not dry_run:
                if (
                    admission.settlement_identity is not None
                    and admission.settlement_identity.todo_id
                    and not admission.checkpoint_supplement
                    and (
                        qualification.blocked_retry is None or task_lease_proof is not None
                        or (admission.settlement_readback is not None and admission.settlement_readback.prepared_lease is not None)
                    )
                ):
                    assert admission.settlement_readback is not None
                    usage_booking_guard.enter_context(
                        guard_first_writeback_execution(
                            runtime_root=runtime_root, registry_path=registry_path, goal_id=safe_goal_id,
                            todo_id=admission.settlement_identity.todo_id,
                            agent_id=admission.settlement_identity.agent_id,
                            requested_proof=dict(task_lease_proof) if task_lease_proof is not None else None,
                            settlement=admission.settlement_readback,
                            locked_goal=current_goal if normalized_next_action else None,
                            allow_same_generation_lease_renewal=allow_same_generation_lease_renewal,
                            validation_recovery_turn_instance_id=validation_recovery_turn_instance_id,
                        )
                    )
                if admission.settlement_readback is not None:
                    persist_external_delivery_pause(admission.settlement_readback, runtime_root, goal_ref=goal_ref)
                generated_at = qualification.generated_at
                runs_dir.mkdir(parents=True, exist_ok=True)
                json_path, markdown_path = reserve_unique_run_paths(runs_dir, generated_at)
                index_record["json_path"] = str(json_path)
                index_record["markdown_path"] = str(markdown_path)
                payload["json_path"] = str(json_path)
                payload["markdown_path"] = str(markdown_path)
                if admission.checkpoint_supplement:
                    assert admission.settlement_identity is not None
                    assert admission.refresh_retry_request is not None
                    commit_checkpoint_supplement_run(
                        runtime_root=runtime_root, registry_path=registry_path,
                        state_file=goal_context.resolved_state_file, identity=admission.settlement_identity,
                        refresh_retry=admission.refresh_retry_request, record=record,
                        index_record=index_record, payload=payload,
                        render_markdown_fn=render_state_refresh_markdown,
                        goal_ref=goal_ref, source_admission=source_admission,
                    )
                else:
                    json_path.write_text(
                        json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                        encoding="utf-8",
                    )
                    markdown_path.write_text(render_state_refresh_markdown(payload) + "\n", encoding="utf-8")
                    append_run_index_record(index_path, index_record, allow_nan=False)
        sync_refresh_global_and_shared_projection(
            payload=payload, record=record, registry_path=registry_path, runtime_root=runtime_root,
            goal_id=safe_goal_id, dry_run=dry_run, sync_global=sync_global,
            route_status=goal_context.route_status, shared_runtime_root=goal_context.shared_runtime_root,
            global_sync_runtime_root=goal_context.global_sync_runtime_root, compact_route=compact_route,
        )
        if admission.settlement_identity is not None and not dry_run:
            committed_readback = read_heartbeat_settlement(
                runtime_root, goal_id=safe_goal_id, agent_id=admission.settlement_identity.agent_id,
                todo_id=admission.settlement_identity.todo_id,
                turn_instance_id=admission.settlement_identity.turn_instance_id,
                replan_obligation_id=admission.settlement_identity.replan_obligation_id,
                registry_path=registry_path, goal_ref=goal_ref,
                source_admission=source_admission, borrow_source_admission=source_admission is not None,
            )
            if committed_readback is None:
                raise RuntimeError("committed refresh settlement readback missing")
            attach_settlement_progress(
                payload, committed_readback, registry_path=registry_path,
                runtime_root=runtime_root, goal_ref=goal_ref,
            )
        return dict(
            recover_refresh_todo_projection(
                finish_external_delivery_refresh(
                    payload, admission.settlement_readback, runtime_root,
                    dry_run=dry_run, goal_ref=goal_ref,
                ),
                registry_path=registry_path, runtime_root=runtime_root, goal_id=safe_goal_id,
                project=goal_context.resolved_project, state_file=goal_context.resolved_state_file,
                canonical_snapshot=goal_context.planning_source.canonical_snapshot,
                registry_data=registry,
            )
        )
