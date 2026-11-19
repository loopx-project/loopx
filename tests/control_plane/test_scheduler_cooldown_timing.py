from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from loopx.control_plane.scheduler import scheduler_hint as scheduler_hint_module
from loopx.control_plane.scheduler.arbitration import (
    SchedulerArbitration,
    SchedulerDisposition,
)
from loopx.control_plane.scheduler.state import (
    SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION,
)


CURRENT_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)


COOLDOWN_TIMING_CASES = [
    {
        "id": "initial_failure_is_suppressed",
        "elapsed_minutes": 1,
        "notification_due": False,
    },
    {
        "id": "reminder_is_due_inside_the_next_host_window",
        "elapsed_minutes": 31,
        "notification_due": True,
    },
    {
        "id": "reminder_is_suppressed_after_the_host_window",
        "elapsed_minutes": 34,
        "notification_due": False,
    },
]


@pytest.mark.parametrize(
    "case",
    COOLDOWN_TIMING_CASES,
    ids=[case["id"] for case in COOLDOWN_TIMING_CASES],
)
def test_user_gate_notification_cooldown_timing(monkeypatch, case: dict) -> None:
    monkeypatch.setattr(scheduler_hint_module, "now_utc", lambda: CURRENT_TIME)
    failed_at = CURRENT_TIME - timedelta(minutes=case["elapsed_minutes"])

    hint = scheduler_hint_module.build_scheduler_hint(
        {"heartbeat_recommendation": {"recommended_mode": "human_gate"}},
        arbitration=SchedulerArbitration(
            SchedulerDisposition.HUMAN_GATE, "human_gate", "human_gate"
        ),
        scheduler_execution_context={
            "host_surface": "codex_app",
            "scheduler_owner": "host_automation",
            "execution_mode": "hosted_automation",
        },
        codex_app_current_rrule="FREQ=MINUTELY;INTERVAL=3",
        codex_app_scheduler_state={
            "host_update_failure": {
                "schema_version": SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION,
                "target_rrule": "FREQ=MINUTELY;INTERVAL=30",
                "observed_host_rrule": "FREQ=MINUTELY;INTERVAL=3",
                "failure_kind": "host_tool_failure",
                "failure_count": 1,
                "failed_at": failed_at.isoformat(),
            }
        },
    )
    cooldown = hint.get("user_gate_notification_cooldown")

    assert cooldown is not None
    assert cooldown["cooldown_minutes"] == 30
    assert cooldown["reminder_window_minutes"] == 3
    assert cooldown["notification_due"] is case["notification_due"]
    assert cooldown["notification_suppressed"] is not case["notification_due"]
