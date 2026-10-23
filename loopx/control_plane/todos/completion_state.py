"""Python compatibility facade for TypeScript-owned Todo completion state."""

from __future__ import annotations

from enum import Enum
from functools import lru_cache
import json
from pathlib import Path
from typing import Any



TODO_COMPLETION_STATE_REQUEST_SCHEMA = "loopx_todo_completion_state_request_v0"
TODO_COMPLETION_STATE_RESULT_SCHEMA = "loopx_todo_completion_state_result_v0"
_VALUE_CODEC = json.loads(Path(__file__).with_name("completion_value_codec.json").read_text(encoding="utf-8"))


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




@lru_cache(maxsize=64)
def _normalize_cached(kind: str, value: bool | str) -> bool | str | None:
    if kind == "no_followup":
        if isinstance(value, bool):
            return value
        candidate = value.strip(_VALUE_CODEC["ecmascript_trim"]).lower()
        if candidate in _VALUE_CODEC["true_tokens"]:
            return True
        if candidate in _VALUE_CODEC["false_tokens"]:
            return False
        return None
    candidate = str(value).strip(_VALUE_CODEC["ecmascript_trim"]).lower()
    vocabulary = TodoCompletionContinuation if kind == "continuation" else TodoCompletionRecovery
    return candidate if candidate in {item.value for item in vocabulary} else None


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
    raw = _string_value(value)
    if key == "completion_continuation":
        normalized = _normalize_cached("continuation", raw)
        if normalized is None:
            raise ValueError("completion_continuation must be one of: active_goal, successor, no_followup")
    else:
        normalized = _normalize_cached("recovery", raw)
        if normalized is None and raw != "":
            raise ValueError("completion_recovery must be same_turn_terminal_closeout or lifecycle_reentry_terminal_closeout")
    return normalized
