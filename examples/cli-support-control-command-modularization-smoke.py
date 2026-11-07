#!/usr/bin/env python3
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "loopx" / "cli.py"
MODULE = ROOT / "loopx" / "cli_commands" / "support_control.py"
HEARTBEAT = MODULE.with_name("support_control_heartbeat.py")
INIT = ROOT / "loopx" / "cli_commands" / "__init__.py"


def run_cli(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def require_success(result: subprocess.CompletedProcess[str]) -> str:
    if result.returncode != 0:
        raise AssertionError(
            f"expected success, got {result.returncode}\n"
            f"stdout:\n{result.stdout}\n\nstderr:\n{result.stderr}"
        )
    return result.stdout


def main() -> None:
    cli_source = CLI.read_text(encoding="utf-8")
    module_source = MODULE.read_text(encoding="utf-8")
    init_source = INIT.read_text(encoding="utf-8")

    forbidden_cli_markers = [
        "backup_state_parser = sub.add_parser",
        "heartbeat_prompt_parser = sub.add_parser",
        "promotion_gate_parser = sub.add_parser",
        "upgrade_plan_parser = sub.add_parser",
        "update_parser = sub.add_parser",
        "registry_boundary_parser = sub.add_parser",
        "serve_status_parser = sub.add_parser",
        "chat_parser = sub.add_parser",
        "resolve_heartbeat_active_state(",
        "build_heartbeat_prompt(",
        "build_promotion_gate(",
        "build_upgrade_plan(",
        "build_update_plan(",
        "build_state_backup_plan(",
        "inspect_registry(",
        "inspect_registry_boundary(",
        "serve_status(",
        "serve_chat(",
        'if args.command == "backup-state":',
        'if args.command == "heartbeat-prompt":',
        'if args.command == "promotion-gate":',
        'if args.command == "upgrade-plan":',
        'if args.command == "update":',
        'if args.command == "registry":',
        'if args.command == "registry-boundary":',
        'if args.command == "serve-status":',
        'if args.command == "chat":',
    ]
    for marker in forbidden_cli_markers:
        require(marker not in cli_source, f"support/control marker leaked into cli.py: {marker}")

    for marker in (
        "SUPPORT_CONTROL_COMMANDS",
        "register_support_control_commands",
        "handle_support_control_command",
        "backup-state",
        "chat",
        "promotion-gate",
        "upgrade-plan",
        "update",
        "registry-boundary",
        "serve-status",
    ):
        require(marker in module_source, f"support/control module missing {marker}")
    require("register_support_control_commands" in cli_source, "cli.py did not register support/control commands")
    require("handle_support_control_command" in cli_source, "cli.py did not dispatch support/control commands")
    require("register_support_control_commands" in init_source, "__init__ omitted support/control registration")
    require("handle_support_control_command" in init_source, "__init__ omitted support/control handler")
    heartbeat_source = HEARTBEAT.read_text(encoding="utf-8")
    require("build_heartbeat_prompt(" not in module_source, "support/control retained duplicate heartbeat dispatch")
    for marker in ("handle_heartbeat_prompt_command", "resolve_heartbeat_active_state", "build_heartbeat_prompt("):
        require(marker in heartbeat_source, f"heartbeat handler missing {marker}")

    for command, options in {
        "backup-state": (
            "--project",
            "--output-dir",
            "--backup-id",
            "--current-project-only",
            "--execute",
        ),
        "heartbeat-prompt": ("--goal-id", "--agent-id", "--thin"),
        "promotion-gate": ("--format",),
        "upgrade-plan": ("--installed-manifest", "--cli-bin", "--mode"),
        "update": ("--check", "--dry-run", "--execute"),
        "registry": (),
        "registry-boundary": ("--path", "--require-not-tracked", "--require-gitignored"),
        "serve-status": ("--global-registry", "--enable-reward-write-api", "--enable-control-plane-write-api"),
        "chat": ("--goal-id", "--codex-bin", "--no-open", "--global-registry"),
    }.items():
        help_text = require_success(run_cli(command, "--help"))
        for option in options:
            require(option in help_text, f"{command} help omitted {option}")

    print("cli-support-control-command-modularization-smoke: ok")


if __name__ == "__main__":
    main()
