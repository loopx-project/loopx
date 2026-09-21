from __future__ import annotations

import argparse
import hashlib
import json
from collections.abc import Callable
from pathlib import Path

# The projection builder and lease loader are reached through this module by
# tests that seed and read the shadow through the command surface; keep them
# importable here even when the command does not call them directly.
from ..control_plane.coordination.runtime_shadow import (  # noqa: F401
    bootstrap_coordination_runtime_shadow,
    build_runtime_shadow_source_snapshot,
    build_todo_runtime_shadow_projection,
    inspect_coordination_runtime_shadow,
    load_task_lease_runtime_shadow_records,
    qualify_coordination_runtime_shadow,
    read_coordination_runtime_shadow_todo_candidate,
    review_local_coordination_authority_promotion,
    resolve_coordination_runtime_shadow_config,
    rollback_coordination_runtime_shadow,
)
from ..history import load_registry
from ..paths import resolve_runtime_root
from ..registry import find_registry_goal
from ..state_refresh import resolve_goal_state


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]], None
]


def register_coordination_shadow_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "coordination-shadow",
        help="Inspect or bootstrap the default-off Stage 2C file shadow.",
    )
    add_subcommand_format(parser)
    actions = parser.add_subparsers(
        dest="coordination_shadow_command",
        required=True,
    )
    for name, help_text in (
        ("inspect", "Compare the current legacy projection with the file shadow."),
        (
            "qualify",
            "Validate bounded parity and transaction coverage for the active outbox lineage.",
        ),
        (
            "read-candidate",
            "Read one Todo from a freshly qualified bounded file shadow.",
        ),
        (
            "promote",
            "Preview or explicitly apply the reviewed whole-Goal coordination-authority cutover.",
        ),
        (
            "bootstrap",
            "Import the current legacy projection into an empty file shadow.",
        ),
        ("rollback", "Quarantine one exact pre-promotion file shadow lineage."),
    ):
        action = actions.add_parser(name, help=help_text)
        action.add_argument("--goal-id", required=True)
        action.add_argument("--project", type=Path)
        action.add_argument("--state-file", type=Path)
        if name in {"bootstrap", "rollback", "promote"}:
            action.add_argument(
                "--execute",
                action="store_true",
                help="Execute the administrative effect; otherwise preview only.",
            )
        if name == "rollback":
            selector = action.add_mutually_exclusive_group(required=True)
            selector.add_argument(
                "--provider-revision",
                help="Exact file shadow revision observed by inspect.",
            )
            selector.add_argument("--bootstrap-operation-id", help="Exact pending bootstrap operation to abort and archive.")
        if name == "qualify":
            action.add_argument(
                "--minimum-operations",
                type=int,
                default=3,
                help="Minimum verified primary mutations in this bounded lineage (default: 3).",
            )
            action.add_argument(
                "--require-event-kind",
                action="append",
                default=[],
                help="Required verified outbox write class; repeat for multiple classes.",
            )
        if name == "promote":
            action.add_argument(
                "--minimum-operations",
                type=int,
                default=3,
                help="Minimum verified primary mutations in the selected lineage (default: 3).",
            )
            action.add_argument(
                "--require-event-kind",
                action="append",
                default=[],
                help="Required verified outbox write class; repeat for multiple classes.",
            )
        if name == "read-candidate":
            action.add_argument(
                "--todo-id",
                required=True,
                help="Exact Todo identity to read from the parity-matched file head.",
            )


def _projection_version(projection: dict[str, object]) -> str:
    payload = json.dumps(
        projection,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _render(payload: dict[str, object]) -> str:
    lines = [
        "# Coordination Shadow",
        "",
        f"- ok: `{str(bool(payload.get('ok'))).lower()}`",
        f"- action: `{payload.get('action')}`",
        f"- goal_id: `{payload.get('goal_id')}`",
        f"- executed: `{str(bool(payload.get('executed'))).lower()}`",
    ]
    configuration = payload.get("configuration")
    if isinstance(configuration, dict):
        lines.append(f"- configuration: `{configuration.get('reason_code')}`")
    inspection = payload.get("inspection")
    if isinstance(inspection, dict):
        lines.extend(
            [
                f"- parity: `{inspection.get('status')}`",
                f"- decision_read_from_shadow: `{inspection.get('decision_read_from_shadow')}`",
            ]
        )
    bootstrap = payload.get("bootstrap")
    if isinstance(bootstrap, dict):
        lines.append(f"- bootstrap: `{bootstrap.get('status')}`")
    rollback = payload.get("rollback")
    if isinstance(rollback, dict):
        lines.append(f"- rollback: `{rollback.get('status')}`")
    qualification = payload.get("qualification")
    if isinstance(qualification, dict):
        lines.append(f"- qualification: `{qualification.get('status')}`")
    read_candidate = payload.get("read_candidate")
    if isinstance(read_candidate, dict):
        lines.extend(
            [
                f"- read_candidate: `{read_candidate.get('status')}`",
                f"- read_candidate_qualified: `{read_candidate.get('read_candidate_qualified')}`",
            ]
        )
    promotion = payload.get("promotion")
    if isinstance(promotion, dict):
        lines.extend(
            [
                f"- promotion: `{promotion.get('status')}`",
                f"- promotion_ready: `{promotion.get('promotion_ready')}`",
                f"- legacy_writer_fenced: `{promotion.get('legacy_writer_fenced')}`",
            ]
        )
    bounded = qualification if isinstance(qualification, dict) else read_candidate
    if isinstance(bounded, dict) and bounded.get("scope") == "bounded":
        lines.extend([
            "- qualification_scope: `bounded`",
            f"- sustained_parity_verdict: `{bounded.get('sustained_parity_verdict')}`",
        ])
        policy = bounded.get("policy")
        if isinstance(policy, dict):
            lines.append(f"- minimum_primary_mutations: `{policy.get('minimum_operations')}`")
    error = payload.get("error")
    if error:
        lines.append(f"- error: `{error}`")
    return "\n".join(lines)


def handle_coordination_shadow_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: Callable[..., str],
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "coordination-shadow":
        return None
    try:
        registry = load_registry(registry_path)
        goal = find_registry_goal(registry, args.goal_id)
        if goal is None:
            raise ValueError(f"goal {args.goal_id!r} is not present in the registry")
        config = resolve_coordination_runtime_shadow_config(goal)
        if not config.enabled:
            payload = {
                "ok": False,
                "schema_version": "loopx_coordination_shadow_admin_v0",
                "action": args.coordination_shadow_command,
                "goal_id": args.goal_id,
                "executed": False,
                "configuration": {
                    "enabled": False,
                    "provider": config.provider,
                    "reason_code": config.reason_code,
                },
                "error": "coordination shadow is not explicitly enabled for this goal",
                "error_code": "coordination_shadow_not_enabled",
                "decision_read_from_shadow": False,
            }
            print_payload(payload, output_format(args), _render)
            return 1
        runtime_root = resolve_runtime_root(
            registry,
            runtime_root_arg,
            registry_path=registry_path,
        )
        _, _, state_path = resolve_goal_state(registry=registry, goal_id=args.goal_id,
            project_override=args.project, state_file_override=args.state_file)
        if args.coordination_shadow_command == "rollback":
            projection, source_snapshot = {}, {"state_path": str(state_path)}
        else:
            projection, source_snapshot = build_runtime_shadow_source_snapshot(goal=goal,
                runtime_root=runtime_root, state_path=state_path, registry_path=registry_path)
        projection_version = _projection_version(projection)
        projected_todos = projection.get("todos")
        projected_leases = projection.get("leases")
        inspection = {"status": "not_evaluated"} if args.coordination_shadow_command == "rollback" else inspect_coordination_runtime_shadow(
            goal=goal,
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            projection=projection,
            source_snapshot=source_snapshot,
        )
        payload: dict[str, object] = {
            "ok": inspection.get("status") != "failed",
            "schema_version": "loopx_coordination_shadow_admin_v0",
            "action": args.coordination_shadow_command,
            "goal_id": args.goal_id,
            "executed": False,
            "configuration": {
                "enabled": config.enabled,
                "provider": config.provider,
                "reason_code": config.reason_code,
            },
            "source_version": f"legacy-projection:{projection_version}",
            "projection_summary": {
                "todo_count": len(projected_todos)
                if isinstance(projected_todos, list)
                else 0,
                "lease_count": len(projected_leases)
                if isinstance(projected_leases, list)
                else 0,
            },
            "inspection": inspection,
            "decision_read_from_shadow": False,
        }
        if args.coordination_shadow_command == "bootstrap" and args.execute:
            from ..control_plane.coordination.shadow_management import read_shadow_management_state
            management = read_shadow_management_state(runtime_root, args.goal_id)
            if management is not None and management["status"] in {"bootstrapping", "active"}:
                operation_id = str(management["operation"]["operation_id"])
            else:
                predecessor = management["operation"]["operation_id"] if management and management["status"] == "inactive" else "initial"
                operation_digest = _projection_version({"predecessor": predecessor, "projection": projection,
                    "source_snapshot": source_snapshot, "runtime_root": str(runtime_root)})
                operation_id = f"shadow-bootstrap:{args.goal_id}:{operation_digest}"
            bootstrap = bootstrap_coordination_runtime_shadow(
                goal=goal,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                operation_id=operation_id,
                source_version=str(payload["source_version"]),
                projection=projection,
                source_snapshot=source_snapshot,
            )
            payload["executed"] = True
            payload["bootstrap"] = bootstrap
            if bootstrap.get("status") in {"applied", "replayed", "recovered"}:
                payload["inspection"] = inspect_coordination_runtime_shadow(
                    goal=goal,
                    runtime_root=runtime_root,
                    goal_id=args.goal_id,
                    projection=projection,
                    source_snapshot=source_snapshot,
                )
            final_inspection = payload["inspection"]
            payload["ok"] = bool(
                bootstrap.get("status") in {"applied", "replayed", "recovered"}
                and isinstance(final_inspection, dict)
                and final_inspection.get("status") == "matched"
            )
        if args.coordination_shadow_command == "qualify":
            qualification = qualify_coordination_runtime_shadow(
                goal=goal,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                projection=projection,
                source_snapshot=source_snapshot,
                minimum_operations=args.minimum_operations,
                required_event_kinds=args.require_event_kind,
            )
            payload["qualification"] = qualification
            payload["ok"] = qualification.get("status") == "qualified"
        if args.coordination_shadow_command == "read-candidate":
            read_candidate = read_coordination_runtime_shadow_todo_candidate(
                goal=goal,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                todo_id=args.todo_id,
                projection=projection,
                source_snapshot=source_snapshot,
            )
            payload["read_candidate"] = read_candidate
            payload["ok"] = bool(
                read_candidate.get("status") == "matched"
                and read_candidate.get("read_candidate_qualified") is True
                and read_candidate.get("decision_read_from_shadow") is False
            )
        if args.coordination_shadow_command == "promote":
            operation_digest = _projection_version(
                {
                    "goal_id": args.goal_id,
                    "projection": projection,
                    "minimum_operations": args.minimum_operations,
                    "required_event_kinds": args.require_event_kind,
                }
            )
            promotion = review_local_coordination_authority_promotion(
                goal=goal,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                operation_id=f"promote:{args.goal_id}:{operation_digest}",
                projection=projection,
                source_snapshot=source_snapshot,
                minimum_operations=args.minimum_operations,
                required_event_kinds=args.require_event_kind,
                execute=bool(args.execute),
            )
            payload["executed"] = bool(args.execute)
            payload["promotion"] = promotion
            payload["ok"] = promotion.get("status") in {
                "preview_ready",
                "applied",
                "replayed",
                "recovered",
            }
        if args.coordination_shadow_command == "rollback":
            provider_revision = getattr(args, "provider_revision", None)
            pending_bootstrap = getattr(args, "bootstrap_operation_id", None)
            payload["expected_provider_revision"] = provider_revision
            payload["expected_bootstrap_operation_id"] = pending_bootstrap
            if args.execute:
                rollback = rollback_coordination_runtime_shadow(
                    goal=goal, runtime_root=runtime_root, goal_id=args.goal_id,
                    operation_id=f"shadow-rollback:{args.goal_id}:{provider_revision or pending_bootstrap}",
                    expected_provider_revision=provider_revision,
                    expected_bootstrap_operation_id=pending_bootstrap,
                    projection=projection, source_snapshot=source_snapshot,
                )
                payload["executed"] = True
                payload["rollback"] = rollback
                payload["ok"] = rollback.get("status") in {"applied", "replayed", "recovered"}
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_coordination_shadow_admin_v0",
            "action": getattr(args, "coordination_shadow_command", None),
            "goal_id": getattr(args, "goal_id", None),
            "executed": False,
            "error": str(exc),
            "error_code": exc.__class__.__name__,
            "decision_read_from_shadow": False,
        }
    print_payload(payload, output_format(args), _render)
    return 0 if payload.get("ok") else 1
