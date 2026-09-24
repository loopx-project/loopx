from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..quota.blocked_retry import active_turn_retry_for_run
from ..work_items.delivery_outcome import (
    MATERIAL_DELIVERY_OUTCOMES,
    PROGRESS_DELIVERY_OUTCOMES,
)
from ..work_items.progress_result import PROGRESS_OBSERVATION_SCHEMA_VERSION, ProgressResultClass
from ..work_items.autonomous_replan_ack import (
    AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK,
    autonomous_replan_ack_recorded,
)
from ..work_items.autonomous_replan_obligation import run_history_agent_id
from .time import now_utc_iso

GOAL_SEMANTIC_HISTORY_SCHEMA_VERSION = "goal_semantic_history_v0"
SEMANTIC_CONTEXT_RUN_FIELDS = (
    "latest_agent_vision_run",
    "latest_vision_checkpoint_run",
    "latest_outcome_vision_checkpoint_run",
    "latest_autonomous_replan_ack_run",
    "latest_replan_ack_feedback_run",
    "latest_material_milestone_run",
    "latest_evidence_delivery_run",
)
SEMANTIC_CONTEXT_RUN_PAYLOAD_FIELDS = {
    "latest_agent_vision_run": (
        "generated_at",
        "agent_id",
        "agent_vision",
    ),
    "latest_vision_checkpoint_run": (
        "generated_at",
        "agent_id",
        "vision_checkpoint",
    ),
    "latest_outcome_vision_checkpoint_run": (
        "generated_at",
        "agent_id",
        "agent_vision",
        "vision_checkpoint",
    ),
    "latest_autonomous_replan_ack_run": (
        "generated_at",
        "agent_id",
        "autonomous_replan_ack",
    ),
    "latest_replan_ack_feedback_run": (
        "generated_at",
        "agent_id",
        "replan_ack_feedback",
    ),
    "latest_material_milestone_run": (
        "generated_at",
        "agent_id",
        "classification",
        "delivery_outcome",
    ),
    "latest_evidence_delivery_run": (
        "generated_at",
        "run_id",
        "goal_id",
        "agent_id",
        "todo_id",
        "delivery_outcome",
        "progress_observation",
    ),
}
OWNER_CORRECTION_RUN_PAYLOAD_FIELDS = (
    "generated_at",
    "agent_id",
    "classification",
    "human_reward",
)
OUTCOME_VISION_CHECKPOINT_TRIGGER_KINDS = {
    "autonomous_replan_recorded",
    "material_delivery_outcome",
}


RunCompactor = Callable[[dict[str, Any]], dict[str, Any]]


def _agent_id_for_run(run: dict[str, Any]) -> str:
    checkpoint = (
        run.get("vision_checkpoint")
        if isinstance(run.get("vision_checkpoint"), dict)
        else {}
    )
    vision = (
        run.get("agent_vision") if isinstance(run.get("agent_vision"), dict) else {}
    )
    return str(
        run.get("agent_id")
        or checkpoint.get("agent_id")
        or vision.get("agent_id")
        or ""
    ).strip()


def _run_retires_agent_vision(run: dict[str, Any]) -> bool:
    checkpoint = run.get("vision_checkpoint")
    return bool(
        isinstance(checkpoint, dict)
        and checkpoint.get("satisfied") is True
        and str(checkpoint.get("decision") or "").strip() == "retired_or_superseded"
    )


def _run_has_outcome_vision_checkpoint(run: dict[str, Any]) -> bool:
    checkpoint = run.get("vision_checkpoint")
    if not isinstance(checkpoint, dict):
        return False
    return any(
        isinstance(trigger, dict)
        and str(trigger.get("kind") or "").strip()
        in OUTCOME_VISION_CHECKPOINT_TRIGGER_KINDS
        for trigger in (checkpoint.get("triggers") or [])
    )


def goal_semantic_history_from_runs(
    runs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Select time-bounded control semantics from newest-first run history.

    The result grows with participating agents and currently waiting Todos,
    not heartbeat count. Recent drill-down rows remain a separate strictly
    bounded list.
    """

    contexts: dict[str, dict[str, Any]] = {}
    resolved_agent_vision: set[str] = set()
    latest_owner_correction_run: dict[str, Any] | None = None
    active_blocked_retry_runs: list[dict[str, Any]] = []
    seen_retry_todos: set[tuple[str, str]] = set()
    observed_at = now_utc_iso()

    for run in runs:
        if latest_owner_correction_run is None and isinstance(
            run.get("human_reward"), dict
        ):
            latest_owner_correction_run = run

        agent_id = _agent_id_for_run(run)
        if not agent_id:
            continue
        todo_id = str(run.get("todo_id") or "").strip()
        classification = str(run.get("classification") or "")
        if todo_id and not classification.startswith(("quota_slot_", "quota_scheduler_")):
            key = (agent_id, todo_id)
            if key not in seen_retry_todos:
                seen_retry_todos.add(key)
                if (
                    active_turn_retry_for_run(run, observed_at=observed_at)
                    is not None
                ):
                    active_blocked_retry_runs.append(run)
        context = contexts.setdefault(agent_id, {"agent_id": agent_id})

        checkpoint = run.get("vision_checkpoint")
        if "latest_vision_checkpoint_run" not in context and isinstance(
            checkpoint, dict
        ):
            context["latest_vision_checkpoint_run"] = run

        if agent_id not in resolved_agent_vision:
            if _run_retires_agent_vision(run):
                context["vision_retired_at"] = run.get("generated_at")
                resolved_agent_vision.add(agent_id)
            elif isinstance(run.get("agent_vision"), dict):
                context["latest_agent_vision_run"] = run
                resolved_agent_vision.add(agent_id)

        if (
            "latest_outcome_vision_checkpoint_run" not in context
            and _run_has_outcome_vision_checkpoint(run)
        ):
            context["latest_outcome_vision_checkpoint_run"] = run

        if (
            "latest_autonomous_replan_ack_run" not in context
            and autonomous_replan_ack_recorded(run)
        ):
            context["latest_autonomous_replan_ack_run"] = run

        if "latest_replan_ack_feedback_run" not in context and isinstance(
            run.get("replan_ack_feedback"), dict
        ):
            context["latest_replan_ack_feedback_run"] = run

        if (
            "latest_material_milestone_run" not in context
            and str(run.get("delivery_outcome") or "").strip()
            in MATERIAL_DELIVERY_OUTCOMES
        ):
            context["latest_material_milestone_run"] = run

        observation = run.get("progress_observation")
        if (
            "latest_evidence_delivery_run" not in context
            and run.get("delivery_outcome") in PROGRESS_DELIVERY_OUTCOMES
            and isinstance(observation, dict)
            and observation.get("schema_version") == PROGRESS_OBSERVATION_SCHEMA_VERSION
            and observation.get("result_class") == ProgressResultClass.ADVANCED.value
            and run.get("todo_id")
            and observation.get("work_item_id") == run.get("todo_id")
            and isinstance(observation.get("evidence_ids"), list)
            and observation["evidence_ids"]
            and all(
                isinstance(ref, str) and ref.strip()
                for ref in observation["evidence_ids"]
            )
        ):
            context["latest_evidence_delivery_run"] = run

    semantic_history: dict[str, Any] = {
        "schema_version": GOAL_SEMANTIC_HISTORY_SCHEMA_VERSION,
        "active_blocked_retry_runs": active_blocked_retry_runs,
        "agents": [
            context
            for context in contexts.values()
            if any(field in context for field in SEMANTIC_CONTEXT_RUN_FIELDS)
            or "vision_retired_at" in context
        ],
    }
    if latest_owner_correction_run is not None:
        semantic_history["latest_owner_correction_run"] = latest_owner_correction_run
    return semantic_history


def compact_goal_semantic_history(
    value: Any,
    *,
    compact_run: RunCompactor,
) -> dict[str, Any] | None:
    """Compact selected semantic runs through the canonical run compactor."""

    if not isinstance(value, dict):
        return None
    agents: list[dict[str, Any]] = []
    for raw_context in value.get("agents") or []:
        if not isinstance(raw_context, dict):
            continue
        agent_id = str(raw_context.get("agent_id") or "").strip()
        if not agent_id:
            continue
        context: dict[str, Any] = {"agent_id": agent_id}
        retired_at = str(raw_context.get("vision_retired_at") or "").strip()
        if retired_at:
            context["vision_retired_at"] = retired_at
        for field in SEMANTIC_CONTEXT_RUN_FIELDS:
            run = raw_context.get(field)
            if isinstance(run, dict):
                compacted_run = compact_run(run)
                context[field] = {
                    key: compacted_run[key]
                    for key in SEMANTIC_CONTEXT_RUN_PAYLOAD_FIELDS[field]
                    if key in compacted_run
                }
        if len(context) > 1:
            agents.append(context)

    compact: dict[str, Any] = {
        "schema_version": GOAL_SEMANTIC_HISTORY_SCHEMA_VERSION,
        "agents": agents,
    }
    compact["active_blocked_retry_runs"] = [
        compact_run(run)
        for run in value.get("active_blocked_retry_runs") or []
        if isinstance(run, dict)
    ]
    owner_correction_run = value.get("latest_owner_correction_run")
    if isinstance(owner_correction_run, dict):
        compacted_run = compact_run(owner_correction_run)
        compact["latest_owner_correction_run"] = {
            key: compacted_run[key]
            for key in OWNER_CORRECTION_RUN_PAYLOAD_FIELDS
            if key in compacted_run
        }
    return compact if agents or "latest_owner_correction_run" in compact else None


def latest_runs_with_agent_context(
    runs: list[dict[str, Any]],
    *,
    limit: int,
    agent_lane_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return a strict recent-run drill-down window.

    Durable control semantics live in ``goal_semantic_history_v0`` instead of
    expanding this display list beyond its advertised limit.

    ``agent_lane_id`` adds one lane-scoped decision window next to the
    goal-wide display window. Agent-lane replan triggers count only that lane's
    material runs since its own last replan ACK, so a Goal with several active
    lanes can interleave more than ``limit`` peer records between two rows of a
    single lane and hide that lane's own threshold. The requesting lane keeps
    the same ``AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK`` budget a single-lane Goal
    would have, and the goal-wide rows stay in place.
    """

    bounded = max(0, limit)
    goal_window = list(runs[:bounded])
    if not agent_lane_id:
        return goal_window
    lane_present = False
    lane_positions: list[int] = []
    for position, run in enumerate(runs):
        attributed = run_history_agent_id(run)
        if attributed == agent_lane_id:
            lane_present = True
        elif attributed is not None:
            continue
        lane_positions.append(position)
    if not lane_present:
        return goal_window
    return [
        runs[position]
        for position in sorted(
            {
                *range(min(bounded, len(runs))),
                *lane_positions[:AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK],
            }
        )
    ]
