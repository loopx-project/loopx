from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import sys

import pytest

from loopx.capabilities.explore.result_log import build_explore_graph_view
from loopx.presentation.explore_views import build_explore_presentation_bundle


def mermaid_node_id(source: str, title: str) -> str:
    """Find a node by its visible title without duplicating the ID encoder."""
    match = re.search(
        rf'^\s+(\w+)\["{re.escape(title)}(?: ·| \(|<br/>|")', source, re.MULTILINE
    )
    assert match is not None, title
    return match.group(1)


def _projection(node_ids: list[str]) -> dict:
    return {
        "goal_id": "identity-fixture",
        "nodes": [
            {"node_id": node_id, "title": f"Route {index}", "node_kind": "experiment",
             "status": "exploring", "tags": ["decision", "selected"] if index % 2 == 0 else ["decision"]}
            for index, node_id in enumerate(node_ids)
        ],
        "edges": [
            {"edge_id": f"edge-{index}", "from_node": source, "to_node": target,
             "edge_type": "depends_on"}
            for index, (source, target) in enumerate(zip(node_ids, node_ids[1:]))
        ],
        "findings": [],
    }


def _assert_topology(source: str, nodes: list[dict], edges: list[dict]) -> dict[str, str]:
    ids = {node["node_id"]: mermaid_node_id(source, node["title"]) for node in nodes}
    assert len(set(ids.values())) == len(nodes), "Distinct records must remain distinct nodes"
    assert all(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value) for value in ids.values())
    assert "end" not in ids.values(), "A valid Explore ID must not become a Mermaid keyword"
    groups = set(re.findall(r'^\s*subgraph\s+(\w+)\[', source, re.MULTILINE))
    assert groups.isdisjoint(ids.values()), "Data nodes must not alias layout containers"
    declarations = re.findall(r'^\s+(\w+)\[".*"\]:::', source, re.MULTILINE)
    assert set(declarations) == set(ids.values())
    actual_edges = re.findall(r'^\s+(\w+) -->\|([^|]+)\| (\w+)$', source, re.MULTILINE)
    expected_edges = [(ids[edge["from_node"]], edge["edge_type"], ids[edge["to_node"]]) for edge in edges]
    assert sorted(actual_edges) == sorted(expected_edges)
    return ids


@pytest.mark.parametrize("view", ["graph", "canonical", "executive", "canonical_stage", "executive_stage"])
@pytest.mark.parametrize("node_ids", [
    ["route-a", "route_a", "route.a", "route:a", "route_2da", "n_726f7574652d61"],
    ["end", "canonical_timeline", "canonical_group_1", "canonical_stage_1",
     "canonical_stage_1_lane_1", "executive_timeline", "executive_group_1",
     "executive_stage_1", "executive_stage_1_lane_1"],
    ["Alpha2", "R" + "-" * 95, "R" + "_" * 95],
], ids=["punctuation", "reserved-and-layout", "maximum-length"])
def test_explore_mermaid_preserves_node_identity_and_edges(view: str, node_ids: list[str]) -> None:
    projection = _projection(node_ids)
    original = copy.deepcopy(projection)
    graph = build_explore_graph_view(projection["nodes"], projection["edges"])
    graph_ids = {node["node_id"]: mermaid_node_id(graph["mermaid"], node["title"]) for node in projection["nodes"]}
    if view == "graph":
        source = graph["mermaid"]
    else:
        bundle = build_explore_presentation_bundle(projection, policy={"stage_node_capacity": 20})
        role = view.removesuffix("_stage")
        source = bundle[role]["stage_views"][0]["mermaid"] if view.endswith("_stage") else bundle[role]["mermaid"]
        assert bundle[role]["nodes"]
        assert {node["node_id"] for node in bundle[role]["nodes"]} == set(node_ids)
    assert _assert_topology(source, projection["nodes"], projection["edges"]) == graph_ids
    assert projection == original


@pytest.mark.parametrize("selection", ["filter", "limit", "reverse"])
def test_explore_mermaid_identity_survives_graph_selection(selection: str) -> None:
    projection = _projection(["route-a", "route_a", "route.a", "route:a"])
    nodes, edges = projection["nodes"], projection["edges"]
    complete = build_explore_graph_view(nodes, edges)
    complete_ids = _assert_topology(complete["mermaid"], nodes, edges)
    options = {"tags": ["selected"], "include_ancestors": False} if selection == "filter" else {}
    if selection == "limit":
        options["node_limit"] = 2
    if selection == "reverse":
        nodes = list(reversed(nodes))
    selected = build_explore_graph_view(nodes, edges, **options)
    shown_nodes = selected["nodes"][:2] if selection == "limit" else selected["nodes"]
    shown_ids = {node["node_id"] for node in shown_nodes}
    shown_edges = [edge for edge in selected["edges"] if edge["from_node"] in shown_ids and edge["to_node"] in shown_ids]
    assert _assert_topology(selected["mermaid"], shown_nodes, shown_edges) == {
        node_id: complete_ids[node_id] for node_id in shown_ids
    }


def test_explore_cli_preserves_colliding_records_in_graph_and_presentation(tmp_path) -> None:
    registry = tmp_path / "registry.json"
    runtime = tmp_path / "runtime"
    registry.write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(runtime),
        "goals": [{"id": "research", "status": "active", "repo": str(tmp_path),
                   "state_file": "goal.md", "explore_graph": {"enabled": True}}],
    }))
    (tmp_path / "goal.md").write_text("# Synthetic Goal\n\n## Agent Todos\n")

    def run(*args: str) -> dict:
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
             "--runtime-root", str(runtime), "--format", "json", "explore", *args],
            capture_output=True, text=True, timeout=45,
            env={**os.environ, "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "config"),
                 "LOOPX_RUNTIME_ROOT": str(runtime)},
        )
        assert result.returncode == 0, result.stderr or result.stdout
        return json.loads(result.stdout)

    for node_id, title in [("route-a", "First route"), ("route_a", "Second route")]:
        run("node", "--goal-id", "research", "--node-id", node_id, "--title", title, "--tag", "decision")
    run("edge", "--goal-id", "research", "--from", "route-a", "--to", "route_a", "--type", "depends_on")
    graph = run("graph", "--goal-id", "research")
    assert {node["node_id"] for node in graph["nodes"]} == {"route-a", "route_a"}
    assert [(edge["from_node"], edge["to_node"]) for edge in graph["edges"]] == [("route-a", "route_a")]
    ids = _assert_topology(graph["mermaid"], graph["nodes"], graph["edges"])
    bundle = run("presentation", "--goal-id", "research")
    for role in ("canonical", "executive"):
        view = bundle[role]
        assert _assert_topology(view["mermaid"], view["nodes"], view["edges"]) == ids
        assert _assert_topology(view["stage_views"][0]["mermaid"], view["nodes"], view["edges"]) == ids
