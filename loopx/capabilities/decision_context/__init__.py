"""Goal-scoped Decision Context capability contracts."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .architecture import (
        DECISION_CONTEXT_ARCHITECTURE_SCHEMA_VERSION as DECISION_CONTEXT_ARCHITECTURE_SCHEMA_VERSION,
        build_decision_context_architecture_packet as build_decision_context_architecture_packet,
    )
    from .assembler import (
        DECISION_CONTEXT_ASSEMBLY_SCHEMA_VERSION as DECISION_CONTEXT_ASSEMBLY_SCHEMA_VERSION,
        DECISION_CONTEXT_EPHEMERAL_RECALL_SCHEMA_VERSION as DECISION_CONTEXT_EPHEMERAL_RECALL_SCHEMA_VERSION,
        DECISION_CURSOR_CHECKPOINT_SCHEMA_VERSION as DECISION_CURSOR_CHECKPOINT_SCHEMA_VERSION,
        DecisionAuthorityEvidence as DecisionAuthorityEvidence,
        DecisionContextAssembly as DecisionContextAssembly,
        DecisionEvidenceCollection as DecisionEvidenceCollection,
        DecisionEvidenceRecords as DecisionEvidenceRecords,
        DecisionRecallEvidence as DecisionRecallEvidence,
        assemble_decision_evidence as assemble_decision_evidence,
    )
    from .cursor_commit import (
        DECISION_CURSOR_COMMIT_RECEIPT_SCHEMA_VERSION as DECISION_CURSOR_COMMIT_RECEIPT_SCHEMA_VERSION,
        commit_profile_decision_cursors as commit_profile_decision_cursors,
    )
    from .outcome_feedback import (
        DECISION_OUTCOME_FEEDBACK_SCHEMA_VERSION as DECISION_OUTCOME_FEEDBACK_SCHEMA_VERSION,
        build_decision_outcome_feedback as build_decision_outcome_feedback,
    )
    from .packets import (
        DECISION_EVIDENCE_PACKET_SCHEMA_VERSION as DECISION_EVIDENCE_PACKET_SCHEMA_VERSION,
        DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION as DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION,
        DECISION_PROPOSAL_SCHEMA_VERSION as DECISION_PROPOSAL_SCHEMA_VERSION,
        DECISION_REVIEW_RECEIPT_SCHEMA_VERSION as DECISION_REVIEW_RECEIPT_SCHEMA_VERSION,
        build_decision_evidence_packet as build_decision_evidence_packet,
        build_decision_outcome_receipt as build_decision_outcome_receipt,
        build_decision_proposal as build_decision_proposal,
        build_decision_review_receipt as build_decision_review_receipt,
    )
    from .private_state import (
        DECISION_PENDING_SETTLEMENT_SCHEMA_VERSION as DECISION_PENDING_SETTLEMENT_SCHEMA_VERSION,
        load_private_decision_cursors as load_private_decision_cursors,
        load_private_pending_decision_settlement as load_private_pending_decision_settlement,
        write_private_pending_decision_settlement as write_private_pending_decision_settlement,
    )
    from .profile import (
        DECISION_CONTEXT_ACTIVATION_STATUS_SCHEMA_VERSION as DECISION_CONTEXT_ACTIVATION_STATUS_SCHEMA_VERSION,
        DECISION_CONTEXT_PROFILE_SCHEMA_VERSION as DECISION_CONTEXT_PROFILE_SCHEMA_VERSION,
        DecisionContextProfile as DecisionContextProfile,
        DecisionSourceProviderBinding as DecisionSourceProviderBinding,
        load_decision_context_profile as load_decision_context_profile,
        normalize_decision_context_profile as normalize_decision_context_profile,
        resolve_decision_context_activation as resolve_decision_context_activation,
    )
    from .providers import (
        LOCAL_FILE_SOURCE_ADAPTER as LOCAL_FILE_SOURCE_ADAPTER,
        LocalFileDecisionSourceProvider as LocalFileDecisionSourceProvider,
        build_decision_source_provider as build_decision_source_provider,
        decision_source_provider_registered as decision_source_provider_registered,
        register_decision_source_provider as register_decision_source_provider,
    )
    from .review_settlement import (
        DECISION_REVIEW_SETTLEMENT_SCHEMA_VERSION as DECISION_REVIEW_SETTLEMENT_SCHEMA_VERSION,
        settle_profile_decision_review as settle_profile_decision_review,
    )
    from .runtime import (
        assemble_profile_decision_evidence as assemble_profile_decision_evidence,
        decision_evidence_records_from_mapping as decision_evidence_records_from_mapping,
        recall_profile_decision_context as recall_profile_decision_context,
    )
    from .sources import (
        DECISION_SOURCE_MANIFEST_SCHEMA_VERSION as DECISION_SOURCE_MANIFEST_SCHEMA_VERSION,
        DECISION_SOURCE_SCAN_RECEIPT_SCHEMA_VERSION as DECISION_SOURCE_SCAN_RECEIPT_SCHEMA_VERSION,
        DecisionSourceExactRead as DecisionSourceExactRead,
        DecisionSourceItem as DecisionSourceItem,
        DecisionSourceProvider as DecisionSourceProvider,
        DecisionSourceScan as DecisionSourceScan,
        DecisionSourceSpec as DecisionSourceSpec,
        build_decision_source_item_refs as build_decision_source_item_refs,
        build_decision_source_manifest as build_decision_source_manifest,
    )
    from .turn_start_hook import (
        DECISION_CONTEXT_HOOK_CAPABILITY_ID as DECISION_CONTEXT_HOOK_CAPABILITY_ID,
        DECISION_CONTEXT_TURN_HOOK_ID as DECISION_CONTEXT_TURN_HOOK_ID,
        decision_context_hook_binding as decision_context_hook_binding,
        extend_turn_start_dispatch as extend_turn_start_dispatch,
        read_decision_context_turn_context as read_decision_context_turn_context,
        resolve_bound_decision_context_locators as resolve_bound_decision_context_locators,
    )

# The Turn start hook checks local bindings without eagerly importing providers,
# profile loaders, capture spools, or evidence assembly on unbound turns.
_EXPORTS = {
    "DECISION_CONTEXT_ASSEMBLY_SCHEMA_VERSION": "assembler",
    "DECISION_CONTEXT_EPHEMERAL_RECALL_SCHEMA_VERSION": "assembler",
    "DECISION_CONTEXT_ARCHITECTURE_SCHEMA_VERSION": "architecture",
    "DECISION_CONTEXT_ACTIVATION_STATUS_SCHEMA_VERSION": "profile",
    "DECISION_CONTEXT_HOOK_CAPABILITY_ID": "turn_start_hook",
    "DECISION_CONTEXT_TURN_HOOK_ID": "turn_start_hook",
    "DECISION_CURSOR_CHECKPOINT_SCHEMA_VERSION": "assembler",
    "DECISION_CURSOR_COMMIT_RECEIPT_SCHEMA_VERSION": "cursor_commit",
    "DECISION_EVIDENCE_PACKET_SCHEMA_VERSION": "packets",
    "DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION": "packets",
    "DECISION_OUTCOME_FEEDBACK_SCHEMA_VERSION": "outcome_feedback",
    "DECISION_PROPOSAL_SCHEMA_VERSION": "packets",
    "DECISION_REVIEW_RECEIPT_SCHEMA_VERSION": "packets",
    "DECISION_PENDING_SETTLEMENT_SCHEMA_VERSION": "private_state",
    "DECISION_REVIEW_SETTLEMENT_SCHEMA_VERSION": "review_settlement",
    "DECISION_CONTEXT_PROFILE_SCHEMA_VERSION": "profile",
    "DECISION_SOURCE_MANIFEST_SCHEMA_VERSION": "sources",
    "DECISION_SOURCE_SCAN_RECEIPT_SCHEMA_VERSION": "sources",
    "DecisionAuthorityEvidence": "assembler",
    "DecisionContextAssembly": "assembler",
    "DecisionContextProfile": "profile",
    "DecisionEvidenceCollection": "assembler",
    "DecisionEvidenceRecords": "assembler",
    "DecisionRecallEvidence": "assembler",
    "DecisionSourceExactRead": "sources",
    "DecisionSourceProviderBinding": "profile",
    "DecisionSourceItem": "sources",
    "DecisionSourceProvider": "sources",
    "DecisionSourceScan": "sources",
    "DecisionSourceSpec": "sources",
    "LOCAL_FILE_SOURCE_ADAPTER": "providers",
    "LocalFileDecisionSourceProvider": "providers",
    "assemble_decision_evidence": "assembler",
    "assemble_profile_decision_evidence": "runtime",
    "commit_profile_decision_cursors": "cursor_commit",
    "build_decision_context_architecture_packet": "architecture",
    "build_decision_evidence_packet": "packets",
    "build_decision_outcome_receipt": "packets",
    "build_decision_outcome_feedback": "outcome_feedback",
    "build_decision_proposal": "packets",
    "build_decision_review_receipt": "packets",
    "build_decision_source_item_refs": "sources",
    "build_decision_source_manifest": "sources",
    "build_decision_source_provider": "providers",
    "decision_source_provider_registered": "providers",
    "load_decision_context_profile": "profile",
    "load_private_decision_cursors": "private_state",
    "load_private_pending_decision_settlement": "private_state",
    "normalize_decision_context_profile": "profile",
    "register_decision_source_provider": "providers",
    "recall_profile_decision_context": "runtime",
    "resolve_decision_context_activation": "profile",
    "decision_context_hook_binding": "turn_start_hook",
    "decision_evidence_records_from_mapping": "runtime",
    "extend_turn_start_dispatch": "turn_start_hook",
    "read_decision_context_turn_context": "turn_start_hook",
    "resolve_bound_decision_context_locators": "turn_start_hook",
    "settle_profile_decision_review": "review_settlement",
    "write_private_pending_decision_settlement": "private_state",
}
__all__ = list(_EXPORTS)


def __getattr__(name: str) -> Any:
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(import_module(f".{module}", __name__), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__))
