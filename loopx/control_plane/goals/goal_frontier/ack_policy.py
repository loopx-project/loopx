from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ...todos.contract import (
    TODO_TASK_CLASS_ADVANCEMENT,
    normalize_todo_claimed_by,
    normalize_todo_id,
    normalize_todo_replan_obligation_id,
    replan_successor_semantic_binding,
)
from ...todos.todo_semantics import todo_item_is_actionable_open, todo_item_task_class
from ...work_items.progress_observation import (
    replan_obligation_trigger_checkpoints,
    replan_obligation_trigger_kinds,
    required_semantic_outcomes,
)
from ...work_items.autonomous_replan_obligation import ensure_replan_novelty_policy
from .long_todo_chain import (
    LONG_TODO_CHAIN_TRIGGER,
    long_todo_chain_successor_checkpoints,
)


def autonomous_replan_ack_has_frontier_delta(ack: dict[str, Any] | None) -> bool:
    if not isinstance(ack, dict) or ack.get("recorded") is not True:
        return False
    semantic_delta = ack.get("semantic_delta")
    return bool(
        isinstance(semantic_delta, dict)
        and semantic_delta.get("accepted") is True
        and semantic_delta.get("outcomes")
    )


def autonomous_replan_ack_satisfies_obligation(
    ack: dict[str, Any] | None,
    *,
    replan_obligation: dict[str, Any] | None,
    acceptance_gaps: list[dict[str, Any]] | None,
    todo_succession_gap_open: bool = False,
) -> bool:
    """Reject ACKs that miss the obligation's typed semantic outcome."""

    if not autonomous_replan_ack_has_frontier_delta(ack):
        return False
    semantic_delta = ack.get("semantic_delta") if isinstance(ack, dict) else {}
    obligation_id = str((replan_obligation or {}).get("obligation_id") or "").strip()
    acknowledged_obligation_id = str(
        semantic_delta.get("obligation_id")
        if isinstance(semantic_delta, dict)
        else ""
    ).strip()
    if not obligation_id or acknowledged_obligation_id != obligation_id:
        return False
    outcomes = {
        str(item or "").strip()
        for item in (
            semantic_delta.get("outcomes") or []
            if isinstance(semantic_delta, dict)
            else []
        )
        if str(item or "").strip()
    }
    required = set(required_semantic_outcomes(replan_obligation or {}))
    if todo_succession_gap_open:
        outcomes.intersection_update({"new_runnable_successor"})
    return bool(outcomes & required)


def replan_successor_transition_ack(
    agent_todo_summary: dict[str, Any] | None,
    *,
    agent_id: str | None,
    replan_obligation: dict[str, Any] | None,
    agent_todo_items: Iterable[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Project an exact runnable-successor Todo as the replan receipt.

    The Todo row is the durable fact.  No prose classification and no second
    ACK command are involved.  A receipt applies only while the bound Todo is
    still open, advancement-class, runnable, and owned by the same agent.
    """

    obligation_id = normalize_todo_replan_obligation_id(
        (replan_obligation or {}).get("obligation_id")
    )
    safe_agent_id = normalize_todo_claimed_by(agent_id)
    if not obligation_id or not safe_agent_id:
        return None
    if agent_todo_items is None:
        source_items = (
            agent_todo_summary.get("first_executable_items")
            if isinstance(agent_todo_summary, dict)
            and isinstance(agent_todo_summary.get("first_executable_items"), list)
            else []
        )
    else:
        source_items = [
            item for item in agent_todo_items if isinstance(item, dict)
        ]
    trigger_kinds = replan_obligation_trigger_kinds(replan_obligation or {})
    candidates = [item for item in source_items
        if todo_item_is_actionable_open(item)
        and todo_item_task_class(item) == TODO_TASK_CLASS_ADVANCEMENT
        and normalize_todo_claimed_by(item.get("claimed_by")) == safe_agent_id
        and normalize_todo_replan_obligation_id(item.get("replan_obligation_id"))
        and normalize_todo_id(item.get("todo_id"))
        and replan_successor_semantic_binding(action_kind=item.get("action_kind"),
            target_key=item.get("target_key"), explore_result_node_refs=item.get("explore_result_node_refs"))]
    if not candidates:
        return None
    trigger_checkpoints: list[dict[str, str]] | None = None
    eligible_ids = {item["todo_id"] for item in candidates if item["replan_obligation_id"] == obligation_id}
    if LONG_TODO_CHAIN_TRIGGER in trigger_kinds:
        source_checkpoint = long_todo_chain_successor_checkpoints(
            source_items,
            agent_id=safe_agent_id,
            triggers=(replan_obligation or {}).get("triggers") or [],
            obligation_id=obligation_id,
            candidates=[{"todo_id": item["todo_id"], "updated_at": item.get("updated_at"),
                "origin_obligation_id": item["replan_obligation_id"]} for item in candidates],
            frontier_revision_index=(agent_todo_summary or {}).get(
                "advancement_frontier_revision_index"
            ),
        )
        if source_checkpoint is None:
            return None
        trigger_checkpoints = source_checkpoint["trigger_checkpoints"]
        eligible_ids = set()
        origins = {item["todo_id"]: item["replan_obligation_id"] for item in candidates}
        for binding in source_checkpoint["bindings"]:
            if binding["kind"] == "exact":
                eligible_ids.add(binding["todo_id"])
            elif binding["kind"] == "predecessor":
                prior = ensure_replan_novelty_policy({**(replan_obligation or {}), "triggers": [
                    {**trigger, "frontier_revision": binding["frontier_revision"],
                     "obligation_identity_revision": binding["obligation_identity_revision"]}
                    for trigger in (replan_obligation or {}).get("triggers") or []]})
                if prior["obligation_id"] == origins[binding["todo_id"]]:
                    eligible_ids.add(binding["todo_id"])
    successor = next((item for item in candidates if item["todo_id"] in eligible_ids), None)
    if successor is None:
        return None
    successor_todo_id = normalize_todo_id(successor.get("todo_id"))
    successor_binding = replan_successor_semantic_binding(
        action_kind=successor.get("action_kind"),
        target_key=successor.get("target_key"),
        explore_result_node_refs=successor.get("explore_result_node_refs"),
    )
    if successor_binding is None:
        return None
    if trigger_checkpoints is None:
        trigger_checkpoints = replan_obligation_trigger_checkpoints(replan_obligation or {})
    semantic_delta = {
        "schema_version": "replan_semantic_delta_v0",
        "accepted": True,
        "outcomes": ["new_runnable_successor"],
        "satisfying_outcomes": ["new_runnable_successor"],
        "required_any_of": required_semantic_outcomes(replan_obligation or {}),
        "trigger_kinds": trigger_kinds,
        "trigger_checkpoints": trigger_checkpoints,
        "obligation_id": obligation_id,
        "successor_todo_id": successor_todo_id,
        "successor_origin_obligation_id": successor["replan_obligation_id"],
        "successor_binding": successor_binding,
        "reason": (
            "an exact or source-proven predecessor Todo transition created a runnable "
            "successor"
        ),
    }
    ack = {
        "schema_version": "autonomous_replan_ack_v0",
        "recorded": True,
        "source": "todo_replan_successor_transition",
        "generated_at": successor.get("updated_at"),
        "semantic_delta": semantic_delta,
    }
    frontier_identity = str(
        (replan_obligation or {}).get("frontier_identity") or ""
    ).strip()
    if frontier_identity:
        ack["frontier_identity"] = frontier_identity
    return ack
