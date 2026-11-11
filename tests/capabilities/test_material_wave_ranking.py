"""Wave-based material reranking contracts over the shared TypeScript owner."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from loopx.capabilities.material_lifecycle import (
    MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION,
    MaterialProjectScope,
    build_material_rerank_apply_receipt,
    build_material_rerank_proposal,
    material_rerank_receipt_chunks,
    plan_material_wave_rerank,
)
from loopx.cli import main

OBSERVED_AT = "2026-07-25T16:30:00+00:00"


def test_wave_rerank_stratifies_monographs_promotes_primaries_and_caps_orthogonal_signals() -> None:
    refs = [
        "material-pinned-anchor",
        "material-orthogonal-news-1",
        "material-monograph-course",
        "material-secondary-commentary",
        "material-orthogonal-news-2",
        "material-quick-baseline",
        "material-method-comparator",
        "material-primary-spec",
        "material-untouched-1",
        "material-untouched-2",
    ]
    reordered, summary = plan_material_wave_rerank(
        refs,
        [
            {
                "material_ref": "material-monograph-course",
                "horizon_wave": "imminent_gate",
                "effort_band": "deep_monograph",
                "scoped_slice_ready": False,
                "decision_gain_score": 92,
                "evidence_role": "foundational_reference",
            },
            {
                "material_ref": "material-quick-baseline",
                "horizon_wave": "imminent_gate",
                "effort_band": "quick_slice",
                "decision_gain_score": 75,
                "evidence_role": "load_bearing_primary",
                "blocks_active_deliverable": True,
            },
            {
                "material_ref": "material-secondary-commentary",
                "horizon_wave": "active_sprint",
                "effort_band": "quick_slice",
                "decision_gain_score": 80,
                "evidence_role": "secondary_or_commentary",
                "primary_source_refs": ["material-primary-spec"],
            },
            {
                "material_ref": "material-method-comparator",
                "horizon_wave": "active_sprint",
                "effort_band": "focused_study",
                "decision_gain_score": 76,
                "evidence_role": "method_comparator",
            },
            {
                "material_ref": "material-orthogonal-news-1",
                "horizon_wave": "active_sprint",
                "effort_band": "quick_slice",
                "decision_gain_score": 88,
                "evidence_role": "orthogonal_signal",
            },
            {
                "material_ref": "material-orthogonal-news-2",
                "horizon_wave": "orthogonal_backlog",
                "effort_band": "quick_slice",
                "decision_gain_score": 65,
                "evidence_role": "orthogonal_signal",
            },
        ],
        top_window_size=6,
        protected_material_refs=["material-pinned-anchor"],
        required_load_bearing_refs=[
            "material-quick-baseline",
            "material-primary-spec",
        ],
        max_orthogonal_in_top_window=0,
        default_evidence_refs=["decision:current"],
    )

    assert summary["schema_version"] == MATERIAL_WAVE_RERANK_PLAN_SCHEMA_VERSION
    assert sorted(reordered) == sorted(refs)
    assert reordered[0] == "material-pinned-anchor"
    assert reordered[1] == "material-quick-baseline"
    # Primary spec is pulled strictly ahead of secondary commentary.
    assert reordered.index("material-primary-spec") < reordered.index(
        "material-secondary-commentary"
    )
    # Unsliced deep monograph is stratified behind imminent_gate and active_sprint items.
    assert reordered.index("material-monograph-course") > reordered.index(
        "material-method-comparator"
    )
    assert summary["demoted_monograph_refs"] == ["material-monograph-course"]
    # Zero orthogonal items remain inside Top-6 while non-orthogonal items fill Top-6.
    assert summary["top_window_orthogonal_count"] == 0
    assert all(
        reordered.index(orth) >= 6
        for orth in ("material-orthogonal-news-1", "material-orthogonal-news-2")
    )
    assert summary["shifted_orthogonal_refs"] == [
        "material-orthogonal-news-1",
        "material-orthogonal-news-2",
    ]
    # Untouched items preserve exact relative order.
    assert reordered.index("material-untouched-1") < reordered.index(
        "material-untouched-2"
    )

    scope = MaterialProjectScope(
        "project:synthetic",
        "profile:materials",
        "grant:workspace-write",
    )
    proposal = build_material_rerank_proposal(
        project_scope=scope,
        proposal_id="proposal:wave-rerank",
        inventory_ref="inventory:rev-10",
        decision_evidence_ref="decision:current",
        observed_at=OBSERVED_AT,
        moves=summary["moves"],
        **summary["constraints"],
    )
    assert proposal["no_change"] is False
    assert proposal["apply_authorized"] is False
    assert proposal["capability"]["scope"] == "project"

    affected = [move["material_ref"] for move in proposal["moves"]]
    chunks = material_rerank_receipt_chunks(affected)
    receipt = build_material_rerank_apply_receipt(
        project_scope=scope,
        receipt_id="receipt:wave-rerank-1",
        proposal_ref=proposal["proposal_ref"],
        observed_at=OBSERVED_AT,
        status="applied",
        before_revision="revision:10",
        after_revision="revision:11",
        owner_gate_ref="gate:owner-wave-1",
        validation_ref="validation:wave-1",
        rollback_ref="rollback:revision-10",
        applied_material_refs=chunks[0],
    )
    assert receipt["status"] == "applied"


def test_wave_rerank_no_change_produces_usable_proposal_bounds() -> None:
    refs = ["material-alpha", "material-beta", "material-gamma"]
    reordered, summary = plan_material_wave_rerank(refs, [], top_window_size=3)
    assert reordered == refs
    assert summary["no_change"] is True
    assert summary["moves"] == []
    assert summary["constraints"] == {
        "target_window_size": 3,
        "max_moved_items": 1,
        "max_rank_displacement": 1,
        "protected_material_refs": [],
    }
    proposal = build_material_rerank_proposal(
        goal_id="goal:materials",
        proposal_id="proposal:wave-no-change",
        inventory_ref="inventory:1",
        decision_evidence_ref="decision:1",
        observed_at=OBSERVED_AT,
        moves=summary["moves"],
        no_change_reason="Queue already matches wave priorities.",
        **summary["constraints"],
    )
    assert proposal["no_change"] is True


def test_wave_rerank_rejects_unranked_load_bearing_cycles_and_unsafe_inputs() -> None:
    with pytest.raises(ValueError, match="settle candidate intake first"):
        plan_material_wave_rerank(
            ["material-secondary-summary"],
            required_load_bearing_refs=["material-unranked-primary"],
        )

    with pytest.raises(ValueError, match="cycle"):
        plan_material_wave_rerank(
            ["material-a", "material-b"],
            [
                {"material_ref": "material-a", "primary_source_refs": ["material-b"]},
                {"material_ref": "material-b", "primary_source_refs": ["material-a"]},
            ],
        )

    fake_token = "ghp_" + ("a" * 36)
    for bad_ref in (fake_token, "/srv/private/material", "https://example.invalid/doc"):
        with pytest.raises(ValueError):
            plan_material_wave_rerank([bad_ref])

    with pytest.raises(ValueError, match="unsafe fields"):
        plan_material_wave_rerank(
            ["material-a"],
            [{"material_ref": "material-a", "raw_content": "secret"}],
        )


def test_material_lifecycle_cli_plan_wave_rerank_supports_file_stdin_and_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    request = {
        "ordered_material_refs": [
            "material-orthogonal-trend",
            "material-monograph-course",
            "material-primary-spec",
            "material-untouched",
        ],
        "top_window_size": 3,
        "max_orthogonal_in_top_window": 0,
        "required_load_bearing_refs": ["material-primary-spec"],
        "annotations": [
            {
                "material_ref": "material-primary-spec",
                "horizon_wave": "imminent_gate",
                "effort_band": "quick_slice",
                "decision_gain_score": 90,
                "evidence_role": "load_bearing_primary",
                "blocks_active_deliverable": True,
            },
            {
                "material_ref": "material-monograph-course",
                "horizon_wave": "imminent_gate",
                "effort_band": "deep_monograph",
                "scoped_slice_ready": False,
                "decision_gain_score": 85,
            },
            {
                "material_ref": "material-orthogonal-trend",
                "evidence_role": "orthogonal_signal",
            },
        ],
    }
    input_path = tmp_path / "wave-request.json"
    input_path.write_text(json.dumps(request), encoding="utf-8")

    assert (
        main(
            [
                "material-lifecycle",
                "plan-wave-rerank",
                "--input",
                str(input_path),
                "--format",
                "json",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["status"] == "planned"
    assert payload["apply_authorized"] is False
    assert payload["reordered_material_refs"] == [
        "material-primary-spec",
        "material-monograph-course",
        "material-untouched",
        "material-orthogonal-trend",
    ]

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(request)))
    assert main(["material-lifecycle", "plan-wave-rerank", "--input", "-"]) == 0
    rendered = capsys.readouterr().out
    assert "# Material Wave Rerank Plan" in rendered
    assert "apply_authorized: `False`" in rendered

    blocked_request = {
        "ordered_material_refs": ["material-untouched"],
        "required_load_bearing_refs": ["material-missing-primary"],
    }
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(blocked_request)))
    assert (
        main(
            [
                "material-lifecycle",
                "plan-wave-rerank",
                "--input",
                "-",
                "--format",
                "json",
            ]
        )
        == 2
    )
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["ok"] is False
    assert blocked["status"] == "blocked"
    assert "material-missing-primary" in blocked["error"]
