from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from . import __version__
from .paths import default_registry_path, global_registry_path, select_default_runtime_root


HOST_GLOBAL_REGISTRY_SELECTOR = "@host-global"


GLOBAL_OPTIONS_WITH_VALUE = frozenset({"--registry", "--runtime-root", "--format"})
GLOBAL_OPTIONS_WITH_EQUALS = tuple(
	f"{option}=" for option in sorted(GLOBAL_OPTIONS_WITH_VALUE)
)

_REGISTRY_OPTIONAL_COMMANDS = frozenset(
	{
		"bootstrap",
		"bootstrap-command-pack",
		"agent-onboard",
		"connect",
		"codex-cli-bootstrap-message",
		"codex-cli-bounded-visible-pilot-adapter",
		"codex-cli-exec-handoff",
		"codex-cli-visible-first-response-capture-plan",
		"codex-cli-local-driver-plan",
		"codex-cli-local-scheduler-exec",
		"codex-cli-local-scheduler-tick",
		"codex-cli-one-message-loop-pilot",
		"codex-cli-runtime-idle-detector",
		"codex-cli-session-probe",
		"codex-cli-visible-attach-acceptance",
		"codex-cli-visible-local-driver-pilot",
		"codex-cli-visible-driver-run",
		"codex-cli-visible-driver-plan",
		"codex-cli-visible-session-proof",
		"commands",
		"demo",
		"doctor",
		"first-run-report",
		"usage-ping",
		"performance-diagnosis",
		"new-project-prompt",
		"resolve-agent-thread",
		"resolve-peer-route",
		"start-goal",
		"slash-commands",
		"workflow-skills",
		"heartbeat-prompt",
		"supervisor-event",
		"supervisor-observe",
		"supervisor-prompt",
		"sync-global",
		"uninstall-project",
		"version",
		"host-mode-plan",
		"migrate-local-state",
	}
)

_STATUS_COMMANDS = frozenset({"check", "status", "diagnose", "review-packet"})
_SELECTED_COMMANDS = _STATUS_COMMANDS | {
	"todo", "quota", "task-lease", "change-window", "delegation", "turn", "doctor", "commands",
	"authority-archive", "coordination-shadow", "extension", "slash-commands",
}


class LoopXArgumentParser(argparse.ArgumentParser):
	"""Require complete option names across the automation-facing CLI."""

	def __init__(self, *args, **kwargs) -> None:
		kwargs.setdefault("allow_abbrev", False)
		super().__init__(*args, **kwargs)


class _SelectedParseRejected(Exception):
	"""Signal that the complete parser must render the canonical diagnostic."""


class _SelectedArgumentParser(LoopXArgumentParser):
	def error(self, message: str) -> None:
		raise _SelectedParseRejected(message)


def print_payload(
	payload: dict[str, object],
	fmt: str,
	markdown_renderer: Callable[[dict[str, object]], str],
) -> None:
	from .usage_ping import capture_result
	capture_result(payload)
	if fmt == "json":
		print(json.dumps(payload, ensure_ascii=False, indent=2))
	else:
		print(markdown_renderer(payload))


def add_subcommand_format(arg_parser: argparse.ArgumentParser) -> None:
	arg_parser.add_argument(
		"--format",
		dest="subcommand_format",
		choices=["markdown", "json"],
		help="Output format for this subcommand. Equivalent to global --format before the command.",
	)


def output_format(args: argparse.Namespace, *local_dests: str) -> str:
	for dest in (*local_dests, "subcommand_format"):
		value = getattr(args, dest, None)
		if value:
			return str(value)
	return str(getattr(args, "format", None) or "markdown")


def resolve_global_output_format(args: argparse.Namespace) -> str:
	if getattr(args, "format", None):
		return str(args.format)
	if args.command == "quota" and getattr(args, "quota_command", None) == "should-run":
		return "json"
	return "markdown"


def build_cli_parser(
	*, parser_class: type[LoopXArgumentParser] = LoopXArgumentParser
) -> tuple[LoopXArgumentParser, argparse._SubParsersAction]:
	"""Build the one canonical global grammar and return its command registrar."""

	parser = parser_class(description="LoopX control-plane helper.")
	parser.add_argument("--version", action="version", version=f"loopx {__version__}")
	parser.add_argument(
		"--registry",
		default=str(default_registry_path()),
		help="Registry path, or @host-global for this host's selected default global registry.",
	)
	parser.add_argument("--runtime-root", help="Override registry common_runtime_root.")
	parser.add_argument("--format", choices=["markdown", "json"])
	return parser, parser.add_subparsers(dest="command", required=True)


def user_supplied_registry(argv: list[str] | None) -> bool:
	values = sys.argv[1:] if argv is None else argv
	return any(value == "--registry" or value.startswith("--registry=") for value in values)


def resolve_cli_registry(
	args: argparse.Namespace, raw_argv: list[str]
) -> tuple[Path, bool]:
	"""Resolve the shared registry path after canonical argument parsing."""

	registry_path = Path(args.registry).expanduser()
	registry_was_configured = user_supplied_registry(raw_argv) or bool(
		os.environ.get("LOOPX_REGISTRY")
	)
	if str(args.registry) == HOST_GLOBAL_REGISTRY_SELECTOR:
		try:
			runtime_root = (
				Path(args.runtime_root).expanduser()
				if args.runtime_root
				else select_default_runtime_root()
			)
		except ValueError as exc:
			raise SystemExit(str(exc)) from exc
		return global_registry_path(runtime_root), True
	project_register_uses_default_registry = (
		args.command == "project"
		and args.project_command == "register"
		and not registry_was_configured
	)
	if project_register_uses_default_registry:
		registry_path = Path(args.knowledge_root).expanduser() / ".loopx" / "registry.json"
	if (
		args.command not in _REGISTRY_OPTIONAL_COMMANDS
		and not (args.command == "canary" and not getattr(args, "goal_id", None))
		and not project_register_uses_default_registry
		and not registry_was_configured
		and not registry_path.exists()
	):
		try:
			runtime_root = (
				Path(args.runtime_root).expanduser()
				if args.runtime_root
				else select_default_runtime_root()
			)
		except ValueError as exc:
			raise SystemExit(str(exc)) from exc
		fallback_registry = global_registry_path(runtime_root)
		if fallback_registry.exists():
			registry_path = fallback_registry
	return registry_path, registry_was_configured


def enforce_native_controller_guard(args: argparse.Namespace) -> int | None:
	if os.environ.get("LOOPX_KUNLUNCODE_OUTER_CONTROLLER") != "1":
		return None
	from .kunluncode_goal_mode.guards import native_controller_cli_write_block

	native_write_block = native_controller_cli_write_block(args)
	if native_write_block is None:
		return None
	print(json.dumps(native_write_block, ensure_ascii=False, indent=2), file=sys.stderr)
	return 2


def _top_level_command(argv: list[str]) -> str | None:
	index = 0
	while index < len(argv):
		value = argv[index]
		if value in GLOBAL_OPTIONS_WITH_VALUE:
			if index + 1 >= len(argv):
				return None
			index += 2
			continue
		if value.startswith(GLOBAL_OPTIONS_WITH_EQUALS):
			index += 1
			continue
		if value.startswith("-"):
			return None
		return value
	return None


def _build_selected_parser(command: str) -> LoopXArgumentParser:
	parser, subparsers = build_cli_parser(parser_class=_SelectedArgumentParser)
	if command in _STATUS_COMMANDS:
		from .cli_commands.status_registration import register_status_commands

		register_status_commands(subparsers, add_subcommand_format)
	elif command == "todo":
		from .cli_commands.todo_registration import register_todo_command

		register_todo_command(subparsers, add_subcommand_format)
	elif command == "quota":
		from .cli_commands.quota_registration import register_quota_command

		register_quota_command(subparsers)
	elif command == "task-lease":
		from .cli_commands.task_lease import register_task_lease_command

		register_task_lease_command(subparsers, add_subcommand_format)
	elif command == "change-window":
		from .capabilities.repository_change_window.cli import register_repository_change_window_commands

		register_repository_change_window_commands(subparsers, add_subcommand_format)
	elif command == "delegation":
		from .cli_commands.delegation import register_delegation

		register_delegation(subparsers, add_subcommand_format)
	elif command == "turn":
		from .cli_commands.turn_registration import register_turn_commands

		register_turn_commands(subparsers, add_subcommand_format)
	elif command == "authority-archive":
		from .cli_commands.authority_archive import register_authority_archive_command

		register_authority_archive_command(subparsers, add_subcommand_format)
	elif command == "coordination-shadow":
		from .cli_commands.coordination_shadow import register_coordination_shadow_command

		register_coordination_shadow_command(subparsers, add_subcommand_format)
	elif command == "extension":
		from .cli_commands.extension import register_extension_commands

		register_extension_commands(subparsers, add_subcommand_format)
	elif command == "slash-commands":
		from .cli_commands.slash_commands import register_slash_commands_command

		register_slash_commands_command(subparsers, add_subcommand_format)
	elif command == "doctor":
		from .cli_commands.doctor import register_doctor_command

		register_doctor_command(subparsers, add_subcommand_format)
	elif command == "commands":
		from .help_surface import register_command_reference

		register_command_reference(subparsers)
	else:  # pragma: no cover - caller guards the private interface
		raise ValueError(f"unsupported selected command: {command}")
	return parser


def _dispatch_common_command(
	args: argparse.Namespace,
	*,
	registry_path: Path,
	allow_missing_registry: bool,
) -> int | None:
	if args.command == "task-lease":
		from .cli_commands.task_lease import handle_task_lease_command

		return handle_task_lease_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	if args.command == "authority-archive":
		from .cli_commands.authority_archive import handle_authority_archive_command

		return handle_authority_archive_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	if args.command == "coordination-shadow":
		from .cli_commands.coordination_shadow import handle_coordination_shadow_command

		return handle_coordination_shadow_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	if args.command == "extension":
		from .cli_commands.extension import handle_extension_command

		return handle_extension_command(
			args, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	if args.command == "slash-commands":
		from .cli_commands.slash_commands import handle_slash_commands_command

		return handle_slash_commands_command(
			args, output_format=output_format, print_payload=print_payload,
		)
	if args.command == "doctor":
		from .cli_commands.doctor import handle_doctor_command

		return handle_doctor_command(args, print_payload, registry_path=registry_path)
	if args.command == "commands":
		from .help_surface import (
			build_command_reference_payload, render_command_reference_markdown,
		)

		print_payload(
			build_command_reference_payload(), output_format(args),
			render_command_reference_markdown,
		)
		return 0
	if args.command == "delegation":
		from .cli_commands.delegation import handle_delegation
		from .paths import (
			effective_runtime_root,
		)

		return handle_delegation(
			args, registry_path,
			effective_runtime_root(registry_path, args.runtime_root),
		)
	if args.command == "turn":
		from .cli_commands.turn import handle_turn_command

		return handle_turn_command(
			args,
			registry_path=registry_path,
			runtime_root_arg=args.runtime_root,
			output_format=output_format,
			print_payload=print_payload,
		)
	if args.command == "change-window":
		from .capabilities.repository_change_window.cli import handle_repository_change_window_command

		return handle_repository_change_window_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	if args.command in _STATUS_COMMANDS:
		from .cli_commands.status import (
			handle_check_command,
			handle_diagnose_command,
			handle_review_packet_command,
			handle_status_command,
		)

		if args.command == "check":
			return handle_check_command(
				args,
				registry_path=registry_path,
				runtime_root_arg=args.runtime_root,
				allow_missing_registry=allow_missing_registry,
				print_payload=print_payload,
			)
		if args.command == "status":
			return handle_status_command(
				args,
				registry_path=registry_path,
				runtime_root_arg=args.runtime_root,
				output_format=output_format,
				print_payload=print_payload,
			)
		if args.command == "diagnose":
			return handle_diagnose_command(
				args,
				registry_path=registry_path,
				runtime_root_arg=args.runtime_root,
				output_format=output_format,
				print_payload=print_payload,
			)
		return handle_review_packet_command(
			args,
			registry_path=registry_path,
			runtime_root_arg=args.runtime_root,
			output_format=output_format,
			print_payload=print_payload,
		)
	if args.command == "todo":
		from .capabilities.periodic_report.post_writeback_hook import (
			build_periodic_report_post_writeback_projection,
			periodic_report_post_writeback_hooks_for_goal,
		)
		from .cli_commands.todo import handle_todo_command
		from .cli_rollout import append_cli_rollout_event
		from .paths import (
			effective_runtime_root,
		)

		return handle_todo_command(
			args,
			registry_path=registry_path,
			runtime_root_arg=args.runtime_root,
			format_name=output_format(args),
			print_payload=print_payload,
			append_cli_rollout_event=append_cli_rollout_event,
			post_writeback_hooks=(
				periodic_report_post_writeback_hooks_for_goal(
					registry_path=registry_path,
					goal_id=args.goal_id,
					runtime_root=effective_runtime_root(
						registry_path,
						args.runtime_root,
					),
				)
				if args.todo_command == "complete"
				else ()
			),
			post_writeback_projection_builder=(
				build_periodic_report_post_writeback_projection
				if args.todo_command == "complete"
				else None
			),
		)
	if args.command == "quota":
		from .cli_commands.quota import handle_quota_command
		from .cli_rollout import append_cli_rollout_event

		return handle_quota_command(
			args,
			registry_path=registry_path,
			runtime_root_arg=args.runtime_root,
			print_payload=print_payload,
			append_cli_rollout_event=append_cli_rollout_event,
		)
	return None


def dispatch_common_command(
	args: argparse.Namespace,
	*,
	registry_path: Path,
	allow_missing_registry: bool,
) -> int | None:
	from .control_plane.effect_runtime import effect_runtime_request_scope

	with effect_runtime_request_scope():
		return _dispatch_common_command(
			args,
			registry_path=registry_path,
			allow_missing_registry=allow_missing_registry,
		)


def _dispatch_selected(args: argparse.Namespace, raw_argv: list[str]) -> int:
	from .usage_ping import select_operation
	select_operation(args)
	args.format = resolve_global_output_format(args)
	guard_result = enforce_native_controller_guard(args)
	if guard_result is not None:
		return guard_result
	registry_path, _registry_was_configured = resolve_cli_registry(args, raw_argv)
	result = dispatch_common_command(
		args,
		registry_path=registry_path,
		allow_missing_registry=not user_supplied_registry(raw_argv),
	)
	if result is None:  # pragma: no cover - caller guards the private interface
		raise AssertionError(f"selected command was not dispatched: {args.command}")
	return result


def _run_full_cli(raw_argv: list[str]) -> int:
	from .cli import main as cli_main

	return cli_main(raw_argv)


def main(argv: list[str] | None = None) -> int:
	raw_argv = sys.argv[1:] if argv is None else list(argv)
	if raw_argv == ["--version"]:
		print(f"loopx {__version__}")
		return 0

	from .help_surface import render_concise_help, top_level_help_requested

	if top_level_help_requested(raw_argv):
		print(render_concise_help(sys.argv[0] if argv is None else "loopx"), end="")
		return 0
	command = _top_level_command(raw_argv)
	from .usage_ping import begin, finish

	ticket = begin(command or "")
	code = 1
	error = None
	try:
		code = _run_command(command, raw_argv)
		return code
	except BaseException as exc:
		error = exc
		if isinstance(exc, SystemExit):
			code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
		raise
	finally:
		finish(ticket, command or "", code, error)


def _run_command(command: str | None, raw_argv: list[str]) -> int:
	if command in _SELECTED_COMMANDS:
		try:
			args = _build_selected_parser(command).parse_args(raw_argv)
		except _SelectedParseRejected:
			return _run_full_cli(raw_argv)
		return _dispatch_selected(args, raw_argv)
	return _run_full_cli(raw_argv)


if __name__ == "__main__":
	raise SystemExit(main())
