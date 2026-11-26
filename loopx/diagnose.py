"""Agent-facing LoopX diagnosis packet builder."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
import shlex

from .control_plane.goals.orphaned_goal_state import (
    ORPHANED_GOAL_STATE_CONNECTION as ORPHANED_GOAL_STATE_CONNECTION,
    ORPHANED_GOAL_STATE_REASON,
    orphaned_goal_state_gate,
    orphaned_goal_state_projection,
)
from .control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    render_scheduler_execution_args,
)
from .control_plane.status.contract_projection import (
    STATUS_CONTRACT_SIGNAL_LIMIT as STATUS_CONTRACT_SIGNAL_LIMIT,
)
from .control_plane.status.diagnose_projection import (
    _as_items as _as_items,
    _as_mapping as _as_mapping,
    _attention_items as _attention_items,
    _compact_interaction_contract as _compact_interaction_contract,
    _compact_quota_signals as _compact_quota_signals,
    _compact_scheduler_hint as _compact_scheduler_hint,
    _compact_text_signals as _compact_text_signals,
    _first_agent_todo_text as _first_agent_todo_text,
    _first_text as _first_text,
    _first_user_question as _first_user_question,
    _goal_ids_for_packet as _goal_ids_for_packet,
    _goal_ids_from_items as _goal_ids_from_items,
    _goal_list_view as _goal_list_view,
    _interaction_user_required as _interaction_user_required,
    _machine_signal as _machine_signal,
    _open_count as _open_count,
    _quota_user_action_required as _quota_user_action_required,
    _quota_user_open_count as _quota_user_open_count,
    _select_attention_item as _select_attention_item,
    _todo_summary as _todo_summary,
    _unregistered_goal_ids as _unregistered_goal_ids,
)
from .control_plane.todos.contract import normalize_required_capabilities
from .paths import registry_project_root
from .presentation.renderers.diagnose_markdown import (
    USER_DIAGNOSE_PROMPT as USER_DIAGNOSE_PROMPT,
    _goal_frontier_projection_line as _goal_frontier_projection_line,
    _scheduler_hint_line as _scheduler_hint_line,
    render_diagnosis_markdown as render_diagnosis_markdown,
)
from .project_prompt import render_available_capability_args
from .quota import build_quota_should_run
from .status import collect_status

DIAGNOSIS_SCHEMA_VERSION = "loopx_agent_diagnosis_packet_v0"
PACKET_KIND = "agent_reasoning_evidence_packet"
ORPHANED_GOAL_STATE_RECOMMENDED_ACTION = (
    "inspect the listed orphaned state and preview its backup; do not bootstrap "
    "until an operator explicitly resolves it"
)
AGENT_REASONING_CHECKLIST = [
    "判断 LoopX 本身是否健康：registry/status/quota 是否能读，是否存在 stale projection 或 contract error。",
    "判断当前 goal 是否已连接：若没有 registry goal 或 attention item，先接入而不是猜任务。",
    "判断是否存在 user/controller gate：开放 user todo、operator_question、interaction_contract.user_channel 都要纳入判断。",
    "判断是否可自主推进：只有在用户 gate 不阻塞所选路径、quota 允许、goal_boundary 允许、且有明确 agent todo/recommended_action 时才推进。",
    "判断是否应先自修复：state_projection_gap、boundary_projection_gap、stale action、todo 投影缺口优先于普通 delivery。",
    "向用户汇报时给出自己的结论，不要把 machine_signals 当成最终裁决；引用具体 evidence 字段说明理由。",
]


def _orphaned_goal_state_for_diagnosis(
    status_payload: Mapping[str, object],
    *,
    registry_path: Path,
    goal_id: str,
) -> dict[str, object] | None:
    """Detect an orphan only when the status snapshot says the Goal is unregistered."""
    if goal_id not in _unregistered_goal_ids(status_payload):
        return None
    projection = orphaned_goal_state_projection(registry_project_root(registry_path), goal_id)
    return dict(projection) if projection is not None else None


def _quota_for_goal(
    status_payload: dict[str, object],
    goal_id: str,
    *,
    agent_id: str | None,
    available_capabilities: object = None,
) -> dict[str, object]:
    try:
        result = build_quota_should_run(
            status_payload,
            goal_id=goal_id,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
            scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
        )
        return dict(result)
    except Exception as exc:  # diagnosis packets should preserve compact failure context.
        return {
            "ok": False,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "error": str(exc),
            "should_run": False,
            "state": "diagnosis_quota_failed",
            "reason": str(exc),
        }


def _agent_commands(
    *,
    goal_id: str | None,
    registry_path: Path,
    limit: int,
    agent_id: str | None,
    available_capabilities: object = None,
    capability_args: str | None = None,
) -> list[str]:
    registry_arg = shlex.quote(str(registry_path))
    agent_arg = f" --agent-id {shlex.quote(agent_id)}" if agent_id else ""
    caps_arg = (
        capability_args
        if capability_args is not None
        else render_available_capability_args(available_capabilities)
    )
    scheduler_args = render_scheduler_execution_args(
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )
    goal_arg = f" --goal-id {shlex.quote(goal_id)}" if goal_id else ""
    bounded_limit = max(1, limit)
    commands = [
        f"loopx --registry {registry_arg} diagnose{goal_arg}{agent_arg}{caps_arg} --limit {bounded_limit}",
        f"loopx --registry {registry_arg} status{goal_arg}{agent_arg} --limit {bounded_limit}",
    ]
    if goal_id:
        quoted_goal = shlex.quote(goal_id)
        commands.append(
            f"loopx --registry {registry_arg} --format json quota should-run "
            f"--goal-id {quoted_goal}{agent_arg}{caps_arg}{scheduler_args}"
        )
        commands.append(
            f"loopx --registry {registry_arg} history --goal-id {quoted_goal} --limit {bounded_limit}"
        )
    return commands


def _build_goal_packet(
    *,
    status_payload: Mapping[str, object],
    item: Mapping[str, object] | None,
    quota: Mapping[str, object],
    registry_path: Path,
    limit: int,
    agent_id: str | None,
    available_capabilities: object = None,
    normalized_capabilities: Sequence[str] | None = None,
    capability_args: str | None = None,
) -> dict[str, object]:
    goal_id = str((item or {}).get("goal_id") or quota.get("goal_id") or "")
    item_map = item or {}
    user_summary = _todo_summary(quota, item_map, role="user")
    agent_summary = _todo_summary(quota, item_map, role="agent")
    capabilities = (
        list(normalized_capabilities)
        if normalized_capabilities is not None
        else normalize_required_capabilities(available_capabilities)
    )
    return {
        "goal_id": goal_id or None,
        "packet_role": "evidence_for_agent_reasoning",
        "machine_signal": _machine_signal(status_payload=status_payload, item=item or None, quota=quota),
        "machine_signals_are_not_final_verdict": True,
        "status": item_map.get("status"),
        "waiting_on": item_map.get("waiting_on"),
        "severity": item_map.get("severity"),
        "recommended_action": quota.get("recommended_action") or item_map.get("recommended_action"),
        "user_question": _first_user_question(quota, item_map),
        "todo_evidence": {
            "user_open_count": _open_count(user_summary),
            "agent_open_count": _open_count(agent_summary),
            "first_user_todo": _first_text(user_summary),
            "first_agent_todo": _first_agent_todo_text(quota, agent_summary),
        },
        "quota_signals": _compact_quota_signals(quota),
        "agent_id": agent_id,
        "available_capabilities": capabilities,
        "interaction_contract": _compact_interaction_contract(_as_mapping(quota.get("interaction_contract"))),
        "work_lane_contract": dict(_as_mapping(quota.get("work_lane_contract"))),
        "goal_boundary": dict(_as_mapping(quota.get("goal_boundary"))),
        "projection_warnings": {
            "state_projection_gap": dict(_as_mapping(quota.get("state_projection_gap"))),
            "state_action_projection_warning": dict(_as_mapping(quota.get("state_action_projection_warning"))),
            "backlog_hygiene_warning": dict(_as_mapping(quota.get("backlog_hygiene_warning"))),
            "autonomous_replan_obligation": dict(_as_mapping(quota.get("autonomous_replan_obligation"))),
        },
        "agent_reasoning_checklist": AGENT_REASONING_CHECKLIST,
        "agent_commands": _agent_commands(
            goal_id=goal_id or None,
            registry_path=registry_path,
            limit=limit,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
            capability_args=capability_args,
        ),
    }


def _apply_orphaned_goal_state_diagnosis(
    packet: dict[str, object],
    projection: Mapping[str, object],
    *,
    registry_path: Path,
    project_root: Path | None = None,
) -> None:
    commands = _as_items(packet.get("agent_commands"))
    status_command = str(commands[1]) if len(commands) > 1 else "loopx status"
    gate = orphaned_goal_state_gate(
        dict(projection),
        project=str(project_root or registry_project_root(registry_path)),
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
            "user_question": "Resolve the orphaned Goal state before reconnecting this goal id.",
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
                for route in _as_items(gate.get("resolution_routes"))
                if isinstance(route, Mapping) and route.get("command")
            ],
        }
    )


def collect_diagnosis(
    *,
    registry_path: Path,
    runtime_root_override: str | None,
    limit: int,
    goal_id: str | None = None,
    agent_id: str | None = None,
    available_capabilities: object = None,
) -> dict[str, object]:
    """Collect a structured, agent-facing diagnosis packet without repeating scans."""
    limit = max(1, limit)
    registry_path = registry_path.expanduser()
    status_payload = dict(
        collect_status(
            registry_path=registry_path,
            runtime_root_override=runtime_root_override,
            limit=limit,
            goal_id=goal_id,
            available_capabilities=available_capabilities,
        )
    )
    normalized_capabilities = normalize_required_capabilities(available_capabilities)
    capability_args = render_available_capability_args(available_capabilities)
    attention_items = _attention_items(status_payload)
    goal_ids = [goal_id] if goal_id else _goal_ids_from_items(attention_items, limit=limit)
    item_by_goal: dict[str, Mapping[str, object]] = {}
    for item in attention_items:
        if (gid := str(item.get("goal_id") or "")) and gid not in item_by_goal:
            item_by_goal[gid] = item
    unregistered_goals = _unregistered_goal_ids(status_payload)
    project_root = registry_project_root(registry_path) if unregistered_goals else None

    goal_packets: list[dict[str, object]] = []
    for current_goal_id in goal_ids:
        quota = _quota_for_goal(
            status_payload,
            current_goal_id,
            agent_id=agent_id,
            available_capabilities=available_capabilities,
        )
        packet = _build_goal_packet(
            status_payload=status_payload,
            item=item_by_goal.get(current_goal_id),
            quota=quota,
            registry_path=registry_path,
            limit=limit,
            agent_id=agent_id,
            normalized_capabilities=normalized_capabilities,
            capability_args=capability_args,
        )
        if current_goal_id in unregistered_goals and project_root is not None:
            orphaned_state = orphaned_goal_state_projection(project_root, current_goal_id)
            if orphaned_state is not None:
                _apply_orphaned_goal_state_diagnosis(
                    packet,
                    orphaned_state,
                    registry_path=registry_path,
                    project_root=project_root,
                )
        goal_packets.append(packet)

    selected_item = (
        item_by_goal.get(goal_id)
        if goal_id
        else (attention_items[0] if attention_items else None)
    )
    selected_goal_id = str((selected_item or {}).get("goal_id") or (goal_ids[0] if goal_ids else ""))
    selected = next((p for p in goal_packets if p.get("goal_id") == selected_goal_id), None)
    if selected is None:
        selected = _build_goal_packet(
            status_payload=status_payload,
            item=selected_item,
            quota={"ok": True, "goal_id": selected_goal_id, "agent_id": agent_id, "should_run": False},
            registry_path=registry_path,
            limit=limit,
            agent_id=agent_id,
            normalized_capabilities=normalized_capabilities,
            capability_args=capability_args,
        )

    contract = _as_mapping(status_payload.get("contract"))
    contract_errors = _compact_text_signals(contract.get("errors"), limit=STATUS_CONTRACT_SIGNAL_LIMIT)
    contract_warnings = _compact_text_signals(contract.get("warnings"), limit=STATUS_CONTRACT_SIGNAL_LIMIT)
    diagnosis_blocked = any(p.get("machine_signal") == ORPHANED_GOAL_STATE_CONNECTION for p in goal_packets)
    goal_overviews = [_goal_list_view(p, selected=p is selected) for p in goal_packets]
    payload: dict[str, object] = {
        "ok": bool(status_payload.get("ok")) and not diagnosis_blocked,
        "schema_version": DIAGNOSIS_SCHEMA_VERSION,
        "packet_kind": PACKET_KIND,
        "agent_must_reason": True,
        "registry": str(registry_path),
        "runtime_root": status_payload.get("runtime_root"),
        "goal_id": goal_id,
        "agent_id": agent_id,
        "available_capabilities": list(normalized_capabilities),
        "selected_goal_id": selected.get("goal_id"),
        "status_ok": status_payload.get("ok"),
        "goal_count": status_payload.get("goal_count"),
        "goal_packet_count": len(goal_packets),
        "run_count": status_payload.get("run_count"),
        "attention_item_count": _as_mapping(status_payload.get("attention_queue")).get("item_count"),
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
            "global_registry": dict(_as_mapping(_as_mapping(status_payload.get("global_registry")).get("summary"))),
        },
        "user_prompt": USER_DIAGNOSE_PROMPT,
    }
    if diagnosis_blocked:
        payload["diagnosis_blocked_by"] = ORPHANED_GOAL_STATE_CONNECTION
    return payload
