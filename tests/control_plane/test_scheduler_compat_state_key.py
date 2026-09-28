from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess

import pytest

from examples.control_plane.quota_plan_fixtures import (
    SCOPED_AGENT_ID,
    write_cli_fixture,
)
from loopx.control_plane.quota.scheduler_ack import (
    record_quota_scheduler_ack_for_decision,
    record_quota_scheduler_failure_for_decision,
)
from loopx.control_plane.scheduler.state import (
    APP_AUTOMATION_STATEFUL_BACKOFF_STATE_KEY as APP_KEY,
    LEGACY_CODEX_APP_STATEFUL_BACKOFF_STATE_KEY as LEGACY_KEY,
    build_scheduler_state,
    load_scheduler_state,
    scheduler_state_path,
    write_scheduler_state,
)
from loopx.control_plane.testing.canary_harness import run_json_cli_result

REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "needs-operator"


@pytest.mark.parametrize("state_layout", ["canonical", "legacy", "coexisting"])
@pytest.mark.parametrize("operation", ["ack", "host_failure"])
@pytest.mark.parametrize("caller", ["native_cli", "python_adapter", "python_cli"])
def test_compat_followup_preserves_the_read_authority_through_public_entry_points(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_layout: str,
    operation: str,
    caller: str,
) -> None:
    registry, runtime, project = write_cli_fixture(
        tmp_path / "fixture", scoped_agents=True
    )
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex-home"))
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    turn_id = "scheduler-compat-turn"

    def guard() -> dict:
        code, result = run_json_cli_result(
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            SCOPED_AGENT_ID,
            "--codex-app",
            "--turn-instance-id",
            turn_id,
            registry_path=registry,
            runtime_root=runtime,
            cwd=project,
        )
        assert code == 0, result
        return result

    first = guard()["scheduler_hint"]["app_automation"]
    selected_key = LEGACY_KEY if state_layout == "legacy" else APP_KEY
    acknowledged = build_scheduler_state(
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        state_key=selected_key,
        reset_token=first["stateful_backoff"]["reset_token"],
        identity_signature=first["stateful_backoff"]["identity_signature"],
        progression_index=0,
        progression_minutes=[30, 60],
        last_applied_rrule="FREQ=MINUTELY;INTERVAL=30",
        updated_at=(datetime.now(timezone.utc) - timedelta(minutes=31)).isoformat(),
    )
    write_scheduler_state(
        runtime,
        acknowledged,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        state_key=selected_key,
    )
    old_path = scheduler_state_path(
        runtime,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        state_key=LEGACY_KEY,
    )
    if state_layout == "coexisting":
        obsolete = build_scheduler_state(
            goal_id=GOAL_ID,
            agent_id=SCOPED_AGENT_ID,
            state_key=LEGACY_KEY,
            reset_token="obsolete-reset",
            identity_signature="obsolete-identity",
            progression_index=0,
            progression_minutes=[3, 6, 10],
            last_applied_rrule="FREQ=MINUTELY;INTERVAL=3",
            updated_at=datetime.now(timezone.utc).isoformat(),
        )
        write_scheduler_state(
            runtime,
            obsolete,
            goal_id=GOAL_ID,
            agent_id=SCOPED_AGENT_ID,
            state_key=LEGACY_KEY,
        )
        old_bytes = old_path.read_bytes()

    decision = guard()
    app = decision["scheduler_hint"]["app_automation"]
    compat = decision["scheduler_hint"]["codex_app"]
    assert app["stateful_backoff"]["progression_index"] == 1
    assert app["stateful_backoff"]["state_key"] == selected_key
    hint = compat["ack_hint" if operation == "ack" else "failure_hint"]
    if caller == "native_cli":
        completed = subprocess.run(
            [
                str(REPO_ROOT / "scripts" / "loopx"),
                "--format",
                "json",
                *hint["cli_args"],
            ],
            cwd=project,
            check=False,
            capture_output=True,
            text=True,
            env={**os.environ, "LOOPX_PYTHON": str(tmp_path / "python-must-not-run")},
            timeout=30,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        result = json.loads(completed.stdout)
    elif caller == "python_cli":
        command = (
            "scheduler-ack-current" if operation == "ack" else "scheduler-fail-current"
        )
        operation_args = (
            ["--applied-rrule", "FREQ=MINUTELY;INTERVAL=60"]
            if operation == "ack"
            else [
                "--failed-rrule",
                "FREQ=MINUTELY;INTERVAL=60",
                "--app-automation-current-rrule",
                "FREQ=MINUTELY;INTERVAL=30",
            ]
        )
        code, result = run_json_cli_result(
            "quota",
            command,
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            SCOPED_AGENT_ID,
            "--codex-app",
            "--turn-instance-id",
            turn_id,
            "--execute",
            *operation_args,
            registry_path=registry,
            runtime_root=runtime,
            cwd=project,
        )
        assert code == 0, result
    elif operation == "ack":
        result = record_quota_scheduler_ack_for_decision(
            decision,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=SCOPED_AGENT_ID,
            execute=True,
            applied_rrule="FREQ=MINUTELY;INTERVAL=60",
            use_current_hint=True,
        )
    else:
        result = record_quota_scheduler_failure_for_decision(
            decision,
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=SCOPED_AGENT_ID,
            execute=True,
            failed_rrule="FREQ=MINUTELY;INTERVAL=60",
            observed_host_rrule="FREQ=MINUTELY;INTERVAL=30",
        )
    assert compat["stateful_backoff"]["state_key"] == selected_key
    assert result["ok"] is True, result
    assert result["state_key"] == selected_key
    assert result["scheduler_commit"]["status"] == "written"
    state = load_scheduler_state(
        runtime,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        state_key=selected_key,
    )
    assert state is not None
    assert state["progression_index"] == 1
    if operation == "ack":
        assert state["last_applied_rrule"] == "FREQ=MINUTELY;INTERVAL=60"
    else:
        assert state["host_update_failures"]
    if state_layout == "coexisting":
        assert old_path.read_bytes() == old_bytes
    if state_layout == "legacy":
        assert (
            load_scheduler_state(
                runtime,
                goal_id=GOAL_ID,
                agent_id=SCOPED_AGENT_ID,
                state_key=APP_KEY,
            )
            is None
        )


def test_explicit_state_key_is_not_silently_retargeted(tmp_path: Path) -> None:
    before = {
        "scheduler_hint": {
            "codex_app": {
                "stateful_backoff": {
                    "state_key": APP_KEY,
                    "reset_token": "reset",
                    "identity_signature": "identity",
                    "progression_index": 0,
                    "progression_minutes": [30, 60],
                    "current_rrule": "FREQ=MINUTELY;INTERVAL=30",
                }
            }
        }
    }
    result = record_quota_scheduler_ack_for_decision(
        before,
        runtime_root=tmp_path,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        execute=True,
        state_key=LEGACY_KEY,
        applied_rrule="FREQ=MINUTELY;INTERVAL=30",
    )
    assert result["ok"] is False
    assert "state-key does not match" in result["reason"]
    assert not scheduler_state_path(
        tmp_path,
        goal_id=GOAL_ID,
        agent_id=SCOPED_AGENT_ID,
        state_key=LEGACY_KEY,
    ).exists()
