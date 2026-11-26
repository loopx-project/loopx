"""Record construction helpers for ``refresh-state`` run artifacts."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

from ..control_plane.progress_scope import AGENT_LANE_PROGRESS_SCOPE
from ..control_plane.quota.settlement import SettlementIdentity
from ..state_projection import active_state_next_action_entries, state_projection_gap_warning
from .text_parsing import extract_section_lines, parse_frontmatter


def _optional_todo_group(
    todo_fields: Mapping[str, object] | None,
    key: str,
) -> dict[str, object] | None:
    if todo_fields is None:
        return None
    raw = todo_fields.get(key)
    return dict(raw) if isinstance(raw, Mapping) else {}


def build_state_refresh_record(
    *,
    goal_id: str,
    state_file: Path,
    state_text: str,
    classification: str,
    recommended_action: str,
    recommended_action_source: str,
    recommended_action_resolution: Mapping[str, object] | None = None,
    generated_at: str,
    registry_goal: Mapping[str, object] | None,
    delivery_batch_scale: str | None = None,
    delivery_outcome: str | None = None,
    progress_scope: str | None = None,
    agent_id: str | None = None,
    agent_lane: str | None = None,
    autonomous_replan_recorded: bool = False,
    repair_delta_contract: Mapping[str, object] | None = None,
    replan_semantic_delta: Mapping[str, object] | None = None,
    autonomous_replan_frontier_identity: str | None = None,
    agent_vision: Mapping[str, object] | None = None,
    vision_checkpoint: Mapping[str, object] | None = None,
    progress_observation: Mapping[str, object] | None = None,
    explore_result: Mapping[str, object] | None = None,
    explore_result_recorded_at: str | None = None,
    explore_result_source_snapshot: Mapping[str, object] | None = None,
    delivery_workspace: Mapping[str, object] | None = None,
    settlement_identity: SettlementIdentity | None = None,
    todo_fields: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Assemble the durable run record persisted for one ``refresh-state`` call."""
    frontmatter = parse_frontmatter(state_text)
    next_action = active_state_next_action_entries(
        state_text,
        limit=8,
        text_limit=None,
    )
    recent_feedback = extract_section_lines(state_text, "Recent User Feedback", limit=5)
    progress = extract_section_lines(state_text, "Progress Ledger", limit=5)
    digest = hashlib.sha256(state_text.encode("utf-8")).hexdigest()[:16]
    authority_sources: list[object] = []
    if registry_goal and isinstance(registry_goal.get("authority_sources"), list):
        raw_sources = registry_goal.get("authority_sources")
        authority_sources = list(raw_sources) if isinstance(raw_sources, list) else []
    record: dict[str, object] = {
        "generated_at": generated_at,
        "goal_id": goal_id,
        "classification": classification,
        "recommended_action": recommended_action,
        "recommended_action_source": recommended_action_source,
        "health_check": (
            f"state_file 1/1; registry_goal {1 if registry_goal else 0}/1; "
            f"authority_sources {len(authority_sources)}"
        ),
        "state": {
            "path": str(state_file),
            "sha256_16": digest,
            "frontmatter": frontmatter,
            "next_action": next_action,
            "recent_feedback": recent_feedback,
            "progress": progress,
        },
        "registry_goal": {
            "present": bool(registry_goal),
            "domain": registry_goal.get("domain") if registry_goal else None,
            "status": registry_goal.get("status") if registry_goal else None,
            "adapter": registry_goal.get("adapter") if registry_goal else None,
            "authority_source_count": len(authority_sources),
        },
    }
    if recommended_action_resolution:
        record["recommended_action_resolution"] = dict(recommended_action_resolution)
    projection_gap = state_projection_gap_warning(
        state_text,
        # An authoritative empty group must not fall back to stale Markdown.
        user_todos=_optional_todo_group(todo_fields, "user_todos"),
        agent_todos=_optional_todo_group(todo_fields, "agent_todos"),
    )
    if projection_gap:
        record["state_projection_gap"] = projection_gap
    if delivery_batch_scale:
        record["delivery_batch_scale"] = delivery_batch_scale
    if delivery_outcome:
        record["delivery_outcome"] = delivery_outcome
    if delivery_workspace:
        record["delivery_workspace"] = dict(delivery_workspace)
    if settlement_identity:
        record["settlement_identity"] = settlement_identity.as_dict()
        record["turn_instance_id"] = settlement_identity.turn_instance_id
        if settlement_identity.request_subject is not None:
            record["request_subject"] = dict(settlement_identity.request_subject)
        if settlement_identity.todo_id:
            record["todo_id"] = settlement_identity.todo_id
        if settlement_identity.replan_obligation_id:
            record["replan_obligation_id"] = (
                settlement_identity.replan_obligation_id
            )
    if autonomous_replan_recorded:
        ack: dict[str, object] = {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "refresh_state",
        }
        if repair_delta_contract:
            ack["delta_contract"] = dict(repair_delta_contract)
        if replan_semantic_delta:
            ack["semantic_delta"] = dict(replan_semantic_delta)
        if autonomous_replan_frontier_identity:
            ack["frontier_identity"] = autonomous_replan_frontier_identity
        record["autonomous_replan_ack"] = ack
    if agent_vision:
        record["agent_vision"] = dict(agent_vision)
    if vision_checkpoint:
        record["vision_checkpoint"] = dict(vision_checkpoint)
    if explore_result is not None:
        record["explore_result"] = dict(explore_result)
        record["explore_result_recorded_at"] = explore_result_recorded_at or generated_at
        if explore_result_source_snapshot is not None:
            record["explore_result_source_snapshot"] = dict(
                explore_result_source_snapshot
            )
    if progress_observation:
        record["progress_observation"] = dict(progress_observation)
    if progress_scope:
        record["progress_scope"] = progress_scope
    if agent_id:
        record["agent_id"] = agent_id
    if progress_scope == AGENT_LANE_PROGRESS_SCOPE and agent_id:
        record["agent_lane"] = agent_lane or agent_id
    return record
