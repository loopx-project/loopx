"""Bounded, read-only app-server observation of one caller-selected turn."""

from __future__ import annotations

import hashlib
import json
import queue
import re
import subprocess
import threading
import time
from typing import Any

from .codex_app_delivery import OBSERVATION_SCHEMA, _reject_constant, _unique_object

_MAX_BYTES = 2 * 1024 * 1024
_HEARTBEAT = re.compile(
    r"<heartbeat>\n  <automation_id>([A-Za-z0-9._:-]+)</automation_id>\n"
    r"  <current_time_iso>\d{4}-\d{2}-\d{2}T[0-9:.]+Z</current_time_iso>\n"
    r"  <instructions>\n(.*)\n  </instructions>\n</heartbeat>\n",
    re.DOTALL,
)
_ACTIVITY = frozenset(
    {"agentMessage", "reasoning", "commandExecution", "mcpToolCall", "dynamicToolCall"}
)


class HostObservationError(Exception):
    """Only fixed public-safe reason codes cross the adapter boundary."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _read_thread(
    codex_bin: str, thread_id: str, *, timeout: float = 10
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            [codex_bin, "app-server", "--listen", "stdio://"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        raise HostObservationError("host_observer_unavailable") from None
    messages: queue.Queue = queue.Queue(maxsize=129)

    def read() -> None:
        total = 0
        try:
            for _ in range(128):
                line = process.stdout.readline(_MAX_BYTES - total + 1)
                total += len(line)
                if not line or total > _MAX_BYTES:
                    break
                messages.put_nowait(
                    json.loads(
                        line,
                        object_pairs_hook=_unique_object,
                        parse_constant=_reject_constant,
                    )
                )
        except (OSError, ValueError, RecursionError, queue.Full):
            pass
        finally:
            messages.put_nowait(None)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()

    def send(payload: dict) -> None:
        process.stdin.write((json.dumps(payload) + "\n").encode())
        process.stdin.flush()

    def receive(request_id: int) -> dict:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise HostObservationError("host_observer_timeout")
            try:
                message = messages.get(timeout=remaining)
            except queue.Empty:
                raise HostObservationError("host_observer_timeout") from None
            if not isinstance(message, dict):
                raise HostObservationError("host_observer_response_invalid")
            if message.get("id") == request_id:
                if "error" in message or not isinstance(message.get("result"), dict):
                    raise HostObservationError("host_observer_read_failed")
                return message["result"]
            # Never answer host requests, including requests for approval.
            if "id" in message:
                raise HostObservationError("host_observer_response_invalid")

    try:
        send(
            {
                "id": 1,
                "method": "initialize",
                "params": {
                    "clientInfo": {"name": "loopx_delivery_canary", "version": "1"}
                },
            }
        )
        receive(1)
        send({"method": "initialized"})
        send(
            {
                "id": 2,
                "method": "thread/read",
                "params": {"threadId": thread_id, "includeTurns": True},
            }
        )
        return receive(2)
    except (OSError, ValueError):
        raise HostObservationError("host_observer_response_invalid") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
        reader.join(timeout=1)
        process.stdin.close()
        process.stdout.close()


def _input_text(item: dict) -> str | None:
    if item.get("type") == "userMessage":
        content = item.get("content")
        if (
            isinstance(content, list)
            and len(content) == 1
            and isinstance(content[0], dict)
            and content[0].get("type") == "text"
        ):
            return content[0].get("text")
    if (
        item.get("type") == "functionCallOutput"
        and item.get("name") == "automation_update"
        and item.get("namespace") == "codex_app"
    ):
        output = item.get("output")
        if isinstance(output, str):
            return output
        if (
            isinstance(output, list)
            and len(output) == 1
            and isinstance(output[0], dict)
            and output[0].get("type") == "input_text"
        ):
            return output[0].get("text")
    return None


def observe_codex_app_delivery(
    *, codex_bin: str, expected: dict[str, str], observed_at_ms: int
) -> dict[str, Any]:
    result = _read_thread(codex_bin, expected["thread_id"])
    thread = result.get("thread")
    if (
        not isinstance(thread, dict)
        or thread.get("id") != expected["thread_id"]
        or not isinstance(thread.get("turns"), list)
    ):
        raise HostObservationError("host_observer_response_invalid")
    turns = [
        t
        for t in thread["turns"]
        if isinstance(t, dict) and t.get("id") == expected["turn_id"]
    ]
    if len(turns) != 1:
        raise HostObservationError("host_selected_turn_unavailable")
    turn = turns[0]
    items = turn.get("items")
    if (
        turn.get("itemsView", "full") != "full"
        or not isinstance(items, list)
        or any(not isinstance(i, dict) for i in items)
    ):
        raise HostObservationError("host_selected_turn_incomplete")
    # The initial input must carry the envelope. Later steering cannot repair it.
    first_input = next(
        (
            i
            for i, item in enumerate(items)
            if item.get("type") in {"userMessage", "functionCallOutput"}
        ),
        None,
    )
    matches = []
    for index, item in enumerate(items):
        text = _input_text(item)
        match = _HEARTBEAT.fullmatch(text) if isinstance(text, str) else None
        if match and match[1] == expected["automation_id"]:
            matches.append((index, match[2]))
    digest = None
    activity = False
    if len(matches) == 1 and matches[0][0] == first_input:
        index, body = matches[0]
        try:
            digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        except UnicodeError:
            raise HostObservationError("host_observer_response_invalid") from None
        activity = any(item.get("type") in _ACTIVITY for item in items[index + 1 :])
    started = turn.get("startedAt")
    return {
        "schema_version": OBSERVATION_SCHEMA,
        **expected,
        "prompt_sha256": digest,
        "turn_started_at_ms": started * 1000 if type(started) is int else None,
        "agent_activity_observed": activity,
        # The check's as-of time; no per-item timestamps are fabricated.
        "observed_at_ms": observed_at_ms,
    }
