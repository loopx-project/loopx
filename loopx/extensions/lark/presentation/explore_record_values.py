"""Pure values for the existing Explore Base tables; no transport or policy.

Row identity and lifecycle are unchanged by optional research readback.
"""
from typing import Any, Mapping

_GOAL_ID_FIELD = "LoopX Goal ID"
_RESULT_ID_FIELD = "LoopX Result ID"


def _joined(values: Any) -> str:
    if isinstance(values, list):
        return ", ".join(str(item) for item in values if str(item or "").strip())
    return str(values or "")


def _lifecycle_values(item: Mapping[str, Any], *, source_id: str) -> dict[str, Any]:
    return {
        "Source ID": source_id,
        "Row Lifecycle": "superseded" if str(item.get("superseded_by") or "") else "current",
        "Supersedes": _joined(item.get("supersedes")),
        "Superseded By": str(item.get("superseded_by") or ""),
    }


def _node_record_values(node: Mapping[str, Any], *, goal_id: str, source_id: str) -> dict[str, Any]:
    return {
        "Title": str(node.get("title") or ""),
        "Kind": str(node.get("node_kind") or ""),
        "Status": str(node.get("status") or ""),
        "Summary": "\n".join(part for part in [str(node.get("summary") or ""), str(node.get("research_summary") or "")] if part),
        "Blocked Reason": str(node.get("blocked_reason") or ""),
        "Parent Node": str(node.get("parent_id") or ""),
        "Findings": node.get("finding_count"),
        "Evidence Refs": _joined(node.get("evidence_refs")),
        "Tags": _joined(node.get("tags")),
        "Agent ID": str(node.get("agent_id") or ""),
        "First Recorded At": str(node.get("first_recorded_at") or ""),
        "Last Updated At": str(node.get("last_updated_at") or ""),
        _GOAL_ID_FIELD: goal_id,
        _RESULT_ID_FIELD: str(node.get("node_id") or ""),
        **_lifecycle_values(node, source_id=source_id),
    }


def _edge_record_values(edge: Mapping[str, Any], *, goal_id: str, source_id: str) -> dict[str, Any]:
    return {
        "From Node": str(edge.get("from_node") or ""),
        "To Node": str(edge.get("to_node") or ""),
        "Type": str(edge.get("edge_type") or ""),
        "Confidence": edge.get("confidence"),
        "Condition": str(edge.get("summary") or ""),
        "State Transition": str(edge.get("edge_type") or ""),
        "Summary": str(edge.get("summary") or ""),
        "Last Updated At": str(edge.get("last_updated_at") or ""),
        _GOAL_ID_FIELD: goal_id,
        _RESULT_ID_FIELD: str(edge.get("edge_id") or ""),
        "Source ID": source_id,
    }


def _finding_summary(finding: Mapping[str, Any]) -> str:
    summary = str(finding.get("summary") or "")
    capture = finding.get("work_yardstick")
    if not isinstance(capture, Mapping):
        return summary
    # Projection only: the canonical event/typed owner has validated this
    # declaration. A Lark row does not upgrade it to acceptance or authority.
    decision = capture["decision"]
    target, outcome, proxy = decision["target"], decision["authoritative"], decision["proxy"]
    next_action = decision["next_action"]
    details = [f"Target: {target['summary']}", f"Scope: {target['scope']}",
        f"Outcome metric: {outcome['metric']} ({outcome['unit']}); {outcome['comparison_scope']}; {outcome['input_revision']}; {outcome['evaluation_ref']}",
        f"Proxy: {proxy['metric']} ({proxy['unit']}); {proxy['comparison_scope']}; {proxy['input_revision']}; {proxy['evaluation_ref']}" if proxy else "Proxy: none (direct evaluation)",
        f"Acceptance source: {target['acceptance_ref']}",
        f"Next action: {next_action['reason']} ({next_action['method_id']})", f"Cost bound: {next_action['cost_bound']}",
        f"Task: {capture['binding']['todo_id']}"]
    if decision["counterevidence_refs"]:
        details.append("Proxy counterevidence: " + ", ".join(decision["counterevidence_refs"]))
    details.append("Recorded research decision; fidelity and acceptance remain independently assessed.")
    return "\n".join([summary, *details])


def _finding_record_values(finding: Mapping[str, Any], *, goal_id: str, source_id: str) -> dict[str, Any]:
    return {
        "Finding": str(finding.get("finding") or ""),
        "Summary": _finding_summary(finding),
        "Status": str(finding.get("status") or ""),
        "Confidence": finding.get("confidence"),
        "Node": str(finding.get("node_id") or ""),
        "Evidence Refs": _joined(finding.get("evidence_refs")),
        "Tags": _joined(finding.get("tags")),
        "Agent ID": str(finding.get("agent_id") or ""),
        "First Recorded At": str(finding.get("first_recorded_at") or ""),
        "Last Updated At": str(finding.get("last_updated_at") or ""),
        _GOAL_ID_FIELD: goal_id,
        _RESULT_ID_FIELD: str(finding.get("finding_id") or ""),
        **_lifecycle_values(finding, source_id=source_id),
    }
