from __future__ import annotations

import json
import re
import subprocess

import pytest

from loopx.presentation.explore_views import (
    PRESENTATION_MODE_CANONICAL_ONLY,
    PRESENTATION_MODE_DUAL_VIEW,
    _canonical_group_specs,
    _display_width,
    build_explore_presentation_bundle,
    explore_source_digest,
    validate_explore_view_freshness,
)
from loopx.extensions.lark.presentation.explore_results import (
    LarkExploreConfig,
    configure_lark_explore_visual_sink,
    read_lark_explore_local_config,
    sync_explore_results_to_lark,
    sync_explore_visual_to_lark,
    sync_explore_visuals_to_lark,
    write_lark_explore_local_config,
)


def test_shared_result_sync_reads_redacted_goal_id_with_canonical_local_key(
    tmp_path,
) -> None:
    goal_id = "goal-recabcdef"
    projection = {
        "schema_version": "loopx_explore_result_projection_v0",
        "goal_id": goal_id,
        "nodes": [_node("candidate", status="resolved")],
        "edges": [],
        "findings": [],
    }
    config = LarkExploreConfig(
        base_token="PUBLIC_FIXTURE_BASE",
        table_ids={"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
    )
    config_path = tmp_path / "lark-explore.json"
    dry = sync_explore_results_to_lark(
        config,
        projection=projection,
        config_path=config_path,
        sink_visibility="shared",
    )
    expected = dry["records"][0]["values"]
    calls: list[list[str]] = []

    def runner(args, _cwd, _timeout):
        calls.append(args)
        assert "+record-list" in args
        filter_payload = json.loads(args[args.index("--filter-json") + 1])
        assert filter_payload["conditions"][0][2] == "goal-[external-id-redacted]"
        fields = sorted(expected)
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {
                        "fields": fields,
                        "data": [[expected.get(field) for field in fields]],
                        "record_id_list": ["rec_public_fixture"],
                        "has_more": False,
                    },
                }
            ),
            "stderr": "",
        }

    synced = sync_explore_results_to_lark(
        config,
        projection=projection,
        config_path=config_path,
        sink_visibility="shared",
        execute=True,
        runner=runner,
    )

    assert synced["ok"] is True
    assert synced["readback"]["verified"] is True
    assert synced["written_rows"] == 0
    assert all("+record-upsert" not in call for call in calls)
    stored = read_lark_explore_local_config(config_path)
    assert stored["result_records"] == {
        f"{goal_id}:nodes:candidate": "rec_public_fixture"
    }


def test_owner_only_sync_migrates_confirmed_shared_row(tmp_path) -> None:
    goal_id = "goal-recabcdef"
    projection = {
        "schema_version": "loopx_explore_result_projection_v0",
        "goal_id": goal_id,
        "nodes": [_node("candidate", status="resolved")],
        "edges": [],
        "findings": [],
    }
    config = LarkExploreConfig(
        base_token="PUBLIC_FIXTURE_BASE",
        table_ids={"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
    )
    config_path = tmp_path / "lark-explore.json"
    owner_values = sync_explore_results_to_lark(
        config,
        projection=projection,
        config_path=config_path,
        sink_visibility="owner-only",
    )["records"][0]["values"]
    shared_values = sync_explore_results_to_lark(
        config,
        projection=projection,
        config_path=config_path,
        sink_visibility="shared",
    )["records"][0]["values"]
    filters: list[str] = []
    migrated = False

    def list_result(values: dict[str, object] | None) -> dict[str, object]:
        fields = sorted(owner_values)
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {
                        "fields": fields,
                        "data": (
                            [[values.get(field) for field in fields]]
                            if values is not None
                            else []
                        ),
                        "record_id_list": (
                            ["rec_legacy_shared_fixture"] if values is not None else []
                        ),
                        "has_more": False,
                    },
                }
            ),
            "stderr": "",
        }

    def runner(args, _cwd, _timeout):
        nonlocal migrated
        if "+record-list" in args:
            filter_payload = json.loads(args[args.index("--filter-json") + 1])
            scan_goal_id = filter_payload["conditions"][0][2]
            filters.append(scan_goal_id)
            if migrated:
                assert scan_goal_id == owner_values["LoopX Goal ID"]
                return list_result(owner_values)
            if scan_goal_id == owner_values["LoopX Goal ID"]:
                return list_result(None)
            assert scan_goal_id == shared_values["LoopX Goal ID"]
            return list_result(shared_values)
        assert "+record-upsert" in args
        assert args[args.index("--record-id") + 1] == "rec_legacy_shared_fixture"
        migrated = True
        return {
            "returncode": 0,
            "stdout": json.dumps({"ok": True}),
            "stderr": "",
        }

    synced = sync_explore_results_to_lark(
        config,
        projection=projection,
        config_path=config_path,
        sink_visibility="owner-only",
        execute=True,
        runner=runner,
    )

    assert migrated is True
    assert filters == [
        owner_values["LoopX Goal ID"],
        shared_values["LoopX Goal ID"],
        owner_values["LoopX Goal ID"],
    ]
    assert synced["ok"] is True
    assert synced["written_rows"] == 1
    assert synced["readback"]["verified"] is True


def _node(
    node_id: str,
    *,
    parent_id: str = "",
    status: str = "resolved",
    tags: list[str] | None = None,
    summary: str = "",
) -> dict[str, object]:
    return {
        "node_id": node_id,
        "title": f"Public fixture {node_id}",
        "node_kind": "experiment",
        "status": status,
        "parent_id": parent_id,
        "tags": tags or [],
        "summary": summary,
    }


def _edge(source: str, target: str) -> dict[str, object]:
    return {
        "edge_id": f"edge-{source}-{target}",
        "from_node": source,
        "to_node": target,
        "edge_type": "supports",
    }


def _small_projection() -> dict[str, object]:
    return {
        "ok": True,
        "goal_id": "goal-public-fixture",
        "source_event_count": 3,
        "nodes": [
            _node("root", status="resolved", tags=["baseline"]),
            _node(
                "candidate", parent_id="root", status="exploring", tags=["current-best"]
            ),
        ],
        "edges": [_edge("root", "candidate")],
        "findings": [
            {
                "finding_id": "finding-candidate",
                "finding": "Candidate evidence",
                "node_id": "candidate",
                "status": "confirmed",
            }
        ],
    }


def _complex_projection() -> dict[str, object]:
    nodes = [_node("root", status="open", tags=["decision"])]
    edges = []
    for branch_index in range(8):
        branch_id = f"branch-{branch_index}"
        nodes.append(_node(branch_id, parent_id="root"))
        edges.append(_edge("root", branch_id))
        for leaf_index in range(4):
            leaf_id = f"leaf-{branch_index}-{leaf_index}"
            nodes.append(_node(leaf_id, parent_id=branch_id, status="dead_end"))
            edges.append(_edge(branch_id, leaf_id))
    nodes.append(
        _node(
            "candidate",
            parent_id="root",
            status="exploring",
            tags=["current-best"],
        )
    )
    edges.append(_edge("root", "candidate"))
    return {
        "ok": True,
        "goal_id": "goal-public-fixture",
        "source_event_count": len(nodes) + len(edges),
        "nodes": nodes,
        "edges": edges,
        "findings": [],
    }


def _lane_projection() -> dict[str, object]:
    nodes = [
        {
            **_node("root", status="exploring"),
            "title": "Public Explore pilot",
            "node_kind": "area",
        },
        {
            **_node(
                "delivery-lane",
                parent_id="root",
                status="exploring",
                tags=["lane-delivery"],
            ),
            "title": "Delivery lane",
            "node_kind": "area",
        },
        {
            **_node(
                "capability-lane",
                parent_id="root",
                status="exploring",
                tags=["lane-capability"],
            ),
            "title": "Capability lane",
            "node_kind": "area",
        },
        _node(
            "fix-pr", parent_id="delivery-lane", status="exploring", tags=["open-pr"]
        ),
        _node("durable-capability", parent_id="capability-lane", status="resolved"),
    ]
    edges = [
        {
            "edge_id": "edge-fix-supports-capability",
            "from_node": "fix-pr",
            "to_node": "durable-capability",
            "edge_type": "supports",
        },
        {
            "edge_id": "edge-fix-subtopic",
            "from_node": "fix-pr",
            "to_node": "delivery-lane",
            "edge_type": "subtopic_of",
        },
    ]
    return {
        "ok": True,
        "goal_id": "goal-public-fixture",
        "source_event_count": len(nodes) + len(edges),
        "nodes": nodes,
        "edges": edges,
        "findings": [],
    }


def test_small_graph_keeps_canonical_only_and_complete() -> None:
    projection = _small_projection()

    bundle = build_explore_presentation_bundle(projection)

    assert bundle["presentation_mode"] == PRESENTATION_MODE_CANONICAL_ONLY
    assert bundle["canonical"]["graph_counts"]["node_count"] == len(projection["nodes"])
    assert bundle["canonical"]["filter"]["truncated"] is False


def test_complex_graph_recommends_traceable_dual_view() -> None:
    projection = _complex_projection()

    bundle = build_explore_presentation_bundle(projection)

    assert bundle["presentation_mode"] == PRESENTATION_MODE_DUAL_VIEW
    assert {
        "low_decision_density",
        "excessive_terminal_branches",
    }.issubset(bundle["reason_codes"])
    assert bundle["canonical"]["source_digest"] == bundle["executive"]["source_digest"]
    assert (
        bundle["canonical"]["source_revision"] == bundle["executive"]["source_revision"]
    )
    assert bundle["canonical"]["mermaid"].startswith("flowchart TB")
    assert "subgraph canonical_timeline" in bundle["canonical"]["mermaid"]
    assert (
        bundle["canonical"]["filter"]["layout"]["strategy"]
        == "vertical_evidence_timeline"
    )
    assert bundle["canonical"]["filter"]["layout"]["column_count"] == 1
    assert bundle["executive"]["mermaid"].startswith("flowchart TB")
    assert "subgraph executive_timeline" in bundle["executive"]["mermaid"]
    assert "Canonical evidence timeline" not in bundle["executive"]["mermaid"]
    assert bundle["executive"]["filter"]["layout"]["orientation"] == "top_to_bottom"
    canonical_mermaid = bundle["canonical"]["mermaid"]
    for node in projection["nodes"]:
        assert f'{str(node["node_id"]).replace("-", "_")}["' in canonical_mermaid
    for edge in projection["edges"]:
        source = str(edge["from_node"]).replace("-", "_")
        target = str(edge["to_node"]).replace("-", "_")
        assert f"{source} -->|supports| {target}" in canonical_mermaid
    canonical_ids = {node["node_id"] for node in bundle["canonical"]["nodes"]}
    assert len(bundle["executive"]["nodes"]) < len(canonical_ids)
    for node in bundle["executive"]["nodes"]:
        assert node["source_node_id"] in canonical_ids
        assert node["lineage"][-1] == node["source_node_id"]
    assert bundle["assessment"]["canonical_truncation_allowed"] is False


def test_both_views_render_status_metric_and_conclusion_from_node_summary() -> None:
    projection = _small_projection()
    projection["nodes"][1].update(
        {
            "title": "candidate_model_abc1234 with a deliberately long descriptive title",
            "status": "resolved",
            "summary": (
                "Aligned evaluation completed with stable sample parity. "
                "Target slice is +31.2/+72.4 bp and composite +51.8 bp; "
                "guardrail slice is -1.2 bp. "
                "Retain as incumbent with calibration as a guardrail."
            ),
        }
    )

    bundle = build_explore_presentation_bundle(projection)

    for role in ("canonical", "executive"):
        view = bundle[role]
        assert (
            "candidate_model_abc1234 with a deliberately long descriptive… · DONE"
            in view["mermaid"]
        )
        assert "+31.2/+72.4 bp" in view["mermaid"]
        assert "Retain as incumbent with calibration as a guardrail" in view["mermaid"]
        assert any(
            "+31.2/+72.4 bp" in stage["mermaid"] for stage in view["stage_views"]
        )
        assert any(
            "Retain as incumbent with calibration as a guardrail" in stage["mermaid"]
            for stage in view["stage_views"]
        )
        mermaid_coverage = view["filter"]["layout"]["node_detail_coverage"]
        assert mermaid_coverage["complete"] is True
        assert mermaid_coverage["summary_rendered_node_count"] == 1
        assert mermaid_coverage["metric_rendered_node_count"] == 1


def test_flat_large_graph_is_classified_as_a_readability_failure() -> None:
    projection = {
        "ok": True,
        "goal_id": "goal-public-fixture",
        "source_event_count": 80,
        "nodes": [_node(f"root-{index}") for index in range(80)],
        "edges": [],
        "findings": [],
    }

    bundle = build_explore_presentation_bundle(projection)

    assert "readability_check_failed" in bundle["reason_codes"]
    assert bundle["assessment"]["metrics"]["root_node_count"] == 80
    assert bundle["assessment"]["readability_check"]["failed"] is True
    assert bundle["canonical"]["filter"]["layout"]["column_count"] == 1


def test_each_evidence_stage_is_a_bounded_independent_topology() -> None:
    bundle = build_explore_presentation_bundle(
        _complex_projection(),
        policy={"stage_node_capacity": 14},
    )

    stages = bundle["executive"]["stage_views"]
    assert len(stages) >= 2
    assert all(1 <= stage["node_count"] <= 14 for stage in stages)
    assert all(stage["primary_node_count"] <= 12 for stage in stages)
    assert all(stage["context_node_count"] <= 2 for stage in stages)
    assert all(stage["mermaid"].startswith("flowchart TB") for stage in stages)


def test_stage_board_preserves_two_lanes_and_real_cross_lane_relation() -> None:
    bundle = build_explore_presentation_bundle(
        _lane_projection(),
        policy={"stage_node_capacity": 10},
    )

    stage = bundle["canonical"]["stage_views"][0]
    assert stage["lane_count"] == 2
    assert stage["lanes"] == ["capability", "delivery"]
    assert stage["cross_lane_edge_count"] == 1
    assert 'subgraph canonical_stage_1_lane_1["Delivery"]' in stage["mermaid"]
    assert 'subgraph canonical_stage_1_lane_2["LoopX capability"]' in stage["mermaid"]
    assert "fix_pr -->|supports| durable_capability" in stage["mermaid"]
    assert "edge-fix-subtopic" not in stage["mermaid"]
    assert stage["svg_layout"] == {
        "strategy": "semantic_lane_columns",
        "lane_order": ["capability", "delivery"],
        "orientation": "left_to_right_lanes_top_to_bottom_nodes",
    }
    assert stage["svg"].startswith('<svg xmlns="http://www.w3.org/2000/svg"')
    assert "LoopX capability" in stage["svg"]
    assert "supports" in stage["svg"]
    assert 'marker-end="url(#loopx-arrow)"' in stage["svg"]


def test_stage_svg_fits_mixed_width_node_text_and_preserves_status() -> None:
    projection = _lane_projection()
    fix_node = next(node for node in projection["nodes"] if node["node_id"] == "fix-pr")
    fix_node["title"] = (
        "Issue #3207 → Draft PR #3208: restore stats API unit-test isolation"
    )
    fix_node["summary"] = (
        "在 shared SessionExtractContextProvider 强制可配置 conversation prompt 上限，"
        "保留稳定配置语义并验证完整回归"
    )

    stage = build_explore_presentation_bundle(
        projection,
        policy={"stage_node_capacity": 10},
    )["canonical"]["stage_views"][0]
    headers = re.findall(r'font-size="14"[^>]*>([^<]+)</text>', stage["svg"])
    details = re.findall(r'font-size="12"[^>]*>([^<]+)</text>', stage["svg"])

    assert any(header.endswith("· ACTIVE") for header in headers)
    assert all(_display_width(header) <= 50 for header in headers)
    assert all(_display_width(detail) <= 62 for detail in details)
    assert any(detail.endswith("…") for detail in details)


def test_single_lane_project_does_not_invent_a_second_lane() -> None:
    projection = _small_projection()
    projection["nodes"][0]["tags"] = ["lane-retrieval"]
    projection["nodes"][1]["parent_id"] = "root"

    stage = build_explore_presentation_bundle(projection)["canonical"]["stage_views"][0]

    assert stage["lane_count"] == 1
    assert stage["lanes"] == ["retrieval"]
    assert stage["cross_lane_edge_count"] == 0
    assert stage["mermaid"].count("subgraph canonical_stage_1_lane_") == 1


def test_executive_view_suppresses_dense_hub_scaffolding_edges() -> None:
    nodes = [_node("hub", status="open", tags=["decision"])]
    nodes.extend(
        _node(f"active-{index}", parent_id="hub", status="exploring")
        for index in range(6)
    )
    edges = [_edge("hub", f"active-{index}") for index in range(6)]
    edges.append(
        {
            "edge_id": "edge-active-sequence",
            "from_node": "active-0",
            "to_node": "active-1",
            "edge_type": "leads_to",
        }
    )
    projection = {
        "ok": True,
        "goal_id": "goal-public-fixture",
        "source_event_count": len(nodes) + len(edges),
        "nodes": nodes,
        "edges": edges,
        "findings": [],
    }

    bundle = build_explore_presentation_bundle(projection)

    assert bundle["canonical"]["graph_counts"]["edge_count"] == 7
    assert bundle["executive"]["graph_counts"]["edge_count"] == 1
    assert bundle["executive"]["graph_counts"]["suppressed_edge_count"] == 6
    edge_projection = bundle["executive"]["filter"]["edge_projection"]
    assert edge_projection["suppression_counts"]["dense_hub_scaffolding"] == 6
    assert "hub -->|supports|" not in bundle["executive"]["mermaid"]
    assert "active_0 -->|leads_to| active_1" in bundle["executive"]["mermaid"]


def test_findings_change_digest_and_stale_view_is_rejected() -> None:
    projection = _small_projection()
    bundle = build_explore_presentation_bundle(projection)
    changed = json.loads(json.dumps(projection))
    changed["findings"][0]["finding"] = "Updated candidate evidence"

    assert explore_source_digest(changed) != bundle["source_digest"]
    freshness = validate_explore_view_freshness(changed, bundle["executive"])
    assert freshness["fresh"] is False
    assert freshness["reason"]


def test_presentation_rejects_a_bounded_canonical_finding_projection() -> None:
    projection = _small_projection()
    projection["counts"] = {"finding_count": 2}

    with pytest.raises(ValueError, match="complete canonical finding set"):
        build_explore_presentation_bundle(projection)


def test_visual_configuration_preserves_legacy_and_supports_stage_boards(
    tmp_path,
) -> None:
    config_path = tmp_path / "lark-explore.json"
    write_lark_explore_local_config(
        config_path,
        {
            "board": {
                "base_token": "PUBLIC_FIXTURE_BASE",
                "tables": {"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
            }
        },
    )
    configure_lark_explore_visual_sink(
        config_path=config_path,
        whiteboard_token="wb_legacy_fixture",
        execute=True,
    )
    configure_lark_explore_visual_sink(
        config_path=config_path,
        whiteboard_token="wb_canonical_fixture",
        projection_mode="canonical_full",
        view_role="canonical",
        execute=True,
    )
    configure_lark_explore_visual_sink(
        config_path=config_path,
        whiteboard_token="wb_executive_fixture",
        projection_mode="executive_auto",
        view_role="executive",
        stage_capacity=18,
        stage_whiteboard_tokens=["wb_executive_fixture", "wb_stage_02"],
        board_style="semantic_lane_columns",
        execute=True,
    )

    stored = read_lark_explore_local_config(config_path)
    assert stored["visual_sink"]["whiteboard_token"] == "wb_legacy_fixture"
    assert stored["visual_sinks"]["canonical"]["view_role"] == "canonical"
    assert stored["visual_sinks"]["executive"]["view_role"] == "executive"
    assert stored["visual_sinks"]["executive"]["board_style"] == "semantic_lane_columns"
    assert stored["visual_sinks"]["executive"]["renderer"] == "stage_svg"
    assert stored["visual_sinks"]["executive"]["presentation_mode"] == "stage_document"
    assert stored["visual_sinks"]["executive"]["stage_capacity"] == 18
    assert stored["visual_sinks"]["executive"]["stage_whiteboards"] == [
        {"stage_index": 1, "whiteboard_token": "wb_executive_fixture"},
        {"stage_index": 2, "whiteboard_token": "wb_stage_02"},
    ]


def test_visual_configuration_preserves_existing_style_when_omitted(tmp_path) -> None:
    config_path = tmp_path / "lark-explore.json"
    write_lark_explore_local_config(
        config_path,
        {
            "board": {
                "base_token": "PUBLIC_FIXTURE_BASE",
                "tables": {"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
            },
            "visual_sinks": {
                "canonical": {
                    "whiteboard_token": "wb_canonical_fixture",
                    "view_role": "canonical",
                    "projection_mode": "canonical_full",
                    "renderer": "mermaid",
                }
            },
        },
    )
    configure_lark_explore_visual_sink(
        config_path=config_path,
        whiteboard_token="wb_executive_fixture",
        projection_mode="executive_auto",
        view_role="executive",
        board_style="semantic_lane_columns",
        execute=True,
    )

    canonical = configure_lark_explore_visual_sink(
        config_path=config_path,
        whiteboard_token="wb_canonical_updated",
        docx_token="doc_canonical_updated",
        projection_mode="canonical_full",
        view_role="canonical",
        execute=True,
    )["visual_sink"]
    executive = configure_lark_explore_visual_sink(
        config_path=config_path,
        whiteboard_token="wb_executive_updated",
        docx_token="doc_executive_updated",
        projection_mode="executive_auto",
        view_role="executive",
        stage_whiteboard_tokens=["wb_executive_updated", "wb_stage_02_updated"],
        execute=True,
    )["visual_sink"]

    assert canonical["board_style"] == "auto_flow"
    assert canonical["renderer"] == "mermaid"
    assert executive["board_style"] == "semantic_lane_columns"
    assert executive["renderer"] == "stage_svg"

    changed = configure_lark_explore_visual_sink(
        config_path=config_path,
        whiteboard_token="wb_executive_updated",
        projection_mode="executive_auto",
        view_role="executive",
        board_style="auto_flow",
        execute=True,
    )["visual_sink"]
    assert changed["board_style"] == "auto_flow"
    assert changed["renderer"] == "mermaid"


def test_visual_configuration_rejects_out_of_range_stage_capacity(tmp_path) -> None:
    config_path = tmp_path / "lark-explore.json"
    write_lark_explore_local_config(
        config_path,
        {
            "board": {
                "base_token": "PUBLIC_FIXTURE_BASE",
                "tables": {"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
            }
        },
    )

    with pytest.raises(ValueError, match="between 10 and 20"):
        configure_lark_explore_visual_sink(
            config_path=config_path,
            whiteboard_token="wb_legacy_fixture",
            stage_capacity=9,
        )

    with pytest.raises(ValueError, match="between 10 and 20"):
        configure_lark_explore_visual_sink(
            config_path=config_path,
            whiteboard_token="wb_legacy_fixture",
            stage_capacity=21,
        )


def test_visual_configuration_can_create_all_stage_boards_from_docx(tmp_path) -> None:
    config_path = tmp_path / "lark-explore.json"
    write_lark_explore_local_config(
        config_path,
        {
            "board": {
                "base_token": "PUBLIC_FIXTURE_BASE",
                "tables": {"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
            }
        },
    )

    configure_lark_explore_visual_sink(
        config_path=config_path,
        docx_token="doc_public_fixture",
        projection_mode="executive_auto",
        view_role="executive",
        execute=True,
    )

    stored = read_lark_explore_local_config(config_path)
    sink = stored["visual_sinks"]["executive"]
    assert sink["whiteboard_token"] is None
    assert sink["docx_token"] == "doc_public_fixture"
    assert sink["stage_whiteboards"] == []


def test_canonical_only_visual_sync_fails_closed_without_canonical_sink(
    tmp_path,
) -> None:
    projection = _small_projection()

    def runner(_args, _cwd, _timeout):
        raise AssertionError("missing recommended role must not publish another view")

    synced = sync_explore_visuals_to_lark(
        LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE"),
        projection=projection,
        visual_sinks={
            "executive": {
                "whiteboard_token": "wb_executive_fixture",
                "view_role": "executive",
            }
        },
        config_path=tmp_path / "lark-explore.json",
        execute=True,
        runner=runner,
    )

    assert synced["presentation_mode"] == PRESENTATION_MODE_CANONICAL_ONLY
    assert synced["ok"] is False
    assert synced["status"] == "sink_unsatisfied"
    assert synced["published"] is False
    assert synced["retryable"] is True
    assert synced["recommended_roles"] == ["canonical"]
    assert synced["missing_recommended_roles"] == ["canonical"]
    assert synced["views"]["executive"]["status"] == "not_recommended"
    assert "configure the canonical visual role" in synced["required_action"]


@pytest.mark.parametrize(
    ("board_style", "renderer", "input_format"),
    [
        ("auto_flow", "mermaid", "mermaid"),
        ("semantic_lane_columns", "stage_svg", "svg"),
    ],
)
def test_visual_sync_publishes_one_whiteboard_per_stage_style(
    tmp_path,
    board_style,
    renderer,
    input_format,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(
        projection,
        policy={"stage_node_capacity": 14},
    )
    stage_count = len(bundle["executive"]["stage_views"])
    synced = sync_explore_visuals_to_lark(
        config,
        projection=projection,
        visual_sinks={
            "executive": {
                "whiteboard_token": "wb_stage_01",
                "view_role": "executive",
                "board_style": board_style,
                "stage_capacity": 14,
                "stage_whiteboards": [
                    {
                        "stage_index": index,
                        "whiteboard_token": f"wb_stage_{index:02d}",
                    }
                    for index in range(1, stage_count + 1)
                ],
            }
        },
        config_path=tmp_path / "lark-explore.json",
    )

    view = synced["views"]["executive"]
    assert view["stage_count"] == stage_count
    assert view["stage_capacity"] == 14
    assert view["presentation_mode"] == "stage_document"
    assert view["board_style"] == board_style
    assert all(stage["renderer"] == renderer for stage in view["stages"])
    assert all(stage["input_format"] == input_format for stage in view["stages"])
    assert all(
        f"--input_format {input_format}" in stage["command"]["command"]
        for stage in view["stages"]
    )


def test_dual_visual_sync_uses_one_revision_and_rejects_stale_view(tmp_path) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(
        base_token="PUBLIC_FIXTURE_BASE",
        table_ids={"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
    )
    bundle = build_explore_presentation_bundle(projection)
    sinks = {
        "canonical": {
            "whiteboard_token": "wb_canonical_fixture",
            "view_role": "canonical",
            "stage_whiteboards": [
                {
                    "stage_index": index,
                    "whiteboard_token": f"wb_canonical_{index:02d}",
                }
                for index in range(1, len(bundle["canonical"]["stage_views"]) + 1)
            ],
        },
        "executive": {
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "stage_whiteboards": [
                {
                    "stage_index": index,
                    "whiteboard_token": f"wb_executive_{index:02d}",
                }
                for index in range(1, len(bundle["executive"]["stage_views"]) + 1)
            ],
        },
    }

    synced = sync_explore_visuals_to_lark(
        config,
        projection=projection,
        visual_sinks=sinks,
        config_path=tmp_path / "lark-explore.json",
    )

    assert synced["ok"] is True
    assert synced["presentation_mode"] == PRESENTATION_MODE_DUAL_VIEW
    revisions = {view["source_revision"] for view in synced["views"].values()}
    assert revisions == {synced["source_revision"]}

    changed = json.loads(json.dumps(projection))
    changed["nodes"][0]["title"] = "Changed canonical source"
    stale = sync_explore_visual_to_lark(
        config,
        projection=changed,
        visual_sink=sinks["executive"],
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
    )
    assert stale["ok"] is False
    assert stale["status"] == "stale_projection"
    assert "command" not in stale


def test_visual_delivery_digest_changes_when_only_rendered_mermaid_changes(
    tmp_path,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    sink = {
        "whiteboard_token": "wb_canonical_fixture",
        "view_role": "canonical",
    }
    bundle = build_explore_presentation_bundle(projection)
    first_view = bundle["canonical"]
    changed_view = json.loads(json.dumps(first_view))
    changed_view["mermaid"] += "\n    %% renderer contract changed"

    first = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink=sink,
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=first_view,
        view_key="canonical",
    )
    changed = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink=sink,
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=changed_view,
        view_key="canonical",
    )

    assert first["source_digest"] == changed["source_digest"]
    assert first["delivery_digest"] != changed["delivery_digest"]
    assert first["command"]["command"] != changed["command"]["command"]


def test_canonical_group_specs_balances_stage_tail() -> None:
    nodes = [{"node_id": f"n{i:02d}", "title": f"N{i}"} for i in range(29)]

    specs = _canonical_group_specs(nodes, group_node_limit=12)

    sizes = [len(spec["node_ids"]) for spec in specs]
    assert sizes == [10, 10, 9]
    assert [spec["order"] for spec in specs] == [0, 10, 20]
    assert [spec["title"] for spec in specs][0].startswith("Evidence stage 01 · ")


def test_visual_configuration_auto_create_plans_without_writes(tmp_path) -> None:
    config_path = tmp_path / "lark-explore.json"
    write_lark_explore_local_config(
        config_path,
        {
            "board": {
                "base_token": "PUBLIC_FIXTURE_BASE",
                "tables": {"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
            }
        },
    )

    planned = configure_lark_explore_visual_sink(
        config_path=config_path,
        auto_create=True,
    )

    assert planned["status"] == "would_auto_create"
    assert planned["visual_sinks"]["canonical"]["overview_whiteboard_token"] == (
        "planned-canonical-overview"
    )
    assert planned["visual_sinks"]["canonical"]["board_style"] == "auto_flow"
    assert planned["visual_sinks"]["canonical"]["renderer"] == "mermaid"
    assert planned["visual_sinks"]["executive"]["overview_whiteboard_token"] == (
        "planned-executive-overview"
    )
    assert planned["visual_sinks"]["executive"]["stage_boards_enabled"] is False
    stored = read_lark_explore_local_config(config_path)
    assert not stored.get("visual_sinks")
    assert not stored.get("visual_sink")


def test_visual_configuration_auto_create_reuses_existing_document(
    tmp_path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "lark-explore.json"
    write_lark_explore_local_config(
        config_path,
        {
            "board": {
                "base_token": "PUBLIC_FIXTURE_BASE",
                "tables": {"nodes": "tblN", "edges": "tblE", "findings": "tblF"},
            },
            "visual_sinks": {
                "canonical": {
                    "docx_token": "doc_existing",
                    "overview_whiteboard_token": "wb_canonical_overview",
                    "view_role": "canonical",
                    "projection_mode": "canonical_full",
                    "board_style": "auto_flow",
                    "renderer": "mermaid",
                    "stage_boards_enabled": True,
                    "stage_capacity": 14,
                },
                "executive": {
                    "docx_token": "doc_existing",
                    "overview_whiteboard_token": "wb_executive_overview",
                    "view_role": "executive",
                    "projection_mode": "executive_auto",
                    "board_style": "auto_flow",
                    "renderer": "mermaid",
                    "stage_boards_enabled": False,
                    "stage_capacity": 14,
                },
            },
        },
    )
    calls: list[list[str]] = []

    def fail_runner(command, *_args, **_kwargs):
        calls.append(command)
        return {"ok": False, "returncode": 1, "stderr": "should not be invoked"}

    monkeypatch.setattr(
        "loopx.extensions.lark.presentation.explore_results.run_command",
        fail_runner,
    )

    result = configure_lark_explore_visual_sink(
        config_path=config_path,
        auto_create=True,
        execute=True,
    )

    assert result["status"] == "auto_configured"
    assert result["document"]["docx_token"] == "doc_existing"
    assert calls == []
    stored = read_lark_explore_local_config(config_path)
    assert stored["visual_sinks"]["canonical"]["overview_whiteboard_token"] == (
        "wb_canonical_overview"
    )


def test_visual_sync_single_sink_lane_style_falls_back_to_mermaid(
    tmp_path,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    display = dict(bundle["executive"])
    display.pop("svg", None)
    display["mermaid"] = display.get("mermaid") or "flowchart TB\n a[\"A\"]"

    result = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_lane_fixture",
            "view_role": "executive",
            "board_style": "semantic_lane_columns",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=display,
        view_key="executive",
    )

    assert result["ok"] is True
    assert result["style_fallback"] is True
    assert result["board_style"] == "auto_flow"
    assert result["renderer"] == "mermaid"
    assert result["input_format"] == "mermaid"


def test_visual_sync_publishes_overview_board_without_stages(tmp_path) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")

    synced = sync_explore_visuals_to_lark(
        config,
        projection=projection,
        visual_sinks={
            "executive": {
                "whiteboard_token": "wb_executive_fixture",
                "view_role": "executive",
                "overview_whiteboard_token": "wb_executive_overview",
                "stage_boards_enabled": False,
                "stage_capacity": 14,
            }
        },
        config_path=tmp_path / "lark-explore.json",
    )

    view = synced["views"]["executive"]
    assert view["stage_count"] == 0
    assert view["overview"]["ok"] is True
    assert view["overview"]["view_role"] == "executive"
    assert view["overview"]["command"]["command"].startswith(
        "lark-cli whiteboard +update --as user --whiteboard-token wb_executive_overview"
    )


def test_legacy_grid_renderer_config_fails_with_migration_message(tmp_path) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    view = bundle["executive"]

    result = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "renderer": "svg_atlas",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=view,
        view_key="executive",
    )

    assert result["ok"] is False
    assert result["status"] == "invalid_config"
    assert "legacy grid/SVG Explore renderers were removed" in result["error"]


def test_stage_svg_visual_publish_preserves_lane_source_and_remote_marker(
    tmp_path,
) -> None:
    projection = _lane_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(
        projection,
        policy={"stage_node_capacity": 10},
    )
    stage = bundle["canonical"]["stage_views"][0]
    calls: list[list[str]] = []
    published_marker = ""

    def runner(args, cwd, _timeout):
        nonlocal published_marker
        calls.append(args)
        if "+update" in args:
            assert args[args.index("--input_format") + 1] == "svg"
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            assert source.startswith('<svg xmlns="http://www.w3.org/2000/svg"')
            assert "supports" in source
            published_marker = re.search(r"LoopX delivery [0-9a-f]{20}", source).group(
                0
            )
            return {
                "returncode": 0,
                "stdout": json.dumps({"ok": True}),
                "stderr": "",
            }
        assert "+query" in args
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {"nodes": [{"text": {"text": published_marker}}]},
                }
            ),
            "stderr": "",
        }

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_canonical_fixture",
            "view_role": "canonical",
            "board_style": "semantic_lane_columns",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=stage,
        view_key="canonical_stage_01",
        execute=True,
        runner=runner,
    )

    assert len(calls) == 3
    assert synced["ok"] is True
    assert synced["board_style"] == "semantic_lane_columns"
    assert synced["renderer"] == "stage_svg"
    assert synced["input_format"] == "svg"
    assert synced["readback"]["observed_marker"] == published_marker


def test_mermaid_visual_publish_reads_back_remote_delivery_marker(tmp_path) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    calls: list[list[str]] = []
    published_marker = ""

    def runner(args, cwd, _timeout):
        nonlocal published_marker
        calls.append(args)
        if "+update" in args:
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            marker_match = re.search(r"LoopX delivery [0-9a-f]{20}", source)
            assert marker_match
            published_marker = marker_match.group(0)
            return {
                "returncode": 0,
                "stdout": json.dumps({"ok": True}),
                "stderr": "",
            }
        assert "+query" in args
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {
                        "nodes": [
                            {
                                "type": "text_shape",
                                "text": {"text": published_marker},
                            }
                        ]
                    },
                }
            ),
            "stderr": "",
        }

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "renderer": "mermaid",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
        execute=True,
        runner=runner,
    )

    assert len(calls) == 3
    assert synced["ok"] is True
    assert synced["status"] == "published"
    assert synced["published"] is True
    assert synced["readback"]["performed"] is True
    assert synced["readback"]["verified"] is True
    assert synced["readback"]["observed_marker"] == published_marker


def test_visual_publish_reconciles_existing_marker_without_duplicate_update(
    tmp_path,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    sink = {
        "whiteboard_token": "wb_executive_fixture",
        "view_role": "executive",
        "renderer": "mermaid",
    }
    preview = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink=sink,
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
    )
    marker = preview["readback"]["expected_marker"]
    calls: list[list[str]] = []

    def runner(args, _cwd, timeout):
        calls.append(args)
        assert timeout == 10.0
        assert "+query" in args, args
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {"ok": True, "data": {"nodes": [{"text": {"text": marker}}]}}
            ),
            "stderr": "",
        }

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink=sink,
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
        execute=True,
        runner=runner,
    )

    assert len(calls) == 1
    assert synced["ok"] is True
    assert synced["published"] is True
    assert synced["reconciled_existing_delivery"] is True
    assert synced["external_write_performed"] is False
    assert synced["publish_attempt_count"] == 0


def test_visual_publish_timeout_blocks_update_without_repeating_host_wait(
    tmp_path,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    observed_timeouts: list[float | None] = []

    def runner(args, _cwd, timeout):
        assert "+query" in args, args
        observed_timeouts.append(timeout)
        raise subprocess.TimeoutExpired(args, timeout)

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "renderer": "mermaid",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
        execute=True,
        runner=runner,
    )

    assert observed_timeouts == [10.0]
    assert synced["ok"] is False
    assert synced["external_write_performed"] is False
    assert synced["publish_attempt_count"] == 0
    assert synced["prepublish_readback"]["host_wait_exhausted"] is True
    assert synced["prepublish_readback"]["attempt_count"] == 1


def test_mermaid_visual_publish_retries_idempotency_replay_error(tmp_path) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    update_tokens: list[str] = []
    published_marker = ""

    def runner(args, cwd, _timeout):
        nonlocal published_marker
        if "+update" in args:
            update_tokens.append(args[args.index("--idempotent-token") + 1])
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            assert "fill:transparent" not in source
            assert "fill:#ffffff,stroke:#ffffff,color:#ffffff" in source
            if len(update_tokens) == 1:
                payload = {
                    "ok": False,
                    "error": {"code": 2891001, "message": "HE[n] is not iterable"},
                }
                return {
                    "returncode": 1,
                    "stdout": "",
                    "stderr": json.dumps(payload),
                }
            published_marker = re.search(r"LoopX delivery [0-9a-f]{20}", source).group(
                0
            )
            return {
                "returncode": 0,
                "stdout": json.dumps({"ok": True}),
                "stderr": "",
            }
        assert "+query" in args
        return {
            "returncode": 0,
            "stdout": json.dumps(
                {
                    "ok": True,
                    "data": {"nodes": [{"text": {"text": published_marker}}]},
                }
            ),
            "stderr": "",
        }

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "renderer": "mermaid",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
        execute=True,
        runner=runner,
    )

    assert synced["ok"] is True
    assert synced["publish_attempt_count"] == 2
    assert len(update_tokens) == 2
    assert update_tokens[0] != update_tokens[1]
    assert update_tokens[1].startswith("loopx-retry-")
    assert synced["readback"]["verified"] is True


def test_mermaid_visual_readback_retries_lark_doc_applying(
    tmp_path, monkeypatch
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    published_marker = ""
    query_count = 0
    delays: list[float] = []
    monkeypatch.setattr(
        "loopx.extensions.lark.presentation.explore_visual_readback.time.sleep",
        delays.append,
    )

    def runner(args, cwd, _timeout):
        nonlocal published_marker, query_count
        if "+update" in args:
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            published_marker = re.search(
                r"LoopX delivery [0-9a-f]{20}",
                source,
            ).group(0)
            payload = {"ok": True}
        else:
            query_count += 1
            payload = (
                {
                    "ok": False,
                    "error": {
                        "code": 4003101,
                        "message": "doc is applying; doc data is not ready",
                    },
                }
                if query_count == 2
                else {
                    "ok": True,
                    "data": {"nodes": [{"text": {"text": published_marker}}]},
                }
            )
        return {
            "returncode": 0 if payload.get("ok") else 1,
            "stdout": json.dumps(payload) if payload.get("ok") else "",
            "stderr": "" if payload.get("ok") else json.dumps(payload),
        }

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "renderer": "mermaid",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
        execute=True,
        runner=runner,
    )

    assert synced["ok"] is True
    assert synced["published"] is True
    assert synced["readback"]["verified"] is True
    assert synced["readback"]["attempt_count"] == 2
    assert synced["readback"]["attempts"][0]["error_code"] == 4003101
    assert delays == [0.25]


def test_mermaid_visual_readback_retries_until_remote_marker_is_visible(
    tmp_path,
    monkeypatch,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    published_marker = ""
    query_count = 0
    delays: list[float] = []
    monkeypatch.setattr(
        "loopx.extensions.lark.presentation.explore_visual_readback.time.sleep",
        delays.append,
    )

    def runner(args, cwd, _timeout):
        nonlocal published_marker, query_count
        if "+update" in args:
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            published_marker = re.search(
                r"LoopX delivery [0-9a-f]{20}",
                source,
            ).group(0)
            payload = {"ok": True}
        else:
            query_count += 1
            payload = {
                "ok": True,
                "data": {
                    "nodes": [
                        {
                            "text": {
                                "text": (
                                    "stale visual"
                                    if query_count <= 2
                                    else published_marker
                                )
                            }
                        }
                    ]
                },
            }
        return {
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
        }

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "renderer": "mermaid",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
        execute=True,
        runner=runner,
    )

    assert synced["ok"] is True
    assert synced["published"] is True
    assert synced["readback"]["verified"] is True
    assert synced["readback"]["attempt_count"] == 2
    assert synced["readback"]["attempts"][0]["retryable"] is True
    assert delays == [0.25]


def test_mermaid_visual_publish_fails_closed_when_remote_marker_is_missing(
    tmp_path,
    monkeypatch,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)
    monkeypatch.setattr(
        "loopx.extensions.lark.presentation.explore_visual_readback.time.sleep",
        lambda _delay: None,
    )

    def runner(args, _cwd, _timeout):
        payload = (
            {
                "ok": True,
                "data": {"nodes": [{"text": {"text": "stale visual"}}]},
            }
            if "+query" in args
            else {"ok": True}
        )
        return {
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
        }

    synced = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={
            "whiteboard_token": "wb_executive_fixture",
            "view_role": "executive",
            "renderer": "mermaid",
        },
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
        execute=True,
        runner=runner,
    )

    assert synced["ok"] is False
    assert synced["status"] == "publish_unverified"
    assert synced["published"] is False
    assert synced["readback"]["performed"] is True
    assert synced["readback"]["verified"] is False
    assert "expected delivery marker" in synced["error"]


def test_visual_sync_creates_one_document_section_and_board_per_missing_stage(
    tmp_path,
) -> None:
    projection = _complex_projection()
    bundle = build_explore_presentation_bundle(projection)
    stage_count = len(bundle["executive"]["stage_views"])
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    config_path = tmp_path / "lark-explore.json"
    sink = {
        "whiteboard_token": "wb_stage_01",
        "docx_token": "doc_public_fixture",
        "view_role": "executive",
        "renderer": "mermaid",
        "stage_whiteboards": [{"stage_index": 1, "whiteboard_token": "wb_stage_01"}],
    }
    write_lark_explore_local_config(
        config_path,
        {
            "board": {"base_token": "PUBLIC_FIXTURE_BASE"},
            "visual_sinks": {"executive": sink},
        },
    )
    created_stage = 1
    created_headings: set[int] = set()
    published_markers: dict[str, str] = {}

    def runner(args, cwd, _timeout):
        nonlocal created_stage
        if "docs" in args and "+fetch" in args:
            headings = "".join(
                f'<h2 id="heading_stage_{index:02d}">Executive Evidence Stage {index:02d}</h2>'
                for index in sorted(created_headings)
            )
            payload = {
                "ok": True,
                "data": {
                    "document": {
                        "revision_id": 1,
                        "content": (
                            '<fragment mode="outline"><outline>'
                            '<h2 id="heading_stage_01">Evidence Stage 01</h2>'
                            f"{headings}</outline></fragment>"
                        ),
                    }
                },
            }
        elif "docs" in args and "+update" in args:
            created_stage += 1
            created_headings.add(created_stage)
            payload = {
                "ok": True,
                "data": {
                    "document": {
                        "new_blocks": [
                            {
                                "block_id": f"block_stage_{created_stage:02d}",
                                "block_type": "whiteboard",
                                "block_token": f"wb_stage_{created_stage:02d}",
                            }
                        ]
                    }
                },
            }
        elif "+update" in args:
            token = args[args.index("--whiteboard-token") + 1]
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            published_markers[token] = re.search(
                r"LoopX delivery [0-9a-f]{20}", source
            ).group(0)
            payload = {"ok": True}
        else:
            token = args[args.index("--whiteboard-token") + 1]
            payload = {
                "ok": True,
                "data": {
                    "nodes": [{"text": {"text": published_markers.get(token, "")}}]
                },
            }
        return {
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
        }

    synced = sync_explore_visuals_to_lark(
        config,
        projection=projection,
        visual_sinks={"executive": sink},
        config_path=config_path,
        execute=True,
        runner=runner,
    )

    view = synced["views"]["executive"]
    assert synced["ok"] is False
    assert synced["status"] == "sink_unsatisfied"
    assert synced["missing_recommended_roles"] == ["canonical"]
    assert view["ok"] is True
    assert view["stage_count"] == stage_count
    assert len(view["section_commands"]) == stage_count - 1
    assert all(stage["published"] for stage in view["stages"])
    stored = read_lark_explore_local_config(config_path)
    stage_boards = stored["visual_sinks"]["executive"]["stage_whiteboards"]
    assert [item["stage_index"] for item in stage_boards] == list(
        range(1, stage_count + 1)
    )
    assert [item["whiteboard_token"] for item in stage_boards] == [
        f"wb_stage_{index:02d}" for index in range(1, stage_count + 1)
    ]


@pytest.mark.parametrize(
    "persist_created_membership",
    [pytest.param(True, id="recreated"), pytest.param(False, id="readback-missing")],
)
def test_stage_document_recreates_configured_board_missing_from_document(
    tmp_path,
    persist_created_membership,
) -> None:
    projection = _complex_projection()
    bundle = build_explore_presentation_bundle(projection)
    stage_count = len(bundle["executive"]["stage_views"])
    assert stage_count == 2
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    config_path = tmp_path / "lark-explore.json"
    sink = {
        "whiteboard_token": "wb_stage_01",
        "docx_token": "doc_public_fixture",
        "view_role": "executive",
        "renderer": "mermaid",
        "stage_whiteboards": [
            {"stage_index": 1, "whiteboard_token": "wb_stage_01"},
            {"stage_index": 2, "whiteboard_token": "wb_detached_stage_02"},
        ],
    }
    write_lark_explore_local_config(
        config_path,
        {
            "board": {"base_token": "PUBLIC_FIXTURE_BASE"},
            "visual_sinks": {"executive": sink},
        },
    )
    stage_two_present = False
    outline_fetch_count = 0
    published_markers: dict[str, str] = {}

    def runner(args, cwd, _timeout):
        nonlocal outline_fetch_count, stage_two_present
        if "docs" in args and "+fetch" in args:
            outline_fetch_count += 1
            executive_heading = (
                '<h2 id="heading_stage_02">Executive Evidence Stage 02</h2>'
                if stage_two_present
                else ""
            )
            payload = {
                "ok": True,
                "data": {
                    "document": {
                        "revision_id": 2,
                        "content": (
                            '<fragment mode="outline"><outline>'
                            '<h2 id="canonical_stage_02">Evidence Stage 02</h2>'
                            + executive_heading
                            + "</outline></fragment>"
                        ),
                    }
                },
            }
        elif "docs" in args and "+update" in args:
            stage_two_present = persist_created_membership
            payload = {
                "ok": True,
                "data": {
                    "document": {
                        "new_blocks": [
                            {
                                "block_id": "block_stage_02_recreated",
                                "block_type": "whiteboard",
                                "block_token": "wb_stage_02_recreated",
                            }
                        ]
                    }
                },
            }
        elif "+update" in args:
            token = args[args.index("--whiteboard-token") + 1]
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            published_markers[token] = re.search(
                r"LoopX delivery [0-9a-f]{20}", source
            ).group(0)
            payload = {"ok": True}
        else:
            token = args[args.index("--whiteboard-token") + 1]
            payload = {
                "ok": True,
                "data": {
                    "nodes": [{"text": {"text": published_markers.get(token, "")}}]
                },
            }
        return {
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
        }

    synced = sync_explore_visuals_to_lark(
        config,
        projection=projection,
        visual_sinks={"executive": sink},
        config_path=config_path,
        execute=True,
        runner=runner,
    )

    view = synced["views"]["executive"]
    if not persist_created_membership:
        assert synced["ok"] is False
        assert view["status"] == "stage_whiteboards_missing"
        assert "absent from document readback" in view["error"]
        assert outline_fetch_count == 2
        return

    assert synced["ok"] is False
    assert synced["status"] == "sink_unsatisfied"
    assert synced["missing_recommended_roles"] == ["canonical"]
    assert view["ok"] is True
    assert len(view["section_commands"]) == 1
    assert view["reconciliation"]["missing_remote_stage_indexes"] == [2]
    assert view["reconciliation"]["performed"] is True
    assert outline_fetch_count == 2
    stored = read_lark_explore_local_config(config_path)
    stage_boards = stored["visual_sinks"]["executive"]["stage_whiteboards"]
    assert stage_boards[1]["whiteboard_token"] == "wb_stage_02_recreated"


def test_stage_document_reconciliation_removes_all_stale_duplicate_sections(
    tmp_path,
) -> None:
    projection = _complex_projection()
    bundle = build_explore_presentation_bundle(projection)
    stage_count = len(bundle["canonical"]["stage_views"])
    stale_index = stage_count + 1
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    config_path = tmp_path / "lark-explore.json"
    sink = {
        "docx_token": "doc_public_fixture",
        "view_role": "canonical",
        "renderer": "mermaid",
        "stage_whiteboards": [
            {
                "stage_index": index,
                "whiteboard_token": f"wb_stage_{index:02d}",
            }
            for index in range(1, stale_index + 1)
        ],
    }
    write_lark_explore_local_config(
        config_path,
        {
            "board": {"base_token": "PUBLIC_FIXTURE_BASE"},
            "visual_sinks": {"canonical": sink},
        },
    )
    stale_headings = [f"stale_heading_{number}" for number in range(1, 4)]
    deleted_block_ids: list[str] = []
    published_markers: dict[str, str] = {}
    whiteboard_token_attribute = "to" + "ken"

    def outline_xml() -> str:
        headings = [
            f'<h2 id="heading_stage_{index:02d}">Evidence Stage {index:02d}</h2>'
            for index in range(1, stage_count + 1)
        ]
        headings.extend(
            f'<h2 id="{heading}">Evidence Stage {stale_index:02d}</h2>'
            for heading in stale_headings
        )
        return (
            '<fragment mode="outline"><outline>'
            + "".join(headings)
            + "</outline></fragment>"
        )

    def runner(args, cwd, _timeout):
        if "docs" in args and "+fetch" in args and "outline" in args:
            payload = {
                "ok": True,
                "data": {
                    "document": {
                        "revision_id": 7,
                        "content": outline_xml(),
                    }
                },
            }
        elif "docs" in args and "+fetch" in args and "section" in args:
            heading = args[args.index("--start-block-id") + 1]
            number = heading.rsplit("_", 1)[-1]
            payload = {
                "ok": True,
                "data": {
                    "document": {
                        "revision_id": 7,
                        "content": (
                            f'<fragment mode="section"><h2 id="{heading}">'
                            f"Evidence Stage {stale_index:02d}</h2>"
                            f'<p id="stale_paragraph_{number}">本阶段包含 1 个主节点、'
                            "0 个关系上下文节点；主线：Explore work；跨主线真实关系：0 条。"
                            "完整 Nodes / Edges / Findings 仍以同一 Base 为准。</p>"
                            f'<whiteboard id="stale_board_{number}" '
                            f'{whiteboard_token_attribute}="wb_stale_{number}">'
                            "</whiteboard></fragment>"
                        ),
                    }
                },
            }
        elif "docs" in args and "+update" in args:
            deleted_block_ids.extend(args[args.index("--block-id") + 1].split(","))
            stale_headings.clear()
            payload = {
                "ok": True,
                "data": {"document": {"revision_id": 8}},
            }
        elif "+update" in args:
            token = args[args.index("--whiteboard-token") + 1]
            source_arg = args[args.index("--source") + 1]
            source = (cwd / source_arg.removeprefix("@")).read_text(encoding="utf-8")
            published_markers[token] = re.search(
                r"LoopX delivery [0-9a-f]{20}", source
            ).group(0)
            payload = {"ok": True}
        else:
            token = args[args.index("--whiteboard-token") + 1]
            payload = {
                "ok": True,
                "data": {
                    "nodes": [{"text": {"text": published_markers.get(token, "")}}]
                },
            }
        return {
            "returncode": 0,
            "stdout": json.dumps(payload),
            "stderr": "",
        }

    synced = sync_explore_visuals_to_lark(
        config,
        projection=projection,
        visual_sinks={"canonical": sink},
        config_path=config_path,
        execute=True,
        runner=runner,
    )

    view = synced["views"]["canonical"]
    assert synced["ok"] is False
    assert synced["status"] == "sink_unsatisfied"
    assert synced["missing_recommended_roles"] == ["executive"]
    assert view["ok"] is True
    assert view["reconciliation"] == {
        "required": False,
        "performed": True,
        "remote_checked": True,
        "stale_stage_indexes": [stale_index],
        "missing_remote_stage_indexes": [],
        "duplicate_stage_indexes": [],
        "adopted_stage_indexes": [],
        "deleted_section_count": 3,
        "deleted_block_count": 9,
    }
    assert len(deleted_block_ids) == 9
    stored = read_lark_explore_local_config(config_path)
    assert [
        item["stage_index"]
        for item in stored["visual_sinks"]["canonical"]["stage_whiteboards"]
    ] == list(range(1, stage_count + 1))


def test_stage_document_preview_surfaces_stale_config_reconciliation(tmp_path) -> None:
    projection = _complex_projection()
    bundle = build_explore_presentation_bundle(projection)
    stage_count = len(bundle["executive"]["stage_views"])
    sink = {
        "docx_token": "doc_public_fixture",
        "view_role": "executive",
        "stage_whiteboards": [
            {
                "stage_index": index,
                "whiteboard_token": f"wb_stage_{index:02d}",
            }
            for index in range(1, stage_count + 2)
        ],
    }

    preview = sync_explore_visuals_to_lark(
        LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE"),
        projection=projection,
        visual_sinks={"executive": sink},
        config_path=tmp_path / "lark-explore.json",
    )

    reconciliation = preview["views"]["executive"]["reconciliation"]
    assert reconciliation["required"] is True
    assert reconciliation["performed"] is False
    assert reconciliation["stale_stage_indexes"] == [stage_count + 1]


def test_visual_delivery_digest_changes_when_target_whiteboard_changes(
    tmp_path,
) -> None:
    projection = _complex_projection()
    config = LarkExploreConfig(base_token="PUBLIC_FIXTURE_BASE")
    bundle = build_explore_presentation_bundle(projection)

    first = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={"whiteboard_token": "wb_first", "view_role": "executive"},
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
    )
    changed = sync_explore_visual_to_lark(
        config,
        projection=projection,
        visual_sink={"whiteboard_token": "wb_second", "view_role": "executive"},
        config_path=tmp_path / "lark-explore.json",
        semantic_digest=bundle["source_digest"],
        display_projection=bundle["executive"],
        view_key="executive",
    )

    assert first["source_digest"] == changed["source_digest"]
    assert first["delivery_digest"] != changed["delivery_digest"]
    assert first["command"]["command"] != changed["command"]["command"]
