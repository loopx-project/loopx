"""Recovery admissions are bounded per original debt, never treated as spend."""
from pathlib import Path

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("reset", ["writeback", "owner_resume", "blocked_closeout"])
def test_unchanged_recovery_waits_then_accepts_progress_or_owner_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, reset: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID,
             "--agent-id", AGENT_ID)
    original = ("--turn-instance-id", "owed-original", "--todo-id", TODO_ID)
    rc, first = _run_cli(registry, runtime, *guard, *original)
    assert rc == 0 and first["heartbeat_receipt"]["closeout_required"], first
    effect = first["heartbeat_receipt"]["settlement_identity"]["effect_id"]
    for index in range(3):
        rc, recovery = _run_cli(registry, runtime, *guard,
                                "--turn-instance-id", f"recovery-{index}")
        assert rc == 0 and recovery["should_run"] is True, recovery
        debt = recovery["unsettled_host_turn_recovery"]
        assert debt["prior_turn_instance_id"] == "owed-original"
        assert debt["settlement_effect_id"] == effect
        assert debt["admissions_without_progress"] == index
    # Repeating a committed recovery identity is not a fresh admission.
    rc, replay = _run_cli(registry, runtime, *guard, "--turn-instance-id", "recovery-2")
    assert rc == 0 and replay["should_run"] is True, replay
    for turn in ("wait-1", "wait-2"):
        rc, wait = _run_cli(registry, runtime, *guard, "--turn-instance-id", turn)
        assert rc == 0 and wait["should_run"] is False, wait
        assert wait["reason_code"] == "unsettled_recovery_admission_limit"
        assert wait["unsettled_host_turn_recovery"]["state"] == "attention_required"
        assert wait["unsettled_host_turn_recovery"]["admissions_without_progress"] == 3
        assert wait["scheduler_hint"]["action"] != "run_now"
        channel = wait["interaction_contract"]["agent_channel"]
        assert channel["must_attempt"] is False and channel["delivery_allowed"] is False
        assert wait["interaction_contract"]["cli_channel"]["spend_allowed_now"] is False
    assert _spend_run_count(runtime) == 0

    if reset == "owner_resume":
        for operation in ("stop", "resume"):
            rc, result = _run_cli(registry, runtime, "goal-lifecycle", "--goal-id", GOAL_ID,
                                  "--operation", operation, "--actor-kind", "owner", "--execute")
            assert rc == 0, result
    elif reset == "blocked_closeout":
        rc, result = _run_cli(registry, runtime, "todo", "update", "--goal-id", GOAL_ID,
                              "--agent-id", AGENT_ID, "--todo-id", TODO_ID,
                              "--status", "blocked", "--reason", "Await independent evidence.")
        assert rc == 0, result
    else:
        # The original identity remains available even after attention. A real
        # writeback is progress; recovery grants themselves never fabricate it.
        rc, reopened = _run_cli(registry, runtime, *guard, *original)
        assert rc == 0 and reopened["should_run"] is True, reopened
        assert reopened["heartbeat_receipt"]["settlement_identity"]["effect_id"] == effect
        rc, result = _run_cli(registry, runtime, "refresh-state", "--goal-id", GOAL_ID,
                              "--agent-id", AGENT_ID, *original,
                              "--classification", "validated_work", "--delivery-batch-scale", "implementation",
                              "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
                              "--no-global-sync", "--suppress-external-sinks", cwd=project)
        assert rc == 0, result
    rc, after = _run_cli(registry, runtime, *guard, "--turn-instance-id", "after-progress")
    assert rc == 0, after
    if reset == "blocked_closeout":
        assert after.get("unsettled_host_turn_recovery") is None, after
    else:
        debt = after["unsettled_host_turn_recovery"]
        assert after["should_run"] is True and debt["admissions_without_progress"] == 0, after
        assert debt["prior_turn_instance_id"] == "owed-original"
        assert debt["settlement_effect_id"] == effect
        if reset == "writeback":
            assert debt["missing_receipts"] == ["quota_spend_receipt"]
    assert _spend_run_count(runtime) == 0
