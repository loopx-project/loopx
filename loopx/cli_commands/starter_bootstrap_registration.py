from __future__ import annotations

import argparse

from ..bootstrap_command_pack import (
    START_GOAL_HOST_SURFACES,
)
from ..project_prompt import (
    DEFAULT_HANDOFF_ADAPTER_KIND,
    DEFAULT_HANDOFF_ADAPTER_STATUS,
)
from .start_goal import add_capability_route_argument, register_start_goal_command


def register_starter_bootstrap_commands(subparsers: argparse._SubParsersAction) -> None:
    agent_onboard_parser = subparsers.add_parser(
        "agent-onboard",
        help=(
            "Generate a deterministic LoopX setup and host-loop activation packet "
            "for one agent runtime."
        ),
    )
    agent_onboard_parser.add_argument(
        "--agent-type",
        help="Agent runtime type: codex-app, trae_app, traex-cli, codex-app-ssh, codex-ide-plugin, codex-cli, claude-code, opencode, pi, manual, or other-agent.",
    )
    agent_onboard_parser.add_argument(
        "--list-agent-types",
        action="store_true",
        help="List canonical agent_type values, accepted aliases, and ambiguous inputs.",
    )
    agent_onboard_parser.add_argument("--project", default=".", help="Project directory to inspect.")
    agent_onboard_parser.add_argument("--goal-id", help="Goal id. Defaults to <project-name>-goal.")
    agent_onboard_parser.add_argument(
        "--agent-id",
        help="Registered LoopX agent id to include in quota/heartbeat commands.",
    )
    agent_onboard_parser.add_argument(
        "--cli-bin",
        default="loopx",
        help="LoopX CLI binary name embedded in generated commands.",
    )
    agent_onboard_parser.add_argument(
        "--task-text",
        help="Optional first task text to include in the bootstrap command pack.",
    )
    agent_onboard_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help="Capability available in this host loop. Repeat for multiple capabilities.",
    )

    bootstrap_command_pack_parser = subparsers.add_parser(
        "bootstrap-command-pack",
        help="Preview the /loopx project bootstrap command pack without mutating state.",
    )
    bootstrap_command_pack_parser.add_argument("--project", default=".", help="Project directory to inspect.")
    bootstrap_command_pack_parser.add_argument("--goal-id", help="Goal id. Defaults to <project-name>-goal.")
    bootstrap_command_pack_parser.add_argument(
        "--agent-id",
        help="Registered LoopX agent id to include in quota/heartbeat commands.",
    )
    bootstrap_command_pack_parser.add_argument(
        "--thread-id",
        help=(
            "Stable opaque host thread id used to reuse the bound agent lane. "
            "Codex App defaults to the ambient CODEX_THREAD_ID when available."
        ),
    )
    bootstrap_command_pack_parser.add_argument(
        "--new-peer",
        action="store_true",
        help="Explicitly request a fresh agent identity for this host thread.",
    )
    bootstrap_command_pack_parser.add_argument(
        "--cli-bin",
        default="loopx",
        help="LoopX CLI binary name embedded in generated commands.",
    )
    bootstrap_command_pack_parser.add_argument(
        "--host-surface",
        default="codex-app",
        choices=START_GOAL_HOST_SURFACES,
        help="Host surface where the slash command pack will be exposed.",
    )
    bootstrap_command_pack_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help="Capability available in this host loop. Repeat for multiple capabilities.",
    )
    add_capability_route_argument(bootstrap_command_pack_parser)
    bootstrap_command_pack_parser.add_argument(
        "--fine-grained",
        action="store_true",
        help="Preview sticky one-checkpoint-per-turn goal execution.",
    )
    bootstrap_command_pack_parser.add_argument(
        "--goal-text",
        help=(
            "Optional text after /loopx. When present, preview the explicit goal-start flow: "
            "connect if needed, plan ranked todos, write them in order, then enter quota-gated automation."
        ),
    )
    bootstrap_command_pack_parser.add_argument(
        "--message-only",
        "--copy-only",
        dest="message_only",
        action="store_true",
        help="Print only the pasteable /loopx handling message.",
    )

    register_start_goal_command(subparsers)

    prompt_parser = subparsers.add_parser(
        "new-project-prompt",
        help="Generate a copy-paste Codex prompt for connecting a project from a goal document.",
    )
    prompt_parser.add_argument("--project", required=True, help="Project directory the target Codex session can access.")
    prompt_parser.add_argument("--goal-doc", required=True, help="Goal document path for the target project.")
    prompt_parser.add_argument("--goal-id", help="Initial stable goal id. Defaults to <project-name>-goal.")
    prompt_parser.add_argument("--objective", help="Initial objective. Defaults to an extraction placeholder.")
    prompt_parser.add_argument("--domain", help="Initial domain label. Defaults to an extraction placeholder.")
    prompt_parser.add_argument("--adapter-kind", default=DEFAULT_HANDOFF_ADAPTER_KIND)
    prompt_parser.add_argument("--adapter-status", default=DEFAULT_HANDOFF_ADAPTER_STATUS)
    prompt_parser.add_argument("--next-probe", help="Optional read-only pre-tick command for the target project.")
    prompt_parser.add_argument("--spawn-allowed", action="store_true", help="Include task-scoped worker flags.")
    prompt_parser.add_argument("--allowed-domain", action="append", default=[], help="Allowed child work domain. Repeatable.")
    prompt_parser.add_argument("--write-scope", action="append", default=[], help="Allowed write scope such as docs/**. Repeatable.")

    codex_cli_bootstrap_parser = subparsers.add_parser(
        "codex-cli-bootstrap-message",
        help="Generate a one-message Codex CLI TUI setup prompt that installs/connects LoopX, then sets the thin /goal loop.",
    )
    codex_cli_bootstrap_parser.add_argument("--project", default=".", help="Project directory to start from.")
    codex_cli_bootstrap_parser.add_argument("--goal-id", help="Goal id. Defaults to <project-name>-goal.")
    codex_cli_bootstrap_parser.add_argument(
        "--agent-id",
        help="Registered LoopX agent id to include in quota/claim instructions.",
    )
    codex_cli_bootstrap_parser.add_argument(
        "--cli-bin",
        default="loopx",
        help="LoopX CLI binary name embedded in generated commands.",
    )
    codex_cli_bootstrap_parser.add_argument(
        "--message-only",
        "--copy-only",
        dest="message_only",
        action="store_true",
        help="Print only the Codex CLI TUI paste message, without the Markdown review packet.",
    )
