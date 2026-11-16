"""Recovery admissions are bounded per original debt, never treated as spend."""
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _heartbeat_receipt_count, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_recovery_append_retry_keeps_the_guided_turn(tmp_path, monkeypatch, provider):
    from loopx.cli import build_parser
    from loopx.cli_commands import quota
    from loopx.cli_rollout import append_cli_rollout_event
    from loopx.control_plane.quota.turn_envelope import assert_complete_turn_decision
    from loopx.rollout_event_log import RolloutAppendPreconditionChanged

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    monkeypatch.chdir(project)
    args = build_parser().parse_args([
        "--format", "json", "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--begin-turn",
        "--runtime-profile", "codex_app_heartbeat", "--context-delivery", "off",
    ])
    attempts = []
    starts = []
    emitted = []
    dispatch = quota._dispatch_quota_turn_start_hooks

    def observe_start(*args, **kwargs):
        starts.append(True)
        return dispatch(*args, **kwargs)

    def append_with_changed_precondition(payload, **options):
        if options["event_kind"] == "quota_should_run":
            attempts.append(options["run_id"])
            if len(attempts) == 1:
                raise RolloutAppendPreconditionChanged("fixture changes authority before append")
        return append_cli_rollout_event(payload, **options)

    monkeypatch.setattr(quota, "_dispatch_quota_turn_start_hooks", observe_start)
    rc = quota.handle_quota_command(
        args, registry_path=registry, runtime_root_arg=str(runtime),
        print_payload=lambda payload, fmt, renderer: emitted.append(payload),
        append_cli_rollout_event=append_with_changed_precondition,
    )

    assert rc == 0, emitted
    [guard] = emitted
    assert_complete_turn_decision(guard)
    turn = guard["heartbeat_receipt"]["turn_instance_id"]
    assert turn.startswith("guided-start:")
    assert attempts == [turn, turn]
    assert len(starts) == _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 0


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
    for turn, detail in (("wait-1", ()), ("wait-2", ("--include-detail", "scheduler"))):
        rc, wait = _run_cli(registry, runtime, *guard, "--turn-instance-id", turn, *detail)
        assert rc == 0 and wait["should_run"] is False, wait
        assert wait["reason_code"] == "unsettled_recovery_admission_limit"
        assert wait["unsettled_host_turn_recovery"]["state"] == "attention_required"
        assert wait["unsettled_host_turn_recovery"]["admissions_without_progress"] == 3
        hint = wait["scheduler_hint"]
        assert hint["action"] == "backoff_waiting_for_user"
        if detail:
            scheduler = hint["cold_path_detail"]["local_scheduler"]
            assert scheduler["recommended_interval_minutes"] > 0
            assert hint["unchanged_poll"]["limits"]["local_scheduler"] == 3
        else:
            assert "cold_path_detail" not in hint
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
    if reset == "owner_resume":
        rc, upgraded = _run_cli(registry, runtime, *guard, "--turn-instance-id", "wait-2")
        assert rc == 0 and upgraded["should_run"] is True, upgraded
        assert upgraded["heartbeat_receipt"]["status"] == "upgraded"
        assert upgraded["heartbeat_receipt"]["event_id"] != wait["heartbeat_receipt"]["event_id"]
        assert upgraded["unsettled_host_turn_recovery"]["admissions_without_progress"] == 0
        rc, replay = _run_cli(registry, runtime, *guard, "--turn-instance-id", "wait-2")
        assert rc == 0 and replay["should_run"] is True, replay
        assert replay["heartbeat_receipt"]["event_id"] == upgraded["heartbeat_receipt"]["event_id"]
        rows = [event for event in load_rollout_events(rollout_event_log_path(runtime, GOAL_ID))
                if event["run_id"] == "wait-2"]
        assert [event["details"]["should_run"] for event in rows] == [False, True]
        assert rows[-1]["details"]["recovery_effect_id"] == effect

    rc, after = _run_cli(registry, runtime, *guard, "--turn-instance-id", "after-progress")
    assert rc == 0, after
    if reset == "blocked_closeout":
        assert after.get("unsettled_host_turn_recovery") is None, after
    elif reset == "controller_resume":
        assert after["should_run"] is False, after
        assert after["unsettled_host_turn_recovery"]["admissions_without_progress"] == 3
    else:
        debt = after["unsettled_host_turn_recovery"]
        assert after["should_run"] is True, after
        assert debt["admissions_without_progress"] == int(reset == "owner_resume"), after
        assert debt["prior_turn_instance_id"] == "owed-original"
        assert debt["settlement_effect_id"] == effect
        if reset == "writeback":
            assert debt["missing_receipts"] == ["quota_spend_receipt"]
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("remote_binding", [False, True])
def test_recovery_wait_keeps_observed_app_schedule_and_acknowledgement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, remote_binding: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    codex_home = tmp_path / "codex"
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    monkeypatch.setenv("CODEX_THREAD_ID", "recovery-thread")
    automation = codex_home / "automations" / "recovery-heartbeat" / "automation.toml"
    automation.parent.mkdir(parents=True)

    def set_host_rrule(rrule: str) -> None:
        automation.write_text(
            'version = 1\nid = "recovery-heartbeat"\nkind = "heartbeat"\n'
            'name = "Recovery schedule fixture"\nstatus = "ACTIVE"\n'
            f'prompt = "Advance `{GOAL_ID}` from active state. Agent: `{AGENT_ID}`."\n'
            f'rrule = "{rrule}"\ntarget_thread_id = "recovery-thread"\n',
            encoding="utf-8",
        )

    def guard(turn: str, *extra: str) -> dict:
        rc, result = _run_cli(
            registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--turn-instance-id", turn, *extra, cwd=project,
        )
        assert rc == 0, result
        return result

    matching_rrule = "FREQ=MINUTELY;INTERVAL=30"
    short_rrule = "FREQ=MINUTELY;INTERVAL=3"
    set_host_rrule(matching_rrule)
    original = guard("owed-original", "--todo-id", TODO_ID)
    assert original["heartbeat_receipt"]["closeout_required"]
    for index in range(3):
        assert guard(f"recovery-{index}")["should_run"]

    if remote_binding:
        # A local recovery receipt still owns closeout after the Goal opts into
        # the remote route. This fixture has no remote credentials or endpoint.
        document = json.loads(registry.read_text(encoding="utf-8"))
        goal, = (goal for goal in document["goals"] if goal["id"] == GOAL_ID)
        goal["remote_authority"] = {"enabled": True}
        registry.write_text(json.dumps(document), encoding="utf-8")

    wait = guard("matching-wait", "--include-detail", "scheduler")
    assert wait["should_run"] is False
    assert wait["reason_code"] == "unsettled_recovery_admission_limit"
    hint = wait["scheduler_hint"]
    assert hint["cold_path_detail"]["local_scheduler"]["recommended_interval_minutes"] == 30
    app = hint["app_automation"]
    backoff = app["stateful_backoff"]
    assert backoff["host_observation"]["current_rrule"] == matching_rrule
    assert backoff["host_observation"]["status"] == "matches_recommended"
    assert backoff["apply_needed"] is False and backoff["ack_needed"] is True
    assert app["host_action"] == "ack_observed_rrule_without_update"
    ack_hint = app["ack_hint"]
    assert ack_hint["route_binding"]["turn_instance_bound"] is True
    assert ack_hint["args"]["turn_instance_id"] == "matching-wait"
    host_config = automation.read_bytes()
    rc, ack = _run_cli(registry, runtime, *ack_hint["cli_args"], cwd=project)
    assert rc == 0 and ack["scheduler_state_mutated"] is True, ack
    assert automation.read_bytes() == host_config

    replay = guard("matching-wait")
    assert replay["heartbeat_receipt"]["event_id"] == wait["heartbeat_receipt"]["event_id"]
    app = replay["scheduler_hint"]["app_automation"]
    backoff = app["stateful_backoff"]
    assert backoff["apply_needed"] is False and backoff["ack_needed"] is False
    assert app["host_action"] == "none"
    assert "cold_path_detail" not in replay["scheduler_hint"]

    set_host_rrule(short_rrule)
    drifted = guard("drifted-wait")["scheduler_hint"]["app_automation"]
    assert drifted["stateful_backoff"]["host_observation"]["current_rrule"] == short_rrule
    assert drifted["stateful_backoff"]["host_observation"]["status"] == "drift_detected"
    assert drifted["host_action"] == "update_current_heartbeat_rrule"
    assert drifted["recommended_rrule"] == matching_rrule
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_only_one_process_can_take_the_last_recovery_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _, runtime, registry = _write_fixture(tmp_path, provider=provider)
    guard = ("quota", "should-run", "--codex-app", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)

    def read(turn: str, *scoping: str) -> dict:
        rc, payload = _run_cli(registry, runtime, *guard, "--turn-instance-id", turn, *scoping)
        assert rc == 0, payload
        return payload

    original = read("owed-original", "--todo-id", TODO_ID)
    assert original["heartbeat_receipt"]["closeout_required"] is True
    effect = original["heartbeat_receipt"]["settlement_identity"]["effect_id"]
    for index in range(2):
        granted = read(f"prior-recovery-{index}")
        assert granted["should_run"] is True
        assert granted["unsettled_host_turn_recovery"]["admissions_without_progress"] == index

    log = rollout_event_log_path(runtime, GOAL_ID)
    before_preview = log.read_bytes()
    rc, preview = _run_cli(registry, runtime, *guard, "--turn-instance-id", "preview-recovery", "--dry-run")
    assert rc == 1 and preview["reason"] == "turn-scoped `quota should-run` cannot use --dry-run", preview
    assert log.read_bytes() == before_preview

    start = Barrier(3)

    def claim_last(turn: str) -> tuple[str, dict]:
        start.wait(timeout=15)
        return turn, read(turn)

    with ThreadPoolExecutor(max_workers=3) as workers:
        results = dict(workers.map(claim_last, (f"racing-recovery-{i}" for i in range(3))))
    granted_turns = [turn for turn, payload in results.items() if payload["should_run"] is True]
    assert len(granted_turns) == 1, {
        turn: (payload.get("should_run"), payload.get("reason_code")) for turn, payload in results.items()
    }
    winner, = granted_turns
    for turn, payload in results.items():
        debt = payload["unsettled_host_turn_recovery"]
        assert debt["settlement_effect_id"] == effect
        if turn == winner:
            assert debt["admissions_without_progress"] == 2
        else:
            assert debt["admissions_without_progress"] == 3
            assert payload["reason_code"] == "unsettled_recovery_admission_limit"

    replay = read(winner)
    assert replay["heartbeat_receipt"]["event_id"] == results[winner]["heartbeat_receipt"]["event_id"]
    exhausted = read("after-race")
    assert exhausted["should_run"] is False
    assert exhausted["unsettled_host_turn_recovery"]["admissions_without_progress"] == 3
    grants = [
        event["run_id"] for event in load_rollout_events(log)
        if event.get("details", {}).get("recovery_effect_id") == effect
        and event["details"].get("should_run") is True
    ]
    assert sorted(grants) == sorted(["prior-recovery-0", "prior-recovery-1", winner])
    assert _spend_run_count(runtime) == 0
