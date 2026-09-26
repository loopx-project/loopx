from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...control_plane.runtime.public_safety import public_safe_compact_text


REWARD_MEMORY_ARCHITECTURE_SCHEMA_VERSION = "reward_memory_architecture_v0"
REWARD_MEMORY_ROUTE_SCHEMA_VERSION = "reward_memory_pilot_meta_route_v0"

MEMORY_CLASS_IDS = (
    "run_bound_reward",
    "hard_policy",
    "soft_preference",
    "procedural_experience",
    "working_context",
)
BEHAVIOR_STATUSES = {"bug_confirmed", "by_design", "uncertain"}
EDGE_CASE_COMPLEXITIES = {"low", "medium", "high"}


def _memory_classes() -> list[dict[str, Any]]:
    return [
        {
            "class_id": "run_bound_reward",
            "purpose": "Judge one exact run or route outcome.",
            "source_kinds": ["explicit_human_reward"],
            "scope_floor": ["goal_id", "run_id"],
            "authority": "outcome_evidence_only",
            "future_influence": "candidate_derivation_and_activation_policy_required",
            "durability": "append_only_run_overlay",
            "supersession": "append_correction_without_rewriting_the_judged_run",
            "revocation": "append_revocation_reference",
            "expiry": "none_by_default",
            "privacy": "compact_reason_only_no_raw_chat",
        },
        {
            "class_id": "hard_policy",
            "purpose": (
                "Constrain or veto actions within an independently verified "
                "actor and repository authority scope."
            ),
            "source_kinds": [
                "explicit_user_boundary",
                "repository_policy",
                "operator_gate",
                "authority_checkpoint",
                "verified_contributor_feedback_inference",
            ],
            "excluded_sources": [
                "recalled_semantic_memory_without_verified_actor_and_scope_binding",
                "provider_soul_or_boundary_text_without_verified_action_authority",
            ],
            "scope_floor": ["project_or_goal", "action_or_surface"],
            "authority": "constraint_or_veto_within_verified_actor_scope",
            "future_influence": (
                "reasoned_application_with_provenance_and_conflict_checks"
            ),
            "durability": "durable_until_superseded_revoked_or_expired",
            "supersession": ("explicit_or_stronger_newer_verified_policy_reference"),
            "revocation": "explicit_authorized_revocation",
            "expiry": "required_for_temporary_policy",
            "privacy": "private_by_default_unless_rewritten_public_safe",
            "derivation": {
                "policy_content_may_be_inferred": True,
                "authority_scope_must_be_verified_independently": True,
                "eligible_actor_roles": [
                    "verified_repository_core_contributor",
                    "verified_project_owner_or_operator",
                ],
                "evidence_inputs": [
                    "explicit_feedback",
                    "selected_option",
                    "verified_scoped_preference",
                    "current_artifact_verified_procedural_experience",
                    "repeated_accepted_or_rejected_outcome",
                    "maintainer_correction",
                ],
                "activation_rule": (
                    "may_activate_with_compact_provenance_when_content_and_scope_"
                    "are_unambiguous_and_no_higher_authority_conflict_exists"
                ),
                "cannot_derive": [
                    "credentials",
                    "new_write_publish_or_production_scope",
                    "current_gate_transition_without_source_receipt",
                    "cross_user_cross_agent_or_cross_repository_authority",
                ],
            },
        },
        {
            "class_id": "soft_preference",
            "purpose": "Rank or shape an output without granting permission.",
            "source_kinds": [
                "explicit_feedback",
                "selected_option",
                "reviewed_preference_candidate",
            ],
            "scope_floor": ["workspace_or_project", "module_surface"],
            "authority": "advisory_ranking_or_rewrite",
            "future_influence": "allowed_only_on_module_owned_surfaces",
            "durability": "durable_after_explicit_review",
            "supersession": "newer_scoped_preference_or_conflict_retirement",
            "revocation": "operator_edit_reject_or_retire",
            "expiry": "recommended_for_unreinforced_or_time_sensitive_items",
            "privacy": "provider_owned_content_compact_receipts_only",
        },
        {
            "class_id": "procedural_experience",
            "experience_subtypes": [
                "trajectory",
                "distilled_experience",
                "architectural_experience",
            ],
            "purpose": (
                "Preserve checkout-verified repair, validation, and architecture "
                "lessons with applicability boundaries."
            ),
            "source_kinds": [
                "revision_stamped_execution_trajectory",
                "validated_outcome",
                "maintainer_correction",
                "accepted_or_rejected_change",
                "reviewed_learning_card",
            ],
            "excluded_sources": [
                "raw_chat_or_tool_transcript",
                "evaluation_case_without_reviewed_distillation",
            ],
            "scope_floor": [
                "repository_or_project",
                "module_or_component",
                "observed_revision",
                "applicability_boundary",
            ],
            "authority": "advisory_until_current_artifact_verification",
            "future_influence": "diagnosis_scope_routing_or_validation_only_after_verification",
            "durability": "revision_stamped_and_supersedable",
            "supersession": "newer_verified_revision_lineage",
            "revocation": "refute_quarantine_or_retire",
            "expiry": "stale_when_revision_or_source_truth_diverges",
            "privacy": "distilled_public_safe_fact_no_raw_evidence",
        },
        {
            "class_id": "working_context",
            "context_subtypes": [
                "fresh_execution_context",
                "session_working_memory",
            ],
            "subtype_status": {
                "fresh_execution_context": "existing_loopx_control_plane_capability",
                "session_working_memory": "provider_backed_capability_reuse",
            },
            "purpose": (
                "Carry fresh execution state or a revisioned session-continuation "
                "summary without promoting either into reusable policy."
            ),
            "source_kinds": [
                "current_registry_state",
                "active_todo",
                "current_checkout",
                "bounded_tool_observation",
                "versioned_session_archive_overview",
            ],
            "scope_floor": [
                "current_execution_or_session",
                "exact_source_revision_archive_or_timestamp",
            ],
            "authority": "fresh_execution_context_not_long_term_memory",
            "future_influence": (
                "current_execution_or_session_continuation_only_never_action_authority"
            ),
            "durability": (
                "fresh_context_expires_quickly_session_summary_is_archive_revision_bound"
            ),
            "supersession": "next_fresh_read_or_newer_completed_archive",
            "revocation": "source_invalidation",
            "expiry": "required_for_fresh_context_session_summary_stales_on_newer_archive",
            "privacy": "never_promote_raw_chat_logs_credentials_or_local_paths",
        },
    ]


def _openviking_alignment() -> dict[str, Any]:
    return {
        "provider_role": "context_database_not_action_authority",
        "content_source_of_truth": "agfs_content",
        "index_role": "retrieval_reference_not_content_source_of_truth",
        "class_mapping": {
            "run_bound_reward": (
                "loopx_overlay_no_direct_openviking_memory_equivalent"
            ),
            "hard_policy": (
                "explicit_or_verified_contributor_derived_policy_content_bound_to_"
                "existing_loopx_repository_operator_or_user_authority"
            ),
            "soft_preference": "reviewed_openviking_preference_candidate",
            "procedural_experience": {
                "trajectory": "openviking_trajectories_add_only_operation_contract",
                "distilled_experience": (
                    "openviking_experiences_upsert_and_supersedes"
                ),
                "architectural_experience": (
                    "reviewed_revision_stamped_loopx_distillation"
                ),
            },
            "working_context": {
                "fresh_execution_context": (
                    "existing_loopx_registry_todo_checkout_and_tool_observation_"
                    "no_new_reward_memory_store"
                ),
                "session_working_memory": (
                    "openviking_archive_overview_bound_to_session_and_archive_revision"
                ),
            },
        },
        "non_instruction_artifacts": {
            "openviking_cases": "training_and_evaluation_fixture_not_executable_memory",
        },
        "scope_boundaries": [
            "account",
            "user",
            "peer",
            "session",
            "repository_revision",
        ],
        "health_states_must_remain_distinct": [
            "corpus_present",
            "index_present",
            "retrieval_query_succeeded",
            "result_readback_verified",
            "memory_applied_with_receipt",
        ],
        "known_stage_1_check": (
            "codex_auto_recall_may_exclude_experiences_even_when_the_corpus_exists"
        ),
    }


def build_reward_memory_architecture_packet() -> dict[str, Any]:
    """Return the Stage-0 classification and precedence compatibility contract."""

    return {
        "ok": True,
        "schema_version": REWARD_MEMORY_ARCHITECTURE_SCHEMA_VERSION,
        "status": "design_contract",
        "memory_classes": _memory_classes(),
        "required_record_fields": [
            "class_id",
            "source",
            "scope",
            "authority",
            "confidence",
            "lifecycle_state",
            "supersession",
            "revocation",
            "expiry",
            "privacy",
        ],
        "field_contracts": {
            "source": ["source_kind", "source_ref", "actor_ref", "observed_at"],
            "scope": [
                "workspace_or_user",
                "project_or_repository",
                "module_or_surface",
                "revision_or_time_boundary",
            ],
            "authority": ["evidence", "advisory", "constraint", "veto"],
            "confidence": {
                "levels": ["low", "medium", "high"],
                "basis_required": True,
                "may_increase_authority": False,
            },
            "lifecycle": [
                "state",
                "supersedes_refs",
                "revoked_by_ref",
                "expires_at",
                "retired_reason",
            ],
            "privacy": [
                "visibility",
                "retention_class",
                "raw_content_captured",
            ],
        },
        "lifecycle_states": [
            "observed",
            "candidate",
            "active",
            "superseded",
            "revoked",
            "expired",
            "retired",
        ],
        "routing_contract": {
            "mode": "model_reasoning_inside_deterministic_safety_guards",
            "deterministic_guards": [
                "verified_actor_and_action_authority_scope",
                "project_surface_and_revision_scope",
                "privacy_and_external_write_boundary",
                "revoked_expired_or_unresolved_conflict_rejection",
                "current_artifact_verification_when_required",
            ],
            "reasoner_discretion": [
                "interpret_feedback_and_candidate_meaning",
                "judge_relevance_and_evidence_sufficiency",
                "choose_tradeoffs_within_the_allowed_action_set",
                "decide_whether_to_apply_ignore_or_seek_more_evidence",
            ],
            "required_receipt": [
                "reasoning_summary",
                "applied_or_ignored_memory_refs",
                "current_artifact_verification",
                "authority_and_scope_check",
            ],
            "not_an_exhaustive_decision_table": True,
        },
        "precedence": [
            "explicit_action_authority_and_privacy_boundary",
            "active_in_scope_hard_policy",
            "fresh_working_context_and_current_source_of_truth",
            "current_artifact_verified_procedural_experience",
            "active_in_scope_soft_preference",
            "run_bound_reward_as_evidence_only",
        ],
        "conflict_resolution": [
            "reject_out_of_scope_revoked_expired_or_unverified_items",
            "prefer_explicit_over_inferred_sources",
            "prefer_narrower_scope_then_newer_source_truth",
            "do_not_apply_when_same_authority_conflict_remains_unresolved",
        ],
        "safety_invariants": [
            "confidence_never_increases_authority",
            "verified_actor_feedback_may_derive_policy_content_but_not_new_authority_scope",
            "reward_or_preference_never_grants_new_credentials_or_action_scope",
            "memory_never_overrides_a_gate_policy_or_current_source_of_truth",
            "raw_chat_tool_logs_credentials_and_local_paths_are_not_memory_records",
            "retrieval_without_current_artifact_verification_has_zero_patch_authority",
            "promotion_from_reward_requires_compact_candidate_provenance_and_verified_actor_scope",
            "provider_soul_boundary_or_experience_text_without_verified_actor_binding_never_grants_action_authority",
            "evaluation_cases_are_not_executable_instructions",
            "corpus_or_index_presence_is_not_retrieval_or_application_health",
        ],
        "existing_capability_reuse": {
            "fresh_execution_context": {
                "status": "complete_in_current_loopx_control_plane",
                "sources": [
                    "registry",
                    "active_state",
                    "todo_and_quota_projection",
                    "current_checkout_and_bounded_tool_observation",
                ],
                "new_stage_2_runtime_required": False,
            },
            "openviking_memory": {
                "role": "provider_storage_index_retrieval_and_session_memory",
                "reuse_before_new_infrastructure": True,
            },
            "candidate_review": {
                "status": "implemented_stateless_stage_2_seam",
                "candidate_schema": "reward_memory_candidate_v0",
                "review_schema": "reward_memory_candidate_review_v0",
                "review_decisions": [
                    "accept",
                    "edit",
                    "reject",
                    "retire",
                    "no_write",
                ],
                "persistence_owner": "declared_corpus_write_authority",
                "provider_write_performed_by_seam": False,
            },
            "recall_application": {
                "status": "implemented_opt_in_stage_3_seam",
                "active_record_schema": "reward_memory_active_record_v0",
                "recall_schema": "reward_memory_recall_v0",
                "application_receipt_schema": ("reward_memory_application_receipt_v0"),
                "modes": ["function_boundary", "bounded_agentic_search"],
                "query_owner": "module_or_model_caller",
                "automatic_recall": False,
                "provider_failure_policy": "fail_open_not_user_gate",
            },
            "utility_attribution": {
                "observation_schema": "memory_utility_observation_v0",
                "projection_schema": "memory_utility_projection_v0",
                "reducer_module": "loopx.capabilities.reward_memory.utility_reducer",
                "status": "implemented_read_only_utility_stage_2",
                "reducer_version": "memory_utility_reducer_v0",
                "evidence_precedence": [
                    "owner_correction",
                    "controlled_replay",
                    "deterministic_effect",
                    "evaluator_inference",
                    "insufficient",
                ],
                "item_set_attribution_separate": True,
                "duplicate_observation_replay_is_noop": True,
                "ranking_influence": False,
                "provider_writeback": False,
                "main_lane_side_effects": False,
            },
        },
        "provider_alignment": {"openviking": _openviking_alignment()},
        "pilot_meta_delegation": {
            "schema_version": "reward_memory_pilot_meta_delegation_v0",
            "pilot_requires": [
                "confirmed_bug_semantics",
                "single_bounded_surface",
                "no_semantic_contract_change",
                "no_generic_boundary_for_specific_policy",
                "named_reproduction",
                "named_validation",
                "low_or_medium_edge_case_complexity",
                "all_relevance_gated_evidence_present",
            ],
            "meta_triggers": [
                "by_design_or_uncertain_semantics",
                "semantic_contract_change",
                "cross_surface_change",
                "generic_boundary_for_specific_policy",
                "high_edge_case_complexity",
            ],
            "relevance_gated_evidence": {
                "effect": "always",
                "ux": "when_user_visible_behavior_changes",
                "benchmark": "when_retrieval_or_memory_quality_is_claimed",
                "performance_cost": "when_hot_path_or_storage_behavior_changes",
            },
            "decisions": ["pilot_fix", "meta_design_gate", "hold_for_evidence"],
            "no_cross_agent_authority": True,
        },
        "stage_boundaries": {
            "stage_0": "classification_precedence_and_delegation_only",
            "stage_1": "corpus_registry_ownership_and_retrieval_health",
            "stage_2": (
                "implemented_thin_candidate_and_review_seam_no_second_store_"
                "scheduler_or_recall"
            ),
            "stage_3": (
                "implemented_opt_in_reasoning_mediated_cross_module_recall_"
                "and_application"
            ),
            "stage_4": "evaluation_harness_and_release_gate",
            "stage_5": "bounded_dogfood_and_operator_controls",
            "utility_stage_1": "typed_post_outcome_observation_contract",
            "utility_stage_2": "implemented_idempotent_read_only_projection",
        },
        "external_writes_performed": False,
        "raw_memory_captured": False,
    }


def pr_3237_regression_observation() -> dict[str, Any]:
    """Public regression fixture derived from the terminal PR review outcome."""

    return {
        "case_ref": "https://github.com/volcengine/OpenViking/pull/3237",
        "behavior_status": "by_design",
        "surface_count": 2,
        "semantic_contract_change": True,
        "generic_boundary_for_specific_policy": True,
        "user_visible_behavior_change": True,
        "hot_path_or_storage_change": True,
        "retrieval_or_memory_quality_claim": False,
        "edge_case_complexity": "high",
        "named_reproduction": True,
        "named_validation": True,
        "effect_evidence": False,
        "ux_evidence": False,
        "benchmark_evidence": False,
        "performance_evidence": False,
    }


def _boolean(observation: Mapping[str, Any], key: str) -> bool:
    value = observation.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def build_reward_memory_route_packet(
    observation: Mapping[str, Any],
) -> dict[str, Any]:
    """Check a bounded issue-fix guard fixture, not the live reasoning router."""

    behavior_status = str(observation.get("behavior_status") or "").strip()
    if behavior_status not in BEHAVIOR_STATUSES:
        raise ValueError(f"behavior_status must be one of {sorted(BEHAVIOR_STATUSES)}")
    complexity = str(observation.get("edge_case_complexity") or "").strip()
    if complexity not in EDGE_CASE_COMPLEXITIES:
        raise ValueError(
            f"edge_case_complexity must be one of {sorted(EDGE_CASE_COMPLEXITIES)}"
        )
    surface_count = observation.get("surface_count")
    if isinstance(surface_count, bool) or not isinstance(surface_count, int):
        raise ValueError("surface_count must be an integer")
    if surface_count < 1 or surface_count > 20:
        raise ValueError("surface_count must be between 1 and 20")

    boolean_keys = (
        "semantic_contract_change",
        "generic_boundary_for_specific_policy",
        "user_visible_behavior_change",
        "hot_path_or_storage_change",
        "retrieval_or_memory_quality_claim",
        "named_reproduction",
        "named_validation",
        "effect_evidence",
        "ux_evidence",
        "benchmark_evidence",
        "performance_evidence",
    )
    values = {key: _boolean(observation, key) for key in boolean_keys}
    meta_reasons: list[str] = []
    if behavior_status != "bug_confirmed":
        meta_reasons.append(f"semantics_{behavior_status}")
    if values["semantic_contract_change"]:
        meta_reasons.append("semantic_contract_change")
    if surface_count > 1:
        meta_reasons.append("cross_surface_change")
    if values["generic_boundary_for_specific_policy"]:
        meta_reasons.append("generic_boundary_for_specific_policy")
    if complexity == "high":
        meta_reasons.append("high_edge_case_complexity")
    required_evidence = {
        "effect_evidence": True,
        "ux_evidence": values["user_visible_behavior_change"],
        "benchmark_evidence": values["retrieval_or_memory_quality_claim"],
        "performance_evidence": values["hot_path_or_storage_change"],
    }
    missing_required_evidence = [
        evidence
        for evidence, required in required_evidence.items()
        if required and not values[evidence]
    ]

    missing_pilot_evidence: list[str] = list(missing_required_evidence)
    if not values["named_reproduction"]:
        missing_pilot_evidence.append("named_reproduction")
    if not values["named_validation"]:
        missing_pilot_evidence.append("named_validation")

    if meta_reasons:
        decision = "meta_design_gate"
        reasons = meta_reasons + [
            f"missing_{item}" for item in missing_required_evidence
        ]
    elif missing_pilot_evidence:
        decision = "hold_for_evidence"
        reasons = [f"missing_{item}" for item in missing_pilot_evidence]
    else:
        decision = "pilot_fix"
        reasons = [
            "confirmed_bug_semantics",
            "single_bounded_surface",
            "no_semantic_contract_change",
            "no_generic_boundary_for_specific_policy",
            f"edge_case_complexity_{complexity}",
            "reproduction_validation_and_relevant_evidence_present",
        ]

    raw_case_ref = str(observation.get("case_ref") or "manual_observation")
    if raw_case_ref.startswith(("/", "~")):
        raise ValueError("case_ref must be compact and public-safe")
    case_ref = public_safe_compact_text(raw_case_ref, limit=240)
    if not case_ref:
        raise ValueError("case_ref must be compact and public-safe")

    return {
        "ok": True,
        "schema_version": REWARD_MEMORY_ROUTE_SCHEMA_VERSION,
        "case_ref": case_ref,
        "decision": decision,
        "reason_codes": reasons,
        "required_evidence": [
            key.removesuffix("_evidence")
            for key, required in required_evidence.items()
            if required
        ],
        "missing_required_evidence": [
            key.removesuffix("_evidence") for key in missing_required_evidence
        ],
        "pilot_authorized": decision == "pilot_fix",
        "meta_review_required": decision == "meta_design_gate",
        "route_check_role": "deterministic_guard_fixture_not_live_reasoner",
        "live_reasoning_required": True,
        "memory_patch_authority": False,
        "external_write_authorized": False,
        "raw_issue_or_memory_captured": False,
    }
