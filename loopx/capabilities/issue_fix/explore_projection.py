"""Project material issue-fix lifecycle facts into the Explore result graph.

Issue-fix domain state, active-state todos, and rollout events remain the
sources of truth.  This module only derives an idempotent, public-safe Explore
projection from those sources; it does not introduce another workflow state
machine or capture raw agent/tool output.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ...history import load_registry
from ...paths import resolve_runtime_root
from ...rollout_event_log import load_rollout_events, rollout_event_log_path
from ...todos import resolve_todo_state_path, section_bounds, todo_blocks
from ..explore.result_log import (
    EVENT_KIND_NODE,
    FINDING_STATUS_CONFIRMED,
    FINDING_STATUS_TENTATIVE,
    NODE_KIND_AREA,
    NODE_KIND_ARTIFACT,
    NODE_STATUS_BLOCKED,
    NODE_STATUS_EXPLORING,
    NODE_STATUS_RESOLVED,
    append_explore_result_event,
    build_explore_edge_event,
    build_explore_finding_event,
    build_explore_node_event,
    build_explore_result_projection,
    explore_result_log_path,
    load_explore_result_events,
    validate_explore_public_ref,
)
from .outcome_projection import build_issue_fix_outcome_collection_from_domain_state


ISSUE_FIX_EXPLORE_PROJECTION_SCHEMA_VERSION = "issue_fix_explore_projection_v0"
ISSUE_FIX_ROOT_ID = "issue_fix_campaign"
ISSUE_FIX_LANE_ID = "fix_pr_lane"
CAPABILITY_LANE_ID = "capability_lane"

_TERMINAL_STAGES = {
    "merged",
    "closed",
    "comment_published",
    "triage_complete",
    "no_follow_up",
    "superseded",
}
_BLOCKED_STAGES = {
    "reproduction_blocked",
    "fix_blocked",
    "delivery_blocked",
}
_CAPABILITY_RESOLVED_STATUSES = {"fixed", "real_callsite_verified"}
_NON_MATERIAL_EVENT_KEYS = {"event_id", "recorded_at", "boundary", "run_id"}
_NON_MATERIAL_VIEW_KEYS = {
    "first_recorded_at",
    "last_updated_at",
    "update_count",
    "finding_count",
    "materialized_from",
}


def _visual_mermaid_label(value: Any) -> str:
    cleaned = re.sub(r'["\[\]{}<>`|]', "'", _text(value, limit=72))
    return cleaned or "untitled"


def build_issue_fix_executive_visual_projection(
    projection: Mapping[str, Any],
) -> dict[str, Any]:
    """Compress canonical issue-fix evidence into two owner-facing lanes."""

    nodes = [dict(item) for item in projection.get("nodes") or [] if isinstance(item, Mapping)]
    edges = [dict(item) for item in projection.get("edges") or [] if isinstance(item, Mapping)]
    by_id = {str(item.get("node_id") or ""): item for item in nodes}
    root_id = "ov_pilot" if "ov_pilot" in by_id else ISSUE_FIX_ROOT_ID

    issue_groups: dict[str, list[dict[str, Any]]] = {}
    for node in nodes:
        if str(node.get("parent_id") or "") != ISSUE_FIX_LANE_ID:
            continue
        tags = {str(tag) for tag in node.get("tags") or []}
        if "superseded" in tags:
            continue
        match = re.match(r"fix_(\d+)", str(node.get("node_id") or ""))
        issue_key = match.group(1) if match else str(node.get("node_id") or "")
        issue_groups.setdefault(issue_key, []).append(node)

    def issue_rank(node: Mapping[str, Any]) -> tuple[int, int]:
        title = str(node.get("title") or "")
        node_id = str(node.get("node_id") or "")
        return (
            2 if "PR #" in title or "→ PR #" in title else 1,
            0 if node_id.endswith("candidate") else 1,
        )

    delivery_nodes = [max(group, key=issue_rank) for group in issue_groups.values()]
    capability_nodes = []
    for node in nodes:
        if str(node.get("parent_id") or "") != CAPABILITY_LANE_ID:
            continue
        tags = {str(tag) for tag in node.get("tags") or []}
        if "lane-capability" in tags or (
            "capability-gap" in tags and str(node.get("status") or "") != NODE_STATUS_RESOLVED
        ):
            capability_nodes.append(node)

    selected = {str(node.get("node_id") or "") for node in [*delivery_nodes, *capability_nodes]}
    lines = [
        "flowchart TB",
        f'  {root_id}["{_visual_mermaid_label(by_id.get(root_id, {}).get("title") or "PR issue-fix campaign")}"]',
        '  subgraph FIX["主线 A · Focused Fix PR 交付"]',
        "    direction LR",
    ]
    for node in delivery_nodes:
        node_id = str(node.get("node_id") or "")
        lines.append(f'    {node_id}["{_visual_mermaid_label(node.get("title"))}"]')
    for left, right in zip(delivery_nodes, delivery_nodes[1:]):
        lines.append(f"    {left['node_id']} --> {right['node_id']}")
    lines.extend(
        [
            "  end",
            '  subgraph CAP["主线 B · Agent / LoopX 能力演进"]',
            "    direction LR",
        ]
    )
    for node in capability_nodes:
        node_id = str(node.get("node_id") or "")
        lines.append(f'    {node_id}["{_visual_mermaid_label(node.get("title"))}"]')
    for left, right in zip(capability_nodes, capability_nodes[1:]):
        lines.append(f"    {left['node_id']} --> {right['node_id']}")
    lines.extend(["  end", f"  {root_id} --> FIX", f"  {root_id} --> CAP"])
    for edge in edges:
        source = str(edge.get("from_node") or "")
        target = str(edge.get("to_node") or "")
        if source in selected and target in selected:
            edge_type = _visual_mermaid_label(edge.get("edge_type") or "related")
            lines.append(f'  {source} -. "{edge_type}" .-> {target}')
    lines.extend(
        [
            "  classDef resolved fill:#E8F5E9,stroke:#43A047,color:#1B5E20",
            "  classDef exploring fill:#E3F2FD,stroke:#1E88E5,color:#0D47A1",
            "  classDef blocked fill:#FFEBEE,stroke:#E53935,color:#B71C1C,stroke-width:2px",
        ]
    )
    for status in (NODE_STATUS_RESOLVED, NODE_STATUS_EXPLORING, NODE_STATUS_BLOCKED):
        members = [
            str(node.get("node_id") or "")
            for node in [*delivery_nodes, *capability_nodes]
            if str(node.get("status") or "") == status
        ]
        if members:
            lines.append(f"  class {','.join(members)} {status}")
    return {
        "schema_version": "issue_fix_executive_visual_projection_v0",
        "mermaid": "\n".join(lines),
        "graph_counts": {
            "node_count": 1 + len(delivery_nodes) + len(capability_nodes),
            "delivery_node_count": len(delivery_nodes),
            "capability_node_count": len(capability_nodes),
        },
        "filter": {"projection_mode": "issue_fix_two_lane"},
    }


def _token(value: Any, *, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.:-]+", "_", str(value or "").strip()).strip("_")
    if not text:
        text = fallback
    if not text[0].isalpha():
        text = f"n_{text}"
    if len(text) <= 96:
        return text
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:10]
    return f"{text[:85]}_{digest}"


def _text(value: Any, *, limit: int = 900) -> str:
    compact = " ".join(str(value or "").split())
    return compact if len(compact) <= limit else compact[: limit - 1].rstrip() + "..."


def _refs(*values: Any, source_ref: str) -> list[str]:
    """Project refs without moving private source evidence into the public log.

    A source pointer resolves to the original local record, not a public artifact.
    Validate it through the same owner as ordinary refs; never relax the writer.
    """
    refs: list[str] = []
    for value in values:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            candidates = value
        else:
            candidates = [value]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if not text:
                continue
            try:
                ref = validate_explore_public_ref(text, field="evidence_refs")
            except ValueError:
                ref = validate_explore_public_ref(source_ref, field="source_ref")
            if ref not in refs:
                refs.append(ref)
    return refs[:16]


def _latest_events(
    events: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, str], dict[str, Any]]:
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        kind = str(event.get("event_kind") or "")
        result_id = str(event.get("result_id") or "")
        if kind and result_id:
            latest[(kind, result_id)] = dict(event)
    return latest


def _material_event(event: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in event.items() if key not in _NON_MATERIAL_EVENT_KEYS}


def _semantic_projection_digest(projection: Mapping[str, Any]) -> str:
    def clean(item: Mapping[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in item.items() if key not in _NON_MATERIAL_VIEW_KEYS}

    material = {
        "nodes": sorted(
            (clean(item) for item in projection.get("nodes") or [] if isinstance(item, Mapping)),
            key=lambda item: str(item.get("node_id") or ""),
        ),
        "edges": sorted(
            (clean(item) for item in projection.get("edges") or [] if isinstance(item, Mapping)),
            key=lambda item: str(item.get("edge_id") or ""),
        ),
        "findings": sorted(
            (clean(item) for item in projection.get("findings") or [] if isinstance(item, Mapping)),
            key=lambda item: str(item.get("finding_id") or ""),
        ),
    }
    encoded = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _all_todos(state_file: Path) -> list[dict[str, Any]]:
    lines = state_file.read_text(encoding="utf-8").splitlines()
    items: list[dict[str, Any]] = []
    for role in ("user", "agent"):
        bounds = section_bounds(lines, role)
        if not bounds:
            continue
        start, end, heading = bounds
        for block in todo_blocks(lines, start, end, role=role, source_section=heading):
            items.append({**block, "role": role, "source_section": heading})
    return items


def _existing_node(existing: Mapping[tuple[str, str], dict[str, Any]], node_id: str) -> dict[str, Any]:
    return dict(existing.get((EVENT_KIND_NODE, node_id)) or {})


def _node_event(
    *,
    goal_id: str,
    node_id: str,
    title: str,
    node_kind: str,
    status: str,
    summary: str,
    parent_id: str | None,
    blocked_reason: str | None,
    evidence_refs: Sequence[str],
    tags: Sequence[str],
    agent_id: str | None,
    existing: Mapping[tuple[str, str], dict[str, Any]],
) -> dict[str, Any]:
    prior = _existing_node(existing, node_id)
    # Human-curated labels and topology remain authoritative. Automation only
    # fills missing presentation fields and advances material lifecycle state.
    return build_explore_node_event(
        goal_id=goal_id,
        node_id=node_id,
        title=str(prior.get("title") or title),
        node_kind=str(prior.get("node_kind") or node_kind),
        status=status,
        summary=str(prior.get("summary") or summary),
        blocked_reason=blocked_reason if status == NODE_STATUS_BLOCKED else None,
        parent_id=str(prior.get("parent_id") or parent_id or "") or None,
        agent_id=str(prior.get("agent_id") or agent_id or "") or None,
        evidence_refs=list(prior.get("evidence_refs") or evidence_refs),
        tags=list(prior.get("tags") or tags),
    )


def _issue_number(outcome: Mapping[str, Any]) -> str:
    issue = outcome.get("issue") if isinstance(outcome.get("issue"), Mapping) else {}
    number = str(issue.get("number") or "").strip()
    if number:
        return number
    ref = str(outcome.get("issue_ref") or "")
    match = re.search(r"(\d+)$", ref)
    return match.group(1) if match else _token(ref, fallback="issue")


def _issue_node_id(outcome: Mapping[str, Any]) -> str:
    issue_number = _issue_number(outcome)
    pull_request = outcome.get("pull_request")
    pr_number = str(pull_request.get("number") or "").strip() if isinstance(pull_request, Mapping) else ""
    return _token(
        f"fix_{issue_number}_{pr_number}" if pr_number else f"fix_{issue_number}_candidate",
        fallback="fix_candidate",
    )


def _todo_node_refs(todo: Mapping[str, Any]) -> list[str]:
    refs = todo.get("explore_result_node_refs")
    if isinstance(refs, Sequence) and not isinstance(refs, (str, bytes)):
        return [_token(item, fallback="capability") for item in refs if str(item).strip()]
    return []


def _todo_capabilities(todo: Mapping[str, Any]) -> list[str]:
    capabilities = todo.get("target_capabilities")
    if isinstance(capabilities, Sequence) and not isinstance(capabilities, (str, bytes)):
        return [str(item).strip() for item in capabilities if str(item).strip()]
    return []


def _capability_node_ids(todo: Mapping[str, Any] | None, capabilities: Sequence[str]) -> list[str]:
    if todo:
        refs = _todo_node_refs(todo)
        if refs:
            return refs
    return [_token(f"cap_{capability}", fallback="capability") for capability in capabilities]


def _candidate_events(
    *,
    goal_id: str,
    agent_id: str | None,
    outcomes: Sequence[Mapping[str, Any]],
    todos: Sequence[Mapping[str, Any]],
    rollout_events: Sequence[Mapping[str, Any]],
    existing_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    existing = _latest_events(existing_events)
    candidates: list[dict[str, Any]] = []
    todo_by_id = {str(todo.get("todo_id") or ""): dict(todo) for todo in todos if str(todo.get("todo_id") or "")}
    has_issue_facts = bool(outcomes)
    capability_events = [event for event in rollout_events if event.get("event_kind") == "capability_gap"]
    has_capability_facts = bool(capability_events) or any(
        _todo_capabilities(todo) or _todo_node_refs(todo) for todo in todos
    )
    issue_fix_context = has_issue_facts or (EVENT_KIND_NODE, ISSUE_FIX_LANE_ID) in existing
    if not issue_fix_context:
        return []

    root_id = "ov_pilot" if (EVENT_KIND_NODE, "ov_pilot") in existing else ISSUE_FIX_ROOT_ID
    candidates.append(
        _node_event(
            goal_id=goal_id,
            node_id=root_id,
            title="PR issue-fix campaign",
            node_kind=NODE_KIND_AREA,
            status=NODE_STATUS_EXPLORING,
            summary="Long-running issue selection, delivery, review, and capability improvement.",
            parent_id=None,
            blocked_reason=None,
            evidence_refs=[],
            tags=["issue-fix", "campaign"],
            agent_id=agent_id,
            existing=existing,
        )
    )
    if has_issue_facts:
        candidates.append(
            _node_event(
                goal_id=goal_id,
                node_id=ISSUE_FIX_LANE_ID,
                title="Focused issue fixes",
                node_kind=NODE_KIND_AREA,
                status=NODE_STATUS_EXPLORING,
                summary="Public issues progressed through reproduction, patch, validation, and closeout.",
                parent_id=root_id,
                blocked_reason=None,
                evidence_refs=[],
                tags=["issue-fix", "delivery"],
                agent_id=agent_id,
                existing=existing,
            )
        )
    if has_capability_facts:
        candidates.append(
            _node_event(
                goal_id=goal_id,
                node_id=CAPABILITY_LANE_ID,
                title="Agent capability improvements",
                node_kind=NODE_KIND_AREA,
                status=NODE_STATUS_EXPLORING,
                summary="Generic LoopX gaps found, fixed, and verified in real call sites.",
                parent_id=root_id,
                blocked_reason=None,
                evidence_refs=[],
                tags=["issue-fix", "capability"],
                agent_id=agent_id,
                existing=existing,
            )
        )

    for outcome in outcomes:
        issue_number = _issue_number(outcome)
        node_id = _issue_node_id(outcome)
        stage = str(outcome.get("stage") or "planned").strip().lower()
        status = (
            NODE_STATUS_RESOLVED
            if stage in _TERMINAL_STAGES
            else NODE_STATUS_BLOCKED
            if stage in _BLOCKED_STAGES
            else NODE_STATUS_EXPLORING
        )
        issue = outcome.get("issue") if isinstance(outcome.get("issue"), Mapping) else {}
        pull_request = outcome.get("pull_request") if isinstance(outcome.get("pull_request"), Mapping) else {}
        pr_number = str(pull_request.get("number") or "").strip()
        repo = str(outcome.get("repo") or "repository")
        route = str(outcome.get("route") or "triage")
        title = f"[{route}] {repo} #{issue_number}"
        if pr_number:
            title += f" -> PR #{pr_number}"
        next_action = _text(outcome.get("next_action"), limit=500)
        source_ref = f"evidence:issue-fix:{node_id}"
        evidence = _refs(issue.get("url"), pull_request.get("url"), source_ref=source_ref)
        candidates.append(
            _node_event(
                goal_id=goal_id,
                node_id=node_id,
                title=title,
                node_kind=NODE_KIND_ARTIFACT,
                status=status,
                summary=_text(outcome.get("summary") or f"Current stage: {stage}"),
                parent_id=ISSUE_FIX_LANE_ID,
                blocked_reason=next_action or f"Issue fix is blocked at {stage}."
                if status == NODE_STATUS_BLOCKED
                else None,
                evidence_refs=evidence,
                tags=["issue-fix", route, stage],
                agent_id=agent_id,
                existing=existing,
            )
        )
        candidate_node_id = _token(f"fix_{issue_number}_candidate", fallback="fix_candidate")
        if pr_number and (EVENT_KIND_NODE, candidate_node_id) in existing:
            candidates.append(
                _node_event(
                    goal_id=goal_id,
                    node_id=candidate_node_id,
                    title=f"[{route}] {repo} #{issue_number} candidate",
                    node_kind=NODE_KIND_ARTIFACT,
                    status=NODE_STATUS_RESOLVED,
                    summary=f"Superseded by published PR #{pr_number}.",
                    parent_id=ISSUE_FIX_LANE_ID,
                    blocked_reason=None,
                    evidence_refs=evidence,
                    tags=["issue-fix", route, "superseded"],
                    agent_id=agent_id,
                    existing=existing,
                )
            )
            candidates.append(
                build_explore_edge_event(
                    goal_id=goal_id,
                    from_node=candidate_node_id,
                    to_node=node_id,
                    edge_type="leads_to",
                    summary=f"Issue #{issue_number} candidate became PR #{pr_number}.",
                    confidence=1.0,
                    agent_id=agent_id,
                )
            )
        reproduction = outcome.get("reproduction") if isinstance(outcome.get("reproduction"), Mapping) else {}
        repro_status = str(reproduction.get("status") or "unknown")
        validation = outcome.get("validation") if isinstance(outcome.get("validation"), Mapping) else {}
        validation_status = str(validation.get("status") or "unknown")
        lifecycle_summary = "; ".join(
            part
            for part in (
                f"reproduction={repro_status}",
                f"validation={validation_status}",
                f"PR #{pr_number} published" if pr_number else "no PR published",
                next_action,
            )
            if part
        )
        candidates.append(
            build_explore_finding_event(
                goal_id=goal_id,
                finding_id=_token(
                    f"issue_{issue_number}_{pr_number or 'candidate'}_lifecycle",
                    fallback="issue_lifecycle",
                ),
                node_id=node_id,
                title=f"Issue #{issue_number} reached {stage}",
                summary=_text(lifecycle_summary),
                status=FINDING_STATUS_CONFIRMED
                if repro_status not in {"missing", "unknown", "blocked"}
                else FINDING_STATUS_TENTATIVE,
                evidence_refs=_refs(
                    evidence,
                    reproduction.get("evidence_refs"),
                    validation.get("evidence_refs"),
                    source_ref=source_ref,
                ),
                tags=["issue-fix", "lifecycle", stage],
                agent_id=agent_id,
            )
        )

    capability_state: dict[str, tuple[str, str, list[str], str | None]] = {}
    capability_findings: list[dict[str, Any]] = []
    for event in capability_events:
        todo_id = str(event.get("todo_id") or "")
        todo = todo_by_id.get(todo_id)
        details = event.get("details") if isinstance(event.get("details"), Mapping) else {}
        raw_capabilities = str(details.get("target_capabilities") or "")
        capabilities = [item.strip() for item in raw_capabilities.split(",") if item.strip()]
        if not capabilities and todo:
            capabilities = _todo_capabilities(todo)
        if not capabilities:
            capabilities = ["issue_fix_capability"]
        status = str(event.get("status") or "found").strip().lower()
        node_ids = _capability_node_ids(todo, capabilities)
        source_ref = (
            f"evidence:rollout:{event['event_id']}"
            if event.get("event_id")
            else f"evidence:capability:{node_ids[0]}"
        )
        evidence = _refs(
            details.get("evidence"),
            event.get("artifact_refs"),
            source_ref=source_ref,
        )
        for index, node_id in enumerate(node_ids):
            capability = capabilities[min(index, len(capabilities) - 1)]
            capability_state[node_id] = (status, capability, evidence, todo_id or None)
            capability_findings.append(
                build_explore_finding_event(
                    goal_id=goal_id,
                    finding_id=_token(f"gap_{node_id}", fallback="capability_gap"),
                    node_id=node_id,
                    title=f"Capability {capability}: {status}",
                    summary=_text(event.get("summary") or f"Capability gap {status}."),
                    status=FINDING_STATUS_CONFIRMED,
                    evidence_refs=evidence,
                    tags=["capability-gap", status],
                    agent_id=str(event.get("agent_id") or agent_id or "") or None,
                )
            )
    for todo in todos:
        refs = _todo_node_refs(todo)
        capabilities = _todo_capabilities(todo)
        for index, node_id in enumerate(refs):
            capability_state.setdefault(
                node_id,
                (
                    "found",
                    capabilities[min(index, len(capabilities) - 1)] if capabilities else node_id,
                    _refs(todo.get("evidence"), source_ref=f"evidence:todo:{todo['todo_id']}"),
                    str(todo.get("todo_id") or "") or None,
                ),
            )
    for node_id, (status, capability, evidence, todo_id) in capability_state.items():
        candidates.append(
            _node_event(
                goal_id=goal_id,
                node_id=node_id,
                title=f"Capability: {capability}",
                node_kind=NODE_KIND_ARTIFACT,
                status=NODE_STATUS_RESOLVED if status in _CAPABILITY_RESOLVED_STATUSES else NODE_STATUS_EXPLORING,
                summary=f"Generic issue-fix capability gap is {status}.",
                parent_id=CAPABILITY_LANE_ID,
                blocked_reason=None,
                evidence_refs=evidence,
                tags=["issue-fix", "capability-gap", status],
                agent_id=agent_id,
                existing=existing,
            )
        )
    candidates.extend(capability_findings)

    todo_nodes: dict[str, list[str]] = {}
    for todo_id, todo in todo_by_id.items():
        nodes = _todo_node_refs(todo)
        if not nodes and _todo_capabilities(todo):
            nodes = _capability_node_ids(todo, _todo_capabilities(todo))
        if nodes:
            todo_nodes[todo_id] = nodes
    for todo_id, todo in todo_by_id.items():
        successor_id = str(todo.get("superseded_by") or "").strip()
        if not successor_id or todo_id not in todo_nodes:
            continue
        for node_id in todo_nodes[todo_id]:
            candidates.append(
                build_explore_finding_event(
                    goal_id=goal_id,
                    finding_id=_token(f"superseded_{todo_id}_{node_id}", fallback="superseded"),
                    node_id=node_id,
                    title=f"Todo {todo_id} superseded",
                    summary=f"Continued by {successor_id}.",
                    status=FINDING_STATUS_CONFIRMED,
                    tags=["todo", "supersession"],
                    agent_id=agent_id,
                )
            )
            for successor_node in todo_nodes.get(successor_id, []):
                if successor_node != node_id:
                    candidates.append(
                        build_explore_edge_event(
                            goal_id=goal_id,
                            from_node=node_id,
                            to_node=successor_node,
                            edge_type="leads_to",
                            summary=f"Todo {todo_id} continued as {successor_id}.",
                            confidence=1.0,
                            agent_id=agent_id,
                        )
                    )
    # A lifecycle can contain several observations for the same node. Keep the
    # latest derived event per stable result id so a repeated projection cannot
    # oscillate between earlier and later states.
    deduplicated: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for event in candidates:
        key = (str(event.get("event_kind") or ""), str(event.get("result_id") or ""))
        if key not in deduplicated:
            order.append(key)
        deduplicated[key] = event
    return [deduplicated[key] for key in order]


def project_issue_fix_explore_graph(
    *,
    registry_path: Path,
    goal_id: str,
    agent_id: str | None = None,
    project: Path | None = None,
    state_file: Path | None = None,
    execute: bool = False,
) -> dict[str, Any]:
    """Derive and optionally append material issue-fix Explore events."""

    resolved_project, resolved_state_file = resolve_todo_state_path(
        registry_path=registry_path,
        goal_id=goal_id,
        project=project,
        state_file=state_file,
    )
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, registry_path=registry_path)
    result_log = explore_result_log_path(runtime_root, goal_id)
    existing_events = load_explore_result_events(result_log, goal_id=goal_id)
    outcomes_packet = build_issue_fix_outcome_collection_from_domain_state(
        goal_id=goal_id,
        project=resolved_project or project or Path("."),
        agent_id=agent_id,
    )
    todos = _all_todos(resolved_state_file)
    rollout_events = load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    candidates = _candidate_events(
        goal_id=goal_id,
        agent_id=agent_id,
        outcomes=[item for item in outcomes_packet.get("issue_fix_outcomes") or [] if isinstance(item, Mapping)],
        todos=todos,
        rollout_events=rollout_events,
        existing_events=existing_events,
    )
    applicable = bool(outcomes_packet.get("issue_fix_outcomes")) or (
        EVENT_KIND_NODE,
        ISSUE_FIX_LANE_ID,
    ) in _latest_events(existing_events)
    latest = _latest_events(existing_events)
    material_events = [
        event
        for event in candidates
        if _material_event(event)
        != _material_event(latest.get((str(event.get("event_kind") or ""), str(event.get("result_id") or ""))) or {})
    ]
    if execute:
        for event in material_events:
            append_explore_result_event(result_log, event)
    projected_events = [*existing_events, *material_events]
    projection = build_explore_result_projection(
        projected_events,
        goal_id=goal_id,
        finding_limit=len(projected_events),
    )
    digest = _semantic_projection_digest(projection)
    return {
        "ok": True,
        "schema_version": ISSUE_FIX_EXPLORE_PROJECTION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "execute": execute,
        "applicable": applicable,
        "source_contract": {
            "issue_fix_domain_state": "source",
            "todo_state": "source",
            "rollout_event_log": "source",
            "explore_result_log": "idempotent_projection",
            "creates_parallel_state_machine": False,
        },
        "candidate_event_count": len(candidates),
        "material_event_count": len(material_events),
        "appended_event_count": len(material_events) if execute else 0,
        "material_change": bool(material_events),
        "semantic_digest": digest,
        "counts": projection.get("counts"),
        "warnings": list(outcomes_packet.get("warnings") or []),
        "projection": projection,
        "boundary": {
            "raw_logs_recorded": False,
            "raw_transcripts_recorded": False,
            "credentials_recorded": False,
            "absolute_paths_projected": False,
        },
    }
