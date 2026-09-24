"""Python adapter for the TypeScript-owned vision-checkpoint transition."""

from __future__ import annotations

import json
import re
from typing import Any, NoReturn

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result

VISION_CHECKPOINT_SCHEMA_VERSION = "vision_checkpoint_v0"
VISION_REFRESH_REQUEST_SCHEMA = "loopx_vision_refresh_request_v0"
VISION_REFRESH_PREPARED_SCHEMA_VERSION = "vision_refresh_prepared_v0"
VISION_CHECKPOINT_REQUEST_SCHEMA = VISION_REFRESH_REQUEST_SCHEMA
GOAL_VISION_BUDGET_ERROR = "vision_budget_exceeded"
_VISION_BUDGET_MESSAGE = re.compile(
    r"^vision_budget_exceeded: (?P<field>.+) uses (?P<used>\d+) chars; "
    r"limit is (?P<limit>\d+)"
)
_VISION_BUDGET_SUGGESTION_MARKER = "; suggested compact value: "


class GoalVisionBudgetError(ValueError):
    """A typed compatibility error for TypeScript-owned Vision budgets."""

    def __init__(
        self,
        *,
        field: str,
        used: int,
        limit: int,
        suggestion: str | None = None,
        message: str | None = None,
    ) -> None:
        self.field = field
        self.used = used
        self.limit = limit
        self.suggestion = suggestion
        super().__init__(
            message
            or f"{GOAL_VISION_BUDGET_ERROR}: {field} uses {used} chars; "
            f"limit is {limit}; shorten one or more vision fields before retrying"
        )


def _raise_rejection(exc: EffectRuntimeRejected) -> NoReturn:
    if exc.diagnostic_code == GOAL_VISION_BUDGET_ERROR:
        message = str(exc)
        matched = _VISION_BUDGET_MESSAGE.match(message)
        if matched:
            suggestion = None
            suggestion_prefix = matched.group(0) + _VISION_BUDGET_SUGGESTION_MARKER
            if message.startswith(suggestion_prefix):
                try:
                    candidate = json.loads(message[len(suggestion_prefix) :])
                except json.JSONDecodeError:
                    candidate = None
                if isinstance(candidate, str):
                    suggestion = candidate
            raise GoalVisionBudgetError(
                field=matched.group("field"),
                used=int(matched.group("used")),
                limit=int(matched.group("limit")),
                suggestion=suggestion,
                message=message,
            ) from None
    raise ValueError(str(exc)) from None


def prepare_vision_refresh(
    packet: dict[str, Any],
    *,
    goal_id: str,
    agent_id: str | None,
    existing_agent_vision: dict[str, Any] | None,
    merge_patch: bool,
    require_path_delta_for_durable_change: bool,
) -> dict[str, Any]:
    """Run the TS-owned Vision preflight before semantic-replan qualification."""

    try:
        result = effect_runtime_result(
            "goal.vision_checkpoint.evaluate",
            {
                "schema_version": VISION_REFRESH_REQUEST_SCHEMA,
                "phase": "prepare",
                "goal_id": goal_id,
                "agent_id": agent_id,
                "agent_vision_packet": packet,
                "existing_agent_vision": existing_agent_vision,
                "merge_patch": bool(merge_patch),
                "require_path_delta_for_durable_change": bool(
                    require_path_delta_for_durable_change
                ),
            },
        )
    except EffectRuntimeRejected as exc:
        _raise_rejection(exc)
    if not isinstance(result, dict):
        raise RuntimeError("TypeScript Vision prepared result must be an object")
    agent_vision = result.get("agent_vision")
    if (
        result.get("schema_version") != VISION_REFRESH_PREPARED_SCHEMA_VERSION
        or not isinstance(agent_vision, dict)
        or agent_vision.get("schema_version") != "goal_vision_replan_contract_v0"
        or not isinstance(agent_vision.get("goal_id"), str)
        or not isinstance(agent_vision.get("agent_id"), str)
        or not isinstance(agent_vision.get("state"), str)
        or not isinstance(agent_vision.get("vision_patch"), dict)
        or not isinstance(agent_vision.get("todo_delta"), list)
        or not isinstance(agent_vision.get("vision_budget"), dict)
        or not isinstance(agent_vision.get("validation"), dict)
    ):
        raise RuntimeError("TypeScript Vision prepared result shape mismatch")
    return agent_vision


def build_vision_checkpoint(
    *,
    agent_id: str | None,
    agent_vision: dict[str, Any] | None,
    existing_agent_vision: dict[str, Any] | None,
    vision_unchanged_reason: str | None,
    delivery_outcome: str | None,
    active_state_next_action_update: dict[str, Any] | None,
    delivery_boundary: str | None = None,
    todo_id: str | None = None,
    completion_todo_id: str | None = None,
    autonomous_replan_recorded: bool = False,
    blocked_retry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Finalize the TS-owned Vision transaction after replan qualification."""

    try:
        result = effect_runtime_result(
            "goal.vision_checkpoint.evaluate",
            {
                "schema_version": VISION_REFRESH_REQUEST_SCHEMA,
                "phase": "finalize",
                "agent_id": agent_id,
                "agent_vision": agent_vision,
                "existing_agent_vision": existing_agent_vision,
                "vision_unchanged_reason": vision_unchanged_reason,
                "delivery_outcome": delivery_outcome,
                "active_state_next_action_would_update": bool(
                    active_state_next_action_update
                    and active_state_next_action_update.get("would_update")
                ),
                "delivery_boundary": delivery_boundary,
                "todo_id": todo_id,
                "completion_todo_id": completion_todo_id,
                "autonomous_replan_recorded": bool(autonomous_replan_recorded),
                "blocked_retry": blocked_retry,
            },
        )
    except EffectRuntimeRejected as exc:
        _raise_rejection(exc)
    if not isinstance(result, dict):
        raise RuntimeError("TypeScript vision checkpoint result must be an object")
    if (
        result.get("schema_version") != VISION_CHECKPOINT_SCHEMA_VERSION
        or result.get("decision")
        not in {
            "patched",
            "unchanged_with_reason",
            "missing_required",
            "not_required",
        }
        or not isinstance(result.get("required"), bool)
        or not isinstance(result.get("satisfied"), bool)
        or not isinstance(result.get("triggers"), list)
        or not isinstance(result.get("delivery_boundary"), str)
    ):
        raise RuntimeError("TypeScript vision checkpoint result shape mismatch")
    continuity_basis = result.get("continuity_basis")
    if continuity_basis is not None and not (
        isinstance(continuity_basis, dict)
        and continuity_basis.get("kind") == "existing_vision_unchanged"
        and isinstance(continuity_basis.get("vision_generated_at"), str)
        and continuity_basis["vision_generated_at"].strip()
    ):
        raise RuntimeError("TypeScript vision checkpoint result shape mismatch")
    return result
