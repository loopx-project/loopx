from __future__ import annotations

from typing import Any

from .decision_summary import compact_quota_decision

QUOTA_CLI_TODO_SUMMARY_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_todo_summary_compaction_v0"
)
QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND = (
    "quota should-run --include-detail agent-todos"
)
QUOTA_CLI_USER_TODO_SUMMARY_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_user_todo_summary_compaction_v0"
)
QUOTA_CLI_USER_TODO_SUMMARY_DETAIL_COMMAND = (
    "quota should-run --include-detail user-todos"
)
QUOTA_CLI_GOAL_BOUNDARY_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_goal_boundary_compaction_v0"
)
QUOTA_CLI_GOAL_BOUNDARY_DETAIL_COMMAND = (
    "quota should-run --include-detail goal-boundary"
)
QUOTA_CLI_VISION_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_vision_continuation_compaction_v0"
)
QUOTA_CLI_VISION_DETAIL_COMMAND = "quota should-run --include-detail vision"
QUOTA_CLI_REPLAN_ACTION_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_replan_action_compaction_v0"
)
QUOTA_CLI_CAPABILITY_GATE_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_capability_gate_compaction_v0"
)
QUOTA_CLI_ACTION_PORTFOLIO_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_action_portfolio_compaction_v1"
)
QUOTA_CLI_MONITOR_POLL_DECISION_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_monitor_poll_decision_compaction_v0"
)
QUOTA_CLI_MONITOR_POLL_DECISION_DETAIL_COMMAND = (
    "quota monitor-poll --include-detail decisions"
)
_RETAINED_AGENT_ITEM_LANES = {
    "first_executable_items": 3,
    "unclaimed_priority_open_items": 3,
    "monitor_due_items": 1,
    "monitor_capability_blocked_due_items": 2,
    "monitor_schedule_gap_items": 1,
    "current_agent_blocker_items": 2,
}
_RETAINED_USER_ITEM_LANES = {
    "first_open_items": 3,
    "gate_open_items": 3,
    "active_next_action_items": 3,
}
_RETAINED_AGENT_ITEM_FIELDS = (
    "schema_version",
    "todo_id",
    "index",
    "text",
    "status",
    "priority",
    "task_class",
    "action_kind",
    "task_domain",
    "claimed_by",
    "bound_agent",
    "goal_bound",
    "blocks_agent",
    "global_gate",
    "task_repository",
    "required_capabilities",
    "required_write_scopes",
    "excluded_agents",
    "unblocks_todo_id",
    "continuation_policy",
    "resume_when",
    "resume_monitor_generation",
    "target_key",
    "cadence",
    "next_due_at",
    "expires_at",
    "last_checked_at",
    "consecutive_no_change",
    "material_change",
    "material_change_generation",
    "result_hash",
    "reason",
)
_RETAINED_SUCCESSION_WARNING_TODO_IDS = 3
_RUNTIME_CAPABILITY_PREFIX_FIELDS = (
    "ok",
    "status_health_ok",
    "mode",
    "goal_id",
    "decision",
    "should_run",
)
_RETAINED_MONITOR_POLL_SELECTED_TODO_FIELDS = (
    "schema_version",
    "source",
    "todo_id",
    "index",
    "role",
    "priority",
    "status",
    "task_class",
    "action_kind",
    "task_domain",
    "task_repository",
    "claimed_by",
    "unblocks_todo_id",
    "agent_id",
    "selected_by",
    "selection_binding",
)
_RETAINED_MONITOR_POLL_INTERACTION_FIELDS = {
    "user_channel": (
        "action_required",
        "notify",
        "max_items",
        "actions",
        "non_blocking",
    ),
    "agent_channel": (
        "must_attempt",
        "delivery_allowed",
        "quiet_noop_allowed",
        "primary_action",
    ),
    "cli_channel": (
        "spend_allowed_now",
        "spend_after_validation",
        "spend_policy",
        "delivery_workspace_causality",
        "settlement_resume_ref",
    ),
}
_RETAINED_MONITOR_POLL_RESPONSE_PLAN_FIELDS = (
    "schema_version",
    "kind",
    "decision",
    "action_sequence",
    "silent_wait_allowed",
)
_RETAINED_MONITOR_POLL_FOLLOW_UP_SUMMARY_FIELDS = (
    "should_run",
    "effective_action",
    "state",
)


def _compact_monitor_poll_interaction_contract(
    contract: Any,
) -> dict[str, Any] | None:
    if not isinstance(contract, dict):
        return None
    compact = {
        key: contract[key]
        for key in ("schema_version", "mode")
        if key in contract
    }
    for channel_name, retained_fields in (
        _RETAINED_MONITOR_POLL_INTERACTION_FIELDS.items()
    ):
        channel = contract.get(channel_name)
        if not isinstance(channel, dict):
            continue
        compact[channel_name] = {
            key: channel[key] for key in retained_fields if key in channel
        }
    response_plan = contract.get("response_plan")
    if isinstance(response_plan, dict):
        compact["response_plan"] = {
            key: response_plan[key]
            for key in _RETAINED_MONITOR_POLL_RESPONSE_PLAN_FIELDS
            if key in response_plan
        }
    repository_delivery = contract.get("repository_delivery")
    if isinstance(repository_delivery, dict):
        compact["repository_delivery"] = repository_delivery
    return compact


def _compact_monitor_poll_decision(decision: dict[str, Any]) -> dict[str, Any]:
    compact = compact_quota_decision(decision)
    selected_todo = decision.get("selected_todo")
    if isinstance(selected_todo, dict):
        compact["selected_todo"] = {
            key: selected_todo[key]
            for key in _RETAINED_MONITOR_POLL_SELECTED_TODO_FIELDS
            if key in selected_todo
        }
    return compact


def compact_quota_monitor_poll_cli_payload(
    payload: dict[str, Any],
    *,
    include_decision_detail: bool = False,
) -> dict[str, Any]:
    """Project monitor-poll guards without duplicating full should-run payloads."""

    if include_decision_detail:
        return payload

    projected = dict(payload)
    decision_summary = (
        dict(payload["decision_summary"])
        if isinstance(payload.get("decision_summary"), dict)
        else {}
    )
    omitted_fields: dict[str, int] = {}
    for phase in ("before", "after"):
        full_decision = payload.get(phase)
        if isinstance(full_decision, dict):
            summary_decision = _compact_monitor_poll_decision(full_decision)
            compact_decision = summary_decision
            if phase == "after":
                compact_decision = dict(summary_decision)
                interaction_contract = _compact_monitor_poll_interaction_contract(
                    full_decision.get("interaction_contract")
                )
                if interaction_contract is not None:
                    compact_decision["interaction_contract"] = interaction_contract
            projected[phase] = compact_decision
            decision_summary[phase] = (
                {
                    key: summary_decision[key]
                    for key in _RETAINED_MONITOR_POLL_FOLLOW_UP_SUMMARY_FIELDS
                }
                if phase == "after"
                else summary_decision
            )
            omitted_fields[phase] = max(0, len(full_decision) - len(compact_decision))

            if phase == "before" and isinstance(payload.get("monitor_event"), dict):
                monitor_event = dict(payload["monitor_event"])
                monitor_event["before"] = compact_decision
                projected["monitor_event"] = monitor_event

    if decision_summary:
        projected["decision_summary"] = decision_summary
    if omitted_fields:
        payload_compaction = (
            dict(payload["payload_compaction"])
            if isinstance(payload.get("payload_compaction"), dict)
            else {}
        )
        payload_compaction["decisions"] = {
            "schema_version": QUOTA_CLI_MONITOR_POLL_DECISION_COMPACTION_SCHEMA_VERSION,
            "omitted_top_level_fields": omitted_fields,
            "detail_ref": {
                "request": QUOTA_CLI_MONITOR_POLL_DECISION_DETAIL_COMMAND,
            },
        }
        projected["payload_compaction"] = payload_compaction
    return projected


def _compact_agent_item(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    return {
        key: item[key]
        for key in _RETAINED_AGENT_ITEM_FIELDS
        if key in item
    }


def _compact_nested_item_lists(
    value: dict[str, Any],
    *,
    omitted_lanes: dict[str, int],
    path: str,
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, child in value.items():
        if isinstance(child, list) and key.endswith("items"):
            if path == "todo_succession_warning":
                todo_ids = [
                    str(item.get("todo_id"))
                    for item in child
                    if isinstance(item, dict) and item.get("todo_id")
                ][:_RETAINED_SUCCESSION_WARNING_TODO_IDS]
                if todo_ids:
                    compact["todo_ids"] = todo_ids
            if child:
                omitted_lanes[f"{path}.{key}"] = len(child)
            continue
        compact[key] = child
    return compact


def _compact_agent_todo_summary(
    summary: dict[str, Any], *, detail_command: str = QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    omitted_lanes: dict[str, int] = {}
    for key, value in summary.items():
        if isinstance(value, list):
            limit = _RETAINED_AGENT_ITEM_LANES.get(key)
            if limit is None:
                if value:
                    omitted_lanes[key] = len(value)
                continue
            compact[key] = [
                _compact_agent_item(item)
                for item in value[:limit]
            ]
            if len(value) > limit:
                omitted_lanes[key] = len(value) - limit
            continue
        if isinstance(value, dict):
            compact[key] = _compact_nested_item_lists(
                value,
                omitted_lanes=omitted_lanes,
                path=key,
            )
            continue
        compact[key] = value

    compact["payload_compaction"] = {
        "schema_version": QUOTA_CLI_TODO_SUMMARY_COMPACTION_SCHEMA_VERSION,
        "retained_item_lanes": sorted(
            lane
            for lane in _RETAINED_AGENT_ITEM_LANES
            if lane != "current_agent_blocker_items" or summary.get(lane)
        ),
        "omitted_lanes": omitted_lanes,
        "full_detail_cold_path": detail_command,
    }
    return compact


def _compact_user_todo_summary(
    summary: dict[str, Any], *, detail_command: str = QUOTA_CLI_USER_TODO_SUMMARY_DETAIL_COMMAND
) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    omitted_lanes: dict[str, int] = {}
    for key, value in summary.items():
        if isinstance(value, list):
            limit = _RETAINED_USER_ITEM_LANES.get(key)
            if limit is None:
                if value:
                    omitted_lanes[key] = len(value)
                continue
            compact[key] = value[:limit]
            if len(value) > limit:
                omitted_lanes[key] = len(value) - limit
            continue
        if isinstance(value, dict):
            compact[key] = _compact_nested_item_lists(
                value,
                omitted_lanes=omitted_lanes,
                path=key,
            )
            continue
        compact[key] = value

    compact["payload_compaction"] = {
        "schema_version": QUOTA_CLI_USER_TODO_SUMMARY_COMPACTION_SCHEMA_VERSION,
        "retained_item_lanes": sorted(_RETAINED_USER_ITEM_LANES),
        "omitted_lanes": omitted_lanes,
        "full_detail_cold_path": detail_command,
    }
    return compact


def _compact_goal_boundary(boundary: dict[str, Any]) -> dict[str, Any]:
    authority = boundary.get("checkpointed_boundary_authority")
    if not isinstance(authority, dict) or not isinstance(
        authority.get("entries"),
        list,
    ):
        return boundary

    compact_authority = dict(authority)
    entries = compact_authority.pop("entries")
    compact_authority["payload_compaction"] = {
        "schema_version": QUOTA_CLI_GOAL_BOUNDARY_COMPACTION_SCHEMA_VERSION,
        "omitted_entry_count": len(entries),
        "full_detail_cold_path": QUOTA_CLI_GOAL_BOUNDARY_DETAIL_COMMAND,
    }
    compact = dict(boundary)
    compact["checkpointed_boundary_authority"] = compact_authority
    return compact


def _compact_action_portfolio(portfolio: dict[str, Any]) -> dict[str, Any]:
    compact = dict(portfolio)
    suggested = portfolio.get("suggested_actions")
    if isinstance(suggested, list):
        compact["suggested_actions"] = [
            {
                **{
                    key: item[key]
                    for key in ("todo_id", "selection_role", "priority", "action_kind")
                    if key in item
                },
                **{
                    key: " ".join(str(item[key]).split())[:180]
                    for key in ("text", "continuation_hint")
                    if item.get(key)
                },
            }
            for item in suggested
            if isinstance(item, dict)
        ]
        compact["suggested_action_details"] = {
            "schema_version": QUOTA_CLI_ACTION_PORTFOLIO_COMPACTION_SCHEMA_VERSION,
            "inlined_fields": [
                "todo_id",
                "selection_role",
                "priority",
                "action_kind",
                "text",
                "continuation_hint",
            ],
            "ref": "$.agent_todo_summary.first_executable_items",
        }
    return compact


def _compact_vision_continuation_audit(
    audit: dict[str, Any],
) -> dict[str, Any]:
    retained_fields = (
        "schema_version",
        "required",
        "agent_id",
        "decision",
        "selected_todo_is_goal_completion",
        "closeout_allowed_without_evidence",
        "trigger_count",
        "trigger_kinds",
        "required_before_closeout",
        "recommended_action",
        "outcome_checkpoint_diagnostics",
    )
    compact = {key: audit[key] for key in retained_fields if key in audit}
    judge = audit.get("vision_gap_judge")
    if isinstance(judge, dict):
        retained_judge_fields = (
            "schema_version",
            "done",
            "decision",
            "reason",
            "evidence_read_instruction",
        )
        compact["vision_gap_judge"] = {
            key: judge[key] for key in retained_judge_fields if key in judge
        }
    omitted_fields = sorted(set(audit) - set(compact))
    compact["payload_compaction"] = {
        "schema_version": QUOTA_CLI_VISION_COMPACTION_SCHEMA_VERSION,
        "mode": "compact_hot_path",
        "omitted_fields": omitted_fields,
        "full_detail_cold_path": QUOTA_CLI_VISION_DETAIL_COMMAND,
    }
    return compact


def _reference_inline_replan_authoring(
    packet: dict[str, Any], *, payload: dict[str, Any]
) -> dict[str, Any]:
    """Reference an identical executable input already present in this response."""

    writeback = packet.get("writeback_contract")
    if not isinstance(writeback, dict) or not isinstance(
        writeback.get("vision_authoring"), dict
    ):
        return packet
    interaction = payload.get("interaction_contract")
    cli = interaction.get("cli_channel") if isinstance(interaction, dict) else None
    plan = cli.get("settlement_plan") if isinstance(cli, dict) else None
    steps = plan.get("ordered_steps") if isinstance(plan, dict) else None
    if not isinstance(steps, list):
        return packet
    index = next(
        (index for index, step in enumerate(steps)
         if isinstance(step, dict) and step.get("kind") == "durable_writeback"
         and step.get("vision_authoring") == writeback["vision_authoring"]),
        None,
    )
    if index is None:
        return packet
    reference = (
        "$.interaction_contract.cli_channel.settlement_plan.ordered_steps"
        f"[{index}].vision_authoring"
    )
    compact_writeback = dict(writeback)
    compact_writeback.pop("vision_authoring")
    compact_writeback["vision_authoring_ref"] = reference
    compact = dict(packet)
    compact["writeback_contract"] = compact_writeback
    compact["payload_compaction"] = {
        "schema_version": QUOTA_CLI_REPLAN_ACTION_COMPACTION_SCHEMA_VERSION,
        "mode": "compact_hot_path",
        "compacted_fields": ["writeback_contract.vision_authoring"],
        "projection_ref": reference,
    }
    return compact


def _vision_continuation_ref(audit: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: audit[key]
        for key in ("schema_version", "required", "decision")
        if key in audit
    }
    judge = audit.get("vision_gap_judge")
    if isinstance(judge, dict):
        compact["vision_gap_judge"] = {
            key: judge[key] for key in ("done", "decision") if key in judge
        }
        compact["vision_gap_judge"]["projection_ref"] = (
            "$.vision_continuation_audit.vision_gap_judge"
        )
    compact["projection_ref"] = "$.vision_continuation_audit"
    return compact


def _replace_nested_vision_continuation_audits(
    payload: dict[str, Any],
    *,
    source_audit: dict[str, Any],
    audit_ref: dict[str, Any],
) -> None:
    frontier = payload.get("goal_frontier_projection")
    if (
        isinstance(frontier, dict)
        and frontier.get("vision_continuation_audit") == source_audit
    ):
        compact_frontier = dict(frontier)
        compact_frontier["vision_continuation_audit"] = dict(audit_ref)
        payload["goal_frontier_projection"] = compact_frontier

    interaction = payload.get("interaction_contract")
    if not isinstance(interaction, dict):
        return
    compact_interaction = dict(interaction)
    for channel_name in ("agent_channel", "cli_channel"):
        channel = compact_interaction.get(channel_name)
        if (
            not isinstance(channel, dict)
            or channel.get("vision_continuation_audit") != source_audit
        ):
            continue
        compact_channel = dict(channel)
        compact_channel["vision_continuation_audit"] = dict(audit_ref)
        compact_interaction[channel_name] = compact_channel
    payload["interaction_contract"] = compact_interaction


def _compact_capability_gate(gate: dict[str, Any]) -> dict[str, Any]:
    omitted_fields = (
        "available",
        "candidate_order_policy",
        "runnable_candidates",
        "blocked_candidates",
        "resolution_bindings",
    )
    compact = {key: value for key, value in gate.items() if key not in omitted_fields}
    compact["payload_compaction"] = {
        "schema_version": QUOTA_CLI_CAPABILITY_GATE_COMPACTION_SCHEMA_VERSION,
        "omitted_fields": [key for key in omitted_fields if key in gate],
        "omitted_candidate_counts": {
            "runnable": len(gate.get("runnable_candidates") or []),
            "blocked": len(gate.get("blocked_candidates") or []),
            "resolution_bindings": len(gate.get("resolution_bindings") or []),
        },
        "full_detail_cold_path": QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND,
    }
    return compact


def _deduplicate_next_action_warning(
    warning: dict[str, Any],
    *,
    payload: dict[str, Any],
) -> dict[str, Any]:
    projection_fields = (
        "active_state_next_action",
        "latest_run_recommended_action",
        "agent_lane_next_action",
    )
    compact = dict(warning)
    projection_refs: dict[str, str] = {}
    for key in projection_fields:
        if key not in compact or key not in payload:
            continue
        projected_value = payload[key]
        if key == "agent_lane_next_action" and isinstance(projected_value, dict):
            projected_value = projected_value.get("text")
        if compact[key] != projected_value:
            continue
        compact.pop(key)
        projection_refs[key] = f"$.{key}"
    if projection_refs:
        compact["projection_refs"] = projection_refs
    return compact


def _deduplicate_agent_lane_next_action(item: dict[str, Any]) -> dict[str, Any]:
    text = item.get("text")
    title = item.get("title")
    if (
        not isinstance(text, str)
        or not isinstance(title, str)
        or (text != title and not text.endswith(f" {title}"))
    ):
        return item
    compact = dict(item)
    compact.pop("title", None)
    return compact


def _compact_goal_route_hint(route: dict[str, Any]) -> dict[str, Any]:
    other_actions = route.get("other_agent_next_actions")
    if not isinstance(other_actions, list) or not other_actions:
        return route
    compact = dict(route)
    compact.pop("other_agent_next_actions", None)
    compact["other_agent_next_action_count"] = len(other_actions)
    compact["other_agent_next_actions_detail_ref"] = (
        QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND
    )
    return compact


def _compact_shadowed_action_projections(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Keep the selected Todo as the only executable action on the hot path."""

    selected = payload.get("selected_todo")
    route = payload.get("goal_route_hint")
    if not isinstance(selected, dict) or not isinstance(route, dict):
        return payload
    current = route.get("current_agent_next_action")
    if (
        not route.get("selected_action_differs_from_durable")
        or not isinstance(current, dict)
        or current.get("todo_id") != selected.get("todo_id")
    ):
        return payload

    shadowed_fields = [
        key
        for key in (
            "active_state_next_action",
            "latest_run_recommended_action",
        )
        if payload.get(key)
    ]
    if not shadowed_fields:
        return payload

    compact = dict(payload)
    for key in shadowed_fields:
        compact.pop(key, None)

    warning = compact.get("next_action_projection_warning")
    if isinstance(warning, dict):
        compact_warning = dict(warning)
        projection_refs = compact_warning.get("projection_refs")
        if isinstance(projection_refs, dict):
            compact_refs = dict(projection_refs)
            for key in shadowed_fields:
                compact_refs.pop(key, None)
            if compact_refs:
                compact_warning["projection_refs"] = compact_refs
            else:
                compact_warning.pop("projection_refs", None)
        for key in shadowed_fields:
            compact_warning.pop(key, None)
        compact_warning["shadowed_by"] = "$.selected_todo"
        compact["next_action_projection_warning"] = compact_warning

    return compact


def _promote_runtime_capability_reentry(
    payload: dict[str, Any],
) -> dict[str, Any]:
    interaction = payload.get("interaction_contract")
    if not isinstance(interaction, dict):
        return payload
    cli_channel = interaction.get("cli_channel")
    if not isinstance(cli_channel, dict):
        return payload
    packet = cli_channel.get("runtime_capability_reentry")
    if not isinstance(packet, dict):
        return payload

    promoted = {
        key: payload[key] for key in _RUNTIME_CAPABILITY_PREFIX_FIELDS if key in payload
    }
    promoted["runtime_capability_reentry"] = packet
    promoted.update(
        (key, value) for key, value in payload.items() if key not in promoted
    )
    return promoted


def _promote_interaction_contract(payload: dict[str, Any]) -> dict[str, Any]:
    interaction = payload.get("interaction_contract")
    if not isinstance(interaction, dict):
        return payload
    promoted = {
        key: payload[key]
        for key in (
            *_RUNTIME_CAPABILITY_PREFIX_FIELDS,
            # Keep Turn receipt state ahead of potentially large guidance.
            "heartbeat_receipt",
            "runtime_capability_reentry",
        )
        if key in payload
    }
    promoted["interaction_contract"] = interaction
    promoted.update(
        (key, value) for key, value in payload.items() if key not in promoted
    )
    return promoted


def compact_quota_should_run_cli_payload(
    payload: dict[str, Any],
    *,
    include_todo_summary_detail: bool = False,
    include_user_todo_summary_detail: bool = False,
    include_goal_boundary_detail: bool = False,
    include_vision_detail: bool = False,
) -> dict[str, Any]:
    """Bound CLI-only diagnostics after the full decision is computed."""

    compact = payload
    compacted_roles: list[str] = []
    summary = payload.get("agent_todo_summary")
    if not include_todo_summary_detail and isinstance(summary, dict):
        compact = dict(compact)
        compact["agent_todo_summary"] = _compact_agent_todo_summary(summary)
        compacted_roles.append("agent")

    user_summary = payload.get("user_todo_summary")
    if not include_user_todo_summary_detail and isinstance(user_summary, dict):
        compact = dict(compact)
        compact["user_todo_summary"] = _compact_user_todo_summary(user_summary)
        compacted_roles.append("user")

    if compacted_roles:
        compact["todo_summary_projection"] = {
            "schema_version": QUOTA_CLI_TODO_SUMMARY_COMPACTION_SCHEMA_VERSION,
            "mode": "compact_hot_path",
            "compacted_roles": compacted_roles,
            "detail_ref": (
                QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND
                if compacted_roles[0] == "agent"
                else QUOTA_CLI_USER_TODO_SUMMARY_DETAIL_COMMAND
            ),
        }
    goal_boundary = payload.get("goal_boundary")
    if not include_goal_boundary_detail and isinstance(goal_boundary, dict):
        compact_goal_boundary = _compact_goal_boundary(goal_boundary)
        if compact_goal_boundary is not goal_boundary:
            compact = dict(compact)
            compact["goal_boundary"] = compact_goal_boundary
    vision_audit = payload.get("vision_continuation_audit")
    if not include_vision_detail and isinstance(vision_audit, dict):
        compact = dict(compact)
        compact_vision_audit = _compact_vision_continuation_audit(vision_audit)
        compact["vision_continuation_audit"] = compact_vision_audit
        _replace_nested_vision_continuation_audits(
            compact,
            source_audit=vision_audit,
            audit_ref=_vision_continuation_ref(compact_vision_audit),
        )
    replan_action = payload.get("replan_action_packet")
    if not include_vision_detail and isinstance(replan_action, dict):
        compact_replan_action = _reference_inline_replan_authoring(
            replan_action, payload=compact
        )
        if compact_replan_action is not replan_action:
            compact = dict(compact)
            compact["replan_action_packet"] = compact_replan_action
    if not include_todo_summary_detail:
        action_portfolio = payload.get("action_portfolio")
        if isinstance(action_portfolio, dict):
            compact = dict(compact)
            compact["action_portfolio"] = _compact_action_portfolio(
                action_portfolio
            )
        capability_gate = payload.get("capability_gate")
        if isinstance(capability_gate, dict):
            compact = dict(compact)
            compact["capability_gate"] = _compact_capability_gate(capability_gate)
        warning = payload.get("next_action_projection_warning")
        if isinstance(warning, dict):
            compact = dict(compact)
            compact["next_action_projection_warning"] = (
                _deduplicate_next_action_warning(warning, payload=payload)
            )
        agent_lane_next_action = payload.get("agent_lane_next_action")
        if isinstance(agent_lane_next_action, dict):
            compact = dict(compact)
            compact["agent_lane_next_action"] = _deduplicate_agent_lane_next_action(
                agent_lane_next_action
            )
        goal_route_hint = payload.get("goal_route_hint")
        if isinstance(goal_route_hint, dict):
            compact = dict(compact)
            compact["goal_route_hint"] = _compact_goal_route_hint(goal_route_hint)
        compact = _compact_shadowed_action_projections(compact)
    agent_lane_next_action = compact.get("agent_lane_next_action")
    if isinstance(agent_lane_next_action, dict) and "content_revision" in agent_lane_next_action:
        compact = dict(compact)
        agent_lane_next_action = dict(agent_lane_next_action)
        agent_lane_next_action.pop("content_revision", None)
        compact["agent_lane_next_action"] = agent_lane_next_action
    return _promote_interaction_contract(
        _promote_runtime_capability_reentry(compact)
    )


def compact_quota_plan_cli_payload(
    payload: dict[str, Any], *, detail_sections: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Bound read-only CLI summaries after complete planning; retain full opt-ins."""
    if payload.get("mode") not in {"status", "plan"} or not isinstance(payload.get("groups"), dict):
        return payload

    def project_row(row: dict[str, Any]) -> dict[str, Any]:
        result = dict(row)
        for role, compact_summary in (("agent", _compact_agent_todo_summary), ("user", _compact_user_todo_summary)):
            key = f"{role}_todos"
            if f"{role}-todos" not in detail_sections and isinstance(row.get(key), dict):
                result[key] = compact_summary(row[key], detail_command=(
                    f"quota {payload['mode']} --include-detail {role}-todos"
                ))
        return result

    result = dict(payload)
    result["groups"] = {state: [project_row(row) for row in rows]
                        for state, rows in payload["groups"].items()}
    if isinstance(payload.get("next_automatic_turn"), dict):
        result["next_automatic_turn"] = project_row(payload["next_automatic_turn"])
    return result
