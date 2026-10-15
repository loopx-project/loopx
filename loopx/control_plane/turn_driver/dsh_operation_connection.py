"""Bounded local IO for one SDK-owned DSH process; never an actor/proof API.

Kernel peer PID and SDK native event/call pairing must both match. Canonical
approval, consumption, revocation and outcome decisions stay in the TS owner.
No retry, journal, domain effect or alternate session store lives here.
"""

from __future__ import annotations

import json
import os
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

FRAME_LIMIT = 262144


def _peer_pid(connection: socket.socket) -> int:
    if sys.platform == "darwin":
        # Apple's sys/un.h: SOL_LOCAL=0, LOCAL_PEERPID=0x002.
        return struct.unpack("i", connection.getsockopt(0, 2, 4))[0]
    if sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED"):
        return struct.unpack(
            "3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        )[0]
    raise ValueError("native operation peer credentials unavailable on this host")


class DshOperationConnection:
    """Private lifetime-scoped endpoint; constructing it grants no execution."""

    def __init__(
        self,
        session_id: str,
        profile: Mapping[str, str],
        *,
        on_start: Callable[[str], None] | None = None,
    ) -> None:
        self.session_id = session_id
        self.profile = dict(profile)
        self.on_start = on_start
        # Short AF_UNIX path on macOS; no workspace/private payload in address.
        self._temporary = tempfile.TemporaryDirectory(prefix="lx-dsh-", dir="/tmp")
        os.chmod(self._temporary.name, 0o700)
        self.path = Path(self._temporary.name) / "op.sock"
        self._condition = threading.Condition()
        self._closed = False
        self._pid: int | None = None
        self._handler: Callable | None = None
        self._turn: tuple[int, int] | None = None
        self._header_current = False
        self._calls: dict[str, dict[str, Any]] = {}
        self._used: set[str] = set()
        self._listener = socket.socket(socket.AF_UNIX)
        self._listener.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._listener.listen(4)
        self._listener.settimeout(0.2)
        self._thread = threading.Thread(
            target=self._serve, name="dsh-operation-io", daemon=True
        )
        self._thread.start()

    def bind(self, pid: int, handler: Callable) -> None:
        with self._condition:
            if (
                self._closed
                or self._pid is not None
                or type(pid) is not int
                or pid <= 0
            ):
                raise ValueError("owned SDK process cannot be rebound")
            self._pid, self._handler = pid, handler

    def observe(self, notification: Any) -> None:
        """Only the owned SDK subscription supplies these notifications."""
        if (
            notification.method != "session.event"
            or notification.payload.get("sessionId") != self.session_id
        ):
            return
        event = notification.payload.get("event") or {}
        data = event.get("data") or {}
        with self._condition:
            if self._closed:
                return
            if event.get("type") == "turn/start":
                turn, seq = data.get("turn"), event.get("seq")
                if type(turn) is not int or turn < 1 or type(seq) is not int or seq < 0:
                    raise ValueError("invalid native Turn boundary")
                self._turn = (turn, seq)
                # DSH logs request/header only when the effective call config
                # changes. Retain its observed value on this exact live SDK
                # connection; never seed it from a persisted session document.
                self._calls.clear()
                self._used.clear()
                if self.on_start is not None:
                    self.on_start(f"dsh-{turn}-{seq}")
            elif event.get("type") == "request/header":
                config = (data.get("header") or {}).get("config") or {}
                observed = {
                    "provider": config.get("provider"),
                    "model": config.get("model"),
                    "reasoning_effort": config.get("reasoningEffort"),
                }
                self._header_current = observed == self.profile
            elif (
                event.get("type") == "tool/call"
                and data.get("name") == "loopx_operation"
            ):
                if self._turn is not None and data.get("turn") == self._turn[0]:
                    call_id = data.get("callId")
                    arguments = data.get("arguments")
                    if isinstance(arguments, str):
                        arguments = json.loads(arguments)
                    if (
                        isinstance(call_id, str)
                        and 0 < len(call_id) <= 256
                        and isinstance(arguments, dict)
                    ):
                        if len(self._calls) >= 128:
                            raise ValueError("native operation call budget exceeded")
                        self._calls[call_id] = arguments
            elif event.get("type") == "turn/end":
                self._turn = None
                self._calls.clear()
            self._condition.notify_all()

    def _dispatch(self, envelope: Any) -> dict[str, Any]:
        if not isinstance(envelope, dict) or set(envelope) != {
            "schema_version",
            "native",
            "call_id",
            "arguments",
        }:
            raise ValueError("invalid frame")
        if envelope["schema_version"] != "loopx_dsh_operation_call_v0":
            raise ValueError("invalid protocol")
        call_id = envelope["call_id"]
        if not isinstance(call_id, str) or not 0 < len(call_id) <= 256:
            raise ValueError("invalid native call")
        with self._condition:
            deadline = time.monotonic() + 5
            # Native event delivery and the Unix socket are independent IO.
            while (
                not self._closed
                and call_id not in self._calls
                and time.monotonic() < deadline
            ):
                self._condition.wait(deadline - time.monotonic())
            if (
                self._closed
                or self._turn is None
                or not self._header_current
                or self._handler is None
            ):
                raise ValueError("inactive native binding")
            expected = {
                "schema_version": "loopx_dsh_native_context_v0",
                "session_id": self.session_id,
                "native_turn_number": self._turn[0],
                "turn_start_seq": self._turn[1],
                "profile_source": "agent_options",
                "profile": self.profile,
                "wire_profile_verified": False,
                "execution_allowed": False,
            }

            # JSON encoding also distinguishes true from 1 at identity fields.
            def canonical(value: Any) -> str:
                return json.dumps(value, sort_keys=True, separators=(",", ":"))

            if (
                canonical(envelope["native"]) != canonical(expected)
                or call_id in self._used
                or call_id not in self._calls
                or canonical(envelope["arguments"]) != canonical(self._calls[call_id])
            ):
                raise ValueError("native call mismatch")
            self._used.add(call_id)
            return self._handler(
                "loopx_operation",
                envelope["arguments"],
                {
                    "thread_id": self.session_id,
                    "host_turn_id": f"dsh-{self._turn[0]}-{self._turn[1]}",
                },
            )

    def _serve(self) -> None:
        while not self._closed:
            try:
                connection, _ = self._listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            with connection:
                connection.settimeout(10)
                try:
                    if self._pid is None or _peer_pid(connection) != self._pid:
                        raise ValueError("peer is not the owned SDK runtime")
                    data = bytearray()
                    while b"\n" not in data and len(data) <= FRAME_LIMIT:
                        part = connection.recv(min(8192, FRAME_LIMIT + 1 - len(data)))
                        if not part:
                            raise ValueError("incomplete frame")
                        data.extend(part)
                    if (
                        len(data) > FRAME_LIMIT
                        or data.count(b"\n") != 1
                        or not data.endswith(b"\n")
                    ):
                        raise ValueError("invalid frame boundary")
                    result = self._dispatch(json.loads(data))
                except (ValueError, TypeError, KeyError, RuntimeError, OSError):
                    result = {
                        "ok": False,
                        "error": "operation_native_binding_rejected",
                        "execution_allowed": False,
                    }
                try:
                    frame = json.dumps(result, separators=(",", ":")).encode() + b"\n"
                    if len(frame) > FRAME_LIMIT:
                        frame = b'{"ok":false,"error":"operation_response_too_large","execution_allowed":false}\n'
                    connection.sendall(frame)
                except OSError:
                    pass  # Response loss grants no retry or second consumption.

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._turn = None
            self._handler = None
            self._condition.notify_all()
        self._listener.close()
        self._thread.join(timeout=11)
        self._temporary.cleanup()

    def __enter__(self) -> DshOperationConnection:
        return self

    def __exit__(self, *_args) -> None:
        self.close()
