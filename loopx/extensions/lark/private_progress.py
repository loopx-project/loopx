"""Lark presentation of persisted Core events; never execution authority.

Only the visible answer stream and coarse, observed activity kinds are shown.
Reasoning text, tool arguments and command output stay out of the transport.
"""
from __future__ import annotations

from collections.abc import Mapping
import re
from typing import Any

ANSWER_WINDOW = 6000
UPDATE_INTERVAL_SEC = 2.0
ACTIVITY_UPDATE_INTERVAL_SEC = 10.0


def presentation_phase(record: Mapping[str, Any], phase: str) -> str:
    """A continuation has its own presentation, alongside the old failure."""
    recovery = record.get("context_recovery") or {}
    if recovery and phase in {"admission", "progress", "terminal", "commission_result"}:
        return f"{phase}.recovery.{recovery.get('presentation_key', recovery['version'])}"
    return phase


def presentation_stream(record: dict[str, Any]) -> dict[str, Any]:
    recovery = record.get("context_recovery") or {}
    if recovery:
        return record.setdefault("recovery_streams", {}).setdefault(str(recovery.get("presentation_key", recovery["version"])), {})
    return record.setdefault("stream", {})


def legacy_recovery_repairs(record: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Discover only known drafts reused across an already-returned recovery.

    This is presentation debt, never authority to enqueue/replay work. The
    sender still verifies the exact prior/current provider body and source.
    Unknown attempts stay unmodified; no message is created by this plan.
    """
    if not record.get("context_recovery"):
        return []
    deliveries = record.get("deliveries") or {}
    final = deliveries.get(presentation_phase(record, "terminal")) or {}
    original = deliveries.get("terminal") or {}
    old_ref = (original.get("attempt") or {}).get("message_ref")
    final_ref = (final.get("attempt") or {}).get("message_ref")
    if not (original.get("verified") and final.get("verified") and old_ref and final_ref and final_ref != old_ref):
        return []
    segments = sorted((key for key in deliveries if re.fullmatch(r"progress\.[0-9]+", key)),
                      key=lambda key: int(key.split(".")[-1]))
    candidates = [(key, deliveries[key]) for key in segments]
    progress, stream = deliveries.get("progress") or {}, record.get("stream") or {}
    if progress.get("verified"):
        candidates.append(("progress", {**progress,
            "text": stream.get("confirmed_text") or progress.get("text"),
            "attempt": stream.get("confirmed_attempt") or progress.get("attempt")}))
    reused = next((index for index, (_, proof) in enumerate(candidates)
        if (proof.get("attempt") or {}).get("message_ref") == old_ref
        and proof.get("text") != original.get("text")), None)
    if reused is None:
        return []
    repairs = []
    for key, proof in candidates[reused:]:
        ref = (proof.get("attempt") or {}).get("message_ref")
        if not proof.get("verified") or not proof.get("text") or not ref or ref == final_ref:
            continue
        phase = presentation_phase(record, "terminal") + ".presentation." + key
        if not deliveries.get(phase, {}).get("verified"):
            repairs.append({"phase": phase, "text": original["text"] if ref == old_ref else
                "本轮回复已回传，后续事项以完整回复为准。", "edit_from": {
                    "text": proof["text"], "attempt": proof["attempt"]}})
    return repairs


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
                     "search": "正在检索资料", "file_change": "正在修改文件"}.get(kind) if isinstance(kind, str) else None
            if label and isinstance(step, Mapping):
                if step.get("state") == "completed":
                    label = "已结束当前步骤，继续处理"
                elif step.get("state") == "failed":
                    label = "当前步骤失败，Agent 正在处理"
                elif kind == "command" and step.get("verb") == "read":
                    label = "正在读取内容"
                if step.get("state") == "running" and payload.get("method") in {
                    "item/commandExecution/outputDelta", "item/mcpToolCall/progress",
                }:
                    duration = step.get("duration_ms")
                    if isinstance(duration, int) and not isinstance(duration, bool) and duration >= 0:
                        observed = "收到新输出" if kind == "command" else "收到工具进展"
                        label = f"{label} · {observed}（已运行 {duration // 1000} 秒）"
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
