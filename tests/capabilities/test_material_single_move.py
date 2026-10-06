"""Single-item intent is bounded by its interval, not the visible shortlist."""

import pytest

from loopx.capabilities.material_lifecycle import (
    build_material_rerank_apply_receipt,
    build_material_rerank_proposal,
    material_rerank_receipt_chunks,
    plan_material_single_move,
)


def test_backlog_promotion_preserves_membership_and_other_relative_order() -> None:
    refs = [f"material:{i}" for i in range(1, 172)]
    reordered, limits = plan_material_single_move(refs, "material:171", 5)
    assert reordered == refs[:4] + ["material:171"] + refs[4:-1]
    assert refs[-1] == "material:171"  # Preview cannot mutate its input.
    assert limits == {
        "target_window_size": 171,
        "max_moved_items": 167,
        "max_rank_displacement": 166,
        "protected_material_refs": [],
    }


def test_visible_first_item_can_move_to_backlog() -> None:
    refs = [f"material:{i}" for i in range(1, 41)]
    reordered, limits = plan_material_single_move(refs, refs[0], 35)
    assert reordered == refs[1:35] + [refs[0]] + refs[35:]
    assert limits["max_moved_items"] == 35


def test_no_change_and_one_item_have_usable_positive_bounds() -> None:
    reordered, limits = plan_material_single_move(["material:1"], "material:1", 1)
    assert reordered == ["material:1"]
    assert limits["max_moved_items"] == limits["max_rank_displacement"] == 1


@pytest.mark.parametrize("rank", [0, 4, True, "2", 1.5])
def test_target_rank_is_strict_and_within_complete_ranked_set(rank: object) -> None:
    with pytest.raises(ValueError, match="target rank"):
        plan_material_single_move(
            ["material:1", "material:2", "material:3"], "material:3", rank
        )  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "refs", [[], ["material:1", "material:1"], ["/private/material"]]
)
def test_source_requires_unique_public_safe_ranked_identities(refs: list[str]) -> None:
    with pytest.raises(ValueError):
        plan_material_single_move(refs, "material:1", 1)


def test_unranked_material_requires_intake_instead_of_silent_insertion() -> None:
    with pytest.raises(ValueError, match="intake"):
        plan_material_single_move(["material:1"], "material:new", 1)


def test_explicit_protection_blocks_both_selected_and_displaced_anchors() -> None:
    refs = [f"material:{i}" for i in range(1, 6)]
    for anchor in ("material:5", "material:3"):
        with pytest.raises(ValueError, match="protected"):
            plan_material_single_move(
                refs, "material:5", 2, protected_material_refs=[anchor]
            )
    reordered, limits = plan_material_single_move(
        refs, "material:5", 3, protected_material_refs=["material:1"]
    )
    assert reordered[0] == "material:1"
    assert limits["protected_material_refs"] == ["material:1"]


@pytest.mark.parametrize(
    "count, sizes",
    [(0, [0]), (1, [1]), (100, [100]), (101, [100, 1]), (167, [100, 67])],
)
def test_receipt_chunks_cover_every_input_exactly_once(
    count: int, sizes: list[int]
) -> None:
    refs = [f"material:{i}" for i in range(count)]
    chunks = material_rerank_receipt_chunks(refs)
    assert [len(chunk) for chunk in chunks] == sizes
    assert [ref for chunk in chunks for ref in chunk] == refs


def test_receipt_chunks_reject_duplicates_and_unsafe_refs_before_any_apply() -> None:
    for refs in (["material:1", "material:1"], ["https://private.invalid/material"]):
        with pytest.raises(ValueError):
            material_rerank_receipt_chunks(refs)
    with pytest.raises(TypeError):
        material_rerank_receipt_chunks("material:1")  # type: ignore[arg-type]


def test_existing_proposal_and_receipt_entrypoints_cover_one_atomic_transition() -> (
    None
):
    refs = [f"material:{i}" for i in range(1, 172)]
    reordered, limits = plan_material_single_move(refs, "material:171", 5)
    old_ranks = {ref: i for i, ref in enumerate(refs, 1)}
    moves = [
        {
            "material_ref": ref,
            "from_rank": old_ranks[ref],
            "to_rank": rank,
            "reason_code": "objective_fit",
            "evidence_refs": ["decision:current"],
        }
        for rank, ref in enumerate(reordered, 1)
        if old_ranks[ref] != rank
    ]
    proposal = build_material_rerank_proposal(
        goal_id="goal:materials",
        proposal_id="proposal:move",
        inventory_ref="inventory:current",
        decision_evidence_ref="decision:current",
        observed_at="2026-01-01T00:00:00Z",
        moves=moves,
        **limits,
    )
    assert len(proposal["moves"]) == 167
    assert proposal["apply_authorized"] is False
    affected = [move["material_ref"] for move in proposal["moves"]]
    receipts = [
        build_material_rerank_apply_receipt(
            goal_id="goal:materials",
            receipt_id=f"receipt:move:{i}",
            proposal_ref=proposal["proposal_ref"],
            observed_at="2026-01-01T00:00:00Z",
            status="applied",
            before_revision="revision:before",
            after_revision="revision:after",
            owner_gate_ref="gate:exact-move",
            validation_ref="validation:readback",
            rollback_ref="rollback:before",
            applied_material_refs=chunk,
        )
        for i, chunk in enumerate(material_rerank_receipt_chunks(affected), 1)
    ]
    assert [len(r["applied_material_refs"]) for r in receipts] == [100, 67]
    assert sorted(
        ref for r in receipts for ref in r["applied_material_refs"]
    ) == sorted(affected)
    assert len({r["proposal_ref"] for r in receipts}) == 1
    assert len({r["before_revision"] for r in receipts}) == 1
    assert len({r["after_revision"] for r in receipts}) == 1
    with pytest.raises(ValueError, match="at most 100"):
        build_material_rerank_apply_receipt(
            goal_id="goal:materials",
            receipt_id="receipt:oversized",
            proposal_ref=proposal["proposal_ref"],
            observed_at="2026-01-01T00:00:00Z",
            status="applied",
            before_revision="revision:before",
            after_revision="revision:after",
            owner_gate_ref="gate:exact-move",
            validation_ref="validation:readback",
            rollback_ref="rollback:before",
            applied_material_refs=affected,
        )
