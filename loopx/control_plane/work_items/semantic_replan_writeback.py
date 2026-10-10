"""Write-time qualification for open semantic replan obligations."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from ...agent_registry import registered_agent_ids_for_goal
from ..agents.agent_scope import agent_scope_item_matches_agent_or_unclaimed
from ..agents.identity import build_quota_agent_identity
from ..goals.goal_frontier import (
    build_goal_frontier_projection_context_from_status,
)
from ..status.autonomous_replan_projection import (
    AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS,
    autonomous_replan_obligation_from_runs,
)
from ..todos.active_state_todo_parser import parse_active_state_todos
from ..todos.quota_summary import (
    select_planning_inventory_source_items,
    select_quota_todo_summary,
)
from ..todos.succession_warning import todo_succession_gap_items
from .autonomous_replan_ack import (
    latest_monitor_replan_frontier_identity,
    watch_lane_continuation_todo_ids,
)
from .autonomous_replan_obligation import (
    build_autonomous_replan_cli_actions,
    project_todo_lifecycle_settlement_reentry,
)
from .progress_observation import guarded_replan_transition_delta, semantic_delta_from_writeback
from .repair_delta import (
    build_repair_delta_contract,
    repair_delta_kinds_have_accountable_progress,
)
from .work_lane_context import build_work_lane_context_contract


REPLAN_WRITEBACK_REJECTION_SCHEMA_VERSION = "replan_writeback_rejection_v0"


@dataclass(frozen=True)
class CapabilityReplanEvidence:
    """Composition-root supplied facts and their capability-owned qualifier."""
    frontier: dict[str, Any]
    qualify: Callable[[Mapping[str, Any], str, dict[str, Any] | None, list[dict[str, Any]]],
                      tuple[dict[str, Any], dict[str, Any]]]


@dataclass(frozen=True)
class RefreshReplanQualification:
    repair_delta_contract: dict[str, Any] | None
    semantic_delta: dict[str, Any] | None
    frontier_identity: str | None
    classification: str
    delivery_outcome: str | None
    autonomous_replan_recorded: bool


class ReplanWritebackRejected(ValueError):
    """A strict writeback rejection with its actionable obligation attached."""

    def __init__(
        self,
        message: str,
        *,
        obligation: Mapping[str, Any],
        semantic_delta: Mapping[str, Any] | None,
    ) -> None:
        super().__init__(message)
        self.obligation = dict(obligation)
        self.semantic_delta = dict(semantic_delta or {})


def project_replan_writeback_rejection(
    rejection: ReplanWritebackRejected,
    *,
    goal_id: str,
    agent_id: str | None,
    runtime_root: str | None = None,
) -> dict[str, Any]:
    """Project exact recovery actions without weakening the writeback gate."""

    safe_agent_id = str(agent_id or "").strip()
    scoped_cli_args = (
        f" --agent-id {shlex.quote(safe_agent_id)}" if safe_agent_id else ""
    )
    obligation = rejection.obligation
    compact_triggers = [
        {
            key: trigger.get(key)
            for key in (
                "kind", "todo_id", "completion_turn_key",
                "reason_code", "component_checks", "resolution_hint",
            )
            if trigger.get(key) is not None
        }
        for trigger in obligation.get("triggers") or []
        if isinstance(trigger, Mapping)
    ]
    lifecycle_reentry = project_todo_lifecycle_settlement_reentry(
        obligation,
        goal_id=goal_id,
        lifecycle_actor_args=scoped_cli_args,
        scoped_cli_args=scoped_cli_args,
        runtime_root=runtime_root,
    )
    triggers = (
        lifecycle_reentry["triggers"]
        if lifecycle_reentry is not None
        else compact_triggers
    )
    next_cli_actions = (
        lifecycle_reentry["next_cli_actions"]
        if lifecycle_reentry is not None
        else build_autonomous_replan_cli_actions(
            obligation,
            goal_id=goal_id,
            settlement_args="",
            scoped_cli_args=scoped_cli_args,
            quota_spend_action="",
            settlement_chain_ready=False,
            lifecycle_actor_args=scoped_cli_args,
            runtime_root=runtime_root,
        )
    )
    capability_guard = obligation.get("capability_guard")
    if isinstance(capability_guard, Mapping) and rejection.semantic_delta.get("readback_actions"):
        next_cli_actions = rejection.semantic_delta["readback_actions"]
    return {
        "schema_version": REPLAN_WRITEBACK_REJECTION_SCHEMA_VERSION,
        "required": True,
        "host_action": (
            "settle_todo_lifecycle"
            if lifecycle_reentry is not None
            else "read_current_capability_evidence" if capability_guard
            else "write_typed_semantic_delta"
        ),
        "obligation_id": obligation.get("obligation_id"),
        "resolution_mode": obligation.get("resolution_mode"),
        **({"retirement_contract": rejection.semantic_delta["retirement_contract"]}
           if rejection.semantic_delta.get("retirement_contract") else {}),
        "reason_code": rejection.semantic_delta.get("reason_code"),
        "triggers": triggers,
        "next_cli_actions": next_cli_actions,
        **({"capability_guard": dict(capability_guard)} if isinstance(capability_guard, Mapping) else {}),
    }


def _obligation_was_created_by_current_completion(
    obligation: dict[str, Any],
    *,
    agent_todo_items: list[dict[str, Any]] | None,
    agent_id: str,
    completion_todo_id: str | None,
    completion_turn_key: str | None,
) -> bool:
    """Keep a new successor obligation for the next decision boundary.

    A validated Turn completes its Todo before its refresh row is written.  The
    completion can therefore create a ``completed_advancement_without_successor``
    obligation inside the same transaction.  That new obligation did not exist
    when the Turn began and must govern the next decision, not reject the
    completion that caused it.

    The exemption is deliberately narrow and causal: every trigger must name
    the Todo completed by this exact Turn, and the current durable Todo row must
    carry the same completion identity.  Any older or mixed obligation remains
    write-gated.
    """

    safe_todo_id = str(completion_todo_id or "").strip()
    safe_turn_key = str(completion_turn_key or "").strip()
    if (
        not safe_todo_id
        or not safe_turn_key
        or not isinstance(agent_todo_items, list)
    ):
        return False
    triggers = obligation.get("triggers")
    if not isinstance(triggers, list) or not triggers:
        return False
    if any(
        not isinstance(trigger, dict)
        or trigger.get("kind") != "completed_advancement_without_successor"
        or str(trigger.get("todo_id") or "").strip() != safe_todo_id
        for trigger in triggers
    ):
        return False
    return any(
        isinstance(item, dict)
        and agent_scope_item_matches_agent_or_unclaimed(item, agent_id=agent_id)
        and str(item.get("todo_id") or "").strip() == safe_todo_id
        and item.get("status") == "done"
        and str(item.get("completion_turn_key") or "").strip() == safe_turn_key
        for item in agent_todo_items
    )


def qualify_replan_writeback(
    *,
    newest_first_runs: list[dict[str, Any]] | None,
    state_text: str,
    agent_id: str,
    goal_id: str,
    progress_observation: dict[str, Any] | None = None,
    registry_goal: dict[str, Any] | None = None,
    agent_vision: dict[str, Any] | None = None,
    completion_todo_id: str | None = None,
    completion_turn_key: str | None = None,
    todo_fields: dict[str, Any] | None = None,
    external_progress_review: Mapping[str, Any] | None = None,
    effective_turn_cadence: dict[str, Any] | None = None,
    guard_scoped: bool = False,
    guard_semantic_replan_obligation_id: str | None = None,
    capability_evidence: CapabilityReplanEvidence | None = None,
    guard_capability: Mapping[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return the shared open obligation and the writeback's typed delta.

    The write path deliberately reuses quota's goal-frontier reducer. It does
    not classify maintenance from prose and therefore sees obligations derived
    from run history, agent vision, and final-outcome checkpoints alike.
    """

    safe_agent_id = str(agent_id or "").strip()
    if not safe_agent_id:
        return None, None
    todo_projection = todo_fields if todo_fields is not None else parse_active_state_todos(
        state_text, item_limit=None,
        goal={**(registry_goal or {}), "latest_runs": list(newest_first_runs or [])},
    )
    registered_agent_ids = registered_agent_ids_for_goal(registry_goal)
    agent_identity = (
        build_quota_agent_identity(registry_goal, agent_id=safe_agent_id)
        if registered_agent_ids
        else {
            "agent_id": safe_agent_id,
            "registered_agents": [safe_agent_id],
        }
    )
    raw_user_todos = todo_projection.get("user_todos")
    raw_agent_todos = todo_projection.get("agent_todos")
    user_todos = select_quota_todo_summary(
        raw_user_todos,
        None,
        agent_identity=agent_identity,
        filter_user_gate_blocks_agent=True,
    )
    agent_todos = select_quota_todo_summary(
        raw_agent_todos,
        None,
        agent_identity=agent_identity,
    )
    agent_todo_source_items = select_planning_inventory_source_items(
        raw_agent_todos,
        None,
        include_terminal=True,
    )
    agent_todo_completion_items = (
        [
            item
            for item in raw_agent_todos.get("items", [])
            if isinstance(item, dict)
        ]
        if isinstance(raw_agent_todos, dict)
        else []
    )
    run_obligation = autonomous_replan_obligation_from_runs(
        newest_first_runs,
        agent_todos=agent_todos,
        agent_id=safe_agent_id,
        external_progress_review=external_progress_review,
        effective_turn_cadence=effective_turn_cadence,
    )
    status_payload = {
        "run_history": {
            "goals": [
                {
                    **(registry_goal or {}),
                    "id": goal_id,
                    "latest_runs": list(newest_first_runs or []),
                }
            ]
        }
    }
    if capability_evidence is not None:
        status_payload["bounded_research_frontier"] = capability_evidence.frontier
    context = build_goal_frontier_projection_context_from_status(
        goal_id=goal_id,
        agent_id=safe_agent_id,
        status_payload=status_payload,
        item={
            **(
                {"autonomous_replan_obligation": run_obligation}
                if run_obligation
                else {}
            )
        },
        project_asset={"execution_profile": (registry_goal or {}).get("execution_profile")},
        user_todo_summary=user_todos,
        agent_todo_summary=agent_todos,
        agent_todo_source_items=agent_todo_source_items,
        work_lane_contract=build_work_lane_context_contract(
            {"progress_scope": "primary_goal"},
            agent_todo_summary=agent_todos,
        ),
        neutral_replan_ack_classifications=(
            AUTONOMOUS_RUN_HISTORY_NEUTRAL_CLASSIFICATIONS
        ),
        registered_agent_ids=list(agent_identity["registered_agents"]),
        goal_status=str((registry_goal or {}).get("status") or "active"),
        receipt_bound_replan_obligation_id=(guard_semantic_replan_obligation_id if guard_scoped else None),
        agent_profile=(
            agent_identity.get("agent_profile")
            if isinstance(agent_identity.get("agent_profile"), dict)
            else None
        ),
    )
    obligation = context.get("replan_obligation")
    selected_capability = guard_capability if guard_scoped else (obligation or {}).get("capability_guard")
    if selected_capability is not None:
        if capability_evidence is None:
            raise ValueError("selected capability writeback owner is not available")
        selected_id = guard_semantic_replan_obligation_id if guard_scoped else (obligation or {}).get("obligation_id")
        return capability_evidence.qualify(selected_capability, str(selected_id), progress_observation,
            [run for run in newest_first_runs or [] if run.get("agent_id") == safe_agent_id])
    if guard_scoped and guard_semantic_replan_obligation_id:
        transition_delta = guarded_replan_transition_delta(
            guard_scoped=guard_scoped,
            selected_obligation_id=guard_semantic_replan_obligation_id,
            transition_acks=[context.get("run_replan_transition_ack"),
                             context.get("replan_transition_ack"),
                             *(candidate.get("ack") for candidate in
                               context.get("replan_transition_candidates", []))],
        )
        if transition_delta is not None:
            # This closes only the selected Turn obligation. The freshly
            # derived frontier obligation remains visible to the next guard.
            return None, transition_delta
    obligation = context.get("replan_obligation")
    if not obligation and guard_scoped and guard_semantic_replan_obligation_id:
        # A now-unclaimed successor may keep the ordinary frontier runnable,
        # but cannot discharge its former owner's admitted review. Retain the
        # exact reconstructed source so stale unchanged writeback is refused.
        obligation = next((candidate["obligation"] for candidate in
            context.get("replan_transition_candidates", [])
            if isinstance(candidate.get("obligation"), dict) and
            candidate["obligation"].get("obligation_id") == guard_semantic_replan_obligation_id), None)
    if not obligation:
        # A validated Todo transition can discharge the read-model obligation
        # before refresh. Preserve that evidence in this run so periodic review
        # starts a new window rather than counting the same history again.
        transition_ack = context.get("replan_transition_ack") or {}
        if transition_ack.get("recorded") is True:
            return None, transition_ack.get("semantic_delta")
        return None, None
    if _obligation_was_created_by_current_completion(
        obligation,
        agent_todo_items=agent_todo_completion_items,
        agent_id=safe_agent_id,
        completion_todo_id=completion_todo_id,
        completion_turn_key=completion_turn_key,
    ):
        return None, None
    semantic_delta = semantic_delta_from_writeback(
        obligation=obligation,
        progress_observation=progress_observation,
        agent_vision=agent_vision,
        history_runs=newest_first_runs,
    )
    if (
        "coverage_backed_no_followup"
        in set(semantic_delta.get("outcomes") or [])
        and todo_succession_gap_items(agent_todos, agent_id=safe_agent_id)
    ):
        semantic_delta = {
            **semantic_delta,
            "accepted": False,
            "satisfying_outcomes": [],
            "reason_code": "todo_no_followup_settlement_required",
            "reason": (
                "coverage-backed no-follow-up cannot replace Todo lifecycle "
                "settlement; first run loopx todo complete with --no-follow-up "
                "for the completed Todo, then write the terminal vision/path"
            ),
        }
    return obligation, semantic_delta


def enforce_open_replan_writeback(
    *,
    newest_first_runs: list[dict[str, Any]] | None,
    state_text: str,
    agent_id: str,
    goal_id: str,
    progress_observation: dict[str, Any] | None = None,
    registry_goal: dict[str, Any] | None = None,
    agent_vision: dict[str, Any] | None = None,
    completion_todo_id: str | None = None,
    completion_turn_key: str | None = None,
    guard_scoped: bool = False,
    guard_semantic_replan_obligation_id: str | None = None,
    todo_fields: dict[str, Any] | None = None,
    external_progress_review: Mapping[str, Any] | None = None,
    capability_evidence: CapabilityReplanEvidence | None = None,
    guard_capability: Mapping[str, Any] | None = None,
    effective_turn_cadence: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Fail closed unless concrete typed evidence satisfies the selected replan.

    An exact Turn guard is the authority for work admitted in that Turn. When
    ``guard_scoped`` is true, a replan obligation applies only if that guard
    selected its exact generation. A different or newly derived obligation is
    preserved for the next Turn instead of retroactively rejecting authorized
    delivery.
    """

    obligation, semantic_delta = qualify_replan_writeback(
        newest_first_runs=newest_first_runs,
        state_text=state_text,
        agent_id=agent_id,
        goal_id=goal_id,
        progress_observation=progress_observation,
        registry_goal=registry_goal,
        external_progress_review=external_progress_review,
        effective_turn_cadence=effective_turn_cadence,
        agent_vision=agent_vision,
        completion_todo_id=completion_todo_id,
        completion_turn_key=completion_turn_key,
        todo_fields=todo_fields,
        guard_scoped=guard_scoped,
        guard_semantic_replan_obligation_id=guard_semantic_replan_obligation_id,
        capability_evidence=capability_evidence, guard_capability=guard_capability,
    )
    if not obligation:
        if isinstance(semantic_delta, dict) and semantic_delta.get("accepted") is True:
            if not guard_scoped or semantic_delta.get("obligation_id") == (
                guard_semantic_replan_obligation_id
            ):
                return semantic_delta
        return None
    if guard_scoped and str(obligation.get("obligation_id") or "").strip() != str(
        guard_semantic_replan_obligation_id or ""
    ).strip():
        return None
    if isinstance(semantic_delta, dict) and semantic_delta.get("accepted") is True:
        return semantic_delta
    if isinstance(semantic_delta, dict) and semantic_delta.get("reason_code"):
        raise ReplanWritebackRejected(
            str(semantic_delta.get("reason") or "invalid replan writeback"),
            obligation=obligation,
            semantic_delta=semantic_delta,
        )
    raise ReplanWritebackRejected(
        (
            "an open autonomous replan obligation requires a typed semantic delta; "
            "this writeback does not change an accepted surface, hypothesis, probe "
            "family, concrete blocker, coverage-backed terminal, or required vision "
            "outcome. Host-projected replan context is authoritative; write a typed "
            "progress observation or a fresh evidence-linked vision path outcome."
        ),
        obligation=obligation,
        semantic_delta=semantic_delta,
    )


def qualify_refresh_replan_writeback(
    *,
    autonomous_replan_recorded: bool,
    requested_delta_kinds: list[str],
    active_state_next_action_update: dict[str, Any] | None,
    agent_vision: dict[str, Any] | None,
    existing_agent_vision: dict[str, Any] | None,
    agent_id: str,
    dry_run: bool,
    settlement_todo_id: str | None,
    settlement_guard_scoped: bool,
    settlement_guard_semantic_replan_obligation_id: str | None,
    newest_first_runs: list[dict[str, Any]],
    state_text: str,
    goal_id: str,
    progress_observation: dict[str, Any] | None,
    registry_goal: dict[str, Any] | None,
    external_progress_review: Mapping[str, Any] | None = None,
    effective_turn_cadence: dict[str, Any] | None = None,
    completion_todo_id: str | None,
    completion_turn_key: str | None,
    classification: str,
    delivery_outcome: str | None,
    todo_fields: dict[str, Any] | None = None,
    capability_evidence: CapabilityReplanEvidence | None = None,
    guard_capability: Mapping[str, Any] | None = None,
) -> RefreshReplanQualification:
    """Qualify one refresh's replan delta and accountable settlement outcome."""

    requested_classification = classification
    requested_delivery_outcome = delivery_outcome
    repair_delta_contract = None
    frontier_identity = None
    effective_recorded = bool(autonomous_replan_recorded)
    if autonomous_replan_recorded:
        repair_delta_contract = build_repair_delta_contract(
            requested_delta_kinds=requested_delta_kinds,
            active_state_next_action_update=active_state_next_action_update,
            agent_vision=agent_vision,
            existing_agent_vision=existing_agent_vision,
            agent_todo_summary=(
                todo_fields if todo_fields is not None
                else parse_active_state_todos(state_text, item_limit=None)
            ).get("agent_todos"),
            agent_id=agent_id or None,
            dry_run=dry_run,
            selected_todo_id=settlement_todo_id,
            newest_first_runs=newest_first_runs,
        )
        validated_delta_kinds = list(repair_delta_contract.get("delta_kinds") or [])
        if not repair_delta_contract["delta_present"]:
            normalized = str(classification or "").strip().lower()
            classification = (
                "repair_noop"
                if "repair" in normalized and "replan" not in normalized
                else "replan_noop"
            )
            effective_recorded = False
            if delivery_outcome in {"outcome_progress", "primary_goal_outcome"}:
                delivery_outcome = "outcome_gap"
        elif (
            delivery_outcome in {"outcome_progress", "primary_goal_outcome"}
            and not repair_delta_kinds_have_accountable_progress(
                validated_delta_kinds
            )
        ):
            delivery_outcome = "outcome_gap"
        if effective_recorded and agent_id:
            frontier_identity = latest_monitor_replan_frontier_identity(
                newest_first_runs,
                agent_id=agent_id,
                watch_todo_ids=watch_lane_continuation_todo_ids(
                    repair_delta_contract
                ),
            )

    semantic_delta = enforce_open_replan_writeback(
        capability_evidence=capability_evidence, guard_capability=guard_capability,
        newest_first_runs=newest_first_runs,
        state_text=state_text,
        agent_id=agent_id,
        goal_id=goal_id,
        progress_observation=progress_observation,
        registry_goal=registry_goal,
        external_progress_review=external_progress_review,
        effective_turn_cadence=effective_turn_cadence,
        agent_vision=agent_vision,
        completion_todo_id=completion_todo_id,
        completion_turn_key=completion_turn_key,
        guard_scoped=settlement_guard_scoped,
        todo_fields=todo_fields,
        guard_semantic_replan_obligation_id=(
            settlement_guard_semantic_replan_obligation_id
        ),
    )
    if semantic_delta:
        if semantic_delta.get("retirement") and delivery_outcome != "outcome_gap":
            raise ValueError("capability duty retirement requires outcome_gap; it is not delivery progress")
        effective_recorded = True
        classification = requested_classification
        delivery_outcome = requested_delivery_outcome
    return RefreshReplanQualification(
        repair_delta_contract=repair_delta_contract,
        semantic_delta=semantic_delta,
        frontier_identity=frontier_identity,
        classification=classification,
        delivery_outcome=delivery_outcome,
        autonomous_replan_recorded=effective_recorded,
    )
