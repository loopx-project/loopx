"""Project the durable completion continuation for the built-in Turn CLI provider.

``loopx turn run-once`` completes the selected Todo through
:func:`loopx.control_plane.todos.mutation_api.complete_goal_todo` and must report
the continuation the Todo lifecycle durably recorded (``successor``, ``no_followup``, or ``active_goal``)
instead of normalizing every completion to ``active_goal``.

Projection reads the persisted Todo lifecycle record only; it never trusts host
result JSON and never creates successors from host text. Contradictory or
dangling durable state fails closed so the typed settlement never sees a
fabricated continuation.
"""

from __future__ import annotations

from collections.abc import Collection
from pathlib import Path
from typing import Any, Literal, Mapping

from .active_state_editing import (
    TODO_SECTION_HEADINGS,
    find_todo_block,
    section_bounds,
    todo_blocks,
)
from .contract import normalize_todo_id
from ..effect_runtime import EffectRuntimeRejected
from .goal_todo_projection import project_todo_read


TodoCompletionProjectionSource = Literal["materialized"]


def _read_legacy_todo_record(
    state_file: Path, *, todo_id: str,
) -> tuple[dict[str, Any], set[str], TodoCompletionProjectionSource]:
    """Decode explicit legacy Markdown; canonical callers never enter this IO path."""
    lines = state_file.read_text(encoding="utf-8").splitlines()
    block: dict[str, Any] | None = None
    projection_source: TodoCompletionProjectionSource = "materialized"
    match = find_todo_block(lines, todo_id=todo_id)
    if match is not None:
        _role, _section, _start, _end, block = match
    existing_todo_ids: set[str] = set()
    for candidate_role in TODO_SECTION_HEADINGS:
        bounds = section_bounds(lines, candidate_role)
        if bounds is None:
            continue
        start, end, _heading = bounds
        for item in todo_blocks(lines, start, end, role=candidate_role):
            item_todo_id = normalize_todo_id(item.get("todo_id"))
            if item_todo_id:
                existing_todo_ids.add(item_todo_id)
    if block is None:
        normalized_todo_id = normalize_todo_id(todo_id) or todo_id
        raise ValueError(
            f"durable completion todo_id {normalized_todo_id!r} was not found "
            "in persisted Todo lifecycle state"
        )
    return block, existing_todo_ids, projection_source


def _completion_facts(todo: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve Python scalar/container text at the legacy format boundary."""
    successors = todo.get("successor_todo_ids")
    values = successors if isinstance(successors, (list, tuple, set)) else [successors]
    return {
        **{
            key: str(todo.get(key) or "")
            for key in (
                "todo_id",
                "status",
                "completion_continuation",
                "completion_recovery",
            )
        },
        "no_followup": todo.get("no_followup")
        if isinstance(todo.get("no_followup"), bool)
        else str(todo.get("no_followup") or ""),
        "successor_todo_ids": [str(value or "") for value in values],
    }


def _completion_request(**facts: Any) -> dict[str, Any]:
    try:
        result = project_todo_read({"kind": "completion_evidence", **facts})
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript durable completion result must be an object")
    if "canonical" in result:
        from ..coordination.local_authority import validate_canonical_todo_read

        validate_canonical_todo_read(
            result["canonical"], goal_id=facts["source"]["goal_id"]
        )
    if "completion_error" in result:
        raise ValueError(result["completion_error"])
    completion = result.get("completion")
    if not isinstance(completion, dict):
        raise RuntimeError("TypeScript durable completion result must be an object")
    return completion


def _project_completion_view(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    existing_todo_ids: Collection[str] | None,
    operation: str,
    completion_turn_key: str | None = None,
    projection_source: str = "materialized",
) -> dict[str, Any]:
    fence_todo = {}
    for key in ("no_followup", "completion_continuation", "completion_turn_key"):
        value = todo.get(key)
        fence_todo[key] = (
            value
            if value is None
            or isinstance(value, str)
            or (key == "no_followup" and isinstance(value, bool))
            else str(value)
        )
    successors = todo.get("successor_todo_ids")
    fence_todo.update(
        status=str(todo.get("status") or "open"),
        successor_todo_ids=(
            list(successors) if isinstance(successors, (tuple, set)) else successors
        ),
    )
    return _completion_request(
        todo=_completion_facts(todo),
        expected_todo_id=str(expected_todo_id or ""),
        existing_todo_ids=list(existing_todo_ids)
        if existing_todo_ids is not None
        else None,
        operation=operation,
        completion_turn_key=completion_turn_key,
        projection_source=projection_source,
        fence_todo=fence_todo,
    )


def project_durable_completion_outcome(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    existing_todo_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    return _project_completion_view(
        todo=todo,
        expected_todo_id=expected_todo_id,
        existing_todo_ids=existing_todo_ids,
        operation="outcome",
    )


def project_durable_completion_intent(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    existing_todo_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    return _project_completion_view(
        todo=todo,
        expected_todo_id=expected_todo_id,
        existing_todo_ids=existing_todo_ids,
        operation="intent",
    )


def project_durable_terminal_completion_readback(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    expected_completion_turn_key: str,
    projection_source: TodoCompletionProjectionSource,
    existing_todo_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    return _project_completion_view(
        todo=todo,
        expected_todo_id=expected_todo_id,
        existing_todo_ids=existing_todo_ids,
        operation="terminal",
        completion_turn_key=expected_completion_turn_key,
        projection_source=projection_source,
    )


def read_durable_completion_evidence(
    state_file: Path,
    *,
    todo_id: str,
    operation: str,
    registry_path: Path | None = None,
    goal_id: str | None = None,
    runtime_root: Path | None = None,
    completion_turn_key: str | None = None,
) -> dict[str, Any]:
    """Read canonical evidence in one native transaction; decode legacy IO locally."""
    if runtime_root is not None and goal_id is not None:
        return _completion_request(
            source={
                "runtime_root": str(runtime_root.expanduser().resolve(strict=False)),
                "goal_id": goal_id,
            },
            expected_todo_id=todo_id,
            operation=operation,
            completion_turn_key=completion_turn_key,
        )
    todo, existing, source = _read_legacy_todo_record(state_file, todo_id=todo_id)
    return _project_completion_view(
        todo=todo,
        expected_todo_id=todo_id,
        existing_todo_ids=existing,
        operation=operation,
        completion_turn_key=completion_turn_key,
        projection_source=source,
    )
