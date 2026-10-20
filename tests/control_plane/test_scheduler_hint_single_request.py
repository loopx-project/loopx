from copy import deepcopy

import pytest

from loopx.control_plane import effect_runtime
from loopx.control_plane.scheduler import scheduler_hint
from loopx.control_plane.scheduler.arbitration import (
    SchedulerArbitration,
    SchedulerDisposition,
)
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)


@pytest.mark.parametrize(
    "profile,disposition,pause,expected",
    [
        ("codex_app_heartbeat", "active_work", None, "run_now"),
        ("trae_app", "active_work", None, "run_now"),
        ("generic_cli", "active_work", None, "run_now"),
        ("codex_app_heartbeat", "human_gate", None, "backoff_waiting_for_user"),
        ("codex_app_heartbeat", None, "quota_paused", "stop_until_explicit_resume"),
        ("invalid-profile", None, None, "repair_scheduler_execution_context"),
    ],
)
def test_complete_hint_crosses_runtime_once(
    monkeypatch, profile, disposition, pause, expected
):
    payload = {
        "goal_id": "hint-fixture",
        "agent_identity": {"agent_id": "agent-fixture"},
        "heartbeat_recommendation": {"recommended_mode": pause or "normal_run"},
        "recommended_action": "Perform the bounded work.",
        "effective_action": "normal_run",
        "interaction_contract": {"mode": "normal_run"},
    }
    original = deepcopy(payload)
    requests = []
    invoke = effect_runtime.effect_runtime_request

    def observed(method, params, **kwargs):
        requests.append(method)
        return invoke(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_request", observed)
    arbitration = (
        SchedulerArbitration(
            SchedulerDisposition(disposition), disposition, "normal_run"
        )
        if disposition is not None
        else None
    )
    hint = scheduler_hint.build_scheduler_hint(
        payload,
        arbitration=arbitration,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            profile
        ),
    )

    assert hint["action"] == expected
    assert requests == ["scheduler.hint.project"]
    assert payload == original
    if profile in {"codex_app_heartbeat", "trae_app"} and disposition == "active_work":
        ack = hint["app_automation"]["ack_hint"]
        assert all(isinstance(argument, str) for argument in ack["cli_args"])
        assert "--scheduler-host-facts-chunk" in ack["cli_args"]
        assert (
            ack["args"]["state_key"] == "scheduler_hint.app_automation.stateful_backoff"
        )


def test_equal_monitor_windows_use_unicode_codepoint_identity_order(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setattr(
        scheduler_hint, "now_utc", lambda: datetime(2026, 10, 20, tzinfo=UTC)
    )
    hint = scheduler_hint.build_scheduler_hint(
        {
            "agent_todo_summary": {
                "monitor_open_items": [
                    {"todo_id": "😀", "next_due_at": "2026-10-20T00:20:00Z"},
                    {"todo_id": "\ue000", "next_due_at": "2026-10-20T00:20:00Z"},
                ]
            }
        },
        arbitration=SchedulerArbitration(
            SchedulerDisposition.MONITOR_WAIT, "monitor_wait", "monitor_quiet_skip"
        ),
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "codex_app_heartbeat"
        ),
        include_detail=True,
    )
    assert (
        hint["cold_path_detail"]["cadence_context"]["selected_monitor_identity"]
        == "\ue000"
    )


def test_large_monitor_source_reaches_last_deadline_in_one_request(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setattr(
        scheduler_hint, "now_utc", lambda: datetime(2026, 10, 20, tzinfo=UTC)
    )
    monitors = [
        {
            "todo_id": f"monitor-{index}",
            "title": "Observe the authorized source. " * 8,
            "next_due_at": "2026-10-20T00:20:00Z",
        }
        for index in range(1000)
    ]
    monitors.append({"todo_id": "monitor-last", "next_due_at": "2026-10-20T00:05:00Z"})
    requests = []
    invoke = effect_runtime.effect_runtime_request

    def observed(method, params, **kwargs):
        requests.append((method, kwargs.get("large_local_snapshot")))
        return invoke(method, params, **kwargs)

    monkeypatch.setattr(effect_runtime, "effect_runtime_request", observed)
    hint = scheduler_hint.build_scheduler_hint(
        {"agent_todo_summary": {"monitor_open_items": monitors}},
        arbitration=SchedulerArbitration(
            SchedulerDisposition.MONITOR_WAIT, "monitor_wait", "monitor_quiet_skip"
        ),
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "codex_app_heartbeat"
        ),
        include_detail=True,
    )
    assert (
        hint["cold_path_detail"]["cadence_context"]["selected_monitor_identity"]
        == "monitor-last"
    )
    assert hint["app_automation"]["recommended_interval_minutes"] == 5
    assert requests == [("scheduler.hint.project", True)]


def test_retry_metadata_is_not_interpreted_as_followup_arguments(monkeypatch):
    from datetime import UTC, datetime

    monkeypatch.setattr(
        scheduler_hint, "now_utc", lambda: datetime(2026, 10, 20, tzinfo=UTC)
    )
    retry = {"due_at": "2026-10-20T00:05:00Z", "cli_args": ["source metadata", 7]}
    hint = scheduler_hint.build_scheduler_hint(
        {"agent_todo_summary": {"blocked_retry_wait": retry}},
        arbitration=SchedulerArbitration(
            SchedulerDisposition.QUIET_WAIT, "blocked_wait", "quiet_wait"
        ),
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "codex_app_heartbeat"
        ),
    )
    assert hint["pending_retry"]["cli_args"] == retry["cli_args"]


@pytest.mark.parametrize(
    "surface,view",
    [("\u0085trae_app", "app_automation"), ("\ufefftrae_app", "codex_app")],
)
def test_invalid_host_view_preserves_python_whitespace_rules(surface, view):
    hint = scheduler_hint.build_scheduler_hint(
        {},
        scheduler_execution_context={
            "host_surface": surface,
            "scheduler_owner": "host_automation",
            "execution_mode": "hosted_automation",
        },
    )
    assert hint["action"] == "repair_scheduler_execution_context"
    assert hint[view]["applicability"] == "blocked_invalid_context"
    assert ("codex_app" if view == "app_automation" else "app_automation") not in hint
