from __future__ import annotations

from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result


def normalize_new_todo(text: str) -> str:
    compact = " ".join(text.strip().split())
    if not compact:
        raise ValueError("todo text must not be empty")
    return compact


def plan_todo_priority(todo: dict[str, Any], intent: dict[str, Any]) -> dict[str, Any]:
    try:
        result = effect_runtime_result("todo.priority.plan", {
            "schema_version": "todo_priority_request_v0", "todo": todo, "intent": intent,
        })
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, dict):
        raise RuntimeError("Todo priority plan must be an object")
    return result
