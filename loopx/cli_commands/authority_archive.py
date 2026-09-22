"""Compact CLI transport for the TS-owned retained-authority archive contract."""
from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.effect_runtime import effect_runtime_result
from ..paths import resolve_runtime_root
from ..history import load_registry


def register_authority_archive_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "authority-archive", help="Export, verify or restore an isolated canonical authority copy."
    )
    add_subcommand_format(parser)
    actions = parser.add_subparsers(dest="authority_archive_action", required=True)
    for name in ("export", "verify", "restore"):
        action = actions.add_parser(name)
        action.add_argument("--archive", type=Path, required=True)
        if name != "verify":
            action.add_argument("--goal-id", required=True)
        if name == "restore":
            action.add_argument("--destination", type=Path, required=True)
            action.add_argument("--provider", choices=("file", "sqlite"), required=True)
            action.add_argument("--archive-sha256", required=True)
            action.add_argument("--execute", action="store_true",
                                help="Restore into a new isolated directory; otherwise preview.")


def handle_authority_archive_command(
    args: argparse.Namespace, *, registry_path: Path, runtime_root_arg: str | None,
    print_payload: Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None],
    output_format: Callable[..., str],
) -> int | None:
    if args.command != "authority-archive":
        return None
    request: dict[str, object] = {
        "schema_version": "loopx_authority_archive_admin_request_v0",
        "action": args.authority_archive_action,
        "archive": str(args.archive.expanduser().resolve()),
    }
    if args.authority_archive_action == "export":
        request.update(goal_id=args.goal_id, runtime_root=str(resolve_runtime_root(
            load_registry(registry_path), runtime_root_arg, registry_path=registry_path)))
    elif args.authority_archive_action == "restore":
        request.update(goal_id=args.goal_id, destination=str(args.destination.expanduser().resolve()),
                       provider=args.provider, archive_sha256=args.archive_sha256, execute=args.execute)
    try:
        result = effect_runtime_result(
            "coordination.authority_archive.manage", request, timeout=300.0, retry_safe=False
        )
    except (RuntimeError, ValueError) as error:
        result = {"status": "failed", "reason": str(error), "authority_changed": False}
    print_payload(result, output_format(args), lambda value: (
        f"Authority archive: {value.get('status')}\n"
        f"{value.get('reason', 'Active authority selection is unchanged.')}"
    ))
    return 1 if result.get("status") == "failed" else 0
