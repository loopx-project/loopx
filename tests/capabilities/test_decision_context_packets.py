from __future__ import annotations

import pytest

from loopx.capabilities.decision_context import (
    build_decision_evidence_packet,
    build_decision_outcome_receipt,
    build_decision_proposal,
    build_decision_review_receipt,
)

OBSERVED_AT = "2026-07-25T13:30:00+00:00"
REVIEW_AT = "2026-07-28T13:30:00+00:00"


def evidence_packet() -> dict[str, object]:
    return build_decision_evidence_packet(
        goal_id="goal:decision-advisor",
        decision_id="decision:20260725:priority",
        observed_at=OBSERVED_AT,
        changed_facts=[
            {
                "fact_id": "fact:adoption-stage",
                "summary": "One lead advanced from discussion to a bounded pilot.",
                "source_ref": "authority:collaboration-ledger",
                "source_revision": "revision:42",
                "observed_at": OBSERVED_AT,
                "freshness": "current",
                "authority": "first_party_receipt",
            }
        ],
        recalled_claims=[
            {
                "claim_id": "claim:old-priority",
                "summary": "The previous priority favored protocol expansion.",
                "provider_ref": "provider-0123456789abcdef",
                "source_ref": "authority:decision-ledger",
                "source_revision": "revision:41",
                "observed_at": OBSERVED_AT,
                "exact_read_verified": True,
                "confidence": 0.8,
            }
        ],
        stale_or_rejected_claims=[
            {
                "claim_id": "claim:stale-user-count",
                "summary": "The recalled user count predates the current ledger.",
                "source_ref": "authority:traction-ledger",
                "source_revision": "revision:39",
                "observed_at": OBSERVED_AT,
                "reason_code": "superseded_revision",
            }
        ],
        conflicts=[
            {
                "conflict_id": "conflict:traction-stage",
                "summary": "A semantic memory says adopted while the ledger says pilot.",
                "source_refs": [
                    "provider:semantic-memory",
                    "authority:traction-ledger",
                ],
                "conflict_rule": "canonical_revision_wins",
                "status": "resolved",
            }
        ],
        source_revisions=[
            {
                "source_ref": "authority:traction-ledger",
                "revision": "revision:42",
                "observed_at": OBSERVED_AT,
                "freshness": "current",
            }
        ],
        provider_health=[
            {
                "provider": "openviking",
                "status": "healthy",
                "observed_at": OBSERVED_AT,
                "fail_open": True,
            }
        ],
    )


def proposal_packet(evidence_ref: str) -> dict[str, object]:
    return build_decision_proposal(
        goal_id="goal:decision-advisor",
        decision_id="decision:20260725:priority",
        evidence_packet_ref=evidence_ref,
        observed_at=OBSERVED_AT,
        review_at=REVIEW_AT,
        objective_scores=[
            {
                "objective_id": "objective:external-adoption",
                "score": 0.8,
                "rationale": "A bounded pilot can produce a real outcome receipt.",
            }
        ],
        recommended_decision={
            "option_id": "option:finish-pilot",
            "summary": "Close one pilot before adding another protocol.",
            "rationale": "Observed adoption compounds more than additional surface area.",
            "confidence": 0.85,
        },
        alternatives=[
            {
                "alternative_id": "option:add-protocol",
                "summary": "Add another protocol first.",
                "tradeoff": "More breadth but no new adoption evidence.",
            }
        ],
        next_actions=[
            {
                "action_id": "action:pilot-receipt",
                "owner_ref": "agent:decision-advisor",
                "summary": "Collect the pilot outcome receipt.",
                "acceptance": "One first-party outcome is revision-stamped.",
                "stop_condition": "Stop if the pilot owner does not confirm scope.",
            }
        ],
        stop_list=[
            {
                "stop_id": "stop:new-protocol",
                "summary": "Do not add another protocol during this window.",
                "condition": "Resume only after the pilot receipt is reviewed.",
            }
        ],
    )


def test_evidence_packet_is_evidence_only_and_public_safe() -> None:
    packet = evidence_packet()

    assert packet["schema_version"] == "decision_evidence_packet_v0"
    assert packet["capability"] == {
        "capability_id": "decision_context",
        "scope": "goal",
        "default_enabled": False,
        "packet_role": "evidence",
        "creates_authority": False,
        "mutates_core_state": False,
    }
    assert "recommended_decision" not in packet
    assert packet["provider_fail_open"] is True
    assert packet["raw_context_captured"] is False
    assert str(packet["packet_ref"]).startswith("decision-evidence-")


def test_evidence_packet_fingerprint_is_order_independent() -> None:
    first = build_decision_evidence_packet(
        goal_id="goal:example",
        decision_id="decision:example",
        observed_at=OBSERVED_AT,
        changed_facts=[
            {
                "fact_id": "fact:b",
                "summary": "Second fact.",
                "source_ref": "source:b",
                "observed_at": OBSERVED_AT,
            },
            {
                "fact_id": "fact:a",
                "summary": "First fact.",
                "source_ref": "source:a",
                "observed_at": OBSERVED_AT,
            },
        ],
    )
    second = build_decision_evidence_packet(
        goal_id="goal:example",
        decision_id="decision:example",
        observed_at=OBSERVED_AT,
        changed_facts=list(reversed(first["changed_facts"])),
    )

    assert first == second


def test_evidence_packet_rejects_raw_context_fields() -> None:
    with pytest.raises(ValueError, match="unsafe fields: raw_content"):
        build_decision_evidence_packet(
            goal_id="goal:example",
            decision_id="decision:example",
            observed_at=OBSERVED_AT,
            changed_facts=[
                {
                    "fact_id": "fact:a",
                    "summary": "Compact fact.",
                    "source_ref": "source:a",
                    "observed_at": OBSERVED_AT,
                    "raw_content": "private transcript",
                }
            ],
        )


def test_evidence_packet_rejects_raw_source_urls() -> None:
    with pytest.raises(ValueError, match="opaque source reference"):
        build_decision_evidence_packet(
            goal_id="goal:example",
            decision_id="decision:example",
            observed_at=OBSERVED_AT,
            changed_facts=[
                {
                    "fact_id": "fact:a",
                    "summary": "See https://example.invalid/document for details.",
                    "source_ref": "source:a",
                    "observed_at": OBSERVED_AT,
                }
            ],
        )


@pytest.mark.parametrize(
    "sensitive_summary",
    [
        "Author" + "ization: sample-value",
        "Bear" + "er sample-value",
        "api" + "_key=sample-value",
    ],
)
def test_evidence_packet_rejects_secret_bearing_text(
    sensitive_summary: str,
) -> None:
    with pytest.raises(ValueError, match="credential-like value"):
        build_decision_evidence_packet(
            goal_id="goal:example",
            decision_id="decision:example",
            observed_at=OBSERVED_AT,
            changed_facts=[
                {
                    "fact_id": "fact:a",
                    "summary": sensitive_summary,
                    "source_ref": "source:a",
                    "observed_at": OBSERVED_AT,
                }
            ],
        )


def test_evidence_packet_rejects_unverified_recalled_claims() -> None:
    with pytest.raises(ValueError, match="must be exact-read verified"):
        build_decision_evidence_packet(
            goal_id="goal:example",
            decision_id="decision:example",
            observed_at=OBSERVED_AT,
            recalled_claims=[
                {
                    "claim_id": "claim:unverified",
                    "summary": "The provider returned a claim without exact read.",
                    "provider_ref": "provider:memory",
                    "source_ref": "source:authority",
                    "source_revision": "revision:1",
                    "observed_at": OBSERVED_AT,
                    "exact_read_verified": False,
                }
            ],
        )


def test_evidence_packet_rejects_non_fail_open_provider_health() -> None:
    with pytest.raises(ValueError, match="must preserve fail-open"):
        build_decision_evidence_packet(
            goal_id="goal:example",
            decision_id="decision:example",
            observed_at=OBSERVED_AT,
            provider_health=[
                {
                    "provider": "memory-provider",
                    "status": "unavailable",
                    "observed_at": OBSERVED_AT,
                    "fail_open": False,
                }
            ],
        )


def test_proposal_is_advisory_and_references_evidence() -> None:
    evidence = evidence_packet()
    proposal = proposal_packet(str(evidence["packet_ref"]))

    assert proposal["schema_version"] == "decision_proposal_v0"
    assert proposal["evidence_packet_ref"] == evidence["packet_ref"]
    assert proposal["capability"]["creates_authority"] is False
    assert proposal["authority_confirmation_required"] is True
    assert "changed_facts" not in proposal
    assert str(proposal["packet_ref"]).startswith("decision-proposal-")


def test_proposal_requires_at_least_one_objective_score() -> None:
    with pytest.raises(ValueError, match="at least one objective"):
        build_decision_proposal(
            goal_id="goal:example",
            decision_id="decision:example",
            evidence_packet_ref="decision-evidence-example",
            observed_at=OBSERVED_AT,
            review_at=REVIEW_AT,
            objective_scores=[],
            recommended_decision={
                "option_id": "option:a",
                "summary": "Choose A.",
                "rationale": "A has evidence.",
                "confidence": 0.7,
            },
        )


@pytest.mark.parametrize("disposition", ["approve", "reject", "defer"])
def test_gated_review_receipt_binds_exact_proposal_and_gate(
    disposition: str,
) -> None:
    evidence = evidence_packet()
    proposal = proposal_packet(str(evidence["packet_ref"]))
    receipt = build_decision_review_receipt(
        goal_id="goal:decision-advisor",
        decision_id="decision:20260725:priority",
        evidence_packet_ref=str(evidence["packet_ref"]),
        proposal_packet_ref=str(proposal["packet_ref"]),
        gate_todo_id="todo:owner-review",
        source_event_id="event:owner-review",
        recorded_at=REVIEW_AT,
        disposition=disposition,
        actor_ref="owner:goal",
        reason_code=f"owner_{disposition}",
        summary=f"The owner recorded {disposition}.",
    )

    assert receipt["disposition"] == disposition
    assert receipt["authority_confirmed"] is True
    assert receipt["quiet_noop"] is False
    assert receipt["outcome_observation_required"] is (disposition == "approve")


def test_no_change_review_receipt_is_quiet_and_has_no_gate() -> None:
    evidence = evidence_packet()
    receipt = build_decision_review_receipt(
        goal_id="goal:decision-advisor",
        decision_id="decision:20260725:priority",
        evidence_packet_ref=str(evidence["packet_ref"]),
        recorded_at=REVIEW_AT,
        disposition="no_change",
        actor_ref="agent:decision-advisor",
        reason_code="no_material_thesis_change",
        summary="Changed source material does not alter the current decision.",
    )

    assert receipt["proposal_packet_ref"] is None
    assert receipt["gate_todo_id"] is None
    assert receipt["authority_confirmed"] is False
    assert receipt["quiet_noop"] is True


def test_observed_review_records_facts_without_authorizing_action() -> None:
    receipt = build_decision_review_receipt(
        goal_id="goal:example",
        decision_id="decision:example",
        evidence_packet_ref="decision-evidence-example",
        recorded_at=REVIEW_AT,
        disposition="observed",
        actor_ref="agent:decision-advisor",
        reason_code="new_facts_reviewed",
        summary="New source facts were reviewed; no action was authorized.",
    )
    assert receipt["authority_confirmation_required"] is False
    assert receipt["authority_confirmed"] is False
    assert receipt["outcome_observation_required"] is False
    assert receipt["quiet_noop"] is False
    assert receipt["source_event_id"] is None


@pytest.mark.parametrize("disposition", ["no_change", "observed"])
def test_evidence_only_review_receipt_rejects_user_gate_refs(disposition: str) -> None:
    with pytest.raises(ValueError, match="must not reference"):
        build_decision_review_receipt(
            goal_id="goal:example",
            decision_id="decision:example",
            evidence_packet_ref="decision-evidence-example",
            proposal_packet_ref="decision-proposal-example",
            recorded_at=REVIEW_AT,
            disposition=disposition,
            actor_ref="agent:decision-advisor",
            reason_code="no_material_change",
            summary="No material change.",
        )


def test_verified_outcome_receipt_can_create_reward_memory_candidate() -> None:
    evidence = evidence_packet()
    proposal = proposal_packet(str(evidence["packet_ref"]))
    receipt = build_decision_outcome_receipt(
        goal_id="goal:decision-advisor",
        decision_id="decision:20260725:priority",
        proposal_packet_ref=str(proposal["packet_ref"]),
        recorded_at=REVIEW_AT,
        review_at="2026-08-01T13:30:00+00:00",
        verification_status="verified",
        accepted_decision={
            "option_id": "option:finish-pilot",
            "summary": "Close one pilot before adding another protocol.",
            "accepted_by": "owner:goal",
            "accepted_at": OBSERVED_AT,
        },
        resulting_transitions=[
            {
                "transition_id": "transition:todo-to-complete",
                "summary": "The pilot todo reached a verified terminal state.",
                "event_ref": "event:pilot-complete",
                "status": "completed",
            }
        ],
        observed_outcomes=[
            {
                "outcome_id": "outcome:first-party-receipt",
                "summary": "The pilot owner confirmed one realized workflow gain.",
                "evidence_ref": "evidence:pilot-owner-receipt",
                "status": "verified",
                "observed_at": REVIEW_AT,
            }
        ],
        invalidated_assumptions=[
            {
                "assumption_id": "assumption:protocol-first",
                "summary": "Another protocol was not required to close the pilot.",
                "evidence_ref": "evidence:pilot-owner-receipt",
                "invalidated_at": REVIEW_AT,
            }
        ],
    )

    assert receipt["schema_version"] == "decision_outcome_receipt_v0"
    assert receipt["proposal_packet_ref"] == proposal["packet_ref"]
    assert receipt["reward_memory_candidate_eligible"] is True
    assert receipt["capability"]["creates_authority"] is False


def test_unverified_outcome_receipt_is_not_reward_memory_eligible() -> None:
    receipt = build_decision_outcome_receipt(
        goal_id="goal:example",
        decision_id="decision:example",
        proposal_packet_ref="decision-proposal-example",
        recorded_at=OBSERVED_AT,
        review_at=REVIEW_AT,
        verification_status="pending",
        accepted_decision={
            "option_id": "option:a",
            "summary": "Choose A.",
            "accepted_by": "owner:goal",
            "accepted_at": OBSERVED_AT,
        },
    )

    assert receipt["reward_memory_candidate_eligible"] is False


def test_verified_outcome_requires_observed_evidence() -> None:
    with pytest.raises(ValueError, match="at least one verified observed outcome"):
        build_decision_outcome_receipt(
            goal_id="goal:example",
            decision_id="decision:example",
            proposal_packet_ref="decision-proposal-example",
            recorded_at=OBSERVED_AT,
            review_at=REVIEW_AT,
            verification_status="verified",
            accepted_decision={
                "option_id": "option:a",
                "summary": "Choose A.",
                "accepted_by": "owner:goal",
                "accepted_at": OBSERVED_AT,
            },
        )


def test_verified_outcome_rejects_only_pending_evidence() -> None:
    with pytest.raises(ValueError, match="at least one verified observed outcome"):
        build_decision_outcome_receipt(
            goal_id="goal:example",
            decision_id="decision:example",
            proposal_packet_ref="decision-proposal-example",
            recorded_at=OBSERVED_AT,
            review_at=REVIEW_AT,
            verification_status="verified",
            accepted_decision={
                "option_id": "option:a",
                "summary": "Choose A.",
                "accepted_by": "owner:goal",
                "accepted_at": OBSERVED_AT,
            },
            observed_outcomes=[
                {
                    "outcome_id": "outcome:pending",
                    "summary": "The outcome has not been verified yet.",
                    "evidence_ref": "evidence:pending",
                    "status": "pending",
                    "observed_at": OBSERVED_AT,
                }
            ],
        )
