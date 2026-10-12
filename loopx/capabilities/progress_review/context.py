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
        progress_review_receipt_order_key,
        progress_review_receipt_summary,
    )

    policy = progress_review_goal_policy(goal)
    goal_id = str(goal.get("id") or "").strip()
    if policy["mode"] == "off" or runtime_root is None or not goal_id:
        return None
    from ...control_plane.effect_runtime import effect_runtime_result, EffectRuntimeRemoteError
    from ...control_plane.goals.goal_ref_validation import exact_goal_ref
    read_error = None
    try:
        loaded, rejected = load_progress_review_receipts(Path(runtime_root), goal_id)
    except (OSError, ValueError, EffectRuntimeRemoteError):
        loaded, rejected = [], 0
        read_error = "receipt_store_unavailable"
    # Only newly scoped canonical observations need this current-owner check.
    # Old receipts and manual studies retain their existing policy semantics.
    current_by_todo = {}
    qualified = []
    for receipt in loaded:
        binding = receipt.get("evidence_scope", {}).get("criterion_binding", {})
        if binding.get("origin") == "goal_acceptance":
            todo_id = binding["todo_id"]
            if todo_id not in current_by_todo:
                try:
                    goal_instance_id = goal.get("goal_instance_id")
                    observation = effect_runtime_result("goal.acceptance.inspect", {
                        "goal_id": goal_id, "runtime_root": str(runtime_root), "todo_id": todo_id,
                        **({"goal_ref": exact_goal_ref(goal_id, goal_instance_id)}
                           if goal_instance_id is not None else {}),
                    })
                    current_by_todo[todo_id] = observation.get("completion_requirements")
                except (OSError, ValueError, EffectRuntimeRemoteError):
                    current_by_todo[todo_id] = None
            try:
                requirements = current_by_todo[todo_id]
                selected = effect_runtime_result("progress_review.criterion_basis", {
                    "requirements": requirements, "criterion_ids": binding["criterion_ids"], "acceptance": [],
                    "goal_id": goal_id, "agent_id": receipt["run"]["agent_id"],
                })["binding"] if requirements is not None else None
                current = selected == binding
            except (ValueError, EffectRuntimeRemoteError):
                current = False
            receipt = {**receipt, "criterion_current": current}
            if not current:
                receipt.update(status="stale", reason="criterion_basis_unavailable",
                               drift_signal={"noul": None, "choice": None})
        qualified.append(receipt)
    loaded = qualified
    pinned = policy.get("contract_revision")
    # Newest by run order, never by the observer's local sequence counter.
    newest = max(loaded, key=progress_review_receipt_order_key, default=None)
    newest_revision = str(newest["contract_revision"]) if newest else None
    if pinned:
        # Only receipts bound to the pinned revision are current evidence; the
        # pin is manual, so a newer receipt under another revision means the
        # observer basis moved and the Goal owner must re-pin (or not).
        receipts = [item for item in loaded if item["contract_revision"] == pinned]
        stale = len(loaded) - len(receipts) + sum(item.get("criterion_current") is False for item in receipts)
    else:
        receipts, stale = loaded, sum(item.get("criterion_current") is False for item in loaded)
    result = {
        "policy": policy,
        "receipts": receipts,
        "summary": progress_review_receipt_summary(
            receipts,
            policy=policy,
            rejected=rejected,
            stale=stale,
            newest_contract_revision=newest_revision,
        ),
    }
    result["summary"]["read_state"] = "unavailable" if read_error else "observed" if loaded else "missing"
    if read_error:
        result["summary"]["read_error"] = read_error
    return result


__all__ = ["external_progress_review_context"]
