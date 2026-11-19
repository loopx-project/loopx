"""Provider-neutral reward-memory architecture contracts."""
from __future__ import annotations

from importlib import import_module
from typing import Any

# Turn hooks import one owner submodule; they do not select evaluation, dogfood
# or Codex App staging. Keep compatibility exports identical to their owners
# without importing every reward-memory workflow when a submodule loads.
_EXPORTS = {
    "build_reward_memory_architecture_packet": "architecture",
    "build_reward_memory_route_packet": "architecture",
    "pr_3237_regression_observation": "architecture",
    "RewardMemoryFilteredRecallItem": "application",
    "RewardMemoryRecallItem": "application",
    "RewardMemoryRecallSession": "application",
    "apply_reward_memory_recall": "application",
    "build_active_reward_memory_record": "application",
    "build_reward_memory_recall_request": "application",
    "execute_reward_memory_recall": "application",
    "normalize_reward_memory_provider_binding": "application",
    "build_issue_fix_reward_memory_candidate": "candidate_review",
    "build_reward_memory_candidate": "candidate_review",
    "issue_fix_verified_contributor_candidate_fixture": "candidate_review",
    "review_reward_memory_candidate": "candidate_review",
    "run_staged_codex_app_turn_outcome_ingest": "codex_app_outcome",
    "run_staged_codex_app_turn_outcome_ingest_fail_open": "codex_app_outcome",
    "stage_codex_app_turn_outcome_candidate": "codex_app_outcome",
    "stage_codex_app_turn_outcome_candidate_fail_open": "codex_app_outcome",
    "build_reward_memory_corpus_health_packet": "health",
    "reward_memory_health_case": "health",
    "ingest_reward_memory_candidate": "ingestion",
    "normalize_reward_memory_standing_policy": "ingestion",
    "run_reward_memory_evaluation": "evaluation",
    "RewardMemoryDecisionResult": "decision",
    "assess_reward_memory_decision": "decision",
    "run_reward_memory_decision": "decision",
    "build_reward_memory_dogfood_batch": "dogfood",
    "build_reward_memory_dogfood_receipt": "dogfood",
    "build_reward_memory_operator_control": "dogfood",
    "MEMORY_UTILITY_OBSERVATION_SCHEMA_VERSION": "memory_utility",
    "build_reward_memory_utility_observation": "memory_utility",
    "reward_memory_application_receipt_id": "memory_utility",
    "validate_reward_memory_utility_observation": "memory_utility",
    "MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION": "utility_reducer",
    "MEMORY_UTILITY_REDUCER_VERSION": "utility_reducer",
    "UTILITY_MAX": "utility_reducer",
    "UTILITY_MIN": "utility_reducer",
    "build_reward_memory_utility_projection": "utility_reducer",
    "reduce_reward_memory_utility": "utility_reducer",
    "reduce_reward_memory_utility_observations": "utility_reducer",
    "reward_memory_utility_reducer_identity": "utility_reducer",
    "validate_reward_memory_utility_projection": "utility_reducer",
    "build_reward_memory_corpus_registry_packet": "registry",
    "normalize_reward_memory_corpus": "registry",
    "semantic_preference_inventory_to_reward_corpora": "registry",
    "run_reward_memory_automatic_ingest_hook": "runtime_hooks",
    "run_reward_memory_automatic_recall_hook": "runtime_hooks",
    "build_reward_memory_surface_read_authority_checkpoints": "read_authority",
    "reconcile_pending_turn_outcome_ingests": "outcome_lifecycle",
    "reconcile_pending_turn_outcome_ingests_fail_open": "outcome_lifecycle",
    "run_configured_turn_outcome_ingest": "outcome_lifecycle",
    "run_configured_turn_outcome_ingest_fail_open": "outcome_lifecycle",
}


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))

__all__ = [
    "build_reward_memory_architecture_packet",
    "RewardMemoryDecisionResult",
    "assess_reward_memory_decision",
    "run_reward_memory_decision",
    "build_reward_memory_surface_read_authority_checkpoints",
    "RewardMemoryFilteredRecallItem",
    "RewardMemoryRecallItem",
    "RewardMemoryRecallSession",
    "apply_reward_memory_recall",
    "build_active_reward_memory_record",
    "build_issue_fix_reward_memory_candidate",
    "build_reward_memory_candidate",
    "build_reward_memory_corpus_health_packet",
    "build_reward_memory_corpus_registry_packet",
    "build_reward_memory_dogfood_batch",
    "build_reward_memory_dogfood_receipt",
    "build_reward_memory_operator_control",
    "build_reward_memory_utility_observation",
    "build_reward_memory_utility_projection",
    "reduce_reward_memory_utility",
    "reduce_reward_memory_utility_observations",
    "reward_memory_utility_reducer_identity",
    "reward_memory_application_receipt_id",
    "build_reward_memory_route_packet",
    "build_reward_memory_recall_request",
    "execute_reward_memory_recall",
    "ingest_reward_memory_candidate",
    "normalize_reward_memory_provider_binding",
    "normalize_reward_memory_standing_policy",
    "normalize_reward_memory_corpus",
    "MEMORY_UTILITY_OBSERVATION_SCHEMA_VERSION",
    "MEMORY_UTILITY_PROJECTION_SCHEMA_VERSION",
    "MEMORY_UTILITY_REDUCER_VERSION",
    "UTILITY_MIN",
    "UTILITY_MAX",
    "issue_fix_verified_contributor_candidate_fixture",
    "pr_3237_regression_observation",
    "reward_memory_health_case",
    "review_reward_memory_candidate",
    "run_reward_memory_evaluation",
    "run_reward_memory_automatic_ingest_hook",
    "run_reward_memory_automatic_recall_hook",
    "reconcile_pending_turn_outcome_ingests",
    "reconcile_pending_turn_outcome_ingests_fail_open",
    "run_configured_turn_outcome_ingest",
    "run_configured_turn_outcome_ingest_fail_open",
    "run_staged_codex_app_turn_outcome_ingest",
    "run_staged_codex_app_turn_outcome_ingest_fail_open",
    "semantic_preference_inventory_to_reward_corpora",
    "stage_codex_app_turn_outcome_candidate",
    "stage_codex_app_turn_outcome_candidate_fail_open",
    "validate_reward_memory_utility_observation",
    "validate_reward_memory_utility_projection",
]
