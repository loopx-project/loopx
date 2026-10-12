"""Readable status over the existing bound-conversation observation, without writes."""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import PurePath
from typing import Any

from ..markdown import markdown_scalar


def _automation_cadence_line(snapshot: Mapping[str, Any]) -> str | None:
    cadence = snapshot.get("automation_cadence")
    if not isinstance(cadence, Mapping):
        return None
    eligibility = cadence.get("eligibility")
    if not isinstance(eligibility, Mapping):
        return "自动执行状态暂不可读，不能据此判断可启动。"
    state = eligibility.get("state")
    if state == "unconfigured":
        return "自动执行最小间隔：未配置。"
    if state == "unavailable":
        return "自动执行状态暂不可读，不能据此判断可启动。"
    floor = cadence.get("min_interval_minutes")
    if isinstance(floor, bool) or not isinstance(floor, int) or floor < 0:
        return "自动执行状态暂不可读，不能据此判断可启动。"
    next_at = eligibility.get("next_eligible_at_ms")
    exact = None
    if next_at is not None:
        try:
            if isinstance(next_at, bool) or not isinstance(next_at, int) or next_at < 0:
                raise ValueError("invalid next eligible time")
            exact = datetime.fromtimestamp(
                next_at / 1000, timezone.utc
            ).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        except (OverflowError, OSError, ValueError):
            return "自动执行状态暂不可读，不能据此判断可启动。"
    if state == "eligible":
        suffix = f"（自 {exact} 起）" if exact else ""
        return f"自动执行最小间隔：{floor} 分钟；最小间隔条件已满足{suffix}。"
    if state == "waiting" and exact:
        return f"自动执行最小间隔：{floor} 分钟；最小间隔条件尚未满足，等待至 {exact}。"
    return "自动执行状态暂不可读，不能据此判断可启动。"


def render_conversation_status(snapshot: Mapping[str, Any]) -> str:
    """Execution is not acceptance; absent/unreadable facts never become ready."""
    if snapshot["session_status"] is None:
        state = "⚪ 尚无会话，发送消息即可开始。"
    elif not snapshot["active_turn_observation_available"]:
        state = "⚠️ 执行状态暂不可读，请在本机检查原会话。"
    elif snapshot["active_turn_status"]:
        state = {
            "queued": "🕓 已受理，等待执行。",
            "starting": "🔵 正在启动。",
            "running": "🔵 正在执行。",
            "completing": "🔵 正在收尾。",
            "interrupting": "🔵 正在停止。",
            "completed": "⚪ 本次执行已结束。",
            "interrupted": "⏹️ 本次执行已停止。",
            "timed_out": "⚠️ 本次执行超时，请检查原会话后再决定是否重试。",
            "failed": "⚠️ 本次执行失败，请在本机检查原会话。",
        }.get(snapshot["active_turn_status"], "⚠️ 执行状态暂不可判定，请在本机检查原会话。")
    else:
        state = {
            "ready": "🟢 可以继续对话。",
            "starting": "🔵 会话正在启动。",
            "resuming": "🔵 会话正在恢复。",
            "closed": "⚪ 会话已关闭，发送消息即可开始新会话。",
            "stale": "⚠️ 会话需要恢复，请在本机检查原会话。",
            "failed": "⚠️ 会话恢复失败，请在本机检查原会话。",
            "resume_failed": "⚠️ 会话恢复失败，请在本机检查原会话。",
        }.get(snapshot["session_status"], "⚠️ 会话状态暂不可判定，请在本机检查原会话。")
    steward = snapshot["context_kind"] == "steward"
    workspace = markdown_scalar(PurePath(snapshot["workspace_path"]).name or snapshot["workspace_path"])
    role = "长期管家" if steward else "个人助手"
    lines = [state, "", f"{role} · {workspace}", f"排队消息：{snapshot['queued_count']} 条。"]
    if snapshot.get("recipient_agent_id"):
        lines.extend([
            f"已选 Agent：{markdown_scalar(snapshot['recipient_agent_id'])}。",
            "等待原宿主领取消息；停止或新建会话请在原宿主操作。",
        ])
        cadence = _automation_cadence_line(snapshot)
        if cadence:
            lines.append(cadence)
    elif steward:
        lines.append(f"已授权委托：{snapshot['authorized_commission_count']} 个；执行结束后仍需验收。")
    else:
        grant = snapshot.get("grant")
        lines.append({
            "workspace_write": "当前工作区可读写。",
            "workspace_read": "当前工作区仅有只读授权。",
        }.get(grant if isinstance(grant, str) else "", "工作区权限暂不可判定，请在本机检查授权。"))
    # Delivery may be delayed or replayed. Keep the snapshot's time visible;
    # formatting it must never substitute the renderer's current clock.
    try:
        observed = datetime.fromisoformat(str(snapshot["observed_at"]).replace("Z", "+00:00"))
        if observed.tzinfo is None:
            raise ValueError("observation timezone unavailable")
        lines.append(f"观察于 {observed.astimezone():%Y-%m-%d %H:%M %z}")
    except (KeyError, ValueError):
        lines.append("观察时间暂不可读。")
    return "\n".join(lines)
