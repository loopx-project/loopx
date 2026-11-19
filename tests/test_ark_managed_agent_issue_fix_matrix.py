from __future__ import annotations

import copy
from pathlib import Path

import pytest

from loopx.capabilities.issue_fix.acceptance_loop import (
    build_issue_fix_acceptance_fixture_packet,
)
from loopx.capabilities.issue_fix.feasibility import (
    build_issue_fix_feasibility_packet,
    validate_issue_fix_feasibility_packet,
)
from loopx.heartbeat_prompt import build_heartbeat_prompt
from loopx.host_loop_activation import build_host_loop_activation_packet


@pytest.mark.parametrize(
    ("reproduction_status", "scope_class", "comment_value", "expected_route"),
    [
        ("confirmed", "bounded", "none", "fix_pr"),
        ("blocked", "uncertain", "diagnosis", "comment_only"),
        ("missing", "uncertain", "none", "triage_only"),
    ],
)
def test_representative_intake_routes_remain_public_safe_and_exclusive(
    reproduction_status: str,
    scope_class: str,
    comment_value: str,
    expected_route: str,
) -> None:
    packet = build_issue_fix_feasibility_packet(
        reproduction_status=reproduction_status,
        scope_class=scope_class,
        reproduction_label=(
            "python test_calculator.py"
            if reproduction_status in {"confirmed", "planned"}
            else None
        ),
        validation_label=(
            "python test_calculator.py"
            if reproduction_status in {"confirmed", "planned"}
            else None
        ),
        comment_value=comment_value,
    )

    assert packet["ok"] is True
    assert packet["decision"]["route"] == expected_route
    assert packet["external_writes_performed"] is False
    assert packet["issue_body_captured"] is False
    assert packet["comment_bodies_captured"] is False
    assert packet["raw_logs_captured"] is False
    assert packet["local_paths_captured"] is False


def test_runnable_route_requires_exact_durable_target_key() -> None:
    packet = build_issue_fix_feasibility_packet(
        repo="owner/repo",
        issue_ref="issue_42",
        reproduction_status="confirmed",
        reproduction_label="focused unit repro",
        scope_class="bounded",
        validation_label="focused unit test",
    )
    packet["transition"]["projected_todo"]["target_key"] = "issue-fix:wrong"

    validation = validate_issue_fix_feasibility_packet(packet)

    assert validation["ok"] is False
    assert validation["errors"] == [
        "projected successor target_key must identify the admitted issue",
        "capability execution binding must match the admitted feasibility transition",
    ]


def test_runnable_route_binds_successor_to_persisted_feasibility_authority() -> None:
    packet = build_issue_fix_feasibility_packet(
        repo="owner/repo",
        issue_ref="issue_42",
        reproduction_status="confirmed",
        reproduction_label="focused unit repro",
        scope_class="bounded",
        validation_label="focused unit test",
    )

    binding = packet["capability_execution_binding"]
    projected_todo = packet["transition"]["projected_todo"]
    assert binding == {
        "schema_version": "capability_execution_binding_v0",
        "binding_ref": projected_todo["capability_binding_ref"],
        "capability_id": "issue-fix",
        "authority": {
            "domain_pack": "issue_fix",
            "stream": "feasibility",
            "packet_schema_version": "issue_fix_feasibility_v0",
            "observation_fingerprint": packet["decision"][
                "observation_fingerprint"
            ],
            "route": "fix_pr",
        },
        "todo_contract": {
            "action_kind": projected_todo["action_kind"],
            "target_key": projected_todo["target_key"],
        },
    }

    forged = copy.deepcopy(packet)
    forged["transition"]["projected_todo"]["capability_binding_ref"] = (
        "issue-fix:feasibility-forged"
    )
    validation = validate_issue_fix_feasibility_packet(forged)
    assert validation["ok"] is False
    assert validation["errors"] == [
        "projected successor capability_binding_ref must match the feasibility authority"
    ]


def test_validated_worker_artifact_does_not_claim_goal_host_closure() -> None:
    packet = build_issue_fix_acceptance_fixture_packet()
    artifact = packet["validated_fix_artifact"]
    review_packet = artifact["review_packet"]

    assert packet["ok"] is True
    assert artifact["repro_before"]["passed"] is False
    assert artifact["patch"]["patch_applied"] is True
    assert artifact["validation_after"]["passed"] is True
    assert artifact["fix_artifact_ready"] is True
    assert artifact["pr_review_packet_ready"] is True

    # Worker evidence and host terminal evidence have different owners. A
    # successful repair must never imply that a Goal evaluator reached
    # Satisfied or that the host reached an achieved terminal state.
    assert "goal_terminal_state" not in artifact
    assert "goal_evaluation" not in artifact

    assert review_packet["external_issue_comment_performed"] is False
    assert review_packet["external_pr_created"] is False
    assert review_packet["merge_performed"] is False


def test_one_shot_host_contract_keeps_goal_closure_with_the_host() -> None:
    prompt = build_heartbeat_prompt(
        goal_id="managed-agent-issue-fix-matrix",
        active_state=Path("/workspace/ACTIVE_GOAL_STATE.md"),
        thin=True,
        runtime_profile="ark_managed_agent_goal",
    )
    activation = build_host_loop_activation_packet(
        agent_type="ark-managed-agent",
        goal_id="managed-agent-issue-fix-matrix",
        agent_id="managed-agent",
        registered_agents=["managed-agent"],
    )

    assert prompt["host_contract"]["activation_mode"] == "goal_once"
    assert prompt["host_contract"]["goal_runtime_owns_continuation"] is True
    assert (
        prompt["host_contract"]["goal_lifecycle_scope"]
        == "registered_goal_until_terminal"
    )
    assert prompt["host_contract"]["phase_handoff_allowed"] is False
    assert prompt["host_contract"]["loopx_turn_driver_required"] is False
    assert prompt["host_contract"]["session_state_authoritative"] is False
    assert len(prompt["task_body"]) <= 4_000
    task_body = prompt["task_body"]
    normalized = " ".join(task_body.split())
    assert "Progress is not a new Goal boundary" in normalized
    assert "do not create a new host Goal merely to continue" in normalized
    assert "settlement_plan.ordered_steps" in normalized
    assert "reconstruct static refresh/spend" in normalized
    assert "ambiguous effects need readback/recovery" in normalized
    assert prompt["progress_refresh_state_command"] not in task_body
    assert prompt["quota_spend_command"] not in task_body

    assert activation["activation_method"] == "submit_goal_once"
    assert activation["host_mutation"]["prompt_field"] == "task_body"
    assert activation["host_mutation"]["transport_contract"] == "goal_prompt_v0"
    assert "loopx turn run-once" not in str(activation).lower()
