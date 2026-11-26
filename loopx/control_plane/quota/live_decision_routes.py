from __future__ import annotations

import os
import shlex
from pathlib import Path

from ...command_invocation import (
    current_loopx_cli_argv,
    loopx_cli_argv,
    resolve_command_path,
)
from .effect_program import is_turn_reference, turn_reference
from .live_decision_contracts import (
    as_json_map,
    as_object_list,
    mapping_or_empty,
    resolve_root_pair,
)


def _bind_followup_turn_argument(
    cli_args: list[object],
    reference: str,
    *,
    authoritative: bool,
) -> str:
    """Point a follow-up's ``--turn-instance-id`` value at ``reference`` in place.

    An authoritative Turn always wins, because it owns the receipt the follow-up
    replays. Without one only a shell reference is re-rendered, so a concrete
    projected Turn survives untouched. Returns the value the vector now carries,
    so a mirrored ``args`` block cannot disagree with the executed command.
    """

    index = (
        cli_args.index("--turn-instance-id")
        if "--turn-instance-id" in cli_args
        else -1
    )
    if index < 0:
        cli_args.extend(["--turn-instance-id", reference])
        return reference
    if index + 1 >= len(cli_args):
        cli_args.append(reference)
        return reference
    current_arg = cli_args[index + 1]
    if authoritative or is_turn_reference(
        current_arg if isinstance(current_arg, str) else None
    ):
        cli_args[index + 1] = reference
    return str(cli_args[index + 1])


def _scheduler_fallback_launcher() -> list[str]:
    """Keep an executable-bound replay on its selected Core installation."""

    fallback = "loopx-apply-rrule"
    route = current_loopx_cli_argv()
    if route[1:] != ["--cli-bin", route[0]]:
        return [fallback]
    configured = route[0]
    candidate = Path(configured)
    resolved = candidate if candidate.is_absolute() else resolve_command_path(configured)
    if resolved is None or not resolved.is_file():
        return [fallback, "--loopx", configured]
    executable = resolved.resolve()
    companion = executable.with_name(fallback)
    if os.name != "nt" and companion.is_file() and os.access(companion, os.X_OK):
        fallback = str(companion)
    return [fallback, "--loopx", str(executable)]


def bind_scheduler_followup_cli_routes(
    payload: dict[str, object],
    *,
    registry_path: Path,
    runtime_root: Path,
    turn_instance_id: str | None = None,
    source: str = "quota_cli_invocation",
) -> None:
    """Bind scheduler follow-ups to the registry/runtime/Turn that built the hint."""

    scheduler_hint = as_json_map(payload.get("scheduler_hint"))
    if scheduler_hint is None:
        return
    app_packet = as_json_map(scheduler_hint.get("app_automation"))
    if app_packet is None:
        return
    launcher_args = loopx_cli_argv()[1:]
    safe_turn_instance_id = str(turn_instance_id or "").strip()
    resolved_roots: tuple[str, str] | None = None

    def bound_roots() -> tuple[str, str]:
        nonlocal resolved_roots
        if resolved_roots is None:
            resolved_roots = resolve_root_pair(registry_path, runtime_root)
        return resolved_roots

    for hint_name in ("ack_hint", "failure_hint", "fallback_hint"):
        followup_hint = as_json_map(app_packet.get(hint_name))
        if followup_hint is None:
            continue
        cli_args = as_object_list(followup_hint.get("cli_args"))
        if not cli_args:
            continue
        if hint_name == "fallback_hint":
            if cli_args[0] != "loopx-apply-rrule" or "--registry" in cli_args:
                continue
            resolved_registry, resolved_runtime_root = bound_roots()
            bound_cli_args: list[object] = [
                *_scheduler_fallback_launcher(),
                "--registry",
                resolved_registry,
                "--runtime-root",
                resolved_runtime_root,
                *cli_args[1:],
            ]
            # The fallback replays this decision through ``loopx quota
            # should-run``, so it needs the Turn that owns the receipt.
            # Projections leave a shell reference here; a native Windows
            # host cannot expand the POSIX form and would replay an empty
            # Turn instead of the decision it came from.
            bound_turn = _bind_followup_turn_argument(
                bound_cli_args,
                turn_reference(safe_turn_instance_id),
                authoritative=bool(safe_turn_instance_id),
            )
            followup_hint["cli_args"] = bound_cli_args
            args_value = as_json_map(followup_hint.get("args"))
            if args_value is not None and "turn_instance_id" in args_value:
                args_value["turn_instance_id"] = bound_turn
            followup_hint["route_binding"] = {
                "schema_version": "codex_app_scheduler_fallback_route_v0",
                "source": source,
                "registry_bound": True,
                "runtime_root_bound": True,
                "turn_instance_bound": bool(safe_turn_instance_id),
            }
            continue
        bound_cli_args = list(cli_args)
        # Standalone Core emits an interpreter route. Its launcher
        # arguments must precede the CLI's global options.
        option_index = (
            len(launcher_args)
            if cli_args[: len(launcher_args)] == launcher_args
            else 0
        )
        if bound_cli_args[option_index : option_index + 1] != ["--registry"]:
            resolved_registry, resolved_runtime_root = bound_roots()
            bound_cli_args[option_index:option_index] = [
                "--registry",
                resolved_registry,
                "--runtime-root",
                resolved_runtime_root,
            ]
        if safe_turn_instance_id and "--turn-instance-id" not in bound_cli_args:
            execute_index = (
                bound_cli_args.index("--execute")
                if "--execute" in bound_cli_args
                else len(bound_cli_args)
            )
            bound_cli_args[execute_index:execute_index] = [
                "--turn-instance-id",
                safe_turn_instance_id,
            ]
            args_value = as_json_map(followup_hint.get("args"))
            if args_value is not None:
                args_value["turn_instance_id"] = safe_turn_instance_id
        followup_hint["cli_args"] = bound_cli_args
        followup_hint["route_binding"] = {
            "schema_version": (
                "scheduler_ack_cli_route_v0"
                if hint_name == "ack_hint"
                else "scheduler_failure_cli_route_v0"
            ),
            "source": source,
            "registry_bound": True,
            "runtime_root_bound": True,
            "turn_instance_bound": bool(safe_turn_instance_id),
        }


def bind_action_selection_cli_routes(
    payload: dict[str, object],
    *,
    registry_path: Path,
    runtime_root: Path,
) -> None:
    """Bind two-phase action-selection commands to this live CLI source."""

    interaction = mapping_or_empty(payload.get("interaction_contract"))
    cli_channel = as_json_map(interaction.get("cli_channel"))
    if cli_channel is None:
        return
    if cli_channel.get("selection_required") is not True:
        return
    selection_command = as_json_map(cli_channel.get("selection_command"))
    if selection_command is None:
        return
    route_prefix = selection_command.get("route_prefix")
    if not isinstance(route_prefix, str):
        return
    try:
        tokens = shlex.split(route_prefix)
    except ValueError:
        return
    if len(tokens) >= 3 and tokens[0] == "loopx":
        try:
            format_index = tokens.index("--format")
        except ValueError:
            return
        if tokens[format_index : format_index + 2] != ["--format", "json"]:
            return
        needs_registry = "--registry" not in tokens
        needs_runtime = "--runtime-root" not in tokens
        if not needs_registry and not needs_runtime:
            selection_command["route_prefix"] = shlex.join(tokens)
            return
        resolved_registry = (
            str(registry_path.expanduser().resolve()) if needs_registry else None
        )
        resolved_runtime = (
            str(runtime_root.expanduser().resolve()) if needs_runtime else None
        )
        if resolved_registry is not None:
            tokens[1:1] = ["--registry", resolved_registry]
            format_index += 2
        if resolved_runtime is not None:
            tokens[format_index:format_index] = [
                "--runtime-root",
                resolved_runtime,
            ]
        selection_command["route_prefix"] = shlex.join(tokens)
