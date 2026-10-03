from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

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
