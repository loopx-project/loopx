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

from ...file_lock import LockAcquisitionPolicy, exclusive_file_lock
from ...registry import atomic_write_json
from .cadence import report_cadence_window
from ...control_plane.content_digest import ENVELOPED_SHA256_PATTERN
from ...control_plane.digest_envelope import sha256_envelope

CADENCE_WINDOW_SCHEMA = "periodic_report_cadence_window_v0"
JOURNAL_SCHEMA = "periodic_report_cadence_journal_v0"
_ID = re.compile(r"^[a-z][a-z0-9_.:-]{2,127}$")


def _digest(value: object) -> str:
    return sha256_envelope(
        json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    )


def cadence_journal_path(root: Path, goal_id: str) -> Path:
    if not _ID.fullmatch(goal_id):
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
        not isinstance(value[key], str) or not _ID.fullmatch(value[key])
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
    path = cadence_journal_path(runtime_root, goal_id)
    if not path.exists():
        return None
    # Invalid durable state must surface a failure, never look like an empty
    # journal: treating corruption as absence would create a second report.
    value = json.loads(path.read_text(encoding="utf-8"))
    fields = {
        "schema_version", "goal_id", "window", "admitted_at", "publication",
    }
    if not isinstance(value, dict) or set(value) not in (fields, fields | {"supersedes"}) or value["schema_version"] != JOURNAL_SCHEMA or value["goal_id"] != goal_id:
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
    return value


def admit_cadence_window(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    subscription: Mapping[str, Any], now: datetime,
    subscription_resolver: Callable[[], Mapping[str, Any] | None] | None = None,
) -> dict[str, Any]:
    """Freeze the latest due interval, preserving unfinished prepared work.

    The caller elects one registered reporter before invoking this function.
    A lock serializes competing hosts. Restart acknowledgement is recovered
    only from the existing validated publication cursor, including after a
    crash between publication and the next admission. Missed periods coalesce
    to the latest interval once the unfinished interval has been delivered.
    A configuration change may supersede an unprepared interval under the same
    lock used by consumption, preserving its predecessor as immutable history.
    """
    if not _ID.fullmatch(agent_id):
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
            agent_id=agent_id, subscription=current, now=now, path=path)


def _admit_cadence_window_locked(
    *, runtime_root: Path, goal_id: str, agent_id: str,
    subscription: Mapping[str, Any], now: datetime, path: Path,
) -> dict[str, Any]:
    if subscription.get("enabled") is not True or subscription.get("schedule") is None:
        return {"status": "disabled", "mutated": False, "window": None}
    calculated = report_cadence_window(subscription["schedule"], now=now)
    assert calculated is not None
    from .presets import build_periodic_report_preset_activation
    activation = build_periodic_report_preset_activation(str(subscription.get("profile_preset") or ""))
    profile = activation.get("profile")
    if activation.get("active") is not True or not isinstance(profile, Mapping) or (
        "cadence_due" not in profile.get("trigger_policy", {}).get("enabled_kinds", [])
    ):
        raise ValueError("periodic-report profile does not support calendar reports")
    proposed = {
        "schema_version": CADENCE_WINDOW_SCHEMA, "goal_id": goal_id,
        "agent_id": agent_id, "subscription_revision": subscription.get("effective_revision"),
        "profile_ref": {"profile_id": profile["profile_id"],
                        "profile_version": profile["profile_version"],
                        "profile_digest": activation["profile_digest"]},
        "trigger_policy": profile["trigger_policy"],
        **calculated,
    }
    proposed["window_id"] = "cadence_" + _digest(proposed).split(":")[1]
    validate_cadence_window(proposed)
    previous = read_cadence_journal(runtime_root=runtime_root, goal_id=goal_id)
    mutated = False
    supersedes = None
    if previous is not None:
        old = previous["window"]
        if previous["publication"] is None:
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
                if old["subscription_revision"] == proposed["subscription_revision"]:
                    return {"status": "pending", "mutated": False, "window": old}
                from .pending_intent import _attempt_dir
                # Admission and consumption share this lock. Once any
                # editorial/generation artifact exists, preserve the old
                # window rather than silently replacing its authority.
                if _attempt_dir(runtime_root, goal_id, cadence_intent(old)).exists():
                    return {"status": "configuration_changed", "mutated": False, "window": old}
                archive = path.parent / "superseded-cadence" / (old["window_id"] + ".json")
                if archive.exists():
                    if json.loads(archive.read_text(encoding="utf-8")) != previous:
                        raise ValueError("cadence predecessor archive conflicts with active window")
                else:
                    atomic_write_json(archive, previous)
                # Archive first. A crash before the replacement is written
                # can safely repeat admission without creating two reports.
                supersedes = old["window_id"]
        # A route/profile edit is not a second report for the same due
        # boundary. Clock rollback likewise cannot replay a delivered one.
        if supersedes is None and old["due_at"] >= proposed["due_at"]:
            return {"status": "already_published", "mutated": mutated, "window": old}
    entry = {
        "schema_version": JOURNAL_SCHEMA, "goal_id": goal_id,
        "window": proposed, "admitted_at": now.astimezone(timezone.utc).isoformat(),
        "publication": None,
        **({"supersedes": supersedes} if supersedes else {}),
    }
    atomic_write_json(path, entry)
    return {"status": "pending", "mutated": True, "window": proposed}


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
