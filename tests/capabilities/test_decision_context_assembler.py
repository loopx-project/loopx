from __future__ import annotations

import json
from typing import Any

import pytest

from loopx.capabilities.context_providers import (
    ContextProviderItem,
    ContextProviderRetrieval,
)
from loopx.capabilities.decision_context import (
    DecisionEvidenceRecords,
    DecisionSourceExactRead,
    DecisionSourceItem,
    DecisionSourceScan,
    DecisionSourceSpec,
    assemble_decision_evidence,
    build_decision_context_architecture_packet,
)

OBSERVED_AT = "2026-07-25T17:30:00+00:00"
BEFORE = "2026-07-25T17:31:00+00:00"
AUTHORITY_CONTENT = (
    "Current authority says adoption is still a bounded pilot.\n"
    "A first-party owner and acceptance receipt remain required.\n"
    "Protocol breadth does not replace a verified outcome."
)


def authority_source(
    *,
    source_id: str = "source:owner",
    provider_id: str = "source-provider",
    exact_read_policy: str = "material_change",
) -> DecisionSourceSpec:
    return DecisionSourceSpec(
        source_id=source_id,
        provider_id=provider_id,
        source_kind="person",
        priority="p0",
        evidence_level="s2",
        objectives=("adoption",),
        scan_mode="incremental",
        exact_read_policy=exact_read_policy,
        freshness_seconds=3600,
        private_locator="private-owner-locator",
        max_changes=3,
    )


class SourceProvider:
    provider_id = "source-provider"

    def __init__(
        self,
        *,
        status: str = "completed",
        fail_scan: bool = False,
        fail_read: bool = False,
    ) -> None:
        self.status = status
        self.fail_scan = fail_scan
        self.fail_read = fail_read

    def scan(self, **kwargs: Any) -> DecisionSourceScan:
        if self.fail_scan:
            raise RuntimeError("private scan failure detail")
        source = kwargs["source"]
        items = (
            DecisionSourceItem(
                resource_ref="private-resource",
                revision="private-revision",
                observed_at=OBSERVED_AT,
            ),
        )
        if self.status != "completed":
            items = ()
        return DecisionSourceScan(
            provider_id=self.provider_id,
            source_id=source.source_id,
            status=self.status,
            observed_at=OBSERVED_AT,
            requested_limit=source.max_changes,
            cursor_before="private-cursor-before",
            cursor_after="private-cursor-after",
            items=items,
        )

    def exact_read(self, **kwargs: Any) -> DecisionSourceExactRead:
        if self.fail_read:
            raise RuntimeError("private exact-read failure detail")
        item = kwargs["item"]
        return DecisionSourceExactRead(
            resource_ref=item.resource_ref,
            revision=item.revision,
            observed_at=OBSERVED_AT,
            content=AUTHORITY_CONTENT,
        )


class RecallProvider:
    provider_id = "recall-provider"

    def __init__(self, *, content: str = AUTHORITY_CONTENT, fail: bool = False) -> None:
        self.content = content
        self.fail = fail

    def retrieve(self, **kwargs: Any) -> ContextProviderRetrieval:
        if self.fail:
            raise RuntimeError("private recall failure detail")
        return ContextProviderRetrieval(
            provider=self.provider_id,
            namespace=kwargs["namespace"],
            status="completed",
            query_summary=kwargs["query_summary"],
            observed_at=kwargs["observed_at"],
            search_performed=True,
            read_performed=True,
            items=(
                ContextProviderItem(
                    resource_ref="private-memory-resource",
                    summary="A prior decision claim.",
                    content=self.content,
                    score=0.8,
                ),
            ),
            requested_limit=kwargs["max_results"],
        )

    def sync(self, **kwargs: Any) -> Any:
        raise AssertionError("assembler must not sync advisory memory")


class InvalidSourceProvider(SourceProvider):
    def scan(self, **kwargs: Any) -> DecisionSourceScan:
        scan = super().scan(**kwargs)
        return DecisionSourceScan(
            provider_id="unexpected-provider",
            source_id=scan.source_id,
            status=scan.status,
            observed_at=scan.observed_at,
            requested_limit=scan.requested_limit,
            cursor_before=scan.cursor_before,
            cursor_after=scan.cursor_after,
            items=scan.items,
        )


class InvalidScanTypeProvider(SourceProvider):
    def scan(self, **kwargs: Any) -> Any:
        return {"status": "completed", "private": "provider payload"}


class InvalidExactReadTypeProvider(SourceProvider):
    def exact_read(self, **kwargs: Any) -> Any:
        return {"verified": True, "private": "provider payload"}


class InvalidRecallTypeProvider(RecallProvider):
    def retrieve(self, **kwargs: Any) -> Any:
        return {"status": "completed", "private": "provider payload"}


def accepted_rebase(collection: Any) -> DecisionEvidenceRecords:
    authority = collection.authority[0]
    recall = collection.recall[0]
    return DecisionEvidenceRecords(
        changed_facts=(
            {
                "fact_id": "fact:pilot-stage",
                "summary": "Current authority keeps the adoption stage at pilot.",
                "source_ref": authority.source_ref,
                "source_revision": authority.source_revision,
                "observed_at": OBSERVED_AT,
                "freshness": "current",
                "authority": "first_party",
            },
        ),
        recalled_claims=(
            {
                "claim_id": "claim:prior-priority",
                "summary": "The prior decision also required an outcome receipt.",
                "provider_ref": recall.provider_ref,
                "source_ref": authority.source_ref,
                "source_revision": authority.source_revision,
                "observed_at": OBSERVED_AT,
                "exact_read_verified": False,
                "confidence": 0.8,
            },
        ),
    )


def test_assembler_rebases_authority_recall_and_cursor_without_raw_capture() -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": SourceProvider()},
        cursors={"source:owner": "private-cursor-before"},
        context_provider=RecallProvider(),
        rebase=accepted_rebase,
    )

    packet = assembly.public_packet()
    serialized = json.dumps(packet, sort_keys=True)

    assert packet["schema_version"] == "decision_context_assembly_v0"
    assert packet["source_coverage"]["schema_version"] == (
        "decision_source_coverage_v0"
    )
    assert packet["source_coverage"]["status"] == "complete"
    assert packet["source_coverage"]["complete"] is True
    assert packet["source_coverage"]["incomplete_required_source_ids"] == []
    assert (
        packet["evidence_packet"]["recalled_claims"][0]["exact_read_verified"] is True
    )
    assert packet["cursor_checkpoint"]["sources"][0]["disposition"] == (
        "ready_after_writeback"
    )
    assert (
        packet["cursor_checkpoint"]["apply_requires_validated_decision_writeback"]
        is True
    )
    assert packet["cursor_checkpoint"]["applied"] is False
    assert assembly.proposed_cursors == {"source:owner": "private-cursor-after"}
    for private_value in (
        "private-owner-locator",
        "private-resource",
        "private-revision",
        "private-cursor-before",
        "private-cursor-after",
        "private-memory-resource",
        AUTHORITY_CONTENT,
    ):
        assert private_value not in serialized


def test_assembler_rejects_recalled_claim_that_does_not_match_authority() -> None:
    with pytest.raises(ValueError, match="reject stale or conflicting recall"):
        assemble_decision_evidence(
            goal_id="goal:decision-advisor",
            decision_id="decision:priority",
            observed_at=OBSERVED_AT,
            before=BEFORE,
            sources=[authority_source()],
            source_providers={"source-provider": SourceProvider()},
            cursors={},
            context_provider=RecallProvider(
                content="A stale memory claims the work is already adopted."
            ),
            rebase=accepted_rebase,
        )


@pytest.mark.parametrize("timeout_seconds", [float("inf"), float("nan")])
def test_assembler_rejects_non_finite_timeout(timeout_seconds: float) -> None:
    with pytest.raises(ValueError, match="timeout_seconds must be a positive number"):
        assemble_decision_evidence(
            goal_id="goal:decision-advisor",
            decision_id="decision:priority",
            observed_at=OBSERVED_AT,
            before=BEFORE,
            sources=[],
            source_providers={},
            cursors={},
            rebase=accepted_rebase,
            timeout_seconds=timeout_seconds,
        )


def test_assembler_rejects_changed_fact_without_exact_authority_revision() -> None:
    def fabricated_rebase(_: Any) -> DecisionEvidenceRecords:
        return DecisionEvidenceRecords(
            changed_facts=(
                {
                    "fact_id": "fact:fabricated",
                    "summary": "This fact has no exact authority revision.",
                    "source_ref": "source-change-fabricated",
                    "source_revision": "source-revision-fabricated",
                    "observed_at": OBSERVED_AT,
                    "freshness": "current",
                    "authority": "first_party",
                },
            )
        )

    with pytest.raises(ValueError, match="exact-read authority revision"):
        assemble_decision_evidence(
            goal_id="goal:decision-advisor",
            decision_id="decision:priority",
            observed_at=OBSERVED_AT,
            before=BEFORE,
            sources=[authority_source()],
            source_providers={"source-provider": SourceProvider()},
            cursors={},
            rebase=fabricated_rebase,
        )


def test_changed_item_must_be_dispositioned_before_cursor_can_advance() -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": SourceProvider()},
        cursors={"source:owner": "private-cursor-before"},
        rebase=lambda _: DecisionEvidenceRecords(),
    )

    row = assembly.public_packet()["cursor_checkpoint"]["sources"][0]
    assert row["disposition"] == "preserve"
    assert row["reason_code"] == "rebase_incomplete"
    assert assembly.proposed_cursors == {}


def test_source_failure_fails_open_and_preserves_cursor() -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": SourceProvider(fail_scan=True)},
        cursors={"source:owner": "private-cursor-before"},
        context_provider=RecallProvider(fail=True),
        rebase=lambda _: DecisionEvidenceRecords(),
    )

    packet = assembly.public_packet()
    serialized = json.dumps(packet, sort_keys=True)

    assert packet["source_scan_receipts"][0]["status"] == "unavailable"
    assert packet["source_coverage"]["status"] == "incomplete"
    assert packet["source_coverage"]["complete"] is False
    assert packet["source_coverage"]["incomplete_required_source_ids"] == [
        "source:owner"
    ]
    assert packet["source_coverage"]["priority_rows"][0] == {
        "priority": "p0",
        "source_count": 1,
        "complete_count": 0,
        "exact_read_incomplete_count": 0,
        "status_counts": {
            "completed": 0,
            "no_change": 0,
            "failed": 0,
            "unavailable": 1,
        },
    }
    assert packet["context_retrieval_receipt"]["status"] == "unavailable"
    assert packet["cursor_checkpoint"]["sources"][0]["disposition"] == "preserve"
    assert assembly.proposed_cursors == {}
    assert "private scan failure detail" not in serialized
    assert "private recall failure detail" not in serialized


def test_invalid_source_provider_contract_fails_open() -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": InvalidSourceProvider()},
        cursors={"source:owner": "private-cursor-before"},
        rebase=lambda _: DecisionEvidenceRecords(),
    )

    packet = assembly.public_packet()
    assert packet["source_scan_receipts"][0]["status"] == "unavailable"
    assert packet["source_scan_receipts"][0]["reason_code"] == (
        "provider_contract_invalid"
    )
    assert packet["cursor_checkpoint"]["sources"][0]["disposition"] == "preserve"
    assert assembly.proposed_cursors == {}


@pytest.mark.parametrize(
    ("source_provider", "expected_reason"),
    [
        (InvalidScanTypeProvider(), "provider_contract_invalid"),
        (InvalidExactReadTypeProvider(), None),
    ],
)
def test_invalid_source_provider_return_type_fails_open(
    source_provider: SourceProvider,
    expected_reason: str | None,
) -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": source_provider},
        cursors={"source:owner": "private-cursor-before"},
        rebase=lambda _: DecisionEvidenceRecords(),
    )

    packet = assembly.public_packet()
    receipt = packet["source_scan_receipts"][0]
    row = packet["cursor_checkpoint"]["sources"][0]
    assert receipt["reason_code"] == expected_reason
    assert row["disposition"] == "preserve"
    assert assembly.proposed_cursors == {}
    assert "provider payload" not in json.dumps(packet, sort_keys=True)


def test_invalid_recall_provider_return_type_fails_open() -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": SourceProvider(status="no_change")},
        cursors={},
        context_provider=InvalidRecallTypeProvider(),
        rebase=lambda _: DecisionEvidenceRecords(),
    )

    receipt = assembly.public_packet()["context_retrieval_receipt"]
    assert receipt["status"] == "unavailable"
    assert receipt["reason_code"] == "provider_contract_invalid"
    assert "provider payload" not in json.dumps(receipt, sort_keys=True)


def test_exact_read_failure_does_not_advance_cursor() -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": SourceProvider(fail_read=True)},
        cursors={"source:owner": "private-cursor-before"},
        rebase=lambda collection: DecisionEvidenceRecords(
            stale_or_rejected_claims=(
                {
                    "claim_id": "claim:unread-authority",
                    "summary": "The changed authority item could not be read exactly.",
                    "observed_at": OBSERVED_AT,
                    "reason_code": "exact_read_failed",
                },
            )
        ),
    )

    packet = assembly.public_packet()
    assert packet["source_scan_receipts"][0]["changed_count"] == 1
    assert packet["source_scan_receipts"][0]["exact_read_count"] == 0
    assert packet["source_coverage"]["status"] == "incomplete"
    assert packet["source_coverage"]["priority_rows"][0][
        "exact_read_incomplete_count"
    ] == 1
    assert packet["cursor_checkpoint"]["sources"][0]["disposition"] == "preserve"
    assert assembly.proposed_cursors == {}


def test_no_change_scan_can_checkpoint_after_validated_writeback() -> None:
    assembly = assemble_decision_evidence(
        goal_id="goal:decision-advisor",
        decision_id="decision:priority",
        observed_at=OBSERVED_AT,
        before=BEFORE,
        sources=[authority_source()],
        source_providers={"source-provider": SourceProvider(status="no_change")},
        cursors={"source:owner": "private-cursor-before"},
        rebase=lambda _: DecisionEvidenceRecords(),
    )

    packet = assembly.public_packet()
    assert packet["source_scan_receipts"][0]["status"] == "no_change"
    assert packet["source_coverage"]["status"] == "complete"
    assert packet["cursor_checkpoint"]["sources"][0]["disposition"] == (
        "ready_after_writeback"
    )
    assert assembly.proposed_cursors == {"source:owner": "private-cursor-after"}


def test_architecture_projects_assembler_and_checkpoint_stage() -> None:
    packet = build_decision_context_architecture_packet()

    assert packet["assembly_schemas"] == [
        "decision_context_assembly_v0",
        "decision_semantic_rebase_receipt_v0",
        "decision_cursor_checkpoint_v0",
        "decision_cursor_commit_receipt_v0",
    ]
    assert "decision_review_receipt_v0" in packet["packet_schemas"]
    assert (
        "incremental_cursors_advance_only_after_rebase_and_writeback"
        in packet["invariants"]
    )
    assert packet["next_stage"] == "private_incremental_source_profile_then_dogfood"
