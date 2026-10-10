from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any

from .control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    render_scheduler_execution_args,
)
from .control_plane.status.contract_projection import STATUS_CONTRACT_SIGNAL_LIMIT
from .control_plane.goals.orphaned_goal_state import (
    ORPHANED_GOAL_STATE_CONNECTION,
    ORPHANED_GOAL_STATE_REASON,
    orphaned_goal_state_gate,
    orphaned_goal_state_projection,
)
from .control_plane.todos.contract import normalize_required_capabilities
from .paths import registry_project_root
from .presentation.markdown import as_dict as _as_dict
from .presentation.markdown import as_list as _as_list
from .presentation.markdown import markdown_scalar
from .project_prompt import render_available_capability_args
from .quota import build_quota_should_run
from .status import collect_status

DIAGNOSIS_SCHEMA_VERSION = "loopx_agent_diagnosis_packet_v0"
PACKET_KIND = "agent_reasoning_evidence_packet"
ORPHANED_GOAL_STATE_RECOMMENDED_ACTION = (
    "inspect the listed orphaned state and preview its backup; do not bootstrap "
    "until an operator explicitly resolves it"
)


USER_DIAGNOSE_PROMPT = (
    "请诊断这个项目的 LoopX 运转情况。不要让我手动跑 shell 命令；如果 "
    "`loopx` 不在 PATH，请先安装/修复。然后运行 `loopx diagnose` "
    "读取诊断证据包，结合项目上下文自己判断：它是否可以自主推进、当前卡在哪、"
    "需要我回答什么、以及你下一步会做什么。"
)


AGENT_REASONING_CHECKLIST = [
    "判断 LoopX 本身是否健康：registry/status/quota 是否能读，是否存在 stale projection 或 contract error。",
    "判断当前 goal 是否已连接：若没有 registry goal 或 attention item，先接入而不是猜任务。",
    "判断是否存在 user/controller gate：开放 user todo、operator_question、interaction_contract.user_channel 都要纳入判断。",
    "判断是否可自主推进：只有在用户 gate 不阻塞所选路径、quota 允许、goal_boundary 允许、且有明确 agent todo/recommended_action 时才推进。",
    "判断是否应先自修复：state_projection_gap、boundary_projection_gap、stale action、todo 投影缺口优先于普通 delivery。",
    "向用户汇报时给出自己的结论，不要把 machine_signals 当成最终裁决；引用具体 evidence 字段说明理由。",
]


def _compact_text_signals(value: Any, *, limit: int = STATUS_CONTRACT_SIGNAL_LIMIT) -> dict[str, Any]:
    items = [str(item).strip() for item in _as_list(value) if str(item).strip()]
    bounded = items[: max(0, limit)]
    return {
        "items": bounded,
        "truncated": len(items) > len(bounded),
        "total_count": len(items),
    }


def _first_text(summary: dict[str, Any]) -> str | None:
    for key in ("first_open_items", "items", "backlog_items"):
        for item in _as_list(summary.get(key)):
            if isinstance(item, dict) and item.get("text"):
                return str(item.get("text"))
    if summary.get("next"):
        return str(summary.get("next"))
    return None


def _first_agent_todo_text(quota: dict[str, Any], summary: dict[str, Any]) -> str | None:
    selected = _as_dict(quota.get("agent_lane_next_action"))
    if selected.get("text"):
        return str(selected.get("text"))
    if selected.get("title"):
        return str(selected.get("title"))
    for item in _as_list(summary.get("first_executable_items")):
        if isinstance(item, dict) and item.get("text"):
            return str(item.get("text"))
    return _first_text(summary)


def _open_count(summary: dict[str, Any]) -> int:
    for key in ("open_count", "open"):
        value = summary.get(key)
        if isinstance(value, int):
            return value
    return len(_as_list(summary.get("first_open_items")) or _as_list(summary.get("items")))


def _attention_items(status_payload: dict[str, Any]) -> list[dict[str, Any]]:
    queue = _as_dict(status_payload.get("attention_queue"))
    return [item for item in _as_list(queue.get("items")) if isinstance(item, dict)]


def _select_attention_item(
    status_payload: dict[str, Any],
    *,
    goal_id: str | None,
) -> dict[str, Any] | None:
    items = _attention_items(status_payload)
    if goal_id:
        for item in items:
            if str(item.get("goal_id") or "") == goal_id:
                return item
        return None
    return items[0] if items else None


def _goal_ids_for_packet(status_payload: dict[str, Any], *, goal_id: str | None, limit: int) -> list[str]:
    if goal_id:
        return [goal_id]
    ids: list[str] = []
    for item in _attention_items(status_payload):
        candidate = str(item.get("goal_id") or "").strip()
        if candidate and candidate not in ids:
            ids.append(candidate)
        if len(ids) >= max(1, limit):
            break
    return ids


def _orphaned_goal_state_for_diagnosis(
    status_payload: dict[str, Any],
    *,
    registry_path: Path,
    goal_id: str,
) -> dict[str, Any] | None:
    """Detect an orphan only when the status snapshot says the Goal is unregistered."""

    history = _as_dict(status_payload.get("run_history"))
    for goal in _as_list(history.get("goals")):
        if not isinstance(goal, dict) or str(goal.get("id") or "") != goal_id:
            continue
        if goal.get("registry_member") is not False:
            return None
        return orphaned_goal_state_projection(
            registry_project_root(registry_path),
            goal_id,
        )
    return None


def _quota_for_goal(
    status_payload: dict[str, Any],
    goal_id: str,
    *,
    agent_id: str | None,
    available_capabilities: Any = None,
) -> dict[str, Any]:
    try:
        return build_quota_should_run(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
            scheduler_execution_context=(
                GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
            ),
        )
    except Exception as exc:  # noqa: BLE001 - diagnosis packets should preserve compact failure context.
        return {
            "ok": False,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "error": str(exc),
            "should_run": False,
            "state": "diagnosis_quota_failed",
            "reason": str(exc),
        }


def _todo_summary(quota: dict[str, Any], item: dict[str, Any], *, role: str) -> dict[str, Any]:
    key = f"{role}_todo_summary"
    fallback = f"{role}_todos"
    return _as_dict(quota.get(key)) or _as_dict(item.get(fallback))


def _interaction_user_required(quota: dict[str, Any]) -> bool:
    contract = _as_dict(quota.get("interaction_contract"))
    user_channel = _as_dict(contract.get("user_channel"))
    if user_channel.get("action_required") is not None:
        return bool(user_channel.get("action_required"))
    return bool(quota.get("requires_user_action") or quota.get("notify_user_on_gate"))


def _quota_user_action_required(quota: dict[str, Any]) -> bool:
    if quota.get("action_required") is not None:
        return bool(quota.get("action_required"))
    return _interaction_user_required(quota)


def _quota_user_open_count(quota: dict[str, Any]) -> int:
    value = quota.get("open_count")
    if isinstance(value, int):
        return value
    return _open_count(_as_dict(quota.get("user_todo_summary")))


def _machine_signal(*, status_payload: dict[str, Any], item: dict[str, Any] | None, quota: dict[str, Any]) -> str:
    if not status_payload.get("ok"):
        return "status_health_attention"
    if item is None:
        return "not_connected_or_not_projected"
    if not quota.get("ok", True):
        return "quota_attention"
    user_summary = _todo_summary(quota, item, role="user")
    if _quota_user_action_required(quota) or _open_count(user_summary) > 0:
        return "user_or_controller_attention"
    if quota.get("capability_repair_allowed"):
        return "capability_repair_attention"
    if quota.get("self_repair_allowed"):
        return "self_repair_attention"
    if quota.get("recovery_delivery_allowed"):
        return "recovery_attention"
    if quota.get("should_run") and quota.get("normal_delivery_allowed", True):
        return "agent_work_attention"
    if quota.get("safe_bypass_allowed"):
        return "safe_bypass_attention"
    if item.get("waiting_on") == "external_evidence":
        return "external_evidence_attention"
    return "no_immediate_agent_delivery_signal"


def _first_user_question(quota: dict[str, Any], item: dict[str, Any]) -> str | None:
    for key in ("operator_question", "gate_prompt"):
        if quota.get(key):
            return str(quota.get(key))
        if item.get(key):
            return str(item.get(key))
    return _first_text(_todo_summary(quota, item, role="user"))


def _agent_commands(
    *,
    goal_id: str | None,
    registry_path: Path,
    scan_roots: list[Path],
    limit: int,
    agent_id: str | None,
    available_capabilities: Any = None,
) -> list[str]:
    registry_arg = shlex.quote(str(registry_path))
    scan_args = " ".join(f"--scan-path {shlex.quote(str(path))}" for path in scan_roots)
    diagnose = f"loopx --registry {registry_arg} diagnose"
    status = f"loopx --registry {registry_arg} status"
    agent_arg = f" --agent-id {shlex.quote(agent_id)}" if agent_id else ""
    capability_args = render_available_capability_args(available_capabilities)
    scheduler_args = render_scheduler_execution_args(
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )
    goal_arg = f" --goal-id {shlex.quote(goal_id)}" if goal_id else ""
    if scan_args:
        diagnose = f"{diagnose} {scan_args}"
        status = f"{status} {scan_args}"
    commands = [
        f"{diagnose}{goal_arg}{agent_arg}{capability_args} --limit {max(1, limit)}",
        f"{status}{goal_arg}{agent_arg} --limit {max(1, limit)}",
    ]
    if goal_id:
        quoted_goal = shlex.quote(goal_id)
        commands.append(
            f"loopx --registry {registry_arg} --format json quota should-run "
            f"--goal-id {quoted_goal}{agent_arg}{capability_args}{scheduler_args}"
        )
        commands.append(f"loopx --registry {registry_arg} history --goal-id {quoted_goal} --limit {max(1, limit)}")
    return commands


def _compact_quota_signals(quota: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "ok",
        "decision",
        "should_run",
        "state",
        "effective_action",
        "actionable_by_codex",
        "normal_delivery_allowed",
        "recovery_delivery_allowed",
        "capability_repair_allowed",
        "self_repair_allowed",
        "safe_bypass_allowed",
        "safe_bypass_kind",
        "requires_user_action",
        "recommended_action",
        "reason",
        "agent_identity",
        "agent_lane_next_action",
        "agent_scoped_user_gate_override",
        "agent_scoped_user_action_override",
        "agent_scope_frontier",
        "goal_frontier_projection",
        "autonomous_replan_decision",
    )
    signals = {key: quota.get(key) for key in keys if key in quota}
    signals["action_required"] = _quota_user_action_required(quota)
    signals["open_count"] = _quota_user_open_count(quota)
    scheduler_hint = _compact_scheduler_hint(_as_dict(quota.get("scheduler_hint")))
    if scheduler_hint:
        signals["scheduler_hint"] = scheduler_hint
    return signals


def _compact_interaction_contract(contract: dict[str, Any]) -> dict[str, Any]:
    compact = dict(contract)
    agent_channel = _as_dict(contract.get("agent_channel"))
    cli_channel = _as_dict(contract.get("cli_channel"))
    for field in ("action_portfolio_ref", "suggested_action_count"):
        agent_channel.pop(field, None)
    for field in ("selection_policy_ref", "selection_command"):
        cli_channel.pop(field, None)
    compact["agent_channel"] = agent_channel
    compact["cli_channel"] = cli_channel
    return compact


def _goal_list_view(packet: dict[str, Any], *, selected: bool) -> dict[str, Any]:
    """Avoid repeating the selected work's source-read plan in the overview."""
    if not selected:
        return packet
    contract = _as_dict(packet.get("interaction_contract"))
    agent_channel = _as_dict(contract.get("agent_channel"))
    if not agent_channel.get("required_reads") and not agent_channel.get("work_context"):
        return packet
    overview_contract = dict(contract)
    overview_agent_channel = dict(agent_channel)
    overview_agent_channel.pop("required_reads", None)
    overview_agent_channel.pop("work_context", None)
    overview_contract["agent_channel"] = overview_agent_channel
    return {**packet, "interaction_contract": overview_contract}


def _compact_scheduler_hint(scheduler_hint: dict[str, Any]) -> dict[str, Any]:
    if not scheduler_hint:
        return {}
    canonical_app_automation = _as_dict(scheduler_hint.get("app_automation"))
    legacy_codex_app = _as_dict(scheduler_hint.get("codex_app"))
    app_automation = canonical_app_automation or legacy_codex_app
    app_key = "app_automation" if canonical_app_automation else "codex_app"
    stateful_backoff = _as_dict(app_automation.get("stateful_backoff"))
    reset_policy = _as_dict(scheduler_hint.get("reset_policy"))
    unchanged_poll = _as_dict(scheduler_hint.get("unchanged_poll"))
    compact = {
        "schema_version": "diagnose_scheduler_hint_summary_v0",
        "action": scheduler_hint.get("action"),
        "cadence_class": scheduler_hint.get("cadence_class"),
        "reason": scheduler_hint.get("reason"),
        app_key: {
            "apply": app_automation.get("apply"),
            "host_action": app_automation.get("host_action"),
            "recommended_rrule": app_automation.get("recommended_rrule"),
            "recommended_interval_minutes": app_automation.get("recommended_interval_minutes"),
            "current_rrule": stateful_backoff.get("current_rrule"),
            "apply_needed": stateful_backoff.get("apply_needed"),
            "no_spend_for_cadence_change": app_automation.get("no_spend_for_cadence_change"),
        },
        "unchanged_poll": {
            "final_quota_replan_check_enabled": unchanged_poll.get(
                "final_quota_replan_check_enabled"
            ),
            "final_quota_replan_check_action": unchanged_poll.get(
                "final_quota_replan_check_action"
            ),
        },
        "reset_policy": {
            "reset_token": reset_policy.get("reset_token"),
        },
    }
    reset_key = (
        "app_automation_initial_rrule"
        if canonical_app_automation
        else "codex_app_initial_rrule"
    )
    compact["reset_policy"][reset_key] = reset_policy.get(reset_key)
    return compact


def _goal_frontier_projection_line(goal_frontier: dict[str, Any]) -> str | None:
    if not goal_frontier:
        return None
    normalized_progress = (
        goal_frontier.get("normalized_progress")
        if isinstance(goal_frontier.get("normalized_progress"), dict)
        else {}
    )
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
    monitor_only_lanes = (
        goal_frontier.get("monitor_only_lanes")
        if isinstance(goal_frontier.get("monitor_only_lanes"), dict)
        else {}
    )
    acceptance_gaps = (
        goal_frontier.get("acceptance_gaps")
        if isinstance(goal_frontier.get("acceptance_gaps"), list)
        else []
    )
    autonomy_blockers = (
        goal_frontier.get("autonomy_blockers")
        if isinstance(goal_frontier.get("autonomy_blockers"), list)
        else []
    )
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


def _scheduler_hint_line(scheduler_hint: dict[str, Any]) -> str | None:
    if not scheduler_hint:
        return None
    canonical_app_automation = _as_dict(scheduler_hint.get("app_automation"))
    app_automation = canonical_app_automation or _as_dict(
        scheduler_hint.get("codex_app")
    )
    apply_label = (
        "app_automation_apply" if canonical_app_automation else "codex_app_apply"
    )
    unchanged_poll = _as_dict(scheduler_hint.get("unchanged_poll"))
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


def _build_goal_packet(
    *,
    status_payload: dict[str, Any],
    item: dict[str, Any] | None,
    quota: dict[str, Any],
    registry_path: Path,
    scan_roots: list[Path],
    limit: int,
    agent_id: str | None,
    available_capabilities: Any = None,
) -> dict[str, Any]:
    goal_id = str((item or {}).get("goal_id") or quota.get("goal_id") or "")
    item = item or {}
    user_summary = _todo_summary(quota, item, role="user")
    agent_summary = _todo_summary(quota, item, role="agent")
    machine_signal = _machine_signal(status_payload=status_payload, item=item or None, quota=quota)
    return {
        "goal_id": goal_id or None,
        "packet_role": "evidence_for_agent_reasoning",
        "machine_signal": machine_signal,
        "machine_signals_are_not_final_verdict": True,
        "status": item.get("status"),
        "waiting_on": item.get("waiting_on"),
        "severity": item.get("severity"),
        "recommended_action": quota.get("recommended_action") or item.get("recommended_action"),
        "user_question": _first_user_question(quota, item),
        "todo_evidence": {
            "user_open_count": _open_count(user_summary),
            "agent_open_count": _open_count(agent_summary),
            "first_user_todo": _first_text(user_summary),
            "first_agent_todo": _first_agent_todo_text(quota, agent_summary),
        },
        "quota_signals": _compact_quota_signals(quota),
        "agent_id": agent_id,
        "available_capabilities": normalize_required_capabilities(available_capabilities),
        "interaction_contract": _compact_interaction_contract(
            _as_dict(quota.get("interaction_contract"))
        ),
        "work_lane_contract": _as_dict(quota.get("work_lane_contract")),
        "goal_boundary": _as_dict(quota.get("goal_boundary")),
        "projection_warnings": {
            "state_projection_gap": _as_dict(quota.get("state_projection_gap")),
            "state_action_projection_warning": _as_dict(quota.get("state_action_projection_warning")),
            "backlog_hygiene_warning": _as_dict(quota.get("backlog_hygiene_warning")),
            "autonomous_replan_obligation": _as_dict(quota.get("autonomous_replan_obligation")),
        },
        "agent_reasoning_checklist": AGENT_REASONING_CHECKLIST,
        "agent_commands": _agent_commands(
            goal_id=goal_id or None,
            registry_path=registry_path,
            scan_roots=scan_roots,
            limit=limit,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
        ),
    }


def _apply_orphaned_goal_state_diagnosis(
    packet: dict[str, Any],
    projection: dict[str, Any],
    *,
    registry_path: Path,
) -> None:
    commands = _as_list(packet.get("agent_commands"))
    status_command = str(commands[1]) if len(commands) > 1 else "loopx status"
    gate = orphaned_goal_state_gate(
        projection,
        project=str(registry_project_root(registry_path)),
        command_prefix="loopx",
        status_command=status_command,
    )
    packet.update(
        {
            "machine_signal": ORPHANED_GOAL_STATE_CONNECTION,
            "status": "blocked",
            "waiting_on": "operator",
            "severity": "high",
            "recommended_action": ORPHANED_GOAL_STATE_RECOMMENDED_ACTION,
            "user_question": (
                "Resolve the orphaned Goal state before reconnecting this goal id."
            ),
            "orphaned_goal_state": gate,
            "quota_signals": {
                "ok": False,
                "decision": "skip",
                "should_run": False,
                "state": ORPHANED_GOAL_STATE_CONNECTION,
                "action_required": True,
                "requires_user_action": True,
                "open_count": 0,
                "reason": ORPHANED_GOAL_STATE_REASON,
                "recommended_action": ORPHANED_GOAL_STATE_RECOMMENDED_ACTION,
            },
            "agent_commands": [
                str(route.get("command"))
                for route in gate["resolution_routes"]
                if isinstance(route, dict) and route.get("command")
            ],
        }
    )


def collect_diagnosis(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    scan_roots: list[Path],
    limit: int,
    goal_id: str | None = None,
    agent_id: str | None = None,
    available_capabilities: Any = None,
) -> dict[str, Any]:
    limit = max(1, limit)
    registry_path = registry_path.expanduser()
    status_payload = collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_override,
        scan_roots=scan_roots,
        limit=limit,
        goal_id=goal_id,
        available_capabilities=available_capabilities,
    )
    goal_ids = _goal_ids_for_packet(status_payload, goal_id=goal_id, limit=limit)
    item_by_goal = {
        str(item.get("goal_id") or ""): item
        for item in _attention_items(status_payload)
        if item.get("goal_id")
    }
    goal_packets = []
    for current_goal_id in goal_ids:
        item = item_by_goal.get(current_goal_id)
        quota = _quota_for_goal(
            status_payload,
            current_goal_id,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
        )
        packet = _build_goal_packet(
            status_payload=status_payload,
            item=item,
            quota=quota,
            registry_path=registry_path,
            scan_roots=scan_roots,
            limit=limit,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
        )
        orphaned_goal_state = _orphaned_goal_state_for_diagnosis(
            status_payload,
            registry_path=registry_path,
            goal_id=current_goal_id,
        )
        if orphaned_goal_state is not None:
            _apply_orphaned_goal_state_diagnosis(
                packet,
                orphaned_goal_state,
                registry_path=registry_path,
            )
        goal_packets.append(packet)
    selected_item = _select_attention_item(status_payload, goal_id=goal_id)
    selected_goal_id = str((selected_item or {}).get("goal_id") or (goal_ids[0] if goal_ids else ""))
    selected = next((item for item in goal_packets if item.get("goal_id") == selected_goal_id), None)
    if selected is None:
        selected = _build_goal_packet(
            status_payload=status_payload,
            item=selected_item,
            quota={"ok": True, "goal_id": selected_goal_id, "agent_id": agent_id, "should_run": False},
            registry_path=registry_path,
            scan_roots=scan_roots,
            limit=limit,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
        )
    contract = _as_dict(status_payload.get("contract"))
    contract_errors = _compact_text_signals(contract.get("errors"))
    contract_warnings = _compact_text_signals(contract.get("warnings"))
    diagnosis_blocked = any(
        item.get("machine_signal") == ORPHANED_GOAL_STATE_CONNECTION
        for item in goal_packets
    )
    goal_overviews = [
        _goal_list_view(packet, selected=packet is selected)
        for packet in goal_packets
    ]
    payload = {
        "ok": bool(status_payload.get("ok")) and not diagnosis_blocked,
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "packet_kind": PACKET_KIND,
        "agent_must_reason": True,
        "registry": str(registry_path),
        "runtime_root": status_payload.get("runtime_root"),
        "goal_id": goal_id,
        "agent_id": agent_id,
        "available_capabilities": normalize_required_capabilities(available_capabilities),
        "selected_goal_id": selected.get("goal_id"),
        "status_ok": status_payload.get("ok"),
        "goal_count": status_payload.get("goal_count"),
        "goal_packet_count": len(goal_packets),
        "run_count": status_payload.get("run_count"),
        "attention_item_count": _as_dict(status_payload.get("attention_queue")).get("item_count"),
        "selected": selected,
        "goals": goal_overviews,
        "status_summary": {
            "contract": contract.get("summary"),
            "contract_errors": contract_errors["items"],
            "contract_errors_total_count": contract_errors["total_count"],
            "contract_errors_truncated": contract_errors["truncated"],
            "contract_warnings": contract_warnings["items"],
            "contract_warnings_total_count": contract_warnings["total_count"],
            "contract_warnings_truncated": contract_warnings["truncated"],
            "global_registry": _as_dict(_as_dict(status_payload.get("global_registry")).get("summary")),
        },
        "user_prompt": USER_DIAGNOSE_PROMPT,
    }
    if diagnosis_blocked:
        payload["diagnosis_blocked_by"] = ORPHANED_GOAL_STATE_CONNECTION
    return payload


def render_diagnosis_markdown(payload: dict[str, Any]) -> str:
    selected = _as_dict(payload.get("selected"))
    todo_evidence = _as_dict(selected.get("todo_evidence"))
    quota = _as_dict(selected.get("quota_signals"))
    status_summary = _as_dict(payload.get("status_summary"))
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
        f"- goal_packets: `{payload.get('goal_packet_count') or len(_as_list(payload.get('goals')))}`",
        f"- runs: `{payload.get('run_count')}`",
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
    if todo_evidence.get("first_user_todo"):
        lines.append(f"- first_user_todo: {markdown_scalar(todo_evidence.get('first_user_todo'))}")
    if todo_evidence.get("first_agent_todo"):
        lines.append(f"- first_agent_todo: {markdown_scalar(todo_evidence.get('first_agent_todo'))}")
    if quota:
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
            _as_dict(quota.get("goal_frontier_projection"))
        )
        if goal_frontier_line:
            lines.append(goal_frontier_line)
        scheduler_hint_line = _scheduler_hint_line(_as_dict(quota.get("scheduler_hint")))
        if scheduler_hint_line:
            lines.append(scheduler_hint_line)

    orphaned_goal_state = _as_dict(selected.get("orphaned_goal_state"))
    if orphaned_goal_state:
        lines.extend(
            [
                "",
                "## Orphaned Goal State",
                "",
                f"- reason: {markdown_scalar(orphaned_goal_state.get('reason'))}",
                "- state_file_routes: "
                + ", ".join(
                    f"`{route}`"
                    for route in _as_list(orphaned_goal_state.get("state_file_routes"))
                ),
                "- forbidden_until_resolved: "
                + ", ".join(
                    f"`{item}`"
                    for item in _as_list(
                        orphaned_goal_state.get("forbidden_until_resolved")
                    )
                ),
            ]
        )

    contract_errors = _as_list(status_summary.get("contract_errors"))
    contract_warnings = _as_list(status_summary.get("contract_warnings"))
    if contract_errors or contract_warnings:
        lines.extend(["", "## Status Contract Signals", ""])
        for item in contract_errors:
            lines.append(f"- contract_error: {markdown_scalar(item)}")
        if status_summary.get("contract_errors_truncated"):
            lines.append(
                f"- contract_errors_truncated: total={status_summary.get('contract_errors_total_count')}"
            )
        for item in contract_warnings:
            lines.append(f"- contract_warning: {markdown_scalar(item)}")
        if status_summary.get("contract_warnings_truncated"):
            lines.append(
                f"- contract_warnings_truncated: total={status_summary.get('contract_warnings_total_count')}"
            )

    checklist = _as_list(selected.get("agent_reasoning_checklist"))
    if checklist:
        lines.extend(["", "## Agent Reasoning Checklist", ""])
        for item in checklist:
            lines.append(f"- {markdown_scalar(item)}")

    commands = _as_list(selected.get("agent_commands"))
    if commands:
        lines.extend(["", "## Agent Commands", ""])
        lines.append("These are for the agent to run when it needs drill-down, not for the user to copy manually.")
        for command in commands:
            lines.append(f"- `{command}`")

    goals = _as_list(payload.get("goals"))
    if goals:
        lines.extend(["", "## Goal Packets", "", "| goal | machine signal | user todo | agent todo |", "| --- | --- | --- | --- |"])
        for goal in goals:
            if not isinstance(goal, dict):
                continue
            evidence = _as_dict(goal.get("todo_evidence"))
            lines.append(
                "| "
                f"`{goal.get('goal_id')}` | "
                f"`{goal.get('machine_signal')}` | "
                f"`{evidence.get('user_open_count')}` | "
                f"`{evidence.get('agent_open_count')}` |"
            )

    lines.extend(["", "## User Prompt", "", str(payload.get("user_prompt") or USER_DIAGNOSE_PROMPT)])
    return "\n".join(lines)
