from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def render_content_ops_exploration_plan_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Content-Ops Exploration Plan",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- private_source_bodies_read: `{payload.get('private_source_bodies_read')}`",
        f"- local_paths_captured: `{payload.get('local_paths_captured')}`",
        f"- autopublish_allowed: `{payload.get('autopublish_allowed')}`",
    ]
    plan = payload.get("exploration_plan")
    if isinstance(plan, Mapping):
        first_screen = plan.get("first_screen")
        if isinstance(first_screen, Mapping):
            lines.extend(
                [
                    "",
                    "## First Screen",
                    "",
                    f"- waiting_on: `{first_screen.get('waiting_on')}`",
                    f"- user_action_required: `{first_screen.get('user_action_required')}`",
                    f"- agent_can_continue: `{first_screen.get('agent_can_continue')}`",
                    f"- top_agent_action: {first_screen.get('top_agent_action')}",
                ]
            )
        lanes = plan.get("selected_source_lanes")
        if isinstance(lanes, Sequence) and not isinstance(lanes, (str, bytes)):
            lines.extend(["", "## Source Lanes", ""])
            for lane in lanes:
                if not isinstance(lane, Mapping):
                    continue
                lines.extend(
                    [
                        f"- `{lane.get('lane_id')}`: status=`{lane.get('source_status')}`, "
                        f"read=`{lane.get('read_status')}`, "
                        f"promotion=`{lane.get('promotion_target')}`, "
                        f"user_gate=`{lane.get('requires_user_gate')}`",
                    ]
                )
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        raw_errors = validation.get("errors")
        errors = raw_errors if isinstance(raw_errors, list) else []
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_ok: `{validation.get('ok')}`",
                f"- lane_count: `{validation.get('lane_count')}`",
                f"- error_count: `{len(errors)}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"



def render_content_ops_public_handle_observation_markdown(
    payload: dict[str, Any],
) -> str:
    lines = [
        "# LoopX Content-Ops Public Handle Observation",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- private_source_bodies_read: `{payload.get('private_source_bodies_read')}`",
        f"- autopublish_allowed: `{payload.get('autopublish_allowed')}`",
    ]
    source_item = payload.get("source_item")
    if isinstance(source_item, Mapping):
        lines.extend(
            [
                "",
                "## Source Item",
                "",
                f"- source_item_id: `{source_item.get('source_item_id')}`",
                f"- source_kind: `{source_item.get('source_kind')}`",
                f"- source_status: `{source_item.get('source_status')}`",
                f"- allowed_use: `{source_item.get('allowed_use')}`",
                f"- attribution: `{source_item.get('attribution')}`",
            ]
        )
    observation = payload.get("observation")
    if isinstance(observation, Mapping):
        lines.extend(
            [
                "",
                "## Observation",
                "",
                f"- http_method: `{observation.get('http_method')}`",
                f"- http_status: `{observation.get('http_status')}`",
                f"- url_effective: `{observation.get('url_effective')}`",
                f"- content_bytes_read: `{observation.get('content_bytes_read')}`",
                f"- external_write_performed: `{observation.get('external_write_performed')}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"



def render_content_ops_packet_aggregation_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Content-Ops Packet Aggregation",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- private_source_bodies_read: `{payload.get('private_source_bodies_read')}`",
        f"- private_source_content_read: `{payload.get('private_source_content_read')}`",
        f"- autopublish_allowed: `{payload.get('autopublish_allowed')}`",
    ]
    input_summary = payload.get("input_summary")
    if isinstance(input_summary, Mapping):
        lines.extend(
            [
                "",
                "## Inputs",
                "",
                f"- public_handle_packet_count: `{input_summary.get('public_handle_packet_count')}`",
                f"- private_connector_gate_packet_count: `{input_summary.get('private_connector_gate_packet_count')}`",
                f"- source_item_count: `{input_summary.get('source_item_count')}`",
                f"- owner_gate_required_count: `{input_summary.get('owner_gate_required_count')}`",
            ]
        )
    projection = payload.get("projection")
    if isinstance(projection, Mapping):
        first_screen = projection.get("first_screen")
        if isinstance(first_screen, Mapping):
            lines.extend(
                [
                    "",
                    "## First Screen",
                    "",
                    f"- waiting_on: `{first_screen.get('waiting_on')}`",
                    f"- user_action_required: `{first_screen.get('user_action_required')}`",
                    f"- agent_can_continue: `{first_screen.get('agent_can_continue')}`",
                    f"- next_safe_action: {first_screen.get('next_safe_action')}",
                ]
            )
        connector_trials = projection.get("connector_trials")
        if isinstance(connector_trials, Mapping):
            lines.extend(
                [
                    "",
                    "## Connector Trials",
                    "",
                    f"- states: `{connector_trials.get('states')}`",
                    f"- owner_gate_required_count: `{connector_trials.get('owner_gate_required_count')}`",
                ]
            )
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        raw_errors = validation.get("errors")
        errors = raw_errors if isinstance(raw_errors, list) else []
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_ok: `{validation.get('ok')}`",
                f"- error_count: `{len(errors)}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"



def render_content_ops_walkthrough_artifact_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Content-Ops Walkthrough Artifact",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- public_repo_safe: `{payload.get('public_repo_safe')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- private_source_bodies_read: `{payload.get('private_source_bodies_read')}`",
        f"- private_source_content_read: `{payload.get('private_source_content_read')}`",
        f"- autopublish_allowed: `{payload.get('autopublish_allowed')}`",
    ]
    artifact = payload.get("operator_artifact")
    if isinstance(artifact, Mapping):
        lines.extend(["", "## Operator Artifact", "", str(artifact.get("headline") or "")])
        preview = artifact.get("private_operator_preview")
        if isinstance(preview, Mapping):
            lines.extend(
                [
                    "",
                    "## Private Operator Preview",
                    "",
                    (
                        "- available_in_current_operator_session: "
                        f"`{preview.get('available_in_current_operator_session')}`"
                    ),
                    f"- sample_record_count: `{preview.get('sample_record_count')}`",
                    f"- theme_signals: `{preview.get('theme_signals')}`",
                    f"- stored_in_repo: `{preview.get('stored_in_repo')}`",
                    (
                        "- source_content_recorded: "
                        f"`{preview.get('source_content_recorded')}`"
                    ),
                    (
                        "- response_payload_recorded: "
                        f"`{preview.get('response_payload_recorded')}`"
                    ),
                ]
            )
        draft_gate = artifact.get("draft_gate")
        if isinstance(draft_gate, Mapping):
            lines.extend(
                [
                    "",
                    "## Draft Gate",
                    "",
                    f"- draft_id: `{draft_gate.get('draft_id')}`",
                    f"- state: `{draft_gate.get('state')}`",
                    f"- publish_status: `{draft_gate.get('publish_status')}`",
                    f"- approval_required: `{draft_gate.get('approval_required')}`",
                    f"- autopublish_allowed: `{draft_gate.get('autopublish_allowed')}`",
                ]
            )
    chain_steps = payload.get("chain_steps")
    if isinstance(chain_steps, Sequence) and not isinstance(chain_steps, (str, bytes)):
        lines.extend(["", "## Chain Steps", ""])
        for step in chain_steps:
            if not isinstance(step, Mapping):
                continue
            lines.append(f"- `{step.get('step')}`: {step.get('result')}")
    projection = payload.get("aggregation_projection")
    if isinstance(projection, Mapping):
        first_screen = projection.get("first_screen")
        if isinstance(first_screen, Mapping):
            lines.extend(
                [
                    "",
                    "## First Screen",
                    "",
                    f"- waiting_on: `{first_screen.get('waiting_on')}`",
                    f"- user_action_required: `{first_screen.get('user_action_required')}`",
                    f"- next_safe_action: {first_screen.get('next_safe_action')}",
                ]
            )
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        raw_errors = validation.get("errors")
        errors = raw_errors if isinstance(raw_errors, list) else []
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_ok: `{validation.get('ok')}`",
                f"- error_count: `{len(errors)}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"



def render_content_ops_chatview_report_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Content-Ops ChatView Report",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- aggregation_ready: `{payload.get('aggregation_ready')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- private_source_bodies_read: `{payload.get('private_source_bodies_read')}`",
        f"- private_source_content_read: `{payload.get('private_source_content_read')}`",
        f"- autopublish_allowed: `{payload.get('autopublish_allowed')}`",
    ]
    report = payload.get("chatview_report")
    if isinstance(report, Mapping):
        lines.extend(
            [
                "",
                "## Operator Card",
                "",
                str(report.get("operator_card") or payload.get("operator_card") or ""),
            ]
        )
        observed_shape = report.get("observed_shape")
        if isinstance(observed_shape, Mapping):
            lines.extend(
                [
                    "",
                    "## Observed Shape",
                    "",
                    f"- channel_count: `{observed_shape.get('channel_count')}`",
                    f"- recent_record_count: `{observed_shape.get('recent_record_count')}`",
                    f"- report_count: `{observed_shape.get('report_count')}`",
                    f"- api_request_count: `{observed_shape.get('api_request_count')}`",
                    f"- api_path_counts: `{observed_shape.get('api_path_counts')}`",
                ]
            )
        boundary = report.get("boundary")
        if isinstance(boundary, Mapping):
            lines.extend(
                [
                    "",
                    "## Boundary",
                    "",
                    f"- source_bodies_saved: `{boundary.get('source_bodies_saved')}`",
                    f"- response_payloads_saved: `{boundary.get('response_payloads_saved')}`",
                    f"- external_write_performed: `{boundary.get('external_write_performed')}`",
                    f"- autopublish_allowed: `{boundary.get('autopublish_allowed')}`",
                ]
            )
    todo = payload.get("user_todo_projection")
    if isinstance(todo, Mapping):
        lines.extend(
            [
                "",
                "## User Todo Projection",
                "",
                f"- action_kind: `{todo.get('action_kind')}`",
                f"- title: {todo.get('title')}",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"



def render_content_ops_private_connector_gate_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Content-Ops Private Connector Gate",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- owner_gate_required: `{payload.get('owner_gate_required')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- private_source_bodies_read: `{payload.get('private_source_bodies_read')}`",
        f"- autopublish_allowed: `{payload.get('autopublish_allowed')}`",
    ]
    connector = payload.get("connector")
    if isinstance(connector, Mapping):
        lines.extend(
            [
                "",
                "## Connector",
                "",
                f"- connector_id: `{connector.get('connector_id')}`",
                f"- connector_name: `{connector.get('connector_name')}`",
                f"- access_mode: `{connector.get('access_mode')}`",
                f"- allowed_use: `{connector.get('allowed_use')}`",
            ]
        )
    gate = payload.get("owner_gate")
    if isinstance(gate, Mapping):
        lines.extend(
            [
                "",
                "## Owner Gate",
                "",
                f"- gate_id: `{gate.get('gate_id')}`",
                f"- status: `{gate.get('status')}`",
                f"- approval_required: `{gate.get('approval_required')}`",
                f"- requested_decision: `{gate.get('requested_decision')}`",
            ]
        )
    todo = payload.get("user_todo_projection")
    if isinstance(todo, Mapping):
        lines.extend(
            [
                "",
                "## User Todo Projection",
                "",
                f"- action_kind: `{todo.get('action_kind')}`",
                f"- title: {todo.get('title')}",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"



def render_content_ops_preview_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Content-Ops Preview",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- private_source_bodies_read: `{payload.get('private_source_bodies_read')}`",
        f"- autopublish_allowed: `{payload.get('autopublish_allowed')}`",
    ]
    projection = payload.get("projection")
    if isinstance(projection, Mapping):
        first_screen = projection.get("first_screen")
        if isinstance(first_screen, Mapping):
            lines.extend(
                [
                    "",
                    "## First Screen",
                    "",
                    f"- waiting_on: `{first_screen.get('waiting_on')}`",
                    f"- user_action_required: `{first_screen.get('user_action_required')}`",
                    f"- agent_can_continue: `{first_screen.get('agent_can_continue')}`",
                    f"- next_safe_action: {first_screen.get('next_safe_action')}",
                ]
            )
        connector_trials = projection.get("connector_trials")
        if isinstance(connector_trials, Mapping):
            lines.extend(
                [
                    "",
                    "## Connector Trials",
                    "",
                    f"- count: `{connector_trials.get('count')}`",
                    f"- ready_for_metadata_trial_count: `{connector_trials.get('ready_for_metadata_trial_count')}`",
                    f"- owner_gate_required_count: `{connector_trials.get('owner_gate_required_count')}`",
                    f"- surfaces: `{connector_trials.get('surfaces')}`",
                    f"- access_modes: `{connector_trials.get('access_modes')}`",
                ]
            )
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        raw_errors = validation.get("errors")
        errors = raw_errors if isinstance(raw_errors, list) else []
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_ok: `{validation.get('ok')}`",
                f"- error_count: `{len(errors)}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"
