"""CLI transport for the existing content-ops composition owner."""
from __future__ import annotations

import json
from pathlib import Path

from .media import MediaWorkspace
from ...paths import default_registry_path, effective_runtime_root


def register_media_commands(subparsers, add_format) -> None:
    parser = subparsers.add_parser("media", help="Prepare and render a private, source-bound local video.")
    parser.add_argument("operation", choices=("prepare", "render", "inspect", "list", "cancel"))
    parser.add_argument("--goal-id", required=True)
    parser.add_argument("--plan-json", help="Agent-prepared composition plan; at most 256 KiB.")
    parser.add_argument("--source-directory", help="Explicit read scope for digest-bound local assets.")
    parser.add_argument("--composition-id")
    parser.add_argument("--expected-revision", type=int)
    add_format(parser)


def handle_media_command(args) -> dict:
    registry = Path(args.registry) if args.registry else default_registry_path()
    workspace = MediaWorkspace(registry_path=registry, goal_id=args.goal_id,
                               runtime_root=effective_runtime_root(registry, args.runtime_root))
    if args.operation == "list": return workspace.list()
    if args.operation == "prepare":
        if not args.plan_json or not args.source_directory:
            raise ValueError("prepare requires --plan-json and --source-directory")
        path = Path(args.plan_json).expanduser()
        if path.stat().st_size > 256 * 1024: raise ValueError("Composition plan exceeds 256 KiB")
        return workspace.prepare(json.loads(path.read_text(encoding="utf-8")), source_directory=Path(args.source_directory),
                                 composition_id=args.composition_id, expected_revision=args.expected_revision)
    if not args.composition_id: raise ValueError("Select an existing composition")
    if args.operation == "inspect": return workspace.inspect(args.composition_id)
    if args.expected_revision is None: raise ValueError("Read the current --expected-revision first")
    if args.operation == "render": return workspace.render(args.composition_id, expected_revision=args.expected_revision)
    return workspace.cancel(args.composition_id, expected_revision=args.expected_revision)
