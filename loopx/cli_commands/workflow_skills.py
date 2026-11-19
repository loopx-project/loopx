from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..workflow_skill_install import (
    render_workflow_skill_install_markdown,
    workflow_skill_install,
)


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]


def register_workflow_skills_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "workflow-skills",
        help="Inspect, install, or uninstall the packaged LoopX workflow skills.",
    )
    add_subcommand_format(parser)
    mutation = parser.add_mutually_exclusive_group()
    mutation.add_argument(
        "--install",
        action="store_true",
        help="Install workflow skills and refresh continuation guidance for an already installed Ego skill.",
    )
    mutation.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove unchanged LoopX-managed skills and restore any managed Ego skill to its upstream link.",
    )
    parser.add_argument(
        "--skills-dir",
        help="Target host skill root. Defaults to CODEX_HOME/skills or ~/.codex/skills.",
    )
    parser.add_argument(
        "--cli-bin",
        default="loopx",
        help="LoopX executable name embedded in the generated $loopx entry skill.",
    )
    parser.add_argument(
        "--host-surface",
        choices=[
            "ark-managed-agent",
            "deepseek-harness-native",
            "agy",
            "antigravity-app",
        ],
        help="Generate the managed $loopx entry for one exact host surface.",
    )
    parser.add_argument(
        "--agy-home",
        help=(
            "Antigravity config home (defaults to AGY_HOME or ~/.gemini/config); "
            "selects <agy_home>/skills when --skills-dir is omitted."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Inspect the requested operation without writing files.",
    )


def handle_workflow_skills_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "workflow-skills":
        return None
    payload = workflow_skill_install(
        skills_dir=Path(args.skills_dir) if args.skills_dir else None,
        execute=bool((args.install or args.uninstall) and not args.dry_run),
        uninstall=bool(args.uninstall),
        cli_bin=args.cli_bin,
        host_surface=args.host_surface,
        agy_home=getattr(args, "agy_home", None),
    )
    print_payload(
        payload,
        output_format(args),
        render_workflow_skill_install_markdown,
    )
    return 0 if payload.get("ok") is True else 1
