"""Capability improvement: original-owner configuration and actual CLI."""
from copy import deepcopy
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from loopx.capabilities.goal_capability_organization.goal_configuration import configuration_summary
from loopx.chat_goal_configuration_api import (
    CHAT_GOAL_CONFIGURATION_PREVIEW_PATH, GoalConfigurationRequestMixin,
)
from loopx.configure_goal import configure_goal
from loopx.control_plane.agent_context import project_goal_agent_context
from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.work_items.autonomous_replan_obligation import build_autonomous_replan_obligation_payload

POLICY = {"mode": "bounded", "discovery_budget_minutes": 5, "max_trials": 1}
SCOPE = {"goal_id": "example", "agent_id": "coordinator", "todo_id": "todo_example0001"}


@pytest.fixture
def registry(tmp_path):
    path = tmp_path / "registry.json"
    path.write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": "example", "repo": str(tmp_path), "status": "active",
                   "coordination": {"registered_agents": ["coordinator"]}},
                  {"id": "other", "repo": str(tmp_path), "status": "active"}],
    }))
    return path


def cli(registry, *args, success=True):
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                             "--format", "json", *args], capture_output=True, text=True)
    assert (result.returncode == 0) is success, result.stderr + result.stdout
    return json.loads(result.stdout)


def contribution(result):
    context = result.get("agent_context") or result["interaction_contract"]["agent_context"]
    return next(item for item in context["contributions"] if item["capability_id"] == "goal_capability_organization")


def test_real_cli_preview_apply_readback_off_and_clear(registry):
    command = ["configure-goal", "--goal-id", "example", "--capability-improvement-mode", "bounded"]
    before = registry.read_bytes()
    preview = cli(registry, *command)
    assert preview["after"]["goal_capability_organization"] == POLICY
    assert registry.read_bytes() == before
    applied = cli(registry, *command, "--execute")
    assert applied["written"] is True
    goals = json.loads(registry.read_text())["goals"]
    assert goals[0]["control_plane"]["capability_improvement"] == POLICY
    assert "spawn_policy" not in goals[0]
    assert configuration_summary(goals[1]) is None
    readback = cli(registry, "capability", "inspect", "--goal-id", "example")
    entry = next(item for item in readback["configuration"]["capability_catalog"]["capabilities"]
                 if item["capability_id"] == "goal_capability_organization")
    assert entry["effective_configuration"]["source"] == "goal_override"
    assert entry["current"] == POLICY
    assert entry["configuration_editor"]["editable"] is True
    context_command = ["agent-context", "--goal-id", "example", "--agent-id", "coordinator", "--phase", "before_plan"]
    assert contribution(cli(registry, *context_command))["facts"]["reason_code"] == "no_goal_gap"
    gap = cli(registry, *context_command, "--capability-gap-ref", "example/gap", "--capability-planning-trigger", "replan")
    assert contribution(gap)["facts"]["recommendation"] == "bounded_discovery"
    assert contribution(gap)["facts"]["planning_trigger"] == "replan"
    cli(registry, "configure-goal", "--goal-id", "example", "--capability-improvement-mode", "off", "--execute")
    assert cli(registry, *context_command)["agent_context"] is None
    cli(registry, "configure-goal", "--goal-id", "example", "--clear-capability-improvement-configuration", "--execute")
    assert configuration_summary(json.loads(registry.read_text())["goals"][0]) is None
    assert "control_plane" not in json.loads(registry.read_text())["goals"][0]


@pytest.mark.parametrize("kwargs", [
    {"capability_improvement_configuration": {"mode": "auto_install"}},
    {"capability_improvement_configuration": {"enabled": True}},
    {"capability_improvement_configuration": {"max_trials": True}},
    {"capability_improvement_configuration": {"discovery_budget_minutes": 31}},
    {"capability_improvement_configuration": POLICY, "clear_capability_improvement_configuration": True},
])
def test_invalid_intent_cannot_mutate_registry(registry, kwargs):
    before = registry.read_bytes()
    with pytest.raises(ValueError):
        configure_goal(registry_path=registry, goal_id="example", execute=True, **kwargs)
    assert registry.read_bytes() == before


def test_corrupt_policy_is_visible_fail_open_and_clearable(registry):
    payload = json.loads(registry.read_text())
    goal = payload["goals"][0]
    goal["control_plane"] = {"capability_improvement": {**POLICY, "max_trials": 500}}
    registry.write_text(json.dumps(payload))
    assert configuration_summary(goal)["configuration_status"] == "invalid"
    context = project_goal_agent_context(phase="before_plan", scope=SCOPE, goal=goal,
                                        registry_path=registry, runtime_root=registry.parent / "runtime")
    assert context["contributions"] == []
    assert context["failures"][0]["code"] == "context_provider_failed"
    result = configure_goal(registry_path=registry, goal_id="example", execute=True,
                            clear_capability_improvement_configuration=True)
    assert result["written"] is True
    assert configuration_summary(json.loads(registry.read_text())["goals"][0]) is None


def test_real_cli_trial_feedback_keeps_failed_inputs_out_of_retry_advice(registry):
    cli(registry, "configure-goal", "--goal-id", "example",
        "--capability-improvement-mode", "bounded", "--execute")
    command = ["agent-context", "--goal-id", "example", "--agent-id", "coordinator",
               "--phase", "before_plan", "--capability-gap-ref", "example/gap"]
    candidate = {"capability_id": "example-source", "applicable": True, "enabled": False,
                 "configuration_ref": "owner/config-v1", "effect_ref": "experiment/baseline-v1",
                 "rollback_ref": "owner/rollback", "candidate_revision": "revision-v1"}

    def observe(*candidates):
        args = command.copy()
        for item in candidates:
            args.extend(["--capability-candidate-json", json.dumps(item)])
        return cli(registry, *args)

    before = registry.read_bytes()
    first = contribution(observe(candidate))["facts"]
    assert first["recommendation"] == "propose_reversible_trial"
    failed = {**candidate, "trial_feedback": {
        "outcome_ref": "owner/outcome-v1", "trial_basis_digest": first["trial_basis_digest"],
        "status": "failed"}}
    facts = contribution(observe(failed))["facts"]
    assert facts["reason_code"] == "prior_trial_failed"
    assert facts["recommendation"] == "continue_current_work"
    assert facts["outcome_ref"] == "owner/outcome-v1"
    assert contribution(observe({**failed, "configuration_ref": "owner/config-v2"}))["facts"]["reason_code"] == "trial_feedback_stale"
    success = {**candidate, "trial_feedback": {**failed["trial_feedback"], "status": "succeeded"}}
    assert contribution(observe(success))["facts"]["recommendation"] == "inspect_trial_outcome"
    fresh = {**candidate, "candidate_revision": "revision-v2"}
    independent = contribution(observe(failed, fresh))["facts"]
    assert independent["recommendation"] == "propose_reversible_trial"
    assert independent["trial_basis_digest"] != first["trial_basis_digest"]
    # Invalid feedback only loses this optional contribution. A fresh original
    # owner read can recover it; neither call changes the registry or admission.
    broken = {**failed, "trial_feedback": {**failed["trial_feedback"], "status": "accepted"}}
    invalid = observe(broken)["agent_context"]
    assert invalid["contributions"] == []
    assert invalid["failures"][0]["code"] == "context_provider_failed"
    assert contribution(observe(candidate))["facts"]["recommendation"] == "propose_reversible_trial"
    assert registry.read_bytes() == before
    cli(registry, "configure-goal", "--goal-id", "example",
        "--capability-improvement-mode", "off", "--execute")
    off_before = registry.read_bytes()
    assert observe(broken)["agent_context"] is None
    assert registry.read_bytes() == off_before


class SettingsHandler(GoalConfigurationRequestMixin):
    def __init__(self, registry, body):
        self.path = CHAT_GOAL_CONFIGURATION_PREVIEW_PATH
        self.server = SimpleNamespace(registry_path=registry, runtime_root_override=None)
        self.body, self.responses = body, []

    def _read_json(self):
        return self.body

    def _send_json(self, payload, *, status=200):
        self.responses.append({"http_status": status, **payload})

    def _send_error(self, message, *, status, error_code, **kwargs):
        self.responses.append({"http_status": status, "error_code": error_code, "error": message})


def test_dashboard_original_owner_cas_and_shared_cli_readback(registry):
    body = {"goal_id": "example", "capability_id": "goal_capability_organization", "configuration": POLICY}
    before = registry.read_bytes()
    handler = SettingsHandler(registry, body)
    handler._goal_configuration_update(execute=False)
    preview = handler.responses.pop()
    assert preview["http_status"] == 201
    assert registry.read_bytes() == before
    configure_goal(registry_path=registry, goal_id="example", capability_improvement_configuration={**POLICY, "max_trials": 0}, execute=True)
    handler.body = {**body, "expected_plan_revision": preview["plan_revision"]}
    handler._goal_configuration_update(execute=True)
    assert handler.responses.pop()["http_status"] == 409
    handler.body = body
    handler._goal_configuration_update(execute=False)
    fresh = handler.responses.pop()
    handler.body["expected_plan_revision"] = fresh["plan_revision"]
    handler._goal_configuration_update(execute=True)
    receipt = handler.responses.pop()
    assert receipt["http_status"] == 200
    assert receipt["readback_verified"] is True
    assert receipt["goal_configuration"] == POLICY
    assert cli(registry, "configure-goal", "--goal-id", "example")["after"]["goal_capability_organization"] == POLICY


@pytest.mark.parametrize("replan", [False, True])
def test_live_quota_context_uses_original_goal_intent_not_parallel_configuration(registry, replan):
    configure_goal(registry_path=registry, goal_id="example", execute=True,
                   capability_improvement_configuration=POLICY)
    obligation = build_autonomous_replan_obligation_payload(
        schema_version="autonomous_replan_obligation_v0", stall_threshold=2, trigger_count=1,
        triggers=[{"kind": "typed_progress_repeat", "progress_fingerprint": "repeat-example",
                   "latest_generated_at": "2026-08-13T01:02:00Z"}],
        guidance_actions=["create_successor"], todo_actions=[],
        stop_condition="stop on owner-only authority", recommended_action="bounded replan",
        agent_id="coordinator", include_agent_id=True,
    ) if replan else None
    replan_extra = {"autonomous_replan_obligation": obligation} if obligation else {}
    status = quota_status_payload(goal_id="example", status="active", recommended_action="Inspect sources",
        item_extra=replan_extra, project_asset_extra=replan_extra,
        coordination={"registered_agents": ["coordinator"]}, agent_todo_items=[{
            "todo_id": "todo_example0001", "index": 1, "text": "Inspect sources", "status": "open",
            "priority": "P1", "role": "agent", "task_class": "advancement_task",
        }])
    before = registry.read_bytes()
    result = build_live_quota_should_run_decision(deepcopy(status), goal_id="example", agent_id="coordinator",
        available_capabilities=["shell"], include_scheduler_detail=False, codex_app_current_rrule=None,
        registry_path=registry, runtime_root=registry.parent / "runtime", scheduler_execution_context={
            "host_surface": "generic_cli", "scheduler_owner": "agent_cli_loop", "execution_mode": "interactive",
        })
    assert contribution(result)["facts"]["reason_code"] == "no_goal_gap"
    assert contribution(result)["facts"]["planning_trigger"] == ("replan" if replan else "before_plan")
    if replan:
        assert result["replan_action_packet"]["obligation_id"] == obligation["obligation_id"]
    assert registry.read_bytes() == before
