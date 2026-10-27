from copy import deepcopy
from hashlib import sha256
import json
import subprocess
import sys

import pytest

from loopx.control_plane.agent_context import (
    envelope_agent_context,
    project_agent_context,
)
from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision
from loopx.control_plane.quota.turn_envelope import (
    build_turn_envelope,
    turn_envelope_action_signature_document,
)
from loopx.control_plane.testing.quota_fixtures import quota_status_payload
from loopx.control_plane.turn_driver import (
    build_loopx_turn_plan,
    build_loopx_turn_host_request,
)
from loopx.control_plane.turn_driver.subagent_execution_topology import (
    observe_subagent_host_result,
    subagent_execution_payload_projection,
)
from tests.test_loopx_turn_driver import _adaptive_envelope
from tests.test_loopx_turn_executor import (
    _callbacks,
    _host_result,
    _journal,
    _passing_validator,
)
from tests.test_turn_envelope import _full_decision

POLICY = {
    "mode": "multi_subagent",
    "spawn_allowed": True,
    "max_children": 2,
    "model_config": {"model": "example-small", "reasoning_effort": "max"},
}
SCOPE = {
    "goal_id": "fixture-goal",
    "agent_id": "codex-fixture",
    "todo_id": "todo_fixture0001",
}


def context(phase="before_plan"):
    return project_agent_context(phase=phase, scope=SCOPE, orchestration=POLICY)


@pytest.mark.parametrize("enabled", [True, False])
def test_live_decision_provides_planning_context_with_one_todo(tmp_path, enabled):
    status = quota_status_payload(
        goal_id=SCOPE["goal_id"],
        status="active",
        recommended_action="Inspect evidence",
        agent_todo_items=[
            {
                "todo_id": SCOPE["todo_id"],
                "index": 1,
                "text": "Inspect evidence",
                "status": "open",
                "priority": "P1",
                "role": "agent",
                "task_class": "advancement_task",
            }
        ],
        coordination={"registered_agents": [SCOPE["agent_id"]]},
        goal_extra={"spawn_policy": {**POLICY, "spawn_allowed": enabled}},
    )
    packet = build_live_quota_should_run_decision(
        status,
        goal_id=SCOPE["goal_id"],
        agent_id=SCOPE["agent_id"],
        available_capabilities=["shell", "subagent_spawn"],
        include_scheduler_detail=False,
        codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json",
        runtime_root=tmp_path / "runtime",
        scheduler_execution_context={
            "host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop",
            "execution_mode": "interactive",
        },
    )
    assert "task_orchestration_contract" not in packet
    injected = packet["interaction_contract"].get("agent_context")
    assert bool(injected) is enabled
    if enabled:
        assert injected["phase"] == "before_plan"
        assert injected["scope"] == SCOPE


def test_signed_envelope_preserves_context_and_detects_changed_guidance():
    decision = _full_decision()
    baseline = build_turn_envelope(decision)
    assert "agent_context" not in baseline
    decision["interaction_contract"]["agent_context"] = context()
    envelope = build_turn_envelope(decision)
    assert envelope["agent_context"] == context()
    assert envelope["action_signature"]["matches"] is True
    assert envelope["compaction"]["within_budget"] is True
    signature = turn_envelope_action_signature_document(envelope)
    assert signature["coverage"] == "turn_envelope_action_dimensions_v4"
    tampered = deepcopy(envelope)
    tampered["agent_context"]["contributions"][0]["guidance"] = ["Skip validation."]
    assert turn_envelope_action_signature_document(tampered) != signature


def test_large_envelope_retains_bounded_context_without_a_full_decision_read():
    decision = _full_decision()
    decision["goal_boundary"] = {"execution_profile": {"padding": "x" * 2500}}
    assert build_turn_envelope(decision)["compaction"]["within_budget"] is True
    expected = context()
    decision["interaction_contract"]["agent_context"] = expected
    envelope = build_turn_envelope(decision)
    assert envelope["agent_context"] == expected
    assert "detail_ref" not in envelope["agent_context"]
    assert envelope["compaction"]["budget_bytes"] == 8192
    assert envelope["action_signature"]["matches"] is True
    original_signature = turn_envelope_action_signature_document(envelope)
    decision["interaction_contract"]["agent_context"]["contributions"][0][
        "guidance"
    ] = ["Changed"]
    assert turn_envelope_action_signature_document(
        build_turn_envelope(decision)
    ) != original_signature


@pytest.mark.parametrize("legacy_reference", [False, True])
@pytest.mark.parametrize("provider_failure", [False, True])
def test_shared_host_delivers_signed_context_and_original_legacy_detail(legacy_reference, provider_failure):
    from loopx.control_plane.turn_driver.host_candidate import extract_turn_authority, render_prompt

    decision = _full_decision()
    full_context = context()
    if provider_failure:
        full_context["failures"] = [{"hook_id": "fixture.unavailable", "code": "context_provider_failed"}]
    decision["interaction_contract"]["agent_context"] = full_context
    envelope = build_turn_envelope(decision, captured_decision_path="private capture/decision.json")
    if legacy_reference:
        # A supported historical carrier: delivery=projected also occurs on
        # complete context; only detail_ref denotes an omitted contribution.
        envelope["agent_context"] = {
            **{k: full_context[k] for k in ("schema_version", "phase", "scope", "target", "authority", "delivery")},
            "content_hash": "sha256:" + sha256(json.dumps(full_context, ensure_ascii=False,
                sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "detail_ref": "full_decision.interaction_contract.agent_context",
            "instruction": "Read original capability context before planning.",
        }
        signature = turn_envelope_action_signature_document(envelope)
        digest = "sha256:" + sha256(json.dumps(signature, ensure_ascii=False,
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        envelope["action_signature"].update(source_hash=digest, envelope_hash=digest)
    authority = extract_turn_authority({"turn_envelope": envelope})
    assert authority["agent_context"] == envelope["agent_context"]
    prompt = render_prompt(authority)
    assert json.dumps(envelope["agent_context"], ensure_ascii=False,
                      sort_keys=True, separators=(",", ":")) in prompt
    # The captured source accompanies current inline work context as well as
    # historical context references; both retain the original signed carrier.
    assert authority["detail_ref"] == envelope["detail_ref"]
    assert authority["action_signature"] == envelope["action_signature"]
    invalid = deepcopy(envelope)
    invalid["agent_context"]["scope"]["agent_id"] = "different-agent"
    with pytest.raises(ValueError, match="action signature"):
        extract_turn_authority({"turn_envelope": invalid})


def test_managed_request_and_return_carry_parent_context_without_fabricating_completion():
    envelope = _adaptive_envelope()
    envelope["agent_context"] = context()
    envelope["boundary"] = {"orchestration": POLICY}
    plan = build_loopx_turn_plan(
        envelope, host="codex-cli", execution_mode="interactive-visible"
    )
    request = build_loopx_turn_host_request(plan)
    assert request["turn_envelope"]["agent_context"] == context()
    assert request["delegation_context"]["phase"] == "before_delegate"
    assert request["delegation_context"]["target"] == "coordinator"
    normalized, errors = {}, []
    observe_subagent_host_result(plan, {}, normalized, errors)
    assert not errors
    returned = normalized["agent_context"]
    assert returned["phase"] == "after_delegate_result"
    assert returned["delivery"] == "projected"
    facts = returned["contributions"][0]["facts"]
    assert facts["reconciliation_counts"]["observed"] == 0
    assert facts["reconciliation_counts"]["incomplete"] > 0
    # The durable host-result journal replays exactly the same context, with no new state.
    assert (
        subagent_execution_payload_projection({"host_result": normalized})[
            "agent_context"
        ]
        == returned
    )
    assert subagent_execution_payload_projection({"host_result": {}}) == {}


def test_managed_return_does_not_relabel_before_plan_receipts_as_fresh():
    before = project_agent_context(
        phase="before_plan",
        scope=SCOPE,
        orchestration=POLICY,
        observations={
            "delegation_context": {
                "schema_version": "loopx_delegation_context_v0",
                "configuration_state": "ready",
                "observed_at": "2026-09-19T00:00:00+00:00",
                "authorized_count": 1,
                "projected_count": 1,
                "routes": [
                    {
                        "binding_id": "review",
                        "agent_id": "reviewer",
                        "todo_id": "todo-review",
                        "runtime_id": "generic-cli",
                        "readiness": "unknown",
                    }
                ],
                "operation_receipts": {"observed": 1, "running": 1},
            }
        },
    )
    envelope = {"agent_context": before, "boundary": {"orchestration": POLICY}}

    returned = envelope_agent_context(
        envelope,
        phase="after_delegate_result",
        observations={"reconciliation_counts": {"observed": 1, "completed": 1}},
    )

    facts = returned["contributions"][0]["facts"]
    assert facts["reconciliation_counts"] == {"observed": 1, "completed": 1}
    assert "delegation_receipts" not in facts


def test_host_prompt_and_durable_journal_replay_keep_lifecycle_context(tmp_path):
    from loopx.control_plane.turn_driver import run_loopx_turn_once
    from loopx.control_plane.turn_driver.codex_cli import _prompt

    envelope = _adaptive_envelope()
    envelope["agent_context"] = context()
    envelope["boundary"] = {"orchestration": POLICY}
    plan = build_loopx_turn_plan(
        envelope, host="codex-cli", execution_mode="isolated-headless"
    )
    calls = {"writeback": 0, "spend": 0, "scheduler": 0, "host": 0}
    writeback, spend, scheduler = _callbacks(calls)

    def host_runner(request):
        calls["host"] += 1
        prompt = _prompt(request)
        assert '"before_plan"' in prompt
        assert '"before_delegate"' in prompt
        return _host_result(plan)

    kwargs = dict(
        plan=plan,
        runtime_root=tmp_path,
        project=tmp_path,
        timeout_seconds=10,
        goal_id="fixture-goal",
        execute=True,
        host_runner=host_runner,
        task_validator=_passing_validator,
        writeback=writeback,
        spend=spend,
        scheduler=scheduler,
    )
    first = run_loopx_turn_once(**kwargs)
    assert first["ok"] is True
    persisted = _journal(tmp_path)["host_result"]["agent_context"]
    assert persisted["phase"] == "after_delegate_result"
    replay = run_loopx_turn_once(**kwargs)
    assert replay["agent_context"] == first["agent_context"] == persisted
    assert calls == {"host": 1, "writeback": 1, "spend": 1, "scheduler": 1}


@pytest.mark.parametrize(
    "phase", ["before_plan", "before_delegate", "after_delegate_result"]
)
def test_native_cli_is_read_only_and_does_not_claim_native_receipts(tmp_path, phase):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": SCOPE["goal_id"],
                        "repo": str(tmp_path),
                        "status": "active",
                        "registered_agents": [SCOPE["agent_id"]],
                        "spawn_policy": POLICY,
                    }
                ],
            }
        )
    )
    before = registry.read_bytes()
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "agent-context",
        "--goal-id",
        SCOPE["goal_id"],
        "--agent-id",
        SCOPE["agent_id"],
        "--phase",
        phase,
        "--format",
        "json",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    assert payload["read_only"] is True
    assert payload["agent_context"]["phase"] == phase
    assert payload["host_receipts_observed"] is False
    assert payload["host_receipts_scope"] == "native_tool_input"
    assert "host_capacity_observed" not in payload
    assert "host_capacity_scope" not in payload
    assert registry.read_bytes() == before
    command[command.index(SCOPE["agent_id"])] = "unregistered"
    rejected = subprocess.run(command, capture_output=True, text=True)
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["ok"] is False


def test_native_cli_projects_typed_capacity_exhaustion_without_raw_error(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "goals": [
                    {
                        "id": SCOPE["goal_id"],
                        "repo": str(tmp_path),
                        "status": "active",
                        "registered_agents": [SCOPE["agent_id"]],
                        "spawn_policy": POLICY,
                    }
                ],
            }
        )
    )
    before = registry.read_bytes()
    command = [
        sys.executable,
        "-m",
        "loopx.cli",
        "--registry",
        str(registry),
        "agent-context",
        "--goal-id",
        SCOPE["goal_id"],
        "--agent-id",
        SCOPE["agent_id"],
        "--phase",
        "after_delegate_result",
        "--native-child-operation",
        "spawn",
        "--native-child-outcome",
        "agent_thread_limit_reached",
        "--native-child-count",
        "1",
        "--format",
        "json",
    ]

    result = subprocess.run(command, capture_output=True, text=True, check=True)
    payload = json.loads(result.stdout)
    [contribution] = payload["agent_context"]["contributions"]
    observation = contribution["facts"]["native_host_capacity"]
    assert payload["host_capacity_observed"] is True
    assert payload["host_receipts_observed"] is False
    assert observation["reason_code"] == "agent_thread_limit_reached"
    assert observation["retry_same_turn"] is False
    assert observation["recovery_actions"] == [
        "continue_parent_work",
        "defer_unlaunched_children",
        "retry_after_capacity_change",
    ]
    assert registry.read_bytes() == before

    invalid = subprocess.run(
        [
            *command[: command.index("after_delegate_result")],
            "before_plan",
            *command[command.index("after_delegate_result") + 1 :],
        ],
        capture_output=True,
        text=True,
    )
    assert invalid.returncode == 1
    assert "after_delegate_result" in json.loads(invalid.stdout)["error"]

    disabled_payload = json.loads(registry.read_text())
    disabled_payload["goals"][0]["spawn_policy"]["spawn_allowed"] = False
    registry.write_text(json.dumps(disabled_payload))
    disabled_before = registry.read_bytes()
    disabled = subprocess.run(command, capture_output=True, text=True)
    assert disabled.returncode == 1
    assert "enabled multi_subagent" in json.loads(disabled.stdout)["error"]
    assert registry.read_bytes() == disabled_before
