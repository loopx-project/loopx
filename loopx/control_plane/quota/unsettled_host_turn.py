"""Transport for the TypeScript-owned prior-host-Turn closeout recovery.

Python reads the exact bound Todo the typed preflight names, hands it
to the typed transaction, and projects the typed verdict back into the
existing public payload.  It owns no closeout policy: which prior Turn needs a
closeout, whether its settlement validates, which closeout is accepted, and
what the recovery obligation is all come from the TypeScript owner.
"""

from __future__ import annotations
from .effective_action import EffectiveAction

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..effect_runtime import (
    EffectRuntimeRejected,
    EffectRuntimeResponseAmbiguous,
    effect_runtime_result,
)
from ..scheduler.execution_context import SchedulerExecutionContextResolution
from ..todos.todo_semantics import todo_item_task_class
from ..work_items.interaction_contract import (
    build_interaction_contract,
)
from .error_codes import (
    CloseoutQueryUnavailableError,
    HeartbeatReceiptIdentityConflictError,
)
from .accounting_admission import quota_accounting_admission

UNSETTLED_HOST_TURN_RECOVERY_SCHEMA_VERSION = "unsettled_host_turn_recovery_v0"

PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_METHOD = (
    "quota.prior_host_turn_closeout.preflight"
)
# The typed owner reads and indexes the Goal history once, then validates each
# candidate from that immutable snapshot. Keep this on the ordinary Effect
# request budget: raising the timeout would hide a return to repeated full-log
# scans and strand the quota entry again on long-lived Goals.
PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_TIMEOUT_SECONDS = 5.0
PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA = (
    "loopx_prior_host_turn_closeout_preflight_request_v0"
)
PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_RESULT_SCHEMA = (
    "loopx_prior_host_turn_closeout_preflight_result_v0"
)
UNSETTLED_HOST_TURN_RECOVERY_METHOD = "quota.unsettled_host_turn_recovery.reduce"
UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA = (
    "loopx_prior_host_turn_recovery_request_v0"
)
UNSETTLED_HOST_TURN_RECOVERY_RESULT_SCHEMA = (
    "loopx_prior_host_turn_recovery_result_v0"
)


def _bound_todo_item(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    todo_id: str | None,
) -> dict[str, Any] | None:
    if not todo_id:
        return None
    # Reuse the exact-ID read path: presentation lanes omit terminal and
    # blocked rows and cannot prove the absence of a lifecycle transition.
    from ...todos import list_goal_todos

    readback = list_goal_todos(
        registry_path=registry_path,
        runtime_root_arg=str(runtime_root),
        goal_id=goal_id,
        role="agent",
        todo_id=todo_id,
    )
    item = readback.get("todo")
    if not isinstance(item, Mapping) or item.get("todo_id") != todo_id:
        return None
    return dict(item)


def _todo_binding_facts(item: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if item is None:
        return None
    return {
        "task_class": todo_item_task_class(dict(item)),
        # The verdict reads the persisted status verbatim; trimming here would
        # accept a value the legacy projection never accepted.
        "status": str(item.get("status") or ""),
        "has_resume_when": bool(item.get("resume_when")),
        "has_successor_todo_ids": (
            isinstance(item.get("successor_todo_ids"), list)
            and bool(item.get("successor_todo_ids"))
        ),
        "target_key": str(item.get("target_key") or "").strip() or None,
        "cadence": str(item.get("cadence") or "").strip() or None,
    }


def _prior_closeout_preflight(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    current_turn_instance_id: str | None,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, Any]] | None:
    """Ask the typed owner which prior Turn must still be closed out.

    The preflight reads the goal's persisted guards and the selected Turn's
    settlement itself, so this side ships a runtime path and an identity rather
    than a megabyte log, and a settled prior Turn never causes a bound-fact read.
    """

    try:
        with quota_accounting_admission(
            runtime_root=runtime_root,
            registry_path=registry_path,
            goal_id=goal_id,
            goal_ref=goal_ref,
            operation="prior-host-turn-closeout-preflight",
            lock_legacy_index=False,
        ) as source_admission:
            request_runtime_root = (
                runtime_root.expanduser().resolve()
                if source_admission is not None
                else runtime_root.expanduser()
            )
            result = effect_runtime_result(
                PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_METHOD,
                {
                    "schema_version": (
                        PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA
                    ),
                    "runtime_root": str(request_runtime_root),
                    "goal_id": goal_id,
                    "agent_id": agent_id,
                    "exclude_turn_instance_id": current_turn_instance_id,
                    **(
                        {"goal_ref": dict(goal_ref)}
                        if goal_ref is not None
                        else {}
                    ),
                    **(
                        {"source_admission": dict(source_admission)}
                        if source_admission is not None
                        else {}
                    ),
                },
                timeout=PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_TIMEOUT_SECONDS,
            )
    except EffectRuntimeResponseAmbiguous as exc:
        # This method only reads receipts. A lost query response is not a
        # possibly committed mutation, and must not send the operator hunting
        # for a nonexistent preflight write receipt. Do not infer a verdict or
        # automatically restart/retry the shared runtime.
        raise CloseoutQueryUnavailableError(
            f"Read-only {PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_METHOD} returned no "
            f"verifiable response within {PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_TIMEOUT_SECONDS:g}s; "
            "closeout state is unknown. Retry the query after checking runtime health; "
            "the preflight itself performs no durable writes",
        ) from exc
    except EffectRuntimeRejected as exc:
        # Keep the public diagnostic the identity rule has always published,
        # even though the rule now lives in the typed owner.
        if exc.diagnostic_code == "heartbeat_receipt_identity_conflict":
            raise HeartbeatReceiptIdentityConflictError(str(exc)) from None
        raise
    if not isinstance(result, Mapping) or (
        result.get("schema_version")
        != PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript closeout preflight result shape mismatch")
    status = result.get("status")
    if status == "none":
        return None
    if status != "candidate":
        raise RuntimeError("TypeScript closeout preflight result shape mismatch")
    candidate = result.get("candidate")
    missing_receipts = result.get("missing_receipts")
    if not isinstance(candidate, Mapping) or not isinstance(missing_receipts, list):
        raise RuntimeError("TypeScript closeout preflight result shape mismatch")
    monitor_poll = result.get("committed_monitor_poll")
    if monitor_poll is not None and not isinstance(monitor_poll, Mapping):
        raise RuntimeError("TypeScript closeout monitor-poll fact shape mismatch")
    return (
        dict(candidate), [str(name) for name in missing_receipts],
        dict(monitor_poll) if isinstance(monitor_poll, Mapping) else {},
    )


def _unsettled_host_turn_recovery(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    current_turn_instance_id: str | None,
    goal_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    if not agent_id or not current_turn_instance_id:
        return None
    preflight = _prior_closeout_preflight(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        current_turn_instance_id=current_turn_instance_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if preflight is None:
        return None
    selected, missing_receipts, monitor_poll = preflight
    # A candidate carries exactly one binding: the Todo it must read, or the
    # autonomous replan obligation that has no Todo to read.
    todo_id = (
        str(selected.get("binding_id") or "")
        if selected.get("binding_kind") == "todo"
        else ""
    ) or None
    # The preflight named this Turn as the one whose bound facts decide the
    # verdict; this is the only provider read this side still performs.
    todo_item = _bound_todo_item(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        todo_id=todo_id,
    )
    binding_facts: dict[str, Any] = {
        "status": "read",
        "todo": _todo_binding_facts(todo_item),
        "committed_monitor_poll": monitor_poll,
    }
    verdict = effect_runtime_result(
        UNSETTLED_HOST_TURN_RECOVERY_METHOD,
        {
            "schema_version": UNSETTLED_HOST_TURN_RECOVERY_REQUEST_SCHEMA,
            "goal_id": goal_id,
            "agent_id": agent_id,
            "candidate": selected,
            "missing_receipts": missing_receipts,
            "binding_facts": binding_facts,
        },
    )
    if not isinstance(verdict, Mapping) or (
        verdict.get("schema_version") != UNSETTLED_HOST_TURN_RECOVERY_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript recovery result shape mismatch")
    status = verdict.get("status")
    if status == "none":
        return None
    if status != "recovery_required":
        raise RuntimeError("TypeScript recovery result shape mismatch")
    recovery = verdict.get("recovery")
    obligation = verdict.get("obligation")
    if not isinstance(recovery, Mapping) or not isinstance(obligation, Mapping):
        raise RuntimeError("TypeScript recovery result shape mismatch")
    if recovery.get("schema_version") != UNSETTLED_HOST_TURN_RECOVERY_SCHEMA_VERSION:
        raise RuntimeError("TypeScript recovery result shape mismatch")
    return {"recovery": dict(recovery), "obligation": dict(obligation)}


def apply_unsettled_host_turn_recovery_if_required(
    payload: dict[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    current_turn_instance_id: str | None,
    available_capabilities: list[str] | None,
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ),
    goal_ref: Mapping[str, Any] | None = None,
) -> bool:
    """Preempt ordinary selection when the preceding host Turn lacks closeout."""

    verdict = _unsettled_host_turn_recovery(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        current_turn_instance_id=current_turn_instance_id,
        goal_ref=goal_ref,
    )
    if verdict is None:
        return False
    return _apply_recovery_projection(payload, verdict=verdict, registry_path=registry_path, runtime_root=runtime_root,
        current_turn_instance_id=current_turn_instance_id, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context)


def apply_receipt_bound_wait_recovery(
    payload: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, agent_id: str, todo_id: str, turn_instance_id: str,
    available_capabilities: list[str] | None,
    scheduler_execution_context: Mapping[str, Any] | SchedulerExecutionContextResolution | None,
    monitor_phase: str | None = None,
) -> bool:
    """Read full provider facts only when a replay lost its executable binding."""
    from ...todos import list_goal_todos
    from ..runtime.time import now_utc_iso

    source = list_goal_todos(
        registry_path=registry_path, runtime_root_arg=str(runtime_root), goal_id=goal_id,
    )
    # Transport one coherent source read, narrowed by its typed dependency
    # reference. TS revalidates the wait; unrelated Todo bodies never cross RPC.
    bound: dict[str, Any] = next((row for row in source["todos"] if row.get("todo_id") == todo_id), {})
    target_id = (bound.get("resume_condition") or {}).get("target_todo_id")
    items = [row for row in source["todos"] if row.get("todo_id") in {todo_id, target_id}]
    verdict = effect_runtime_result("quota.settlement.read", {
        "schema_version": "loopx_quota_receipt_bound_wait_request_v0",
        "todos": items, "todo_id": todo_id, "agent_id": agent_id,
        "turn_instance_id": turn_instance_id, "observed_at": now_utc_iso(),
        "monitor_phase": monitor_phase,
    })
    if verdict.get("status") == "none":
        return False
    if verdict.get("status") != "recovery_required":
        raise RuntimeError("TypeScript bound wait recovery result shape mismatch")
    # This Turn already has a committed Todo binding. A successor's action or
    # replan projection cannot become part of its recovery contract. Prior-Turn
    # recovery retains its separate semantic replan observation below.
    from .settlement_precedence import clear_quota_action_projections

    clear_quota_action_projections(payload)
    return _apply_recovery_projection(payload, verdict=verdict, registry_path=registry_path, runtime_root=runtime_root,
        current_turn_instance_id=turn_instance_id, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context)


def _apply_recovery_projection(
    payload: dict[str, Any], *, verdict: Mapping[str, Any], registry_path: Path, runtime_root: Path,
    current_turn_instance_id: str | None, available_capabilities: list[str] | None,
    scheduler_execution_context: Mapping[str, Any] | SchedulerExecutionContextResolution | None,
) -> bool:
    recovery = verdict["recovery"]
    obligation = verdict["obligation"]
    payload.pop("selected_todo", None)
    payload.pop("todo_id", None)
    payload.pop("action_portfolio", None)
    payload.update(
        {
            "decision": "unsettled_host_turn_recovery",
            "should_run": True,
            "state": "eligible",
            "effective_action": EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value,
            "actionable_by_codex": True,
            "normal_delivery_allowed": False,
            "recovery_delivery_allowed": False,
            "self_repair_allowed": False,
            "capability_repair_allowed": False,
            "workspace_repair_allowed": False,
            "safe_bypass_allowed": False,
            "safe_bypass_kind": None,
            "safe_bypass_policy": None,
            "reason": obligation["reason"],
            "recommended_action": obligation["recommended_action"],
            "unsettled_host_turn_recovery": recovery,
            "heartbeat_recommendation": {
                "source": "unsettled_host_turn_recovery",
                "recommended_mode": "unsettled_host_turn_recovery",
                "notify": obligation["notify"],
                "spend_policy": obligation["spend_policy"],
                "reason": obligation["recommendation_reason"],
                "agent_must_attempt": True,
            },
            "execution_obligation": {
                "must_attempt_work": True,
                "kind": "unsettled_host_turn_recovery",
                "contract": obligation["contract"],
                "contract_obligation": obligation["contract_obligation"],
                "delivery_allowed": obligation["delivery_allowed"],
                "notify_is_execution_gate": False,
                "reason": obligation["recommendation_reason"],
            },
            "work_lane_contract": {
                "schema_version": "work_lane_contract_v1",
                "lane": obligation["lane"],
                "next_lane": obligation["next_lane"],
                "obligation": obligation["obligation"],
                "must_attempt_work": obligation["must_attempt_work"],
                "reason_codes": [obligation["reason_code"]],
                "monitor_policy": "typed_observation_only",
                "action": obligation["recommended_action"],
            },
            "automation_liveness": {
                "schema_version": "automation_liveness_v0",
                "keep_active": True,
                "pause_allowed": False,
                "automation_action": "execute_bounded_recovery",
                "reason": obligation["unsettled_reason"],
                "spend_policy": obligation["spend_policy"],
            },
        }
    )
    interaction_contract = build_interaction_contract(
        payload,
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=current_turn_instance_id,
        runtime_root=str(runtime_root),
        registry_path=str(registry_path),
    )
    agent_channel = interaction_contract.get("agent_channel")
    if isinstance(agent_channel, dict):
        agent_channel["primary_action"] = payload["recommended_action"]
        agent_channel.pop("next_task_action", None)
        agent_channel["recovery_ref"] = "$.unsettled_host_turn_recovery"
    cli_channel = interaction_contract.get("cli_channel")
    if isinstance(cli_channel, dict):
        cli_channel["spend_policy"] = obligation["spend_policy"]
        cli_channel["recovery_ref"] = "$.unsettled_host_turn_recovery"
    payload["interaction_contract"] = interaction_contract
    return True
