from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from typing import Any

from ...control_plane.goals.goal_frontier.terminal import (
    GOAL_TERMINAL_STATE_SCHEMA_VERSION,
)
from ...control_plane.goals.goal_vision_state import goal_vision_state_is_closed
from ...control_plane.todos.contract import normalize_todo_replan_obligation_id
from ...control_plane.work_items.autonomous_replan_ack import (
    normalize_projected_autonomous_replan_ack,
)


STAGE_COMPLETION_RECEIPT_SCHEMA = "periodic_report_stage_completion_receipt_v0"
_VISION_SCHEMA = "goal_vision_replan_contract_v0"
_CHECKPOINT_SCHEMA = "vision_checkpoint_v0"
_FRONTIER_SCHEMA = "goal_frontier_projection_v0"
_SUCCESSOR_TRIGGER = "vision_successor_required"
_MATERIAL_OUTCOMES = {"outcome_progress", "primary_goal_outcome"}
_SUCCESSOR_OUTCOMES = {"fresh_vision_path_outcome", "new_runnable_successor"}


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _text(value: object) -> str:
    return str(value or "").strip()


def _vision_identity(vision: Mapping[str, Any]) -> tuple[str, str] | None:
    if vision.get("schema_version") != _VISION_SCHEMA:
        return None
    agent_id = _text(vision.get("agent_id"))
    generated_at = _text(vision.get("generated_at"))
    patch = _mapping(vision.get("vision_patch"))
    if not agent_id or not generated_at or not _text(patch.get("acceptance_summary")):
        return None
    return agent_id, generated_at


def _material_checkpoint_satisfied(checkpoint: Mapping[str, Any]) -> bool:
    if (
        checkpoint.get("schema_version") != _CHECKPOINT_SCHEMA
        or checkpoint.get("satisfied") is not True
        or checkpoint.get("decision") not in {"patched", "unchanged_with_reason"}
    ):
        return False
    return any(
        trigger.get("kind") == "material_delivery_outcome"
        and _text(trigger.get("delivery_outcome")) in _MATERIAL_OUTCOMES
        for trigger in checkpoint.get("triggers") or []
        if isinstance(trigger, Mapping)
    )


def _validated_terminal_state(value: Mapping[str, Any]) -> bool:
    return bool(
        value.get("schema_version") == GOAL_TERMINAL_STATE_SCHEMA_VERSION
        and value.get("kind") == "no_followup"
        and value.get("derived") is True
        and value.get("source") == "validated_goal_closure"
    )


def _successor_frontier_owned(value: Mapping[str, Any]) -> bool:
    if value.get("schema_version") != _FRONTIER_SCHEMA:
        return False
    frontier = _mapping(value.get("remaining_advancement_frontier"))
    current_count = int(frontier.get("current_agent_claimed_advancement_count") or 0)
    unclaimed_count = int(frontier.get("unclaimed_advancement_count") or 0)
    advancement_count = current_count + unclaimed_count
    blocking_gate_count = value.get("blocking_handoff_gate_count")
    return bool(
        value.get("replan_required") is False
        and (
            advancement_count > 0
            or (type(blocking_gate_count) is int and blocking_gate_count > 0)
        )
    )


def _stage_identity(*, agent_id: str, closed_revision: str, frontier_identity: str) -> str:
    raw = f"{agent_id}:{closed_revision}:{frontier_identity}".encode()
    return "stage-" + hashlib.sha256(raw).hexdigest()[:16]


def derive_periodic_report_stage_completion(
    *,
    closed_vision: Mapping[str, Any],
    outcome_checkpoint: Mapping[str, Any],
    goal_terminal_state: Mapping[str, Any] | None = None,
    replan_obligation: Mapping[str, Any] | None = None,
    replan_ack: Mapping[str, Any] | None = None,
    successor_vision: Mapping[str, Any] | None = None,
    successor_frontier: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Derive a reportable success-path boundary from existing durable facts.

    Ordinary Todo completion and generic replan are intentionally absent. The
    capability consumes only durable Vision/frontier facts and creates no
    separate Stage lifecycle.
    """

    closed_identity = _vision_identity(closed_vision)
    if (
        closed_identity is None
        or _text(closed_vision.get("state")) != "vision_closed"
        or not goal_vision_state_is_closed(closed_vision.get("state"))
        or not _material_checkpoint_satisfied(outcome_checkpoint)
    ):
        return None
    agent_id, closed_revision = closed_identity

    transition: str
    frontier_identity: str
    evidence: list[str]
    if _validated_terminal_state(_mapping(goal_terminal_state)):
        transition = "goal_terminal"
        frontier_identity = "validated-goal-terminal"
        evidence = [closed_revision, GOAL_TERMINAL_STATE_SCHEMA_VERSION]
        completed_at = closed_revision
    else:
        obligation = _mapping(replan_obligation)
        triggers = [
            trigger
            for trigger in obligation.get("triggers") or []
            if isinstance(trigger, Mapping)
        ]
        if not any(trigger.get("kind") == _SUCCESSOR_TRIGGER for trigger in triggers):
            return None
        obligation_id = normalize_todo_replan_obligation_id(obligation.get("obligation_id"))
        # Vision successors carry the accepted obligation identity; an explicit
        # monitor frontier remains the version for existing stage receipts.
        frontier_identity = _text(obligation.get("frontier_identity")) or obligation_id or ""
        obligation_agent = _text(obligation.get("agent_id") or obligation.get("claimed_by"))
        if obligation_agent and obligation_agent != agent_id:
            return None
        normalized_ack = normalize_projected_autonomous_replan_ack(dict(replan_ack or {}))
        semantic_delta = _mapping(
            normalized_ack.get("semantic_delta") if normalized_ack else None
        )
        ack_obligation_id = normalize_todo_replan_obligation_id(semantic_delta.get("obligation_id"))
        ack_frontier_identity = _text(_mapping(normalized_ack).get("frontier_identity")) or ack_obligation_id or ""
        ack_agent = _text(
            normalized_ack.get("agent_id") or normalized_ack.get("claimed_by")
            if normalized_ack
            else None
        )
        if ack_agent and ack_agent != agent_id:
            return None
        ack_outcomes = {_text(value) for value in semantic_delta.get("outcomes") or []}
        ack_trigger_kinds = {
            _text(value) for value in semantic_delta.get("trigger_kinds") or []
        }
        successor_identity = _vision_identity(_mapping(successor_vision))
        if (
            not frontier_identity
            or normalized_ack is None
            or ack_frontier_identity != frontier_identity
            or (obligation_id is not None and ack_obligation_id != obligation_id)
            or _SUCCESSOR_TRIGGER not in ack_trigger_kinds
            or not ack_outcomes.intersection(_SUCCESSOR_OUTCOMES)
            or successor_identity is None
            or successor_identity[0] != agent_id
            or goal_vision_state_is_closed(_mapping(successor_vision).get("state"))
            or not _successor_frontier_owned(_mapping(successor_frontier))
        ):
            return None
        transition = "successor_frontier_settled"
        completed_at = successor_identity[1]
        evidence = [
            closed_revision,
            frontier_identity,
            successor_identity[1],
            _text(semantic_delta.get("obligation_id")),
        ]

    stage_identity = _stage_identity(
        agent_id=agent_id,
        closed_revision=closed_revision,
        frontier_identity=frontier_identity,
    )
    return {
        "schema_version": STAGE_COMPLETION_RECEIPT_SCHEMA,
        "stage_identity": stage_identity,
        "agent_id": agent_id,
        "closed_vision_revision": closed_revision,
        "frontier_identity": frontier_identity,
        "transition": transition,
        "completed_at": completed_at,
        "acceptance": "validated",
        "outcome_checkpoint_satisfied": True,
        "durable_writeback_required": True,
        "evidence_refs": [value for value in evidence if value],
    }


def derive_periodic_report_stage_completion_from_runs(
    *,
    latest_runs: Sequence[Mapping[str, Any]],
    agent_id: str,
    goal_frontier_projection: Mapping[str, Any],
    settled_replan_obligation: Mapping[str, Any] | None = None,
    settled_replan_ack: Mapping[str, Any] | None = None,
    source_run_path: str | None = None,
) -> dict[str, Any] | None:
    """Derive the boundary from durable runtime history and current frontier."""

    normalized_agent_id = _text(agent_id)
    if not normalized_agent_id:
        return None
    current_vision: Mapping[str, Any] | None = None
    successor_vision: Mapping[str, Any] | None = None
    successor_run: Mapping[str, Any] | None = None
    closed_vision: Mapping[str, Any] | None = None
    outcome_checkpoint: Mapping[str, Any] | None = None
    for raw_run in latest_runs:
        run = _mapping(raw_run)
        vision = dict(_mapping(run.get("agent_vision")))
        if not _text(vision.get("generated_at")) and _text(run.get("generated_at")):
            vision["generated_at"] = _text(run.get("generated_at"))
        if _text(vision.get("agent_id")) != normalized_agent_id:
            continue
        if current_vision is None:
            current_vision = vision
        if successor_vision is None and not goal_vision_state_is_closed(
            vision.get("state")
        ):
            successor_vision = vision
            successor_run = run
        checkpoint = _mapping(run.get("vision_checkpoint"))
        if (
            closed_vision is None
            and goal_vision_state_is_closed(vision.get("state"))
            and _material_checkpoint_satisfied(checkpoint)
        ):
            closed_vision = vision
            outcome_checkpoint = checkpoint
            break
    if closed_vision is None or outcome_checkpoint is None:
        return None
    terminal_state = _mapping(goal_frontier_projection.get("terminal_state"))
    if _validated_terminal_state(terminal_state):
        if current_vision is not closed_vision:
            # Do not skip a newer Vision to reuse an older material closure.
            return None
        return derive_periodic_report_stage_completion(
            closed_vision=closed_vision,
            outcome_checkpoint=outcome_checkpoint,
            goal_terminal_state=terminal_state,
        )
    if successor_vision is None:
        return None
    if (
        source_run_path is not None
        and _mapping(successor_run).get("json_path") != source_run_path
    ):
        return None
    ack = _mapping(settled_replan_ack)
    if not _text(ack.get("frontier_identity")):
        accepted_delta = _mapping(ack.get("semantic_delta"))
        # The selected Vision and accepted ACK must occupy the same durable run.
        run = _mapping(successor_run)
        run_ack = normalize_projected_autonomous_replan_ack(
            dict(_mapping(run.get("autonomous_replan_ack")))
        )
        if (
            run_ack is None
            or run_ack.get("semantic_delta") != accepted_delta
            or _text(run.get("agent_id")) not in {"", normalized_agent_id}
            or _text(run_ack.get("agent_id") or run_ack.get("claimed_by"))
            not in {"", normalized_agent_id}
        ):
            return None
    return derive_periodic_report_stage_completion(
        closed_vision=closed_vision,
        outcome_checkpoint=outcome_checkpoint,
        replan_obligation=_mapping(settled_replan_obligation),
        replan_ack=_mapping(settled_replan_ack),
        successor_vision=successor_vision,
        successor_frontier=goal_frontier_projection,
    )


def project_periodic_report_stage_completion_event_details(
    receipt: Mapping[str, Any] | None,
) -> dict[str, object]:
    """Flatten one derived receipt into public-safe rollout event details."""

    value = _mapping(receipt)
    if value.get("schema_version") != STAGE_COMPLETION_RECEIPT_SCHEMA:
        return {}
    required_text = {
        "stage_identity": _text(value.get("stage_identity")),
        "closed_vision_revision": _text(value.get("closed_vision_revision")),
        "frontier_identity": _text(value.get("frontier_identity")),
        "stage_transition": _text(value.get("transition")),
        "stage_acceptance": _text(value.get("acceptance")),
        "stage_completed_at": _text(value.get("completed_at")),
    }
    if (
        not all(required_text.values())
        or required_text["stage_transition"]
        not in {"goal_terminal", "successor_frontier_settled"}
        or required_text["stage_acceptance"] != "validated"
        or value.get("outcome_checkpoint_satisfied") is not True
        or value.get("durable_writeback_required") is not True
    ):
        return {}
    return {
        "stage_completion_schema": STAGE_COMPLETION_RECEIPT_SCHEMA,
        **required_text,
        "stage_outcome_checkpoint_satisfied": True,
        "stage_durable_writeback_required": True,
    }


__all__ = [
    "STAGE_COMPLETION_RECEIPT_SCHEMA",
    "derive_periodic_report_stage_completion",
    "derive_periodic_report_stage_completion_from_runs",
    "project_periodic_report_stage_completion_event_details",
]
