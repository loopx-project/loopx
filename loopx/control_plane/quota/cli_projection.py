from __future__ import annotations

from typing import Any

from .decision_summary import compact_quota_decision
from .should_run_packet import reference_duplicate_fallback_guidance

QUOTA_CLI_TODO_SUMMARY_DETAIL_COMMAND = (
    "quota should-run --include-detail agent-todos"
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
QUOTA_CLI_MONITOR_POLL_DECISION_COMPACTION_SCHEMA_VERSION = (
    "quota_cli_monitor_poll_decision_compaction_v0"
)
QUOTA_CLI_MONITOR_POLL_DECISION_DETAIL_COMMAND = (
    "quota monitor-poll --include-detail decisions"
)
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


def _compact_vision_continuation_audit(
    audit: dict[str, Any],
) -> dict[str, Any]:
    # This is an executable decision contract, including the evidence and
    # continuation rules in its prose. Keep it complete once; identical
    # nested copies can reference this in-response owner below.
    compact = dict(audit)
    compact["payload_compaction"] = {
        "schema_version": QUOTA_CLI_VISION_COMPACTION_SCHEMA_VERSION,
        "mode": "compact_hot_path",
        "omitted_fields": [],
        "full_detail_cold_path": QUOTA_CLI_VISION_DETAIL_COMMAND,
    }
    return compact


def _reference_inline_replan_authoring(
    packet: dict[str, Any], *, payload: dict[str, Any]
) -> dict[str, Any]:
    """Legacy display metadata around the shared complete authoring owner."""
    from .turn_envelope import _turn_envelope_result

    shared = _turn_envelope_result("reference_replan_authoring",
        payload={**payload, "replan_action_packet": packet})["replan_action_packet"]
    if shared == packet:
        return packet
    reference = shared["writeback_contract"]["vision_authoring_ref"]
    return {**shared, "payload_compaction": {
        "schema_version": QUOTA_CLI_REPLAN_ACTION_COMPACTION_SCHEMA_VERSION,
        "mode": "compact_hot_path",
        "compacted_fields": ["writeback_contract.vision_authoring"],
        "projection_ref": reference,
    }}


def _reference_vision_continuation(payload: dict[str, Any]) -> dict[str, Any]:
    """Legacy display delegates exact audit sharing to the complete owner."""
    from .turn_envelope import _turn_envelope_result

    return _turn_envelope_result("reference_vision_continuation", payload=payload)


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
    # Display-only omission stays local; exact source sharing runs once below.
    text = item.get("text")
    title = item.get("title")
    if isinstance(title, str) and isinstance(text, str) and (text == title or text.endswith(f" {title}")):
        compact = dict(item)
        compact.pop("title", None)
        return compact
    return item


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


def _reference_context(
    payload: dict[str, Any], *, task_title: bool = True, explore_guidance: bool = True,
    goal_acceptance: bool = False, diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """One coarse transport call for the existing typed context owner."""
    contract = payload.get("interaction_contract")
    agent = contract.get("agent_channel") if isinstance(contract, dict) else None
    context = agent.get("work_context") if isinstance(agent, dict) else None
    # Task/Explore sharing needs a source carrier; acceptance may use other root
    # owners. Leave semantic matching and reference validation in TypeScript.
    if diagnostics is None and not goal_acceptance and (
        not (task_title or explore_guidance) or not isinstance(context, dict) or not context.get("sources")
    ):
        return payload
    from .turn_envelope import _turn_envelope_result

    return _turn_envelope_result("reference_context", payload=payload,
        task_title=task_title, explore_guidance=explore_guidance,
        goal_acceptance=goal_acceptance, **({"diagnostics": diagnostics} if diagnostics is not None else {}))


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
    goal_boundary = payload.get("goal_boundary")
    if not include_goal_boundary_detail and isinstance(goal_boundary, dict):
        compact_goal_boundary = _compact_goal_boundary(goal_boundary)
        if compact_goal_boundary is not goal_boundary:
            compact = dict(compact)
            compact["goal_boundary"] = compact_goal_boundary
    vision_audit = payload.get("vision_continuation_audit")
    if not include_vision_detail and isinstance(vision_audit, dict):
        compact = _reference_vision_continuation(compact)
        compact["vision_continuation_audit"] = _compact_vision_continuation_audit(vision_audit)
    replan_action = payload.get("replan_action_packet")
    if not include_vision_detail and isinstance(replan_action, dict):
        compact_replan_action = _reference_inline_replan_authoring(
            replan_action, payload=compact
        )
        if compact_replan_action is not replan_action:
            compact = dict(compact)
            compact["replan_action_packet"] = compact_replan_action
    if not include_todo_summary_detail:
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
                agent_lane_next_action,
            )
        goal_route_hint = payload.get("goal_route_hint")
        if isinstance(goal_route_hint, dict):
            compact = dict(compact)
            compact["goal_route_hint"] = _compact_goal_route_hint(goal_route_hint)
        compact = _compact_shadowed_action_projections(compact)
        compact = reference_duplicate_fallback_guidance(compact)
    agent_lane_next_action = compact.get("agent_lane_next_action")
    if isinstance(agent_lane_next_action, dict) and "content_revision" in agent_lane_next_action:
        compact = dict(compact)
        agent_lane_next_action = dict(agent_lane_next_action)
        agent_lane_next_action.pop("content_revision", None)
        compact["agent_lane_next_action"] = agent_lane_next_action
    compact = _reference_context(compact, task_title=not include_todo_summary_detail,
        explore_guidance=not include_vision_detail, goal_acceptance=True, diagnostics={
            "agent_detail": include_todo_summary_detail, "user_detail": include_user_todo_summary_detail})
    return _promote_interaction_contract(
        _promote_runtime_capability_reentry(compact)
    )


def compact_quota_plan_cli_payload(
    payload: dict[str, Any], *, detail_sections: frozenset[str] = frozenset()
) -> dict[str, Any]:
    """Bound read-only CLI summaries after complete planning; retain full opt-ins."""
    if payload.get("mode") not in {"status", "plan"} or not isinstance(payload.get("groups"), dict):
        return payload

    return _reference_context(payload, task_title=False, explore_guidance=False, diagnostics={
        "agent_detail": "agent-todos" in detail_sections,
        "user_detail": "user-todos" in detail_sections})
