from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..global_registry import (
    render_global_goal_retirement_markdown,
    render_global_sync_markdown,
    retire_global_registry_goals,
    sync_project_registry_to_global,
)
from ..history import load_registry
from ..local_state_migration import (
    migrate_local_state,
    render_local_state_migration_markdown,
    rollback_local_state_migration,
)
from ..paths import (
    DEFAULT_RUNTIME_ROOT,
    LEGACY_RUNTIME_ROOT as LEGACY_LOCAL_RUNTIME_ROOT,
    global_registry_path,
    resolve_runtime_root,
    select_default_runtime_root,
)
from ..project_uninstall import render_project_uninstall_markdown, uninstall_project
from ..runtime import archive_runtime_goal, render_archive_runtime_markdown
from ..state_migration import (
    LEGACY_GLOBAL_REGISTRY,
    LEGACY_RUNTIME_ROOT,
    legacy_registry_goal_ids,
    migrate_legacy_state,
    parse_key_value_map,
    render_state_migration_markdown,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]

REGISTRY_LIFECYCLE_COMMANDS = {
    "archive-runtime",
    "retire-global-goal",
    "uninstall-project",
    "sync-global",
    "migrate-state",
    "migrate-local-state",
}


def register_registry_lifecycle_commands(
    subparsers: argparse._SubParsersAction,
) -> None:
    local_migration = subparsers.add_parser(
        "migrate-local-state",
        help="Preview or explicitly migrate default .codex LoopX state to .loopx; supports verified rollback.",
    )
    local_migration.add_argument("--source-runtime-root", type=Path, help="Legacy runtime root; defaults to ~/.codex/loopx.")
    local_migration.add_argument("--target-runtime-root", type=Path, help="New runtime root; defaults to ~/.loopx.")
    local_migration.add_argument("--backup-dir", type=Path, help="Private backup directory; defaults to a content-bound directory beside the source.")
    local_migration.add_argument("--expected-plan-id", help="Exact plan_id from a fresh dry-run preview; required with --execute.")
    local_migration.add_argument("--rollback-receipt", type=Path, help="Preview or roll back a completed migration receipt.")
    local_migration.add_argument("--execute", action="store_true", help="Apply the previewed migration or verified rollback.")

    archive_runtime_parser = subparsers.add_parser(
        "archive-runtime",
        help="Move an obsolete runtime goal directory into the archive area. Defaults to dry-run.",
    )
    archive_runtime_parser.add_argument("--goal-id", required=True, help="Runtime goal id to archive.")
    archive_runtime_parser.add_argument(
        "--archive-root",
        help="Archive directory. Defaults to <runtime-root>/archived-goals.",
    )
    archive_runtime_parser.add_argument(
        "--allow-registered",
        action="store_true",
        help="Allow archiving a goal that is still present in the registry.",
    )
    archive_runtime_parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually move the runtime directory. Without this flag the command is a dry-run.",
    )

    retire_global_goal_parser = subparsers.add_parser(
        "retire-global-goal",
        help=(
            "Remove explicitly named obsolete goals from the global registry only when "
            "both source_registry and state_file are missing. Defaults to dry-run."
        ),
    )
    retire_global_goal_parser.add_argument(
        "--goal-id",
        action="append",
        required=True,
        help="Obsolete global goal id to retire. Repeat for multiple explicit goals.",
    )
    retire_global_goal_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write a full registry backup, then remove the eligible goals.",
    )

    uninstall_project_parser = subparsers.add_parser(
        "uninstall-project",
        help="Disconnect the current project from LoopX without uninstalling the LoopX CLI or other projects.",
    )
    uninstall_project_parser.add_argument(
        "--goal-id",
        action="append",
        default=None,
        help="Goal id to disconnect. Repeatable; defaults to every goal in this project registry.",
    )
    uninstall_project_parser.add_argument(
        "--archive-state",
        action="store_true",
        help="Archive each selected registered Goal state directory into .loopx/archived-project-state/.",
    )
    uninstall_project_parser.add_argument(
        "--remove-empty-registry",
        action="store_true",
        help="Remove .loopx/registry.json when all local goals are uninstalled. A backup is written first on --execute.",
    )
    uninstall_project_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write registry changes. Without this flag, uninstall-project is a dry-run preview.",
    )

    sync_global_parser = subparsers.add_parser(
        "sync-global",
        help="Merge this project-local registry into the shared global registry.",
    )
    sync_global_parser.add_argument("--goal-id", help="Only sync one goal id from the source registry.")
    sync_global_parser.add_argument(
        "--replace-state",
        action="store_true",
        help="Allow replacing an existing global route and write a backup before doing so.",
    )
    sync_global_parser.add_argument("--dry-run", action="store_true", help="Preview the global registry merge.")

    migrate_state_parser = subparsers.add_parser(
        "migrate-state",
        help="One-shot migration from a legacy Goal Harness registry/runtime into LoopX state.",
    )
    migrate_state_parser.add_argument(
        "--legacy-registry",
        default=str(LEGACY_GLOBAL_REGISTRY),
        help="Legacy registry JSON to import from. Defaults to ~/.codex/goal-harness/registry.global.json.",
    )
    migrate_state_parser.add_argument(
        "--legacy-runtime-root",
        default=str(LEGACY_RUNTIME_ROOT),
        help="Legacy runtime root. Defaults to ~/.codex/goal-harness.",
    )
    migrate_state_parser.add_argument(
        "--target-runtime-root",
        help="LoopX runtime root. Defaults to --runtime-root or ~/.loopx.",
    )
    migrate_goal_selector = migrate_state_parser.add_mutually_exclusive_group(required=True)
    migrate_goal_selector.add_argument(
        "--goal-id",
        action="append",
        help="Legacy goal id to migrate. Repeat for multiple explicit goals.",
    )
    migrate_goal_selector.add_argument(
        "--all-goals",
        action="store_true",
        help="Migrate every goal listed in the explicit legacy registry. Still dry-run by default.",
    )
    migrate_state_parser.add_argument(
        "--goal-id-map",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="Rename a goal id during migration, for example goal-harness-meta=loopx-meta.",
    )
    migrate_state_parser.add_argument(
        "--path-map",
        action="append",
        default=[],
        metavar="OLD=NEW",
        help="Rewrite local path prefixes during migration.",
    )
    migrate_state_parser.add_argument(
        "--copy-active-state",
        action="store_true",
        help="Copy and rewrite selected goals' active-state files into their migrated target paths.",
    )
    migrate_state_parser.add_argument(
        "--copy-runtime",
        action="store_true",
        help="Copy and rewrite selected runtime goal directories from the legacy runtime root.",
    )
    migrate_state_parser.add_argument(
        "--no-global-sync",
        action="store_true",
        help="Do not sync the migrated project registry into the LoopX global registry after --execute.",
    )
    migrate_state_parser.add_argument(
        "--execute",
        action="store_true",
        help="Write migrated state. Without this flag the command is a dry-run preview.",
    )


def handle_registry_lifecycle_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
) -> int | None:
    if args.command not in REGISTRY_LIFECYCLE_COMMANDS:
        return None

    if args.command == "migrate-local-state":
        try:
            if args.rollback_receipt and any((
                args.source_runtime_root,
                args.target_runtime_root,
                args.backup_dir,
                args.expected_plan_id,
                args.runtime_root,
            )):
                raise ValueError("--rollback-receipt cannot be combined with source, target, backup, or plan overrides")
            if args.source_runtime_root and args.runtime_root:
                raise ValueError("select one source root using --source-runtime-root or --runtime-root")
            if args.rollback_receipt:
                payload = rollback_local_state_migration(
                    args.rollback_receipt, execute=bool(args.execute)
                )
            else:
                payload = migrate_local_state(
                    source_runtime_root=args.source_runtime_root or (
                        Path(args.runtime_root) if args.runtime_root else LEGACY_LOCAL_RUNTIME_ROOT
                    ),
                    target_runtime_root=args.target_runtime_root or DEFAULT_RUNTIME_ROOT,
                    backup_dir=args.backup_dir,
                    expected_plan_id=args.expected_plan_id,
                    execute=bool(args.execute),
                )
        except Exception as exc:
            payload = {"ok": False, "schema_version": "loopx_local_state_migration_v1", "dry_run": not bool(args.execute), "error": str(exc)}
        print_payload(payload, args.format, render_local_state_migration_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "archive-runtime":
        try:
            payload = archive_runtime_goal(
                registry_path=registry_path,
                runtime_root_override=args.runtime_root,
                goal_id=args.goal_id,
                archive_root=Path(args.archive_root).expanduser() if args.archive_root else None,
                allow_registered=bool(args.allow_registered),
                execute=bool(args.execute),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "registry": str(registry_path),
                "runtime_root": args.runtime_root,
                "goal_id": args.goal_id,
                "dry_run": not bool(args.execute),
                "archived": False,
                "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            }
        print_payload(payload, args.format, render_archive_runtime_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "retire-global-goal":
        try:
            payload = retire_global_registry_goals(
                runtime_root_override=args.runtime_root,
                goal_ids=args.goal_id,
                execute=bool(args.execute),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "schema_version": "loopx_global_goal_retirement_v0",
                "dry_run": not bool(args.execute),
                "execute": bool(args.execute),
                "requested_goal_ids": args.goal_id or [],
                "retired_goal_ids": [],
                "wrote": False,
                "backup_written": False,
                "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            }
        print_payload(payload, args.format, render_global_goal_retirement_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "uninstall-project":
        try:
            payload = uninstall_project(
                registry_path=registry_path,
                runtime_root_override=args.runtime_root,
                goal_ids=args.goal_id,
                archive_state=bool(args.archive_state),
                remove_empty_registry=bool(args.remove_empty_registry),
                execute=bool(args.execute),
            )
        except Exception as exc:
            payload = {
                "ok": False,
                "schema_version": "loopx_project_uninstall_v0",
                "dry_run": not bool(args.execute),
                "execute": bool(args.execute),
                "registry": str(registry_path),
                "goal_ids": args.goal_id or [],
                "wrote_local_registry": False,
                "wrote_global_registry": False,
                "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            }
        print_payload(payload, args.format, render_project_uninstall_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "sync-global":
        try:
            payload = sync_project_registry_to_global(
                registry_path=registry_path,
                runtime_root_override=args.runtime_root,
                goal_id=args.goal_id,
                dry_run=bool(args.dry_run),
                allow_route_replacement=bool(args.replace_state),
            )
        except Exception as exc:
            registry = load_registry(registry_path)
            runtime_root = resolve_runtime_root(registry, args.runtime_root)
            payload = {
                "ok": False,
                "registry": str(registry_path),
                "runtime_root": str(runtime_root),
                "global_registry": str(global_registry_path(runtime_root)),
                "dry_run": bool(args.dry_run),
                "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            }
        print_payload(payload, args.format, render_global_sync_markdown)
        return 0 if payload.get("ok") else 1

    if args.command == "migrate-state":
        try:
            target_runtime_root = (
                Path(args.target_runtime_root).expanduser()
                if args.target_runtime_root
                else (Path(args.runtime_root).expanduser() if args.runtime_root else select_default_runtime_root())
            )
            selected_goal_ids = (
                legacy_registry_goal_ids(Path(args.legacy_registry))
                if args.all_goals
                else (args.goal_id or [])
            )
            payload = migrate_legacy_state(
                legacy_registry_path=Path(args.legacy_registry),
                target_registry_path=registry_path,
                legacy_runtime_root=Path(args.legacy_runtime_root),
                target_runtime_root=target_runtime_root,
                goal_ids=selected_goal_ids,
                goal_id_map=parse_key_value_map(args.goal_id_map, flag_name="--goal-id-map"),
                path_map=parse_key_value_map(args.path_map, flag_name="--path-map"),
                copy_active_state=bool(args.copy_active_state),
                copy_runtime=bool(args.copy_runtime),
                execute=bool(args.execute),
            )
            if payload.get("ok") and args.execute and not args.no_global_sync:
                sync_results = []
                for migrated_goal_id in payload.get("migrated_goal_ids") or []:
                    sync_results.append(
                        sync_project_registry_to_global(
                            registry_path=registry_path,
                            runtime_root_override=str(target_runtime_root),
                            goal_id=str(migrated_goal_id),
                            dry_run=False,
                        )
                    )
                payload["global_sync"] = {
                    "ok": all(result.get("ok") for result in sync_results),
                    "dry_run": False,
                    "wrote": bool(sync_results),
                    "results": sync_results,
                    "synced_goal_ids": [
                        goal_id
                        for result in sync_results
                        for goal_id in (result.get("synced_goal_ids") or [])
                    ],
                }
        except Exception as exc:
            payload = {
                "ok": False,
                "schema_version": "loopx_state_migration_v0",
                "dry_run": not bool(args.execute),
                "execute": bool(args.execute),
                "legacy_registry": args.legacy_registry,
                "target_registry": str(registry_path),
                "legacy_runtime_root": args.legacy_runtime_root,
                "target_runtime_root": args.target_runtime_root or args.runtime_root or str(select_default_runtime_root()),
                "selected_goal_ids": args.goal_id or ([] if not getattr(args, "all_goals", False) else ["<all-goals>"]),
                "error": str(exc),
                **({"error_code": exc.code, **getattr(exc, "payload", {})} if isinstance(getattr(exc, "code", None), str) else {}),
            }
        print_payload(payload, args.format, render_state_migration_markdown)
        return 0 if payload.get("ok") else 1

    return None
