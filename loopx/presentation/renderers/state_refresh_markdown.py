"""Markdown renderer for LoopX ``refresh-state`` run and recovery payloads."""

from __future__ import annotations

from collections.abc import Mapping

from ...control_plane.quota.settlement import (
    render_first_refresh_checkpoint_hint,
    render_refresh_recovery_markdown,
    render_settlement_progress_markdown,
)
from ...refresh_state.text_parsing import section_list_items
from ..markdown import append_operator_action_markdown


def _mapping_value(container: Mapping[str, object], key: str) -> dict[str, object]:
    value = container.get(key)
    return dict(value) if isinstance(value, Mapping) else {}


def _str_list_value(container: Mapping[str, object], key: str) -> list[str]:
    value = container.get(key)
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if isinstance(item, str) or item is not None]


def _render_vision_checkpoint_lines(
    lines: list[str],
    payload: dict[str, object],
) -> None:
    vision_checkpoint = _mapping_value(payload, "vision_checkpoint")
    if not vision_checkpoint:
        return
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
    if isinstance(required_resolution, list) and required_resolution:
        lines.append(
            "- vision_checkpoint_required_resolution: "
            f"{','.join(str(item) for item in required_resolution)}"
        )
    gaps = vision_checkpoint.get("acceptance_gaps")
    if isinstance(gaps, list):
        for gap in gaps:
            if not isinstance(gap, Mapping):
                continue
            checks = _mapping_value(gap, "component_checks")
            failed = [name for name, passed in checks.items() if not passed]
            lines.append(
                f"- outcome qualification gap: {', '.join(failed)}. "
                f"{gap.get('resolution_hint', '')}"
            )
    lines.extend(render_first_refresh_checkpoint_hint(payload))


def _render_state_diagnostic_lines(
    lines: list[str],
    payload: dict[str, object],
) -> None:
    repair_delta = _mapping_value(payload, "repair_delta_contract")
    if repair_delta:
        lines.append(
            "- repair_delta_contract: "
            f"delta_present={repair_delta.get('delta_present')} "
            f"kinds={','.join(_str_list_value(repair_delta, 'delta_kinds'))}"
        )
    agent_vision = _mapping_value(payload, "agent_vision")
    if agent_vision:
        budget = _mapping_value(agent_vision, "vision_budget")
        lines.append(
            "- agent_vision: "
            f"state={agent_vision.get('state')} "
            f"agent_id={agent_vision.get('agent_id')} "
            f"budget={budget.get('total_usage')}/{budget.get('total_limit')}"
        )
    _render_vision_checkpoint_lines(lines, payload)

    projection_gap = _mapping_value(payload, "state_projection_gap")
    if projection_gap:
        lines.append(
            "- state_projection_gap: "
            f"requires_todo_expansion={projection_gap.get('requires_todo_expansion')} "
            f"user_open={projection_gap.get('user_open_count')} "
            f"agent_open={projection_gap.get('agent_open_count')} "
            f"target_roles={','.join(_str_list_value(projection_gap, 'target_roles'))}"
        )
        if projection_gap.get("recommended_action"):
            lines.append(
                f"- state_projection_gap_action: {projection_gap.get('recommended_action')}"
            )

    next_action_update = _mapping_value(payload, "active_state_next_action_update")
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

    write_correctness = _mapping_value(payload, "local_state_write_correctness")
    if write_correctness:
        intent = _mapping_value(write_correctness, "write_intent")
        preview = _mapping_value(write_correctness, "preview")
        apply_result = _mapping_value(write_correctness, "apply_result")
        lines.append(
            "- local_state_write_correctness: "
            f"schema={write_correctness.get('schema_version')} "
            f"write_class={intent.get('write_class')} "
            f"status={apply_result.get('status')} "
            f"non_destructive={preview.get('non_destructive')}"
        )

    global_sync = _mapping_value(payload, "global_sync")
    if global_sync:
        lines.extend(
            [
                f"- global_registry: `{global_sync.get('global_registry')}`",
                f"- global_sync_wrote: `{global_sync.get('wrote')}`",
            ]
        )


def render_state_refresh_markdown(payload: Mapping[str, object]) -> str:
    """Render one ``refresh-state`` result or recovery payload as Markdown."""
    payload_dict = dict(payload)
    delivery = _mapping_value(payload_dict, "projection_outbox")
    delivery_lines: list[str] = []
    if delivery:
        delivery_lines.append(f"- Todo display: `{delivery['status']}`")
        if delivery.get("status") == "pending":
            delivery_lines.append(
                "- Canonical Todo state is committed; repair the display without repeating business work or quota spend."
            )
            delivery_lines.append(str(delivery.get("recommended_action") or ""))
    recovery_markdown = render_refresh_recovery_markdown(payload_dict)
    if recovery_markdown is not None:
        return "\n".join([recovery_markdown, *delivery_lines])
    state = _mapping_value(payload_dict, "state")
    frontmatter = _mapping_value(state, "frontmatter")
    lines = [
        "# LoopX State Refresh",
        "",
        f"- ok: `{payload_dict.get('ok')}`",
        f"- dry_run: `{payload_dict.get('dry_run')}`",
        f"- appended: `{payload_dict.get('appended')}`",
        f"- goal_id: `{payload_dict.get('goal_id')}`",
        f"- classification: `{payload_dict.get('classification')}`",
        f"- progress_scope: `{payload_dict.get('progress_scope')}`",
        f"- agent_id: `{payload_dict.get('agent_id')}`",
        f"- agent_lane: `{payload_dict.get('agent_lane')}`",
        f"- delivery_batch_scale: `{payload_dict.get('delivery_batch_scale')}`",
        f"- delivery_outcome: `{payload_dict.get('delivery_outcome')}`",
        f"- autonomous_replan_recorded: `{payload_dict.get('autonomous_replan_recorded')}`",
        f"- autonomous_replan_recorded_requested: `{payload_dict.get('autonomous_replan_recorded_requested')}`",
        f"- generated_at: `{payload_dict.get('generated_at')}`",
        f"- state_file: `{state.get('path')}`",
        f"- state_updated_at: `{frontmatter.get('updated_at')}`",
        f"- health_check: `{payload_dict.get('health_check')}`",
    ]
    lines.extend(delivery_lines)
    lines.extend(render_settlement_progress_markdown(payload_dict))
    if "external_sink_delivery_authorized" in payload_dict:
        lines.append(
            "- external_sink_delivery_authorized: "
            f"`{payload_dict.get('external_sink_delivery_authorized')}`"
        )
    delivery_workspace = _mapping_value(payload_dict, "delivery_workspace")
    if delivery_workspace:
        lines.append(
            "- delivery_workspace: "
            f"repository={delivery_workspace.get('task_repository')} "
            f"kind={delivery_workspace.get('workspace_kind')}"
        )
    if payload_dict.get("error"):
        lines.append(f"- error: {payload_dict.get('error')}")
        if payload_dict.get("error_detail"):
            lines.append(f"- detail: {payload_dict['error_detail']}")
        append_operator_action_markdown(lines, payload_dict)
        return "\n".join(lines)

    _render_state_diagnostic_lines(lines, payload_dict)
    lines.extend(
        [
            "",
            "## Recommended Action",
            f"- source: `{payload_dict.get('recommended_action_source')}`",
        ]
    )
    recommendation_resolution = _mapping_value(
        payload_dict, "recommended_action_resolution"
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
    lines.append(str(payload_dict.get("recommended_action") or ""))
    for heading, key in (
        ("Next Action", "next_action"),
        ("Recent Feedback", "recent_feedback"),
        ("Progress", "progress"),
    ):
        raw_values = state.get(key)
        values = (
            [str(item) for item in raw_values]
            if isinstance(raw_values, list)
            else []
        )
        if values:
            lines.extend(["", f"## {heading}"])
            lines.extend(f"- {value}" for value in section_list_items(values))
    return "\n".join(lines)
