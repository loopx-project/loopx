from __future__ import annotations

import shlex
from typing import Any

from .control_plane.runtime.decision_freshness import (
    decision_freshness_warning as runtime_decision_freshness_warning,
)
from .control_plane.handoff.project_agent_context import (
    assemble_project_agent_context,
    command_block,
    compact_packet_text,
    redact_local_absolute_paths,
)
from .control_plane.handoff.review_packet_context import (
    project_asset_source_line,
    todo_text_from_project_asset,
)
from .control_plane.handoff.handoff_fragments import render_handoff_transport


def operator_gate_reason_summary(goal_id: str, decision: str) -> str:
    if decision == "approve":
        return controller_approval_reason(goal_id)
    if decision == "reject":
        return (
            f"暂不同意 {goal_id} 先做 read-only map dry-run，原因：<public-safe-reason>"
        )
    if decision == "defer":
        return f"暂缓 {goal_id} read-only map dry-run，等待：<public-safe-condition>"
    return "<public-safe-reason>"


def build_operator_gate_command(
    status_payload: dict[str, Any], goal_id: str, *, decision: str = "approve"
) -> str:
    return "\n".join(
        [
            "loopx \\",
            f"  --registry {shlex.quote(str(status_payload.get('registry') or '<registry>'))} \\",
            f"  --runtime-root {shlex.quote(str(status_payload.get('runtime_root') or '<runtime-root>'))} \\",
            "  operator-gate \\",
            f"  --goal-id {shlex.quote(goal_id)} \\",
            f"  --decision {shlex.quote(decision)} \\",
            f"  --reason-summary {shlex.quote(operator_gate_reason_summary(goal_id, decision))} \\",
            "  --dry-run",
        ]
    )


def controller_reply(goal_id: str) -> str:
    return f"同意 {goal_id} 先做 read-only map dry-run / 暂不同意 + 一句话原因。"


def controller_approval_reason(goal_id: str) -> str:
    return f"同意 {goal_id} 先做 read-only map dry-run，不授权写入或生产动作"


def operator_gate_decision_commands(
    status_payload: dict[str, Any], goal_id: str
) -> dict[str, str]:
    return {
        decision: build_operator_gate_command(
            status_payload, goal_id, decision=decision
        )
        for decision in ("approve", "reject", "defer")
    }


def decision_freshness_packet_lines(warning: dict[str, Any] | None) -> list[str]:
    if not isinstance(warning, dict) or not warning:
        return []
    lines = [
        "",
        "【决策 freshness 警告】",
        str(warning.get("message") or "旧决策复用前需做 decision-point rebase。"),
    ]
    for item in warning.get("items") or []:
        if not isinstance(item, dict):
            continue
        lines.append(
            "- "
            f"{compact_packet_text(str(item.get('decision_kind') or 'decision'), limit=60)} "
            f"state={compact_packet_text(str(item.get('freshness_state') or 'unknown'), limit=80)} "
            f"age_days={item.get('age_days')} "
            f"newer_7d={item.get('newer_event_count_7d')} "
            f"at={compact_packet_text(str(item.get('decision_at') or ''), limit=80)}"
        )
    lines.append(
        "处理方式：这不是仓库回滚；只在审批/转交这一瞬间重读当前控制面状态后再复用旧决策。"
    )
    return [redact_local_absolute_paths(line) for line in lines]


def stale_latest_run_packet_lines(warning: dict[str, Any] | None) -> list[str]:
    if not isinstance(warning, dict) or not warning:
        return []
    lines = [
        "",
        "【状态投影警告】",
        "当前 active state 看起来比 latest_run 投影更新；先 refresh-state，再信任基于 latest_run 的路由/交接。",
        "- "
        f"active_state_updated_at={compact_packet_text(str(warning.get('active_state_updated_at') or ''), limit=80)} "
        f"latest_run_generated_at={compact_packet_text(str(warning.get('latest_run_generated_at') or ''), limit=80)} "
        f"reason={compact_packet_text(str(warning.get('reason') or ''), limit=120)}",
    ]
    return [redact_local_absolute_paths(line) for line in lines]


def human_prompt(kind: str) -> dict[str, str]:
    if kind == "reward":
        return {
            "question": "是否把这次判断记录为 run-bound human_reward？",
            "reply": "同意记录 / 暂不同意 + 一句话原因。",
            "boundary": "只有去掉 --dry-run 才会写 human_reward 和 active-state 摘要；这不是 write-control、controller opt-in 或生产动作授权。",
        }
    if kind == "controller":
        return {
            "question": "是否允许目标项目进入 read-only/controller opt-in？",
            "reply": "同意先做 read-only map dry-run / 暂不同意 + 一句话原因。",
            "boundary": "这只授权项目 Agent 预览 dry-run 路径；不写 operator gate、run history、write-control、实验控制或生产动作。",
        }
    if kind == "codex":
        return {
            "question": "是否让项目 Agent 沿 safe local path 继续？",
            "reply": "同意继续 / 暂不同意 + 一句话原因。",
            "boundary": "如果下一步需要写入、reward append、approval 或 write-control，项目 Agent 必须先停下等明确授权。",
        }
    if kind == "evidence":
        return {
            "question": "是否继续等待外部证据，而不升级成决策建议？",
            "reply": "继续等待 / 不继续等待 + 一句话原因。",
            "boundary": "观察状态不是 reward、approval 或 controller opt-in。",
        }
    if kind == "focus_wait":
        return {
            "question": "是否继续保持 focus wait，直到 owner blocker 有新证据？",
            "reply": "继续等待 / 提供新证据并恢复 delivery / 暂缓该线 + 一句话原因。",
            "boundary": "focus wait 不是 delivery 授权；没有新 owner evidence、clean baseline 或外部 eval 时，项目 Agent 只读 status/history。",
        }
    if kind == "health":
        return {
            "question": "是否先修健康阻塞，再讨论 reward/controller/codex handoff？",
            "reply": "先修阻塞 / 暂不处理 + 一句话原因。",
            "boundary": "健康修复不等于授权 reward append、approval 或 write-control。",
        }
    return {
        "question": "当前是否需要转给项目 Agent 继续处理？",
        "reply": "继续 / 不继续 / 继续观察 + 一句话原因。",
        "boundary": "本回复不自动写 reward、approval、controller opt-in 或 write-control。",
    }


def suggested_decision(
    kind: str, item: dict[str, Any] | None, goal_id: str | None = None
) -> str:
    if kind == "controller":
        lead = f"同意 {goal_id} 先做" if goal_id else "同意先做"
        question = str(item.get("operator_question") if isinstance(item, dict) else "")
        if "read-only map" in question:
            return f"{lead} read-only map dry-run；不授权写入或生产动作。"
        return f"{lead}只读 controller dry-run；不授权写入或生产动作。"
    if kind == "reward":
        return "同意记录这次 human reward / 暂不同意，原因是..."
    if kind == "codex":
        return "同意让 Codex 沿 safe path 继续；如需写入再单独请求授权。"
    if kind == "evidence":
        return "继续等待外部证据；暂不升级成决策建议。"
    if kind == "focus_wait":
        return "继续保持 focus wait；有新 owner evidence、clean baseline 或外部 eval 后再恢复 delivery。"
    if kind == "health":
        return "先修健康阻塞；暂不处理 reward/controller/codex handoff。"
    return "继续 / 不继续 / 继续观察，并补一句原因。"


def build_review_packet(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    action_kind: str | None = None,
    review_url: str | None = None,
) -> dict[str, Any]:
    try:
        context = assemble_project_agent_context(
            status_payload, goal_id=goal_id, action_kind=action_kind
        )
    except ValueError as exc:
        return {"ok": False, "goal_id": goal_id, "error": str(exc)}
    item, kind = context.item, context.kind
    handoff = context.payload()
    prompt = human_prompt(kind)
    question = (
        str(item.get("operator_question") or prompt["question"])
        if isinstance(item, dict)
        else prompt["question"]
    )
    summary = (
        str(item.get("recommended_action") or "当前状态源没有对应的 action card。")
        if isinstance(item, dict)
        else "当前状态源没有对应的 action card。"
    )
    user_todo_text = todo_text_from_project_asset(item, "user_todos")
    asset_source_line = project_asset_source_line(context.asset_source)
    authority_summary = context.authority_summary
    freshness_warning = runtime_decision_freshness_warning(
        status_payload,
        goal_id=goal_id,
        message="旧 reward/gate 决策复用前需在当前 registry/state/quota/policy/run status 上重新对齐。",
    )
    freshness_warning_lines = decision_freshness_packet_lines(freshness_warning)
    stale_latest_run_warning = (
        item.get("stale_latest_run_warning")
        if isinstance(item, dict)
        and isinstance(item.get("stale_latest_run_warning"), dict)
        else None
    )
    task_graph_projection = (
        item.get("task_graph_projection")
        if isinstance(item, dict)
        and isinstance(item.get("task_graph_projection"), dict)
        else None
    )
    stale_latest_run_lines = stale_latest_run_packet_lines(stale_latest_run_warning)
    approved_handoff = context.approved_handoff
    effective_kind = context.effective_kind
    gate_commands = (
        operator_gate_decision_commands(status_payload, goal_id)
        if kind == "controller"
        else {}
    )
    gate_command = gate_commands.get("approve") if gate_commands else None
    decision = suggested_decision(kind, item, goal_id)
    if user_todo_text and kind == "controller":
        decision = f"先确认待办；完成后：{decision}"
    reply = controller_reply(goal_id) if kind == "controller" else prompt["reply"]
    boundary = prompt["boundary"]
    if approved_handoff:
        question = "operator gate 已批准；是否把短交接发给目标项目 Agent？"
        decision = "直接转发给项目 Agent；不追加写权限、主控接管或生产动作授权。"
        reply = "转发下方【给项目 Agent】即可。"
        boundary = "这只是执行已批准的只读/dry-run agent_command；如需写入或更高权限，项目 Agent 必须再次停下。"
    owner_blocker_text = user_todo_text if kind == "focus_wait" else None
    agent_text = render_handoff_transport(
        handoff["project_agent_handoff"],
        handoff.get("project_agent_handoff_fragments", []),
    )
    type_label = {
        "reward": "Reward",
        "controller": "Controller",
        "codex": "Codex",
        "focus_wait": "Focus Wait",
        "evidence": "Evidence",
        "health": "Health",
    }.get(effective_kind, "Status")
    lines = [
        "【LoopX Review Packet】",
        f"目标：{goal_id}",
        f"类型：{type_label}",
        f"链接：{review_url or 'CLI generated packet; no dashboard URL provided.'}",
        f"摘要：{summary}",
        f"来源：{asset_source_line}",
        f"材料：{authority_summary}（仅脱敏计数；不含内部链接、路径或正文。）"
        if authority_summary
        else None,
        *stale_latest_run_lines,
        *freshness_warning_lines,
        "",
        "【人只需判断】",
        f"解锁条件：{owner_blocker_text}（有新证据或明确暂缓后再调整 focus）"
        if owner_blocker_text
        else None,
        f"待办：{user_todo_text}（先处理/暂缓再判 gate）"
        if user_todo_text and kind == "controller"
        else None,
        f"问题：{question}",
        f"建议判断：{decision}",
        f"回复：{reply}",
        f"边界：{boundary}",
    ]
    if gate_command:
        lines.extend(
            [
                "",
                "【用户本地 Gate 记录草稿】",
                "用途：人确认后，由用户或主控先 dry-run 预览 durable operator gate；不要把它当作项目 Agent 执行命令。",
                "记录规则：保留 --dry-run 只预览；确认写入 durable operator gate 时再删除 --dry-run。若拒绝或暂缓，只把 --decision 和 --reason-summary 改成 reject / defer 与一句 public-safe 原因。",
                command_block(gate_command),
            ]
        )
    lines.extend(
        [
            "",
            "【给项目 Agent】",
            agent_text,
        ]
    )
    lines.extend(
        [
            "",
            "回报：用中文说明 changed files、validation 和 next safe action。",
        ]
    )
    result = {
        **handoff,
        "review_url": review_url,
        "question": question,
        "suggested_decision": decision,
        "operator_gate_dry_run_command": gate_command,
        "operator_gate_decision_commands": gate_commands,
        "user_todo_text": user_todo_text,
        "owner_blocker_text": owner_blocker_text,
        "decision_freshness_warning": freshness_warning,
        "stale_latest_run_warning": stale_latest_run_warning,
        "task_graph_projection": task_graph_projection,
        "packet": "\n".join(line for line in lines if line),
    }
    return result


def render_review_packet_markdown(payload: dict[str, Any]) -> str:
    if not payload.get("ok"):
        return "\n".join(
            [
                "# LoopX Review Packet",
                "",
                f"- ok: `{payload.get('ok')}`",
                f"- goal_id: `{payload.get('goal_id')}`",
                f"- error: {payload.get('error')}",
            ]
        )
    return str(payload.get("packet") or "")
