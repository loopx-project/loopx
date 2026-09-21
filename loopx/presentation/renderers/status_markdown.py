from __future__ import annotations

from collections.abc import Collection
from typing import Any

from ...control_plane import control_plane_policy_summary
from ...control_plane.runtime.event_ledger import EVENT_LEDGER_CLASSES
from ...execution_profile import execution_profile_summary
from ...long_task_cadence import long_task_cadence_hint_summary
from ...orchestration import orchestration_policy_summary
from ..markdown import as_dict, as_list, markdown_scalar
from .goal_acceptance_observation_markdown import append_goal_acceptance_observation_markdown
from .goal_artifact_lifecycle_markdown import append_goal_artifact_lifecycle_markdown
from .reward_memory_markdown import append_agent_reward_memory_markdown


def goals_by_id(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    run_history = as_dict(payload.get("run_history"))
    goals = as_list(run_history.get("goals"))
    result: dict[str, dict[str, Any]] = {}
    for goal in goals:
        if not isinstance(goal, dict):
            continue
        goal_id = str(goal.get("id") or "")
        if goal_id:
            result[goal_id] = goal
    return result


def authority_registry_markdown_summary(goal: dict[str, Any] | None) -> str | None:
    registry = as_dict(as_dict(goal).get("authority_registry"))
    if not registry.get("declared"):
        return None
    materials = int(registry.get("project_material_count") or 0)
    topics = int(registry.get("topic_authority_count") or 0)
    if materials <= 0 and topics <= 0:
        return None
    return (
        f"entries={int(registry.get('default_entries_present') or 0)}/"
        f"{int(registry.get('default_entry_count') or 0)} "
        f"topics={topics} "
        f"materials={materials} "
        f"repositories={int(registry.get('project_material_repository_count') or 0)} "
        f"owner_review_required={int(registry.get('project_material_owner_review_required_count') or 0)} "
        f"stale={int(registry.get('project_material_stale_count') or 0)} "
        f"current_authority={int(registry.get('project_material_current_authority_count') or 0)} "
        f"risk={markdown_scalar(registry.get('conflict_risk') or 'unknown')}"
    )


def append_status_overview_markdown(
    lines: list[str],
    payload: dict[str, Any],
) -> None:
    lines.extend(
        [
            "# LoopX Status",
            "",
            f"- ok: `{payload.get('ok')}`",
            f"- registry: `{payload.get('registry')}`",
            f"- runtime_root: `{payload.get('runtime_root')}`",
            f"- goals: `{payload.get('goal_count')}`",
            f"- runs: `{payload.get('run_count')}`",
        ]
    )

    status_contract = as_dict(payload.get("status_contract"))
    if status_contract:
        lines.append(
            "- status_contract: "
            f"schema_version={status_contract.get('schema_version')}, "
            f"minimum_dashboard_schema_version={status_contract.get('minimum_dashboard_schema_version')}, "
            f"producer={status_contract.get('producer')}"
        )
    if payload.get("goal_filter"):
        lines.append(f"- goal_filter: `{payload.get('goal_filter')}`")

    projection_scope = as_dict(payload.get("goal_projection")).get("scope")
    if projection_scope:
        lines.append(f"- activation_filter: `{projection_scope}`")

    contract = as_dict(payload.get("contract"))
    summary = as_dict(contract.get("summary"))
    lines.append(
        "- contract: "
        f"ok={contract.get('ok')}, "
        f"errors={summary.get('errors')}, "
        f"warnings={summary.get('warnings')}, "
        f"checks={summary.get('checks')}"
    )
    contract_errors = as_list(payload.get("contract_errors"))
    contract_warnings = as_list(payload.get("contract_warnings"))
    if contract_errors or contract_warnings:
        lines.extend(["", "## Status Contract Signals"])
        for item in contract_errors:
            lines.append(f"- error: {item}")
        if payload.get("contract_errors_truncated"):
            lines.append(
                f"- contract_errors_truncated: total={payload.get('contract_errors_total_count')}"
            )
        for item in contract_warnings:
            lines.append(f"- warning: {item}")
        if payload.get("contract_warnings_truncated"):
            lines.append(
                f"- contract_warnings_truncated: total={payload.get('contract_warnings_total_count')}"
            )


def append_status_contract_detail_sections_markdown(
    lines: list[str],
    contract: dict[str, Any],
) -> None:
    for title, key in (("Errors", "errors"), ("Warnings", "warnings"), ("Checks", "checks")):
        entries = contract.get(key) if isinstance(contract.get(key), list) else []
        if entries:
            lines.extend(["", f"## {title}"])
            lines.extend(f"- {entry}" for entry in entries)


def append_global_registry_summary_markdown(
    lines: list[str],
    global_registry: dict[str, Any],
) -> None:
    global_summary = as_dict(global_registry.get("summary"))
    lines.extend(
        [
            "- global_registry: "
            f"available={global_registry.get('available')}, "
            f"ok={global_registry.get('ok')}, "
            f"findings={global_summary.get('findings')}, "
            f"high={global_summary.get('high')}, "
            f"action={global_summary.get('action')}, "
            f"info={global_summary.get('info')}",
        ]
    )


def append_runtime_projection_routes_markdown(
    lines: list[str],
    diagnostics: dict[str, Any],
) -> None:
    healthy = diagnostics.get("healthy")
    if healthy is None:
        return
    count = diagnostics.get("goal_count")
    scope = f" (goals={count})" if isinstance(count, int) else ""
    suffix = "" if healthy else ", global details=loopx doctor"
    lines.append(f"- runtime_projection_routes: healthy={healthy}{scope}{suffix}")


def append_global_registry_findings_markdown(
    lines: list[str],
    global_registry: dict[str, Any],
) -> None:
    findings = as_list(global_registry.get("findings"))
    if not findings:
        return
    lines.extend(["", "## Global Registry Findings"])
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        lines.append(
            "- "
            f"{finding.get('severity')} "
            f"{finding.get('kind')} "
            f"goal={finding.get('goal_id') or finding.get('goal_ids') or 'global'}: "
            f"{finding.get('message')}"
        )
        if finding.get("recommended_action"):
            lines.append(f"  - action: {finding.get('recommended_action')}")


def append_human_reward_markdown(lines: list[str], goal_id: Any, reward: dict[str, Any]) -> None:
    headline_parts = []
    for field in ("recorded_at", "actor_kind", "decision", "reward"):
        value = reward.get(field)
        if value:
            headline_parts.append(f"{field}={markdown_scalar(value)}")
    if not headline_parts:
        headline_parts.append("recorded=True")
    lines.append(f"    - human_reward: {' '.join(headline_parts)}")
    reason = reward.get("reason_summary")
    if reason:
        lines.append(f"      - reason_summary: {markdown_scalar(reason)}")
    follow_up = reward.get("follow_up")
    if follow_up:
        lines.append(f"      - follow_up: {markdown_scalar(follow_up)}")
    lesson = as_dict(reward.get("lesson"))
    if lesson:
        lines.append(
            "      - lesson: "
            f"kind={markdown_scalar(lesson.get('kind') or '')} "
            f"summary={markdown_scalar(lesson.get('summary') or '')}"
        )
        for field in ("avoid", "prefer"):
            values = as_list(lesson.get(field))
            if values:
                lines.append(
                    f"        - lesson_{field}: "
                    + ", ".join(markdown_scalar(value) for value in values[:5])
                )
    if goal_id:
        lines.append(
            "      - project_agent_visibility: "
            f"`loopx history --goal-id {markdown_scalar(goal_id)} --limit 3`"
        )


def append_operator_gate_resume_contract_markdown(lines: list[str], contract: dict[str, Any]) -> None:
    headline_parts = []
    for field in ("version", "gate_id", "operator_decision"):
        value = contract.get(field)
        if value:
            headline_parts.append(f"{field}={markdown_scalar(value)}")
    if not headline_parts:
        headline_parts.append("recorded=True")
    lines.append(f"    - operator_gate_resume_contract: {' '.join(headline_parts)}")
    for field in (
        "latest_state_ref",
        "freshness_check",
        "precondition_check",
        "migration_or_rebase_result",
        "validation_after_resume",
    ):
        value = contract.get(field)
        if value:
            lines.append(f"      - {field}: {markdown_scalar(value)}")


def append_run_history_markdown(lines: list[str], run_history: dict[str, Any]) -> None:
    run_goals = run_history.get("goals") if isinstance(run_history.get("goals"), list) else []
    lines.extend(
        [
            "",
            "## Run History",
            "- summary: "
            f"goals={run_history.get('goal_count')}, "
            f"runs={run_history.get('run_count')}",
        ]
    )
    if not run_goals:
        lines.append("- none")
    for goal in run_goals:
        if not isinstance(goal, dict):
            continue
        lines.append(
            "- "
            f"`{goal.get('id')}`: "
            f"status={goal.get('status')} "
            f"phase={goal.get('lifecycle_phase')} "
            f"adapter={goal.get('adapter_kind')}:{goal.get('adapter_status')} "
            f"records={goal.get('raw_index_records')} "
            f"unique_runs={goal.get('unique_runs')}"
        )
        append_goal_acceptance_observation_markdown(lines, goal)
        append_goal_artifact_lifecycle_markdown(lines, goal)
        quota = goal.get("quota") if isinstance(goal.get("quota"), dict) else {}
        if quota:
            lines.append(
                "  - quota: "
                f"compute={quota.get('compute')} "
                f"state={quota.get('state')} "
                f"slots={quota.get('spent_slots')}/{quota.get('allowed_slots')}"
            )
        latest_runs = goal.get("latest_runs") if isinstance(goal.get("latest_runs"), list) else []
        if latest_runs:
            latest = latest_runs[0]
            if isinstance(latest, dict):
                reward = latest.get("human_reward") if isinstance(latest.get("human_reward"), dict) else {}
                reward_text = (
                    f" reward={reward.get('decision')}:{reward.get('reward')}"
                    if reward
                    else ""
                )
                operator_gate = (
                    latest.get("operator_gate")
                    if isinstance(latest.get("operator_gate"), dict)
                    else {}
                )
                operator_gate_text = (
                    f" operator_gate={operator_gate.get('gate')}:{operator_gate.get('decision')}"
                    if operator_gate
                    else ""
                )
                readiness = (
                    latest.get("controller_readiness")
                    if isinstance(latest.get("controller_readiness"), dict)
                    else {}
                )
                readiness_text = (
                    f" readiness={readiness.get('classification')}"
                    if readiness
                    else ""
                )
                lines.append(
                    "  - latest: "
                    f"{latest.get('generated_at')} "
                    f"classification={latest.get('classification')} "
                    f"phase={latest.get('lifecycle_phase')} "
                    f"artifacts={latest.get('json_exists')}/{latest.get('markdown_exists')}"
                    f"{reward_text}"
                    f"{operator_gate_text}"
                    f"{readiness_text}"
                )
                if reward:
                    append_human_reward_markdown(lines, goal.get("id"), reward)
                resume_contract = (
                    latest.get("operator_gate_resume_contract")
                    if isinstance(latest.get("operator_gate_resume_contract"), dict)
                    else {}
                )
                if resume_contract:
                    append_operator_gate_resume_contract_markdown(lines, resume_contract)


def event_class_count_text(counts: dict[str, Any], event_classes: Collection[str]) -> str:
    return " ".join(
        f"{event_class}={counts.get(event_class, 0)}"
        for event_class in event_classes
    )


def append_event_ledger_summary_markdown(
    lines: list[str],
    event_ledger: dict[str, Any],
    *,
    event_classes: Collection[str],
) -> None:
    event_totals = (
        event_ledger.get("totals")
        if isinstance(event_ledger.get("totals"), dict)
        else {}
    )
    if not event_ledger.get("available") or not event_totals:
        return

    by_class_24h = (
        event_totals.get("by_class_24h")
        if isinstance(event_totals.get("by_class_24h"), dict)
        else {}
    )
    by_class_7d = (
        event_totals.get("by_class_7d")
        if isinstance(event_totals.get("by_class_7d"), dict)
        else {}
    )
    lines.extend(
        [
            "",
            "## Event Ledger Summary",
            "- summary: "
            f"source={markdown_scalar(event_ledger.get('source') or '')} "
            f"samples={event_ledger.get('sample_run_count')} "
            f"events_24h={event_totals.get('events_24h')} "
            f"events_7d={event_totals.get('events_7d')} "
            f"classes_24h={event_class_count_text(by_class_24h, event_classes)} "
            f"classes_7d={event_class_count_text(by_class_7d, event_classes)}",
        ]
    )

    event_goals = (
        event_ledger.get("goals")
        if isinstance(event_ledger.get("goals"), list)
        else []
    )
    for goal in event_goals[:3]:
        if not isinstance(goal, dict):
            continue
        goal_by_class_24h = (
            goal.get("by_class_24h")
            if isinstance(goal.get("by_class_24h"), dict)
            else {}
        )
        lines.append(
            "- "
            f"`{markdown_scalar(goal.get('goal_id') or '')}`: "
            f"events_24h={goal.get('events_24h')} "
            f"events_7d={goal.get('events_7d')} "
            f"latest={markdown_scalar(goal.get('latest_event_class') or '')} "
            f"classes_24h={event_class_count_text(goal_by_class_24h, event_classes)}"
        )


def append_promotion_readiness_summary_markdown(
    lines: list[str],
    promotion_readiness: dict[str, Any],
) -> None:
    if not promotion_readiness:
        return
    lines.extend(
        [
            "",
            "## Promotion Readiness Summary",
            "- summary: "
            f"source={markdown_scalar(promotion_readiness.get('source') or '')} "
            f"available={promotion_readiness.get('available')} "
            f"samples={promotion_readiness.get('sample_run_count')} "
            f"freshness={markdown_scalar(promotion_readiness.get('freshness_status') or '')} "
            f"age_hours={promotion_readiness.get('age_hours')} "
            f"requires_readiness_run={promotion_readiness.get('requires_readiness_run')} "
            f"window_hours={promotion_readiness.get('freshness_window_hours')}",
            "- latest: "
            f"goal={markdown_scalar(promotion_readiness.get('goal_id') or '')} "
            f"generated_at={markdown_scalar(promotion_readiness.get('generated_at') or '')} "
            f"classification={markdown_scalar(promotion_readiness.get('classification') or '')} "
            f"outcome={markdown_scalar(promotion_readiness.get('delivery_outcome') or '')} "
            f"artifacts={promotion_readiness.get('json_exists')}/{promotion_readiness.get('markdown_exists')}",
        ]
    )


def append_promotion_gate_markdown(
    lines: list[str],
    promotion_gate: dict[str, Any],
) -> None:
    if not promotion_gate:
        return
    promotion_gate_readiness = (
        promotion_gate.get("readiness")
        if isinstance(promotion_gate.get("readiness"), dict)
        else {}
    )
    lines.extend(
        [
            "",
            "## Promotion Gate",
            "- gate: "
            f"state={markdown_scalar(promotion_gate.get('gate_state') or '')} "
            f"can_promote={promotion_gate.get('can_promote')} "
            f"should_warn={promotion_gate.get('should_warn')} "
            f"non_blocking={promotion_gate.get('non_blocking')} "
            f"freshness={markdown_scalar(promotion_gate_readiness.get('freshness_status') or '')} "
            f"requires_readiness_run={promotion_gate_readiness.get('requires_readiness_run')}",
            "- latest: "
            f"generated_at={markdown_scalar(promotion_gate_readiness.get('generated_at') or '')} "
            f"age_hours={promotion_gate_readiness.get('age_hours')} "
            f"action={markdown_scalar(promotion_gate.get('recommended_action') or '')}",
        ]
    )
    if promotion_gate.get("warning_message"):
        lines.append(
            "- warning: "
            f"{markdown_scalar(promotion_gate.get('warning_message') or '')}"
        )


def append_decision_freshness_summary_markdown(
    lines: list[str],
    decision_freshness: dict[str, Any],
) -> None:
    decision_summary = (
        decision_freshness.get("summary")
        if isinstance(decision_freshness.get("summary"), dict)
        else {}
    )
    if not decision_freshness.get("available") or not decision_summary:
        return

    lines.extend(
        [
            "",
            "## Decision Freshness Summary",
            "- summary: "
            f"source={markdown_scalar(decision_freshness.get('source') or '')} "
            f"samples={decision_freshness.get('sample_run_count')} "
            f"window_days={decision_freshness.get('window_days')} "
            f"decisions={decision_summary.get('decision_count')} "
            f"stale={decision_summary.get('stale_count')} "
            f"rebase_required={decision_summary.get('rebase_required_count')} "
            f"fresh={decision_summary.get('fresh_count')}",
        ]
    )
    decision_items = (
        decision_freshness.get("items")
        if isinstance(decision_freshness.get("items"), list)
        else []
    )
    for item in decision_items[:3]:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- "
            f"`{markdown_scalar(item.get('goal_id') or '')}`: "
            f"kind={markdown_scalar(item.get('decision_kind') or '')} "
            f"state={markdown_scalar(item.get('freshness_state') or '')} "
            f"age_days={item.get('age_days')} "
            f"newer_7d={item.get('newer_event_count_7d')} "
            f"decision_at={markdown_scalar(item.get('decision_at') or '')}"
        )


def append_usage_summary_markdown(lines: list[str], usage: dict[str, Any]) -> None:
    usage_totals = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
    if not usage.get("available") or not usage_totals:
        return

    lines.extend(
        [
            "",
            "## Usage Summary",
            "- summary: "
            f"source={markdown_scalar(usage.get('source') or '')} "
            f"samples={usage.get('sample_run_count')} "
            f"runs_24h={usage_totals.get('runs_24h')} "
            f"runs_7d={usage_totals.get('runs_7d')} "
            f"quota_slots_24h={usage_totals.get('quota_spend_slots_24h')} "
            f"quota_slots_7d={usage_totals.get('quota_spend_slots_7d')} "
            f"automation_24h={usage_totals.get('automation_run_count_24h')} "
            f"automation_7d={usage_totals.get('automation_run_count_7d')} "
            f"progress_signals_24h={usage_totals.get('progress_signal_run_count_24h')} "
            f"progress_signals_7d={usage_totals.get('progress_signal_run_count_7d')}",
        ]
    )
    usage_goals = usage.get("goals") if isinstance(usage.get("goals"), list) else []
    for goal in usage_goals[:3]:
        if not isinstance(goal, dict):
            continue
        lines.append(
            "- "
            f"`{markdown_scalar(goal.get('goal_id') or '')}`: "
            f"runs_24h={goal.get('runs_24h')} "
            f"runs_7d={goal.get('runs_7d')} "
            f"quota_slots_24h={goal.get('quota_spend_slots_24h')} "
            f"automation_24h={goal.get('automation_run_count_24h')} "
            f"progress_signals_24h={goal.get('progress_signal_run_count_24h')} "
            f"share_24h={goal.get('project_share_24h')}"
        )


def append_project_asset_warning_markdown(
    lines: list[str],
    project_asset: dict[str, Any],
    item: dict[str, Any],
) -> None:
    projection_warning = (
        project_asset.get("stale_latest_run_warning")
        if isinstance(project_asset.get("stale_latest_run_warning"), dict)
        else {}
    )
    if projection_warning:
        lines.append(
            "    - stale_latest_run_warning: "
            f"requires_refresh_state={projection_warning.get('requires_refresh_state')} "
            f"active_state_updated_at={markdown_scalar(projection_warning.get('active_state_updated_at') or '')} "
            f"latest_run_generated_at={markdown_scalar(projection_warning.get('latest_run_generated_at') or '')} "
            f"reason={markdown_scalar(projection_warning.get('reason') or '')}"
        )
    next_action_warning = (
        project_asset.get("next_action_projection_warning")
        if isinstance(project_asset.get("next_action_projection_warning"), dict)
        else item.get("next_action_projection_warning")
        if isinstance(item.get("next_action_projection_warning"), dict)
        else {}
    )
    if next_action_warning:
        lines.append(
            "    - next_action_projection_warning: "
            f"requires_state_writeback={next_action_warning.get('requires_state_writeback')} "
            f"severity={markdown_scalar(next_action_warning.get('severity') or '')} "
            f"reason={markdown_scalar(next_action_warning.get('reason') or '')}"
        )
    backlog_warning = (
        project_asset.get("backlog_hygiene_warning")
        if isinstance(project_asset.get("backlog_hygiene_warning"), dict)
        else {}
    )
    if backlog_warning:
        lines.append(
            "    - backlog_hygiene_warning: "
            f"requires_agent_todo={backlog_warning.get('requires_agent_todo')} "
            f"evidence_count={backlog_warning.get('evidence_count')} "
            f"source_sections={markdown_scalar(','.join(backlog_warning.get('source_sections') or []))}"
        )
    projection_gap = (
        project_asset.get("state_projection_gap")
        if isinstance(project_asset.get("state_projection_gap"), dict)
        else {}
    )
    if projection_gap:
        lines.append(
            "    - state_projection_gap: "
            f"requires_todo_expansion={projection_gap.get('requires_todo_expansion')} "
            f"user_open={projection_gap.get('user_open_count')} "
            f"agent_open={projection_gap.get('agent_open_count')} "
            f"target_roles={markdown_scalar(','.join(projection_gap.get('target_roles') or []))}"
        )
    todo_projection_gap = (
        project_asset.get("todo_projection_gap")
        if isinstance(project_asset.get("todo_projection_gap"), dict)
        else {}
    )
    if todo_projection_gap:
        lines.append(
            "    - todo_projection_gap: "
            f"missing_roles={markdown_scalar(','.join(todo_projection_gap.get('missing_roles') or []))} "
            f"source={markdown_scalar(todo_projection_gap.get('source') or '')}"
        )
    archive_warning = (
        project_asset.get("completed_todo_archive_warning")
        if isinstance(project_asset.get("completed_todo_archive_warning"), dict)
        else {}
    )
    if archive_warning:
        lines.append(
            "    - completed_todo_archive_warning: "
            f"requires_archive={archive_warning.get('requires_archive')} "
            f"active_done={archive_warning.get('active_done_count')} "
            f"max_active_done={archive_warning.get('max_active_done_count')} "
            f"default_archive_keep={archive_warning.get('default_archive_keep_count')} "
            f"archive_section={markdown_scalar(archive_warning.get('archive_section') or '')} "
            f"archive_command={markdown_scalar(archive_warning.get('archive_command_template') or '')}"
        )
    replan_obligation = (
        project_asset.get("autonomous_replan_obligation")
        if isinstance(project_asset.get("autonomous_replan_obligation"), dict)
        else {}
    )
    if replan_obligation:
        trigger_kinds = [
            str(trigger.get("kind") or "")
            for trigger in replan_obligation.get("triggers") or []
            if isinstance(trigger, dict) and trigger.get("kind")
        ]
        lines.append(
            "    - autonomous_replan_obligation: "
            f"required={replan_obligation.get('required')} "
            f"trigger_count={replan_obligation.get('trigger_count')} "
            f"triggers={markdown_scalar(','.join(trigger_kinds))}"
        )
    interface_budget_cadence = (
        project_asset.get("interface_budget_cadence")
        if isinstance(project_asset.get("interface_budget_cadence"), dict)
        else {}
    )
    if interface_budget_cadence:
        lines.append(
            "    - interface_budget_cadence: "
            f"overdue={interface_budget_cadence.get('overdue')} "
            f"within_budget={interface_budget_cadence.get('within_budget')} "
            f"checked_at={markdown_scalar(interface_budget_cadence.get('checked_at') or '')} "
            f"next_check_due_at={markdown_scalar(interface_budget_cadence.get('next_check_due_at') or '')} "
            f"tightest={markdown_scalar(interface_budget_cadence.get('tightest_surface') or '')}/"
            f"{markdown_scalar(interface_budget_cadence.get('tightest_metric') or '')} "
            f"headroom={interface_budget_cadence.get('headroom_remaining')} "
            f"recommendation={markdown_scalar(interface_budget_cadence.get('recommendation') or '')}"
        )
    latest_validation = (
        project_asset.get("latest_validation")
        if isinstance(project_asset.get("latest_validation"), dict)
        else {}
    )
    if latest_validation:
        lines.append(
            "    - latest_validation: "
            f"classification={markdown_scalar(latest_validation.get('classification') or '')} "
            f"at={markdown_scalar(latest_validation.get('generated_at') or '')}"
        )


def append_project_asset_session_runtime_markdown(
    lines: list[str],
    project_asset: dict[str, Any],
) -> None:
    session_projection = (
        project_asset.get("session_runtime_projection")
        if isinstance(project_asset.get("session_runtime_projection"), dict)
        else {}
    )
    if not session_projection:
        return
    first_screen = (
        session_projection.get("first_screen")
        if isinstance(session_projection.get("first_screen"), dict)
        else {}
    )
    boundary = (
        session_projection.get("boundary")
        if isinstance(session_projection.get("boundary"), dict)
        else {}
    )
    source = (
        session_projection.get("source")
        if isinstance(session_projection.get("source"), dict)
        else {}
    )
    lines.append(
        "    - session_runtime_projection: "
        f"waiting_on={markdown_scalar(first_screen.get('waiting_on') or '')} "
        f"agent_can_continue={first_screen.get('agent_can_continue')} "
        f"user_action_required={first_screen.get('user_action_required')} "
        f"gate={markdown_scalar(first_screen.get('gate_state') or '')} "
        f"raw_material_detected={boundary.get('raw_material_detected')} "
        f"runtime_writeback_allowed={boundary.get('runtime_writeback_allowed')} "
        f"host={markdown_scalar(source.get('host_kind') or '')}"
    )
    if first_screen.get("first_user_todo"):
        lines.append(
            "      - session_runtime_user_todo: "
            f"{markdown_scalar(first_screen.get('first_user_todo') or '')}"
        )
    if first_screen.get("first_agent_todo"):
        lines.append(
            "      - session_runtime_agent_todo: "
            f"{markdown_scalar(first_screen.get('first_agent_todo') or '')}"
        )


def append_handoff_readiness_markdown(
    lines: list[str],
    handoff_readiness: dict[str, Any],
) -> None:
    if not handoff_readiness:
        return
    lines.append(
        "    - handoff_readiness: "
        f"ready={handoff_readiness.get('ready')} "
        f"codex_ready={handoff_readiness.get('codex_ready')} "
        f"source={markdown_scalar(handoff_readiness.get('source') or '')} "
        f"quota_state={markdown_scalar(handoff_readiness.get('quota_state') or '')}"
    )
    interface_budget = (
        handoff_readiness.get("handoff_interface_budget")
        if isinstance(handoff_readiness.get("handoff_interface_budget"), dict)
        else {}
    )
    if interface_budget:
        lines.append(
            "      - handoff_interface_budget: "
            f"mode={markdown_scalar(interface_budget.get('mode') or '')} "
            f"max_lines={interface_budget.get('max_lines')} "
            f"max_chars={interface_budget.get('max_chars')}"
        )
    checks = (
        handoff_readiness.get("checks")
        if isinstance(handoff_readiness.get("checks"), dict)
        else {}
    )
    passed = [key for key, value in checks.items() if value]
    failed = [key for key, value in checks.items() if not value]
    if checks:
        lines.append(
            "      - handoff_checks: "
            f"pass={','.join(passed) if passed else '-'} "
            f"fail={','.join(failed) if failed else '-'}"
        )
    lines.append(
        "      - handoff_state: "
        f"status={markdown_scalar(handoff_readiness.get('handoff_status') or '')} "
        f"post_handoff_run_seen={handoff_readiness.get('post_handoff_run_seen')} "
        f"ready_at={markdown_scalar(handoff_readiness.get('handoff_ready_at') or '')}"
    )
    latest_handoff_run = (
        handoff_readiness.get("post_handoff_latest_run")
        if isinstance(handoff_readiness.get("post_handoff_latest_run"), dict)
        else {}
    )
    if latest_handoff_run:
        outcome_suffix = ""
        if latest_handoff_run.get("delivery_outcome"):
            outcome_suffix = (
                " "
                f"outcome={markdown_scalar(latest_handoff_run.get('delivery_outcome') or '')}"
            )
        turn_kind_suffix = ""
        if latest_handoff_run.get("delivery_turn_kind"):
            turn_kind_suffix = (
                " "
                f"turn_kind={markdown_scalar(latest_handoff_run.get('delivery_turn_kind') or '')}"
            )
        lines.append(
            "      - post_handoff_run: "
            f"classification={markdown_scalar(latest_handoff_run.get('classification') or '')} "
            f"at={markdown_scalar(latest_handoff_run.get('generated_at') or '')} "
            f"scale={markdown_scalar(latest_handoff_run.get('delivery_batch_scale') or '')}"
            f"{outcome_suffix}"
            f"{turn_kind_suffix}"
        )
    recent_handoff_runs = (
        handoff_readiness.get("post_handoff_recent_runs")
        if isinstance(handoff_readiness.get("post_handoff_recent_runs"), list)
        else []
    )
    recent_scales = [
        markdown_scalar(str(run.get("delivery_batch_scale") or ""))
        for run in recent_handoff_runs
        if isinstance(run, dict)
    ]
    if recent_scales:
        recent_outcomes = [
            markdown_scalar(str(run.get("delivery_outcome") or ""))
            for run in recent_handoff_runs
            if isinstance(run, dict) and run.get("delivery_outcome")
        ]
        outcome_text = f" outcome={','.join(recent_outcomes)}" if recent_outcomes else ""
        recent_turn_kinds = [
            markdown_scalar(str(run.get("delivery_turn_kind") or ""))
            for run in recent_handoff_runs
            if isinstance(run, dict) and run.get("delivery_turn_kind")
        ]
        turn_kind_text = f" turn_kind={','.join(recent_turn_kinds)}" if recent_turn_kinds else ""
        gap_text = (
            f" outcome_gap_streak={handoff_readiness.get('post_handoff_outcome_gap_streak')}"
            if "post_handoff_outcome_gap_streak" in handoff_readiness
            else ""
        )
        lines.append(
            "      - post_handoff_recent_scales: "
            f"{','.join(recent_scales)} "
            f"small_streak={handoff_readiness.get('post_handoff_small_scale_streak', 0)}"
            f"{outcome_text}"
            f"{turn_kind_text}"
            f"{gap_text}"
        )
    if handoff_readiness.get("next_probe"):
        handoff_probe = markdown_scalar(handoff_readiness.get("next_probe") or "")
        lines.append(f"      - handoff_probe: `{handoff_probe}`")


def append_project_asset_todo_quota_markdown(
    lines: list[str],
    project_asset: dict[str, Any],
    *,
    goal_todo_scope_suffix: str = "",
) -> None:
    asset_user_todos = (
        project_asset.get("user_todos")
        if isinstance(project_asset.get("user_todos"), dict)
        else {}
    )
    asset_agent_todos = (
        project_asset.get("agent_todos")
        if isinstance(project_asset.get("agent_todos"), dict)
        else {}
    )
    if asset_user_todos or asset_agent_todos:
        todo_parts = []
        if asset_user_todos:
            todo_parts.append(f"user_open={asset_user_todos.get('open')}")
            if asset_user_todos.get("claimed_open_count"):
                todo_parts.append(f"user_claimed={asset_user_todos.get('claimed_open_count')}")
        if asset_agent_todos:
            todo_parts.append(f"agent_open={asset_agent_todos.get('open')}")
            if asset_agent_todos.get("claimed_open_count"):
                todo_parts.append(f"agent_claimed={asset_agent_todos.get('claimed_open_count')}")
        lines.append(f"    - asset_todos: {' '.join(todo_parts)}")
        if asset_user_todos.get("next"):
            claimed = asset_user_todos.get("next_claimed_by")
            claim_suffix = f" claimed_by={markdown_scalar(claimed)}" if claimed else ""
            lines.append(
                f"      - asset_user_todo: "
                f"{markdown_scalar(asset_user_todos.get('next') or '')}"
                f"{claim_suffix}"
            )
        for todo in (asset_user_todos.get("items") or [])[1:3]:
            if isinstance(todo, dict) and todo.get("text"):
                index = todo.get("index")
                suffix = f"[{index}]" if index is not None else ""
                claimed = todo.get("claimed_by")
                claim_suffix = f" claimed_by={markdown_scalar(claimed)}" if claimed else ""
                lines.append(
                    f"      - asset_user_todo{suffix}: "
                    f"{markdown_scalar(todo.get('text') or '')}"
                    f"{claim_suffix}"
                )
        if asset_agent_todos.get("next"):
            claimed = asset_agent_todos.get("next_claimed_by")
            claim_suffix = f" claimed_by={markdown_scalar(claimed)}" if claimed else ""
            lines.append(
                f"      - asset_agent_todo: "
                f"{markdown_scalar(asset_agent_todos.get('next') or '')}"
                f"{claim_suffix}{goal_todo_scope_suffix}"
            )
        for todo in (asset_agent_todos.get("items") or [])[1:3]:
            if isinstance(todo, dict) and todo.get("text"):
                index = todo.get("index")
                suffix = f"[{index}]" if index is not None else ""
                claimed = todo.get("claimed_by")
                claim_suffix = f" claimed_by={markdown_scalar(claimed)}" if claimed else ""
                lines.append(
                    f"      - asset_agent_todo{suffix}: "
                    f"{markdown_scalar(todo.get('text') or '')}"
                    f"{claim_suffix}{goal_todo_scope_suffix}"
                )
    asset_quota = (
        project_asset.get("quota")
        if isinstance(project_asset.get("quota"), dict)
        else {}
    )
    if asset_quota:
        lines.append(
            "    - asset_quota: "
            f"compute={asset_quota.get('compute')} "
            f"state={asset_quota.get('state')} "
            f"slots={asset_quota.get('spent_slots')}/{asset_quota.get('allowed_slots')}"
        )


def append_attention_queue_summary_markdown(
    lines: list[str],
    queue: dict[str, Any],
) -> None:
    lines.extend(
        [
            "",
            "## Attention Queue",
            "- summary: "
            f"items={queue.get('item_count')}, "
            f"needs_user_or_controller={queue.get('needs_user_or_controller')}, "
            f"needs_controller={queue.get('needs_controller')}, "
            f"needs_codex={queue.get('needs_codex')}, "
            f"watching_external_evidence={queue.get('watching_external_evidence')}, "
            f"watching_monitor={queue.get('watching_monitor')}",
        ]
    )
    backlog = (
        queue.get("autonomous_backlog_candidates")
        if isinstance(queue.get("autonomous_backlog_candidates"), dict)
        else {}
    )
    append_attention_queue_candidate_group_markdown(
        lines,
        backlog,
        group_name="autonomous_backlog_candidates",
        item_name="autonomous_candidate",
    )
    monitor_candidates = (
        queue.get("autonomous_monitor_candidates")
        if isinstance(queue.get("autonomous_monitor_candidates"), dict)
        else {}
    )
    append_attention_queue_candidate_group_markdown(
        lines,
        monitor_candidates,
        group_name="autonomous_monitor_candidates",
        item_name="autonomous_monitor_candidate",
    )


def render_status_markdown(
    payload: dict[str, Any],
    *,
    event_classes: Collection[str] = EVENT_LEDGER_CLASSES,
) -> str:
    lines: list[str] = []
    append_status_overview_markdown(lines, payload)
    contract = payload.get("contract") if isinstance(payload.get("contract"), dict) else {}

    global_registry = (
        payload.get("global_registry")
        if isinstance(payload.get("global_registry"), dict)
        else {}
    )
    append_global_registry_summary_markdown(lines, global_registry)
    runtime_projection_routes = (
        payload.get("runtime_projection_routes")
        if isinstance(payload.get("runtime_projection_routes"), dict)
        else {}
    )
    append_runtime_projection_routes_markdown(lines, runtime_projection_routes)

    event_ledger = (
        payload.get("event_ledger_summary")
        if isinstance(payload.get("event_ledger_summary"), dict)
        else {}
    )
    append_event_ledger_summary_markdown(
        lines,
        event_ledger,
        event_classes=event_classes,
    )

    promotion_readiness = (
        payload.get("promotion_readiness_summary")
        if isinstance(payload.get("promotion_readiness_summary"), dict)
        else {}
    )
    append_promotion_readiness_summary_markdown(lines, promotion_readiness)

    promotion_gate = (
        payload.get("promotion_gate")
        if isinstance(payload.get("promotion_gate"), dict)
        else {}
    )
    append_promotion_gate_markdown(lines, promotion_gate)

    decision_freshness = (
        payload.get("decision_freshness_summary")
        if isinstance(payload.get("decision_freshness_summary"), dict)
        else {}
    )
    append_decision_freshness_summary_markdown(lines, decision_freshness)

    usage = payload.get("usage_summary") if isinstance(payload.get("usage_summary"), dict) else {}
    append_usage_summary_markdown(lines, usage)

    queue = payload.get("attention_queue") if isinstance(payload.get("attention_queue"), dict) else {}
    append_attention_queue_summary_markdown(lines, queue)
    items = queue.get("items") if isinstance(queue.get("items"), list) else []
    goals = goals_by_id(payload)
    if not items:
        lines.append("- none")
    for item in items:
        if not isinstance(item, dict):
            continue
        authority_summary = authority_registry_markdown_summary(
            goals.get(str(item.get("goal_id") or ""))
        )
        append_attention_queue_item_header_markdown(
            lines,
            item,
            authority_summary=authority_summary,
        )
        goal_todo_scope_suffix = attention_queue_goal_todo_scope_suffix(item)
        append_attention_queue_project_asset_markdown(
            lines,
            item,
            goal_todo_scope_suffix=goal_todo_scope_suffix,
        )
        append_attention_queue_item_operational_markdown(
            lines,
            item,
            goal_todo_scope_suffix=goal_todo_scope_suffix,
        )

    run_history = payload.get("run_history") if isinstance(payload.get("run_history"), dict) else {}
    append_run_history_markdown(lines, run_history)

    append_status_contract_detail_sections_markdown(lines, contract)

    append_global_registry_findings_markdown(lines, global_registry)

    return "\n".join(lines)


def append_attention_queue_candidate_group_markdown(
    lines: list[str],
    candidates: dict[str, Any],
    *,
    group_name: str,
    item_name: str,
) -> None:
    if not candidates:
        return
    lines.append(
        f"- {group_name}: "
        f"open={candidates.get('open_count')} "
        f"task_class={markdown_scalar(candidates.get('task_class') or '')} "
        f"source={markdown_scalar(candidates.get('source') or '')}"
    )
    for candidate in candidates.get("items") or []:
        if not isinstance(candidate, dict):
            continue
        priority_text = ""
        if candidate.get("priority"):
            priority_text = f" priority={markdown_scalar(candidate.get('priority') or '')}"
        lines.append(
            f"  - {item_name}: "
            f"goal={markdown_scalar(candidate.get('goal_id') or '')}"
            f"{priority_text} "
            f"text={markdown_scalar(candidate.get('text') or '')}"
        )


def append_attention_queue_item_header_markdown(
    lines: list[str],
    item: dict[str, Any],
    *,
    authority_summary: str | None = None,
) -> None:
    action = markdown_scalar(item.get("recommended_action") or "")
    lines.append(
        "- "
        f"`{item.get('goal_id')}`: "
        f"status={item.get('status')} "
        f"phase={item.get('lifecycle_phase')} "
        f"waiting_on={item.get('waiting_on')} "
        f"severity={item.get('severity')} "
        f"source={item.get('source')}"
    )
    if action:
        lines.append(f"  - action: {action}")
    active_state_action = markdown_scalar(item.get("active_state_next_action") or "")
    if active_state_action:
        lines.append(f"  - active_state_next_action: {active_state_action}")
    latest_run_action = markdown_scalar(item.get("latest_run_recommended_action") or "")
    if latest_run_action:
        lines.append(f"  - latest_run_recommended_action: {latest_run_action}")
    if authority_summary:
        lines.append(f"  - authority_material: {authority_summary}")


def _attention_queue_item_project_asset(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("project_asset") if isinstance(item.get("project_asset"), dict) else {}


def _attention_queue_item_agent_lane_next_action(
    item: dict[str, Any],
    project_asset: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(project_asset.get("agent_lane_next_action"), dict):
        return project_asset["agent_lane_next_action"]
    if isinstance(item.get("agent_lane_next_action"), dict):
        return item["agent_lane_next_action"]
    return {}


def _attention_queue_item_agent_lane_frontier_hint(
    item: dict[str, Any],
    project_asset: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(project_asset.get("agent_lane_frontier_hint"), dict):
        return project_asset["agent_lane_frontier_hint"]
    if isinstance(item.get("agent_lane_frontier_hint"), dict):
        return item["agent_lane_frontier_hint"]
    return {}


def attention_queue_goal_todo_scope_suffix(item: dict[str, Any]) -> str:
    project_asset = _attention_queue_item_project_asset(item)
    return (
        " scope=goal_all_agents"
        if _attention_queue_item_agent_lane_next_action(item, project_asset)
        else ""
    )


def _attention_queue_item_goal_frontier(
    item: dict[str, Any],
    project_asset: dict[str, Any],
) -> dict[str, Any]:
    if isinstance(project_asset.get("goal_frontier_projection"), dict):
        return project_asset["goal_frontier_projection"]
    if isinstance(item.get("goal_frontier_projection"), dict):
        return item["goal_frontier_projection"]
    return {}


def _append_project_asset_agent_lane_markdown(
    lines: list[str],
    item: dict[str, Any],
    project_asset: dict[str, Any],
    *,
    agent_lane_next_action: dict[str, Any],
    agent_lane_frontier_hint: dict[str, Any],
) -> None:
    if agent_lane_next_action:
        agent = markdown_scalar(agent_lane_next_action.get("agent_id") or "")
        todo_id = markdown_scalar(agent_lane_next_action.get("todo_id") or "")
        selected_by = markdown_scalar(agent_lane_next_action.get("selected_by") or "")
        confidence = markdown_scalar(agent_lane_next_action.get("confidence") or "")
        action = markdown_scalar(agent_lane_next_action.get("text") or "")
        lines.append(
            "    - current_agent_todo: "
            f"agent={agent} todo_id={todo_id} selected_by={selected_by} "
            f"confidence={confidence} source=agent_lane_next_action action={action}"
        )
    if agent_lane_frontier_hint:
        lines.append(
            "    - agent_lane_frontier_hint: "
            f"agent={markdown_scalar(agent_lane_frontier_hint.get('agent_id') or '')} "
            f"decision={markdown_scalar(agent_lane_frontier_hint.get('decision') or '')} "
            f"source={markdown_scalar(agent_lane_frontier_hint.get('source') or '')} "
            f"reason_code={markdown_scalar(agent_lane_frontier_hint.get('reason_code') or '')} "
            f"target_todo_id={markdown_scalar(agent_lane_frontier_hint.get('target_todo_id') or '')}"
        )

    goal_frontier = _attention_queue_item_goal_frontier(item, project_asset)
    if not goal_frontier:
        return
    remaining = (
        goal_frontier.get("remaining_advancement_frontier")
        if isinstance(goal_frontier.get("remaining_advancement_frontier"), dict)
        else {}
    )
    deferred_successors = (
        goal_frontier.get("deferred_successors")
        if isinstance(goal_frontier.get("deferred_successors"), dict)
        else {}
    )
    acceptance_gaps = (
        goal_frontier.get("acceptance_gaps")
        if isinstance(goal_frontier.get("acceptance_gaps"), list)
        else []
    )
    lines.append(
        "    - goal_frontier_projection: "
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
            "    - vision_continuation_audit: "
            f"required={vision_audit.get('required')} "
            f"decision={markdown_scalar(vision_audit.get('decision') or '')} "
            f"trigger_count={vision_audit.get('trigger_count')}"
        )
        vision_gap_judge = as_dict(vision_audit.get("vision_gap_judge"))
        if vision_gap_judge:
            lines.append(
                "    - vision_gap_judge: "
                f"done={vision_gap_judge.get('done')} "
                f"decision={markdown_scalar(vision_gap_judge.get('decision') or '')}"
            )
    vision_wait_state = as_dict(goal_frontier.get("vision_wait_state"))
    if vision_wait_state:
        lines.append(
            "    - vision_wait_state: "
            f"state={markdown_scalar(vision_wait_state.get('state') or '')} "
            f"todo_id={markdown_scalar(vision_wait_state.get('selected_todo_id') or '')} "
            f"resume_when={markdown_scalar(vision_wait_state.get('resume_when') or '')} "
            f"automatic_resume={vision_wait_state.get('automatic_resume')}"
        )


def _append_project_asset_runtime_policy_markdown(
    lines: list[str],
    project_asset: dict[str, Any],
) -> None:
    dreaming_lane_badge = (
        project_asset.get("dreaming_lane_badge")
        if isinstance(project_asset.get("dreaming_lane_badge"), dict)
        else {}
    )
    if dreaming_lane_badge:
        lines.append(
            "    - dreaming_lane_badge: "
            f"lane={markdown_scalar(dreaming_lane_badge.get('lane') or '')} "
            f"status={markdown_scalar(dreaming_lane_badge.get('status') or '')} "
            f"proposal_id={markdown_scalar(dreaming_lane_badge.get('proposal_id') or '')} "
            f"advisory={dreaming_lane_badge.get('advisory')} "
            f"interrupts_delivery={dreaming_lane_badge.get('interrupts_delivery')} "
            f"execution_allowed={dreaming_lane_badge.get('execution_allowed')}"
        )

    asset_execution_profile = (
        project_asset.get("execution_profile")
        if isinstance(project_asset.get("execution_profile"), dict)
        else None
    )
    if asset_execution_profile:
        lines.append(
            "    - execution_profile: "
            f"{markdown_scalar(execution_profile_summary(asset_execution_profile))}"
        )
    long_task_cadence_hint = (
        project_asset.get("long_task_cadence_hint")
        if isinstance(project_asset.get("long_task_cadence_hint"), dict)
        else None
    )
    if long_task_cadence_hint:
        lines.append(
            "    - long_task_cadence_hint: "
            f"{markdown_scalar(long_task_cadence_hint_summary(long_task_cadence_hint))}"
        )
    asset_orchestration = (
        project_asset.get("orchestration")
        if isinstance(project_asset.get("orchestration"), dict)
        else None
    )
    if asset_orchestration:
        lines.append(
            "    - orchestration: "
            f"{markdown_scalar(orchestration_policy_summary(asset_orchestration))}"
        )

    subagent_activity = (
        project_asset.get("subagent_activity")
        if isinstance(project_asset.get("subagent_activity"), dict)
        else {}
    )
    if not subagent_activity:
        return
    lines.append(
        "    - subagent_activity: "
        f"children={subagent_activity.get('child_count')} "
        f"visible={subagent_activity.get('visible_child_count')} "
        f"active={subagent_activity.get('active_count')} "
        f"completed={subagent_activity.get('completed_count')} "
        f"quota_slots={subagent_activity.get('quota_spend_slots')}"
    )
    for child in (subagent_activity.get("items") or [])[:3]:
        if not isinstance(child, dict):
            continue
        role = markdown_scalar(child.get("agent_role") or "subagent")
        state = markdown_scalar(child.get("state") or "unknown")
        run_id = markdown_scalar(child.get("run_id") or "")
        parent_run_id = markdown_scalar(child.get("parent_run_id") or "")
        lines.append(
            f"      - child_run: role={role} state={state} "
            f"run_id={run_id} parent_run_id={parent_run_id}"
        )


def append_attention_queue_project_asset_markdown(
    lines: list[str],
    item: dict[str, Any],
    *,
    goal_todo_scope_suffix: str = "",
) -> None:
    project_asset = _attention_queue_item_project_asset(item)
    agent_lane_next_action = _attention_queue_item_agent_lane_next_action(item, project_asset)
    agent_lane_frontier_hint = _attention_queue_item_agent_lane_frontier_hint(
        item,
        project_asset,
    )
    lines.append(
        "  - project_asset_source: "
        + (
            "project_asset"
            if project_asset
            else "legacy/raw fallback; owner/gate/stop are not project_asset-backed"
        )
    )
    if not project_asset:
        return

    lines.append(
        "  - project_asset: "
        f"owner={markdown_scalar(project_asset.get('owner') or '')} "
        f"gate={markdown_scalar(project_asset.get('gate') or '')} "
        f"stop={markdown_scalar(project_asset.get('stop_condition') or '')}"
    )
    asset_next_action = markdown_scalar(project_asset.get("next_action") or "")
    if asset_next_action:
        lines.append(f"    - asset_next_action: {asset_next_action}")
    asset_active_next_action = markdown_scalar(project_asset.get("active_state_next_action") or "")
    if asset_active_next_action:
        lines.append(f"    - asset_active_state_next_action: {asset_active_next_action}")
    asset_latest_run_action = markdown_scalar(project_asset.get("latest_run_recommended_action") or "")
    if asset_latest_run_action:
        lines.append(f"    - asset_latest_run_recommended_action: {asset_latest_run_action}")

    agent_lane_recommendation = (
        project_asset.get("agent_lane_recommendation")
        if isinstance(project_asset.get("agent_lane_recommendation"), dict)
        else {}
    )
    if agent_lane_recommendation:
        lane = markdown_scalar(agent_lane_recommendation.get("agent_lane") or "")
        agent = markdown_scalar(agent_lane_recommendation.get("agent_id") or "")
        recommendation = markdown_scalar(agent_lane_recommendation.get("recommended_action") or "")
        lines.append(
            "    - agent_lane_recommendation: "
            f"agent={agent} lane={lane} action={recommendation}"
        )

    agent_member = (
        project_asset.get("agent_member")
        if isinstance(project_asset.get("agent_member"), dict)
        else item.get("agent_member")
        if isinstance(item.get("agent_member"), dict)
        else {}
    )
    if agent_member:
        current_claims = ",".join(
            markdown_scalar(claim)
            for claim in (agent_member.get("current_claims") or [])
            if str(claim or "").strip()
        )
        member_fields = [
            f"agent={markdown_scalar(agent_member.get('agent_id') or '')}",
            f"agent_model={markdown_scalar(agent_member.get('agent_model') or '')}",
        ]
        if agent_member.get("profile_role"):
            member_fields.append(f"profile_role={markdown_scalar(agent_member['profile_role'])}")
        if agent_member.get("scope_summary"):
            member_fields.append(f"scope={markdown_scalar(agent_member['scope_summary'])}")
        if agent_member.get("vision_requirement"):
            member_fields.append(
                f"vision_requirement={markdown_scalar(agent_member['vision_requirement'])}"
            )
        member_fields.extend(
            [
                f"claims={markdown_scalar(current_claims)}",
                f"handoff_assignment={markdown_scalar(agent_member.get('handoff_assignment_status') or '')}",
                f"source={markdown_scalar(agent_member.get('profile_source') or '')}",
                "authority=advisory_projection",
            ]
        )
        lines.append("    - agent_member: " + " ".join(member_fields))

    agent_interaction = (
        project_asset.get("agent_interaction_summary")
        if isinstance(project_asset.get("agent_interaction_summary"), dict)
        else item.get("agent_interaction_summary")
        if isinstance(item.get("agent_interaction_summary"), dict)
        else {}
    )
    if agent_interaction:
        user_open_count = agent_interaction.get("user_open_count")
        open_count_part = (
            f"open_count={user_open_count} "
            if user_open_count is not None
            else ""
        )
        lines.append(
            "    - current_agent_interaction: "
            f"agent={markdown_scalar(agent_interaction.get('agent_id') or '')} "
            f"mode={markdown_scalar(agent_interaction.get('mode') or '')} "
            f"action_required={agent_interaction.get('user_action_required')} "
            f"{open_count_part}"
            f"notify={markdown_scalar(agent_interaction.get('user_notify') or '')} "
            f"must_attempt={agent_interaction.get('agent_must_attempt')} "
            f"delivery_allowed={agent_interaction.get('delivery_allowed')} "
            f"quiet_noop_allowed={agent_interaction.get('quiet_noop_allowed')} "
            f"spend_after_validation={agent_interaction.get('spend_after_validation')}"
        )

    append_agent_reward_memory_markdown(lines, item, project_asset)

    _append_project_asset_agent_lane_markdown(
        lines,
        item,
        project_asset,
        agent_lane_next_action=agent_lane_next_action,
        agent_lane_frontier_hint=agent_lane_frontier_hint,
    )
    _append_project_asset_runtime_policy_markdown(lines, project_asset)

    append_project_asset_todo_quota_markdown(
        lines,
        project_asset,
        goal_todo_scope_suffix=goal_todo_scope_suffix,
    )
    append_project_asset_warning_markdown(lines, project_asset, item)
    append_project_asset_session_runtime_markdown(lines, project_asset)
    handoff_readiness = (
        item.get("handoff_readiness")
        if isinstance(item.get("handoff_readiness"), dict)
        else {}
    )
    append_handoff_readiness_markdown(lines, handoff_readiness)


def append_attention_queue_item_operational_markdown(
    lines: list[str],
    item: dict[str, Any],
    *,
    goal_todo_scope_suffix: str = "",
) -> None:
    user_todos = item.get("user_todos") if isinstance(item.get("user_todos"), dict) else {}
    if user_todos:
        todo_parts = [
            f"open={user_todos.get('open_count')}",
            f"done={user_todos.get('done_count')}",
            f"total={user_todos.get('total_count')}",
        ]
        if user_todos.get("claimed_open_count"):
            todo_parts.insert(1, f"claimed={user_todos.get('claimed_open_count')}")
            todo_parts.insert(2, f"unclaimed={user_todos.get('unclaimed_open_count', 0)}")
        lines.append(f"  - user_todos: {' '.join(todo_parts)}")
        for todo in user_todos.get("items") or []:
            if not isinstance(todo, dict) or todo.get("done"):
                continue
            claimed = todo.get("claimed_by")
            claim_suffix = f" claimed_by={markdown_scalar(claimed)}" if claimed else ""
            lines.append(f"    - next_user_todo: {markdown_scalar(todo.get('text') or '')}{claim_suffix}")
            for material in todo.get("review_materials") or []:
                if not isinstance(material, dict):
                    continue
                lines.append(
                    "      - review_material: "
                    f"{markdown_scalar(material.get('label') or material.get('path') or '')} "
                    f"exists={material.get('exists')}"
                )
            break

    agent_todos = item.get("agent_todos") if isinstance(item.get("agent_todos"), dict) else {}
    if agent_todos:
        todo_parts = [
            f"open={agent_todos.get('open_count')}",
            f"done={agent_todos.get('done_count')}",
            f"total={agent_todos.get('total_count')}",
        ]
        if agent_todos.get("claimed_open_count"):
            todo_parts.insert(1, f"claimed={agent_todos.get('claimed_open_count')}")
            todo_parts.insert(2, f"unclaimed={agent_todos.get('unclaimed_open_count', 0)}")
        lines.append(f"  - agent_todos: {' '.join(todo_parts)}")
        for todo in agent_todos.get("items") or []:
            if not isinstance(todo, dict) or todo.get("done"):
                continue
            claimed = todo.get("claimed_by")
            claim_suffix = f" claimed_by={markdown_scalar(claimed)}" if claimed else ""
            lines.append(
                f"    - next_agent_todo: "
                f"{markdown_scalar(todo.get('text') or '')}"
                f"{claim_suffix}{goal_todo_scope_suffix}"
            )
            break

    dependency_blockers = (
        item.get("dependency_blockers")
        if isinstance(item.get("dependency_blockers"), dict)
        else {}
    )
    if dependency_blockers:
        lines.append(
            "  - dependency_blockers: "
            f"open={dependency_blockers.get('open_count')} "
            f"source={markdown_scalar(dependency_blockers.get('source') or '')}"
        )
        for blocker in dependency_blockers.get("items") or []:
            if not isinstance(blocker, dict):
                continue
            lines.append(
                "    - dependency_user_todo: "
                f"goal={markdown_scalar(blocker.get('goal_id') or '')} "
                f"waiting_on={markdown_scalar(blocker.get('waiting_on') or '')} "
                f"text={markdown_scalar(blocker.get('text') or '')}"
            )

    quota = item.get("quota") if isinstance(item.get("quota"), dict) else {}
    if quota:
        lines.append(
            "  - quota: "
            f"compute={quota.get('compute')} "
            f"state={quota.get('state')} "
            f"slots={quota.get('spent_slots')}/{quota.get('allowed_slots')} "
            f"reason={quota.get('reason')}"
        )

    control_plane = item.get("control_plane") if isinstance(item.get("control_plane"), dict) else None
    if control_plane:
        lines.append(f"  - control_plane: {control_plane_policy_summary(control_plane)}")

    operator_question = item.get("operator_question")
    agent_command = item.get("agent_command")
    if operator_question:
        lines.append(f"  - operator_question: {operator_question}")
        if agent_command:
            goal_id = item.get("goal_id")
            lines.append(
                "  - operator_gate_dry_run: "
                f"`loopx operator-gate --goal-id {goal_id} --decision approve "
                '--reason-summary "<public-safe reason>" --dry-run`'
            )
    if agent_command:
        lines.append(f"  - agent_command: `{agent_command}`")

    gates = item.get("missing_gates") if isinstance(item.get("missing_gates"), list) else []
    gate_text = ", ".join(str(gate) for gate in gates if gate)
    controller_stage = item.get("controller_stage")
    next_condition = item.get("next_handoff_condition")
    if controller_stage or gate_text:
        lines.append(
            "  - gates: "
            f"stage={controller_stage or 'none'} "
            f"missing={gate_text or 'none'}"
        )
    if next_condition:
        lines.append(f"  - next_handoff_condition: {next_condition}")
