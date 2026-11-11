"""Explicit blockers must not create work merely by surviving a fresh Turn."""
from __future__ import annotations

import json

import pytest

from canonical_authority_fixture import initialize_canonical_source_fixture, read_fixture_todos
from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision
from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item
from loopx.quota import build_quota_should_run
from loopx.rollout_event_log import build_rollout_event
from tests.control_plane.test_dated_vision_wait_cli import (
    AGENT, GOAL, TODO, cli, fixture, isolated_effect as isolated_effect,
)


def _status(task_class="blocker", *, resume=None, claimed_by=AGENT, with_vision=False,
            extra=(), settled=True):
    rows = [quota_todo_item(todo_id=f"todo_done_{index}", title="Completed advancement.",
                           status="done", claimed_by=AGENT, no_followup=settled)
            for index in range(2)]
    rows.append(quota_todo_item(todo_id=TODO, title="Wait for external input.", status="blocked",
                               task_class=task_class, claimed_by=claimed_by,
                               reason="External input is pending.",
                               **({"resume_when": resume} if resume else {})))
    runs = [{"classification": "fixture_vision", "generated_at": "2026-01-01T00:00:00Z",
             "agent_id": AGENT, "progress_scope": "agent_lane", "agent_vision": {
                 "schema_version": "goal_vision_replan_contract_v0", "agent_id": AGENT,
                 "state": "vision_drift_detected", "todo_delta": [f"retain:{TODO}"],
                 "vision_patch": {"acceptance_summary": "Deliver after external input.",
                                  "replan_trigger_summary": "External input is pending.",
                                  "advancement_policy": "as_needed"}}}] if with_vision else []
    return quota_status_payload(goal_id=GOAL, status="active", agent_todo_items=[*rows, *extra],
                                recommended_action="Wait for external input.", latest_runs=runs,
                                coordination={"agent_model": "peer_v1", "registered_agents": [AGENT]})


def _quota(status):
    return build_quota_should_run(status, goal_id=GOAL, agent_id=AGENT,
                                 turn_instance_id="fresh-blocked-turn",
                                 scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
                                 include_scheduler_detail=True)


def _assert_wait(guard, *, observe=False):
    assert guard["decision"] == guard["effective_action"] == "agent_scope_wait", {
        key: guard[key] for key in ("decision", "effective_action", "should_run")}
    assert guard["should_run"] is False
    assert guard["normal_delivery_allowed"] is False
    assert guard["execution_obligation"]["must_attempt_work"] is False
    channel = guard["interaction_contract"]["agent_channel"]
    assert channel["must_attempt"] is observe
    assert channel["quiet_noop_allowed"] is not observe
    assert channel["delivery_allowed"] is False
    hint = guard["scheduler_hint"]
    assert hint["action"] == "backoff_until_reassigned"
    assert hint["unchanged_poll"]["limits"]["local_scheduler"] == 3
    assert hint["cold_path_detail"]["local_scheduler"]["recommended_interval_minutes"] > 0


@pytest.mark.parametrize("task_class", ["blocker", "advancement_task"])
@pytest.mark.parametrize("resume", [None, "resume_at:2099-01-01T00:00:00Z"])
@pytest.mark.parametrize("claimed_by", [AGENT, None])
@pytest.mark.parametrize("with_vision", [False, True])
def test_only_blocked_work_waits(task_class, resume, claimed_by, with_vision):
    guard = _quota(_status(task_class, resume=resume, claimed_by=claimed_by, with_vision=with_vision))
    timed_successor = bool(with_vision and resume and task_class == "advancement_task")
    _assert_wait(guard, observe=timed_successor)
    if timed_successor:
        wait = guard["goal_frontier_projection"]["vision_wait_state"]
        assert wait["reason_code"] == "exact_blocked_successor"
        assert wait["automatic_resume"] is True
        assert wait["resume_when"] == resume


@pytest.mark.parametrize("task_class", ["blocker", "advancement_task"])
def test_legacy_unsupported_resume_does_not_make_a_blocked_todo_executable(task_class):
    _assert_wait(_quota(_status(task_class, resume="external_input:corpus.txt")))


@pytest.mark.parametrize("extra_class,claim", [
    ("advancement_task", AGENT), ("advancement_task", None), ("continuous_monitor", AGENT),
])
def test_blocker_does_not_hide_executable_work(extra_class, claim):
    extra = quota_todo_item(todo_id="todo_ready", title="Perform independent work.",
                            task_class=extra_class, claimed_by=claim, action_kind="validate",
                            **({"cadence": "30m", "next_due_at": "2026-01-01T00:00:00Z"}
                               if extra_class == "continuous_monitor" else {}))
    guard = _quota(_status(extra=[extra]))
    assert guard["should_run"] is True
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert guard["selected_todo"]["todo_id"] == "todo_ready"


def test_blocker_does_not_hide_owed_replan():
    guard = _quota(_status(settled=False))
    assert guard["effective_action"] == "autonomous_replan_required"
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True


@pytest.mark.parametrize("task_class", ["blocker", "advancement_task"])
@pytest.mark.parametrize("with_vision", [False, True])
def test_satisfied_resume_still_requires_an_attempt(task_class, with_vision):
    guard = _quota(_status(task_class, resume="resume_at:2000-01-01T00:00:00Z", with_vision=with_vision))
    assert guard["should_run"] is True
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
    assert "vision_wait_state" not in guard["goal_frontier_projection"]


@pytest.mark.parametrize("provider", ["file", "sqlite"], ids=["f", "s"])
@pytest.mark.parametrize("with_vision", [False, True], ids=["plain", "vision"])
def test_prior_settlement_preempts_wait(tmp_path, isolated_effect, provider, with_vision):
    registry, runtime, state = fixture(tmp_path, with_vision=with_vision)
    state.write_text(state.read_text(encoding="utf-8").replace("status=deferred", "status=blocked"),
                     encoding="utf-8")
    prior = "prior-turn"
    replan = "replan-1111111111111111"
    event = build_rollout_event(
        goal_id=GOAL, event_kind="quota_should_run", agent_id=AGENT,
        run_id=prior, status="autonomous_replan_required", summary="A previous Turn still requires settlement.",
        details={"replan_obligation_id": replan, "settlement_effect_id": f"{GOAL}:{AGENT}:{replan}:{prior}",
                 "closeout_required": True},
    )
    log = runtime / "goals" / GOAL / "rollout-event-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider=provider)
    status = quota_status_payload(
        goal_id=GOAL, status="active", agent_todo_items=read_fixture_todos(state),
        recommended_action="Wait for external input.",
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT]},
    )
    guard = build_live_quota_should_run_decision(
        status, goal_id=GOAL, agent_id=AGENT, available_capabilities=[],
        include_scheduler_detail=False, codex_app_current_rrule=None,
        registry_path=registry, runtime_root=runtime, turn_instance_id="next-turn",
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )
    assert guard["effective_action"] == "unsettled_host_turn_recovery"
    assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True


@pytest.mark.parametrize("provider", ["file", "sqlite"], ids=["f", "s"])
@pytest.mark.parametrize("resume", ["resume_at:2099-01-01T00:00:00Z", "resume_at:2000-01-01T00:00:00Z"],
                         ids=["pending", "due"])
def test_cli_timed_blocked_vision_preserves_resume_proof(tmp_path, isolated_effect, provider, resume):
    registry, runtime, state = fixture(tmp_path, resume=resume)
    state.write_text(state.read_text(encoding="utf-8").replace("status=deferred", "status=blocked"),
                     encoding="utf-8")
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider=provider)
    before = state.read_bytes()
    for turn in ("first-timed-turn", "next-timed-turn"):
        rc, guard = cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
                        "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", turn,
                        "--include-detail", "scheduler")
        assert rc == 0, guard
        if "2099" in resume:
            wait = guard["vision_wait_state"]
            assert guard["should_run"] is False
            assert wait["reason_code"] == "exact_blocked_successor"
            assert wait["automatic_resume"] is True
            assert wait["resume_when"] == resume
            assert wait["resume_condition"]["scheduled_for"] == resume.removeprefix("resume_at:")
            assert guard["scheduler_hint"]["cold_path_detail"]["local_scheduler"]["recommended_interval_minutes"] > 0
        else:
            assert guard["should_run"] is True
            assert guard["interaction_contract"]["agent_channel"]["must_attempt"] is True
            assert "vision_wait_state" not in guard["goal_frontier_projection"]
    assert state.read_bytes() == before


@pytest.mark.parametrize("provider", ["file", "sqlite"], ids=["f", "s"])
@pytest.mark.parametrize("task_class", ["blocker", "advancement_task"], ids=["b", "a"])
@pytest.mark.parametrize("resume", [None, "resume_at:2099-01-01T00:00:00Z"], ids=["m", "c"])
def test_cli(
    tmp_path, isolated_effect, provider, task_class, resume,
):
    registry, runtime, state = fixture(tmp_path, with_vision=False)
    prefix = state.read_text(encoding="utf-8").split("## Agent Todo")[0]
    completed = "".join(
        f"- [x] Completed advancement {index}.\n"
        f"  <!-- loopx:todo todo_id=todo_done_{index} status=done claimed_by={AGENT} "
        "task_class=advancement_task no_followup=true -->\n"
        for index in range(2)
    )
    blocked = (f"- [ ] Wait for external input.\n"
               f"  <!-- loopx:todo todo_id={TODO} status=blocked claimed_by={AGENT} "
               f"task_class={task_class} action_kind=validate reason=await_input "
               + (f"resume_when={resume} " if resume else "") + "-->\n")
    state.write_text(prefix + "## Agent Todo\n\n" + completed + blocked, encoding="utf-8")
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider=provider)
    before = state.read_bytes()
    for turn in ("first-blocked-turn", "next-blocked-turn"):
        rc, guard = cli(registry, runtime, "quota", "should-run", "--runtime-profile", "generic_cli",
                        "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", turn,
                        "--include-detail", "scheduler")
        assert rc == 0, guard
        _assert_wait(guard)
        assert "settlement_identity" not in guard["heartbeat_receipt"]
    assert state.read_bytes() == before
