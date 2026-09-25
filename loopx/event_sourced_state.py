from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from .control_plane.runtime.time import now_utc_iso as runtime_now_utc_iso
from .file_lock import exclusive_file_lock
from .control_plane.todos.contract import (
    TODO_MONITOR_METADATA_FIELDS,
    TODO_STATUS_DONE,
    TODO_STATUS_BLOCKED,
    TODO_STATUS_DEFERRED,
    TODO_STATUS_OPEN,
    TODO_TASK_PATTERN,
    build_todo_id,
    format_todo_metadata_line,
    normalize_explicit_todo_task_class,
    normalize_required_capabilities,
    normalize_required_write_scopes,
    normalize_removed_todo_continuation_policy,
    normalize_todo_action_kind,
    normalize_todo_blocks_agent,
    normalize_todo_bound_agent,
    normalize_todo_capability_binding_ref,
    normalize_todo_claimed_by,
    normalize_todo_continuation_policy,
    normalize_todo_excluded_agents,
    normalize_todo_global_gate,
    normalize_todo_goal_bound,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_status,
    normalize_todo_task_domain,
    normalize_todo_task_repository,
    parse_todo_metadata_line,
    todo_done_for_status,
    todo_marker_for_status,
    todo_status_from_marker,
)


STATE_EVENT_SCHEMA_VERSION = "loopx_state_event_v0"
STATE_PROJECTION_SCHEMA_VERSION = "event_sourced_state_projection_v0"
STATE_PROJECTION_VERSION = "event_sourced_state_contract_v0"

PUBLIC_PRIVACY = "public_safe"
LOCAL_PRIVATE_PRIVACY = "local_private"
PRIVATE_POINTER_PRIVACY = "private_pointer"
PRIVACY_VALUES = {PUBLIC_PRIVACY, LOCAL_PRIVATE_PRIVACY, PRIVATE_POINTER_PRIVACY}

TODO_ADDED = "todo_added"
TODO_CLAIMED = "todo_claimed"
TODO_UPDATED = "todo_updated"
TODO_BLOCKED = "todo_blocked"
TODO_DEFERRED = "todo_deferred"
TODO_COMPLETED = "todo_completed"
REFRESH_RECORDED = "refresh_recorded"
RUN_RECORDED = "run_recorded"
QUOTA_SPENT = "quota_spent"
EVIDENCE_ATTACHED = "evidence_attached"
SUPERVISOR_PROPOSED = "supervisor_proposed"
SUPERVISOR_RECEIPT_RECORDED = "supervisor_receipt_recorded"

MARKDOWN_BACKFILL_PRODUCER = "loopx.backfill"
MARKDOWN_HEADING_PATTERN = re.compile(r"^##\s+(.+?)\s*$")
TODO_PRIORITY_PREFIX_PATTERN = re.compile(r"^\[(P[0-4])\]\s+(.+)$", re.IGNORECASE)
PUBLIC_BACKFILL_REDACTION = "[redacted-private-state]"
PUBLIC_BACKFILL_UNSAFE_PATTERNS = (
    re.compile(r"(?i)(?:^|[\s`'\"])(?:/Users/|/private/|/var/folders/)"),
    re.compile(r"(?i)\b(?:secret|password|credential|token)\b"),
    re.compile(r"https?://"),
)

SUPPORTED_EVENT_TYPES = {
    TODO_ADDED,
    TODO_CLAIMED,
    TODO_UPDATED,
    TODO_BLOCKED,
    TODO_DEFERRED,
    TODO_COMPLETED,
    REFRESH_RECORDED,
    RUN_RECORDED,
    QUOTA_SPENT,
    EVIDENCE_ATTACHED,
    SUPERVISOR_PROPOSED,
    SUPERVISOR_RECEIPT_RECORDED,
}

TODO_EVENT_TYPES = {
    TODO_ADDED,
    TODO_CLAIMED,
    TODO_UPDATED,
    TODO_BLOCKED,
    TODO_DEFERRED,
    TODO_COMPLETED,
}


class StateEventError(ValueError):
    """Raised when a state event cannot be accepted or replayed."""


class StateEventConflictError(StateEventError):
    """Raised when a duplicate event id carries different event content."""


class StateEventSourceChangedError(StateEventError):
    """The locked event stream differs from the basis used to plan the write."""


class StateEventCommitUnknownError(StateEventError):
    """Publication may have landed; read back before repeating business work."""


def now_utc_iso() -> str:
    return runtime_now_utc_iso()


def compact_text(value: Any) -> str:
    return " ".join(str(value or "").strip().split())


def _copy_todo_added_validation_fields(
    source: dict[str, Any], target: dict[str, Any]
) -> None:
    """Copy caller-approved completion validation fields from a TODO_ADDED payload."""
    for key in (
        "validation_command",
        "validation_label",
        "validation_timeout_seconds",
    ):
        if key in source and source.get(key) is not None:
            target[key] = source[key]
    if "validation_command_argv" in source:
        target["validation_command_argv"] = source.get("validation_command_argv")


def _redact_public_backfill_text(value: Any, *, privacy: str) -> str:
    text = compact_text(value)
    if privacy != PUBLIC_PRIVACY:
        return text
    if any(pattern.search(text) for pattern in PUBLIC_BACKFILL_UNSAFE_PATTERNS):
        return PUBLIC_BACKFILL_REDACTION
    return text


def _redact_public_backfill_source_ref(value: Any, *, privacy: str) -> str:
    text = compact_text(value) or "ACTIVE_GOAL_STATE.md"
    if privacy != PUBLIC_PRIVACY:
        return text
    if any(pattern.search(text) for pattern in PUBLIC_BACKFILL_UNSAFE_PATTERNS):
        return "ACTIVE_GOAL_STATE.md"
    return text


def _role_for_markdown_heading(heading: str) -> str | None:
    normalized = compact_text(heading).lower()
    if normalized.startswith("user todo") or "owner review" in normalized:
        return "user"
    if normalized.startswith("agent todo"):
        return "agent"
    return None


def _todo_priority_and_title(text: Any, *, privacy: str) -> tuple[str, str]:
    compact = compact_text(text)
    match = TODO_PRIORITY_PREFIX_PATTERN.match(compact)
    if match:
        return match.group(1).upper(), _redact_public_backfill_text(match.group(2), privacy=privacy)
    return "P2", _redact_public_backfill_text(compact, privacy=privacy)


def _backfill_event_id(*, goal_id: str, todo_id: str, suffix: str) -> str:
    digest = hashlib.sha1(f"{goal_id}|{todo_id}|{suffix}".encode("utf-8")).hexdigest()[:16]
    return f"backfill-{suffix}-{digest}"


def _backfill_source_refs(
    *,
    source_ref: str,
    source_section: str,
    source_line: int | None,
    privacy: str,
) -> dict[str, Any]:
    refs: dict[str, Any] = {
        "source_ref": _redact_public_backfill_source_ref(source_ref, privacy=privacy),
        "source_section": compact_text(source_section),
    }
    if source_line is not None:
        refs["source_line"] = source_line
    return refs


def _markdown_todo_records(state_text: str) -> list[dict[str, Any]]:
    role: str | None = None
    source_section: str | None = None
    current: dict[str, Any] | None = None
    records: list[dict[str, Any]] = []
    role_indexes = {"user": 0, "agent": 0}

    for line_number, line in enumerate(state_text.splitlines(), start=1):
        heading_match = MARKDOWN_HEADING_PATTERN.match(line)
        if heading_match:
            source_section = compact_text(heading_match.group(1))
            role = _role_for_markdown_heading(source_section)
            current = None
            continue
        if role is None or source_section is None:
            continue
        todo_match = TODO_TASK_PATTERN.match(line)
        if todo_match:
            marker, text = todo_match.groups()
            role_indexes[role] += 1
            current = {
                "role": role,
                "source_section": source_section,
                "source_line": line_number,
                "planner_order": role_indexes[role],
                "status": todo_status_from_marker(marker),
                "text": compact_text(text),
            }
            records.append(current)
            continue
        if current is None or not line.startswith((" ", "\t")):
            continue
        metadata = parse_todo_metadata_line(line)
        if metadata:
            current.update(metadata)
            continue
        continuation = compact_text(line)
        if continuation:
            current["text"] = compact_text(f"{current.get('text', '')} {continuation}")
    for record in records:
        role = str(record.get("role") or "agent")
        source_section = str(record.get("source_section") or "")
        title_text = compact_text(record.get("text"))
        if not normalize_todo_id(record.get("todo_id")):
            record["todo_id"] = build_todo_id(
                role=role,
                source_section=source_section,
                index=record.get("planner_order"),
                text=title_text,
            )
        record["status"] = normalize_todo_status(record.get("status")) or TODO_STATUS_OPEN
    return records


def backfill_todo_events_from_markdown(
    state_text: str,
    *,
    goal_id: str,
    source_ref: str = "ACTIVE_GOAL_STATE.md",
    recorded_at: str | None = None,
    producer: str = MARKDOWN_BACKFILL_PRODUCER,
    privacy: str = LOCAL_PRIVATE_PRIVACY,
) -> list[dict[str, Any]]:
    """Convert Markdown workbench todos into idempotent append-only events.

    The helper does not mutate the Markdown source. When writing a public-safe
    stream, compact todo titles/evidence/reasons that look like private state
    are redacted; local-private streams preserve the workbench text.
    """
    normalized_goal_id = compact_text(goal_id)
    if not normalized_goal_id:
        raise StateEventError("goal_id is required")
    if privacy not in PRIVACY_VALUES:
        raise StateEventError(f"privacy must be one of: {', '.join(sorted(PRIVACY_VALUES))}")

    events: list[dict[str, Any]] = []
    for record in _markdown_todo_records(state_text):
        role = str(record.get("role") or "agent")
        todo_id = normalize_todo_id(record.get("todo_id")) or build_todo_id(
            role=role,
            source_section=record.get("source_section"),
            index=record.get("planner_order"),
            text=record.get("text"),
        )
        priority, title = _todo_priority_and_title(record.get("text"), privacy=privacy)
        refs = {
            "todo_id": todo_id,
            **_backfill_source_refs(
                source_ref=source_ref,
                source_section=str(record.get("source_section") or ""),
                source_line=record.get("source_line"),
                privacy=privacy,
            ),
        }
        payload: dict[str, Any] = {
            "role": role,
            "priority": priority,
            "title": title,
            "planner_order": record.get("planner_order"),
        }
        task_class = normalize_explicit_todo_task_class(record.get("task_class"))
        action_kind = normalize_todo_action_kind(record.get("action_kind"))
        task_domain = normalize_todo_task_domain(record.get("task_domain"))
        capability_binding_ref = normalize_todo_capability_binding_ref(
            record.get("capability_binding_ref")
        )
        task_repository = normalize_todo_task_repository(
            record.get("task_repository")
        )
        continuation_policy = normalize_todo_continuation_policy(
            record.get("continuation_policy")
        )
        removed_continuation_policy = normalize_removed_todo_continuation_policy(
            record.get("removed_continuation_policy")
        )
        required_write_scopes = normalize_required_write_scopes(
            record.get("required_write_scopes")
        )
        required_capabilities = normalize_required_capabilities(
            record.get("required_capabilities")
        )
        blocks_agent = normalize_todo_blocks_agent(record.get("blocks_agent"))
        bound_agent = normalize_todo_bound_agent(record.get("bound_agent"))
        goal_bound = normalize_todo_goal_bound(record.get("goal_bound"))
        global_gate = normalize_todo_global_gate(record.get("global_gate"))
        excluded_agents = normalize_todo_excluded_agents(record.get("excluded_agents"))
        if task_class:
            payload["task_class"] = task_class
        if action_kind:
            payload["action_kind"] = action_kind
        if task_domain:
            payload["task_domain"] = task_domain
        if capability_binding_ref:
            payload["capability_binding_ref"] = capability_binding_ref
        if task_repository:
            payload["task_repository"] = task_repository
        if continuation_policy:
            payload["continuation_policy"] = continuation_policy
        if removed_continuation_policy:
            payload["removed_continuation_policy"] = removed_continuation_policy
        if required_write_scopes:
            payload["required_write_scopes"] = required_write_scopes
        if required_capabilities:
            payload["required_capabilities"] = required_capabilities
        if blocks_agent:
            payload["blocks_agent"] = blocks_agent
        if bound_agent:
            payload["bound_agent"] = bound_agent
        if goal_bound is not None:
            payload["goal_bound"] = goal_bound
        if global_gate is not None:
            payload["global_gate"] = global_gate
        if excluded_agents:
            payload["excluded_agents"] = excluded_agents
        _copy_todo_added_validation_fields(record, payload)
        if privacy == PUBLIC_PRIVACY:
            for key in (
                "validation_command",
                "validation_command_argv",
                "validation_label",
            ):
                if key in payload:
                    payload[key] = _redact_public_backfill_text(
                        payload[key], privacy=privacy
                    )
        events.append(
            make_state_event(
                event_id=_backfill_event_id(goal_id=normalized_goal_id, todo_id=todo_id, suffix="add"),
                goal_id=normalized_goal_id,
                event_type=TODO_ADDED,
                refs=refs,
                payload=payload,
                recorded_at=recorded_at,
                producer=producer,
                privacy=privacy,
            )
        )

        claimed_by = normalize_todo_claimed_by(record.get("claimed_by"))
        if claimed_by:
            events.append(
                make_state_event(
                    event_id=_backfill_event_id(goal_id=normalized_goal_id, todo_id=todo_id, suffix="claim"),
                    goal_id=normalized_goal_id,
                    event_type=TODO_CLAIMED,
                    refs=refs,
                    payload={"claimed_by": claimed_by},
                    recorded_at=recorded_at,
                    producer=producer,
                    privacy=privacy,
                )
            )

        status = normalize_todo_status(record.get("status")) or TODO_STATUS_OPEN
        if status == TODO_STATUS_DONE:
            completion_payload: dict[str, Any] = {}
            for key in ("evidence", "reason"):
                if record.get(key):
                    completion_payload[key] = _redact_public_backfill_text(record[key], privacy=privacy)
            events.append(
                make_state_event(
                    event_id=_backfill_event_id(goal_id=normalized_goal_id, todo_id=todo_id, suffix="complete"),
                    goal_id=normalized_goal_id,
                    event_type=TODO_COMPLETED,
                    refs=refs,
                    payload=completion_payload,
                    recorded_at=recorded_at,
                    producer=producer,
                    privacy=privacy,
                )
            )
        elif status == TODO_STATUS_DEFERRED:
            deferred_payload = {}
            for key in ("reason", "resume_when"):
                if record.get(key):
                    deferred_payload[key] = _redact_public_backfill_text(record[key], privacy=privacy)
            events.append(
                make_state_event(
                    event_id=_backfill_event_id(goal_id=normalized_goal_id, todo_id=todo_id, suffix="defer"),
                    goal_id=normalized_goal_id,
                    event_type=TODO_DEFERRED,
                    refs=refs,
                    payload=deferred_payload,
                    recorded_at=recorded_at,
                    producer=producer,
                    privacy=privacy,
                )
            )
        elif status == TODO_STATUS_BLOCKED:
            blocked_payload = {}
            if record.get("reason"):
                blocked_payload["reason"] = _redact_public_backfill_text(record["reason"], privacy=privacy)
            events.append(
                make_state_event(
                    event_id=_backfill_event_id(goal_id=normalized_goal_id, todo_id=todo_id, suffix="block"),
                    goal_id=normalized_goal_id,
                    event_type=TODO_BLOCKED,
                    refs=refs,
                    payload=blocked_payload,
                    recorded_at=recorded_at,
                    producer=producer,
                    privacy=privacy,
                )
            )
    return events


def _sorted_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value)}


def event_fingerprint(event: dict[str, Any]) -> str:
    comparable = {key: value for key, value in event.items() if key != "append_sequence"}
    return json.dumps(comparable, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def event_stream_checksum(events: Iterable[dict[str, Any]]) -> str:
    body = "\n".join(
        json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for event in events
    )
    return "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()


def _require_dict(value: Any, *, field_name: str) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise StateEventError(f"{field_name} must be an object")
    return dict(value)


def normalize_state_event(event: dict[str, Any], *, append_sequence: int | None = None) -> dict[str, Any]:
    if not isinstance(event, dict):
        raise StateEventError("event must be an object")

    event_id = compact_text(event.get("event_id"))
    goal_id = compact_text(event.get("goal_id"))
    event_type = compact_text(event.get("event_type"))
    if not event_id:
        raise StateEventError("event_id is required")
    if not goal_id:
        raise StateEventError("goal_id is required")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise StateEventError(f"unsupported event_type: {event_type}")

    refs = _require_dict(event.get("refs"), field_name="refs")
    if refs.get("mutates_prior_event_id"):
        raise StateEventError("events must not mutate prior events")
    payload = _require_dict(event.get("payload"), field_name="payload")

    if event_type in TODO_EVENT_TYPES:
        todo_id = normalize_todo_id(refs.get("todo_id"))
        if not todo_id:
            raise StateEventError(f"{event_type} requires refs.todo_id")
        refs["todo_id"] = todo_id
        if payload.get("capability_binding_ref") is not None:
            capability_binding_ref = normalize_todo_capability_binding_ref(
                payload.get("capability_binding_ref")
            )
            if not capability_binding_ref:
                raise StateEventError(
                    "capability_binding_ref must be a public-safe namespaced token"
                )
            payload["capability_binding_ref"] = capability_binding_ref
        if payload.get("task_domain") is not None:
            task_domain = normalize_todo_task_domain(payload.get("task_domain"))
            if not task_domain:
                raise StateEventError(
                    "task_domain must be a public-safe lowercase token"
                )
            payload["task_domain"] = task_domain

    privacy = compact_text(event.get("privacy") or PUBLIC_PRIVACY)
    if privacy not in PRIVACY_VALUES:
        raise StateEventError(f"privacy must be one of: {', '.join(sorted(PRIVACY_VALUES))}")

    sequence = append_sequence if append_sequence is not None else event.get("append_sequence")
    if sequence is not None:
        if isinstance(sequence, bool) or not isinstance(sequence, int):
            raise StateEventError("append_sequence must be an integer")
        if sequence < 1:
            raise StateEventError("append_sequence must be positive")

    normalized = {
        "schema_version": compact_text(event.get("schema_version") or STATE_EVENT_SCHEMA_VERSION),
        "event_id": event_id,
        "goal_id": goal_id,
        "event_type": event_type,
        "recorded_at": compact_text(event.get("recorded_at") or now_utc_iso()),
        "producer": compact_text(event.get("producer") or "loopx.event_sourced_state"),
        "privacy": privacy,
        "projection_version": compact_text(event.get("projection_version") or STATE_PROJECTION_VERSION),
        "refs": _sorted_dict(refs),
        "payload": _sorted_dict(payload),
    }
    actor_agent_id = normalize_todo_claimed_by(event.get("actor_agent_id"))
    if actor_agent_id:
        normalized["actor_agent_id"] = actor_agent_id
    if normalized["schema_version"] != STATE_EVENT_SCHEMA_VERSION:
        raise StateEventError(f"schema_version must be {STATE_EVENT_SCHEMA_VERSION}")
    if sequence is not None:
        normalized["append_sequence"] = sequence
    return normalized


def make_state_event(
    *,
    event_id: str,
    goal_id: str,
    event_type: str,
    refs: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    recorded_at: str | None = None,
    producer: str | None = None,
    privacy: str = PUBLIC_PRIVACY,
    projection_version: str = STATE_PROJECTION_VERSION,
    actor_agent_id: str | None = None,
) -> dict[str, Any]:
    return normalize_state_event(
        {
            "schema_version": STATE_EVENT_SCHEMA_VERSION,
            "event_id": event_id,
            "goal_id": goal_id,
            "event_type": event_type,
            "recorded_at": recorded_at or now_utc_iso(),
            "producer": producer or "loopx.event_sourced_state",
            "privacy": privacy,
            "projection_version": projection_version,
            "refs": refs or {},
            "payload": payload or {},
            "actor_agent_id": actor_agent_id,
        }
    )


@dataclass
class AppendOnlyStateEventStore:
    path: Path

    def load(self) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        if self.path.exists():
            for line_number, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
                if not line.strip():
                    continue
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise StateEventError(f"invalid JSONL at line {line_number}: {exc}") from exc
                events.append(normalize_state_event(raw))
        return _dedupe_events(events)

    def append(self, event: dict[str, Any]) -> dict[str, Any]:
        return self.append_many((event,))[0]

    def append_many(
        self,
        events: Iterable[dict[str, Any]],
        *,
        expected_checksum: str | None = None,
    ) -> list[dict[str, Any]]:
        """Publish an eager batch atomically; lazy iterables retain per-item visibility.

        A compare-and-append requires an eager batch. Materialize caller-owned
        iterators outside this method when one atomic publication is intended.
        """
        if type(events) not in (list, tuple):
            if expected_checksum is not None:
                raise StateEventError("a source-bound append requires a list or tuple")
            return [self.append(event) for event in events]
        if not events and expected_checksum is None:
            return []

        from .control_plane.effect_runtime import effect_runtime_result
        from .control_plane.todos.active_state_editing import (
            atomic_write_state_text,
            verify_state_text_durable,
        )

        # Validate all caller data before entering the lock or publishing any
        # bytes. Sequence allocation is deliberately deferred to the TS owner.
        normalized = [
            normalize_state_event(event, append_sequence=1) for event in events
        ]
        requested_ids = {item["event_id"] for item in normalized}

        def identity(item: dict[str, Any]) -> dict[str, Any]:
            return {
                "event_id": item["event_id"],
                "fingerprint": hashlib.sha256(
                    event_fingerprint(item).encode("utf-8")
                ).hexdigest(),
            }

        with exclusive_file_lock(self.path):
            stored = self.load()
            existing = {item["event_id"]: item for item in stored}
            plan = effect_runtime_result(
                "goal.state_event.plan_append",
                {
                    "schema_version": "loopx_state_event_append_plan_v0",
                    "source_checksum": event_stream_checksum(
                        sorted(stored, key=event_sort_key)
                    ),
                    "expected_checksum": expected_checksum,
                    "last_sequence": max(
                        (int(item["append_sequence"]) for item in stored), default=0
                    ),
                    "existing": [
                        {**identity(item), "append_sequence": item["append_sequence"]}
                        for item in stored
                        if item["event_id"] in requested_ids
                    ],
                    "events": [identity(item) for item in normalized],
                },
            )
            if plan.get("schema_version") != "loopx_state_event_append_result_v0":
                raise StateEventError("invalid event append plan result schema")
            if plan.get("status") != "planned":
                reason = plan.get("reason_code")
                if reason == "event_source_changed":
                    raise StateEventSourceChangedError(
                        "event source changed before append"
                    )
                if reason == "event_id_conflict":
                    raise StateEventConflictError(
                        f"conflicting event_id: {plan['event_id']}"
                    )
                raise StateEventError(str(reason or "invalid event append plan"))
            choices = plan.get("choices")
            if not isinstance(choices, list) or len(choices) != len(normalized):
                raise StateEventError("invalid event append plan choices")
            appended: list[dict[str, Any]] = []
            additions: list[dict[str, Any]] = []
            for event, choice in zip(normalized, choices, strict=True):
                if (
                    not isinstance(choice, dict)
                    or choice.get("event_id") != event["event_id"]
                    or choice.get("kind") not in {"append", "replay"}
                    or isinstance(choice.get("append_sequence"), bool)
                    or not isinstance(choice.get("append_sequence"), int)
                    or not 1 <= choice["append_sequence"] <= 2**53 - 1
                ):
                    raise StateEventError("invalid event append plan choice")
                if choice["kind"] == "append":
                    event["append_sequence"] = choice["append_sequence"]
                    existing[event["event_id"]] = event
                    additions.append(event)
                appended.append(existing[event["event_id"]])
            # Preserve all historical bytes, including harmless blank lines.
            # Replacing the whole file changes no prior event or sequence.
            prior_text = (
                self.path.read_bytes().decode("utf-8") if self.path.exists() else ""
            )
            if additions:
                separator = "\n" if prior_text and not prior_text.endswith("\n") else ""
                suffix = "".join(
                    json.dumps(item, sort_keys=True, ensure_ascii=False) + "\n"
                    for item in additions
                )
                try:
                    atomic_write_state_text(self.path, prior_text + separator + suffix)
                except OSError as error:
                    raise StateEventCommitUnknownError(
                        "event append outcome uncertain; read back the event stream before retrying the original operation"
                    ) from error
            else:
                # A prior replace may have succeeded before directory fsync
                # failed. Exact replay must establish durability, not just see it.
                if self.path.exists():
                    try:
                        verify_state_text_durable(self.path, prior_text)
                    except OSError as error:
                        raise StateEventCommitUnknownError(
                            "event replay durability uncertain; read back the event stream before retrying"
                        ) from error
            return appended


def _dedupe_events(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    ordered: list[dict[str, Any]] = []
    for event in events:
        prior = by_id.get(event["event_id"])
        if prior is not None:
            if event_fingerprint(prior) != event_fingerprint(event):
                raise StateEventConflictError(
                    f"conflicting event_id: {event['event_id']}"
                )
            continue
        by_id[event["event_id"]] = event
        ordered.append(event)
    return ordered


def event_sort_key(event: dict[str, Any]) -> tuple[int, str, str]:
    return (
        int(event.get("append_sequence") or 0),
        str(event.get("recorded_at") or ""),
        str(event.get("event_id") or ""),
    )


def _decode_added_todo_content(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") or {}
    refs = event.get("refs") or {}
    text = compact_text(payload.get("text") or payload.get("title"))
    todo_id = refs["todo_id"]  # The legacy decoder already requires this identity.
    task_class = normalize_explicit_todo_task_class(payload.get("task_class"))
    action_kind = normalize_todo_action_kind(payload.get("action_kind"))
    task_domain = normalize_todo_task_domain(payload.get("task_domain"))
    capability_binding_ref = normalize_todo_capability_binding_ref(
        payload.get("capability_binding_ref")
    )
    task_repository = normalize_todo_task_repository(payload.get("task_repository"))
    continuation_policy = normalize_todo_continuation_policy(
        payload.get("continuation_policy")
    )
    removed_continuation_policy = normalize_removed_todo_continuation_policy(
        payload.get("removed_continuation_policy")
        or payload.get("continuation_policy")
    )
    required_write_scopes = normalize_required_write_scopes(
        payload.get("required_write_scopes")
    )
    required_capabilities = normalize_required_capabilities(
        payload.get("required_capabilities")
    )
    blocks_agent = normalize_todo_blocks_agent(payload.get("blocks_agent"))
    bound_agent = normalize_todo_bound_agent(payload.get("bound_agent"))
    goal_bound = normalize_todo_goal_bound(payload.get("goal_bound"))
    global_gate = normalize_todo_global_gate(payload.get("global_gate"))
    excluded_agents = normalize_todo_excluded_agents(payload.get("excluded_agents"))
    claimed_by = normalize_todo_claimed_by(payload.get("claimed_by"))
    actor_agent_id = normalize_todo_claimed_by(event.get("actor_agent_id"))
    todo: dict[str, Any] = {
        "schema_version": "todo_item_v0", "todo_id": todo_id,
        "title": text, "text": text, "planner_order": payload.get("planner_order"),
        "append_sequence": event.get("append_sequence"),
        "last_event_id": event.get("event_id"),
        "updated_at": compact_text(payload.get("updated_at")),
    }
    if task_class:
        todo["task_class"] = task_class
    if action_kind:
        todo["action_kind"] = action_kind
    if task_domain:
        todo["task_domain"] = task_domain
    if capability_binding_ref:
        todo["capability_binding_ref"] = capability_binding_ref
    if task_repository:
        todo["task_repository"] = task_repository
    if removed_continuation_policy:
        todo["removed_continuation_policy"] = removed_continuation_policy
    elif continuation_policy:
        todo["continuation_policy"] = continuation_policy
    if required_write_scopes:
        todo["required_write_scopes"] = required_write_scopes
    if required_capabilities:
        todo["required_capabilities"] = required_capabilities
    if blocks_agent:
        todo["blocks_agent"] = blocks_agent
    if bound_agent:
        todo["bound_agent"] = bound_agent
    if goal_bound is not None:
        todo["goal_bound"] = goal_bound
    if global_gate is not None:
        todo["global_gate"] = global_gate
    if excluded_agents:
        todo["excluded_agents"] = excluded_agents
    unblocks_todo_id = normalize_todo_id(payload.get("unblocks_todo_id"))
    if unblocks_todo_id:
        todo["unblocks_todo_id"] = unblocks_todo_id
    for key in TODO_MONITOR_METADATA_FIELDS:
        if payload.get(key):
            todo[key] = compact_text(payload[key])
    if claimed_by:
        todo["claimed_by"] = claimed_by
    if actor_agent_id:
        todo["created_by"] = actor_agent_id
    _copy_todo_added_validation_fields(payload, todo)
    return todo


def _decode_todo_event_content(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize historical payload values without reading prior Todo state."""
    todo: dict[str, Any] = {}
    payload = event.get("payload") or {}
    event_type = event.get("event_type")
    actor_agent_id = normalize_todo_claimed_by(event.get("actor_agent_id"))
    if actor_agent_id:
        todo["last_actor_agent_id"] = actor_agent_id
    if event_type == TODO_CLAIMED:
        claimed_by = normalize_todo_claimed_by(payload.get("claimed_by"))
        if claimed_by:
            todo["claimed_by"] = claimed_by
    elif event_type == TODO_UPDATED:
        for key in (
            "title",
            "task_class",
            "action_kind",
        ):
            if payload.get(key):
                todo[key] = compact_text(payload[key])
        task_domain = normalize_todo_task_domain(payload.get("task_domain"))
        if task_domain:
            todo["task_domain"] = task_domain
        capability_binding_ref = normalize_todo_capability_binding_ref(
            payload.get("capability_binding_ref")
        )
        if capability_binding_ref:
            todo["capability_binding_ref"] = capability_binding_ref
        continuation_policy = normalize_todo_continuation_policy(
            payload.get("continuation_policy")
        )
        removed_continuation_policy = normalize_removed_todo_continuation_policy(
            payload.get("removed_continuation_policy")
            or payload.get("continuation_policy")
        )
        update_excluded_agents = normalize_todo_excluded_agents(
            payload.get("excluded_agents")
        )
        if removed_continuation_policy:
            todo["removed_continuation_policy"] = removed_continuation_policy
        elif continuation_policy:
            todo["continuation_policy"] = continuation_policy
        required_write_scopes = normalize_required_write_scopes(
            payload.get("required_write_scopes")
        )
        if required_write_scopes:
            todo["required_write_scopes"] = required_write_scopes
        task_repository = normalize_todo_task_repository(
            payload.get("task_repository")
        )
        if task_repository:
            todo["task_repository"] = task_repository
        required_capabilities = normalize_required_capabilities(
            payload.get("required_capabilities")
        )
        if required_capabilities:
            todo["required_capabilities"] = required_capabilities
        blocks_agent = normalize_todo_blocks_agent(payload.get("blocks_agent"))
        if blocks_agent:
            todo["blocks_agent"] = blocks_agent
        bound_agent = normalize_todo_bound_agent(payload.get("bound_agent"))
        if bound_agent:
            todo["bound_agent"] = bound_agent
        goal_bound = normalize_todo_goal_bound(payload.get("goal_bound"))
        if goal_bound is not None:
            todo["goal_bound"] = goal_bound
        global_gate = normalize_todo_global_gate(payload.get("global_gate"))
        if global_gate is not None:
            todo["global_gate"] = global_gate
        if update_excluded_agents:
            todo["excluded_agents"] = update_excluded_agents
        for key in TODO_MONITOR_METADATA_FIELDS:
            if payload.get(key):
                todo[key] = compact_text(payload[key])
        if payload.get("text") or payload.get("title"):
            title = compact_text(payload.get("text") or payload.get("title"))
            todo["title"] = title
    elif event_type == TODO_BLOCKED:
        if payload.get("reason"):
            todo["reason"] = compact_text(payload["reason"])
    elif event_type == TODO_DEFERRED:
        if payload.get("reason"):
            todo["reason"] = compact_text(payload["reason"])
        if payload.get("resume_when"):
            todo["resume_when"] = compact_text(payload["resume_when"])
    elif event_type == TODO_COMPLETED:
        for key in (
            "evidence",
            "reason",
            "note",
            "completed_at",
            "updated_at",
            "no_followup",
            "completion_turn_key",
            "completion_continuation",
            "completion_recovery",
        ):
            if payload.get(key) is not None:
                todo[key] = compact_text(payload[key])
        successor_todo_ids = normalize_todo_id_list(payload.get("successor_todo_ids"))
        if successor_todo_ids:
            todo["successor_todo_ids"] = successor_todo_ids
    todo["last_event_id"] = event.get("event_id")
    todo["last_append_sequence"] = event.get("append_sequence")
    return todo


def build_state_projection(
    events: Iterable[dict[str, Any]],
    *,
    goal_id: str | None = None,
    generated_at: str | None = None,
) -> dict[str, Any]:
    # Python owns legacy event normalization and exact historical checksum bytes.
    # TS receives bounded semantic facts, never evidence, validation commands or
    # arbitrary payloads; returned ordinals address this exact normalized batch.
    from .control_plane.effect_runtime import EffectRuntimeRejected, effect_runtime_result

    normalized = sorted(_dedupe_events(normalize_state_event(event) for event in events), key=event_sort_key)
    facts = []
    contents = []
    for event in normalized:
        payload = event["payload"]
        kind = event["event_type"]
        edits = kind in (TODO_ADDED, TODO_UPDATED)
        order = payload.get("planner_order") if kind == TODO_ADDED else None
        # The typed fold rejects non-integer orders, so the adapter must not
        # coerce them first: truncating 1.5 to 1 would sort a Todo by one value
        # and report another. Every producer of this payload writes an integer.
        if order is not None and (isinstance(order, bool) or not isinstance(order, int)):
            raise StateEventError("planner_order must be an integer")
        sequence = event.get("append_sequence")
        for value in (order, sequence):
            if value is not None and abs(value) > 2**53 - 1:
                raise StateEventError("event replay integers must be safe integers")
        content = (_decode_added_todo_content(event) if kind == TODO_ADDED else
                   _decode_todo_event_content(event) if kind in TODO_EVENT_TYPES else {})
        contents.append(content)
        facts.append({
            "event_id": event["event_id"], "goal_id": event["goal_id"],
            "event_type": kind, "append_sequence": sequence,
            "recorded_at": event["recorded_at"],
            "todo_id": event["refs"].get("todo_id") if kind in TODO_EVENT_TYPES else None,
            "role": compact_text(payload.get("role")) or None if edits else None,
            "priority": compact_text(payload.get("priority")) or None if edits else None,
            "planner_order": order, "fields": list(content),
            "capability_binding_ref": content.get("capability_binding_ref"),
            "continuation_policy": content.get("continuation_policy"),
            "removed_continuation_policy": content.get("removed_continuation_policy"),
            "has_exclusions": bool(content.get("excluded_agents")),
            "goal_bound": content.get("goal_bound"),
            "content_changed": bool(payload.get("text") or payload.get("title")) if edits else False,
        })
    # Only touched Todo continuation rows cross each bounded call. The host
    # retains content and prior results; TS alone decides all state transitions.
    rows: dict[str, dict[str, Any]] = {}
    timeline_indices: list[int] = []
    inferred_goal = goal_id or (normalized[0]["goal_id"] if normalized else "")
    for offset in range(0, len(facts), 256):
        batch = facts[offset:offset + 256]
        touched = {fact["todo_id"] for fact in batch if fact["todo_id"] is not None}
        try:
            plan = effect_runtime_result("goal.state_event.plan_replay", {
                "schema_version": "state_event_replay_request_v0", "events": batch,
                "goal_id": inferred_goal, "offset": offset,
                "initial_todos": [rows[key] for key in touched if key in rows],
            })
        except EffectRuntimeRejected as exc:
            raise StateEventError(str(exc)) from exc
        if not isinstance(plan, dict) or plan.get("schema_version") != "state_event_replay_plan_v0":
            raise RuntimeError("invalid typed state event replay plan")
        for row in plan["todos"]:
            rows[row["todo_id"]] = row
        timeline_indices.extend(plan["timeline_indices"])
    ordered = normalized  # Exact historical checksum order remains in the codec.
    todo_items = []
    for row in sorted(rows.values(), key=lambda row: row["sort_key"]):
        todo = {key: contents[index][key] for key, index in row["field_sources"].items()}
        for key in ("role", "priority", "status", "done", "source_section"):
            todo[key] = row[key]
        if row["render_priority"]:
            todo["text"] = f"[{row['priority']}] {todo['title']}"
        todo_items.append(todo)
    timeline = []
    for index in timeline_indices:
        event = normalized[index]
        entry = {key: event.get(key) for key in (
            "event_id", "event_type", "append_sequence", "recorded_at", "refs",
        )}
        entry["summary"] = compact_text(event["payload"].get("summary"))
        if event.get("actor_agent_id"):
            entry["actor_agent_id"] = event["actor_agent_id"]
        timeline.append(entry)
    last_event = ordered[-1] if ordered else {}
    return {
        "schema_version": STATE_PROJECTION_SCHEMA_VERSION,
        "goal_id": inferred_goal,
        "generated_at": generated_at or now_utc_iso(),
        "source_event_count": len(ordered),
        "source_checksum": event_stream_checksum(ordered),
        "last_event_id": last_event.get("event_id"),
        "last_append_sequence": last_event.get("append_sequence"),
        "projection_version": STATE_PROJECTION_VERSION,
        "user_todos": _todo_summary([item for item in todo_items if item["role"] == "user"], role="user"),
        "agent_todos": _todo_summary([item for item in todo_items if item["role"] != "user"], role="agent"),
        "timeline": timeline,
    }


def _todo_summary(items: list[dict[str, Any]], *, role: str) -> dict[str, Any]:
    open_items = [item for item in items if not todo_done_for_status(item.get("status"))]
    done_items = [item for item in items if todo_done_for_status(item.get("status"))]
    return {
        "schema_version": "todo_summary_v0",
        "role": role,
        "total_count": len(items),
        "open_count": len(open_items),
        "done_count": len(done_items),
        "items": items,
        "first_open_items": open_items[:5],
    }


def render_todo_markdown(item: dict[str, Any]) -> list[str]:
    status = normalize_todo_status(item.get("status")) or TODO_STATUS_OPEN
    marker = todo_marker_for_status(status)
    text = compact_text(item.get("text") or item.get("title"))
    if not text.startswith("[") and item.get("priority"):
        text = f"[{compact_text(item.get('priority'))}] {text}"
    lines = [f"- [{marker}] {text}"]
    monitor_metadata = {
        key: item.get(key)
        for key in TODO_MONITOR_METADATA_FIELDS
    }
    metadata = format_todo_metadata_line(
        todo_id=item.get("todo_id"),
        status=status,
        task_class=item.get("task_class"),
        action_kind=item.get("action_kind"),
        task_domain=item.get("task_domain"),
        capability_binding_ref=item.get("capability_binding_ref"),
        task_repository=item.get("task_repository"),
        continuation_policy=item.get("continuation_policy"),
        removed_continuation_policy=item.get("removed_continuation_policy"),
        required_write_scopes=item.get("required_write_scopes"),
        required_capabilities=item.get("required_capabilities"),
        claimed_by=item.get("claimed_by"),
        bound_agent=item.get("bound_agent"),
        goal_bound=item.get("goal_bound"),
        blocks_agent=item.get("blocks_agent"),
        excluded_agents=item.get("excluded_agents"),
        global_gate=item.get("global_gate"),
        unblocks_todo_id=item.get("unblocks_todo_id"),
        resume_when=item.get("resume_when"),
        successor_todo_ids=item.get("successor_todo_ids"),
        completion_continuation=item.get("completion_continuation"),
        completion_recovery=item.get("completion_recovery"),
        no_followup=True if item.get("no_followup") == "true" else None,
        **monitor_metadata,
        note=item.get("note"),
        evidence=item.get("evidence"),
        validation_command=item.get("validation_command"),
        validation_command_argv=(
            json.dumps(
                item.get("validation_command_argv"),
                separators=(",", ":"),
                ensure_ascii=False,
            )
            if isinstance(item.get("validation_command_argv"), list)
            else item.get("validation_command_argv")
        ),
        validation_label=item.get("validation_label"),
        validation_timeout_seconds=(
            str(item.get("validation_timeout_seconds"))
            if item.get("validation_timeout_seconds") is not None
            else None
        ),
        completion_turn_key=item.get("completion_turn_key"),
        reason=item.get("reason"),
        completed_at=item.get("completed_at"),
        updated_at=item.get("updated_at"),
    )
    if metadata:
        lines.append(metadata)
    return lines


def render_active_state_sections(projection: dict[str, Any]) -> str:
    lines: list[str] = []
    for heading, summary_key in (
        ("User Todo / Owner Review Reading Queue", "user_todos"),
        ("Agent Todo", "agent_todos"),
    ):
        items = ((projection.get(summary_key) or {}).get("items") or [])
        if not items:
            continue
        if lines:
            lines.append("")
        lines.extend([f"## {heading}", ""])
        for item in items:
            lines.extend(render_todo_markdown(item))

    timeline = projection.get("timeline") or []
    if timeline:
        if lines:
            lines.append("")
        lines.extend(["## Progress Ledger", ""])
        for event in timeline:
            event_type = compact_text(event.get("event_type"))
            summary = compact_text(event.get("summary")) or "event recorded"
            lines.append(f"- {event_type}: {summary}")
    return "\n".join(lines).rstrip() + "\n"
