"""Provider-neutral architecture projection for Material Lifecycle."""

from __future__ import annotations

from .decision_planning import MATERIAL_EXPLORE_INTENT_SCHEMA_VERSION
from .intake import (
    MATERIAL_CANDIDATE_INTAKE_APPLY_RECEIPT_SCHEMA_VERSION,
    MATERIAL_CANDIDATE_INTAKE_PROPOSAL_SCHEMA_VERSION,
    MATERIAL_CANDIDATE_INTAKE_ROLLBACK_RECEIPT_SCHEMA_VERSION,
)
from .inventory import (
    MATERIAL_MIGRATION_PLAN_SCHEMA_VERSION,
    MATERIAL_STORE_INVENTORY_SCHEMA_VERSION,
)
from .lifecycle import MATERIAL_LIFECYCLE_RECEIPT_SCHEMA_VERSION
from .ranking import (
    MATERIAL_RERANK_APPLY_RECEIPT_SCHEMA_VERSION,
    MATERIAL_RERANK_PROPOSAL_SCHEMA_VERSION,
)
from .readable_projection import (
    MATERIAL_READABLE_PROJECTION_RECEIPT_SCHEMA_VERSION,
)
from .ranked_state import (
    MATERIAL_LIFECYCLE_RANKING_APPLY_RECEIPT_SCHEMA_VERSION,
    MATERIAL_RANKED_STATE_REPAIR_APPLY_RECEIPT_SCHEMA_VERSION,
    MATERIAL_RANKED_STATE_REPAIR_PLAN_SCHEMA_VERSION,
)
from .rebuild import (
    MATERIAL_RANKED_ENTRY_REBUILD_APPLY_RECEIPT_SCHEMA_VERSION,
    MATERIAL_RANKED_ENTRY_REBUILD_PLAN_SCHEMA_VERSION,
)
from .settlement import MATERIAL_INTAKE_RANKING_SETTLEMENT_SCHEMA_VERSION

MATERIAL_LIFECYCLE_ARCHITECTURE_SCHEMA_VERSION = "material_lifecycle_architecture_v0"


def build_material_lifecycle_architecture_packet() -> dict[str, object]:
    """Render the default-off Stage-0 capability contract."""

    return {
        "schema_version": MATERIAL_LIFECYCLE_ARCHITECTURE_SCHEMA_VERSION,
        "status": "experimental",
        "capability": {
            "capability_id": "material_lifecycle",
            "scope": "goal",
            "default_enabled": False,
            "creates_authority": False,
            "mutates_core_state": False,
        },
        "contract_schemas": [
            MATERIAL_CANDIDATE_INTAKE_PROPOSAL_SCHEMA_VERSION,
            MATERIAL_CANDIDATE_INTAKE_APPLY_RECEIPT_SCHEMA_VERSION,
            MATERIAL_CANDIDATE_INTAKE_ROLLBACK_RECEIPT_SCHEMA_VERSION,
            MATERIAL_INTAKE_RANKING_SETTLEMENT_SCHEMA_VERSION,
            MATERIAL_STORE_INVENTORY_SCHEMA_VERSION,
            MATERIAL_MIGRATION_PLAN_SCHEMA_VERSION,
            MATERIAL_LIFECYCLE_RECEIPT_SCHEMA_VERSION,
            MATERIAL_RERANK_PROPOSAL_SCHEMA_VERSION,
            MATERIAL_RERANK_APPLY_RECEIPT_SCHEMA_VERSION,
            MATERIAL_RANKED_ENTRY_REBUILD_PLAN_SCHEMA_VERSION,
            MATERIAL_RANKED_ENTRY_REBUILD_APPLY_RECEIPT_SCHEMA_VERSION,
            MATERIAL_READABLE_PROJECTION_RECEIPT_SCHEMA_VERSION,
            MATERIAL_RANKED_STATE_REPAIR_PLAN_SCHEMA_VERSION,
            MATERIAL_RANKED_STATE_REPAIR_APPLY_RECEIPT_SCHEMA_VERSION,
            MATERIAL_LIFECYCLE_RANKING_APPLY_RECEIPT_SCHEMA_VERSION,
            MATERIAL_EXPLORE_INTENT_SCHEMA_VERSION,
        ],
        "sibling_capabilities": {
            "decision_context": (
                "supplies revisioned evidence for bounded rerank proposals"
            ),
            "reward_memory": (
                "stores reviewed reusable lessons, not current material queues"
            ),
            "content_ops": (
                "consumes selected materials for creation, but does not own "
                "candidate or archive truth"
            ),
        },
        "provider_boundaries": {
            "raw_material_store": "private_external_authority",
            "inventory_provider": "read_only_snapshot_and_parse_metadata",
            "migration_adapter": "owner_gated_dual_read_apply_and_rollback",
            "decision_policy": "replaceable_public_safe_evidence_evaluator",
            "exploration_provider": "deferred_provider_neutral_candidate_intake",
            "candidate_intake_adapter": (
                "source_backed_owner_gated_append_apply_and_rollback"
            ),
            "ranking_settlement": (
                "project_classified_completion_receipt_joining_intake_and_ranking"
            ),
        },
        "lifecycle": [
            "snapshot",
            "inventory",
            "candidate",
            "active",
            "archive_or_carryover",
            "bounded_rerank",
            "lossless_ranked_entry_rebuild",
            "bounded_explore_intent",
            "source_backed_candidate_intake",
            "candidate_intake_ranking_settlement",
            "audited_apply",
        ],
        "invariants": [
            "raw_material_and_private_locations_never_enter_public_packets",
            "source_snapshot_and_backup_precede_migration",
            "source_digest_is_unchanged_across_read_only_inspection",
            "legacy_and_new_stores_dual_read_before_owner_gated_cutover",
            "stable_material_refs_survive_archive_and_reactivation",
            "rerank_is_a_bounded_delta_with_protected_items_and_no_change",
            "oversized_ranked_entries_split_instead_of_hiding_members",
            "ranked_entry_rebuild_preserves_exact_coverage_and_unique_membership",
            "ranked_entry_children_have_deterministic_stable_references",
            "readable_projection_preserves_exact_ranked_coverage",
            "readable_projection_receipt_excludes_content_and_private_locations",
            "ranked_summary_repairs_preserve_membership_and_order",
            "lifecycle_rank_removal_preserves_remaining_entry_identity_and_order",
            "ranking_lineage_resolves_by_applied_authority_revision",
            "decision_evidence_is_revision_bound_and_public_safe",
            "explore_intent_is_budgeted_analysis_only_and_has_a_stop_condition",
            "invalid_or_unavailable_policy_fails_open_to_no_change",
            "proposal_and_apply_receipt_remain_separate",
            "candidate_intake_requires_exact_read_content_backing_and_cas",
            "candidate_intake_appends_exactly_one_without_rewriting_existing_records",
            "every_candidate_intake_records_one_ranking_disposition",
            "high_value_candidate_intake_requires_verified_ranked_membership",
            "intake_and_ranking_keep_separate_receipts_and_authority_transitions",
            "automation_prompts_do_not_own_source_lists_or_ranking_rules",
        ],
        "next_stage": ("private_decision_driven_rerank_dogfood_then_owner_gated_apply"),
    }
