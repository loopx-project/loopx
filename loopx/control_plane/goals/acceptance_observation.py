"""Bounded, read-only Goal acceptance observations; never completion authority."""

from __future__ import annotations

from typing import Any

from .acceptance_readback import configured_checks

from ..todos.user_gate import open_user_gate_todo_items
from ..todos.contract import normalize_todo_decision_scope
from ..runtime.public_safety import public_safe_compact_text, validate_public_safe_value
from .goal_vision_read_model import (
    acceptance_gaps_from_agent_vision,
    latest_agent_vision_from_runs,
)
from .goal_frontier.outcome_continuity import (
    acceptance_gaps_from_outcome_checkpoint,
    latest_outcome_vision_checkpoint_from_status_payload,
)

GOAL_ACCEPTANCE_OBSERVATION_SCHEMA_VERSION = "goal_acceptance_observation_projection_v0"
OBSERVATION_LIMIT = 12
# These are historical observations, not acceptance milestones or permissions.
OBSERVED_LIFECYCLE_FLAGS = frozenset(
    {
        "connected",
        "mapped",
        "refreshed",
        "adapter_inspected",
        "run_recorded",
        "reward_judged",
        "operator_approved",
        "controller_ready",
    }
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _rows(value: Any) -> list[dict[str, Any]]:
    return (
        [row for row in value if isinstance(row, dict)]
        if isinstance(value, list)
        else []
    )


def _strings(value: Any) -> list[str]:
    return (
        [item for item in value if isinstance(item, str)]
        if isinstance(value, list)
        else []
    )


def _text(value: Any) -> str | None:
    # Validate before truncation so a long safe prefix cannot conceal a secret.
    if not isinstance(value, str):
        return None
    try:
        validate_public_safe_value(value)
    except ValueError:
        return None
    return public_safe_compact_text(value, limit=420)


def build_goal_acceptance_observation(
    goal: dict[str, Any], item: dict[str, Any] | None
) -> dict[str, Any]:
    """Reuse acceptance rules over already-read runs, keeping per-agent lineage.

    The collected history and Todo projections are bounded; empty observations
    cannot establish complete Goal acceptance. No file, quota, or mutation IO.
    """
    item = _dict(item)
    asset = _dict(item.get("project_asset"))
    runs = _rows(goal.get("latest_runs"))
    goal_id = _text(goal.get("id")) or "unknown"
    runs = [run for run in runs if run.get("goal_id") in (None, goal.get("id"))]
    historical_progress: list[dict[str, Any]] = []
    seen_flags: set[str] = set()
    for source in runs:
        for flag in _strings(source.get("lifecycle_flags")):
            if flag not in OBSERVED_LIFECYCLE_FLAGS or flag in seen_flags:
                continue
            seen_flags.add(flag)
            historical_progress.append(
                {
                    "kind": flag,
                    "observed_at": _text(source.get("generated_at")),
                    "source": "run_history",
                    "evidence_refs": [_text(source.get("run_id"))]
                    if _text(source.get("run_id"))
                    else [],
                }
            )
    for flag in _strings(goal.get("lifecycle_flags")):
        if flag in OBSERVED_LIFECYCLE_FLAGS and flag not in seen_flags:
            seen_flags.add(flag)
            historical_progress.append(
                {
                    "kind": flag,
                    "observed_at": None,
                    "source": "legacy_lifecycle_flags",
                    "evidence_refs": [],
                }
            )

    agents = list(
        dict.fromkeys(
            str(
                _dict(run.get("agent_vision")).get("agent_id")
                or run.get("agent_id")
                or ""
            )
            for run in runs
            if isinstance(run.get("agent_vision"), dict)
        )
    )
    gaps: list[dict[str, Any]] = []
    for agent in agents:
        # Unknown agent identity is kept unknown, never assigned to another lane.
        agent_runs = [
            run
            for run in runs
            if str(
                _dict(run.get("agent_vision")).get("agent_id")
                or run.get("agent_id")
                or ""
            )
            == agent
        ]
        vision = latest_agent_vision_from_runs(
            agent_runs, goal_id=goal_id, agent_id=agent or None
        )
        patch = _dict(_dict(vision).get("vision_patch"))
        checkpoint = latest_outcome_vision_checkpoint_from_status_payload(
            {"run_history": {"goals": [goal]}}, goal_id=goal_id, agent_id=agent or None,
        )
        agent_gaps = acceptance_gaps_from_agent_vision(
            vision, goal_status=goal.get("status")
        ) + acceptance_gaps_from_outcome_checkpoint(vision, checkpoint)
        for gap in agent_gaps:
            gaps.append(
                {
                    "kind": _text(gap.get("kind")) or "acceptance_gap",
                    "owner": _text(gap.get("agent_id")),
                    "reason": _text(patch.get("replan_trigger_summary"))
                    if patch.get("replan_trigger_summary")
                    and gap.get("kind") == "vision_acceptance_gap"
                    else _text(gap.get("replan_trigger_summary")),
                    "evidence_required": _text(patch.get("acceptance_summary"))
                    if gap.get("kind") == "vision_acceptance_gap"
                    else _text(gap.get("acceptance_summary")),
                    "observed_at": _text(gap.get("generated_at")),
                    "source": gap.get("source") or "latest_agent_vision",
                    **{
                        key: gap[key]
                        for key in ("reason_code", "component_checks", "resolution_hint")
                        if key in gap
                    },
                }
            )
    guards: list[dict[str, Any]] = []
    user_group = _dict(item.get("user_todos") or asset.get("user_todos"))
    for todo in open_user_gate_todo_items(user_group):
        if todo.get("status", "open") not in {"open", "blocked"}:
            continue
        scope = normalize_todo_decision_scope(todo.get("decision_scope"))
        scope_label = (
            ":".join(str(scope[key]) for key in ("kind", "granularity", "scope_key"))
            if scope
            else None
        )
        guards.append(
            {
                "kind": "user_gate",
                "todo_id": _text(todo.get("todo_id")),
                "owner": None,
                "blocks_agent": _text(todo.get("blocks_agent")),
                "reason": _text(todo.get("title") or todo.get("text")),
                "evidence_required": _text(todo.get("resume_when")),
                "decision_scope": _text(scope_label),
            }
        )
    if item.get("waiting_on") in {"controller", "user_or_controller"} and not guards:
        guards.append(
            {
                "kind": "operator_gate",
                "todo_id": None,
                "blocks_agent": None,
                "owner": _text(asset.get("owner")),
                "reason": _text(item.get("operator_question") or asset.get("gate")),
                "evidence_required": _text(item.get("next_handoff_condition")),
                "decision_scope": None,
            }
        )
    sources_missing = []
    if not item:
        sources_missing.append("attention_queue")
    if not agents:
        sources_missing.append("agent_vision")
    if item.get("todo_projection_gap") or asset.get("todo_projection_gap"):
        sources_missing.append("todo_projection")
    if item.get("stale_latest_run_warning"):
        sources_missing.append("current_run")
    acceptance_contract = next((candidate for candidate in (
        item.get("goal_acceptance_contract"), asset.get("goal_acceptance_contract"),
        _dict(item.get("agent_todos")).get("goal_acceptance_contract"),
        _dict(asset.get("agent_todos")).get("goal_acceptance_contract"),
    ) if isinstance(candidate, dict) and (configured_checks(candidate) is not None or candidate.get("outcome_requirements") or candidate.get("goal_objective"))), None)
    return {
        "schema_version": GOAL_ACCEPTANCE_OBSERVATION_SCHEMA_VERSION,
        "goal_id": goal_id,
        "read_only": True,
        "acceptance_assessed": False,
        "coverage": "partial" if item or runs else "unavailable",
        "missing_sources": sources_missing,
        "truncated": len(gaps) > OBSERVATION_LIMIT or len(guards) > OBSERVATION_LIMIT,
        "historical_progress": historical_progress[:OBSERVATION_LIMIT],
        "acceptance_gaps": gaps[:OBSERVATION_LIMIT],
        "guards": guards[:OBSERVATION_LIMIT],
        "next_action": _text(
            asset.get("next_action") or item.get("recommended_action")
        ),
        "next_action_source": "attention_queue" if item else None,
        # Executed configured checks are narrower than independent Goal
        # acceptance. Keep acceptance_assessed false and preserve their basis.
        **({"goal_acceptance_contract": acceptance_contract} if acceptance_contract is not None else {}),
    }


def attach_goal_acceptance_observations(
    payload: dict[str, Any], *, history: dict[str, Any]
) -> None:
    items = _rows(_dict(payload.get("attention_queue")).get("items"))
    sources = {goal.get("id"): goal for goal in _rows(history.get("goals"))}
    for goal in _rows(_dict(payload.get("run_history")).get("goals")):
        item = next(
            (row for row in items if row.get("goal_id") == goal.get("id")), None
        )
        goal["acceptance_observation"] = build_goal_acceptance_observation(
            {
                **sources.get(goal.get("id"), goal),
                "lifecycle_flags": goal.get("lifecycle_flags"),
            },
            item,
        )
