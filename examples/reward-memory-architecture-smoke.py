#!/usr/bin/env python3
"""Smoke-test the durable reward-memory classification and routing contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from loopx.capabilities.reward_memory import (  # noqa: E402
    build_reward_memory_architecture_packet,
    build_reward_memory_route_packet,
    pr_3237_regression_observation,
)


def run_cli(*args: str) -> dict[str, object]:
    completed = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=REPO_ROOT,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    return json.loads(completed.stdout)


def main() -> int:
    architecture = build_reward_memory_architecture_packet()
    classes = {item["class_id"]: item for item in architecture["memory_classes"]}
    assert set(classes) == {
        "run_bound_reward",
        "hard_policy",
        "soft_preference",
        "procedural_experience",
        "working_context",
    }
    assert classes["run_bound_reward"]["future_influence"] == (
        "candidate_derivation_and_activation_policy_required"
    )
    hard_policy = classes["hard_policy"]
    assert hard_policy["authority"] == (
        "constraint_or_veto_within_verified_actor_scope"
    )
    assert hard_policy["derivation"]["policy_content_may_be_inferred"] is True
    assert (
        hard_policy["derivation"]["authority_scope_must_be_verified_independently"]
        is True
    )
    assert (
        "verified_repository_core_contributor"
        in hard_policy["derivation"]["eligible_actor_roles"]
    )
    assert classes["soft_preference"]["authority"] == ("advisory_ranking_or_rewrite")
    assert classes["procedural_experience"]["authority"] == (
        "advisory_until_current_artifact_verification"
    )
    assert classes["procedural_experience"]["experience_subtypes"] == [
        "trajectory",
        "distilled_experience",
        "architectural_experience",
    ]
    assert classes["working_context"]["context_subtypes"] == [
        "fresh_execution_context",
        "session_working_memory",
    ]
    assert classes["working_context"]["durability"] == (
        "fresh_context_expires_quickly_session_summary_is_archive_revision_bound"
    )
    assert classes["working_context"]["subtype_status"]["fresh_execution_context"] == (
        "existing_loopx_control_plane_capability"
    )
    assert architecture["precedence"][0] == (
        "explicit_action_authority_and_privacy_boundary"
    )
    assert architecture["stage_boundaries"]["stage_1"] == (
        "corpus_registry_ownership_and_retrieval_health"
    )
    assert architecture["field_contracts"]["confidence"] == {
        "levels": ["low", "medium", "high"],
        "basis_required": True,
        "may_increase_authority": False,
    }
    assert architecture["routing_contract"]["mode"] == (
        "model_reasoning_inside_deterministic_safety_guards"
    )
    assert architecture["routing_contract"]["not_an_exhaustive_decision_table"]
    assert (
        architecture["existing_capability_reuse"]["fresh_execution_context"][
            "new_stage_2_runtime_required"
        ]
        is False
    )
    candidate_review = architecture["existing_capability_reuse"]["candidate_review"]
    assert candidate_review["status"] == "implemented_stateless_stage_2_seam"
    assert candidate_review["review_decisions"] == [
        "accept",
        "edit",
        "reject",
        "retire",
        "no_write",
    ]
    assert candidate_review["provider_write_performed_by_seam"] is False
    recall_application = architecture["existing_capability_reuse"]["recall_application"]
    assert recall_application["status"] == "implemented_opt_in_stage_3_seam"
    assert recall_application["modes"] == [
        "function_boundary",
        "bounded_agentic_search",
    ]
    assert recall_application["automatic_recall"] is False
    assert recall_application["provider_failure_policy"] == ("fail_open_not_user_gate")
    utility_attribution = architecture["existing_capability_reuse"][
        "utility_attribution"
    ]
    assert utility_attribution["projection_schema"] == "memory_utility_projection_v0"
    assert utility_attribution["reducer_version"] == "memory_utility_reducer_v0"
    assert utility_attribution["duplicate_observation_replay_is_noop"] is True
    assert utility_attribution["item_set_attribution_separate"] is True
    assert utility_attribution["ranking_influence"] is False
    assert architecture["stage_boundaries"]["stage_3"].startswith("implemented_opt_in_")
    assert architecture["stage_boundaries"]["utility_stage_2"].startswith(
        "implemented_idempotent_"
    )
    openviking = architecture["provider_alignment"]["openviking"]
    assert openviking["content_source_of_truth"] == "agfs_content"
    assert openviking["non_instruction_artifacts"]["openviking_cases"] == (
        "training_and_evaluation_fixture_not_executable_memory"
    )
    assert openviking["health_states_must_remain_distinct"][-1] == (
        "memory_applied_with_receipt"
    )

    regression = build_reward_memory_route_packet(pr_3237_regression_observation())
    assert regression["decision"] == "meta_design_gate", regression
    assert regression["pilot_authorized"] is False
    assert regression["route_check_role"] == (
        "deterministic_guard_fixture_not_live_reasoner"
    )
    assert regression["live_reasoning_required"] is True
    assert {
        "semantics_by_design",
        "semantic_contract_change",
        "cross_surface_change",
        "generic_boundary_for_specific_policy",
        "high_edge_case_complexity",
        "missing_effect_evidence",
        "missing_ux_evidence",
        "missing_performance_evidence",
    } <= set(regression["reason_codes"])
    assert regression["required_evidence"] == ["effect", "ux", "performance"]
    assert "benchmark" not in regression["missing_required_evidence"]

    pilot = build_reward_memory_route_packet(
        {
            "behavior_status": "bug_confirmed",
            "surface_count": 1,
            "semantic_contract_change": False,
            "generic_boundary_for_specific_policy": False,
            "user_visible_behavior_change": False,
            "hot_path_or_storage_change": False,
            "retrieval_or_memory_quality_claim": False,
            "edge_case_complexity": "medium",
            "named_reproduction": True,
            "named_validation": True,
            "effect_evidence": True,
            "ux_evidence": False,
            "benchmark_evidence": False,
            "performance_evidence": False,
        }
    )
    assert pilot["decision"] == "pilot_fix", pilot

    hold = build_reward_memory_route_packet(
        {
            "behavior_status": "bug_confirmed",
            "surface_count": 1,
            "semantic_contract_change": False,
            "generic_boundary_for_specific_policy": False,
            "user_visible_behavior_change": False,
            "hot_path_or_storage_change": False,
            "retrieval_or_memory_quality_claim": False,
            "edge_case_complexity": "low",
            "named_reproduction": False,
            "named_validation": True,
            "effect_evidence": True,
            "ux_evidence": False,
            "benchmark_evidence": False,
            "performance_evidence": False,
        }
    )
    assert hold["decision"] == "hold_for_evidence", hold

    quality_hold = build_reward_memory_route_packet(
        {
            "behavior_status": "bug_confirmed",
            "surface_count": 1,
            "semantic_contract_change": False,
            "generic_boundary_for_specific_policy": False,
            "user_visible_behavior_change": False,
            "hot_path_or_storage_change": False,
            "retrieval_or_memory_quality_claim": True,
            "edge_case_complexity": "low",
            "named_reproduction": True,
            "named_validation": True,
            "effect_evidence": True,
            "ux_evidence": False,
            "benchmark_evidence": False,
            "performance_evidence": False,
        }
    )
    assert quality_hold["decision"] == "hold_for_evidence", quality_hold
    assert quality_hold["required_evidence"] == ["effect", "benchmark"]
    assert quality_hold["missing_required_evidence"] == ["benchmark"]

    unsafe = pr_3237_regression_observation() | {"case_ref": "~/private-case"}
    try:
        build_reward_memory_route_packet(unsafe)
    except ValueError as exc:
        assert "public-safe" in str(exc)
    else:
        raise AssertionError("local case_ref should be rejected")

    cli_architecture = run_cli("reward-memory", "architecture")
    assert cli_architecture["schema_version"] == "reward_memory_architecture_v0"
    cli_regression = run_cli("reward-memory", "route-check", "--case", "pr-3237")
    assert cli_regression["decision"] == "meta_design_gate", cli_regression
    assert cli_regression["raw_issue_or_memory_captured"] is False
    print("reward-memory-architecture-smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
