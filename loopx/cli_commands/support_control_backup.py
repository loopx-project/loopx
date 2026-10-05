from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..history import load_registry
from ..paths import resolve_runtime_root
from ..state_backup import (
    build_state_backup_plan,
    execute_state_backup_plan,
    render_state_backup_markdown,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
AddFormat = Callable[[argparse.ArgumentParser], None]


def register_backup_state_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: AddFormat,
) -> None:
    parser = subparsers.add_parser(
        "backup-state",
        help="Preview or create a private local archive of LoopX state.",
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--project",
        default=".",
        help=(
            "Current project root whose .loopx, .codex/goals, .claude/goals, and "
            ".local/goals state is included alongside every project discovered from "
            "the global registry."
        ),
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for the backup archive and manifest. Defaults to <runtime-root>/backups.",
    )
    parser.add_argument(
        "--backup-id",
        help="Stable id for the archive name. Defaults to a UTC timestamp.",
    )
    parser.add_argument(
        "--no-automations",
        action="store_true",
        help="Exclude $CODEX_HOME/automations from the backup.",
    )
    parser.add_argument(
        "--no-skills",
        action="store_true",
        help="Exclude $CODEX_HOME/skills/loopx-* skill directories from the backup.",
    )
    parser.add_argument(
        "--current-project-only",
        action="store_true",
        help="Do not discover additional project state from the global registry.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Write the backup archive and manifest. Omit for a dry-run plan.",
    )


def handle_backup_state_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    print_payload: PrintPayload,
    output_format: FormatSelector,
) -> int:
    try:
        runtime_root = (
            Path(args.runtime_root).expanduser() if args.runtime_root else None
        )
        if runtime_root is None and registry_path.exists():
            runtime_root = resolve_runtime_root(load_registry(registry_path))
        payload = build_state_backup_plan(
            project=Path(args.project).expanduser(),
            runtime_root=runtime_root,
            output_dir=Path(args.output_dir).expanduser() if args.output_dir else None,
            backup_id=args.backup_id,
            include_automations=not bool(args.no_automations),
            include_skills=not bool(args.no_skills),
            include_registry_projects=not bool(args.current_project_only),
            registry_path=(Path(args.project).expanduser() / ".loopx/registry.json") if args.current_project_only else None,
        )
        if args.execute:
            payload = execute_state_backup_plan(payload)
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "loopx_state_backup_v0",
            "mode": "state_backup",
            "dry_run": not bool(getattr(args, "execute", False)),
            "execute_requested": bool(getattr(args, "execute", False)),
            "error": str(exc),
            "recommended_action": "fix backup planning before retrying",
        }
    print_payload(payload, output_format(args), render_state_backup_markdown)
    return 0 if payload.get("ok") else 1
