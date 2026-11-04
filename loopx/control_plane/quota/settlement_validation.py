"""Todo validation authority checks for quota settlement."""

from __future__ import annotations

from typing import Any, cast


from ..effect_runtime import effect_runtime_result
from ..todos.completion_validation_projection import (
    pending_completion_validation_todo,
)
from ..todos.contract import normalize_todo_id


def qualified_open_todo_semantic_checkpoint(
    delivery_run: dict[str, Any] | None, *, goal_id: str,
    todo_id: str, agent_id: str | None,
) -> bool:
    """Transport prepared or exact receipted facts to the shared TS qualifier."""
    if not isinstance(delivery_run, dict) or agent_id is None:
        return False
    identity = delivery_run.get("settlement_identity")
    if not isinstance(identity, dict):
        return False
    turn_id = identity.get("turn_instance_id")
    if not isinstance(turn_id, str) or not turn_id.strip():
        return False
    return effect_runtime_result("settlement.open_todo_semantic_checkpoint", {
        "identity": {"goal_id": goal_id, "agent_id": agent_id, "todo_id": todo_id,
                     "turn_instance_id": turn_id},
        "writeback": delivery_run,
    }) is True


def _is_qualified_semantic_replan_writeback(
    delivery_run: dict[str, Any] | None,
    *,
    todo_id: str,
) -> bool:
    """Recognize the durable proof that an open Todo changed its path.

    The refresh path writes this acknowledgement only after typed replan
    qualification.  Spend may trust it only through the exact Turn settlement
    readback; callers must not pass an unrelated latest run.
    """

    if not isinstance(delivery_run, dict):
        return False
    settlement_identity = delivery_run.get("settlement_identity")
    if (
        normalize_todo_id(delivery_run.get("todo_id")) != todo_id
        or not isinstance(settlement_identity, dict)
        or normalize_todo_id(settlement_identity.get("todo_id")) != todo_id
    ):
        return False
    ack = delivery_run.get("autonomous_replan_ack")
    if (
        not isinstance(ack, dict)
        or ack.get("schema_version") != "autonomous_replan_ack_v0"
        or ack.get("recorded") is not True
    ):
        return False
    semantic_delta = ack.get("semantic_delta")
    if (
        isinstance(semantic_delta, dict)
        and semantic_delta.get("schema_version") == "replan_semantic_delta_v0"
        and semantic_delta.get("accepted") is True
    ):
        return True
    delta_contract = ack.get("delta_contract")
    return bool(
        isinstance(delta_contract, dict)
        and delta_contract.get("schema_version") == "repair_delta_contract_v0"
        and delta_contract.get("delta_present") is True
    )


def _is_qualified_in_flight_writeback(
    delivery_run: dict[str, Any] | None,
    *,
    goal_id: str,
    todo_id: str,
    agent_id: str | None,
) -> bool:
    """An open Todo may settle validated progress without terminal validation.

    The delivery run is the exact Turn-bound writeback supplied by settlement
    readback.  Its vision checkpoint owns the accepted continuation boundary;
    the completion validator still owns the Todo's eventual terminal state.
    """

    if not isinstance(delivery_run, dict):
        return False
    identity = delivery_run.get("settlement_identity")
    checkpoint = delivery_run.get("vision_checkpoint")
    if agent_id is None:
        return False
    if (
        delivery_run.get("goal_id") != goal_id
        or normalize_todo_id(delivery_run.get("todo_id")) != todo_id
        or delivery_run.get("agent_id") != agent_id
        or not isinstance(identity, dict)
        or identity.get("goal_id") != goal_id
        or normalize_todo_id(identity.get("todo_id")) != todo_id
        or identity.get("agent_id") != agent_id
        or delivery_run.get("delivery_outcome") != "outcome_progress"
        or not isinstance(checkpoint, dict)
        or checkpoint.get("schema_version") != "vision_checkpoint_v0"
        or checkpoint.get("agent_id") != agent_id
        or checkpoint.get("delivery_boundary") != "in_flight_continuation"
        or checkpoint.get("satisfied") is not True
    ):
        return False
    triggers = checkpoint.get("triggers")
    if not isinstance(triggers, list):
        return False
    return any(
        isinstance(trigger, dict)
        and trigger.get("kind") == "in_flight_continuation"
        and normalize_todo_id(trigger.get("todo_id")) == todo_id
        for trigger in triggers
    )


def completion_validation_spend_error(
    status_payload: dict[str, Any],
    *,
    goal_id: str,
    todo_id: str | None,
    agent_id: str | None,
    selected_todo: dict[str, Any] | None,
    delivery_run: dict[str, Any] | None = None,
) -> str | None:
    """Explain why one requested settlement still lacks validation authority."""

    normalized_todo_id = normalize_todo_id(todo_id)
    if not normalized_todo_id:
        return None
    if _is_qualified_semantic_replan_writeback(
        delivery_run,
        todo_id=normalized_todo_id,
    ) or _is_qualified_in_flight_writeback(
        delivery_run,
        goal_id=goal_id,
        todo_id=normalized_todo_id,
        agent_id=agent_id,
    ):
        return None
    summaries: list[dict[str, Any]] = []
    if (
        selected_todo
        and normalize_todo_id(selected_todo.get("todo_id")) == normalized_todo_id
    ):
        summaries.append({"items": [selected_todo]})
    queue = status_payload.get("attention_queue")
    queue_items = queue.get("items") if isinstance(queue, dict) else []
    queue_item = next(
        (
            item
            for item in cast(list[Any], queue_items) if isinstance(queue_items, list)
            if isinstance(item, dict) and item.get("goal_id") == goal_id
        ),
        {},
    )
    project_asset = (
        cast(dict[str, Any], queue_item.get("project_asset"))
        if isinstance(queue_item.get("project_asset"), dict)
        else {}
    )
    for owner in (queue_item, project_asset):
        summary = owner.get("agent_todos")
        if isinstance(summary, dict):
            summaries.append(summary)
    if any(
        pending_completion_validation_todo(
            summary,
            todo_id=normalized_todo_id,
            agent_id=agent_id,
        )
        is not None
        for summary in summaries
    ):
        if qualified_open_todo_semantic_checkpoint(
            delivery_run, goal_id=goal_id, todo_id=normalized_todo_id, agent_id=agent_id,
        ):
            return None
        return (
            "quota spend is blocked until controller-declared completion "
            f"validation durably completes todo {normalized_todo_id}"
        )
    return None
