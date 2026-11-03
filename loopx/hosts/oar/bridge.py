"""Python framing for the TypeScript OAR owner and existing Host supervisor."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from contextlib import AbstractContextManager
import json
import threading
from pathlib import Path
from typing import Any

from ...control_plane.turn_driver.host_process_transport import HostOutputLines, run_host_process
from .installation import node_executable, runtime_directory


_FRAGMENT_CHARACTERS = 64 * 1024
_MAX_RECORD_BYTES = 32 * 1024 * 1024
# A surrogate boundary can shorten each full writer chunk by one code unit.
# Bound list overhead too, so tiny malicious fragments cannot exhaust memory.
_MAX_RECORD_FRAGMENTS = 1 + _MAX_RECORD_BYTES // (_FRAGMENT_CHARACTERS - 1)
_FRAGMENT_FIELDS = {"type", "sequence", "index", "last", "data"}


def _unique_json_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for key, value in pairs:
        if key in record:
            raise ValueError("Duplicate OAR transport field")
        record[key] = value
    return record


def _invalid_json_constant(value: str) -> None:
    raise ValueError("Invalid OAR transport JSON constant")


def _load_record(text: str) -> dict[str, Any]:
    record = json.loads(text, object_pairs_hook=_unique_json_fields, parse_constant=_invalid_json_constant)
    if not isinstance(record, dict):
        raise ValueError("Invalid OAR transport record")
    return record


def run_oar_turn(
    request: Mapping[str, Any], *, project: Path,
    timeout_seconds: float | None = None, drain_timeout_seconds: float = 2,
    on_record: Callable[[dict[str, Any]], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    launch_guard: Callable[[], AbstractContextManager[None]] | None = None,
    cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Run one OAR Turn without acquiring Goal, scheduling or retry authority.

    The shared Host supervisor still owns process groups, hard deadlines and
    drain evidence. A successful process exit alone never proves Turn success.
    """
    command = [node_executable(), "--no-warnings", "--experimental-strip-types", str(runtime_directory() / "stdio.ts")]
    terminal: list[dict[str, Any]] = []
    sessions: list[str] = []
    protocol_complete = True
    # Fragment is local transport vocabulary, never an OAR event. Only one
    # contiguous record may be in flight; an invalid stream cannot recover.
    fragment_parts: list[str] = []
    fragment_sequence = 0
    fragment_index = 0
    fragment_bytes = 0

    def invalidate() -> None:
        nonlocal protocol_complete
        protocol_complete = False
        fragment_parts.clear()

    def observe(line: str) -> None:
        nonlocal protocol_complete, fragment_sequence, fragment_index, fragment_bytes
        if not protocol_complete:
            return
        try:
            record = _load_record(line)
        except (ValueError, RecursionError):
            invalidate()
            return
        if record.get("type") == "fragment":
            if (record.keys() != _FRAGMENT_FIELDS
                    or type(record["sequence"]) is not int or record["sequence"] != fragment_sequence
                    or type(record["index"]) is not int or record["index"] != fragment_index
                    or fragment_index >= _MAX_RECORD_FRAGMENTS
                    or type(record["last"]) is not bool
                    or not isinstance(record["data"], str) or not record["data"]):
                invalidate()
                return
            data = record["data"]
            try:
                fragment_bytes += len(data.encode("utf-8"))
                characters = len(data.encode("utf-16-le")) // 2
            except UnicodeError:
                invalidate()
                return
            if characters > _FRAGMENT_CHARACTERS or fragment_bytes > _MAX_RECORD_BYTES:
                invalidate()
                return
            fragment_parts.append(data)
            fragment_index += 1
            if not record["last"]:
                return
            serialized = "".join(fragment_parts)
            fragment_parts.clear()
            fragment_sequence += 1
            fragment_index = 0
            fragment_bytes = 0
            try:
                record = _load_record(serialized)
            except (ValueError, RecursionError):
                invalidate()
                return
        elif fragment_parts:
            invalidate()
            return
        if record.get("type") not in ("ready", "event", "native", "result"):
            invalidate()
            return
        if record["type"] == "ready":
            session_id = record.get("sessionId")
            if not isinstance(session_id, str) or not session_id:
                protocol_complete = False
            else:
                sessions.append(session_id)
        elif record["type"] == "result":
            result = record.get("result")
            if not isinstance(result, dict):
                protocol_complete = False
            else:
                terminal.append(result)
        if on_record is not None:
            on_record(record)

    lines = HostOutputLines(observe)
    def cleanup_confirmed() -> bool:
        return (protocol_complete and lines.complete and not fragment_parts
                and len(terminal) == 1 and terminal[0].get("cleanupConfirmed") is True)

    observed = run_host_process(
        command, project=project,
        input_text=json.dumps(dict(request), ensure_ascii=False, separators=(",", ":")) + "\n",
        timeout_seconds=timeout_seconds, drain_timeout_seconds=drain_timeout_seconds,
        on_stdout=lines.feed, on_stderr=on_stderr, launch_guard=launch_guard, track_descendants=True,
        cancel_event=cancel_event,
        termination_grace_seconds=6, cleanup_check=cleanup_confirmed,
    )
    lines.finish()
    result = terminal[0] if len(terminal) == 1 else None
    complete = bool(observed["output_complete"] and lines.complete and protocol_complete
                    and not fragment_parts and len(terminal) == 1)
    if result is not None and sessions and result.get("sessionId") != sessions[-1]:
        complete = False
    if len(sessions) > 1:
        complete = False
    turn_outcome = result.get("outcome") if result else None
    kind = turn_outcome.get("kind") if isinstance(turn_outcome, dict) else None
    if kind == "completed" and (len(sessions) != 1 or not isinstance(result.get("sessionId"), str)):
        complete = False
    final_response = result.get("text") if result is not None and kind == "completed" and complete else None
    success = isinstance(final_response, str) and observed["outcome"] == "exited" and observed["returncode"] == 0
    return {
        **observed,
        "returncode": observed["returncode"] if success or observed["returncode"] != 0 else 1,
        "output_complete": complete,
        "final_response": final_response if success else None,
        "session_id": sessions[-1] if sessions else None,
        "turn_outcome": turn_outcome,
        "oar_result": result,
    }
