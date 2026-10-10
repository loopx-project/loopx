from __future__ import annotations

from copy import deepcopy
from typing import Any

import pytest

from loopx.capabilities.decision_context import build_decision_evidence_packet
from loopx.capabilities.material_lifecycle import (
    MaterialDecisionPolicyResult,
    build_material_explore_intent,
    plan_material_decision_actions,
)

OBSERVED_AT = "2026-07-26T07:30:00+08:00"


def evidence_packet(
    *,
    goal_id: str = "goal:material-example",
    fact_freshness: str = "fresh",
    fact_revision: str = "revision:7",
    source_revision: str = "revision:7",
    source_freshness: str = "fresh",
) -> dict[str, Any]:
    return build_decision_evidence_packet(
        goal_id=goal_id,
        decision_id="decision:material-rerank",
        observed_at=OBSERVED_AT,
        changed_facts=[
            {
                "fact_id": "fact:priority-shift",
                "summary": "The current objective now favors runtime evidence.",
                "source_ref": "source:decision-ledger",
                "source_revision": fact_revision,
                "observed_at": OBSERVED_AT,
                "freshness": fact_freshness,
                "authority": "curated-ledger",
            }
        ],
        stale_or_rejected_claims=[
            {
                "claim_id": "claim:old-priority",
                "summary": "The prior ranking objective is no longer current.",
                "observed_at": OBSERVED_AT,
                "reason_code": "superseded_revision",
            }
        ],
        source_revisions=[
            {
                "source_ref": "source:decision-ledger",
                "revision": source_revision,
                "observed_at": OBSERVED_AT,
                "freshness": source_freshness,
            }
        ],
        provider_health=[
            {
                "provider": "provider:local",
                "status": "healthy",
                "observed_at": OBSERVED_AT,
                "fail_open": True,
            }
        ],
    )


class ReadyPolicy:
    policy_id = "policy:material-fit"

    def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
        return MaterialDecisionPolicyResult(
            moves=(
                {
                    "material_ref": "material:runtime",
                    "from_rank": 8,
                    "to_rank": 4,
                    "reason_code": "current_objective_fit",
                    "evidence_refs": ["fact:priority-shift"],
                },
            ),
            explore_topics=(
                {
                    "topic_ref": "topic:runtime-adoption",
                    "reason_code": "evidence_gap",
                    "evidence_refs": ["fact:priority-shift"],
                },
            ),
        )


class NoChangePolicy:
    policy_id = "policy:no-change"

    def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
        return MaterialDecisionPolicyResult(
            no_change_reason="Current evidence does not justify a queue change."
        )


class FailingPolicy:
    policy_id = "policy:failing"

    def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
        raise RuntimeError("private provider failed")


def plan_with(policy: Any, **overrides: Any) -> Any:
    values = {
        "policy": policy,
        "policy_id": policy.policy_id,
        "goal_id": "goal:material-example",
        "proposal_id": "proposal:decision-rerank",
        "explore_intent_id": "intent:decision-explore",
        "inventory_ref": "material-inventory-0123456789abcdef",
        "decision_evidence": evidence_packet(),
        "observed_at": OBSERVED_AT,
        "target_window_size": 30,
        "max_moved_items": 3,
        "max_rank_displacement": 5,
        "protected_material_refs": ["material:pinned"],
        "max_explore_topics": 2,
        "max_provider_calls": 2,
        "max_new_candidates": 5,
        "explore_stop_condition": (
            "Stop after the bounded evidence gap is resolved or budget is exhausted."
        ),
    }
    values.update(overrides)
    return plan_material_decision_actions(**values)


def test_decision_evidence_drives_bounded_rerank_and_explore_intent() -> None:
    planning = plan_with(ReadyPolicy())

    assert planning.policy_status == "ready"
    assert planning.readiness_blockers == ()
    assert planning.no_change is False
    assert planning.rerank_proposal["moves"] == [
        {
            "material_ref": "material:runtime",
            "from_rank": 8,
            "to_rank": 4,
            "reason_code": "current_objective_fit",
            "evidence_refs": ["fact:priority-shift"],
        }
    ]
    assert planning.rerank_proposal["apply_authorized"] is False

    assert planning.explore_intent is not None
    assert planning.explore_intent["schema_version"] == "material_explore_intent_v0"
    assert planning.explore_intent["topics"][0]["topic_ref"] == (
        "topic:runtime-adoption"
    )
    assert planning.explore_intent["execution_authorized"] is False
    assert planning.explore_intent["provider_calls_performed"] is False
    assert planning.explore_intent["source_cursor_apply_authorized"] is False


@pytest.mark.parametrize("action_kind", ["move", "explore"])
@pytest.mark.parametrize("fact_freshness", ["stale", "unknown"])
def test_stale_referenced_fact_cannot_drive_rerank_or_explore(
    action_kind: str,
    fact_freshness: str,
) -> None:
    class StaleEvidencePolicy:
        policy_id = "policy:stale-evidence"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            move = {
                "material_ref": "material:runtime",
                "from_rank": 8,
                "to_rank": 4,
                "reason_code": "current_objective_fit",
                "evidence_refs": ["fact:priority-shift"],
            }
            topic = {
                "topic_ref": "topic:runtime-adoption",
                "reason_code": "evidence_gap",
                "evidence_refs": ["fact:priority-shift"],
            }
            return MaterialDecisionPolicyResult(
                moves=(move,) if action_kind == "move" else (),
                explore_topics=(topic,) if action_kind == "explore" else (),
            )

    planning = plan_with(
        StaleEvidencePolicy(),
        decision_evidence=evidence_packet(
            fact_freshness=fact_freshness,
        ),
    )

    assert planning.policy_status == "invalid"
    assert planning.readiness_blockers == ("decision_policy_contract_invalid",)
    assert planning.no_change is True
    assert planning.rerank_proposal["moves"] == []
    assert planning.rerank_proposal["apply_authorized"] is False
    assert planning.explore_intent is None


@pytest.mark.parametrize("source_freshness", ["stale", "unknown"])
def test_stale_source_revision_cannot_support_a_fresh_labeled_fact(
    source_freshness: str,
) -> None:
    planning = plan_with(
        ReadyPolicy(),
        decision_evidence=evidence_packet(source_freshness=source_freshness),
    )

    assert planning.policy_status == "invalid"
    assert planning.readiness_blockers == ("decision_policy_contract_invalid",)
    assert planning.no_change is True
    assert planning.rerank_proposal["moves"] == []
    assert planning.explore_intent is None


def test_fact_without_matching_current_source_revision_is_not_support() -> None:
    planning = plan_with(
        ReadyPolicy(),
        decision_evidence=evidence_packet(source_revision="revision:8"),
    )

    assert planning.policy_status == "invalid"
    assert planning.readiness_blockers == ("decision_policy_contract_invalid",)
    assert planning.no_change is True
    assert planning.rerank_proposal["moves"] == []
    assert planning.explore_intent is None


def test_fresh_referenced_fact_survives_unrelated_stale_claim() -> None:
    planning = plan_with(ReadyPolicy())

    assert planning.policy_status == "ready"
    assert planning.no_change is False
    assert planning.rerank_proposal["moves"][0]["evidence_refs"] == [
        "fact:priority-shift"
    ]
    assert planning.explore_intent is not None
    assert planning.explore_intent["topics"][0]["evidence_refs"] == [
        "fact:priority-shift"
    ]


def test_unrecognized_opaque_evidence_refs_remain_compatible() -> None:
    class ExternalEvidencePolicy:
        policy_id = "policy:external-evidence"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            return MaterialDecisionPolicyResult(
                moves=(
                    {
                        "material_ref": "material:runtime",
                        "from_rank": 8,
                        "to_rank": 4,
                        "reason_code": "external_evidence",
                        "evidence_refs": ["artifact:external-123"],
                    },
                ),
                explore_topics=(
                    {
                        "topic_ref": "topic:external",
                        "reason_code": "external_evidence_gap",
                        "evidence_refs": ["review:external-456"],
                    },
                ),
            )

    planning = plan_with(ExternalEvidencePolicy())

    assert planning.policy_status == "ready"
    assert planning.rerank_proposal["moves"][0]["evidence_refs"] == [
        "artifact:external-123"
    ]
    assert planning.explore_intent is not None
    assert planning.explore_intent["topics"][0]["evidence_refs"] == [
        "review:external-456"
    ]


def test_current_freshness_literals_remain_compatible() -> None:
    planning = plan_with(
        ReadyPolicy(),
        decision_evidence=evidence_packet(
            fact_freshness="current",
            source_freshness="current",
        ),
    )

    assert planning.policy_status == "ready"
    assert planning.rerank_proposal["moves"][0]["evidence_refs"] == [
        "fact:priority-shift"
    ]


def test_identified_stale_source_revision_cannot_be_cited_directly() -> None:
    class RevisionReferencePolicy:
        policy_id = "policy:revision-reference"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            return MaterialDecisionPolicyResult(
                moves=(
                    {
                        "material_ref": "material:runtime",
                        "from_rank": 8,
                        "to_rank": 4,
                        "reason_code": "stale_source_revision",
                        "evidence_refs": ["revision:7"],
                    },
                )
            )

    planning = plan_with(
        RevisionReferencePolicy(),
        decision_evidence=evidence_packet(source_freshness="stale"),
    )

    assert planning.policy_status == "invalid"
    assert planning.no_change is True
    assert planning.rerank_proposal["moves"] == []


@pytest.mark.parametrize("evidence_ref", ["source:scan-only", "revision:scan-only-7"])
def test_scan_only_source_revision_is_not_action_support(evidence_ref: str) -> None:
    packet = build_decision_evidence_packet(
        goal_id="goal:material-example",
        decision_id="decision:material-rerank",
        observed_at=OBSERVED_AT,
        source_revisions=[
            {
                "source_ref": "source:scan-only",
                "revision": "revision:scan-only-7",
                "observed_at": OBSERVED_AT,
                "freshness": "fresh",
            }
        ],
    )

    class ScanOnlyPolicy:
        policy_id = "policy:scan-only"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            return MaterialDecisionPolicyResult(
                moves=(
                    {
                        "material_ref": "material:runtime",
                        "from_rank": 8,
                        "to_rank": 4,
                        "reason_code": "scan_only_reference",
                        "evidence_refs": [evidence_ref],
                    },
                )
            )

    planning = plan_with(ScanOnlyPolicy(), decision_evidence=packet)

    assert planning.policy_status == "invalid"
    assert planning.no_change is True
    assert planning.rerank_proposal["moves"] == []
    assert planning.rerank_proposal["apply_authorized"] is False


@pytest.mark.parametrize("evidence_ref", ["source:decision-ledger", "revision:7"])
def test_exact_read_claim_can_support_direct_source_or_revision_ref(
    evidence_ref: str,
) -> None:
    packet = build_decision_evidence_packet(
        goal_id="goal:material-example",
        decision_id="decision:material-rerank",
        observed_at=OBSERVED_AT,
        recalled_claims=[
            {
                "claim_id": "claim:exact-read",
                "summary": "An exact read confirmed the current objective.",
                "provider_ref": "provider:local",
                "source_ref": "source:decision-ledger",
                "source_revision": "revision:7",
                "observed_at": OBSERVED_AT,
                "exact_read_verified": True,
                "confidence": 0.9,
            }
        ],
        source_revisions=[
            {
                "source_ref": "source:decision-ledger",
                "revision": "revision:7",
                "observed_at": OBSERVED_AT,
                "freshness": "current",
            }
        ],
    )

    class ExactReadPolicy:
        policy_id = "policy:exact-read"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            return MaterialDecisionPolicyResult(
                moves=(
                    {
                        "material_ref": "material:runtime",
                        "from_rank": 8,
                        "to_rank": 4,
                        "reason_code": "exact_read_support",
                        "evidence_refs": [evidence_ref],
                    },
                )
            )

    planning = plan_with(ExactReadPolicy(), decision_evidence=packet)

    assert planning.policy_status == "ready"
    assert planning.rerank_proposal["moves"][0]["evidence_refs"] == [evidence_ref]
    assert planning.rerank_proposal["apply_authorized"] is False


def test_ambiguous_revision_alias_with_scan_only_pair_fails_closed() -> None:
    packet = build_decision_evidence_packet(
        goal_id="goal:material-example",
        decision_id="decision:material-rerank",
        observed_at=OBSERVED_AT,
        recalled_claims=[
            {
                "claim_id": "claim:exact-read",
                "summary": "An exact read confirmed the current objective.",
                "provider_ref": "provider:local",
                "source_ref": "source:verified",
                "source_revision": "revision:shared",
                "observed_at": OBSERVED_AT,
                "exact_read_verified": True,
                "confidence": 0.9,
            }
        ],
        source_revisions=[
            {
                "source_ref": "source:verified",
                "revision": "revision:shared",
                "observed_at": OBSERVED_AT,
                "freshness": "current",
            },
            {
                "source_ref": "source:scan-only",
                "revision": "revision:shared",
                "observed_at": OBSERVED_AT,
                "freshness": "current",
            },
        ],
    )

    class AmbiguousRevisionPolicy:
        policy_id = "policy:ambiguous-revision"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            return MaterialDecisionPolicyResult(
                moves=(
                    {
                        "material_ref": "material:runtime",
                        "from_rank": 8,
                        "to_rank": 4,
                        "reason_code": "ambiguous_revision_alias",
                        "evidence_refs": ["revision:shared"],
                    },
                )
            )

    planning = plan_with(AmbiguousRevisionPolicy(), decision_evidence=packet)

    assert planning.policy_status == "invalid"
    assert planning.no_change is True
    assert planning.rerank_proposal["moves"] == []


@pytest.mark.parametrize("action_kind", ["move", "explore"])
def test_policy_mutation_cannot_refresh_stale_snapshot_or_mutate_caller(
    action_kind: str,
) -> None:
    packet = evidence_packet(fact_freshness="stale", source_freshness="stale")
    before = deepcopy(packet)

    class MutatingPolicy:
        policy_id = "policy:mutating"
        received_evidence: dict[str, Any] | None = None

        def evaluate(self, *, decision_evidence: dict[str, Any], **_: Any):
            self.received_evidence = decision_evidence
            decision_evidence["changed_facts"][0]["freshness"] = "fresh"
            decision_evidence["source_revisions"][0]["freshness"] = "fresh"
            move = {
                "material_ref": "material:runtime",
                "from_rank": 8,
                "to_rank": 4,
                "reason_code": "mutated_stale_support",
                "evidence_refs": ["fact:priority-shift"],
            }
            topic = {
                "topic_ref": "topic:runtime-adoption",
                "reason_code": "mutated_stale_support",
                "evidence_refs": ["fact:priority-shift"],
            }
            return MaterialDecisionPolicyResult(
                moves=(move,) if action_kind == "move" else (),
                explore_topics=(topic,) if action_kind == "explore" else (),
            )

    policy = MutatingPolicy()
    planning = plan_with(policy, decision_evidence=packet)

    assert policy.received_evidence is not packet
    assert packet == before
    assert planning.policy_status == "invalid"
    assert planning.no_change is True
    assert planning.rerank_proposal["moves"] == []
    assert planning.rerank_proposal["decision_evidence_ref"] == before["packet_ref"]
    assert planning.rerank_proposal["apply_authorized"] is False
    assert planning.explore_intent is None


def test_identified_rejected_claim_cannot_be_cited_as_support() -> None:
    class RejectedClaimPolicy:
        policy_id = "policy:rejected-claim"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            return MaterialDecisionPolicyResult(
                explore_topics=(
                    {
                        "topic_ref": "topic:old-priority",
                        "reason_code": "old_priority",
                        "evidence_refs": ["claim:old-priority"],
                    },
                )
            )

    planning = plan_with(RejectedClaimPolicy())

    assert planning.policy_status == "invalid"
    assert planning.no_change is True
    assert planning.explore_intent is None


def test_decision_policy_can_preserve_order_without_exploration() -> None:
    planning = plan_with(NoChangePolicy())

    assert planning.policy_status == "ready"
    assert planning.no_change is True
    assert planning.explore_intent is None
    assert planning.rerank_proposal["no_change_reason"].startswith("Current evidence")


def test_unavailable_policy_fails_open_to_audited_no_change() -> None:
    planning = plan_with(FailingPolicy())

    assert planning.policy_status == "unavailable"
    assert planning.readiness_blockers == ("decision_policy_unavailable",)
    assert planning.no_change is True
    assert planning.explore_intent is None
    assert planning.rerank_proposal["apply_authorized"] is False


def test_invalid_policy_output_fails_open_without_partial_explore() -> None:
    class InvalidPolicy:
        policy_id = "policy:invalid"

        def evaluate(self, **_: Any) -> MaterialDecisionPolicyResult:
            return MaterialDecisionPolicyResult(
                moves=(
                    {
                        "material_ref": "material:pinned",
                        "from_rank": 2,
                        "to_rank": 1,
                        "reason_code": "move_protected",
                    },
                ),
                explore_topics=(
                    {
                        "topic_ref": "topic:must-not-leak",
                        "reason_code": "invalid_partial_output",
                    },
                ),
            )

    planning = plan_with(InvalidPolicy())

    assert planning.policy_status == "invalid"
    assert planning.readiness_blockers == ("decision_policy_contract_invalid",)
    assert planning.no_change is True
    assert planning.explore_intent is None


def test_tampered_or_cross_goal_decision_evidence_is_rejected() -> None:
    tampered = evidence_packet()
    tampered["changed_facts"][0]["summary"] = "tampered"
    with pytest.raises(ValueError, match="packet_ref"):
        plan_with(NoChangePolicy(), decision_evidence=tampered)

    with pytest.raises(ValueError, match="goal_id does not match"):
        plan_with(
            NoChangePolicy(),
            decision_evidence=evidence_packet(goal_id="goal:other"),
        )


def test_policy_identity_mismatch_is_rejected_before_provider_call() -> None:
    with pytest.raises(ValueError, match="identity"):
        plan_with(NoChangePolicy(), policy_id="policy:other")


def test_explore_intent_rejects_raw_queries_and_budget_overflow() -> None:
    with pytest.raises(ValueError, match="unsafe fields"):
        build_material_explore_intent(
            goal_id="goal:material-example",
            intent_id="intent:unsafe",
            inventory_ref="material-inventory-0123456789abcdef",
            decision_evidence_ref="decision-evidence-0123456789abcdef",
            policy_ref="policy:material-fit",
            observed_at=OBSERVED_AT,
            max_explore_topics=1,
            max_provider_calls=1,
            max_new_candidates=2,
            stop_condition="Stop after one bounded provider call.",
            topics=[
                {
                    "topic_ref": "topic:runtime",
                    "reason_code": "evidence_gap",
                    "raw_content": "private query",
                }
            ],
        )

    with pytest.raises(ValueError, match="max_explore_topics"):
        build_material_explore_intent(
            goal_id="goal:material-example",
            intent_id="intent:overflow",
            inventory_ref="material-inventory-0123456789abcdef",
            decision_evidence_ref="decision-evidence-0123456789abcdef",
            policy_ref="policy:material-fit",
            observed_at=OBSERVED_AT,
            max_explore_topics=1,
            max_provider_calls=2,
            max_new_candidates=2,
            stop_condition="Stop after one bounded provider call.",
            topics=[
                {
                    "topic_ref": "topic:runtime",
                    "reason_code": "evidence_gap",
                },
                {
                    "topic_ref": "topic:memory",
                    "reason_code": "evidence_gap",
                },
            ],
        )
