"""One shared fresh Turn decision for every Turn subcommand that needs one.

``run-once`` and ``managed-step`` must agree on what "the current governing
decision" means: read the same live status, apply the same controller advisory
primary, and sign the same ``loopx_turn_envelope_v0``. Duplicating that chain
would let the two drift, so both owners build through this module.

The owner carries the inputs the decision was derived from as well, because a
Turn that later spends quota or re-checks the scheduler must use the same live
status, the same scheduler context and the same activation-bound capability
hooks as the decision it already took. A call site that re-derives one of those
inputs is free to drift from the decision it is settling.

Nothing here executes, writes, or spends: it only projects the control plane's
current decision into the envelope the loop controller consumes.
"""

from __future__ import annotations

import argparse
import shlex
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..capabilities.explore.composition_frontier import (
    project_live_explore_composition_frontier,
)
from ..capabilities.periodic_report.pending_intent import (
    periodic_report_pending_intent_interaction_hook,
)
from ..control_plane.quota.live_decision import build_live_quota_should_run_decision
from ..control_plane.quota.effective_action import EffectiveAction
from ..control_plane.quota.turn_envelope import build_turn_envelope
from ..control_plane.quota.settlement import SettlementIdentity, build_turn_scoped_cli_settlement_plan
from ..control_plane.quota.heartbeat_receipt import (
    find_heartbeat_receipt,
    heartbeat_receipt_settlement_replan_obligation_id,
)
from ..control_plane.goals.first_party_host_admission import FirstPartyHostRuntimeRejected
from ..control_plane.turn_driver.driver import build_loopx_turn_plan, selected_turn_todo
from ..control_plane.scheduler.execution_context import (
    scheduler_execution_context_for_turn,
)
from ..status import AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK, collect_status
from ..control_plane.effect_runtime import effect_runtime_result
from ..control_plane.work_items.autonomous_replan_obligation import replan_obligation_id_from_packet
from .lark_inbox import build_lark_operator_inbox_urgency_projector
from .turn_selection import turn_controller_advisory_primary

#: The route source every Turn owner attributes its decision to.
TURN_DECISION_ROUTE_SOURCE = "loopx_turn_plan"


def _attach_original_replan_settlement(
    decision: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, agent_id: str,
    turn_instance_id: str | None, goal_ref: Mapping[str, object] | None,
) -> dict[str, Any]:
    """Give an outer executor the existing quota-owned scoped plan.

    Outer-controller profiles omit in-agent settlement commands because the
    controller executes effects. Their genuine planning binding still belongs
    to the same shared settlement owner, not to a made-up Todo or Host session.
    """
    obligation = replan_obligation_id_from_packet(decision.get("replan_action_packet"))
    if not turn_instance_id or not obligation or decision.get("should_run") is not True:
        return decision
    agent = (decision.get("interaction_contract") or {}).get("agent_channel") or {}
    if (agent.get("must_attempt") is True and agent.get("delivery_allowed") is False
            and decision.get("effective_action") == EffectiveAction.AUTONOMOUS_REPLAN_REQUIRED.value):
        # A fallback business Todo is context, not the selected owner of a
        # required semantic replan. Bind the existing obligation so launch,
        # writeback and spend all retain that same governance identity.
        decision.pop("selected_todo", None)
    if decision.get("selected_todo"):
        return decision
    cli = (decision.get("interaction_contract") or {}).get("cli_channel")
    if not isinstance(cli, dict) or cli.get("settlement_plan"):
        return decision
    cli["settlement_plan"] = build_turn_scoped_cli_settlement_plan(
        goal_id=goal_id, agent_id=agent_id,
        replan_obligation_id=obligation, turn_instance_id=turn_instance_id,
        command_prefix=shlex.join(["loopx", "--registry", str(registry_path),
                                  "--runtime-root", str(runtime_root)]),
        scoped_cli_args="", lifecycle_actor_args="", goal_ref=goal_ref,
    ).as_dict()
    return decision


class TurnTodoSelectionError(ValueError):
    """Transport quota's existing refusal, without a new admission rule."""

    def __init__(self, projection: dict[str, Any]) -> None:
        self.code = projection["error_code"]
        self.payload = {"selection_rejection": projection["selection_rejection"]}
        reason = projection["selection_rejection"].get("reason_code")
        super().__init__(
            "Requested Turn Todo is not currently eligible; no alternate task was selected"
            + (f"; reason={reason}" if reason else "")
        )


def collect_turn_status_payload(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> dict[str, Any]:
    """Read the live status the Turn decision is derived from."""

    return collect_status(
        registry_path=registry_path,
        runtime_root_override=runtime_root_arg,
        limit=max(max(0, args.limit), AUTONOMOUS_REPLAN_PERIODIC_LOOKBACK),
        goal_id=args.goal_id,
        available_capabilities=args.available_capabilities,
        agent_lane_id=args.agent_id,
    )


def _build_turn_decision(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root: Path,
    status_payload: Mapping[str, Any],
    scheduler_execution_context: Mapping[str, Any],
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]],
    turn_start_hook_dispatch: Mapping[str, Any] | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> Callable[..., dict[str, Any]]:
    """Return the ``build_turn_decision`` every Turn owner resolves through.

    ``turn_start_hook_dispatch`` is the caller's business: an executing Turn
    may publish Go/No-Go hooks before deciding, while a read-only managed step
    must not. Passing the projection in keeps that choice with the caller.
    Every other input is the owner's, so the two commands cannot disagree about
    the status, the scheduler context or the capability hooks behind a decision.
    """

    # run-once executes in its explicit project, not the process that requests
    # the plan (for example the App or delegation service). Resolve once and
    # carry only that host fact into the original workspace guard. Commands
    # without a host project retain the established invocation-cwd boundary.
    project = getattr(args, "project", None)
    workspace_path = (
        Path(project).expanduser().resolve()
        if getattr(args, "turn_command", None) == "run-once" and project is not None else None
    )
    turn_instance_id = getattr(args, "turn_instance_id", None)
    receipt = (find_heartbeat_receipt(runtime_root, goal_id=args.goal_id,
        agent_id=args.agent_id, turn_instance_id=turn_instance_id, goal_ref=goal_ref)
        if turn_instance_id else None)
    bound_replan = heartbeat_receipt_settlement_replan_obligation_id(receipt) if receipt else None

    def build_turn_decision(
        *, requested_action_todo_id: str | None = None
    ) -> dict[str, Any]:
        decision = build_live_quota_should_run_decision(
            status_payload,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            available_capabilities=args.available_capabilities,
            include_scheduler_detail=False,
            codex_app_current_rrule=None,
            registry_path=registry_path,
            runtime_root=runtime_root,
            route_source=TURN_DECISION_ROUTE_SOURCE,
            scheduler_execution_context=scheduler_execution_context,
            operator_inbox_urgency_projector=operator_inbox_urgency_projector,
            bounded_research_frontier_projector=(
                project_live_explore_composition_frontier
            ),
            requested_action_todo_id=requested_action_todo_id,
            # Preserve ordinary Todo decision/context behavior. Only an
            # already bound empty-frontier replan needs this scoped reentry.
            receipt_bound_replan_obligation_id=bound_replan,
            turn_instance_id=turn_instance_id if bound_replan else None,
            turn_start_hook_dispatch=dict(turn_start_hook_dispatch or {}),
            workspace_path=workspace_path,
            goal_ref=goal_ref,
            interaction_projection_hooks=(
                periodic_report_pending_intent_interaction_hook(
                    registry_path=registry_path,
                    runtime_root=runtime_root,
                    goal_id=args.goal_id,
                    agent_id=args.agent_id,
                ),
            ),
        )
        return _attach_original_replan_settlement(
            decision, registry_path=registry_path, runtime_root=runtime_root,
            goal_id=args.goal_id, agent_id=args.agent_id,
            turn_instance_id=turn_instance_id, goal_ref=goal_ref,
        )

    return build_turn_decision


def apply_controller_advisory_primary(
    build_turn_decision: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Build the decision, preferring the controller's advisory primary Todo."""

    decision = build_turn_decision()
    controller_default = turn_controller_advisory_primary(decision)
    if controller_default is None:
        return decision
    primary_todo_id, advisory_portfolio = controller_default
    decision = build_turn_decision(requested_action_todo_id=primary_todo_id)
    selected_todo = decision.get("selected_todo")
    if not isinstance(selected_todo, dict) or (
        selected_todo.get("todo_id") != primary_todo_id
    ):
        raise ValueError(
            "Turn controller advisory primary failed current eligibility"
        )
    selected_todo["selected_by"] = "turn_controller_advisory_primary"
    decision["action_portfolio"] = advisory_portfolio
    return decision


@dataclass(frozen=True)
class FreshTurnDecisionOwner:
    """The one owner of a fresh Turn decision and the inputs behind it.

    A Turn that already decided must settle against what it decided with: the
    same live status, the same scheduler context and the same
    activation-bound operator-inbox projector. Carrying them here is what keeps
    a later spend or scheduler re-check on the decision's own inputs instead of
    a second reading of the same arguments.
    """

    status_payload: Mapping[str, Any]
    scheduler_execution_context: Mapping[str, Any]
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]]
    build_turn_decision: Callable[..., dict[str, Any]]
    requested_todo_id: str | None = None

    def resolve(self) -> dict[str, Any]:
        """Use an explicit eligible Todo, or preserve controller selection."""

        if self.requested_todo_id is None:
            return apply_controller_advisory_primary(self.build_turn_decision)
        decision = self.build_turn_decision(requested_action_todo_id=self.requested_todo_id)
        selected = decision.get("selected_todo")
        if not isinstance(selected, dict) or selected.get("todo_id") != self.requested_todo_id:
            summary = self.status_payload.get("contract_summary")
            raise TurnTodoSelectionError(effect_runtime_result("turn.selection.rejection", {
                "requested_todo_id": self.requested_todo_id, "decision": decision,
                "contract_error_count": summary.get("errors") if isinstance(summary, Mapping) else None,
            }))
        selected["selected_by"] = "turn_explicit_todo"
        return decision


def build_fresh_turn_decision_owner(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root: Path,
    runtime_root_arg: str | None,
    turn_start_hook_dispatch: Mapping[str, Any] | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> FreshTurnDecisionOwner:
    """Read the live status and derive the shared decision inputs from it.

    ``runtime_root_arg`` is only the override the status read forwards, as
    ``collect_status`` documents. The root every activation check uses is the
    resolved ``runtime_root``: when a registry declares
    ``common_runtime_root`` and the command omits ``--runtime-root``, the raw
    argument is None and the check would otherwise read the operator's global
    extension state instead of this registry's own.
    """

    status_payload = collect_turn_status_payload(
        args,
        registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
    )
    scheduler_execution_context = scheduler_execution_context_for_turn(
        host=args.host,
        execution_mode=args.execution_mode,
        scheduler_owner=args.scheduler_owner,
    )
    operator_inbox_urgency_projector = build_lark_operator_inbox_urgency_projector(
        runtime_root_arg=runtime_root,
    )
    return FreshTurnDecisionOwner(
        status_payload=status_payload,
        scheduler_execution_context=scheduler_execution_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        build_turn_decision=_build_turn_decision(
            args,
            registry_path=registry_path,
            runtime_root=runtime_root,
            status_payload=status_payload,
            scheduler_execution_context=scheduler_execution_context,
            operator_inbox_urgency_projector=operator_inbox_urgency_projector,
            turn_start_hook_dispatch=turn_start_hook_dispatch,
            goal_ref=goal_ref,
        ),
        requested_todo_id=getattr(args, "todo_id", None),
    )


def current_turn_host_launch_decision(
    args: argparse.Namespace, *, registry_path: Path, runtime_root: Path,
    identity: SettlementIdentity, replan_obligation_id: str | None,
    planned_effective_action: object,
    scheduler_execution_context: Mapping[str, Any],
    operator_inbox_urgency_projector: Callable[..., dict[str, Any]],
    turn_start_hook_dispatch: Mapping[str, Any] | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Re-read the original Turn's eligibility inside its launch lifetime.

    The caller supplies the real receipt identity; a Host session or turn_key
    cannot reconstruct it. This read neither creates another receipt nor
    substitutes a newly selected task into the already planned Host request.
    """
    current = build_live_quota_should_run_decision(
        collect_turn_status_payload(args, registry_path=registry_path,
                                    runtime_root_arg=str(runtime_root)),
        goal_id=identity.goal_id, agent_id=identity.agent_id,
        available_capabilities=args.available_capabilities, include_scheduler_detail=False,
        codex_app_current_rrule=None, registry_path=registry_path, runtime_root=runtime_root,
        route_source=TURN_DECISION_ROUTE_SOURCE,
        scheduler_execution_context=scheduler_execution_context,
        operator_inbox_urgency_projector=operator_inbox_urgency_projector,
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
        receipt_bound_todo_id=identity.todo_id or None,
        receipt_bound_replan_obligation_id=replan_obligation_id,
        turn_instance_id=identity.turn_instance_id,
        turn_start_hook_dispatch=turn_start_hook_dispatch,
        workspace_path=Path(args.project).expanduser().resolve(), goal_ref=goal_ref,
        interaction_projection_hooks=(periodic_report_pending_intent_interaction_hook(
            registry_path=registry_path, runtime_root=runtime_root,
            goal_id=identity.goal_id, agent_id=identity.agent_id,
        ),),
    )
    _attach_original_replan_settlement(current, registry_path=registry_path,
        runtime_root=runtime_root, goal_id=identity.goal_id, agent_id=identity.agent_id,
        turn_instance_id=identity.turn_instance_id, goal_ref=goal_ref)
    launch = {"current": current, "planned_effective_action": planned_effective_action}
    # The shared typed launch owner retains refusal precedence (including
    # Stop/witness failures) and compares the original effective action.
    if (current.get("should_run") is not True
            or current.get("effective_action") != planned_effective_action):
        return launch
    current_envelope = build_turn_envelope(
        current, scheduler_execution_context=scheduler_execution_context,
    )
    # Reuse the existing side-effect-free Host route projection, including
    # delivery, user-gate and protocol checks. No plan/session is persisted.
    plan = build_loopx_turn_plan(
        current_envelope, host=args.host, execution_mode=args.execution_mode,
        scheduler_owner=args.scheduler_owner, turn_instance_id=identity.turn_instance_id,
        goal_ref=goal_ref,
    )
    route = plan["route"]
    if route.get("would_invoke_host") is not True:
        raise FirstPartyHostRuntimeRejected("goal_launch_not_allowed")
    if selected_turn_todo(current_envelope).get("todo_id") != identity.todo_id:
        raise FirstPartyHostRuntimeRejected("current_turn_selection_changed")
    if identity.replan_obligation_id and plan["transaction"]["settlement_plan"]["identity"] != identity.as_dict():
        raise FirstPartyHostRuntimeRejected("current_turn_selection_changed")
    return launch


def current_heartbeat_host_launch_decision(
    *, registry_path: Path, runtime_root: Path, project: Path,
    goal_id: str, agent_id: str, turn_instance_id: str,
    original_quota: Mapping[str, Any] | None = None,
    goal_ref: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    """Re-read one native heartbeat/initial-plan launch without minting a receipt.

    Unlike an executing governed Turn, a first heartbeat or planning wake can
    legitimately have no chosen Todo and no committed guard yet. Reuse the
    public quota owner's original-selection reader and full live decision. A
    checkpoint continuation additionally retains its exact held packet; its
    committed binding cannot be replaced with the enclosing wake's identity.
    Call only inside the Goal launch lifetime, immediately before native spawn.
    """
    from ..control_plane.quota.heartbeat_receipt import heartbeat_receipt_view
    from ..control_plane.scheduler.execution_context import scheduler_execution_context_for_runtime_profile
    from ..turn_identity import normalize_turn_instance_id
    from .quota_action_selection import load_requested_quota_action_selection

    original_turn = normalize_turn_instance_id(turn_instance_id)
    if original_turn is None:
        raise FirstPartyHostRuntimeRejected("heartbeat_launch_turn_missing")
    args = argparse.Namespace(
        goal_id=goal_id, agent_id=agent_id, turn_instance_id=original_turn,
        todo_id=None, codex_app=False, runtime_profile="generic_cli",
        limit=0,
        available_capabilities=["shell", "filesystem_write"],
    )
    selection = load_requested_quota_action_selection(
        args, runtime_root=runtime_root, turn_instance_id=original_turn, goal_ref=goal_ref,
    )
    if original_quota is not None:
        held = original_quota.get("heartbeat_receipt")
        if (original_quota.get("goal_id") != goal_id or not isinstance(held, Mapping)
                or held.get("turn_instance_id") != original_turn or selection.receipt is None):
            raise FirstPartyHostRuntimeRejected("heartbeat_launch_original_receipt_missing")
        current_receipt = heartbeat_receipt_view(
            selection.receipt, turn_instance_id=original_turn, status="replayed",
        )
        if (not held.get("event_id") or current_receipt.get("event_id") != held.get("event_id")
                or current_receipt.get("settlement_identity") != held.get("settlement_identity")):
            raise FirstPartyHostRuntimeRejected("heartbeat_launch_original_binding_changed")
    current = build_live_quota_should_run_decision(
        collect_turn_status_payload(args, registry_path=registry_path, runtime_root_arg=str(runtime_root)),
        goal_id=goal_id, agent_id=agent_id, available_capabilities=args.available_capabilities,
        include_scheduler_detail=False, codex_app_current_rrule=None,
        registry_path=registry_path, runtime_root=runtime_root,
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile("generic_cli"),
        operator_inbox_urgency_projector=build_lark_operator_inbox_urgency_projector(runtime_root_arg=runtime_root),
        bounded_research_frontier_projector=project_live_explore_composition_frontier,
        receipt_bound_todo_id=selection.receipt_bound_todo_id,
        receipt_bound_replan_obligation_id=selection.receipt_bound_replan_obligation_id,
        retained_action_selection_todo_id=selection.retained_todo_id_for_decision,
        turn_instance_id=original_turn, workspace_path=project, goal_ref=goal_ref,
        interaction_projection_hooks=(periodic_report_pending_intent_interaction_hook(
            registry_path=registry_path, runtime_root=runtime_root, goal_id=goal_id, agent_id=agent_id,
        ),),
    )
    # Initial heartbeat/planning has not chosen an action yet. Its generic
    # prompt asks the Host to consume the current decision after launch. A
    # held continuation already has an action and cannot substitute another.
    return {"current": current, "planned_effective_action": (
        original_quota.get("effective_action") if original_quota is not None
        else current.get("effective_action"))}


def fresh_turn_envelope(
    decision: Mapping[str, Any],
    *,
    scheduler_execution_context: Any,
) -> dict[str, Any]:
    """Sign the decision into the envelope the loop controller consumes.

    The causal link to a failed predecessor is *not* written here: the envelope
    schema is a signed contract, so the managed step passes its predecessor
    beside the envelope instead of mutating it.
    """

    return build_turn_envelope(
        decision,
        scheduler_execution_context=scheduler_execution_context,
    )


def build_fresh_envelope_for_managed_step(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root: Path,
    runtime_root_arg: str | None,
) -> dict[str, Any]:
    """Project the current decision as the managed step's fresh envelope.

    Read-only: no Turn-start hooks are dispatched and no Turn instance is
    minted, because the managed step only decides whether the outer scheduler
    may wake the same failed Turn again.
    """

    owner = build_fresh_turn_decision_owner(
        args,
        registry_path=registry_path,
        runtime_root=runtime_root,
        runtime_root_arg=runtime_root_arg,
    )
    return fresh_turn_envelope(
        owner.resolve(),
        scheduler_execution_context=owner.scheduler_execution_context,
    )


__all__ = [
    "FreshTurnDecisionOwner",
    "TURN_DECISION_ROUTE_SOURCE",
    "apply_controller_advisory_primary",
    "build_fresh_envelope_for_managed_step",
    "build_fresh_turn_decision_owner",
    "collect_turn_status_payload",
    "fresh_turn_envelope",
]
