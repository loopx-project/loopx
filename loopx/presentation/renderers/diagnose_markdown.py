"""Pure Markdown renderer for ``loopx diagnose`` evidence packets."""

from __future__ import annotations

from collections.abc import Mapping

from ...control_plane.status.diagnose_projection import _as_items, _as_mapping
from ..markdown import markdown_scalar

USER_DIAGNOSE_PROMPT = (
    "请诊断这个项目的 LoopX 运转情况。不要让我手动跑 shell 命令；如果 "
    "`loopx` 不在 PATH，请先安装/修复。然后运行 `loopx diagnose` "
    "读取诊断证据包，结合项目上下文自己判断：它是否可以自主推进、当前卡在哪、"
    "需要我回答什么、以及你下一步会做什么。"
)


def _goal_frontier_projection_line(goal_frontier: Mapping[str, object]) -> str | None:
    """Format compact goal-frontier summary line when projection data is present."""
    if not goal_frontier:
        return None
    normalized_progress = _as_mapping(goal_frontier.get("normalized_progress"))
    remaining = _as_mapping(goal_frontier.get("remaining_advancement_frontier"))
    deferred_successors = _as_mapping(goal_frontier.get("deferred_successors"))
    monitor_only_lanes = _as_mapping(goal_frontier.get("monitor_only_lanes"))
    acceptance_gaps = _as_items(goal_frontier.get("acceptance_gaps"))
    autonomy_blockers = _as_items(goal_frontier.get("autonomy_blockers"))
    return (
        "- goal_frontier_projection: "
        f"replan_required={goal_frontier.get('replan_required')} "
        f"user_open={normalized_progress.get('user_open_count')} "
        f"agent_open={normalized_progress.get('agent_open_count')} "
        f"current_agent_advancement={remaining.get('current_agent_claimed_advancement_count')} "
        f"unclaimed_advancement={remaining.get('unclaimed_advancement_count')} "
        f"other_agent_advancement={remaining.get('other_agent_claimed_advancement_count')} "
        f"deferred_ready={deferred_successors.get('ready_count')} "
        f"monitor_only={monitor_only_lanes.get('present')} "
        f"acceptance_gaps={len(acceptance_gaps)} "
        f"autonomy_blockers={len(autonomy_blockers)}"
    )


def _scheduler_hint_line(scheduler_hint: Mapping[str, object]) -> str | None:
    """Format compact scheduler-cadence hint line for diagnose Markdown."""
    if not scheduler_hint:
        return None
    canonical_app_automation = _as_mapping(scheduler_hint.get("app_automation"))
    app_automation = canonical_app_automation or _as_mapping(
        scheduler_hint.get("codex_app")
    )
    apply_label = (
        "app_automation_apply" if canonical_app_automation else "codex_app_apply"
    )
    unchanged_poll = _as_mapping(scheduler_hint.get("unchanged_poll"))
    return (
        "- scheduler_hint: "
        f"action={scheduler_hint.get('action')} "
        f"cadence={scheduler_hint.get('cadence_class')} "
        f"{apply_label}={app_automation.get('apply')} "
        f"apply_needed={app_automation.get('apply_needed')} "
        f"recommended_rrule={app_automation.get('recommended_rrule')} "
        f"current_rrule={app_automation.get('current_rrule')} "
        f"final_replan_check={unchanged_poll.get('final_quota_replan_check_enabled')} "
        f"no_spend_for_cadence_change={app_automation.get('no_spend_for_cadence_change')}"
    )


def _append_machine_signals(
    lines: list[str],
    selected: Mapping[str, object],
) -> None:
    todo_evidence = _as_mapping(selected.get("todo_evidence"))
    quota = _as_mapping(selected.get("quota_signals"))
    lines.extend(
        [
            "",
            "## Machine Signals",
            "",
            f"- machine_signal: `{selected.get('machine_signal')}`",
            f"- status: `{selected.get('status')}`",
            f"- waiting_on: `{selected.get('waiting_on')}`",
            f"- severity: `{selected.get('severity')}`",
            f"- recommended_action: {markdown_scalar(selected.get('recommended_action'))}",
            f"- user_question: {markdown_scalar(selected.get('user_question'))}",
            f"- user_todo_open_count: `{todo_evidence.get('user_open_count')}`",
            f"- agent_todo_open_count: `{todo_evidence.get('agent_open_count')}`",
        ]
    )
    if todo_evidence.get("first_user_todo"):
        lines.append(f"- first_user_todo: {markdown_scalar(todo_evidence.get('first_user_todo'))}")
    if todo_evidence.get("first_agent_todo"):
        lines.append(f"- first_agent_todo: {markdown_scalar(todo_evidence.get('first_agent_todo'))}")
    if not quota:
        return
    lines.extend(
        [
            f"- quota_state: `{quota.get('state')}`",
            f"- quota_should_run: `{quota.get('should_run')}`",
            f"- effective_action: `{quota.get('effective_action')}`",
            f"- action_required: `{quota.get('action_required')}`",
            f"- open_count: `{quota.get('open_count')}`",
            f"- requires_user_action: `{quota.get('requires_user_action')}`",
            f"- normal_delivery_allowed: `{quota.get('normal_delivery_allowed')}`",
            f"- self_repair_allowed: `{quota.get('self_repair_allowed')}`",
            f"- safe_bypass_allowed: `{quota.get('safe_bypass_allowed')}`",
            f"- quota_reason: {markdown_scalar(quota.get('reason'))}",
        ]
    )
    goal_frontier_line = _goal_frontier_projection_line(
        _as_mapping(quota.get("goal_frontier_projection"))
    )
    if goal_frontier_line:
        lines.append(goal_frontier_line)
    scheduler_hint_line = _scheduler_hint_line(_as_mapping(quota.get("scheduler_hint")))
    if scheduler_hint_line:
        lines.append(scheduler_hint_line)


def _append_orphaned_state(
    lines: list[str],
    selected: Mapping[str, object],
) -> None:
    orphaned_goal_state = _as_mapping(selected.get("orphaned_goal_state"))
    if not orphaned_goal_state:
        return
    routes = ", ".join(
        f"`{route}`" for route in _as_items(orphaned_goal_state.get("state_file_routes"))
    )
    forbidden = ", ".join(
        f"`{item}`"
        for item in _as_items(orphaned_goal_state.get("forbidden_until_resolved"))
    )
    lines.extend(
        [
            "",
            "## Orphaned Goal State",
            "",
            f"- reason: {markdown_scalar(orphaned_goal_state.get('reason'))}",
            f"- state_file_routes: {routes}",
            f"- forbidden_until_resolved: {forbidden}",
        ]
    )


def _append_contract_and_goals(
    lines: list[str],
    payload: Mapping[str, object],
    selected: Mapping[str, object],
) -> None:
    status_summary = _as_mapping(payload.get("status_summary"))
    contract_errors = _as_items(status_summary.get("contract_errors"))
    contract_warnings = _as_items(status_summary.get("contract_warnings"))
    if contract_errors or contract_warnings:
        lines.extend(["", "## Status Contract Signals", ""])
        lines.extend(f"- contract_error: {markdown_scalar(item)}" for item in contract_errors)
        if status_summary.get("contract_errors_truncated"):
            lines.append(
                f"- contract_errors_truncated: total={status_summary.get('contract_errors_total_count')}"
            )
        lines.extend(
            f"- contract_warning: {markdown_scalar(item)}" for item in contract_warnings
        )
        if status_summary.get("contract_warnings_truncated"):
            lines.append(
                f"- contract_warnings_truncated: total={status_summary.get('contract_warnings_total_count')}"
            )

    checklist = _as_items(selected.get("agent_reasoning_checklist"))
    if checklist:
        lines.extend(["", "## Agent Reasoning Checklist", ""])
        lines.extend(f"- {markdown_scalar(item)}" for item in checklist)

    commands = _as_items(selected.get("agent_commands"))
    if commands:
        lines.extend(
            [
                "",
                "## Agent Commands",
                "",
                "These are for the agent to run when it needs drill-down, not for the user to copy manually.",
                *(f"- `{command}`" for command in commands),
            ]
        )

    goals = _as_items(payload.get("goals"))
    if goals:
        lines.extend(
            [
                "",
                "## Goal Packets",
                "",
                "| goal | machine signal | user todo | agent todo |",
                "| --- | --- | --- | --- |",
            ]
        )
        for goal in goals:
            if not isinstance(goal, Mapping):
                continue
            evidence = _as_mapping(goal.get("todo_evidence"))
            lines.append(
                "| "
                f"`{goal.get('goal_id')}` | "
                f"`{goal.get('machine_signal')}` | "
                f"`{evidence.get('user_open_count')}` | "
                f"`{evidence.get('agent_open_count')}` |"
            )


def render_diagnosis_markdown(payload: Mapping[str, object]) -> str:
    """Render an evidence-backed Markdown report for ``loopx diagnose``."""
    selected = _as_mapping(payload.get("selected"))
    goal_packets = payload.get("goal_packet_count") or len(_as_items(payload.get("goals")))
    lines = [
        "# LoopX Diagnosis Packet",
        "",
        "This packet is evidence for the user's agent. LoopX is not making the final diagnosis.",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- packet_kind: `{payload.get('packet_kind')}`",
        f"- agent_must_reason: `{payload.get('agent_must_reason')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- selected_goal_id: `{payload.get('selected_goal_id')}`",
        f"- status_goals: `{payload.get('goal_count')}`",
        f"- goal_packets: `{goal_packets}`",
        f"- runs: `{payload.get('run_count')}`",
    ]
    _append_machine_signals(lines, selected)
    _append_orphaned_state(lines, selected)
    _append_contract_and_goals(lines, payload, selected)
    lines.extend(["", "## User Prompt", "", str(payload.get("user_prompt") or USER_DIAGNOSE_PROMPT)])
    return "\n".join(lines)
