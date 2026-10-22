from __future__ import annotations

import pytest

from loopx.capabilities.material_lifecycle import (
    build_material_lifecycle_architecture_packet,
    build_material_lifecycle_ranking_apply_receipt,
    build_material_lifecycle_receipt,
    build_material_ranked_state_repair_apply_receipt,
    build_material_ranked_state_repair_plan,
    inspect_material_ranked_state,
    resolve_material_ranking_lineage_receipt,
)

OBSERVED_AT = "2026-10-21T12:00:00+00:00"


def entry(rank: int, entry_ref: str, *material_refs: str) -> dict[str, object]:
    return {
        "entry_ref": entry_ref,
        "rank": rank,
        "material_refs": list(material_refs),
    }


def test_architecture_advertises_ranked_state_integrity_contracts() -> None:
    packet = build_material_lifecycle_architecture_packet()

    assert "material_ranked_state_repair_plan_v0" in packet["contract_schemas"]
    assert "material_ranked_state_repair_apply_receipt_v0" in packet["contract_schemas"]
    assert "material_lifecycle_ranking_apply_receipt_v0" in packet["contract_schemas"]
    assert (
        "ranking_lineage_resolves_by_applied_authority_revision" in packet["invariants"]
    )


def test_ranked_state_repair_derives_only_structural_summary_fields() -> None:
    entries = [
        entry(1, "entry:1", "material:1"),
        entry(2, "entry:2", "material:2", "material:3"),
        entry(3, "entry:3", "material:4"),
    ]
    plan = build_material_ranked_state_repair_plan(
        goal_id="goal:materials",
        plan_id="plan:repair-summary",
        source_revision="revision:41",
        observed_at=OBSERVED_AT,
        entries=entries,
        current_summary={
            "source_entry_count": 3,
            "rebuilt_entry_count": 3,
            "top_window_size": 2,
        },
        top_window_size=2,
    )

    assert plan["repair_required"] is True
    assert plan["expected_summary"] == {
        "rebuilt_entry_count": 3,
        "material_ref_count": 4,
        "top_window_size": 2,
        "top_window_entry_count": 2,
        "ranked_backlog_entry_count": 1,
    }
    assert set(plan["changed_summary_fields"]) == {
        "material_ref_count",
        "top_window_entry_count",
        "ranked_backlog_entry_count",
    }
    assert "source_entry_count" not in plan["expected_summary"]
    assert plan["raw_content_captured"] is False

    receipt = build_material_ranked_state_repair_apply_receipt(
        goal_id="goal:materials",
        receipt_id="receipt:repair-summary",
        plan=plan,
        observed_at=OBSERVED_AT,
        before_revision="revision:41",
        after_revision="revision:42",
        owner_gate_ref="gate:repair-summary",
        validation_ref="validation:repair-summary",
        rollback_ref="revision:41",
        authority_readback_verified=True,
    )
    assert receipt["after_revision"] == "revision:42"
    assert receipt["verification"]["ranking_membership_unchanged_verified"] is True

    tampered = {
        **plan,
        "expected_summary": {**plan["expected_summary"], "material_ref_count": 99},
    }
    with pytest.raises(ValueError, match="does not match packet content"):
        build_material_ranked_state_repair_apply_receipt(
            goal_id="goal:materials",
            receipt_id="receipt:repair-summary-tampered",
            plan=tampered,
            observed_at=OBSERVED_AT,
            before_revision="revision:41",
            after_revision="revision:42",
            owner_gate_ref="gate:repair-summary",
            validation_ref="validation:repair-summary",
            rollback_ref="revision:41",
            authority_readback_verified=True,
        )


def test_ranked_state_repair_binds_the_complete_material_owner() -> None:
    entries = [entry(1, "entry:1", "material:1")]
    goal_plan = build_material_ranked_state_repair_plan(
        goal_id="goal:materials",
        plan_id="plan:repair-goal",
        source_revision="revision:41",
        observed_at=OBSERVED_AT,
        entries=entries,
        current_summary={},
        top_window_size=1,
    )
    with pytest.raises(ValueError, match="plan material owner does not match"):
        build_material_ranked_state_repair_apply_receipt(
            goal_id="goal:other",
            receipt_id="receipt:wrong-goal",
            plan=goal_plan,
            observed_at=OBSERVED_AT,
            before_revision="revision:41",
            after_revision="revision:42",
            owner_gate_ref="gate:repair",
            validation_ref="validation:repair",
            rollback_ref="revision:41",
            authority_readback_verified=True,
        )
    with pytest.raises(ValueError, match="plan material owner does not match"):
        build_material_ranked_state_repair_apply_receipt(
            project_scope={
                "project_ref": "project:materials",
                "source_profile_ref": "profile:local",
                "workspace_grant_ref": "grant:workspace",
            },
            receipt_id="receipt:goal-to-project",
            plan=goal_plan,
            observed_at=OBSERVED_AT,
            before_revision="revision:41",
            after_revision="revision:42",
            owner_gate_ref="gate:repair",
            validation_ref="validation:repair",
            rollback_ref="revision:41",
            authority_readback_verified=True,
        )

    project_plan = build_material_ranked_state_repair_plan(
        project_scope={
            "project_ref": "project:materials",
            "source_profile_ref": "profile:local",
            "workspace_grant_ref": "grant:workspace",
        },
        plan_id="plan:repair-project",
        source_revision="revision:51",
        observed_at=OBSERVED_AT,
        entries=entries,
        current_summary={},
        top_window_size=1,
    )
    receipt = build_material_ranked_state_repair_apply_receipt(
        project_scope={
            "project_ref": "project:materials",
            "source_profile_ref": "profile:local",
            "workspace_grant_ref": "grant:workspace",
        },
        receipt_id="receipt:same-project",
        plan=project_plan,
        observed_at=OBSERVED_AT,
        before_revision="revision:51",
        after_revision="revision:52",
        owner_gate_ref="gate:repair",
        validation_ref="validation:repair",
        rollback_ref="revision:51",
        authority_readback_verified=True,
    )
    assert receipt["project_scope"]["project_ref"] == "project:materials"

    with pytest.raises(ValueError, match="plan material owner does not match"):
        build_material_ranked_state_repair_apply_receipt(
            project_scope={
                "project_ref": "project:other",
                "source_profile_ref": "profile:local",
                "workspace_grant_ref": "grant:workspace",
            },
            receipt_id="receipt:other-project",
            plan=project_plan,
            observed_at=OBSERVED_AT,
            before_revision="revision:51",
            after_revision="revision:52",
            owner_gate_ref="gate:repair",
            validation_ref="validation:repair",
            rollback_ref="revision:51",
            authority_readback_verified=True,
        )


def test_ranked_state_inspection_rejects_lossy_or_ambiguous_structure() -> None:
    with pytest.raises(ValueError, match="contiguous"):
        inspect_material_ranked_state(
            entries=[
                entry(1, "entry:1", "material:1"),
                entry(3, "entry:3", "material:2"),
            ],
            top_window_size=1,
        )

    with pytest.raises(ValueError, match="globally unique"):
        inspect_material_ranked_state(
            entries=[
                entry(1, "entry:1", "material:1"),
                entry(2, "entry:2", "material:1"),
            ],
            top_window_size=1,
        )


def test_archive_ranking_receipt_removes_only_target_and_preserves_order() -> None:
    before_entries = [
        entry(1, "entry:1", "material:1"),
        entry(2, "entry:2", "material:2"),
        entry(3, "entry:3", "material:3"),
    ]
    after_entries = [
        entry(1, "entry:1", "material:1"),
        entry(2, "entry:3", "material:3"),
    ]
    lifecycle = build_material_lifecycle_receipt(
        goal_id="goal:materials",
        receipt_id="receipt:archive-2",
        material_ref="material:2",
        from_state="candidate",
        to_state="archived",
        observed_at=OBSERVED_AT,
        source_ref="source:2",
        source_revision="revision:41",
        transition_authority_ref="decision:archive-2",
        archive_ref="archive:2",
    )
    receipt = build_material_lifecycle_ranking_apply_receipt(
        goal_id="goal:materials",
        receipt_id="receipt:archive-ranking-2",
        lifecycle_receipt=lifecycle,
        observed_at=OBSERVED_AT,
        before_revision="revision:41",
        after_revision="revision:42",
        owner_gate_ref="gate:archive-2",
        validation_ref="validation:archive-2",
        rollback_ref="revision:41",
        before_entries=before_entries,
        after_entries=after_entries,
        top_window_size=1,
        authority_readback_verified=True,
    )

    assert receipt["previous_rank"] == 2
    assert receipt["summary"]["material_ref_count"] == 2
    assert receipt["verification"]["archived_membership_removed_verified"] is True
    assert (
        receipt["verification"]["remaining_relative_order_preserved_verified"] is True
    )

    with pytest.raises(ValueError, match="preserve order"):
        build_material_lifecycle_ranking_apply_receipt(
            goal_id="goal:materials",
            receipt_id="receipt:archive-ranking-reordered",
            lifecycle_receipt=lifecycle,
            observed_at=OBSERVED_AT,
            before_revision="revision:41",
            after_revision="revision:42",
            owner_gate_ref="gate:archive-2",
            validation_ref="validation:archive-2",
            rollback_ref="revision:41",
            before_entries=before_entries,
            after_entries=[
                entry(1, "entry:3", "material:3"),
                entry(2, "entry:1", "material:1"),
            ],
            top_window_size=1,
            authority_readback_verified=True,
        )


def test_archive_ranking_receipt_binds_lifecycle_owner() -> None:
    before_entries = [entry(1, "entry:1", "material:1")]
    after_entries: list[dict[str, object]] = []
    lifecycle = build_material_lifecycle_receipt(
        goal_id="goal:materials",
        receipt_id="receipt:archive-1",
        material_ref="material:1",
        from_state="candidate",
        to_state="archived",
        observed_at=OBSERVED_AT,
        source_ref="source:1",
        source_revision="revision:41",
        transition_authority_ref="decision:archive-1",
        archive_ref="archive:1",
    )

    with pytest.raises(ValueError, match="lifecycle_receipt material owner"):
        build_material_lifecycle_ranking_apply_receipt(
            goal_id="goal:other",
            receipt_id="receipt:wrong-goal",
            lifecycle_receipt=lifecycle,
            observed_at=OBSERVED_AT,
            before_revision="revision:41",
            after_revision="revision:42",
            owner_gate_ref="gate:archive-1",
            validation_ref="validation:archive-1",
            rollback_ref="revision:41",
            before_entries=before_entries,
            after_entries=after_entries,
            top_window_size=1,
            authority_readback_verified=True,
        )
    with pytest.raises(ValueError, match="lifecycle_receipt material owner"):
        build_material_lifecycle_ranking_apply_receipt(
            project_scope={
                "project_ref": "project:materials",
                "source_profile_ref": "profile:local",
                "workspace_grant_ref": "grant:workspace",
            },
            receipt_id="receipt:goal-to-project",
            lifecycle_receipt=lifecycle,
            observed_at=OBSERVED_AT,
            before_revision="revision:41",
            after_revision="revision:42",
            owner_gate_ref="gate:archive-1",
            validation_ref="validation:archive-1",
            rollback_ref="revision:41",
            before_entries=before_entries,
            after_entries=after_entries,
            top_window_size=1,
            authority_readback_verified=True,
        )


@pytest.mark.parametrize(("before_count", "after_count"), [(30, 29), (3, 2), (1, 0)])
def test_archive_ranking_window_can_underfill_or_become_empty(
    before_count: int,
    after_count: int,
) -> None:
    before_entries = [
        entry(rank, f"entry:{rank}", f"material:{rank}")
        for rank in range(1, before_count + 1)
    ]
    archive_rank = max(1, (before_count + 1) // 2)
    lifecycle = build_material_lifecycle_receipt(
        goal_id="goal:materials",
        receipt_id=f"receipt:archive-{archive_rank}",
        material_ref=f"material:{archive_rank}",
        from_state="candidate",
        to_state="archived",
        observed_at=OBSERVED_AT,
        source_ref=f"source:{archive_rank}",
        source_revision="revision:41",
        transition_authority_ref=f"decision:archive-{archive_rank}",
        archive_ref=f"archive:{archive_rank}",
    )
    after_entries = [
        entry(rank, f"entry:{old_rank}", f"material:{old_rank}")
        for rank, old_rank in enumerate(
            (value for value in range(1, before_count + 1) if value != archive_rank),
            start=1,
        )
    ]

    receipt = build_material_lifecycle_ranking_apply_receipt(
        goal_id="goal:materials",
        receipt_id=f"receipt:archive-ranking-{archive_rank}",
        lifecycle_receipt=lifecycle,
        observed_at=OBSERVED_AT,
        before_revision="revision:41",
        after_revision="revision:42",
        owner_gate_ref="gate:archive",
        validation_ref="validation:archive",
        rollback_ref="revision:41",
        before_entries=before_entries,
        after_entries=after_entries,
        top_window_size=before_count,
        authority_readback_verified=True,
    )

    assert receipt["summary"]["rebuilt_entry_count"] == after_count
    assert receipt["summary"]["material_ref_count"] == after_count
    assert receipt["summary"]["top_window_size"] == before_count
    assert receipt["summary"]["top_window_entry_count"] == after_count
    assert receipt["summary"]["ranked_backlog_entry_count"] == 0


def test_ranking_lineage_resolves_by_applied_authority_revision() -> None:
    entries = [entry(1, "entry:1", "material:1")]
    plan = build_material_ranked_state_repair_plan(
        goal_id="goal:materials",
        plan_id="plan:repair-summary",
        source_revision="revision:41",
        observed_at=OBSERVED_AT,
        entries=entries,
        current_summary={},
        top_window_size=1,
    )
    receipt = build_material_ranked_state_repair_apply_receipt(
        goal_id="goal:materials",
        receipt_id="receipt:repair-summary",
        plan=plan,
        observed_at=OBSERVED_AT,
        before_revision="revision:41",
        after_revision="revision:42",
        owner_gate_ref="gate:repair-summary",
        validation_ref="validation:repair-summary",
        rollback_ref="revision:41",
        authority_readback_verified=True,
    )
    ignored = {**receipt, "schema_version": "private_project_receipt_v0"}

    resolved = resolve_material_ranking_lineage_receipt(
        receipts=[ignored, receipt],
        authority_revision="revision:42",
    )
    assert resolved["receipt_ref"] == receipt["receipt_ref"]

    with pytest.raises(ValueError, match="exactly one"):
        resolve_material_ranking_lineage_receipt(
            receipts=[receipt, dict(receipt)],
            authority_revision="revision:42",
        )
