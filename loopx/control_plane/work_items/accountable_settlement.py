from __future__ import annotations

from collections.abc import Mapping

from ...turn_identity import normalize_turn_instance_id
from ..quota.settlement import (
    SettlementPlan,
    build_codex_app_settlement_plan,
    build_turn_scoped_cli_settlement_plan,
)
from ..scheduler.execution_context import (
    APP_HEARTBEAT_SETTLEMENT_RUNTIME_PROFILES,
    SchedulerRuntimeProfile,
    VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES,
)


def build_accountable_work_item_settlement_plan(
    *,
    runtime_profile: SchedulerRuntimeProfile | None,
    goal_id: str,
    agent_id: str,
    todo_id: str | None,
    replan_obligation_id: str | None,
    scoped_cli_args: str,
    lifecycle_actor_args: str,
    turn_instance_id: str | None,
    delivery_boundary: str | None = None,
    command_prefix: str = "loopx",
    goal_ref: Mapping[str, object] | None = None,
) -> SettlementPlan | None:
    if runtime_profile in APP_HEARTBEAT_SETTLEMENT_RUNTIME_PROFILES:
        normalized_turn_instance_id = normalize_turn_instance_id(turn_instance_id)
        return build_codex_app_settlement_plan(
            goal_id=goal_id,
            agent_id=agent_id,
            command_prefix=command_prefix,
            todo_id=todo_id,
            replan_obligation_id=replan_obligation_id,
            scoped_cli_args=scoped_cli_args,
            lifecycle_actor_args=lifecycle_actor_args,
            turn_instance_id_ref=normalized_turn_instance_id,
            delivery_boundary=delivery_boundary,
            goal_ref=goal_ref,
        )
    if runtime_profile in VISIBLE_GOAL_SETTLEMENT_RUNTIME_PROFILES:
        normalized_turn_instance_id = normalize_turn_instance_id(turn_instance_id)
        if normalized_turn_instance_id is None:
            return None
        return build_turn_scoped_cli_settlement_plan(
            goal_id=goal_id,
            agent_id=agent_id,
            command_prefix=command_prefix,
            todo_id=todo_id,
            replan_obligation_id=replan_obligation_id,
            scoped_cli_args=scoped_cli_args,
            lifecycle_actor_args=lifecycle_actor_args,
            turn_instance_id=normalized_turn_instance_id,
            delivery_boundary=delivery_boundary,
            quota_spend_source="visible-goal",
            goal_ref=goal_ref,
        )
    if runtime_profile is not SchedulerRuntimeProfile.GENERIC_CLI_AGENT_LOOP:
        return None
    normalized_turn_instance_id = normalize_turn_instance_id(turn_instance_id)
    if normalized_turn_instance_id is None:
        return None
    return build_turn_scoped_cli_settlement_plan(
        goal_id=goal_id,
        agent_id=agent_id,
        command_prefix=command_prefix,
        todo_id=todo_id,
        replan_obligation_id=replan_obligation_id,
        scoped_cli_args=scoped_cli_args,
        lifecycle_actor_args=lifecycle_actor_args,
        turn_instance_id=normalized_turn_instance_id,
        delivery_boundary=delivery_boundary,
        goal_ref=goal_ref,
    )
