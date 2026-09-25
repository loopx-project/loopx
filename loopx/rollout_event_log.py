from __future__ import annotations

import hashlib
import json
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

from .file_lock import exclusive_file_lock


ROLLOUT_EVENT_SCHEMA_VERSION = "loopx_rollout_event_v0"
ROLLOUT_EVENT_SUMMARY_SCHEMA_VERSION = "loopx_rollout_event_summary_v0"
DEFAULT_ROLLOUT_EVENT_LOG_NAME = "rollout-event-log.jsonl"

ROLLOUT_EVENT_KINDS = {
    "codex_session_observed",
    "capability_gap",
    "compact_blocker",
    "compact_case_result",
    "evidence_log_read",
    "failure_attribution",
    "native_child_decision",
    "native_child_result",
    "native_child_review",
    "pr_merge",
    "pr_review_ack",
    "quota_monitor_poll",
    "quota_should_run",
    "quota_spend",
    "quota_void",
    "refresh_state",
    "refresh_external_delivery",
    "research_evidence",
    "research_hypothesis",
    "todo_add",
    "todo_archive_completed",
    "todo_claim",
    "todo_complete",
    "todo_supersede",
    "todo_update",
    "validation",
}

PRIVATE_SOURCE_KINDS = {
    "codex_sessions_jsonl",
    "local_runtime_state",
    "private_runner_artifact",
    "unknown_private_source",
}

FORBIDDEN_TEXT_MARKERS = (
    "/" + "Users/",
    "/" + "root/",
    "/" + "home/",
    "/" + "private/",
    ".local/" + "private",
    "Auth" + "orization:",
    "api" + "_key",
    "api" + "key",
    "pass" + "word",
    "sec" + "ret=",
    "tok" + "en=",
    "loopx-" + "ecs",
    "115." + "190.",
)

RAW_KEY_HINTS = (
    "credential",
    "local_path",
    "log",
    "path",
    "raw",
    "secret",
    "stderr",
    "stdout",
    "task_text",
    "token",
    "trace",
    "trajectory",
    "transcript",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compact_text(value: Any, *, limit: int = 500, field: str = "value") -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    if not text:
        return None
    lowered = text.lower()
    for marker in FORBIDDEN_TEXT_MARKERS:
        if marker.lower() in lowered:
            raise ValueError(f"{field} contains private or credential-like material")
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)].rstrip() + "..."


def _safe_key(key: str, *, field: str) -> str:
    text = str(key).strip()
    if not text:
        raise ValueError(f"{field} key is empty")
    lowered = text.lower()
    if any(hint in lowered for hint in RAW_KEY_HINTS):
        raise ValueError(f"{field} key {text!r} looks like raw/private material")
    return text


def _safe_scalar(value: Any, *, field: str) -> bool | int | float | str | None:
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    return _compact_text(value, limit=240, field=field)


def _safe_public_ref(value: Any, *, field: str) -> str | None:
    text = _compact_text(value, limit=240, field=field)
    if text is None:
        return None
    path = Path(text)
    if text.startswith(("~", "/", "\\")) or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field} must be a public relative ref or opaque id")
    return text


def _safe_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (details or {}).items():
        safe_key = _safe_key(str(key), field="details")
        safe[safe_key] = _safe_scalar(value, field=f"details.{safe_key}")
    return safe


def _safe_source_refs(
    source_refs: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    safe_refs: list[dict[str, Any]] = []
    for index, ref in enumerate(source_refs or []):
        item: dict[str, Any] = {}
        for key, value in ref.items():
            safe_key = _safe_key(str(key), field=f"source_refs[{index}]")
            item[safe_key] = _safe_scalar(
                value, field=f"source_refs[{index}].{safe_key}"
            )
        if item:
            safe_refs.append(item)
    return safe_refs


def _safe_public_refs(
    values: Sequence[str] | None, *, field: str
) -> list[str]:
    safe_refs: list[str] = []
    for value in values or []:
        safe = _safe_public_ref(value, field=field)
        if safe:
            safe_refs.append(safe)
    return safe_refs


def _normalized_event_kind(event_kind: str) -> str:
    text = str(event_kind).strip().lower().replace("-", "_")
    if text not in ROLLOUT_EVENT_KINDS:
        choices = ", ".join(sorted(ROLLOUT_EVENT_KINDS))
        raise ValueError(f"unsupported rollout event kind {event_kind!r}; choose {choices}")
    return text


def _event_id(payload: Mapping[str, Any]) -> str:
    """Identify one event occurrence, including its observation timestamp."""

    stable = {
        key: value
        for key, value in payload.items()
        if key != "event_id"
    }
    encoded = json.dumps(stable, sort_keys=True, ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _idempotency_body(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in payload.items() if key != "event_id"}


def _iter_rollout_event_lines(
    log_path: Path,
    *,
    strict: bool = False,
) -> Iterator[tuple[int, str]]:
    try:
        handle = log_path.open("rb")
    except OSError:
        return
    with handle:
        for line_number, encoded_line in enumerate(handle, start=1):
            try:
                line = encoded_line.decode("utf-8")
            except UnicodeDecodeError:
                if strict:
                    raise ValueError(
                        f"rollout event log line {line_number} must contain valid UTF-8"
                    ) from None
                continue
            yield line_number, line


def _append_rollout_event_line(
    log_path: Path,
    payload: Mapping[str, Any],
) -> None:
    """Append one readable JSONL row after any torn final record."""

    encoded = (
        json.dumps(dict(payload), sort_keys=True, ensure_ascii=False).encode("utf-8")
        + b"\n"
    )
    with log_path.open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() > 0:
            handle.seek(-1, 2)
            if handle.read(1) != b"\n":
                handle.write(b"\n")
        handle.write(encoded)


def rollout_event_log_path(runtime_root: Path, goal_id: str) -> Path:
    # Goal ids are used as directory names throughout the control plane. Keep
    # this shared path helper fail-closed so every reader and writer inherits
    # the same single-segment boundary.
    from .history import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    return (
        runtime_root.expanduser()
        / "goals"
        / safe_goal_id
        / DEFAULT_ROLLOUT_EVENT_LOG_NAME
    )


def build_rollout_event(
    *,
    goal_id: str,
    event_kind: str,
    agent_id: str | None = None,
    todo_id: str | None = None,
    case_id: str | None = None,
    run_id: str | None = None,
    lane_id: str | None = None,
    agent_role: str | None = None,
    gate_id: str | None = None,
    decision_id: str | None = None,
    from_state: str | None = None,
    to_state: str | None = None,
    caused_by: str | None = None,
    source_event_id: str | None = None,
    blocks: Sequence[str] | None = None,
    unblocks: Sequence[str] | None = None,
    handoff_to: str | None = None,
    commit_ref: str | None = None,
    pr_ref: str | None = None,
    revert_of: str | None = None,
    status: str | None = None,
    classification: str | None = None,
    delivery_outcome: str | None = None,
    labels: Sequence[str] | None = None,
    summary: str | None = None,
    artifact_refs: Sequence[str] | None = None,
    source_refs: Sequence[Mapping[str, Any]] | None = None,
    details: Mapping[str, Any] | None = None,
    private_source_kind: str | None = None,
    private_source_count: int | None = None,
    recorded_at: str | None = None,
) -> dict[str, Any]:
    """Build a public-safe append-only LoopX rollout event.

    The event records lifecycle metadata only. It intentionally keeps raw Codex
    sessions, runner logs, task text, trajectories, credentials, and absolute
    local paths out of the payload.
    """

    from .history import validate_goal_id_path_segment

    safe_goal_id = validate_goal_id_path_segment(goal_id)
    private_kind = (
        str(private_source_kind).strip()
        if private_source_kind
        else None
    )
    if private_kind and private_kind not in PRIVATE_SOURCE_KINDS:
        choices = ", ".join(sorted(PRIVATE_SOURCE_KINDS))
        raise ValueError(f"unsupported private source kind {private_kind!r}; choose {choices}")
    safe_artifact_refs = [
        ref
        for ref in (
            _safe_public_ref(value, field="artifact_refs") for value in artifact_refs or []
        )
        if ref
    ]
    safe_labels = [
        text
        for text in (_compact_text(value, limit=80, field="labels") for value in labels or [])
        if text
    ]
    payload: dict[str, Any] = {
        "schema_version": ROLLOUT_EVENT_SCHEMA_VERSION,
        "goal_id": safe_goal_id,
        "event_kind": _normalized_event_kind(event_kind),
        "recorded_at": recorded_at or _now_iso(),
        "boundary": {
            "raw_task_text_recorded": False,
            "raw_logs_recorded": False,
            "raw_trajectory_recorded": False,
            "raw_session_transcript_recorded": False,
            "credential_values_recorded": False,
            "absolute_paths_recorded": False,
        },
    }
    optional_scalars = {
        "agent_id": agent_id,
        "todo_id": todo_id,
        "case_id": case_id,
        "run_id": run_id,
        "status": status,
        "classification": classification,
        "delivery_outcome": delivery_outcome,
        "summary": summary,
    }
    for key, value in optional_scalars.items():
        safe = _compact_text(value, limit=500 if key == "summary" else 180, field=key)
        if safe:
            payload[key] = safe
    lane: dict[str, Any] = {}
    for key, value in {
        "lane_id": lane_id,
        "agent_role": agent_role,
    }.items():
        safe = _compact_text(value, limit=180, field=key)
        if safe:
            lane[key] = safe
    if lane:
        payload["lane"] = lane
    transition: dict[str, Any] = {}
    for key, value in {
        "from_state": from_state,
        "to_state": to_state,
    }.items():
        safe = _compact_text(value, limit=180, field=key)
        if safe:
            transition[key] = safe
    if transition:
        payload["state_transition"] = transition
    causality: dict[str, Any] = {}
    for key, value in {
        "caused_by": caused_by,
        "source_event_id": source_event_id,
        "gate_id": gate_id,
        "decision_id": decision_id,
    }.items():
        safe = _compact_text(value, limit=180, field=key)
        if safe:
            causality[key] = safe
    relation_blocks = _safe_public_refs(blocks, field="blocks")
    relation_unblocks = _safe_public_refs(unblocks, field="unblocks")
    if relation_blocks:
        causality["blocks"] = relation_blocks
    if relation_unblocks:
        causality["unblocks"] = relation_unblocks
    if causality:
        payload["causality"] = causality
    code_refs: dict[str, Any] = {}
    for key, value in {
        "commit_ref": commit_ref,
        "pr_ref": pr_ref,
        "revert_of": revert_of,
    }.items():
        safe = _safe_public_ref(value, field=key)
        if safe:
            code_refs[key] = safe
    if code_refs:
        payload["code_refs"] = code_refs
    safe_handoff_to = _compact_text(handoff_to, limit=180, field="handoff_to")
    if safe_handoff_to:
        payload["handoff"] = {"to_agent_id": safe_handoff_to}
    if safe_labels:
        payload["labels"] = safe_labels
    if safe_artifact_refs:
        payload["artifact_refs"] = safe_artifact_refs
    safe_source_refs = _safe_source_refs(source_refs)
    if safe_source_refs:
        payload["source_refs"] = safe_source_refs
    safe_details = _safe_details(details)
    if safe_details:
        payload["details"] = safe_details
    if private_kind:
        payload["private_source"] = {
            "kind": private_kind,
            "raw_values_recorded": False,
        }
        if private_source_count is not None:
            if private_source_count < 0:
                raise ValueError("private_source_count must be non-negative")
            payload["private_source"]["count"] = int(private_source_count)
    payload["event_id"] = _event_id(payload)
    return payload


def append_rollout_event(log_path: Path, event: Mapping[str, Any]) -> dict[str, Any]:
    """Append once by event id, preserving distinct timestamped observations."""

    payload = dict(event)
    if payload.get("schema_version") != ROLLOUT_EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported rollout event schema")
    event_id = str(payload.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("rollout event_id is required")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(log_path):
        for _, line in _iter_rollout_event_lines(log_path):
            if event_id not in line:
                continue
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(existing, dict) or existing.get("event_id") != event_id:
                continue
            if _idempotency_body(existing) == _idempotency_body(payload):
                return existing
            raise ValueError(f"conflicting rollout event_id: {event_id}")
        _append_rollout_event_line(log_path, payload)
    return payload


def append_rollout_event_once(
    log_path: Path,
    event: Mapping[str, Any],
    *,
    identity_fields: Sequence[str],
    precondition: Callable[[], None] | None = None,
) -> tuple[dict[str, Any], bool]:
    """Append once by a stable identity; check a transition under the same lock."""

    payload = dict(event)
    if payload.get("schema_version") != ROLLOUT_EVENT_SCHEMA_VERSION:
        raise ValueError("unsupported rollout event schema")
    fields = tuple(str(field).strip() for field in identity_fields if str(field).strip())
    if not fields:
        raise ValueError("rollout idempotency identity_fields are required")
    missing = [field for field in fields if payload.get(field) in {None, ""}]
    if missing:
        raise ValueError(
            "rollout idempotency fields must be populated: " + ", ".join(missing)
        )
    event_id = str(payload.get("event_id") or "").strip()
    if not event_id:
        raise ValueError("rollout event_id is required")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(log_path):
        for _, line in _iter_rollout_event_lines(log_path):
            try:
                existing = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(existing, dict):
                continue
            if all(existing.get(field) == payload.get(field) for field in fields):
                return existing, False
            if existing.get("event_id") == event_id:
                if _idempotency_body(existing) == _idempotency_body(payload):
                    return existing, False
                raise ValueError(f"conflicting rollout event_id: {event_id}")
        if precondition is not None:
            precondition()
        _append_rollout_event_line(log_path, payload)
    return payload, True


def iter_rollout_events(
    log_path: Path,
    *,
    strict: bool = False,
) -> Iterator[dict[str, Any]]:
    for line_number, line in _iter_rollout_event_lines(log_path, strict=strict):
        if not line.strip():
            continue
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            if strict:
                raise ValueError(
                    f"rollout event log line {line_number} must contain valid JSON"
                ) from None
            continue
        if not isinstance(parsed, dict):
            if strict:
                raise ValueError(
                    f"rollout event log line {line_number} must contain an object"
                )
            continue
        if parsed.get("schema_version") != ROLLOUT_EVENT_SCHEMA_VERSION:
            if strict:
                raise ValueError(
                    f"rollout event log line {line_number} must use "
                    f"{ROLLOUT_EVENT_SCHEMA_VERSION}"
                )
            continue
        yield parsed


def load_rollout_events(log_path: Path, *, limit: int | None = None) -> list[dict[str, Any]]:
    events = iter_rollout_events(log_path)
    if limit is None or limit <= 0:
        return list(events)
    return list(deque(events, maxlen=limit))


class RolloutEventSnapshot:
    def __init__(self, runtime_root: Path, *, limit: int) -> None:
        if limit <= 0:
            raise ValueError("rollout event snapshot limit must be positive")
        self._runtime_root = runtime_root
        self._limit = limit
        self._events_by_goal: dict[str, tuple[Mapping[str, Any], ...]] = {}

    def events_for_goal(
        self,
        goal_id: str,
        *,
        limit: int,
    ) -> Sequence[Mapping[str, Any]]:
        if limit != self._limit:
            raise ValueError(
                "rollout event snapshot limit mismatch: "
                f"configured {self._limit}, requested {limit}"
            )
        if goal_id not in self._events_by_goal:
            self._events_by_goal[goal_id] = tuple(
                load_rollout_events(
                    rollout_event_log_path(self._runtime_root, goal_id),
                    limit=self._limit,
                )
            )
        return self._events_by_goal[goal_id]


def _safe_event_view(event: Mapping[str, Any]) -> dict[str, Any]:
    keys = (
        "event_id",
        "recorded_at",
        "event_kind",
        "status",
        "agent_id",
        "todo_id",
        "case_id",
        "run_id",
        "lane",
        "state_transition",
        "causality",
        "code_refs",
        "handoff",
        "classification",
        "delivery_outcome",
        "summary",
    )
    return {key: event[key] for key in keys if key in event}


def summarize_rollout_events(
    events: Iterable[Mapping[str, Any]], *, limit: int = 12
) -> dict[str, Any]:
    event_list = [dict(event) for event in events]
    counts_by_kind = Counter(str(event.get("event_kind") or "") for event in event_list)
    counts_by_status = Counter(str(event.get("status") or "") for event in event_list if event.get("status"))
    latest = event_list[-1] if event_list else None
    return {
        "schema_version": ROLLOUT_EVENT_SUMMARY_SCHEMA_VERSION,
        "event_count": len(event_list),
        "counts_by_kind": dict(sorted(counts_by_kind.items())),
        "counts_by_status": dict(sorted(counts_by_status.items())),
        "latest_event": _safe_event_view(latest) if latest else None,
        "recent_events": [_safe_event_view(event) for event in event_list[-max(0, limit) :]],
        "boundary": {
            "raw_task_text_recorded": False,
            "raw_logs_recorded": False,
            "raw_trajectory_recorded": False,
            "raw_session_transcript_recorded": False,
            "credential_values_recorded": False,
            "absolute_paths_recorded": False,
        },
    }
