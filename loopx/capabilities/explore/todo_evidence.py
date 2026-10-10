from __future__ import annotations

from collections import Counter
from typing import Any, Mapping

from ...control_plane.todos.contract import normalize_explore_result_node_refs


TODO_EVIDENCE_AUDIT_SCHEMA_VERSION = "loopx_explore_todo_evidence_audit_v0"
MAX_AUDIT_FINDINGS = 24
MAX_AUDIT_EDGES = 24
RELEVANT_EDGE_TYPES = {"supports", "refutes"}


def _compact_node(node: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "node_id": str(node.get("node_id") or ""),
        "status": str(node.get("status") or "open"),
        "node_kind": str(node.get("node_kind") or "area"),
        "title": str(node.get("title") or ""),
        "finding_count": int(node.get("finding_count") or 0),
    }


def _compact_finding(finding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": str(finding.get("finding_id") or ""),
        "node_id": str(finding.get("node_id") or ""),
        "status": str(finding.get("status") or "tentative"),
        "confidence": finding.get("confidence"),
        "finding": str(finding.get("finding") or ""),
    }


def _compact_edge(edge: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": str(edge.get("edge_id") or ""),
        "from_node": str(edge.get("from_node") or ""),
        "to_node": str(edge.get("to_node") or ""),
        "edge_type": str(edge.get("edge_type") or ""),
        "confidence": edge.get("confidence"),
    }


def build_todo_typed_evidence_audit(
    todo: Mapping[str, Any],
    projection: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Resolve only explicit todo-to-node links into bounded diagnostics.

    The audit is deliberately advisory. It never changes planner score or
    authority, and unlinked todos preserve the pre-linkage planner shape.
    """

    requested_refs = normalize_explore_result_node_refs(
        todo.get("explore_result_node_refs")
    )
    if not requested_refs:
        return None

    projection = projection if isinstance(projection, Mapping) else {}
    nodes_by_id = {
        str(node.get("node_id") or ""): node
        for node in projection.get("nodes") or []
        if isinstance(node, Mapping) and node.get("node_id")
    }
    linked_nodes = [nodes_by_id[ref] for ref in requested_refs if ref in nodes_by_id]
    linked_ids = {str(node.get("node_id") or "") for node in linked_nodes}
    unknown_refs = [ref for ref in requested_refs if ref not in nodes_by_id]

    findings = [
        finding
        for finding in projection.get("findings") or []
        if isinstance(finding, Mapping)
        and str(finding.get("node_id") or "") in linked_ids
    ]
    edges = [
        edge
        for edge in projection.get("edges") or []
        if isinstance(edge, Mapping)
        and str(edge.get("edge_type") or "") in RELEVANT_EDGE_TYPES
        and (
            str(edge.get("from_node") or "") in linked_ids
            or str(edge.get("to_node") or "") in linked_ids
        )
    ]

    # Classify every linked row in the supplied projection; detail budgets must
    # not erase a hazard or change the meaning of the status counts.
    node_statuses = Counter(str(node.get("status") or "open") for node in linked_nodes)
    finding_statuses = Counter(
        str(finding.get("status") or "tentative") for finding in findings
    )
    edge_types = Counter(str(edge.get("edge_type") or "") for edge in edges)
    reason_codes = ["explicit_result_node_link"]
    hazards: list[str] = []
    if linked_nodes:
        reason_codes.append("linked_nodes_resolved")
    if finding_statuses.get("confirmed"):
        reason_codes.append("confirmed_finding_present")
    if edge_types.get("supports"):
        reason_codes.append("support_edge_present")
    if unknown_refs:
        hazards.append("unknown_result_node_ref")
    if node_statuses.get("dead_end"):
        hazards.append("linked_node_dead_end")
    if finding_statuses.get("refuted"):
        hazards.append("linked_finding_refuted")
    if edge_types.get("refutes"):
        hazards.append("refute_edge_present")

    return {
        "schema_version": TODO_EVIDENCE_AUDIT_SCHEMA_VERSION,
        "mode": "diagnostic_only",
        "score_delta": 0.0,
        "requested_node_refs": requested_refs,
        "resolved_node_refs": [str(node.get("node_id") or "") for node in linked_nodes],
        "unknown_node_refs": unknown_refs,
        "nodes": [_compact_node(node) for node in linked_nodes],
        "findings": [_compact_finding(finding) for finding in findings[:MAX_AUDIT_FINDINGS]],
        "relevant_edges": [_compact_edge(edge) for edge in edges[:MAX_AUDIT_EDGES]],
        "status_counts": {
            "nodes": dict(sorted(node_statuses.items())),
            "findings": dict(sorted(finding_statuses.items())),
            "edges": dict(sorted(edge_types.items())),
        },
        "reason_codes": reason_codes,
        "hazards": hazards,
        "repair_hint": (
            "Replace unknown ids with existing Explore node ids, or run `loopx todo update "
            "--clear-explore-result-node-refs` to unlink this todo."
            if unknown_refs
            else ""
        ),
        "boundary": {
            "changes_score": False,
            "writes_state": False,
            "claims_todos": False,
            "acquires_leases": False,
            "starts_agents": False,
            "changes_quota": False,
        },
    }
