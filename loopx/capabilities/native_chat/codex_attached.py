"""Owner-local Codex Desktop transport. Never starts/resumes an executor.

The Desktop follower IPC is private and version-sensitive. Protocol discovery
comes from codex-web; this independent adapter implements only discovery and
owner-routed input. History stays in the host store, read in bounded windows.
"""

from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import stat
import struct
import subprocess
import sys
import time
from typing import Any
from uuid import UUID, uuid4

from ...codex_app_thread_activity import codex_homes, observe_codex_threads

WINDOW_BYTES = 2 * 1024 * 1024
MAX_FRAME_BYTES = 8 * 1024 * 1024


class DesktopUnavailable(RuntimeError):
    pass


class DesktopUncertain(RuntimeError):
    """A submitted input has no acknowledgement; never retry automatically."""


def open_conversation(thread_id: str) -> None:
    """Request OS navigation to an already validated binding, without host input."""
    if str(UUID(thread_id)) != thread_id:
        raise ValueError("Conversation identity is not canonical")
    if sys.platform != "darwin":
        raise DesktopUnavailable("Opening Codex App is not supported on this host")
    try:
        subprocess.run(
            ["/usr/bin/open", "-b", "com.openai.codex", f"codex://threads/{thread_id}"],
            check=True,
            timeout=3,
            capture_output=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise DesktopUnavailable("Codex App could not be opened") from exc


class DesktopClient:
    def __init__(self, home: Path):
        self.path = home / "ipc" / "ipc.sock"
        self.socket: socket.socket | None = None
        self.client_id = "initializing-client"
        self.deadline = 0.0

    def __enter__(self) -> DesktopClient:
        if os.name != "posix" or not hasattr(socket, "AF_UNIX"):
            raise DesktopUnavailable(
                "This host does not expose the local Desktop transport"
            )
        try:
            info = self.path.lstat()
            if (
                not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
            ):
                raise DesktopUnavailable(
                    "Desktop connection is not private to this user"
                )
            self.socket = socket.socket(socket.AF_UNIX)
            self.socket.settimeout(3)
            self.socket.connect(str(self.path))
            result = self.call("initialize", {"clientType": "loopx"}, version=0)
            self.client_id = result["result"]["clientId"]
            if not isinstance(self.client_id, str) or not self.client_id:
                raise DesktopUnavailable("Desktop client identity is unsupported")
            return self
        except (OSError, KeyError, TypeError, ValueError, DesktopUnavailable) as exc:
            self.__exit__(None, None, None)
            raise DesktopUnavailable("Codex App connection is unavailable") from exc

    def __exit__(self, *_args: Any) -> None:
        if self.socket is not None:
            self.socket.close()

    def _send(self, value: dict[str, Any]) -> None:
        assert self.socket is not None
        raw = json.dumps(value).encode()
        if len(raw) > MAX_FRAME_BYTES:
            raise DesktopUnavailable("Desktop input exceeds the bounded frame")
        self.socket.sendall(struct.pack("<I", len(raw)) + raw)

    def _take(self, length: int) -> bytes:
        assert self.socket is not None
        out = bytearray()
        while len(out) < length:
            remaining = self.deadline - time.monotonic()
            if remaining <= 0:
                raise DesktopUnavailable("Desktop response timed out")
            self.socket.settimeout(remaining)
            chunk = self.socket.recv(length - len(out))
            if not chunk:
                raise DesktopUnavailable("Desktop disconnected")
            out.extend(chunk)
        return bytes(out)

    def call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        version: int,
        target: str | None = None,
    ) -> dict[str, Any]:
        self.deadline = time.monotonic() + 3
        request_id = str(uuid4())
        self._send(
            {
                "type": "request",
                "requestId": request_id,
                "sourceClientId": self.client_id,
                "method": method,
                "version": version,
                "params": params,
                "timeoutMs": 2500,
                **({"targetClientId": target} if target else {}),
            }
        )
        for _ in range(128):
            length = struct.unpack("<I", self._take(4))[0]
            if not 0 < length <= MAX_FRAME_BYTES:
                raise DesktopUnavailable("Desktop frame is unsupported")
            value = json.loads(self._take(length))
            if not isinstance(value, dict):
                raise DesktopUnavailable("Desktop frame has an unsupported shape")
            if value.get("type") == "client-discovery-request":
                self._send(
                    {
                        "type": "client-discovery-response",
                        "requestId": value["requestId"],
                        "response": {"canHandle": False},
                    }
                )
            if value.get("type") == "response" and value.get("requestId") == request_id:
                if value.get("resultType") != "success":
                    raise DesktopUnavailable("Desktop did not accept this operation")
                return value
        raise DesktopUnavailable("Desktop response exceeded the bounded event window")

    def owner(self, thread_id: str) -> str:
        value = self.call(
            "thread-owner-discovery",
            {"hostId": "local", "conversationId": thread_id},
            version=1,
        )
        owner = value.get("handledByClientId")
        result = value.get("result")
        if (
            not isinstance(owner, str)
            or not 0 < len(owner) <= 160
            or not isinstance(result, dict)
            or result.get("supportsUntrustedAppInput") is not True
        ):
            raise DesktopUnavailable(
                "The original App owner does not support external input"
            )
        return owner

    def send(self, thread_id: str, text: str, owner: str) -> str:
        # The host keeps model, tool, sandbox and approval settings. No resume,
        # fork, steer, permission decision or second app-server is available.
        try:
            value = self.call(
                "thread-follower-start-turn",
                {
                    "conversationId": thread_id,
                    "turnStart": {
                        "request": {
                            "threadId": thread_id,
                            "input": [
                                {"type": "text", "text": text, "text_elements": []}
                            ],
                        },
                        "context": {
                            "attachments": [],
                            "commentAttachments": [],
                            "inheritThreadSettings": True,
                            "useAppServerPermissionDefault": True,
                        },
                    },
                },
                version=2,
                target=owner,
            )
            if value.get("handledByClientId") != owner:
                raise DesktopUncertain("Delivery owner was not confirmed")
            result = value.get("result")
            result = result.get("result") if isinstance(result, dict) else None
            turn = result.get("turn") if isinstance(result, dict) else None
            turn_id = turn.get("id") if isinstance(turn, dict) else None
            if not isinstance(turn_id, str) or not turn_id:
                raise DesktopUncertain(
                    "Desktop acknowledgement is incomplete; check the original conversation before sending again"
                )
            return turn_id
        except (OSError, ValueError, DesktopUnavailable) as exc:
            raise DesktopUncertain(
                "Delivery is unconfirmed; check the original conversation before sending again"
            ) from exc


def locate_thread(thread_id: str, homes: list[Path] | None = None) -> tuple[Path, Path]:
    matches: list[tuple[Path, Path]] = []
    for home in homes if homes is not None else codex_homes():
        versions = [
            (int(p.stem.split("_")[-1]), p)
            for p in home.glob("state_*.sqlite")
            if p.stem.split("_")[-1].isdigit()
        ]
        if not versions:
            continue
        try:
            with closing(
                sqlite3.connect(
                    max(versions)[1].as_uri() + "?mode=ro", uri=True, timeout=0.3
                )
            ) as connection:
                row = connection.execute(
                    "SELECT rollout_path, archived FROM threads WHERE id=?",
                    (thread_id,),
                ).fetchone()
            if not row or row[1]:
                continue
            path = Path(row[0]).resolve(strict=True)
            # The host DB is not permission to read an arbitrary local file.
            if not any(
                path.is_relative_to((home / folder).resolve())
                for folder in ["sessions", "archived_sessions"]
            ):
                continue
            matches.append((home, path))
        except (OSError, sqlite3.Error, TypeError, ValueError):
            continue
    if len(matches) != 1:
        raise DesktopUnavailable(
            "The bound conversation is missing or ambiguous on this host"
        )
    return matches[0]


def history_revision(stamp: os.stat_result) -> str:
    # Replacing a rollout must invalidate offset-based message identities.
    return f"{stamp.st_dev}:{stamp.st_ino}:{stamp.st_size}:{stamp.st_mtime_ns}"


def read_messages(path: Path, *, before: int | None = None) -> dict[str, Any]:
    with path.open("rb") as stream:
        stamp = os.fstat(stream.fileno())
        size = stamp.st_size
        end = min(size, before) if before is not None else size
        if end < 0:
            raise ValueError("History position must be nonnegative")
        start = max(0, end - WINDOW_BYTES)
        stream.seek(start)
        if start:
            stream.readline(WINDOW_BYTES)  # Never parse a partial first record.
        older_cursor = min(stream.tell(), end) if start else 0
        if older_cursor >= end and start:
            older_cursor = start
        messages = []
        while stream.tell() < end:
            offset = stream.tell()
            raw = stream.readline(min(WINDOW_BYTES, end - offset))
            if not raw.endswith(b"\n"):
                continue  # A partial append is retried on the next observation.
            try:
                record = json.loads(raw)
                item = record.get("payload", {})
                if (
                    record.get("type") != "response_item"
                    or item.get("type") != "message"
                    or item.get("role") not in ["user", "assistant"]
                ):
                    continue
                text = "\n".join(
                    part["text"]
                    for part in item.get("content", [])
                    if isinstance(part, dict)
                    and part.get("type") in ["input_text", "output_text"]
                    and isinstance(part.get("text"), str)
                )
                if text:
                    messages.append(
                        {
                            "id": str(offset),
                            "role": item["role"],
                            "text": text[:24000],
                            "truncated": len(text) > 24000,
                            "timestamp": record.get("timestamp"),
                        }
                    )
            except (ValueError, TypeError, AttributeError):
                continue
    if len(messages) > 100:
        older_cursor = int(messages[-100]["id"])
    return {
        "messages": messages[-100:],
        "before": older_cursor if older_cursor else None,
        "revision": history_revision(stamp),
        "source_ref": hashlib.sha256(
            f"{stamp.st_dev}:{stamp.st_ino}".encode()
        ).hexdigest(),
        "history_size": size,
        "bounded": bool(start) or len(messages) > 100,
    }


def activity(home: Path, thread_id: str) -> dict[str, Any]:
    return observe_codex_threads([thread_id], homes=[home])[thread_id].to_payload()


def input_fingerprint(binding: str, text: str) -> str:
    return hashlib.sha256((binding + "\0" + text).encode()).hexdigest()
