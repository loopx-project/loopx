from __future__ import annotations

import argparse
from collections.abc import Callable

from ..pi_goal_mode.installation import inspect_pi_installations, render_pi_installation_markdown
from ..slash_command_install import install_slash_commands, render_slash_command_install_markdown
from ..slash_commands import build_slash_command_catalog, render_slash_command_catalog_markdown


PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]


def register_slash_commands_command(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    parser = subparsers.add_parser(
        "slash-commands",
        help="List LoopX chat slash commands, onboarding hints, and CLI references.",
    )
    add_subcommand_format(parser)
    parser.add_argument(
        "--cli-bin",
        default="loopx",
        help="LoopX CLI binary name to show in command references.",
    )
    parser.add_argument(
        "--no-legacy-aliases",
        action="store_true",
        help="Hide legacy /loop-global-* aliases from the command catalog.",
    )
    install_group = parser.add_mutually_exclusive_group()
    install_group.add_argument(
        "--install",
        action="store_true",
        help="Install LoopX command skill files for supported hosts.",
    )
    install_group.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove LoopX-managed command skill files for supported hosts while preserving user-owned files.",
    )
    install_group.add_argument(
        "--inspect",
        action="store_true",
        help="Read project and user Pi extension installation state without writing files.",
    )
    parser.add_argument(
        "--surface",
        action="append",
        choices=[
            "all",
            "codex",
            "codex-cli",
            "codex-app",
            "codex-app-ssh",
            "codex-ide-plugin",
            "codex-ide",
            "claude-code",
            "opencode",
            "gemini",
            "gemini-cli",
            "cursor",
            "cursor-agent",
            "zcode",
            "z-code",
            "agy",
            "antigravity",
            "kiro",
            "kiro-cli",
            "pi",
        ],
        help=(
            "Host surface to install. Repeatable. Defaults to static command facades "
            "for Codex, Claude Code, and OpenCode. `gemini`, `cursor`, `zcode`, "
            "`agy`, `kiro-cli`, `pi` are opt-in: they write into those hosts' own "
            "homes only when requested."
        ),
    )
    parser.add_argument(
        "--with-goal-bridge",
        action="store_true",
        help=(
            "Also install or uninstall the executable OpenCode goal bridge. Requires "
            "an effective OpenCode surface and explicit opt-in."
        ),
    )
    parser.add_argument(
        "--with-gated-agent",
        action="store_true",
        help=(
            "Also install the opt-in Kiro CLI `loopx` agent whose preToolUse hook "
            "denies state-changing tool calls unless LoopX quota should-run allows "
            "them. Requires the kiro-cli surface; run it with "
            "`kiro-cli chat --agent loopx`."
        ),
    )
    parser.add_argument(
        "--codex-home",
        help="Codex home for skill installation. Defaults to CODEX_HOME or ~/.codex.",
    )
    parser.add_argument(
        "--claude-home",
        help="Claude Code home for skill installation. Defaults to CLAUDE_HOME or ~/.claude.",
    )
    parser.add_argument(
        "--gemini-home",
        help="Gemini CLI home for skill installation. Defaults to GEMINI_HOME or ~/.gemini.",
    )
    parser.add_argument(
        "--cursor-home",
        help="Cursor CLI home for MCP registration. Defaults to CURSOR_HOME or ~/.cursor.",
    )
    parser.add_argument(
        "--zcode-home",
        dest="zcode_home",
        help="ZCode home for skill installation. Defaults to ZCODE_HOME or ~/.zcode.",
    )
    parser.add_argument(
        "--zcode-agents-home",
        dest="zcode_home",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--opencode-home",
        help="OpenCode config directory. Defaults to OPENCODE_CONFIG_DIR or ~/.config/opencode.",
    )
    parser.add_argument(
        "--pi-project",
        default=".",
        help="Project directory for the Pi goal extension install. Defaults to the current directory.",
    )
    parser.add_argument(
        "--pi-scope",
        choices=("project", "user"),
        default="project",
        help="Install Pi extension code for one project or the current user. Defaults to project.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what slash-command files would be installed without writing them.",
    )


def handle_slash_commands_command(
    args: argparse.Namespace,
    *,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.command != "slash-commands":
        return None
    if args.inspect:
        if args.surface != ["pi"]:
            raise ValueError("--inspect requires exactly --surface pi")
        if args.dry_run or args.with_goal_bridge:
            raise ValueError("--inspect cannot be combined with --dry-run or --with-goal-bridge")
        payload = inspect_pi_installations(pi_project=args.pi_project)
        print_payload(payload, output_format(args), render_pi_installation_markdown)
        return 0 if payload["ok"] else 1
    if args.install or args.uninstall or args.dry_run:
        payload = install_slash_commands(
            execute=bool((args.install or args.uninstall) and not args.dry_run),
            uninstall=bool(args.uninstall),
            with_goal_bridge=bool(args.with_goal_bridge),
            with_gated_agent=bool(getattr(args, "with_gated_agent", False)),
            surfaces=args.surface,
            cli_bin=args.cli_bin,
            include_legacy_aliases=not bool(args.no_legacy_aliases),
            codex_home=args.codex_home,
            claude_home=args.claude_home,
            opencode_home=args.opencode_home,
            gemini_home=args.gemini_home,
            cursor_home=args.cursor_home,
            zcode_home=getattr(args, "zcode_home", None),
            pi_project=args.pi_project,
            pi_scope=args.pi_scope,
        )
        print_payload(payload, output_format(args), render_slash_command_install_markdown)
        return 0 if payload.get("ok") is True else 1
    payload = build_slash_command_catalog(
        cli_bin=args.cli_bin,
        include_legacy_aliases=not bool(args.no_legacy_aliases),
    )
    print_payload(payload, output_format(args), render_slash_command_catalog_markdown)
    return 0
