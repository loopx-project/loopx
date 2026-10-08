"""Lark presentation of persisted Core events; never execution authority.

Only the visible answer stream and coarse, observed activity kinds are shown.
Reasoning text, tool arguments and command output stay out of the transport.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

ANSWER_WINDOW = 6000
UPDATE_INTERVAL_SEC = 2.0
ACTIVITY_UPDATE_INTERVAL_SEC = 10.0


def project_progress(state: dict[str, Any], events: list[dict[str, Any]]) -> str | None:
    """Fold each exact Turn event once into a bounded, replayable view."""
    for event in events:
        state["cursor"] = event["event_id"]
        payload = event.get("payload") or {}
        if event.get("kind") == "answer.delta" and isinstance(payload.get("text"), str):
            text = str(state.get("answer") or "") + payload["text"]
            state["clipped"] = bool(state.get("clipped")) or len(text) > ANSWER_WINDOW
            state["answer"] = text[-ANSWER_WINDOW:]
            state["label"] = "正在生成回答"
        elif event.get("kind") == "agent.phase":
            step = payload.get("step")
            kind = step.get("kind") if isinstance(step, Mapping) else None
            # Summary deltas contain reasoning, not owner-facing progress.
            if str(payload.get("method") or "").startswith("item/reasoning/"):
                continue
            # Core preparation and confirmed host start precede model output.
            # Render fixed activity text, never the context/provider label.
            if payload.get("phase") == "manager_context":
                state["label"] = "正在读取当前工作状态"
            elif payload.get("method") == "turn/started":
                state["label"] = "已开始处理"
            label = {"reasoning": "正在思考", "command": "正在执行命令", "tool": "正在调用工具",
                     "search": "正在检索资料", "file_change": "正在修改文件"}.get(kind)
            if label:
                if step.get("state") == "completed":
                    label = "已结束当前步骤，继续处理"
                elif step.get("state") == "failed":
                    label = "当前步骤失败，Agent 正在处理"
                elif kind == "command" and step.get("verb") == "read":
                    label = "正在读取内容"
                state["label"] = label
            elif payload.get("method") == "item/started":
                # Older adapters emit only this fixed vocabulary, without steps.
                label = {"Agent 正在思考": "正在思考", "Agent 正在执行命令": "正在执行命令", "Agent 正在调用工具": "正在调用工具",
                         "Agent 正在检索": "正在检索资料", "Agent 正在修改文件": "正在修改文件",
                         "Agent 正在生成回答": "正在生成回答",
                         "Agent 正在压缩会话上下文": "正在压缩会话上下文"}.get(payload.get("label"))
                if label:
                    state["label"] = label
            elif (payload.get("method") == "item/completed"
                  and payload.get("label") == "Agent 会话上下文压缩已结束"):
                state["label"] = "会话上下文压缩已结束，继续处理"
    if not state.get("label") and not state.get("answer"):
        return None
    header = f"⏳ **{state.get('label') or '正在处理'}**"
    answer = str(state.get("answer") or "")
    if answer:
        if state.get("clipped"):
            answer = "…（当前显示最近的回答片段）\n\n" + answer
        return header + "\n\n" + answer + "\n\n_内容仍在生成，结束后会更新为完整结果。_"
    return header
