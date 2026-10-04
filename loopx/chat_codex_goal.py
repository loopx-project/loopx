"""Explicit native Goal continuation inside an existing read-only Goal Chat.

This is Codex transport supervision, not a LoopX scheduler or acceptance owner.
One local Chat turn observes multiple native turns; ordinary messages never
activate this driver. Native usage is retained by Codex across explicit resumes.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Callable

from .chat import VisibleResponseStreamFilter, parse_agent_response
from .chat_agent import (
    _agent_item_text,
    _event_thread_id,
    _event_turn_id,
    _host_tool_gate,
    _terminal_turn_error,
)

if TYPE_CHECKING:
    from .chat_agent import CodexChatAgentSession


USAGE = (
    "/goal start --tokens N <objective> · /goal resume --tokens N · /goal status. "
    "Use Stop to pause. N is the total native token allowance, including prior "
    "usage and input context; it is not a fresh allowance on resume. In-flight "
    "requests can exceed this allowance."
)
UPSTREAM_MODE = "chat_native_goal"


class NativeGoalStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    BLOCKED = "blocked"
    USAGE_LIMITED = "usageLimited"
    BUDGET_LIMITED = "budgetLimited"
    COMPLETE = "complete"


@dataclass(frozen=True)
class NativeGoalCommand:
    operation: str
    tokens: int | None = None
    objective: str | None = None


def parse_native_goal_command(message: str) -> NativeGoalCommand | None:
    text = message.strip()
    if not re.match(r"^/goal(?:\s|$)", text):
        return None
    if text in {"/goal", "/goal help"}:
        return NativeGoalCommand("help")
    if text == "/goal status":
        return NativeGoalCommand("status")
    match = re.fullmatch(
        r"/goal (start|resume) --tokens ([1-9][0-9]*)(?:\s+(.+))?", text, re.S
    )
    if match:
        operation, tokens, objective = match.groups()
        if int(tokens) <= 2_147_483_647 and (
            (operation == "start" and objective and len(objective) <= 3000)
            or (operation == "resume" and objective is None)
        ):
            return NativeGoalCommand(operation, int(tokens), objective)
    raise ValueError(USAGE + " Start requires an objective of at most 3000 characters.")


def validate_goal_chat(session: dict[str, Any], attachments: list[Any] | None) -> None:
    if (
        session.get("agent_id") != "codex"
        or session.get("session_mode") == "attached_host"
        or session.get("channel_id") != f"goal.{session.get('goal_id')}"
    ):
        raise ValueError(
            "/goal requires the managed Codex endpoint in the existing Goal Chat."
        )
    if attachments:
        raise ValueError("Send images as an ordinary message before starting /goal.")


class CodexGoalDriver:
    def __init__(self, session: CodexChatAgentSession) -> None:
        self.session = session
        self.stopped = threading.Event()
        self.mutation_lock = threading.RLock()
        self.turn_start_handler: Callable[[str], None] | None = None
        self.resume_context: str | None = None

    def read(self) -> dict[str, Any] | None:
        raw = self.session._request(
            "thread/goal/get", {"threadId": self.session.thread_id}
        ).get("goal")
        if raw is None:
            return None
        if not isinstance(raw, dict) or raw.get("threadId") != self.session.thread_id:
            raise self.session._runtime_error(
                "Codex returned a Goal for an unexpected thread."
            )
        try:
            NativeGoalStatus(raw["status"])
        except (KeyError, ValueError) as exc:
            raise self.session._runtime_error(
                "Codex returned an unsupported Goal status."
            ) from exc
        return raw

    def pause(self) -> None:
        """Fence activation before pausing; stop the in-flight native turn too."""
        self.stopped.set()
        with self.mutation_lock:
            current = self.read()
            if current and current["status"] == NativeGoalStatus.ACTIVE:
                self.session._request(
                    "thread/goal/set",
                    {
                        "threadId": self.session.thread_id,
                        "status": "paused",
                    },
                )
            self.session.interrupt()

    @staticmethod
    def compact(goal: dict[str, Any] | None) -> dict[str, Any]:
        return (
            {
                key: goal.get(key)
                for key in (
                    "status",
                    "tokenBudget",
                    "tokensUsed",
                    "timeUsedSeconds",
                )
            }
            if goal
            else {"status": "absent"}
        )

    def _response(self, goal: dict[str, Any] | None, text: str = "", *, messages: list[str] | None = None) -> dict[str, Any]:
        facts = self.compact(goal)
        status = facts["status"]
        note = f"Codex Goal: {status}."
        if goal:
            note += f" Tokens: {facts['tokensUsed']} / {facts['tokenBudget']}."
        note += " This is host execution status; LoopX work acceptance is unchanged."
        response = parse_agent_response(text, protected_paths=[self.session.work_dir])
        if messages is not None:
            response["message"] = "\n\n---\n\n".join(messages)
        # Autonomous execution has no fresh action-confirmation context. It
        # cannot manufacture a canonical acceptance or a handoff receipt.
        return {
            "schema_version": response["schema_version"],
            "message": (
                str(response.get("message") or "").strip() + "\n\n" + note
            ).strip(),
            "proposals": [],
            "protected_action": None,
            "gate": None
            if status in {"complete", "absent"}
            else _host_tool_gate(
                note,
                "Use /goal resume --tokens N to continue, or send an ordinary message.",
            ),
        }

    def run(
        self, command: NativeGoalCommand, emit: Callable[[str, dict[str, Any]], None],
        *, execution_context: str | None = None,
    ) -> dict[str, Any]:
        if command.operation == "help":
            return parse_agent_response(USAGE, protected_paths=[self.session.work_dir])
        if self.session.execution_mode or self.session.sandbox != "read-only":
            raise ValueError("Goal Chat continuation retains the read-only sandbox.")
        with self.mutation_lock:
            current = self.read()
            if command.operation == "status":
                return self._response(current)
            if self.stopped.is_set():
                raise self.session._runtime_error(
                    "Goal continuation was stopped before activation."
                )
            if command.operation == "start":
                if current and current["status"] != NativeGoalStatus.COMPLETE:
                    raise ValueError(
                        "A native Goal already exists. Resume it to retain its objective and usage."
                    )
                objective = (
                    "Work on this objective in the current Goal conversation, using its existing "
                    "read-only sandbox. Analyze, inspect and verify; report the result in the "
                    "conversation. Do not claim that LoopX tasks or the canonical Goal were "
                    "accepted or completed. Stop if tools or authority are insufficient. "
                    "Mark only the native Codex Goal complete once this objective is satisfied.\n\n"
                    + str(command.objective)
                )
                if execution_context:
                    objective = execution_context + "\n\nCurrent Goal objective:\n" + str(command.objective)
            else:
                self.resume_context = execution_context
                if not current or current["status"] in {
                    NativeGoalStatus.ACTIVE,
                    NativeGoalStatus.COMPLETE,
                }:
                    raise ValueError(
                        "Resume requires a paused, blocked or limited native Goal."
                    )
                if command.tokens <= current.get("tokensUsed", 0):
                    raise ValueError(
                        "Resume token allowance must exceed the native tokens already used."
                    )
                objective = None
            # The read response is an ordered transport barrier. Old queued
            # notifications are historical and must not become this run's text.
            while not self.session._pending_events.empty():
                self.session._pending_events.get_nowait()
            try:
                self.session._request(
                    "thread/goal/set",
                    {
                        "threadId": self.session.thread_id,
                        "status": "active",
                        "tokenBudget": command.tokens,
                        **({"objective": objective} if objective is not None else {}),
                    },
                )
            except Exception:
                # A lost response does not prove that activation failed.
                self._stop_owned_transport_on_failure()
                raise
        try:
            return self._observe(emit)
        finally:
            # A host timeout/error/Stop cannot leave an unobserved Goal running.
            # If reconciliation itself fails, terminate this owned transport.
            self._stop_owned_transport_on_failure()
            self.session.current_turn_id = ""

    def _stop_owned_transport_on_failure(self) -> None:
        try:
            self.pause()
        except Exception:
            self.session.close()

    def _observe(self, emit: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
        deadline = (None if self.session.hard_timeout_sec is None
                    else time.monotonic() + self.session.hard_timeout_sec)
        parts: list[str] = []
        completed_messages: list[str] = []
        display = VisibleResponseStreamFilter(protected_paths=[self.session.work_dir])
        current_turn = ""
        delta_items: set[str] = set()
        goal = self.read()
        last_facts = None
        while True:
            if goal is None:
                raise self.session._runtime_error("The active native Goal disappeared.")
            facts = self.compact(goal)
            if facts != last_facts:
                emit("native_goal.status", facts)
                last_facts = facts
            if self.stopped.is_set() or (
                goal["status"] != NativeGoalStatus.ACTIVE
                and (
                    goal["status"] not in {NativeGoalStatus.COMPLETE, NativeGoalStatus.BLOCKED}
                    or (not current_turn and self.session._pending_events.empty())
                )
            ):
                tail = display.finish()
                if tail:
                    emit("answer.delta", {"text": tail})
                if parts:
                    completed_messages.append(parse_agent_response("".join(parts), protected_paths=[self.session.work_dir])["message"])
                return self._response(goal, messages=completed_messages)
            if deadline is not None and time.monotonic() >= deadline:
                raise self.session._timeout_error(
                    "hard_timeout",
                    "Native Goal reached the Chat time limit; resume it in this conversation.",
                )
            event = self.session._next_event(
                deadline=deadline if deadline is not None else time.monotonic() + self.session.idle_timeout_sec
            )
            if self.session._check_server_gate(event):
                continue
            if _event_thread_id(event) not in {"", self.session.thread_id}:
                continue
            method, params = event.get("method"), event.get("params") or {}
            if method == "thread/goal/updated":
                # Notifications may be replayed on resume. They trigger a read,
                # never a lifecycle decision based on their stale payload.
                goal = self.read()
                continue
            turn_id = _event_turn_id(event)
            if method == "turn/started" and turn_id:
                current_turn = self.session.current_turn_id = turn_id
                delta_items.clear()
                emit("turn.started", {"upstream_turn_id": turn_id})
                if self.resume_context:
                    context, self.resume_context = self.resume_context, None
                    self.session.steer(context, expected_turn_id=turn_id)
                if self.turn_start_handler:
                    self.turn_start_handler(turn_id)
            if turn_id and turn_id != current_turn:
                continue
            if method == "item/agentMessage/delta":
                text = str(params.get("delta") or "")
                delta_items.add(str(params.get("itemId") or ""))
            elif method == "item/completed":
                item = params.get("item") or {}
                text = (
                    ""
                    if str(item.get("id") or "") in delta_items
                    else _agent_item_text(event)
                )
                text = text + "\n\n" if text else ""
            else:
                text = ""
            if text:
                parts.append(text)
                visible = display.feed(text)
                if visible:
                    emit("answer.delta", {"text": visible})
            if method == "turn/completed":
                turn = params.get("turn") or {}
                if turn.get("status") == "failed":
                    raise _terminal_turn_error(
                        turn.get("error"), "Native Goal turn failed."
                    )
                if parts:
                    completed_messages.append(parse_agent_response("".join(parts), protected_paths=[self.session.work_dir])["message"])
                    parts.clear()
                tail = display.finish()
                if tail:
                    emit("answer.delta", {"text": tail})
                display = VisibleResponseStreamFilter(protected_paths=[self.session.work_dir])
                current_turn = self.session.current_turn_id = ""
                goal = self.read()
            elif method == "error" and not params.get("willRetry"):
                raise _terminal_turn_error(
                    params.get("error"), "Native Goal execution failed."
                )
            elif method == "item/started":
                emit("agent.phase", {"label": "Goal 正在持续推进", "method": method})
