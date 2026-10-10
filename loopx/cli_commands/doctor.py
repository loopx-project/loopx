from __future__ import annotations

import argparse
from collections.abc import Callable
from typing import Any
from pathlib import Path

from ..doctor import collect_doctor, render_doctor_markdown
from ..host_loop_activation import SUPPORTED_AGENT_TYPES


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
AddFormat = Callable[[argparse.ArgumentParser], None]


def register_doctor_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(
        "doctor",
        help="Diagnose local CLI installation, PATH, wrapper, and import health.",
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--deep",
        action="store_true",
        help="Run slower representative release-candidate checks.",
    )
    parser.add_argument(
        "--restart-runtime",
        action="store_true",
        help=(
            "Stop the managed Effect runtime serving this revision so the next "
            "request resolves Node from PATH again. Use after installing a "
            "qualified Node when a reused runtime reports SQLite not qualified."
        ),
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--installation-only",
        action="store_true",
        help="Check the installed package and toolchain without inspecting user projects or integrations.",
    )
    scope.add_argument(
        "--agent-type",
        choices=SUPPORTED_AGENT_TYPES,
        help=(
            "Evaluate host-specific integration checks. For other-agent, custom-host "
            "skill delivery replaces the Codex skill-directory check."
        ),
    )
    for option, help_text in (
        ("--zcode-cli", "ZCode CLI executable or existing JS bundle; requires --agent-type zcode."),
        ("--zcode-desktop", "ZCode Desktop executable, installation directory or .app bundle; requires --agent-type zcode."),
        ("--zcode-source", "ZCode source checkout; its package and built CLI versions are reported separately. Requires --agent-type zcode."),
    ):
        parser.add_argument(option, metavar="PATH", help=help_text)
    return parser


def handle_doctor_command(
    args: argparse.Namespace, print_payload: PrintPayload, *, registry_path: Path | None = None,
) -> int:
    host_options = {
        name: getattr(args, name, None)
        for name in ("zcode_cli", "zcode_desktop", "zcode_source")
        if getattr(args, name, None) is not None
    }
    if host_options and args.agent_type != "zcode":
        raise ValueError("ZCode path options require doctor --agent-type zcode.")
    restart: dict[str, Any] | None = None
    if bool(getattr(args, "restart_runtime", False)):
        from ..control_plane.effect_runtime import restart_effect_runtime

        restart = restart_effect_runtime()
    payload = collect_doctor(
        deep=bool(args.deep),
        agent_type=args.agent_type,
        installation_only=bool(getattr(args, "installation_only", False)),
        registry_path=registry_path,
        runtime_root_override=getattr(args, "runtime_root", None),
        **host_options,
    )
    if restart is not None:
        payload["effect_runtime_restart"] = restart
    output_format = getattr(args, "subcommand_format", None) or args.format
    print_payload(payload, output_format, render_doctor_markdown)
    return 0 if payload.get("ok") else 1
