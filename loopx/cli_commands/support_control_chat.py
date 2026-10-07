"""Owner-local parser registration for the chat and dashboard commands.

Both commands launch the same local presentation surface with different
defaults, so they share one cohesive registration unit. Keeping them beside
the other support commands pushed that module over its size budget.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from ..chat_server import DEFAULT_CHAT_HOST, DEFAULT_CHAT_PORT
from ..kiro_cli_goal_mode import KIRO_CLI_BIN
from ..paths import default_public_scan_root
from .support_control_agent_runtime import register_agent_runtime_arguments
from .support_control_chat_endpoint import register_chat_endpoint_command


def register_chat_and_dashboard_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: Callable[[argparse.ArgumentParser], None],
) -> None:
    chat_parser = subparsers.add_parser(
        "chat",
        help="Open the local Goal Studio and review Agent-proposed LoopX Todos.",
    )
    chat_parser.add_argument(
        "--goal-id", help="Goal to select when the local workspace opens."
    )
    chat_parser.add_argument(
        "--host", default=DEFAULT_CHAT_HOST, help="Loopback bind host."
    )
    chat_parser.add_argument("--port", type=int, default=DEFAULT_CHAT_PORT)
    register_agent_runtime_arguments(chat_parser, kiro_cli_bin=KIRO_CLI_BIN)
    chat_parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=30.0,
        help="Maximum seconds allowed for Codex app-server startup and handshake.",
    )
    chat_parser.add_argument(
        "--idle-timeout-seconds",
        type=float,
        default=180.0,
        help="Maximum seconds without an upstream event before interrupting the active turn.",
    )
    chat_parser.add_argument(
        "--hard-timeout-seconds",
        type=float,
        default=900.0,
        help="Absolute maximum seconds for one Agent turn.",
    )
    chat_parser.add_argument(
        "--assets-dir",
        help="Optional LoopX Chat web bundle directory. Defaults to packaged assets.",
    )
    chat_parser.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help="Public files used by the underlying status projection.",
    )
    chat_parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help="Specific public file or directory to scan. Repeatable.",
    )
    chat_parser.add_argument("--limit", type=int, default=20)
    chat_parser.add_argument(
        "--project-workspace-grant",
        choices=("workspace_read", "workspace_write"),
        default="workspace_write",
        help="Ordinary project Chat workspace access. Defaults to workspace_write; workspace_read prevents App write grants.",
    )
    chat_parser.add_argument(
        "--project-filesystem-scope",
        choices=("host_default", "workspace_only"),
        default="host_default",
        help="Restrict project Codex filesystem tools to the workspace with minimal system reads; workspace_only also disables tool network access. Does not isolate host-loaded skills or context.",
    )
    chat_parser.add_argument(
        "--global-registry",
        action="store_true",
        help="Use the shared global registry even when the command runs in a project directory.",
    )
    chat_parser.add_argument(
        "--enable-goal-subagent-configuration",
        action="store_true",
        help=(
            "Enable the preview-locked Goal sub-agent configuration API, "
            "status projection, and dashboard controls."
        ),
    )
    chat_parser.add_argument(
        "--no-open",
        action="store_true",
        help="Start the local server without opening a browser.",
    )
    chat_parser.add_argument(
        "--replace-existing-loopx-chat",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    chat_parser.add_argument(
        "--verbose", action="store_true", help="Print HTTP request logs."
    )

    register_chat_endpoint_command(subparsers, add_subcommand_format)

    dashboard_parser = subparsers.add_parser(
        "dashboard",
        help="Start the local LoopX dashboard, status service, and Chat service.",
    )
    dashboard_parser.add_argument(
        "--goal-id", help="Goal to select when the local workspace opens."
    )
    dashboard_parser.add_argument(
        "--host", default=DEFAULT_CHAT_HOST, help="Loopback bind host."
    )
    dashboard_parser.add_argument("--port", type=int, default=DEFAULT_CHAT_PORT)
    register_agent_runtime_arguments(dashboard_parser, kiro_cli_bin=KIRO_CLI_BIN)
    dashboard_parser.add_argument(
        "--assets-dir",
        help="Optional LoopX Chat web bundle directory. Defaults to packaged assets.",
    )
    dashboard_parser.add_argument(
        "--scan-root",
        default=default_public_scan_root(),
        help="Public files used by the underlying status projection.",
    )
    dashboard_parser.add_argument(
        "--scan-path",
        action="append",
        default=[],
        help="Specific public file or directory to scan. Repeatable.",
    )
    dashboard_parser.add_argument("--limit", type=int, default=20)
    dashboard_parser.add_argument(
        "--global-registry",
        action="store_true",
        help="Use the shared global registry even when the command runs in a project directory.",
    )
    dashboard_parser.add_argument(
        "--enable-goal-subagent-configuration",
        action="store_true",
        help=(
            "Enable the preview-locked Goal sub-agent configuration API, "
            "status projection, and dashboard controls."
        ),
    )
    dashboard_parser.add_argument(
        "--no-open",
        action="store_true",
        help="Start the local server without opening a browser.",
    )
    dashboard_parser.add_argument(
        "--dev",
        action="store_true",
        help="Prefer the Vite HMR dev launcher if running from a local repository checkout.",
    )
    dashboard_parser.add_argument(
        "--verbose", action="store_true", help="Print HTTP request logs."
    )
    chat_parser.add_argument(
        "--no-private-reactions", action="store_true",
        help="Disable received/processing reactions for native Lark private conversations. Enabled by default.",
    )


__all__ = ["register_chat_and_dashboard_commands"]
