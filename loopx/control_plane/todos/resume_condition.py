from __future__ import annotations

import re
import json
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from pathlib import Path

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..runtime.time import now_utc_iso
from ..coordination.coordination_state_contract_generated import (
    TODO_RESUME_EVALUATION_REQUEST_SCHEMA,
    TODO_RESUME_EVALUATION_RESULT_SCHEMA,
    TODO_RESUME_NORMALIZE_REQUEST_SCHEMA,
)


TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION = TODO_RESUME_NORMALIZE_REQUEST_SCHEMA
TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION = TODO_RESUME_EVALUATION_REQUEST_SCHEMA
TODO_RESUME_EVALUATION_SCHEMA_VERSION = TODO_RESUME_EVALUATION_RESULT_SCHEMA

TODO_RESUME_KIND_TODO_DONE = "todo_done"
TODO_RESUME_KIND_PR_MERGED = "pr_merged"
TODO_RESUME_KIND_CAPACITY_AVAILABLE = "capacity_available"
TODO_RESUME_KIND_MONITOR_CHANGED = "monitor_changed"
TODO_RESUME_KIND_RESUME_AT = "resume_at"
TODO_RESUME_KIND_VALUES = {
    TODO_RESUME_KIND_TODO_DONE,
    TODO_RESUME_KIND_PR_MERGED,
    TODO_RESUME_KIND_CAPACITY_AVAILABLE,
    TODO_RESUME_KIND_MONITOR_CHANGED,
    TODO_RESUME_KIND_RESUME_AT,
}

_TODO_ID_PATTERN = re.compile(r"^todo_[a-z0-9_-]{3,64}$")
_CAPABILITY_PATTERN = re.compile(r"^[a-z][a-z0-9_:-]{0,63}$")
_RESUME_WHEN_PATTERN = re.compile(
    r"^[a-z][a-z0-9_-]{0,31}(?::[a-z0-9_.:@-]{1,96})?$"
)
_RESUME_PR_MERGED_PATTERN = re.compile(
    r"^pr_merged:(?:[a-z\d_.-]{1,80}/[a-z\d_.-]{1,100})?#[1-9]\d{0,8}$"
)
_RESUME_AT_PATTERN = re.compile(
    r"^resume_at:(?P<timestamp>[1-9]\d{3}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}"
    r"(?:\.\d{1,3})?(?:Z|[+-]\d{2}:\d{2}))$",
    re.IGNORECASE,
)
_PR_REF_NUMBER_PATTERN = re.compile(
    r"(?:/pull/|#|pr[-_\s]*)([1-9]\d{0,8})(?:\b|/|#|\?|$)",
    re.IGNORECASE,
)
_GITHUB_PULL_URL_PATTERN = re.compile(
    r"^https://github\.com/([^/]+/[^/]+)/pull/([1-9]\d{0,8})(?:\b|/|#|\?)",
    re.IGNORECASE,
)
_QUALIFIED_PR_REF_PATTERN = re.compile(
    r"^([a-z\d_.-]+/[a-z\d_.-]+)#([1-9]\d{0,8})$",
    re.IGNORECASE,
)
_PR_MERGED_EVENT_KINDS = {
    "pr_merge",
    "pr_merged",
    "pull_request_merge",
    "pull_request_merged",
}

_RESUME_ITEM_FIELDS = (
    "todo_id",
    "role",
    "status",
    "task_class",
    "archive_state",
    "source_section",
    "claimed_by",
    "task_repository",
    "resume_when",
    "resume_ready",
    "resume_monitor_generation",
    "material_change_generation",
)


def normalize_todo_generation(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return None
    return normalized if normalized >= 0 else None


def normalize_todo_resume_when(value: Any) -> str | None:
    """Read a persisted public-safe resume token without starting the runtime."""

    candidate = " ".join(str(value or "").strip().split())
    resume_at = _RESUME_AT_PATTERN.match(candidate)
    if resume_at:
        timestamp = resume_at.group("timestamp")
        if timestamp[-1].lower() != "z":
            offset_hour = int(timestamp[-5:-3])
            offset_minute = int(timestamp[-2:])
            if (
                offset_hour > 14
                or offset_minute > 59
                or (offset_hour == 14 and offset_minute != 0)
            ):
                return None
        try:
            parsed = datetime.fromisoformat(
                timestamp.replace("Z", "+00:00").replace("z", "+00:00")
            )
            utc = parsed.astimezone(timezone.utc)
        except (OverflowError, ValueError):
            return None
        if parsed.tzinfo is None:
            return None
        offset = parsed.utcoffset()
        if offset is None or abs(offset.total_seconds()) > 14 * 60 * 60:
            return None
        if not 1000 <= utc.year <= 9999:
            return None
        canonical = (
            utc.isoformat(timespec="milliseconds")
            if utc.microsecond
            else utc.replace(microsecond=0).isoformat()
        ).replace("+00:00", "Z")
        return f"{TODO_RESUME_KIND_RESUME_AT}:{canonical}"
    candidate = candidate.lower()
    if candidate.startswith(f"{TODO_RESUME_KIND_RESUME_AT}:"):
        return None
    if candidate and _RESUME_PR_MERGED_PATTERN.match(candidate):
        return candidate
    if candidate and _RESUME_WHEN_PATTERN.match(candidate):
        return candidate
    return None


def normalize_supported_todo_resume_when(value: Any) -> str | None:
    """Read only resume kinds whose evaluator is shipped by LoopX."""

    candidate = normalize_todo_resume_when(value)
    if not candidate:
        return None
    kind, separator, target = candidate.partition(":")
    if kind in {TODO_RESUME_KIND_TODO_DONE, TODO_RESUME_KIND_MONITOR_CHANGED}:
        return candidate if separator and _TODO_ID_PATTERN.match(target) else None
    if kind == TODO_RESUME_KIND_PR_MERGED:
        return candidate if _RESUME_PR_MERGED_PATTERN.match(candidate) else None
    if kind == TODO_RESUME_KIND_CAPACITY_AVAILABLE:
        return candidate if separator and _CAPABILITY_PATTERN.match(target) else None
    if kind == TODO_RESUME_KIND_RESUME_AT:
        return candidate if _RESUME_AT_PATTERN.match(candidate) else None
    return None


def require_supported_todo_resume_when(value: Any) -> str | None:
    """Validate new authoring through the TypeScript Todo-domain contract."""

    if value is None or not str(value).strip():
        return None
    normalized = normalize_todo_resume_when_via_runtime(value)
    if normalized:
        return normalized
    raise ValueError(
        "resume_when must use a supported condition: todo_done:<todo_id>, "
        "monitor_changed:<monitor_todo_id>, pr_merged:[owner/repo]#<number>, "
        "capacity_available:<capability>, or "
        "resume_at:<timezone-aware-rfc3339-timestamp>"
    )


def _compact_item(value: Mapping[str, Any]) -> dict[str, Any]:
    return {
        field: value[field]
        for field in _RESUME_ITEM_FIELDS
        if value.get(field) is not None
    }


def compact_todo_resume_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Lossless-for-resume facts, independent of display limits and prose size."""
    return [_compact_item(item) for item in items if item.get("todo_id")]


def _pr_ref_number(value: Any) -> int | None:
    if not isinstance(value, str):
        return None
    match = _PR_REF_NUMBER_PATTERN.search(value.strip())
    return int(match.group(1)) if match else None


def _normalized_pr_ref(value: Any) -> tuple[str | None, int] | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip().lower()
    pull_url = _GITHUB_PULL_URL_PATTERN.match(candidate)
    if pull_url:
        return pull_url.group(1), int(pull_url.group(2))
    qualified = _QUALIFIED_PR_REF_PATTERN.match(candidate)
    if qualified:
        return qualified.group(1), int(qualified.group(2))
    number = _pr_ref_number(candidate)
    return (None, number) if number is not None else None


def _github_repository(value: Any) -> str | None:
    candidate = str(value or "").strip().lower()
    prefix = "git:github.com/"
    if not candidate.startswith(prefix):
        return None
    repository = candidate[len(prefix) :].rstrip("/")
    return repository if len(repository.split("/")) == 2 else None


def _resume_pr_targets(
    items: list[dict[str, Any]],
) -> set[tuple[str | None, int]]:
    targets: set[tuple[str | None, int]] = set()
    for item in items:
        resume_when = normalize_supported_todo_resume_when(item.get("resume_when"))
        if not resume_when or not resume_when.startswith(
            f"{TODO_RESUME_KIND_PR_MERGED}:"
        ):
            continue
        target = _normalized_pr_ref(resume_when.partition(":")[2])
        if target is None:
            continue
        repository, number = target
        targets.add(
            (
                repository or _github_repository(item.get("task_repository")),
                number,
            )
        )
    return targets


def _pr_ref_matches_targets(
    value: Any,
    *,
    targets: set[tuple[str | None, int]],
) -> bool:
    ref = _normalized_pr_ref(value)
    if ref is None:
        return False
    repository, number = ref
    return any(
        target_number == number
        and (target_repository is None or target_repository == repository)
        for target_repository, target_number in targets
    )


def _compact_merge_event(
    event: Mapping[str, Any],
    *,
    targets: set[tuple[str | None, int]],
) -> dict[str, Any] | None:
    event_kind = str(event.get("event_kind") or "").strip().lower()
    if event_kind not in _PR_MERGED_EVENT_KINDS:
        return None
    compact: dict[str, Any] = {"event_kind": event_kind}
    for field in ("event_id", "recorded_at"):
        value = event.get(field)
        if isinstance(value, str) and value.strip():
            compact[field] = value.strip()
    refs: list[str] = []
    direct_ref = event.get("pr_ref")
    if isinstance(direct_ref, str) and _pr_ref_matches_targets(
        direct_ref, targets=targets
    ):
        refs.append(direct_ref)
        compact["pr_ref"] = direct_ref
    code_refs = event.get("code_refs")
    if (
        isinstance(code_refs, Mapping)
        and isinstance(code_refs.get("pr_ref"), str)
        and _pr_ref_matches_targets(code_refs["pr_ref"], targets=targets)
    ):
        refs.append(code_refs["pr_ref"])
        compact["code_refs"] = {"pr_ref": code_refs["pr_ref"]}
    source_refs: list[dict[str, str]] = []
    for source_ref in event.get("source_refs") or []:
        if not isinstance(source_ref, Mapping):
            continue
        kind = str(source_ref.get("kind") or "").strip().lower()
        ref = source_ref.get("ref")
        if kind not in {"pull_request", "pr"} or not isinstance(ref, str):
            continue
        if _pr_ref_matches_targets(ref, targets=targets):
            refs.append(ref)
            source_refs.append({"kind": kind, "ref": ref})
    if source_refs:
        compact["source_refs"] = source_refs
    if not refs:
        return None
    return compact


def _compact_resume_rollout_events(
    items: list[dict[str, Any]],
    rollout_events: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    targets = _resume_pr_targets(items)
    if not targets:
        return []
    compacted: list[dict[str, Any]] = []
    # This is transport-only de-duplication. Different references remain visible
    # to the typed evaluator; no global count may evict another dependency.
    seen: set[str] = set()
    for event in rollout_events or []:
        if not isinstance(event, Mapping):
            continue
        compact = _compact_merge_event(event, targets=targets)
        if compact is None:
            continue
        identity = json.dumps({key: compact[key] for key in ("pr_ref", "code_refs", "source_refs")
            if key in compact}, sort_keys=True)
        if identity in seen:
            continue
        seen.add(identity)
        compacted.append(compact)
    return compacted


class TodoResumeRolloutEvents(list[dict[str, Any]]):
    """A display snapshot with its local, complete evidence source attached.

    Only the resume adapter consumes this pointer. Other read-model consumers
    still see the original bounded list, with unchanged counts and ordering.
    """

    def __init__(self, events: Any, *, runtime_root: Path, goal_id: str) -> None:
        super().__init__(dict(event) for event in events)
        self.source = {"runtime_root": str(runtime_root), "goal_id": goal_id}


def normalize_todo_resume_when_via_runtime(value: Any) -> str | None:
    """Normalize new resume authoring through the Todo-domain TS contract."""

    try:
        result = effect_runtime_result(
            "todo.resume_condition.normalize",
            {
                "schema_version": TODO_RESUME_NORMALIZE_REQUEST_SCHEMA_VERSION,
                "resume_when": value,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if result is None:
        return None
    if not isinstance(result, str):
        raise RuntimeError("TypeScript Todo resume normalization shape mismatch")
    return result


def evaluate_todo_resume_conditions(
    items: list[dict[str, Any]],
    *,
    source_items: list[dict[str, Any]],
    rollout_events: list[dict[str, Any]] | None = None,
    available_capabilities: Any = None,
    kinds: list[str] | None = None,
    evaluated_at: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Return TS-owned resume conditions keyed by the waiting Todo id."""

    request: dict[str, Any] = {
        "schema_version": TODO_RESUME_EVALUATION_REQUEST_SCHEMA_VERSION,
        "items": [_compact_item(item) for item in items if item.get("todo_id")],
        "source_items": [
            _compact_item(item) for item in source_items if item.get("todo_id")
        ],
        # Resume evaluation needs only compact PR-merge identity evidence.
        # Sending complete rollout rows made long-lived Goals exceed the
        # Effect-runtime transport budget even without a PR-waiting Todo.
        "rollout_events": _compact_resume_rollout_events(items, rollout_events),
        "evaluated_at": evaluated_at or now_utc_iso(),
    }
    if isinstance(rollout_events, TodoResumeRolloutEvents):
        request["rollout_event_source"] = rollout_events.source
    if available_capabilities is not None:
        request["available_capabilities"] = sorted(
            {
                str(item).strip().lower()
                for item in available_capabilities
                if str(item).strip()
            }
        )
    if kinds is not None:
        request["kinds"] = kinds
    try:
        result = effect_runtime_result("todo.resume_condition.evaluate", request)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or (
        result.get("schema_version") != TODO_RESUME_EVALUATION_SCHEMA_VERSION
    ):
        raise RuntimeError("TypeScript Todo resume evaluation shape mismatch")
    conditions: dict[str, dict[str, Any]] = {}
    for row in result.get("conditions", []):
        if not isinstance(row, Mapping) or not isinstance(row.get("condition"), Mapping):
            continue
        todo_id = str(row.get("todo_id") or "").strip()
        if todo_id:
            conditions[todo_id] = dict(row["condition"])
    return conditions
