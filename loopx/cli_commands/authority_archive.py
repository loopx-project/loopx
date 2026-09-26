"""Compact CLI transport for the TS-owned retained-authority archive contract."""
from __future__ import annotations

import argparse
import os
from collections.abc import Callable
from pathlib import Path

from ..control_plane.effect_runtime import effect_runtime_result
from ..paths import DEFAULT_RUNTIME_ROOT, global_registry_path, resolve_runtime_root
from ..history import load_registry


def register_authority_archive_command(
    subparsers: argparse._SubParsersAction[argparse.ArgumentParser],
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "authority-archive", help="Export, verify, audit or restore a canonical authority copy."
    )
    add_subcommand_format(parser)
    actions = parser.add_subparsers(dest="authority_archive_action", required=True)
    inspect = actions.add_parser("inspect", help="Identify store, archive or backup format from its content.")
    inspect.add_argument("--source", type=Path, required=True)
    upgrade = actions.add_parser("upgrade", help="Back up, verify and migrate local authority formats.")
    mode = upgrade.add_mutually_exclusive_group()
    mode.add_argument("--execute", action="store_true")
    upgrade.add_argument("--all-known", action="store_true", help="Include runtime roots of registered projects.")
    mode.add_argument("--require-current", action="store_true", help="Fail if a format upgrade is needed; never write.")
    for name in ("export", "verify", "restore", "audit"):
        action = actions.add_parser(name)
        action.add_argument("--archive", type=Path, required=True)
        if name != "verify":
            action.add_argument("--goal-id", required=True)
        if name == "audit":
            action.add_argument("--archive-sha256", required=True)
            action.add_argument("--destination", type=Path,
                                help="Audit an isolated restore directory; otherwise audit the selected runtime provider.")
            action.add_argument("--allow-newer-head", action="store_true",
                                help="Verify only the retained archive prefix, permitting later target commits.")
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
    try:
        request: dict[str, object] = {
            "schema_version": "loopx_authority_archive_admin_request_v0",
            "action": args.authority_archive_action,
        }
        if args.authority_archive_action == "inspect":
            request["source"] = str(args.source.expanduser().resolve())
        elif args.authority_archive_action == "upgrade":
            request.update(runtime_roots=authority_upgrade_roots(
                registry_path, runtime_root_arg, all_known=args.all_known), execute=args.execute)
        else:
            request["archive"] = str(args.archive.expanduser().resolve())
        if args.authority_archive_action == "export":
            request.update(goal_id=args.goal_id, runtime_root=str(resolve_runtime_root(
                load_registry(registry_path), runtime_root_arg, registry_path=registry_path)))
        elif args.authority_archive_action == "restore":
            request.update(goal_id=args.goal_id, destination=str(args.destination.expanduser().resolve()),
                           provider=args.provider, archive_sha256=args.archive_sha256, execute=args.execute)
        elif args.authority_archive_action == "audit":
            request.update(goal_id=args.goal_id, archive_sha256=args.archive_sha256,
                           allow_newer_head=args.allow_newer_head)
            if args.destination is not None:
                request["destination"] = str(args.destination.expanduser().resolve())
            else:
                request["runtime_root"] = str(resolve_runtime_root(
                    load_registry(registry_path), runtime_root_arg, registry_path=registry_path))
        result = effect_runtime_result(
            "coordination.authority_archive.manage", request, timeout=300.0, retry_safe=False
        )
    except (OSError, RuntimeError, ValueError) as error:
        result = {"status": "failed", "reason": str(error), "authority_changed": False}
    if (args.authority_archive_action == "upgrade" and args.require_current
            and any(row.get("status") == "planned" for row in result.get("results", []))):
        result.update(status="failed", reason="Authority format upgrade required before activating this runtime.")
    print_payload(result, output_format(args), lambda value: (
        f"Authority archive: {value.get('status')}\n"
        f"{value.get('reason', 'Active authority selection is unchanged.')}\n"
        f"{value.get('audit', '')}"
    ))
    return 1 if result.get("status") == "failed" else 0


def authority_upgrade_roots(registry_path: Path, runtime_root_arg: str | None,
                            *, all_known: bool) -> list[str]:
    """Bounded discovery from existing registries; never scan arbitrary home paths."""
    registry = load_registry(registry_path) if registry_path.exists() else {}
    override = runtime_root_arg or os.environ.get("LOOPX_RUNTIME_ROOT")
    selected = resolve_runtime_root(registry, override, registry_path=registry_path).resolve()
    roots = {selected}
    if all_known:
        common = Path(override).expanduser().resolve() if override else DEFAULT_RUNTIME_ROOT.resolve()
        roots.add(common)
        global_path = global_registry_path(common)
        global_registry = load_registry(global_path) if global_path.exists() else {}
        for goal in global_registry.get("goals", []):
            source = goal.get("source_registry") if isinstance(goal, dict) else None
            if not source:
                continue
            path = Path(str(source)).expanduser()
            if not path.is_absolute():
                raise ValueError("Registered source_registry must be absolute for automatic upgrade")
            # A disconnected checkout does not hide the common runtime store.
            if path.exists():
                roots.add(resolve_runtime_root(load_registry(path), registry_path=path).resolve())
    return sorted(str(root) for root in roots)
