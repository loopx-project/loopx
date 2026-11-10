"""Reserve a calendar window; freeze prepared work until verified publication.

This journal is admission state, not report progress or a publication cursor.
It never sends a report and never advances the authoritative delivery cursor.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .field_shapes import REPORT_IDENTITY_PATTERN
from ...file_lock import LockAcquisitionPolicy, exclusive_file_lock, try_exclusive_file_lock
from ...registry import atomic_write_json
from .cadence import report_cadence_next_due_at, report_cadence_window
from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from ...control_plane.digest_envelope import sha256_envelope

CADENCE_WINDOW_SCHEMA = "periodic_report_cadence_window_v0"
JOURNAL_SCHEMA = "periodic_report_cadence_journal_v0"


def _digest(value: object) -> str:
    return sha256_envelope(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )


def cadence_journal_path(root: Path, goal_id: str) -> Path:
    if not REPORT_IDENTITY_PATTERN.fullmatch(goal_id):
        raise ValueError("cadence Goal identity is invalid")
    return root / "goals" / goal_id / "periodic_reports" / "cadence.json"


def validate_cadence_window(raw: object) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version", "goal_id", "agent_id", "subscription_revision",
        "schedule", "start_at", "due_at", "next_due_at", "window_id",
        "profile_ref", "trigger_policy",
    }:
        raise ValueError("cadence window contract is invalid")
    value = dict(raw)
    if value["schema_version"] != CADENCE_WINDOW_SCHEMA or any(
        not isinstance(value[key], str) or not REPORT_IDENTITY_PATTERN.fullmatch(value[key])
        for key in ("goal_id", "agent_id")
    ):
        raise ValueError("cadence window identity is invalid")
    if not ENVELOPED_SHA256_PATTERN.fullmatch(str(value["subscription_revision"])):
        raise ValueError("cadence subscription revision is invalid")
    if not isinstance(value["profile_ref"], Mapping) or not isinstance(value["trigger_policy"], Mapping):
        raise ValueError("cadence profile facts are invalid")
    try:
        due = datetime.fromisoformat(str(value["due_at"]).replace("Z", "+00:00"))
        calculated = report_cadence_window(value["schedule"], now=due)
    except (TypeError, ValueError) as exc:
        raise ValueError("cadence window boundary is invalid") from exc
    if calculated is None or any(value[key] != calculated[key] for key in calculated):
        raise ValueError("cadence window boundaries do not match schedule")
    identity = {key: item for key, item in value.items() if key != "window_id"}
    if value["window_id"] != "cadence_" + _digest(identity).split(":")[1]:
        raise ValueError("cadence window digest does not match contents")
    return value


def read_cadence_journal(*, runtime_root: Path, goal_id: str) -> dict[str, Any] | None:
    return _read_journal(cadence_journal_path(runtime_root, goal_id), goal_id)


def _read_journal(path: Path, goal_id: str) -> dict[str, Any] | None:
    if not path.exists():
        return None
    # Invalid durable state must surface a failure, never look like an empty
    # journal: treating corruption as absence would create a second report.
    value = json.loads(path.read_text(encoding="utf-8"))
    fields = {
        "schema_version", "goal_id", "window", "admitted_at", "publication",
    }
    if not isinstance(value, dict) or set(value) - {"supersedes", "retirement", "task_completion"} != fields or value["schema_version"] != JOURNAL_SCHEMA or value["goal_id"] != goal_id:
        raise ValueError("cadence journal contract is invalid")
    if "supersedes" in value and not re.fullmatch(r"cadence_[0-9a-f]{64}", str(value["supersedes"])):
        raise ValueError("cadence journal predecessor is invalid")
    window = validate_cadence_window(value["window"])
    if window["goal_id"] != goal_id:
        raise ValueError("cadence journal Goal does not match window")
    publication = value["publication"]
    if publication is not None and (
        not isinstance(publication, dict)
        or set(publication) != {"cursor_id", "publication_id", "delivered_at"}
        or any(not isinstance(item, str) or not item for item in publication.values())
    ):
        raise ValueError("cadence journal publication reference is invalid")
    retirement = value.get("retirement")
    completion = value.get("task_completion")
    if completion is not None and (publication is not None or not isinstance(completion, dict)
            or window["profile_ref"].get("profile_id") != "session_task"
            or set(completion) != {"todo_id", "turn_instance_id", "message_id", "message_sha256", "completed_at"}
            or any(not isinstance(item, str) or not item for item in completion.values())
            or not ENVELOPED_SHA256_PATTERN.fullmatch(completion["message_sha256"])):
        raise ValueError("cadence task completion reference is invalid")
    if retirement is not None and (publication is not None or completion is not None or not isinstance(retirement, dict)
            or set(retirement) != {"reason", "source_receipt_id", "retired_at"}
            or retirement["reason"] not in {"owner_cancelled_failed_generation", "owner_cancelled_failed_task"}
            or any(not isinstance(item, str) or not item for item in retirement.values())):
        raise ValueError("cadence journal retirement reference is invalid")
    return value


def record_cadence_task_completion(*, runtime_root: Path, goal_id: str,
                                   expected_window_id: str, receipt: dict[str, str]) -> dict[str, Any]:
    """Checkpoint the native owner's independently read Task and Core reply.

    This is a finite task completion, not a report publication. The caller
    verifies native commit, Task validation and the exact original Core body.
    Reconciliation after a lost write must supply the same receipt.
    """
    path = cadence_journal_path(runtime_root, goal_id)
    with exclusive_file_lock(path, policy=LockAcquisitionPolicy.MUTATION,
                             operation="complete_managed_calendar_task"):
        current = read_cadence_journal(runtime_root=runtime_root, goal_id=goal_id)
        if (not current or current["window"]["window_id"] != expected_window_id
                or current["window"]["profile_ref"].get("profile_id") != "session_task"
                or current["publication"] is not None or current.get("retirement")):
            raise ValueError("the original unpublished calendar task changed")
        if current.get("task_completion"):
            if current["task_completion"] != receipt:
                raise ValueError("the calendar task completion changed its original result")
            return current
        proposed = {**current, "task_completion": receipt}
        # Validate before writing using the same receipt shape as readback.
        if (set(receipt) != {"todo_id", "turn_instance_id", "message_id", "message_sha256", "completed_at"}
                or any(not isinstance(item, str) or not item for item in receipt.values())
                or not ENVELOPED_SHA256_PATTERN.fullmatch(receipt["message_sha256"])):
            raise ValueError("cadence task completion reference is invalid")
        atomic_write_json(path, proposed)
        return proposed


def retire_failed_cadence_window(*, runtime_root: Path, goal_id: str,
                                expected_window_id: str, source_receipt_id: str,
                                reason: str = "owner_cancelled_failed_generation") -> dict[str, Any]:
    """Retain an explicitly cancelled known failure; never invent publication.

    The original execution owner must prove a terminal failed generation with
    no generated report or delivery attempt before calling this transition.
    This retires admission only; it neither retries a model nor drops evidence.
    """
    path = cadence_journal_path(runtime_root, goal_id)
    with exclusive_file_lock(path, policy=LockAcquisitionPolicy.MUTATION,
                             operation="retire_failed_report_window"):
        return _retire_failed_cadence_window_locked(runtime_root=runtime_root, goal_id=goal_id,
            expected_window_id=expected_window_id, source_receipt_id=source_receipt_id, reason=reason)


def _retire_failed_cadence_window_locked(*, runtime_root: Path, goal_id: str,
                                       expected_window_id: str, source_receipt_id: str,
                                       reason: str = "owner_cancelled_failed_generation") -> dict[str, Any]:
    """Reuse the transition while the settings owner holds the cadence lock."""
    if (not isinstance(source_receipt_id, str) or not source_receipt_id
            or reason not in {"owner_cancelled_failed_generation", "owner_cancelled_failed_task"}):
        raise ValueError("failed cadence retirement requires its original receipt")
    current = read_cadence_journal(runtime_root=runtime_root, goal_id=goal_id)
    if not current or current["window"]["window_id"] != expected_window_id or current["publication"] is not None or current.get("task_completion"):
        raise ValueError("the unpublished failed report window changed")
    if current.get("retirement"):
        if current["retirement"]["source_receipt_id"] != source_receipt_id or current["retirement"]["reason"] != reason:
            raise ValueError("the failed window retirement changed its source")
        return current
    current["retirement"] = {"reason": reason,
        "source_receipt_id": source_receipt_id, "retired_at": datetime.now(timezone.utc).isoformat()}
    atomic_write_json(cadence_journal_path(runtime_root, goal_id), current)
    return current


def _archive_predecessor(path: Path, previous: dict[str, Any]) -> str:
    identity = previous["window"]["window_id"]
    archive = path.parent / "superseded-cadence" / (identity + ".json")
    if archive.exists():
        if json.loads(archive.read_text(encoding="utf-8")) != previous:
            raise ValueError("cadence predecessor archive conflicts with active window")
    else:
        atomic_write_json(archive, previous)
    return identity


def _missed_windows(previous: dict[str, Any], window: Mapping[str, Any]) -> bool:
    old = previous["window"]
    return (old["agent_id"] == window["agent_id"]
        and old["subscription_revision"] == window["subscription_revision"]
        and old["next_due_at"] < window["due_at"]
        and previous["publication"] is None
        and not previous.get("retirement") and not previous.get("task_completion"))


def missed_cadence_bounds(*, runtime_root: Path, window: dict[str, Any]) -> dict[str, str] | None:
    """Read the skipped due boundaries from the retained predecessor chain."""
    goal_id = window["goal_id"]
    path = cadence_journal_path(runtime_root, goal_id)
    current = _read_journal(path, goal_id)
    if not current or current["window"] != window:
        return None
    first_due_at = None
    while predecessor := current.get("supersedes"):
        previous = _read_journal(path.parent / "superseded-cadence" / (predecessor + ".json"), goal_id)
        if not previous or previous["window"]["window_id"] != predecessor:
            raise ValueError("cadence predecessor archive is unavailable")
        if not _missed_windows(previous, current["window"]):
            break
        first_due_at = previous["window"]["due_at"]
        current = previous
    return {"first_due_at": first_due_at, "last_due_at": window["start_at"]} if first_due_at else None


def _reserve_window(path: Path, window: dict[str, Any], now: datetime,
                    supersedes: str | None) -> dict[str, Any]:
    entry = {
        "schema_version": JOURNAL_SCHEMA, "goal_id": window["goal_id"],
        "window": window, "admitted_at": now.astimezone(timezone.utc).isoformat(),
        "publication": None,
        **({"supersedes": supersedes} if supersedes else {}),
    }
    atomic_write_json(path, entry)
    return {"status": "pending", "mutated": True, "window": window}


def admit_cadence_window(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    subscription: Mapping[str, Any], now: datetime,
    subscription_resolver: Callable[[], Mapping[str, Any] | None] | None = None,
    can_replace_failed_window: Callable[[dict[str, Any]], bool] | None = None,
) -> dict[str, Any]:
    """Freeze the latest due interval, preserving unfinished prepared work.

    The caller elects one registered reporter before invoking this function.
    A lock serializes competing hosts. Restart acknowledgement is recovered
    only from the existing validated publication cursor, including after a
    crash between publication and the next admission. Missed periods coalesce
    to the latest interval once the unfinished interval has been delivered.
    A configuration change may supersede an unprepared interval under the same
    lock used by consumption, preserving its predecessor as immutable history.
    An original Session report can also keep a proven generation failure as
    missed history when more than one new boundary has elapsed. The original
    execution owner supplies that proof; ordinary callers preserve the window.
    """
    if not REPORT_IDENTITY_PATTERN.fullmatch(agent_id):
        raise ValueError("cadence Agent identity is invalid")
    path = cadence_journal_path(runtime_root, goal_id)
    if subscription_resolver is None and (
        subscription.get("enabled") is not True or subscription.get("schedule") is None
    ):
        return {"status": "disabled", "mutated": False, "window": None}
    with exclusive_file_lock(path, policy=LockAcquisitionPolicy.MUTATION,
                             agent_id=agent_id, operation="periodic_report_cadence_admission"):
        # Live callers resolve current registration/configuration only after
        # acquiring admission ownership; a constructed hook is not authority.
        current = subscription_resolver() if subscription_resolver else subscription
        if current is None:
            return {"status": "disabled", "mutated": False, "window": None}
        return _admit_cadence_window_locked(runtime_root=runtime_root, goal_id=goal_id,
            agent_id=agent_id, subscription=current, now=now, path=path,
            can_replace_failed_window=can_replace_failed_window)


def _admit_cadence_window_locked(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    subscription: Mapping[str, Any], now: datetime, path: Path,
    can_replace_failed_window: Callable[[dict[str, Any]], bool] | None,
) -> dict[str, Any]:
    if subscription.get("enabled") is not True or subscription.get("schedule") is None:
        return {"status": "disabled", "mutated": False, "window": None}
    calculated = report_cadence_window(subscription["schedule"], now=now)
    if subscription.get("profile_preset") == "session-task":
        from .goal_configuration import managed_session_report_destination, normalize_session_task
        if subscription.get("source") != "goal_override" or not managed_session_report_destination(subscription):
            raise ValueError("calendar tasks require an explicit original managed Session")
        normalize_session_task(subscription.get("task"))
        # Reuse the native calendar, without advertising a report renderer or
        # a report source adapter that an ordinary Task never invokes.
        profile_ref = {"profile_id": "session_task", "profile_version": "v1"}
        profile_ref["profile_digest"] = _digest(profile_ref)
        trigger_policy = {"enabled_kinds": ["cadence_due"], "minimum_interval_seconds": 0}
    else:
        from .presets import build_periodic_report_preset_activation
        activation = build_periodic_report_preset_activation(str(subscription.get("profile_preset") or ""))
        profile = activation.get("profile")
        if activation.get("active") is not True or not isinstance(profile, Mapping) or (
            "cadence_due" not in profile.get("trigger_policy", {}).get("enabled_kinds", [])
        ):
            raise ValueError("periodic-report profile does not support calendar reports")
        profile_ref = {"profile_id": profile["profile_id"], "profile_version": profile["profile_version"],
            "profile_digest": activation["profile_digest"]}
        trigger_policy = profile["trigger_policy"]
    proposed = {
        "schema_version": CADENCE_WINDOW_SCHEMA, "goal_id": goal_id,
        "agent_id": agent_id, "subscription_revision": subscription.get("effective_revision"),
        "profile_ref": profile_ref,
        "trigger_policy": trigger_policy,
        **calculated,
    } if calculated is not None else None
    if proposed is not None:
        proposed["window_id"] = "cadence_" + _digest(proposed).split(":")[1]
        validate_cadence_window(proposed)
    previous = read_cadence_journal(runtime_root=runtime_root, goal_id=goal_id)
    mutated = False
    supersedes = None
    if previous is not None:
        old = previous["window"]
        if previous["publication"] is None and not previous.get("retirement") and not previous.get("task_completion"):
            # Import lazily to keep the pure calendar evaluator independent
            # of subscription and generation composition.
            from .incremental import read_periodic_report_publication_cursor
            from .post_writeback_hook import evaluate_periodic_report_trigger_evaluation_intent
            cursor = read_periodic_report_publication_cursor(runtime_root=runtime_root, goal_id=goal_id, agent_id=old["agent_id"])
            trigger = evaluate_periodic_report_trigger_evaluation_intent(cadence_intent(old))
            if cursor and trigger["selected_trigger_id"] in cursor["covered_trigger_ids"]:
                previous["publication"] = {key: cursor[key] for key in (
                    "cursor_id", "publication_id", "delivered_at")}
                atomic_write_json(path, previous)
                mutated = True
            else:
                from .pending_intent import _attempt_dir
                if old["subscription_revision"] == subscription.get("effective_revision"):
                    pending = {"status": "pending", "mutated": False, "window": old}
                    if (can_replace_failed_window is None or proposed is None
                            or not _missed_windows(previous, proposed)):
                        return pending
                    folder = _attempt_dir(runtime_root, goal_id, cadence_intent(old))
                    if not folder.is_dir() or folder.is_symlink():
                        return pending
                    # Archive and replace under the same lock as generation.
                    with try_exclusive_file_lock(folder / "generation.lock",
                            operation="replace_failed_report_window") as ownership:
                        if ownership is None or not can_replace_failed_window(old):
                            return pending
                        return _reserve_window(path, proposed, now, _archive_predecessor(path, previous))
                # Admission and consumption share this lock. Once any
                # editorial/generation artifact exists, preserve the old
                # window rather than silently replacing its authority.
                if _attempt_dir(runtime_root, goal_id, cadence_intent(old)).exists():
                    return {"status": "configuration_changed", "mutated": False, "window": old}
                if proposed is None:
                    return {"status": "not_due", "mutated": False, "window": None,
                            "next_due_at": report_cadence_next_due_at(subscription["schedule"], now=now)}
                # Archive first. A crash before the replacement is written
                # can safely repeat admission without creating two reports.
                supersedes = _archive_predecessor(path, previous)
        if previous.get("retirement"):
            if proposed is None or old["due_at"] >= proposed["due_at"]:
                return {"status": "retired", "mutated": False, "window": old}
            supersedes = _archive_predecessor(path, previous)
        if previous.get("task_completion"):
            if proposed is not None and old["due_at"] >= proposed["due_at"]:
                return {"status": "already_completed", "mutated": False, "window": old}
            if proposed is not None:
                supersedes = _archive_predecessor(path, previous)
        # A route/profile edit is not a second report for the same due
        # boundary. Clock rollback likewise cannot replay a delivered one.
        if proposed is not None and supersedes is None and old["due_at"] >= proposed["due_at"]:
            return {"status": "already_published", "mutated": mutated, "window": old}
    if proposed is None:
        return {"status": "not_due", "mutated": mutated, "window": None,
                "next_due_at": report_cadence_next_due_at(subscription["schedule"], now=now)}
    return _reserve_window(path, proposed, now, supersedes)


def cadence_intent(window: Mapping[str, Any]) -> dict[str, Any]:
    """Use a typed clock event, without inventing a user or stage receipt."""
    typed = validate_cadence_window(window)
    return {
        "schema_version": "loopx_capability_intent_v0",
        "intent_kind": "periodic_report.trigger_evaluation",
        "idempotency_key": "periodic-report:" + typed["window_id"],
        "source_receipt_id": typed["window_id"], "requested_write_scope": [],
        "payload": {
            "schema_version": "periodic_report_trigger_evaluation_intent_v0",
            "cadence_window": typed,
            "profile_ref": typed["profile_ref"],
            "trigger_policy": typed["trigger_policy"],
            "generation_authorized": False, "external_delivery_authorized": False,
        },
    }
