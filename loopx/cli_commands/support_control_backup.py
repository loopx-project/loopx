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
    parser.add_argument("backup_action", nargs="?", choices=["verify"],
                        help="Verify a full archive and inspect an inert copy; never reactivate it.")
    parser.add_argument("--archive", help="For verify, the private full-state tar.gz archive.")
    parser.add_argument("--manifest", help="For verify, the matching private external manifest.")
    parser.add_argument("--destination", help="For verify, a new inert directory below a physical parent.")
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
        help="Id for a new backup. Defaults to a UTC timestamp; existing output names are never replaced.",
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
        "--configuration-goal-id",
        action="append",
        help=("Select source-owned Goal configurations for the embedded checkpoint; "
              "repeat for a reviewed cohort. Raw state discovery is unchanged."),
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
    if getattr(args, "backup_action", None) == "verify":
        from ..control_plane.state_recovery_io import StateRecoveryError, render_state_recovery_markdown, verify_state_backup

        try:
            if not all(getattr(args, key, None) for key in ("archive", "manifest", "destination")):
                raise StateRecoveryError("recovery_inputs_required")
            if args.output_dir or args.backup_id or args.no_automations or args.no_skills or args.current_project_only or args.project != ".":
                raise StateRecoveryError("recovery_creation_options_invalid")
            payload = verify_state_backup(archive=Path(args.archive).expanduser(),
                manifest=Path(args.manifest).expanduser(), destination=Path(args.destination).expanduser().absolute(),
                execute=bool(args.execute))
        except StateRecoveryError as exc:
            payload = {"ok": False, "error_code": exc.code, "execution_authority_granted": False}
        except OSError:
            payload = {"ok": False, "error_code": "recovery_io_unavailable", "execution_authority_granted": False}
        print_payload(payload, output_format(args), render_state_recovery_markdown)
        return 0 if payload.get("ok") else 1
    if any(getattr(args, key, None) for key in ("archive", "manifest", "destination")):
        payload = {"ok": False, "error": "archive, manifest and destination require backup-state verify"}
        print_payload(payload, output_format(args), render_state_backup_markdown)
        return 1
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
            configuration_goal_ids=getattr(args, "configuration_goal_id", None),
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
            "recommended_action": "fix the reported backup error before retrying; preserve existing archive and manifest files",
        }
    print_payload(payload, output_format(args), render_state_backup_markdown)
    return 0 if payload.get("ok") else 1
