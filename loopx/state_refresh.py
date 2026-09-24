from __future__ import annotations

import hashlib
import json
import re
from contextlib import ExitStack, nullcontext
from pathlib import Path
from typing import Any

from .control_plane.runtime.time import chronology_key, now_local_iso
from .control_plane.work_items.delivery_history import require_consistent_delivery_claim
from .control_plane.work_items.delivery_batch_scale import (
    DELIVERY_BATCH_SCALE_CHOICES as DELIVERY_BATCH_SCALE_CHOICES,
    require_delivery_batch_scale,
)
from .control_plane.work_items.delivery_outcome import (
    ACCOUNTABLE_DELIVERY_OUTCOMES,
    DELIVERY_OUTCOME_CHOICES as DELIVERY_OUTCOME_CHOICES,
    TURN_SCOPED_SETTLEMENT_GAP_FALLBACK,
    TURN_SCOPED_SETTLEMENT_REQUIREMENT,
    explain_turn_scoped_settlement_gap,
    qualifies_turn_scoped_settlement,
    require_delivery_outcome,
)
from .control_plane.agents.workspace_guard import (
    capture_delivery_workspace,
)
from .control_plane.quota.refresh_external_delivery import (
    finish_external_delivery_refresh, refresh_recovery_payload,
)
from .control_plane.quota.blocked_retry import require_blocked_retry_wait
from .control_plane.coordination.local_authority import local_authority_is_promoted
from .control_plane.todos.active_state_todo_parser import parse_active_state_todos
from .control_plane.quota.settlement import (
    SettlementIdentity,
    attach_settlement_progress,
    read_heartbeat_settlement,
    render_first_refresh_checkpoint_hint,
    render_refresh_recovery_markdown,
    render_settlement_progress_markdown,
)
from .control_plane.quota.settlement_workspace_causality import resolve_settlement_workspace_requirement
from .control_plane.quota.codex_session_usage import (
    book_codex_session_usage,
    usage_booking_lock_target,
)
from .control_plane.quota.usage_collector import ingest_usage_into_run_record
from .control_plane.work_items.repair_delta import (
    REPAIR_DELTA_CONTRACT_SCHEMA_VERSION as REPAIR_DELTA_CONTRACT_SCHEMA_VERSION,
    REPAIR_DELTA_KIND_CHOICES as REPAIR_DELTA_KIND_CHOICES,
    normalize_repair_delta_kinds,
)
from .control_plane.work_items.progress_observation import (
    normalize_progress_observation,
)
from .control_plane.work_items.semantic_replan_writeback import (
    qualify_refresh_replan_writeback,
)
from .capabilities.progress_review.context import external_progress_review_context
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
    load_refresh_planning_source,
    resolve_refresh_recommendation,
)
from .control_plane.runtime.shared_runtime_refresh_projection import (
    build_shared_runtime_projection,
    write_shared_runtime_projection,
)
from .control_plane.runtime.runtime_projection_route import (
    compact_runtime_projection_route,
    resolve_runtime_projection_route,
)
from .feedback import validate_local_control_text, validate_public_safe_text
from .file_lock import exclusive_file_lock, exclusive_cross_runtime_file_lock
from .control_plane.coordination.runtime_shadow_writer_adapter import require_prose_state_write_allowed
from .control_plane.todos.active_state_editing import atomic_write_state_text
from .global_registry import sync_project_registry_to_global
from .history import (
    load_index,
    load_registry,
    reserve_unique_run_paths,
    unique_run_paths,
)
from .control_plane.runtime.local_state_write_correctness import build_local_state_write_correctness_dry_run_packet
from .paths import resolve_runtime_root
from .control_plane.goals.vision_checkpoint import (
    build_vision_checkpoint,
    prepare_vision_refresh,
)
from .control_plane.goals.goal_frontier import latest_agent_vision_from_runs
from .control_plane.goals.checkpoint_context_io import (
    checkpoint_commit_guard, commit_checkpoint_run, require_complete_checkpoint_index, inspect_checkpoint_replay,
)
from .file_lock import exclusive_run_index_lock
from .registry import registry_goals, resolve_state_file
from .runtime import validate_goal_id_path_segment
from .state_projection import (
    active_state_next_action_entries,
    state_projection_gap_warning,
)
from .control_plane.todos.contract import (
    normalize_todo_claimed_by,
    normalize_todo_replan_obligation_id,
)
from .control_plane.todos.completion_validation_accountability import (
    require_accountable_completion_validation,
)
from .control_plane.turn_driver.delivery_continuity import (
    normalize_delivery_boundary,
)

DEFAULT_REFRESH_CLASSIFICATION = "state_refreshed"
GOAL_PROGRESS_SCOPE = "goal"
AGENT_LANE_PROGRESS_SCOPE = "agent_lane"
PROGRESS_SCOPE_CHOICES = (GOAL_PROGRESS_SCOPE, AGENT_LANE_PROGRESS_SCOPE)
BULLET_PREFIX_RE = re.compile(r"^(?:[-*]\s+|\d+[.)]\s+)")
CHECKBOX_PREFIX_RE = re.compile(r"^\[(?P<mark>[ xX])\]\s+")
ACTIVE_STATE_NEXT_ACTION_UPDATE_SCHEMA_VERSION = "active_state_next_action_update_v0"
REPAIR_NOOP_SCHEMA_VERSION = "repair_noop_v0"


def now_local() -> str:
    return now_local_iso()


def run_file_stem(generated_at: str) -> str:
    return re.sub(r"[^0-9A-Za-z-]+", "-", generated_at).strip("-")


def parse_frontmatter(text: str) -> dict[str, str]:
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    values: dict[str, str] = {}
    for line in parts[1].splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        value = value.strip().strip('"')
        values[key.strip()] = value
    return values


def extract_section_lines(text: str, heading: str, limit: int = 8) -> list[str]:
    lines = text.splitlines()
    in_section = False
    collected: list[str] = []
    for line in lines:
        if line.startswith("## "):
            if in_section:
                break
            in_section = line[3:].strip() == heading
            continue
        if in_section and line.strip():
            collected.append(line.strip())
            if len(collected) >= limit:
                break
    return collected


def replace_updated_at(text: str, updated_at: str) -> str:
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    frontmatter = parts[1]
    body = parts[2]
    if re.search(r"(?m)^updated_at:\s*.+$", frontmatter):
        frontmatter = re.sub(
            r"(?m)^updated_at:\s*.+$",
            f"updated_at: {updated_at}",
            frontmatter,
            count=1,
        )
    else:
        frontmatter = frontmatter.rstrip("\n") + f"\nupdated_at: {updated_at}\n"
    return "---" + frontmatter + "---" + body


def normalize_next_action_text(value: str) -> str:
    text = " ".join(str(value or "").strip().split())
    if not text:
        raise ValueError("next_action must not be empty")
    validate_local_control_text("active_state_next_action", text)
    return text


def registered_agents_for_goal(registry_goal: dict[str, Any] | None) -> list[str]:
    coordination = (
        registry_goal.get("coordination")
        if registry_goal and isinstance(registry_goal.get("coordination"), dict)
        else {}
    )
    registered_raw = coordination.get("registered_agents") if isinstance(coordination, dict) else []
    registered_values = registered_raw if isinstance(registered_raw, list) else []
    registered_agents: list[str] = []
    for value in registered_values:
        candidate = value.get("id") if isinstance(value, dict) else value
        normalized = normalize_todo_claimed_by(candidate)
        if normalized:
            registered_agents.append(normalized)
    return registered_agents


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


def next_action_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    for index, line in enumerate(lines):
        if line.strip() != "## Next Action":
            continue
        end = len(lines)
        for next_index in range(index + 1, len(lines)):
            if lines[next_index].startswith("## "):
                end = next_index
                break
        return index, end
    return None


def next_action_insert_anchor(lines: list[str]) -> int:
    preferred = {
        "## Recent User Feedback",
        "## Progress Ledger",
        "## Operating Lessons",
        "## Completed Work Archive",
    }
    for index, line in enumerate(lines):
        if line.strip() in preferred:
            return index
    return len(lines)


def replace_next_action_section(
    state_text: str,
    *,
    next_action: str,
    updated_at: str,
) -> tuple[str, bool]:
    lines = state_text.splitlines()
    section = ["## Next Action", "", f"- {next_action}", ""]
    bounds = next_action_section_bounds(lines)
    if bounds:
        start, end = bounds
        updated_lines = [*lines[:start], *section, *lines[end:]]
    else:
        anchor = next_action_insert_anchor(lines)
        insert = list(section)
        if anchor > 0 and lines[anchor - 1].strip():
            insert.insert(0, "")
        updated_lines = [*lines[:anchor], *insert, *lines[anchor:]]
    section_text = "\n".join(updated_lines).rstrip() + "\n"
    if section_text.rstrip("\n") == state_text.rstrip("\n"):
        return state_text, False
    return replace_updated_at(section_text, updated_at), True


def clean_action_line(line: str) -> str:
    text = BULLET_PREFIX_RE.sub("", line.strip()).strip()
    return CHECKBOX_PREFIX_RE.sub("", text).strip()


def is_bullet_line(line: str) -> bool:
    return bool(BULLET_PREFIX_RE.match(line.strip()))


def first_action_item(lines: list[str], start: int) -> str:
    first_line = lines[start]
    parts = [clean_action_line(first_line)]
    if is_bullet_line(first_line):
        for line in lines[start + 1 :]:
            if is_bullet_line(line):
                break
            if line.strip().startswith("<!--"):
                continue
            cleaned = clean_action_line(line)
            if cleaned:
                parts.append(cleaned)
    return " ".join(part for part in parts if part).strip()


def section_list_items(lines: list[str]) -> list[str]:
    items: list[str] = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if is_bullet_line(line):
            item = first_action_item(lines, index)
            if item:
                items.append(item)
            index += 1
            while index < len(lines) and not is_bullet_line(lines[index]):
                index += 1
            continue
        cleaned = clean_action_line(line)
        if cleaned:
            items.append(cleaned)
        index += 1
    return items


def resolve_goal_state(
    *,
    registry: dict[str, Any],
    goal_id: str,
    project_override: Path | None,
    state_file_override: Path | None,
) -> tuple[dict[str, Any] | None, Path | None, Path]:
    goal = next((item for item in registry_goals(registry) if str(item.get("id")) == goal_id), None)
    project = project_override.expanduser().resolve() if project_override else None
    if project is None and goal and goal.get("repo"):
        project = Path(str(goal.get("repo"))).expanduser()

    registered_state_file = (
        resolve_state_file(project, goal.get("state_file"))
        if project and goal and goal.get("state_file")
        else None
    )
    state_file = state_file_override.expanduser() if state_file_override else None
    if state_file is None and goal:
        state_file = registered_state_file
    if state_file is None:
        raise ValueError("state file is required when the goal is not resolvable from registry")
    if not state_file.is_absolute():
        if project is None:
            raise ValueError("relative state file requires --project or registry repo")
        state_file = project / state_file
    state_file = state_file.resolve()
    if state_file_override is not None:
        if project is None:
            raise ValueError("--state-file override requires --project or a registry goal with repo")
        registered_resolved = (
            registered_state_file.resolve() if registered_state_file is not None else None
        )
        if state_file != registered_resolved and not state_file.is_relative_to(project):
            raise ValueError(
                f"--state-file {state_file} escapes project root {project}"
            )
    return goal, project, state_file


def build_state_refresh_record(
    *,
    goal_id: str,
    state_file: Path,
    state_text: str,
    classification: str,
    recommended_action: str,
    recommended_action_source: str,
    recommended_action_resolution: dict[str, Any] | None = None,
    generated_at: str,
    registry_goal: dict[str, Any] | None,
    delivery_batch_scale: str | None = None,
    delivery_outcome: str | None = None,
    progress_scope: str | None = None,
    agent_id: str | None = None,
    agent_lane: str | None = None,
    autonomous_replan_recorded: bool = False,
    repair_delta_contract: dict[str, Any] | None = None,
    replan_semantic_delta: dict[str, Any] | None = None,
    autonomous_replan_frontier_identity: str | None = None,
    agent_vision: dict[str, Any] | None = None,
    vision_checkpoint: dict[str, Any] | None = None,
    progress_observation: dict[str, Any] | None = None,
    delivery_workspace: dict[str, Any] | None = None,
    settlement_identity: SettlementIdentity | None = None,
    todo_fields: dict[str, Any] | None = None,
) -> dict[str, Any]:
    frontmatter = parse_frontmatter(state_text)
    next_action = active_state_next_action_entries(
        state_text,
        limit=8,
        text_limit=None,
    )
    recent_feedback = extract_section_lines(state_text, "Recent User Feedback", limit=5)
    progress = extract_section_lines(state_text, "Progress Ledger", limit=5)
    digest = hashlib.sha256(state_text.encode("utf-8")).hexdigest()[:16]
    authority_sources = []
    if registry_goal and isinstance(registry_goal.get("authority_sources"), list):
        authority_sources = registry_goal.get("authority_sources") or []
    record = {
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
        record["recommended_action_resolution"] = recommended_action_resolution
    projection_gap = state_projection_gap_warning(
        state_text,
        # An authoritative empty group must not fall back to stale Markdown.
        user_todos=(todo_fields.get("user_todos") or {}) if todo_fields is not None else None,
        agent_todos=(todo_fields.get("agent_todos") or {}) if todo_fields is not None else None,
    )
    if projection_gap:
        record["state_projection_gap"] = projection_gap
    if delivery_batch_scale:
        record["delivery_batch_scale"] = delivery_batch_scale
    if delivery_outcome:
        record["delivery_outcome"] = delivery_outcome
    if delivery_workspace:
        record["delivery_workspace"] = delivery_workspace
    if settlement_identity:
        record["settlement_identity"] = settlement_identity.as_dict()
        record["turn_instance_id"] = settlement_identity.turn_instance_id
        if settlement_identity.todo_id:
            record["todo_id"] = settlement_identity.todo_id
        if settlement_identity.replan_obligation_id:
            record["replan_obligation_id"] = (
                settlement_identity.replan_obligation_id
            )
    if autonomous_replan_recorded:
        record["autonomous_replan_ack"] = {
            "schema_version": "autonomous_replan_ack_v0",
            "recorded": True,
            "source": "refresh_state",
        }
        if repair_delta_contract:
            record["autonomous_replan_ack"]["delta_contract"] = repair_delta_contract
        if replan_semantic_delta:
            record["autonomous_replan_ack"]["semantic_delta"] = (
                replan_semantic_delta
            )
        if autonomous_replan_frontier_identity:
            record["autonomous_replan_ack"]["frontier_identity"] = (
                autonomous_replan_frontier_identity
            )
    if agent_vision:
        record["agent_vision"] = agent_vision
    if vision_checkpoint:
        record["vision_checkpoint"] = vision_checkpoint
    if progress_observation:
        record["progress_observation"] = progress_observation
    if progress_scope:
        record["progress_scope"] = progress_scope
    if agent_id:
        record["agent_id"] = agent_id
    if progress_scope == AGENT_LANE_PROGRESS_SCOPE and agent_id:
        record["agent_lane"] = agent_lane or agent_id
    return record


def _build_state_refresh_output_projections(
    *,
    record: dict[str, Any],
    registry_path: Path,
    runtime_root: Path,
    project: Path | None,
    json_path: Path,
    markdown_path: Path,
    index_path: Path,
    dry_run: bool,
    autonomous_replan_recorded_requested: bool,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Project one refresh record into its compact index and CLI response."""

    record_state = record.get("state") if isinstance(record.get("state"), dict) else {}
    record_frontmatter = record_state.get("frontmatter") or {}
    index_record = {
        field: record[field]
        for field in (
            "generated_at", "goal_id", "classification", "recommended_action",
            "recommended_action_source", "health_check",
        )
    }
    index_record.update({
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "state": {
            "sha256_16": record_state.get("sha256_16"),
            "frontmatter": {"updated_at": record_frontmatter.get("updated_at")},
        },
        "runtime_projection_route": record["runtime_projection_route"],
    })
    for field in (
        "recommended_action_resolution",
        "delivery_batch_scale",
        "delivery_outcome",
        "delivery_workspace",
        "settlement_identity",
        "blocked_retry",
        "refresh_recovery",
        "turn_instance_id",
        "todo_id",
        "replan_obligation_id",
    ):
        if field in record:
            index_record[field] = record[field]

    replan_ack = record.get("autonomous_replan_ack") or {}
    if autonomous_replan_recorded_requested or replan_ack.get("recorded") is True:
        index_record["autonomous_replan_ack"] = replan_ack
        if replan_ack.get("requested_classification"):
            index_record["requested_classification"] = replan_ack["requested_classification"]

    agent_vision = record.get("agent_vision")
    if isinstance(agent_vision, dict):
        index_record["agent_vision"] = {
            field: agent_vision.get(field)
            for field in (
                "schema_version", "agent_id", "state", "vision_patch",
                "todo_delta", "fallback_declarations", "vision_budget",
            )
        }
        if isinstance(agent_vision.get("path_delta"), dict):
            index_record["agent_vision"]["path_delta"] = agent_vision["path_delta"]

    for field in (
        "vision_checkpoint",
        "progress_observation",
        "progress_scope",
        "agent_id",
        "agent_lane",
    ):
        if field in record:
            index_record[field] = record[field]

    payload: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "appended": not dry_run,
        "registry": str(registry_path),
        "runtime_root": str(runtime_root),
        "project": str(project) if project else None,
    }
    payload.update({
        field: record.get(field)
        for field in ("goal_id", "classification", "progress_scope", "agent_id", "agent_lane")
    })
    payload.update({
        "autonomous_replan_recorded": bool(replan_ack.get("recorded")),
        "autonomous_replan_recorded_requested": autonomous_replan_recorded_requested,
        "repair_delta_contract": replan_ack.get("delta_contract"),
        "json_path": str(json_path),
        "markdown_path": str(markdown_path),
        "index_path": str(index_path),
    })
    payload.update({
        field: record.get(field)
        for field in (
            "agent_vision", "vision_checkpoint", "recommended_action",
            "recommended_action_source", "active_state_next_action_update",
            "generated_at", "health_check",
        )
    })
    payload.update(record)
    return index_record, payload


def render_state_refresh_markdown(payload: dict[str, Any]) -> str:
    delivery = payload.get("projection_outbox")
    delivery_lines = []
    if isinstance(delivery, dict):
        delivery_lines.append(f"- Todo display: `{delivery['status']}`")
        if delivery.get("status") == "pending":
            delivery_lines.append("- Canonical Todo state is committed; repair the display without repeating business work or quota spend.")
            delivery_lines.append(str(delivery.get("recommended_action") or ""))
    recovery_markdown = render_refresh_recovery_markdown(payload)
    if recovery_markdown is not None:
        return "\n".join([recovery_markdown, *delivery_lines])
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    frontmatter = state.get("frontmatter") if isinstance(state.get("frontmatter"), dict) else {}
    lines = [
        "# LoopX State Refresh",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- dry_run: `{payload.get('dry_run')}`",
        f"- appended: `{payload.get('appended')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- classification: `{payload.get('classification')}`",
        f"- progress_scope: `{payload.get('progress_scope')}`",
        f"- agent_id: `{payload.get('agent_id')}`",
        f"- agent_lane: `{payload.get('agent_lane')}`",
        f"- delivery_batch_scale: `{payload.get('delivery_batch_scale')}`",
        f"- delivery_outcome: `{payload.get('delivery_outcome')}`",
        f"- autonomous_replan_recorded: `{payload.get('autonomous_replan_recorded')}`",
        f"- autonomous_replan_recorded_requested: `{payload.get('autonomous_replan_recorded_requested')}`",
        f"- generated_at: `{payload.get('generated_at')}`",
        f"- state_file: `{state.get('path')}`",
        f"- state_updated_at: `{frontmatter.get('updated_at')}`",
        f"- health_check: `{payload.get('health_check')}`",
    ]
    lines.extend(delivery_lines)
    lines.extend(render_settlement_progress_markdown(payload))
    if "external_sink_delivery_authorized" in payload:
        lines.append(
            "- external_sink_delivery_authorized: "
            f"`{payload.get('external_sink_delivery_authorized')}`"
        )
    delivery_workspace = (
        payload.get("delivery_workspace")
        if isinstance(payload.get("delivery_workspace"), dict)
        else {}
    )
    if delivery_workspace:
        lines.append(
            "- delivery_workspace: "
            f"repository={delivery_workspace.get('task_repository')} "
            f"kind={delivery_workspace.get('workspace_kind')}"
        )
    if payload.get("error"):
        lines.append(f"- error: {payload.get('error')}")
        return "\n".join(lines)

    repair_delta = (
        payload.get("repair_delta_contract")
        if isinstance(payload.get("repair_delta_contract"), dict)
        else {}
    )
    if repair_delta:
        lines.append(
            "- repair_delta_contract: "
            f"delta_present={repair_delta.get('delta_present')} "
            f"kinds={','.join(repair_delta.get('delta_kinds') or [])}"
        )
    agent_vision = (
        payload.get("agent_vision")
        if isinstance(payload.get("agent_vision"), dict)
        else {}
    )
    if agent_vision:
        budget = (
            agent_vision.get("vision_budget")
            if isinstance(agent_vision.get("vision_budget"), dict)
            else {}
        )
        lines.append(
            "- agent_vision: "
            f"state={agent_vision.get('state')} "
            f"agent_id={agent_vision.get('agent_id')} "
            f"budget={budget.get('total_usage')}/{budget.get('total_limit')}"
        )
    vision_checkpoint = (
        payload.get("vision_checkpoint")
        if isinstance(payload.get("vision_checkpoint"), dict)
        else {}
    )
    if vision_checkpoint:
        lines.append(
            "- vision_checkpoint: "
            f"agent_id={vision_checkpoint.get('agent_id')} "
            f"required={vision_checkpoint.get('required')} "
            f"satisfied={vision_checkpoint.get('satisfied')} "
            f"decision={vision_checkpoint.get('decision')}"
        )
        if vision_checkpoint.get("unchanged_reason"):
            lines.append(
                f"- vision_unchanged_reason: {vision_checkpoint.get('unchanged_reason')}"
            )
        required_resolution = vision_checkpoint.get("required_resolution")
        if required_resolution:
            lines.append(
                "- vision_checkpoint_required_resolution: "
                f"{','.join(str(item) for item in required_resolution)}"
            )
        lines.extend(render_first_refresh_checkpoint_hint(payload))

    projection_gap = (
        payload.get("state_projection_gap")
        if isinstance(payload.get("state_projection_gap"), dict)
        else {}
    )
    if projection_gap:
        lines.append(
            "- state_projection_gap: "
            f"requires_todo_expansion={projection_gap.get('requires_todo_expansion')} "
            f"user_open={projection_gap.get('user_open_count')} "
            f"agent_open={projection_gap.get('agent_open_count')} "
            f"target_roles={','.join(projection_gap.get('target_roles') or [])}"
        )
        if projection_gap.get("recommended_action"):
            lines.append(f"- state_projection_gap_action: {projection_gap.get('recommended_action')}")

    next_action_update = (
        payload.get("active_state_next_action_update")
        if isinstance(payload.get("active_state_next_action_update"), dict)
        else {}
    )
    if next_action_update:
        lines.append(
            "- active_state_next_action_update: "
            f"updated={next_action_update.get('updated')} "
            f"would_update={next_action_update.get('would_update')} "
            f"dry_run={next_action_update.get('dry_run')}"
        )
        if next_action_update.get("next_action"):
            lines.append(
                f"- active_state_next_action: {next_action_update.get('next_action')}"
            )

    write_correctness = (
        payload.get("local_state_write_correctness")
        if isinstance(payload.get("local_state_write_correctness"), dict)
        else {}
    )
    if write_correctness:
        intent = write_correctness.get("write_intent") if isinstance(write_correctness.get("write_intent"), dict) else {}
        preview = write_correctness.get("preview") if isinstance(write_correctness.get("preview"), dict) else {}
        apply_result = (
            write_correctness.get("apply_result")
            if isinstance(write_correctness.get("apply_result"), dict)
            else {}
        )
        lines.append(
            "- local_state_write_correctness: "
            f"schema={write_correctness.get('schema_version')} "
            f"write_class={intent.get('write_class')} "
            f"status={apply_result.get('status')} "
            f"non_destructive={preview.get('non_destructive')}"
        )

    global_sync = payload.get("global_sync") if isinstance(payload.get("global_sync"), dict) else {}
    if global_sync:
        lines.extend(
            [
                f"- global_registry: `{global_sync.get('global_registry')}`",
                f"- global_sync_wrote: `{global_sync.get('wrote')}`",
            ]
        )

    lines.extend(
        [
            "",
            "## Recommended Action",
            f"- source: `{payload.get('recommended_action_source')}`",
        ]
    )
    recommendation_resolution = (
        payload.get("recommended_action_resolution")
        if isinstance(payload.get("recommended_action_resolution"), dict)
        else {}
    )
    if recommendation_resolution:
        lines.append(
            "- authority: "
            f"`{recommendation_resolution.get('authority')}`; "
            "settlement_alignment: "
            f"`{recommendation_resolution.get('settlement_alignment')}`"
        )
        if recommendation_resolution.get("todo_id"):
            lines.append(
                f"- todo_id: `{recommendation_resolution.get('todo_id')}`"
            )
    lines.append(str(payload.get("recommended_action") or ""))
    for heading, key in (
        ("Next Action", "next_action"),
        ("Recent Feedback", "recent_feedback"),
        ("Progress", "progress"),
    ):
        values = state.get(key) if isinstance(state.get(key), list) else []
        if values:
            lines.extend(["", f"## {heading}"])
            lines.extend(f"- {value}" for value in section_list_items(values))
    return "\n".join(lines)


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
    repair_delta_kinds: list[str] | None = None,
    agent_vision_packet: dict[str, Any] | None = None,
    merge_agent_vision_patch: bool = False,
    vision_unchanged_reason: str | None = None,
    checkpoint_read_context_id: str | None = None,
    progress_observation: dict[str, Any] | None = None,
    completion_todo_id: str | None = None,
    completion_turn_key: str | None = None,
    usage_measurement: dict[str, Any] | None = None,
    usage_codex_session: Path | None = None,
    dry_run: bool,
    sync_global: bool = True,
    external_delivery: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .control_plane.todos.provider_projection import recover_refresh_todo_projection

    safe_goal_id = validate_goal_id_path_segment(goal_id)
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
    normalized_repair_delta_kinds = normalize_repair_delta_kinds(repair_delta_kinds)
    normalized_progress_observation = (
        normalize_progress_observation(
            progress_observation,
            work_item_id=todo_id or normalized_replan_obligation_id,
        )
        if progress_observation is not None
        else None
    )
    # Validate individual fields before asking the typed owner about their
    # combination. Reuse normalized facts; malformed input must not open a
    # registry or create a write lock in either normal or dry-run execution.
    require_consistent_delivery_claim({
        "delivery_outcome": normalized_delivery_outcome,
        "progress_observation": normalized_progress_observation,
    })
    turn_scoped_settlement_qualified = qualifies_turn_scoped_settlement(
        normalized_delivery_outcome,
        normalized_progress_observation,
        work_item_id=todo_id,
        replan_obligation_id=normalized_replan_obligation_id,
    )
    # A refusal has to name the typed input the writer still owes; the diagnosis
    # is computed once and reused by both settlement guards below.
    turn_scoped_settlement_gap = (
        TURN_SCOPED_SETTLEMENT_GAP_FALLBACK
        if turn_scoped_settlement_qualified
        else explain_turn_scoped_settlement_gap(
            normalized_delivery_outcome,
            normalized_progress_observation,
            work_item_id=todo_id,
            replan_obligation_id=normalized_replan_obligation_id,
        )
        or TURN_SCOPED_SETTLEMENT_GAP_FALLBACK
    )
    if delivery_workspace_path is not None and not turn_scoped_settlement_qualified:
        raise ValueError(
            "--delivery-workspace-path requires a progress outcome or a typed "
            "blocked outcome_gap settlement: " + turn_scoped_settlement_gap
        )
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_override, registry_path=registry_path)
    # State-dependent admission through the final append remains serialized.
    # Only pure input validation runs before this transitional persistence lock.
    with (nullcontext() if dry_run else exclusive_run_index_lock(
        runtime_root / "goals" / safe_goal_id / "runs" / "index.jsonl", operation="refresh-state"
    )):
        settlement_identity = None
        settlement_result = None
        delivery_workspace_causality = None
        settlement_workspace_requirement = None
        settlement_readback = None
        refresh_recovery = None
        prior_writeback_run = None
        checkpoint_supplement = False
        if todo_id or normalized_replan_obligation_id or turn_instance_id:
            if checkpoint_read_context_id or agent_vision_packet or vision_unchanged_reason:
                require_complete_checkpoint_index(runtime_root / "goals" / safe_goal_id / "runs" / "index.jsonl")
            if not turn_scoped_settlement_qualified:
                raise ValueError(
                    TURN_SCOPED_SETTLEMENT_REQUIREMENT + ": " + turn_scoped_settlement_gap
                )
            settlement_readback = read_heartbeat_settlement(
                runtime_root,
                goal_id=safe_goal_id,
                agent_id=normalized_agent_id or None,
                todo_id=todo_id,
                turn_instance_id=turn_instance_id,
                replan_obligation_id=normalized_replan_obligation_id,
                refresh_retry=(refresh_retry_request := {
                    "checkpoint_read_context_id": checkpoint_read_context_id,
                    "external_delivery": external_delivery,
                    "vision": agent_vision_packet,
                    "unchanged_reason": vision_unchanged_reason,
                    "merge_patch": bool(merge_agent_vision_patch),
                    "workspace_requested": delivery_workspace_path is not None,
                    "mutation": {
                        "next_action": next_action,
                        "autonomous_replan_recorded": autonomous_replan_recorded,
                        "repair_delta_kinds": repair_delta_kinds,
                        "usage_measurement": usage_measurement,
                        "usage_codex_session": str(usage_codex_session)
                        if usage_codex_session
                        else None,
                    },
                    "delivery_outcome": normalized_delivery_outcome,
                    "delivery_batch_scale": normalized_delivery_batch_scale,
                    "delivery_boundary": normalized_delivery_boundary,
                    "progress_observation": normalized_progress_observation,
                }),
            )
            if settlement_readback is None:
                raise RuntimeError("exact settlement readback unexpectedly returned not-found")
            settlement_result = settlement_readback.identity
            if settlement_result.failure is not None:
                raise ValueError(settlement_result.failure.reason)
            settlement_identity = settlement_result.value
            if settlement_identity is None:
                raise ValueError("turn-scoped refresh-state has no settlement identity")
            delivery_workspace_causality = settlement_readback.workspace_causality
            refresh_recovery = settlement_readback.refresh_recovery
            if not refresh_recovery:
                raise RuntimeError("settlement readback omitted refresh recovery admission")
            prior_writeback_run = settlement_readback.writeback_run
            checkpoint_supplement = bool(
                refresh_recovery["decision"] == "supplement_checkpoint"
            )
            if refresh_recovery.get("decision") in {"replay", "repair_receipt"} and prior_writeback_run:
                inspect_checkpoint_replay(runtime_root, safe_goal_id, prior_writeback_run)
            recovery_payload = refresh_recovery_payload(
                settlement_readback, registry_path=registry_path, runtime_root=runtime_root,
                goal_id=safe_goal_id, dry_run=dry_run,
            )
            if recovery_payload is not None:
                return recover_refresh_todo_projection(
                    recovery_payload, registry_path=registry_path, runtime_root=runtime_root,
                    goal_id=safe_goal_id, project=project, state_file=state_file,
                )
            if checkpoint_read_context_id and not checkpoint_supplement:
                raise ValueError("--checkpoint-read-context applies only to a missing-checkpoint supplement")
            settlement_workspace_requirement = resolve_settlement_workspace_requirement(
                delivery_workspace_causality, settlement_binding_kind=settlement_identity.binding_kind.value
            )
        runtime_projection_route = resolve_runtime_projection_route(
            registry_path=registry_path,
            goal_id=safe_goal_id,
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
        registry_goal, resolved_project, resolved_state_file = resolve_goal_state(
            registry=registry,
            goal_id=safe_goal_id,
            project_override=project,
            state_file_override=state_file,
        )
        planning_source = load_refresh_planning_source(
            runtime_root, safe_goal_id, resolved_state_file, require_display=bool(next_action)
        )
        state_text, planning_events, todo_fields = (
            planning_source.state_text, planning_source.events, planning_source.todo_fields,
        )
        expected_write_state_text = state_text
        normalized_next_action = normalize_next_action_text(next_action) if next_action else None
        registered_agents = registered_agents_for_goal(registry_goal)
        known_agents = {agent for agent in registered_agents if agent}
        multi_agent_goal = len(known_agents) > 1
        workspace_guard_policy = (
            registry_goal.get("workspace_guard_policy")
            if isinstance(registry_goal.get("workspace_guard_policy"), dict)
            else {}
        )
        explicit_peer_worktree_requirement = workspace_guard_policy.get(
            "peer_independent_worktree_required"
        )
        peer_independent_worktree_required = multi_agent_goal and (
            explicit_peer_worktree_requirement is None
            or explicit_peer_worktree_requirement is True
        )
        if normalized_agent_id and known_agents and normalized_agent_id not in known_agents:
            raise ValueError(
                f"agent_id {normalized_agent_id!r} is not registered for goal {safe_goal_id!r}"
            )
        if multi_agent_goal and not normalized_agent_id:
            raise ValueError(
                "multi-agent refresh-state requires --agent-id; text inference is disabled"
            )
        if not normalized_progress_scope:
            normalized_progress_scope = (
                AGENT_LANE_PROGRESS_SCOPE if normalized_agent_id else GOAL_PROGRESS_SCOPE
            )
        if normalized_progress_scope == AGENT_LANE_PROGRESS_SCOPE:
            if not normalized_agent_id:
                raise ValueError("--progress-scope agent_lane requires --agent-id")
            if normalized_next_action:
                raise ValueError(
                    "agent-lane refresh-state cannot update the durable active-state Next Action; "
                    "rerun without --next-action or use --progress-scope goal from a registered peer"
                )
        if normalized_progress_scope == GOAL_PROGRESS_SCOPE:
            if normalized_agent_lane:
                raise ValueError("--agent-lane requires --progress-scope agent_lane")
        if (agent_vision_packet is not None or vision_unchanged_reason) and not normalized_agent_id:
            raise ValueError("vision writeback requires --agent-id")
        agent_vision: dict[str, Any] | None = None
        existing_agent_vision: dict[str, Any] | None = None
        autonomous_replan_frontier_identity: str | None = None
        newest_first_runs: list[dict[str, Any]] = []
        if normalized_agent_id:
            existing_runs, _ = load_index(
                runtime_root / "goals" / safe_goal_id / "runs" / "index.jsonl"
            )
            newest_first_runs = [
                run
                for _, run in sorted(
                    enumerate(existing_runs),
                    key=lambda item: (
                        *chronology_key(item[1].get("generated_at")),
                        item[0],
                    ),
                    reverse=True,
                )
            ]
            existing_agent_vision = latest_agent_vision_from_runs(
                newest_first_runs,
                goal_id=safe_goal_id,
                agent_id=normalized_agent_id,
            )
        if agent_vision_packet is not None:
            agent_vision = prepare_vision_refresh(
                agent_vision_packet,
                goal_id=safe_goal_id,
                agent_id=normalized_agent_id or None,
                existing_agent_vision=existing_agent_vision,
                merge_patch=merge_agent_vision_patch,
                require_path_delta_for_durable_change=autonomous_replan_recorded,
            )
        generated_at = now_local()
        active_state_next_action_update: dict[str, Any] | None = None
        if normalized_next_action:
            with exclusive_cross_runtime_file_lock(resolved_state_file):
                locked_state_text = resolved_state_file.read_text(encoding="utf-8")
                expected_write_state_text = locked_state_text
                updated_state_text, state_updated = replace_next_action_section(
                    locked_state_text,
                    next_action=normalized_next_action,
                    updated_at=generated_at,
                )
                active_state_next_action_update = {
                    "schema_version": ACTIVE_STATE_NEXT_ACTION_UPDATE_SCHEMA_VERSION,
                    "source": "refresh_state",
                    "next_action": normalized_next_action,
                    "updated": bool(state_updated and not dry_run),
                    "would_update": bool(state_updated),
                    "dry_run": bool(dry_run),
                    "updated_at": generated_at if state_updated else None,
                }
                state_text = updated_state_text if state_updated else locked_state_text

        recommendation_resolution = resolve_refresh_recommendation(
            state_text,
            todo_fields=todo_fields,
            explicit_action=recommended_action,
            agent_id=normalized_agent_id or None,
            settlement_identity=(
                settlement_identity.as_dict() if settlement_identity is not None else None
            ),
            registry_goal=registry_goal,
            state_path=resolved_state_file,
            rollout_events=(
                planning_events if normalized_agent_id or settlement_identity is not None else None
            ),
        )
        action = str(recommendation_resolution["recommended_action"])
        recommended_action_source = str(
            recommendation_resolution["recommended_action_source"]
        )
        requested_classification = classification
        settlement_replan_guard = (
            settlement_readback.semantic_replan_guard
            if settlement_readback is not None
            and settlement_readback.semantic_replan_guard is not None
            else {}
        )
        replan_qualification = qualify_refresh_replan_writeback(
            todo_fields=todo_fields,
            autonomous_replan_recorded=autonomous_replan_recorded,
            requested_delta_kinds=normalized_repair_delta_kinds,
            active_state_next_action_update=active_state_next_action_update,
            agent_vision=agent_vision,
            existing_agent_vision=existing_agent_vision,
            agent_id=normalized_agent_id,
            dry_run=dry_run,
            settlement_todo_id=(settlement_identity.todo_id if settlement_identity else None),
            settlement_guard_scoped=(
                settlement_replan_guard.get("scope") == "turn_guard"
            ),
            settlement_guard_semantic_replan_obligation_id=(
                settlement_replan_guard.get("selected_obligation_id")
                if isinstance(
                    settlement_replan_guard.get("selected_obligation_id"), str
                )
                else None
            ),
            newest_first_runs=newest_first_runs,
            state_text=state_text,
            goal_id=safe_goal_id,
            progress_observation=normalized_progress_observation,
            registry_goal=registry_goal,
            # The acknowledgement is judged against the same sentinel-derived
            # obligation that status shows; `off` loads nothing.
            external_progress_review=external_progress_review_context(
                registry_goal or {"id": safe_goal_id}, runtime_root
            ),
            completion_todo_id=completion_todo_id,
            completion_turn_key=completion_turn_key,
            classification=classification,
            delivery_outcome=normalized_delivery_outcome,
        )
        repair_delta_contract = replan_qualification.repair_delta_contract
        replan_semantic_delta = replan_qualification.semantic_delta
        autonomous_replan_frontier_identity = replan_qualification.frontier_identity
        classification = replan_qualification.classification
        normalized_delivery_outcome = replan_qualification.delivery_outcome
        effective_autonomous_replan_recorded = (
            replan_qualification.autonomous_replan_recorded
        )
        blocked_retry = None
        if checkpoint_supplement and prior_writeback_run is not None:
            blocked_retry = prior_writeback_run.get("blocked_retry")
        elif (
            settlement_identity is not None
            and settlement_identity.binding_kind.value == "todo"
            and normalized_delivery_outcome == "outcome_gap"
            and isinstance(normalized_progress_observation, dict)
            and normalized_progress_observation.get("result_class") == "blocked"
        ):
            blocked_todo_fields = todo_fields
            if blocked_todo_fields is None:
                blocked_todo_fields = parse_active_state_todos(
                    state_text,
                    goal=registry_goal,
                    state_path=resolved_state_file,
                    preferred_todo_ids={settlement_identity.todo_id or ""},
                    rollout_events=planning_events,
                    item_limit=None,
                )
            blocked_retry = require_blocked_retry_wait(
                blocked_todo_fields,
                todo_id=settlement_identity.todo_id or "",
                observed_at=generated_at,
                allow_turn_settlement_retry=local_authority_is_promoted(
                    runtime_root=runtime_root, goal_id=safe_goal_id,
                ),
            )
        # read_heartbeat_settlement admits checkpoint_supplement only for a
        # checkpoint-only retry of an already committed writeback with the
        # exact Goal/Agent/Todo/Turn and delivery identity. It rejects replayed
        # mutations and conflicting vision decisions before this fence. The
        # supplement repairs only the missing vision decision, so terminal
        # validation remains attached to the original Todo completion.
        if (
            normalized_delivery_outcome in ACCOUNTABLE_DELIVERY_OUTCOMES
            and not checkpoint_supplement
        ):
            require_accountable_completion_validation(
                state_text,
                todo_fields=todo_fields,
                todo_id=(settlement_identity.todo_id if settlement_identity else None),
                agent_id=normalized_agent_id or None,
                delivery_boundary=normalized_delivery_boundary,
                delivery_outcome=normalized_delivery_outcome,
                semantic_replan_recorded=(
                    effective_autonomous_replan_recorded
                ),
            )
        vision_checkpoint = build_vision_checkpoint(
            agent_id=normalized_agent_id or None,
            agent_vision=agent_vision,
            existing_agent_vision=existing_agent_vision,
            vision_unchanged_reason=vision_unchanged_reason,
            delivery_outcome=normalized_delivery_outcome,
            active_state_next_action_update=active_state_next_action_update,
            delivery_boundary=normalized_delivery_boundary,
            todo_id=(settlement_identity.todo_id if settlement_identity else None),
            completion_todo_id=completion_todo_id,
            autonomous_replan_recorded=effective_autonomous_replan_recorded,
            blocked_retry=blocked_retry,
        )
        if checkpoint_supplement and not vision_checkpoint.get("satisfied"):
            raise ValueError(
                "checkpoint supplement did not satisfy the missing decision; "
                "an unchanged reason requires an existing vision. Supply a valid vision "
                "patch on the same Turn; the original writeback and quota are unchanged."
            )
        delivery_workspace = None
        workspace_requirement = str(
            (settlement_workspace_requirement or {}).get("requirement") or "unknown"
        )
        if delivery_workspace_path is not None and workspace_requirement == "not_required":
            raise ValueError(
                "--delivery-workspace-path conflicts with the original Todo's "
                "explicit non-delivery settlement contract"
            )
        if (
            turn_scoped_settlement_qualified
            and workspace_requirement != "not_required"
            and not checkpoint_supplement
        ):
            delivery_workspace = capture_delivery_workspace(
                current_path=delivery_workspace_path,
                peer_independent_worktree_required=peer_independent_worktree_required,
                local_goal_id=safe_goal_id,
                local_project_root=resolved_project,
                repository_source=(
                    "refresh_state.delivery_workspace_path"
                    if delivery_workspace_path is not None
                    else None
                ),
            )
            if (
                peer_independent_worktree_required
                and (
                    delivery_workspace is None
                    or delivery_workspace.get("workspace_kind")
                    != "independent_git_worktree"
                )
            ):
                raise ValueError(
                    "accountable peer delivery must be refreshed from the independent "
                    "git worktree that produced it, or name that worktree with "
                    "--delivery-workspace-path"
                )
            if delivery_workspace_path is not None and delivery_workspace is None:
                raise ValueError(
                    "--delivery-workspace-path must identify the registered local goal "
                    "workspace or a git checkout with a credential-free origin repository"
                )
        if checkpoint_supplement:
            # The supplemental row must not reattribute the original delivery to
            # the recovery caller's current directory or change its accounting.
            assert prior_writeback_run is not None
            delivery_workspace = prior_writeback_run.get("delivery_workspace")
            normalized_delivery_outcome = prior_writeback_run.get("delivery_outcome")
            normalized_delivery_batch_scale = prior_writeback_run.get(
                "delivery_batch_scale"
            )
            normalized_progress_observation = prior_writeback_run.get(
                "progress_observation"
            )
            classification = prior_writeback_run["classification"]
        if (
            active_state_next_action_update
            and active_state_next_action_update.get("would_update")
            and not dry_run
        ):
            with exclusive_cross_runtime_file_lock(resolved_state_file):
                current_state_text = resolved_state_file.read_text(encoding="utf-8")
                if current_state_text != expected_write_state_text:
                    raise ValueError(
                        "active goal state changed while refresh-state was qualifying "
                        "its semantic writeback; retry from the current state"
                    )
                require_prose_state_write_allowed(
                    registry_path=registry_path, runtime_root=runtime_root,
                    goal_id=safe_goal_id, state_path=resolved_state_file,
                    original_text=current_state_text, planned_text=state_text,
                )
                atomic_write_state_text(resolved_state_file, state_text)
        record = build_state_refresh_record(
            goal_id=safe_goal_id,
            state_file=resolved_state_file,
            state_text=state_text,
            classification=classification,
            recommended_action=action,
            recommended_action_source=recommended_action_source,
            recommended_action_resolution=recommendation_resolution,
            generated_at=generated_at,
            registry_goal=registry_goal,
            delivery_batch_scale=normalized_delivery_batch_scale,
            delivery_outcome=normalized_delivery_outcome,
            progress_scope=normalized_progress_scope,
            agent_id=normalized_agent_id or None,
            agent_lane=normalized_agent_lane or None,
            autonomous_replan_recorded=effective_autonomous_replan_recorded,
            repair_delta_contract=repair_delta_contract,
            autonomous_replan_frontier_identity=autonomous_replan_frontier_identity,
            agent_vision=agent_vision,
            vision_checkpoint=vision_checkpoint,
            progress_observation=normalized_progress_observation,
            delivery_workspace=delivery_workspace,
            settlement_identity=settlement_identity,
            todo_fields=todo_fields,
        )
        if blocked_retry is not None:
            record["blocked_retry"] = blocked_retry
        if delivery_workspace_causality:
            record["delivery_workspace_causality"] = delivery_workspace_causality
        if refresh_recovery:
            record["refresh_recovery"] = refresh_recovery
        if settlement_workspace_requirement:
            record["settlement_workspace_requirement"] = (
                settlement_workspace_requirement
            )
        if autonomous_replan_recorded:
            if "autonomous_replan_ack" not in record:
                record["autonomous_replan_ack"] = {
                    "schema_version": "autonomous_replan_ack_v0",
                    "recorded": False,
                    "source": "refresh_state",
                    "delta_contract": repair_delta_contract,
                }
            record["autonomous_replan_ack"]["requested"] = True
            if autonomous_replan_frontier_identity:
                record["autonomous_replan_ack"]["frontier_identity"] = (
                    autonomous_replan_frontier_identity
                )
            if requested_classification != classification:
                record["autonomous_replan_ack"]["requested_classification"] = requested_classification
                record["autonomous_replan_noop"] = {
                    "schema_version": REPAIR_NOOP_SCHEMA_VERSION,
                    "classification": classification,
                    "requested_classification": requested_classification,
                    "reason": "autonomous replan ACK requested without a machine-visible repair delta",
                }
        if replan_semantic_delta:
            record.setdefault(
                "autonomous_replan_ack",
                {
                    "schema_version": "autonomous_replan_ack_v0",
                    "recorded": True,
                    "source": "refresh_state_semantic_delta",
                },
            )
            record["autonomous_replan_ack"]["semantic_delta"] = (
                replan_semantic_delta
            )
        if active_state_next_action_update:
            record["active_state_next_action_update"] = active_state_next_action_update
        compact_route = compact_runtime_projection_route(runtime_projection_route)
        compact_route["projection_enabled"] = bool(sync_global)
        compact_route["projection_marker_field"] = "shared_runtime_projection"
        record["runtime_projection_route"] = compact_route

        runs_dir = runtime_root / "goals" / safe_goal_id / "runs"
        json_path, markdown_path = unique_run_paths(runs_dir, generated_at)
        index_path = runs_dir / "index.jsonl"
        index_record, payload = _build_state_refresh_output_projections(
            record=record,
            registry_path=registry_path,
            runtime_root=runtime_root,
            project=resolved_project,
            json_path=json_path,
            markdown_path=markdown_path,
            index_path=index_path,
            dry_run=dry_run,
            autonomous_replan_recorded_requested=bool(autonomous_replan_recorded),
        )
        # GH-C95 producer boundary: attach the typed run_usage_v0 row before the
        # durable record and index rows are written, so malformed or negative usage
        # fails the whole refresh instead of entering run history. The booking lock
        # spans ledger-basis read + row append so concurrent refreshes cannot fund
        # two deltas from one stale basis; the appended row advances the basis.
        with ExitStack() as usage_booking_guard:
            if checkpoint_supplement:
                assert settlement_identity is not None
                context = usage_booking_guard.enter_context(checkpoint_commit_guard(
                    runtime_root=runtime_root, registry_path=registry_path,
                    state_file=resolved_state_file, identity=settlement_identity,
                    read_context_id=checkpoint_read_context_id,
                ))
                for projection in (record, index_record, payload):
                    projection["vision_checkpoint"] = {
                        **projection["vision_checkpoint"], "read_context": context,
                    }
            if usage_codex_session is not None:
                if not dry_run:
                    runs_dir.mkdir(parents=True, exist_ok=True)
                    usage_booking_guard.enter_context(
                        exclusive_file_lock(
                            usage_booking_lock_target(runs_dir),
                            agent_id=normalized_agent_id or None,
                            operation="refresh-state-usage-booking",
                        )
                    )
                book_codex_session_usage(
                    record, usage_codex_session, index_path, index_record=index_record
                )
            elif usage_measurement is not None:
                ingest_usage_into_run_record(
                    record, usage_measurement, index_record=index_record
                )
            if isinstance(record.get("usage"), dict):
                payload["usage"] = dict(record["usage"])
            if dry_run:
                expected_write_scopes = ["runtime_history"]
                if active_state_next_action_update and active_state_next_action_update.get("would_update"):
                    expected_write_scopes.insert(0, "active_state")
                if sync_global and route_status in {"resolved", "single_runtime"}:
                    expected_write_scopes.append("global_registry")
                if shared_runtime_root:
                    expected_write_scopes.append("shared_runtime_projection")
                patch_parts = [f"append refresh-state run classification={classification}"]
                if active_state_next_action_update:
                    if active_state_next_action_update.get("would_update"):
                        patch_parts.append("preview active-state Next Action update")
                    else:
                        patch_parts.append("preserve active-state Next Action")
                if sync_global and route_status in {"resolved", "single_runtime"}:
                    patch_parts.append("sync public-safe registry projection")
                elif sync_global:
                    patch_parts.append(f"block global sync on {route_status} runtime projection route")
                if shared_runtime_root:
                    patch_parts.append("project compact refresh to registered shared runtime")
                payload["local_state_write_correctness"] = build_local_state_write_correctness_dry_run_packet(
                    goal_id=safe_goal_id,
                    writer_id=normalized_agent_id or "loopx.refresh-state",
                    write_class="refresh_state",
                    state_text=expected_write_state_text,
                    target_refs={
                        "state_file_ref": "registry.goal.state_file",
                        "run_history_ref": "runtime.goal.runs",
                        "index_ref": "runtime.goal.runs.index",
                        "global_registry_ref": (
                            "runtime.registry.global"
                            if sync_global and route_status in {"resolved", "single_runtime"}
                            else None
                        ),
                        "shared_runtime_projection_ref": (
                            "shared_runtime.goal.runs.index" if shared_runtime_root else None
                        ),
                    },
                    patch_summary="; ".join(patch_parts),
                    expected_write_scopes=expected_write_scopes,
                    lease_ref=None,
                    projection_status_surface=f"refresh-state dry-run: {classification}",
                )
            if not dry_run:
                runs_dir.mkdir(parents=True, exist_ok=True)
                json_path, markdown_path = reserve_unique_run_paths(runs_dir, generated_at)
                index_record["json_path"] = str(json_path)
                index_record["markdown_path"] = str(markdown_path)
                payload["json_path"] = str(json_path)
                payload["markdown_path"] = str(markdown_path)
                if checkpoint_supplement:
                    saved = commit_checkpoint_run(runtime_root=runtime_root, registry_path=registry_path,
                        state_file=resolved_state_file, identity=settlement_identity,
                        refresh_retry=refresh_retry_request, record=record, index_record=index_record,
                        markdown=render_state_refresh_markdown(payload) + "\n")
                    for projection in (record, index_record, payload):
                        projection["vision_checkpoint"]["read_context"] = saved["context"]
                    for projection in (index_record, payload):
                        projection.update({key: saved[key] for key in ("json_path", "markdown_path")})
                    if saved["replayed"]:
                        payload.update(appended=False, idempotent_replay=True)
                else:
                    json_path.write_text(
                        json.dumps(record, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
                        encoding="utf-8",
                    )
                    markdown_path.write_text(render_state_refresh_markdown(payload) + "\n", encoding="utf-8")
                    with index_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(index_record, ensure_ascii=False, allow_nan=False) + "\n")
        if sync_global and route_status in {"missing", "ambiguous"}:
            payload["ok"] = False
            payload["partial_write"] = not dry_run
            payload["global_sync"] = {
                "ok": False,
                "enabled": False,
                "wrote": False,
                "reason": f"runtime projection route is {route_status}",
                "route_status": route_status,
            }
            payload["shared_runtime_projection"] = {
                "ok": False,
                "status": f"route_{route_status}",
                "dry_run": dry_run,
                "raw_artifacts_copied": False,
                "recommended_action_copied": False,
                "runtime_projection_route_id": compact_route.get("route_id"),
            }
        elif sync_global:
            payload["global_sync"] = sync_project_registry_to_global(
                registry_path=registry_path,
                runtime_root_override=str(global_sync_runtime_root or runtime_root),
                goal_id=safe_goal_id,
                dry_run=dry_run,
            )
            if shared_runtime_root and payload["global_sync"].get("ok"):
                projection_record, projection_index = build_shared_runtime_projection(
                    record=record,
                )
                try:
                    payload["shared_runtime_projection"] = write_shared_runtime_projection(
                        shared_runtime_root=shared_runtime_root,
                        goal_id=safe_goal_id,
                        record=projection_record,
                        index_record=projection_index,
                        dry_run=dry_run,
                    )
                except OSError as exc:
                    payload["ok"] = False
                    payload["partial_write"] = not dry_run
                    payload["shared_runtime_projection"] = {
                        "ok": False,
                        "status": "write_failed",
                        "dry_run": dry_run,
                        "shared_runtime_root": str(shared_runtime_root),
                        "raw_artifacts_copied": False,
                        "recommended_action_copied": False,
                        "error": str(exc),
                    }
            elif shared_runtime_root:
                payload["ok"] = False
                payload["partial_write"] = not dry_run
                payload["shared_runtime_projection"] = {
                    "ok": False,
                    "status": "blocked_by_global_sync",
                    "dry_run": dry_run,
                    "shared_runtime_root": str(shared_runtime_root),
                    "raw_artifacts_copied": False,
                    "recommended_action_copied": False,
                }
            else:
                payload["shared_runtime_projection"] = {
                    "ok": True,
                    "status": "not_required",
                    "dry_run": dry_run,
                    "raw_artifacts_copied": False,
                    "recommended_action_copied": False,
                }
        else:
            payload["global_sync"] = {
                "enabled": False,
                "global_registry": str(runtime_root / "registry.global.json"),
                "synced_goal_ids": [],
                "wrote": False,
            }
            payload["shared_runtime_projection"] = {
                "ok": True,
                "status": "disabled",
                "dry_run": dry_run,
                "raw_artifacts_copied": False,
                "recommended_action_copied": False,
            }
        if settlement_identity is not None and not dry_run:
            committed_readback = read_heartbeat_settlement(
                runtime_root, goal_id=safe_goal_id, agent_id=settlement_identity.agent_id,
                todo_id=settlement_identity.todo_id, turn_instance_id=settlement_identity.turn_instance_id,
                replan_obligation_id=settlement_identity.replan_obligation_id,
            )
            if committed_readback is None:
                raise RuntimeError("committed refresh settlement readback missing")
            attach_settlement_progress(payload, committed_readback, registry_path=registry_path, runtime_root=runtime_root)
        return recover_refresh_todo_projection(
            finish_external_delivery_refresh(
                payload, settlement_readback, runtime_root, dry_run=dry_run,
            ),
            registry_path=registry_path, runtime_root=runtime_root, goal_id=safe_goal_id,
            project=resolved_project, state_file=resolved_state_file,
            canonical_snapshot=planning_source.canonical_snapshot,
        )
