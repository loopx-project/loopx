"""Project the durable completion continuation for the built-in Turn CLI provider.

``loopx turn run-once`` completes the selected Todo through
:func:`loopx.todos.complete_goal_todo` and must report the continuation the Todo
lifecycle durably recorded (``successor``, ``no_followup``, or ``active_goal``)
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
from .contract import (
    TODO_STATUS_DONE,
    normalize_todo_id,
    normalize_todo_id_list,
    normalize_todo_no_followup,
    normalize_todo_status,
)
from .completion_state import (
    TodoCompletionContinuation,
    normalize_todo_completion_continuation,
    normalize_todo_completion_recovery,
)
from .completion_fence import evaluate_todo_completion_fence


TodoCompletionProjectionSource = Literal["materialized"]


def read_persisted_todo_record(
    state_file: Path,
    *,
    todo_id: str,
    registry_path: Path | None = None,
    goal_id: str | None = None,
    runtime_root: Path | None = None,
) -> tuple[dict[str, Any], set[str]]:
    """Read one durable Todo record and the goal-state Todo id universe.

    Returns the parsed Todo record for ``todo_id`` together with the set of
    Todo ids present in the persisted goal state. Like
    ``complete_goal_todo``, the record is looked up in the active Markdown
    blocks first and falls back to the event projection for event-sourced
    goals. Raises ``ValueError`` when the Todo cannot be located in the
    durable lifecycle, so the caller can fail closed instead of projecting a
    fabricated continuation.
    """
    todo, existing_todo_ids, _projection_source = (
        read_persisted_todo_record_with_source(
            state_file,
            todo_id=todo_id,
            registry_path=registry_path,
            goal_id=goal_id,
            runtime_root=runtime_root,
        )
    )
    return todo, existing_todo_ids


def read_persisted_todo_record_with_source(
    state_file: Path,
    *,
    todo_id: str,
    registry_path: Path | None = None,
    goal_id: str | None = None,
    runtime_root: Path | None = None,
) -> tuple[dict[str, Any], set[str], TodoCompletionProjectionSource]:
    """Read one durable Todo together with its authoritative projection source.

    A promoted canonical provider is the primary persisted lifecycle. Markdown
    and event projection remain the pre-promotion sources only; a stale
    Markdown projection must never hide a canonical commit during Turn retry.
    """

    if runtime_root is not None and goal_id is not None:
        from ..coordination.local_authority import (
            read_canonical_todos_if_promoted,
        )

        canonical = read_canonical_todos_if_promoted(
            runtime_root=runtime_root,
            goal_id=goal_id,
        )
        if canonical is not None:
            records = [
                dict(item)
                for item in canonical.get("todos", [])
                if isinstance(item, Mapping)
            ]
            canonical_todo_ids = {
                normalized
                for item in records
                if (normalized := normalize_todo_id(item.get("todo_id")))
            }
            normalized_todo_id = normalize_todo_id(todo_id) or todo_id
            for item in records:
                if normalize_todo_id(item.get("todo_id")) == normalized_todo_id:
                    return item, canonical_todo_ids, "materialized"
            raise ValueError(
                f"durable completion todo_id {normalized_todo_id!r} was not found "
                "in canonical provider state"
            )

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


def project_durable_completion_outcome(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    existing_todo_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Project the typed completion continuation recorded by the Todo lifecycle.

    ``todo`` is the durable Todo record re-read from persisted lifecycle state
    after ``complete_goal_todo``. The outcome is one of:

    - ``successor`` when the Todo durably declares ``successor_todo_ids``;
      every declared successor must exist in the goal state
      (``existing_todo_ids``) or projection fails closed;
    - ``no_followup`` when the Todo durably records ``no_followup``;
    - ``active_goal`` when that value is durably recorded.

    Raises ``ValueError`` (fail closed) when the durable record contradicts
    itself, omits its explicit completion continuation, declares a dangling
    successor, does not match ``expected_todo_id``, or is not durably done.
    """
    return _project_durable_completion(
        todo=todo,
        expected_todo_id=expected_todo_id,
        existing_todo_ids=existing_todo_ids,
        require_done=True,
    )


def project_durable_terminal_completion_readback(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    expected_completion_turn_key: str,
    projection_source: TodoCompletionProjectionSource,
    existing_todo_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Resolve terminal completion evidence under the canonical Turn fence.

    A still-open Todo or a same-Turn terminal upgrade proves the terminal
    effect absent.  A completed no-follow-up Todo proves the effect committed
    only when its durable ``completion_turn_key`` matches the recovering Turn.
    The TypeScript fence raises for missing, cross-Turn, or contradictory
    completion identity so callers can map uncertainty to fail-closed recovery.
    """

    fence = evaluate_todo_completion_fence(
        todo=todo,
        projection_source=projection_source,
        completion_turn_key=expected_completion_turn_key,
        no_followup=True,
    )
    if fence["outcome"] == "continue":
        if fence["reason"] == "same_turn_terminal_upgrade" or (
            fence["reason"] == "not_terminal" and fence["status"] == "open"
        ):
            return {"kind": "absent"}
        raise ValueError("durable terminal completion is not safely replayable")
    if normalize_todo_status(todo.get("status")) != TODO_STATUS_DONE:
        raise ValueError("durable terminal completion is not safely replayable")

    completion = project_durable_completion_outcome(
        todo=todo,
        expected_todo_id=expected_todo_id,
        existing_todo_ids=existing_todo_ids,
    )
    if completion.get("continuation") != TodoCompletionContinuation.NO_FOLLOWUP.value:
        raise ValueError("durable terminal completion is not no-follow-up")
    return {"kind": "committed", "completion": completion}


def project_durable_completion_intent(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    existing_todo_ids: Collection[str] | None = None,
) -> dict[str, Any]:
    """Project the declared continuation before applying lifecycle closeout.

    Turn settlement uses this read-only projection to distinguish an ordinary
    completion from the terminal ``no_followup`` effect.  It validates the same
    continuation invariants as :func:`project_durable_completion_outcome` but
    deliberately accepts an open Todo so terminal mutation can be deferred
    until matching writeback and spend receipts exist.
    """

    return _project_durable_completion(
        todo=todo,
        expected_todo_id=expected_todo_id,
        existing_todo_ids=existing_todo_ids,
        require_done=False,
    )


def _project_durable_completion(
    *,
    todo: Mapping[str, Any],
    expected_todo_id: str,
    existing_todo_ids: Collection[str] | None,
    require_done: bool,
) -> dict[str, Any]:
    normalized_expected_todo_id = normalize_todo_id(expected_todo_id)
    if not normalized_expected_todo_id:
        raise ValueError("durable completion requires a public Todo id")
    durable_todo_id = normalize_todo_id(todo.get("todo_id"))
    if durable_todo_id != normalized_expected_todo_id:
        raise ValueError(
            "durable completion todo_id does not match the selected Todo"
        )
    if require_done and normalize_todo_status(todo.get("status")) != TODO_STATUS_DONE:
        raise ValueError(
            "durable completion requires the selected Todo to be durably done"
        )
    successor_todo_ids = normalize_todo_id_list(todo.get("successor_todo_ids"))
    no_followup = normalize_todo_no_followup(todo.get("no_followup"))
    completion_continuation = normalize_todo_completion_continuation(
        todo.get("completion_continuation")
    )
    raw_completion_recovery = todo.get("completion_recovery")
    completion_recovery = normalize_todo_completion_recovery(
        raw_completion_recovery
    )
    if raw_completion_recovery and completion_recovery is None:
        raise ValueError("durable completion_recovery is invalid")
    if require_done and completion_continuation is None:
        raise ValueError(
            "durable completion is missing completion_continuation; repair the "
            "Todo with `loopx todo complete`"
        )
    if completion_continuation is None:
        if no_followup is True:
            completion_continuation = TodoCompletionContinuation.NO_FOLLOWUP.value
        elif successor_todo_ids:
            completion_continuation = TodoCompletionContinuation.SUCCESSOR.value
        else:
            completion_continuation = TodoCompletionContinuation.ACTIVE_GOAL.value
    if no_followup is True:
        if successor_todo_ids:
            raise ValueError(
                "durable completion records both no_followup and "
                "successor_todo_ids"
            )
        if completion_continuation != TodoCompletionContinuation.NO_FOLLOWUP.value:
            raise ValueError(
                "durable completion_continuation contradicts no_followup"
            )
        return {
            "todo_id": normalized_expected_todo_id,
            "continuation": "no_followup",
        }
    if successor_todo_ids:
        if completion_recovery is not None:
            raise ValueError(
                "durable completion_recovery requires a no_followup continuation"
            )
        if completion_continuation != TodoCompletionContinuation.SUCCESSOR.value:
            raise ValueError(
                "durable completion_continuation contradicts successor_todo_ids"
            )
        if existing_todo_ids is not None:
            existing = set(existing_todo_ids)
            missing_todo_ids = [
                successor_todo_id
                for successor_todo_id in successor_todo_ids
                if successor_todo_id not in existing
            ]
            if missing_todo_ids:
                raise ValueError(
                    "durable completion declares missing successor Todo ids: "
                    + ", ".join(missing_todo_ids)
                )
        return {
            "todo_id": normalized_expected_todo_id,
            "continuation": "successor",
            "successor_todo_ids": successor_todo_ids,
        }
    if completion_continuation != TodoCompletionContinuation.ACTIVE_GOAL.value:
        raise ValueError(
            "durable completion_continuation requires matching no_followup or "
            "successor_todo_ids"
        )
    if completion_recovery is not None:
        raise ValueError(
            "durable completion_recovery requires a no_followup continuation"
        )
    return {
        "todo_id": normalized_expected_todo_id,
        "continuation": "active_goal",
    }
