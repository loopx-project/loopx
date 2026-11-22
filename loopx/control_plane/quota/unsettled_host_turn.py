"""Transport for the TypeScript-owned prior-host-Turn closeout recovery.

Python reads the exact bound Todo the typed preflight names, hands it
to the typed transaction, and projects the typed verdict back into the
existing public payload.  It owns no closeout policy: which prior Turn needs a
closeout, whether its settlement validates, which closeout is accepted, and
what the recovery obligation is all come from the TypeScript owner.
"""

from __future__ import annotations
from .effective_action import EffectiveAction
from .states import quota_item_is_paused

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ...rollout_event_log import RolloutAppendPreconditionChanged
from ..effect_runtime import (
    EffectRuntimeRejected,
    EffectRuntimeResponseAmbiguous,
    effect_runtime_result,
)
from ..scheduler.arbitration import SchedulerArbitration
from ..scheduler.execution_context import SchedulerExecutionContextResolution
from ..agents.agent_scope_frontier import AgentScopeFrontierAction
from ..todos.todo_semantics import todo_item_task_class
from ..work_items.interaction_contract import (
    _scoped_cli_args,
    _build_interaction_contract_projection,
)
from .error_codes import (
    CloseoutQueryUnavailableError,
    HeartbeatReceiptIdentityConflictError,
)
from .accounting_admission import quota_accounting_admission
from .effect_program import (
    ReceiptBoundReplayPhase,
    SettlementStepKind,
    build_turn_scoped_cli_settlement_plan,
    settlement_step_command,
)

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
    from ..todos.list_readback import list_goal_todos

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


def restore_receipt_bound_todo_source(
    payload: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, todo_id: str | None, replay_phase: ReceiptBoundReplayPhase | None,
    replan_guard_scoped: bool = False,
) -> dict[str, Any]:
    """Complete a display snapshot from the original binding's provider reader.

    Display lane caps cannot decide whether a committed Todo still exists.
    This only transports provider facts to the existing decision owner.
    """
    from ..todos.quota_summary import select_planning_inventory_source_items

    if replan_guard_scoped and replay_phase is ReceiptBoundReplayPhase.OPEN:
        # A replan receipt names an obligation, not a Todo. Its lifecycle
        # source may be absent from every bounded display lane; the typed
        # owner still validates the original source identity and timestamps.
        from ..todos.list_readback import list_goal_todos

        goal_todos = list_goal_todos(
            registry_path=registry_path, runtime_root_arg=str(runtime_root), goal_id=goal_id,
        )
        todo_fields = {key: goal_todos[key] for key in ("user_todos", "agent_todos")}
        queue = payload.get("attention_queue") or {}
        return {**payload, "attention_queue": {**queue, "items": [
            {**row, **todo_fields,
                "project_asset": {**(row.get("project_asset") or {}), **todo_fields}}
            if row.get("goal_id") == goal_id else row
            for row in queue.get("items") or []
        ]}}
    if not todo_id or replay_phase is ReceiptBoundReplayPhase.SETTLED:
        return payload
    queue = payload.get("attention_queue") or {}
    rows = list(queue.get("items") or [])
    for index, row in enumerate(rows):
        if row.get("goal_id") != goal_id:
            continue
        asset = row.get("project_asset") or {}
        source = select_planning_inventory_source_items(
            row.get("agent_todos"), asset.get("agent_todos"), include_terminal=True,
        )
        if any(item.get("todo_id") == todo_id for item in source):
            return payload
        bound = _bound_todo_item(registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, todo_id=todo_id)
        if bound is None:
            raise ValueError("The original receipt-bound Todo cannot be read; recover its lifecycle before selecting or settling work")
        def restored(summary: Mapping[str, Any] | None) -> dict[str, Any]:
            current = dict(summary or {})
            current["items"] = [*(current.get("items") or []), bound]
            return current
        rows[index] = {**row, "agent_todos": restored(row.get("agent_todos")),
            "project_asset": {**asset, "agent_todos": restored(asset.get("agent_todos"))}}
        return {**payload, "attention_queue": {**queue, "items": rows}}
    return payload


def _prior_closeout_preflight(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    current_turn_instance_id: str | None,
    registry_path: Path | None = None,
    goal_ref: Mapping[str, Any] | None = None,
    owner_action_id: str | None = None,
) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, Any]] | None:
    """Ask the typed owner which prior Turn must still be closed out.

    The preflight reads the goal's persisted guards and the selected Turn's
    settlement itself, so this side ships a runtime path and an identity rather
    than a megabyte log, and a settled prior Turn never causes a bound-fact read.
    """

    with quota_accounting_admission(
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=goal_id,
        goal_ref=goal_ref,
        operation="prior-host-turn-closeout-preflight",
        lock_legacy_index=False,
    ) as source_admission:
        return _read_prior_closeout_preflight(
            runtime_root=runtime_root,
            goal_id=goal_id,
            agent_id=agent_id,
            current_turn_instance_id=current_turn_instance_id,
            goal_ref=goal_ref,
            owner_action_id=owner_action_id,
            source_admission=source_admission,
        )


def _read_prior_closeout_preflight(
    *,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    current_turn_instance_id: str | None,
    goal_ref: Mapping[str, Any] | None,
    owner_action_id: str | None,
    source_admission: Mapping[str, Any] | None,
    borrow_source_admission: bool = False,
) -> tuple[dict[str, Any], list[str], dict[str, Any], dict[str, Any]] | None:
    try:
        request_runtime_root = (
            runtime_root.expanduser().resolve()
            if source_admission is not None
            else runtime_root.expanduser()
        )
        result = effect_runtime_result(
            PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_METHOD,
            {
                "schema_version": PRIOR_HOST_TURN_CLOSEOUT_PREFLIGHT_REQUEST_SCHEMA,
                "runtime_root": str(request_runtime_root),
                "goal_id": goal_id,
                "agent_id": agent_id,
                "exclude_turn_instance_id": current_turn_instance_id,
                "owner_action_id": owner_action_id,
                **({"goal_ref": dict(goal_ref)} if goal_ref is not None else {}),
                **({"source_admission": dict(source_admission)} if source_admission is not None else {}),
                **({"borrow_source_admission": True} if borrow_source_admission else {}),
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
        dict(result["recovery_admissions"]),
    )


@contextmanager
def recovery_admission_append_precondition(
    payload: Mapping[str, Any],
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str,
    turn_instance_id: str,
    goal_ref: Mapping[str, Any] | None,
) -> Iterator[Callable[[], None] | None]:
    recovery = payload.get("unsettled_host_turn_recovery")
    if (payload.get("should_run") is not True or not isinstance(recovery, Mapping)
            or recovery.get("state") != "recovery_required"):
        yield None
        return

    from ...agent_registry import load_goal_from_registry
    from ..goals.activation import goal_is_stopped, goal_owner_resume_id

    # The append owns the rollout lock. Acquire any source-lifetime locks
    # first, as quota settlement does, and reuse those locks for the read.
    with quota_accounting_admission(
        runtime_root=runtime_root,
        registry_path=registry_path,
        goal_id=goal_id,
        goal_ref=goal_ref,
        operation="recovery_admission_append",
        lock_legacy_index=False,
    ) as source_admission:
        def check() -> None:
            goal = load_goal_from_registry(registry_path, goal_id)
            if goal is None or goal_is_stopped(goal):
                raise RolloutAppendPreconditionChanged("The Goal is unavailable or stopped before recovery admission.")
            current = _read_prior_closeout_preflight(
                runtime_root=runtime_root,
                goal_id=goal_id,
                agent_id=agent_id,
                current_turn_instance_id=turn_instance_id,
                goal_ref=goal_ref,
                owner_action_id=goal_owner_resume_id(goal),
                source_admission=source_admission,
                borrow_source_admission=source_admission is not None,
            )
            if (current is None or current[3]["checkpoint"] != recovery["admission_checkpoint"]
                    or current[3]["count"] != recovery["admissions_without_progress"]):
                raise RolloutAppendPreconditionChanged("The recovery admission basis changed before append.")

        yield check


def _unsettled_host_turn_recovery(
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_id: str,
    agent_id: str | None,
    current_turn_instance_id: str | None,
    goal_ref: Mapping[str, Any] | None = None,
    owner_action_id: str | None = None,
) -> dict[str, Any] | None:
    if not agent_id:
        return None
    preflight = _prior_closeout_preflight(
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        current_turn_instance_id=current_turn_instance_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
        owner_action_id=owner_action_id,
    )
    if preflight is None:
        return None
    selected, missing_receipts, monitor_poll, recovery_admissions = preflight
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
            "recovery_admissions": recovery_admissions,
        },
    )
    if not isinstance(verdict, Mapping) or (
        verdict.get("schema_version") != UNSETTLED_HOST_TURN_RECOVERY_RESULT_SCHEMA
    ):
        raise RuntimeError("TypeScript recovery result shape mismatch")
    status = verdict.get("status")
    if status == "none":
        return None
    if status not in ("recovery_required", "attention_required"):
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
    project_scheduler_hint: Callable[..., dict[str, Any]],
    scheduler_execution_context: (
        Mapping[str, Any] | SchedulerExecutionContextResolution | None
    ),
    goal_ref: Mapping[str, Any] | None = None,
    owner_action_id: str | None = None,
) -> bool:
    """Preempt ordinary selection when the preceding host Turn lacks closeout."""

    if quota_item_is_paused(payload):
        return False

    verdict = _unsettled_host_turn_recovery(
        registry_path=registry_path,
        runtime_root=runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        current_turn_instance_id=current_turn_instance_id,
        goal_ref=goal_ref,
        owner_action_id=owner_action_id,
    )
    if verdict is None:
        return False
    arbitration = _apply_recovery_projection(
        payload, verdict=verdict, registry_path=registry_path, runtime_root=runtime_root,
        current_turn_instance_id=current_turn_instance_id, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
    )
    # Refresh scheduler_hint for both recovery_required and attention_required so
    # an earlier wait or terminal stop hint never overrides recovery arbitration.
    payload["scheduler_hint"] = project_scheduler_hint(payload, arbitration=arbitration)
    return True


def apply_receipt_bound_wait_recovery(
    payload: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, agent_id: str, todo_id: str, turn_instance_id: str,
    available_capabilities: list[str] | None,
    scheduler_execution_context: Mapping[str, Any] | SchedulerExecutionContextResolution | None,
    monitor_phase: str | None = None,
    settlement_progress: dict[str, Any] | None = None,
    replay_phase: ReceiptBoundReplayPhase | None = None,
    heartbeat_receipt: Mapping[str, Any] | None = None,
) -> bool:
    """Read full provider facts only when a replay lost its executable binding."""
    from ..todos.list_readback import list_goal_todos
    from .blocked_retry import compact_settlement_wait_todos
    from ..runtime.time import now_utc_iso

    source = list_goal_todos(
        registry_path=registry_path, runtime_root_arg=str(runtime_root), goal_id=goal_id,
    )
    # Keep transitive dependencies from this coherent read. A direct-target
    # slice loses longer cycles; compact facts keep prose out of the RPC.
    source_items = source["todos"]
    if not any(item.get("todo_id") == todo_id for item in source_items):
        bound = _bound_todo_item(
            registry_path=registry_path, runtime_root=runtime_root,
            goal_id=goal_id, todo_id=todo_id,
        )
        if bound is None:
            raise ValueError("The original receipt-bound Todo cannot be read; recover its lifecycle before settling this Turn")
        source_items = [*source_items, bound]
    items = compact_settlement_wait_todos(source_items, todo_id=todo_id)
    verdict = effect_runtime_result("quota.settlement.read", {
        "schema_version": "loopx_quota_receipt_bound_wait_request_v0",
        "todos": items, "todo_id": todo_id, "agent_id": agent_id, "goal_id": goal_id,
        "turn_instance_id": turn_instance_id, "observed_at": now_utc_iso(),
        "monitor_phase": monitor_phase,
        "settlement_progress": settlement_progress,
        "replay_phase": replay_phase.value if replay_phase else None,
        "heartbeat_receipt": dict(heartbeat_receipt) if heartbeat_receipt is not None else None,
    }, large_local_snapshot=True)
    if verdict.get("status") == "none":
        return False
    if verdict.get("status") != "recovery_required":
        raise RuntimeError("TypeScript bound wait recovery result shape mismatch")
    # This Turn already has a committed Todo binding. A successor's action or
    # replan projection cannot become part of its recovery contract. Prior-Turn
    # recovery retains its separate semantic replan observation below.
    from .settlement_precedence import clear_quota_action_projections

    clear_quota_action_projections(payload)
    _apply_recovery_projection(payload, verdict=verdict, registry_path=registry_path, runtime_root=runtime_root,
        current_turn_instance_id=turn_instance_id, available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context)
    if verdict["recovery"].get("repair") == "durable_writeback":
        # Resume the original effect through the same composed command as
        # ordinary C1 delivery. Recovery never grants new task execution.
        from ..work_items.action_selection_contract import render_cli_command_prefix

        assert settlement_progress is not None
        plan = build_turn_scoped_cli_settlement_plan(
            goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
            turn_instance_id=turn_instance_id,
            command_prefix=render_cli_command_prefix(
                registry_path=str(registry_path), runtime_root=str(runtime_root),
            ),
            scoped_cli_args=_scoped_cli_args(
                {"agent_id": agent_id}, available_capabilities=available_capabilities,
            ),
            lifecycle_actor_args="",
            quota_spend_source=settlement_progress["quota_spend_source"],
            goal_ref=payload.get("goal_ref"),
            composed_settlement=True,
        ).as_dict()
        cli = payload["interaction_contract"]["cli_channel"]
        cli.update({
            "settlement_plan": plan,
            "next_cli_actions": [settlement_step_command(plan, SettlementStepKind.DURABLE_WRITEBACK)],
            "spend_after_validation": True,
            "quota_spend_source": settlement_progress["quota_spend_source"],
        })
    return True


def _apply_recovery_projection(
    payload: dict[str, Any], *, verdict: Mapping[str, Any], registry_path: Path, runtime_root: Path,
    current_turn_instance_id: str | None, available_capabilities: list[str] | None,
    scheduler_execution_context: Mapping[str, Any] | SchedulerExecutionContextResolution | None,
) -> SchedulerArbitration:
    recovery = verdict["recovery"]
    obligation = verdict["obligation"]
    attention = recovery.get("state") == "attention_required"
    if attention:
        from .settlement_precedence import clear_quota_action_projections

        reads = payload.get("required_reads")
        clear_quota_action_projections(payload)
        # The recovery hold suppresses the stale work selection. The
        # authorized Inbox read is scoped to the Goal and Agent independently.
        if isinstance(reads, list):
            inbox_reads = [
                read for read in reads
                if isinstance(read, Mapping)
                and read.get("source") == "turn_start_capability_hook"
                and read.get("kind") == "operator_inbox"
            ]
            if inbox_reads:
                payload["required_reads"] = inbox_reads
    payload.pop("selected_todo", None)
    payload.pop("todo_id", None)
    payload.pop("action_portfolio", None)
    if recovery.get("repair") == "execution_loss_closeout":
        # Ordinary preparation was built for potential current work. Its
        # claim must never replace the prior Turn's administrative recovery.
        payload.pop("execution_preparation", None)
    payload.update(
        {
            "decision": "blocked_wait" if attention else "unsettled_host_turn_recovery",
            "should_run": obligation["must_attempt_work"],
            "state": "waiting" if attention else "eligible",
            "effective_action": EffectiveAction.UNSETTLED_HOST_TURN_RECOVERY.value,
            "actionable_by_codex": obligation["must_attempt_work"],
            "normal_delivery_allowed": False,
            "recovery_delivery_allowed": False,
            "self_repair_allowed": False,
            "capability_repair_allowed": False,
            "workspace_repair_allowed": False,
            "safe_bypass_allowed": False,
            "safe_bypass_kind": None,
            "safe_bypass_policy": None,
            "reason": obligation["reason"],
            **({"reason_code": obligation["reason_code"], "requires_user_action": True} if attention else {}),
            "recommended_action": obligation["recommended_action"],
            "unsettled_host_turn_recovery": recovery,
            "heartbeat_recommendation": {
                "source": "unsettled_host_turn_recovery",
                "recommended_mode": "unsettled_host_turn_recovery",
                "notify": obligation["notify"],
                "spend_policy": obligation["spend_policy"],
                "reason": obligation["recommendation_reason"],
                "agent_must_attempt": obligation["must_attempt_work"],
            },
            "execution_obligation": {
                "must_attempt_work": obligation["must_attempt_work"],
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
                "pause_allowed": attention,
                "automation_action": "wait_for_owner" if attention else "execute_bounded_recovery",
                "reason": obligation["unsettled_reason"],
                "spend_policy": obligation["spend_policy"],
            },
        }
    )
    if attention:
        payload["effective_action"] = EffectiveAction.BLOCKED_WAIT.value
    recovery_lease_proof: dict[str, Any] | None = None
    if not attention and recovery.get("repair") == "monitor_poll":
        from .execution_preparation import project_selected_todo_execution_preparation

        agent_identity = payload.get("agent_identity")
        agent_id = (
            agent_identity.get("agent_id")
            if isinstance(agent_identity, Mapping)
            else payload.get("agent_id")
        )
        prior_todo_id = recovery.get("binding_id")
        if (
            isinstance(prior_todo_id, str)
            and prior_todo_id.strip()
            and isinstance(payload.get("goal_id"), str)
            and payload["goal_id"].strip()
            and isinstance(agent_id, str)
            and agent_id.strip()
        ):
            preparation = project_selected_todo_execution_preparation(
                runtime_root=runtime_root,
                goal_id=payload["goal_id"],
                selected_todo={"todo_id": prior_todo_id},
                agent_id=agent_id,
            )
            if (
                isinstance(preparation, Mapping)
                and preparation.get("handoff_mode") == "hard_lease"
                and preparation.get("state") == "ready"
                and isinstance(preparation.get("lease_proof"), Mapping)
            ):
                recovery_lease_proof = dict(preparation["lease_proof"])
    interaction_contract, arbitration = _build_interaction_contract_projection(
        payload,
        agent_scope_frontier_actions=[action.value for action in AgentScopeFrontierAction],
        available_capabilities=available_capabilities,
        scheduler_execution_context=scheduler_execution_context,
        turn_instance_id=current_turn_instance_id,
        runtime_root=str(runtime_root),
        registry_path=str(registry_path),
        recovery_lease_proof=recovery_lease_proof,
    )
    agent_channel = interaction_contract.get("agent_channel")
    if isinstance(agent_channel, dict):
        agent_channel["primary_action"] = payload["recommended_action"]
        agent_channel.pop("next_task_action", None)
        agent_channel["recovery_ref"] = "$.unsettled_host_turn_recovery"
    cli_channel = interaction_contract.get("cli_channel")
    if isinstance(cli_channel, dict):
        cli_channel["spend_policy"] = obligation["spend_policy"]
        cli_channel["spend_allowed_now"] = obligation.get("spend_allowed_now") is True
        cli_channel["recovery_ref"] = "$.unsettled_host_turn_recovery"
    payload["interaction_contract"] = interaction_contract
    return arbitration
