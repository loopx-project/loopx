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
from typing import Callable, Iterable, Mapping, Sequence, TypedDict

from ...file_lock import exclusive_file_lock
from ...control_plane.runtime.time import chronology_key


Payload = dict[str, object]
PayloadView = Mapping[str, object]


class _ExploreResultProjectionRequired(TypedDict):
    ok: bool
    schema_version: str
    goal_id: str
    generated_at: str
    source_event_count: int
    counts: Payload
    nodes: list[Payload]
    edges: list[Payload]
    findings: list[Payload]
    stuck: list[Payload]
    frontier: list[Payload]
    tree: list[Payload]
    mermaid: str
    boundary: dict[str, bool]


class ExploreResultProjection(_ExploreResultProjectionRequired, total=False):
    research_frontier: Payload


EXPLORE_RESULT_EVENT_SCHEMA_VERSION = "loopx_explore_result_event_v0"
EXPLORE_RESULT_PROJECTION_VERSION = "loopx_explore_result_projection_v0"
DEFAULT_EXPLORE_RESULT_LOG_NAME = "explore-result-log.jsonl"

EVENT_KIND_NODE, EVENT_KIND_EDGE, EVENT_KIND_FINDING = "node", "edge", "finding"
EXPLORE_EVENT_KINDS = {EVENT_KIND_NODE, EVENT_KIND_EDGE, EVENT_KIND_FINDING}

NODE_KIND_QUESTION, NODE_KIND_AREA, NODE_KIND_HYPOTHESIS, NODE_KIND_EXPERIMENT, NODE_KIND_ARTIFACT = (
    "question", "area", "hypothesis", "experiment", "artifact",
)
NODE_KINDS = {NODE_KIND_QUESTION, NODE_KIND_AREA, NODE_KIND_HYPOTHESIS, NODE_KIND_EXPERIMENT, NODE_KIND_ARTIFACT}

NODE_STATUS_OPEN, NODE_STATUS_EXPLORING, NODE_STATUS_BLOCKED, NODE_STATUS_RESOLVED, NODE_STATUS_DEAD_END = (
    "open", "exploring", "blocked", "resolved", "dead_end",
)
NODE_STATUSES = {NODE_STATUS_OPEN, NODE_STATUS_EXPLORING, NODE_STATUS_BLOCKED, NODE_STATUS_RESOLVED, NODE_STATUS_DEAD_END}

EDGE_TYPE_SUBTOPIC_OF = "subtopic_of"
EDGE_TYPES = {EDGE_TYPE_SUBTOPIC_OF, "depends_on", "answers", "supports", "refutes", "leads_to"}

FINDING_STATUS_TENTATIVE, FINDING_STATUS_CONFIRMED, FINDING_STATUS_REFUTED = "tentative", "confirmed", "refuted"
FINDING_STATUSES = {FINDING_STATUS_TENTATIVE, FINDING_STATUS_CONFIRMED, FINDING_STATUS_REFUTED}

TITLE_LIMIT = 200
# A writeback finding includes its revision, applicability, observation and
# complete route decision. Bound display separately without clipping that scope.
SUMMARY_LIMIT = 2000
# Persisted source compatibility includes the historical compactor's
# two-character ellipsis overflow; it is independent of the writer budget.
PERSISTED_TEXT_LIMIT = 2002
REF_LIMIT = 240
MAX_EVIDENCE_REFS = 16
MAX_TAGS = 8
DEFAULT_FINDING_LIMIT = 200
DEFAULT_MERMAID_NODE_LIMIT = 60
DEFAULT_TREE_DEPTH_LIMIT = 6

RESULT_ID_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,95}$")
_WINDOWS_ABS_PATH = re.compile(r"(?<![A-Za-z0-9_.-])[A-Za-z]:[\\/](?![\\/])")

FORBIDDEN_TEXT_MARKERS = (
    "/" + "Users/", "/" + "root/", "/" + "home/", "/" + "private/",
    "\\" + "Users\\", "\\" + "root\\", "\\" + "home\\", "\\" + "private\\",
    ".local/" + "private", ".local\\" + "private",
    "Auth" + "orization:", "api" + "_key", "api" + "key", "pass" + "word", "sec" + "ret=", "tok" + "en=",
)

PUBLIC_BOUNDARY = {
    "raw_task_text_recorded": False, "raw_logs_recorded": False, "raw_trajectory_recorded": False,
    "raw_session_transcript_recorded": False, "credential_values_recorded": False, "absolute_paths_recorded": False,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compact_text(value: object, *, limit: int, field: str) -> str:
    text = " ".join(str(value or "").split())
    lowered = text.lower()
    if (
        "file://" in lowered
        or _WINDOWS_ABS_PATH.search(text)
        or any(marker.lower() in lowered for marker in FORBIDDEN_TEXT_MARKERS)
    ):
        raise ValueError(f"{field} contains private or credential-like material")
    return text if len(text) <= limit else text[: max(0, limit - 1)].rstrip() + "..."


def _persisted_text(value: object, *, field: str) -> str:
    """Validate stored source text without applying a current writer's budget."""
    text = _compact_text(value, limit=PERSISTED_TEXT_LIMIT, field=field)
    if len(text) > PERSISTED_TEXT_LIMIT or text != str(value or ""):
        raise ValueError(f"{field} must be canonical text within {PERSISTED_TEXT_LIMIT} characters")
    return text


def validate_explore_public_ref(value: object, *, field: str) -> str:
    """Canonicalize a reference using the Explore reader/writer privacy boundary."""
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


def _safe_public_refs(values: object, *, field: str, max_items: int) -> list[str]:
    if not values:
        return []
    if not isinstance(values, Iterable):
        raise TypeError(f"{field} must be iterable")
    return [
        validate_explore_public_ref(value, field=f"{field}[{index}]")
        for index, value in enumerate(values)
    ][:max_items]


def _safe_result_id(value: object, *, field: str) -> str:
    text = str(value or "").strip()
    if not RESULT_ID_PATTERN.match(text):
        raise ValueError(f"{field} must match {RESULT_ID_PATTERN.pattern}")
    return text


def _safe_goal_id(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError("goal_id is required")
    if text != Path(text).name or text in {".", ".."}:
        raise ValueError("goal_id must be a single path segment")
    return text


def _safe_confidence(value: object) -> float | None:
    if value is None:
        return None
    if not isinstance(value, (int, float, str, bytes, bytearray)):
        raise ValueError("confidence must be a number")
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError("confidence must be a number") from exc
    if not 0.0 <= number <= 1.0:
        raise ValueError("confidence must be between 0 and 1")
    return round(number, 3)


def _as_int(value: object, default: int = 1) -> int:
    if not value:
        return default
    if isinstance(value, (bool, int)):
        return int(value)
    if isinstance(value, (float, str)):
        return int(value)
    return default


def _as_list(value: object) -> list[object]:
    if not value:
        return []
    return list(value) if isinstance(value, Iterable) and not isinstance(value, (str, bytes)) else []


def _text(row: PayloadView, key: str, default: str = "") -> str:
    return str(row.get(key) or default)


def _event_id(payload: PayloadView) -> str:
    stable = {key: value for key, value in payload.items() if key != "event_id"}
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _derived_result_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def explore_result_log_path(runtime_root: Path, goal_id: str) -> Path:
    return runtime_root.expanduser() / "goals" / _safe_goal_id(goal_id) / DEFAULT_EXPLORE_RESULT_LOG_NAME


def _choice(value: object, *, choices: set[str], default: str, field: str) -> str:
    text = str(value or default).strip().lower()
    if text not in choices:
        raise ValueError(f"unsupported {field} {value!r}; choose {', '.join(sorted(choices))}")
    return text


def _require_nonempty_title(title: object) -> str:
    safe_title = _compact_text(title, limit=TITLE_LIMIT, field="title")
    if not safe_title:
        raise ValueError("title is required")
    return safe_title


def _explicit_or_derived_id(explicit_id: object, *, field: str, prefix: str, goal_id: str, title: str) -> str:
    return (
        _safe_result_id(explicit_id, field=field)
        if explicit_id else _derived_result_id(prefix, goal_id, title.lower())
    )


def build_explore_node_event(
    *, goal_id: str, title: object, node_id: object = None, node_kind: object = None,
    status: object = None, summary: object = None, blocked_reason: object = None,
    parent_id: object = None, agent_id: object = None, run_id: object = None,
    evidence_refs: object = None, tags: object = None, supersedes: object = None,
    recorded_at: object = None, research_observation: object = None,
) -> Payload:
    safe_goal_id = _safe_goal_id(goal_id)
    safe_title = _require_nonempty_title(title)
    resolved_status = _choice(status, choices=NODE_STATUSES, default=NODE_STATUS_OPEN, field="status")
    safe_blocked_reason = _compact_text(blocked_reason, limit=SUMMARY_LIMIT, field="blocked_reason")
    if resolved_status == NODE_STATUS_BLOCKED and not safe_blocked_reason:
        raise ValueError("blocked nodes must state a blocked_reason")
    event = _base_event(
        goal_id=safe_goal_id, event_kind=EVENT_KIND_NODE, title=safe_title,
        summary=summary, agent_id=agent_id, run_id=run_id,
        evidence_refs=evidence_refs, tags=tags, supersedes=supersedes, recorded_at=recorded_at,
    )
    event["result_id"] = _explicit_or_derived_id(
        node_id, field="node_id", prefix="node", goal_id=safe_goal_id, title=safe_title,
    )
    event["node_kind"] = _choice(node_kind, choices=NODE_KINDS, default=NODE_KIND_AREA, field="node_kind")
    event["status"] = resolved_status
    if safe_blocked_reason:
        event["blocked_reason"] = safe_blocked_reason
    if parent_id:
        event["parent_id"] = _safe_result_id(parent_id, field="parent_id")
    if research_observation is not None:
        if not isinstance(research_observation, Mapping):
            raise ValueError("research observation must be an object")
        from .research_evidence import normalize_research_observation

        observation = normalize_research_observation({str(k): v for k, v in research_observation.items()})
        if observation["explore_node_id"] != event["result_id"]:
            raise ValueError("research observation belongs to a different node")
        event["research_observation"] = observation
    event["event_id"] = _event_id(event)
    return event


def build_explore_edge_event(
    *, goal_id: str, from_node: object, to_node: object, edge_type: object,
    summary: object = None, confidence: object = None, agent_id: object = None,
    run_id: object = None, recorded_at: object = None,
) -> Payload:
    safe_goal_id = _safe_goal_id(goal_id)
    safe_from = _safe_result_id(from_node, field="from_node")
    safe_to = _safe_result_id(to_node, field="to_node")
    if safe_from == safe_to:
        raise ValueError("edge must connect two different nodes")
    resolved_type = _choice(edge_type, choices=EDGE_TYPES, default="", field="edge_type")
    event = _base_event(
        goal_id=safe_goal_id, event_kind=EVENT_KIND_EDGE,
        title=f"{safe_from} -{resolved_type}-> {safe_to}",
        summary=summary, agent_id=agent_id, run_id=run_id,
        evidence_refs=None, tags=None, supersedes=None, recorded_at=recorded_at,
    )
    event["result_id"] = _derived_result_id("edge", safe_goal_id, safe_from, resolved_type, safe_to)
    event.update({"from_node": safe_from, "to_node": safe_to, "edge_type": resolved_type})
    if (safe_confidence := _safe_confidence(confidence)) is not None:
        event["confidence"] = safe_confidence
    event["event_id"] = _event_id(event)
    return event


def build_explore_finding_event(
    *, goal_id: str, title: object, finding_id: object = None, node_id: object = None,
    status: object = None, summary: object = None, confidence: object = None,
    agent_id: object = None, run_id: object = None, evidence_refs: object = None,
    tags: object = None, supersedes: object = None, recorded_at: object = None,
    execution_observation: object = None, measurements: object = None,
    work_yardstick: object = None, work_binding: object = None,
    _check_execution_measurements: bool = True,
) -> Payload:
    safe_goal_id = _safe_goal_id(goal_id)
    safe_title = _require_nonempty_title(title)
    event = _base_event(
        goal_id=safe_goal_id, event_kind=EVENT_KIND_FINDING, title=safe_title,
        summary=summary, agent_id=agent_id, run_id=run_id,
        evidence_refs=evidence_refs, tags=tags, supersedes=supersedes, recorded_at=recorded_at,
    )
    event["result_id"] = _explicit_or_derived_id(
        finding_id, field="finding_id", prefix="finding", goal_id=safe_goal_id, title=safe_title,
    )
    if node_id:
        event["node_id"] = _safe_result_id(node_id, field="node_id")
    event["status"] = _choice(
        status, choices=FINDING_STATUSES, default=FINDING_STATUS_TENTATIVE, field="status"
    )
    if (safe_confidence := _safe_confidence(confidence)) is not None:
        event["confidence"] = safe_confidence
    event_refs = _as_list(event.get("evidence_refs"))
    if execution_observation is not None:
        from .execution_observation import normalize_execution_observation

        observed = normalize_execution_observation(execution_observation)
        if any(ref not in event_refs for ref in observed["evidence_refs"]):
            raise ValueError("execution evidence must be included in the finding's evidence_refs")
        event["execution_observation"] = observed
    if measurements is not None:
        from .measurements import normalize_measurements

        if not node_id:
            raise ValueError("measurements require an explicit node")
        measured = (
            normalize_measurements(measurements, execution_observation=event.get("execution_observation"))
            if _check_execution_measurements else normalize_measurements(measurements)
        )
        if any(item["evidence_ref"] not in event_refs for item in measured):
            raise ValueError("measurement evidence must be included in the finding's evidence_refs")
        event["measurements"] = measured
    yardstick: dict[str, object] | None = None
    if work_yardstick is not None:
        from ...control_plane.work_items.yardstick_decision import normalize_work_yardstick_capture

        yardstick = normalize_work_yardstick_capture(
            work_yardstick, goal_id=safe_goal_id, agent_id=str(agent_id or ""),
            evidence_refs=event_refs,
        )
        event["work_yardstick"] = yardstick
    if work_binding is not None:
        if (not isinstance(work_binding, Mapping)
                or set(work_binding) != {"goal_id", "agent_id", "todo_id", "turn_instance_id"}
                or work_binding["goal_id"] != safe_goal_id
                or work_binding["agent_id"] != event.get("agent_id")
                or not work_binding["agent_id"] or not node_id):
            raise ValueError("Explore work binding must match the finding's Goal, Agent and question")
        binding = {
            str(key): validate_explore_public_ref(value, field=f"work_binding.{key}")
            for key, value in work_binding.items()
        }
        if (binding != work_binding
                or not _safe_result_id(binding["todo_id"], field="work_binding.todo_id").startswith("todo_")
                or (yardstick is not None and yardstick.get("binding") != binding)):
            raise ValueError("Explore finding requires a canonical work binding")
        event["work_binding"] = binding
    event["event_id"] = _event_id(event)
    return event


def _base_event(
    *, goal_id: str, event_kind: str, title: object, summary: object, agent_id: object,
    run_id: object, evidence_refs: object, tags: object, supersedes: object, recorded_at: object,
) -> Payload:
    event: Payload = {
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
        event["run_id"] = validate_explore_public_ref(run_id, field="run_id")
    refs = _safe_public_refs(evidence_refs, field="evidence_refs", max_items=MAX_EVIDENCE_REFS)
    if refs:
        event["evidence_refs"] = refs
    if tags:
        if not isinstance(tags, Iterable):
            raise TypeError("tags must be iterable")
        safe_tags = [
            tag for index, raw_tag in enumerate(tags)
            if (tag := _compact_text(raw_tag, limit=48, field=f"tags[{index}]"))
        ][:MAX_TAGS]
        if safe_tags:
            event["tags"] = safe_tags
    if supersedes:
        event["supersedes"] = _safe_result_id(supersedes, field="supersedes")
    return event


def validate_explore_result_event(
    event: PayloadView,
    *,
    expected_goal_id: str | None = None,
) -> Payload:
    """Return one canonical public-safe Explore event or fail closed.

    Event builders are the schema authority. Rebuilding the event catches
    unknown fields, invalid ids, unsafe text, forged boundary flags, and stale
    event ids. Persisted text has its own bounded compatibility budget: it is
    validated and restored before hashing, never clipped to the writer default.
    This structural read validation preserves older attribution contradictions;
    writers reject new contradictions, and current views exclude their numbers.
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

    summary = _persisted_text(payload.get("summary"), field="summary")
    agent_id, run_id, recorded_at = payload.get("agent_id"), payload.get("run_id"), payload.get("recorded_at")
    if event_kind == EVENT_KIND_NODE:
        blocked_reason = _persisted_text(payload.get("blocked_reason"), field="blocked_reason")
        rebuilt = build_explore_node_event(
            goal_id=goal_id, summary=None, agent_id=agent_id, run_id=run_id, recorded_at=recorded_at,
            title=payload.get("title"), node_id=payload.get("result_id"), node_kind=payload.get("node_kind"),
            status=payload.get("status"), blocked_reason=blocked_reason[:SUMMARY_LIMIT],
            parent_id=payload.get("parent_id"), evidence_refs=payload.get("evidence_refs"),
            tags=payload.get("tags"), supersedes=payload.get("supersedes"),
            research_observation=payload.get("research_observation"),
        )
        if blocked_reason:
            rebuilt["blocked_reason"] = blocked_reason
    elif event_kind == EVENT_KIND_EDGE:
        rebuilt = build_explore_edge_event(
            goal_id=goal_id, summary=None, agent_id=agent_id, run_id=run_id, recorded_at=recorded_at,
            from_node=payload.get("from_node"), to_node=payload.get("to_node"),
            edge_type=payload.get("edge_type"), confidence=payload.get("confidence"),
        )
    else:
        rebuilt = build_explore_finding_event(
            goal_id=goal_id, summary=None, agent_id=agent_id, run_id=run_id, recorded_at=recorded_at,
            title=payload.get("title"), finding_id=payload.get("result_id"), node_id=payload.get("node_id"),
            status=payload.get("status"), confidence=payload.get("confidence"),
            evidence_refs=payload.get("evidence_refs"), tags=payload.get("tags"),
            supersedes=payload.get("supersedes"), execution_observation=payload.get("execution_observation"),
            measurements=payload.get("measurements"), work_yardstick=payload.get("work_yardstick"),
            work_binding=payload.get("work_binding"),
            # Canonical history predates the stricter new-write rule. Keep
            # original bytes/identities auditable; current views qualify the
            # folded result separately, and append rechecks new writes below.
            _check_execution_measurements=False,
        )
    if summary:
        rebuilt["summary"] = summary
    rebuilt["event_id"] = _event_id(rebuilt)
    if payload != rebuilt:
        raise ValueError("explore result event is not canonical or contains unknown fields")
    return rebuilt


def validate_explore_result_event_attribution(event: PayloadView) -> None:
    """Check typed measurement attribution after canonical event validation."""
    if "measurements" in event:
        from .measurements import normalize_measurements

        normalize_measurements(event["measurements"], execution_observation=event.get("execution_observation"))


def append_explore_result_event(path: Path, event: PayloadView) -> Payload:
    validated = validate_explore_result_event(event)
    validate_explore_result_event_attribution(validated)
    log_path = path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(log_path):
        if validated.get("research_observation"):
            from .research_evidence import validate_research_append
            validate_research_append(load_explore_result_events_strict(log_path, goal_id=str(validated["goal_id"])), validated)
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


def rebase_explore_knowledge_events(
    events: Sequence[PayloadView], *, source_goal_id: str,
    destination_goal_id: str, source_ref: str,
) -> list[Payload]:
    """Transport verified knowledge to a distinct Goal without execution grants.

    The caller owns source verification and current destination authorization.
    Keep stable knowledge identities, full scope, attribution and timestamps;
    bind each new event to its source event in the existing opaque run reference.
    Operational research observations and work bindings stay in the source
    archive. Rebasing them would manufacture destination execution evidence.
    """
    source, destination = _safe_goal_id(source_goal_id), _safe_goal_id(destination_goal_id)
    if source == destination:
        raise ValueError("knowledge replay requires distinct source and destination Goals")
    result: dict[str, Payload] = {}
    for raw in events:
        event = validate_explore_result_event(raw, expected_goal_id=source)
        event = {**event, "goal_id": destination, "run_id": f"{source_ref}:{event['event_id']}"}
        for key in ("research_observation", "work_binding", "work_yardstick"):
            event.pop(key, None)
        if event["event_kind"] == EVENT_KIND_EDGE:
            event["result_id"] = _derived_result_id(
                "edge", destination, str(event["from_node"]), str(event["edge_type"]), str(event["to_node"]),
            )
        event["event_id"] = _event_id(event)
        event = validate_explore_result_event(event, expected_goal_id=destination)
        result[str(event["event_id"])] = event
    return list(result.values())


def append_explore_result_events(
    path: Path,
    events: Sequence[PayloadView],
    *,
    expected_goal_id: str,
    create_only_node_ids: Sequence[str] = (),
    require_ordered_prefix: bool = False,
    expected_event_ids: Sequence[str] | None = None,
    before_commit: Callable[[], object] | None = None,
) -> Payload:
    """Append a validated batch once by event id under the result-log lock.

    A historical replay can require this exact batch as the initial prefix.
    Recovery may finish that prefix or reuse it before newer current evidence;
    it cannot weave old revisions into a different or incomplete current graph.
    Ordinary callers retain their existing append semantics.
    """

    if type(require_ordered_prefix) is not bool:
        raise ValueError("require_ordered_prefix must be an explicit boolean")
    if require_ordered_prefix and create_only_node_ids:
        raise ValueError("ordered prefix replay cannot skip existing node revisions")
    validated = [validate_explore_result_event(event, expected_goal_id=expected_goal_id) for event in events]
    # Ordered replay restores an audited historical prefix, not new evidence.
    # Its old contradictions remain visible only as archival facts/diagnostics.
    if not require_ordered_prefix:
        for event in validated:
            validate_explore_result_event_attribution(event)
    return _append_validated_explore_result_events(
        path, validated, expected_goal_id=expected_goal_id,
        create_only_node_ids=create_only_node_ids,
        require_ordered_prefix=require_ordered_prefix,
        expected_event_ids=expected_event_ids,
        before_commit=before_commit,
    )


def _append_validated_explore_result_events(
    path: Path,
    validated: Sequence[Payload],
    *,
    expected_goal_id: str,
    create_only_node_ids: Sequence[str] = (),
    require_ordered_prefix: bool = False,
    expected_event_ids: Sequence[str] | None = None,
    before_commit: Callable[[], object] | None = None,
) -> Payload:
    """Shared file effect after the owning caller qualifies the events.

    Ordinary writes validate new attribution above. Source reconciliation
    preflights the same attribution after checking its registered baseline and
    missing result ids, then binds execution to the exact canonical snapshot.
    This internal seam adds no public validation switch or historical-prefix
    claim.
    """
    requested_ids = [str(event["event_id"]) for event in validated]
    if len(requested_ids) != len(set(requested_ids)):
        raise ValueError("Explore result event batch contains duplicate event ids")

    log_path = path.expanduser()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    appended = 0
    reused = 0
    with exclusive_file_lock(log_path):
        existing_by_id: dict[str, Payload] = {}
        if require_ordered_prefix:
            raw = log_path.read_bytes() if log_path.exists() else b""
            if raw and not raw.endswith(b"\n"):
                raise ValueError("Explore result prefix has an incomplete tail")
            current_events = load_explore_result_events_strict(log_path, goal_id=expected_goal_id)
            if current_events[:len(validated)] != list(validated[:len(current_events)]):
                raise ValueError("Explore result history differs from the required initial prefix")
            existing_by_id = {str(row["event_id"]): row for row in current_events}
        elif expected_event_ids is not None:
            existing_by_id = {
                str(row["event_id"]): row for row in
                load_explore_result_events_strict(log_path, goal_id=expected_goal_id)
            }
        else:
            existing_by_id = {
                str(row["event_id"]): row
                for _, row in _iter_jsonl_rows(log_path, strict=False)
                if row.get("event_id")
            }
        current_nodes = {
            str(row["result_id"]): row for row in existing_by_id.values()
            if row.get("event_kind") == EVENT_KIND_NODE
        }
        # Optional stateful result evolution must not overwrite a concurrent
        # owner update or admit a relation against a stale graph. Exact replay
        # remains read-only, including after later valid graph changes.
        if (expected_event_ids is not None
                and list(existing_by_id) != list(expected_event_ids)
                and not all(existing_by_id.get(str(row["event_id"])) == row for row in validated)):
            raise ValueError("Explore graph changed before question evolution; retry the committed writeback")
        pending: list[Payload] = []
        for event in validated:
            event_id = str(event["event_id"])
            if event.get("event_kind") == EVENT_KIND_NODE and str(event.get("result_id") or "") in create_only_node_ids:
                node = current_nodes.get(str(event["result_id"]))
                if node is not None:
                    if any(node.get(key) != event.get(key) for key in ("node_kind", "title", "summary")):
                        raise ValueError("Explore question identity conflicts with its existing scope")
                    reused += 1
                    continue
            existing = existing_by_id.get(event_id)
            if existing is None:
                pending.append(event)
                continue
            if existing != event:
                raise ValueError(f"conflicting Explore result event id: {event_id}")
            reused += 1
        if pending:
            if any(event.get("research_observation") for event in pending):
                from .research_evidence import validate_research_append
                current_events = load_explore_result_events_strict(log_path, goal_id=expected_goal_id)
                proposal = build_explore_result_projection([*current_events, *pending], goal_id=expected_goal_id)
                for event in pending:
                    validate_research_append(current_events, event, proposal=proposal)
                    current_events.append(event)
            payload = "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in pending)
            # The owning caller may require final authority admission after
            # the lock wait and CAS. The log never decides that authority.
            if before_commit is not None:
                before_commit()
            with log_path.open("a", encoding="utf-8") as handle:
                handle.write(payload)
            appended = len(pending)
    return {
        "ok": True,
        "schema_version": EXPLORE_RESULT_EVENT_SCHEMA_VERSION,
        "requested_event_count": len(validated),
        "appended_event_count": appended,
        "reused_event_count": reused,
    }


def _iter_jsonl_rows(log_path: Path, *, strict: bool) -> list[tuple[int, Payload]]:
    if not log_path.exists():
        return []
    rows: list[tuple[int, Payload]] = []
    for line_number, line in enumerate(log_path.read_text(encoding="utf-8").split("\n"), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            if strict:
                raise ValueError(f"invalid Explore result JSON at line {line_number}") from exc
            continue
        if not isinstance(payload, dict):
            if strict:
                raise ValueError(f"Explore result row {line_number} is not an object")
            continue
        rows.append((line_number, {str(key): value for key, value in payload.items()}))
    return rows


def load_explore_result_events(
    path: Path, *, goal_id: str | None = None, limit: int | None = None,
) -> list[Payload]:
    events = [
        payload for _, payload in _iter_jsonl_rows(path.expanduser(), strict=False)
        if payload.get("schema_version") == EXPLORE_RESULT_EVENT_SCHEMA_VERSION
        and payload.get("event_kind") in EXPLORE_EVENT_KINDS
        and (not goal_id or _text(payload, "goal_id") == goal_id)
    ]
    return (events[-limit:] if limit else []) if (limit is not None and limit >= 0) else events


def load_explore_result_events_strict(path: Path, *, goal_id: str) -> list[Payload]:
    """Load a complete result log and reject malformed or unsafe rows."""
    events: list[Payload] = []
    for line_number, payload in _iter_jsonl_rows(path.expanduser(), strict=True):
        try:
            events.append(validate_explore_result_event(payload, expected_goal_id=goal_id))
        except ValueError as exc:
            raise ValueError(f"invalid Explore result event at line {line_number}: {exc}") from exc
    return events


def explore_history_rows(events: Sequence[PayloadView], *, goal_id: str | None = None) -> list[Payload]:
    """Shared read projection: preserve history without exposing arbitrary payloads."""
    if goal_id is not None:
        events = [validate_explore_result_event(event, expected_goal_id=goal_id) for event in events]
    fields = (
        "schema_version", "event_id", "goal_id", "event_kind", "result_id",
        "recorded_at", "title", "summary", "status", "node_kind",
        "blocked_reason", "parent_id", "node_id", "from_node", "to_node",
        "edge_type", "agent_id", "run_id", "evidence_refs", "supersedes", "confidence",
        "execution_observation", "measurements", "work_yardstick", "work_binding",
    )
    from ...control_plane.work_items.yardstick_decision import project_work_yardstick_presentations

    rows = [{key: event[key] for key in fields if key in event} for event in events]
    return [{str(k): v for k, v in row.items()} for row in project_work_yardstick_presentations(rows, goal_id=goal_id)]


def _fold_by_result_id(events: Sequence[PayloadView], *, event_kind: str) -> list[Payload]:
    folded: dict[str, Payload] = {}
    for recorded_order, event in enumerate(events):
        if event.get("event_kind") != event_kind:
            continue
        result_id = _text(event, "result_id")
        if not result_id:
            continue
        prior = folded.get(result_id)
        recorded_at = _text(event, "recorded_at")
        view = dict(event)
        view["_recorded_order"] = recorded_order
        view["first_recorded_at"] = prior["first_recorded_at"] if prior else recorded_at
        view["last_updated_at"] = recorded_at
        view["update_count"] = (_as_int(prior.get("update_count"), 0) + 1) if prior else 1
        folded[result_id] = view
    return list(folded.values())


def _common_view_fields(event: PayloadView) -> Payload:
    return {
        "summary": _text(event, "summary"),
        "agent_id": _text(event, "agent_id"),
        "evidence_refs": _as_list(event.get("evidence_refs")),
        "tags": _as_list(event.get("tags")),
        "supersedes": _text(event, "supersedes"),
        "first_recorded_at": _text(event, "first_recorded_at"),
        "last_updated_at": _text(event, "last_updated_at"),
        "update_count": _as_int(event.get("update_count"), 1),
    }


def _node_view(event: PayloadView, *, finding_count: int) -> Payload:
    observation = event.get("research_observation")
    view: Payload = {
        **({"research_observation": {str(k): v for k, v in observation.items()}}
           if isinstance(observation, Mapping) else {}),
        "node_id": _text(event, "result_id"),
        "title": _text(event, "title"),
        "node_kind": _text(event, "node_kind", NODE_KIND_AREA),
        "status": _text(event, "status", NODE_STATUS_OPEN),
        "blocked_reason": _text(event, "blocked_reason"),
        "parent_id": _text(event, "parent_id"),
        "finding_count": finding_count,
        **_common_view_fields(event),
    }
    return view


def _edge_view(event: PayloadView) -> Payload:
    return {
        "edge_id": _text(event, "result_id"),
        "from_node": _text(event, "from_node"),
        "to_node": _text(event, "to_node"),
        "edge_type": _text(event, "edge_type"),
        "summary": _text(event, "summary"),
        "confidence": event.get("confidence"),
        "last_updated_at": _text(event, "last_updated_at"),
    }


def _materialized_parent_edges(nodes: Sequence[PayloadView], edges: Sequence[PayloadView]) -> list[Payload]:
    """Represent node parent links as display edges for graph-oriented sinks.

    ``parent_id`` is the compact tree source of truth, but visual consumers such
    as Base relationship views and Sankey-like components need rows in the edge
    table. These derived edges are parent -> child and use ``supports`` so they
    do not alter the ``subtopic_of`` tree parser.
    """

    known = {_text(node, "node_id") for node in nodes}
    existing_pairs = {frozenset((_text(edge, "from_node"), _text(edge, "to_node"))) for edge in edges}
    derived: list[Payload] = []
    for node in nodes:
        child = _text(node, "node_id")
        parent = _text(node, "parent_id")
        if not child or not parent or child == parent or parent not in known:
            continue
        if frozenset((parent, child)) in existing_pairs:
            continue
        digest = hashlib.sha1(f"{parent}->{child}".encode("utf-8")).hexdigest()[:12]
        derived.append({
            "edge_id": f"parent_{digest}",
            "from_node": parent,
            "to_node": child,
            "edge_type": "supports",
            "summary": "Parent topic contains this exploration node.",
            "confidence": 1.0,
            "last_updated_at": _text(node, "last_updated_at"),
            "materialized_from": "node_parent_id",
        })
    return derived


def _finding_view(event: PayloadView, *, include_internal_order: bool = False) -> Payload:
    measured: Payload = {}
    if "measurements" in event:
        try:
            validate_explore_result_event_attribution(event)
        except ValueError as error:
            # Quarantine contradictory historical quantities in the current
            # view, not the append-only history. Runtime failure propagates.
            measured["measurement_error"] = str(error)
        else:
            measured["measurements"] = event["measurements"]
    return {
        **{key: event[key] for key in ("work_binding", "work_yardstick", "execution_observation") if key in event},
        **({"recorded_order": event["_recorded_order"]} if include_internal_order
            or "measurements" in event or "work_yardstick" in event else {}),
        **measured,
        "finding_id": _text(event, "result_id"),
        "finding": _text(event, "title"),
        "status": _text(event, "status", FINDING_STATUS_TENTATIVE),
        "confidence": event.get("confidence"),
        "node_id": _text(event, "node_id"),
        **_common_view_fields(event),
    }


def _parent_map(nodes: Sequence[PayloadView], edges: Sequence[PayloadView]) -> dict[str, str]:
    known = {str(node.get("node_id")) for node in nodes}
    parents: dict[str, str] = {}
    for edge in edges:
        if str(edge.get("edge_type")) != EDGE_TYPE_SUBTOPIC_OF:
            continue
        child, parent = _text(edge, "from_node"), _text(edge, "to_node")
        if child in known and parent in known:
            parents[child] = parent
    for node in nodes:
        parent = _text(node, "parent_id")
        if parent and parent in known:
            parents[str(node.get("node_id"))] = parent
    return parents


def _build_tree(
    nodes: Sequence[PayloadView],
    parents: Mapping[str, str],
    *,
    depth_limit: int = DEFAULT_TREE_DEPTH_LIMIT,
) -> list[Payload]:
    children: dict[str, list[str]] = {}
    node_by_id = {str(node.get("node_id")): node for node in nodes}
    roots: list[str] = []
    for node_id in node_by_id:
        parent = parents.get(node_id)
        if parent and parent != node_id and parent in node_by_id:
            children.setdefault(parent, []).append(node_id)
        else:
            roots.append(node_id)

    def branch(node_id: str, *, depth: int, seen: frozenset[str]) -> Payload:
        node = node_by_id[node_id]
        child_views: list[Payload] = (
            [branch(child, depth=depth + 1, seen=seen | {node_id})
             for child in children.get(node_id, []) if child not in seen]
            if depth < depth_limit else []
        )
        return {
            "node_id": node_id,
            "title": _text(node, "title"),
            "status": _text(node, "status", NODE_STATUS_OPEN),
            "children": child_views,
        }

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


def explore_mermaid_node_id(node_id: str) -> str:
    """Encode identity losslessly, outside Mermaid keywords and layout IDs."""
    return "n_" + str(node_id).encode("utf-8").hex()


def build_explore_mermaid(
    nodes: Sequence[PayloadView],
    edges: Sequence[PayloadView],
    *,
    node_limit: int = DEFAULT_MERMAID_NODE_LIMIT,
) -> str:
    """Render the exploration topology as Mermaid flowchart source."""

    lines = ["flowchart TD"]
    shown = list(nodes)[:node_limit]
    shown_ids = {str(node.get("node_id")) for node in shown}
    for node in shown:
        node_id = explore_mermaid_node_id(str(node.get("node_id")))
        status = _text(node, "status", NODE_STATUS_OPEN)
        marker = {"blocked": " (BLOCKED)", "resolved": " (done)", "dead_end": " (dead end)"}.get(status, "")
        label = _mermaid_label(f"{node.get('title')}{marker}")
        lines.append(f'    {node_id}["{label}"]:::{_MERMAID_STATUS_CLASS.get(status, "open")}')
    for edge in edges:
        from_node, to_node = _text(edge, "from_node"), _text(edge, "to_node")
        if from_node not in shown_ids or to_node not in shown_ids:
            continue
        lines.append(
            f"    {explore_mermaid_node_id(from_node)} -->|{_mermaid_label(_text(edge, 'edge_type'))}| "
            f"{explore_mermaid_node_id(to_node)}"
        )
    if len(nodes) > node_limit:
        lines.append(f"    %% {len(nodes) - node_limit} more nodes omitted")
    lines.extend([
        "    classDef open fill:#f5f5f5,stroke:#9e9e9e",
        "    classDef exploring fill:#e3f2fd,stroke:#1e88e5",
        "    classDef blocked fill:#ffebee,stroke:#e53935,stroke-width:2px",
        "    classDef resolved fill:#e8f5e9,stroke:#43a047",
        "    classDef deadend fill:#eeeeee,stroke:#9e9e9e,stroke-dasharray: 4 4",
    ])
    return "\n".join(lines)


def build_explore_graph_view(
    nodes: Sequence[PayloadView],
    edges: Sequence[PayloadView],
    *,
    statuses: Sequence[str] | None = None,
    tags: Sequence[str] | None = None,
    include_ancestors: bool = True,
    node_limit: int = DEFAULT_MERMAID_NODE_LIMIT,
) -> Payload:
    """Build a focused graph without mutating the full result projection.

    Status and tag filters are combined with AND semantics. Tag matching uses
    exact values and is OR within the requested tag set. Ancestors are included
    by default so a focused evidence graph keeps enough topology to explain
    where each matched node belongs. Executive decision views require a
    separate display projection with semantic compression and visual review.
    """

    requested_statuses = {_choice(status, choices=NODE_STATUSES, default="", field="status") for status in statuses or []}
    requested_tags = {str(tag or "").strip() for tag in tags or []}
    requested_tags.discard("")

    node_list = [dict(node) for node in nodes]
    edge_list = [dict(edge) for edge in edges]
    node_by_id = {nid: node for node in node_list if (nid := _text(node, "node_id"))}

    def matches(node: PayloadView) -> bool:
        if requested_statuses and _text(node, "status") not in requested_statuses:
            return False
        node_tags = {str(tag) for tag in _as_list(node.get("tags"))}
        return not (requested_tags and requested_tags.isdisjoint(node_tags))

    filtering = bool(requested_statuses or requested_tags)
    matched_ids = {node_id for node_id, node in node_by_id.items() if not filtering or matches(node)}
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

    selected_nodes = [node for node in node_list if _text(node, "node_id") in selected_ids]
    selected_edges = [
        edge for edge in edge_list
        if _text(edge, "from_node") in selected_ids and _text(edge, "to_node") in selected_ids
    ]
    return {
        "nodes": selected_nodes, "edges": selected_edges,
        "mermaid": build_explore_mermaid(selected_nodes, selected_edges, node_limit=max(1, int(node_limit))),
        "graph_counts": {
            "node_count": len(selected_nodes), "edge_count": len(selected_edges),
            "matched_node_count": len(matched_ids), "context_node_count": len(selected_ids - matched_ids),
        },
        "filter": {
            "active": filtering, "statuses": sorted(requested_statuses), "tags": sorted(requested_tags),
            "include_ancestors": bool(include_ancestors), "semantics": "status_and_any_tag",
        },
    }


def _count_by_status(rows: Sequence[PayloadView], allowed: set[str]) -> dict[str, int]:
    counts: dict[str, int] = {status: 0 for status in sorted(allowed)}
    for row in rows:
        status = _text(row, "status")
        counts[status] = counts.get(status, 0) + 1
    return counts


def build_explore_result_projection(
    events: Sequence[PayloadView],
    *,
    goal_id: str,
    finding_limit: int = DEFAULT_FINDING_LIMIT,
    mermaid_node_limit: int = DEFAULT_MERMAID_NODE_LIMIT,
    include_internal_finding_order: bool = False,
) -> ExploreResultProjection:
    """Fold result events into the bounded projection display sinks render."""

    safe_goal_id = _safe_goal_id(goal_id)
    scoped = [
        validate_explore_result_event(event, expected_goal_id=safe_goal_id)
        if event.get("research_observation") else event
        for event in events if _text(event, "goal_id") == safe_goal_id
    ]

    folded_findings = _fold_by_result_id(scoped, event_kind=EVENT_KIND_FINDING)
    finding_counts: dict[str, int] = {}
    for finding in folded_findings:
        if node_id := _text(finding, "node_id"):
            finding_counts[node_id] = finding_counts.get(node_id, 0) + 1

    nodes = [
        _node_view(event, finding_count=finding_counts.get(str(event.get("result_id")), 0))
        for event in sorted(
            _fold_by_result_id(scoped, event_kind=EVENT_KIND_NODE),
            key=lambda item: chronology_key(item.get("first_recorded_at")),
        )
    ]
    edges = [_edge_view(event) for event in _fold_by_result_id(scoped, event_kind=EVENT_KIND_EDGE)]
    edges = [*edges, *_materialized_parent_edges(nodes, edges)]
    # Whole-second source clocks can tie. Folding retains creation order, so
    # break equal chronology keys with the existing latest-append ordinal
    # before bounding any consumer; do not rewrite the stored evidence clock.
    findings = [
        _finding_view(event, include_internal_order=include_internal_finding_order)
        for event in sorted(
            folded_findings,
            key=lambda item: (chronology_key(item.get("last_updated_at")), _as_int(item.get("_recorded_order"), 0)),
            reverse=True,
        )
    ]

    parents = _parent_map(nodes, edges)
    projection: ExploreResultProjection = {
        "ok": True,
        "schema_version": EXPLORE_RESULT_PROJECTION_VERSION,
        "goal_id": safe_goal_id,
        "generated_at": _now_iso(),
        "source_event_count": len(scoped),
        "counts": {
            "node_count": len(nodes),
            "edge_count": len(edges),
            "finding_count": len(findings),
            "nodes_by_status": _count_by_status(nodes, NODE_STATUSES),
            "findings_by_status": _count_by_status(findings, FINDING_STATUSES),
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
    research_events = [event for event in scoped if event.get("research_observation")]
    if research_events:
        from .research_evidence import project_research_frontier

        frontier = project_research_frontier(
            projection,
            candidate_sources=[
                {"node_id": event["result_id"], "research_observation": event["research_observation"]}
                for event in research_events
            ],
        )
        node_summaries = frontier.pop("node_summaries", None)
        summaries = {
            str(item["node_id"]): item["summary"]
            for item in (node_summaries if isinstance(node_summaries, Sequence) else ())
            if isinstance(item, Mapping) and "node_id" in item and "summary" in item
        }
        for node in nodes:
            if (node_key := _text(node, "node_id")) in summaries:
                node["research_summary"] = summaries[node_key]
        projection["research_frontier"] = frontier
    return projection
