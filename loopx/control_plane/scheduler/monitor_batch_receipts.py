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
