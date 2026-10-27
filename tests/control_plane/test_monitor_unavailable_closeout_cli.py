"""Unavailable observation attempts settle their Turn, never a Monitor poll."""
from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, read_fixture_todos
from loopx.cli import main as cli_main
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _classification_count, _spend_run_count, _write_fixture,
)

MONITOR_ID = "todo_unavailable_monitor"
OBSERVATION_FIELDS = (
    "last_checked_at", "next_due_at", "result_hash", "material_change",
    "material_change_generation", "cadence", "expires_at",
)


@pytest.mark.parametrize("provider", ["markdown", "file", "sqlite"])
@pytest.mark.parametrize("scenario", [
    "unavailable", "capability_restore", "observed", "peer_git", "late_nonmaterial", "late_material",
])
def test_unavailable_monitor_closes_original_turn_without_observation_or_debit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
    provider: str, scenario: str,
) -> None:
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(
        tmp_path, required_capability="network" if scenario == "capability_restore" else None,
        claimed=False,  # The replacement below authors the Monitor's claim.
    )
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        f"todo_id={TODO_ID} status=open task_class=advancement_task action_kind=validate",
        f"todo_id={MONITOR_ID} status=open task_class=continuous_monitor action_kind=observe "
        f"claimed_by={AGENT_ID} target_key=synthetic-source watch_only=true cadence=30m "
        "last_checked_at=1999-01-01T00%3A00%3A00Z next_due_at=2000-01-01T00%3A00%3A00Z "
        f"result_hash={'a' * 64} material_change=false material_change_generation=7",
    ))
    if scenario == "peer_git":
        registered = json.loads(registry.read_text())
        registered["goals"][0]["coordination"]["registered_agents"].append("other-peer")
        registry.write_text(json.dumps(registered))
        subprocess.run(["git", "init", str(project)], capture_output=True, check=True)

    def cli(*args: str) -> tuple[int, dict]:
        with monkeypatch.context() as context:
            context.chdir(project)
            rc = cli_main(["--registry", str(registry), "--runtime-root", str(runtime),
                           "--format", "json", *args])
            return rc, json.loads(capsys.readouterr().out)

    if provider == "markdown":
        before = state.read_bytes()
        rc, refused = cli("todo", "list", "--goal-id", GOAL_ID)
        assert rc == 1 and refused["error_code"] == "canonical_authority_required", refused
        assert refused["write_admitted"] is False
        assert refused["legacy_fallback_used"] is False
        assert state.read_bytes() == before
        assert _classification_count(runtime, "monitor_observation_unavailable") == 0
        assert _spend_run_count(runtime) == 0
        return
    source_todos = read_fixture_todos(state)
    if provider != "markdown":
        initialize_canonical_authority(runtime, GOAL_ID, build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=source_todos, handoff_mode="hard_lease", leases=[],
        ), state_path=state, provider=provider)
    original_turn = f"unavailable-original-{provider}"
    binding = ("--agent-id", AGENT_ID, "--todo-id", MONITOR_ID,
               "--turn-instance-id", original_turn)
    capabilities = ("--available-capability", "network") if scenario == "capability_restore" else ()
    if scenario == "capability_restore":
        rc, unavailable = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
        assert rc != 0 and unavailable["error_code"] == "quota_action_selection_rejected", unavailable
        assert _classification_count(runtime, "monitor_observation_unavailable") == 0
        assert _spend_run_count(runtime) == 0
    rc, guard = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding, *capabilities)
    assert rc == 0 and guard["should_run"] is True, guard
    assert guard["work_lane_contract"]["obligation"] == "attempt_due_monitor"
    assert guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] == MONITOR_ID
    plan = guard["interaction_contract"]["cli_channel"]["settlement_plan"]
    writeback_step = next(step for step in plan["ordered_steps"] if step["kind"] == "durable_writeback")
    assert "observation is unavailable" in writeback_step["precondition"]
    lease_args: tuple[str, ...] = ()
    if provider != "markdown":
        rc, lease = cli("task-lease", "acquire", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID,
                        "--owner", AGENT_ID, "--idempotency-key", f"unavailable-{provider}")
        assert rc == 0 and lease["acquired"] is True, lease
        lease_args = ("--task-lease-idempotency-key", f"unavailable-{provider}",
                      "--task-lease-expected-version", str(lease["lease"]["version"]))
    rc, before = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID)
    assert rc == 0, before
    fields = {k: before["todo"].get(k) for k in OBSERVATION_FIELDS}

    # The next real Turn must recover the original identity; it cannot use
    # metadata as a result hash or silently move the unfinished receipt to P1.
    recovery_turn = f"unavailable-recovery-{provider}"
    if scenario != "observed":
        rc, recovery = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app",
                           "--agent-id", AGENT_ID, "--turn-instance-id", recovery_turn, *capabilities)
        assert rc == 0 and recovery["effective_action"] == "unsettled_host_turn_recovery", recovery
        actions = recovery["interaction_contract"]["cli_channel"]["next_cli_actions"]
        command = next(action for action in actions if " refresh-state " in action)
        # Consume the actual recovery packet rather than inventing an action.
        projected = shlex.split(command)
        projected = projected[projected.index("refresh-state"):]
        assert projected[projected.index("--todo-id") + 1] == MONITOR_ID
        assert projected[projected.index("--turn-instance-id") + 1] == original_turn

    refresh = ("refresh-state", "--goal-id", GOAL_ID, *binding,
               "--classification", "monitor_observation_unavailable",
               "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_gap",
               "--progress-result-class", "blocked", "--progress-blocker-id", "blocker:source-control",
               "--progress-evidence-id", "evidence:control-ownership-readback",
               "--vision-unchanged-reason", "Source unavailable; monitor acceptance and clocks retained.",
               "--no-global-sync", "--suppress-external-sinks")
    if scenario == "observed":
        rc, poll = cli("quota", "monitor-poll", "--goal-id", GOAL_ID, "--codex-app", *binding,
                       "--target-key", "synthetic-source", "--result-hash", "synthetic-observed",
                       *lease_args,
                       "--execute")
        if provider == "markdown":
            assert rc == 1 and poll["error_code"] == "canonical_authority_required", poll
            assert poll["write_admitted"] is False
            assert poll["legacy_fallback_used"] is False
            assert _classification_count(runtime, "quota_monitor_poll") == 0
            assert _spend_run_count(runtime) == 0
            return
        assert rc == 0 and poll["appended"] is True, poll
        rc, denied = cli(*refresh)
        assert rc != 0 and denied["appended"] is False, denied
        assert _classification_count(runtime, "monitor_observation_unavailable") == 0
        assert _classification_count(runtime, "quota_monitor_poll") == 1
        assert _spend_run_count(runtime) == 0
        rc, cold = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
        assert rc == 0 and cold["interaction_contract"]["mode"] == "heartbeat_settled_skip", cold
        return
    replacements = {
        "<verified-blocker-id>": "blocker:source-control",
        "<verified-evidence-ref>": "evidence:control-ownership-readback",
        "<verified-unchanged-vision-reason>": "Source unavailable; monitor acceptance and clocks retained.",
    }
    refresh = (*[replacements.get(arg, arg) for arg in projected],
               "--no-global-sync", "--suppress-external-sinks")
    without_evidence = list(refresh)
    evidence_index = without_evidence.index("--progress-evidence-id")
    del without_evidence[evidence_index:evidence_index + 2]
    rc, denied = cli(*without_evidence)
    assert rc != 0 and denied["appended"] is False, denied
    assert _classification_count(runtime, "monitor_observation_unavailable") == 0
    rc, closed = cli(*refresh)
    assert rc == 0, closed
    wait = closed["blocked_retry"]
    assert wait["schema_version"] == "quota_monitor_unavailable_v0"
    assert wait["observation_available"] is False and "due_at" not in wait
    assert {k: wait["monitor_todo"].get(k) for k in OBSERVATION_FIELDS} == fields
    assert closed["settlement_progress"]["state"] == "settled"
    assert closed["settlement_progress"]["closeout_kind"] == "typed_blocked_writeback_no_spend"
    assert {r["effect_id"] for r in closed["settlement_result"]["receipts"]} == {
        guard["heartbeat_receipt"]["settlement_identity"]["effect_id"]
    }
    rc, replay = cli(*refresh)
    assert rc == 0 and replay["idempotent_replay"] is True, replay
    assert replay["blocked_retry"] == wait
    assert _classification_count(runtime, "monitor_observation_unavailable") == 1
    assert _classification_count(runtime, "quota_monitor_poll") == 0
    assert _spend_run_count(runtime) == 0
    rc, cold = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
    assert rc == 0 and cold["should_run"] is False, cold
    assert cold["interaction_contract"]["mode"] == "heartbeat_settled_skip"
    rc, after = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID)
    assert rc == 0 and after["todo"]["status"] == "open", after
    assert {k: after["todo"].get(k) for k in OBSERVATION_FIELDS} == fields
    assert not after["todo"].get("resume_when")
    if scenario.startswith("late_"):
        poll_args = ("quota", "monitor-poll", "--goal-id", GOAL_ID, "--codex-app", *binding,
                     "--target-key", "synthetic-source", "--result-hash", "late-observation",
                     *lease_args)
        if scenario == "late_material":
            poll_args += ("--material-change", "--next-agent-todo", "Inspect the source transition.",
                          "--next-action-kind", "validate")
        for execute in (True, False):
            # Execution stops at the retired-writer boundary. A preview still
            # evaluates the original settled-Turn admission without writing.
            rejection_code = (
                "canonical_authority_required" if provider == "markdown" and execute
                else "monitor_poll_admission_rejected"
            )
            rc, late = cli(*poll_args, *(("--execute",) if execute else ()))
            read_rc, current = cli("todo", "list", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID)
            replay_rc, frozen = cli(*refresh)
            assert rc != 0 and late["error_code"] == rejection_code, json.dumps({
                "poll_rc": rc, "appended": late.get("appended"), "error_code": late.get("error_code"),
                "observation_changed": read_rc == 0 and
                {k: current["todo"].get(k) for k in OBSERVATION_FIELDS} != fields,
                "refresh_rc": replay_rc, "refresh_error_code": frozen.get("error_code"),
            })
            assert read_rc == 0 and {k: current["todo"].get(k) for k in OBSERVATION_FIELDS} == fields
            if provider == "markdown" and execute:
                assert late["write_admitted"] is False
                assert late["legacy_fallback_used"] is False
            assert replay_rc == 0 and frozen["idempotent_replay"] is True, frozen
            assert frozen["blocked_retry"] == wait
        if scenario == "late_nonmaterial":
            from loopx.control_plane.quota import monitor_poll as monitor_runtime
            native = monitor_runtime._native_result

            # The native compatibility request permits an omitted actor;
            # provider actor metadata cannot rebind the Turn's accounting Agent.
            for actor in (None, "other-synthetic-actor"):
                def actor_metadata_request(request):
                    request = {**request, "observation": {**request["observation"], "actor_agent_id": actor}}
                    return native(request)

                with monkeypatch.context() as context:
                    context.setattr(monitor_runtime, "_native_result", actor_metadata_request)
                    rc, actor_reply = cli(*poll_args, "--execute")
                expected_error = (
                    "canonical_authority_required" if provider == "markdown"
                    else "monitor_poll_admission_rejected"
                )
                assert rc != 0 and actor_reply["error_code"] == expected_error, actor_reply
        rc, after_late = cli("todo", "list", "--goal-id", GOAL_ID)
        assert rc == 0, after_late
        monitor = next(todo for todo in after_late["todos"] if todo["todo_id"] == MONITOR_ID)
        assert {k: monitor.get(k) for k in OBSERVATION_FIELDS} == fields
        assert {todo["todo_id"] for todo in after_late["todos"]} == {
            todo["todo_id"] for todo in source_todos
        }
        assert _classification_count(runtime, "monitor_observation_unavailable") == 1
        assert _classification_count(runtime, "quota_monitor_poll") == 0
        assert _spend_run_count(runtime) == 0
        # Re-enter the current recovery Turn after closing the original attempt;
        # a third identity would leave the already-admitted recovery unresolved.
        # Its advancement lane observes this due Monitor through the existing
        # auxiliary route, rather than selecting a Monitor as advancement work.
        state_before_add = state.read_bytes()
        rc, independent = cli("todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
                              "--text", "Inspect an independent artifact.",
                              "--task-class", "advancement_task", "--claimed-by", AGENT_ID,
                              *(("--operation-id", f"fresh-independent-{provider}")
                                if provider != "markdown" else ()))
        if provider == "markdown":
            assert rc == 1 and independent["error_code"] == "canonical_authority_required", independent
            assert independent["write_admitted"] is False
            assert independent["legacy_fallback_used"] is False
            assert state.read_bytes() == state_before_add
            assert _classification_count(runtime, "quota_monitor_poll") == 0
            assert _spend_run_count(runtime) == 0
            return
        assert rc == 0, independent
        rc, fresh_guard = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app",
                              "--agent-id", AGENT_ID, "--turn-instance-id", recovery_turn)
        assert rc == 0 and fresh_guard["should_run"] is True, json.dumps({
            "rc": rc, "error_code": fresh_guard.get("error_code"),
            "error": fresh_guard.get("error"), "effective_action": fresh_guard.get("effective_action"),
            "message": fresh_guard.get("message"), "reason": fresh_guard.get("reason"),
            "selection": fresh_guard.get("action_selection"),
        })
        assert fresh_guard["heartbeat_receipt"]["settlement_identity"]["todo_id"] != MONITOR_ID
        assert fresh_guard["work_lane_contract"]["lane"] == "advancement_task"
        fresh_poll = list(poll_args)
        fresh_poll[fresh_poll.index("--turn-instance-id") + 1] = recovery_turn
        rc, observed = cli(*fresh_poll, "--execute")
        assert rc == 0 and observed["appended"] is True, observed
        rc, poll_replay = cli(*fresh_poll, "--execute")
        assert rc == 0 and poll_replay["replayed"] is True, poll_replay
        rc, original_replay = cli(*refresh)
        assert rc == 0 and original_replay["idempotent_replay"] is True, original_replay
        assert original_replay["blocked_retry"] == wait
        assert _classification_count(runtime, "monitor_observation_unavailable") == 1
        assert _classification_count(runtime, "quota_monitor_poll") == 1
        assert _spend_run_count(runtime) == 0
        return
    if scenario == "peer_git":
        assert closed.get("delivery_workspace") is None
        return

    # A newly executable independent advancement belongs to the same recovery
    # host Turn after the original effect is closed, without a third identity.
    successor_args = (
        "todo", "add", "--goal-id", GOAL_ID, "--role", "agent",
        "--text", "Inspect an independent public source.",
        "--task-class", "advancement_task", "--claimed-by", AGENT_ID,
    )
    if provider == "markdown":
        state_before_add = state.read_bytes()
        rc, successor = cli(*successor_args)
        assert rc == 1 and successor["error_code"] == "canonical_authority_required", successor
        assert successor["write_admitted"] is False
        assert successor["legacy_fallback_used"] is False
        assert state.read_bytes() == state_before_add
        return

    rc, restored = cli("task-lease", "release", "--goal-id", GOAL_ID, "--todo-id", MONITOR_ID,
                       "--owner", AGENT_ID, "--idempotency-key", f"unavailable-{provider}",
                       "--expected-version", str(lease["lease"]["version"]))
    assert rc == 0, restored
    rc, successor = cli(*successor_args, "--operation-id", f"independent-{provider}")
    assert rc == 0, successor
    rc, next_turn = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app",
                        "--agent-id", AGENT_ID, "--turn-instance-id", recovery_turn, *capabilities)
    assert rc == 0 and next_turn["effective_action"] != "unsettled_host_turn_recovery", next_turn
    assert next_turn["work_lane_contract"]["lane"] == "advancement_task"
    # The shared blocked_retry receipt slot must not turn an unavailable
    # Monitor observation into the advancement retry scheduling overlay.
    assert next_turn["scheduler_hint"].get("pending_retry") is None
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("invalid", ["evidence", "binding"])
def test_unavailable_closeout_does_not_accept_missing_evidence_or_wrong_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], invalid: str,
) -> None:
    project, runtime, registry = _write_fixture(tmp_path)
    # This guard binds an advancement. A caller cannot close another Monitor
    # or omit the typed evidence required by the existing settlement owner.
    def cli(*args: str) -> tuple[int, dict]:
        with monkeypatch.context() as context:
            context.chdir(project)
            rc = cli_main(["--registry", str(registry), "--runtime-root", str(runtime),
                           "--format", "json", *args])
            return rc, json.loads(capsys.readouterr().out)

    binding = ("--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", "invalid-monitor")
    rc, guard = cli("quota", "should-run", "--goal-id", GOAL_ID, "--codex-app", *binding)
    assert rc == 0 and guard["should_run"] is True, guard
    args = ["refresh-state", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--todo-id", MONITOR_ID if invalid == "binding" else TODO_ID,
            "--turn-instance-id", "invalid-monitor", "--classification", "invalid_unavailable",
            "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_gap",
            "--progress-result-class", "blocked", "--progress-blocker-id", "blocker:source-control",
            "--no-global-sync", "--suppress-external-sinks"]
    if invalid == "binding":
        args += ["--progress-evidence-id", "evidence:control-readback"]
    rc, denied = cli(*args)
    assert rc != 0 and denied["appended"] is False, denied
    assert _classification_count(runtime, "invalid_unavailable") == 0
    assert _spend_run_count(runtime) == 0
