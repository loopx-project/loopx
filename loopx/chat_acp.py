"""ACP v1 stdio adapter for owner-configured LoopX Chat Agents."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time
from typing import Any, Callable

from .chat import (
    CHAT_REVIEW_CLOSE_TAG,
    CHAT_REVIEW_OPEN_TAG,
    VisibleResponseStreamFilter,
    parse_agent_response,
    redact_local_paths,
)
from .chat_agent import (
    CodexChatAgentError,
    CodexChatTimeoutError,
    _host_tool_gate,
    _turn_prompt,
)


EventSink = Callable[[str, dict[str, Any]], None]
ACP_PROTOCOL_VERSION = 1
ACP_INITIALIZE_TIMEOUT_DEFAULT_SEC = 30.0
ACP_SESSION_STARTUP_TIMEOUT_DEFAULT_SEC = 45.0


def _reader(stream: Any, messages: "queue.Queue[dict[str, Any] | Exception]") -> None:
    try:
        for line in stream:
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except Exception as exc:  # pragma: no cover - defensive transport path.
                messages.put(exc)
                continue
            if isinstance(payload, dict):
                messages.put(payload)
    finally:
        messages.put(EOFError("ACP stdio stream closed"))


def _compact_label(value: Any, *, fallback: str) -> str:
    return " ".join(str(value or "").split())[:100].strip() or fallback


@dataclass
class ACPStdioAdapter:
    process: subprocess.Popen[str]
    messages: "queue.Queue[dict[str, Any] | Exception]"
    session_id: str
    work_dir: Path
    agent_work_dir: Path
    agent_capabilities: dict[str, Any]
    session_config_options: dict[str, dict[str, Any]] = field(default_factory=dict)
    execution_mode: bool = False
    startup_timeout_sec: float = 30.0
    idle_timeout_sec: float = 180.0
    hard_timeout_sec: float = 900.0
    # Grace window for the agent to exit on stdin EOF and release any
    # per-session lock before LoopX signals the process.
    _graceful_exit_timeout_sec: float = 5.0
    next_request_id: int = 3
    current_request_id: int | None = None
    _write_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _request_lock: threading.Lock = field(default_factory=threading.Lock, repr=False)
    _pending: list[dict[str, Any]] = field(default_factory=list, repr=False)

    @property
    def upstream_thread_id(self) -> str:
        return self.session_id

    @classmethod
    def start(
        cls,
        *,
        command: tuple[str, ...],
        work_dir: Path,
        agent_work_dir: Path | None = None,
        resume_thread_id: str | None = None,
        startup_timeout_sec: float | None = None,
        idle_timeout_sec: float = 180.0,
        hard_timeout_sec: float = 900.0,
        execution_mode: bool = False,
        config_values: dict[str, str] | None = None,
    ) -> "ACPStdioAdapter":
        if not command:
            raise ValueError("ACP command is required")
        executable = command[0]
        resolved = executable if Path(executable).is_absolute() else shutil.which(executable)
        if not resolved:
            raise cls._provider_error(
                "ACP Agent executable is unavailable.",
                "Check the owner-local Agent endpoint command, then retry.",
            )
        argv = (str(resolved), *command[1:])
        try:
            process = subprocess.Popen(
                argv,
                cwd=str(work_dir.expanduser().resolve()),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise cls._provider_error(
                "ACP Agent could not start.",
                "Check the owner-local endpoint and its authentication, then retry.",
            ) from exc
        messages: "queue.Queue[dict[str, Any] | Exception]" = queue.Queue()
        assert process.stdout is not None
        threading.Thread(target=_reader, args=(process.stdout, messages), daemon=True).start()
        initialize_timeout_sec = (
            ACP_INITIALIZE_TIMEOUT_DEFAULT_SEC
            if startup_timeout_sec is None
            else startup_timeout_sec
        )
        adapter = cls(
            process=process,
            messages=messages,
            session_id="",
            work_dir=work_dir.expanduser().resolve(),
            agent_work_dir=agent_work_dir or work_dir.expanduser().resolve(),
            agent_capabilities={},
            execution_mode=execution_mode,
            startup_timeout_sec=initialize_timeout_sec,
            idle_timeout_sec=idle_timeout_sec,
            hard_timeout_sec=hard_timeout_sec,
        )
        try:
            initialized = adapter._request(
                "initialize",
                {
                    "protocolVersion": ACP_PROTOCOL_VERSION,
                    "clientCapabilities": {
                        "fs": {"readTextFile": False, "writeTextFile": False},
                        "terminal": False,
                    },
                    "clientInfo": {
                        "name": "loopx-chat",
                        "title": "LoopX Chat",
                        "version": "0.1.0",
                    },
                },
                request_id=1,
                timeout_sec=initialize_timeout_sec,
            )
            if int(initialized.get("protocolVersion") or 0) != ACP_PROTOCOL_VERSION:
                raise adapter._runtime_error("ACP Agent selected an unsupported protocol version.")
            capabilities = initialized.get("agentCapabilities")
            adapter.agent_capabilities = capabilities if isinstance(capabilities, dict) else {}
            # ACP agents may defer session creation until the client confirms
            # initialization before accepting ``session/new``.
            adapter._notify("initialized", {})
            session_startup_timeout_sec = (
                ACP_SESSION_STARTUP_TIMEOUT_DEFAULT_SEC
                if startup_timeout_sec is None
                else startup_timeout_sec
            )
            session_params = {"cwd": str(adapter.agent_work_dir), "mcpServers": []}
            if resume_thread_id:
                session_capabilities = adapter.agent_capabilities.get("sessionCapabilities")
                session_capabilities = session_capabilities if isinstance(session_capabilities, dict) else {}
                if isinstance(session_capabilities.get("resume"), dict):
                    result = adapter._request(
                        "session/resume",
                        {**session_params, "sessionId": resume_thread_id},
                        request_id=2,
                        timeout_sec=session_startup_timeout_sec,
                    )
                elif bool(adapter.agent_capabilities.get("loadSession")):
                    result = adapter._request(
                        "session/load",
                        {**session_params, "sessionId": resume_thread_id},
                        request_id=2,
                        timeout_sec=session_startup_timeout_sec,
                    )
                else:
                    raise adapter._runtime_error("ACP Agent does not support session recovery.")
                selected = str(result.get("sessionId") or resume_thread_id)
                if selected != resume_thread_id:
                    raise adapter._runtime_error("ACP Agent resumed an unexpected session.")
                adapter.session_id = selected
            else:
                result = adapter._request(
                    "session/new",
                    session_params,
                    request_id=2,
                    timeout_sec=session_startup_timeout_sec,
                )
                adapter.session_id = str(result.get("sessionId") or "")
                if not adapter.session_id:
                    raise adapter._runtime_error("ACP Agent did not return a session id.")
            adapter.session_config_options = adapter._session_config_options(result)
            # ACP session recovery restores the upstream configuration.  Some
            # providers omit configOptions from session/load, so reapplying the
            # stored pick here would turn a valid recovery into a false error.
            if config_values and not resume_thread_id:
                adapter.set_config_values(config_values)
            return adapter
        except Exception:
            adapter._terminate()
            raise

    @staticmethod
    def _provider_error(summary: str, next_action: str) -> CodexChatAgentError:
        return CodexChatAgentError(
            summary,
            error_code="provider_unavailable",
            gate=_host_tool_gate(summary, next_action),
        )

    def _runtime_error(self, summary: str, *, error_code: str = "transport_disconnected") -> CodexChatAgentError:
        return CodexChatAgentError(
            summary,
            error_code=error_code,
            gate=_host_tool_gate(
                summary,
                "Check the owner-local ACP endpoint, then retry or start a new session.",
            ),
        )

    def capabilities(self) -> dict[str, Any]:
        session_capabilities = self.agent_capabilities.get("sessionCapabilities")
        session_capabilities = session_capabilities if isinstance(session_capabilities, dict) else {}
        return {
            "adapter_kind": "acp",
            "protocol_version": ACP_PROTOCOL_VERSION,
            "streaming": True,
            "resume": bool(self.agent_capabilities.get("loadSession"))
            or isinstance(session_capabilities.get("resume"), dict),
            "interrupt": True,
            "tool_calls": True,
        }

    @staticmethod
    def _session_config_options(result: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Normalize ACP's optional session configuration readback.

        Values are retained only in the live adapter.  The dashboard receives a
        deliberately small projection, so provider-specific response fields
        and any local launch details cannot leak through the discovery API.
        """
        raw = result.get("configOptions")
        if not isinstance(raw, list):
            return {}
        options: dict[str, dict[str, Any]] = {}
        for item in raw:
            if not isinstance(item, dict):
                continue
            config_id = str(item.get("id") or "")
            if not config_id.strip() or len(config_id) > 80:
                continue
            if item.get("type", "select") != "select":
                continue
            choices: list[dict[str, str]] = []
            raw_choices = item.get("options") if isinstance(item.get("options"), list) else []
            for entry in raw_choices:
                group_choices = entry.get("options") if isinstance(entry, dict) else None
                candidates = group_choices if isinstance(group_choices, list) else [entry]
                for choice in candidates:
                    if not isinstance(choice, dict):
                        continue
                    value = choice.get("value")
                    if not isinstance(value, str) or not value or len(value) > 160:
                        continue
                    label = _compact_label(choice.get("name") or choice.get("label") or value, fallback=value)
                    choices.append({"value": value, "label": label})
            selected = str(item.get("currentValue") or item.get("value") or "")
            options[config_id] = {
                "label": _compact_label(item.get("name") or item.get("label") or config_id, fallback=config_id),
                "category": str(item.get("category") or ""),
                "selected": selected if len(selected) <= 160 else "",
                "options": choices,
            }
        return options

    def model_options(self) -> dict[str, Any] | None:
        for config_id, option in self.session_config_options.items():
            if option["category"] == "model":
                return {"id": config_id, "label": option["label"], "selected": option["selected"], "options": option["options"]}
        if "model" in self.session_config_options:
            option = self.session_config_options["model"]
            return {"id": "model", "label": option["label"], "selected": option["selected"], "options": option["options"]}
        return None

    @property
    def current_model_id(self) -> str | None:
        model = self.model_options()
        return (str(model["selected"]) or None) if model else None

    def set_config_values(self, values: dict[str, str]) -> None:
        """Apply caller-selected ACP options after the session exists."""
        for config_id, value in values.items():
            if config_id == "model":
                model = self.model_options()
                config_id = str(model["id"]) if model else config_id
            known = self.session_config_options.get(config_id)
            if known is None:
                raise self._runtime_error("ACP Agent does not offer the selected configuration.", error_code="unsupported_configuration")
            allowed = {str(item["value"]) for item in known["options"]}
            if value not in allowed:
                raise self._runtime_error("ACP Agent rejected an unavailable configuration value.", error_code="unsupported_configuration")
            result = self._request(
                "session/set_config_option",
                {"sessionId": self.session_id, "configId": config_id, "type": "id", "value": value},
                timeout_sec=self.startup_timeout_sec,
            )
            self.session_config_options = self._session_config_options(result)

    def _write(self, payload: dict[str, Any]) -> None:
        if self.process.stdin is None:
            raise self._runtime_error("ACP Agent input stream is closed.")
        try:
            with self._write_lock:
                self.process.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
                self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise self._runtime_error("ACP Agent input stream closed unexpectedly.") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _respond_to_agent_request(self, message: dict[str, Any]) -> None:
        request_id = message.get("id")
        method = str(message.get("method") or "")
        if request_id is None or not method:
            return
        if method == "session/request_permission":
            self._write(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"outcome": {"outcome": "cancelled"}},
                }
            )
            return
        self._write(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32601, "message": "LoopX Chat does not expose client host tools."},
            }
        )

    def _next_message(self, timeout_sec: float) -> dict[str, Any]:
        try:
            message = self.messages.get(timeout=max(0.01, timeout_sec))
        except queue.Empty as exc:
            raise TimeoutError("ACP Agent response wait expired") from exc
        if isinstance(message, EOFError):
            raise self._runtime_error("ACP Agent closed its stdio stream.")
        if isinstance(message, Exception):
            raise self._runtime_error("ACP Agent returned an unreadable stdio message.")
        return message

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        request_id: int | None = None,
        timeout_sec: float,
        on_notification: Callable[[dict[str, Any]], None] | None = None,
        on_activity: Callable[[], None] | None = None,
        idle_timeout_sec: float | None = None,
    ) -> dict[str, Any]:
        with self._request_lock:
            selected_id = request_id if request_id is not None else self.next_request_id
            if request_id is None:
                self.next_request_id += 1
            self.current_request_id = selected_id
            self._write(
                {"jsonrpc": "2.0", "id": selected_id, "method": method, "params": params}
            )
            deadline = time.monotonic() + timeout_sec
            idle_deadline = (
                time.monotonic() + idle_timeout_sec if idle_timeout_sec is not None else deadline
            )
            pending = self._pending
            self._pending = []
            try:
                while True:
                    remaining = min(deadline, idle_deadline) - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError(f"ACP Agent timed out during {method}")
                    if pending:
                        message = pending.pop(0)
                    else:
                        try:
                            message = self._next_message(min(0.5, remaining))
                        except TimeoutError:
                            continue
                    if on_activity is not None:
                        on_activity()
                    if idle_timeout_sec is not None:
                        idle_deadline = time.monotonic() + idle_timeout_sec
                    if message.get("id") == selected_id and not message.get("method"):
                        if message.get("error"):
                            raise self._runtime_error(f"ACP Agent rejected {method}.", error_code="protocol_error")
                        result = message.get("result")
                        return result if isinstance(result, dict) else {}
                    if message.get("id") is not None and message.get("method"):
                        self._respond_to_agent_request(message)
                        continue
                    if on_notification is not None:
                        on_notification(message)
            finally:
                self.current_request_id = None

    def start_turn(self, message: str, event_sink: EventSink) -> dict[str, Any]:
        event_sink("turn.started", {"upstream_turn_id": self.session_id})
        event_sink("agent.phase", {"label": "正在连接 ACP Agent"})
        parts: list[str] = []
        display_filter = VisibleResponseStreamFilter(
            protected_paths=[self.work_dir, self.agent_work_dir],
        )
        visible_delta_count = 0
        started_at = time.monotonic()
        last_activity_at = started_at

        def on_notification(payload: dict[str, Any]) -> None:
            nonlocal last_activity_at, visible_delta_count
            last_activity_at = time.monotonic()
            if payload.get("method") != "session/update":
                return
            params = payload.get("params")
            if not isinstance(params, dict) or str(params.get("sessionId") or "") != self.session_id:
                return
            update = params.get("update")
            if not isinstance(update, dict):
                return
            kind = str(update.get("sessionUpdate") or "")
            if kind == "config_option_update":
                self.session_config_options = self._session_config_options(update)
                event_sink("agent.model_config", {"model_id": self.current_model_id})
            elif kind == "agent_message_chunk":
                content = update.get("content")
                if isinstance(content, dict) and content.get("type") == "text":
                    chunk = str(content.get("text") or "")
                    parts.append(chunk)
                    visible = display_filter.feed(chunk)
                    if visible:
                        visible_delta_count += 1
                        event_sink("answer.delta", {"text": visible})
            elif kind == "agent_thought_chunk":
                event_sink("agent.phase", {"label": "ACP Agent 正在分析"})
            elif kind in {"tool_call", "tool_call_update"}:
                title = _compact_label(
                    redact_local_paths(
                        str(update.get("title") or ""),
                        protected_paths=[self.work_dir, self.agent_work_dir],
                    ),
                    fallback="正在使用工具",
                )
                event_sink("agent.phase", {"label": title})
            elif kind == "plan":
                event_sink("agent.phase", {"label": "ACP Agent 已更新计划"})

        request_id = self.next_request_id
        self.next_request_id += 1

        def on_activity() -> None:
            nonlocal last_activity_at
            last_activity_at = time.monotonic()

        try:
            self._request(
                "session/prompt",
                {
                    "sessionId": self.session_id,
                    "prompt": [
                        {
                            "type": "text",
                            "text": _turn_prompt(
                                message,
                                execution_mode=self.execution_mode,
                            ),
                        }
                    ],
                },
                request_id=request_id,
                timeout_sec=self.hard_timeout_sec,
                idle_timeout_sec=self.idle_timeout_sec,
                on_notification=on_notification,
                on_activity=on_activity,
            )
        except TimeoutError as exc:
            elapsed = time.monotonic() - started_at
            error_code = "hard_timeout" if elapsed >= self.hard_timeout_sec else "idle_timeout"
            summary = (
                "ACP Chat turn reached its hard time limit."
                if error_code == "hard_timeout"
                else "ACP Chat turn stopped producing activity."
            )
            raise CodexChatTimeoutError(
                summary,
                error_code=error_code,
                gate=_host_tool_gate(summary, "Interrupt the turn or retry in the same session."),
            ) from exc
        raw_response = "".join(parts)
        visible_tail = display_filter.finish()
        if visible_tail:
            visible_delta_count += 1
            event_sink("answer.delta", {"text": visible_tail})
        response = parse_agent_response(
            raw_response,
            protected_paths=[self.work_dir, self.agent_work_dir],
            team_plan_context=getattr(self, "team_plan_context", None),
        )
        if CHAT_REVIEW_OPEN_TAG not in raw_response or CHAT_REVIEW_CLOSE_TAG not in raw_response:
            event_sink("protocol.warning", {"error_code": "missing_review_envelope"})
        if visible_delta_count == 0:
            event_sink("answer.delta", {"text": str(response.get("message") or "")})
        event_sink("answer.final", {"response": response})
        return response

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        del turn_id
        if self.session_id and self.process.poll() is None:
            self._notify("session/cancel", {"sessionId": self.session_id})

    def close_session(self) -> None:
        if self.current_request_id is not None:
            try:
                self.interrupt_turn()
            except Exception:
                pass
            self._terminate()
            return
        session_capabilities = self.agent_capabilities.get("sessionCapabilities")
        session_capabilities = session_capabilities if isinstance(session_capabilities, dict) else {}
        if self.session_id and isinstance(session_capabilities.get("close"), dict) and self.process.poll() is None:
            try:
                self._request(
                    "session/close",
                    {"sessionId": self.session_id},
                    timeout_sec=min(2.0, self.startup_timeout_sec),
                )
            except Exception:
                pass
        self._terminate()

    def _terminate(self) -> None:
        if self.process.poll() is not None:
            return
        # Close stdin first so the agent sees EOF and shuts down on its own.
        # An ACP agent that persists sessions holds a per-session lock while it
        # runs; signalling it instead leaves that lock behind, and the next
        # `session/load` from a new process is rejected because the session
        # still looks active. Verified against a real host: after stdin EOF the
        # agent exits 0 and the same session id resumes, while a straight
        # terminate produced "Session is active in another process".
        try:
            if self.process.stdin is not None and not self.process.stdin.closed:
                self.process.stdin.close()
        except Exception:
            pass
        try:
            self.process.wait(timeout=self._graceful_exit_timeout_sec)
            return
        except subprocess.TimeoutExpired:
            pass
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)

    def healthcheck(self) -> bool:
        return self.process.poll() is None
