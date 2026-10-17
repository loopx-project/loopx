from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loopx.control_plane.quota.scheduler_ack import (
    record_quota_scheduler_ack_for_decision,
    record_quota_scheduler_failure_for_decision,
)
from loopx.control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_runtime_profile,
)
from tests.control_plane.scheduler_arbitration_fixture import build_native_scheduler_hint as build_scheduler_hint
from loopx.control_plane.scheduler.state import (
    CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
    CODEX_APP_SURFACE,
    SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION,
    SCHEDULER_STATE_SCHEMA_VERSION,
    normalize_scheduler_host_update_failures,
    normalize_scheduler_state,
    retained_scheduler_host_update_failures,
)

GOAL_ID = "scheduler-failure-pair-cache-replay"
AGENT_ID = "codex-main-control"
HOST_30 = "FREQ=MINUTELY;INTERVAL=30"
HOST_20 = "FREQ=MINUTELY;INTERVAL=20"
ACTIVE_3 = "FREQ=MINUTELY;INTERVAL=3"
MONITOR_15 = "FREQ=MINUTELY;INTERVAL=15"
APP_CONTEXT = scheduler_execution_context_for_runtime_profile(
    "codex_app_heartbeat"
)


def _decision(*, mode: str, must_attempt: bool, quiet_noop_allowed: bool) -> dict:
    return {
        "goal_id": GOAL_ID,
        "agent_identity": {"agent_id": AGENT_ID},
        "effective_action": mode,
        "recommended_action": "Exercise scheduler failure pair retention.",
        "heartbeat_recommendation": {"recommended_mode": mode},
        "interaction_contract": {
            "schema_version": "loopx_interaction_contract_v0",
            "mode": mode,
            "user_channel": {"action_required": False, "notify": "DONT_NOTIFY"},
            "agent_channel": {
                "must_attempt": must_attempt,
                "delivery_allowed": must_attempt,
                "quiet_noop_allowed": quiet_noop_allowed,
            },
            "cli_channel": {"next_cli_actions": [], "spend_allowed_now": False},
        },
    }


def _active_decision() -> dict:
    return _decision(
        mode="bounded_delivery", must_attempt=True, quiet_noop_allowed=False
    )


def _monitor_decision() -> dict:
    return _decision(
        mode="monitor_quiet_skip", must_attempt=False, quiet_noop_allowed=True
    )


def _with_scheduler_hint(
    decision: dict,
    *,
    scheduler_state: dict | None,
    host_rrule: str,
) -> dict:
    result = deepcopy(decision)
    result["scheduler_hint"] = build_scheduler_hint(
        result,
        codex_app_scheduler_state=scheduler_state,
        codex_app_current_rrule=host_rrule,
        scheduler_execution_context=APP_CONTEXT,
    )
    return result


def _record_failure(
    decision: dict,
    *,
    runtime_root: Path,
    generated_at: str,
    observed_host_rrule: str = HOST_30,
) -> dict:
    app = decision["scheduler_hint"]["codex_app"]
    target_rrule = app["recommended_rrule"]
    result = record_quota_scheduler_failure_for_decision(
        decision,
        runtime_root=runtime_root,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        failed_rrule=target_rrule,
        observed_host_rrule=observed_host_rrule,
        generated_at=generated_at,
    )
    assert result["ok"] is True
    return result["scheduler_failure_event"]["scheduler_state"]


def _failure(target: str, host: str, *, failed_at: datetime, count: int = 1) -> dict:
    return {
        "schema_version": SCHEDULER_HOST_UPDATE_FAILURE_SCHEMA_VERSION,
        "target_rrule": target,
        "observed_host_rrule": host,
        "failure_kind": "timeout",
        "failure_count": count,
        "failed_at": failed_at.isoformat(),
    }


def test_alternating_cadence_failures_remain_suppressed_until_host_changes(
    tmp_path: Path,
) -> None:
    generated_at = datetime.now(timezone.utc)
    active = _with_scheduler_hint(
        _active_decision(),
        scheduler_state=None,
        host_rrule=HOST_30,
    )
    assert active["scheduler_hint"]["codex_app"]["recommended_rrule"] == ACTIVE_3
    active_failure_state = _record_failure(
        active,
        runtime_root=tmp_path,
        generated_at=generated_at.isoformat(),
    )

    monitor = _with_scheduler_hint(
        _monitor_decision(),
        scheduler_state=active_failure_state,
        host_rrule=HOST_30,
    )
    assert monitor["scheduler_hint"]["codex_app"]["recommended_rrule"] == MONITOR_15
    monitor_failure_state = _record_failure(
        monitor,
        runtime_root=tmp_path,
        generated_at=(generated_at + timedelta(seconds=1)).isoformat(),
    )
    assert [
        failure["target_rrule"]
        for failure in monitor_failure_state["host_update_failures"]
    ] == [ACTIVE_3, MONITOR_15]

    active_replay = _with_scheduler_hint(
        _active_decision(),
        scheduler_state=monitor_failure_state,
        host_rrule=HOST_30,
    )
    active_backoff = active_replay["scheduler_hint"]["codex_app"]["stateful_backoff"]
    assert active_backoff["current_rrule"] == ACTIVE_3
    assert active_backoff["apply_needed"] is False
    assert active_backoff["state_status"] == "host_update_failure_suppressed"

    monitor_replay = _with_scheduler_hint(
        _monitor_decision(),
        scheduler_state=monitor_failure_state,
        host_rrule=HOST_30,
    )
    monitor_backoff = monitor_replay["scheduler_hint"]["codex_app"]["stateful_backoff"]
    assert monitor_backoff["current_rrule"] == MONITOR_15
    assert monitor_backoff["apply_needed"] is False
    assert monitor_backoff["state_status"] == "host_update_failure_suppressed"

    changed_host = _with_scheduler_hint(
        _active_decision(),
        scheduler_state=monitor_failure_state,
        host_rrule=HOST_20,
    )
    changed_app = changed_host["scheduler_hint"]["codex_app"]
    assert changed_app["stateful_backoff"]["apply_needed"] is True
    assert changed_app["recommended_rrule"] == ACTIVE_3
    assert "host_update_failures" not in changed_app["stateful_backoff"]


def test_matching_host_ack_clears_the_failure_cache(tmp_path: Path) -> None:
    generated_at = datetime.now(timezone.utc)
    active = _with_scheduler_hint(
        _active_decision(),
        scheduler_state=None,
        host_rrule=HOST_30,
    )
    failure_state = _record_failure(
        active,
        runtime_root=tmp_path,
        generated_at=generated_at.isoformat(),
    )
    host_matched = _with_scheduler_hint(
        _active_decision(),
        scheduler_state=failure_state,
        host_rrule=ACTIVE_3,
    )
    backoff = host_matched["scheduler_hint"]["codex_app"]["stateful_backoff"]
    assert backoff["apply_needed"] is False
    assert backoff["ack_needed"] is True

    ack = record_quota_scheduler_ack_for_decision(
        host_matched,
        runtime_root=tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        applied_rrule=ACTIVE_3,
        generated_at=(generated_at + timedelta(seconds=1)).isoformat(),
    )
    scheduler_state = ack["scheduler_ack_event"]["scheduler_state"]
    assert "host_update_failures" not in scheduler_state
    assert "host_update_failure" not in scheduler_state


def test_fallback_ack_retains_other_failed_target_for_same_host(
    tmp_path: Path,
) -> None:
    generated_at = datetime.now(timezone.utc)
    active = _with_scheduler_hint(
        _active_decision(),
        scheduler_state=None,
        host_rrule=MONITOR_15,
    )
    failure_state = _record_failure(
        active,
        runtime_root=tmp_path,
        generated_at=generated_at.isoformat(),
        observed_host_rrule=MONITOR_15,
    )

    fallback = _with_scheduler_hint(
        _monitor_decision(),
        scheduler_state=failure_state,
        host_rrule=MONITOR_15,
    )
    fallback_app = fallback["scheduler_hint"]["codex_app"]
    assert fallback_app["stateful_backoff"]["current_rrule"] == MONITOR_15
    assert fallback_app["stateful_backoff"]["apply_needed"] is False
    assert fallback_app["stateful_backoff"]["ack_needed"] is True

    fallback_ack = record_quota_scheduler_ack_for_decision(
        fallback,
        runtime_root=tmp_path,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        applied_rrule=MONITOR_15,
        generated_at=(generated_at + timedelta(seconds=1)).isoformat(),
    )
    fallback_state = fallback_ack["scheduler_ack_event"]["scheduler_state"]
    assert [
        failure["target_rrule"] for failure in fallback_state["host_update_failures"]
    ] == [ACTIVE_3]

    fallback_replay = _with_scheduler_hint(
        _monitor_decision(),
        scheduler_state=fallback_state,
        host_rrule=MONITOR_15,
    )
    fallback_replay_app = fallback_replay["scheduler_hint"]["codex_app"]
    assert fallback_replay_app["stateful_backoff"]["apply_needed"] is False
    assert fallback_replay_app["stateful_backoff"]["ack_needed"] is False
    assert fallback_replay_app["host_action"] == "none"

    active_replay = _with_scheduler_hint(
        _active_decision(),
        scheduler_state=fallback_state,
        host_rrule=MONITOR_15,
    )
    replay_app = active_replay["scheduler_hint"]["codex_app"]
    assert replay_app["stateful_backoff"]["apply_needed"] is False
    assert replay_app["stateful_backoff"]["ack_needed"] is False
    assert replay_app["stateful_backoff"]["state_status"] == (
        "host_update_failure_suppressed"
    )
    assert replay_app["host_action"] == "none_recorded_host_failure"
    assert "recommended_rrule" not in replay_app
    assert "failure_hint" not in replay_app


def test_failure_cache_is_bounded_expires_and_reads_legacy_scalar_state() -> None:
    reference_time = datetime.now(timezone.utc)
    failures = [
        _failure(
            f"FREQ=MINUTELY;INTERVAL={minute}",
            HOST_30,
            failed_at=reference_time - timedelta(minutes=minute),
        )
        for minute in range(1, 7)
    ]
    bounded = normalize_scheduler_host_update_failures(failures)
    assert len(bounded) == 4
    assert bounded[0]["target_rrule"] == "FREQ=MINUTELY;INTERVAL=3"

    expiring = [
        _failure(ACTIVE_3, HOST_30, failed_at=reference_time - timedelta(hours=25)),
        _failure(MONITOR_15, HOST_30, failed_at=reference_time - timedelta(hours=1)),
    ]
    retained = retained_scheduler_host_update_failures(
        expiring,
        reference_time=reference_time,
    )
    assert [failure["target_rrule"] for failure in retained] == [MONITOR_15]

    legacy_failure = _failure(
        ACTIVE_3,
        HOST_30,
        failed_at=reference_time,
        count=2,
    )
    normalized_state = normalize_scheduler_state(
        {
            "schema_version": SCHEDULER_STATE_SCHEMA_VERSION,
            "goal_id": GOAL_ID,
            "agent_id": AGENT_ID,
            "surface": CODEX_APP_SURFACE,
            "state_key": CODEX_APP_STATEFUL_BACKOFF_STATE_KEY,
            "reset_token": "legacy-reset",
            "identity_signature": "legacy-identity",
            "progression_index": 0,
            "progression_minutes": [3, 6, 10],
            "last_applied_rrule": HOST_30,
            "host_update_failure": legacy_failure,
        },
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert normalized_state is not None
    assert normalized_state["host_update_failures"] == [legacy_failure]
    assert normalized_state["host_update_failure"] == legacy_failure
