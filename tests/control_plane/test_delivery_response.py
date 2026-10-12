"""Current canonical waits constrain historical supervision, not vice versa."""
from copy import deepcopy
import pytest

from loopx.control_plane.handoff.delivery_contract import handoff_delivery_contract
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item, quota_todo_summary
from loopx.control_plane.work_items.delivery_history import project_delivery_response
from loopx.control_plane.todos.summary_item import TODO_PLANNING_SOURCE_KEYS
from loopx.control_plane.todos.resume_condition import evaluate_todo_resume_conditions
from loopx.control_plane.work_items.work_lane_context import build_work_lane_context_contract
from loopx.quota import build_quota_should_run, quota_with_handoff_outcome_floor
from loopx.status import project_post_handoff_history


PROFILE = {"outcome_floor": {"outcome_markers": ["outcome"], "surface_only_hints": ["surface"]}}

# Every resume evaluation in this module reads one injected clock instead of the
# wall clock, so the fixtures stay valid before and after any scheduled instant.
EVALUATED_AT = "2026-09-14T00:00:00Z"
RESUME_AT = "2026-09-15T00:00:00Z"


def blocked_run():
    return {"agent_id": "agent-a", "todo_id": "todo_delivery", "delivery_outcome": "outcome_gap",
        "delivery_batch_scale": "implementation", "progress_observation": {
            "schema_version": "typed_progress_observation_v0", "result_class": "blocked",
            "work_item_id": "todo_delivery", "blocker_id": "blocker_dependency", "evidence_ids": ["evidence_dependency"]}}


def waiting_todo():
    return quota_todo_item(todo_id="todo_delivery", title="Wait for the dependency", status="deferred",
        claimed_by="agent-a", resume_when="todo_done:todo_dependency", resume_ready=False,
        resume_condition={"schema_version": "todo_resume_condition_v0", "resume_when": "todo_done:todo_dependency",
                          "satisfied": False, "kind": "todo_done", "target_todo_id": "todo_dependency", "target_status": "open",
                          "target_task_class": "advancement_task", "target_archive_state": "active"})


def dependency_todo():
    return quota_todo_item(todo_id="todo_dependency", title="Complete prerequisite", claimed_by="agent-b")


@pytest.mark.parametrize("patch", [
    {"target_todo_id": "todo_other"}, {"target_todo_id": None},
    {"target_task_class": "unknown_class"}, {"target_task_class": ""},
    {"kind": "monitor_changed"},
])
def test_stale_or_malformed_target_cannot_suppress_any_consumer(patch):
    todo = waiting_todo()
    extra = quota_todo_item(todo_id="todo_alternative", title="Continue independent work", claimed_by="agent-a")
    summary = quota_todo_summary([todo, dependency_todo(), extra], claim_scope_agent_id="agent-a")
    # Mutate the evaluated snapshot, not the authoring input: summary building
    # legitimately recomputes supplied conditions from its canonical source.
    for key in TODO_PLANNING_SOURCE_KEYS:
        for row in summary.get(key, []):
            if row["todo_id"] == "todo_delivery":
                row["resume_condition"].update(patch)
    readiness = project_post_handoff_history([blocked_run()] * 3, PROFILE)
    asset = {"execution_profile": PROFILE, "agent_todos": summary}
    item = {"handoff_readiness": readiness, "agent_todos": summary, "project_asset": asset}
    before = deepcopy(item)
    assert project_delivery_response(blocked_run(), summary)["reason"] == "history_supervision"
    assert quota_with_handoff_outcome_floor({"state": "eligible"}, waiting_on="codex",
        project_asset=asset, handoff_readiness=readiness)["state"] == "focus_wait"
    assert handoff_delivery_contract(item) is not None
    assert build_work_lane_context_contract(item, agent_todo_summary=summary)["must_attempt_work"] is True
    assert item == before


def test_status_compaction_preserves_binding_and_all_consumers_defer_to_current_wait():
    readiness = project_post_handoff_history([blocked_run()] * 3, PROFILE)
    summary = quota_todo_summary([waiting_todo(), dependency_todo()], claim_scope_agent_id="agent-a")
    asset = {"execution_profile": PROFILE, "agent_todos": summary}
    item = {"handoff_readiness": readiness, "agent_todos": summary, "project_asset": asset}
    assert project_delivery_response(readiness["post_handoff_latest_run"], summary)["reason"] == "canonical_todo_wait"
    quota = {"state": "eligible", "reason": "fixture"}
    assert quota_with_handoff_outcome_floor(quota, waiting_on="codex", project_asset=asset,
        handoff_readiness=readiness) == quota
    assert handoff_delivery_contract(item) is None
    for state in ("paused", "operator_gate", "blocked_health", "throttled", "waiting"):
        hard = {"state": state, "reason": "existing gate"}
        assert quota_with_handoff_outcome_floor(hard, waiting_on="codex", project_asset=asset,
            handoff_readiness=readiness) == hard
    small = {**item, "handoff_readiness": {**readiness, "post_handoff_small_scale_streak": 3}}
    assert handoff_delivery_contract(small)["mode"] == "expand_after_repeated_small_delivery"
    extra = quota_todo_item(todo_id="todo_alternative", title="Implement independent work", claimed_by="agent-a")
    with_work = quota_todo_summary([waiting_todo(), dependency_todo(), extra], claim_scope_agent_id="agent-a")
    lane = build_work_lane_context_contract(item, agent_todo_summary=with_work)
    assert lane["must_attempt_work"] is True
    assert lane["obligation"] == "advance_one_bounded_segment"
    assert "outcome_followthrough" not in lane


@pytest.mark.parametrize("resume, patch", [
    ("monitor_changed:todo_dependency", {"baseline_generation": 1}),
    ("capacity_available:network", {"capability": "other"}),
    # The scheduled instant is one day ahead of the injected evaluation clock,
    # so the condition stays unsatisfied no matter when CI runs.
    (f"resume_at:{RESUME_AT}", {"clock_provider": "other"}),
])
def test_real_resume_projection_identity_survives_python_transport(resume, patch):
    todo = {**waiting_todo(), "resume_when": resume, "resume_monitor_generation": 0,
            "task_repository": "git:github.com/example/project"}
    dependency = {**dependency_todo(), "task_class": "continuous_monitor", "material_change_generation": 0}
    condition = evaluate_todo_resume_conditions(
        [todo], source_items=[dependency], available_capabilities=[],
        evaluated_at=EVALUATED_AT,
    )[todo["todo_id"]]
    summary = quota_todo_summary([todo, dependency], claim_scope_agent_id="agent-a",
                                evaluated_at=EVALUATED_AT)
    for key in TODO_PLANNING_SOURCE_KEYS:
        for row in summary.get(key, []):
            if row["todo_id"] == todo["todo_id"]:
                row["resume_condition"] = condition
    assert project_delivery_response(blocked_run(), summary)["reason"] == "canonical_todo_wait"
    condition.update(patch)
    assert project_delivery_response(blocked_run(), summary)["reason"] == "history_supervision"


@pytest.mark.parametrize("resume", ["pr_merged:#1", "pr_merged:example/project#1"])
def test_unobserved_pr_binding_cannot_prove_a_pending_delivery_wait(resume):
    todo = {**waiting_todo(), "resume_when": resume,
            "task_repository": "git:github.com/example/project"}
    condition = evaluate_todo_resume_conditions(
        [todo], source_items=[], available_capabilities=[],
        evaluated_at=EVALUATED_AT,
    )[todo["todo_id"]]
    assert condition["pr_repo"] == "example/project"
    assert condition["pr_number"] == 1
    assert condition["satisfied"] is False
    summary = quota_todo_summary([todo], claim_scope_agent_id="agent-a",
                                evaluated_at=EVALUATED_AT)
    for key in TODO_PLANNING_SOURCE_KEYS:
        for row in summary.get(key, []):
            if row["todo_id"] == todo["todo_id"]:
                row["resume_condition"] = condition
    # Repo/number identify what to observe. Missing merge evidence does not
    # establish that the PR is pending or relax stalled-delivery supervision.
    assert project_delivery_response(blocked_run(), summary)["reason"] == "history_supervision"
    condition.update({"pr_repo": "example/other", "pr_number": 2})
    assert project_delivery_response(blocked_run(), summary)["reason"] == "history_supervision"


def _resume_at_projection(evaluated_at: str, tamper_clock: bool = False) -> tuple[bool, str]:
    """Evaluate one `resume_at` wait at an explicit instant and project it."""

    todo = {**waiting_todo(), "resume_when": f"resume_at:{RESUME_AT}", "resume_monitor_generation": 0,
            "task_repository": "git:github.com/example/project"}
    dependency = {**dependency_todo(), "task_class": "continuous_monitor", "material_change_generation": 0}
    condition = evaluate_todo_resume_conditions(
        [todo], source_items=[dependency], available_capabilities=[],
        evaluated_at=evaluated_at,
    )[todo["todo_id"]]
    summary = quota_todo_summary([todo, dependency], claim_scope_agent_id="agent-a",
                                evaluated_at=evaluated_at)
    for key in TODO_PLANNING_SOURCE_KEYS:
        for row in summary.get(key, []):
            if row["todo_id"] == todo["todo_id"]:
                row["resume_condition"] = condition
    if tamper_clock:
        condition.update({"clock_provider": "other"})
    return bool(condition.get("satisfied")), project_delivery_response(blocked_run(), summary)["reason"]


@pytest.mark.parametrize("evaluated_at, expected_reason", [
    ("2026-09-14T00:00:00Z", "canonical_todo_wait"),
    ("2026-09-14T23:59:59Z", "canonical_todo_wait"),
    ("2026-09-15T00:00:00Z", "history_supervision"),
    ("2026-09-15T00:00:01Z", "history_supervision"),
    ("2027-09-15T00:00:00Z", "history_supervision"),
])
def test_resume_at_boundary_projects_from_the_injected_clock_not_the_wall_clock(evaluated_at, expected_reason):
    """A `resume_at` boundary must stay stable before and after the scheduled instant.

    The wall clock passed `RESUME_AT` long before this regression was written, so
    only an injected evaluation time can keep both sides of the boundary covered.
    """

    satisfied, reason = _resume_at_projection(evaluated_at)
    assert reason == expected_reason
    assert satisfied is (expected_reason == "history_supervision")
    # An unsatisfied wait still yields to a tampered clock provider, which is the
    # history-supervision half of the original transport contract.
    assert _resume_at_projection(evaluated_at, tamper_clock=True)[1] == "history_supervision"


def test_surface_supervision_remains_and_invalid_wait_does_not_clear_floor():
    summary = quota_todo_summary([waiting_todo()], claim_scope_agent_id="agent-a")
    asset = {"execution_profile": PROFILE, "agent_todos": summary}
    for run in ({"delivery_outcome": "surface_only"}, {"delivery_outcome": "outcome_gap"}):
        readiness = project_post_handoff_history([run] * 3, PROFILE)
        assert quota_with_handoff_outcome_floor({"state": "eligible"}, waiting_on="codex",
            project_asset=asset, handoff_readiness=readiness)["state"] == "focus_wait"
        assert handoff_delivery_contract({"handoff_readiness": readiness, "project_asset": asset}) is not None


def test_unknown_refreshes_do_not_close_canonical_work_or_mutate_sources():
    rows = [quota_todo_item(todo_id="todo_active", title="Implement remaining work", claimed_by="agent-a")]
    payload = quota_status_payload(goal_id="delivery-response", status="ready", agent_todo_items=rows,
        claim_scope_agent_id="agent-a", recommended_action="Advance remaining work", latest_runs=[],
        coordination={"registered_agents": ["agent-a"]})
    before = deepcopy(payload)
    baseline = build_quota_should_run(payload, goal_id="delivery-response", agent_id="agent-a")
    payload["run_history"]["goals"][0]["latest_runs"] = [
        {"agent_id": "agent-a", "classification": "state_refreshed"} for _ in range(50)]
    actual = build_quota_should_run(payload, goal_id="delivery-response", agent_id="agent-a")
    assert actual["work_lane_contract"]["must_attempt_work"] is True
    assert actual["work_lane_contract"] == baseline["work_lane_contract"]
    assert payload["attention_queue"] == before["attention_queue"]
