"""Read-only CLI transport for an existing canonical Monitor execution proof.

The coordination writer remains the authority for lease admission and its
version CAS. A prior quota transaction receipt is preferred so a retry keeps
the same immutable observation fingerprint after its lease is released.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from ..coordination.local_authority import read_canonical_todos_if_promoted
from ..work_items.task_lease import lease_is_active
from .error_codes import QuotaCommandValidationError


def _proof(value: object) -> tuple[str, int] | None:
    if not isinstance(value, Mapping):
        return None
    key = value.get("idempotency_key")
    version = value.get("expected_version")
    if (
        not isinstance(key, str)
        or not key
        or key != key.strip()
        or not isinstance(version, int)
        or isinstance(version, bool)
        or not 1 <= version <= 9007199254740991
    ):
        return None
    return key, version


def _receipt_proof(
    *, runtime_root: Path, goal_id: str, effect_id: str
) -> tuple[bool, tuple[str, int] | None]:
    transaction = (
        runtime_root / "goals" / goal_id / "runs" / ".transactions"
        / "quota-monitor-poll" / f"{hashlib.sha256(effect_id.encode()).hexdigest()[:24]}.json"
    )
    try:
        receipt = json.loads(transaction.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise QuotaCommandValidationError(
            "prior monitor-poll transaction receipt cannot be read for lease-proof replay"
        ) from exc
    if not isinstance(receipt, Mapping) or receipt.get("effect_id") != effect_id:
        raise QuotaCommandValidationError(
            "prior monitor-poll transaction receipt has an invalid effect identity"
        )
    status = receipt.get("status")
    if status == "provider_pending":
        source = receipt.get("provider_plan")
        unleased_schema = "monitor_poll_todo_provider_plan_v0"
    elif status in {"prepared", "committed"}:
        record = receipt.get("record")
        event = record.get("monitor_event") if isinstance(record, Mapping) else None
        source = event.get("todo_writeback") if isinstance(event, Mapping) else None
        unleased_schema = "monitor_poll_todo_writeback_v0"
    else:
        source = None
        unleased_schema = ""
    value = source.get("lease_proof") if isinstance(source, Mapping) else None
    if value is None and isinstance(source, Mapping) and source.get("schema_version") == unleased_schema:
        # A committed legacy or soft-claim observation has no lease to replay.
        # Keep it distinct from a missing receipt so the caller can still
        # reject a later hard-lease authority transition.
        return True, None
    proof = _proof(value)
    if proof is None:
        raise QuotaCommandValidationError(
            "prior monitor-poll transaction receipt lacks a valid lease proof; reconcile this effect before retrying"
        )
    return True, proof


def current_monitor_lease_proof(
    *, runtime_root: Path, goal_id: str, todo_id: str, agent_id: str, effect_id: str
) -> tuple[str | None, int | None]:
    """Resolve an existing proof; never claim, renew, or relax a lease."""
    snapshot = read_canonical_todos_if_promoted(
        runtime_root=runtime_root, goal_id=goal_id, include_leases=True,
    )
    has_prior, prior = _receipt_proof(
        runtime_root=runtime_root, goal_id=goal_id, effect_id=effect_id
    )
    if has_prior:
        if prior is None and (
            snapshot.get("handoff_mode") == "hard_lease"
            or any(lease.get("todo_id") == todo_id for lease in snapshot["leases"])
        ):
            raise QuotaCommandValidationError(
                "prior monitor-poll transaction receipt has no lease proof for current canonical authority"
            )
        return prior if prior is not None else (None, None)
    leases = [lease for lease in snapshot["leases"] if lease.get("todo_id") == todo_id]
    if snapshot.get("handoff_mode") != "hard_lease" and not leases:
        return None, None
    active = [lease for lease in leases if lease_is_active(lease)]
    if len(active) != 1 or active[0].get("owner") != agent_id:
        raise QuotaCommandValidationError(
            "monitor-poll requires a current active task lease owned by --agent-id; "
            "acquire or renew that exact Monitor lease before observing"
        )
    lease = active[0]
    proof = _proof({
        "idempotency_key": lease.get("idempotency_key"),
        "expected_version": lease.get("version"),
    })
    if proof is None:
        raise QuotaCommandValidationError("current active task lease has no valid execution proof")
    return proof
