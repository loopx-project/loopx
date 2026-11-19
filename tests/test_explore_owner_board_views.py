"""Thin pytest: explore owner-board views — canonical/executive/dual modes,
decision lineage, evidence lineage, semantic lanes, stage views, freshness.

Covers GH-C69 core assertions.
"""

from __future__ import annotations

import json
import re

from tests.test_explore_mermaid_identity import mermaid_node_id

from loopx.presentation.explore_views import (
    PRESENTATION_MODE_CANONICAL_ONLY,
    PRESENTATION_MODE_DUAL_VIEW,
    build_explore_presentation_bundle,
    explore_source_digest,
    validate_explore_view_freshness,
)

_PRIVATE_RE = [
    re.compile(r"/Users/[A-Za-z0-9._-]+/"),
    re.compile(r"/home/[A-Za-z0-9._-]+/"),
    re.compile(r"\bBearer" + r"\s+[A-Za-z0-9._-]+"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\bghp_[A-Za-z0-9]{36}\b"),
    re.compile(r"\bsk-[A-Za-z0-9]{32,}\b"),
]


def _public_safe(obj: object) -> bool:
    text = json.dumps(obj, sort_keys=True) if not isinstance(obj, str) else obj
    return not any(p.search(text) for p in _PRIVATE_RE)


def _node(nid, title="", node_kind="experiment", status="resolved", parent_id="", tags=None, summary="", blocked_reason=""):
    return {"node_id": nid, "title": title or f"Node {nid}", "node_kind": node_kind,
            "status": status, "parent_id": parent_id, "tags": tags or [],
            "summary": summary, "blocked_reason": blocked_reason}


def _edge(frm, to, edge_type="supports"):
    return {"edge_id": f"edge-{frm}-{to}", "from_node": frm, "to_node": to, "edge_type": edge_type}


def _finding(fid, nid, finding="", status="confirmed", confidence=0.8):
    return {"finding_id": fid, "finding": finding or f"Finding for {nid}",
            "node_id": nid, "status": status, "confidence": confidence}


def _proj(**kw):
    base = {"ok": True, "goal_id": "g:explore", "source_event_count": 0,
            "nodes": [], "edges": [], "findings": []}
    base.update(kw)
    base["source_event_count"] = len(base["nodes"]) + len(base["edges"]) + len(base["findings"])
    return base


def _decision_tree():
    nodes = [
        _node("root", title="SCADE tool landscape", node_kind="area", status="exploring", tags=["decision"]),
        _node("cand-a", parent_id="root", title="Lustre toolchain", status="exploring",
              tags=["current-best"], summary="Mature ecosystem."),
        _node("cand-b", parent_id="root", title="Custom DSL", status="dead_end",
              tags=["counterevidence"], summary="Vendor unclear.", blocked_reason="Licence unclear"),
        _node("eval-l", parent_id="cand-a", title="Lustre eval", status="resolved",
              tags=["baseline"], summary="+31.2/+72.4 bp, composite +51.8 bp."),
        _node("eval-c", parent_id="cand-b", title="Custom eval", status="dead_end",
              tags=["counterevidence"], summary="Failed: -1.2 bp below threshold."),
    ]
    edges = [_edge("cand-a", "root", "subtopic_of"), _edge("cand-b", "root", "subtopic_of"),
             _edge("eval-l", "cand-a", "supports"), _edge("eval-c", "cand-b", "refutes"),
             _edge("cand-a", "cand-b", "leads_to")]
    findings = [_finding("f-l", "cand-a", finding="Two open-source Lustre toolchains cover KCG core.", confidence=0.85),
                _finding("f-c", "cand-b", finding="Custom DSL requires proprietary compiler.", confidence=0.90)]
    return _proj(nodes=nodes, edges=edges, findings=findings)


def _complex_proj():
    nodes = [_node("root", title="Explore pilot", node_kind="area", status="open", tags=["decision"])]
    edges = []
    for i in range(8):
        bid = f"b-{i}"
        nodes.append(_node(bid, parent_id="root", title=f"Branch {i}"))
        edges.append(_edge(bid, "root", "subtopic_of"))
        for j in range(4):
            lid = f"leaf-{i}-{j}"
            nodes.append(_node(lid, parent_id=bid, status="dead_end", title=f"Dead leaf {i}.{j}"))
            edges.append(_edge(lid, bid, "subtopic_of"))
    nodes.append(_node("cand", parent_id="root", title="Best candidate", status="exploring", tags=["current-best"]))
    edges.append(_edge("cand", "root", "supports"))
    return _proj(nodes=nodes, edges=edges)


class TestPresentationModes:
    def test_small_graph_canonical_only(self):
        proj = _decision_tree()
        bundle = build_explore_presentation_bundle(proj)
        assert bundle["presentation_mode"] == PRESENTATION_MODE_CANONICAL_ONLY
        assert bundle["canonical"]["graph_counts"]["node_count"] == len(proj["nodes"])
        assert bundle["canonical"]["graph_counts"]["edge_count"] == len(proj["edges"])
        assert bundle["canonical"]["filter"]["truncated"] is False
        assert _public_safe(bundle)

    def test_complex_graph_dual_view(self):
        proj = _complex_proj()
        bundle = build_explore_presentation_bundle(proj)
        assert bundle["presentation_mode"] == PRESENTATION_MODE_DUAL_VIEW
        assert {"low_decision_density", "excessive_terminal_branches"}.issubset(bundle["reason_codes"])
        assert bundle["canonical"]["source_digest"] == bundle["executive"]["source_digest"]
        assert _public_safe(bundle)


class TestCanonicalView:
    def test_mermaid_has_expected_structure(self):
        proj = _decision_tree()
        bundle = build_explore_presentation_bundle(proj)
        canonical = bundle["canonical"]
        mermaid = canonical["mermaid"]
        assert mermaid.startswith("flowchart TB")
        assert canonical["filter"]["layout"]["strategy"] == "vertical_evidence_timeline"
        titles = {node["node_id"]: node["title"] for node in proj["nodes"]}
        # Non-subtopic_of edges appear
        for edge in proj["edges"]:
            if edge["edge_type"] == "subtopic_of":
                continue
            src = mermaid_node_id(mermaid, titles[edge["from_node"]])
            dst = mermaid_node_id(mermaid, titles[edge["to_node"]])
            assert f"{src} -->|{edge['edge_type']}| {dst}" in mermaid
        assert _public_safe(canonical)


class TestExecutiveView:
    def test_suppresses_hub_scaffolding(self):
        nodes = [_node("hub", title="Decision hub", node_kind="area", status="open", tags=["decision"])]
        edges = []
        for i in range(6):
            aid = f"active-{i}"
            nodes.append(_node(aid, parent_id="hub", title=f"Option {i}", status="exploring"))
            edges.append(_edge("hub", aid, "subtopic_of"))
        edges.append(_edge("active-0", "active-1", "leads_to"))
        proj = _proj(nodes=nodes, edges=edges)
        bundle = build_explore_presentation_bundle(proj)
        assert bundle["canonical"]["graph_counts"]["edge_count"] == 7
        exec_view = bundle["executive"]
        assert exec_view["graph_counts"]["edge_count"] == 1
        assert exec_view["graph_counts"]["suppressed_edge_count"] == 6
        source = mermaid_node_id(exec_view["mermaid"], "Option 0")
        target = mermaid_node_id(exec_view["mermaid"], "Option 1")
        assert f"{source} -->|leads_to| {target}" in exec_view["mermaid"]
        assert "-->|subtopic_of|" not in exec_view["mermaid"]


class TestSemanticLanes:
    def test_lane_view_with_cross_lane_edges(self):
        nodes = [
            _node("root", title="Explore pilot", node_kind="area", status="exploring"),
            _node("del", parent_id="root", title="Delivery lane", node_kind="area",
                  status="exploring", tags=["lane-delivery"]),
            _node("cap", parent_id="root", title="Capability lane", node_kind="area",
                  status="exploring", tags=["lane-capability"]),
            _node("fix", parent_id="del", title="Fix PR", node_kind="experiment",
                  status="exploring", tags=["open-pr"],
                  summary="Restore stats API isolation."),
            _node("dur", parent_id="cap", title="Durable capability", node_kind="experiment",
                  status="resolved", tags=["incumbent"]),
        ]
        edges = [_edge("fix", "dur", "supports"), _edge("fix", "del", "subtopic_of")]
        proj = _proj(nodes=nodes, edges=edges)
        bundle = build_explore_presentation_bundle(proj, policy={"stage_node_capacity": 10})
        stage = bundle["canonical"]["stage_views"][0]
        assert stage["lane_count"] == 2
        assert stage["cross_lane_edge_count"] == 1
        source = mermaid_node_id(stage["mermaid"], "Fix PR")
        target = mermaid_node_id(stage["mermaid"], "Durable capability")
        assert f"{source} -->|supports| {target}" in stage["mermaid"]
        assert _public_safe(stage)

    def test_single_lane_does_not_invent_second(self):
        proj = _decision_tree()
        for n in proj["nodes"]:
            n["tags"] = ["lane-retrieval"]
        for n in proj["nodes"]:
            if n["node_id"] != "root":
                n["parent_id"] = "root"
        bundle = build_explore_presentation_bundle(proj)
        stage = bundle["canonical"]["stage_views"][0]
        assert stage["lane_count"] == 1


class TestStageViews:
    def test_large_graph_split_into_stages(self):
        bundle = build_explore_presentation_bundle(_complex_proj(), policy={"stage_node_capacity": 14})
        stages = bundle["executive"]["stage_views"]
        assert len(stages) >= 2
        for stage in stages:
            assert 1 <= stage["node_count"] <= 14
            assert stage["mermaid"].startswith("flowchart TB")


class TestReadability:
    def test_flat_graph_fails_readability(self):
        nodes = [_node(f"r-{i}") for i in range(80)]
        proj = _proj(nodes=nodes, edges=[])
        bundle = build_explore_presentation_bundle(proj)
        assert "readability_check_failed" in bundle["reason_codes"]
        assert bundle["assessment"]["readability_check"]["failed"] is True


class TestFreshness:
    def test_source_digest_deterministic(self):
        proj = _decision_tree()
        d1 = explore_source_digest(proj)
        d2 = explore_source_digest(json.loads(json.dumps(proj)))
        assert d1 == d2
        changed = json.loads(json.dumps(proj))
        changed["nodes"][0]["title"] = "Changed"
        assert explore_source_digest(changed) != d1

    def test_stale_view_rejected(self):
        proj = _decision_tree()
        bundle = build_explore_presentation_bundle(proj)
        changed = json.loads(json.dumps(proj))
        changed["findings"][0]["finding"] = "Updated evidence"
        assert explore_source_digest(changed) != bundle["source_digest"]
        freshness = validate_explore_view_freshness(changed, bundle["executive"])
        assert freshness["fresh"] is False


class TestPublicSafety:
    def test_no_external_sink(self):
        proj = _decision_tree()
        bundle = build_explore_presentation_bundle(proj)
        serialized = json.dumps(bundle, ensure_ascii=False)
        assert "api_key" not in serialized.lower()
        for label, p in [("bundle", bundle), ("canonical", bundle["canonical"]),
                         ("executive", bundle["executive"])]:
            assert _public_safe(p), label
