"""Compatibility transport for the single TypeScript Todo display owner."""
from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from .handoff_note import todo_read_source


class _CanonicalTodoSummary(dict[str, Any]):
    """Ephemeral source lineage; dict serialization exposes only product fields."""

    def __init__(self, value: dict[str, Any], *, read: dict[str, Any],
                 expected: dict[str, Any], field: str, digest: str,
                 basis: bytes, request_identity: object, evaluations: Any = None,
                 source_row_fields: tuple[str, ...] = ()) -> None:
        super().__init__(value)
        self.read, self.expected, self.field, self.digest = read, expected, field, digest
        self.basis, self.request_identity = basis, request_identity
        self.evaluations = evaluations
        self.source_row_fields = source_row_fields

    def __deepcopy__(self, memo: dict[int, Any]) -> _CanonicalTodoSummary:
        from copy import deepcopy
        result = _CanonicalTodoSummary({}, read=deepcopy(self.read, memo),
            expected=deepcopy(self.expected, memo), field=self.field, digest=self.digest,
            basis=self.basis, request_identity=self.request_identity,
            evaluations=deepcopy(self.evaluations, memo), source_row_fields=self.source_row_fields)
        memo[id(self)] = result
        result.update(deepcopy(dict(self), memo))
        return result


def preserve_canonical_todo_source(source: dict[str, Any], value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(source, _CanonicalTodoSummary):
        return value
    return _CanonicalTodoSummary(value, read=source.read, expected=source.expected,
        field=source.field, digest=source.digest, basis=source.basis,
        request_identity=source.request_identity, evaluations=source.evaluations,
        source_row_fields=source.source_row_fields)


def bind_canonical_todo_sources(result: dict[str, Any], read: dict[str, Any]) -> None:
    from ..effect_runtime import todo_projection_request_identity

    identity = todo_projection_request_identity()
    metadata = result.get("source_projection")
    fields = result.get("fields", (result.get("projection") or {}).get("summaries", result.get("payload")))
    if identity is None or not isinstance(metadata, dict) or not isinstance(fields, dict):
        return
    for field, value in fields.items():
        digest = metadata.get("field_digests", {}).get(field)
        if not isinstance(value, dict) or not isinstance(digest, str):
            continue
        basis = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "backslashreplace")
        observed = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8", "backslashreplace")
        if sha256(observed).hexdigest() != digest:
            # A modified response or a numeric JSON spelling mismatch cannot
            # mint a native reference. Keep the complete supplied source codec.
            continue
        fields[field] = _CanonicalTodoSummary(value, read={key: value for key, value in metadata.get("replay_read", read).items() if key != "return_projection"},
            expected=metadata["source"], field=field, digest=digest,
            basis=basis, request_identity=identity,
            evaluations=(result.get("field_evaluations") or {}).get(field),
            source_row_fields=tuple(metadata.get("source_row_fields", [])))


def canonical_todo_select_read(params: dict[str, Any]) -> dict[str, Any] | None:
    """Compose the existing selector only when every consumed field is native."""
    fields, references = params.get("fields") or {}, {}
    read = expected = None
    for role in params.get("roles") or []:
        field = f"{role}_todos"
        source = fields.get(field)
        if not isinstance(source, _CanonicalTodoSummary):
            return None
        reference = canonical_todo_source_reference(source)
        if reference is None or (params.get("field_evaluations") or {}).get(field) != source.evaluations:
            return None
        if read is not None and (read != reference["read"] or expected != reference["expected_source"]):
            return None
        read, expected = reference["read"], reference["expected_source"]
        references[field] = {key: value for key, value in reference.items() if key not in {"read", "expected_source"}}
    if read is None:
        return None
    selector = {key: value for key, value in params.items() if key not in {"kind", "fields", "field_evaluations"}}
    return {**read, "return_projection": True,
        "read_pipeline": [*(read.get("read_pipeline") or []),
            {"expected_source": expected, "sources": references, "selector": selector}]}


def canonical_todo_source_reference(value: dict[str, Any]) -> dict[str, Any] | None:
    from ..effect_runtime import todo_projection_request_identity

    if (not isinstance(value, _CanonicalTodoSummary)
            or value.request_identity is not todo_projection_request_identity()):
        return None
    current = todo_read_source(value)
    original = json.loads(value.basis)
    changes: list[dict[str, Any]] = []

    def diff(before: Any, after: Any, path: list[Any]) -> None:
        if isinstance(before, dict) and isinstance(after, dict):
            for key in before.keys() - after.keys():
                changes.append({"path": [*path, key], "delete": True})
            for key, child in after.items():
                if key not in before:
                    changes.append({"path": [*path, key], "value": child})
                else:
                    diff(before[key], child, [*path, key])
        elif isinstance(before, list) and isinstance(after, list) and len(before) == len(after):
            for index, (left, right) in enumerate(zip(before, after, strict=True)):
                diff(left, right, [*path, index])
        elif type(before) is not type(after) or before != after:
            changes.append({"path": path, "value": after})

    diff(original, current["record"], [])
    if (any(change["path"][0] in value.source_row_fields or
            change["path"][0] in original and change["path"][0] != "goal_acceptance_contract"
            for change in changes) or any(fact[0] and fact[0][0] in original
            and fact[0][0] != "goal_acceptance_contract" for fact in current.get("python_values", []))):
        # Transformed Todo facts remain complete supplied source, rather than
        # retaining provider provenance. The native owner declares row lanes.
        return None
    return {"read": value.read, "expected_source": value.expected,
        "field": value.field, "field_sha256": value.digest,
        "changes": changes, "python_values": current.get("python_values", [])}


class _QuotaProjectedSummary(dict[str, Any]):
    """Prepared wait facts over an explicitly consumed immutable source view."""

    def __init__(self, value: dict[str, Any], basis: dict[str, Any], rows: list[dict[str, Any]], agent: Any):
        from ..effect_runtime import todo_projection_request_identity
        super().__init__(value)
        self.wait_basis, self.agent = basis, agent
        self.request_identity = todo_projection_request_identity()
        self.rows = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "backslashreplace")

    def __deepcopy__(self, memo: dict[int, Any]) -> _QuotaProjectedSummary:
        from copy import deepcopy
        result = _QuotaProjectedSummary({}, deepcopy(self.wait_basis, memo), [], self.agent)
        memo[id(self)] = result
        result.update(deepcopy(dict(self), memo))
        result.rows, result.request_identity = self.rows, self.request_identity
        return result


def quota_vision_wait_basis(summary: dict[str, Any], rows: Any, agent: Any) -> dict[str, Any] | None:
    from ..effect_runtime import todo_projection_request_identity
    if (not isinstance(summary, _QuotaProjectedSummary) or summary.request_identity is None
            or summary.request_identity is not todo_projection_request_identity() or summary.agent != agent):
        return None
    observed = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8", "backslashreplace")
    return summary.wait_basis if observed == summary.rows else None

TODO_SUMMARY_SOURCE_KEYS = (
    "active_next_action_items",
    "active_next_action_executable_items",
    "first_open_items",
    "backlog_items",
    "unclaimed_priority_open_items",
    "claimed_open_items",
    "claimed_advancement_open_items",
    "claimed_monitor_open_items",
    "monitor_open_items",
    "current_agent_claimed_open_items",
    "current_agent_claimed_advancement_items",
    "current_agent_claimed_monitor_items",
    "blocker_items",
    "resume_blocked_items",
    "monitor_blocked_resume_candidates",
    "current_agent_monitor_blocked_resume_candidates",
    "unclaimed_monitor_blocked_resume_candidates",
    "items",
)

TODO_PLANNING_SOURCE_KEYS = (
    "items",
    "deferred_items",
    "blocker_items",
    "monitor_open_items",
)


def todo_text_content_revision(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    if not normalized:
        return None
    return "sha256:" + sha256(normalized.encode("utf-8")).hexdigest()


def _project(request: dict[str, Any]) -> list[dict[str, Any]]:
    from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

    try:
        result = effect_runtime_result("todo.context.page", {"summary_display": request},
                                       large_local_snapshot=True)
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    rows = result.get("items") if isinstance(result, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("invalid typed Todo summary display")
    return rows


def compact_todo_summary_items(
    items: list[dict[str, Any]], *, texts: list[str | None] | None = None, strip_text: bool = False,
) -> list[dict[str, Any]]:
    from ..effect_runtime import MAX_REQUEST_BYTES

    if texts is not None and len(texts) != len(items):
        raise ValueError("summary text cardinality mismatch")
    sources = [todo_read_source(item, **({"text": texts[i]} if texts is not None else {}))
               for i, item in enumerate(items)]
    result: list[dict[str, Any]] = []
    start, size = 0, 0
    for i in range(len(sources) + 1):
        row_size = 0 if i == len(sources) else len(json.dumps(sources[i], separators=(",", ":")).encode()) + 2
        if i > start and (i == len(sources) or size + row_size > MAX_REQUEST_BYTES - 4096):
            rows = _project({"items": sources[start:i], "strip_text": strip_text})
            if len(rows) != i - start:
                raise ValueError("summary display cardinality mismatch")
            result.extend(rows)
            start, size = i, 0
        size += row_size
    return result


def compact_todo_summary_item(item: dict[str, Any], *, text: str | None = None) -> dict[str, Any]:
    return compact_todo_summary_items([item], texts=[text])[0]


def todo_summary_source_items(value: dict[str, Any]) -> list[dict[str, Any]]:
    from .handoff_gate import todo_summary_handoff_gates

    if not any(value.get(key) for key in TODO_SUMMARY_SOURCE_KEYS):
        return []
    return _project({"source": todo_read_source(value), "handoff_gates": todo_summary_handoff_gates(value)})


def todo_planning_source_items(value: dict[str, Any], *, include_terminal: bool = False) -> list[dict[str, Any]]:
    if not any(value.get(key) for key in TODO_PLANNING_SOURCE_KEYS):
        return []
    return _project({"source": todo_read_source(value), "include_terminal": include_terminal})


def todo_source_display_views(value: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    """Compose displays only for a caller consuming all three existing views."""
    from ..effect_runtime import MAX_REQUEST_BYTES, EffectRuntimeRejected, effect_runtime_result
    from .handoff_gate import todo_summary_handoff_gates

    has_summary = any(value.get(key) for key in TODO_SUMMARY_SOURCE_KEYS)
    has_planning = any(value.get(key) for key in TODO_PLANNING_SOURCE_KEYS)
    if not has_summary and not has_planning:
        return {"summary": [], "planning": [], "terminal": []}
    request = {"summary_display_views": {
        "source": todo_read_source(value), "handoff_gates": todo_summary_handoff_gates(value),
    }}
    # This bounds the opt-in batch input; it does not predict response size or
    # promise inline transport. Large sources retain the existing view limits.
    if len(json.dumps(request, ensure_ascii=False, separators=(",", ":")).encode("utf-8", errors="backslashreplace")) > MAX_REQUEST_BYTES // 4:
        return {"summary": todo_summary_source_items(value),
                "planning": todo_planning_source_items(value),
                "terminal": todo_planning_source_items(value, include_terminal=True)}
    try:
        result = effect_runtime_result("todo.context.page", request, large_local_snapshot=True)
    except EffectRuntimeRejected as error:
        raise ValueError(str(error)) from error
    return {"summary": result["summary"] if has_summary else [],
            "planning": result["planning"] if has_planning else [],
            "terminal": result["terminal"] if has_planning else []}
