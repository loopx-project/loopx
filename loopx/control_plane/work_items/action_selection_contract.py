from __future__ import annotations

import shlex
from collections.abc import Mapping
from typing import Any

from ..agents.capability_gate import runtime_capabilities_for_cli_projection
from ..todos.contract import normalize_todo_id
from ..quota.effective_action import EffectiveAction


def render_cli_command_prefix(*, runtime_root: str | None = None, registry_path: str | None = None) -> str:
    return ("loopx" + (f" --registry {shlex.quote(str(registry_path))}" if registry_path else "")
        + (f" --runtime-root {shlex.quote(str(runtime_root))}" if runtime_root else ""))


def action_portfolio_requires_explicit_selection(
    payload: Mapping[str, Any],
) -> bool:
    portfolio = payload.get("action_portfolio")
    if not isinstance(portfolio, Mapping):
        return False
    policy = portfolio.get("selection_policy")
    return bool(
        isinstance(policy, Mapping)
        and policy.get("requires_explicit_turn_binding") is True
    )


def action_portfolio_selection_command_template(
    payload: Mapping[str, Any],
    *,
    scoped_cli_args: str,
    scheduler_args: str,
    turn_instance_id: str | None,
    runtime_root: str | None = None,
    registry_path: str | None = None,
) -> str | None:
    if not action_portfolio_requires_explicit_selection(payload):
        return None
    goal_id = str(payload.get("goal_id") or "").strip()
    if not goal_id:
        return None
    turn_arg = (
        f" --turn-instance-id {shlex.quote(turn_instance_id)}"
        if turn_instance_id
        else ""
    )
    command_prefix = render_cli_command_prefix(
        runtime_root=runtime_root, registry_path=registry_path,
    )
    return (
        f"{command_prefix} --format json quota should-run"
        f" --goal-id {shlex.quote(goal_id)}"
        " --todo-id {todo_id}"
        f"{scoped_cli_args}{scheduler_args}{turn_arg}"
    )


def apply_action_selection_agent_gate(
    channel: dict[str, Any],
    payload: Mapping[str, Any],
) -> None:
    if not action_portfolio_requires_explicit_selection(payload):
        return
    portfolio = payload.get("action_portfolio")
    suggested_actions = (
        portfolio.get("suggested_actions")
        if isinstance(portfolio, Mapping)
        else None
    )
    channel.update(
        {
            "selection_required": True,
            "delivery_allowed": False,
            "primary_action": (
                "choose any current eligible Todo; recommendations are non-binding"
            ),
            "suggested_action_count": (
                len(suggested_actions)
                if isinstance(suggested_actions, list)
                else 0
            ),
        }
    )


def apply_action_selection_cli_gate(
    channel: dict[str, Any],
    payload: Mapping[str, Any],
) -> None:
    if not action_portfolio_requires_explicit_selection(payload):
        return
    actions = channel.get("next_cli_actions")
    command_template = (
        actions[0]
        if isinstance(actions, list)
        and len(actions) == 1
        and isinstance(actions[0], str)
        else None
    )
    channel["next_cli_actions"] = []
    goal_id = str(payload.get("goal_id") or "").strip()
    command_args_template: str | None = None
    route_prefix = "loopx --format json"
    if command_template:
        try:
            command_tokens = shlex.split(command_template)
        except ValueError:
            command_tokens = []
        try:
            format_index = command_tokens.index("--format")
        except ValueError:
            format_index = -1
        if (
            format_index >= 1
            and command_tokens[format_index : format_index + 2]
            == ["--format", "json"]
            and command_tokens[0] == "loopx"
        ):
            command_args_template = shlex.join(command_tokens[format_index + 2 :])
            route_prefix = shlex.join(command_tokens[: format_index + 2])
        else:
            route_prefix = "loopx --format json"
    if command_args_template is None:
        raise RuntimeError(
            "explicit action selection requires one typed quota command template"
        )
    channel.update(
        {
            "selection_required": True,
            "selection_policy_ref": "$.action_portfolio.selection_policy",
            "spend_policy": (
                "no delivery or quota spend until one eligible action is "
                "explicitly bound by rerunning quota in this turn"
            ),
            "selection_command": {
                "schema_version": "action_selection_cli_command_v1",
                "route_prefix": route_prefix,
                "command_args_template": command_args_template,
                "candidate_discovery_args": (
                    "todo list"
                    f" --goal-id {shlex.quote(goal_id)}"
                    " --role agent --status open --limit 50"
                ),
            },
        }
    )


def delivery_spend_allowed(
    payload: Mapping[str, Any],
    spend_after_validation: bool,
) -> bool:
    return spend_after_validation and not action_portfolio_requires_explicit_selection(
        payload
    )


def current_action_selection_admission(
    payload: Mapping[str, Any], *, requested_todo_id: str,
    agent_must_attempt: bool, agent_delivery_refused: bool,
) -> tuple[str | None, bool]:
    """Read current obligation admission, including repair and monitor positives."""
    selected_todo = payload.get("selected_todo")
    selected_todo_id = (
        normalize_todo_id(selected_todo.get("todo_id"))
        if isinstance(selected_todo, Mapping)
        else None
    )
    qualification_value = payload.get("action_selection_qualification")
    qualification: Mapping[str, object] = (
        qualification_value if isinstance(qualification_value, Mapping) else {}
    )
    if selected_todo_id is None and str(qualification.get("state") or "") == (
        "qualified"
    ):
        # An unsettled-host-turn recovery decision carries no top-level
        # `selected_todo`: its qualification names the Todo that prior Turn
        # has to settle, and binding the guard to that Todo is the documented
        # closeout path rather than a conflict with the projection.
        qualification_selected = qualification.get("selected_todo")
        selected_todo_id = (
            normalize_todo_id(qualification_selected.get("todo_id"))
            if isinstance(qualification_selected, Mapping)
            else None
        )
    selection_binding = (
        selected_todo.get("selection_binding")
        if isinstance(selected_todo, Mapping)
        else None
    )
    execution_obligation_value = payload.get("execution_obligation")
    execution_obligation: Mapping[str, object] = (
        execution_obligation_value
        if isinstance(execution_obligation_value, Mapping)
        else {}
    )
    pending_selection_delivery_qualified = (
        selection_binding == "pending_action_selection"
        and payload.get("normal_delivery_allowed") is True
    )
    pending_selection_workspace_repair_qualified = (
        selection_binding == "pending_action_selection"
        and payload.get("workspace_repair_allowed") is True
        and payload.get("effective_action") == EffectiveAction.AGENT_WORKSPACE_REPAIR.value
        and execution_obligation.get("kind") == "agent_workspace_repair"
        and execution_obligation.get("must_attempt_work") is True
        and agent_must_attempt
        and agent_delivery_refused
    )
    exact_current_obligation_qualified = (
        selection_binding != "pending_action_selection"
        and execution_obligation.get("must_attempt_work") is True
        and agent_must_attempt
    )
    if (
        selected_todo_id == requested_todo_id
        and payload.get("ok") is True
        and payload.get("should_run") is True
        and (
            pending_selection_delivery_qualified
            or pending_selection_workspace_repair_qualified
            or exact_current_obligation_qualified
        )
    ):
        return selected_todo_id, True

    return selected_todo_id, False


def action_selection_needs_recovery(
    payload: Mapping[str, Any], *, agent_must_attempt: bool = False,
    agent_delivery_refused: bool = False,
) -> bool:
    """Read the typed qualifier's result without reimplementing admission."""
    qualification = payload.get("action_selection_qualification")
    if not isinstance(qualification, Mapping) or qualification.get("state") not in {"deferred", "rejected"}:
        return False
    # A committed binding is reconciled by the receipt owner, not by pending
    # selection recovery. Preserve the same exemption as CLI preflight.
    selected = payload.get("selected_todo")
    replan = payload.get("autonomous_replan_obligation")
    if any(isinstance(value, Mapping) and value.get("selection_binding") == "heartbeat_receipt"
           for value in (selected, replan)):
        return False
    _, admitted = current_action_selection_admission(
        payload, requested_todo_id=str(qualification.get("requested_todo_id") or ""),
        agent_must_attempt=agent_must_attempt, agent_delivery_refused=agent_delivery_refused,
    )
    if admitted:
        return False
    if qualification.get("recovery_action") != "reenter_guard_without_selection":
        raise RuntimeError("rejected action selection omitted its typed recovery action")
    return True


def action_selection_recovery_fields(
    recovery: dict[str, Any],
) -> dict[str, Any]:
    """Complete the one preflight result in the owning projection module."""
    recovery["execution_obligation"] = {
        "must_attempt_work": False,
        "kind": EffectiveAction.QUOTA_SKIP.value,
        "delivery_allowed": False,
        "notify_is_execution_gate": False,
        "reason": recovery["recommended_action"],
        "spend_policy": "no quota spend until an eligible Todo is selected",
    }
    return recovery


def build_action_selection_recovery_fields(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Construct the closed root facts for one typed selection refusal."""

    qualification = payload.get("action_selection_qualification")
    if not isinstance(qualification, Mapping):
        raise RuntimeError("selection recovery requires a typed qualification")
    qualification_state = str(qualification.get("state") or "")
    if qualification_state not in {"deferred", "rejected"}:
        raise RuntimeError("selection recovery requires a deferred or rejected state")
    qualification_reason = str(
        qualification.get("reason") or "candidate_not_currently_eligible"
    )
    deferred = qualification_state == "deferred"
    auxiliary_monitor = (
        qualification_reason
        == "auxiliary_monitor_not_selectable_in_advancement_lane"
    )
    error_code = (
        "quota_action_selection_deferred"
        if deferred
        else "quota_action_selection_rejected"
    )
    recovery = {
        "ok": False,
        "spend_allowed_now": False,
        "spend_after_validation": False,
        "decision": "skip",
        "should_run": False,
        "normal_delivery_allowed": False,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "actionable_by_codex": False,
        # An unadmitted selection grants no safe bypass either: the heartbeat
        # task body reads safe_bypass_allowed as permission to run one bounded
        # step and spend, so the refusal must close that authority too.
        "safe_bypass_allowed": False,
        "safe_bypass_kind": None,
        "safe_bypass_policy": None,
        "effective_action": EffectiveAction.QUOTA_SKIP.value,
        "state": error_code,
        "waiting_on": "codex",
        "status": error_code,
        "error_code": error_code,
        "reason": (
            "explicit action selection was deferred by the current "
            f"delivery frontier: {qualification_reason}"
            if deferred
            else "explicit action selection is not currently eligible: "
            f"{qualification_reason}"
        ),
        "recommended_action": (
            "handle the current delivery preemption, then rerun quota "
            "should-run with the same --turn-instance-id; omit --todo-id "
            "first when a refreshed action portfolio is needed"
            if deferred
            else "the due monitor is visible as auxiliary context, not an "
            "independently selectable action in the current advancement lane; "
            "choose a current advancement Todo, or rerun after the monitor "
            "becomes the hard lane"
            if auxiliary_monitor
            else "rerun quota should-run with the same --turn-instance-id "
            "without --todo-id, then choose a currently eligible Todo"
        ),
    }
    return action_selection_recovery_fields(recovery)


def apply_action_selection_recovery_projection(payload: dict[str, Any]) -> bool:
    """Replace an unadmitted action with its closed pre-finalization facts."""

    qualification = payload.get("action_selection_qualification")
    if not isinstance(qualification, Mapping) or qualification.get("state") not in {
        "deferred",
        "rejected",
    }:
        return False
    if qualification.get("recovery_action") != "reenter_guard_without_selection":
        raise RuntimeError("rejected action selection omitted its typed recovery action")
    payload.update(build_action_selection_recovery_fields(payload))
    return True


def action_selection_recovery_command(
    *, registry_path: str | None = None, runtime_root: str | None = None,
    goal_id: str, agent_id: str | None, turn_instance_id: str | None,
    scheduler_args: str, available_capabilities: Any = None,
    goal_ref: Mapping[str, object] | None = None,
) -> str:
    argv = ["loopx"]
    if registry_path:
        argv.extend(["--registry", registry_path])
    if runtime_root:
        argv.extend(["--runtime-root", runtime_root])
    argv.extend(["--format", "json", "quota", "should-run", "--goal-id", goal_id])
    if agent_id:
        argv.extend(["--agent-id", agent_id])
    if turn_instance_id:
        argv.extend(["--turn-instance-id", turn_instance_id])
    if goal_ref is not None:
        from ..goals.source_session_registry_state import exact_goal_ref

        normalized_goal_ref = exact_goal_ref(
            str(goal_ref.get("goal_id") or ""),
            str(goal_ref.get("goal_instance_id") or ""),
        )
        if normalized_goal_ref["goal_id"] != goal_id:
            raise ValueError("action selection GoalRef does not match goal_id")
        argv.extend(
            ["--goal-instance-id", normalized_goal_ref["goal_instance_id"]]
        )
    for capability in runtime_capabilities_for_cli_projection(available_capabilities):
        argv.extend(["--available-capability", capability])
    return shlex.join(argv) + scheduler_args


def action_selection_recovery_cli_channel(command: str) -> dict[str, Any]:
    return {
        "next_cli_actions": [command], "selection_required": False,
        "spend_allowed_now": False, "spend_after_validation": False,
        "spend_policy": "rerun this turn's guard before delivery or settlement",
    }


def bind_action_selection_recovery_command(
    payload: dict[str, Any], *, registry_path: str, runtime_root: str,
    goal_id: str, agent_id: str | None, turn_instance_id: str | None,
    scheduler_args: str, available_capabilities: Any = None,
    goal_ref: Mapping[str, object] | None = None,
) -> None:
    """Bind the existing recovery projection to the invoking CLI's exact argv."""
    if not action_selection_needs_recovery(payload):
        raise RuntimeError("selection recovery binding requires a typed recovery result")
    command = action_selection_recovery_command(
        registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id,
        agent_id=agent_id, turn_instance_id=turn_instance_id,
        scheduler_args=scheduler_args, available_capabilities=available_capabilities,
        goal_ref=goal_ref,
    )
    interaction = payload["interaction_contract"]
    interaction["agent_channel"]["primary_action"] = command
    interaction["cli_channel"]["next_cli_actions"] = [command]
