from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from ..control_plane.runtime.trajectory_hygiene import build_trajectory_hygiene_summary
from ..history import (
    collect_history,
    inspect_index_duplicates,
    load_registry,
    repair_index_duplicates,
    rebuild_index_artifact_collisions,
    render_history_markdown,
    render_index_duplicate_inspection_markdown,
    render_index_duplicate_repair_markdown,
    render_index_collision_rebuild_markdown,
)
from ..paths import resolve_runtime_root
from ..presentation.renderers.trajectory_hygiene_markdown import (
    render_trajectory_hygiene_markdown,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]


def register_history_command(subparsers: argparse._SubParsersAction) -> None:
    history_parser = subparsers.add_parser(
        "history",
        help="Read compact run history from the shared runtime root.",
    )
    history_parser.add_argument(
        "history_action",
        nargs="?",
        choices=[
            "inspect-index-duplicates",
            "repair-index-duplicates",
            "rebuild-index-collisions",
            "trajectory-hygiene",
        ],
        help=(
            "Inspect duplicate run-index identities; repair safe duplicate index rows; "
            "rebuild reviewed artifact collisions without deleting events; "
            "or audit compact-history trajectory hygiene."
        ),
    )
    history_parser.add_argument("--goal-id", help="Only show one goal.")
    history_parser.add_argument("--limit", type=int, default=10)
    history_parser.add_argument("--agent-id", help="Scope compact history to one Agent.")
    history_parser.add_argument("--evidence-ref", help="Read the exact public-safe evidence reference supplied by replan_context.")
    history_parser.add_argument(
        "--review-plan-json",
        help=(
            "For rebuild-index-collisions --execute, a JSON review plan emitted by the dry-run. "
            "Use '-' to read stdin."
        ),
    )
    history_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview append without writing. This is the default.",
    )
    history_parser.add_argument(
        "--execute",
        action="store_true",
        help="Append or repair. Without this flag, history write actions are dry-run previews.",
    )


def handle_history_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: PrintPayload,
) -> int:
    if args.agent_id and (not args.goal_id or args.history_action):
        print_payload({"ok": False, "error": "--agent-id requires --goal-id without a history action"},
                      args.format, render_history_markdown)
        return 1
    if args.evidence_ref:
        from ..control_plane.work_items.replan_context_codec import project_replan_context

        try:
            if not args.goal_id or not args.agent_id or args.history_action:
                raise ValueError("--evidence-ref requires --goal-id and --agent-id without a history action")
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(registry, runtime_root_arg, registry_path=registry_path)
            history = collect_history(
                registry_path=registry_path, runtime_root=runtime_root, goal_id=args.goal_id,
                agent_lane_id=args.agent_id, limit=max(0, args.limit),
            )
            payload = project_replan_context(
                goal_id=args.goal_id, agent_id=args.agent_id,
                runs=(), evidence_ref=args.evidence_ref,
                source_status={"run_history": history, "registry": str(registry_path),
                               "runtime_root": str(runtime_root)},
            )
        except Exception as exc:
            payload = {"ok": False, "error": str(exc)}
        print_payload(payload, args.format, lambda value: json.dumps(value, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1
    if args.history_action == "trajectory-hygiene":
        try:
            if not args.goal_id:
                raise ValueError("history trajectory-hygiene requires --goal-id")
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                runtime_root_arg,
                registry_path=registry_path,
            )
            history = collect_history(
                registry_path=registry_path,
                runtime_root=runtime_root,
                goal_id=args.goal_id,
                limit=max(0, args.limit),
            )
            payload = build_trajectory_hygiene_summary(history)
            payload["registry"] = str(registry_path)
            payload["runtime_root"] = str(runtime_root)
        except Exception as exc:
            payload = {
                "ok": False,
                "registry": str(registry_path),
                "runtime_root": runtime_root_arg,
                "goal_filter": args.goal_id,
                "error": str(exc),
            }
        print_payload(payload, args.format, render_trajectory_hygiene_markdown)
        return 0 if payload.get("ok") else 1

    if args.history_action == "inspect-index-duplicates":
        try:
            payload = inspect_index_duplicates(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                goal_id=args.goal_id,
                limit=args.limit,
            )
        except Exception as exc:
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                runtime_root_arg,
                registry_path=registry_path,
            )
            payload = {
                "ok": False,
                "registry": str(registry_path),
                "runtime_root": str(runtime_root),
                "goal_filter": args.goal_id,
                "error": str(exc),
            }
        print_payload(payload, args.format, render_index_duplicate_inspection_markdown)
        return 0 if payload.get("ok") else 1

    if args.history_action == "repair-index-duplicates":
        try:
            payload = repair_index_duplicates(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                goal_id=args.goal_id,
                limit=args.limit,
                execute=bool(args.execute),
            )
        except Exception as exc:
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                runtime_root_arg,
                registry_path=registry_path,
            )
            payload = {
                "ok": False,
                "dry_run": not bool(args.execute),
                "registry": str(registry_path),
                "runtime_root": str(runtime_root),
                "goal_filter": args.goal_id,
                "error": str(exc),
            }
        print_payload(payload, args.format, render_index_duplicate_repair_markdown)
        return 0 if payload.get("ok") else 1

    if args.history_action == "rebuild-index-collisions":
        try:
            if args.dry_run and args.execute:
                raise ValueError(
                    "history rebuild-index-collisions accepts either --dry-run or --execute, not both"
                )
            reviewed_plan = None
            if args.review_plan_json:
                raw_plan = (
                    sys.stdin.read()
                    if args.review_plan_json == "-"
                    else Path(args.review_plan_json)
                    .expanduser()
                    .read_text(encoding="utf-8")
                )
                reviewed_plan = json.loads(raw_plan)
                if not isinstance(reviewed_plan, dict):
                    raise ValueError("--review-plan-json must contain a JSON object")
            payload = rebuild_index_artifact_collisions(
                registry_path=registry_path,
                runtime_root_override=runtime_root_arg,
                goal_id=args.goal_id,
                limit=args.limit,
                reviewed_plan=reviewed_plan,
                execute=bool(args.execute),
            )
        except Exception as exc:
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(
                registry,
                runtime_root_arg,
                registry_path=registry_path,
            )
            payload = {
                "ok": False,
                "dry_run": not bool(args.execute),
                "registry": str(registry_path),
                "runtime_root": str(runtime_root),
                "goal_filter": args.goal_id,
                "error": str(exc),
            }
        print_payload(payload, args.format, render_index_collision_rebuild_markdown)
        return 0 if payload.get("ok") else 1

    try:
        registry = load_registry(registry_path)
        runtime_root = resolve_runtime_root(
            registry,
            runtime_root_arg,
            registry_path=registry_path,
        )
        payload = collect_history(
            registry_path=registry_path,
            runtime_root=runtime_root,
            goal_id=args.goal_id,
            limit=max(0, args.limit),
            agent_lane_id=args.agent_id,
            scoped_agent_id=args.agent_id,
        )
        if args.agent_id:
            # Scope every row-bearing history surface, including retained
            # semantic references. The global quota metadata stays goal-wide.
            def scoped(rows):
                return [row for row in rows if row.get("agent_id") == args.agent_id][:max(0, args.limit)]

            payload["runs"] = scoped(payload["runs"])
            for goal in payload["goals"]:
                goal["latest_runs"] = scoped(goal["latest_runs"])
                latest = goal.get("latest_status_run")
                if latest and latest.get("agent_id") != args.agent_id:
                    goal["latest_status_run"] = None
                semantic = goal["semantic_history"]
                semantic["agents"] = scoped(semantic["agents"])
                semantic["active_blocked_retry_runs"] = scoped(semantic["active_blocked_retry_runs"])
                correction = semantic.get("latest_owner_correction_run")
                if correction and correction.get("agent_id") != args.agent_id:
                    del semantic["latest_owner_correction_run"]
    except Exception as exc:
        payload = {
            "ok": False,
            "registry": str(registry_path),
            "runtime_root": runtime_root_arg,
            "error": str(exc),
        }
    print_payload(payload, args.format, render_history_markdown)
    return 0 if payload.get("ok") else 1
