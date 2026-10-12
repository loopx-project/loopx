from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from loopx.capabilities.context_providers.base import (
    ContextProviderItem,
    ContextProviderRetrieval,
)
from loopx.capabilities.reward_memory.application import (
    _active_item,
    build_reward_memory_recall_request,
    execute_reward_memory_recall,
)
from loopx.capabilities.reward_memory.scoped_feedback import (
    build_scoped_feedback_reward_memory_candidate,
    ingest_scoped_feedback_reward_memory_event,
)
from tests.capabilities.test_reward_memory_ingestion import FakeProvider


OBSERVED_AT = "2026-09-20T17:00:00+00:00"
WORKSPACE = "workspace:finance"
PROJECT = "project:finance-research"
SURFACE = "finance.trade.precheck"
SCOPE_REF = "viking://resources/reward-memory/finance-trade"


class DestinationMissProvider(FakeProvider):
    def retrieve(self, **kwargs: Any) -> ContextProviderRetrieval:
        if self.retrieve_calls == 0:
            return super().retrieve(**kwargs)
        self.retrieve_calls += 1
        return ContextProviderRetrieval(
            provider=self.provider_id,
            namespace=str(kwargs["namespace"]),
            status="completed",
            query_summary=str(kwargs["query_summary"]),
            observed_at=str(kwargs["observed_at"]),
            search_performed=True,
            read_performed=True,
            items=(),
            requested_limit=int(kwargs["max_results"]),
        )


def experience() -> dict[str, Any]:
    return {
        "schema_version": "procedural_experience_contract_v0",
        "applicability": [
            "Considering a short after price and open interest accelerate together"
        ],
        "observed_outcome": (
            "A NEAR short planned near 3.80 would have reached its 4.15 stop before "
            "costs."
        ),
        "attribution": (
            "The thesis relied on unrealized value capture while price and open "
            "interest were still accelerating without exhaustion confirmation."
        ),
        "future_behavior": {
            "trigger": (
                "A short thesis conflicts with accelerating price and open interest."
            ),
            "action": (
                "Wait for exhaustion or a failed breakout before proposing entry."
            ),
            "validation": (
                "Recheck price, open interest, funding, liquidity, and invalidation at "
                "the executable quote."
            ),
            "stop_condition": (
                "Abandon the entry when momentum remains intact or the quote exceeds "
                "the precomputed invalidation."
            ),
        },
        "limitations": [
            "One counterfactual loss does not prove all momentum shorts are invalid.",
            "Execution delay is not evidence that the original thesis was correct.",
        ],
        "evidence_refs": ["artifact:near-postmortem", "receipt:market-readback"],
    }


def event() -> dict[str, Any]:
    return {
        "schema_version": "scoped_feedback_reward_memory_event_v0",
        "feedback_ref": "trade-review:near-3p8-short",
        "workspace_ref": WORKSPACE,
        "project_ref": PROJECT,
        "peer_ref": "agent:finance-research-explorer",
        "surface_id": SURFACE,
        "revision_ref": "revision:near-postmortem-v1",
        "target_class": "procedural_experience",
        "content_summary": (
            "Do not short accelerating price and open interest without exhaustion "
            "confirmation."
        ),
        "experience": experience(),
        "source": {
            "source_kind": "real_outcome_review",
            "source_ref": "trade-review:near-3p8-short",
            "actor_ref": "agent:finance-research-explorer",
            "actor_role": "validated_goal_agent",
        },
        "reasoning": {
            "summary": "A counterfactual stop was verified against market readback.",
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


def corpus() -> dict[str, Any]:
    return {
        "corpus_id": "finance_trade_outcome",
        "class_id": "procedural_experience",
        "provider_id": "fake_provider",
        "owner_ref": "finance_reward_memory_owner",
        "source_of_truth": "verified_trade_outcome",
        "read_authority": "module_scoped",
        "write_authority": "provider_managed",
        "scope": {
            "workspace_ref": WORKSPACE,
            "project_ref": PROJECT,
            "peer_ref": "agent:finance-research-explorer",
            "surface_ids": [SURFACE],
        },
        "freshness": {"mode": "source_truth_bound"},
        "lifecycle": {"state": "active", "supersedes": []},
        "retrieval": {
            "index_required": True,
            "readback_required": True,
            "application_receipt_required": True,
        },
        "maintenance": {
            "writeback_triggers": ["real_outcome_review"],
            "closure_policy": "write_exact_readback_then_recall",
            "retirement_authority": "finance_reward_memory_owner",
        },
        "privacy": {"visibility": "private", "raw_content_in_registry": False},
        "provider_scope_ref_digest": hashlib.sha256(
            SCOPE_REF.encode("utf-8")
        ).hexdigest()[:16],
    }


def policy() -> dict[str, Any]:
    return {
        "schema_version": "reward_memory_standing_policy_v0",
        "policy_id": "policy:finance:trade-outcome",
        "enabled": True,
        "auto_activate": True,
        "owner_ref": "finance_reward_memory_owner",
        "reviewer_ref": "agent:finance-research-explorer",
        "authority_source_ref": "policy:finance:reward-memory",
        "scope": {
            "workspace_ref": WORKSPACE,
            "project_ref": PROJECT,
            "peer_ref": "agent:finance-research-explorer",
            "surface_ids": [SURFACE],
        },
        "allowed_target_classes": ["procedural_experience"],
        "allowed_source_kinds": ["real_outcome_review"],
        "allowed_actor_roles": ["validated_goal_agent"],
        "allowed_action_scopes": [],
        "raw_content_captured": False,
    }


def binding() -> dict[str, Any]:
    return {
        "corpus_id": "finance_trade_outcome",
        "provider_id": "fake_provider",
        "namespace": "reward_memory",
        "scope_ref": SCOPE_REF,
        "timeout_seconds": 5,
        "actor_peer_id": "finance-research-explorer",
    }


def recall_request(
    *, selected_corpus: dict[str, Any] | None = None,
    revision_ref: str = "revision:near-postmortem-v1",
) -> dict[str, Any]:
    return build_reward_memory_recall_request(
        selected_corpus if selected_corpus is not None else corpus(),
        {
            "workspace_ref": WORKSPACE,
            "project_ref": PROJECT,
            "peer_ref": "agent:finance-research-explorer",
            "surface_id": SURFACE,
            "revision_ref": revision_ref,
            "mode": "function_boundary",
            "queries": [
                {
                    "query": "What bounded lesson applies to this trade review?",
                    "query_summary": "current trade review lesson",
                }
            ],
            "limit": 5,
            "observed_at": OBSERVED_AT,
            "freshness_context": {
                "source_truth_current": True,
                "source_revision": revision_ref,
                "age_seconds": 0,
            },
            "conflict_state": "clear",
            "raw_content_captured": False,
        },
        read_authority_checkpoint={
            "verified": True,
            "corpus_id": "finance_trade_outcome",
            "workspace_ref": WORKSPACE,
            "project_ref": PROJECT,
            "peer_ref": "agent:finance-research-explorer",
            "surface_id": SURFACE,
            "read_authority": "module_scoped",
            "source_ref": "policy:finance:reward-memory",
        },
    )


def test_fact_only_summary_is_blocked_before_provider_write() -> None:
    fact_only = event()
    fact_only.pop("experience")
    candidate = build_scoped_feedback_reward_memory_candidate(fact_only)[
        "shared_candidate"
    ]
    provider = FakeProvider()

    receipt = ingest_scoped_feedback_reward_memory_event(
        fact_only,
        corpus=corpus(),
        standing_policy=policy(),
        provider_binding=binding(),
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )

    assert candidate["status"] == "guard_blocked"
    assert candidate["guard"]["reason_codes"] == [
        "procedural_experience_quality_contract_missing"
    ]
    assert receipt["status"] == "guard_blocked"
    assert receipt["experience_quality"]["passed"] is False
    assert provider.sync_calls == 0
    assert provider.retrieve_calls == 0


def test_missing_future_behavior_field_is_invalid_before_provider_write() -> None:
    incomplete = event()
    del incomplete["experience"]["future_behavior"]["action"]
    provider = FakeProvider()

    with pytest.raises(
        ValueError, match="experience.future_behavior has invalid fields"
    ):
        ingest_scoped_feedback_reward_memory_event(
            incomplete,
            corpus=corpus(),
            standing_policy=policy(),
            provider_binding=binding(),
            observed_at=OBSERVED_AT,
            execute=True,
            provider=provider,
        )

    assert provider.sync_calls == 0
    assert provider.retrieve_calls == 0


def test_finance_experience_cannot_cross_to_a_different_surface() -> None:
    wrong_surface = event() | {"surface_id": "finance.research.review"}
    provider = FakeProvider()

    receipt = ingest_scoped_feedback_reward_memory_event(
        wrong_surface,
        corpus=corpus(),
        standing_policy=policy(),
        provider_binding=binding(),
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )

    assert receipt["status"] == "guard_blocked"
    assert "candidate_surface_not_allowed" in receipt["guard"]["reason_codes"]
    assert "candidate_surface_corpus_mismatch" in receipt["guard"]["reason_codes"]
    assert provider.sync_calls == 0
    assert provider.retrieve_calls == 0


def test_qualified_experience_is_preserved_and_exactly_read_back() -> None:
    provider = FakeProvider()
    receipt = ingest_scoped_feedback_reward_memory_event(
        event(),
        corpus=corpus(),
        standing_policy=policy(),
        provider_binding=binding(),
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )

    assert receipt["status"] == "activated"
    assert receipt["exact_readback_verified"] is True
    assert receipt["memory_available_for_recall"] is True
    assert receipt["experience_quality"]["required"] is True
    assert receipt["experience_quality"]["passed"] is True
    assert receipt["experience_quality"]["status"] == "qualified_for_activation"
    assert receipt["experience_quality"]["experience_digest"].startswith("sha256:")
    assert receipt["experience_quality"]["value_status"] == (
        "unproven_until_application_evidence"
    )
    assert receipt["destination_recall"]["verified"] is True
    assert receipt["destination_recall"]["query_kind"] == "business_recall"
    assert provider.retrieve_calls == 2
    stored = json.loads(next(iter(provider.resources.values())))
    assert stored["experience"] == experience()
    assert stored["experience_quality"] == receipt["experience_quality"]


@pytest.mark.parametrize("mode", ["revision_bound", "source_truth_bound"])
def test_recall_respects_revision_policy_after_corpus_update(mode) -> None:
    provider = FakeProvider()
    selected = corpus()
    old_event = event()
    old_revision = old_event["revision_ref"]
    old_event["experience"]["evidence_refs"].append(old_revision)
    selected["freshness"] = {"mode": mode, "source_revision": old_revision}
    activated = ingest_scoped_feedback_reward_memory_event(
        old_event, corpus=selected, standing_policy=policy(),
        provider_binding=binding(), observed_at=OBSERVED_AT,
        execute=True, provider=provider,
    )
    assert activated["status"] == "activated"
    original_resources = dict(provider.resources)
    original_writes = provider.sync_calls
    same_revision = execute_reward_memory_recall(
        recall_request(selected_corpus=selected), provider_binding=binding(), provider=provider,
    )
    assert len(same_revision.items) == 1

    next_revision = "revision:next-artifact"
    selected["freshness"]["source_revision"] = next_revision
    request = recall_request(selected_corpus=selected, revision_ref=next_revision)
    assert request["guard"]["passed"] is True
    recalled = execute_reward_memory_recall(request, provider_binding=binding(), provider=provider)
    expected_count = 0 if mode == "revision_bound" else 1
    assert len(recalled.items) == expected_count
    assert recalled.public_packet["filtered_reason_counts"] == (
        {"scope_filtered": 1} if mode == "revision_bound" else {}
    )
    assert provider.resources == original_resources
    assert provider.sync_calls == original_writes
    if mode == "source_truth_bound":
        assert recalled.items[0].experience == old_event["experience"]
        return

    # Separately verified new-version activation may retain historical evidence.
    new_event = event()
    new_event["revision_ref"] = next_revision
    new_event["experience"] = old_event["experience"]
    activated = ingest_scoped_feedback_reward_memory_event(
        new_event, corpus=selected, standing_policy=policy(),
        provider_binding=binding(), observed_at=OBSERVED_AT,
        execute=True, provider=provider,
    )
    assert activated["status"] == "activated"
    assert len(provider.resources) == 2
    writes_after_activation = provider.sync_calls
    recalled = execute_reward_memory_recall(request, provider_binding=binding(), provider=provider)
    assert len(recalled.items) == 1
    assert recalled.items[0].candidate_ref != same_revision.items[0].candidate_ref
    assert recalled.items[0].experience == old_event["experience"]
    assert recalled.public_packet["provider_item_count"] == 2
    assert recalled.public_packet["filtered_reason_counts"] == {"scope_filtered": 1}
    assert provider.sync_calls == writes_after_activation


def test_exact_readback_without_destination_recall_is_not_available() -> None:
    provider = DestinationMissProvider()
    receipt = ingest_scoped_feedback_reward_memory_event(
        event(),
        corpus=corpus(),
        standing_policy=policy(),
        provider_binding=binding(),
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )

    assert receipt["exact_readback_verified"] is True
    assert receipt["destination_recall"]["verified"] is False
    assert receipt["status"] == "recall_unverified"
    assert receipt["memory_available_for_recall"] is False
    assert receipt["reason_codes"] == ["destination_business_recall_unverified"]
    assert provider.sync_calls == 1
    assert provider.retrieve_calls == 2


def test_legacy_fact_only_procedural_record_is_not_recalled() -> None:
    provider = FakeProvider()
    receipt = ingest_scoped_feedback_reward_memory_event(
        event(),
        corpus=corpus(),
        standing_policy=policy(),
        provider_binding=binding(),
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )
    target, serialized = next(iter(provider.resources.items()))
    legacy = json.loads(serialized)
    legacy.pop("experience")
    legacy.pop("experience_quality")

    item = ContextProviderItem(
        resource_ref=target,
        summary="Legacy fact-only record",
        content=json.dumps(legacy),
        score=0.99,
    )

    assert receipt["status"] == "activated"
    assert (
        _active_item(
            item,
            corpus(),
            surface_id=SURFACE,
            observed_at=OBSERVED_AT,
        )
        is None
    )

    provider.resources[target] = json.dumps(legacy)
    session = execute_reward_memory_recall(
        recall_request(),
        provider_binding=binding(),
        provider=provider,
    )

    assert session.public_packet["status"] == "empty"
    assert session.public_packet["reason_code"] == "no_active_exact_corpus_results"
    assert session.public_packet["empty_cause"] == "all_provider_items_filtered"
    assert session.public_packet["provider_item_count"] == 1
    assert session.public_packet["filtered_item_count"] == 1
    assert session.public_packet["filtered_reason_counts"] == {
        "legacy_contract_missing": 1
    }
    maintenance = session.public_packet["legacy_record_maintenance"]
    assert maintenance["status"] == "owner_action_required"
    assert maintenance["record_count"] == 1
    assert len(maintenance["record_refs"]) == 1
    assert maintenance["record_refs"][0].startswith("provider-")
    assert SCOPE_REF not in maintenance["record_refs"][0]
    assert maintenance["allowed_actions"] == ["migrate", "retire"]
    assert maintenance["migration_path"] == (
        "ingest_validated_replacement_then_retire_legacy"
    )
    assert maintenance["retirement_path"] == (
        "declared_retirement_authority_write_then_exact_readback"
    )
    assert maintenance["provider_write_performed"] is False
    assert maintenance["exact_readback_verified"] is False


def test_malformed_procedural_experience_reports_quality_filter() -> None:
    provider = FakeProvider()
    receipt = ingest_scoped_feedback_reward_memory_event(
        event(),
        corpus=corpus(),
        standing_policy=policy(),
        provider_binding=binding(),
        observed_at=OBSERVED_AT,
        execute=True,
        provider=provider,
    )
    target, serialized = next(iter(provider.resources.items()))
    malformed = json.loads(serialized)
    del malformed["experience"]["future_behavior"]["action"]
    provider.resources[target] = json.dumps(malformed)

    session = execute_reward_memory_recall(
        recall_request(),
        provider_binding=binding(),
        provider=provider,
    )

    assert receipt["status"] == "activated"
    assert session.public_packet["status"] == "empty"
    assert session.public_packet["empty_cause"] == "all_provider_items_filtered"
    assert session.public_packet["filtered_reason_counts"] == {"quality_filtered": 1}
    assert session.public_packet["legacy_record_maintenance"]["status"] == (
        "not_required"
    )


def test_future_behavior_changes_candidate_identity() -> None:
    first = build_scoped_feedback_reward_memory_candidate(event())["shared_candidate"]
    changed = event()
    changed["experience"]["future_behavior"]["action"] = (
        "Require a failed breakout and declining open interest before entry."
    )
    second = build_scoped_feedback_reward_memory_candidate(changed)["shared_candidate"]

    assert first["candidate"]["candidate_ref"] != second["candidate"]["candidate_ref"]
