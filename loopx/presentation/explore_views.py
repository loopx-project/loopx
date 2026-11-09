"""Same-source canonical and executive views over Explore evidence.

The Explore result projection remains the only evidence source.  This module
adds presentation advice and derived display views; it never mutates or
truncates the canonical node, edge, or finding collections.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from ..capabilities.explore.result_log import explore_mermaid_node_id

EXPLORE_PRESENTATION_BUNDLE_VERSION = "loopx_explore_presentation_bundle_v0"
EXPLORE_PRESENTATION_ASSESSMENT_VERSION = "loopx_explore_presentation_assessment_v0"
EXPLORE_CANONICAL_VIEW_VERSION = "loopx_explore_canonical_view_v0"
EXPLORE_EXECUTIVE_VIEW_VERSION = "loopx_explore_executive_view_v0"

PRESENTATION_MODE_CANONICAL_ONLY = "canonical_only"
PRESENTATION_MODE_DUAL_VIEW = "dual_view"

_ACTIVE_STATUSES = {"open", "exploring", "blocked"}
_TERMINAL_STATUSES = {"resolved", "dead_end"}
_DECISION_TAGS = {
    "active",
    "baseline",
    "capacity",
    "contract",
    "current-best",
    "decision",
    "executive",
    "guardrail",
    "incumbent",
    "resource",
    "risk",
}
_LEGACY_LEADER_TAGS = {"leader", "provisional-leader", "winner"}
_COUNTEREVIDENCE_TAGS = {
    "counterevidence",
    "negative",
    "no-promote",
    "no_promote",
    "refuted",
    "retired",
}
_EXECUTIVE_EXPANSION_EDGE_TYPES = {"answers", "depends_on", "leads_to", "refutes"}
_VOLATILE_VIEW_KEYS = {
    "first_recorded_at",
    "last_updated_at",
    "update_count",
    "generated_at",
    "log_path",
}

DEFAULT_EXPLORE_PRESENTATION_POLICY: dict[str, float | int] = {
    "decision_density_node_floor": 24,
    "decision_density_ceiling": 0.35,
    "terminal_ratio_node_floor": 30,
    "terminal_ratio_floor": 0.60,
    "terminal_neighborhood_floor": 2,
    "decision_depth_floor": 6,
    "readability_node_floor": 60,
    "readability_edge_density_floor": 2.0,
    "readability_label_chars": 96,
    "readability_root_count_floor": 16,
    "readability_root_ratio_floor": 0.30,
    "stage_node_capacity": 14,
    "executive_counterevidence_limit": 8,
    "executive_hub_edge_degree_floor": 4,
}

_MERMAID_STATUS_CLASS = {
    "open": "open",
    "exploring": "exploring",
    "blocked": "blocked",
    "resolved": "resolved",
    "dead_end": "deadend",
}

_SVG_STATUS_STYLE = {
    "open": ("#f5f5f5", "#9e9e9e"),
    "exploring": ("#e3f2fd", "#1e88e5"),
    "blocked": ("#ffebee", "#e53935"),
    "resolved": ("#e8f5e9", "#43a047"),
    "dead_end": ("#eeeeee", "#9e9e9e"),
}
_SVG_NODE_HEADER_DISPLAY_WIDTH = 50
_SVG_NODE_DETAIL_DISPLAY_WIDTH = 62


def build_stage_lane_svg(
    *,
    title: str,
    node_order: Sequence[str],
    node_by_id: Mapping[str, Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> str:
    """Render one Evidence Stage as fixed left/right semantic lanes.

    Mermaid ignores a nested subgraph's direction when one of its nodes has an
    edge to another subgraph. That turns intended vertical lanes into a wide
    card row on the real whiteboard renderer. SVG keeps the semantic lane and
    edge model while making placement deterministic.
    """

    lane_nodes: dict[str, list[str]] = defaultdict(list)
    for node_id in node_order:
        lane_nodes[_explore_lane(node_by_id[node_id], node_by_id=node_by_id)].append(
            node_id
        )
    preferred = [lane for lane in ("fix_pr", "capability") if lane in lane_nodes]
    lanes = preferred + sorted(lane for lane in lane_nodes if lane not in preferred)
    margin, lane_width, lane_gap = 32, 500, 44
    lane_top, lane_header_height, lane_padding = 76, 48, 24
    node_width, node_height, node_gap = 436, 96, 34
    max_lane_nodes = max((len(lane_nodes[lane]) for lane in lanes), default=1)
    lane_height = (
        lane_header_height
        + lane_padding * 2
        + max_lane_nodes * node_height
        + max(0, max_lane_nodes - 1) * node_gap
    )
    width = margin * 2 + len(lanes) * lane_width + max(0, len(lanes) - 1) * lane_gap
    height = lane_top + lane_height + margin
    positions: dict[str, tuple[float, float]] = {}
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        "<defs>",
        '<marker id="loopx-arrow" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto" markerUnits="strokeWidth">',
        '<path d="M0,0 L0,6 L9,3 z" fill="#607d8b"/>',
        "</marker>",
        "</defs>",
        '<rect width="100%" height="100%" fill="#ffffff"/>',
        f'<text x="{width / 2:.1f}" y="34" text-anchor="middle" font-family="Arial, sans-serif" font-size="20" font-weight="700" fill="#263238">{html.escape(title)}</text>',
    ]
    lane_titles = {"fix_pr": "PR issue-fix", "capability": "LoopX capability"}
    for lane_index, lane in enumerate(lanes):
        lane_x = margin + lane_index * (lane_width + lane_gap)
        parts.extend(
            [
                f'<rect x="{lane_x}" y="{lane_top}" width="{lane_width}" height="{lane_height}" rx="16" fill="#fffde7" stroke="#c0b94f" stroke-width="2"/>',
                f'<text x="{lane_x + lane_width / 2:.1f}" y="{lane_top + 31}" text-anchor="middle" font-family="Arial, sans-serif" font-size="18" font-weight="700" fill="#37474f">{html.escape(lane_titles.get(lane, lane.replace("_", " ").title()))}</text>',
            ]
        )
        node_x = lane_x + (lane_width - node_width) / 2
        node_y = lane_top + lane_header_height + lane_padding
        for offset, node_id in enumerate(lane_nodes[lane]):
            positions[node_id] = (node_x, node_y + offset * (node_height + node_gap))

    edge_parts: list[str] = []
    grouped_edges: dict[tuple[str, str], set[str]] = {}
    for edge in edges:
        source_id = str(edge.get("from_node") or "")
        target_id = str(edge.get("to_node") or "")
        if source_id not in positions or target_id not in positions:
            continue
        grouped_edges.setdefault((source_id, target_id), set()).add(
            str(edge.get("edge_type") or "").strip()
        )
    for (source_id, target_id), edge_types in grouped_edges.items():
        source_x, source_y = positions[source_id]
        target_x, target_y = positions[target_id]
        same_lane = _explore_lane(
            node_by_id[source_id], node_by_id=node_by_id
        ) == _explore_lane(node_by_id[target_id], node_by_id=node_by_id)
        if same_lane:
            if target_y >= source_y:
                start_x = source_x + node_width / 2
                end_x = target_x + node_width / 2
                start_y = source_y + node_height
                end_y = target_y
                middle_y = (start_y + end_y) / 2
                path = f"M {start_x:.1f} {start_y:.1f} C {start_x:.1f} {middle_y:.1f}, {end_x:.1f} {middle_y:.1f}, {end_x:.1f} {end_y:.1f}"
            else:
                start_x = source_x + node_width
                end_x = target_x + node_width
                start_y = source_y + node_height / 2
                end_y = target_y + node_height / 2
                side_x = max(source_x, target_x) + node_width + 18
                path = f"M {start_x:.1f} {start_y:.1f} C {side_x:.1f} {start_y:.1f}, {side_x:.1f} {end_y:.1f}, {end_x:.1f} {end_y:.1f}"
        else:
            left_to_right = source_x < target_x
            start_x = source_x + (node_width if left_to_right else 0)
            end_x = target_x + (0 if left_to_right else node_width)
            start_y = source_y + node_height / 2
            end_y = target_y + node_height / 2
            middle_x = (start_x + end_x) / 2
            path = f"M {start_x:.1f} {start_y:.1f} C {middle_x:.1f} {start_y:.1f}, {middle_x:.1f} {end_y:.1f}, {end_x:.1f} {end_y:.1f}"
        label = " / ".join(sorted(edge_type for edge_type in edge_types if edge_type))
        edge_parts.append(
            f'<path d="{path}" data-edge-types="{html.escape(label)}" fill="none" stroke="#607d8b" stroke-width="2" marker-end="url(#loopx-arrow)"/>'
        )
    parts.extend(edge_parts)
    for node_id in node_order:
        node = node_by_id[node_id]
        node_x, node_y = positions[node_id]
        fill, stroke = _SVG_STATUS_STYLE.get(
            str(node.get("status") or "open"), _SVG_STATUS_STYLE["open"]
        )
        parts.append(
            f'<rect x="{node_x:.1f}" y="{node_y:.1f}" width="{node_width}" height="{node_height}" rx="10" fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        )
        display_lines = _svg_node_display_lines(node)
        for line_index, line in enumerate(display_lines):
            weight = "700" if line_index == 0 else "400"
            size = 14 if line_index == 0 else 12
            parts.append(
                f'<text x="{node_x + node_width / 2:.1f}" y="{node_y + 27 + line_index * 24:.1f}" text-anchor="middle" font-family="Arial, sans-serif" font-size="{size}" font-weight="{weight}" fill="#263238">{html.escape(line)}</text>'
            )
    parts.append("</svg>")
    return "\n".join(parts)


def _material_rows(values: Sequence[Any] | None, *, id_key: str) -> list[dict[str, Any]]:
    rows = []
    for value in values or []:
        if not isinstance(value, Mapping):
            continue
        row = {
            key: item
            for key, item in value.items()
            if key not in _VOLATILE_VIEW_KEYS
        }
        if row.get(id_key):
            rows.append(row)
    return sorted(rows, key=lambda item: str(item.get(id_key) or ""))


def explore_source_digest(projection: Mapping[str, Any]) -> str:
    """Return a stable digest for the complete canonical evidence projection."""

    material = {
        "goal_id": projection.get("goal_id"),
        "nodes": _material_rows(projection.get("nodes"), id_key="node_id"),
        "edges": _material_rows(projection.get("edges"), id_key="edge_id"),
        "findings": _material_rows(projection.get("findings"), id_key="finding_id"),
    }
    encoded = json.dumps(
        material,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def explore_source_revision(projection: Mapping[str, Any], *, digest: str | None = None) -> str:
    source_digest = digest or explore_source_digest(projection)
    event_count = max(0, int(projection.get("source_event_count") or 0))
    return f"events-{event_count}-{source_digest[:12]}"


def _parent_map(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
) -> dict[str, str]:
    parents = {
        str(node.get("node_id") or ""): str(node.get("parent_id") or "")
        for node in nodes
        if str(node.get("node_id") or "") and str(node.get("parent_id") or "")
    }
    for edge in edges:
        if str(edge.get("edge_type") or "") != "subtopic_of":
            continue
        child = str(edge.get("from_node") or "")
        parent = str(edge.get("to_node") or "")
        if child and parent:
            parents.setdefault(child, parent)
    return parents


def _lineage(node_id: str, parents: Mapping[str, str], node_ids: set[str]) -> list[str]:
    path = [node_id]
    seen = {node_id}
    parent = parents.get(node_id)
    while parent and parent in node_ids and parent not in seen:
        path.append(parent)
        seen.add(parent)
        parent = parents.get(parent)
    path.reverse()
    return path


def _node_depths(node_ids: set[str], parents: Mapping[str, str]) -> dict[str, int]:
    return {
        node_id: max(0, len(_lineage(node_id, parents, node_ids)) - 1)
        for node_id in node_ids
    }


def _mermaid_label(value: str, *, limit: int = 60) -> str:
    cleaned = re.sub(r'["\[\]{}<>`|]', "'", str(value or ""))
    return _truncate_display(cleaned, limit=limit) or "untitled"


_METRIC_SIGNAL = re.compile(
    r"(?:"
    r"[+-]\s*\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*/\s*[+-]?\s*\d+(?:\.\d+)?"
    r"|\d+(?:\.\d+)?\s*(?:bp|bps|%|ms|sec|secs|seconds?|x|×)\b"
    r")",
    flags=re.IGNORECASE,
)

_NODE_STATUS_LABEL = {
    "open": "OPEN",
    "exploring": "ACTIVE",
    "blocked": "BLOCKED",
    "resolved": "DONE",
    "dead_end": "NO-PROMOTE",
}


def _summary_clauses(value: str) -> list[str]:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    if not normalized:
        return []
    return [
        clause.strip(" .;。；")
        for clause in re.split(r"(?<=[!?。！？；;])\s*|(?<=\.)\s+", normalized)
        if clause.strip(" .;。；")
    ]


def _compact_detail_clause(value: str, *, limit: int) -> str:
    """Keep a metric-bearing portion visible when a long clause is compacted."""

    normalized = re.sub(r"\s+", " ", str(value or "")).strip()
    metric = _METRIC_SIGNAL.search(normalized)
    if metric and _display_width(normalized) > limit:
        context_width = max(18, limit // 3)
        prefix = normalized[: metric.start()].rstrip()
        metric_and_tail = normalized[metric.start() :]
        if _display_width(prefix) > context_width:
            prefix = "…" + _tail_display(prefix, limit=context_width - 1)
        normalized = f"{prefix} {metric_and_tail}".strip()
    return _truncate_display(normalized, limit=limit)


def _node_decision_lines(
    node: Mapping[str, Any],
    *,
    detail_limit: int,
) -> list[str]:
    """Project one metric-bearing evidence line and one conclusion line."""

    clauses = _summary_clauses(str(node.get("summary") or ""))
    if not clauses:
        return []
    metric_clause = next((clause for clause in clauses if _METRIC_SIGNAL.search(clause)), None)
    selected = []
    if metric_clause:
        selected.append(metric_clause)
    else:
        selected.append(clauses[0])
    if clauses[-1] != selected[0]:
        selected.append(clauses[-1])
    return [
        _compact_detail_clause(clause, limit=detail_limit)
        for clause in selected[:2]
    ]


def _node_display_lines(
    node: Mapping[str, Any],
    *,
    title_limit: int,
    detail_limit: int,
) -> list[str]:
    status = str(node.get("status") or "open")
    title = _truncate_display(
        str(node.get("title") or node.get("node_id") or "untitled"),
        limit=title_limit,
    )
    header = f"{title} · {_NODE_STATUS_LABEL.get(status, status.upper())}"
    return [header, *_node_decision_lines(node, detail_limit=detail_limit)]


def _svg_node_display_lines(node: Mapping[str, Any]) -> list[str]:
    status = str(node.get("status") or "open")
    status_suffix = f" · {_NODE_STATUS_LABEL.get(status, status.upper())}"
    title_limit = max(
        1,
        _SVG_NODE_HEADER_DISPLAY_WIDTH - _display_width(status_suffix),
    )
    title = _truncate_display(
        str(node.get("title") or node.get("node_id") or "untitled"),
        limit=title_limit,
    )
    return [
        f"{title}{status_suffix}",
        *_node_decision_lines(
            node,
            detail_limit=_SVG_NODE_DETAIL_DISPLAY_WIDTH,
        ),
    ][:3]


def _node_detail_coverage(nodes: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary_nodes = [node for node in nodes if str(node.get("summary") or "").strip()]
    rendered = [node for node in summary_nodes if _node_decision_lines(node, detail_limit=112)]
    metric_nodes = [
        node for node in summary_nodes if _METRIC_SIGNAL.search(str(node.get("summary") or ""))
    ]
    rendered_metric_nodes = [
        node
        for node in metric_nodes
        if _METRIC_SIGNAL.search(" ".join(_node_decision_lines(node, detail_limit=112)))
    ]
    complete = len(rendered) == len(summary_nodes) and len(rendered_metric_nodes) == len(metric_nodes)
    return {
        "summary_eligible_node_count": len(summary_nodes),
        "summary_rendered_node_count": len(rendered),
        "metric_eligible_node_count": len(metric_nodes),
        "metric_rendered_node_count": len(rendered_metric_nodes),
        "complete": complete,
    }


def _canonical_group_specs(
    nodes: Sequence[Mapping[str, Any]],
    *,
    group_node_limit: int,
) -> list[dict[str, Any]]:
    """Split stable source order into balanced bounded evidence stages."""

    node_by_id = {str(node.get("node_id") or ""): node for node in nodes}
    ordered_ids = list(node_by_id)
    if not ordered_ids:
        return []
    limit = max(1, int(group_node_limit))
    group_count = (len(ordered_ids) + limit - 1) // limit
    base, extra = divmod(len(ordered_ids), group_count)
    specs = []
    offset = 0
    for index in range(group_count):
        size = base + (1 if index < extra else 0)
        chunk = ordered_ids[offset : offset + size]
        offset += size
        first_title = str(node_by_id[chunk[0]].get("title") or chunk[0])
        specs.append(
            {
                "title": (
                    f"Evidence stage {index + 1:02d} · "
                    f"{first_title}"
                ),
                "node_ids": chunk,
                "order": offset - size,
            }
        )
    return specs


def _explore_lane(
    node: Mapping[str, Any],
    *,
    node_by_id: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Return a generic lane id, inheriting explicit lane tags from lineage.

    Projects can define any number of lanes with ``lane-<name>`` tags on a
    node or one of its ancestors.  The id fallbacks preserve useful behavior
    for existing issue-fix projections that predate explicit lane tags.
    """

    direct_tags = {
        str(tag).strip().lower()
        for tag in node.get("tags") or []
        if str(tag).strip()
    }
    direct_lanes = sorted(
        tag for tag in direct_tags if tag.startswith("lane-") and len(tag) > 5
    )
    if direct_lanes:
        return direct_lanes[0].removeprefix("lane-").replace("-", "_")
    node_id = str(node.get("node_id") or "").lower()
    if node_id.startswith("fix_") or direct_tags.intersection({"fix-pr", "fix_pr"}):
        return "fix_pr"
    if (
        node_id.startswith("cap_")
        or node_id.startswith("capability")
        or "capability" in direct_tags
    ):
        return "capability"

    ancestors = []
    if node_by_id:
        ancestors = [
            node_by_id[str(ancestor_id)]
            for ancestor_id in node.get("lineage") or []
            if str(ancestor_id) in node_by_id
            and str(ancestor_id) != str(node.get("node_id") or "")
        ]
    for candidate in reversed(ancestors):
        tags = {
            str(tag).strip().lower()
            for tag in candidate.get("tags") or []
            if str(tag).strip()
        }
        explicit_lanes = sorted(
            tag for tag in tags if tag.startswith("lane-") and len(tag) > 5
        )
        if explicit_lanes:
            return explicit_lanes[0].removeprefix("lane-").replace("-", "_")
    for candidate in reversed(ancestors):
        node_id = str(candidate.get("node_id") or "").lower()
        tags = {
            str(tag).strip().lower()
            for tag in candidate.get("tags") or []
            if str(tag).strip()
        }
        if node_id.startswith("fix_") or tags.intersection({"fix-pr", "fix_pr"}):
            return "fix_pr"
        if (
            node_id.startswith("cap_")
            or node_id.startswith("capability")
            or "capability" in tags
        ):
            return "capability"
    return "default"


def build_vertical_explore_mermaid(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    view_role: str,
    group_node_limit: int = 14,
) -> dict[str, Any]:
    """Render bounded Evidence Stage views over the canonical graph."""

    all_nodes = [node for node in nodes if str(node.get("node_id") or "")]
    node_list = [
        node for node in all_nodes if str(node.get("node_kind") or "") != "area"
    ] or all_nodes
    group_limit = max(10, min(20, int(group_node_limit)))
    primary_limit = group_limit - 2
    groups = _canonical_group_specs(node_list, group_node_limit=primary_limit)
    role = "executive" if view_role == "executive" else "canonical"
    timeline_title = f"{role.title()} evidence timeline"

    node_by_id = {str(node.get("node_id") or ""): node for node in all_nodes}
    shown_ids = {str(node.get("node_id") or "") for node in node_list}
    global_lanes = {
        _explore_lane(node, node_by_id=node_by_id) for node in node_list
    }
    detail_coverage = _node_detail_coverage(node_list)
    if not detail_coverage["complete"]:
        raise ValueError("Explore node detail projection lost summary or metric evidence")
    flow_direction = "TB"
    lines = [
        f"flowchart {flow_direction}",
        f'    subgraph {role}_timeline["{timeline_title}"]',
    ]
    lines.append(f"        direction {flow_direction}")
    group_chains = []
    stage_views = []
    for group_index, group in enumerate(groups, start=1):
        group_id = f"{role}_group_{group_index}"
        lines.append(f'        subgraph {group_id}["{_mermaid_label(group["title"])}"]')
        lines.append("            direction TB")
        chain = []
        for node_id in group["node_ids"]:
            node = node_by_id[node_id]
            status = str(node.get("status") or "open")
            label = "<br/>".join(
                _mermaid_label(line, limit=112)
                for line in _node_display_lines(
                    node,
                    title_limit=64,
                    detail_limit=112,
                )
            )
            mermaid_id = explore_mermaid_node_id(node_id)
            chain.append(mermaid_id)
            lines.append(
                f'            {mermaid_id}["{label}"]:::{_MERMAID_STATUS_CLASS.get(status, "open")}'
            )
        if len(chain) > 1:
            lines.append(f"            {' ~~~ '.join(chain)}")
        lines.append("        end")
        group_chains.append(chain)
        primary_node_ids = list(group["node_ids"])
        stage_node_order = list(primary_node_ids)
        stage_node_ids = set(primary_node_ids)
        relation_context = []
        for edge in edges:
            source = str(edge.get("from_node") or "")
            target = str(edge.get("to_node") or "")
            if source in stage_node_ids and target not in stage_node_ids:
                local_id, candidate_id = source, target
            elif target in stage_node_ids and source not in stage_node_ids:
                local_id, candidate_id = target, source
            else:
                continue
            candidate = node_by_id.get(candidate_id)
            local = node_by_id.get(local_id)
            if not candidate or not local:
                continue
            if str(candidate.get("node_kind") or "") == "area":
                continue
            if _explore_lane(candidate, node_by_id=node_by_id) == _explore_lane(
                local,
                node_by_id=node_by_id,
            ):
                continue
            relation_context.append(candidate_id)
        for candidate_id in relation_context:
            if len(stage_node_order) >= group_limit:
                break
            if candidate_id in stage_node_ids:
                continue
            stage_node_order.append(candidate_id)
            stage_node_ids.add(candidate_id)
        present_lanes = {
            _explore_lane(node_by_id[node_id], node_by_id=node_by_id)
            for node_id in stage_node_order
        }
        for missing_lane in sorted(global_lanes - present_lanes):
            if len(stage_node_order) >= group_limit:
                break
            candidate_id = next(
                (
                    str(candidate.get("node_id") or "")
                    for candidate in node_list
                    if str(candidate.get("node_id") or "") not in stage_node_ids
                    and _explore_lane(candidate, node_by_id=node_by_id)
                    == missing_lane
                ),
                None,
            )
            if candidate_id:
                stage_node_order.append(candidate_id)
                stage_node_ids.add(candidate_id)
        stage_edges = [
            edge
            for edge in edges
            if str(edge.get("from_node") or "") in stage_node_ids
            and str(edge.get("to_node") or "") in stage_node_ids
        ]
        stage_lanes: dict[str, list[str]] = defaultdict(list)
        for node_id in stage_node_order:
            stage_lanes[
                _explore_lane(node_by_id[node_id], node_by_id=node_by_id)
            ].append(node_id)
        stage_lines = [
            "flowchart TB",
            f'    subgraph {role}_stage_{group_index}["{_mermaid_label(group["title"])}"]',
            "        direction LR" if len(stage_lanes) > 1 else "        direction TB",
        ]
        for lane_index, (lane, lane_node_ids) in enumerate(stage_lanes.items(), start=1):
            lane_id = f"{role}_stage_{group_index}_lane_{lane_index}"
            lane_title = {
                "fix_pr": "PR issue-fix",
                "capability": "LoopX capability",
            }.get(lane, lane.replace("_", " ").title())
            stage_lines.append(
                f'        subgraph {lane_id}["{_mermaid_label(lane_title)}"]'
            )
            stage_lines.append("            direction TB")
            lane_chain = []
            for node_id in lane_node_ids:
                node = node_by_id[node_id]
                status = str(node.get("status") or "open")
                label = "<br/>".join(
                    _mermaid_label(line, limit=96)
                    for line in _node_display_lines(
                        node,
                        title_limit=56,
                        detail_limit=88,
                    )
                )
                stage_lines.append(
                    f'            {explore_mermaid_node_id(node_id)}["{label}"]:::{_MERMAID_STATUS_CLASS.get(status, "open")}'
                )
                lane_chain.append(explore_mermaid_node_id(node_id))
            if len(lane_chain) > 1:
                stage_lines.append(f"            {' ~~~ '.join(lane_chain)}")
            stage_lines.append("        end")
        stage_lines.append("    end")
        for edge in stage_edges:
            source = str(edge.get("from_node") or "")
            target = str(edge.get("to_node") or "")
            label = _mermaid_label(str(edge.get("edge_type") or ""))
            stage_lines.append(
                f"    {explore_mermaid_node_id(source)} -->|{label}| {explore_mermaid_node_id(target)}"
            )
        stage_lines.append(
            f"    style {role}_stage_{group_index} fill:#ffffff,stroke:#90a4ae,stroke-width:2px"
        )
        stage_lines.extend(
            [
                "    classDef open fill:#f5f5f5,stroke:#9e9e9e",
                "    classDef exploring fill:#e3f2fd,stroke:#1e88e5",
                "    classDef blocked fill:#ffebee,stroke:#e53935,stroke-width:2px",
                "    classDef resolved fill:#e8f5e9,stroke:#43a047",
                "    classDef deadend fill:#eeeeee,stroke:#9e9e9e,stroke-dasharray: 4 4",
            ]
        )
        incoming_edges = [
            edge
            for edge in edges
            if str(edge.get("to_node") or "") in stage_node_ids
            and str(edge.get("from_node") or "") not in stage_node_ids
        ]
        outgoing_edges = [
            edge
            for edge in edges
            if str(edge.get("from_node") or "") in stage_node_ids
            and str(edge.get("to_node") or "") not in stage_node_ids
        ]
        stage_svg = build_stage_lane_svg(
            title=group["title"],
            node_order=stage_node_order,
            node_by_id=node_by_id,
            edges=stage_edges,
        )
        stage_views.append(
            {
                "stage_index": group_index,
                "title": group["title"],
                "node_ids": stage_node_order,
                "primary_node_ids": primary_node_ids,
                "context_node_ids": stage_node_order[len(primary_node_ids) :],
                "node_count": len(stage_node_order),
                "primary_node_count": len(primary_node_ids),
                "context_node_count": len(stage_node_order) - len(primary_node_ids),
                "fix_pr_node_count": sum(
                    1
                    for node_id in stage_node_order
                    if _explore_lane(node_by_id[node_id], node_by_id=node_by_id)
                    == "fix_pr"
                ),
                "capability_node_count": sum(
                    1
                    for node_id in stage_node_order
                    if _explore_lane(node_by_id[node_id], node_by_id=node_by_id)
                    == "capability"
                ),
                "lanes": sorted(
                    {
                        _explore_lane(node_by_id[node_id], node_by_id=node_by_id)
                        for node_id in stage_node_order
                    }
                ),
                "lane_count": len(
                    {
                        _explore_lane(node_by_id[node_id], node_by_id=node_by_id)
                        for node_id in stage_node_order
                    }
                ),
                "cross_lane_edge_count": sum(
                    1
                    for edge in stage_edges
                    if _explore_lane(
                        node_by_id[str(edge.get("from_node") or "")],
                        node_by_id=node_by_id,
                    )
                    != _explore_lane(
                        node_by_id[str(edge.get("to_node") or "")],
                        node_by_id=node_by_id,
                    )
                ),
                "internal_edge_count": len(stage_edges),
                "incoming_edge_count": len(incoming_edges),
                "outgoing_edge_count": len(outgoing_edges),
                "mermaid": "\n".join(stage_lines),
                "svg": stage_svg,
                "svg_layout": {
                    "strategy": "semantic_lane_columns",
                    "lane_order": [
                        lane
                        for lane in ("fix_pr", "capability")
                        if lane in stage_lanes
                    ]
                    + sorted(
                        lane
                        for lane in stage_lanes
                        if lane not in {"fix_pr", "capability"}
                    ),
                    "orientation": "left_to_right_lanes_top_to_bottom_nodes",
                },
            }
        )
    for previous, current in zip(group_chains, group_chains[1:]):
        lines.append(f"        {previous[-1]} ~~~ {current[0]}")
    lines.append("    end")
    for edge in edges:
        source = str(edge.get("from_node") or "")
        target = str(edge.get("to_node") or "")
        if source not in shown_ids or target not in shown_ids:
            continue
        label = _mermaid_label(str(edge.get("edge_type") or ""))
        lines.append(f"    {explore_mermaid_node_id(source)} -->|{label}| {explore_mermaid_node_id(target)}")
    lines.append(
        f"    style {role}_timeline fill:#ffffff,stroke:#90a4ae,stroke-width:2px"
    )
    for group_index in range(1, len(groups) + 1):
        lines.append(
            f"    style {role}_group_{group_index} fill:#fafafa,stroke:#cfd8dc"
        )
    lines.extend(
        [
            "    classDef open fill:#f5f5f5,stroke:#9e9e9e",
            "    classDef exploring fill:#e3f2fd,stroke:#1e88e5",
            "    classDef blocked fill:#ffebee,stroke:#e53935,stroke-width:2px",
            "    classDef resolved fill:#e8f5e9,stroke:#43a047",
            "    classDef deadend fill:#eeeeee,stroke:#9e9e9e,stroke-dasharray: 4 4",
        ]
    )
    return {
        "mermaid": "\n".join(lines),
        "strategy": "vertical_evidence_timeline",
        "view_role": role,
        "group_count": len(groups),
        "column_count": 1,
        "orientation": "top_to_bottom",
        "max_group_node_count": group_limit,
        "primary_group_node_count": primary_limit,
        "stage_views": stage_views,
        "node_detail_coverage": detail_coverage,
    }


def _display_width(value: str) -> int:
    return sum(2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1 for character in value)


def _truncate_display(value: str, *, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if _display_width(cleaned) <= limit:
        return cleaned
    result = []
    width = 0
    for character in cleaned:
        character_width = 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        if width + character_width > max(1, limit - 1):
            break
        result.append(character)
        width += character_width
    truncated = "".join(result).rstrip()
    if (
        truncated
        and len(truncated) < len(cleaned)
        and truncated[-1].isascii()
        and truncated[-1].isalnum()
        and cleaned[len(truncated)].isascii()
        and cleaned[len(truncated)].isalnum()
    ):
        boundary = truncated.rfind(" ")
        if boundary >= max(1, len(truncated) // 2):
            truncated = truncated[:boundary].rstrip()
    return truncated + "…"


def _tail_display(value: str, *, limit: int) -> str:
    cleaned = re.sub(r"\s+", " ", str(value or "")).strip()
    if _display_width(cleaned) <= limit:
        return cleaned
    result = []
    width = 0
    for character in reversed(cleaned):
        character_width = 2 if unicodedata.east_asian_width(character) in {"W", "F"} else 1
        if width + character_width > limit:
            break
        result.append(character)
        width += character_width
    tail = "".join(reversed(result)).lstrip()
    if tail and tail[0].isascii() and tail[0].isalnum():
        boundary = tail.find(" ")
        if 0 < boundary <= len(tail) // 2:
            tail = tail[boundary + 1 :].lstrip()
    return tail


def _tags(node: Mapping[str, Any]) -> set[str]:
    return {str(tag or "").strip().lower() for tag in node.get("tags") or [] if str(tag or "").strip()}


def _decision_seed_ids(nodes: Sequence[Mapping[str, Any]]) -> set[str]:
    seeds = set()
    has_current_incumbent = any(
        _tags(node).intersection({"current-best", "incumbent"})
        for node in nodes
    )
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        if not node_id:
            continue
        tags = _tags(node)
        if (
            str(node.get("status") or "") in _ACTIVE_STATUSES
            or tags.intersection(_DECISION_TAGS)
            or (not has_current_incumbent and tags.intersection(_LEGACY_LEADER_TAGS))
        ):
            seeds.add(node_id)
    return seeds


def _terminal_neighborhood_count(
    nodes: Sequence[Mapping[str, Any]],
    parents: Mapping[str, str],
) -> int:
    statuses = {
        str(node.get("node_id") or ""): str(node.get("status") or "")
        for node in nodes
    }
    children: dict[str, list[str]] = defaultdict(list)
    for child, parent in parents.items():
        children[parent].append(child)
    return sum(
        1
        for child_ids in children.values()
        if len(child_ids) >= 3
        and sum(statuses.get(child) in _TERMINAL_STATUSES for child in child_ids) >= 3
    )


def assess_explore_presentation(
    projection: Mapping[str, Any],
    *,
    readability_check: Mapping[str, Any] | None = None,
    policy: Mapping[str, float | int] | None = None,
) -> dict[str, Any]:
    """Recommend one or two views from several advisory readability signals."""

    thresholds = dict(DEFAULT_EXPLORE_PRESENTATION_POLICY)
    supplied_policy = dict(policy or {})
    if (
        "atlas_group_node_limit" in supplied_policy
        and "stage_node_capacity" not in supplied_policy
    ):
        supplied_policy["stage_node_capacity"] = supplied_policy.pop(
            "atlas_group_node_limit"
        )
    thresholds.update(supplied_policy)
    nodes = [item for item in projection.get("nodes") or [] if isinstance(item, Mapping)]
    edges = [item for item in projection.get("edges") or [] if isinstance(item, Mapping)]
    node_ids = {str(node.get("node_id") or "") for node in nodes if str(node.get("node_id") or "")}
    parents = _parent_map(nodes, edges)
    depths = _node_depths(node_ids, parents)
    decision_ids = _decision_seed_ids(nodes)
    terminal_count = sum(str(node.get("status") or "") in _TERMINAL_STATUSES for node in nodes)
    node_count = len(nodes)
    edge_count = len(edges)
    decision_density = len(decision_ids) / node_count if node_count else 1.0
    terminal_ratio = terminal_count / node_count if node_count else 0.0
    edge_density = edge_count / node_count if node_count else 0.0
    max_depth = max(depths.values(), default=0)
    root_count = sum(not parents.get(node_id) for node_id in node_ids)
    root_ratio = root_count / node_count if node_count else 0.0
    terminal_neighborhoods = _terminal_neighborhood_count(nodes, parents)
    long_label_count = sum(
        len(str(node.get("title") or "")) > int(thresholds["readability_label_chars"])
        for node in nodes
    )

    reasons = []
    if (
        node_count >= int(thresholds["decision_density_node_floor"])
        and decision_density < float(thresholds["decision_density_ceiling"])
    ):
        reasons.append("low_decision_density")
    if (
        terminal_neighborhoods >= int(thresholds["terminal_neighborhood_floor"])
        or (
            node_count >= int(thresholds["terminal_ratio_node_floor"])
            and terminal_ratio >= float(thresholds["terminal_ratio_floor"])
        )
    ):
        reasons.append("excessive_terminal_branches")
    if max_depth >= int(thresholds["decision_depth_floor"]):
        reasons.append("deep_decision_path")

    observed_readability_failure = any(
        readability_check and readability_check.get(key) is True
        for key in (
            "overlap_detected",
            "text_overflow_detected",
            "canvas_expansion_detected",
        )
    )
    estimated_readability_failure = (
        node_count >= int(thresholds["readability_node_floor"])
        and (
            edge_density >= float(thresholds["readability_edge_density_floor"])
            or long_label_count > 0
            or max_depth >= int(thresholds["decision_depth_floor"])
            or (
                root_count >= int(thresholds["readability_root_count_floor"])
                and root_ratio >= float(thresholds["readability_root_ratio_floor"])
            )
        )
    )
    if observed_readability_failure or estimated_readability_failure:
        reasons.append("readability_check_failed")

    mode = (
        PRESENTATION_MODE_DUAL_VIEW
        if "readability_check_failed" in reasons or len(reasons) >= 2
        else PRESENTATION_MODE_CANONICAL_ONLY
    )
    return {
        "schema_version": EXPLORE_PRESENTATION_ASSESSMENT_VERSION,
        "presentation_mode": mode,
        "reason_codes": reasons,
        "metrics": {
            "node_count": node_count,
            "edge_count": edge_count,
            "decision_node_count": len(decision_ids),
            "decision_density": round(decision_density, 4),
            "terminal_node_count": terminal_count,
            "terminal_ratio": round(terminal_ratio, 4),
            "terminal_neighborhood_count": terminal_neighborhoods,
            "max_depth": max_depth,
            "root_node_count": root_count,
            "root_ratio": round(root_ratio, 4),
            "edge_density": round(edge_density, 4),
            "long_label_count": long_label_count,
        },
        "readability_check": {
            "source": "observed" if readability_check else "static_estimate",
            "failed": observed_readability_failure or estimated_readability_failure,
        },
        "policy": thresholds,
        "advisory_only": True,
        "canonical_truncation_allowed": False,
    }


def _executive_roles(node: Mapping[str, Any]) -> list[str]:
    roles = []
    status = str(node.get("status") or "")
    tags = _tags(node)
    if status in _ACTIVE_STATUSES:
        roles.append("active_work")
    for role, role_tags in (
        ("decision_contract", {"contract", "decision"}),
        ("baseline", {"baseline"}),
        (
            "incumbent",
            {"current-best", "incumbent", "leader", "provisional-leader", "winner"},
        ),
        ("guardrail", {"guardrail", "risk"}),
        ("resource_state", {"capacity", "resource"}),
        ("counterevidence", {"counterevidence", "negative", "no-promote", "retired"}),
    ):
        if tags.intersection(role_tags):
            roles.append(role)
    if status == "dead_end" and "counterevidence" not in roles:
        roles.append("counterevidence")
    return roles or ["lineage_context"]


def _executive_node_ids(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    parents: Mapping[str, str],
    *,
    counterevidence_limit: int,
) -> set[str]:
    node_ids = {str(node.get("node_id") or "") for node in nodes if str(node.get("node_id") or "")}
    selected = _decision_seed_ids(nodes)
    if not selected:
        selected = {node_id for node_id in node_ids if not parents.get(node_id)}
    counterevidence_by_parent: dict[str, str] = {}
    for node in nodes:
        node_id = str(node.get("node_id") or "")
        status = str(node.get("status") or "")
        if not node_id or not (
            status == "dead_end" or _tags(node).intersection(_COUNTEREVIDENCE_TAGS)
        ):
            continue
        neighborhood = parents.get(node_id) or node_id
        counterevidence_by_parent.setdefault(neighborhood, node_id)
    selected.update(
        list(counterevidence_by_parent.values())[: max(0, counterevidence_limit)]
    )
    relation_seeds = set(selected)
    for edge in edges:
        if str(edge.get("edge_type") or "") not in _EXECUTIVE_EXPANSION_EDGE_TYPES:
            continue
        source = str(edge.get("from_node") or "")
        target = str(edge.get("to_node") or "")
        if source in relation_seeds or target in relation_seeds:
            selected.update({source, target}.intersection(node_ids))
    for node_id in tuple(selected):
        selected.update(_lineage(node_id, parents, node_ids))
    return selected


def _view_node(
    node: Mapping[str, Any],
    *,
    parents: Mapping[str, str],
    node_ids: set[str],
    executive: bool,
) -> dict[str, Any]:
    view = dict(node)
    node_id = str(node.get("node_id") or "")
    view["source_node_id"] = node_id
    view["lineage"] = _lineage(node_id, parents, node_ids)
    if executive:
        view["executive_roles"] = _executive_roles(node)
    return view


def _executive_edge_projection(
    edges: Sequence[Mapping[str, Any]],
    executive_ids: set[str],
    *,
    hub_degree_floor: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Hide lineage and dense hub scaffolding that the executive layout already conveys."""

    candidates = [
        edge
        for edge in edges
        if str(edge.get("from_node") or "") in executive_ids
        and str(edge.get("to_node") or "") in executive_ids
    ]
    outgoing_counts: dict[str, int] = defaultdict(int)
    for edge in candidates:
        outgoing_counts[str(edge.get("from_node") or "")] += 1

    visible = []
    suppressed = []
    degree_floor = max(2, int(hub_degree_floor))
    for edge in candidates:
        edge_type = str(edge.get("edge_type") or "")
        source = str(edge.get("from_node") or "")
        suppression_reason = None
        if edge_type == "subtopic_of":
            suppression_reason = "lineage_encoded_on_node"
        elif edge_type == "supports" and outgoing_counts[source] >= degree_floor:
            suppression_reason = "dense_hub_scaffolding"
        projected = dict(edge, source_edge_id=str(edge.get("edge_id") or ""))
        if suppression_reason:
            suppressed.append(dict(projected, suppression_reason=suppression_reason))
        else:
            visible.append(projected)
    return visible, suppressed


def build_explore_presentation_bundle(
    projection: Mapping[str, Any],
    *,
    readability_check: Mapping[str, Any] | None = None,
    policy: Mapping[str, float | int] | None = None,
) -> dict[str, Any]:
    """Build canonical and executive views atomically from one projection."""

    nodes = [dict(item) for item in projection.get("nodes") or [] if isinstance(item, Mapping)]
    edges = [dict(item) for item in projection.get("edges") or [] if isinstance(item, Mapping)]
    findings = [dict(item) for item in projection.get("findings") or [] if isinstance(item, Mapping)]
    counts = projection.get("counts")
    declared_finding_count = (
        int(counts.get("finding_count") or 0)
        if isinstance(counts, Mapping)
        else len(findings)
    )
    if declared_finding_count > len(findings):
        raise ValueError(
            "Explore presentation requires the complete canonical finding set"
        )
    node_ids = {str(node.get("node_id") or "") for node in nodes if str(node.get("node_id") or "")}
    parents = _parent_map(nodes, edges)
    digest = explore_source_digest(projection)
    revision = explore_source_revision(projection, digest=digest)
    assessment = assess_explore_presentation(
        projection,
        readability_check=readability_check,
        policy=policy,
    )

    canonical_nodes = [
        _view_node(node, parents=parents, node_ids=node_ids, executive=False)
        for node in nodes
    ]
    canonical_edges = [dict(edge, source_edge_id=str(edge.get("edge_id") or "")) for edge in edges]
    thresholds = dict(DEFAULT_EXPLORE_PRESENTATION_POLICY)
    thresholds.update(policy or {})
    canonical_layout = build_vertical_explore_mermaid(
        canonical_nodes,
        canonical_edges,
        view_role="canonical",
        group_node_limit=int(thresholds["stage_node_capacity"]),
    )
    canonical = {
        "schema_version": EXPLORE_CANONICAL_VIEW_VERSION,
        "goal_id": projection.get("goal_id"),
        "view_role": "canonical",
        "source_revision": revision,
        "source_digest": digest,
        "nodes": canonical_nodes,
        "edges": canonical_edges,
        "findings": findings,
        "mermaid": canonical_layout["mermaid"],
        "stage_views": canonical_layout["stage_views"],
        "graph_counts": {
            "node_count": len(canonical_nodes),
            "edge_count": len(canonical_edges),
            "finding_count": len(findings),
        },
        "filter": {
            "projection_mode": "canonical_full",
            "truncated": False,
            "layout": {
                key: value
                for key, value in canonical_layout.items()
                if key not in {"mermaid", "stage_views"}
            },
        },
    }

    executive_ids = _executive_node_ids(
        nodes,
        edges,
        parents,
        counterevidence_limit=int(thresholds["executive_counterevidence_limit"]),
    )
    executive_nodes = [
        _view_node(node, parents=parents, node_ids=node_ids, executive=True)
        for node in nodes
        if str(node.get("node_id") or "") in executive_ids
    ]
    executive_edges, suppressed_executive_edges = _executive_edge_projection(
        edges,
        executive_ids,
        hub_degree_floor=int(thresholds["executive_hub_edge_degree_floor"]),
    )
    executive_layout = build_vertical_explore_mermaid(
        executive_nodes,
        executive_edges,
        view_role="executive",
        group_node_limit=int(thresholds["stage_node_capacity"]),
    )
    executive_findings = [
        finding
        for finding in findings
        if str(finding.get("node_id") or "") in executive_ids
    ]
    executive = {
        "schema_version": EXPLORE_EXECUTIVE_VIEW_VERSION,
        "goal_id": projection.get("goal_id"),
        "view_role": "executive",
        "source_revision": revision,
        "source_digest": digest,
        "source_node_ids": sorted(executive_ids),
        "nodes": executive_nodes,
        "edges": executive_edges,
        "findings": executive_findings,
        "mermaid": executive_layout["mermaid"],
        "stage_views": executive_layout["stage_views"],
        "graph_counts": {
            "node_count": len(executive_nodes),
            "edge_count": len(executive_edges),
            "canonical_edge_count": len(canonical_edges),
            "suppressed_edge_count": len(suppressed_executive_edges),
            "finding_count": len(executive_findings),
            "canonical_node_count": len(canonical_nodes),
        },
        "filter": {
            "projection_mode": "executive_auto",
            "selection": "active_or_tagged_plus_material_neighbors_and_lineage",
            "truncated": False,
            "edge_projection": {
                "selection": "material_edges_without_lineage_or_dense_hub_scaffolding",
                "suppressed_source_edge_ids": [
                    str(edge.get("source_edge_id") or "")
                    for edge in suppressed_executive_edges
                ],
                "suppression_counts": {
                    reason: sum(
                        1
                        for edge in suppressed_executive_edges
                        if edge.get("suppression_reason") == reason
                    )
                    for reason in (
                        "lineage_encoded_on_node",
                        "dense_hub_scaffolding",
                    )
                },
            },
            "layout": {
                key: value
                for key, value in executive_layout.items()
                if key not in {"mermaid", "stage_views"}
            },
        },
    }
    return {
        "ok": True,
        "schema_version": EXPLORE_PRESENTATION_BUNDLE_VERSION,
        "goal_id": projection.get("goal_id"),
        "presentation_mode": assessment["presentation_mode"],
        "reason_codes": assessment["reason_codes"],
        "source_revision": revision,
        "source_digest": digest,
        "assessment": assessment,
        "canonical": canonical,
        "executive": executive,
    }


def validate_explore_view_freshness(
    projection: Mapping[str, Any],
    view: Mapping[str, Any],
) -> dict[str, Any]:
    """Fail closed when a derived display view is not from this projection."""

    digest = explore_source_digest(projection)
    revision = explore_source_revision(projection, digest=digest)
    observed_digest = str(view.get("source_digest") or "")
    observed_revision = str(view.get("source_revision") or "")
    fresh = observed_digest == digest and observed_revision == revision
    return {
        "ok": fresh,
        "fresh": fresh,
        "expected_source_digest": digest,
        "observed_source_digest": observed_digest or None,
        "expected_source_revision": revision,
        "observed_source_revision": observed_revision or None,
        "reason": None if fresh else "derived Explore view is stale; rebuild it from the current canonical projection",
    }
