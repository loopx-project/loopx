"""Append-only exploration topology events: nodes, edges, and findings.

Long-running exploration loops (Codex, Claude Code, or another runtime driven
through LoopX) record compact, public-safe result facts here as a topology:
exploration nodes (questions, areas, hypotheses, experiments, artifacts) with
explicit status including where the loop is blocked and why, typed edges
between nodes, and findings attached to nodes. The log is the bounded LoopX
projection source for operator display sinks such as the Lark exploration
result board; sinks render the projection built by
:func:`build_explore_result_projection` and never read raw transcripts,
session files, or private planning documents.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...file_lock import exclusive_file_lock


EXPLORE_RESULT_EVENT_SCHEMA_VERSION = "loopx_explore_result_event_v0"
EXPLORE_RESULT_PROJECTION_VERSION = "loopx_explore_result_projection_v0"
DEFAULT_EXPLORE_RESULT_LOG_NAME = "explore-result-log.jsonl"

EVENT_KIND_NODE = "node"
EVENT_KIND_EDGE = "edge"
EVENT_KIND_FINDING = "finding"
EXPLORE_EVENT_KINDS = {EVENT_KIND_NODE, EVENT_KIND_EDGE, EVENT_KIND_FINDING}

NODE_KIND_QUESTION = "question"
NODE_KIND_AREA = "area"
NODE_KIND_HYPOTHESIS = "hypothesis"
NODE_KIND_EXPERIMENT = "experiment"
NODE_KIND_ARTIFACT = "artifact"
NODE_KINDS = {
    NODE_KIND_QUESTION,
    NODE_KIND_AREA,
    NODE_KIND_HYPOTHESIS,
    NODE_KIND_EXPERIMENT,
    NODE_KIND_ARTIFACT,
}

NODE_STATUS_OPEN = "open"
NODE_STATUS_EXPLORING = "exploring"
NODE_STATUS_BLOCKED = "blocked"
NODE_STATUS_RESOLVED = "resolved"
NODE_STATUS_DEAD_END = "dead_end"
NODE_STATUSES = {
    NODE_STATUS_OPEN,
    NODE_STATUS_EXPLORING,
    NODE_STATUS_BLOCKED,
    NODE_STATUS_RESOLVED,
    NODE_STATUS_DEAD_END,
}

EDGE_TYPE_SUBTOPIC_OF = "subtopic_of"
EDGE_TYPES = {
    EDGE_TYPE_SUBTOPIC_OF,
    "depends_on",
    "answers",
    "supports",
    "refutes",
    "leads_to",
}

FINDING_STATUS_TENTATIVE = "tentative"
FINDING_STATUS_CONFIRMED = "confirmed"
FINDING_STATUS_REFUTED = "refuted"
FINDING_STATUSES = {
    FINDING_STATUS_TENTATIVE,
    FINDING_STATUS_CONFIRMED,
    FINDING_STATUS_REFUTED,
}

TITLE_LIMIT = 200
SUMMARY_LIMIT = 1200
REF_LIMIT = 240
MAX_EVIDENCE_REFS = 16
MAX_TAGS = 8
DEFAULT_FINDING_LIMIT = 200
DEFAULT_MERMAID_NODE_LIMIT = 60
DEFAULT_TREE_DEPTH_LIMIT = 6

RESULT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,95}$")
_WINDOWS_ABS_PATH = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z]:[\\/](?![\\/])")

FORBIDDEN_TEXT_MARKERS = (
    "/" + "Users/",
    "/" + "root/",
    "/" + "home/",
    "/" + "private/",
    "\\" + "Users\\",
    "\\" + "root\\",
    "\\" + "home\\",
    "\\" + "private\\",
    ".local/" + "private",
    ".local\\" + "private",
    "Auth" + "orization:",
    "api" + "_key",
    "api" + "key",
    "pass" + "word",
    "sec" + "ret=",
    "tok" + "en=",
)

PUBLIC_BOUNDARY = {
    "raw_task_text_recorded": False,
    "raw_logs_recorded": False,
    "raw_trajectory_recorded": False,
    "raw_session_transcript_recorded": False,
    "credential_values_recorded": False,
    "absolute_paths_recorded": False,
}


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _compact_text(value: Any, *, limit: int, field: str) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.lower()
    if "file://" in lowered or _WINDOWS_ABS_PATH.search(text):
        raise ValueError(f"{field} contains private or credential-like material")
    for marker in FORBIDDEN_TEXT_MARKERS:
        if marker.lower() in lowered:
            raise ValueError(f"{field} contains private or credential-like material")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _safe_public_ref(value: Any, *, field: str) -> str:
    text = _compact_text(value, limit=REF_LIMIT, field=field)
    if not text:
        raise ValueError(f"{field} is empty")
    path = Path(text)
    if (
        text.startswith(("~", "/", "\\"))
        or text.lower().startswith("file://")
        or _WINDOWS_ABS_PATH.match(text)
        or path.is_absolute()
        or ".." in path.parts
    ):
        raise ValueError(f"{field} must be a public relative ref or opaque id, not a local path")
    return text


def _safe_public_refs(values: Sequence[Any] | None, *, field: str, max_items: int) -> list[str]:
    refs: list[str] = []
    for index, value in enumerate(values or []):
        refs.append(_safe_public_ref(value, field=f"{field}[{index}]"))
    return refs[:max_items]


def _safe_result_id(value: Any, *, field: str) -> str:
    text = str(value or "").strip()
    if not RESULT_ID_PATTERN.match(text):
        raise ValueError(f"{field} must match {RESULT_ID_PATTERN.pattern}")
    return text


def _safe_goal_id(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("goal_id is required")
    if text != Path(text).name or text in {".", ".."}:
        raise ValueError("goal_id must be a single path segment")
    return text


def _safe_confidence(value: Any) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return round(number, 3)


def _event_id(payload: Mapping[str, Any]) -> str:
    stable = {key: value for key, value in payload.items() if key != "event_id"}
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _derived_result_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def explore_result_log_path(runtime_root: Path, goal_id: str) -> Path:
    return (
        runtime_root.expanduser()
        / "goals"
        / _safe_goal_id(goal_id)
        / DEFAULT_EXPLORE_RESULT_LOG_NAME
    )


def _choice(value: Any, *, choices: set[str], default: str, field: str) -> str:
    text = str(value or default).strip().lower()
    if text not in choices:
        raise ValueError(f"unsupported {field} {value!r}; choose {', '.join(sorted(choices))}")
    return text


def build_explore_node_event(
    *,
    goal_id: str,
    title: str,
    node_id: str | None = None,
    node_kind: str | None = None,
    status: str | None = None,
    summary: str | None = None,
    blocked_reason: str | None = None,
    parent_id: str | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    evidence_refs: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    supersedes: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    safe_goal_id = _safe_goal_id(goal_id)
    safe_title = _compact_text(title, limit=TITLE_LIMIT, field="title")
    if not safe_title:
        raise ValueError("title is required")
    resolved_status = _choice(status, choices=NODE_STATUSES, default=NODE_STATUS_OPEN, field="status")
    safe_blocked_reason = _compact_text(blocked_reason, limit=SUMMARY_LIMIT, field="blocked_reason")
    if resolved_status == NODE_STATUS_BLOCKED and not safe_blocked_reason:
        raise ValueError("blocked nodes must state a blocked_reason")
    event = _base_event(
        goal_id=safe_goal_id,
        event_kind=EVENT_KIND_NODE,
        title=safe_title,
        summary=summary,
        agent_id=agent_id,
        run_id=run_id,
        evidence_refs=evidence_refs,
        tags=tags,
        supersedes=supersedes,
        recorded_at=recorded_at,
    )
    event["result_id"] = (
        _safe_result_id(node_id, field="node_id")
        if node_id
        else _derived_result_id("node", safe_goal_id, safe_title.lower())
    )
    event["node_kind"] = _choice(node_kind, choices=NODE_KINDS, default=NODE_KIND_AREA, field="node_kind")
    event["status"] = resolved_status
    if safe_blocked_reason:
        event["blocked_reason"] = safe_blocked_reason
    if parent_id:
        event["parent_id"] = _safe_result_id(parent_id, field="parent_id")
    event["event_id"] = _event_id(event)
    return event


def build_explore_edge_event(
    *,
    goal_id: str,
    from_node: str,
    to_node: str,
    edge_type: str,
    summary: str | None = None,
    confidence: float | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    safe_goal_id = _safe_goal_id(goal_id)
    safe_from = _safe_result_id(from_node, field="from_node")
    safe_to = _safe_result_id(to_node, field="to_node")
    if safe_from == safe_to:
        raise ValueError("edge must connect two different nodes")
    resolved_type = _choice(edge_type, choices=EDGE_TYPES, default="", field="edge_type")
    event = _base_event(
        goal_id=safe_goal_id,
        event_kind=EVENT_KIND_EDGE,
        title=f"{safe_from} -{resolved_type}-> {safe_to}",
        summary=summary,
        agent_id=agent_id,
        run_id=run_id,
        evidence_refs=None,
        tags=None,
        supersedes=None,
        recorded_at=recorded_at,
    )
    event["result_id"] = _derived_result_id("edge", safe_goal_id, safe_from, resolved_type, safe_to)
    event["from_node"] = safe_from
    event["to_node"] = safe_to
    event["edge_type"] = resolved_type
    safe_confidence = _safe_confidence(confidence)
    if safe_confidence is not None:
        event["confidence"] = safe_confidence
    event["event_id"] = _event_id(event)
    return event


def build_explore_finding_event(
    *,
    goal_id: str,
    title: str,
    finding_id: str | None = None,
    node_id: str | None = None,
    status: str | None = None,
    summary: str | None = None,
    confidence: float | None = None,
    agent_id: str | None = None,
    run_id: str | None = None,
    evidence_refs: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    supersedes: str | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    safe_goal_id = _safe_goal_id(goal_id)
    safe_title = _compact_text(title, limit=TITLE_LIMIT, field="title")
    if not safe_title:
        raise ValueError("title is required")
    event = _base_event(
        goal_id=safe_goal_id,
        event_kind=EVENT_KIND_FINDING,
        title=safe_title,
        summary=summary,
        agent_id=agent_id,
        run_id=run_id,
        evidence_refs=evidence_refs,
        tags=tags,
        supersedes=supersedes,
        recorded_at=recorded_at,
    )
    event["result_id"] = (
        _safe_result_id(finding_id, field="finding_id")
        if finding_id
        else _derived_result_id("finding", safe_goal_id, safe_title.lower())
    )
    if node_id:
        event["node_id"] = _safe_result_id(node_id, field="node_id")
    event["status"] = _choice(
        status, choices=FINDING_STATUSES, default=FINDING_STATUS_TENTATIVE, field="status"
    )
    safe_confidence = _safe_confidence(confidence)
    if safe_confidence is not None:
        event["confidence"] = safe_confidence
    event["event_id"] = _event_id(event)
    return event


def _base_event(
    *,
    goal_id: str,
    event_kind: str,
    title: str,
    summary: str | None,
    agent_id: str | None,
    run_id: str | None,
    evidence_refs: Sequence[str] | None,
    tags: Sequence[str] | None,
    supersedes: str | None,
    recorded_at: str | None,
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "schema_version": EXPLORE_RESULT_EVENT_SCHEMA_VERSION,
        "goal_id": goal_id,
        "event_kind": event_kind,
        "recorded_at": str(recorded_at or _now_iso()),
        "title": title,
        "boundary": dict(PUBLIC_BOUNDARY),
    }
    safe_summary = _compact_text(summary, limit=SUMMARY_LIMIT, field="summary")
    if safe_summary:
        event["summary"] = safe_summary
    if agent_id:
        event["agent_id"] = _compact_text(agent_id, limit=80, field="agent_id")
    if run_id:
        event["run_id"] = _safe_public_ref(run_id, field="run_id")
    refs = _safe_public_refs(evidence_refs, field="evidence_refs", max_items=MAX_EVIDENCE_REFS)
    if refs:
        event["evidence_refs"] = refs
    safe_tags = [
        _compact_text(tag, limit=48, field=f"tags[{index}]")
        for index, tag in enumerate(tags or [])
    ]
    safe_tags = [tag for tag in safe_tags if tag][:MAX_TAGS]
    if safe_tags:
        event["tags"] = safe_tags
    if supersedes:
        event["supersedes"] = _safe_result_id(supersedes, field="supersedes")
    return event


def validate_explore_result_event(
    event: Mapping[str, Any],
    *,
    expected_goal_id: str | None = None,
) -> dict[str, Any]:
    """Return one canonical public-safe Explore event or fail closed.

    Event builders are the schema authority. Rebuilding the event catches
    unknown fields, invalid ids, unsafe text, forged boundary flags, and stale
    event ids without maintaining a second field-level validator.
    """

    payload = dict(event)
    if payload.get("schema_version") != EXPLORE_RESULT_EVENT_SCHEMA_VERSION:
        raise ValueError(f"explore result event must use schema {EXPLORE_RESULT_EVENT_SCHEMA_VERSION}")
    event_kind = str(payload.get("event_kind") or "")
    if event_kind not in EXPLORE_EVENT_KINDS:
        raise ValueError(f"unsupported explore result event kind {event_kind!r}")
    goal_id = _safe_goal_id(payload.get("goal_id"))
    if expected_goal_id is not None and goal_id != _safe_goal_id(expected_goal_id):
        raise ValueError("explore result event belongs to a different goal")
    if payload.get("boundary") != PUBLIC_BOUNDARY:
        raise ValueError("explore result event must declare the public-safe boundary")

    common = {
        "goal_id": goal_id,
        "summary": payload.get("summary"),
        "agent_id": payload.get("agent_id"),
        "run_id": payload.get("run_id"),
        "recorded_at": payload.get("recorded_at"),
    }
    if event_kind == EVENT_KIND_NODE:
        rebuilt = build_explore_node_event(
            **common,
            title=payload.get("title"),
            node_id=payload.get("result_id"),
            node_kind=payload.get("node_kind"),
            status=payload.get("status"),
            blocked_reason=payload.get("blocked_reason"),
            parent_id=payload.get("parent_id"),
            evidence_refs=payload.get("evidence_refs"),
            tags=payload.get("tags"),
            supersedes=payload.get("supersedes"),
        )
    elif event_kind == EVENT_KIND_EDGE:
        rebuilt = build_explore_edge_event(
            **common,
            from_node=payload.get("from_node"),
            to_node=payload.get("to_node"),
            edge_type=payload.get("edge_type"),
            confidence=payload.get("confidence"),
        )
    else:
        rebuilt = build_explore_finding_event(
            **common,
            title=payload.get("title"),
            finding_id=payload.get("result_id"),
            node_id=payload.get("node_id"),
            status=payload.get("status"),
            confidence=payload.get("confidence"),
            evidence_refs=payload.get("evidence_refs"),
            tags=payload.get("tags"),
            supersedes=payload.get("supersedes"),
        )
    if payload != rebuilt:
        raise ValueError("explore result event is not canonical or contains unknown fields")
    return rebuilt


def append_explore_result_event(path: Path, event: Mapping[str, Any]) -> dict[str, Any]:
    validated = validate_explore_result_event(event)
    log_path = path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(log_path):
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(validated, ensure_ascii=False, sort_keys=True) + "\n")
    return {
        "ok": True,
        "schema_version": EXPLORE_RESULT_EVENT_SCHEMA_VERSION,
        "path": str(log_path),
        "event_id": validated.get("event_id"),
        "result_id": validated.get("result_id"),
        "event_kind": validated.get("event_kind"),
    }


def append_explore_result_events(
    path: Path,
    events: Sequence[Mapping[str, Any]],
    *,
    expected_goal_id: str,
) -> dict[str, Any]:
    """Append a validated batch once by event id under the result-log lock."""

    validated = [validate_explore_result_event(event, expected_goal_id=expected_goal_id) for event in events]
    requested_ids = [str(event["event_id"]) for event in validated]
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("Explore result event batch contains duplicate event ids")

    log_path = path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    reused = 0
    with exclusive_file_lock(log_path):
        existing_by_id: dict[str, dict[str, Any]] = {}
        if log_path.exists():
            for line in log_path.read_text(encoding="utf-8").split("\n"):
                try:
                    current = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(current, dict) and current.get("event_id"):
                    existing_by_id[str(current["event_id"])] = current
        pending: list[dict[str, Any]] = []
        for event in validated:
            event_id = str(event["event_id"])
            existing = existing_by_id.get(event_id)
            if existing is None:
                pending.append(event)
                continue
            if existing != event:
                raise ValueError(f"conflicting Explore result event id: {event_id}")
            reused += 1
        if pending:
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write("".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in pending))
            appended = len(pending)
    return {
        "ok": True,
        "schema_version": EXPLORE_RESULT_EVENT_SCHEMA_VERSION,
        "requested_event_count": len(validated),
        "appended_event_count": appended,
        "reused_event_count": reused,
    }


def load_explore_result_events(
    path: Path,
    *,
    goal_id: str | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    log_path = path.expanduser()
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get("schema_version") != EXPLORE_RESULT_EVENT_SCHEMA_VERSION:
            continue
        if payload.get("event_kind") not in EXPLORE_EVENT_KINDS:
            continue
        if goal_id and str(payload.get("goal_id") or "") != goal_id:
            continue
        events.append(payload)
    if limit is not None and limit >= 0:
        events = events[-limit:] if limit else []
    return events


def load_explore_result_events_strict(
    path: Path,
    *,
    goal_id: str,
) -> list[dict[str, Any]]:
    """Load a complete result log and reject malformed or unsafe rows."""

    log_path = path.expanduser()
    if not log_path.exists():
        return []
    events: list[dict[str, Any]] = []
    for line_number, line in enumerate(log_path.read_text(encoding="utf-8").split("\n"), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid Explore result JSON at line {line_number}") from exc
        if not isinstance(payload, dict):
            raise ValueError(f"Explore result row {line_number} is not an object")
        try:
            events.append(validate_explore_result_event(payload, expected_goal_id=goal_id))
        except ValueError as exc:
            raise ValueError(f"invalid Explore result event at line {line_number}: {exc}") from exc
    return events


def _fold_by_result_id(
    events: Sequence[Mapping[str, Any]], *, event_kind: str
) -> list[dict[str, Any]]:
    folded: dict[str, dict[str, Any]] = {}
    for event in events:
        if event.get("event_kind") != event_kind:
            continue
        result_id = str(event.get("result_id") or "")
        if not result_id:
            continue
        prior = folded.get(result_id)
        view = dict(event)
        view["first_recorded_at"] = (
            prior["first_recorded_at"] if prior else str(event.get("recorded_at") or "")
        )
        view["last_updated_at"] = str(event.get("recorded_at") or "")
        view["update_count"] = (prior["update_count"] + 1) if prior else 1
        folded[result_id] = view
    return list(folded.values())


def _node_view(event: Mapping[str, Any], *, finding_count: int) -> dict[str, Any]:
    return {
        "node_id": str(event.get("result_id") or ""),
        "title": str(event.get("title") or ""),
        "node_kind": str(event.get("node_kind") or NODE_KIND_AREA),
        "status": str(event.get("status") or NODE_STATUS_OPEN),
        "summary": str(event.get("summary") or ""),
        "blocked_reason": str(event.get("blocked_reason") or ""),
        "parent_id": str(event.get("parent_id") or ""),
        "agent_id": str(event.get("agent_id") or ""),
        "evidence_refs": list(event.get("evidence_refs") or []),
        "tags": list(event.get("tags") or []),
        "supersedes": str(event.get("supersedes") or ""),
        "finding_count": finding_count,
        "first_recorded_at": str(event.get("first_recorded_at") or ""),
        "last_updated_at": str(event.get("last_updated_at") or ""),
        "update_count": int(event.get("update_count") or 1),
    }


def _edge_view(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "edge_id": str(event.get("result_id") or ""),
        "from_node": str(event.get("from_node") or ""),
        "to_node": str(event.get("to_node") or ""),
        "edge_type": str(event.get("edge_type") or ""),
        "summary": str(event.get("summary") or ""),
        "confidence": event.get("confidence"),
        "last_updated_at": str(event.get("last_updated_at") or ""),
    }


def _materialized_parent_edges(
    nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    """Represent node parent links as display edges for graph-oriented sinks.

    ``parent_id`` is the compact tree source of truth, but visual consumers such
    as Base relationship views and Sankey-like components need rows in the edge
    table. These derived edges are parent -> child and use ``supports`` so they
    do not alter the ``subtopic_of`` tree parser.
    """

    known = {str(node.get("node_id") or "") for node in nodes}
    existing_pairs = {
        frozenset((str(edge.get("from_node") or ""), str(edge.get("to_node") or "")))
        for edge in edges
    }
    derived: list[dict[str, Any]] = []
    for node in nodes:
        child = str(node.get("node_id") or "")
        parent = str(node.get("parent_id") or "")
        if not child or not parent or child == parent or parent not in known:
            continue
        if frozenset((parent, child)) in existing_pairs:
            continue
        digest = hashlib.sha1(f"{parent}->{child}".encode("utf-8")).hexdigest()[:12]
        derived.append(
            {
                "edge_id": f"parent_{digest}",
                "from_node": parent,
                "to_node": child,
                "edge_type": "supports",
                "summary": "Parent topic contains this exploration node.",
                "confidence": 1.0,
                "last_updated_at": str(node.get("last_updated_at") or ""),
                "materialized_from": "node_parent_id",
            }
        )
    return derived


def _finding_view(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": str(event.get("result_id") or ""),
        "finding": str(event.get("title") or ""),
        "summary": str(event.get("summary") or ""),
        "status": str(event.get("status") or FINDING_STATUS_TENTATIVE),
        "confidence": event.get("confidence"),
        "node_id": str(event.get("node_id") or ""),
        "agent_id": str(event.get("agent_id") or ""),
        "evidence_refs": list(event.get("evidence_refs") or []),
        "tags": list(event.get("tags") or []),
        "supersedes": str(event.get("supersedes") or ""),
        "first_recorded_at": str(event.get("first_recorded_at") or ""),
        "last_updated_at": str(event.get("last_updated_at") or ""),
        "update_count": int(event.get("update_count") or 1),
    }


def _parent_map(nodes: Sequence[Mapping[str, Any]], edges: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    known = {str(node.get("node_id")) for node in nodes}
    parents: dict[str, str] = {}
    for edge in edges:
        if str(edge.get("edge_type")) != EDGE_TYPE_SUBTOPIC_OF:
            continue
        child = str(edge.get("from_node") or "")
        parent = str(edge.get("to_node") or "")
        if child in known and parent in known:
            parents[child] = parent
    for node in nodes:
        parent = str(node.get("parent_id") or "")
        if parent and parent in known:
            parents[str(node.get("node_id"))] = parent
    return parents


def _build_tree(
    nodes: Sequence[Mapping[str, Any]],
    parents: Mapping[str, str],
    *,
    depth_limit: int = DEFAULT_TREE_DEPTH_LIMIT,
) -> list[dict[str, Any]]:
    children: dict[str, list[str]] = {}
    node_by_id = {str(node.get("node_id")): node for node in nodes}
    roots: list[str] = []
    for node_id in node_by_id:
        parent = parents.get(node_id)
        if parent and parent != node_id and parent in node_by_id:
            children.setdefault(parent, []).append(node_id)
        else:
            roots.append(node_id)

    def branch(node_id: str, *, depth: int, seen: frozenset[str]) -> dict[str, Any]:
        node = node_by_id[node_id]
        view = {
            "node_id": node_id,
            "title": str(node.get("title") or ""),
            "status": str(node.get("status") or NODE_STATUS_OPEN),
            "children": [],
        }
        if depth < depth_limit:
            view["children"] = [
                branch(child, depth=depth + 1, seen=seen | {node_id})
                for child in children.get(node_id, [])
                if child not in seen
            ]
        return view

    return [branch(root, depth=1, seen=frozenset({root})) for root in roots]


_MERMAID_STATUS_CLASS = {
    NODE_STATUS_OPEN: "open",
    NODE_STATUS_EXPLORING: "exploring",
    NODE_STATUS_BLOCKED: "blocked",
    NODE_STATUS_RESOLVED: "resolved",
    NODE_STATUS_DEAD_END: "deadend",
}


def _mermaid_label(text: str) -> str:
    cleaned = re.sub(r'["\[\]{}<>`|]', "'", str(text or ""))
    return cleaned[:60].strip() or "untitled"


def _mermaid_id(node_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(node_id))


def build_explore_mermaid(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    node_limit: int = DEFAULT_MERMAID_NODE_LIMIT,
) -> str:
    """Render the exploration topology as Mermaid flowchart source."""

    lines = ["flowchart TD"]
    shown = list(nodes)[:node_limit]
    shown_ids = {str(node.get("node_id")) for node in shown}
    for node in shown:
        node_id = _mermaid_id(str(node.get("node_id")))
        status = str(node.get("status") or NODE_STATUS_OPEN)
        marker = {"blocked": " (BLOCKED)", "resolved": " (done)", "dead_end": " (dead end)"}.get(
            status, ""
        )
        label = _mermaid_label(f"{node.get('title')}{marker}")
        lines.append(f'    {node_id}["{label}"]:::{_MERMAID_STATUS_CLASS.get(status, "open")}')
    for edge in edges:
        from_node = str(edge.get("from_node") or "")
        to_node = str(edge.get("to_node") or "")
        if from_node not in shown_ids or to_node not in shown_ids:
            continue
        label = _mermaid_label(str(edge.get("edge_type") or ""))
        lines.append(f"    {_mermaid_id(from_node)} -->|{label}| {_mermaid_id(to_node)}")
    if len(nodes) > node_limit:
        lines.append(f"    %% {len(nodes) - node_limit} more nodes omitted")
    lines.extend(
        [
            "    classDef open fill:#f5f5f5,stroke:#9e9e9e",
            "    classDef exploring fill:#e3f2fd,stroke:#1e88e5",
            "    classDef blocked fill:#ffebee,stroke:#e53935,stroke-width:2px",
            "    classDef resolved fill:#e8f5e9,stroke:#43a047",
            "    classDef deadend fill:#eeeeee,stroke:#9e9e9e,stroke-dasharray: 4 4",
        ]
    )
    return "\n".join(lines)


def build_explore_graph_view(
    nodes: Sequence[Mapping[str, Any]],
    edges: Sequence[Mapping[str, Any]],
    *,
    statuses: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    include_ancestors: bool = True,
    node_limit: int = DEFAULT_MERMAID_NODE_LIMIT,
) -> dict[str, Any]:
    """Build a focused graph without mutating the full result projection.

    Status and tag filters are combined with AND semantics. Tag matching uses
    exact values and is OR within the requested tag set. Ancestors are included
    by default so a focused evidence graph keeps enough topology to explain
    where each matched node belongs. Executive decision views require a
    separate display projection with semantic compression and visual review.
    """

    requested_statuses = {
        _choice(status, choices=NODE_STATUSES, default="", field="status")
        for status in statuses or []
    }
    requested_tags = {str(tag or "").strip() for tag in tags or []}
    requested_tags.discard("")

    node_list = [dict(node) for node in nodes]
    edge_list = [dict(edge) for edge in edges]
    node_by_id = {
        str(node.get("node_id") or ""): node
        for node in node_list
        if str(node.get("node_id") or "")
    }

    def matches(node: Mapping[str, Any]) -> bool:
        if requested_statuses and str(node.get("status") or "") not in requested_statuses:
            return False
        node_tags = {str(tag) for tag in node.get("tags") or []}
        if requested_tags and requested_tags.isdisjoint(node_tags):
            return False
        return True

    filtering = bool(requested_statuses or requested_tags)
    matched_ids = {
        node_id for node_id, node in node_by_id.items() if not filtering or matches(node)
    }
    selected_ids = set(matched_ids)
    if filtering and include_ancestors:
        parents = _parent_map(node_list, edge_list)
        for node_id in tuple(matched_ids):
            seen = {node_id}
            parent = parents.get(node_id)
            while parent and parent not in seen and parent in node_by_id:
                selected_ids.add(parent)
                seen.add(parent)
                parent = parents.get(parent)

    selected_nodes = [
        node for node in node_list if str(node.get("node_id") or "") in selected_ids
    ]
    selected_edges = [
        edge
        for edge in edge_list
        if str(edge.get("from_node") or "") in selected_ids
        and str(edge.get("to_node") or "") in selected_ids
    ]
    return {
        "nodes": selected_nodes,
        "edges": selected_edges,
        "mermaid": build_explore_mermaid(
            selected_nodes,
            selected_edges,
            node_limit=max(1, int(node_limit)),
        ),
        "graph_counts": {
            "node_count": len(selected_nodes),
            "edge_count": len(selected_edges),
            "matched_node_count": len(matched_ids),
            "context_node_count": len(selected_ids - matched_ids),
        },
        "filter": {
            "active": filtering,
            "statuses": sorted(requested_statuses),
            "tags": sorted(requested_tags),
            "include_ancestors": bool(include_ancestors),
            "semantics": "status_and_any_tag",
        },
    }


def build_explore_result_projection(
    events: Sequence[Mapping[str, Any]],
    *,
    goal_id: str,
    finding_limit: int = DEFAULT_FINDING_LIMIT,
    mermaid_node_limit: int = DEFAULT_MERMAID_NODE_LIMIT,
) -> dict[str, Any]:
    """Fold result events into the bounded projection display sinks render."""

    safe_goal_id = _safe_goal_id(goal_id)
    scoped = [event for event in events if str(event.get("goal_id") or "") == safe_goal_id]

    folded_findings = _fold_by_result_id(scoped, event_kind=EVENT_KIND_FINDING)
    finding_counts: dict[str, int] = {}
    for finding in folded_findings:
        node_id = str(finding.get("node_id") or "")
        if node_id:
            finding_counts[node_id] = finding_counts.get(node_id, 0) + 1

    nodes = [
        _node_view(event, finding_count=finding_counts.get(str(event.get("result_id")), 0))
        for event in sorted(
            _fold_by_result_id(scoped, event_kind=EVENT_KIND_NODE),
            key=lambda item: str(item.get("first_recorded_at") or ""),
        )
    ]
    edges = [
        _edge_view(event) for event in _fold_by_result_id(scoped, event_kind=EVENT_KIND_EDGE)
    ]
    edges = [*edges, *_materialized_parent_edges(nodes, edges)]
    findings = sorted(
        (_finding_view(event) for event in folded_findings),
        key=lambda item: item["last_updated_at"],
        reverse=True,
    )

    nodes_by_status: dict[str, int] = {status: 0 for status in sorted(NODE_STATUSES)}
    for node in nodes:
        nodes_by_status[node["status"]] = nodes_by_status.get(node["status"], 0) + 1
    findings_by_status: dict[str, int] = {status: 0 for status in sorted(FINDING_STATUSES)}
    for finding in findings:
        findings_by_status[finding["status"]] = findings_by_status.get(finding["status"], 0) + 1

    parents = _parent_map(nodes, edges)
    return {
        "ok": True,
        "schema_version": EXPLORE_RESULT_PROJECTION_VERSION,
        "goal_id": safe_goal_id,
        "generated_at": _now_iso(),
        "source_event_count": len(scoped),
        "counts": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "finding_count": len(findings),
            "nodes_by_status": nodes_by_status,
            "findings_by_status": findings_by_status,
        },
        "nodes": nodes,
        "edges": edges,
        "findings": findings[: max(0, finding_limit)] if finding_limit else [],
        "stuck": [node for node in nodes if node["status"] == NODE_STATUS_BLOCKED],
        "frontier": [node for node in nodes if node["status"] == NODE_STATUS_EXPLORING],
        "tree": _build_tree(nodes, parents),
        "mermaid": build_explore_mermaid(nodes, edges, node_limit=max(1, mermaid_node_limit)),
        "boundary": dict(PUBLIC_BOUNDARY),
    }
