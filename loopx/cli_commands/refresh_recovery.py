"""Keep a rejected write or maintenance refresh attached to its owed Turn.

The shared TS closeout reader selects persisted debt; this adapter only exposes
its exact readback and CLI continuation. It never admits or settles a Turn.
"""
from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..control_plane.quota.settlement import attach_settlement_progress, read_heartbeat_settlement
from ..command_invocation import loopx_cli_argv
from ..install_contract import render_host_cli_command
from ..history import load_registry
from ..paths import resolve_runtime_root


def attach_refresh_settlement_recovery(
    payload: dict[str, Any], args: argparse.Namespace, registry_path: Path,
    goal_ref: Mapping[str, Any] | None,
) -> None:
    """Expose existing debt without turning a state update into a write receipt."""
    if (args.dry_run or not args.agent_id or isinstance(payload.get("settlement_progress"), dict)
            or "settlement_recovery" in payload or "settlement_recovery_error" in payload):
        return
    try:
        runtime_root = resolve_runtime_root(load_registry(registry_path), args.runtime_root,
            registry_path=registry_path)
        # TS selects persisted debt and validates every caller-supplied binding
        # against one immutable snapshot; Python supplies no latest-Run guess.
        readback = read_heartbeat_settlement(runtime_root, goal_id=args.goal_id,
            agent_id=args.agent_id, todo_id=args.todo_id,
            replan_obligation_id=args.replan_obligation_id, turn_instance_id=args.turn_instance_id,
            pending_closeout=True, include_record_bodies=False,
            registry_path=registry_path, goal_ref=goal_ref)
        if readback is None:
            return
        if readback.identity.failure is not None:
            raise ValueError(readback.identity.failure.reason)
        identity = readback.identity.value
        if identity is None:
            raise RuntimeError("original Turn settlement identity is unavailable")
        recovery: dict[str, Any] = {"ok": True}
        attach_settlement_progress(recovery, readback, registry_path=registry_path,
            runtime_root=runtime_root, goal_ref=goal_ref)
        recovery.pop("ok")
        owed = recovery.get("settlement_owed")
        # Select the complete CLI consumer explicitly: an unprofiled guard can
        # return legacy unbound refresh templates. Replan guards recover their
        # obligation from the Turn receipt (that flag is spend-only on quota).
        command = render_host_cli_command([
            *loopx_cli_argv(), "--registry", str(registry_path), "--runtime-root", str(runtime_root),
            "--format", "json", "quota", "should-run",
            "--complete-turn", "--runtime-profile", "generic_cli",
            "--goal-id", identity.goal_id, "--agent-id", identity.agent_id,
            "--turn-instance-id", identity.turn_instance_id,
            *(["--todo-id", identity.todo_id] if identity.todo_id else []),
            *(["--goal-instance-id", str(goal_ref["goal_instance_id"])] if goal_ref is not None else []),
            *[word for capability in args.available_capabilities or [] for word in ("--available-capability", capability)],
        ])
        recovery.update(required=True, settlement_identity=identity.as_dict(),
            next_cli_actions=[owed["command"] if isinstance(owed, dict) else command],
            instruction="This response does not settle the original Turn. Follow its recovery with the same identity; "
                        "state-only updates and installation maintenance cannot replace accountable writeback.")
        payload["settlement_recovery"] = recovery
    except (OSError, RuntimeError, ValueError) as error:
        # Preserve the original write result, including an already committed
        # state update. An unavailable read is not evidence of no pending debt.
        payload["settlement_recovery_error"] = str(error)
