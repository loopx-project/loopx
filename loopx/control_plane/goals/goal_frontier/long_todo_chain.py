"""Edge-triggered long Todo-chain observations for goal-frontier replanning."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...todos.frontier_revision import (
    TODO_FRONTIER_REVISION_SCHEMA_VERSION,
    frontier_source_facts,
)
from ...effect_runtime import effect_runtime_result


LONG_TODO_CHAIN_TRIGGER = "long_todo_chain"
LONG_TODO_CHAIN_FRONTIER_REVISION_SCHEMA_VERSION = (
    TODO_FRONTIER_REVISION_SCHEMA_VERSION
)


@dataclass(frozen=True)
class LongTodoChainObservation:
    trigger_count: int
    count_kind: str
    selectable_open_count: int
    selectable_advancement_count: int
    current_agent_claimed_open_count: int
    current_agent_claimed_advancement_count: int
    unclaimed_advancement_count: int
    threshold: int
    agent_id: str | None
    frontier_revision: str | None
    frontier_revision_complete: bool
    trigger: dict[str, Any]
    frontier_owned_identity: str | None = None


@dataclass(frozen=True)
class LongTodoChainAckDecision:
    acknowledged: bool
    rearmed_after_obligation_id: str | None = None


def long_todo_chain_successor_checkpoints(
    source_items: list[dict[str, Any]],
    *,
    agent_id: str | None,
    triggers: list[dict[str, Any]],
    obligation_id: str,
    candidates: list[dict[str, Any]],
    frontier_revision_index: Any = None,
) -> dict[str, Any] | None:
    """Resolve successor checkpoints and fresh causal bindings in one TS read."""

    needs_predecessor_source = any(row["origin_obligation_id"] != obligation_id for row in candidates)
    result: dict[str, Any] | None = effect_runtime_result("todo.frontier_revision.project", {
        "schema_version": "todo_frontier_revision_request_v0",
        "operation": "successor_checkpoints", "agent_id": agent_id,
        "triggers": triggers,
        "obligation_id": obligation_id, "candidates": candidates,
        "index": frontier_revision_index,
        "rows": frontier_source_facts(source_items) if needs_predecessor_source or not isinstance(frontier_revision_index, dict) else None,
    })["source_checkpoint"]
    return result


def evaluate_long_todo_chain(
    *, agent_todo_summary: dict[str, Any] | None,
    agent_counts: dict[str, int], frontier_counts: dict[str, int],
    agent_id: str | None, agent_todo_source_items: list[dict[str, Any]] | None = None,
    latest_replan_ack: dict[str, Any] | None = None,
) -> tuple[LongTodoChainObservation | None, LongTodoChainAckDecision | None]:
    """One typed observation + checkpoint qualification, not two rule RPCs."""
    result = effect_runtime_result("goal.long_todo_chain.evaluate", {
        "schema_version": "long_todo_chain_request_v0", "operation": "observe",
        "summary": agent_todo_summary, "agent_counts": agent_counts,
        "frontier_counts": frontier_counts, "agent_id": agent_id,
        "rows": (
            None if isinstance((agent_todo_summary or {}).get("advancement_frontier_revision_index"), dict)
            else frontier_source_facts(agent_todo_source_items)
        ),
        "ack": latest_replan_ack,
    })
    observation = result["observation"]
    decision = result["decision"]
    return (
        LongTodoChainObservation(**observation) if observation is not None else None,
        LongTodoChainAckDecision(**decision) if decision is not None else None,
    )
