"""Compose an explicitly requested refresh/spend through the existing CLI owner.

This adapter has no admission or accounting policy. Only a verified refresh's
canonical settlement_owed command may execute, with its original identity.
"""
from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ..control_plane.quota.settlement import (
    SettlementIdentity, attach_settlement_progress, read_heartbeat_settlement, settlement_result_payload,
)


def settle_refreshed_turn(
    payload: dict[str, Any], *, registry_path: Path, runtime_root: Path,
    goal_id: str, agent_id: str, todo_id: str | None,
    replan_obligation_id: str | None, turn_instance_id: str,
    goal_ref: Mapping[str, str] | None,
    delivery_workspace_path: Path | None = None,
) -> None:
    """Follow one owed step, then verify its exact receipts; never collect a guard.

    A failed/ambiguous child is not retried here. The original refresh or owed
    command remains replayable under the shared exactly-once settlement owner.
    """
    if payload.get("ok") is not True or payload.get("dry_run"):
        return
    owed = payload.get("settlement_owed")
    if not isinstance(owed, dict):
        return  # Includes settled replay, missing checkpoints and no-spend closeout.
    identity = payload.get("settlement_identity")
    expected = SettlementIdentity(goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
        replan_obligation_id=replan_obligation_id, turn_instance_id=turn_instance_id).as_dict()
    if (owed.get("schema_version") != "turn_settlement_owed_v0" or owed.get("kind") != "quota_spend"
            or not isinstance(identity, dict)
            or any(identity.get(k) != v or owed.get(k) != v for k, v in expected.items() if k != "schema_version")):
        payload.update(ok=False, error="refresh settlement command does not match its original identity")
        return
    error = None
    spent: dict[str, Any] = {}
    try:
        workspace = delivery_workspace_path.expanduser().resolve() if delivery_workspace_path else None
        command = shlex.split(owed["command"])
        # Validate the owner-rendered argv through the actual CLI parser too:
        # a projector bug must not debit a different valid Turn before readback.
        from ..cli import build_parser
        args = build_parser().parse_args(["--format", "json", *command[1:]])
        if (args.command != "quota" or args.quota_command != "spend-slot"
                or not args.execute or args.slots != 1
                or Path(args.registry).expanduser().resolve() != registry_path.resolve()
                or Path(args.runtime_root).expanduser().resolve() != runtime_root.resolve()
                or args.source != payload["settlement_progress"]["quota_spend_source"]
                or getattr(args, "goal_instance_id", None) != (goal_ref or {}).get("goal_instance_id")
                or any(getattr(args, key, None) != value for key, value in (
                    ("goal_id", goal_id), ("agent_id", agent_id), ("todo_id", todo_id),
                    ("replan_obligation_id", replan_obligation_id), ("turn_instance_id", turn_instance_id)))):
            raise ValueError("owed quota command does not preserve the exact original binding")
        # Keep the validated producing cwd for accounting, but pin imports to
        # this CLI's package: the delivered worktree may contain another LoopX.
        # -P excludes the child cwd from Python's initial module search path.
        # Retain the complete CLI guards, hooks and rollout receipts; neither
        # ambient PATH nor a lower-level debit may replace that adapter.
        entry = (
            "import sys; sys.path.insert(0, sys.argv.pop(1)); "
            "import runpy; runpy.run_module('loopx.cli', run_name='__main__')"
        )
        result = subprocess.run(
            [sys.executable, "-P", "-c", entry, str(Path(__file__).resolve().parents[2]),
             "--format", "json", *command[1:]],
            cwd=workspace,
            capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
        )
        value = json.loads(result.stdout)
        if not isinstance(value, dict):
            raise ValueError("quota settlement CLI returned a non-object result")
        spent = value
        if result.returncode or spent.get("ok") is not True:
            error = str(spent.get("error") or spent.get("reason") or "quota settlement CLI failed")
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired, SystemExit) as exc:
        error = str(exc)
    try:
        readback = read_heartbeat_settlement(
            runtime_root, registry_path=registry_path, goal_ref=goal_ref,
            goal_id=goal_id, agent_id=agent_id, todo_id=todo_id,
            replan_obligation_id=replan_obligation_id, turn_instance_id=turn_instance_id,
        )
    except Exception as exc:
        payload["ok"] = False
        payload["error"] = f"Writeback retained; exact quota settlement readback failed: {exc}. Retry the original identity."
        return
    if readback is None:
        payload["ok"] = False
        payload["error"] = error or "exact settlement readback unavailable; retry the original identity"
        return
    attach_settlement_progress(payload, readback, registry_path=registry_path,
        runtime_root=runtime_root, goal_ref=goal_ref)
    payload["quota_spend"] = {key: spent[key] for key in (
        "ok", "appended", "idempotent_replay", "json_path", "rollout_event",
        "reward_memory_ingest", "error", "error_code",
    ) if key in spent}
    payload["settlement_result"] = settlement_result_payload(readback.settlement)
    if readback.settlement.failure is not None or spent and spent.get("ok") is not True:
        payload["ok"] = False
        payload["error"] = error or readback.settlement.failure.reason
    elif error:
        # Receipt readback, not child transport success, proves commitment.
        payload["quota_spend"]["transport_warning"] = error
