"""Minimal JSON-RPC client for KunlunCode's native Goal runtime."""

from __future__ import annotations

import hashlib
import json
import os
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, TextIO


KUNLUN_APP_SERVER_PROTOCOL_VERSION = "2026-07-27"
KUNLUN_APP_SERVER_CLIENT_NAME = "loopx-kunluncode"
KUNLUN_APP_SERVER_CLIENT_VERSION = "0.1.0"
NATIVE_GOAL_MODES = {"goal": "arrangement", "goal-pro": "strict"}
TERMINAL_GOAL_STATUSES = {
    "blocked",
    "budget_limited",
    "complete",
    "paused",
    "usage_limited",
}


class KunlunAppServerError(RuntimeError):
    """Raised when the KunlunCode app-server contract cannot be completed."""


def stable_text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_app_server_command(
    kunluncode_bin: str,
    *,
    project: Path,
    permission_mode: str,
    max_duration_secs: int,
    scenario: str | None = None,
) -> list[str]:
    command = [
        kunluncode_bin,
        "app-server",
        "--listen",
        "stdio://",
        "--cwd",
        str(project.resolve()),
        "--permission-mode",
        permission_mode,
        "--tool-profile",
        "full",
        "--max-duration-secs",
        str(max(1, max_duration_secs)),
    ]
    if scenario:
        command += ["--scenario", scenario]
    return command


def goal_from_result(result: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(result, Mapping):
        return {}
    nested = result.get("goal")
    if isinstance(nested, Mapping):
        return dict(nested)
    if any(key in result for key in ("goalId", "goal_id", "objective", "status")):
        return dict(result)
    return {}


def goal_value(goal: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in goal:
            return goal[name]
    return None


def goal_event_kinds(goal: Mapping[str, Any]) -> list[str]:
    values: list[str] = []
    raw_events = goal_value(goal, "events", "history")
    if not isinstance(raw_events, list):
        return values
    for event in raw_events:
        if isinstance(event, str):
            values.append(event.strip().lower())
            continue
        if not isinstance(event, Mapping):
            continue
        for key in ("kind", "type", "event", "name", "status"):
            value = event.get(key)
            if isinstance(value, str) and value.strip():
                values.append(value.strip().lower())
    return values


def strict_goal_verification_passed(goal: Mapping[str, Any]) -> bool:
    if "verification_passed" in goal_event_kinds(goal):
        return True
    for key in ("verification", "verifier", "verificationResult"):
        value = goal.get(key)
        if not isinstance(value, Mapping):
            continue
        status = str(value.get("status") or value.get("result") or "").lower()
        if status in {"pass", "passed", "verification_passed"}:
            return True
    return False


def compact_goal(goal: Mapping[str, Any]) -> dict[str, Any]:
    objective = str(goal.get("objective") or "")
    return {
        "goal_id": goal_value(goal, "goalId", "goal_id", "id"),
        "revision": goal.get("revision"),
        "mode": goal.get("mode"),
        "verification_kind": goal_value(goal, "verificationKind", "verification_kind"),
        "status": goal.get("status"),
        "objective_sha256": stable_text_digest(objective) if objective else None,
        "event_kinds": sorted(set(goal_event_kinds(goal))),
        "verification_passed": strict_goal_verification_passed(goal),
    }


def native_goal_success(goal: Mapping[str, Any], *, mode: str) -> bool:
    expected_mode = NATIVE_GOAL_MODES.get(mode)
    if expected_mode is None:
        raise ValueError(f"unsupported native KunlunCode goal mode: {mode}")
    if str(goal.get("status") or "") != "complete":
        return False
    if str(goal.get("mode") or "") != expected_mode:
        return False
    return mode != "goal-pro" or strict_goal_verification_passed(goal)


def _stdout_reader(
    stream: TextIO,
    output: "queue.Queue[dict[str, Any] | BaseException]",
) -> None:
    try:
        for line in stream:
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("app-server message is not a JSON object")
                output.put(value)
            except BaseException as exc:  # pragma: no cover - defensive protocol path
                output.put(exc)
    finally:
        output.put(EOFError("KunlunCode app-server stdout closed"))


def _stderr_reader(stream: TextIO, output: "deque[str]") -> None:
    for line in stream:
        value = line.rstrip()
        if value:
            output.append(value[-1000:])


class KunlunAppServerClient:
    """One single-threaded stdio JSON-RPC connection to KunlunCode."""

    def __init__(
        self,
        command: list[str],
        *,
        cwd: Path,
        env: Mapping[str, str] | None = None,
        event_handler: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        self.command = list(command)
        self.event_handler = event_handler
        self._request_id = 0
        self._messages: "queue.Queue[dict[str, Any] | BaseException]" = queue.Queue(
            maxsize=2048
        )
        self._stderr: "deque[str]" = deque(maxlen=24)
        self.process = subprocess.Popen(
            self.command,
            cwd=str(cwd.resolve()),
            env=dict(env or os.environ),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
            bufsize=1,
        )
        if self.process.stdout is None or self.process.stderr is None:
            self.close()
            raise KunlunAppServerError("KunlunCode app-server pipes are unavailable")
        self._stdout_thread = threading.Thread(
            target=_stdout_reader,
            args=(self.process.stdout, self._messages),
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=_stderr_reader,
            args=(self.process.stderr, self._stderr),
            daemon=True,
        )
        self._stdout_thread.start()
        self._stderr_thread.start()

    def __enter__(self) -> "KunlunAppServerClient":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def diagnostic_tail(self) -> str:
        return "\n".join(self._stderr)[-3000:]

    def _failure(self, message: str) -> KunlunAppServerError:
        diagnostic = self.diagnostic_tail()
        if diagnostic:
            return KunlunAppServerError(f"{message}\nKunlunCode stderr:\n{diagnostic}")
        return KunlunAppServerError(message)

    def _send(self, message: Mapping[str, Any]) -> None:
        if self.process.stdin is None or self.process.stdin.closed:
            raise self._failure("KunlunCode app-server stdin is closed")
        try:
            self.process.stdin.write(json.dumps(dict(message)) + "\n")
            self.process.stdin.flush()
        except OSError as exc:
            raise self._failure("failed to write to KunlunCode app-server") from exc

    def _handle_notification(self, message: dict[str, Any]) -> None:
        if self.event_handler is not None:
            self.event_handler(message)

    def _next_message(self, timeout_seconds: float) -> dict[str, Any]:
        try:
            message = self._messages.get(timeout=max(0.01, timeout_seconds))
        except queue.Empty as exc:
            raise self._failure("timed out waiting for KunlunCode app-server") from exc
        if isinstance(message, EOFError):
            raise self._failure(
                "KunlunCode app-server exited before completing the request"
            )
        if isinstance(message, BaseException):
            raise self._failure(str(message)) from message
        return message

    def request(
        self,
        method: str,
        params: Mapping[str, Any] | None = None,
        *,
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        self._request_id += 1
        request_id = self._request_id
        self._send(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": dict(params or {}),
            }
        )
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        while time.monotonic() < deadline:
            message = self._next_message(max(0.01, deadline - time.monotonic()))
            incoming_method = message.get("method")
            if incoming_method:
                if message.get("id") is not None:
                    self._send(
                        {
                            "jsonrpc": "2.0",
                            "id": message["id"],
                            "error": {
                                "code": -32001,
                                "message": (
                                    "LoopX native Goal runs do not service interactive "
                                    "app-server requests"
                                ),
                            },
                        }
                    )
                    raise self._failure(
                        "KunlunCode requested interactive input via "
                        f"{incoming_method!r}; use a non-interactive permission mode"
                    )
                self._handle_notification(message)
                continue
            if message.get("id") != request_id:
                raise self._failure("KunlunCode returned an unexpected response id")
            error = message.get("error")
            if error:
                raise self._failure(
                    f"KunlunCode {method} failed: "
                    + json.dumps(error, ensure_ascii=False, sort_keys=True)
                )
            result = message.get("result")
            if result is None:
                return {}
            if not isinstance(result, dict):
                raise self._failure(f"KunlunCode {method} returned a non-object result")
            return result
        raise self._failure(f"timed out waiting for KunlunCode {method}")

    def notify(self, method: str, params: Mapping[str, Any] | None = None) -> None:
        self._send(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": dict(params or {}),
            }
        )

    def initialize(self, *, timeout_seconds: float = 15.0) -> dict[str, Any]:
        result = self.request(
            "initialize",
            {
                "protocolVersion": KUNLUN_APP_SERVER_PROTOCOL_VERSION,
                "clientInfo": {
                    "name": KUNLUN_APP_SERVER_CLIENT_NAME,
                    "version": KUNLUN_APP_SERVER_CLIENT_VERSION,
                },
                "capabilities": {"experimentalApi": True},
            },
            timeout_seconds=timeout_seconds,
        )
        observed = str(result.get("protocolVersion") or "")
        if observed != KUNLUN_APP_SERVER_PROTOCOL_VERSION:
            raise self._failure(
                "KunlunCode app-server protocol mismatch: "
                f"expected {KUNLUN_APP_SERVER_PROTOCOL_VERSION}, got {observed or 'missing'}"
            )
        self.notify("initialized")
        return result

    def start_thread(self, project: Path) -> str:
        result = self.request("thread/start", {"cwd": str(project.resolve())})
        thread = result.get("thread")
        thread_id = (
            str(thread.get("id") or "")
            if isinstance(thread, Mapping)
            else str(result.get("threadId") or result.get("thread_id") or "")
        )
        if not thread_id:
            raise self._failure("KunlunCode thread/start returned no thread id")
        return thread_id

    def resume_thread(self, thread_id: str) -> dict[str, Any]:
        return self.request("thread/resume", {"threadId": thread_id})

    def set_goal(
        self,
        thread_id: str,
        *,
        objective: str,
        mode: str,
        token_budget: int | None = None,
    ) -> dict[str, Any]:
        native_mode = NATIVE_GOAL_MODES.get(mode)
        if native_mode is None:
            raise ValueError(f"unsupported native KunlunCode goal mode: {mode}")
        params: dict[str, Any] = {
            "threadId": thread_id,
            "objective": objective,
            "mode": native_mode,
            "requireNoGoal": True,
        }
        if token_budget is not None:
            if token_budget <= 0:
                raise ValueError("KunlunCode native goal token budget must be positive")
            params["tokenBudget"] = token_budget
        return goal_from_result(self.request("thread/goal/set", params))

    def get_goal(
        self, thread_id: str, *, timeout_seconds: float = 15.0
    ) -> dict[str, Any]:
        goal = goal_from_result(
            self.request(
                "thread/goal/get",
                {"threadId": thread_id},
                timeout_seconds=timeout_seconds,
            )
        )
        if not goal:
            raise self._failure("KunlunCode thread/goal/get returned no goal")
        return goal

    def start_turn(self, thread_id: str, prompt: str) -> dict[str, Any]:
        if not prompt.strip():
            raise ValueError("KunlunCode turn prompt must be non-empty")
        return self.request(
            "turn/start",
            {
                "threadId": thread_id,
                "input": [{"type": "text", "text": prompt}],
            },
        )

    def wait_for_goal_terminal(
        self,
        thread_id: str,
        *,
        timeout_seconds: float,
        poll_interval_seconds: float = 0.5,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + max(0.1, timeout_seconds)
        last_goal: dict[str, Any] = {}
        while time.monotonic() < deadline:
            remaining = max(0.1, deadline - time.monotonic())
            last_goal = self.get_goal(
                thread_id,
                timeout_seconds=min(15.0, remaining),
            )
            status = str(last_goal.get("status") or "")
            if status in TERMINAL_GOAL_STATUSES:
                return last_goal
            wait_until = min(
                deadline, time.monotonic() + max(0.05, poll_interval_seconds)
            )
            while time.monotonic() < wait_until:
                try:
                    message = self._messages.get(
                        timeout=max(0.01, wait_until - time.monotonic())
                    )
                except queue.Empty:
                    break
                if isinstance(message, EOFError):
                    raise self._failure(
                        "KunlunCode app-server exited while the native goal was active"
                    )
                if isinstance(message, BaseException):
                    raise self._failure(str(message)) from message
                if message.get("method"):
                    if message.get("id") is not None:
                        self._send(
                            {
                                "jsonrpc": "2.0",
                                "id": message["id"],
                                "error": {
                                    "code": -32001,
                                    "message": (
                                        "LoopX native Goal runs do not service interactive "
                                        "app-server requests"
                                    ),
                                },
                            }
                        )
                        raise self._failure(
                            "KunlunCode requested interactive input while the native goal was active"
                        )
                    self._handle_notification(message)
                    continue
                raise self._failure(
                    "KunlunCode returned an unexpected asynchronous response"
                )
        compact = compact_goal(last_goal)
        raise self._failure(
            "timed out while KunlunCode native goal remained non-terminal: "
            + json.dumps(compact, ensure_ascii=False, sort_keys=True)
        )

    def close(self) -> None:
        process = getattr(self, "process", None)
        if process is None or process.poll() is not None:
            return
        try:
            if process.stdin is not None and not process.stdin.closed:
                process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
            return
        except subprocess.TimeoutExpired:
            process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive shutdown
            process.kill()
            process.wait(timeout=2)
