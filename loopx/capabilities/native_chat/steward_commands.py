"""In-process transport to the existing native command owners.

No stdout redirection or subprocess: original Chat workers can run concurrently.
Arguments are constructed by the host, never accepted from a model or HTTP body.
"""
from __future__ import annotations

import argparse
from typing import Any

from .steward_triage import (
    StewardFastPathError,
    build_portfolio_triage_projection,
    execute_steward_fast_path,
    parse_steward_fast_path_intent,
    resolve_all_registered_goal_ids,
)


class NativeStewardCommandError(ValueError):
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        super().__init__("the native steward operation was not committed: " + str(
            payload.get("error_code") or "unavailable") + ": " + str(
            payload.get("error") or payload.get("reason") or payload.get("message") or "inspect the native receipt"))


def _triage_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="steward-triage", add_help=False)
    parser.add_argument("--goal-id", action="append", default=[])
    parser.add_argument("--all-goals", action="store_true")
    parser.add_argument("--focus", default="all")
    parser.add_argument("--max-gates", type=int, default=8)
    parser.add_argument("--max-todos", type=int, default=8)
    parser.add_argument("--max-progress", type=int, default=5)
    return parser


def native_fast_path_triage(
    controller: Any,
    *,
    text: str,
    binding_id: str,
    source_ref: str,
    sender_ref: str,
    bindings: Any = None,
    private_human_message: bool = True,
    origin: str | None = None,
    session_context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Execute a read-only Steward Fast-Path triage query without leases or Turns."""

    active_bindings = bindings if bindings is not None else getattr(controller, "conversation_bindings", None)
    if active_bindings is None:
        raise StewardFastPathError(
            "steward conversation bindings are unavailable",
            code="steward_bindings_unavailable",
        )
    return execute_steward_fast_path(
        text=text,
        bindings=active_bindings,
        binding_id=binding_id,
        source_ref=source_ref,
        sender_ref=sender_ref,
        registry_path=controller.registry_path,
        runtime_root_arg=str(controller.coordination_runtime_root),
        private_human_message=private_human_message,
        origin=origin,
        session_context=session_context,
    )


def native_command(controller: Any, argv: list[str]) -> dict[str, Any]:
    if argv and argv[0] == "triage":
        try:
            parsed = _triage_parser().parse_args(argv[1:])
            authorized = (
                resolve_all_registered_goal_ids(controller.registry_path)
                if parsed.all_goals
                else list(parsed.goal_id)
            )
            return build_portfolio_triage_projection(
                registry_path=controller.registry_path,
                runtime_root_arg=str(controller.coordination_runtime_root),
                authorized_goal_ids=authorized,
                requested_goal_ids=list(parsed.goal_id) or None,
                focus=parsed.focus,
                max_gates=parsed.max_gates,
                max_todos=parsed.max_todos,
                max_progress=parsed.max_progress,
            )
        except (StewardFastPathError, SystemExit, ValueError) as exc:
            error_code = getattr(exc, "code", "invalid_triage_command")
            raise NativeStewardCommandError(
                {"ok": False, "error_code": error_code, "error": str(exc)}
            ) from exc

    from ...cli import build_parser
    from ...cli_rollout import append_cli_rollout_event
    from ...cli_commands.quota import handle_quota_command
    from ...cli_commands.todo import handle_todo_command
    from ...cli_commands.task_lease import handle_task_lease_command

    args = build_parser().parse_args(["--format", "json", *argv])
    result: dict[str, Any] = {}
    kwargs: dict[str, Any] = {"registry_path": controller.registry_path,
        "runtime_root_arg": str(controller.coordination_runtime_root),
        "print_payload": lambda payload, *_args: result.update(payload)}
    if args.command == "todo":
        code = handle_todo_command(args, **kwargs, format_name="json",
            append_cli_rollout_event=append_cli_rollout_event)
    elif args.command == "quota":
        code = handle_quota_command(args, **kwargs,
            append_cli_rollout_event=append_cli_rollout_event)
    elif args.command == "task-lease":
        code = handle_task_lease_command(args, **kwargs, output_format=lambda *_a, **_kw: "json")
    else:
        raise ValueError("unsupported native steward operation")
    if code or result.get("ok") is not True:
        raise NativeStewardCommandError(result)
    return result


__all__ = [
    "NativeStewardCommandError",
    "execute_steward_fast_path",
    "native_command",
    "native_fast_path_triage",
    "parse_steward_fast_path_intent",
]
