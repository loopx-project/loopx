"""An unreadable quota artifact must not read as a zero spend.

The quota ledger is the authority for how much of a Goal's rolling window has
been spent. An index row that claims a ``quota_slot_spent`` or
``quota_slot_voided`` event but whose artifact cannot be read back is an
integrity failure, not a spend of zero: treating it as "no spend" would hand
the Goal back the quota it already used. These tests drive the real
``index.jsonl`` + artifact shape rather than an inline event, because the
inline event is exactly the shortcut that hides the failure.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from loopx.control_plane.quota.ledger_readback import (
    QUOTA_EVENT_UNREADABLE_ARTIFACT_INVALID_JSON,
    QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING,
    QUOTA_EVENT_UNREADABLE_ARTIFACT_NOT_OBJECT,
    QUOTA_EVENT_UNREADABLE_ARTIFACT_UNREADABLE,
    QUOTA_EVENT_UNREADABLE_EVENT_INVALID,
    QUOTA_EVENT_UNREADABLE_EVENT_MISSING,
    QUOTA_EVENT_UNREADABLE_MISSING_SOURCE,
    quota_slot_observation,
)
from loopx.control_plane.quota.usage_summary import build_usage_summary
from loopx.quota import (
    goal_quota_config,
    goal_quota_with_spend_ledger,
    quota_status,
)


GOAL_ID = "quota-ledger-integrity"
NOW = datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc)


def _goal(*, allowed_slots: int = 60) -> dict[str, Any]:
    return {
        "id": GOAL_ID,
        "quota": {
            "compute": 1.0,
            "window_hours": 24,
            "slot_minutes": 1,
            "allowed_slots": allowed_slots,
        },
    }


def _write_artifact(path: Path, event: dict[str, Any] | None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if event is None:
        path.write_text("{\"quota_event\": ", encoding="utf-8")
        return
    path.write_text(json.dumps({"quota_event": event}), encoding="utf-8")


def _spend_row(
    json_path: Path,
    *,
    generated_at: datetime,
    slots: int = 1,
) -> dict[str, Any]:
    """An index row for a spend, with the event only in the artifact."""

    return {
        "generated_at": generated_at.isoformat(),
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "json_path": str(json_path),
    }


def _spend_event(generated_at: datetime, *, slots: int = 1) -> dict[str, Any]:
    return {
        "event_type": "quota_slot_spent",
        "slots": slots,
        "run_generated_at": generated_at.isoformat(),
    }


def _index_rows(runtime: Path, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index_path = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index_path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )
    return rows


def _ledger(runtime: Path, rows: list[dict[str, Any]]) -> dict[str, Any]:
    return goal_quota_with_spend_ledger(_goal(), _index_rows(runtime, rows), now=NOW)


def test_missing_artifact_is_reported_instead_of_counted_as_no_spend(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "missing.json"
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]

    ledger = _ledger(tmp_path, rows)

    assert ledger["spent_slots"] == 0
    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING]


def test_truncated_artifact_is_reported_instead_of_counted_as_no_spend(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "truncated.json"
    _write_artifact(artifact, None)
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]

    ledger = _ledger(tmp_path, rows)

    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_INVALID_JSON]


def test_artifact_without_quota_event_is_reported(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "no-event.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps({"recommended_action": "continue"}), encoding="utf-8")
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]

    ledger = _ledger(tmp_path, rows)

    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_EVENT_MISSING]


def test_non_object_artifact_is_reported(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "list.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]

    ledger = _ledger(tmp_path, rows)

    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_NOT_OBJECT]


def test_index_row_without_any_event_source_is_reported(tmp_path: Path) -> None:
    rows = [
        {
            "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_spent",
        }
    ]

    ledger = _ledger(tmp_path, rows)

    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_MISSING_SOURCE]


def test_unreadable_event_keeps_the_goal_blocked_instead_of_eligible(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "missing.json"
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]
    ledger = _ledger(tmp_path, rows)

    status = quota_status(
        {**_goal(), "quota": {**_goal()["quota"], **ledger}},
        waiting_on="codex",
    )

    assert status["state"] == "blocked_health"
    assert "could not" in status["reason"]
    assert status["blocked_action_scope"] == "automatic_agent_turns"


def test_readable_events_still_count_while_an_unreadable_one_is_reported(
    tmp_path: Path,
) -> None:
    readable = tmp_path / "runs" / "readable.json"
    _write_artifact(readable, _spend_event(NOW - timedelta(minutes=20), slots=2))
    broken = tmp_path / "runs" / "broken.json"
    rows = [
        _spend_row(readable, generated_at=NOW - timedelta(minutes=20)),
        _spend_row(broken, generated_at=NOW - timedelta(minutes=10)),
    ]

    ledger = _ledger(tmp_path, rows)

    assert ledger["spent_slots"] == 2
    assert ledger["spend_event_count"] == 1
    assert ledger["unreadable_event_count"] == 1


def test_restoring_the_artifact_restores_the_ledger_and_releases_the_goal(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "runs" / "spend.json"
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]

    broken = _ledger(tmp_path, rows)
    assert broken["unreadable_event_count"] == 1

    _write_artifact(artifact, _spend_event(NOW - timedelta(minutes=10)))
    repaired = _ledger(tmp_path, rows)

    assert repaired.get("unreadable_event_count", 0) == 0
    assert repaired["spent_slots"] == 1
    assert repaired["spend_event_count"] == 1
    status = quota_status(
        {**_goal(), "quota": {**_goal()["quota"], **repaired}},
        waiting_on="codex",
    )
    assert status["state"] == "eligible"


def test_usage_summary_reports_the_same_unreadable_count(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "missing.json"
    # The usage summary windows are relative to the wall clock, so this row
    # has to sit inside the live 24h window rather than the fixed ledger one.
    live_now = datetime.now(timezone.utc)
    rows = [_spend_row(artifact, generated_at=live_now - timedelta(minutes=10))]

    ledger = _ledger(tmp_path, rows)
    summary = build_usage_summary(
        {"runs": rows},
        parse_timestamp=lambda value: datetime.fromisoformat(str(value)),
    )

    assert summary["totals"]["unreadable_event_count_24h"] == 1
    assert summary["totals"]["unreadable_event_count_7d"] == 1
    assert summary["goals"][0]["unreadable_event_count_24h"] == 1
    # The readable spend stays the same number on both surfaces.
    assert summary["totals"]["quota_spend_slots_24h"] == ledger["spent_slots"]


def test_a_legal_void_still_cancels_the_spend_it_targets(tmp_path: Path) -> None:
    spend = tmp_path / "runs" / "spend.json"
    void = tmp_path / "runs" / "void.json"
    _write_artifact(spend, _spend_event(NOW - timedelta(minutes=20)))
    _write_artifact(
        void,
        {
            "event_type": "quota_slot_voided",
            "slots": 1,
            "voided_run_generated_at": (NOW - timedelta(minutes=20)).isoformat(),
        },
    )
    rows = [
        _spend_row(spend, generated_at=NOW - timedelta(minutes=20)),
        {
            "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_voided",
            "json_path": str(void),
        },
    ]

    ledger = _ledger(tmp_path, rows)

    assert ledger["spent_slots"] == 0
    assert ledger["void_event_count"] == 1
    assert ledger.get("unreadable_event_count", 0) == 0


def test_observation_distinguishes_an_unreadable_event_from_a_zero_spend_event(
    tmp_path: Path,
) -> None:
    artifact = tmp_path / "runs" / "missing.json"
    broken_row = _spend_row(artifact, generated_at=NOW - timedelta(minutes=10))
    contribution, reason = quota_slot_observation(broken_row)
    assert contribution is None
    assert reason == QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING

    zero = {
        "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "quota_event": {"event_type": "quota_slot_spent", "slots": 0},
    }
    contribution, reason = quota_slot_observation(zero)
    assert contribution is None
    assert reason is None


def test_a_malformed_quota_event_is_reported_instead_of_counted_as_no_spend() -> None:
    """A readable object that names no known event type is still unreadable.

    The index row claims a spend, so reading the event as "this event spends
    nothing" would hand the Goal back the quota it already used.
    """

    malformed = {
        "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "quota_event": {"slots": 1},
    }

    contribution, reason = quota_slot_observation(malformed)
    assert contribution is None
    assert reason == QUOTA_EVENT_UNREADABLE_EVENT_INVALID

    ledger = goal_quota_with_spend_ledger(_goal(), [malformed], now=NOW)
    assert ledger["spent_slots"] == 0
    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_EVENT_INVALID]

    status = quota_status(
        {**_goal(), "quota": {**_goal()["quota"], **ledger}},
        waiting_on="codex",
    )
    assert status["state"] == "blocked_health"
    assert status["blocked_action_scope"] == "automatic_agent_turns"


def test_an_unknown_event_type_is_reported_instead_of_counted_as_no_spend() -> None:
    unknown = {
        "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "quota_event": {"event_type": "quota_slot_something_else", "slots": 1},
    }

    contribution, reason = quota_slot_observation(unknown)
    assert contribution is None
    assert reason == QUOTA_EVENT_UNREADABLE_EVENT_INVALID

    ledger = goal_quota_with_spend_ledger(_goal(), [unknown], now=NOW)
    assert ledger["unreadable_event_count"] == 1


def test_a_legal_zero_spend_event_is_not_reported_as_unreadable() -> None:
    zero = {
        "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "quota_event": {"event_type": "quota_slot_spent", "slots": 0},
    }

    contribution, reason = quota_slot_observation(zero)
    assert contribution is None
    assert reason is None

    ledger = goal_quota_with_spend_ledger(_goal(), [zero], now=NOW)
    assert ledger.get("unreadable_event_count", 0) == 0


def test_the_public_projection_keeps_the_unreadable_reason(tmp_path: Path) -> None:
    """The count says how many events broke; the reason says what to repair."""

    artifact = tmp_path / "runs" / "truncated.json"
    _write_artifact(artifact, None)
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]
    ledger = _ledger(tmp_path, rows)

    config = goal_quota_config({**_goal(), "quota": {**_goal()["quota"], **ledger}})
    assert config["unreadable_event_count"] == 1
    assert config["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_INVALID_JSON]

    status = quota_status(
        {**_goal(), "quota": {**_goal()["quota"], **ledger}},
        waiting_on="codex",
    )
    assert status["state"] == "blocked_health"
    assert status["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_INVALID_JSON]


def test_the_plan_keeps_the_unreadable_reason(tmp_path: Path) -> None:
    from loopx.quota import build_quota_plan

    artifact = tmp_path / "runs" / "missing.json"
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]
    ledger = _ledger(tmp_path, rows)
    payload = _plan_payload({**(_goal()["quota"]), **ledger})

    plan = build_quota_plan(payload, mode="plan")

    item = plan["groups"]["blocked_health"][0]
    assert item["quota"]["unreadable_event_count"] == 1
    assert item["quota"]["unreadable_event_reasons"] == [
        QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING
    ]


def test_an_unreadable_void_is_also_reported(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "missing-void.json"
    rows = [
        {
            "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_voided",
            "json_path": str(artifact),
        }
    ]

    ledger = _ledger(tmp_path, rows)

    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_MISSING]


def test_an_artifact_that_is_not_utf8_is_reported(tmp_path: Path) -> None:
    artifact = tmp_path / "runs" / "binary.json"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_bytes(b"\xff\xfe\x00binary")
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]

    ledger = _ledger(tmp_path, rows)

    assert ledger["unreadable_event_count"] == 1
    assert ledger["unreadable_event_reasons"] == [QUOTA_EVENT_UNREADABLE_ARTIFACT_UNREADABLE]


def _plan_payload(
    goal_quota: dict[str, Any],
    *,
    attention_quota: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A status payload whose run history carries the Goal's ledger quota.

    The attention queue normally carries the quota status derived from the
    same ledger payload, so the projection is built from the ledger rather
    than written by hand.
    """

    goal = {
        "id": GOAL_ID,
        "registry_member": True,
        "quota": goal_quota,
    }
    if attention_quota is None:
        attention_quota = quota_status(
            {"id": GOAL_ID, "quota": goal_quota},
            waiting_on="codex",
        )
    return {
        "ok": True,
        "registry": None,
        "runtime_root": "/tmp/loopx-quota-ledger-integrity",
        "goal_count": 1,
        "run_count": 1,
        "attention_queue": {
            "items": [
                {"goal_id": GOAL_ID, "waiting_on": "codex", "quota": attention_quota}
            ]
        },
        "run_history": {"goals": [goal]},
    }


def test_the_quota_plan_does_not_offer_a_goal_with_an_unreadable_event(
    tmp_path: Path,
) -> None:
    from loopx.quota import build_quota_plan

    artifact = tmp_path / "runs" / "missing.json"
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]
    ledger = _ledger(tmp_path, rows)
    payload = _plan_payload({**(_goal()["quota"]), **ledger})

    plan = build_quota_plan(payload, mode="plan")

    assert plan["groups"]["blocked_health"], plan["groups"]
    item = plan["groups"]["blocked_health"][0]
    assert item["goal_id"] == GOAL_ID
    assert item["quota"]["unreadable_event_count"] == 1
    assert item["quota"]["blocked_action_scope"] == "automatic_agent_turns"
    assert plan["summary"]["next_automatic_turn"] is None


def test_the_quota_plan_still_offers_a_goal_without_an_unreadable_event(
    tmp_path: Path,
) -> None:
    from loopx.quota import build_quota_plan

    artifact = tmp_path / "runs" / "spend.json"
    _write_artifact(artifact, _spend_event(NOW - timedelta(minutes=10)))
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]
    ledger = _ledger(tmp_path, rows)
    payload = _plan_payload({**(_goal()["quota"]), **ledger})

    plan = build_quota_plan(payload, mode="plan")

    assert plan["groups"]["eligible"], plan["groups"]
    assert plan["summary"]["next_automatic_turn"] == GOAL_ID


def test_a_stale_eligible_projection_cannot_hide_an_unreadable_ledger_event(
    tmp_path: Path,
) -> None:
    """A projection built before the artifact broke must not stay eligible."""

    from loopx.quota import build_quota_plan

    artifact = tmp_path / "runs" / "missing.json"
    rows = [_spend_row(artifact, generated_at=NOW - timedelta(minutes=10))]
    ledger = _ledger(tmp_path, rows)
    stale_quota = {
        "compute": 1.0,
        "window_hours": 24,
        "slot_minutes": 1,
        "allowed_slots": 60,
        "spent_slots": 0,
        "state": "eligible",
        "reason": "1 compute quota; eligible for the next automatic agent turn",
    }
    payload = _plan_payload(
        {**(_goal()["quota"]), **ledger},
        attention_quota=stale_quota,
    )

    plan = build_quota_plan(payload, mode="plan")

    assert plan["groups"]["blocked_health"], plan["groups"]
    item = plan["groups"]["blocked_health"][0]
    assert item["quota"]["unreadable_event_count"] == 1
    assert plan["summary"]["next_automatic_turn"] is None


def _slot_artifact(
    path: Path,
    *,
    generated_at: datetime | None = None,
    event: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Write a physical quota artifact and return its index row.

    The index row is identical across the cases below; only the artifact on
    disk changes, which is the shape a real repair takes.
    """

    generated_at = generated_at or (NOW - timedelta(minutes=10))
    if event is None:
        event = _spend_event(generated_at)
    _write_artifact(path, event)
    return _spend_row(path, generated_at=generated_at)


def test_a_spend_artifact_with_an_unreadable_slot_count_is_not_a_zero_spend(
    tmp_path: Path,
) -> None:
    """An unknown quantity is not an explicit zero.

    The index row still claims a spend; only the artifact lost its size. That
    must be reported as an unreadable event rather than folded into a zero
    spend, which would hand the Goal back the quota it already used.
    """

    readable = {
        "event_type": "quota_slot_spent",
        "run_generated_at": (NOW - timedelta(minutes=10)).isoformat(),
    }
    broken_counts: list[Any] = ["lost", "", True, ["1"], {"value": 1}]

    for broken in broken_counts:
        artifact = tmp_path / f"runs-{abs(hash(str(broken)))}.json"
        row = _slot_artifact(artifact, event={**readable, "slots": broken})

        observation, reason = quota_slot_observation(row)
        assert observation is None
        assert reason == QUOTA_EVENT_UNREADABLE_EVENT_INVALID, broken

        ledger = _ledger(tmp_path, [row])
        assert ledger["spent_slots"] == 0
        assert ledger["unreadable_event_count"] == 1
        assert ledger["unreadable_event_reasons"] == [
            QUOTA_EVENT_UNREADABLE_EVENT_INVALID
        ]

    # The field is gone entirely, not just unparseable.
    missing = tmp_path / "runs-missing-slots.json"
    row = _slot_artifact(missing, event=dict(readable))
    observation, reason = quota_slot_observation(row)
    assert observation is None
    assert reason == QUOTA_EVENT_UNREADABLE_EVENT_INVALID


def test_an_unreadable_slot_count_keeps_the_goal_out_of_the_plan(
    tmp_path: Path,
) -> None:
    """The should-run guard must not offer a Goal whose spend size is unknown."""

    from loopx.quota import build_quota_plan

    artifact = tmp_path / "runs" / "slots-lost.json"
    row = _slot_artifact(
        artifact,
        event={
            "event_type": "quota_slot_spent",
            "run_generated_at": (NOW - timedelta(minutes=10)).isoformat(),
            "slots": "lost",
        },
    )
    ledger = _ledger(tmp_path, [row])
    payload = _plan_payload({**_goal()["quota"], **ledger})

    plan = build_quota_plan(payload, mode="plan")

    assert plan["groups"]["blocked_health"], plan["groups"]
    item = plan["groups"]["blocked_health"][0]
    assert item["goal_id"] == GOAL_ID
    assert item["quota"]["unreadable_event_count"] == 1
    assert item["quota"]["unreadable_event_reasons"] == [
        QUOTA_EVENT_UNREADABLE_EVENT_INVALID
    ]
    assert plan["summary"]["next_automatic_turn"] is None


def test_a_void_artifact_with_an_unreadable_slot_count_is_not_a_zero_void(
    tmp_path: Path,
) -> None:
    """A void of an unknown size cannot be read as cancelling nothing."""

    for slots_marker in ("missing", "lost"):
        artifact = tmp_path / f"runs-void-{slots_marker}.json"
        event: dict[str, Any] = {
            "event_type": "quota_slot_voided",
            "voided_run_generated_at": (NOW - timedelta(minutes=20)).isoformat(),
        }
        if slots_marker != "missing":
            event["slots"] = slots_marker
        _write_artifact(artifact, event)
        row = {
            "generated_at": (NOW - timedelta(minutes=10)).isoformat(),
            "goal_id": GOAL_ID,
            "classification": "quota_slot_voided",
            "json_path": str(artifact),
        }

        observation, reason = quota_slot_observation(row)
        assert observation is None
        assert reason == QUOTA_EVENT_UNREADABLE_EVENT_INVALID, slots_marker


def test_an_explicit_zero_slot_count_stays_a_legal_zero(tmp_path: Path) -> None:
    """A written zero is readable, and stays a zero contribution."""

    artifact = tmp_path / "runs" / "zero.json"
    zero = {
        "event_type": "quota_slot_spent",
        "run_generated_at": (NOW - timedelta(minutes=10)).isoformat(),
        "slots": 0,
    }
    row = _slot_artifact(artifact, event=zero)

    observation, reason = quota_slot_observation(row)
    assert observation is None
    assert reason is None

    ledger = _ledger(tmp_path, [row])
    assert ledger["spent_slots"] == 0
    assert "unreadable_event_count" not in ledger
    status = quota_status(
        {**_goal(), "quota": {**_goal()["quota"], **ledger}},
        waiting_on="codex",
    )
    assert status["state"] == "eligible"


def test_a_numeric_string_slot_count_is_still_readable(tmp_path: Path) -> None:
    """The supported historical representation keeps working."""

    artifact = tmp_path / "runs" / "string-slots.json"
    event = {
        "event_type": "quota_slot_spent",
        "run_generated_at": (NOW - timedelta(minutes=10)).isoformat(),
        "slots": "3",
    }
    row = _slot_artifact(artifact, event=event)

    observation, reason = quota_slot_observation(row)
    assert reason is None
    assert observation == ("spent", event["run_generated_at"], 3)

    ledger = _ledger(tmp_path, [row])
    assert ledger["spent_slots"] == 3
    assert "unreadable_event_count" not in ledger


def test_restoring_the_slot_count_releases_the_goal_again(tmp_path: Path) -> None:
    """Repairing the artifact is the whole recovery: the index row never moves."""

    artifact = tmp_path / "runs" / "spend.json"
    row = _slot_artifact(
        artifact,
        event={
            "event_type": "quota_slot_spent",
            "run_generated_at": (NOW - timedelta(minutes=10)).isoformat(),
            "slots": "lost",
        },
    )

    broken = _ledger(tmp_path, [row])
    assert broken["unreadable_event_count"] == 1
    broken_status = quota_status(
        {**_goal(), "quota": {**_goal()["quota"], **broken}},
        waiting_on="codex",
    )
    assert broken_status["state"] == "blocked_health"

    _write_artifact(artifact, _spend_event(NOW - timedelta(minutes=10), slots=4))
    repaired = _ledger(tmp_path, [row])

    assert "unreadable_event_count" not in repaired
    assert repaired["spent_slots"] == 4
    repaired_status = quota_status(
        {**_goal(), "quota": {**_goal()["quota"], **repaired}},
        waiting_on="codex",
    )
    assert repaired_status["state"] == "eligible"
