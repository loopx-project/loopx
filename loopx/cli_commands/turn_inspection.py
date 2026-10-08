from __future__ import annotations

import argparse
from collections.abc import Callable
import json
import math
from pathlib import Path
import time

from ..control_plane.runtime.status_projection_cache import (
    resolve_status_projection_cache_runtime_root,
)
from .turn_rendering import render_loopx_turn_journal_inspection_markdown

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]
TURN_PROGRESS_EVENT_SCHEMA_VERSION = "loopx_turn_progress_event_v0"
_TERMINAL_TURN_STATUSES = {"committed", "stopped", "failed"}


def handle_turn_journal_inspection(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    if args.turn_command != "inspect-journal":
        return None
    from ..control_plane.turn_driver import (
        LOOPX_TURN_JOURNAL_INSPECTION_SCHEMA_VERSION,
        codex_cli_session_binding,
        inspect_loopx_turn_journal,
        load_loopx_turn_plan_from_journal,
    )

    try:
        runtime_root = resolve_status_projection_cache_runtime_root(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
        )
        scope = "todo"
        if args.retry_failed_turn:
            plan = load_loopx_turn_plan_from_journal(
                runtime_root, goal_id=args.goal_id, turn_key=args.turn_key,
            )
            scope = str(((plan.get("session") or {}).get("context_policy") or {}).get("binding_scope") or "todo")
        fmt = output_format(args)
        if args.watch:
            interval = args.watch_interval
            if not math.isfinite(interval) or interval <= 0:
                payload = {
                    "ok": False,
                    "schema_version": LOOPX_TURN_JOURNAL_INSPECTION_SCHEMA_VERSION,
                    "error": "watch interval must be a finite positive number",
                    "effects": [],
                }
                print_payload(
                    payload,
                    fmt,
                    render_loopx_turn_journal_inspection_markdown,
                )
                return 1

            previous: tuple[str, tuple[str, ...]] | None = None
            try:
                while True:
                    payload = inspect_loopx_turn_journal(
                        runtime_root,
                        goal_id=args.goal_id,
                        agent_id=args.agent_id,
                        turn_key=args.turn_key,
                        retry_failed=bool(args.retry_failed_turn),
                        session_binding_resolver=(
                            lambda turn_envelope: codex_cli_session_binding(
                                runtime_root,
                                turn_envelope,
                                session_scope=scope,
                            )
                        ),
                    )
                    if payload.get("ok") is not True:
                        print_payload(
                            payload,
                            fmt,
                            render_loopx_turn_journal_inspection_markdown,
                        )
                        return 1

                    status = str(payload.get("journal_status") or "")
                    phases = payload.get("completed_phases")
                    completed_phases = (
                        [str(phase) for phase in phases]
                        if isinstance(phases, list)
                        else []
                    )
                    snapshot = (status, tuple(completed_phases))
                    if snapshot != previous:
                        event: dict[str, object] = {
                            "schema_version": TURN_PROGRESS_EVENT_SCHEMA_VERSION,
                            "goal_id": args.goal_id,
                            "agent_id": args.agent_id,
                            "turn_key": args.turn_key,
                            "journal_status": status,
                            "completed_phases": completed_phases,
                            "effects": [],
                        }
                        if fmt == "json":
                            print(
                                json.dumps(
                                    event,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                flush=True,
                            )
                        else:
                            from .turn_rendering import (
                                render_loopx_turn_journal_progress_markdown,
                            )

                            print(
                                render_loopx_turn_journal_progress_markdown(event),
                                flush=True,
                            )
                        previous = snapshot
                    if status in _TERMINAL_TURN_STATUSES:
                        return 0
                    time.sleep(interval)
            except KeyboardInterrupt:
                return 130

        payload = inspect_loopx_turn_journal(
            runtime_root,
            goal_id=args.goal_id,
            agent_id=args.agent_id,
            turn_key=args.turn_key,
            retry_failed=bool(args.retry_failed_turn),
            session_binding_resolver=(
                lambda turn_envelope: codex_cli_session_binding(
                    runtime_root,
                    turn_envelope,
                    session_scope=scope,
                )
            ),
        )
    except Exception as exc:  # noqa: BLE001 - typed CLI failure boundary
        payload = {
            "ok": False,
            "schema_version": LOOPX_TURN_JOURNAL_INSPECTION_SCHEMA_VERSION,
            "error": str(exc),
            "effects": [],
        }
    print_payload(
        payload,
        output_format(args),
        render_loopx_turn_journal_inspection_markdown,
    )
    return 0 if payload.get("ok") else 1
