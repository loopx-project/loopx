"""Private runtime adapters for Claude Code and direct model providers."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
from typing import Any, Callable
from urllib import request
import uuid

from .chat import VisibleResponseStreamFilter, parse_agent_response
from .chat_agent import CodexChatAgentError, _host_tool_gate, _turn_prompt


EventSink = Callable[[str, dict[str, Any]], None]

_PRIVATE_PARTS = {".git", ".env", ".ssh", ".aws", ".config", "node_modules"}


def _read_only_tools(provider: str) -> list[dict[str, Any]]:
    definitions = [
        ("list_files", "List public project files matching a bounded glob.", {
            "type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"], "additionalProperties": False,
        }),
        ("search_text", "Search public project text files for a literal string.", {
            "type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"], "additionalProperties": False,
        }),
        ("read_file", "Read a bounded public project text file by relative path.", {
            "type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"], "additionalProperties": False,
        }),
    ]
    if provider == "anthropic":
        return [{"name": name, "description": description, "input_schema": schema} for name, description, schema in definitions]
    return [{"type": "function", "name": name, "description": description, "parameters": schema, "strict": True} for name, description, schema in definitions]


def _safe_project_path(root: Path, value: Any) -> Path:
    relative = Path(str(value or "").strip())
    if not str(relative) or relative.is_absolute() or ".." in relative.parts:
        raise ValueError("tool path must be project-relative")
    if any(part in _PRIVATE_PARTS or part.startswith(".env") for part in relative.parts):
        raise ValueError("tool path is outside the public project boundary")
    selected = (root / relative).resolve()
    try:
        selected.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("tool path escaped the project root") from exc
    return selected


def _execute_read_only_tool(root: Path, name: str, arguments: Any) -> str:
    if not isinstance(arguments, dict):
        return json.dumps({"ok": False, "error": "tool arguments must be an object"})
    try:
        if name == "list_files":
            pattern = str(arguments.get("pattern") or "").strip()
            if not pattern or pattern.startswith("/") or ".." in Path(pattern).parts:
                raise ValueError("glob must stay inside the project")
            rows = []
            for path in root.glob(pattern):
                try:
                    relative = path.resolve().relative_to(root.resolve())
                except ValueError:
                    continue
                if path.is_file() and not any(part in _PRIVATE_PARTS or part.startswith(".env") for part in relative.parts):
                    rows.append(relative.as_posix())
                if len(rows) >= 80:
                    break
            return json.dumps({"ok": True, "files": sorted(rows)}, ensure_ascii=False)
        if name == "read_file":
            path = _safe_project_path(root, arguments.get("path"))
            if not path.is_file() or path.stat().st_size > 256_000:
                raise ValueError("file is unavailable or exceeds the read limit")
            return json.dumps({"ok": True, "text": path.read_text(encoding="utf-8", errors="replace")[:64_000]}, ensure_ascii=False)
        if name == "search_text":
            query = str(arguments.get("query") or "").strip()
            if not query or len(query) > 240:
                raise ValueError("search query must be 1 to 240 characters")
            matches: list[dict[str, Any]] = []
            for path in root.rglob("*"):
                if not path.is_file() or path.stat().st_size > 256_000:
                    continue
                try:
                    relative = path.resolve().relative_to(root.resolve())
                except ValueError:
                    continue
                if any(part in _PRIVATE_PARTS or part.startswith(".env") for part in relative.parts):
                    continue
                try:
                    for line_number, line in enumerate(path.read_text(encoding="utf-8", errors="strict").splitlines(), 1):
                        if query in line:
                            matches.append({"file": relative.as_posix(), "line": line_number, "text": line[:500]})
                            if len(matches) >= 40:
                                return json.dumps({"ok": True, "matches": matches}, ensure_ascii=False)
                except (OSError, UnicodeError):
                    continue
            return json.dumps({"ok": True, "matches": matches}, ensure_ascii=False)
        raise ValueError("unknown read-only tool")
    except (OSError, ValueError) as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


def _provider_error(label: str, next_action: str) -> CodexChatAgentError:
    return CodexChatAgentError(
        f"{label} is unavailable for LoopX Chat.",
        error_code="provider_unavailable",
        gate=_host_tool_gate(f"{label} is unavailable for LoopX Chat.", next_action),
    )


@dataclass
class ClaudeCodeAdapter:
    claude_bin: str
    work_dir: Path
    session_id: str
    tool_scope: str = "read_only"
    context_summary: str = ""
    execution_mode: bool = False
    model: str | None = None
    reasoning_effort: str | None = None
    resumed: bool = False
    current_process: subprocess.Popen[str] | None = field(default=None, repr=False)
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False)

    @property
    def upstream_thread_id(self) -> str:
        return self.session_id

    @classmethod
    def start(
        cls,
        *,
        claude_bin: str,
        work_dir: Path,
        resume_thread_id: str | None = None,
        tool_scope: str = "read_only",
        context_summary: str = "",
        execution_mode: bool = False,
        model: str | None = None,
        reasoning_effort: str | None = None,
    ) -> "ClaudeCodeAdapter":
        resolved = shutil.which(claude_bin)
        if not resolved:
            raise _provider_error(
                "Claude Code",
                "Install Claude Code or select another healthy Agent endpoint.",
            )
        if tool_scope not in {"read_only", "disabled"}:
            raise ValueError("Claude Code tool_scope must be read_only or disabled")
        if tool_scope == "disabled" and execution_mode:
            raise ValueError("Claude Code execution requires an enabled tool scope")
        return cls(
            claude_bin=resolved,
            work_dir=work_dir.resolve(),
            session_id=resume_thread_id or str(uuid.uuid4()),
            tool_scope=tool_scope,
            context_summary=context_summary,
            execution_mode=execution_mode,
            model=model,
            reasoning_effort=reasoning_effort,
            resumed=bool(resume_thread_id),
        )

    def capabilities(self) -> dict[str, Any]:
        return {
            "adapter_kind": "claude_code_cli",
            "streaming": True,
            "resume": True,
            "interrupt": True,
            "tool_calls": self.tool_scope != "disabled",
            "tool_scope": "workspace_write" if self.execution_mode else self.tool_scope,
            "execution_mode": self.execution_mode,
            "model": self.model,
        }

    def start_turn(self, message: str, event_sink: EventSink) -> dict[str, Any]:
        writable = self.execution_mode
        command = [
            self.claude_bin,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            "acceptEdits" if writable else "plan",
            "--tools",
            "Read,Glob,Grep,Bash,Edit,Write" if writable else (
                "Read,Glob,Grep" if self.tool_scope == "read_only" else ""
            ),
            "--disable-slash-commands",
        ]
        if writable:
            # Only an explicitly admitted task opts into shell tools. Ordinary
            # Chat keeps its read-only tool set.
            command.extend(["--allowedTools", "Read,Glob,Grep,Bash,Edit,Write"])
        if self.model:
            command.extend(["--model", self.model])
        if self.reasoning_effort:
            command.extend(["--effort", self.reasoning_effort])
        if self.resumed:
            command.extend(["--resume", self.session_id])
        else:
            command.extend(["--session-id", self.session_id])
        command.append(_turn_prompt(
            message, context_summary=self.context_summary,
            execution_mode=self.execution_mode,
        ))
        event_sink("turn.started", {"upstream_turn_id": self.session_id})
        event_sink("agent.phase", {"label": "正在连接 Claude Code"})
        try:
            process = subprocess.Popen(
                command,
                cwd=str(self.work_dir),
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True, encoding="utf-8", errors="replace",
                bufsize=1,
            )
        except OSError as exc:
            raise _provider_error("Claude Code", "Check the local Claude Code installation.") from exc
        with self.lock:
            self.current_process = process
        parts: list[str] = []
        result_text = ""
        result_error = False
        display_filter = VisibleResponseStreamFilter(protected_paths=[self.work_dir])
        visible_delta_count = 0
        try:
            assert process.stdout is not None
            for raw_line in process.stdout:
                try:
                    payload = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(payload, dict):
                    continue
                session_id = payload.get("session_id")
                if isinstance(session_id, str) and session_id:
                    self.session_id = session_id
                if payload.get("type") == "stream_event":
                    event = payload.get("event")
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "content_block_start":
                        block = event.get("content_block")
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            event_sink("agent.phase", {"label": "Claude Code 正在检查上下文"})
                    if event.get("type") == "content_block_delta":
                        delta = event.get("delta")
                        if isinstance(delta, dict) and delta.get("type") == "text_delta":
                            chunk = str(delta.get("text") or "")
                            parts.append(chunk)
                            visible = display_filter.feed(chunk)
                            if visible:
                                visible_delta_count += 1
                                event_sink("answer.delta", {"text": visible})
                elif payload.get("type") == "result":
                    result_text = str(payload.get("result") or "")
                    result_error = payload.get("is_error") is True
            return_code = process.wait()
            if return_code != 0 or result_error:
                raise _provider_error("Claude Code", "Sign in to Claude Code and retry this session.")
        finally:
            with self.lock:
                self.current_process = None
        streamed_response = "".join(parts)
        raw_response = streamed_response if streamed_response.strip() else result_text
        if not raw_response.strip():
            summary = "Claude Code completed without returning an answer."
            raise CodexChatAgentError(
                summary,
                error_code="provider_empty_response",
                gate=_host_tool_gate(
                    summary,
                    "Retry this session or select another healthy Agent endpoint.",
                ),
            )
        if not parts and result_text:
            visible = display_filter.feed(result_text)
            if visible:
                visible_delta_count += 1
                event_sink("answer.delta", {"text": visible})
        visible_tail = display_filter.finish()
        if visible_tail:
            visible_delta_count += 1
            event_sink("answer.delta", {"text": visible_tail})
        response = parse_agent_response(
            raw_response,
            protected_paths=[self.work_dir],
            team_plan_context=getattr(self, "team_plan_context", None),
        )
        self.resumed = True
        event_sink("agent.phase", {"label": "正在整理回答"})
        if visible_delta_count == 0:
            event_sink("answer.delta", {"text": str(response.get("message") or "")})
        event_sink("answer.final", {"response": response})
        return response

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        del turn_id
        with self.lock:
            process = self.current_process
        if process is not None and process.poll() is None:
            process.terminate()

    def close_session(self) -> None:
        self.interrupt_turn()

    def healthcheck(self) -> bool:
        return bool(shutil.which(self.claude_bin))


@dataclass
class DirectModelAdapter:
    provider: str
    model: str
    api_key: str = field(repr=False)
    work_dir: Path
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    history: list[dict[str, Any]] = field(default_factory=list)
    timeout_sec: float = 180.0

    @property
    def upstream_thread_id(self) -> str:
        return self.session_id

    def capabilities(self) -> dict[str, Any]:
        return {
            "adapter_kind": f"{self.provider}_messages_api",
            "streaming": False,
            "resume": True,
            "interrupt": False,
            "tool_calls": True,
            "tool_scope": "read_only",
        }

    def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        encoded = json.dumps(body).encode("utf-8")
        req = request.Request(url, data=encoded, headers=headers, method="POST")
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as response:  # noqa: S310 - fixed provider URL.
                payload = json.loads(response.read().decode("utf-8"))
        except Exception as exc:
            raise _provider_error(
                self.provider.title(),
                "Check the local credential reference, network, and selected model.",
            ) from exc
        if not isinstance(payload, dict):
            raise _provider_error(self.provider.title(), "Select another healthy Agent endpoint.")
        return payload

    def start_turn(self, message: str, event_sink: EventSink) -> dict[str, Any]:
        event_sink("turn.started", {"upstream_turn_id": self.session_id})
        event_sink("agent.phase", {"label": f"正在连接 {self.provider.title()}"})
        prompt = _turn_prompt(message)
        messages: list[dict[str, Any]] = [*self.history, {"role": "user", "content": prompt}]
        raw_response = ""
        for _round in range(5):
            if self.provider == "anthropic":
                payload = self._post(
                    "https://api.anthropic.com/v1/messages",
                    {"model": self.model, "max_tokens": 2048, "messages": messages, "tools": _read_only_tools("anthropic")},
                    {
                        "content-type": "application/json",
                        "x-api-key": self.api_key,
                        "anthropic-version": "2023-06-01",
                    },
                )
                content = payload.get("content")
                blocks = content if isinstance(content, list) else []
                raw_response = "".join(str(block.get("text") or "") for block in blocks if isinstance(block, dict) and block.get("type") == "text")
                calls = [block for block in blocks if isinstance(block, dict) and block.get("type") == "tool_use"]
                if not calls:
                    break
                event_sink("agent.phase", {"label": f"{self.provider.title()} 正在检查项目上下文"})
                messages.append({"role": "assistant", "content": blocks})
                messages.append({"role": "user", "content": [{
                    "type": "tool_result",
                    "tool_use_id": str(call.get("id") or ""),
                    "content": _execute_read_only_tool(self.work_dir, str(call.get("name") or ""), call.get("input")),
                } for call in calls]})
            elif self.provider == "openai":
                payload = self._post(
                    "https://api.openai.com/v1/responses",
                    {"model": self.model, "input": messages, "tools": _read_only_tools("openai"), "tool_choice": "auto"},
                    {"content-type": "application/json", "authorization": f"Bearer {self.api_key}"},
                )
                output = payload.get("output")
                rows = output if isinstance(output, list) else []
                calls = [item for item in rows if isinstance(item, dict) and item.get("type") == "function_call"]
                raw_response = str(payload.get("output_text") or "") or "".join(
                    str(part.get("text") or "") for item in rows if isinstance(item, dict)
                    for part in item.get("content", []) if isinstance(part, dict) and part.get("type") == "output_text"
                )
                if not calls:
                    break
                event_sink("agent.phase", {"label": f"{self.provider.title()} 正在检查项目上下文"})
                messages.extend(rows)
                for call in calls:
                    try:
                        arguments = json.loads(str(call.get("arguments") or "{}"))
                    except json.JSONDecodeError:
                        arguments = {}
                    messages.append({
                        "type": "function_call_output",
                        "call_id": str(call.get("call_id") or ""),
                        "output": _execute_read_only_tool(self.work_dir, str(call.get("name") or ""), arguments),
                    })
            else:
                raise ValueError(f"unsupported direct model provider: {self.provider}")
        else:
            raise _provider_error(self.provider.title(), "The model exceeded the bounded read-only tool-call limit.")
        response = parse_agent_response(
            raw_response,
            protected_paths=[self.work_dir],
            team_plan_context=getattr(self, "team_plan_context", None),
        )
        self.history.extend([
            {"role": "user", "content": prompt},
            {"role": "assistant", "content": raw_response},
        ])
        event_sink("agent.phase", {"label": "正在整理回答"})
        event_sink("answer.delta", {"text": str(response.get("message") or "")})
        event_sink("answer.final", {"response": response})
        return response

    def interrupt_turn(self, turn_id: str | None = None) -> None:
        del turn_id

    def close_session(self) -> None:
        return None

    def healthcheck(self) -> bool:
        return bool(self.api_key)


def direct_model_from_environment(
    *,
    provider: str,
    work_dir: Path,
    session_id: str | None = None,
    history: list[dict[str, Any]] | None = None,
) -> DirectModelAdapter:
    if provider == "anthropic":
        key_name = "ANTHROPIC_API_KEY"
        model = os.environ.get("LOOPX_ANTHROPIC_MODEL", "claude-sonnet-4-5")
    elif provider == "openai":
        key_name = "OPENAI_API_KEY"
        model = os.environ.get("LOOPX_OPENAI_MODEL", "gpt-5")
    else:
        raise ValueError(f"unsupported direct model provider: {provider}")
    api_key = os.environ.get(key_name, "").strip()
    if not api_key:
        raise _provider_error(provider.title(), f"Set {key_name} in the LoopX Chat process environment.")
    return DirectModelAdapter(
        provider=provider,
        model=model,
        api_key=api_key,
        work_dir=work_dir.resolve(),
        session_id=session_id or uuid.uuid4().hex,
        history=list(history or []),
    )
