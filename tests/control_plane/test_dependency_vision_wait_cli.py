"""Public creation and canonical source changes must reach the actual worker."""
from __future__ import annotations

import json
import io
import os
import sys
from argparse import Namespace
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.cli import main
from test_dated_vision_wait_cli import REPO, isolated_effect  # noqa: F401

GOAL = "dependency-vision-fixture"
AGENT = "agent-waiter"
PEER = "agent-prerequisite"


@pytest.fixture(params=["file", "sqlite"])
def public_goal(request, tmp_path, monkeypatch):
    # Configuration, registry, Todo state and source facts all enter through the
    # shipped CLI. No prebuilt provider projection or run history is installed.
    request.getfixturevalue("isolated_effect")
    monkeypatch.setenv("NODE_OPTIONS", os.environ.get("NODE_OPTIONS", "") + " --experimental-sqlite")
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project = tmp_path / "project"
    project.mkdir()
    registry, runtime = project / ".loopx/registry.json", tmp_path / "runtime"

    def call(*args, expected_exit=0):
        output = io.StringIO()
        with redirect_stdout(output):
            result = main(["--registry", str(registry), "--runtime-root", str(runtime),
                           "--format", "json", *args])
        payload = json.loads(output.getvalue())
        assert result == expected_exit, payload
        return payload

    storage = tmp_path / "storage.json"
    storage.write_text(json.dumps({"schema_version": "loopx_goal_storage_defaults_v1",
        "new_goal_provider": request.param, "canonical_creation": True, "new_goal_handoff_mode": "soft_claim"}))
    preview = call("machine-config", "preview", "--namespace", "goal_storage", "--config-json", str(storage))
    call("machine-config", "apply", "--namespace", "goal_storage", "--config-json", str(storage),
         "--expected-plan-revision", preview["plan_revision"], "--execute")
    registered = call("project", "register", "--project-id", "dependency-fixture", "--project-kind", "work",
        "--knowledge-root", str(project), "--goal-id", GOAL,
        "--objective", "Validate the retained result after its prerequisite changes.",
        "--acceptance", "The retained result is validated.", "--next-effect", "Wait for the prerequisite result.",
        "--stop-condition", "Stop if the owner cancels.")
    assert registered["storage_selection"]["authority_initialized"] is True
    assert registered["storage_selection"]["provider"] == request.param
    configured = call("configure-goal", "--goal-id", GOAL, "--registered-agent", AGENT,
                      "--registered-agent", PEER, "--execute")
    assert configured["global_sync"]["selected_target"]["runtime_root"] == str(runtime)
    return SimpleNamespace(call=call, registry=registry, runtime=runtime, root=tmp_path,
                           state=Path(registered["state_file"]), provider=request.param)


def add(goal, text, *options, owner=AGENT):
    return goal.call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--text", text,
                     "--claimed-by", owner, *options)["todo_id"]


def update(goal, todo_id, *options):
    listed = goal.call("todo", "list", "--goal-id", GOAL)
    revision = listed["authority_read"]["provider_revision"]
    return goal.call("todo", "update", "--goal-id", GOAL, "--todo-id", todo_id, "--agent-id", AGENT,
        "--update-operation-id", f"update-{revision}", "--update-expected-provider-revision", revision, *options)


def wait_on(goal, kind, *, missing_source=False, monitor_due="2099-01-01T00:00:00Z",
            monitor_expiry="2100-01-01T00:00:00Z", monitor_cadence="1h"):
    if kind == "todo_done":
        artifact = goal.root / "prerequisite.txt"
        artifact.write_text("pending")
        validator = [sys.executable, "-c",
                     "import pathlib,sys; assert pathlib.Path(sys.argv[1]).read_text() == 'ready'", str(artifact)]
        source = add(goal, "Prepare the prerequisite artifact.", "--task-class", "advancement_task",
                     "--action-kind", "implement", "--validation-command-json", json.dumps(validator), owner=PEER)
        waiting = add(goal, "Validate the retained result.", "--task-class", "advancement_task",
            "--action-kind", "validate", "--status", "deferred", "--resume-when",
            f"todo_done:{'todo_absent_source' if missing_source else source}")
    else:
        # The waiting Agent owns the Monitor too: its eventual due work must
        # still run, or this wait would silence its only generation producer.
        schedule = []
        if monitor_cadence is not None:
            schedule.extend(["--cadence", monitor_cadence])
        if monitor_due is not None:
            schedule.extend(["--next-due-at", monitor_due])
        if monitor_expiry is not None:
            schedule.extend(["--expires-at", monitor_expiry])
        source = add(goal, "Observe the prerequisite artifact.", "--task-class", "continuous_monitor",
                     "--action-kind", "observe", "--target-key", "prerequisite-artifact",
                     *schedule)
        independent = add(goal, "Validate an independent artifact.", "--task-class", "advancement_task",
                          "--action-kind", "validate", owner=PEER)
        waiting = add(goal, "Validate the retained result.", "--task-class", "advancement_task", "--action-kind", "validate")
        update(goal, waiting, "--resume-when", f"monitor_changed:{source}", "--successor-todo-id", independent)
    vision = goal.root / "vision.json"
    vision.write_text(json.dumps({"schema_version": "goal_vision_replan_contract_v0", "state": "vision_drift_detected",
        "todo_delta": [f"activate:{waiting}"], "vision_patch": {"acceptance_summary": "Validate the retained result.",
            "advancement_policy": "as_needed", "replan_trigger_summary": "The retained result still requires its prerequisite."}}))
    goal.call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT, "--classification", "dependency_fixture",
              "--agent-vision-json", str(vision), "--no-global-sync", "--suppress-external-sinks")
    return source, waiting


def guard(goal, *options):
    return goal.call("quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
                     "--runtime-profile", "generic_cli", "--include-detail", "scheduler", *options)


def assert_quiet(packet, waiting, *, monitor=False):
    assert packet["should_run"] is False
    assert packet["effective_action"] == ("monitor_quiet_skip" if monitor else "agent_scope_wait")
    assert packet["vision_wait_state"]["selected_todo_id"] == waiting
    channel = packet["interaction_contract"]["agent_channel"]
    assert channel["must_attempt"] is False and channel["quiet_noop_allowed"] is True
    assert channel["delivery_allowed"] is False
    if not monitor:
        assert not any("monitor-poll" in command for command in packet["interaction_contract"]["cli_channel"]["next_cli_actions"])
    hint = packet["scheduler_hint"]
    assert hint["action"] == ("backoff_until_material_transition" if monitor else "backoff_until_state_change")
    assert all(value is None for value in hint["unchanged_poll"]["limits"].values())
    assert set(hint["unchanged_poll"]["after_limits"].values()) == {"continue"}
    assert hint["unchanged_poll"]["final_quota_replan_check_enabled"] is False


def canonical_todos(listed):
    # Resume conditions are fresh read models, including their clock witness.
    return [{key: value for key, value in todo.items() if key not in ("resume_condition", "resume_ready")}
            for todo in listed["todos"]]


def worker_args(goal):
    return Namespace(cli_bin="loopx", registry=str(goal.registry), runtime_root=str(goal.runtime),
        runtime_profile="generic_cli", goal_id=GOAL, agent_id=AGENT, state_file=str(goal.root / "scheduler.json"),
        once=True, wake_cmd="unused", wake_stop_exit_code=75,
        error_backoff_seconds=5, quota_timeout_seconds=90, wake_timeout_seconds=None)


def test_pending_dependency_stays_quiet_for_100_polls_restart_and_100_more(public_goal, monkeypatch):
    goal = public_goal
    source, waiting = wait_on(goal, "todo_done")
    before = goal.call("todo", "list", "--goal-id", GOAL)
    run_index = goal.runtime / f"goals/{GOAL}/runs/index.jsonl"
    runs_before = [json.loads(line) for line in run_index.read_text().splitlines()]
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import external_scheduler_worker as worker

    packets, sleeps = [], []
    counts = {"D": 0, "P": 0, "E": 0, "Q": 0}

    def probe(command, **kwargs):
        # A new public command reloads the actual provider on every tick.
        packet = goal.call(*command[command.index("quota"):])
        assert_quiet(packet, waiting)
        assert packet["vision_wait_state"]["resume_condition"]["target_todo_id"] == source
        packets.append(packet)
        return packet

    def unexpected_wake(*args, **kwargs):
        counts["D"] += 1
        raise AssertionError("an unchanged dependency dispatched new model work")

    class RestartWorker(Exception):
        pass

    def advance(seconds):
        sleeps.append(seconds)
        if len(sleeps) in (100, 200):
            raise RestartWorker

    monkeypatch.setattr(worker, "run_quota_should_run", probe)
    monkeypatch.setattr(worker, "_run_wake", unexpected_wake)
    args = worker_args(goal)
    args.once = False
    for expected in (100, 200):
        # Interrupt only after the actual worker has persisted the tick, then
        # reopen that same state route. No real hours or model calls are used.
        with pytest.raises(RestartWorker):
            worker.run_worker(args, sleep=advance)
        persisted = json.loads(Path(args.state_file).read_text())
        assert persisted["unchanged_count"] == expected
        assert len(packets) == expected
        assert not persisted.get("replan_attempts")
    after = goal.call("todo", "list", "--goal-id", GOAL)
    assert canonical_todos(after) == canonical_todos(before)
    assert after["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]
    runs = [json.loads(line) for line in run_index.read_text().splitlines()]
    counts["Q"] = sum(bool(run.get("quota_event")) for run in runs[len(runs_before):])
    events_path = goal.runtime / f"goals/{GOAL}/rollout-event-log.jsonl"
    events = [json.loads(line) for line in events_path.read_text().splitlines()]
    assert not any(event.get("event_kind") in ("quota_monitor_poll", "quota_spend") for event in events)
    assert counts == {"D": 0, "P": 0, "E": 0, "Q": 0}
    assert len(sleeps) == 200 and sleeps[-1] == 3600


@pytest.mark.parametrize("kind", ["todo_done", "monitor_changed"])
def test_real_worker_survives_restart_and_follows_its_dependency_source(public_goal, monkeypatch, kind):
    goal = public_goal
    source, waiting = wait_on(goal, kind)
    before = goal.call("todo", "list", "--goal-id", GOAL)
    first = guard(goal)
    assert_quiet(first, waiting, monitor=kind == "monitor_changed")
    if kind == "monitor_changed":
        wait = first["vision_wait_state"]
        assert wait["resume_monitor_generation"] == wait["resume_condition"]["baseline_generation"] == 0
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import external_scheduler_worker as worker

    packets, sleeps, wakes = [], [], []

    def probe(command, **kwargs):
        packet = goal.call(*command[command.index("quota"):])
        packets.append(packet)
        return packet

    def advance(seconds):
        sleeps.append(seconds)
        assert len(sleeps) <= 2, "fresh source changes must leave dependency waiting"
        if len(sleeps) == 2:
            unchanged = goal.call("todo", "list", "--goal-id", GOAL)
            assert canonical_todos(unchanged) == canonical_todos(before)
            assert unchanged["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]
            if kind == "todo_done":
                (goal.root / "prerequisite.txt").write_text("ready")
                goal.call("todo", "complete", "--goal-id", GOAL, "--todo-id", source, "--agent-id", PEER,
                          "--no-follow-up", "--evidence", str(goal.root / "prerequisite.txt"))
            else:
                update(goal, source, "--next-due-at", "2026-01-01T00:00:00Z")

    def wake(*args, **kwargs):
        assert len(sleeps) == 2, {key: packets[-1].get(key) for key in (
            "effective_action", "autonomous_replan", "goal_frontier_projection", "selected_todo", "vision_wait_state")}
        wakes.append(packets[-1])
        return SimpleNamespace(returncode=75, failure_kind=None)

    monkeypatch.setattr(worker, "run_quota_should_run", probe)
    monkeypatch.setattr(worker, "_run_wake", wake)
    args = worker_args(goal)
    # Two separate invocations resume the persisted backoff; the live run then
    # passes the old three-poll stop and observes the next real source event.
    assert worker.run_worker(args, sleep=advance) == 0
    assert worker.run_worker(args, sleep=advance) == 0
    args.once = False
    assert worker.run_worker(args, sleep=advance) == 0
    assert sleeps == ([3600, 3600] if kind == "monitor_changed" else [1800, 3600])
    assert len(packets) == 5 and len(wakes) == 1
    for packet in packets[:-1]:
        assert_quiet(packet, waiting, monitor=kind == "monitor_changed")
    assert packets[-1]["scheduler_hint"]["action"] == "run_now"
    assert packets[-1]["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert packets[-1]["selected_todo"]["todo_id"] == (waiting if kind == "todo_done" else source)
    if kind == "todo_done":
        assert packets[-1]["effective_action"] == "successor_replan_required"
    else:
        binding = ("--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", "actual-monitor-event")
        admitted = goal.call("quota", "should-run", "--codex-app", *binding)
        assert admitted["heartbeat_receipt"]["settlement_identity"]["todo_id"] == source
        poll = goal.call("quota", "monitor-poll", "--execute", "--codex-app", *binding,
            "--todo-id", source, "--result-hash", "new-prerequisite-result", "--material-change",
            "--next-agent-todo", "Validate the new prerequisite result.", "--next-action-kind", "validate")
        assert poll["material_change"] is True
        resumed = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", waiting)["todo"]
        assert resumed["resume_ready"] is True
        assert resumed["resume_condition"]["material_change_generation"] > resumed["resume_monitor_generation"]


def test_unknown_dependency_keeps_bounded_supervision(public_goal):
    goal = public_goal
    _, waiting = wait_on(goal, "todo_done", missing_source=True)
    packet = guard(goal)
    assert packet["vision_wait_state"]["selected_todo_id"] == waiting
    assert "target_status" not in packet["vision_wait_state"]["resume_condition"]
    assert packet["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert set(packet["scheduler_hint"]["unchanged_poll"]["limits"].values()) == {3}


@pytest.mark.parametrize("expiry", ["2026-01-01T00:00:00Z", "2098-01-01T00:00:00Z"])
def test_expired_or_unreachable_monitor_does_not_keep_waiting_forever(public_goal, expiry):
    goal = public_goal
    _, waiting = wait_on(goal, "monitor_changed", monitor_expiry=expiry)
    packet = guard(goal)
    assert packet["effective_action"] == "monitor_quiet_skip"
    assert packet["should_run"] is False
    assert packet["vision_wait_state"]["selected_todo_id"] == waiting
    assert packet["vision_wait_state"]["resume_condition"]["target_expires_at"] == expiry
    assert set(packet["scheduler_hint"]["unchanged_poll"]["limits"].values()) == {3}


def test_monitor_without_schedule_keeps_original_observation_admission(public_goal):
    goal = public_goal
    source, _ = wait_on(goal, "monitor_changed", monitor_due=None, monitor_cadence=None)
    packet = guard(goal)
    assert packet["should_run"] is True, packet
    assert packet["effective_action"] == "external_evidence_observe"
    assert packet["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert packet["scheduler_hint"]["action"] == "run_now"
    monitor = goal.call("todo", "list", "--goal-id", GOAL, "--todo-id", source)["todo"]
    assert not monitor.get("next_due_at") and not monitor.get("cadence")


def test_unavailable_provider_cannot_fall_back_or_wake_waiting_work(public_goal, monkeypatch):
    from loopx.control_plane.effect_runtime import restart_effect_runtime

    goal = public_goal
    _, waiting = wait_on(goal, "todo_done")
    assert_quiet(guard(goal), waiting)
    before = goal.call("todo", "list", "--goal-id", GOAL)
    provider = goal.runtime / "authority" / f"{goal.provider}-v0"
    retained = goal.root / "temporarily-unavailable-provider"
    assert provider.is_dir()
    restart_effect_runtime()
    provider.rename(retained)
    monkeypatch.syspath_prepend(str(REPO / "scripts"))
    import external_scheduler_worker as worker

    def unavailable(command, **kwargs):
        packet = goal.call(*command[command.index("quota"):], expected_exit=1)
        assert packet.get("should_run") is not True, packet
        raise RuntimeError("canonical dependency provider is unavailable")

    def unexpected_wake(*args, **kwargs):
        raise AssertionError("an unavailable authority must never dispatch work")

    monkeypatch.setattr(worker, "run_quota_should_run", unavailable)
    monkeypatch.setattr(worker, "_run_wake", unexpected_wake)
    try:
        assert worker.run_worker(worker_args(goal)) == 2
        assert not provider.exists()
    finally:
        retained.rename(provider)
        restart_effect_runtime()
    assert_quiet(guard(goal), waiting)
    restored = goal.call("todo", "list", "--goal-id", GOAL)
    assert canonical_todos(restored) == canonical_todos(before)
    assert restored["authority_read"]["provider_revision"] == before["authority_read"]["provider_revision"]


def test_stop_and_independent_work_override_the_registered_wait(public_goal):
    goal = public_goal
    _, waiting = wait_on(goal, "todo_done")
    assert_quiet(guard(goal), waiting)
    goal.call("goal-lifecycle", "--goal-id", GOAL, "--operation", "stop", "--actor-kind", "owner",
              "--reason", "Owner stopped this isolated test.", "--execute")
    stopped = guard(goal)
    assert stopped["scheduler_hint"]["action"] == "stop_until_explicit_resume"
    assert stopped["should_run"] is False
    goal.call("goal-lifecycle", "--goal-id", GOAL, "--operation", "resume", "--actor-kind", "owner",
              "--reason", "Owner resumed this isolated test.", "--execute")
    assert_quiet(guard(goal), waiting)
    independent = add(goal, "Validate a newly available independent result.", "--task-class", "advancement_task",
                      "--action-kind", "validate")
    fresh = guard(goal)
    assert fresh["selected_todo"]["todo_id"] == independent
    assert fresh["scheduler_hint"]["action"] == "run_now"
    assert fresh["interaction_contract"]["agent_channel"]["must_attempt"] is True


def test_reopened_work_and_restored_vision_leave_peer_dependency_pending(public_goal):
    goal = public_goal
    prerequisite, waiting = wait_on(goal, "todo_done")
    ongoing = add(goal, "Continue investigating the retained objective.",
                  "--task-class", "advancement_task", "--action-kind", "research_analysis",
                  "--status", "blocked")
    vision = goal.root / "continuation-vision.json"

    def write_vision(state):
        vision.write_text(json.dumps({
            "schema_version": "goal_vision_replan_contract_v0", "state": state,
            "todo_delta": [f"activate:{ongoing}"],
            "vision_patch": {
                "acceptance_summary": "Investigate the ongoing objective using available evidence.",
                "advancement_policy": "repeat_until_closed",
                "replan_trigger_summary": "The next independent investigation remains executable.",
            },
        }))
        goal.call("refresh-state", "--goal-id", GOAL, "--agent-id", AGENT,
                  "--classification", "continuation_fixture", "--agent-vision-json", str(vision),
                  "--no-global-sync", "--suppress-external-sinks")

    # A falsely retired stage plus blocked work leaves only the genuine wait.
    write_vision("retired")
    before = guard(goal)
    assert before["should_run"] is False
    assert before["effective_action"] == "agent_scope_wait"

    update(goal, ongoing, "--status", "open", "--clear-resume-when")
    write_vision("vision_patch_proposed")
    repaired = guard(goal)
    assert repaired["should_run"] is True
    assert repaired["normal_delivery_allowed"] is True
    assert repaired["selected_todo"]["todo_id"] == ongoing
    assert repaired["interaction_contract"]["agent_channel"]["must_attempt"] is True
    todos = {todo["todo_id"]: todo for todo in goal.call("todo", "list", "--goal-id", GOAL)["todos"]}
    assert todos[prerequisite]["status"] == "open"
    assert todos[waiting]["status"] == "deferred"
    assert todos[waiting]["resume_when"] == f"todo_done:{prerequisite}"

    # Restoring continuation must still respect an actual owner stop.
    goal.call("goal-lifecycle", "--goal-id", GOAL, "--operation", "stop", "--actor-kind", "owner",
              "--reason", "Owner stopped this isolated test.", "--execute")
    stopped = guard(goal)
    assert stopped["should_run"] is False
    assert stopped["scheduler_hint"]["action"] == "stop_until_explicit_resume"
