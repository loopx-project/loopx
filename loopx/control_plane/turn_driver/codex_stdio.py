"""Shared Codex app-server stdio IO; no Goal or execution authority."""
from __future__ import annotations

import json
import queue
import subprocess
import threading
import time
from collections import deque
from collections.abc import Mapping, Sequence
from typing import Any, Self, TextIO


class NativeGoalProtocolError(RuntimeError):
    """The native app-server exchange did not prove the requested transaction."""


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
