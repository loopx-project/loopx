"""Typed successor-intent adapter for terminal Todo lifecycle callers."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import effect_runtime_result
from .contract import normalize_todo_metadata_for_write

TODO_SUCCESSOR_DERIVATION_REQUEST_SCHEMA = "loopx_todo_successor_derivation_request_v0"
TODO_SUCCESSOR_DERIVATION_RESULT_SCHEMA = "loopx_todo_successor_derivation_result_v0"


def build_successor_intents(
    *,
    next_agent_todo: str | None,
    next_user_todo: str | None,
    next_user_task_class: str | None,
    next_claimed_by: str | None,
    next_task_class: str | None,
    next_action_kind: str | None,
    next_task_repository: str | None,
    next_required_capabilities: list[str] | None,
    next_continuation_policy: str | None,
    next_excluded_agents: list[str] | None,
) -> list[dict[str, Any]]:
    """Serialize caller intent; defaults and inheritance remain TypeScript-owned."""

    intents: list[dict[str, Any]] = []
    agent_options = (
        ("task_class", next_task_class),
        ("action_kind", next_action_kind),
        ("task_repository", next_task_repository),
        ("required_capabilities", next_required_capabilities),
        ("continuation_policy", next_continuation_policy),
        ("claimed_by", next_claimed_by),
        ("excluded_agents", next_excluded_agents),
    )
    if next_agent_todo or any(
        value is not None and value != "" and value != []
        for _key, value in agent_options
    ):
        intent: dict[str, Any] = {
            "role": "agent",
            "text": next_agent_todo or "",
        }
        intent.update(
            normalize_todo_metadata_for_write(
                {key: value for key, value in agent_options if value is not None}
            )
        )
        intents.append(intent)
    if next_user_todo:
        intent = {"role": "user", "text": next_user_todo}
        if next_user_task_class is not None:
            intent["task_class"] = next_user_task_class
        intents.append(intent)
    return intents


def derive_successor_proposals(
    *,
    command: str,
    predecessor: Mapping[str, Any],
    registered_agents: list[str],
    actor_agent_id: str | None,
    completion_policy: Mapping[str, Any] | None,
    successor_intents: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Call the TypeScript semantic owner and return provider-neutral proposals."""

    result = effect_runtime_result(
        "todo.successor.derive",
        {
            "schema_version": TODO_SUCCESSOR_DERIVATION_REQUEST_SCHEMA,
            "command": command,
            "predecessor": dict(predecessor),
            "registered_agents": registered_agents,
            "actor_agent_id": actor_agent_id,
            "completion_policy": (
                dict(completion_policy) if completion_policy is not None else None
            ),
            "successor_intents": successor_intents,
        },
    )
    if (
        not isinstance(result, Mapping)
        or result.get("schema_version") != TODO_SUCCESSOR_DERIVATION_RESULT_SCHEMA
        or result.get("status") != "derived"
        or not isinstance(result.get("successors"), list)
    ):
        payload = dict(result) if isinstance(result, Mapping) else {}
        raise ValueError(
            str(payload.get("reason") or "Todo successor derivation failed")
        )
    proposals: list[dict[str, Any]] = []
    for index, value in enumerate(result["successors"]):
        if not isinstance(value, Mapping):
            raise ValueError(f"derived successor {index} must be an object")
        proposals.append(dict(value))
    return proposals


__all__ = [
    "build_successor_intents",
    "derive_successor_proposals",
]
