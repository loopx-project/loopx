from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from ..scheduler.execution_context import (
    SchedulerExecutionContextResolution,
    SchedulerRuntimeProfile,
    VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES,
    scheduler_runtime_profile_for_execution_context,
)
from ..todos.contract import normalize_todo_id, normalize_todo_replan_obligation_id
from .effect_program import SettlementStepKind, settlement_step_command

DEFAULT_SLOT_SPEND_SOURCE = "heartbeat"
VISIBLE_GOAL_SLOT_SPEND_SOURCE = "visible-goal"
VALID_SLOT_SPEND_SOURCES = {
    "heartbeat",
    "controller",
    "adapter",
    VISIBLE_GOAL_SLOT_SPEND_SOURCE,
}
TURN_SCOPED_SLOT_SPEND_SOURCES = frozenset(
    {DEFAULT_SLOT_SPEND_SOURCE, VISIBLE_GOAL_SLOT_SPEND_SOURCE}
)


def _typed_spend_source(command: str) -> str | None:
    tokens = shlex.split(command)
    try:
        source_index = tokens.index("--source")
    except ValueError:
        return None
    if source_index + 1 >= len(tokens):
        return None
    return tokens[source_index + 1]


def quota_spend_source_for_execution_context(
    value: Mapping[str, Any] | SchedulerExecutionContextResolution | None,
) -> str:
    profile = scheduler_runtime_profile_for_execution_context(value)
    if profile in VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES:
        return VISIBLE_GOAL_SLOT_SPEND_SOURCE
    return DEFAULT_SLOT_SPEND_SOURCE


def host_goal_turn_reentry_action(
    payload: Mapping[str, Any],
    settlement_plan: Mapping[str, Any] | None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ),
    turn_instance_id: str | None,
    typed_quota_guard: str,
) -> str | None:
    profile = scheduler_runtime_profile_for_execution_context(
        scheduler_execution_context
    )
    selected_value = payload.get("selected_todo")
    selected = selected_value if isinstance(selected_value, Mapping) else {}
    replan_value = payload.get("replan_action_packet")
    replan = replan_value if isinstance(replan_value, Mapping) else {}
    replan_obligation_id = normalize_todo_replan_obligation_id(
        replan.get("obligation_id")
    )
    has_settlement_binding = bool(
        normalize_todo_id(selected.get("todo_id"))
        or replan_obligation_id
    )
    requires_turn_reentry = profile in VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES
    if (
        requires_turn_reentry
        and has_settlement_binding
        and settlement_plan is None
        and turn_instance_id is None
    ):
        if profile in {
            SchedulerRuntimeProfile.CODEX_APP_SSH_VISIBLE,
            SchedulerRuntimeProfile.CODEX_APP_NATIVE_GOAL,
        }:
            return f"{typed_quota_guard} --begin-turn"
        # CLI/managed Goal hosts own their iteration identity, not an App
        # heartbeat receipt. Re-enter before exposing any unbound settlement.
        return (
            f"{typed_quota_guard} --turn-instance-id "
            "'<unique-work-iteration-id-reuse-on-retry>'"
        )
    return None


def build_quota_spend_action(
    goal_id: str,
    *,
    command_prefix: str = "loopx",
    scoped_cli_args: str,
    payload: Mapping[str, Any],
    settlement_plan: Mapping[str, Any] | None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ) = None,
) -> str:
    source = quota_spend_source_for_execution_context(scheduler_execution_context)
    typed_command = settlement_step_command(
        settlement_plan,
        SettlementStepKind.QUOTA_SPEND,
    )
    if typed_command:
        planned_source = _typed_spend_source(typed_command)
        if planned_source != source:
            raise ValueError(
                "settlement spend source mismatch: "
                f"plan={planned_source or 'missing'} runtime={source}"
            )
        return typed_command
    selected_value = payload.get("selected_todo")
    selected = selected_value if isinstance(selected_value, Mapping) else {}
    todo_id = normalize_todo_id(selected.get("todo_id"))
    todo_arg = (
        f" --todo-id {todo_id}"
        if todo_id and source == DEFAULT_SLOT_SPEND_SOURCE
        else ""
    )
    cli_prefix = command_prefix.strip() or "loopx"
    return (
        f"{cli_prefix} quota spend-slot --goal-id {goal_id} --slots 1 "
        f"--source {source} --execute{todo_arg}{scoped_cli_args}"
    )
