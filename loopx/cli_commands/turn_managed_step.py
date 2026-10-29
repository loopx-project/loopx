"""CLI surface for the managed-step consumer of same-Turn continuation.

Split from ``turn.py`` to respect the CLI command-owner size budget. The
subcommand reads the canonical Turn journal and a fresh control-plane envelope,
then sends both to the typed controller for qualification and continuation.
It prints the existing managed-step answer. It never launches a
host, writes state, or spends quota.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from typing import cast

from ..control_plane.runtime.status_projection_cache import (
    resolve_status_projection_cache_runtime_root,
)
from ..control_plane.turn_driver import (
    load_turn_journal,
    turn_journal_path,
)
from ..control_plane.turn_driver.managed_step import (
    LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION,
    decide_managed_step,
)
from .turn_decision import build_fresh_envelope_for_managed_step
from .turn_rendering import render_loopx_turn_managed_step_markdown

PrintPayload = Callable[
    [dict[str, object], str, Callable[[dict[str, object]], str]],
    None,
]
FormatSelector = Callable[..., str]


def _load_journal(
    runtime_root: Path,
    *,
    goal_id: str,
    turn_key: str,
) -> dict[str, object]:
    """Read the canonical journal, or refuse when it does not exist."""

    path = turn_journal_path(runtime_root, goal_id=goal_id, turn_key=turn_key)
    journal = load_turn_journal(path)
    if journal is None:
        raise ValueError("LoopX Turn journal does not exist")
    return cast(dict[str, object], journal)


def handle_turn_managed_step(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    output_format: FormatSelector,
    print_payload: PrintPayload,
) -> int | None:
    """Handle ``loopx turn managed-step`` and return its exit code."""

    if args.turn_command != "managed-step":
        return None
    try:
        runtime_root = resolve_status_projection_cache_runtime_root(
            registry_path=registry_path,
            runtime_root_override=runtime_root_arg,
        )
        journal = _load_journal(
            runtime_root,
            goal_id=args.goal_id,
            turn_key=args.turn_key,
        )
        fresh_decision = build_fresh_envelope_for_managed_step(
            args,
            registry_path=registry_path,
            runtime_root=runtime_root,
            runtime_root_arg=runtime_root_arg,
        )
        payload: dict[str, object] = {
            "ok": True,
            **decide_managed_step(
                journal,
                fresh_decision,
                goal_id=args.goal_id,
                agent_id=args.agent_id,
                turn_key=args.turn_key,
                observed_attempt=args.observed_attempt,
                observed_max_attempts=args.observed_max_attempts,
            ),
        }
    except Exception as exc:  # noqa: BLE001 - typed CLI failure boundary
        payload = {
            "ok": False,
            "schema_version": LOOPX_TURN_MANAGED_STEP_SCHEMA_VERSION,
            "error": str(exc),
        }
    print_payload(
        payload,
        output_format(args),
        render_loopx_turn_managed_step_markdown,
    )
    return 0 if payload.get("ok") else 1
