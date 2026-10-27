"""Immutable Markdown Monitor receipts and their existing typed replay adapter.

Historical recovery must remain available after the live source writer retires.
Python owns the durable codec and filesystem read; TypeScript still validates
original-operation identity and decides replay through scheduler.monitor_batch.
"""
from __future__ import annotations

import base64
import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..content_digest import BARE_SHA256_PATTERN
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..todos.path_resolution import resolve_todo_state_path


def _receipt_marker(operation_id: str) -> str:
    return f"<!-- loopx:monitor-batch:{hashlib.sha256(operation_id.encode()).hexdigest()} "


def _read_receipt(text: str, operation_id: str) -> dict[str, Any] | None:
    marker = _receipt_marker(operation_id)
    matches = re.findall(re.escape(marker) + r"([A-Za-z0-9+/=]+) -->", text)
    if text.count(marker) != len(matches):
        raise ValueError("Monitor batch operation receipt is malformed")
    if len(matches) > 1:
        raise ValueError("duplicate Monitor batch operation receipts")
    if not matches:
        return None
    receipt = json.loads(base64.b64decode(matches[0], validate=True))
    if not isinstance(receipt, dict):
        raise ValueError("Monitor batch operation receipt must be an object")
    return receipt


def is_monitor_receipt_line(line: str, *, goal_id: str) -> bool:
    """Recognize only the historical writer's complete, identity-bound line.

    This codec check establishes generated ownership, not replay authority.
    The original bytes remain available to the typed replay validator.
    """
    match = re.fullmatch(r"<!-- loopx:monitor-batch:([0-9a-f]{64}) ([A-Za-z0-9+/=]+) -->",
        line.rstrip("\r\n"))
    if match is None:
        return False

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate Monitor receipt field")
            result[key] = value
        return result

    try:
        receipt = json.loads(base64.b64decode(match[2], validate=True), object_pairs_hook=unique_object)
    except (ValueError, UnicodeError, RecursionError):
        return False
    if not isinstance(receipt, dict) or set(receipt) != {
            "schema_version", "goal_id", "operation_id", "request_sha256", "writeback"}:
        return False
    operation = receipt.get("operation_id")
    writeback = receipt.get("writeback")
    return (receipt.get("schema_version") == "loopx_monitor_batch_receipt_v0"
        and receipt.get("goal_id") == goal_id
        and isinstance(operation, str) and bool(operation)
        and hashlib.sha256(operation.encode()).hexdigest() == match[1]
        and isinstance(receipt.get("request_sha256"), str)
        and BARE_SHA256_PATTERN.fullmatch(receipt["request_sha256"]) is not None
        and isinstance(writeback, dict)
        and writeback.get("schema_version") == "monitor_poll_todo_writeback_v0"
        and writeback.get("goal_id") == goal_id
        and writeback.get("monitor_effect_id") == operation)


def _plan(request: dict[str, Any], **facts: Any) -> dict[str, Any]:
    try:
        result = effect_runtime_result(
            "scheduler.monitor_batch.replay",
            {**request, **facts},
        )
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    if not isinstance(result, dict) or result.get("schema_version") != "loopx_monitor_batch_plan_result_v0":
        raise TypeError("TypeScript Monitor batch plan shape mismatch")
    return result


def _writeback(planned: dict[str, Any]) -> dict[str, Any]:
    return dict(planned["writeback"])


def replay_legacy_monitor_poll(
    *, registry_path: Path, request: dict[str, Any],
) -> dict[str, Any] | None:
    """Read immutable pre-promotion receipts without reopening a legacy writer."""
    _, state = resolve_todo_state_path(registry_path=registry_path, goal_id=request["goal_id"],
                                      require_existing=False)
    if not state.exists():
        return None
    previous = _read_receipt(state.read_text(encoding="utf-8"), request["operation_id"])
    if previous is None:
        return None
    return _writeback(_plan(request, registered_agents=[], todos=[], previous_receipt=previous))
