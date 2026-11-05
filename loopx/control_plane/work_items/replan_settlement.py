"""Python adapter for the TypeScript-owned replan settlement projection."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..effect_runtime import EffectRuntimeRejected, effect_runtime_result
from ..coordination.coordination_state_contract_generated import (
    REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
    REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_RESULT_SCHEMA,
    REPLAN_SETTLEMENT_REQUEST_SCHEMA as GENERATED_REPLAN_SETTLEMENT_REQUEST_SCHEMA,
    REPLAN_SETTLEMENT_RESULT_SCHEMA,
)

REPLAN_SETTLEMENT_REQUEST_SCHEMA = GENERATED_REPLAN_SETTLEMENT_REQUEST_SCHEMA
REPLAN_SETTLEMENT_CONTRACT_SCHEMA = REPLAN_SETTLEMENT_RESULT_SCHEMA
TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA = REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_REQUEST_SCHEMA
TODO_LIFECYCLE_REENTRY_SCHEMA = REPLAN_SETTLEMENT_LIFECYCLE_REENTRY_RESULT_SCHEMA


def project_todo_lifecycle_settlement_reentry(
    *,
    goal_id: str,
    runtime_root: str | None = None,
    triggers: list[dict[str, Any]],
    lifecycle_actor_args: list[str],
    quota_scoped_args: list[str],
    quota_guard_argv: list[str] | None = None,
) -> dict[str, Any]:
    """Return TS-owned exact actions for a terminal Todo succession gap."""

    try:
        request = {
            "schema_version": TODO_LIFECYCLE_REENTRY_REQUEST_SCHEMA,
            "goal_id": goal_id,
            "triggers": triggers,
            "lifecycle_actor_args": lifecycle_actor_args,
            "quota_scoped_args": quota_scoped_args,
        }
        if runtime_root is not None:
            request["runtime_root"] = runtime_root
        if quota_guard_argv is not None:
            request["quota_guard_argv"] = quota_guard_argv
        result = effect_runtime_result(
            "work_item.replan_settlement.reentry",
            request,
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript Todo lifecycle reentry result must be an object")
    projected_triggers = result.get("triggers")
    next_cli_actions = result.get("next_cli_actions")
    expected_triggers = triggers[:3]
    if (
        result.get("schema_version") != TODO_LIFECYCLE_REENTRY_SCHEMA
        or result.get("resolution_mode") != "todo_lifecycle_settlement"
        or not isinstance(projected_triggers, list)
        or not projected_triggers
        or any(
            not isinstance(trigger, Mapping)
            or trigger.get("kind") != "completed_advancement_without_successor"
            or not isinstance(trigger.get("todo_id"), str)
            or not trigger.get("todo_id")
            or not isinstance(trigger.get("completion_turn_key"), str)
            or not trigger.get("completion_turn_key")
            or trigger.get("completion_identity_source")
            not in {"turn_settlement", "unscoped_completion"}
            for trigger in projected_triggers
        )
        or any(
            dict(projected).get("kind") != expected.get("kind")
            or dict(projected).get("todo_id") != expected.get("todo_id")
            or (
                expected.get("completion_turn_key") is not None
                and dict(projected).get("completion_turn_key")
                != expected.get("completion_turn_key")
            )
            or (
                expected.get("completion_turn_key") is None
                and not str(
                    dict(projected).get("completion_turn_key") or ""
                ).startswith("local_completion_")
            )
            or dict(projected).get("completion_identity_source")
            != (
                "unscoped_completion"
                if expected.get("completion_turn_key") is None
                or str(expected.get("completion_turn_key") or "").startswith(
                    "local_completion_"
                )
                else "turn_settlement"
            )
            for projected, expected in zip(
                projected_triggers,
                expected_triggers,
                strict=True,
            )
        )
        or not isinstance(next_cli_actions, list)
        or not next_cli_actions
        or any(not isinstance(action, str) or not action for action in next_cli_actions)
    ):
        raise RuntimeError("TypeScript Todo lifecycle reentry result shape mismatch")
    return dict(result)


def project_replan_settlement_contract(
    *,
    selected_todo_id: str | None,
    semantic_replan_obligation_id: str,
) -> dict[str, Any]:
    """Return the one causal binding for a semantic replan writeback."""

    try:
        result = effect_runtime_result(
            "work_item.replan_settlement.project",
            {
                "schema_version": REPLAN_SETTLEMENT_REQUEST_SCHEMA,
                "selected_todo_id": selected_todo_id,
                "semantic_replan_obligation_id": semantic_replan_obligation_id,
            },
        )
    except EffectRuntimeRejected as exc:
        raise ValueError(str(exc)) from None
    if not isinstance(result, Mapping):
        raise RuntimeError("TypeScript replan settlement result must be an object")
    binding = result.get("settlement_binding")
    obligation = result.get("semantic_obligation")
    if (
        result.get("schema_version") != REPLAN_SETTLEMENT_CONTRACT_SCHEMA
        or result.get("single_binding_required") is not True
        or not isinstance(binding, Mapping)
        or binding.get("kind") not in {"todo", "autonomous_replan"}
        or not isinstance(binding.get("id"), str)
        or binding.get("cli_argument") not in {"--todo-id", "--replan-obligation-id"}
        or not isinstance(obligation, Mapping)
        or obligation.get("kind") != "autonomous_replan"
        or not isinstance(obligation.get("id"), str)
        or not isinstance(obligation.get("settlement_bound"), bool)
        or obligation.get("discharge")
        not in {"direct_settlement", "todo_bound_writeback"}
    ):
        raise RuntimeError("TypeScript replan settlement result shape mismatch")
    return dict(result)
