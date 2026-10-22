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

from ...chat_agent import (
    CodexChatAgentError, CodexChatAgentSession, CodexChatTimeoutError,
    _retry_error_details, _routing_discovery_timeout,
)
from ...chat_codex_goal import CodexGoalDriver


def failed_request_readback(session: CodexChatAgentSession, turn_id: str) -> dict[str, Any] | None:
    """Observe a historical pre-execution failure without resuming it.

    Unlike a same-worker retry, a migration may inspect an older failure.
    Every native Turn must be terminal. Routing recovery additionally
    requires the latest Turn and permits only user input plus native
    compaction bookkeeping: no answer, tool, correction or unknown item.
    The Core still owns any new continuation admission.
    """
    deadline = time.monotonic() + session.response_timeout_sec

    def terminal() -> dict[str, Any] | None:
        turns = list(session._control_items("thread/turns/list", {
            "sortDirection": "desc", "itemsView": "summary",
        }, deadline))
        ids = [item.get("id") for item in turns]
        if (not ids or any(not isinstance(value, str) or not value for value in ids)
                or len(set(ids)) != len(ids)
                or any(item.get("status") not in {"completed", "failed", "interrupted"} for item in turns)):
            return None
        selected = next((item for item in turns if item["id"] == turn_id), None)
        if not selected or selected.get("status") != "failed":
            return None
        if _routing_discovery_timeout(selected.get("error")):
            # A later request may supersede this one. Compaction is native
            # bookkeeping, but only the exact latest routing failure can
            # use that exception to the historical user-only inventory.
            if ids[0] != turn_id:
                return None
            return {"codex_error_info": "other", "pre_execution_routing_timeout": True}
        info = _retry_error_details(selected.get("error")).get("retry", {}).get("codex_error_info")
        # `other` is evidence of a failed native request, not permission to
        # retry it. The typed admission owner separately decides whether
        # this proven user-only failure is eligible for one continuation.
        if info in ("contextWindowExceeded", "serverOverloaded", "internalServerError", "other", "usageLimitExceeded"):
            return {"codex_error_info": info}
        if isinstance(info, dict) and len(info) == 1:
            detail = next(iter(info.values()))
            status = detail.get("httpStatusCode") if isinstance(detail, dict) else False
            if isinstance(detail, dict) and "httpStatusCode" in detail and (
                status is None or type(status) is int and 500 <= status <= 599
            ):
                return {"codex_error_info": info}
        return None

    try:
        proof = terminal()
        if proof is None:
            return None
        items = list(session._control_items("thread/items/list", {"turnId": turn_id}, deadline))
        routing = proof.get("pre_execution_routing_timeout") is True
        allowed = {"userMessage", "contextCompaction"} if routing else {"userMessage"}
        ids: set[str] = set()
        users = 0
        for entry in items:
            item = entry.get("item")
            if (entry.get("turnId") != turn_id or not isinstance(item, dict)
                    or item.get("type") not in allowed or not item.get("id")
                    or not isinstance(item["id"], str) or item["id"] in ids):
                return None
            ids.add(item["id"])
            users += item["type"] == "userMessage"
        if users != 1 or (not routing and len(items) != 1) or terminal() != proof:
            return None
        return {"thread_id": session.thread_id, "turn_id": turn_id,
                "only_original_user_item": len(items) == 1,
                **({"only_pre_execution_items": True} if routing else {}), **proof}
    except (CodexChatAgentError, OSError, TypeError, ValueError):
        return None


@dataclass
class CodexAppServerAdapter:
    session: CodexChatAgentSession
    goal_driver: CodexGoalDriver | None = None

    @staticmethod
    def failed_request_observation(*, codex_bin: str, codex_home: Path,
                                   thread_ids: list[str], turn_id: str,
                                   model_auth_home: Path | None = None) -> dict[str, Any] | None:
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
                proof = failed_request_readback(native, turn_id)
                if proof is not None:
                    if proof["codex_error_info"] == "usageLimitExceeded":
                        # Reuse the optional provider's TS availability owner;
                        # neither a reset clock nor a generic successful RPC
                        # establishes usable allowance. Absence fails closed.
                        try:
                            from loopx_codex_provider_routing.native import policy
                            from loopx_codex_provider_routing.native_host import read_usage
                            from .codex_auth import CodexHostModelAuth, for_isolated_process
                            import json

                            shared = for_isolated_process(model_auth_home, codex_home, codex_bin) if model_auth_home else None
                            auth = (shared or CodexHostModelAuth(codex_home, codex_bin)).read()
                            usage = read_usage(json.dumps({"auth_mode": "chatgpt", "tokens": {
                                "access_token": auth["accessToken"], "account_id": auth["chatgptAccountId"]}}).encode())
                            now = time.time()
                            result = policy({"now": now, "policy": {"enabled": False, "slots": [],
                                "cooldown_seconds": 300}, "active": "current", "observations": [
                                    {"slot": "current", "observed_at": now, "status": "ok", "usage": usage}],
                                "last_switch_at": None, "recovery_required": False, "login_matches": True})
                            proof.update(quota_available=result["accounts"][0]["availability"] == "available",
                                quota_observed_at=now)
                        except (ImportError, KeyError, OSError, RuntimeError, ValueError, subprocess.SubprocessError):
                            proof["quota_available"] = False
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
