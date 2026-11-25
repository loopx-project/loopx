"""Lark presentation of persisted Core events; never execution authority.

The original private owner sees bounded reasoning summaries in the same shape
as Codex, without repeating tool/command titles. Hidden reasoning, tool
arguments and output never travel here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from functools import partial
import hashlib
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any

from ...chat_store import TERMINAL_TURN_STATES, _atomic_write_json
from ...file_lock import exclusive_file_lock
from .inbox_reply import update_lark_inbox_reply, verify_lark_inbox_reply
from .outbound import present_lark_conversation_text as _presentation_text
from .private_web_projection import _same_fields

if TYPE_CHECKING:
    from .private_conversations import LarkPrivateConversations

ANSWER_WINDOW = 6000
UPDATE_INTERVAL_SEC = 2.0
ACTIVITY_UPDATE_INTERVAL_SEC = 10.0
# Conservative transport budget, not a claim about the provider's maximum.
# Reserve edits for a final/closing notice; continue long Turns on a new draft.
PROGRESS_EDIT_BUDGET = 12


def _pc() -> Any:
    from . import private_conversations
    return private_conversations


def presentation_phase(record: Mapping[str, Any], phase: str) -> str:
    """A continuation has its own presentation, alongside the old failure."""
    recovery = record.get("context_recovery") or {}
    if recovery and phase in {"admission", "progress", "terminal", "commission_result"}:
        recovery_key = (
            recovery["presentation_key"]
            if "presentation_key" in recovery
            else recovery.get("version")
        )
        return f"{phase}.recovery.{recovery_key}"
    return phase


def presentation_stream(record: dict[str, Any]) -> dict[str, Any]:
    recovery = record.get("context_recovery") or {}
    if recovery:
        recovery_key = (
            recovery["presentation_key"]
            if "presentation_key" in recovery
            else recovery.get("version")
        )
        return record.setdefault("recovery_streams", {}).setdefault(str(recovery_key), {})
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


def project_progress(state: dict[str, Any], events: list[dict[str, Any]], *,
                     owner_private: bool = False) -> str | None:
    """Fold each exact Turn event once into a bounded, replayable view."""
    state.pop("activities", None)
    if not owner_private:
        state.pop("public_summary", None)
        state.pop("thinking_summaries", None)
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
            method = str(payload.get("method") or "")
            if method.startswith("item/reasoning/") and not (
                    owner_private and method == "item/reasoning/summaryTextDelta"
                    and isinstance(step, Mapping) and step.get("reasoning_summary") is True):
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
                if owner_private and kind == "reasoning" and step.get("reasoning_summary") is True:
                    summary = step.get("detail") or step.get("title")
                    if isinstance(summary, str) and summary.strip():
                        cleaned = " ".join(summary.split())[:280]
                        step_id = str(step.get("id") or "reasoning")
                        raw_summaries = state.get("thinking_summaries")
                        summaries = [
                            row
                            for row in (raw_summaries if isinstance(raw_summaries, list) else [])
                            if isinstance(row, Mapping) and row.get("id") != step_id
                        ]
                        summaries.append({"id": step_id, "text": cleaned})
                        summaries = summaries[-4:]
                        state["thinking_summaries"] = summaries
                        if len(summaries) == 1:
                            state["public_summary"] = summary.strip()[:1200]
                        else:
                            state["public_summary"] = "\n".join(
                                f"• {row['text']}" for row in summaries
                            )[:1200]
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
                raw_label = payload.get("label")
                label = (
                    {
                        "Agent 正在思考": "正在思考",
                        "Agent 正在执行命令": "正在执行命令",
                        "Agent 正在调用工具": "正在调用工具",
                        "Agent 正在检索": "正在检索资料",
                        "Agent 正在修改文件": "正在修改文件",
                        "Agent 正在生成回答": "正在生成回答",
                        "Agent 正在压缩会话上下文": "正在压缩会话上下文",
                    }.get(raw_label)
                    if isinstance(raw_label, str)
                    else None
                )
                if label:
                    state["label"] = label
            elif (payload.get("method") == "item/completed"
                  and payload.get("label") == "Agent 会话上下文压缩已结束"):
                state["label"] = "会话上下文压缩已结束，继续处理"
    if not state.get("label") and not state.get("answer"):
        return None
    header = f"⏳ **{state.get('label') or '正在处理'}**"
    if owner_private and state.get("public_summary"):
        header += "\n\n**思考摘要**\n" + state["public_summary"]
    answer = str(state.get("answer") or "")
    if answer:
        if state.get("clipped"):
            answer = "…（当前显示最近的回答片段）\n\n" + answer
        return header + "\n\n" + answer + "\n\n_内容仍在生成，结束后会更新为完整结果。_"
    return header


class PrivateProgressMixin:
    """Provisional progress draft editing and legacy recovery presentation repair."""

    @staticmethod
    def _progress_proof(record: dict[str, Any]) -> tuple[str, dict[str, Any], dict[str, Any]]:
        key = presentation_phase(record, "progress")
        return key, record["deliveries"].get(key) or {}, presentation_stream(record)

    def _edit_progress(self: LarkPrivateConversations, path: Path, record: dict[str, Any], text: str, *, inbox: Callable[[], Path], finalizing: bool = False) -> bool:
        from .private_delivery_discovery import private_answer_images

        text = _presentation_text(private_answer_images(text)[0] if record["event"].get("chat_type") == "p2p" else text)
        _, progress, stream = self._progress_proof(record)
        if not progress.get("verified"):
            return self._deliver(path, record, "progress", text, inbox=inbox)
        if stream.get("edit_limit_reached") or (not finalizing and int(stream.get("edit_attempts") or 0) >= PROGRESS_EDIT_BUDGET):
            return False
        confirmed: dict[str, Any] = {}

        def before_send(_intent: str) -> dict[str, bool]:
            # Persist before the provider write: an uncertain acknowledgement
            # may have consumed an edit too. Restarts retain the same budget.
            stream["edit_attempts"] = int(stream.get("edit_attempts") or 0) + 1
            _atomic_write_json(path, record)
            return {"continue_delivery": True}

        result = update_lark_inbox_reply(project=self.runtime_root, config_path=inbox(), message_id=record["event"]["message_id"],
            text=text, previous_text=_presentation_text(stream.get("confirmed_text") or progress["text"]),
            attempt=stream.get("confirmed_attempt") or progress["attempt"], runner=self._reply_runner,
            source_authority_verifier=partial(self._source_authority, record),
            before_send=before_send, delivery_attempt_recorder=lambda value: confirmed.update(value))
        stream["blocker"] = result.get("blocker")
        if result.get("blocker") == "provider_update_edit_limit":
            stream["edit_limit_reached"] = True
        if result.get("reply_verified") is True and result.get("ok") is True:
            stream.update(confirmed_text=text, confirmed_attempt=confirmed)
        _atomic_write_json(path, record)
        return result.get("ok") is True

    def _close_progress(self: LarkPrivateConversations, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        if self._retire_progress_parts(path, record, inbox=inbox):
            return True
        if not self._edit_progress(path, record, "回答已生成，完整结果将单独发送。", inbox=inbox, finalizing=True):
            return False
        presentation_stream(record)["closed"] = True
        _atomic_write_json(path, record)
        return True

    def _retire_progress_parts(self: LarkPrivateConversations, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        """Verify every concrete draft post before retiring a split draft."""
        from .post_delivery import POST_PARTS_LOCATOR

        _, progress, stream = self._progress_proof(record)
        attempt = stream.get("confirmed_attempt") or progress.get("attempt") or {}
        if not POST_PARTS_LOCATOR.fullmatch(str(attempt.get("message_ref") or "")):
            return False
        result = verify_lark_inbox_reply(project=self.runtime_root, config_path=inbox(), message_id=record["event"]["message_id"],
            text=stream.get("confirmed_text") or progress["text"], attempt=attempt, runner=self._reply_runner,
            reply_placement="source_thread" if record.get("auto_topic_lane") is True else None,
            finalize_reactions=False, source_authority_verifier=partial(self._source_authority, record))
        stream["blocker"] = result.get("blocker")
        verified = result.get("reply_verified") is True and result.get("ok") is True
        if verified:
            stream["closed"] = True
        _atomic_write_json(path, record)
        return verified

    def _continue_progress(self: LarkPrivateConversations, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path]) -> bool:
        """Retire a verified draft before creating the next source-bound one.

        Only a verified closing edit or a definite provider edit-limit rejection
        permits rollover. An uncertain close/create keeps its frozen intent on
        replay; continuation never submits or restarts the Core Turn.
        """
        progress_key, progress, stream = self._progress_proof(record)
        if not progress.get("verified"):
            return False
        if not stream.get("edit_limit_reached") and not self._retire_progress_parts(path, record, inbox=inbox):
            if not self._edit_progress(path, record, "仍在处理，后续进展见下一条消息。", inbox=inbox, finalizing=True) and not stream.get("edit_limit_reached"):
                return False
        segment = int(stream.get("segment") or 0)
        record["deliveries"][f"{progress_key}.{segment}"] = {
            **progress, "text": stream.get("confirmed_text") or progress["text"],
            "attempt": stream.get("confirmed_attempt") or progress["attempt"],
            "edit_attempts": int(stream.get("edit_attempts") or 0), "edit_limit_reached": bool(stream.get("edit_limit_reached")),
        }
        record["deliveries"].pop(progress_key)
        for key in ("confirmed_text", "confirmed_attempt", "edit_attempts", "edit_limit_reached", "blocker", "closed"):
            stream.pop(key, None)
        stream["segment"] = segment + 1
        _atomic_write_json(path, record)
        return True

    def _progress(self: LarkPrivateConversations, path: Path, record: dict[str, Any], *, inbox: Callable[[], Path],
                  session_id: str | None = None, turn_id: str | None = None) -> None:
        progress_key, progress, stream = self._progress_proof(record)
        events = self.core.controller.store.events_after(session_id or record["session_id"], turn_id or record["turn_id"], stream.get("cursor"))
        text = project_progress(stream, events, owner_private=(
            record.get("source", {}).get("private_human_message") is True and record.get("event", {}).get("chat_type") == "p2p"))
        if text:
            # Journal the exact presentation the sender freezes, not the raw
            # Core text. The same conversion also recovers older draft proofs.
            text = _presentation_text(text)
        view = text
        if text and stream.get("segment"):
            text = f"**后续进展 {int(stream['segment']) + 1}**\n\n" + text
        _atomic_write_json(path, record)
        if not text or text == stream.get("confirmed_text"):
            return
        now = _pc().time.time()
        answer_digest = hashlib.sha256(str(stream.get("answer") or "").encode("utf-8")).hexdigest()
        interval = ACTIVITY_UPDATE_INTERVAL_SEC if answer_digest == stream.get("confirmed_answer_digest") else UPDATE_INTERVAL_SEC
        if now - float(stream.get("last_attempt_at") or 0) < interval:
            return
        stream["last_attempt_at"] = now
        _atomic_write_json(path, record)
        # A failed creation must resume its frozen intent rather than trying a
        # new body on an uncertain message. New chunks remain in the view.
        if progress.get("verified") and (stream.get("edit_limit_reached") or int(stream.get("edit_attempts") or 0) >= PROGRESS_EDIT_BUDGET):
            if not self._continue_progress(path, record, inbox=inbox):
                return
            text, progress = f"**后续进展 {int(stream['segment']) + 1}**\n\n{view}", {}
        if not progress.get("verified"):
            initial = str(progress.get("text") or text)
            if self._deliver(path, record, "progress", initial, inbox=inbox):
                stream.update(confirmed_text=initial, confirmed_attempt=progress.get("attempt") or record["deliveries"][progress_key]["attempt"])
                _atomic_write_json(path, record)
        else:
            self._edit_progress(path, record, text, inbox=inbox)
        if stream.get("confirmed_text") == text:
            stream["confirmed_answer_digest"] = answer_digest
            _atomic_write_json(path, record)

    def _repair_recovery_presentation(self: LarkPrivateConversations, path: Path) -> None:
        """Edit proven legacy drafts only; preserve execution and final receipts."""
        with exclusive_file_lock(path, operation="deliver_private_chat_request"):
            record = _pc()._read_json(path)
            repairs = legacy_recovery_repairs(record)
            if record["status"] != "delivered" or not repairs:
                return
            try:
                native = self.core.read_request(record["request_ref"])
                turn = self.core.controller.store.load_turn(record["session_id"], record["turn_id"])
                if (native.get("delivery_verified") is not True or not turn or turn["status"] not in TERMINAL_TURN_STATES
                        or not _same_fields(native, record, "request_ref", "binding_id", "source", "session_id", "turn_id", "context_recovery")):
                    return
                selected = self.bindings.resolve(binding_id=record["binding_id"], include_observation=True, **record["source"])
                if self._recovery_source_observation(record, selected) is None:
                    return
                config: Path | None = None

                def inbox() -> Path:
                    nonlocal config
                    if config is None:
                        config = self._inbox(record)
                    return config

                for repair in repairs:
                    state = record["deliveries"].setdefault(repair["phase"], {})
                    state.setdefault("edit_from", repair["edit_from"])
                    _atomic_write_json(path, record)
                    self._deliver(path, record, repair["phase"], repair["text"], inbox=inbox, edit_only=True)
            except (KeyError, ValueError, OSError):
                return  # Unknown authority or provider effects stay pending.
