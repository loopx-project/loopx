from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from . import __version__
from .cli_commands.summary_all_registration import GLOBAL_READ_COMMANDS
from .paths import default_registry_path, global_registry_path, select_default_runtime_root


HOST_GLOBAL_REGISTRY_SELECTOR = "@host-global"


GLOBAL_OPTIONS_WITH_VALUE = frozenset({"--registry", "--runtime-root", "--format", "--cli-bin"})
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
PROJECT_LIFECYCLE_COMMANDS = {
	"refresh-state", "checkpoint-context", "read-only-map", "reward", "operator-gate",
}
# Existing CLI vocabulary; this group selects loading, not registry authority.
_REGISTRY_INSPECTION_COMMANDS = frozenset({"registry", "registry-boundary"})
_SELECTED_COMMANDS = _STATUS_COMMANDS | GLOBAL_READ_COMMANDS | PROJECT_LIFECYCLE_COMMANDS | _REGISTRY_INSPECTION_COMMANDS | {
	"todo", "quota", "task-lease", "change-window", "delegation", "turn", "doctor", "commands", "version",
	"bootstrap", "connect", "configure-goal", "start-goal", "bootstrap-command-pack",
	"register-agent", "bind-agent-thread",
	"authority-archive", "coordination-shadow", "extension", "slash-commands",
	"manager-inbox",
	"agent-context", "agent-directory", "agent-capabilities",
	"handoff", "handoff-mode",
	"performance-diagnosis",
	"heartbeat-prompt",
	"heartbeat-prequota",
	"goal-acceptance",
	"host-mode-plan",
	"capability",
	"history",
}
# Existing CLI vocabulary; this set chooses loading, not operation authority.
_CAPABILITY_DISCOVERY_COMMANDS = frozenset({"list", "show"})


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
		# JSON is the machine contract; retain every value and remove only
		# presentation whitespace. Human-readable output remains Markdown.
		print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
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
	if args.command == "manager-inbox":
		return "json"
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
	from .command_invocation import validate_cli_bin
	parser.add_argument(
		"--cli-bin", dest="control_cli_bin", type=validate_cli_bin,
		help="Preserve this executable name/path in generated control commands; does not execute it or change permissions.",
	)
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
	if args.command == "update" and getattr(args, "update_action", None) == "activate":
		# Service activation reads its route from the loaded original manager.
		# It neither opens Goal authority nor guesses a default Chat store.
		return registry_path, registry_was_configured
	if args.command == "quota" and getattr(args, "quota_command", None) in {"context-read", "context-project", "replan-view"}:
		from .cli_commands.context_machine import has_explicit_context_read_source

		# Saved views own source validation (including missing references) and
		# native-session scope; machine defaults keep their separate route owner.
		if args.quota_command in {"context-project", "replan-view"} or has_explicit_context_read_source(args):
			return registry_path, registry_was_configured
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
		args.command == "resolve-agent-thread"
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
		from .project_alias import resolve_canonical_project_alias

		alias = resolve_canonical_project_alias(
			Path.cwd(), global_registry=global_registry_path(runtime_root)
		)
		source_registry = alias.get("source_registry")
		canonical_project = alias.get("canonical_project")
		if alias.get("applied") and source_registry and canonical_project:
			source_path = Path(str(source_registry)).expanduser().resolve()
			expected_path = (
				Path(str(canonical_project)).expanduser().resolve()
				/ ".loopx"
				/ "registry.json"
			)
			if source_path == expected_path and source_path.is_file():
				registry_path = source_path
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


def parse_cli_args(parser: LoopXArgumentParser, argv: list[str]) -> argparse.Namespace:
	if _top_level_command(argv) == "quota":
		from .cli_commands.quota_registration import normalize_context_list_args

		argv = normalize_context_list_args(argv)
	return parser.parse_args(argv)


@contextmanager
def cli_invocation_route(argv: list[str]):
	"""Read the global route only; the canonical parser still validates argv."""
	from .command_invocation import loopx_cli_route
	program = None
	index = 0
	while index < len(argv):
		value = argv[index]
		option = value.split("=", 1)[0]
		if option not in GLOBAL_OPTIONS_WITH_VALUE:
			break
		if "=" in value:
			argument = value.split("=", 1)[1]
		else:
			index += 1
			argument = argv[index] if index < len(argv) else None
		if option == "--cli-bin":
			program = argument
		index += 1
	# A standalone core may be invoked without activating its environment. Its
	# returned commands must stay on this artifact, even when PATH/PYTHONPATH
	# names another LoopX installation. Explicit routes remain caller-owned.
	default_argv = [sys.executable, "-I", "-m", "loopx.entrypoint"] if _standalone_core() else None
	with loopx_cli_route(program, default_argv=default_argv):
		yield


def _register_registry_inspection_commands(subparsers: argparse._SubParsersAction) -> None:
	"""Share the existing registry grammar with the complete support parser."""
	subparsers.add_parser(
		"registry", help="Inspect registry goals and adapter declarations."
	)
	registry_boundary_parser = subparsers.add_parser(
		"registry-boundary",
		help="Classify a registry file as local-only, global-local, public projection, or public fixture.",
	)
	registry_boundary_parser.add_argument(
		"--path",
		help="Registry path to classify. Defaults to the active --registry path.",
	)
	registry_boundary_parser.add_argument(
		"--require-not-tracked",
		action="store_true",
		help=(
			"For registries that cannot be pushed, return non-zero if tracked "
			"or if Git cannot verify the tracking state."
		),
	)
	registry_boundary_parser.add_argument(
		"--require-gitignored",
		action="store_true",
		help="Return non-zero if the registry should be ignored but is neither ignored nor tracked.",
	)


def _handle_registry_inspection_command(
	args: argparse.Namespace,
	*,
	registry_path: Path,
	print_payload: Callable[[dict[str, Any], str, Callable[[dict[str, Any]], str]], None],
	inspect_registry: Callable[[Path], dict[str, Any]],
	render_registry_markdown: Callable[[dict[str, Any]], str],
	inspect_registry_boundary: Callable[[Path], dict[str, Any]],
	render_registry_boundary_markdown: Callable[[dict[str, Any]], str],
) -> int:
	"""Keep one inspection adapter and the full CLI's existing patch points."""
	if args.command == "registry":
		payload = inspect_registry(registry_path)
		print_payload(payload, args.format, render_registry_markdown)
		return 0 if payload.get("ok") else 1

	boundary_path = Path(args.path).expanduser() if args.path else registry_path
	payload = inspect_registry_boundary(boundary_path)
	git = payload.get("git") if isinstance(payload.get("git"), dict) else {}
	if (
		args.require_not_tracked
		and payload.get("ok")
		and not payload.get("github_push_allowed")
		and git.get("probe_status") == "git_unavailable"
		and (git.get("inside_worktree") or git.get("worktree_discovery_unknown"))
	):
		payload["ok"] = False
		payload["error"] = "Could not determine whether the registry is tracked by Git."
	if (
		args.require_gitignored
		and payload.get("ok")
		and payload.get("should_be_gitignored")
	):
		if (
			git.get("inside_worktree")
			and not git.get("ignored")
			and not git.get("tracked")
		):
			payload = dict(payload)
			payload["ok"] = False
			payload.setdefault("risks", []).append("registry_should_be_gitignored")
	print_payload(payload, args.format, render_registry_boundary_markdown)
	return 0 if payload.get("ok") else 1


def _build_selected_parser(command: str) -> LoopXArgumentParser:
	parser, subparsers = build_cli_parser(parser_class=_SelectedArgumentParser)
	if command == "start-goal":
		from .cli_commands.start_goal import register_start_goal_command

		register_start_goal_command(subparsers)
	elif command == "bootstrap-command-pack":
		from .cli_commands.starter_bootstrap_registration import register_starter_bootstrap_commands

		register_starter_bootstrap_commands(subparsers)
	elif command in {"register-agent", "bind-agent-thread"}:
		from .cli_commands.registry_admin import register_registry_admin_commands

		register_registry_admin_commands(subparsers)
	elif command in {"bootstrap", "connect"}:
		from .cli_commands.bootstrap_connect import register_bootstrap_connect_command

		register_bootstrap_connect_command(subparsers)
	elif command == "configure-goal":
		from .cli_commands.registry_admin_configure import register_configure_goal_command

		register_configure_goal_command(subparsers)
	elif command in _REGISTRY_INSPECTION_COMMANDS:
		_register_registry_inspection_commands(subparsers)
	elif command in _STATUS_COMMANDS:
		from .cli_commands.status_registration import register_status_commands

		register_status_commands(subparsers, add_subcommand_format)
	elif command in GLOBAL_READ_COMMANDS:
		from .cli_commands.summary_all_registration import register_summary_all_command

		register_summary_all_command(subparsers, add_subcommand_format)
	elif command in PROJECT_LIFECYCLE_COMMANDS:
		from .cli_commands.project_lifecycle import register_project_lifecycle_commands

		register_project_lifecycle_commands(subparsers, add_subcommand_format)
	elif command == "todo":
		from .cli_commands.todo_registration import register_todo_command

		register_todo_command(subparsers, add_subcommand_format)
	elif command == "quota":
		from .cli_commands.quota_registration import register_quota_command

		register_quota_command(subparsers)
	elif command == "task-lease":
		from .cli_commands.task_lease import register_task_lease_command

		register_task_lease_command(subparsers, add_subcommand_format)
	elif command == "handoff":
		from .cli_commands.todo_continuation import register_todo_continuation

		register_todo_continuation(subparsers, add_subcommand_format)
	elif command == "handoff-mode":
		from .cli_commands.handoff_mode import register_handoff_mode_command

		register_handoff_mode_command(subparsers, add_subcommand_format)
	elif command == "change-window":
		from .capabilities.repository_change_window.cli import register_repository_change_window_commands

		register_repository_change_window_commands(subparsers, add_subcommand_format)
	elif command == "delegation":
		from .cli_commands.delegation import register_delegation

		register_delegation(subparsers, add_subcommand_format)
	elif command == "turn":
		from .cli_commands.turn_registration import register_turn_commands

		register_turn_commands(subparsers, add_subcommand_format)
	elif command == "manager-inbox":
		from .cli_commands.manager_inbox import register_manager_inbox

		register_manager_inbox(subparsers, add_subcommand_format)
	elif command == "agent-context":
		from .cli_commands.agent_context import register_agent_context

		register_agent_context(subparsers, add_subcommand_format)
	elif command == "agent-directory":
		from .cli_commands.agent_directory import register_agent_directory

		register_agent_directory(subparsers, add_subcommand_format)
	elif command == "agent-capabilities":
		from .cli_commands.agent_capabilities import register_agent_capabilities

		register_agent_capabilities(subparsers, add_subcommand_format)
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
	elif command == "version":
		from .cli_commands.version import register_version_command

		register_version_command(subparsers, add_subcommand_format)
	elif command == "performance-diagnosis":
		from .capabilities.performance_diagnosis.cli import register_performance_diagnosis_commands

		register_performance_diagnosis_commands(subparsers, add_subcommand_format)
	elif command in {"heartbeat-prompt", "heartbeat-prequota"}:
		from .cli_commands.support_control_heartbeat_registration import register_heartbeat_control_commands

		register_heartbeat_control_commands(subparsers, add_subcommand_format)
	elif command == "goal-acceptance":
		from .cli_commands.goal_acceptance import register_goal_acceptance_command

		register_goal_acceptance_command(subparsers, add_subcommand_format)
	elif command == "host-mode-plan":
		from .cli_commands.host_mode_plan import register_host_mode_plan_command

		register_host_mode_plan_command(subparsers, add_subcommand_format)
	elif command == "capability":
		from .cli_commands.capability import register_capability_commands

		register_capability_commands(subparsers, add_subcommand_format)
	elif command == "history":
		from .cli_commands.history import register_history_command

		register_history_command(subparsers)
	else:  # pragma: no cover - caller guards the private interface
		raise ValueError(f"unsupported selected command: {command}")
	return parser


def _dispatch_common_command(
	args: argparse.Namespace,
	*,
	registry_path: Path,
	allow_missing_registry: bool,
) -> int | None:
	if args.command == "start-goal":
		from .cli_commands.start_goal import handle_start_goal_command

		return handle_start_goal_command(
			args, print_payload, runtime_root_arg=args.runtime_root,
		)
	if args.command in {"bootstrap-command-pack", "agent-onboard"}:
		from .bootstrap_command_pack import BootstrapRegistryConflict
		from .cli_commands.starter_bootstrap import (
			handle_agent_onboard_command, handle_loopx_bootstrap_command_pack_command,
		)

		runtime_root_arg = args.runtime_root
		explicit_registry = None
		if not allow_missing_registry:
			if str(args.registry) == HOST_GLOBAL_REGISTRY_SELECTOR:
				if runtime_root_arg:
					runtime_root_arg = str(registry_path.parent.absolute())
			else:
				explicit_registry = registry_path
		handler = (
			handle_agent_onboard_command if args.command == "agent-onboard"
			else handle_loopx_bootstrap_command_pack_command
		)
		try:
			return handler(args, print_payload, runtime_root_arg=runtime_root_arg,
				explicit_registry=explicit_registry)
		except BootstrapRegistryConflict as exc:
			print_payload({"ok": False, "error": str(exc)}, args.format, lambda result: str(result["error"]))
			return 2
	if args.command in {"bootstrap", "connect"}:
		from .cli_commands.bootstrap_connect import handle_bootstrap_connect_command

		return handle_bootstrap_connect_command(
			args, registry_path=registry_path, print_payload=print_payload,
		)
	if args.command in {"configure-goal", "register-agent", "bind-agent-thread"}:
		from .cli_commands.registry_admin import handle_registry_admin_command

		return handle_registry_admin_command(
			args, registry_path=registry_path, print_payload=print_payload,
		)
	if args.command == "host-mode-plan":
		from .cli_commands.host_mode_plan import handle_host_mode_plan_command

		return handle_host_mode_plan_command(
			args, output_format=output_format, print_payload=print_payload,
		)
	if args.command == "heartbeat-prompt":
		from .cli_commands.support_control_heartbeat import handle_heartbeat_prompt_command

		return handle_heartbeat_prompt_command(
			args, registry_path=registry_path,
			registry_was_supplied=not allow_missing_registry,
			print_payload=print_payload, output_format=output_format,
		)
	if args.command == "goal-acceptance":
		from .cli_commands.goal_acceptance import handle_goal_acceptance_command

		return handle_goal_acceptance_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	if args.command == "performance-diagnosis":
		from .capabilities.performance_diagnosis.cli import handle_performance_diagnosis_command

		return handle_performance_diagnosis_command(
			args, output_format=output_format, print_payload=print_payload,
		)
	if args.command in {"agent-context", "agent-directory", "agent-capabilities"}:
		from .paths import effective_runtime_root

		if args.command == "agent-context":
			from .cli_commands.agent_context import handle_agent_context as handle
		elif args.command == "agent-directory":
			from .cli_commands.agent_directory import handle_agent_directory as handle
		else:
			from .cli_commands.agent_capabilities import handle_agent_capabilities as handle
		return handle(
			args, registry_path, effective_runtime_root(registry_path, args.runtime_root),
			print_payload, output_format,
		)
	if args.command in PROJECT_LIFECYCLE_COMMANDS:
		from .cli_commands.project_lifecycle import handle_project_lifecycle_command
		from .cli_rollout import append_cli_rollout_event
		from .capabilities.periodic_report.post_writeback_hook import (
			build_periodic_report_post_writeback_projection,
			periodic_report_post_writeback_hooks_for_goal,
		)
		from .paths import effective_runtime_root

		return handle_project_lifecycle_command(
			args, registry_path=registry_path, print_payload=print_payload,
			output_format=output_format, append_cli_rollout_event=append_cli_rollout_event,
			post_writeback_hooks=(periodic_report_post_writeback_hooks_for_goal(
				registry_path=registry_path, goal_id=args.goal_id,
				runtime_root=effective_runtime_root(registry_path, args.runtime_root),
			) if args.command == "refresh-state" else ()),
			post_writeback_projection_builder=(build_periodic_report_post_writeback_projection
				if args.command == "refresh-state" else None),
		)
	if args.command == "manager-inbox":
		from .cli_commands.manager_inbox import handle_manager_inbox
		from .control_plane.projects.registry_codec import load_project_registry
		from .paths import resolve_runtime_root

		return handle_manager_inbox(args, registry_path, resolve_runtime_root(
			load_project_registry(registry_path), args.runtime_root, registry_path=registry_path,
		))
	if args.command == "task-lease":
		from .cli_commands.task_lease import handle_task_lease_command

		return handle_task_lease_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	if args.command in {"handoff", "handoff-mode"}:
		if args.command == "handoff":
			from .cli_commands.todo_continuation import handle_todo_continuation as handoff_handler
		else:
			from .cli_commands.handoff_mode import handle_handoff_mode_command as handoff_handler
		return handoff_handler(
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
			build_command_reference_payload(
				available_commands=_SELECTED_COMMANDS if _standalone_core() else None,
			), output_format(args),
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
	if args.command in GLOBAL_READ_COMMANDS:
		from .cli_commands.summary_all import handle_summary_all_command

		return handle_summary_all_command(
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
		if args.todo_command == "list":
			from .cli_commands.todo_list import handle_todo_list_command
			from .cli_rollout import append_cli_rollout_event

			return handle_todo_list_command(
				args,
				registry_path=registry_path,
				runtime_root_arg=args.runtime_root,
				format_name=output_format(args),
				print_payload=print_payload,
				append_cli_rollout_event=append_cli_rollout_event,
			)
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
	if args.command in _REGISTRY_INSPECTION_COMMANDS:
		from .registry import (
			inspect_registry, inspect_registry_boundary,
			render_registry_markdown, render_registry_boundary_markdown,
		)

		result = _handle_registry_inspection_command(
			args, registry_path=registry_path, print_payload=print_payload,
			inspect_registry=inspect_registry, render_registry_markdown=render_registry_markdown,
			inspect_registry_boundary=inspect_registry_boundary,
			render_registry_boundary_markdown=render_registry_boundary_markdown,
		)
	elif args.command == "version":
		from .cli_commands.version import handle_version_command

		result = handle_version_command(
			args, output_format=output_format, print_payload=print_payload,
		)
	elif args.command == "capability":
		from .cli_commands.capability import handle_capability_command

		# Keep the explicit full CLI's handler and protected operations unchanged.
		result = handle_capability_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			output_format=output_format, print_payload=print_payload,
		)
	elif args.command == "heartbeat-prequota":
		from .cli_commands.support_control_heartbeat import handle_heartbeat_prequota_command

		result = handle_heartbeat_prequota_command(
			args, registry_path=registry_path,
			registry_was_supplied=user_supplied_registry(raw_argv),
			output_format=output_format, print_payload=print_payload,
		)
	elif args.command == "history":
		from .cli_commands.history import handle_history_command

		# Direct full-CLI callers retain their original facade-bound handler.
		result = handle_history_command(
			args, registry_path=registry_path, runtime_root_arg=args.runtime_root,
			print_payload=print_payload,
		)
	else:
		result = dispatch_common_command(
			args,
			registry_path=registry_path,
			allow_missing_registry=not user_supplied_registry(raw_argv),
		)
	if result is None:  # pragma: no cover - caller guards the private interface
		raise AssertionError(f"selected command was not dispatched: {args.command}")
	return result


def _standalone_core() -> bool:
	from importlib.util import find_spec

	if find_spec("loopx.cli") is not None:
		return False
	from importlib.metadata import PackageNotFoundError, distribution
	from .python_install_owner import installed_loopx_distribution

	try:
		return installed_loopx_distribution(distribution).name == "loopx-core"
	except PackageNotFoundError:
		return False


def _run_full_cli(raw_argv: list[str]) -> int:
	try:
		from .cli import main as cli_main
	except ModuleNotFoundError as exc:
		if exc.name != "loopx.cli" or not _standalone_core():
			raise
		# Only the omitted full CLI is optional; never hide a broken dependency.
		parser, _ = build_cli_parser()
		parser.error(
			"this command or argument is unavailable in the standalone core; "
			"use its supported CLI options or the ordinary LoopX installation"
		)
	return cli_main(raw_argv)


def main(argv: list[str] | None = None) -> int:
	raw_argv = sys.argv[1:] if argv is None else list(argv)
	if raw_argv == ["--version"]:
		print(f"loopx {__version__}")
		return 0

	from .help_surface import render_concise_help, top_level_help_requested

	if top_level_help_requested(raw_argv):
		print(render_concise_help(sys.argv[0] if argv is None else "loopx",
			standalone_core=_standalone_core()), end="")
		return 0
	command = _top_level_command(raw_argv)
	if command is None and _standalone_core():
		# The canonical global parser handles version and malformed/missing
		# arguments without importing an intentionally omitted command facade.
		parser, _ = build_cli_parser()
		parser.parse_args(raw_argv)
		return 0
	from .usage_ping import begin, finish

	ticket = begin(command or "")
	code = 1
	error = None
	try:
		with cli_invocation_route(raw_argv):
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
	if command == "version":
		version_exports = sys.modules.get("loopx.cli_commands")
		# Inspect bound exports without triggering the lazy facade. Explicit
		# package/full-CLI imports retain their registrar and handler patch points.
		if "loopx.cli" in sys.modules or (
			version_exports is not None
			and ("handle_version_command" in vars(version_exports)
				or "register_version_command" in vars(version_exports))
		):
			return _run_full_cli(raw_argv)
	if command == "history" and "loopx.cli" in sys.modules:
		# An explicitly loaded facade keeps its existing handler/renderer bindings.
		return _run_full_cli(raw_argv)
	if command in _SELECTED_COMMANDS:
		try:
			args = parse_cli_args(_build_selected_parser(command), raw_argv)
		except _SelectedParseRejected:
			return _run_full_cli(raw_argv)
		if command == "history" and args.history_action is not None and not _standalone_core():
			# Select ordinary reads; maintenance keeps its complete original route.
			return _run_full_cli(raw_argv)
		if command == "capability":
			if args.capability_command not in _CAPABILITY_DISCOVERY_COMMANDS:
				return _run_full_cli(raw_argv)
			try:
				return _dispatch_selected(args, raw_argv)
			except _SelectedParseRejected:
				# Catalog/manifest refusals use their canonical argparse diagnostic.
				return _run_full_cli(raw_argv)
		return _dispatch_selected(args, raw_argv)
	return _run_full_cli(raw_argv)


if __name__ == "__main__":
	raise SystemExit(main())
