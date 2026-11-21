from __future__ import annotations

from collections.abc import Collection, Mapping

from ...control_plane import control_plane_policy_summary
from ...control_plane.projection_envelope_facts import render_projection_envelope_markdown
from ...control_plane.runtime.event_ledger import EVENT_LEDGER_CLASSES
from ...execution_profile import execution_profile_summary
from ...long_task_cadence import long_task_cadence_hint_summary
from ...orchestration import orchestration_policy_summary
from ..markdown import markdown_scalar
from .goal_acceptance_observation_markdown import append_goal_acceptance_observation_markdown
from .goal_artifact_lifecycle_markdown import append_goal_artifact_lifecycle_markdown
from .reward_memory_markdown import append_agent_reward_memory_markdown


def _map(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _as_dict(value: object) -> dict[str, object]:
    return {str(k): v for k, v in value.items()} if isinstance(value, Mapping) else {}


def _seq(value: object) -> list[object]:
    return list(value) if isinstance(value, list) else []


def _pick_map(
    primary: Mapping[str, object],
    fallback: Mapping[str, object],
    key: str,
) -> Mapping[str, object]:
    for source in (primary, fallback):
        value = source.get(key)
        if isinstance(value, Mapping):
            return value
    return {}


def _int(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float, str)):
        return int(value or 0)
    return 0


def _scalar(mapping: Mapping[str, object], key: str, default: str = "") -> str:
    return markdown_scalar(mapping.get(key) or default)


def _csv(mapping: Mapping[str, object], key: str) -> str:
    return markdown_scalar(",".join(str(item) for item in _seq(mapping.get(key))))


def _claim_suffix(claimed: object) -> str:
    return f" claimed_by={markdown_scalar(claimed)}" if claimed else ""


def _headline_parts(mapping: Mapping[str, object], fields: tuple[str, ...]) -> str:
    parts = [f"{field}={_scalar(mapping, field)}" for field in fields if mapping.get(field)]
    return " ".join(parts) if parts else "recorded=True"


def _append_scalar_fields(
    lines: list[str],
    mapping: Mapping[str, object],
    prefix: str,
    fields: tuple[str, ...],
) -> None:
    for field in fields:
        value = mapping.get(field)
        if value:
            lines.append(f"{prefix}{field}: {markdown_scalar(value)}")


def goals_by_id(payload: Mapping[str, object]) -> dict[str, dict[str, object]]:
    goals = _seq(_map(payload.get("run_history")).get("goals"))
    result: dict[str, dict[str, object]] = {}
    for goal in goals:
        if isinstance(goal, Mapping):
            goal_id = str(goal.get("id") or "")
            if goal_id:
                result[goal_id] = _as_dict(goal)
    return result


def authority_registry_markdown_summary(goal: Mapping[str, object] | None) -> str | None:
    registry = _map(_map(goal).get("authority_registry"))
    if not registry.get("declared"):
        return None
    materials = _int(registry, "project_material_count")
    topics = _int(registry, "topic_authority_count")
    if materials <= 0 and topics <= 0:
        return None
    return (
        f"entries={_int(registry, 'default_entries_present')}/{_int(registry, 'default_entry_count')} "
        f"topics={topics} "
        f"materials={materials} "
        f"repositories={_int(registry, 'project_material_repository_count')} "
        f"owner_review_required={_int(registry, 'project_material_owner_review_required_count')} "
        f"stale={_int(registry, 'project_material_stale_count')} "
        f"current_authority={_int(registry, 'project_material_current_authority_count')} "
        f"risk={_scalar(registry, 'conflict_risk', 'unknown')}"
    )


def append_status_overview_markdown(
    lines: list[str],
    payload: Mapping[str, object],
) -> None:
    lines.extend([
        "# LoopX Status",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- registry: `{payload.get('registry')}`",
        f"- runtime_root: `{payload.get('runtime_root')}`",
        f"- goals: `{payload.get('goal_count')}`",
        f"- runs: `{payload.get('run_count')}`",
        *render_projection_envelope_markdown(payload.get("projection_envelope")),
    ])
    status_contract = _map(payload.get("status_contract"))
    if status_contract:
        lines.append(
            "- status_contract: "
            f"schema_version={status_contract.get('schema_version')}, "
            f"minimum_dashboard_schema_version={status_contract.get('minimum_dashboard_schema_version')}, "
            f"producer={status_contract.get('producer')}"
        )
    if payload.get("goal_filter"):
        lines.append(f"- goal_filter: `{payload.get('goal_filter')}`")
    projection_scope = _map(payload.get("goal_projection")).get("scope")
    if projection_scope:
        lines.append(f"- activation_filter: `{projection_scope}`")
    contract = _map(payload.get("contract"))
    summary = _map(contract.get("summary"))
    lines.append(
        "- contract: "
        f"ok={contract.get('ok')}, "
        f"errors={summary.get('errors')}, "
        f"warnings={summary.get('warnings')}, "
        f"checks={summary.get('checks')}"
    )
    contract_errors = _seq(payload.get("contract_errors"))
    contract_warnings = _seq(payload.get("contract_warnings"))
    if contract_errors or contract_warnings:
        lines.extend(["", "## Status Contract Signals"])
        for label, entries, trunc_key, total_key in (
            ("error", contract_errors, "contract_errors_truncated", "contract_errors_total_count"),
            ("warning", contract_warnings, "contract_warnings_truncated", "contract_warnings_total_count"),
        ):
            lines.extend(f"- {label}: {item}" for item in entries)
            if payload.get(trunc_key):
                lines.append(f"- {trunc_key}: total={payload.get(total_key)}")


def append_status_contract_detail_sections_markdown(
    lines: list[str],
    contract: Mapping[str, object],
) -> None:
    for title, key in (("Errors", "errors"), ("Warnings", "warnings"), ("Checks", "checks")):
        entries = _seq(contract.get(key))
        if entries:
            lines.extend(["", f"## {title}"])
            lines.extend(f"- {entry}" for entry in entries)


def append_global_registry_summary_markdown(
    lines: list[str],
    global_registry: Mapping[str, object],
) -> None:
    summary = _map(global_registry.get("summary"))
    lines.append(
        "- global_registry: "
        f"available={global_registry.get('available')}, "
        f"ok={global_registry.get('ok')}, "
        f"findings={summary.get('findings')}, "
        f"high={summary.get('high')}, "
        f"action={summary.get('action')}, "
        f"info={summary.get('info')}"
    )


def append_runtime_projection_routes_markdown(
    lines: list[str],
    diagnostics: Mapping[str, object],
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
    global_registry: Mapping[str, object],
) -> None:
    findings = _seq(global_registry.get("findings"))
    if not findings:
        return
    lines.extend(["", "## Global Registry Findings"])
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        goal_ref = finding.get("goal_id") or finding.get("goal_ids") or "global"
        lines.append(
            f"- {finding.get('severity')} {finding.get('kind')} goal={goal_ref}: {finding.get('message')}"
        )
        if finding.get("recommended_action"):
            lines.append(f"  - action: {finding.get('recommended_action')}")


def append_human_reward_markdown(
    lines: list[str],
    goal_id: object,
    reward: Mapping[str, object],
) -> None:
    lines.append(
        f"    - human_reward: {_headline_parts(reward, ('recorded_at', 'actor_kind', 'decision', 'reward'))}"
    )
    _append_scalar_fields(lines, reward, "      - ", ("reason_summary", "follow_up"))
    lesson = _map(reward.get("lesson"))
    if lesson:
        lines.append(f"      - lesson: kind={_scalar(lesson, 'kind')} summary={_scalar(lesson, 'summary')}")
        for field in ("avoid", "prefer"):
            values = _seq(lesson.get(field))
            if values:
                lines.append(
                    f"        - lesson_{field}: " + ", ".join(markdown_scalar(v) for v in values[:5])
                )
    if goal_id:
        lines.append(f"      - project_agent_visibility: `loopx history --goal-id {markdown_scalar(goal_id)} --limit 3`")


def append_operator_gate_resume_contract_markdown(
    lines: list[str],
    contract: Mapping[str, object],
) -> None:
    lines.append(
        f"    - operator_gate_resume_contract: {_headline_parts(contract, ('version', 'gate_id', 'operator_decision'))}"
    )
    _append_scalar_fields(
        lines,
        contract,
        "      - ",
        (
            "latest_state_ref",
            "freshness_check",
            "precondition_check",
            "migration_or_rebase_result",
            "validation_after_resume",
        ),
    )


def append_run_history_markdown(lines: list[str], run_history: Mapping[str, object]) -> None:
    run_goals = _seq(run_history.get("goals"))
    lines.extend([
        "",
        "## Run History",
        f"- summary: goals={run_history.get('goal_count')}, runs={run_history.get('run_count')}",
    ])
    if not run_goals:
        lines.append("- none")
    for goal in run_goals:
        if not isinstance(goal, Mapping):
            continue
        lines.append(
            f"- `{goal.get('id')}`: "
            f"status={goal.get('status')} "
            f"phase={goal.get('lifecycle_phase')} "
            f"adapter={goal.get('adapter_kind')}:{goal.get('adapter_status')} "
            f"records={goal.get('raw_index_records')} "
            f"unique_runs={goal.get('unique_runs')}"
        )
        goal_dict = _as_dict(goal)
        append_goal_acceptance_observation_markdown(lines, goal_dict)
        append_goal_artifact_lifecycle_markdown(lines, goal_dict)
        quota = _map(goal.get("quota"))
        if quota:
            lines.append(
                f"  - quota: compute={quota.get('compute')} state={quota.get('state')} "
                f"slots={quota.get('spent_slots')}/{quota.get('allowed_slots')}"
            )
        latest_runs = _seq(goal.get("latest_runs"))
        if latest_runs and isinstance(latest_runs[0], Mapping):
            latest = latest_runs[0]
            reward = _map(latest.get("human_reward"))
            operator_gate = _map(latest.get("operator_gate"))
            readiness = _map(latest.get("controller_readiness"))
            reward_text = f" reward={reward.get('decision')}:{reward.get('reward')}" if reward else ""
            gate_text = f" operator_gate={operator_gate.get('gate')}:{operator_gate.get('decision')}" if operator_gate else ""
            readiness_text = f" readiness={readiness.get('classification')}" if readiness else ""
            lines.append(
                f"  - latest: {latest.get('generated_at')} "
                f"classification={latest.get('classification')} "
                f"phase={latest.get('lifecycle_phase')} "
                f"artifacts={latest.get('json_exists')}/{latest.get('markdown_exists')}"
                f"{reward_text}{gate_text}{readiness_text}"
            )
            if reward:
                append_human_reward_markdown(lines, goal.get("id"), reward)
            resume_contract = _map(latest.get("operator_gate_resume_contract"))
            if resume_contract:
                append_operator_gate_resume_contract_markdown(lines, resume_contract)


def event_class_count_text(counts: Mapping[str, object], event_classes: Collection[str]) -> str:
    return " ".join(f"{event_class}={counts.get(event_class, 0)}" for event_class in event_classes)


def append_event_ledger_summary_markdown(
    lines: list[str],
    event_ledger: Mapping[str, object],
    *,
    event_classes: Collection[str],
) -> None:
    totals = _map(event_ledger.get("totals"))
    if not event_ledger.get("available") or not totals:
        return
    by_24h = _map(totals.get("by_class_24h"))
    by_7d = _map(totals.get("by_class_7d"))
    lines.extend([
        "",
        "## Event Ledger Summary",
        f"- summary: source={_scalar(event_ledger, 'source')} "
        f"samples={event_ledger.get('sample_run_count')} "
        f"events_24h={totals.get('events_24h')} "
        f"events_7d={totals.get('events_7d')} "
        f"classes_24h={event_class_count_text(by_24h, event_classes)} "
        f"classes_7d={event_class_count_text(by_7d, event_classes)}",
    ])
    for goal in _seq(event_ledger.get("goals"))[:3]:
        if isinstance(goal, Mapping):
            lines.append(
                f"- `{_scalar(goal, 'goal_id')}`: "
                f"events_24h={goal.get('events_24h')} "
                f"events_7d={goal.get('events_7d')} "
                f"latest={_scalar(goal, 'latest_event_class')} "
                f"classes_24h={event_class_count_text(_map(goal.get('by_class_24h')), event_classes)}"
            )


def append_promotion_readiness_summary_markdown(
    lines: list[str],
    promotion_readiness: Mapping[str, object],
) -> None:
    if not promotion_readiness:
        return
    lines.extend([
        "",
        "## Promotion Readiness Summary",
        f"- summary: source={_scalar(promotion_readiness, 'source')} "
        f"available={promotion_readiness.get('available')} "
        f"samples={promotion_readiness.get('sample_run_count')} "
        f"freshness={_scalar(promotion_readiness, 'freshness_status')} "
        f"age_hours={promotion_readiness.get('age_hours')} "
        f"requires_readiness_run={promotion_readiness.get('requires_readiness_run')} "
        f"window_hours={promotion_readiness.get('freshness_window_hours')}",
        f"- latest: goal={_scalar(promotion_readiness, 'goal_id')} "
        f"generated_at={_scalar(promotion_readiness, 'generated_at')} "
        f"classification={_scalar(promotion_readiness, 'classification')} "
        f"outcome={_scalar(promotion_readiness, 'delivery_outcome')} "
        f"artifacts={promotion_readiness.get('json_exists')}/{promotion_readiness.get('markdown_exists')}",
    ])


def append_promotion_gate_markdown(
    lines: list[str],
    promotion_gate: Mapping[str, object],
) -> None:
    if not promotion_gate:
        return
    readiness = _map(promotion_gate.get("readiness"))
    lines.extend([
        "",
        "## Promotion Gate",
        f"- gate: state={_scalar(promotion_gate, 'gate_state')} "
        f"can_promote={promotion_gate.get('can_promote')} "
        f"should_warn={promotion_gate.get('should_warn')} "
        f"non_blocking={promotion_gate.get('non_blocking')} "
        f"freshness={_scalar(readiness, 'freshness_status')} "
        f"requires_readiness_run={readiness.get('requires_readiness_run')}",
        f"- latest: generated_at={_scalar(readiness, 'generated_at')} "
        f"age_hours={readiness.get('age_hours')} "
        f"action={_scalar(promotion_gate, 'recommended_action')}",
    ])
    if promotion_gate.get("warning_message"):
        lines.append(f"- warning: {_scalar(promotion_gate, 'warning_message')}")


def append_decision_freshness_summary_markdown(
    lines: list[str],
    decision_freshness: Mapping[str, object],
) -> None:
    summary = _map(decision_freshness.get("summary"))
    if not decision_freshness.get("available") or not summary:
        return
    lines.extend([
        "",
        "## Decision Freshness Summary",
        f"- summary: source={_scalar(decision_freshness, 'source')} "
        f"samples={decision_freshness.get('sample_run_count')} "
        f"window_days={decision_freshness.get('window_days')} "
        f"decisions={summary.get('decision_count')} "
        f"stale={summary.get('stale_count')} "
        f"rebase_required={summary.get('rebase_required_count')} "
        f"fresh={summary.get('fresh_count')}",
    ])
    for item in _seq(decision_freshness.get("items"))[:3]:
        if isinstance(item, Mapping):
            lines.append(
                f"- `{_scalar(item, 'goal_id')}`: "
                f"kind={_scalar(item, 'decision_kind')} "
                f"state={_scalar(item, 'freshness_state')} "
                f"age_days={item.get('age_days')} "
                f"newer_7d={item.get('newer_event_count_7d')} "
                f"decision_at={_scalar(item, 'decision_at')}"
            )


def append_usage_summary_markdown(lines: list[str], usage: Mapping[str, object]) -> None:
    totals = _map(usage.get("totals"))
    if not usage.get("available") or not totals:
        return
    lines.extend([
        "",
        "## Usage Summary",
        f"- summary: source={_scalar(usage, 'source')} "
        f"samples={usage.get('sample_run_count')} "
        f"runs_24h={totals.get('runs_24h')} "
        f"runs_7d={totals.get('runs_7d')} "
        f"quota_slots_24h={totals.get('quota_spend_slots_24h')} "
        f"quota_slots_7d={totals.get('quota_spend_slots_7d')} "
        f"automation_24h={totals.get('automation_run_count_24h')} "
        f"automation_7d={totals.get('automation_run_count_7d')} "
        f"progress_signals_24h={totals.get('progress_signal_run_count_24h')} "
        f"progress_signals_7d={totals.get('progress_signal_run_count_7d')}",
    ])
    for goal in _seq(usage.get("goals"))[:3]:
        if isinstance(goal, Mapping):
            lines.append(
                f"- `{_scalar(goal, 'goal_id')}`: "
                f"runs_24h={goal.get('runs_24h')} "
                f"runs_7d={goal.get('runs_7d')} "
                f"quota_slots_24h={goal.get('quota_spend_slots_24h')} "
                f"automation_24h={goal.get('automation_run_count_24h')} "
                f"progress_signals_24h={goal.get('progress_signal_run_count_24h')} "
                f"share_24h={goal.get('project_share_24h')}"
            )


def append_project_asset_warning_markdown(
    lines: list[str],
    project_asset: Mapping[str, object],
    item: Mapping[str, object],
) -> None:
    stale_warn = _map(project_asset.get("stale_latest_run_warning"))
    if stale_warn:
        lines.append(
            "    - stale_latest_run_warning: "
            f"requires_refresh_state={stale_warn.get('requires_refresh_state')} "
            f"active_state_updated_at={_scalar(stale_warn, 'active_state_updated_at')} "
            f"latest_run_generated_at={_scalar(stale_warn, 'latest_run_generated_at')} "
            f"reason={_scalar(stale_warn, 'reason')}"
        )
    next_warn = _pick_map(project_asset, item, "next_action_projection_warning")
    if next_warn:
        lines.append(
            "    - next_action_projection_warning: "
            f"requires_state_writeback={next_warn.get('requires_state_writeback')} "
            f"severity={_scalar(next_warn, 'severity')} "
            f"reason={_scalar(next_warn, 'reason')}"
        )
    backlog_warn = _map(project_asset.get("backlog_hygiene_warning"))
    if backlog_warn:
        lines.append(
            "    - backlog_hygiene_warning: "
            f"requires_agent_todo={backlog_warn.get('requires_agent_todo')} "
            f"evidence_count={backlog_warn.get('evidence_count')} "
            f"source_sections={_csv(backlog_warn, 'source_sections')}"
        )
    proj_gap = _map(project_asset.get("state_projection_gap"))
    if proj_gap:
        lines.append(
            "    - state_projection_gap: "
            f"requires_todo_expansion={proj_gap.get('requires_todo_expansion')} "
            f"user_open={proj_gap.get('user_open_count')} "
            f"agent_open={proj_gap.get('agent_open_count')} "
            f"target_roles={_csv(proj_gap, 'target_roles')}"
        )
    todo_gap = _map(project_asset.get("todo_projection_gap"))
    if todo_gap:
        lines.append(
            f"    - todo_projection_gap: missing_roles={_csv(todo_gap, 'missing_roles')} source={_scalar(todo_gap, 'source')}"
        )
    archive_warn = _map(project_asset.get("completed_todo_archive_warning"))
    if archive_warn:
        lines.append(
            "    - completed_todo_archive_warning: "
            f"requires_archive={archive_warn.get('requires_archive')} "
            f"active_done={archive_warn.get('active_done_count')} "
            f"max_active_done={archive_warn.get('max_active_done_count')} "
            f"default_archive_keep={archive_warn.get('default_archive_keep_count')} "
            f"archive_section={_scalar(archive_warn, 'archive_section')} "
            f"archive_command={_scalar(archive_warn, 'archive_command_template')}"
        )
    replan = _map(project_asset.get("autonomous_replan_obligation"))
    if replan:
        trigger_kinds = [
            str(trigger.get("kind") or "")
            for trigger in _seq(replan.get("triggers"))
            if isinstance(trigger, Mapping) and trigger.get("kind")
        ]
        lines.append(
            "    - autonomous_replan_obligation: "
            f"required={replan.get('required')} "
            f"trigger_count={replan.get('trigger_count')} "
            f"triggers={markdown_scalar(','.join(trigger_kinds))}"
        )
    research = _map(project_asset.get("bounded_research_frontier"))
    if research:
        lines.append(
            "    - research_execution: "
            f"state={_scalar(research, 'state')} "
            f"pending={research.get('pending_count')} scheduled={research.get('scheduled_count')} "
            f"observed={research.get('observed_count')} ineligible={research.get('ineligible_count')} "
            f"dismissed={research.get('dismissed_count')} deferred={research.get('deferred_count')}"
        )
        for gap in _seq(research.get("gaps")):
            gap_map = _map(gap)
            lines.append(f"      - {_scalar(gap_map, 'gap_id')}: {_scalar(gap_map, 'status')}")
    cadence = _map(project_asset.get("interface_budget_cadence"))
    if cadence:
        lines.append(
            "    - interface_budget_cadence: "
            f"overdue={cadence.get('overdue')} "
            f"within_budget={cadence.get('within_budget')} "
            f"checked_at={_scalar(cadence, 'checked_at')} "
            f"next_check_due_at={_scalar(cadence, 'next_check_due_at')} "
            f"tightest={_scalar(cadence, 'tightest_surface')}/{_scalar(cadence, 'tightest_metric')} "
            f"headroom={cadence.get('headroom_remaining')} "
            f"recommendation={_scalar(cadence, 'recommendation')}"
        )
    latest_val = _map(project_asset.get("latest_validation"))
    if latest_val:
        lines.append(
            f"    - latest_validation: classification={_scalar(latest_val, 'classification')} at={_scalar(latest_val, 'generated_at')}"
        )


def append_project_asset_session_runtime_markdown(
    lines: list[str],
    project_asset: Mapping[str, object],
) -> None:
    session_projection = _map(project_asset.get("session_runtime_projection"))
    if not session_projection:
        return
    first_screen = _map(session_projection.get("first_screen"))
    boundary = _map(session_projection.get("boundary"))
    source = _map(session_projection.get("source"))
    lines.append(
        "    - session_runtime_projection: "
        f"waiting_on={_scalar(first_screen, 'waiting_on')} "
        f"agent_can_continue={first_screen.get('agent_can_continue')} "
        f"user_action_required={first_screen.get('user_action_required')} "
        f"gate={_scalar(first_screen, 'gate_state')} "
        f"raw_material_detected={boundary.get('raw_material_detected')} "
        f"runtime_writeback_allowed={boundary.get('runtime_writeback_allowed')} "
        f"host={_scalar(source, 'host_kind')}"
    )
    if first_screen.get("first_user_todo"):
        lines.append(f"      - session_runtime_user_todo: {_scalar(first_screen, 'first_user_todo')}")
    if first_screen.get("first_agent_todo"):
        lines.append(f"      - session_runtime_agent_todo: {_scalar(first_screen, 'first_agent_todo')}")


def append_handoff_readiness_markdown(
    lines: list[str],
    handoff_readiness: Mapping[str, object],
) -> None:
    if not handoff_readiness:
        return
    lines.append(
        "    - handoff_readiness: "
        f"ready={handoff_readiness.get('ready')} "
        f"codex_ready={handoff_readiness.get('codex_ready')} "
        f"source={_scalar(handoff_readiness, 'source')} "
        f"quota_state={_scalar(handoff_readiness, 'quota_state')}"
    )
    budget = _map(handoff_readiness.get("handoff_interface_budget"))
    if budget:
        lines.append(
            "      - handoff_interface_budget: "
            f"mode={_scalar(budget, 'mode')} "
            f"max_lines={budget.get('max_lines')} "
            f"max_chars={budget.get('max_chars')}"
        )
    checks = _map(handoff_readiness.get("checks"))
    if checks:
        passed = [str(key) for key, value in checks.items() if value]
        failed = [str(key) for key, value in checks.items() if not value]
        lines.append(
            f"      - handoff_checks: pass={','.join(passed) or '-'} fail={','.join(failed) or '-'}"
        )
    lines.append(
        "      - handoff_state: "
        f"status={_scalar(handoff_readiness, 'handoff_status')} "
        f"post_handoff_run_seen={handoff_readiness.get('post_handoff_run_seen')} "
        f"ready_at={_scalar(handoff_readiness, 'handoff_ready_at')}"
    )
    latest_run = _map(handoff_readiness.get("post_handoff_latest_run"))
    if latest_run:
        outcome_suffix = f" outcome={_scalar(latest_run, 'delivery_outcome')}" if latest_run.get("delivery_outcome") else ""
        turn_kind_suffix = f" turn_kind={_scalar(latest_run, 'delivery_turn_kind')}" if latest_run.get("delivery_turn_kind") else ""
        lines.append(
            "      - post_handoff_run: "
            f"classification={_scalar(latest_run, 'classification')} "
            f"at={_scalar(latest_run, 'generated_at')} "
            f"scale={_scalar(latest_run, 'delivery_batch_scale')}"
            f"{outcome_suffix}{turn_kind_suffix}"
        )
    recent_runs = [run for run in _seq(handoff_readiness.get("post_handoff_recent_runs")) if isinstance(run, Mapping)]
    recent_scales = [markdown_scalar(str(run.get("delivery_batch_scale") or "")) for run in recent_runs]
    if recent_scales:
        recent_outcomes = [markdown_scalar(str(run.get("delivery_outcome") or "")) for run in recent_runs if run.get("delivery_outcome")]
        recent_turn_kinds = [markdown_scalar(str(run.get("delivery_turn_kind") or "")) for run in recent_runs if run.get("delivery_turn_kind")]
        outcome_text = f" outcome={','.join(recent_outcomes)}" if recent_outcomes else ""
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
            f"{outcome_text}{turn_kind_text}{gap_text}"
        )
    if handoff_readiness.get("next_probe"):
        lines.append(f"      - handoff_probe: `{_scalar(handoff_readiness, 'next_probe')}`")


def _append_asset_todo_group(
    lines: list[str],
    todos: Mapping[str, object],
    *,
    label: str,
    scope_suffix: str,
) -> None:
    if todos.get("next"):
        claim = _claim_suffix(todos.get("next_claimed_by"))
        lines.append(f"      - {label}: {_scalar(todos, 'next')}{claim}{scope_suffix}")
    for todo in _seq(todos.get("items"))[1:3]:
        if isinstance(todo, Mapping) and todo.get("text"):
            index = todo.get("index")
            suffix = f"[{index}]" if index is not None else ""
            claim = _claim_suffix(todo.get("claimed_by"))
            lines.append(f"      - {label}{suffix}: {_scalar(todo, 'text')}{claim}{scope_suffix}")


def append_project_asset_todo_quota_markdown(
    lines: list[str],
    project_asset: Mapping[str, object],
    *,
    goal_todo_scope_suffix: str = "",
) -> None:
    user_todos = _map(project_asset.get("user_todos"))
    agent_todos = _map(project_asset.get("agent_todos"))
    if user_todos or agent_todos:
        todo_parts: list[str] = []
        for prefix, todos in (("user", user_todos), ("agent", agent_todos)):
            if todos:
                todo_parts.append(f"{prefix}_open={todos.get('open')}")
                if todos.get("claimed_open_count"):
                    todo_parts.append(f"{prefix}_claimed={todos.get('claimed_open_count')}")
        lines.append(f"    - asset_todos: {' '.join(todo_parts)}")
        _append_asset_todo_group(lines, user_todos, label="asset_user_todo", scope_suffix="")
        _append_asset_todo_group(lines, agent_todos, label="asset_agent_todo", scope_suffix=goal_todo_scope_suffix)
    asset_quota = _map(project_asset.get("quota"))
    if asset_quota:
        lines.append(
            "    - asset_quota: "
            f"compute={asset_quota.get('compute')} "
            f"state={asset_quota.get('state')} "
            f"slots={asset_quota.get('spent_slots')}/{asset_quota.get('allowed_slots')}"
        )


def append_attention_queue_summary_markdown(
    lines: list[str],
    queue: Mapping[str, object],
) -> None:
    lines.extend([
        "",
        "## Attention Queue",
        "- summary: "
        f"items={queue.get('item_count')}, "
        f"needs_user_or_controller={queue.get('needs_user_or_controller')}, "
        f"needs_controller={queue.get('needs_controller')}, "
        f"needs_codex={queue.get('needs_codex')}, "
        f"watching_external_evidence={queue.get('watching_external_evidence')}, "
        f"watching_monitor={queue.get('watching_monitor')}",
    ])
    append_attention_queue_candidate_group_markdown(
        lines,
        _map(queue.get("autonomous_backlog_candidates")),
        group_name="autonomous_backlog_candidates",
        item_name="autonomous_candidate",
    )
    append_attention_queue_candidate_group_markdown(
        lines,
        _map(queue.get("autonomous_monitor_candidates")),
        group_name="autonomous_monitor_candidates",
        item_name="autonomous_monitor_candidate",
    )


def render_status_markdown(
    payload: Mapping[str, object],
    *,
    event_classes: Collection[str] = EVENT_LEDGER_CLASSES,
) -> str:
    lines: list[str] = []
    append_status_overview_markdown(lines, payload)
    contract = _map(payload.get("contract"))
    global_registry = _map(payload.get("global_registry"))
    append_global_registry_summary_markdown(lines, global_registry)
    append_runtime_projection_routes_markdown(lines, _map(payload.get("runtime_projection_routes")))
    append_event_ledger_summary_markdown(lines, _map(payload.get("event_ledger_summary")), event_classes=event_classes)
    append_promotion_readiness_summary_markdown(lines, _map(payload.get("promotion_readiness_summary")))
    append_promotion_gate_markdown(lines, _map(payload.get("promotion_gate")))
    append_decision_freshness_summary_markdown(lines, _map(payload.get("decision_freshness_summary")))
    append_usage_summary_markdown(lines, _map(payload.get("usage_summary")))

    queue = _map(payload.get("attention_queue"))
    append_attention_queue_summary_markdown(lines, queue)
    items = _seq(queue.get("items"))
    goals = goals_by_id(payload)
    if not items:
        lines.append("- none")
    for item in items:
        if not isinstance(item, Mapping):
            continue
        authority_summary = authority_registry_markdown_summary(goals.get(str(item.get("goal_id") or "")))
        append_attention_queue_item_header_markdown(lines, item, authority_summary=authority_summary)
        scope_suffix = attention_queue_goal_todo_scope_suffix(item)
        append_attention_queue_project_asset_markdown(lines, item, goal_todo_scope_suffix=scope_suffix)
        append_attention_queue_item_operational_markdown(lines, item, goal_todo_scope_suffix=scope_suffix)

    append_run_history_markdown(lines, _map(payload.get("run_history")))
    append_status_contract_detail_sections_markdown(lines, contract)
    append_global_registry_findings_markdown(lines, global_registry)
    return "\n".join(lines)


def append_attention_queue_candidate_group_markdown(
    lines: list[str],
    candidates: Mapping[str, object],
    *,
    group_name: str,
    item_name: str,
) -> None:
    if not candidates:
        return
    lines.append(
        f"- {group_name}: open={candidates.get('open_count')} "
        f"task_class={_scalar(candidates, 'task_class')} source={_scalar(candidates, 'source')}"
    )
    for candidate in _seq(candidates.get("items")):
        if not isinstance(candidate, Mapping):
            continue
        priority_text = f" priority={_scalar(candidate, 'priority')}" if candidate.get("priority") else ""
        lines.append(
            f"  - {item_name}: goal={_scalar(candidate, 'goal_id')}{priority_text} text={_scalar(candidate, 'text')}"
        )


def append_attention_queue_item_header_markdown(
    lines: list[str],
    item: Mapping[str, object],
    *,
    authority_summary: str | None = None,
) -> None:
    lines.append(
        f"- `{item.get('goal_id')}`: "
        f"status={item.get('status')} "
        f"phase={item.get('lifecycle_phase')} "
        f"waiting_on={item.get('waiting_on')} "
        f"severity={item.get('severity')} "
        f"source={item.get('source')}"
    )
    action = _scalar(item, "recommended_action")
    if action:
        lines.append(f"  - action: {action}")
    active_state_action = _scalar(item, "active_state_next_action")
    if active_state_action:
        lines.append(f"  - active_state_next_action: {active_state_action}")
    for route in _seq(item.get("agent_next_actions")):
        if isinstance(route, Mapping):
            lines.append(
                f"  - agent_next_action: {_scalar(route, 'agent_id')} {_scalar(route, 'todo_id')} {_scalar(route, 'text')}"
            )
            if route.get("next_step"):
                lines.append(f"    - next_step: {markdown_scalar(route['next_step'])}")
    latest_run_action = _scalar(item, "latest_run_recommended_action")
    if latest_run_action:
        lines.append(f"  - latest_run_recommended_action: {latest_run_action}")
    if authority_summary:
        lines.append(f"  - authority_material: {authority_summary}")


def attention_queue_goal_todo_scope_suffix(item: Mapping[str, object]) -> str:
    project_asset = _map(item.get("project_asset"))
    return " scope=goal_all_agents" if _pick_map(project_asset, item, "agent_lane_next_action") else ""


def _append_project_asset_agent_lane_markdown(
    lines: list[str],
    item: Mapping[str, object],
    project_asset: Mapping[str, object],
    *,
    agent_lane_next_action: Mapping[str, object],
    agent_lane_frontier_hint: Mapping[str, object],
) -> None:
    if agent_lane_next_action:
        lines.append(
            "    - current_agent_todo: "
            f"agent={_scalar(agent_lane_next_action, 'agent_id')} "
            f"todo_id={_scalar(agent_lane_next_action, 'todo_id')} "
            f"selected_by={_scalar(agent_lane_next_action, 'selected_by')} "
            f"confidence={_scalar(agent_lane_next_action, 'confidence')} "
            f"source=agent_lane_next_action action={_scalar(agent_lane_next_action, 'text')}"
        )
        if agent_lane_next_action.get("next_step"):
            lines.append(f"      - next_step: {markdown_scalar(agent_lane_next_action['next_step'])}")
    if agent_lane_frontier_hint:
        lines.append(
            "    - agent_lane_frontier_hint: "
            f"agent={_scalar(agent_lane_frontier_hint, 'agent_id')} "
            f"decision={_scalar(agent_lane_frontier_hint, 'decision')} "
            f"source={_scalar(agent_lane_frontier_hint, 'source')} "
            f"reason_code={_scalar(agent_lane_frontier_hint, 'reason_code')} "
            f"target_todo_id={_scalar(agent_lane_frontier_hint, 'target_todo_id')}"
        )
    goal_frontier = _pick_map(project_asset, item, "goal_frontier_projection")
    if not goal_frontier:
        return
    remaining = _map(goal_frontier.get("remaining_advancement_frontier"))
    deferred = _map(goal_frontier.get("deferred_successors"))
    acceptance_gaps = _seq(goal_frontier.get("acceptance_gaps"))
    lines.append(
        "    - goal_frontier_projection: "
        f"replan_required={goal_frontier.get('replan_required')} "
        f"current_agent_advancement={remaining.get('current_agent_claimed_advancement_count')} "
        f"unclaimed_advancement={remaining.get('unclaimed_advancement_count')} "
        f"other_agent_advancement={remaining.get('other_agent_claimed_advancement_count')} "
        f"deferred_ready={deferred.get('ready_count')} "
        f"acceptance_gaps={len(acceptance_gaps)}"
    )
    vision_audit = _map(goal_frontier.get("vision_continuation_audit"))
    if vision_audit:
        lines.append(
            "    - vision_continuation_audit: "
            f"required={vision_audit.get('required')} "
            f"decision={_scalar(vision_audit, 'decision')} "
            f"trigger_count={vision_audit.get('trigger_count')}"
        )
        gap_judge = _map(vision_audit.get("vision_gap_judge"))
        if gap_judge:
            lines.append(
                f"    - vision_gap_judge: done={gap_judge.get('done')} decision={_scalar(gap_judge, 'decision')}"
            )
    vision_wait = _map(goal_frontier.get("vision_wait_state"))
    if vision_wait:
        lines.append(
            "    - vision_wait_state: "
            f"state={_scalar(vision_wait, 'state')} "
            f"todo_id={_scalar(vision_wait, 'selected_todo_id')} "
            f"resume_when={_scalar(vision_wait, 'resume_when')} "
            f"automatic_resume={vision_wait.get('automatic_resume')}"
        )


def _append_project_asset_runtime_policy_markdown(
    lines: list[str],
    project_asset: Mapping[str, object],
) -> None:
    badge = _map(project_asset.get("dreaming_lane_badge"))
    if badge:
        lines.append(
            "    - dreaming_lane_badge: "
            f"lane={_scalar(badge, 'lane')} status={_scalar(badge, 'status')} "
            f"proposal_id={_scalar(badge, 'proposal_id')} advisory={badge.get('advisory')} "
            f"interrupts_delivery={badge.get('interrupts_delivery')} execution_allowed={badge.get('execution_allowed')}"
        )
    exec_profile = _map(project_asset.get("execution_profile"))
    if exec_profile:
        lines.append(f"    - execution_profile: {markdown_scalar(execution_profile_summary(_as_dict(exec_profile)))}")
    cadence_hint = _map(project_asset.get("long_task_cadence_hint"))
    if cadence_hint:
        lines.append(f"    - long_task_cadence_hint: {markdown_scalar(long_task_cadence_hint_summary(cadence_hint))}")
    orchestration = _map(project_asset.get("orchestration"))
    if orchestration:
        lines.append(f"    - orchestration: {markdown_scalar(orchestration_policy_summary(_as_dict(orchestration)))}")
    native_child = _map(project_asset.get("native_child_activity"))
    if native_child.get("observation") in {"coordinator_reported", "host_observed", "mixed"}:
        lines.append(
            "    - native_child_activity: "
            f"turn={markdown_scalar(native_child.get('turn_instance_id'))} "
            f"source={native_child.get('observation')} "
            f"host_attested={str(native_child.get('host_attested')).lower()} "
            f"configured_max={native_child.get('configured_limit')} "
            f"starts={native_child.get('launched_count')} "
            f"skips={native_child.get('skipped_count')} "
            f"capacity_rejections={native_child.get('capacity_rejected_count')} "
            f"host_failures={native_child.get('host_failed_count')} "
            f"parent_accepted={native_child.get('parent_accepted_count')}"
        )
    subagent = _map(project_asset.get("subagent_activity"))
    if not subagent:
        return
    lines.append(
        "    - subagent_activity: "
        f"children={subagent.get('child_count')} "
        f"visible={subagent.get('visible_child_count')} "
        f"active={subagent.get('active_count')} "
        f"completed={subagent.get('completed_count')} "
        f"quota_slots={subagent.get('quota_spend_slots')}"
    )
    for child in _seq(subagent.get("items"))[:3]:
        if isinstance(child, Mapping):
            lines.append(
                f"      - child_run: role={_scalar(child, 'agent_role', 'subagent')} "
                f"state={_scalar(child, 'state', 'unknown')} "
                f"run_id={_scalar(child, 'run_id')} parent_run_id={_scalar(child, 'parent_run_id')}"
            )


def append_attention_queue_project_asset_markdown(
    lines: list[str],
    item: Mapping[str, object],
    *,
    goal_todo_scope_suffix: str = "",
) -> None:
    project_asset = _map(item.get("project_asset"))
    lines.append(
        "  - project_asset_source: "
        + ("project_asset" if project_asset else "legacy/raw fallback; owner/gate/stop are not project_asset-backed")
    )
    if not project_asset:
        return
    lines.append(
        f"  - project_asset: owner={_scalar(project_asset, 'owner')} "
        f"gate={_scalar(project_asset, 'gate')} stop={_scalar(project_asset, 'stop_condition')}"
    )
    for field, label in (
        ("next_action", "asset_next_action"),
        ("active_state_next_action", "asset_active_state_next_action"),
        ("latest_run_recommended_action", "asset_latest_run_recommended_action"),
    ):
        text = _scalar(project_asset, field)
        if text:
            lines.append(f"    - {label}: {text}")
    lane_rec = _map(project_asset.get("agent_lane_recommendation"))
    if lane_rec:
        lines.append(
            f"    - agent_lane_recommendation: agent={_scalar(lane_rec, 'agent_id')} "
            f"lane={_scalar(lane_rec, 'agent_lane')} action={_scalar(lane_rec, 'recommended_action')}"
        )
    agent_member = _pick_map(project_asset, item, "agent_member")
    if agent_member:
        claims = ",".join(
            markdown_scalar(claim) for claim in _seq(agent_member.get("current_claims")) if str(claim or "").strip()
        )
        member_fields = [
            f"agent={_scalar(agent_member, 'agent_id')}",
            f"agent_model={_scalar(agent_member, 'agent_model')}",
        ]
        for field, key in (("profile_role", "profile_role"), ("scope", "scope_summary"), ("vision_requirement", "vision_requirement")):
            if agent_member.get(key):
                member_fields.append(f"{field}={markdown_scalar(agent_member[key])}")
        member_fields.extend([
            f"claims={markdown_scalar(claims)}",
            f"handoff_assignment={_scalar(agent_member, 'handoff_assignment_status')}",
            f"source={_scalar(agent_member, 'profile_source')}",
            "authority=advisory_projection",
        ])
        lines.append("    - agent_member: " + " ".join(member_fields))
    interaction = _pick_map(project_asset, item, "agent_interaction_summary")
    if interaction:
        user_open_count = interaction.get("user_open_count")
        open_count_part = f"open_count={user_open_count} " if user_open_count is not None else ""
        lines.append(
            "    - current_agent_interaction: "
            f"agent={_scalar(interaction, 'agent_id')} "
            f"mode={_scalar(interaction, 'mode')} "
            f"action_required={interaction.get('user_action_required')} "
            f"{open_count_part}"
            f"notify={_scalar(interaction, 'user_notify')} "
            f"must_attempt={interaction.get('agent_must_attempt')} "
            f"delivery_allowed={interaction.get('delivery_allowed')} "
            f"quiet_noop_allowed={interaction.get('quiet_noop_allowed')} "
            f"spend_after_validation={interaction.get('spend_after_validation')}"
        )
    append_agent_reward_memory_markdown(lines, _as_dict(item), _as_dict(project_asset))
    _append_project_asset_agent_lane_markdown(
        lines,
        item,
        project_asset,
        agent_lane_next_action=_pick_map(project_asset, item, "agent_lane_next_action"),
        agent_lane_frontier_hint=_pick_map(project_asset, item, "agent_lane_frontier_hint"),
    )
    _append_project_asset_runtime_policy_markdown(lines, project_asset)
    append_project_asset_todo_quota_markdown(lines, project_asset, goal_todo_scope_suffix=goal_todo_scope_suffix)
    append_project_asset_warning_markdown(lines, project_asset, item)
    append_project_asset_session_runtime_markdown(lines, project_asset)
    append_handoff_readiness_markdown(lines, _map(item.get("handoff_readiness")))


def _append_operational_todos(
    lines: list[str],
    todos: Mapping[str, object],
    *,
    kind: str,
    scope_suffix: str = "",
    include_materials: bool = False,
) -> None:
    if not todos:
        return
    todo_parts = [
        f"open={todos.get('open_count')}",
        f"done={todos.get('done_count')}",
        f"total={todos.get('total_count')}",
    ]
    if todos.get("claimed_open_count"):
        todo_parts.insert(1, f"claimed={todos.get('claimed_open_count')}")
        todo_parts.insert(2, f"unclaimed={todos.get('unclaimed_open_count', 0)}")
    lines.append(f"  - {kind}_todos: {' '.join(todo_parts)}")
    for todo in _seq(todos.get("items")):
        if not isinstance(todo, Mapping) or todo.get("done"):
            continue
        lines.append(
            f"    - next_{kind}_todo: {_scalar(todo, 'text')}{_claim_suffix(todo.get('claimed_by'))}{scope_suffix}"
        )
        if include_materials:
            for material in _seq(todo.get("review_materials")):
                if isinstance(material, Mapping):
                    label = markdown_scalar(material.get("label") or material.get("path") or "")
                    lines.append(f"      - review_material: {label} exists={material.get('exists')}")
        break


def append_attention_queue_item_operational_markdown(
    lines: list[str],
    item: Mapping[str, object],
    *,
    goal_todo_scope_suffix: str = "",
) -> None:
    _append_operational_todos(lines, _map(item.get("user_todos")), kind="user", include_materials=True)
    _append_operational_todos(lines, _map(item.get("agent_todos")), kind="agent", scope_suffix=goal_todo_scope_suffix)
    blockers = _map(item.get("dependency_blockers"))
    if blockers:
        lines.append(f"  - dependency_blockers: open={blockers.get('open_count')} source={_scalar(blockers, 'source')}")
        for blocker in _seq(blockers.get("items")):
            if isinstance(blocker, Mapping):
                lines.append(
                    f"    - dependency_user_todo: goal={_scalar(blocker, 'goal_id')} "
                    f"waiting_on={_scalar(blocker, 'waiting_on')} text={_scalar(blocker, 'text')}"
                )
    quota = _map(item.get("quota"))
    if quota:
        lines.append(
            f"  - quota: compute={quota.get('compute')} state={quota.get('state')} "
            f"slots={quota.get('spent_slots')}/{quota.get('allowed_slots')} reason={quota.get('reason')}"
        )
    control_plane = _map(item.get("control_plane"))
    if control_plane:
        lines.append(f"  - control_plane: {control_plane_policy_summary(control_plane)}")
    operator_question = item.get("operator_question")
    agent_command = item.get("agent_command")
    if operator_question:
        lines.append(f"  - operator_question: {operator_question}")
        if agent_command:
            lines.append(
                f"  - operator_gate_dry_run: `loopx operator-gate --goal-id {item.get('goal_id')} --decision approve "
                '--reason-summary "<public-safe reason>" --dry-run`'
            )
    if agent_command:
        lines.append(f"  - agent_command: `{agent_command}`")
    gate_text = ", ".join(str(gate) for gate in _seq(item.get("missing_gates")) if gate)
    controller_stage = item.get("controller_stage")
    if controller_stage or gate_text:
        lines.append(f"  - gates: stage={controller_stage or 'none'} missing={gate_text or 'none'}")
    if item.get("next_handoff_condition"):
        lines.append(f"  - next_handoff_condition: {item.get('next_handoff_condition')}")
