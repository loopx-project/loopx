"""Load one Goal's typed progress-review receipts for read models and writebacks.

Both `loopx status` and the refresh-state replan writeback call this so the
obligation a reader shows and the obligation an acknowledgement is judged
against come from the same receipts under the same policy.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any


def external_progress_review_context(
    goal: Mapping[str, Any],
    runtime_root: Path | None,
) -> dict[str, Any] | None:
    """Return policy, receipts and a compact summary, or None when off.

    `off`, an unknown runtime root or a missing goal id load nothing, so the
    default configuration adds zero work and zero fields.
    """

    from .policy import progress_review_goal_policy
    from .receipt import (
        load_progress_review_receipts,
        progress_review_receipt_summary,
    )

    policy = progress_review_goal_policy(goal)
    goal_id = str(goal.get("id") or "").strip()
    if policy["mode"] == "off" or runtime_root is None or not goal_id:
        return None
    from ...control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRemoteError
    read_error = None
    try:
        loaded, rejected = load_progress_review_receipts(Path(runtime_root), goal_id)
    except (OSError, ValueError, EffectRuntimeRemoteError):
        loaded, rejected = [], 0
        read_error = "receipt_store_unavailable"
    # Empty IO results need no qualification; all nonempty decisions belong to TS.
    qualification = effect_runtime_result("progress_review.context.qualify", {
        "goal_id": goal_id, "runtime_root": str(runtime_root),
        "goal_instance_id": goal.get("goal_instance_id"),
        "receipts": loaded,
        "contract_revision": policy.get("contract_revision"),
    }) if loaded else None
    receipts = [
        _receipt_number_codec(row, loaded[index])
        for row, index in zip(qualification["selected"], qualification["selected_indices"], strict=True)
    ] if qualification else []
    result = {
        "policy": policy,
        "receipts": receipts,
        "summary": progress_review_receipt_summary(
            receipts,
            policy=policy,
            rejected=rejected,
            stale=qualification["stale"] if qualification else 0,
            newest_contract_revision=qualification["newest_contract_revision"] if qualification else None,
        ),
    }
    result["summary"]["read_state"] = "unavailable" if read_error else "observed" if loaded else "missing"
    if read_error:
        result["summary"]["read_error"] = read_error
    return result


def _receipt_number_codec(value: Any, source: Any) -> Any:
    """Qualification never edits numeric facts; retain their Python IO spelling.

    JSON numbers cannot carry integral floats or arbitrary integer precision
    through JavaScript. Selected source indices preserve those historical facts
    without repeating any qualification or selection decision here.
    """
    if type(value) in (int, float) and type(source) in (int, float):
        return source
    if isinstance(value, dict) and isinstance(source, dict):
        return {key: _receipt_number_codec(child, source.get(key)) for key, child in value.items()}
    if isinstance(value, list) and isinstance(source, list):
        return [
            _receipt_number_codec(child, original)
            for child, original in zip(value, source, strict=True)
        ]
    return value


__all__ = ["external_progress_review_context"]
