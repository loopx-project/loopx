"""Audited feedback from Decision Context outcomes into Reward Memory."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from ..context_providers.base import CONTEXT_PROVIDER_RETRIEVAL_SCHEMA_VERSION
from ..reward_memory.candidate_review import build_reward_memory_candidate
from .packets import (
    DECISION_EVIDENCE_PACKET_SCHEMA_VERSION,
    DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION,
    build_decision_evidence_packet,
    build_decision_outcome_receipt,
)


DECISION_OUTCOME_FEEDBACK_SCHEMA_VERSION = "decision_outcome_feedback_v0"
_RETRIEVAL_RECEIPT_FIELDS = {
    "schema_version",
    "ok",
    "provider",
    "namespace",
    "visibility",
    "target_scope_kind",
    "actor_binding_verified",
    "provider_preflight_performed",
    "status",
    "reason_code",
    "provider_version",
    "query_summary",
    "observed_at",
    "search_performed",
    "read_performed",
    "requested_limit",
    "result_count",
    "results",
    "telemetry",
    "fail_open",
    "external_writes_performed",
    "raw_provider_payload_captured",
    "raw_content_captured",
    "credentials_captured",
}
_RETRIEVAL_RESULT_FIELDS = {"provider_ref", "summary", "score"}
_RETRIEVAL_TELEMETRY_FIELDS = {"latency_ms", "result_cap_applied"}


def _canonical_evidence_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, Mapping):
        raise TypeError("evidence_packet must be an object")
    if packet.get("schema_version") != DECISION_EVIDENCE_PACKET_SCHEMA_VERSION:
        raise ValueError(
            f"evidence_packet must use {DECISION_EVIDENCE_PACKET_SCHEMA_VERSION}"
        )
    canonical = build_decision_evidence_packet(
        goal_id=packet.get("goal_id"),
        decision_id=packet.get("decision_id"),
        observed_at=packet.get("observed_at"),
        changed_facts=packet.get("changed_facts"),
        recalled_claims=packet.get("recalled_claims"),
        stale_or_rejected_claims=packet.get("stale_or_rejected_claims"),
        conflicts=packet.get("conflicts"),
        source_revisions=packet.get("source_revisions"),
        provider_health=packet.get("provider_health"),
    )
    if dict(packet) != canonical:
        raise ValueError("evidence_packet must be canonical and untampered")
    return canonical


def _canonical_outcome_receipt(packet: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(packet, Mapping):
        raise TypeError("outcome_receipt must be an object")
    if packet.get("schema_version") != DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION:
        raise ValueError(
            f"outcome_receipt must use {DECISION_OUTCOME_RECEIPT_SCHEMA_VERSION}"
        )
    canonical = build_decision_outcome_receipt(
        goal_id=packet.get("goal_id"),
        decision_id=packet.get("decision_id"),
        proposal_packet_ref=packet.get("proposal_packet_ref"),
        recorded_at=packet.get("recorded_at"),
        verification_status=packet.get("verification_status"),
        accepted_decision=packet.get("accepted_decision"),
        resulting_transitions=packet.get("resulting_transitions"),
        observed_outcomes=packet.get("observed_outcomes"),
        invalidated_assumptions=packet.get("invalidated_assumptions"),
        review_at=packet.get("review_at"),
    )
    if dict(packet) != canonical:
        raise ValueError("outcome_receipt must be canonical and untampered")
    return canonical


def _packet_ref(packet: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(
            packet,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:20]
    return f"decision-feedback-{digest}"


def _reason_counts(records: list[dict[str, Any]]) -> dict[str, int]:
    return dict(
        sorted(Counter(str(record["reason_code"]) for record in records).items())
    )


def _retrieval_counts(
    packet: Mapping[str, Any] | None,
    *,
    promoted_count: int,
    rejected_record_count: int,
) -> tuple[int, int, str]:
    if packet is None:
        result_count: Any = promoted_count + rejected_record_count
        return result_count, rejected_record_count, "evidence_records"
    if not isinstance(packet, Mapping):
        raise TypeError("retrieval_receipt must be an object")
    if packet.get("schema_version") != CONTEXT_PROVIDER_RETRIEVAL_SCHEMA_VERSION:
        raise ValueError(
            f"retrieval_receipt must use {CONTEXT_PROVIDER_RETRIEVAL_SCHEMA_VERSION}"
        )
    if set(packet) != _RETRIEVAL_RECEIPT_FIELDS:
        raise ValueError("retrieval_receipt must be a canonical public packet")
    required_false_flags = (
        "external_writes_performed",
        "raw_provider_payload_captured",
        "raw_content_captured",
        "credentials_captured",
    )
    if any(packet.get(field) is not False for field in required_false_flags):
        raise ValueError("retrieval_receipt violates the public read-only boundary")
    if packet.get("fail_open") is not True:
        raise ValueError("retrieval_receipt must preserve fail-open behavior")
    if packet.get("visibility") != "public":
        raise ValueError("retrieval_receipt visibility must be public")
    if packet.get("ok") is not (packet.get("status") == "completed"):
        raise ValueError("retrieval_receipt ok must match completed status")
    result_count = packet.get("result_count")
    results = packet.get("results")
    requested_limit = packet.get("requested_limit")
    if (
        isinstance(result_count, bool)
        or not isinstance(result_count, int)
        or result_count < 0
        or not isinstance(results, list)
        or len(results) != result_count
        or isinstance(requested_limit, bool)
        or not isinstance(requested_limit, int)
        or requested_limit < 0
    ):
        raise ValueError("retrieval_receipt result_count must match its result list")
    if requested_limit and result_count > requested_limit:
        raise ValueError("retrieval_receipt exceeds its requested result limit")
    if any(
        not isinstance(result, Mapping) or set(result) != _RETRIEVAL_RESULT_FIELDS
        for result in results
    ):
        raise ValueError("retrieval_receipt results must use the canonical projection")
    telemetry = packet.get("telemetry")
    if not isinstance(telemetry, Mapping) or set(telemetry) != (
        _RETRIEVAL_TELEMETRY_FIELDS
    ):
        raise ValueError(
            "retrieval_receipt telemetry must use the canonical projection"
        )
    if result_count and (
        packet.get("search_performed") is not True
        or packet.get("read_performed") is not True
    ):
        raise ValueError("retrieval_receipt results require search and read")
    if result_count < promoted_count:
        raise ValueError(
            "retrieval_receipt result_count cannot be smaller than promoted claims"
        )
    return result_count, result_count - promoted_count, "retrieval_receipt"


def build_decision_outcome_feedback(
    *,
    evidence_packet: Mapping[str, Any],
    outcome_receipt: Mapping[str, Any],
    workspace_ref: str,
    project_ref: str,
    surface_id: str = "decision_context.outcome_review",
    retrieval_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build retrieval telemetry and, when eligible, one review-only candidate.

    Rejected recall contributes telemetry only. A Reward Memory candidate requires
    an exact-read-promoted claim plus a verified outcome linked to the same evidence
    packet. This function never persists, ingests, reviews, or activates memory.
    """

    evidence = _canonical_evidence_packet(evidence_packet)
    outcome = _canonical_outcome_receipt(outcome_receipt)
    if evidence["goal_id"] != outcome["goal_id"]:
        raise ValueError("evidence_packet and outcome_receipt goal_id must match")
    if evidence["decision_id"] != outcome["decision_id"]:
        raise ValueError("evidence_packet and outcome_receipt decision_id must match")

    promoted = list(evidence["recalled_claims"])
    rejected = list(evidence["stale_or_rejected_claims"])
    conflicts = list(evidence["conflicts"])
    unresolved_conflicts = [
        conflict
        for conflict in conflicts
        if conflict["status"] not in {"clear", "resolved"}
    ]
    linked_verified_outcomes = [
        item
        for item in outcome["observed_outcomes"]
        if item["status"] == "verified"
        and item["evidence_ref"] == evidence["packet_ref"]
    ]
    current_source_revisions = sum(
        revision["freshness"] == "current" for revision in evidence["source_revisions"]
    )
    (
        discovered_result_count,
        rejected_result_count,
        retrieval_count_mode,
    ) = _retrieval_counts(
        retrieval_receipt,
        promoted_count=len(promoted),
        rejected_record_count=len(rejected),
    )

    ineligible_reasons: list[str] = []
    if outcome["verification_status"] != "verified":
        ineligible_reasons.append("outcome_not_verified")
    if outcome["reward_memory_candidate_eligible"] is not True:
        ineligible_reasons.append("outcome_not_candidate_eligible")
    if not promoted:
        ineligible_reasons.append("no_exact_read_promoted_claim")
    if not linked_verified_outcomes:
        ineligible_reasons.append("verified_outcome_not_linked_to_evidence")
    if unresolved_conflicts:
        ineligible_reasons.append("unresolved_authority_conflict")
    if not current_source_revisions:
        ineligible_reasons.append("no_current_source_revision")

    candidate = None
    if not ineligible_reasons:
        accepted = outcome["accepted_decision"]
        verified_outcome = linked_verified_outcomes[0]
        candidate = build_reward_memory_candidate(
            {
                "target_class": "procedural_experience",
                "content_summary": (
                    f"Decision: {accepted['summary']} "
                    f"Verified outcome: {verified_outcome['summary']}"
                ),
                "experience": {
                    "schema_version": "procedural_experience_contract_v0",
                    "applicability": [
                        f"A future decision matches: {accepted['summary']}"
                    ],
                    "observed_outcome": verified_outcome["summary"],
                    "attribution": (
                        "The accepted decision and verified outcome are linked to "
                        "the same exact-read evidence packet; rejected or stale "
                        "claims were excluded from the attribution."
                    ),
                    "future_behavior": {
                        "trigger": (
                            "The same decision pattern recurs with a current source "
                            "revision and no unresolved authority conflict."
                        ),
                        "action": (
                            f"Re-evaluate the evidence before applying this decision: "
                            f"{accepted['summary']}"
                        ),
                        "validation": (
                            "Require an exact-read promoted claim and a verified outcome "
                            "linked to the same evidence packet."
                        ),
                        "stop_condition": (
                            "Do not reuse the experience when the source revision is "
                            "stale, the outcome is unverified, or a conflict is unresolved."
                        ),
                    },
                    "limitations": [
                        "One verified outcome does not establish universal causality.",
                        "Reuse requires current evidence and the original scope boundaries.",
                    ],
                    "evidence_refs": [evidence["packet_ref"], outcome["packet_ref"]],
                },
                "source": {
                    "source_kind": "verified_decision_outcome",
                    "source_ref": outcome["packet_ref"],
                    "actor_ref": accepted["accepted_by"],
                    "actor_role": "verified_decision_owner",
                },
                "scope": {
                    "workspace_ref": workspace_ref,
                    "project_ref": project_ref,
                    "surface_ids": [surface_id],
                    "revision_ref": evidence["packet_ref"],
                },
                "reasoning": {
                    "summary": (
                        "An exact-read-promoted claim and a verified outcome are "
                        "linked to the same canonical evidence packet."
                    ),
                    "confidence": "high",
                },
                "guard_context": {
                    "source_freshness": "current",
                    "conflict_state": "clear",
                    "current_artifact_verified": True,
                },
                "requested_action_scopes": [],
                "raw_content_captured": False,
            }
        )

    feedback: dict[str, Any] = {
        "schema_version": DECISION_OUTCOME_FEEDBACK_SCHEMA_VERSION,
        "goal_id": evidence["goal_id"],
        "decision_id": evidence["decision_id"],
        "evidence_packet_ref": evidence["packet_ref"],
        "outcome_receipt_ref": outcome["packet_ref"],
        "observed_at": outcome["recorded_at"],
        "visibility": "public_safe",
        "capability": {
            "capability_id": "decision_context",
            "scope": "goal",
            "default_enabled": False,
            "packet_role": "audited_outcome_feedback",
            "creates_authority": False,
            "mutates_core_state": False,
        },
        "retrieval_telemetry": {
            "discovered_result_count": discovered_result_count,
            "exact_read_promoted_count": len(promoted),
            "rejected_result_count": rejected_result_count,
            "rejected_recall_record_count": len(rejected),
            "rejection_reason_counts": _reason_counts(rejected),
            "provider_health_status_counts": dict(
                sorted(
                    Counter(
                        str(item["status"]) for item in evidence["provider_health"]
                    ).items()
                )
            ),
            "current_source_revision_count": current_source_revisions,
            "count_mode": retrieval_count_mode,
        },
        "outcome_telemetry": {
            "verification_status": outcome["verification_status"],
            "linked_verified_outcome_count": len(linked_verified_outcomes),
            "invalidated_assumption_count": len(outcome["invalidated_assumptions"]),
            "resulting_transition_count": len(outcome["resulting_transitions"]),
        },
        "reward_memory": {
            "candidate_created": candidate is not None,
            "candidate_status": (
                candidate["status"] if candidate is not None else "ineligible"
            ),
            "candidate_ref": (
                candidate["candidate"]["candidate_ref"]
                if candidate is not None
                else None
            ),
            "ineligible_reason_codes": sorted(ineligible_reasons),
            "review_required": candidate is not None,
            "automatic_ingest_performed": False,
            "automatic_activation_performed": False,
        },
        "reward_memory_candidate": candidate,
        "external_writes_performed": False,
        "raw_context_captured": False,
        "raw_provider_payload_captured": False,
        "private_locators_captured": False,
        "credentials_captured": False,
    }
    feedback["feedback_ref"] = _packet_ref(feedback)
    return feedback
