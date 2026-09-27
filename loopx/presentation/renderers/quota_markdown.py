from __future__ import annotations

from typing import Any

from ...control_plane import control_plane_policy_summary
from ...control_plane.quota.states import QUOTA_STATE_ORDER
from ...control_plane.projection_envelope_facts import render_projection_envelope_markdown
from ...control_plane.runtime.decision_freshness import (
    DECISION_FRESHNESS_WARNING_ITEM_LIMIT,
)
from ...execution_profile import execution_profile_summary
from ...long_task_cadence import long_task_cadence_hint_summary
from ...orchestration import orchestration_policy_summary
from ..markdown import (
    append_operator_action_markdown,
    as_dict,
    as_list,
    markdown_scalar,
)


def _append_fallback_projection_markdown(
    lines: list[str],
    fallback: dict[str, Any],
    *,
    label: str,
    blocked_items_key: str,
    blocked_item_label: str,
    blocked_gate_key: str | None = None,
) -> None:
    if not fallback:
        return
    selected = as_dict(fallback.get("selected_executable"))
    blocked_items = as_list(fallback.get(blocked_items_key))
    lines.append(
        f"- {label}_fallback: "
        f"notify_user={fallback.get('notify_user')} "
        f"blocked_count={len(blocked_items)} "
        f"selected_index={selected.get('index')} "
        f"reason={fallback.get('reason')}"
    )
    blocked_gate = as_dict(fallback.get(blocked_gate_key)) if blocked_gate_key else {}
    if blocked_gate.get("text"):
        lines.append(f"- {label}: {blocked_gate.get('text')}")
    for blocked in blocked_items[:3]:
        if not isinstance(blocked, dict):
            continue
        text = str(blocked.get("text") or "").strip()
        if not text:
            continue
        index = blocked.get("index")
        suffix = f"[{index}]" if index is not None else ""
        lines.append(f"- {blocked_item_label}{suffix}: {text}")
    if selected.get("text"):
        lines.append(f"- {label}_selected: {selected.get('text')}")
    if fallback.get("recommended_action"):
        lines.append(f"- {label}_action: {fallback.get('recommended_action')}")


def _compact_todo_identity(todo: dict[str, Any]) -> str:
    return " ".join(
        f"{key}={value}"
        for key in ("todo_id", "status", "priority", "task_class", "action_kind")
        if (value := str(todo.get(key) or "").strip())
    )


def _render_todo_row(todo: Any, *, label: str, row_kind: str) -> tuple[tuple[str, Any, str], str] | None:
    if not isinstance(todo, dict):
        return None
    text = str(todo.get("text") or "").strip()
    if not text:
        return None
    index = todo.get("index")
    suffix = f"[{index}]" if index is not None else ""
    metadata = ""
    if row_kind == "next" and label == "agent_todo" and todo.get("todo_id"):
        metadata = f" todo_id={todo.get('todo_id')}"
    elif todo.get("claimed_by"):
        metadata = f" claimed_by={todo.get('claimed_by')}"
    key = (str(todo.get("todo_id") or ""), todo.get("index"), text)
    return key, f"- {label}_{row_kind}{suffix}: {text}{metadata}"


def _append_todo_summary(lines: list[str], label: str, summary: dict[str, Any]) -> None:
    summary_parts = [
        f"open={summary.get('open_count')}",
        f"total={summary.get('total_count')}",
    ]
    if summary.get("claimed_open_count"):
        summary_parts[1:1] = [
            f"claimed={summary.get('claimed_open_count')}",
            f"unclaimed={summary.get('unclaimed_open_count', 0)}",
        ]
    for key, name in (
        ("monitor_due_count", "monitor_due"),
        ("monitor_schedule_gap_count", "monitor_schedule_gap"),
        ("current_agent_blocker_count", "current_agent_blocker"),
        ("completed_without_successor_count", "succession_warning"),
    ):
        if summary.get(key):
            summary_parts.append(f"{name}={summary.get(key)}")
    lines.append(f"- {label}_summary: {' '.join(summary_parts)}")

    for lane, suffix, limit in (
        ("unclaimed_priority_open_items", "unclaimed_candidates", 3),
        ("monitor_due_items", "monitor_due", 1),
        ("monitor_capability_blocked_due_items", "monitor_capability_blocked_due", 2),
        ("monitor_schedule_gap_items", "monitor_schedule_gap", 1),
        ("current_agent_blocker_items", "current_agent_blocker", 2),
    ):
        lane_items = as_list(summary.get(lane))
        identities = [
            (
                f"{_compact_todo_identity(item)} "
                f"reason={str(item.get('reason') or '').strip()[:120]}"
                if lane == "current_agent_blocker_items"
                and str(item.get("reason") or "").strip()
                else _compact_todo_identity(item)
            )
            for item in lane_items[:limit]
            if isinstance(item, dict) and item.get("todo_id")
        ]
        if identities:
            lines.append(f"- {label}_{suffix}: {'; '.join(identities)}")

    succession_warning = as_dict(summary.get("todo_succession_warning"))
    if succession_warning:
        projected_todo_ids = as_list(succession_warning.get("todo_ids"))
        warning_items = as_list(succession_warning.get("items"))
        todo_ids = [
            str(todo_id)
            for todo_id in projected_todo_ids[:3]
            if str(todo_id or "").strip()
        ] or [
            str(item.get("todo_id"))
            for item in warning_items[:3]
            if isinstance(item, dict) and item.get("todo_id")
        ]
        todo_ids_text = ",".join(todo_ids) if todo_ids else "n/a"
        lines.append(
            f"- {label}_succession_warning: "
            f"reason={succession_warning.get('reason_code')} "
            f"count={succession_warning.get('count')} "
            f"todo_ids={todo_ids_text}"
        )

    first_open_key = (
        "first_executable_items"
        if label == "agent_todo"
        and isinstance(summary.get("first_executable_items"), list)
        else "first_open_items"
    )
    first_open = list(as_list(summary.get(first_open_key)))
    if label == "user_todo" and isinstance(summary.get("user_action_items"), list):
        first_open.extend(
            item
            for item in summary.get("user_action_items", [])
            if isinstance(item, dict)
        )
    for todo in first_open[:3]:
        if row := _render_todo_row(todo, label=label, row_kind="next"):
            lines.append(row[1])

    first_keys = {
        (
            str(todo.get("todo_id") or ""),
            todo.get("index"),
            str(todo.get("text") or "").strip(),
        )
        for todo in first_open
        if isinstance(todo, dict)
    }
    extra_count = 0
    for todo in as_list(summary.get("backlog_items")):
        row = _render_todo_row(todo, label=label, row_kind="backlog")
        if row is None or row[0] in first_keys:
            continue
        lines.append(row[1])
        extra_count += 1
        if extra_count >= 5:
            break


def render_quota_markdown(payload: dict[str, Any]) -> str:
    title = "Quota Plan" if payload.get("mode") == "plan" else "Quota Status"
    lines = [
        f"# LoopX {title}",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- goals: `{payload.get('goal_count')}`",
        f"- runs: `{payload.get('run_count')}`",
    ]
    if payload.get("goal_filter"):
        lines.append(f"- goal_filter: `{markdown_scalar(payload['goal_filter'])}` (counts and ranking cover this Goal only)")
    if payload.get("reason"):
        lines.append(f"- reason: {markdown_scalar(payload['reason'])}")
    if payload.get("recommended_action"):
        lines.append(f"- action: {markdown_scalar(payload['recommended_action'])}")
    if payload.get("status_projection_envelope"):
        lines.extend(render_projection_envelope_markdown(payload["status_projection_envelope"]))
    summary = as_dict(payload.get("summary"))
    states = as_dict(summary.get("states"))
    state_text = ", ".join(f"{state}={states.get(state, 0)}" for state in QUOTA_STATE_ORDER)
    lines.append(
        "- summary: "
        f"registered_goals={summary.get('registered_goals')}, "
        f"health_blockers={summary.get('health_blockers')}, "
        f"next_automatic_turn={summary.get('next_automatic_turn') or 'none'}"
    )
    lines.append(f"- states: {state_text}")

    next_turn = as_dict(payload.get("next_automatic_turn"))
    next_title = "Next Automatic Turn Within Selected Goal" if payload.get("goal_filter") else "Next Automatic Turn"
    lines.extend(["", f"## {next_title}"])
    if next_turn:
        quota = as_dict(next_turn.get("quota"))
        lines.append(
            "- "
            f"`{next_turn.get('goal_id')}` "
            f"compute={quota.get('compute')} "
            f"slot_minutes={quota.get('slot_minutes')} "
            f"slots={quota.get('spent_slots')}/{quota.get('allowed_slots')} "
            f"action={next_turn.get('recommended_action') or 'inspect latest run'}"
        )
    else:
        lines.append("- none")

    health_items = as_list(payload.get("health_items"))
    if health_items:
        lines.extend(["", "## Health Items"])
        for item in health_items:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- "
                f"`{item.get('goal_id')}` "
                f"severity={item.get('severity')} "
                f"waiting_on={item.get('waiting_on')} "
                f"action={item.get('recommended_action')}"
            )

    groups = as_dict(payload.get("groups"))
    lines.extend(["", "## Groups"])
    render_states = list(QUOTA_STATE_ORDER)
    if groups.get("unknown"):
        render_states.append("unknown")
    for state in render_states:
        items = as_list(groups.get(state))
        if payload.get("mode") == "plan" and not items:
            continue
        lines.extend(["", f"### {state}"])
        if not items:
            lines.append("- none")
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            quota = as_dict(item.get("quota"))
            action = markdown_scalar(item.get("recommended_action") or "")
            lines.append(
                "- "
                f"`{item.get('goal_id')}`: "
                f"compute={quota.get('compute')} "
                f"slot_minutes={quota.get('slot_minutes')} "
                f"slots={quota.get('spent_slots')}/{quota.get('allowed_slots')} "
                f"waiting_on={item.get('waiting_on')} "
                f"status={item.get('status')} "
                f"phase={item.get('lifecycle_phase')}"
            )
            reason = quota.get("reason")
            if reason:
                lines.append(f"  - reason: {reason}")
            if action:
                lines.append(f"  - action: {action}")
            control_plane = as_dict(item.get("control_plane"))
            if control_plane:
                lines.append(f"  - control_plane: {control_plane_policy_summary(control_plane)}")
            if item.get("agent_command"):
                lines.append(f"  - agent_command: `{item.get('agent_command')}`")
            if item.get("next_handoff_condition"):
                lines.append(f"  - next_handoff_condition: {item.get('next_handoff_condition')}")
    append_operator_action_markdown(lines, payload)
    return "\n".join(lines)


def render_quota_scheduler_ack_markdown(payload: dict[str, Any]) -> str:
    event = as_dict(payload.get("scheduler_ack_event"))
    state = as_dict(event.get("scheduler_state"))
    before = as_dict(event.get("before"))
    lines = [
        "# LoopX Quota Scheduler Ack",
        "",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- classification: `{payload.get('classification')}`",
        f"- agent_id: `{payload.get('agent_id') or event.get('agent_id') or state.get('agent_id') or ''}`",
        f"- surface: `{payload.get('surface') or event.get('surface')}`",
        f"- state_key: `{payload.get('state_key') or event.get('state_key')}`",
        f"- applied_rrule: `{payload.get('applied_rrule') or event.get('applied_rrule')}`",
        f"- progression_index: `{state.get('progression_index')}`",
        f"- reset_token: `{state.get('reset_token') or ''}`",
        f"- identity_signature: `{state.get('identity_signature') or ''}`",
        f"- appended: `{payload.get('appended')}`",
        f"- registry_mutated: `{payload.get('registry_mutated')}`",
        f"- effective_action: `{before.get('effective_action')}`",
        f"- state: `{before.get('state')}`",
        f"- should_run: `{before.get('should_run')}`",
        f"- health_check: {payload.get('health_check') or 'scheduler ack state updated; no quota spend'}",
    ]
    if payload.get("scheduler_state_path"):
        lines.append(f"- scheduler_state_path: `{payload.get('scheduler_state_path')}`")
    if payload.get("reason"):
        lines.append(f"- reason: {payload.get('reason')}")
    append_operator_action_markdown(lines, payload)
    return "\n".join(lines)


def render_quota_scheduler_failure_markdown(payload: dict[str, Any]) -> str:
    event = as_dict(payload.get("scheduler_failure_event"))
    state = as_dict(event.get("scheduler_state"))
    failure = as_dict(state.get("host_update_failure"))
    before = as_dict(event.get("before"))
    lines = [
        "# LoopX Quota Scheduler Host Update Failure",
        "",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- classification: `{payload.get('classification')}`",
        f"- agent_id: `{payload.get('agent_id') or state.get('agent_id') or ''}`",
        f"- surface: `{payload.get('surface') or event.get('surface')}`",
        f"- state_key: `{payload.get('state_key') or event.get('state_key')}`",
        f"- failed_rrule: `{payload.get('failed_rrule') or failure.get('target_rrule')}`",
        f"- observed_host_rrule: `{payload.get('observed_host_rrule') or failure.get('observed_host_rrule') or ''}`",
        f"- failure_kind: `{payload.get('failure_kind') or failure.get('failure_kind')}`",
        f"- failure_count: `{failure.get('failure_count')}`",
        f"- scheduler_state_mutated: `{payload.get('scheduler_state_mutated')}`",
        f"- effective_action: `{before.get('effective_action')}`",
        f"- should_run: `{before.get('should_run')}`",
        f"- health_check: {payload.get('health_check') or 'scheduler host update failure recorded; no quota spend'}",
    ]
    if payload.get("scheduler_state_path"):
        lines.append(f"- scheduler_state_path: `{payload.get('scheduler_state_path')}`")
    if payload.get("reason"):
        lines.append(f"- reason: {payload.get('reason')}")
    append_operator_action_markdown(lines, payload)
    return "\n".join(lines)


def render_quota_should_run_markdown(payload: dict[str, Any]) -> str:
    quota = as_dict(payload.get("quota"))
    lines = [
        "# LoopX Quota Should Run",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- decision: `{payload.get('decision')}`",
        f"- should_run: `{payload.get('should_run')}`",
        f"- normal_delivery_allowed: `{payload.get('normal_delivery_allowed')}`",
        f"- recovery_delivery_allowed: `{payload.get('recovery_delivery_allowed')}`",
        f"- self_repair_allowed: `{payload.get('self_repair_allowed')}`",
        f"- effective_action: `{payload.get('effective_action')}`",
        f"- actionable_by_codex: `{payload.get('actionable_by_codex')}`",
        f"- state: `{payload.get('state')}`",
        f"- waiting_on: `{payload.get('waiting_on')}`",
        f"- status: `{payload.get('status')}`",
    ]
    if payload.get("project_asset_source"):
        lines.append(f"- project_asset_source: {payload.get('project_asset_source')}")
    agent_identity = as_dict(payload.get("agent_identity"))
    if agent_identity:
        lines.append(
            "- agent_identity: "
            f"agent_id={agent_identity.get('agent_id')} agent_model=peer_v1"
        )
    if payload.get("active_state_next_action"):
        lines.append(f"- active_state_next_action: {markdown_scalar(payload.get('active_state_next_action'))}")
    if payload.get("latest_run_recommended_action"):
        lines.append(
            f"- latest_run_recommended_action: {markdown_scalar(payload.get('latest_run_recommended_action'))}"
        )
    agent_lane_next_action = as_dict(payload.get("agent_lane_next_action"))
    if agent_lane_next_action:
        lines.append(
            "- agent_lane_next_action: "
            f"todo_id={agent_lane_next_action.get('todo_id')} "
            f"selected_by={agent_lane_next_action.get('selected_by')} "
            f"confidence={agent_lane_next_action.get('confidence')}"
        )
        if agent_lane_next_action.get("text"):
            lines.append(
                f"- agent_lane_next_action_text: {markdown_scalar(agent_lane_next_action.get('text'))}"
            )
    agent_lane_frontier_hint = as_dict(payload.get("agent_lane_frontier_hint"))
    if agent_lane_frontier_hint:
        lines.append(
            "- agent_lane_frontier_hint: "
            f"decision={agent_lane_frontier_hint.get('decision')} "
            f"source={agent_lane_frontier_hint.get('source')} "
            f"reason_code={agent_lane_frontier_hint.get('reason_code')} "
            f"target_todo_id={agent_lane_frontier_hint.get('target_todo_id')}"
        )
    goal_route_hint = as_dict(payload.get("goal_route_hint"))
    if goal_route_hint:
        counts = as_dict(goal_route_hint.get("counts"))
        lines.append(
            "- goal_route_hint: "
            f"decision={goal_route_hint.get('route_decision')} "
            f"agent_id={goal_route_hint.get('agent_id')} "
            f"preserves_goal_next_action={goal_route_hint.get('preserves_goal_next_action')} "
            f"other_agent_claimed_advancement_count={counts.get('other_agent_claimed_advancement_count')}"
        )
        current_action = as_dict(goal_route_hint.get("current_agent_next_action"))
        if current_action:
            lines.append(
                "- goal_route_hint_current_action: "
                f"todo_id={current_action.get('todo_id')} "
                f"selected_by={current_action.get('selected_by')}"
            )
    agent_scope_frontier = as_dict(payload.get("agent_scope_frontier"))
    if agent_scope_frontier:
        lines.append(
            "- agent_scope_frontier: "
            f"action={agent_scope_frontier.get('action')} "
            f"agent_id={agent_scope_frontier.get('agent_id')} "
            f"quiet_noop_allowed={agent_scope_frontier.get('quiet_noop_allowed')}"
        )
        if agent_scope_frontier.get("reason"):
            lines.append(
                f"- agent_scope_frontier_reason: {markdown_scalar(agent_scope_frontier.get('reason'))}"
            )
        deferred_resume_candidates = as_list(agent_scope_frontier.get("deferred_resume_candidates"))
        if deferred_resume_candidates:
            first_candidate = as_dict(deferred_resume_candidates[0])
            lines.append(
                "- agent_scope_deferred_resume_candidates: "
                f"`{len(deferred_resume_candidates)}` "
                f"top_todo_id={first_candidate.get('todo_id')}"
            )
        route_continuation_candidates = as_list(
            agent_scope_frontier.get("route_continuation_replan_candidates")
        )
        if route_continuation_candidates:
            first_candidate = as_dict(route_continuation_candidates[0])
            lines.append(
                "- agent_scope_route_continuation_replan_candidates: "
                f"`{len(route_continuation_candidates)}` "
                f"top_todo_id={first_candidate.get('todo_id')} "
                f"route={first_candidate.get('route_id') or first_candidate.get('route_key') or ''}"
            )
    goal_frontier = as_dict(payload.get("goal_frontier_projection"))
    if goal_frontier:
        remaining = as_dict(goal_frontier.get("remaining_advancement_frontier"))
        deferred_successors = as_dict(goal_frontier.get("deferred_successors"))
        acceptance_gaps = as_list(goal_frontier.get("acceptance_gaps"))
        lines.append(
            "- goal_frontier_projection: "
            f"replan_required={goal_frontier.get('replan_required')} "
            f"current_agent_advancement={remaining.get('current_agent_claimed_advancement_count')} "
            f"unclaimed_advancement={remaining.get('unclaimed_advancement_count')} "
            f"other_agent_advancement={remaining.get('other_agent_claimed_advancement_count')} "
            f"deferred_ready={deferred_successors.get('ready_count')} "
            f"acceptance_gaps={len(acceptance_gaps)}"
        )
        vision_audit = as_dict(goal_frontier.get("vision_continuation_audit"))
        if vision_audit:
            lines.append(
                "- vision_continuation_audit: "
                f"required={vision_audit.get('required')} "
                f"decision={vision_audit.get('decision')} "
                f"trigger_count={vision_audit.get('trigger_count')}"
            )
            vision_gap_judge = as_dict(vision_audit.get("vision_gap_judge"))
            if vision_gap_judge:
                lines.append(
                    "- vision_gap_judge: "
                    f"done={vision_gap_judge.get('done')} "
                    f"decision={vision_gap_judge.get('decision')}"
                )
        vision_wait_state = as_dict(goal_frontier.get("vision_wait_state"))
        if vision_wait_state:
            if vision_wait_state.get("reason_code") == "current_agent_blocker":
                lines.append(
                    "- vision_wait_state: "
                    f"state={vision_wait_state.get('state')} "
                    f"todo_id={vision_wait_state.get('selected_todo_id')} "
                    f"reason={vision_wait_state.get('blocker_reason')} "
                    "automatic_resume=False"
                )
            else:
                lines.append(
                    "- vision_wait_state: "
                    f"state={vision_wait_state.get('state')} "
                    f"todo_id={vision_wait_state.get('selected_todo_id')} "
                    f"resume_when={vision_wait_state.get('resume_when')} "
                    f"automatic_resume={vision_wait_state.get('automatic_resume')}"
                )
    orchestration = as_dict(payload.get("task_orchestration_contract"))
    for label, task_orchestration in (
        ("task_orchestration", orchestration),
        ("peer_activation_diagnostic", as_dict(orchestration.get("peer_activation_diagnostic"))),
    ):
        if not task_orchestration:
            continue
        lanes = as_list(task_orchestration.get("eligible_child_lanes"))
        if not lanes:
            lanes = as_list(task_orchestration.get("eligible_peer_lanes"))
        blocked_lanes = as_list(task_orchestration.get("blocked_lanes"))
        if not blocked_lanes:
            blocked_lanes = as_list(task_orchestration.get("blocked_peer_lanes"))
        lines.append(
            f"- {label}: "
            f"mode={task_orchestration.get('mode')} "
            f"activation_required={task_orchestration.get('activation_required')} "
            f"lanes={task_orchestration.get('eligible_peer_count', len(lanes))} "
            f"blocked_lanes={task_orchestration.get('blocked_peer_count', len(blocked_lanes))} "
            f"writeback_owner={task_orchestration.get('writeback_owner')}"
        )
        fields = " ".join(
            f"{key}={markdown_scalar(task_orchestration[key])}"
            for key in ("execution_scope", "execution_state", "task_selection", "activation_allowed")
            if key in task_orchestration
        )
        if fields:
            lines.append(f"- {label}_admission: {fields}")
        reasons = sorted({str(reason) for row in blocked_lanes if isinstance(row, dict)
                          for reason in as_list(row.get("reason_codes"))})
        if reasons:
            lines.append(f"- {label}_blockers: {', '.join(reasons)}")
        if task_orchestration.get("read_required") is True:
            lines.append(f"- {label}_required_detail: {task_orchestration.get('detail_ref')}")
    replan_decision = as_dict(payload.get("autonomous_replan_decision"))
    if replan_decision:
        lines.append(
            "- autonomous_replan_decision: "
            f"decision={replan_decision.get('decision')} "
            f"plane={replan_decision.get('decision_plane')}"
        )
    automation_prompt_upgrade = as_dict(payload.get("automation_prompt_upgrade"))
    if automation_prompt_upgrade:
        lines.append(
            "- automation_prompt_upgrade: "
            f"required={automation_prompt_upgrade.get('required')} "
            f"blocks_should_run={automation_prompt_upgrade.get('blocks_should_run')} "
            f"contract={automation_prompt_upgrade.get('contract')} "
            f"migration_id={automation_prompt_upgrade.get('migration_id')}"
        )
        if automation_prompt_upgrade.get("recommended_action"):
            lines.append(f"- automation_prompt_upgrade_action: {automation_prompt_upgrade.get('recommended_action')}")
        for example in as_list(automation_prompt_upgrade.get("agent_example_commands")):
            if isinstance(example, dict) and example.get("command"):
                lines.append(
                    f"- automation_prompt_upgrade_agent[{example.get('agent_id')}]: "
                    f"{example.get('command')}"
                )
        if automation_prompt_upgrade.get("completion_command"):
            lines.append(
                "- automation_prompt_upgrade_complete: "
                f"{automation_prompt_upgrade.get('completion_command')}"
            )
    capability_gate = as_dict(payload.get("capability_gate"))
    if capability_gate:
        lines.append(
            "- capability_gate: "
            f"action={capability_gate.get('action')} "
            f"required={capability_gate.get('required')} "
            f"missing={capability_gate.get('missing')}"
        )
        if capability_gate.get("decision_owner"):
            lines.append(f"- capability_gate_decision_owner: {capability_gate.get('decision_owner')}")
        runnable_candidates = as_list(capability_gate.get("runnable_candidates"))
        if runnable_candidates:
            lines.append(f"- capability_gate_runnable_candidates: `{len(runnable_candidates)}`")
        blocked_candidates = as_list(capability_gate.get("blocked_candidates"))
        if blocked_candidates:
            lines.append(f"- capability_gate_blocked_candidates: `{len(blocked_candidates)}`")
    workspace_guard = as_dict(payload.get("workspace_guard"))
    if workspace_guard:
        lines.append(
            "- workspace_guard: "
            f"action={workspace_guard.get('action')} "
            f"current_workspace={workspace_guard.get('current_workspace')} "
            f"required_workspace={workspace_guard.get('required_workspace')}"
        )
        if workspace_guard.get("required_action"):
            lines.append(f"- workspace_guard_action: {workspace_guard.get('required_action')}")
    stale_latest_run_warning = as_dict(payload.get("stale_latest_run_warning"))
    if stale_latest_run_warning:
        lines.append(
            "- stale_latest_run_warning: "
            f"requires_refresh_state={stale_latest_run_warning.get('requires_refresh_state')} "
            f"active_state_updated_at={stale_latest_run_warning.get('active_state_updated_at')} "
            f"latest_run_generated_at={stale_latest_run_warning.get('latest_run_generated_at')} "
            f"reason={stale_latest_run_warning.get('reason')}"
        )
        if stale_latest_run_warning.get("recommended_action"):
            lines.append(f"- stale_latest_run_action: {stale_latest_run_warning.get('recommended_action')}")
    state_action_projection_warning = as_dict(payload.get("state_action_projection_warning"))
    if state_action_projection_warning:
        lines.append(
            "- state_action_projection_warning: "
            f"requires_state_writeback={state_action_projection_warning.get('requires_state_writeback')} "
            f"reason={state_action_projection_warning.get('reason')}"
        )
        if state_action_projection_warning.get("selected_recommended_action"):
            lines.append(
                "- state_action_selected: "
                f"{state_action_projection_warning.get('selected_recommended_action')}"
            )
        if state_action_projection_warning.get("active_state_next_action"):
            lines.append(
                "- state_action_visible_next: "
                f"{state_action_projection_warning.get('active_state_next_action')}"
            )
        if state_action_projection_warning.get("recommended_action"):
            lines.append(
                f"- state_action_projection_action: {state_action_projection_warning.get('recommended_action')}"
            )
    next_action_projection = as_dict(payload.get("next_action_projection_warning"))
    if next_action_projection:
        lines.append(
            "- next_action_projection_warning: "
            f"requires_state_writeback={next_action_projection.get('requires_state_writeback')} "
            f"reason={next_action_projection.get('reason')}"
        )
        if next_action_projection.get("latest_run_recommended_action"):
            lines.append(
                "- latest_run_projection_action: "
                f"{next_action_projection.get('latest_run_recommended_action')}"
            )
    backlog_hygiene_warning = as_dict(payload.get("backlog_hygiene_warning"))
    if backlog_hygiene_warning:
        lines.append(
            "- backlog_hygiene_warning: "
            f"requires_agent_todo={backlog_hygiene_warning.get('requires_agent_todo')} "
            f"evidence_count={backlog_hygiene_warning.get('evidence_count')} "
            f"source_sections={','.join(backlog_hygiene_warning.get('source_sections') or [])}"
        )
        if backlog_hygiene_warning.get("recommended_action"):
            lines.append(f"- backlog_hygiene_action: {backlog_hygiene_warning.get('recommended_action')}")
    projection_gap = as_dict(payload.get("state_projection_gap"))
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
    _append_fallback_projection_markdown(
        lines,
        as_dict(payload.get("blocked_priority_fallback")),
        label="blocked_priority",
        blocked_items_key="blocked_items",
        blocked_item_label="blocked_priority_item",
    )
    _append_fallback_projection_markdown(
        lines,
        as_dict(payload.get("scoped_user_gate_fallback")),
        label="scoped_user_gate",
        blocked_items_key="blocked_agent_items",
        blocked_item_label="scoped_user_gate_blocked_item",
        blocked_gate_key="blocked_user_gate",
    )
    completed_todo_archive_warning = as_dict(payload.get("completed_todo_archive_warning"))
    if completed_todo_archive_warning:
        lines.append(
            "- completed_todo_archive_warning: "
            f"requires_archive={completed_todo_archive_warning.get('requires_archive')} "
            f"active_done={completed_todo_archive_warning.get('active_done_count')} "
            f"max_active_done={completed_todo_archive_warning.get('max_active_done_count')} "
            f"default_archive_keep={completed_todo_archive_warning.get('default_archive_keep_count')} "
            f"archive_section={completed_todo_archive_warning.get('archive_section')} "
            f"archive_command={completed_todo_archive_warning.get('archive_command_template')}"
        )
        if completed_todo_archive_warning.get("recommended_action"):
            lines.append(
                f"- completed_todo_archive_action: {completed_todo_archive_warning.get('recommended_action')}"
            )
    replan_obligation = as_dict(payload.get("autonomous_replan_obligation"))
    if replan_obligation:
        trigger_kinds = [
            str(trigger.get("kind") or "")
            for trigger in replan_obligation.get("triggers") or []
            if isinstance(trigger, dict) and trigger.get("kind")
        ]
        lines.append(
            "- autonomous_replan_obligation: "
            f"required={replan_obligation.get('required')} "
            f"trigger_count={replan_obligation.get('trigger_count')} "
            f"triggers={','.join(trigger_kinds)}"
        )
        for trigger in as_list(replan_obligation.get("triggers")):
            if not isinstance(trigger, dict) or not trigger.get("reason_code"):
                continue
            lines.append(f"  - reason_code: {trigger['reason_code']}")
            checks = as_dict(trigger.get("component_checks"))
            lines.append("  - component_checks: " + ", ".join(
                f"{key}={'pass' if passed else 'fail'}" for key, passed in checks.items()
            ))
            lines.append(f"  - resolution: {trigger.get('resolution_hint')}")
    required_reads = as_list(payload.get("required_reads"))
    for read in required_reads[:3]:
        if not isinstance(read, dict):
            continue
        command = str(read.get("command") or "").strip()
        if command:
            lines.append(
                "- required_read: "
                f"kind={read.get('kind')} "
                f"agent_id={read.get('agent_id')} "
                f"todo_id={read.get('todo_id') or ''} "
                f"command=`{command}`"
            )
    work_lane_contract = as_dict(payload.get("work_lane_contract"))
    if work_lane_contract:
        lines.append(
            "- work_lane_contract: "
            f"lane={work_lane_contract.get('lane')} "
            f"next={work_lane_contract.get('next_lane')} "
            f"obligation={work_lane_contract.get('obligation')} "
            f"must_attempt_work={work_lane_contract.get('must_attempt_work')}"
        )
        reason_codes = as_list(work_lane_contract.get("reason_codes"))
        if reason_codes:
            lines.append(f"- work_lane_reason_codes: {','.join(str(code) for code in reason_codes)}")
        if work_lane_contract.get("monitor_policy"):
            lines.append(f"- work_lane_monitor_policy: {work_lane_contract.get('monitor_policy')}")
        if work_lane_contract.get("monitor_due_count"):
            lines.append(
                "- work_lane_monitor_due: "
                f"count={work_lane_contract.get('monitor_due_count')} "
                f"selected={work_lane_contract.get('selected_todo_id') or ''} "
                f"next_due_at={work_lane_contract.get('selected_next_due_at') or ''}"
            )
        if work_lane_contract.get("lane") == "lark_event_inbox":
            lines.append(
                "- work_lane_lark_inbox: "
                f"pending={work_lane_contract.get('pending_count')} "
                f"questions={work_lane_contract.get('direct_question_count')} "
                f"mentions={work_lane_contract.get('direct_mention_count')} "
                f"bot_replies={work_lane_contract.get('reply_to_bot_count')} "
                f"oldest_age_seconds={work_lane_contract.get('oldest_pending_age_seconds')}"
            )
        if work_lane_contract.get("action"):
            lines.append(f"- work_lane_action: {work_lane_contract.get('action')}")
        outcome_followthrough = as_dict(work_lane_contract.get("outcome_followthrough"))
        if outcome_followthrough:
            lines.append(
                "- work_lane_outcome_followthrough: "
                f"latest_outcome={outcome_followthrough.get('latest_delivery_outcome')} "
                f"latest_kind={outcome_followthrough.get('latest_delivery_turn_kind')} "
                f"obligation={outcome_followthrough.get('obligation')}"
            )
    interface_budget_cadence = as_dict(payload.get("interface_budget_cadence"))
    if interface_budget_cadence:
        lines.append(
            "- interface_budget_cadence: "
            f"overdue={interface_budget_cadence.get('overdue')} "
            f"within_budget={interface_budget_cadence.get('within_budget')} "
            f"checked_at={interface_budget_cadence.get('checked_at')} "
            f"next_check_due_at={interface_budget_cadence.get('next_check_due_at')} "
            f"tightest={interface_budget_cadence.get('tightest_surface')}/"
            f"{interface_budget_cadence.get('tightest_metric')} "
            f"headroom={interface_budget_cadence.get('headroom_remaining')} "
            f"recommendation={interface_budget_cadence.get('recommendation')}"
        )
    execution_profile = as_dict(payload.get("execution_profile"))
    if execution_profile:
        lines.append(f"- execution_profile: {execution_profile_summary(execution_profile)}")
    long_task_cadence_hint = as_dict(payload.get("long_task_cadence_hint"))
    if long_task_cadence_hint:
        lines.append(
            f"- long_task_cadence_hint: {long_task_cadence_hint_summary(long_task_cadence_hint)}"
        )
    if control_plane := as_dict(payload.get("control_plane")):
        lines.append(f"- control_plane: {control_plane_policy_summary(control_plane)}")

    if quota:
        lines.append(
            "- quota: "
            f"compute={quota.get('compute')} "
            f"slot_minutes={quota.get('slot_minutes')} "
            f"slots={quota.get('spent_slots')}/{quota.get('allowed_slots')}"
        )
    if payload.get("reason"):
        lines.append(f"- reason: {payload.get('reason')}")
    handoff_readiness = as_dict(payload.get("handoff_readiness"))
    if handoff_readiness:
        lines.append(
            "- handoff_readiness: "
            f"ready={handoff_readiness.get('ready')} "
            f"codex_ready={handoff_readiness.get('codex_ready')} "
            f"source={handoff_readiness.get('source')} "
            f"quota_state={handoff_readiness.get('quota_state')}"
        )
        interface_budget = as_dict(handoff_readiness.get("handoff_interface_budget"))
        if interface_budget:
            lines.append(
                "- handoff_interface_budget: "
                f"mode={interface_budget.get('mode')} "
                f"max_lines={interface_budget.get('max_lines')} "
                f"max_chars={interface_budget.get('max_chars')}"
            )
        lines.append(
            "- handoff_state: "
            f"status={handoff_readiness.get('handoff_status')} "
            f"post_handoff_run_seen={handoff_readiness.get('post_handoff_run_seen')} "
            f"ready_at={handoff_readiness.get('handoff_ready_at') or ''}"
        )
        latest_handoff_run = as_dict(handoff_readiness.get("post_handoff_latest_run"))
        if latest_handoff_run:
            outcome_suffix = ""
            if latest_handoff_run.get("delivery_outcome"):
                outcome_suffix = f" outcome={latest_handoff_run.get('delivery_outcome')}"
            lines.append(
                "- post_handoff_run: "
                f"classification={latest_handoff_run.get('classification')} "
                f"at={latest_handoff_run.get('generated_at')} "
                f"scale={latest_handoff_run.get('delivery_batch_scale') or ''}"
                f"{outcome_suffix}"
            )
        recent_handoff_runs = as_list(handoff_readiness.get("post_handoff_recent_runs"))
        recent_scales = [
            str(run.get("delivery_batch_scale") or "")
            for run in recent_handoff_runs
            if isinstance(run, dict)
        ]
        if recent_scales:
            recent_outcomes = [
                str(run.get("delivery_outcome") or "")
                for run in recent_handoff_runs
                if isinstance(run, dict) and run.get("delivery_outcome")
            ]
            outcome_text = f" outcome={','.join(recent_outcomes)}" if recent_outcomes else ""
            gap_text = (
                f" outcome_gap_streak={handoff_readiness.get('post_handoff_outcome_gap_streak')}"
                if "post_handoff_outcome_gap_streak" in handoff_readiness
                else ""
            )
            lines.append(
                "- post_handoff_recent_scales: "
                f"{','.join(recent_scales)} "
                f"small_streak={handoff_readiness.get('post_handoff_small_scale_streak', 0)}"
                f"{outcome_text}"
                f"{gap_text}"
            )
    heartbeat_recommendation = as_dict(payload.get("heartbeat_recommendation"))
    if heartbeat_recommendation:
        lines.append(
            "- heartbeat_recommendation: "
            f"mode={heartbeat_recommendation.get('recommended_mode')} "
            f"notify={heartbeat_recommendation.get('notify')}"
        )
        if heartbeat_recommendation.get("command"):
            lines.append(f"- heartbeat_command: `{heartbeat_recommendation.get('command')}`")
        if heartbeat_recommendation.get("stop_if_unchanged"):
            lines.append("- heartbeat_stop_if_unchanged: `True`")
        if heartbeat_recommendation.get("repeat_notification_required"):
            lines.append("- heartbeat_repeat_notification_required: `True`")
        if heartbeat_recommendation.get("spend_policy"):
            lines.append(f"- heartbeat_spend_policy: {heartbeat_recommendation.get('spend_policy')}")
        if heartbeat_recommendation.get("reason"):
            lines.append(f"- heartbeat_reason: {heartbeat_recommendation.get('reason')}")
    execution_obligation = as_dict(payload.get("execution_obligation"))
    if execution_obligation:
        lines.append(
            "- execution_obligation: "
            f"must_attempt_work={execution_obligation.get('must_attempt_work')} "
            f"kind={execution_obligation.get('kind')} "
            f"notify_is_execution_gate={execution_obligation.get('notify_is_execution_gate')}"
        )
        if execution_obligation.get("contract_obligation"):
            lines.append(f"- execution_contract_obligation: {execution_obligation.get('contract_obligation')}")
        if execution_obligation.get("reason"):
            lines.append(f"- execution_obligation_reason: {execution_obligation.get('reason')}")
    interaction_contract = as_dict(payload.get("interaction_contract"))
    if interaction_contract:
        user_channel = as_dict(interaction_contract.get("user_channel"))
        agent_channel = as_dict(interaction_contract.get("agent_channel"))
        cli_channel = as_dict(interaction_contract.get("cli_channel"))
        lines.append(
            "- interaction_contract: "
            f"schema={interaction_contract.get('schema_version')} "
            f"mode={interaction_contract.get('mode')} "
            f"user_required={user_channel.get('action_required')} "
            f"agent_must_attempt={agent_channel.get('must_attempt')} "
            f"quiet_noop_allowed={agent_channel.get('quiet_noop_allowed')} "
            f"spend_after_validation={cli_channel.get('spend_after_validation')}"
        )
        if agent_channel.get("primary_action"):
            lines.append(f"- interaction_agent_action: {agent_channel.get('primary_action')}")
        if resolution_trace := as_dict(agent_channel.get("resolution_trace")):
            lines.append(
                f"- interaction_agent_resolution: {resolution_trace.get('summary')}"
            )
    automation_liveness = as_dict(payload.get("automation_liveness"))
    if automation_liveness:
        lines.append(
            "- automation_liveness: "
            f"action={automation_liveness.get('automation_action')} "
            f"keep_active={automation_liveness.get('keep_active')} "
            f"pause_allowed={automation_liveness.get('pause_allowed')}"
        )
        if automation_liveness.get("reason"):
            lines.append(f"- automation_liveness_reason: {automation_liveness.get('reason')}")
        if automation_liveness.get("pause_policy"):
            lines.append(f"- automation_pause_policy: {automation_liveness.get('pause_policy')}")
    scheduler_hint = as_dict(payload.get("scheduler_hint"))
    if scheduler_hint:
        app_automation = as_dict(
            scheduler_hint.get("app_automation") or scheduler_hint.get("codex_app")
        )
        unchanged_poll = as_dict(scheduler_hint.get("unchanged_poll"))
        limits = as_dict(unchanged_poll.get("limits"))
        codex_cli_tui = as_dict(scheduler_hint.get("codex_cli_tui"))
        claude_code_loop = as_dict(scheduler_hint.get("claude_code_loop"))
        cli_unchanged_limit = (
            limits.get("codex_cli_tui")
            if "codex_cli_tui" in limits
            else codex_cli_tui.get("unchanged_poll_limit")
        )
        claude_unchanged_limit = (
            limits.get("claude_code_loop")
            if "claude_code_loop" in limits
            else claude_code_loop.get("unchanged_poll_limit")
        )
        lines.append(
            "- scheduler_hint: "
            f"action={scheduler_hint.get('action')} "
            f"cadence={scheduler_hint.get('cadence_class')} "
            f"app_automation_minutes={app_automation.get('recommended_interval_minutes')} "
            f"app_automation_rrule={app_automation.get('recommended_rrule')} "
            f"app_automation_apply_needed={(app_automation.get('stateful_backoff') or {}).get('apply_needed') if isinstance(app_automation.get('stateful_backoff'), dict) else None} "
            f"app_automation_progression={app_automation.get('example_progression_minutes')} "
            f"cli_unchanged_limit={cli_unchanged_limit} "
            f"claude_unchanged_limit={claude_unchanged_limit}"
        )
        if scheduler_hint.get("reason"):
            lines.append(f"- scheduler_hint_reason: {scheduler_hint.get('reason')}")
        if reset_policy := as_dict(scheduler_hint.get("reset_policy")):
            lines.append(
                "- scheduler_reset: "
                f"initial_interval={reset_policy.get('app_automation_initial_interval_minutes')} "
                f"initial_rrule={reset_policy.get('app_automation_initial_rrule')} "
                f"reset_generation={reset_policy.get('reset_token')} "
                f"identity_signature={reset_policy.get('identity_signature')}"
            )
    protocol_action_packet = as_dict(payload.get("protocol_action_packet"))
    if protocol_action_packet:
        lines.append(
            "- protocol_action_packet: "
            f"schema={protocol_action_packet.get('schema_version')} "
            f"{protocol_action_packet.get('summary') or ''}"
        )
    stall_self_repair = as_dict(payload.get("stall_self_repair"))
    if stall_self_repair:
        lines.append(
            "- stall_self_repair: "
            f"trigger={stall_self_repair.get('trigger')} "
            f"mode={stall_self_repair.get('recommended_mode')} "
            f"action={stall_self_repair.get('effective_action')}"
        )
        if stall_self_repair.get("repair_focus"):
            lines.append(f"- stall_repair_focus: {stall_self_repair.get('repair_focus')}")
        if stall_self_repair.get("missing_write_scopes"):
            lines.append(
                "- boundary_missing_write_scopes: "
                f"{', '.join(str(value) for value in stall_self_repair.get('missing_write_scopes') or [])}"
            )
        blockers = as_list(stall_self_repair.get("blocking_health_items"))
        for blocker in blockers[:3]:
            if not isinstance(blocker, dict):
                continue
            lines.append(
                "- stall_health_blocker: "
                f"goal={blocker.get('goal_id')} "
                f"status={blocker.get('status')} "
                f"waiting_on={blocker.get('waiting_on')} "
                f"action={blocker.get('recommended_action')}"
            )
    if payload.get("safe_bypass_allowed"):
        lines.append(f"- safe_bypass_allowed: `{payload.get('safe_bypass_allowed')}`")
    if payload.get("safe_bypass_kind"):
        lines.append(f"- safe_bypass_kind: {payload.get('safe_bypass_kind')}")
    if payload.get("blocked_action_scope"):
        lines.append(f"- blocked_action_scope: `{payload.get('blocked_action_scope')}`")
    if payload.get("safe_bypass_policy"):
        lines.append(f"- safe_bypass_policy: {payload.get('safe_bypass_policy')}")
    decision_freshness_warning = as_dict(payload.get("decision_freshness_warning"))
    if decision_freshness_warning:
        lines.append(
            "- decision_freshness_warning: "
            f"rebase_required={decision_freshness_warning.get('rebase_required_count')} "
            f"window_days={decision_freshness_warning.get('window_days')} "
            f"source={decision_freshness_warning.get('source')}"
        )
        if decision_freshness_warning.get("message"):
            lines.append(f"- decision_freshness_action: {decision_freshness_warning.get('message')}")
        freshness_items = as_list(decision_freshness_warning.get("items"))
        for item in freshness_items[:DECISION_FRESHNESS_WARNING_ITEM_LIMIT]:
            if not isinstance(item, dict):
                continue
            lines.append(
                "- decision_freshness_item: "
                f"kind={item.get('decision_kind')} "
                f"state={item.get('freshness_state')} "
                f"age_days={item.get('age_days')} "
                f"newer_7d={item.get('newer_event_count_7d')} "
                f"at={item.get('decision_at')}"
            )
    promotion_readiness_warning = as_dict(payload.get("promotion_readiness_warning"))
    if promotion_readiness_warning:
        lines.append(
            "- promotion_readiness_warning: "
            f"status={promotion_readiness_warning.get('freshness_status')} "
            f"requires_readiness_run={promotion_readiness_warning.get('requires_readiness_run')} "
            f"window_hours={promotion_readiness_warning.get('freshness_window_hours')} "
            f"source={promotion_readiness_warning.get('source')}"
        )
        if promotion_readiness_warning.get("message"):
            lines.append(f"- promotion_readiness_action: {promotion_readiness_warning.get('message')}")
        lines.append(
            "- promotion_readiness_evidence: "
            f"goal={promotion_readiness_warning.get('goal_id') or ''} "
            f"generated_at={promotion_readiness_warning.get('generated_at') or ''} "
            f"age_hours={promotion_readiness_warning.get('age_hours')} "
            f"artifacts={promotion_readiness_warning.get('json_exists')}/{promotion_readiness_warning.get('markdown_exists')}"
        )
        if promotion_readiness_warning.get("reason"):
            lines.append(f"- promotion_readiness_reason: {promotion_readiness_warning.get('reason')}")
    reward_lesson_warning = as_dict(payload.get("reward_lesson_projection_warning"))
    if reward_lesson_warning:
        lines.append(
            "- reward_lesson_projection_warning: "
            f"matches={reward_lesson_warning.get('match_count')} "
            f"source={reward_lesson_warning.get('source')}"
        )
        if reward_lesson_warning.get("message"):
            lines.append(f"- reward_lesson_action: {reward_lesson_warning.get('message')}")
        for match in (reward_lesson_warning.get("matches") or [])[:3]:
            if not isinstance(match, dict):
                continue
            lines.append(
                "- reward_lesson_match: "
                f"kind={match.get('kind')} "
                f"decision={match.get('decision')} "
                f"avoid={match.get('avoid')} "
                f"summary={match.get('summary')}"
            )
    goal_boundary = as_dict(payload.get("goal_boundary"))
    if goal_boundary:
        adapter = as_dict(goal_boundary.get("adapter"))
        if adapter:
            lines.append(
                "- goal_boundary_adapter: "
                f"{adapter.get('kind') or ''}:{adapter.get('status') or ''}"
            )
        write_scope = as_list(goal_boundary.get("write_scope"))
        if write_scope:
            lines.append(f"- goal_boundary_write_scope: {', '.join(str(value) for value in write_scope)}")
        approvals = as_list(goal_boundary.get("requires_parent_approval"))
        if approvals:
            lines.append(f"- goal_boundary_requires_approval: {', '.join(str(value) for value in approvals)}")
        guards = as_list(goal_boundary.get("guards"))
        for guard in guards[:5]:
            lines.append(f"- goal_boundary_guard: {guard}")
        orchestration = (
            goal_boundary.get("orchestration")
            if isinstance(goal_boundary.get("orchestration"), dict)
            else None
        )
        if orchestration:
            lines.append(f"- goal_boundary_orchestration: {orchestration_policy_summary(orchestration)}")
        capabilities = as_dict(goal_boundary.get("capabilities"))
        lark_inbox = as_dict(capabilities.get("lark_event_inbox"))
        urgency = as_dict(lark_inbox.get("urgency"))
        if urgency:
            lines.append(
                "- goal_boundary_lark_inbox_urgency: "
                f"pending={urgency.get('pending_count')} "
                f"questions={urgency.get('direct_question_count')} "
                f"mentions={urgency.get('direct_mention_count')} "
                f"bot_replies={urgency.get('reply_to_bot_count')} "
                f"reply_due={urgency.get('reply_due')} "
                f"material_review={urgency.get('material_review_count')} "
                f"material_review_due={urgency.get('material_review_due')} "
                f"oldest_age_seconds={urgency.get('oldest_pending_age_seconds')}"
            )
        run_permission_policy = (
            goal_boundary.get("run_permission_policy")
            if isinstance(goal_boundary.get("run_permission_policy"), dict)
            else None
        )
        if run_permission_policy:
            lines.append(
                "- goal_boundary_run_permission_policy: "
                f"valid={run_permission_policy.get('valid')} "
                f"delivery_allowed={run_permission_policy.get('delivery_allowed')} "
                f"no_upload={run_permission_policy.get('no_upload_required')} "
                f"compact_only={run_permission_policy.get('compact_observation_only')} "
                f"max_minutes={run_permission_policy.get('max_wall_time_minutes')}"
            )
        if goal_boundary.get("stop_condition"):
            lines.append(f"- goal_boundary_stop_condition: {goal_boundary.get('stop_condition')}")
    if payload.get("operator_question"):
        lines.append(f"- operator_question: {payload.get('operator_question')}")
    if payload.get("notify_user_on_gate"):
        lines.append(f"- notify_user_on_gate: `{payload.get('notify_user_on_gate')}`")
    if payload.get("notify_user_on_open_todo"):
        lines.append(f"- notify_user_on_open_todo: `{payload.get('notify_user_on_open_todo')}`")
    if payload.get("open_todo_notify_reason"):
        lines.append(f"- open_todo_notify_reason: {payload.get('open_todo_notify_reason')}")
    if payload.get("open_todo_notification_policy"):
        lines.append(f"- open_todo_notification_policy: {payload.get('open_todo_notification_policy')}")
    if payload.get("gate_prompt"):
        lines.extend(["", "## Gate Prompt", str(payload.get("gate_prompt"))])
    user_todo_summary = as_dict(payload.get("user_todo_summary"))
    if user_todo_summary:
        _append_todo_summary(lines, "user_todo", user_todo_summary)
    agent_todo_summary = as_dict(payload.get("agent_todo_summary"))
    selected_todo = as_dict(payload.get("selected_todo"))
    selected_todo_id = str(selected_todo.get("todo_id") or "").strip()
    lane_todo_id = str(agent_lane_next_action.get("todo_id") or "").strip()
    if selected_todo and (not selected_todo_id or selected_todo_id != lane_todo_id):
        lines.append(f"- selected_todo: {_compact_todo_identity(selected_todo)}")
    if agent_todo_summary:
        _append_todo_summary(lines, "agent_todo", agent_todo_summary)
    todo_write_hint = as_dict(payload.get("todo_write_hint"))
    if todo_write_hint:
        lines.append(f"- todo_write_hint: {todo_write_hint.get('rule')}")
        lines.append(f"- user_gate_command_template: `{todo_write_hint.get('user_gate_command_template')}`")
        lines.append(f"- user_action_command_template: `{todo_write_hint.get('user_action_command_template')}`")
        lines.append(f"- agent_todo_command_template: `{todo_write_hint.get('agent_todo_command_template')}`")
    if payload.get("recommended_action"):
        lines.append(f"- recommended_action: {payload.get('recommended_action')}")
    if payload.get("agent_command"):
        lines.append(f"- agent_command: `{payload.get('agent_command')}`")
    if payload.get("next_handoff_condition"):
        lines.append(f"- next_handoff_condition: {payload.get('next_handoff_condition')}")
    summary = as_dict(payload.get("plan_summary"))
    states = as_dict(summary.get("states"))
    if summary:
        state_text = ", ".join(f"{state}={states.get(state, 0)}" for state in QUOTA_STATE_ORDER)
        lines.append(
            "- plan_summary: "
            f"next_automatic_turn={summary.get('next_automatic_turn') or 'none'} "
            f"{state_text}"
        )
    append_operator_action_markdown(lines, payload)
    return "\n".join(lines)
