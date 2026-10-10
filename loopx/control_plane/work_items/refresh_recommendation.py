from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..agents.agent_lane_recommendation import build_agent_lane_next_action
from ..coordination.local_authority import read_canonical_todos_if_promoted, canonical_todo_summary_fields
from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..todos.active_state_todo_parser import parse_active_state_todos
from ..todos.contract import normalize_todo_id
from ...feedback import validate_local_control_text
from ...state_projection import active_state_next_action_entries
from ...rollout_event_log import load_rollout_events, rollout_event_log_path

REFRESH_RECOMMENDATION_REQUEST_SCHEMA_VERSION = "refresh_recommendation_request_v0"
REFRESH_RECOMMENDATION_SCHEMA_VERSION = "refresh_recommendation_v0"
DEFAULT_REFRESH_ACTION = (
    "inspect refreshed active goal state and continue the next bounded progress segment"
)
RECOMMENDED_ACTION_SOURCE_EXPLICIT = "explicit_arg"
RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO = "settlement_bound_todo"
RECOMMENDED_ACTION_SOURCE_AGENT_LANE_SELECTED_TODO = "agent_lane_selected_todo"
RECOMMENDED_ACTION_SOURCE_ACTIVE_NEXT_ACTION = "active_state_next_action"
RECOMMENDED_ACTION_SOURCE_AGENT_TODO_FALLBACK = "agent_todo_fallback"
RECOMMENDED_ACTION_SOURCE_DEFAULT = "default_refresh_action"


@dataclass(frozen=True)
class RefreshPlanningSource:
    state_text: str
    events: list[dict[str, Any]]
    todo_fields: dict[str, Any] | None
    canonical_snapshot: dict[str, Any] | None


def load_refresh_planning_source(
    runtime_root: Path,
    goal_id: str,
    state_path: Path,
    *,
    require_display: bool,
) -> RefreshPlanningSource:
    """Read one shared planning snapshot without repairing its display.

    Canonical Todos remain available without a Markdown display; steps bind to
    that task snapshot. Provider failures propagate rather than using prose.
    """
    events = load_rollout_events(rollout_event_log_path(runtime_root, goal_id))
    canonical = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
    fields = canonical_todo_summary_fields(
        canonical["todos"], rollout_events=events,
        goal_acceptance_contract=canonical.get("goal_acceptance_contract"),
        goal_acceptance_work_guards=canonical.get("goal_acceptance_work_guards"),
    ) if canonical is not None else None
    try:
        text = state_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        if fields is None or require_display:
            raise FileNotFoundError(f"state file does not exist: {state_path}") from None
        text = ""
    return RefreshPlanningSource(text, events, fields, canonical)


def _first_valid_action(values: list[str]) -> str | None:
    for value in values:
        try:
            validate_local_control_text("derived recommended_action", value)
        except ValueError:
            continue
        return value
    return None


def _agent_todo_summary(
    state_text: str,
    *,
    registry_goal: dict[str, Any] | None,
    state_path: Path | None,
    settlement_todo_id: str | None,
    rollout_events: list[dict[str, Any]] | None,
    todo_fields: dict[str, Any] | None,
) -> dict[str, Any] | None:
    preferred = {settlement_todo_id} if settlement_todo_id else None
    parsed = todo_fields if todo_fields is not None else parse_active_state_todos(
        state_text,
        goal=registry_goal,
        state_path=state_path,
        preferred_todo_ids=preferred,
        rollout_events=rollout_events,
        item_limit=None,
    )
    summary = parsed.get("agent_todos")
    return summary if isinstance(summary, dict) else None


def _exact_todo(
    summary: Mapping[str, Any] | None,
    todo_id: str | None,
) -> dict[str, Any] | None:
    normalized = normalize_todo_id(todo_id)
    if not normalized or not isinstance(summary, Mapping):
        return None
    items = summary.get("items")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping):
            continue
        if normalize_todo_id(item.get("todo_id")) != normalized:
            continue
        exact = dict(item)
        exact["selection_binding"] = "heartbeat_receipt"
        return exact
    return None


def _first_executable_todo(
    summary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(summary, Mapping):
        return None
    items = summary.get("first_executable_items")
    for item in items if isinstance(items, list) else []:
        if isinstance(item, Mapping):
            return dict(item)
    return None


def resolve_refresh_recommendation(
    state_text: str,
    *,
    explicit_action: str | None = None,
    agent_id: str | None = None,
    settlement_identity: Mapping[str, Any] | None = None,
    registry_goal: dict[str, Any] | None = None,
    state_path: Path | None = None,
    rollout_events: list[dict[str, Any]] | None = None,
    todo_fields: dict[str, Any] | None = None,
    next_step: str | None = None,
    next_step_basis: str | None = None,
    source_context: dict[str, Any] | None = None,
    runs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Adapt canonical Todo facts into the TS-owned refresh read reducer."""

    settlement_todo_id: str | None = None
    next_action_entries: list[str] = []
    shared_action: str | None = None
    summary: dict[str, Any] | None = None
    lane_candidate: dict[str, Any] | None = None
    if not explicit_action or next_step:
        settlement_todo_id = normalize_todo_id(
            settlement_identity.get("todo_id")
            if isinstance(settlement_identity, Mapping)
            else None
        )
        next_action_entries = active_state_next_action_entries(
            state_text,
            limit=None,
            text_limit=500,
        )
        shared_action = _first_valid_action(next_action_entries)
        summary = _agent_todo_summary(
            state_text,
            registry_goal=registry_goal,
            state_path=state_path,
            settlement_todo_id=settlement_todo_id,
            rollout_events=rollout_events,
            todo_fields=todo_fields,
        )
        lane_candidate = build_agent_lane_next_action(
            agent_identity={"agent_id": agent_id} if agent_id else None,
            agent_todo_summary=summary,
            capability_gate=None,
            active_next_action=next_action_entries,
            receipt_bound_todo_id=settlement_todo_id,
        )
    if next_step and not agent_id:
        raise ValueError("--next-action requires a registered --agent-id")
    if next_step and explicit_action and next_step != explicit_action:
        raise ValueError("--next-action and --recommended-action must agree when supplied together")
    try:
        result = effect_runtime_result(
            "work_item.refresh_recommendation.resolve",
            {
                "schema_version": REFRESH_RECOMMENDATION_REQUEST_SCHEMA_VERSION,
                # A step decorates the selected task; its text must not bypass
                # task selection via the unbound explicit recommendation arm.
                "explicit_action": None if next_step else explicit_action,
                "agent_id": agent_id,
                "settlement_identity": (
                    dict(settlement_identity)
                    if settlement_todo_id and isinstance(settlement_identity, Mapping)
                    else None
                ),
                "settlement_candidate": _exact_todo(summary, settlement_todo_id),
                "agent_lane_candidate": lane_candidate,
                "active_state_next_action": shared_action,
                "unscoped_agent_todo_fallback": _first_executable_todo(summary),
                "default_action": DEFAULT_REFRESH_ACTION,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping) or (
        result.get("schema_version") != REFRESH_RECOMMENDATION_SCHEMA_VERSION
    ):
        raise RuntimeError("TypeScript refresh recommendation shape mismatch")
    resolved = dict(result)
    if source_context is not None and agent_id and summary is not None:
        from .recommendation_source_io import lane_recommendation_context, latest_bound_recommendation
        task = _exact_todo(summary, resolved.get("todo_id"))
        selected = lane_candidate
        if resolved.get("recommended_action_source") == RECOMMENDED_ACTION_SOURCE_SETTLEMENT_BOUND_TODO:
            selected = task
        context = lane_recommendation_context(source_context, agent_id=agent_id,
            selected_todo=selected, task=task,
            prior_resolution=latest_bound_recommendation(runs or [], agent_id),
            write={"text": next_step, "expected_basis": next_step_basis} if next_step else None)
        if next_step:
            resolved = context["resolution"]
        elif isinstance(context.get("selected_todo"), dict) and context["selected_todo"].get("next_step"):
            # A derived refresh may reuse the bound step but never mint a new
            # step receipt or silently change its original read basis.
            resolved = {**resolved, "recommended_action": context["selected_todo"]["next_step"]}
    validate_local_control_text(
        "recommended_action",
        str(resolved.get("recommended_action") or ""),
    )
    return resolved


def derive_recommended_action_with_source(
    state_text: str,
    *,
    agent_id: str | None = None,
) -> tuple[str, str]:
    resolved = resolve_refresh_recommendation(state_text, agent_id=agent_id)
    return (
        str(resolved["recommended_action"]),
        str(resolved["recommended_action_source"]),
    )


def derive_recommended_action(state_text: str) -> str:
    return derive_recommended_action_with_source(state_text)[0]
