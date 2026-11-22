"""Settlement and post-writeback hook helpers for `loopx todo` commands."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import shlex
from typing import Any

from ..capabilities.todo_replan_cadence.machine_defaults import (
    resolve_todo_replan_cadence_goal,
)
from ..control_plane.agents.capability_gate import (
    runtime_capabilities_for_cli_projection,
)
from ..control_plane.capability_hooks import PostWritebackHookRegistration
from ..control_plane.coordination.local_authority import (
    read_canonical_todo_fields_if_promoted,
)
from ..control_plane.quota.settlement import (
    QuotaSettlementReadback,
    SettlementIdentity,
    SettlementStepKind,
    attach_settlement_progress,
    build_turn_scoped_cli_settlement_plan,
    read_heartbeat_settlement,
    settlement_result_payload,
    settlement_step_command,
)
from ..control_plane.runtime.time import chronology_key
from ..control_plane.todos.contract import replan_successor_semantic_binding
from ..control_plane.turn_driver.journal_store import turn_journal_observed_capabilities
from ..control_plane.work_items.replan_history_codec import (
    effective_turn_cadence_context,
)
from ..control_plane.work_items.semantic_replan_writeback import (
    qualify_replan_writeback,
)
from ..history import load_index, load_registry
from ..paths import resolve_runtime_root
from ..registry import registry_goals
from .post_writeback import (
    PostWritebackProjectionBuilder,
    dispatch_committed_cli_post_writeback_hooks,
    post_writeback_source_failure,
)


def _read_todo_turn_settlement(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root: Path,
    goal_ref: dict[str, str] | None,
) -> QuotaSettlementReadback:
    """Transport the original lifecycle tuple to the TS identity owner."""
    readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        todo_id=args.todo_id,
        turn_instance_id=args.turn_instance_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if readback is None:
        raise RuntimeError("exact settlement readback unexpectedly returned not-found")
    if readback.identity.failure is not None:
        raise ValueError(readback.identity.failure.reason)
    if readback.identity.value is None:
        operation = "completion" if args.todo_command == "complete" else "supersede"
        raise ValueError(f"turn-scoped Todo {operation} has no identity")
    return readback


def _completion_settlement_error(
    settlement_readback: QuotaSettlementReadback,
    *,
    no_follow_up: bool,
) -> str | None:
    # Todo acceptance and Turn settlement are distinct facts. Ordinary
    # completion can precede accounting (including controller validation).
    # Only terminal intent requires the full chain before closing out.
    if not no_follow_up or settlement_readback.settlement.failure is None:
        return None
    return (
        "terminal no-follow-up closeout requires matching writeback and quota spend receipts: "
        + settlement_readback.settlement.failure.reason
    )


def _todo_settlement_plan(
    identity: SettlementIdentity,
    *,
    args: argparse.Namespace,
    registry_path: Path,
    runtime_root: Path,
    goal_ref: dict[str, str] | None,
    quota_spend_source: str = "heartbeat",
) -> dict[str, object]:
    """Render the native plan with the original route and supplied lease facts."""
    actor_args = ""
    path_args = ""
    for name, option in (
        ("project", "--project"),
        ("state_file", "--state-file"),
        ("claim_operation_id", "--claim-operation-id"),
        ("task_lease_idempotency_key", "--task-lease-idempotency-key"),
        ("task_lease_expected_version", "--task-lease-expected-version"),
    ):
        value = getattr(args, name, None)
        if value is not None:
            argument = f" {option} {shlex.quote(str(value))}"
            actor_args += argument
            if name in {"project", "state_file"}:
                path_args += argument
    prefix = (
        f"loopx --registry {shlex.quote(str(registry_path))}"
        f" --runtime-root {shlex.quote(str(runtime_root))}"
    )
    plan = build_turn_scoped_cli_settlement_plan(
        goal_id=identity.goal_id,
        agent_id=identity.agent_id,
        todo_id=identity.todo_id,
        replan_obligation_id=identity.replan_obligation_id,
        turn_instance_id=identity.turn_instance_id,
        command_prefix=prefix,
        scoped_cli_args="",
        lifecycle_actor_args=actor_args,
        writeback_path_args=path_args,
        goal_ref=goal_ref,
        quota_spend_source=quota_spend_source,
        composed_settlement=True,
    ).as_dict()

    from ..capabilities.explore.turn_context import project_settlement_attachment

    return project_settlement_attachment(
        plan, registry_path=registry_path, runtime_root=runtime_root
    )


def _validated_replan_successor_obligation(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
) -> str | None:
    requested = str(getattr(args, "replan_obligation_id", None) or "").strip()
    if not requested:
        return None
    if not (
        args.role == "agent"
        and args.task_class == "advancement_task"
        and args.claimed_by
    ):
        raise ValueError(
            "--replan-obligation-id requires --role agent --task-class "
            "advancement_task and --claimed-by"
        )
    if (
        replan_successor_semantic_binding(
            action_kind=args.action_kind,
            target_key=args.monitor_target_key,
            explore_result_node_refs=args.explore_result_node_refs,
        )
        is None
    ):
        raise ValueError(
            "--replan-obligation-id requires --action-kind and either "
            "--target-key or --explore-result-node-ref"
        )
    registry = load_registry(registry_path)
    runtime_root = resolve_runtime_root(registry, runtime_root_arg)
    todo_fields = read_canonical_todo_fields_if_promoted(
        runtime_root=runtime_root,
        goal_id=args.goal_id,
    )
    state_text = ""
    existing_runs, _ = load_index(
        runtime_root / "goals" / args.goal_id / "runs" / "index.jsonl"
    )
    newest_first_runs = [
        run
        for _, run in sorted(
            enumerate(existing_runs),
            key=lambda item: (
                *chronology_key(item[1].get("generated_at")),
                item[0],
            ),
            reverse=True,
        )
    ]
    registry_goal = next(
        (
            item
            for item in registry_goals(registry)
            if str(item.get("id") or "").strip() == args.goal_id
        ),
        None,
    )
    from ..capabilities.explore.research_frontier import (
        prepare_research_replan_evidence,
    )

    capability_evidence = prepare_research_replan_evidence(
        runtime_root=runtime_root,
        goal_id=args.goal_id,
        agent_id=args.claimed_by,
        registry_goal=registry_goal,
        state_text=state_text,
    )
    obligation, _ = qualify_replan_writeback(
        capability_evidence=capability_evidence,
        todo_fields=todo_fields,
        newest_first_runs=newest_first_runs,
        state_text=state_text,
        agent_id=args.claimed_by,
        goal_id=args.goal_id,
        registry_goal=registry_goal,
        effective_turn_cadence=effective_turn_cadence_context(
            resolve_todo_replan_cadence_goal(
                registry_goal or {"id": args.goal_id},
                runtime_root,
            ),
            runtime_root,
            registry_path=registry_path,
        ),
    )
    current = str((obligation or {}).get("obligation_id") or "").strip()
    if not current:
        raise ValueError(
            "--replan-obligation-id was provided but this agent has no open "
            "replan obligation"
        )
    if current != requested:
        raise ValueError(
            "--replan-obligation-id does not match the current open obligation: "
            f"expected {current}"
        )
    if (obligation or {}).get("capability_guard"):
        from ..capabilities.explore.research_evidence import _research_result

        if capability_evidence is None:
            raise ValueError("selected capability successor owner is not available")
        _research_result(
            "explore.research.composition_successor",
            {
                "frontier": capability_evidence.frontier,
                "agent_id": args.claimed_by,
                "obligation_id": requested,
                "todo": {
                    "replan_obligation_id": requested,
                    "claimed_by": args.claimed_by,
                    "action_kind": args.action_kind,
                    "task_class": args.task_class,
                    "explore_result_node_refs": args.explore_result_node_refs,
                    "target_key": args.monitor_target_key,
                    "status": args.status or "open",
                    "resume_when": args.resume_when,
                },
            },
        )
    return current


def reconcile_todo_complete_settlement(
    payload: dict[str, object],
    *,
    args: argparse.Namespace,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_ref: dict[str, str] | None,
    settlement_identity: SettlementIdentity,
) -> None:
    runtime_root = resolve_runtime_root(
        load_registry(registry_path),
        runtime_root_arg,
    )
    settlement_readback = read_heartbeat_settlement(
        runtime_root,
        goal_id=args.goal_id,
        agent_id=args.agent_id,
        todo_id=args.todo_id,
        turn_instance_id=settlement_identity.turn_instance_id,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if settlement_readback is None:
        raise RuntimeError("exact settlement readback unexpectedly returned not-found")
    settlement_result = (
        settlement_readback.terminal_settlement
        if args.no_follow_up and settlement_identity is not None
        else settlement_readback.identity
    )
    payload["settlement_result"] = settlement_result_payload(settlement_result)
    if settlement_result.failure is not None:
        payload["ok"] = False
        payload["receipt_repair_required"] = True
        payload["error"] = settlement_result.failure.reason
    elif not args.no_follow_up:
        attach_settlement_progress(
            payload,
            settlement_readback,
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_ref=goal_ref,
        )
        progress = settlement_readback.progress
        assert progress is not None
        if progress.get("next_step") == "durable_writeback":
            identity = settlement_readback.identity.value
            assert identity is not None
            plan = _todo_settlement_plan(
                identity,
                args=args,
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_ref=goal_ref,
                quota_spend_source=progress["quota_spend_source"],
            )
            payload["settlement_plan"] = plan
            payload["next_cli_actions"] = [
                settlement_step_command(plan, SettlementStepKind.DURABLE_WRITEBACK)
            ]


def dispatch_todo_complete_post_writeback(
    payload: dict[str, object],
    *,
    args: argparse.Namespace,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_ref: dict[str, str] | None,
    settlement_identity: SettlementIdentity,
    post_writeback_hooks: Sequence[PostWritebackHookRegistration],
    post_writeback_projection_builder: PostWritebackProjectionBuilder | None,
    admission_factory: Any,
) -> None:
    identity = settlement_identity.as_dict()
    committed_at = str(payload.get("updated_at") or "").strip()
    receipt_id = payload.get("completion_receipt_id")
    if not committed_at:
        return
    try:
        admission = admission_factory.for_plan(
            registry_path=registry_path,
            goal_id=args.goal_id,
            planned_goal_ref=goal_ref,
        )
        with admission.current_lifetime(
            operation="todo_post_writeback_hooks",
        ):
            # The source lifetime lock keeps the journal evidence and
            # its consumers bound to the same exact Goal instance.
            observed = turn_journal_observed_capabilities(
                resolve_runtime_root(
                    load_registry(registry_path),
                    runtime_root_arg,
                ),
                settlement_identity=identity,
                goal_ref=goal_ref,
            )
            projected = runtime_capabilities_for_cli_projection(observed)
            if projected:
                payload["available_capabilities"] = projected
            payload["post_writeback_hooks"] = dispatch_committed_cli_post_writeback_hooks(
                payload=payload,
                registry_path=registry_path,
                runtime_root_arg=runtime_root_arg,
                goal_id=args.goal_id,
                event_kind="todo_complete",
                identity=identity,
                state_version=receipt_id or committed_at,
                receipt_id=receipt_id,
                committed_at=committed_at,
                hooks=post_writeback_hooks,
                projection_builder=post_writeback_projection_builder,
            )
    except Exception:
        # Optional post-writeback composition cannot invalidate or
        # repeat the Todo completion that already committed above.
        payload.pop("available_capabilities", None)
        payload["post_writeback_hooks"] = post_writeback_source_failure(
            post_writeback_hooks
        )
