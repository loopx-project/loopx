"""Existing rolling-slot read model, independent of commit adapters.

Status, history and usage must interpret the same ledger without importing
spend/void execution. The legacy slot_accounting module re-exports these
objects; transaction and admission authority stays with its typed owners.
"""

from __future__ import annotations

from typing import Any, cast

import json
import math
import os
import stat
from collections.abc import Iterable
from pathlib import Path

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


def _lexical_absolute(path: Path) -> Path:
    """Collapse ``.`` and ``..`` without resolving symlinks.

    The TypeScript reader uses lexical ``path.resolve`` for the goal-id check,
    so a symlink target must not decide which goal the stored path names.
    """

    return Path(os.path.abspath(path))


def _copied_goal_run_file_name(stored_path: str, goal_id: str, extension: str) -> str | None:
    """File name when ``stored_path`` is ``<any>/goals/<goalId>/runs/<file>``.

    A runtime copy leaves the old absolute path in the index row. Only that
    same-goal runs file may be opened from the current runs directory.
    """

    if not goal_id:
        return None
    raw = Path(stored_path).expanduser()
    if not raw.is_absolute():
        return None
    normalized = _lexical_absolute(raw)
    file_name = normalized.name
    runs_parent = normalized.parent
    goal_dir = runs_parent.parent
    goals_dir = goal_dir.parent
    if (
        not file_name
        or file_name in {".", ".."}
        or normalized.suffix != extension
        or runs_parent.name != "runs"
        or goal_dir.name != goal_id
        or goals_dir.name != "goals"
    ):
        return None
    return file_name


def _stays_inside_runs_dir(runs_dir: Path, stored_path: str) -> bool:
    """True when the stored path is already lexically inside ``runs_dir``."""

    try:
        runs_real = Path(runs_dir).expanduser().resolve(strict=True)
    except OSError:
        return False
    resolved = _lexical_absolute(Path(stored_path).expanduser())
    relative = os.path.relpath(resolved, runs_real)
    return bool(
        relative
        and relative != "."
        and relative != ".."
        and not relative.startswith(".." + os.sep)
        and not os.path.isabs(relative)
    )


def _relocated_run_artifact_path(
    runs_dir: Path,
    goal_id: str,
    stored_path: str,
    extension: str,
) -> Path | None:
    """Same-named regular file in the current runs directory, or None.

    Symlinks, directories, and files whose real path leaves the runs directory
    are not substitutes for the stored artifact.
    """

    file_name = _copied_goal_run_file_name(stored_path, goal_id, extension)
    if file_name is None or _stays_inside_runs_dir(runs_dir, stored_path):
        return None
    try:
        runs_real = Path(runs_dir).expanduser().resolve(strict=True)
    except OSError:
        return None
    if not runs_real.is_dir():
        return None
    candidate = runs_real / file_name
    try:
        if not stat.S_ISREG(candidate.lstat().st_mode):
            return None
        target = candidate.resolve(strict=True)
    except OSError:
        return None
    relative = os.path.relpath(target, runs_real)
    if relative != file_name:
        return None
    return target


def _quota_event_artifact_path(
    raw_json_path: str,
    *,
    runs_dir: Path | None,
    goal_id: str,
) -> str | None:
    """Path to open, or None when a known runs directory must not leave itself.

    Callers that do not know the current runs directory keep the stored path.
    Callers that do keep a path already inside that directory, or the same-named
    regular file copied into it. Every other absolute location stays unread.
    """

    if runs_dir is None:
        return raw_json_path
    if _stays_inside_runs_dir(runs_dir, raw_json_path):
        return raw_json_path
    relocated = _relocated_run_artifact_path(runs_dir, goal_id, raw_json_path, ".json")
    if relocated is None:
        return None
    return str(relocated)


# Commit metadata the TypeScript writer repeats on the index row and the
# JSON artifact. A historical row that omits the field has no binding to check.
_COMMIT_METADATA_FIELDS = {
    QUOTA_SLOT_SPENT_CLASSIFICATION: "quota_spend_commit",
    QUOTA_SLOT_VOIDED_CLASSIFICATION: "quota_void_commit",
}
# Bindings the spend writers kept before commit metadata existed. A missing
# side is unknown; both sides stating different values is a conflict.
_BOUND_IDENTITY_FIELDS = (
    "agent_id",
    "effect_ref",
    "turn_instance_id",
    "todo_id",
    "replan_obligation_id",
    "settlement_identity",
    "goal_ref",
)


def _canonical_json(value: Any) -> str:
    """Stable object text for the same comparison the TypeScript reader uses."""

    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _stated(value: Any) -> bool:
    return value is not None and value != ""


def _explicit_identity_conflict(index_row: dict[str, Any], retained: dict[str, Any]) -> bool:
    for field in _BOUND_IDENTITY_FIELDS:
        expected = index_row.get(field)
        actual = retained.get(field)
        if not _stated(expected) or not _stated(actual):
            continue
        if _canonical_json(expected) != _canonical_json(actual):
            return True
    return False


def _commit_identity_conflict(index_row: dict[str, Any], artifact: dict[str, Any]) -> bool:
    field = _COMMIT_METADATA_FIELDS.get(str(index_row.get("classification") or ""))
    if field is None or field not in index_row:
        return False
    index_commit = index_row.get(field)
    artifact_commit = artifact.get(field)
    if not isinstance(index_commit, dict) or not isinstance(artifact_commit, dict):
        return True
    return _canonical_json(index_commit) != _canonical_json(artifact_commit)


def _artifact_identity_conflict(
    index_row: dict[str, Any],
    artifact: dict[str, Any],
    goal_id: str,
) -> bool:
    """True when the artifact cannot be the debit named by the index row.

    A file in the current runs directory can still describe another commit for
    the same Goal. Omitted historical fields stay unknown. A commit reference
    recorded on the index must match the artifact, and any identity both sides
    state must agree.
    """

    classification = str(index_row.get("classification") or "")
    artifact_classification = artifact.get("classification")
    if artifact_classification is not None and artifact_classification != classification:
        return True
    artifact_goal_id = artifact.get("goal_id")
    if goal_id and artifact_goal_id is not None and artifact_goal_id != goal_id:
        return True
    index_generated_at = index_row.get("generated_at")
    artifact_generated_at = artifact.get("generated_at")
    if (
        isinstance(index_generated_at, str)
        and index_generated_at
        and isinstance(artifact_generated_at, str)
        and artifact_generated_at
        and index_generated_at != artifact_generated_at
    ):
        return True
    if _commit_identity_conflict(index_row, artifact):
        return True
    if _explicit_identity_conflict(index_row, artifact):
        return True
    event = artifact.get("quota_event")
    return isinstance(event, dict) and _explicit_identity_conflict(index_row, event)


def _read_quota_event_artifact(
    raw_json_path: str,
    *,
    index_row: dict[str, Any] | None = None,
    goal_id: str = "",
) -> tuple[dict[str, Any] | None, str | None]:
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
    if index_row is not None and _artifact_identity_conflict(index_row, record, goal_id):
        return None, QUOTA_EVENT_UNREADABLE_EVENT_INVALID
    event = cast(dict[str, Any], record.get("quota_event")) if isinstance(record.get("quota_event"), dict) else None
    if not event:
        return None, QUOTA_EVENT_UNREADABLE_EVENT_MISSING
    return event, None


def _artifact_goal_id(run: dict[str, Any], goal_id: str | None) -> str:
    if goal_id:
        return str(goal_id)
    return str(run.get("goal_id") or "")


def load_quota_event_from_run(
    run: dict[str, Any],
    *,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
) -> dict[str, Any] | None:
    if str(run.get("classification") or "") not in {
        QUOTA_SLOT_SPENT_CLASSIFICATION,
        QUOTA_SLOT_VOIDED_CLASSIFICATION,
    }:
        return None
    event = cast(dict[str, Any], run.get("quota_event")) if isinstance(run.get("quota_event"), dict) else None
    if event:
        return event

    raw_json_path = str(run.get("json_path") or "")
    if not raw_json_path:
        return None
    artifact_path = _quota_event_artifact_path(
        raw_json_path,
        runs_dir=runs_dir,
        goal_id=_artifact_goal_id(run, goal_id),
    )
    if artifact_path is None:
        return None
    event, _reason = _read_quota_event_artifact(
        artifact_path,
        index_row=run,
        goal_id=_artifact_goal_id(run, goal_id),
    )
    return event


def resolve_quota_event_from_run(
    run: dict[str, Any],
    *,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
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
    event = cast(dict[str, Any], run.get("quota_event")) if isinstance(run.get("quota_event"), dict) else None
    if event:
        return event, None
    raw_json_path = str(run.get("json_path") or "")
    if not raw_json_path:
        return None, QUOTA_EVENT_UNREADABLE_MISSING_SOURCE
    artifact_path = _quota_event_artifact_path(
        raw_json_path,
        runs_dir=runs_dir,
        goal_id=_artifact_goal_id(run, goal_id),
    )
    # A known runs directory that cannot accept this path has no safe artifact.
    # Leaving the stored absolute path unread stops a copy from opening an
    # unrelated file that happens to still exist at the old location.
    if artifact_path is None:
        return None, QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING
    return _read_quota_event_artifact(
        artifact_path,
        index_row=run,
        goal_id=_artifact_goal_id(run, goal_id),
    )


def quota_slot_contribution(
    run: dict[str, Any],
    *,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
) -> tuple[str, str, int] | None:
    """Classify one run's contribution to the rolling-window slot ledger.

    ``goal_quota_with_spend_ledger`` enforces quota from this rule and the
    usage summary reports from it, so both read an event the same way: the
    quota event's ``event_type`` decides, a spend is keyed by the run it was
    recorded against, and a void by the run it targets. A run with no usable
    event contributes no slot rather than a default one, which is what the
    ledger already assumed.
    """

    contribution, _reason = quota_slot_observation(
        run,
        runs_dir=runs_dir,
        goal_id=goal_id,
    )
    return contribution


def quota_slot_observation(
    run: dict[str, Any],
    *,
    runs_dir: Path | None = None,
    goal_id: str | None = None,
) -> tuple[tuple[str, str, int] | None, str | None]:
    """Observe one run's slot contribution and its readability.

    ``(contribution, None)`` means the run's quota event was read, whether or
    not it spends a slot. ``(None, reason)`` means the index row claims a quota
    event that cannot be read back, which is an integrity failure the ledger
    must report instead of silently dropping.
    """

    event, reason = resolve_quota_event_from_run(
        run,
        runs_dir=runs_dir,
        goal_id=goal_id,
    )
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
