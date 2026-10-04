"""Existing rolling-slot read model, independent of commit adapters.

Status, history and usage must interpret the same ledger without importing
spend/void execution. The legacy slot_accounting module re-exports these
objects; transaction and admission authority stays with its typed owners.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

QUOTA_SLOT_SPENT_CLASSIFICATION = "quota_slot_spent"
QUOTA_SLOT_VOIDED_CLASSIFICATION = "quota_slot_voided"


def _int_number(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except ValueError:
            return default
    return default


def load_quota_event_from_run(run: dict[str, Any]) -> dict[str, Any] | None:
    if str(run.get("classification") or "") not in {
        QUOTA_SLOT_SPENT_CLASSIFICATION,
        QUOTA_SLOT_VOIDED_CLASSIFICATION,
    }:
        return None
    event = run.get("quota_event") if isinstance(run.get("quota_event"), dict) else None
    if event:
        return event

    raw_json_path = str(run.get("json_path") or "")
    if not raw_json_path:
        return None
    json_path = Path(raw_json_path).expanduser()
    if not json_path.exists():
        return None
    try:
        record = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(record, dict):
        return None
    event = record.get("quota_event") if isinstance(record.get("quota_event"), dict) else None
    return event


def quota_slot_contribution(run: dict[str, Any]) -> tuple[str, str, int] | None:
    """Classify one run's contribution to the rolling-window slot ledger.

    ``goal_quota_with_spend_ledger`` enforces quota from this rule and the
    usage summary reports from it, so both read an event the same way: the
    quota event's ``event_type`` decides, a spend is keyed by the run it was
    recorded against, and a void by the run it targets. A run with no usable
    event contributes no slot rather than a default one, which is what the
    ledger already assumed.
    """

    event = load_quota_event_from_run(run)
    if not event:
        return None
    slots = max(0, _int_number(event.get("slots"), default=0))
    if slots <= 0:
        return None
    event_type = str(event.get("event_type") or "")
    if event_type == QUOTA_SLOT_SPENT_CLASSIFICATION:
        run_key = str(event.get("run_generated_at") or run.get("generated_at") or "")
        if not run_key:
            return None
        return ("spent", run_key, slots)
    if event_type == QUOTA_SLOT_VOIDED_CLASSIFICATION:
        voided_run_generated_at = str(event.get("voided_run_generated_at") or "")
        if not voided_run_generated_at:
            return None
        return ("voided", voided_run_generated_at, slots)
    return None


def net_quota_slot_spend(
    contributions: Iterable[tuple[Any, str, int]],
) -> dict[Any, int]:
    """Clamp each spend bucket against the voids that target it.

    A void only cancels the spend recorded against the key it names, so a
    window that no longer holds that spend is never pushed negative and a void
    never cancels an unrelated spend.
    """

    spent: dict[Any, int] = {}
    voided: dict[Any, int] = {}
    for bucket, kind, slots in contributions:
        target = spent if kind == "spent" else voided
        target[bucket] = target.get(bucket, 0) + slots
    return {
        bucket: max(0, slots - voided.get(bucket, 0))
        for bucket, slots in spent.items()
    }
