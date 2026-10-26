"""Bounded IO observations for native-selected Codex replay, never Turn authority.

SessionStart(resume) is emitted after native history reconstruction. Reuse still
assumes the qualified native host replays its selected append-only source. Bind
that source to an observed complete read, require its unchanged physical prefix
and exact persisted last reader output, and reject unknown/lossy replay tails.
This is not a scan of model inputs or a certificate from a session ID/journal.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
from typing import Any

# A compatibility profile of the native rollout, not a shared control vocabulary.
# Unknown record types fall back to full until their replay semantics qualify.
_APPEND_RECORDS = {"response_item", "turn_context", "world_state", "token_usage_record"}
_APPEND_EVENTS = {"task_started", "task_complete", "item_completed", "token_count",
    "thread_settings_applied", "exec_command_begin", "exec_command_end", "agent_message",
    "agent_reasoning", "agent_reasoning_raw_content", "agent_reasoning_section_break",
    "agent_reasoning_raw_content_delta", "agent_reasoning_delta", "agent_message_delta",
    "hook_started", "hook_completed", "warning", "error", "stream_error", "turn_aborted",
    "terminal_interaction", "background_event"}
_MAX_HISTORY_BYTES = 64 * 1024 * 1024
_SHELL_OUTPUT = re.compile(r"\AChunk ID: [^\r\n]+\nWall time: [0-9.]+ seconds\n"
    r"Process exited with code 0\n(?:Original token count: [0-9]+\n)?Output:\n(.*)\Z", re.S)


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode()).hexdigest()


def _snapshot(event: dict[str, Any]) -> tuple[dict[str, Any], bytes] | None:
    path = Path(event.get("transcript_path") or "")
    if not path.is_absolute() or path.resolve(strict=True) != path:
        return None
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or info.st_ino != path.lstat().st_ino
                or info.st_mode & 0o022 or not 0 < info.st_size <= _MAX_HISTORY_BYTES):
            return None
        raw = stream.read(info.st_size + 1)
        after = os.fstat(stream.fileno())
    if len(raw) != info.st_size or after.st_size != info.st_size or not raw.endswith(b"\n"):
        return None
    first = json.loads(raw.split(b"\n", 1)[0])
    meta = first.get("payload", {})
    if (first.get("type") != "session_meta" or meta.get("id") != event.get("session_id")
            or meta.get("cwd") != event.get("cwd") or meta.get("forked_from_id")
            or meta.get("history_mode") != "paginated" or meta.get("cli_version") != "0.162.1"
            or not event.get("model") or not event.get("permission_mode")):
        return None
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino,
        "prefix_bytes": len(raw), "prefix_sha256": hashlib.sha256(raw).hexdigest(),
        "model": event["model"], "permission_mode": event["permission_mode"]}, raw


def _read_pages(output: object) -> list[dict[str, Any]]:
    from .codex_context import _context_pages

    # This is the native exec_command result envelope, not a prose classifier.
    if isinstance(output, str) and (match := _SHELL_OUTPUT.fullmatch(output)):
        output = match[1]
    return _context_pages(output)


def _matches(anchor: dict[str, Any], snapshot: dict[str, Any], raw: bytes,
             event: dict[str, Any]) -> bool:
    if any(anchor.get(k) != snapshot.get(k) for k in
           ("path", "device", "inode", "model", "permission_mode")):
        return False
    end = anchor.get("prefix_bytes")
    if (type(end) is not int or not 0 < end <= len(raw) or raw[end - 1:end] != b"\n"
            or hashlib.sha256(raw[:end]).hexdigest() != anchor.get("prefix_sha256")):
        return False
    # A native writer may persist the last output before or after PostToolUse.
    # Require its exact page bytes once in the selected history in either case.
    matches = 0
    for line in raw.splitlines():
        record = json.loads(line)
        payload = record.get("payload", {})
        if (record.get("type") == "response_item" and payload.get("type") == "function_call_output"
                and payload.get("call_id") == anchor.get("read_call_id")):
            if _digest(_read_pages(payload.get("output"))) != anchor.get("read_pages_sha256"):
                return False
            matches += 1
    if matches != 1:
        return False
    for line in raw[end:].splitlines():
        record = json.loads(line)
        kind, payload = record.get("type"), record.get("payload", {})
        if kind == "event_msg":
            if payload.get("type") not in _APPEND_EVENTS:
                return False
            # Changes to history mode are not ordinary append-only settings.
            if payload.get("type") == "thread_settings_applied" and payload.get("history_mode") not in (None, "paginated"):
                return False
        elif kind not in _APPEND_RECORDS:
            return False  # includes compaction, retained-history replacement and rollback
        if kind == "turn_context" and (payload.get("cwd") != event["cwd"] or payload.get("model") != event["model"]):
            return False
    return True


def native_resume_verified(state: dict[str, Any], event: dict[str, Any]) -> bool:
    """Observe a qualified replay source; the shared TS owner decides reuse."""
    candidate = state.get("candidate") or {}
    if not isinstance(candidate, dict):
        return False
    anchor = candidate.get("native_resume")
    if (event.get("source") != "resume" or state.get("pending") or not isinstance(anchor, dict)
            or state.get("session_id") != event.get("session_id") or state.get("cwd") != event.get("cwd")):
        return False
    try:
        snapshot = _snapshot(event)
        return snapshot is not None and _matches(anchor, *snapshot, event)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return False


def checkpoint_native_read(state: dict[str, Any], updated: dict[str, Any],
                           event: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    """Attach content-free native source facts only after the typed complete read.

    A delta checkpoint can advance only a previously verified replay chain.
    Failure affects cross-process reuse, not normal in-process reader delivery.
    """
    candidate = updated.get("candidate")
    if (not isinstance(candidate, dict) or updated.get("pending") or not pages
            or candidate == state.get("candidate") or not event.get("tool_use_id")):
        return
    try:
        snapshot = _snapshot(event)
        if snapshot is None:
            return
        facts, raw = snapshot
        if candidate.get("ancestry", 0):
            anchor = (state.get("candidate") or {}).get("native_resume")
            if not isinstance(anchor, dict) or not _matches(anchor, facts, raw, event):
                return
        candidate["native_resume"] = {**facts, "read_call_id": event["tool_use_id"],
            "read_pages_sha256": _digest(pages)}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
        return
