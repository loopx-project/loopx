"""Optional fail-open observers that run after accountable Turn settlement."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any


PostSettlement = Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    Mapping[str, Any],
]


def run_post_settlement_callback(
    *,
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    post_settlement: PostSettlement | None,
    journal: dict[str, Any],
    persist_journal: Callable[[Mapping[str, Any]], None],
) -> None:
    if post_settlement is None:
        return
    existing = journal.get("post_settlement")
    if isinstance(existing, Mapping) and existing.get("status") not in {
        "provider_unavailable",
        "committed_pending",
        "readback_unverified",
        "runtime_unavailable",
    }:
        return
    try:
        receipt = post_settlement(
            plan,
            result,
            {
                "schema_version": "turn_post_settlement_evidence_v0",
                "task_validation": dict(journal.get("task_validation") or {}),
                "writeback": dict(journal.get("writeback") or {}),
                "quota_spend": dict(journal.get("quota_spend") or {}),
            },
        )
        if not isinstance(receipt, Mapping):
            raise TypeError("post-settlement callback must return an object")
        journal["post_settlement"] = dict(receipt)
    except Exception:  # noqa: BLE001 - optional learning never gates the Turn
        journal["post_settlement"] = {
            "ok": True,
            "schema_version": "turn_post_settlement_observer_v0",
            "status": "runtime_unavailable",
            "reason_code": "post_settlement_observer_failed",
            "fail_open": True,
            "external_writes_performed": False,
        }
    persist_journal(journal)
