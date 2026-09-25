"""Assemble the existing project-agent context independently of human review.

Read-only projection over status facts; no request identity or ownership writer.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

from .review_packet_context import (
    agent_member_from_item,
    agent_member_summary,
    agent_todo_texts_for_handoff,
    project_agent_required_reads,
    project_asset_source,
    project_asset_source_line,
)
from .delivery_contract import (
    handoff_delivery_contract,
    handoff_delivery_contract_summary,
)
from .handoff_fragments import build_handoff_shard_manifest, split_handoff_text
from ...handoff_budget import build_handoff_interface_budget

LOCAL_ABSOLUTE_PATH_PATTERN = re.compile(
    r"(^|[\s`'\"=:(])(?:/[A-Za-z0-9._-]+(?:/[^\s`'\",)]+)+|[A-Za-z]:[\\/][^\s`'\",)]+)"
)


def redact_local_absolute_paths(value: str) -> str:
    return LOCAL_ABSOLUTE_PATH_PATTERN.sub(
        lambda match: f"{match.group(1)}<local-path>", value
    )


def compact_packet_text(value: str, limit: int = 180) -> str:
    compact = " ".join(str(value).split())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 1].rstrip() + "…"


def compact_shell_command(command: str) -> str:
    parts: list[str] = []
    for line in command.splitlines():
        part = line.strip()
        if part.endswith("\\"):
            part = part[:-1].rstrip()
        if part:
            parts.append(part)
    return " ".join(parts)


def command_block(command: str | None, *, compact: bool = False) -> str:
    if not command:
        return "（当前没有可执行命令；先读取 status/history。）"
    if compact:
        command = compact_shell_command(command)
    return "\n".join(["```bash", command, "```"])


def compact_last_bash_command_block(text: str) -> str:
    lines = text.splitlines()
    try:
        start = len(lines) - 1 - lines[::-1].index("```bash")
    except ValueError:
        return text
    try:
        end = start + 1 + lines[start + 1 :].index("```")
    except ValueError:
        return text
    command = "\n".join(lines[start + 1 : end])
    compact_command = compact_shell_command(command)
    return "\n".join([*lines[: start + 1], compact_command, *lines[end:]])


def normalize_project_agent_handoff_text(text: str) -> str:
    """Prepare oversized text using the existing bash-block normalization.

    Unlike the former prefix-dropping fit pass, this never removes sections:
    if the normalized text still exceeds the interface budget, the caller
    fragments it into verifiable continuation shards instead.
    """

    if build_handoff_interface_budget(text)["within_budget"]:
        return text
    return compact_last_bash_command_block(text)


def build_status_command(status_payload: dict[str, Any]) -> str:
    return "\n".join(
        [
            "loopx \\",
            f"  --registry {shlex.quote(str(status_payload.get('registry') or '<registry>'))} \\",
            f"  --runtime-root {shlex.quote(str(status_payload.get('runtime_root') or '<runtime-root>'))} \\",
            "  --format json \\",
            "  status",
        ]
    )


def build_history_command(status_payload: dict[str, Any], goal_id: str) -> str:
    return "\n".join(
        [
            "loopx \\",
            f"  --registry {shlex.quote(str(status_payload.get('registry') or '<registry>'))} \\",
            f"  --runtime-root {shlex.quote(str(status_payload.get('runtime_root') or '<runtime-root>'))} \\",
            "  history \\",
            f"  --goal-id {shlex.quote(goal_id)} \\",
            "  --limit 3",
        ]
    )


def build_read_only_map_command(status_payload: dict[str, Any], goal_id: str) -> str:
    return "\n".join(
        [
            "loopx \\",
            f"  --registry {shlex.quote(str(status_payload.get('registry') or '<registry>'))} \\",
            f"  --runtime-root {shlex.quote(str(status_payload.get('runtime_root') or '<runtime-root>'))} \\",
            "  read-only-map \\",
            f"  --goal-id {shlex.quote(goal_id)} \\",
            "  --dry-run",
        ]
    )


def build_quota_should_run_command(status_payload: dict[str, Any], goal_id: str) -> str:
    return " ".join(
        (
            "loopx",
            f"--registry {shlex.quote(str(status_payload.get('registry') or '<registry>'))}",
            "--format json",
            "quota should-run",
            f"--goal-id {shlex.quote(goal_id)}",
            "--runtime-profile generic_cli",
        )
    )


def find_goal(status_payload: dict[str, Any], goal_id: str) -> dict[str, Any] | None:
    run_history = status_payload.get("run_history")
    if not isinstance(run_history, dict):
        return None
    for goal in run_history.get("goals") or []:
        if isinstance(goal, dict) and goal.get("id") == goal_id:
            return goal
    return None


def find_queue_item(
    status_payload: dict[str, Any], goal_id: str
) -> dict[str, Any] | None:
    attention_queue = status_payload.get("attention_queue")
    if not isinstance(attention_queue, dict):
        return None
    for item in attention_queue.get("items") or []:
        if isinstance(item, dict) and item.get("goal_id") == goal_id:
            return item
    return None


def handoff_followthrough_summary(item: dict[str, Any] | None) -> str | None:
    if not isinstance(item, dict):
        return None
    readiness = (
        item.get("handoff_readiness")
        if isinstance(item.get("handoff_readiness"), dict)
        else {}
    )
    latest_run = (
        readiness.get("post_handoff_latest_run")
        if isinstance(readiness.get("post_handoff_latest_run"), dict)
        else {}
    )
    if not latest_run:
        return None
    classification = (
        str(latest_run.get("classification") or "unknown").strip() or "unknown"
    )
    scale = (
        str(latest_run.get("delivery_batch_scale") or "unknown").strip() or "unknown"
    )
    generated_at = str(latest_run.get("generated_at") or "").strip()
    streak = readiness.get("post_handoff_small_scale_streak")
    streak_text = f", small_streak={streak}" if isinstance(streak, int) else ""
    suffix = f", at={generated_at}" if generated_at else ""
    return compact_packet_text(
        f"post_handoff_run={classification}, scale={scale}{streak_text}{suffix}",
        limit=440,
    )


def authority_material_summary(goal: dict[str, Any] | None) -> str | None:
    if not isinstance(goal, dict):
        return None
    registry = goal.get("authority_registry")
    if not isinstance(registry, dict) or not registry.get("declared"):
        return None
    material_total = int(registry.get("project_material_count") or 0)
    topic_count = int(registry.get("topic_authority_count") or 0)
    if material_total <= 0 and topic_count <= 0:
        return None
    parts = [
        f"topics={topic_count}",
        f"materials={material_total}",
        f"repositories={int(registry.get('project_material_repository_count') or 0)}",
        f"owner_review_required={int(registry.get('project_material_owner_review_required_count') or 0)}",
        f"stale={int(registry.get('project_material_stale_count') or 0)}",
        f"current_authority={int(registry.get('project_material_current_authority_count') or 0)}",
        f"risk={registry.get('conflict_risk') or 'unknown'}",
    ]
    return "authority/material: " + ", ".join(parts)


def latest_run(goal: dict[str, Any] | None) -> dict[str, Any] | None:
    runs = goal.get("latest_runs") if isinstance(goal, dict) else None
    if isinstance(runs, list) and runs and isinstance(runs[0], dict):
        return runs[0]
    return None


def infer_action_kind(item: dict[str, Any] | None, goal: dict[str, Any] | None) -> str:
    run = latest_run(goal)
    missing_gates = item.get("missing_gates") if isinstance(item, dict) else None
    if not isinstance(missing_gates, list) and isinstance(run, dict):
        readiness = run.get("controller_readiness")
        missing_gates = (
            readiness.get("missing_gates") if isinstance(readiness, dict) else None
        )
    missing_gate_set = {str(gate) for gate in missing_gates or [] if gate}
    if isinstance(item, dict) and item.get("severity") == "high":
        return "health"
    if "human_reward_capture" in missing_gate_set:
        return "reward"
    waiting_on = str(item.get("waiting_on") if isinstance(item, dict) else "")
    if waiting_on in {"controller", "user_or_controller"}:
        return "controller"
    if waiting_on == "external_evidence":
        return "evidence"
    if waiting_on == "codex":
        quota = (
            item.get("quota")
            if isinstance(item, dict) and isinstance(item.get("quota"), dict)
            else {}
        )
        asset = (
            item.get("project_asset")
            if isinstance(item, dict) and isinstance(item.get("project_asset"), dict)
            else {}
        )
        asset_quota = asset.get("quota") if isinstance(asset.get("quota"), dict) else {}
        if (
            quota.get("state") == "focus_wait"
            or asset_quota.get("state") == "focus_wait"
        ):
            return "focus_wait"
        return "codex"
    return "status"


def project_agent_command(
    status_payload: dict[str, Any],
    goal_id: str,
    kind: str,
    item: dict[str, Any] | None,
    goal: dict[str, Any] | None = None,
) -> str:
    if kind == "reward":
        return build_history_command(status_payload, goal_id)
    if (
        isinstance(item, dict)
        and item.get("agent_command")
        and (
            kind in {"controller", "codex"}
            or operator_gate_approved_handoff(item, goal)
        )
    ):
        return str(item.get("agent_command"))
    if kind == "controller":
        return build_read_only_map_command(status_payload, goal_id)
    if kind == "codex":
        if connected_delivery_handoff(item, goal):
            return build_quota_should_run_command(status_payload, goal_id)
        return build_history_command(status_payload, goal_id)
    if kind == "focus_wait":
        return build_history_command(status_payload, goal_id)
    return build_status_command(status_payload)


def target_goal_guard(goal_id: str) -> str:
    return (
        f"目标校验：本段只适用于 goal_id=`{goal_id}`；如果与你当前 active goal "
        "或 registry entry 不一致，停止并回报目标不匹配。"
    )


def agent_context_rule() -> str:
    return (
        "上下文规则：本段只携带最小当前指令；如需核验上下文，只读目标 active "
        "state/status/history 和本命令输出，不要从旧聊天或旧 packet 拼当前状态。"
    )


def operator_gate_approved_handoff(
    item: dict[str, Any] | None, goal: dict[str, Any] | None
) -> bool:
    if not isinstance(item, dict) or not item.get("agent_command"):
        return False
    if str(item.get("status") or "") == "operator_gate_approved":
        return True
    run = latest_run(goal)
    operator_gate = run.get("operator_gate") if isinstance(run, dict) else None
    return (
        isinstance(operator_gate, dict)
        and operator_gate.get("decision") == "approve"
        and bool(operator_gate.get("agent_command"))
    )


def connected_delivery_handoff(
    item: dict[str, Any] | None, goal: dict[str, Any] | None = None
) -> bool:
    if not isinstance(item, dict):
        return False
    adapter_status = str(item.get("adapter_status") or "").strip()
    if adapter_status != "connected-delivery" and isinstance(goal, dict):
        adapter_status = str(goal.get("adapter_status") or "").strip()
    if adapter_status != "connected-delivery":
        return False
    if str(item.get("waiting_on") or "") != "codex":
        return False
    quota = item.get("quota") if isinstance(item.get("quota"), dict) else {}
    return str(quota.get("state") or "") == "eligible"


def project_agent_section(
    kind: str,
    command: str,
    goal_id: str,
    *,
    agent_todo_text: str | None = None,
    agent_todo_items: list[str] | None = None,
    authority_summary: str | None = None,
    project_asset_source_text: str | None = None,
    agent_member_text: str | None = None,
    handoff_followthrough_text: str | None = None,
    handoff_delivery_contract_text: str | None = None,
    required_reads: list[dict[str, Any]] | None = None,
    approved_operator_gate: bool = False,
    connected_delivery: bool = False,
) -> str:
    goal_guard = target_goal_guard(goal_id)
    context_rule = agent_context_rule()
    todo_line = f"Agent 待办：{agent_todo_text}" if agent_todo_text else None
    extra_todo_lines = [
        f"Agent 待办候选 {index + 2}：{text}"
        for index, text in enumerate((agent_todo_items or [])[1:3])
        if text
    ]
    authority_line = (
        f"材料上下文：{authority_summary}；只用这些脱敏计数判断 freshness / owner gap，不要要求内部链接或原文。"
        if authority_summary
        else None
    )
    source_line = (
        f"项目资产来源：{project_asset_source_text}"
        if project_asset_source_text
        else None
    )
    member_line = f"Agent 成员：{agent_member_text}" if agent_member_text else None
    followthrough_line = (
        f"交付观测：{handoff_followthrough_text}"
        if handoff_followthrough_text
        else None
    )
    delivery_contract_line = (
        f"交付合同：{handoff_delivery_contract_text}"
        if handoff_delivery_contract_text
        else None
    )
    first_required_read = next(
        (
            item
            for item in (required_reads or [])
            if isinstance(item, dict) and item.get("command")
        ),
        None,
    )
    required_read_line = (
        "必读流水账：replan/接力前运行 "
        f"`{compact_shell_command(str(first_required_read.get('command') or ''))}`；"
        "只展开本 agent，其他 agent 只看 frontier。"
        if first_required_read
        else None
    )
    context_lines = [
        goal_guard,
        context_rule,
        source_line,
        member_line,
        required_read_line,
        todo_line,
        *extra_todo_lines,
        authority_line,
        followthrough_line,
        delivery_contract_line,
    ]
    if approved_operator_gate:
        lines = [
            *context_lines,
            "转发条件：operator gate 已记录为 approve；本段只用于把已批准的 agent_command 交给目标项目 Agent。",
            "执行边界：只执行下面命令；这是只读/dry-run 执行，不是写权限、主控接管或生产动作授权。",
            "停止条件：命令失败，或需要写入、run history append、生产动作、更高权限时，停下并用中文回报结果。",
            "",
            command_block(command),
        ]
    elif connected_delivery and kind == "codex":
        lines = [
            *context_lines,
            "转发条件：目标 registry 已是 connected-delivery，且 quota/owner/gate 显示 codex-ready；本段用于目标项目 Agent 做真实 delivery。",
            "执行边界：先执行下面 quota guard；若 should_run=true，读取 active state/status/goal_boundary/execution_profile 后，选择一个 write_scope 内的 bounded delivery segment，可改文件、验证、写回、spend。",
            "停止条件：只能继续 isolated test、surface-only 下游传播，或需要未授权写入范围、生产动作、destructive git、私密材料时，回报 blocker，不 spend。",
            "",
            command_block(command, compact=True),
        ]
    elif kind == "reward":
        lines = [
            *context_lines,
            "转发条件：只有用户已经真实记录 run-bound human_reward 后，才把本段发给项目 Agent。",
            "执行边界：不要替用户写 reward；active state 只做摘要，reward 的权威来源是 run-bound human_reward overlay。",
            "停止条件：如果 reward 还停留在 dry-run / 草稿 / 口头判断，停下等待用户记录；如果已经记录，只用下面 history 路径读取。",
            "",
            command_block(command),
        ]
    elif kind == "controller":
        lines = [
            *context_lines,
            "转发条件：只有用户已经明确同意 read-only/controller dry-run 后，才把本段发给项目 Agent。",
            "执行边界：只执行下面只读或 dry-run 项目路径；不要运行用户本地 Gate 记录草稿。",
            "停止条件：需要真实 approval、write-control、run history append、生产动作或命令失败时，停下等明确授权。",
            "",
            command_block(command),
        ]
    elif kind == "focus_wait":
        lines = [
            *context_lines,
            "转发条件：仅当目标项目 Agent 需要当前等待边界时转发；这不是恢复 delivery 的授权。",
            "执行边界：只读 status/history，确认当前 owner blocker、证据入口和 stop condition；不要继续实现、adapter work、写入或生产动作。",
            "停止条件：没有新的 owner evidence、clean baseline 或外部 eval 时，保持 focus_wait 并用中文回报仍在等待什么。",
            "",
            command_block(command),
        ]
    else:
        lines = [
            *context_lines,
            "转发条件：只有用户已经同意 safe local path 后，才把本段发给项目 Agent。",
            "执行边界：读取本项目 status/history 后，只执行下面只读或 dry-run 路径。",
            "停止条件：需要真实写 reward、approval、write-control、run history append、生产动作或命令失败时，停下等明确授权。",
            "",
            command_block(command),
        ]
    return normalize_project_agent_handoff_text(
        "\n".join(line for line in lines if line)
    )


@dataclass(frozen=True)
class ProjectAgentContext:
    """Current source facts and their existing bounded display projections."""

    goal_id: str
    item: dict[str, Any] | None
    goal: dict[str, Any] | None
    kind: str
    command: str
    agent_todo_items: list[str]
    asset_source: str
    member: dict[str, Any] | None
    member_summary: str | None
    authority_summary: str | None
    followthrough_summary: str | None
    delivery_contract: dict[str, Any] | None
    required_reads: list[dict[str, Any]]
    approved_handoff: bool
    delivery_handoff: bool

    @property
    def agent_todo_text(self) -> str | None:
        return self.agent_todo_items[0] if self.agent_todo_items else None

    @property
    def effective_kind(self) -> str:
        return "codex" if self.approved_handoff else self.kind

    def payload(self) -> dict[str, Any]:
        text = project_agent_section(
            self.kind,
            self.command,
            self.goal_id,
            agent_todo_text=self.agent_todo_text,
            agent_todo_items=self.agent_todo_items,
            authority_summary=self.authority_summary,
            project_asset_source_text=project_asset_source_line(self.asset_source),
            agent_member_text=self.member_summary,
            handoff_followthrough_text=self.followthrough_summary,
            handoff_delivery_contract_text=handoff_delivery_contract_summary(
                self.delivery_contract
            ),
            required_reads=self.required_reads,
            approved_operator_gate=self.approved_handoff,
            connected_delivery=self.delivery_handoff,
        )
        result: dict[str, Any] = {
            "ok": True,
            "goal_id": self.goal_id,
            "kind": self.effective_kind,
            "waiting_on": self.item.get("waiting_on") if self.item else None,
            "status": self.item.get("status")
            if self.item
            else self.goal.get("status")
            if self.goal
            else None,
            "project_agent_command": self.command,
            "project_agent_handoff": text,
            "operator_gate_approved_handoff": self.approved_handoff,
            "connected_delivery_handoff": self.delivery_handoff,
            "agent_todo_text": self.agent_todo_text,
            "agent_todo_items": self.agent_todo_items,
            "agent_member": self.member,
            "agent_member_summary": self.member_summary,
            "authority_summary": self.authority_summary,
            "handoff_followthrough_summary": self.followthrough_summary,
            "handoff_delivery_contract": self.delivery_contract,
            "project_agent_required_reads": self.required_reads,
            "handoff_interface_budget": build_handoff_interface_budget(text),
            "project_asset_source": self.asset_source,
        }
        shards = split_handoff_text(text)
        if len(shards) > 1:
            # Complete fields stay complete. The transport array includes ALL shards.
            result["project_agent_handoff_fragments"] = shards
            result["handoff_fragment_manifest"] = build_handoff_shard_manifest(
                text, shards
            )
        return result


def assemble_project_agent_context(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    action_kind: str | None = None,
) -> ProjectAgentContext:
    item = find_queue_item(status_payload, goal_id)
    goal = find_goal(status_payload, goal_id)
    if item is None and goal is None:
        raise ValueError(f"goal not found in status payload: {goal_id}")
    kind = action_kind or infer_action_kind(item, goal)
    return ProjectAgentContext(
        goal_id=goal_id,
        item=item,
        goal=goal,
        kind=kind,
        command=redact_local_absolute_paths(
            project_agent_command(status_payload, goal_id, kind, item, goal)
        ),
        agent_todo_items=agent_todo_texts_for_handoff(item),
        asset_source=project_asset_source(item),
        member=agent_member_from_item(item),
        member_summary=agent_member_summary(item),
        authority_summary=authority_material_summary(goal),
        followthrough_summary=handoff_followthrough_summary(item),
        delivery_contract=handoff_delivery_contract(item),
        required_reads=project_agent_required_reads(goal_id, item),
        approved_handoff=operator_gate_approved_handoff(item, goal),
        delivery_handoff=connected_delivery_handoff(item, goal) and kind == "codex",
    )


def build_project_agent_handoff(
    status_payload: dict[str, Any], *, goal_id: str, action_kind: str | None = None
) -> dict[str, Any]:
    try:
        return assemble_project_agent_context(
            status_payload, goal_id=goal_id, action_kind=action_kind
        ).payload()
    except ValueError as exc:
        return {"ok": False, "goal_id": goal_id, "error": str(exc)}
