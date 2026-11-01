"""Existing rolling-slot read model, independent of commit adapters.

Status, history and usage must interpret the same ledger without importing
spend/void execution. The legacy slot_accounting module re-exports these
objects; transaction and admission authority stays with its typed owners.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

QUOTA_SLOT_SPENT_CLASSIFICATION = "quota_slot_spent"
QUOTA_SLOT_VOIDED_CLASSIFICATION = "quota_slot_voided"

# Why an indexed quota event could not be read back. The ledger must not
# confuse "this event spends nothing" with "this event cannot be read", so
# every unreadable case carries a stable machine-readable reason.
QUOTA_EVENT_UNREADABLE_MISSING_SOURCE = "missing_source"
QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING = "artifact_missing"
QUOTA_EVENT_UNREADABLE_ARTIFACT_UNREADABLE = "artifact_unreadable"
QUOTA_EVENT_UNREADABLE_ARTIFACT_INVALID_JSON = "artifact_invalid_json"
QUOTA_EVENT_UNREADABLE_ARTIFACT_NOT_OBJECT = "artifact_not_object"
QUOTA_EVENT_UNREADABLE_EVENT_MISSING = "event_missing"
QUOTA_EVENT_UNREADABLE_EVENT_INVALID = "event_invalid"

# The closed vocabulary of unreadable reasons. The public quota projection
# only reports a reason from this set, so an unreadable event cannot smuggle
# free-form text into the status surface.
QUOTA_EVENT_UNREADABLE_REASONS = frozenset(
    {
        QUOTA_EVENT_UNREADABLE_MISSING_SOURCE,
        QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING,
        QUOTA_EVENT_UNREADABLE_ARTIFACT_UNREADABLE,
        QUOTA_EVENT_UNREADABLE_ARTIFACT_INVALID_JSON,
        QUOTA_EVENT_UNREADABLE_ARTIFACT_NOT_OBJECT,
        QUOTA_EVENT_UNREADABLE_EVENT_MISSING,
        QUOTA_EVENT_UNREADABLE_EVENT_INVALID,
    }
)


def _int_number(value: Any, *, default: int) -> int:
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        try:
            return int(value)
        except (ValueError, OverflowError):
            return default
    if isinstance(value, str):
        try:
            return int(float(value.strip()))
        except (ValueError, OverflowError):
            return default
    return default


def _slot_count(value: Any) -> int | None:
    """Read one quota event's slot count, or ``None`` when it is unknown.

    A missing or unparseable count means the remaining budget is unknown, which
    the ledger must not fold into "this event spends nothing". Integers, finite
    floats and numeric strings are the supported representations; booleans and
    unmapped shapes are not counts. A readable negative count clamps to zero,
    preserving the existing contribution semantics.
    """

    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return max(0, int(value))
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return max(0, int(float(text)))
        except (ValueError, OverflowError):
            return None
    return None


def _read_quota_event_artifact(raw_json_path: str) -> tuple[dict[str, Any] | None, str | None]:
    """Read the quota event out of one durable artifact.

    Returns ``(event, unreadable_reason)``. A readable artifact without a
    usable quota event is still an integrity problem: the index row claims a
    spend or void happened, so the missing event must be reported instead of
    being treated as "no spend".
    """

    json_path = Path(raw_json_path).expanduser()
    if not json_path.exists():
        return None, QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING
    try:
        raw = json_path.read_bytes()
    except OSError:
        return None, QUOTA_EVENT_UNREADABLE_ARTIFACT_UNREADABLE
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None, QUOTA_EVENT_UNREADABLE_ARTIFACT_UNREADABLE
    try:
        record = json.loads(text)
    except json.JSONDecodeError:
        return None, QUOTA_EVENT_UNREADABLE_ARTIFACT_INVALID_JSON
    if not isinstance(record, dict):
        return None, QUOTA_EVENT_UNREADABLE_ARTIFACT_NOT_OBJECT
    event = record.get("quota_event") if isinstance(record.get("quota_event"), dict) else None
    if not event:
        return None, QUOTA_EVENT_UNREADABLE_EVENT_MISSING
    return event, None


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
    event, _reason = _read_quota_event_artifact(raw_json_path)
    return event


def resolve_quota_event_from_run(
    run: dict[str, Any],
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve one run's quota event and report why it could not be read.

    ``(event, None)`` means the event was read, ``(None, reason)`` means the
    index row claims a quota event but the event cannot be read back, and
    ``(None, None)`` means the row is not a quota event row at all.
    """

    classification = str(run.get("classification") or "")
    if classification not in {
        QUOTA_SLOT_SPENT_CLASSIFICATION,
        QUOTA_SLOT_VOIDED_CLASSIFICATION,
    }:
        return None, None
    event = run.get("quota_event") if isinstance(run.get("quota_event"), dict) else None
    if event:
        return event, None
    raw_json_path = str(run.get("json_path") or "")
    if not raw_json_path:
        return None, QUOTA_EVENT_UNREADABLE_MISSING_SOURCE
    return _read_quota_event_artifact(raw_json_path)


def quota_slot_contribution(run: dict[str, Any]) -> tuple[str, str, int] | None:
    """Classify one run's contribution to the rolling-window slot ledger.

    ``goal_quota_with_spend_ledger`` enforces quota from this rule and the
    usage summary reports from it, so both read an event the same way: the
    quota event's ``event_type`` decides, a spend is keyed by the run it was
    recorded against, and a void by the run it targets. A run with no usable
    event contributes no slot rather than a default one, which is what the
    ledger already assumed.
    """

    contribution, _reason = quota_slot_observation(run)
    return contribution


def quota_slot_observation(
    run: dict[str, Any],
) -> tuple[tuple[str, str, int] | None, str | None]:
    """Observe one run's slot contribution and its readability.

    ``(contribution, None)`` means the run's quota event was read, whether or
    not it spends a slot. ``(None, reason)`` means the index row claims a quota
    event that cannot be read back, which is an integrity failure the ledger
    must report instead of silently dropping.
    """

    event, reason = resolve_quota_event_from_run(run)
    if reason:
        return None, reason
    if not event:
        return None, None
    event_type = str(event.get("event_type") or "")
    if event_type == QUOTA_SLOT_SPENT_CLASSIFICATION:
        run_key = str(event.get("run_generated_at") or run.get("generated_at") or "")
        if not run_key:
            return None, QUOTA_EVENT_UNREADABLE_EVENT_INVALID
        slots = _slot_count(event.get("slots"))
        if slots is None:
            # The event names a spend but not how much it spent. That is an
            # unknown quantity, not a zero spend: guessing zero would hand the
            # Goal back the quota it already used.
            return None, QUOTA_EVENT_UNREADABLE_EVENT_INVALID
        if slots == 0:
            # An explicit, readable zero is a legal zero contribution.
            return None, None
        return ("spent", run_key, slots), None
    if event_type == QUOTA_SLOT_VOIDED_CLASSIFICATION:
        voided_run_generated_at = str(event.get("voided_run_generated_at") or "")
        if not voided_run_generated_at:
            return None, QUOTA_EVENT_UNREADABLE_EVENT_INVALID
        slots = _slot_count(event.get("slots"))
        if slots is None:
            # Same rule as a spend: an unknown cancelled quantity cannot be
            # read as "this void cancels nothing".
            return None, QUOTA_EVENT_UNREADABLE_EVENT_INVALID
        if slots == 0:
            # An explicit, readable zero is a legal zero contribution.
            return None, None
        return ("voided", voided_run_generated_at, slots), None
    # The index row claims a quota event, but the event does not name a type
    # this ledger understands. Reading it as "this event spends nothing" would
    # hand the Goal back the quota it already used, so the malformed event is
    # reported as unreadable instead.
    return None, QUOTA_EVENT_UNREADABLE_EVENT_INVALID


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
