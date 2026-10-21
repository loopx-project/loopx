"""Optional capability effects that run after accountable Turn settlement."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any


PostSettlement = Callable[
    [Mapping[str, Any], Mapping[str, Any], Mapping[str, Any]],
    Mapping[str, Any],
]


@dataclass(frozen=True, slots=True)
class PostSettlementHookRegistration:
    """One optional capability IO adapter using the existing settlement owner."""

    hook_id: str
    capability_id: str
    callback: PostSettlement


def registered_post_settlement(
    registrations: tuple[PostSettlementHookRegistration, ...],
) -> PostSettlement | None:
    if not registrations:
        return None
    if len({item.hook_id for item in registrations}) != len(registrations):
        raise ValueError("duplicate post-settlement registration")

    def dispatch(
        plan: Mapping[str, Any], result: Mapping[str, Any], evidence: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        previous = evidence.get("previous_post_settlement")
        previous = previous if isinstance(previous, Mapping) else {}
        receipts = dict(previous.get("results") or {})
        identities = dict(previous.get("capability_ids") or {})
        pending = False
        for registration in registrations:
            prior = receipts.get(registration.hook_id)
            if (
                identities.get(registration.hook_id) == registration.capability_id
                and isinstance(prior, Mapping)
                and prior.get("status") not in _RETRYABLE
            ):
                continue
            try:
                receipt = dict(registration.callback(plan, result, evidence))
            except (
                Exception
            ):  # The capability owner recovers ambiguous provider effects.
                receipt = {
                    "status": "runtime_unavailable",
                    "fail_open": True,
                    "external_writes_performed": None,
                }
            receipts[registration.hook_id] = receipt
            identities[registration.hook_id] = registration.capability_id
            pending = pending or receipt.get("status") in _RETRYABLE
        return {
            "status": "runtime_unavailable" if pending else "observed",
            "results": receipts,
            "capability_ids": identities,
            "fail_open": True,
            "external_writes_performed": (
                True
                if any(
                    item.get("external_writes_performed") is True
                    for item in receipts.values()
                )
                else None
                if any(
                    item.get("external_writes_performed") is None
                    for item in receipts.values()
                )
                else False
            ),
        }

    return dispatch


_RETRYABLE = frozenset(
    {
        "provider_unavailable",
        "committed_pending",
        "readback_unverified",
        "runtime_unavailable",
    }
)


def post_settlement_receipt(
    *,
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    post_settlement: PostSettlement | None,
    journal: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Read/reconcile through capability owners without changing Turn state.

    Owners retain durable effect identity and retry policy. A terminal Turn's
    original receipt remains immutable even when an owner recovers a lost reply.
    """
    if post_settlement is None:
        return None
    existing = journal.get("post_settlement")
    if isinstance(existing, Mapping) and existing.get("status") not in _RETRYABLE:
        return None
    try:
        receipt = post_settlement(
            plan,
            result,
            {
                "schema_version": "turn_post_settlement_evidence_v0",
                "task_validation": dict(journal.get("task_validation") or {}),
                "writeback": dict(journal.get("writeback") or {}),
                "quota_spend": dict(journal.get("quota_spend") or {}),
                "previous_post_settlement": dict(existing or {}),
            },
        )
        if not isinstance(receipt, Mapping):
            raise TypeError("post-settlement callback must return an object")
        return dict(receipt)
    except Exception:  # noqa: BLE001 - optional learning never gates the Turn
        return {
            "ok": True,
            "schema_version": "turn_post_settlement_observer_v0",
            "status": "runtime_unavailable",
            "reason_code": "post_settlement_observer_failed",
            "fail_open": True,
            "external_writes_performed": None,
        }


def run_post_settlement_callback(
    *,
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
    post_settlement: PostSettlement | None,
    journal: dict[str, Any],
    persist_journal: Callable[[Mapping[str, Any]], None],
) -> None:
    receipt = post_settlement_receipt(
        plan=plan,
        result=result,
        post_settlement=post_settlement,
        journal=journal,
    )
    if receipt is not None:
        journal["post_settlement"] = receipt
        persist_journal(journal)
