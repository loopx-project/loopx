from __future__ import annotations

import argparse
from collections.abc import Callable

from ..control_plane.scheduler.execution_context import SchedulerRuntimeProfile

AddFormat = Callable[[argparse.ArgumentParser], None]


def register_heartbeat_control_commands(
    subparsers: argparse._SubParsersAction,
    add_subcommand_format: AddFormat,
) -> None:
    heartbeat_prompt_parser = subparsers.add_parser(
        "heartbeat-prompt",
        help="Generate a guarded heartbeat or visible-goal host-loop task body.",
    )
    add_subcommand_format(heartbeat_prompt_parser)
    heartbeat_prompt_parser.add_argument(
        "--bootstrap", action="store_true",
        help="Generate a stable host entrypoint that reloads installed rules; no persisted Turn identity.",
    )
    heartbeat_prompt_parser.add_argument(
        "--goal-id", required=True, help="Stable LoopX goal id."
    )
    heartbeat_prompt_parser.add_argument(
        "--active-state",
        help="Active goal state file the heartbeat should read and write back. Defaults to the registry goal state_file.",
    )
    heartbeat_prompt_parser.add_argument(
        "--material-rule",
        help="Optional project-specific material queue rule appended to the task body.",
    )
    heartbeat_prompt_parser.add_argument(
        "--permission-rule",
        help="Optional trusted-session permission rule appended to the task body.",
    )
    heartbeat_prompt_parser.add_argument(
        "--cli-bin",
        default="loopx",
        help="Command name embedded in generated preflight/guard/spend commands. Use loopx-canary for gray rollout targets.",
    )
    heartbeat_prompt_parser.add_argument(
        "--agent-id",
        help="Optional public-safe automation agent id, such as codex-main-control or codex-side-bypass.",
    )
    heartbeat_prompt_parser.add_argument(
        "--turn-instance-id",
        help=(
            "Optional exact public-safe heartbeat receipt id. Requires an exact "
            "agent id and a receipt-producing runtime profile."
        ),
    )
    heartbeat_prompt_parser.add_argument(
        "--agent-scope",
        dest="agent_scopes",
        action="append",
        help="Optional natural-language scope for this automation agent. Repeat for multiple scope lines.",
    )
    heartbeat_prompt_parser.add_argument(
        "--available-capability",
        dest="available_capabilities",
        action="append",
        help=(
            "Declare a capability available in this host loop, such as network or "
            "external_evidence_poll. Repeat for multiple capabilities; generated "
            "quota guard and spend commands preserve the declaration."
        ),
    )
    heartbeat_prompt_parser.add_argument(
        "--runtime-profile",
        choices=[profile.value for profile in SchedulerRuntimeProfile],
        help=(
            "Embed one explicit scheduler runtime profile in the generated quota "
            "guard. Cannot be combined with the explicit scheduler context fields."
        ),
    )
    heartbeat_prompt_parser.add_argument(
        "--codex-app",
        action="store_true",
        help=(
            "Compact explicit alias for --runtime-profile "
            "codex_app_heartbeat in generated heartbeat commands."
        ),
    )
    heartbeat_prompt_parser.add_argument(
        "--trae_app",
        action="store_true",
        help=(
            "Compact explicit alias for --runtime-profile "
            "trae_app in generated heartbeat commands."
        ),
    )
    heartbeat_prompt_parser.add_argument(
        "--visible-goal-host",
        choices=["traex-cli"],
        help=(
            "Render the visible Goal task contract for an explicitly verified "
            "host while preserving its scheduler runtime profile."
        ),
    )
    heartbeat_prompt_parser.add_argument(
        "-H",
        "--host-surface",
        choices=[
            "ark_managed_agent",
            "codex_app",
            "codex_app_ssh",
            "codex_cli",
            "trae_app",
            "generic_cli",
            "claude_code",
            "kunluncode",
            "kiro_cli",
            "local_scheduler",
        ],
        help="Host surface embedded in the generated quota guard.",
    )
    heartbeat_prompt_parser.add_argument(
        "-O",
        "--scheduler-owner",
        choices=[
            "host_automation",
            "agent_cli_loop",
            "goal_runtime",
            "outer_controller",
            "none",
        ],
        help="Cadence owner embedded in the generated quota guard.",
    )
    heartbeat_prompt_parser.add_argument(
        "-M",
        "--execution-mode",
        choices=["interactive", "isolated_headless", "hosted_automation"],
        help="Execution mode embedded in the generated quota guard.",
    )
    heartbeat_style_group = heartbeat_prompt_parser.add_mutually_exclusive_group()
    heartbeat_style_group.add_argument(
        "--full",
        action="store_true",
        help="Generate the expanded audit body. The default installed heartbeat body is thin.",
    )
    heartbeat_style_group.add_argument(
        "--compact",
        action="store_true",
        help="Generate a shorter automation body that points edge cases back to the expanded lifecycle contract.",
    )
    heartbeat_style_group.add_argument(
        "--brief",
        action="store_true",
        help="Generate a minimal installed automation body that delegates details to the compact lifecycle contract.",
    )
    heartbeat_style_group.add_argument(
        "--thin",
        action="store_true",
        help="Generate the thinnest generic dispatcher body for trusted agents that inspect LoopX state themselves.",
    )

    heartbeat_prompt_parser.add_argument(
        "--decision-output-root",
        help="Opt into the short TurnEnvelope with full local captures; requires thin mode and an explicit host-owned Turn ID.",
    )

    heartbeat_prequota_parser = subparsers.add_parser(
        "heartbeat-prequota",
        help=(
            "Run best-effort kernel reconciliation hooks before heartbeat quota "
            "selection without spending quota."
        ),
    )
    add_subcommand_format(heartbeat_prequota_parser)
    heartbeat_prequota_parser.add_argument(
        "-g",
        "--goal-id",
        required=True,
        help="Stable LoopX goal id.",
    )
    heartbeat_prequota_parser.add_argument(
        "-a",
        "--agent-id",
        required=True,
        help="Registered heartbeat lifecycle actor.",
    )
    heartbeat_prequota_parser.add_argument(
        "--fetch-timeout-seconds",
        type=int,
        default=10,
        help="Timeout for each bounded compact external metadata fetch.",
    )
