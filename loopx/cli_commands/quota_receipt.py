"""Read the original persisted guard without collecting live quota or writing."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import json
from pathlib import Path

from ..control_plane.quota.heartbeat_receipt import find_heartbeat_receipt, heartbeat_receipt_view
from ..history import load_registry
from ..paths import resolve_runtime_root, validate_goal_id_path_segment
from ..registry import find_registry_goal
from ..turn_identity import normalize_turn_instance_id


def read_quota_receipt(
    args: argparse.Namespace,
    *,
    registry_path: Path,
    runtime_root_arg: str | None,
    goal_ref_resolver: Callable[[], Mapping[str, object] | None],
) -> dict[str, object]:
    payload: dict[str, object] = {"ok": False, "schema_version": "quota_receipt_readback_v0"}
    try:
        if not (args.goal_id and args.agent_id and args.turn_instance_id):
            raise ValueError("quota receipt requires --goal-id, --agent-id and --turn-instance-id")
        goal_id = validate_goal_id_path_segment(args.goal_id)
        turn_id = normalize_turn_instance_id(args.turn_instance_id)
        assert turn_id is not None
        registry = load_registry(registry_path)
        if find_registry_goal(registry, goal_id) is None:
            raise ValueError("quota receipt requires a registered Goal")
        runtime_root = resolve_runtime_root(registry, runtime_root_arg, registry_path=registry_path)
        event = find_heartbeat_receipt(
            runtime_root, goal_id=goal_id, agent_id=args.agent_id, turn_instance_id=turn_id,
            goal_ref=goal_ref_resolver(),
        )
        payload.update(
            ok=True, goal_id=goal_id, agent_id=args.agent_id, turn_instance_id=turn_id,
            heartbeat_receipt=(
                heartbeat_receipt_view(event, turn_instance_id=turn_id, status="committed")
                if event is not None else None
            ),
        )
    except (OSError, RuntimeError, ValueError) as exc:
        payload["error"] = str(exc)
    return payload


def render_quota_receipt(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
