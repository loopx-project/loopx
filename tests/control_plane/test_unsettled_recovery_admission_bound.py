"""Recovery admissions are bounded per original debt, never treated as spend."""
from pathlib import Path

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("reset", ["writeback", "owner_resume", "blocked_closeout", "controller_resume"])
def test_unchanged_recovery_waits_then_accepts_progress_or_owner_resume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, reset: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    if reset == "controller_resume":
        for operation in ("stop", "resume"):
            rc, result = _run_cli(registry, runtime, "goal-lifecycle", "--goal-id", GOAL_ID,
                                  "--operation", operation, "--actor-kind", "owner", "--execute")
            assert rc == 0, result
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

    if reset in ("owner_resume", "controller_resume"):
        for operation in ("stop", "resume"):
            rc, result = _run_cli(registry, runtime, "goal-lifecycle", "--goal-id", GOAL_ID,
                                  "--operation", operation, "--actor-kind",
                                  "owner" if reset == "owner_resume" else "controller", "--execute")
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
    elif reset == "controller_resume":
        assert after["should_run"] is False, after
        assert after["unsettled_host_turn_recovery"]["admissions_without_progress"] == 3
    else:
        debt = after["unsettled_host_turn_recovery"]
        assert after["should_run"] is True and debt["admissions_without_progress"] == 0, after
        assert debt["prior_turn_instance_id"] == "owed-original"
        assert debt["settlement_effect_id"] == effect
        if reset == "writeback":
            assert debt["missing_receipts"] == ["quota_spend_receipt"]
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("intermediate_guard", [False, True])
def test_owner_resume_survives_controller_lifecycle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, intermediate_guard: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _, runtime, registry = _write_fixture(tmp_path, provider=provider)
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    lifecycle = ("goal-lifecycle", "--goal-id", GOAL_ID)

    rc, original = _run_cli(registry, runtime, *guard,
                            "--turn-instance-id", "owed-original", "--todo-id", TODO_ID)
    assert rc == 0 and original["heartbeat_receipt"]["closeout_required"], original
    effect_id = original["heartbeat_receipt"]["settlement_identity"]["effect_id"]
    for attempt in range(3):
        rc, admitted = _run_cli(registry, runtime, *guard,
                                "--turn-instance-id", f"recovery-{attempt}")
        assert rc == 0 and admitted["should_run"] is True, admitted
    rc, limited = _run_cli(registry, runtime, *guard, "--turn-instance-id", "limited")
    assert rc == 0 and limited["should_run"] is False, limited

    registry_before_preview = registry.read_bytes()
    rc, preview = _run_cli(registry, runtime, *lifecycle, "--operation", "stop",
                          "--actor-kind", "owner")
    assert rc == 0 and preview["dry_run"] is True and preview["written"] is False, preview
    assert registry.read_bytes() == registry_before_preview

    for actor in ("owner", "controller"):
        for operation in ("stop", "resume"):
            rc, transition = _run_cli(registry, runtime, *lifecycle, "--operation", operation,
                                      "--actor-kind", actor, "--execute")
            assert rc == 0 and transition["written"] is True, transition
        if actor == "owner" and intermediate_guard:
            rc, interim = _run_cli(registry, runtime, *guard, "--turn-instance-id", "interim")
            assert rc == 0 and interim["should_run"] is True, interim
            assert interim["unsettled_host_turn_recovery"]["admissions_without_progress"] == 0

    rc, after = _run_cli(registry, runtime, *guard, "--turn-instance-id", "after-controller")
    assert rc == 0 and after["should_run"] is True, {
        "should_run": after.get("should_run"),
        "debt_state": (after.get("unsettled_host_turn_recovery") or {}).get("state"),
        "admissions_without_progress": (after.get("unsettled_host_turn_recovery") or {}).get(
            "admissions_without_progress"),
    }
    debt = after["unsettled_host_turn_recovery"]
    assert debt["admissions_without_progress"] == int(intermediate_guard)
    assert debt["prior_turn_instance_id"] == "owed-original"
    assert debt["settlement_effect_id"] == effect_id

    rc, replay = _run_cli(registry, runtime, *guard, "--turn-instance-id", "after-controller")
    assert rc == 0 and replay["should_run"] is True, replay
    assert replay["heartbeat_receipt"]["event_id"] == after["heartbeat_receipt"]["event_id"]

    rc, noop = _run_cli(registry, runtime, *lifecycle, "--operation", "resume",
                        "--actor-kind", "owner", "--execute")
    assert rc == 0 and noop["written"] is False, noop
    rc, next_guard = _run_cli(registry, runtime, *guard, "--turn-instance-id", "after-noop")
    assert rc == 0 and next_guard["should_run"] is True, next_guard
    assert next_guard["unsettled_host_turn_recovery"]["admissions_without_progress"] == 1 + int(intermediate_guard)
    assert _spend_run_count(runtime) == 0
