"""Python compatibility facade for TypeScript-owned Todo completion state."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
from typing import Any, Mapping

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


TODO_COMPLETION_STATE_REQUEST_SCHEMA = "loopx_todo_completion_state_request_v0"
TODO_COMPLETION_STATE_RESULT_SCHEMA = "loopx_todo_completion_state_result_v0"


class TodoCompletionContinuation(str, Enum):
    """Stable Python import compatibility for the persisted wire values."""

    ACTIVE_GOAL = "active_goal"
    SUCCESSOR = "successor"
    NO_FOLLOWUP = "no_followup"


class TodoCompletionRecovery(str, Enum):
    """Stable Python import compatibility for the persisted wire value."""

    SAME_TURN_TERMINAL_CLOSEOUT = "same_turn_terminal_closeout"
    LIFECYCLE_REENTRY_TERMINAL_CLOSEOUT = "lifecycle_reentry_terminal_closeout"


def _string_value(value: Any) -> str:
    return str(value or "")


def _no_followup_value(value: Any) -> bool | str:
    return value if isinstance(value, bool) else _string_value(value)


def _result(method: str, params: dict[str, Any]) -> Mapping[str, Any]:
    try:
        result = effect_runtime_result(method, params)
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript Todo completion state result must be an object")
    if result.get("schema_version") != TODO_COMPLETION_STATE_RESULT_SCHEMA:
        raise RuntimeError("TypeScript Todo completion state result shape mismatch")
    return result


@lru_cache(maxsize=64)
def _normalize_cached(kind: str, value: bool | str) -> bool | str | None:
    result = _result(
        "todo.completion_state.normalize",
        {
            "schema_version": TODO_COMPLETION_STATE_REQUEST_SCHEMA,
            "kind": kind,
            "value": value,
        },
    )
    normalized = result.get("value")
    if kind == "no_followup":
        if normalized is not None and not isinstance(normalized, bool):
            raise RuntimeError("TypeScript no_followup normalization shape mismatch")
    elif normalized is not None and not isinstance(normalized, str):
        raise RuntimeError(
            "TypeScript completion metadata normalization shape mismatch"
        )
    return normalized


def normalize_todo_no_followup(value: Any) -> bool | None:
    normalized = _normalize_cached("no_followup", _no_followup_value(value))
    return normalized if isinstance(normalized, bool) else None


def normalize_todo_completion_continuation(value: Any) -> str | None:
    normalized = _normalize_cached("continuation", _string_value(value))
    return normalized if isinstance(normalized, str) else None


def normalize_todo_completion_recovery(value: Any) -> str | None:
    normalized = _normalize_cached("recovery", _string_value(value))
    return normalized if isinstance(normalized, str) else None


def require_todo_completion_metadata(key: str, value: Any) -> str | None:
    result = _result(
        "todo.completion_state.require_metadata",
        {
            "schema_version": TODO_COMPLETION_STATE_REQUEST_SCHEMA,
            "key": key,
            "value": _string_value(value),
        },
    )
    normalized = result.get("value")
    if normalized is not None and not isinstance(normalized, str):
        raise RuntimeError("TypeScript completion metadata result shape mismatch")
    return normalized
