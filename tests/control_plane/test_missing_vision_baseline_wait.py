"""A retained missing baseline cannot repeatedly wake a blocked-only Agent."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from loopx import cli_rollout, rollout_event_log
from loopx.control_plane.quota.effect_program import SettlementIdentity
from loopx.cli import main as cli_main
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload

import pytest

from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item
from loopx.control_plane.scheduler.execution_context import GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT
from loopx.quota import build_quota_should_run

GOAL = "baseline-wait-fixture"
AGENT = "agent-baseline"


def status(*, goal=GOAL, agent=AGENT, extra=()):
    checkpoint = {"schema_version": "vision_checkpoint_v0", "agent_id": agent,
                  "required": True, "satisfied": False, "decision": "missing_required",
                  "missing_baseline": True,
                  "triggers": [{"kind": "material_delivery_outcome", "delivery_outcome": "outcome_progress"}]}
    return quota_status_payload(
        goal_id=goal, status="active", recommended_action="Wait for external input.",
        agent_todo_items=[quota_todo_item(todo_id="todo_blocked", title="External input pending.",
                                        status="blocked", claimed_by=agent), *extra],
        latest_runs=[{"classification": "state_refreshed", "generated_at": "2026-01-01T00:00:00Z",
                      "agent_id": agent, "progress_scope": "agent_lane", "vision_checkpoint": checkpoint}],
        coordination={"agent_model": "peer_v1", "registered_agents": [agent]},
        quota_extra={"allowed_slots": 12, "spent_slots": 1})


def guard(payload, runtime, turn, *, goal=GOAL, agent=AGENT, **kwargs):
    return build_quota_should_run(
        payload, goal_id=goal, agent_id=agent, runtime_root=runtime, turn_instance_id=turn,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
        include_scheduler_detail=True, **kwargs)


def commit_initialization(payload, runtime, turn, *, goal=GOAL, agent=AGENT):
    result = guard(payload, runtime, turn, goal=goal, agent=agent)
    assert result["vision_baseline_initialization"] is True
    obligation = result["autonomous_replan_obligation"]["obligation_id"]
    receipt = rollout_event_log.build_rollout_event(
        goal_id=goal, event_kind="quota_should_run", agent_id=agent, run_id=turn,
        status="autonomous_replan_required",
        details={
            "ok": True, "should_run": True, "must_attempt_work": True,
            "quiet_noop_allowed": False, "delivery_allowed": False,
            "interaction_mode": "autonomous_replan", "vision_baseline_initialization": True,
            "semantic_replan_obligation_id": obligation, "replan_obligation_id": obligation,
            "settlement_effect_id": SettlementIdentity(
                goal_id=goal, agent_id=agent, todo_id=None, turn_instance_id=turn,
                replan_obligation_id=obligation,
            ).effect_id,
        },
    )
    rollout_event_log.append_rollout_event(
        rollout_event_log.rollout_event_log_path(runtime, goal), receipt,
    )
    return result


def assert_wait(result):
    assert result["effective_action"] == "agent_scope_wait"
    assert result["should_run"] is False
    assert result["normal_delivery_allowed"] is False
    assert result["execution_obligation"]["must_attempt_work"] is False
    channel = result["interaction_contract"]["agent_channel"]
    assert channel["delivery_allowed"] is False
    assert channel["quiet_noop_allowed"] is True
    assert channel["must_attempt"] is False
    assert result["scheduler_hint"]["action"] == "backoff_until_reassigned"
    assert result["autonomous_replan_obligation"]["required"] is True


def test_first_initialization_then_fresh_turns_and_process_readback_wait(tmp_path):
    payload = status()
    first = commit_initialization(payload, tmp_path, "initialization")
    assert first["effective_action"] == "autonomous_replan_required"
    assert first["should_run"] is True
    assert guard(payload, tmp_path, "initialization")["should_run"] is True
    for turn in ("poll-1", "poll-2"):
        assert_wait(guard(payload, tmp_path, turn))
    script = (
        "import json,sys; from pathlib import Path; "
        "from tests.control_plane.test_missing_vision_baseline_wait import guard,assert_wait; "
        "result=guard(json.load(sys.stdin),Path(sys.argv[1]),'process-readback'); "
        "assert_wait(result); print(result['effective_action'])"
    )
    completed = subprocess.run([sys.executable, "-c", script, str(tmp_path)], input=json.dumps(payload),
                               text=True, capture_output=True, check=False,
                               env={**os.environ, "LOOPX_USAGE_PING": "0", "PYTHONPATH": str(Path(__file__).resolve().parents[2])})
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "agent_scope_wait"
    events = rollout_event_log.load_rollout_events(rollout_event_log.rollout_event_log_path(tmp_path, GOAL))
    assert [event["run_id"] for event in events] == ["initialization"]


@pytest.mark.parametrize("task_class", ["advancement_task", "continuous_monitor"])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_work_and_due_monitor_resume_after_wait(tmp_path, task_class, provider):
    from canonical_authority_fixture import initialize_canonical_authority

    commit_initialization(status(), tmp_path, "initialization")
    assert_wait(guard(status(), tmp_path, "blocked"))
    work = quota_todo_item(todo_id="todo_independent", title="Perform independent work.",
                               task_class=task_class, claimed_by=AGENT, archive_state="active",
                           **({"cadence": "30m", "next_due_at": "2000-01-01T00:00:00Z"}
                              if task_class == "continuous_monitor" else {}))
    state = tmp_path / "state.md"
    state.write_text("# Synthetic fixture\n\n## Agent Todo\n", encoding="utf-8")
    initialize_canonical_authority(
        tmp_path, GOAL, build_todo_runtime_shadow_projection(
            goal_id=GOAL, todos=[work], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    result = guard(status(extra=[work]), tmp_path, "ready")
    assert result["should_run"] is True
    assert result["effective_action"] != "agent_scope_wait"
    assert_wait(guard(status(), tmp_path, "blocked-again"))


def test_admission_is_scoped_to_goal_and_agent(tmp_path):
    commit_initialization(status(), tmp_path, "initialization")
    for goal, agent in (("another-goal", AGENT), (GOAL, "another-agent")):
        result = commit_initialization(status(goal=goal, agent=agent), tmp_path, "initialization", goal=goal, agent=agent)
        assert result["effective_action"] == "autonomous_replan_required"
    assert len(list(tmp_path.glob("goals/*/rollout-event-log.jsonl"))) == 2


def test_receipt_bound_replan_retains_original_settlement(tmp_path):
    first = commit_initialization(status(), tmp_path, "initialization")
    obligation = first["autonomous_replan_obligation"]["obligation_id"]
    result = guard(status(), tmp_path, "original-owed-turn",
                   receipt_bound_replan_obligation_id=obligation)
    assert result["should_run"] is True
    assert result["autonomous_replan_obligation"]["obligation_id"] == obligation


def test_changed_checkpoint_timestamp_does_not_rearm_baseline(tmp_path):
    commit_initialization(status(), tmp_path, "initialization")
    updated = status()
    updated["run_history"]["goals"][0]["latest_runs"][0]["generated_at"] = "2026-02-01T00:00:00Z"
    assert_wait(guard(updated, tmp_path, "changed-checkpoint"))


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    "failure", ["before_commit", "torn_before_commit", "after_commit", "racing_commit"],
)
def test_runtime_admission_requires_the_committed_positive_receipt(
    tmp_path, monkeypatch, capsys, provider, failure,
):
    from canonical_authority_fixture import initialize_canonical_source_fixture, isolate_sqlite_runtime
    from tests.control_plane.test_dated_vision_wait_cli import AGENT as cli_agent
    from tests.control_plane.test_dated_vision_wait_cli import GOAL as cli_goal, cli, fixture

    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    registry, runtime, state = fixture(tmp_path, with_vision=False)
    state.write_text(state.read_text().replace(
        f"status=deferred claimed_by={cli_agent} task_class=advancement_task action_kind=validate "
        "resume_when=resume_at:2099-01-01T00:00:00Z",
        f"status=blocked claimed_by={cli_agent} task_class=advancement_task action_kind=validate",
    ), encoding="utf-8")
    initialize_canonical_source_fixture(runtime, cli_goal, state_path=state, provider=provider)
    run_root = runtime / "goals" / cli_goal / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    run = status(goal=cli_goal, agent=cli_agent)["run_history"]["goals"][0]["latest_runs"][0]
    run.update(goal_id=cli_goal, delivery_outcome="outcome_progress")
    run_path = run_root / "checkpoint.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    (run_root / "index.jsonl").write_text(
        json.dumps({**run, "json_path": str(run_path)}) + "\n", encoding="utf-8",
    )

    def invoke(turn=None, *extra):
        identity = ["--turn-instance-id", turn] if turn else []
        rc = cli_main([
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "quota", "should-run", "--codex-app", "--context-delivery", "off", "--goal-id", cli_goal,
            "--agent-id", cli_agent, *identity, *extra,
        ])
        return rc, read_cli_policy_payload(capsys.readouterr().out, returncode=rc)

    def receipts():
        return [row for row in rollout_event_log.iter_rollout_events(
            rollout_event_log.rollout_event_log_path(runtime, cli_goal), committed_only=True,
        ) if row["event_kind"] == "quota_should_run"]

    before = receipts()
    rc, preview = invoke(None, "--dry-run")
    assert rc == 0 and preview["effective_action"] == "autonomous_replan_required", json.dumps(preview)
    assert receipts() == before

    append = rollout_event_log._append_rollout_event_line
    injected = []

    def interrupt_at_append(path, event):
        if event.get("event_kind") != "quota_should_run" or event.get("run_id") != "initial":
            return append(path, event)
        injected.append(event)
        if failure == "after_commit":
            append(path, event)
        elif failure == "torn_before_commit":
            with path.open("ab") as output:
                output.write(json.dumps(event).encode())
        raise OSError("injected Vision admission append interruption")

    if failure == "racing_commit":
        append_once = cli_rollout.append_rollout_event_once

        def commit_competing_guard(log_path, event, **kwargs):
            if event.get("run_id") == "initial" and not injected:
                injected.append(event)
                rc, competing = cli(
                    registry, runtime, "quota", "should-run", "--codex-app",
                    "--context-delivery", "off", "--goal-id", cli_goal, "--agent-id", cli_agent,
                    "--turn-instance-id", "competing",
                )
                assert rc == 0 and competing["effective_action"] == "autonomous_replan_required", competing
            return append_once(log_path, event, **kwargs)

        with monkeypatch.context() as race_scope:
            race_scope.setattr(cli_rollout, "append_rollout_event_once", commit_competing_guard)
            rc, original = invoke("initial")
        assert rc == 0, original
        assert_wait(original)
        admitted_turn, waiting_turn = "competing", "initial"
        expected_wait_receipts = ("replayed",)
    else:
        with monkeypatch.context() as failure_scope:
            failure_scope.setattr(rollout_event_log, "_append_rollout_event_line", interrupt_at_append)
            rc, first = invoke("initial")
        if failure != "after_commit":
            assert rc == 1 and first.get("effective_action") == "heartbeat_receipt_write_failed", json.dumps(first)
            assert receipts() == before
            rc, admitted = invoke("fresh")
            assert rc == 0 and admitted["effective_action"] == "autonomous_replan_required", admitted
            admitted_turn, waiting_turn = "fresh", "initial"
        else:
            assert rc == 0 and first["effective_action"] == "autonomous_replan_required", first
            admitted_turn, waiting_turn = "initial", "fresh"
        expected_wait_receipts = ("committed", "replayed")
    assert len(injected) == 1

    rc, replay = invoke(admitted_turn)
    assert rc == 0 and replay["effective_action"] == "autonomous_replan_required", replay
    assert replay["heartbeat_receipt"]["settlement_identity"]["binding_kind"] == "autonomous_replan"
    for expected_status in expected_wait_receipts:
        rc, waiting = invoke(waiting_turn)
        assert rc == 0, waiting
        assert_wait(waiting)
        receipt = waiting["heartbeat_receipt"]
        assert receipt["status"] == expected_status
        assert "settlement_identity" not in receipt
        assert not waiting["interaction_contract"]["cli_channel"].get("settlement_plan")
    admitted_rows = [row for row in receipts() if row["details"].get("vision_baseline_initialization")]
    assert [row["run_id"] for row in admitted_rows] == [admitted_turn]
    runs = (run_root / "index.jsonl").read_text(encoding="utf-8").splitlines()
    assert not any(json.loads(row).get("classification") == "quota_slot_spent" for row in runs)
