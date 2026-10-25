"""Todo read CLI transport; provider rules stay in the existing read owner."""
from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path

from ..control_plane.todos.list_readback import list_goal_todos
from ..control_plane.todos.markdown import render_todo_markdown
from .todo_argument_validation import (
    validate_capability_gap_options,
    validate_shared_todo_options,
    validate_todo_list_options,
)
from .todo_event import RolloutEventAppender, append_todo_rollout_event, todo_error_payload


def handle_todo_list_command(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    print_payload: Callable[[dict[str, object], str, Callable[[dict[str, object]], str]], None],
    append_cli_rollout_event: RolloutEventAppender,
    format_name: str | None = None,
    list_reader: Callable[..., dict[str, object]] = list_goal_todos,
) -> int:
    try:
        validate_shared_todo_options(args)
        validate_capability_gap_options(args)
        validate_todo_list_options(args)
        payload = list_reader(
            registry_path=registry_path,
            goal_id=args.goal_id,
            role=args.role,
            status=args.status,
            todo_id=args.todo_id,
            agent_id=args.agent_id,
            limit=args.todo_limit,
            thin=bool(args.todo_thin),
            project=Path(args.project).expanduser() if args.project else None,
            state_file=Path(args.state_file).expanduser() if args.state_file else None,
            runtime_root_arg=runtime_root_arg,
            json_references=(format_name or str(getattr(args, "format", None) or "markdown")) == "json",
        )
    except Exception as exc:
        from ..usage_ping import capture_failure
        capture_failure(exc)
        payload = todo_error_payload(
            args, exc, registry_path=registry_path, runtime_root_arg=runtime_root_arg,
        )
    append_todo_rollout_event(
        payload, args=args, registry_path=registry_path,
        runtime_root_arg=runtime_root_arg,
        append_cli_rollout_event=append_cli_rollout_event,
    )
    output_format = format_name or str(getattr(args, "format", None) or "markdown")
    print_payload(payload, output_format, render_todo_markdown)
    return 0 if payload.get("ok") else 1
