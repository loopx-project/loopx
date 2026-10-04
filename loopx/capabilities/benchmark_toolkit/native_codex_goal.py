"""Reusable Codex app-server Goal runtime for benchmark adapters.

The benchmark harness retains ownership of isolation, environment bridging,
timeouts, and scoring.  This module owns the native Goal JSON-RPC transaction,
stdio process transport, event correlation, and public-safe receipts so runner
implementations do not carry a second copy of that state machine.
"""

from __future__ import annotations

import json
import queue
import re
import subprocess
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Protocol, Self, TextIO


class NativeGoalProtocolError(RuntimeError):
    """The app-server exchange did not prove the required Goal transaction."""


class NativeGoalDeadlineExceeded(NativeGoalProtocolError):
    """The admitted native Goal remained active through its total deadline."""


class NativeGoalTransport(Protocol):
    def request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def notify(self, method: str, params: Mapping[str, Any]) -> None: ...


class NativeGoalEventTransport(NativeGoalTransport, Protocol):
    def next_event(self, *, timeout_sec: float) -> Mapping[str, Any] | None: ...


def _digest(value: str) -> str:
    return sha256(value.encode("utf-8")).hexdigest()


def _nested(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    nested = value.get(key)
    return nested if isinstance(nested, Mapping) else {}


def _error_signature(params: Mapping[str, Any]) -> str:
    error = params.get("error")
    typed_parts: list[str] = []
    for container in (params, error):
        if not isinstance(container, Mapping):
            continue
        for key in ("code", "type", "kind"):
            value = container.get(key)
            if isinstance(value, (str, int)):
                normalized = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(value))[:64]
                typed_parts.append(f"{key}={normalized}")
    canonical = json.dumps(params, sort_keys=True, default=str)
    digest = sha256(canonical.encode("utf-8")).hexdigest()[:12]
    typed = ",".join(dict.fromkeys(typed_parts))
    return f"{typed},sha256={digest}" if typed else f"sha256={digest}"


@dataclass(frozen=True)
class NativeGoalConfig:
    cwd: str
    objective: str
    task_instruction: str
    model: str | None = None
    effort: str | None = None
    token_budget: int | None = None
    approval_policy: str = "never"
    sandbox: str = "workspace-write"
    sandbox_policy: Mapping[str, Any] | None = None
    required_skill_ids: tuple[str, ...] = ()

    def validate(self) -> None:
        if not self.cwd.strip():
            raise ValueError("cwd must be non-empty")
        if not self.objective.strip():
            raise ValueError("objective must be non-empty")
        if not self.task_instruction.strip():
            raise ValueError("task_instruction must be non-empty")
        if self.token_budget is not None and (
            isinstance(self.token_budget, bool)
            or not isinstance(self.token_budget, int)
            or self.token_budget <= 0
        ):
            raise ValueError("token_budget must be a positive integer when provided")
        for skill_id in self.required_skill_ids:
            if not isinstance(skill_id, str) or not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", skill_id
            ):
                raise ValueError("required_skill_ids must contain safe skill ids")


@dataclass
class NativeGoalTurn:
    thread_id: str
    turn_id: str
    response_turn_id: str
    goal_status: str
    objective_sha256: str
    objective_chars: int
    task_instruction_sha256: str
    task_instruction_chars: int
    token_budget_present: bool
    methods: list[str] = field(default_factory=list)
    notifications: list[str] = field(default_factory=list)
    notification_counts: dict[str, int] = field(default_factory=dict)
    error_signatures: list[str] = field(default_factory=list)
    event_turn_id_observed: bool = False
    terminal_event_observed: bool = False
    turn_status: str = "not_started"
    post_goal_status: str = ""
    item_event_count: int = 0
    error_event_count: int = 0
    turn_started_count: int = 0
    turn_completed_count: int = 0
    goal_status_poll_count: int = 0
    required_skill_ids: tuple[str, ...] = ()
    discovered_required_skill_ids: tuple[str, ...] = ()
    skill_catalog_count: int = 0
    skill_error_count: int = 0


def _read_required_skills(
    transport: NativeGoalTransport,
    *,
    cwd: str,
    required_skill_ids: tuple[str, ...],
) -> tuple[tuple[str, ...], int, int]:
    """Prove required skills through the app-server discovery surface."""

    required = tuple(dict.fromkeys(required_skill_ids))
    if not required:
        return (), 0, 0
    result = transport.request(
        "skills/list",
        {"cwds": [cwd], "forceReload": True},
    )
    data = result.get("data")
    if not isinstance(data, list) or not data:
        raise NativeGoalProtocolError("skills_list_data_missing")
    matching = [
        row
        for row in data
        if isinstance(row, Mapping) and str(row.get("cwd") or "") == cwd
    ]
    if len(matching) != 1:
        raise NativeGoalProtocolError("skills_list_cwd_mismatch")
    row = matching[0]
    errors = row.get("errors")
    if not isinstance(errors, list):
        raise NativeGoalProtocolError("skills_list_errors_missing")
    if errors:
        raise NativeGoalProtocolError(f"skills_list_errors:{len(errors)}")
    skills = row.get("skills")
    if not isinstance(skills, list):
        raise NativeGoalProtocolError("skills_list_catalog_missing")
    names = {
        str(skill.get("name") or "")
        for skill in skills
        if isinstance(skill, Mapping) and skill.get("enabled") is not False
    }
    missing = [skill_id for skill_id in required if skill_id not in names]
    if missing:
        raise NativeGoalProtocolError(
            "required_skills_missing:" + ",".join(sorted(missing))
        )
    return required, len(skills), 0


def attach_native_goal(
    transport: NativeGoalTransport,
    config: NativeGoalConfig,
) -> NativeGoalTurn:
    """Initialize one app-server thread and attach an active native Goal."""

    config.validate()
    methods: list[str] = []

    transport.request(
        "initialize",
        {
            "clientInfo": {
                "name": "loopx_benchmark_toolkit",
                "title": "LoopX Benchmark Toolkit",
                "version": "0.1.0",
            },
            "capabilities": {"experimentalApi": True},
        },
    )
    methods.append("initialize")
    transport.notify("initialized", {})
    methods.append("initialized")

    discovered_skills, skill_catalog_count, skill_error_count = _read_required_skills(
        transport,
        cwd=config.cwd,
        required_skill_ids=config.required_skill_ids,
    )
    if config.required_skill_ids:
        methods.append("skills/list")

    thread_params: dict[str, Any] = {
        "cwd": config.cwd,
        "sandbox": config.sandbox,
        "approvalPolicy": config.approval_policy,
    }
    if config.model:
        thread_params["model"] = config.model
    thread_result = transport.request("thread/start", thread_params)
    methods.append("thread/start")
    thread = _nested(thread_result, "thread")
    thread_id = str(thread.get("id") or thread_result.get("threadId") or "")
    if not thread_id:
        raise NativeGoalProtocolError("thread_start_id_missing")

    goal_set: dict[str, Any] = {
        "threadId": thread_id,
        "objective": config.objective,
        "status": "active",
    }
    if config.token_budget is not None:
        goal_set["tokenBudget"] = config.token_budget
    transport.request("thread/goal/set", goal_set)
    methods.append("thread/goal/set")

    goal_result = transport.request("thread/goal/get", {"threadId": thread_id})
    methods.append("thread/goal/get")
    goal = _nested(goal_result, "goal")
    if str(goal.get("status") or "") != "active":
        raise NativeGoalProtocolError("goal_not_active")
    if str(goal.get("threadId") or "") != thread_id:
        raise NativeGoalProtocolError("goal_thread_mismatch")
    if str(goal.get("objective") or "") != config.objective:
        raise NativeGoalProtocolError("goal_objective_mismatch")

    return NativeGoalTurn(
        thread_id=thread_id,
        turn_id="",
        response_turn_id="",
        goal_status="active",
        objective_sha256=_digest(config.objective),
        objective_chars=len(config.objective),
        task_instruction_sha256=_digest(config.task_instruction),
        task_instruction_chars=len(config.task_instruction),
        token_budget_present=config.token_budget is not None,
        methods=methods,
        required_skill_ids=tuple(dict.fromkeys(config.required_skill_ids)),
        discovered_required_skill_ids=discovered_skills,
        skill_catalog_count=skill_catalog_count,
        skill_error_count=skill_error_count,
    )


def start_native_goal_turn(
    transport: NativeGoalTransport,
    config: NativeGoalConfig,
) -> NativeGoalTurn:
    """Attach an active Goal to a new thread and start one task turn."""

    turn = attach_native_goal(transport, config)
    turn_params: dict[str, Any] = {
        "threadId": turn.thread_id,
        "input": [{"type": "text", "text": config.task_instruction}],
        "cwd": config.cwd,
        "approvalPolicy": config.approval_policy,
    }
    if config.model:
        turn_params["model"] = config.model
    if config.effort:
        turn_params["effort"] = config.effort
    if config.sandbox_policy is not None:
        turn_params["sandboxPolicy"] = dict(config.sandbox_policy)
    turn_result = transport.request("turn/start", turn_params)
    turn.methods.append("turn/start")
    response_turn = _nested(turn_result, "turn")
    response_turn_id = str(response_turn.get("id") or turn_result.get("turnId") or "")
    if not response_turn_id:
        raise NativeGoalProtocolError("turn_start_id_missing")
    turn.turn_id = response_turn_id
    turn.response_turn_id = response_turn_id
    turn.turn_status = str(response_turn.get("status") or "accepted")
    return turn


def observe_native_goal_event(
    turn: NativeGoalTurn,
    event: Mapping[str, Any],
) -> bool:
    """Apply one app-server notification and return terminal observation state."""

    method = str(event.get("method") or "")
    params = _nested(event, "params")
    if not method:
        event_type = str(event.get("type") or "")
        payload = _nested(event, "payload")
        payload_type = str(payload.get("type") or "")
        method = (
            f"{event_type}:{payload_type}"
            if event_type and payload_type
            else event_type
        )
        params = payload
    if not method:
        return turn.terminal_event_observed

    turn.notifications.append(method)
    turn.notification_counts[method] = turn.notification_counts.get(method, 0) + 1
    event_thread_id = str(params.get("threadId") or "")
    if event_thread_id and event_thread_id != turn.thread_id:
        return turn.terminal_event_observed
    event_turn = _nested(params, "turn")
    event_turn_id = str(event_turn.get("id") or params.get("turnId") or "")

    if method == "turn/started" and event_turn_id:
        turn.turn_id = event_turn_id
        turn.event_turn_id_observed = True
        turn.turn_status = str(event_turn.get("status") or "inProgress")
        turn.turn_started_count += 1
        return False
    if event_turn_id and event_turn_id != turn.turn_id:
        return turn.terminal_event_observed
    if method.startswith(("item/", "response_item:")):
        turn.item_event_count += 1
    if method == "error":
        turn.error_event_count += 1
        turn.turn_status = "error"
        if len(turn.error_signatures) < 3:
            turn.error_signatures.append(_error_signature(params))
    if method == "turn/completed" or method in {
        "event_msg:task_complete",
        "event_msg:task_completed",
        "event_msg:turn_completed",
    }:
        turn.terminal_event_observed = True
        turn.turn_status = str(event_turn.get("status") or "completed")
        turn.turn_completed_count += 1
    return turn.terminal_event_observed


def wait_native_goal_turn(
    transport: NativeGoalEventTransport,
    turn: NativeGoalTurn,
    *,
    timeout_sec: float | None,
    completed_before: int | None = None,
) -> NativeGoalTurn:
    """Drain events until one more correlated turn reaches a terminal event.

    ``completed_before`` lets a native Goal runtime wait for automatic
    continuation turns without starting another model turn itself.  Omitting it
    preserves the single-turn behavior for existing callers.
    """

    if timeout_sec is not None and timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    deadline = None if timeout_sec is None else time.monotonic() + timeout_sec
    if completed_before is None:
        if turn.terminal_event_observed:
            return turn
        completed_before = turn.turn_completed_count
    while turn.turn_completed_count <= completed_before:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise NativeGoalProtocolError("goal_turn_timeout")
        event = transport.next_event(timeout_sec=0.25 if remaining is None else min(0.25, remaining))
        if event is not None:
            observe_native_goal_event(turn, event)
    return turn


def refresh_native_goal_status(
    transport: NativeGoalTransport,
    turn: NativeGoalTurn,
) -> str:
    """Read the Goal status after a turn and bind it to the same thread."""

    result = transport.request("thread/goal/get", {"threadId": turn.thread_id})
    turn.methods.append("thread/goal/get")
    goal = _nested(result, "goal")
    if str(goal.get("threadId") or "") != turn.thread_id:
        raise NativeGoalProtocolError("post_goal_thread_mismatch")
    status = str(goal.get("status") or "")
    if not status:
        raise NativeGoalProtocolError("post_goal_status_missing")
    turn.post_goal_status = status
    turn.goal_status_poll_count += 1
    return status


def run_native_goal_turn(
    transport: NativeGoalEventTransport,
    config: NativeGoalConfig,
    *,
    timeout_sec: float | None,
) -> NativeGoalTurn:
    """Execute the complete native Goal transaction over an admitted transport."""

    turn = start_native_goal_turn(transport, config)
    wait_native_goal_turn(transport, turn, timeout_sec=timeout_sec)
    refresh_native_goal_status(transport, turn)
    return turn


def run_native_goal_until_terminal(
    transport: NativeGoalEventTransport,
    config: NativeGoalConfig,
    *,
    timeout_sec: float | None,
    on_turn_started: Callable[[NativeGoalTurn], None] | None = None,
) -> NativeGoalTurn:
    """Run one native Goal until its status leaves ``active``.

    Codex may schedule continuation turns while a Goal remains active.  The
    caller starts exactly one task turn, then keeps draining those correlated
    continuation events and reading the Goal status under one total timeout.
    """

    if timeout_sec is not None and timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    turn = start_native_goal_turn(transport, config)
    if on_turn_started is not None:
        on_turn_started(turn)
    deadline = None if timeout_sec is None else time.monotonic() + timeout_sec
    completed_before = turn.turn_completed_count
    while True:
        remaining = None if deadline is None else deadline - time.monotonic()
        if remaining is not None and remaining <= 0:
            raise NativeGoalDeadlineExceeded("goal_timeout_before_terminal")
        try:
            wait_native_goal_turn(
                transport,
                turn,
                timeout_sec=remaining,
                completed_before=completed_before,
            )
        except NativeGoalProtocolError as exc:
            if str(exc) == "goal_turn_timeout":
                raise NativeGoalDeadlineExceeded(
                    "goal_timeout_before_terminal"
                ) from exc
            raise
        completed_before = turn.turn_completed_count
        if refresh_native_goal_status(transport, turn) != "active":
            return turn


_StreamItem = Mapping[str, Any] | Exception


def _read_stream(stream: TextIO, messages: queue.Queue[_StreamItem]) -> None:
    try:
        for line in stream:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                messages.put(NativeGoalProtocolError("app_server_frame_invalid_json"))
                continue
            if not isinstance(message, Mapping):
                messages.put(NativeGoalProtocolError("app_server_frame_not_object"))
                continue
            messages.put(message)
    finally:
        messages.put(EOFError("app_server_stream_closed"))


class StdioNativeGoalTransport:
    """Line-delimited JSON-RPC transport backed by a real app-server process."""

    def __init__(
        self,
        process: subprocess.Popen[str],
        *,
        response_timeout_sec: float = 30,
    ) -> None:
        if process.stdin is None or process.stdout is None:
            raise ValueError("process must expose text stdin and stdout pipes")
        if response_timeout_sec <= 0:
            raise ValueError("response_timeout_sec must be positive")
        self.process = process
        self.response_timeout_sec = float(response_timeout_sec)
        self._messages: queue.Queue[_StreamItem] = queue.Queue()
        self._pending_events: deque[Mapping[str, Any]] = deque()
        self._next_request_id = 1
        self._reader = threading.Thread(
            target=_read_stream,
            args=(process.stdout, self._messages),
            daemon=True,
        )
        self._reader.start()

    @classmethod
    def spawn(
        cls,
        command: Sequence[str],
        *,
        cwd: str,
        env: Mapping[str, str] | None = None,
        response_timeout_sec: float = 30,
        stderr: int | TextIO | None = subprocess.DEVNULL,
    ) -> StdioNativeGoalTransport:
        if not command:
            raise ValueError("command must be non-empty")
        process = subprocess.Popen(
            list(command),
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            text=True,
            encoding="utf-8",
            bufsize=1,
        )
        return cls(process, response_timeout_sec=response_timeout_sec)

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    def _send(self, message: Mapping[str, Any]) -> None:
        if self.process.stdin is None or self.process.poll() is not None:
            raise NativeGoalProtocolError("app_server_stdin_closed")
        try:
            self.process.stdin.write(json.dumps(message) + "\n")
            self.process.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise NativeGoalProtocolError("app_server_write_failed") from exc

    def _next_message(self, *, timeout_sec: float) -> Mapping[str, Any] | None:
        try:
            message = self._messages.get(timeout=max(0.0, timeout_sec))
        except queue.Empty:
            return None
        if isinstance(message, Exception):
            raise NativeGoalProtocolError(str(message)) from message
        return message

    def request(self, method: str, params: Mapping[str, Any]) -> Mapping[str, Any]:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._send({"id": request_id, "method": method, "params": dict(params)})
        deadline = time.monotonic() + self.response_timeout_sec
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NativeGoalProtocolError(f"app_server_response_timeout:{method}")
            message = self._next_message(timeout_sec=remaining)
            if message is None:
                if self.process.poll() is not None:
                    raise NativeGoalProtocolError("app_server_exited_before_response")
                continue
            if message.get("id") == request_id:
                if message.get("error") is not None:
                    raise NativeGoalProtocolError(f"app_server_request_failed:{method}")
                result = message.get("result")
                return result if isinstance(result, Mapping) else {}
            if message.get("method"):
                self._pending_events.append(message)
                continue
            raise NativeGoalProtocolError("app_server_unexpected_response")

    def notify(self, method: str, params: Mapping[str, Any]) -> None:
        self._send({"method": method, "params": dict(params)})

    def next_event(self, *, timeout_sec: float) -> Mapping[str, Any] | None:
        if self._pending_events:
            return self._pending_events.popleft()
        message = self._next_message(timeout_sec=timeout_sec)
        if message is None:
            if self.process.poll() is not None:
                raise NativeGoalProtocolError("app_server_exited_before_terminal_event")
            return None
        if message.get("method"):
            return message
        raise NativeGoalProtocolError("app_server_unexpected_response")

    def close(self) -> None:
        if self.process.stdin is not None:
            try:
                self.process.stdin.close()
            except OSError:
                pass
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=2)


def _default_app_server_command(codex_bin: str) -> list[str]:
    return [
        codex_bin,
        "app-server",
        "--listen",
        "stdio://",
        "--enable",
        "goals",
    ]


def probe_native_goal_process(
    config: NativeGoalConfig,
    *,
    codex_bin: str = "codex",
    process_command: Sequence[str] | None = None,
    process_env: Mapping[str, str] | None = None,
    process_cwd: str | None = None,
    response_timeout_sec: float = 30,
) -> NativeGoalTurn:
    """Exercise a real app-server through Goal attachment without starting a model turn."""

    command = process_command or _default_app_server_command(codex_bin)
    with StdioNativeGoalTransport.spawn(
        command,
        cwd=process_cwd or config.cwd,
        env=process_env,
        response_timeout_sec=response_timeout_sec,
    ) as transport:
        return attach_native_goal(transport, config)


def run_native_goal_process(
    config: NativeGoalConfig,
    *,
    codex_bin: str = "codex",
    process_command: Sequence[str] | None = None,
    process_env: Mapping[str, str] | None = None,
    process_cwd: str | None = None,
    response_timeout_sec: float = 30,
    goal_timeout_sec: float | None = 21_600,
) -> NativeGoalTurn:
    """Spawn a real app-server and execute one complete native Goal turn."""

    command = process_command or _default_app_server_command(codex_bin)
    with StdioNativeGoalTransport.spawn(
        command,
        cwd=process_cwd or config.cwd,
        env=process_env,
        response_timeout_sec=response_timeout_sec,
    ) as transport:
        return run_native_goal_turn(transport, config, timeout_sec=goal_timeout_sec)


def run_native_goal_process_until_terminal(
    config: NativeGoalConfig,
    *,
    codex_bin: str = "codex",
    process_command: Sequence[str] | None = None,
    process_env: Mapping[str, str] | None = None,
    process_cwd: str | None = None,
    response_timeout_sec: float = 30,
    goal_timeout_sec: float | None = 21_600,
) -> NativeGoalTurn:
    """Spawn app-server and keep the native Goal alive through continuations."""

    command = process_command or _default_app_server_command(codex_bin)
    with StdioNativeGoalTransport.spawn(
        command,
        cwd=process_cwd or config.cwd,
        env=process_env,
        response_timeout_sec=response_timeout_sec,
    ) as transport:
        return run_native_goal_until_terminal(
            transport,
            config,
            timeout_sec=goal_timeout_sec,
        )


def compact_native_goal_receipt(turn: NativeGoalTurn) -> dict[str, Any]:
    """Return public-safe transaction evidence without task or response content."""

    counts = turn.notification_counts or dict(Counter(turn.notifications))
    return {
        "schema_version": "native_codex_goal_turn_receipt_v0",
        "thread_id_present": bool(turn.thread_id),
        "turn_id_present": bool(turn.turn_id),
        "response_turn_id_present": bool(turn.response_turn_id),
        "event_turn_id_observed": turn.event_turn_id_observed,
        "goal_status": turn.goal_status,
        "post_goal_status": turn.post_goal_status or None,
        "token_budget_present": turn.token_budget_present,
        "objective_sha256": turn.objective_sha256,
        "objective_chars": turn.objective_chars,
        "task_instruction_sha256": turn.task_instruction_sha256,
        "task_instruction_chars": turn.task_instruction_chars,
        "methods": list(turn.methods),
        "notifications": sorted(set(turn.notifications)),
        "notification_counts": dict(sorted(counts.items())),
        "turn_status": turn.turn_status,
        "terminal_event_observed": turn.terminal_event_observed,
        "item_event_count": turn.item_event_count,
        "error_event_count": turn.error_event_count,
        "turn_started_count": turn.turn_started_count,
        "turn_completed_count": turn.turn_completed_count,
        "goal_continuation_turn_completed_count": max(0, turn.turn_completed_count - 1),
        "goal_status_poll_count": turn.goal_status_poll_count,
        "required_skill_ids": list(turn.required_skill_ids),
        "discovered_required_skill_ids": list(turn.discovered_required_skill_ids),
        "skill_catalog_count": turn.skill_catalog_count,
        "skill_error_count": turn.skill_error_count,
        "required_skills_discovered": (
            turn.discovered_required_skill_ids == turn.required_skill_ids
            if turn.required_skill_ids
            else None
        ),
        "error_signatures": list(turn.error_signatures),
        "public_boundary": {
            "raw_objective_recorded": False,
            "raw_task_instruction_recorded": False,
            "raw_assistant_message_recorded": False,
            "raw_tool_events_recorded": False,
            "credentials_recorded": False,
            "local_paths_recorded": False,
        },
    }


__all__ = [
    "NativeGoalConfig",
    "NativeGoalEventTransport",
    "NativeGoalProtocolError",
    "NativeGoalTransport",
    "NativeGoalTurn",
    "StdioNativeGoalTransport",
    "attach_native_goal",
    "compact_native_goal_receipt",
    "observe_native_goal_event",
    "probe_native_goal_process",
    "refresh_native_goal_status",
    "run_native_goal_process",
    "run_native_goal_process_until_terminal",
    "run_native_goal_turn",
    "run_native_goal_until_terminal",
    "start_native_goal_turn",
    "wait_native_goal_turn",
]
