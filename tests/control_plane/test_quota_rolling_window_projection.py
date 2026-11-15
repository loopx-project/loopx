from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from loopx.quota import goal_quota_with_spend_ledger


GOAL_ID = "quota-rolling-window-projection"


def _goal() -> dict[str, Any]:
    return {
        "id": GOAL_ID,
        "quota": {
            "compute": 1.0,
            "window_hours": 1,
            "slot_minutes": 1,
            "allowed_slots": 60,
            # A registry/config mirror is not accounting authority.
            "spent_slots": 999,
        },
    }


def _spend(generated_at: str, turn_instance_id: str) -> dict[str, Any]:
    return {
        "generated_at": generated_at,
        "goal_id": GOAL_ID,
        "classification": "quota_slot_spent",
        "quota_event": {
            "event_type": "quota_slot_spent",
            "slots": 1,
            "turn_instance_id": turn_instance_id,
            "run_generated_at": generated_at,
        },
    }


def test_distinct_turn_keeps_counter_flat_when_old_spend_expires() -> None:
    first = _spend("2026-01-01T00:00:00+00:00", "turn-1")
    second = _spend("2026-01-01T00:10:00+00:00", "turn-2")
    before_runs = [first, second]
    before = goal_quota_with_spend_ledger(
        _goal(),
        before_runs,
        now=datetime(2026, 1, 1, 0, 59, tzinfo=timezone.utc),
    )

    third = _spend("2026-01-01T01:01:00+00:00", "turn-3")
    append_only_runs = [*before_runs, third]
    after = goal_quota_with_spend_ledger(
        _goal(),
        append_only_runs,
        now=datetime(2026, 1, 1, 1, 1, tzinfo=timezone.utc),
    )

    assert len(append_only_runs) == 3
    assert (
        len({run["quota_event"]["turn_instance_id"] for run in append_only_runs}) == 3
    )
    assert before["spent_slots"] == 2
    assert after["spent_slots"] == 2
    assert after["spend_event_count"] == 2
    assert after["spend_source"] == "runtime_events"


def test_rolling_counter_can_decrease_without_replaying_or_voiding_a_spend() -> None:
    append_only_runs = [
        _spend("2026-01-01T00:00:00+00:00", "turn-1"),
        _spend("2026-01-01T00:10:00+00:00", "turn-2"),
    ]

    before = goal_quota_with_spend_ledger(
        _goal(),
        append_only_runs,
        now=datetime(2026, 1, 1, 0, 59, tzinfo=timezone.utc),
    )
    after = goal_quota_with_spend_ledger(
        _goal(),
        append_only_runs,
        now=datetime(2026, 1, 1, 1, 1, tzinfo=timezone.utc),
    )

    assert before["spent_slots"] == 2
    assert after["spent_slots"] == 1
    assert after["spend_event_count"] == 1
    assert append_only_runs[0]["classification"] == "quota_slot_spent"
    assert all(run["classification"] != "quota_slot_voided" for run in append_only_runs)


def test_rolling_counter_isolated_by_exact_goal_instance() -> None:
    goal_ref_a = {
        "goal_id": GOAL_ID,
        "goal_instance_id": "ginst_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    }
    goal_ref_b = {
        "goal_id": GOAL_ID,
        "goal_instance_id": "ginst_bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    }
    legacy = _spend("2026-01-01T00:10:00+00:00", "turn-legacy")
    spend_a = {
        **_spend("2026-01-01T00:20:00+00:00", "turn-a"),
        "goal_ref": goal_ref_a,
    }
    spend_b = {
        **_spend("2026-01-01T00:30:00+00:00", "turn-b"),
        "goal_ref": goal_ref_b,
    }

    current = goal_quota_with_spend_ledger(
        {**_goal(), "goal_instance_id": goal_ref_b["goal_instance_id"]},
        [legacy, spend_a, spend_b],
        now=datetime(2026, 1, 1, 0, 59, tzinfo=timezone.utc),
    )
    legacy_owner = goal_quota_with_spend_ledger(
        _goal(),
        [legacy, spend_a, spend_b],
        now=datetime(2026, 1, 1, 0, 59, tzinfo=timezone.utc),
    )

    assert current["spent_slots"] == 1
    assert current["spend_event_count"] == 1
    assert legacy_owner["spent_slots"] == 1
    assert legacy_owner["spend_event_count"] == 1


@pytest.mark.parametrize("window_hours", [10**8, 10**12, 1e12])
def test_unrepresentable_window_covers_all_history_without_crashing(
    window_hours: float,
) -> None:
    goal = _goal()
    goal["quota"]["window_hours"] = window_hours
    spent = _spend("2026-01-01T00:00:00+00:00", "turn-1")

    ledger = goal_quota_with_spend_ledger(
        goal,
        [spent],
        now=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )

    assert ledger["spent_slots"] == 1


@pytest.mark.parametrize("offset_hours", [-8, 0, 8])
def test_unrepresentable_window_keeps_earliest_utc_events_at_any_offset(
    offset_hours: int,
) -> None:
    goal = _goal()
    goal["quota"]["window_hours"] = 10**12
    earliest = _spend("0001-01-01T00:00:00+00:00", "turn-earliest")
    early = _spend("0001-01-01T07:00:00+00:00", "turn-early")
    later = _spend("2026-01-01T00:00:00+00:00", "turn-later")

    ledger = goal_quota_with_spend_ledger(
        goal,
        [earliest, early, later],
        now=datetime(
            2026, 6, 1, tzinfo=timezone(timedelta(hours=offset_hours))
        ),
    )

    assert ledger["spent_slots"] == 3
    assert ledger["spend_event_count"] == 3


@pytest.mark.parametrize("offset_hours", [-8, 0, 8])
def test_representable_long_window_has_the_same_cutoff_at_any_offset(
    offset_hours: int,
) -> None:
    goal = _goal()
    # Cutoff 0001-01-01T04:00:59Z for a 2026-11-13T16:00:59Z reference: it is
    # representable in UTC but underflows in a UTC-08:00 local calendar.
    goal["quota"]["window_hours"] = 17_758_380
    outside = _spend("0001-01-01T00:00:00+00:00", "turn-outside")
    inside = _spend("0001-01-01T07:00:00+00:00", "turn-inside")
    later = _spend("2026-01-01T00:00:00+00:00", "turn-later")
    now = datetime(2026, 11, 13, 16, 0, 59, tzinfo=timezone.utc).astimezone(
        timezone(timedelta(hours=offset_hours))
    )

    ledger = goal_quota_with_spend_ledger(goal, [outside, inside, later], now=now)

    assert ledger["spent_slots"] == 2
    assert ledger["spend_event_count"] == 2
