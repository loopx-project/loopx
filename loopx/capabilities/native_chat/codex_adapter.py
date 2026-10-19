"""Codex app-server transport for managed native Chat.

Provider-specific start/resume, observed model settings and interruption stay
here; the Chat controller continues to own Session/Turn admission and effects.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from typing import Any, Callable

from ...chat_agent import CodexChatAgentSession, CodexChatTimeoutError
from ...chat_codex_goal import CodexGoalDriver


@dataclass
class CodexAppServerAdapter:
    session: CodexChatAgentSession
    goal_driver: CodexGoalDriver | None = None

    @staticmethod
    def failed_request_observation(*, codex_bin: str, codex_home: Path,
                                   thread_ids: list[str], turn_id: str) -> dict[str, Any] | None:
        """Read persisted native metadata/items; create no native conversation.

        The initialized stdio client is not a listener or a model runner. It
        uses the same native dispatcher as authentication/control and never
        calls thread/start, resume, inject or turn/start.
        """
        from ...chat_agent import _reader

        process = subprocess.Popen([codex_bin, "app-server", "--listen", "stdio://"],
            cwd=str(codex_home), env={**os.environ, "CODEX_HOME": str(codex_home)},
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, encoding="utf-8", bufsize=1)
        native = CodexChatAgentSession(process=process, messages=queue.Queue(), thread_id="",
            work_dir=codex_home, response_timeout_sec=5)
        try:
            threading.Thread(target=_reader, args=(process.stdout, native.messages), daemon=True).start()
            native._request("initialize", {"clientInfo": {"name": "loopx_chat", "version": "0.1.0"},
                "capabilities": {"experimentalApi": True}})
            native._notify("initialized", {})
            deadline = time.monotonic() + 5
            for thread_id in dict.fromkeys(thread_ids):
                native.thread_id = thread_id
                native.response_timeout_sec = max(.01, deadline - time.monotonic())
                proof = native.failed_request_readback(turn_id)
                if proof is not None:
                    return proof
                if time.monotonic() >= deadline:
                    break
            return None
        finally:
            native.close()

    @property
    def current_model_id(self) -> str | None:
        return getattr(self.session, "resolved_model", None)

    @property
    def model_settings(self) -> dict[str, str | None]:
        return {"agent_reasoning_effort": getattr(self.session, "resolved_reasoning_effort", None),
                "agent_service_tier": getattr(self.session, "service_tier", None)}

    @property
    def upstream_thread_id(self) -> str:
        return self.session.thread_id

    @classmethod
    def start(
        cls,
        *,
        codex_bin: str,
        work_dir: Path,
        goal_id: str | None,
        objective: str,
        history: list[dict[str, Any]] | None = None,
        resume_thread_id: str | None = None,
        startup_timeout_sec: float = 30.0,
        idle_timeout_sec: float = 180.0,
        hard_timeout_sec: float = 900.0,
        execution_mode: bool = False,
        runtime_profile: str = "restricted",
        sandbox: str | None = None,
        project_context: dict[str, str] | None = None,
        codex_home: Path | None = None,
        model: str | None = None,
        reasoning_effort: str | None = None,
        service_tier: str | None = None,
        dynamic_tools: list[dict[str, Any]] | None = None,
    ) -> "CodexAppServerAdapter":
        history_items = []
        # Migration restores the full visible conversation in native history.
        # The objective is repeated per Turn; putting the transcript there
        # duplicates it indefinitely and prevents native compaction from helping.
        for item in ([] if resume_thread_id else history or []):
            role, text = item.get("role", "user"), str(item.get("content") or "")
            if role not in {"user", "assistant"}:
                raise ValueError("Visible Chat history requires user or assistant roles")
            if text.strip():
                history_items.append({
                    "type": "message", "role": role,
                    "content": [{"type": "output_text" if role == "assistant" else "input_text", "text": text}],
                })
        retry_timeout = bool(resume_thread_id)
        while True:
            try:
                session = CodexChatAgentSession.start(
                    codex_bin=codex_bin,
                    work_dir=work_dir,
                    goal_id=goal_id,
                    objective=objective,
                    response_timeout_sec=startup_timeout_sec,
                    idle_timeout_sec=idle_timeout_sec,
                    hard_timeout_sec=hard_timeout_sec,
                    execution_mode=execution_mode,
                    runtime_profile=runtime_profile,
                    sandbox=sandbox,
                    project_context=project_context,
                    resume_thread_id=resume_thread_id,
                    codex_home=codex_home,
                    model=model,
                    reasoning_effort=reasoning_effort,
                    host_config={"service_tier": service_tier} if service_tier else None,
                    dynamic_tools=dynamic_tools,
                )
                if not resume_thread_id:
                    try:
                        # A native thread/start id alone is not durable until
                        # its first Turn. Naming this new managed conversation
                        # materializes the empty thread before Core stores its
                        # binding, without sending a model request.
                        session._request("thread/name/set", {
                            "threadId": session.thread_id, "name": "LoopX Chat",
                        })
                        if history_items:
                            # Persist before Core publishes the upstream binding,
                            # even if the service restarts before its first Turn.
                            # An unknown outcome fails closed: never retry or
                            # fall back to replaying history inside a user Turn.
                            session._request("thread/inject_items", {
                                "threadId": session.thread_id, "items": history_items,
                            })
                    except Exception:
                        session.close()
                        raise
                return cls(session)
            except CodexChatTimeoutError as exc:
                if not retry_timeout or exc.error_code != "response_timeout":
                    raise
                # start() has closed the failed process; no Turn was dispatched.
                # Retry the same known thread once, never an unknown thread/start
                # effect, a host gate or a request that has already begun.
                retry_timeout = False
                logging.getLogger(__name__).warning(
                    "Codex resumed-session startup timed out; retrying once before Turn dispatch"
                )

    def capabilities(self) -> dict[str, Any]:
        return {
            "adapter_kind": "codex_app_server",
            "streaming": True,
            "resume": True,
            "interrupt": True,
            "steering": True,
        }

    def start_turn(self, message: str, event_sink: Callable[[str, dict[str, Any]], None]) -> dict[str, Any]:
        return self.session.send(message, on_event=event_sink)

    def steer_turn(self, message: str, expected_turn_id: str) -> str:
        return self.session.steer(message, expected_turn_id=expected_turn_id)

    def start_turn_with_attachments(
        self,
        message: str,
        event_sink: Callable[[str, dict[str, Any]], None],
        attachments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return self.session.send(message, attachments=attachments, on_event=event_sink)

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        driver = self.goal_driver
        if driver is not None:
            driver.pause()
            return
        self.session.interrupt(turn_id)

    def close_session(self) -> None:
        try:
            driver = self.goal_driver
            if driver is not None:
                driver.pause()
        finally:
            self.session.close()

    def healthcheck(self) -> bool:
        return self.session.process.poll() is None
