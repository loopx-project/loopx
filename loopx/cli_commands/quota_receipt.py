"""Read the original persisted guard without collecting live quota or writing."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping
import hashlib
import json
from pathlib import Path

from ..control_plane.quota.heartbeat_receipt import (
    find_heartbeat_receipt,
    heartbeat_receipt_view,
)
from ..control_plane.quota.settlement import QuotaSettlementReadback, read_heartbeat_settlement
from ..history import load_registry
from ..paths import resolve_runtime_root, validate_goal_id_path_segment
from ..registry import find_registry_goal
from ..rollout_event_log import rollout_event_log_path
from ..turn_identity import normalize_turn_instance_id


def _clean_text_list(value: object, *, limit: int) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if not isinstance(item, str):
            continue
        trimmed = item.strip()
        if not trimmed or trimmed in seen:
            continue
        seen.add(trimmed)
        out.append(trimmed[:512])
        if len(out) >= limit:
            break
    return out


def _verified_settlement_projection(
    *,
    runtime_root: Path,
    registry_path: Path,
    goal_id: str,
    agent_id: str,
    turn_id: str,
    heartbeat_receipt: Mapping[str, object] | None,
    goal_ref: Mapping[str, object] | None,
) -> dict[str, object]:
    identity = (
        heartbeat_receipt.get("settlement_identity")
        if isinstance(heartbeat_receipt, Mapping)
        else None
    )
    bound_todo_id = (
        str(identity.get("todo_id") or "").strip()
        if isinstance(identity, Mapping)
        else ""
    )
    settlement: QuotaSettlementReadback | None = read_heartbeat_settlement(
        runtime_root,
        goal_id=goal_id,
        agent_id=agent_id,
        todo_id=bound_todo_id or None,
        turn_instance_id=turn_id,
        infer_turn_instance_id=False,
        resolve_original_binding=True,
        registry_path=registry_path,
        goal_ref=goal_ref,
    )
    if settlement is None:
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "settlement_readback_unavailable",
            "completion_receipt": None,
        }
    if settlement.successor_replan_pending or settlement.replan_execution_pending:
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "replan_conflict",
            "completion_receipt": None,
        }
    if (
        isinstance(settlement.refresh_recovery, Mapping)
        and settlement.refresh_recovery.get("decision") == "reject"
    ):
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "recovery_rejected",
            "completion_receipt": None,
        }
    if (settlement.progress or {}).get("checkpoint_recovery_required") is True:
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "checkpoint_recovery_required",
            "completion_receipt": None,
        }
    completion_ev = settlement.completion_event
    if not isinstance(completion_ev, Mapping):
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "missing_completion_event",
            "completion_receipt": None,
        }
    replay_phase = settlement.replay_phase.value if settlement.replay_phase else None
    settlement_ok = (
        settlement.settlement.failure is None
        and settlement.settlement.value is not None
    ) or replay_phase == "settled"
    writeback_ok = settlement.writeback_event is not None or (
        settlement.writeback.failure is None and settlement.writeback.value is not None
    )
    spend_ok = settlement.spend_event is not None or (
        settlement.spend.failure is None and settlement.spend.value is not None
    )
    if not (settlement_ok and writeback_ok and spend_ok):
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "unsettled_turn",
            "completion_receipt": None,
        }
    ev_todo_id = str(completion_ev.get("todo_id") or bound_todo_id).strip()
    if not ev_todo_id or (bound_todo_id and ev_todo_id != bound_todo_id):
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "todo_id_mismatch",
            "completion_receipt": None,
        }
    receipt_id = str(completion_ev.get("event_id") or "").strip() or turn_id
    log_path = rollout_event_log_path(runtime_root, goal_id)
    if not log_path.is_file():
        return {
            "settlement_verified": False,
            "settlement_conflict_reason": "missing_source_ref",
            "completion_receipt": None,
        }
    source_sha = hashlib.sha256(log_path.read_bytes()).hexdigest()
    raw_details = completion_ev.get("details")
    details: Mapping[str, object] = raw_details if isinstance(raw_details, Mapping) else {}
    failure_facts = _clean_text_list(details.get("failure_facts"), limit=32)
    open_obligations = _clean_text_list(details.get("open_obligations"), limit=64)
    completion_digest = hashlib.sha256(
        json.dumps(dict(completion_ev), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return {
        "settlement_verified": True,
        "settlement_conflict_reason": None,
        "completion_receipt": {
            "todo_id": ev_todo_id,
            "completion_receipt_id": receipt_id,
            "exit_code": 0,
            "sha256": completion_digest,
            "failure_facts": failure_facts,
            "open_obligations": open_obligations,
            "source_ref": {"path": str(log_path), "sha256": source_sha},
        },
    }


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
        goal_ref = goal_ref_resolver()
        event = find_heartbeat_receipt(
            runtime_root, goal_id=goal_id, agent_id=args.agent_id, turn_instance_id=turn_id,
            goal_ref=goal_ref,
        )
        receipt_view = (
            heartbeat_receipt_view(event, turn_instance_id=turn_id, status="committed")
            if event is not None else None
        )
        payload.update(
            ok=True, goal_id=goal_id, agent_id=args.agent_id, turn_instance_id=turn_id,
            heartbeat_receipt=receipt_view,
        )
        if getattr(args, "include_settlement", False):
            payload.update(
                _verified_settlement_projection(
                    runtime_root=runtime_root,
                    registry_path=registry_path,
                    goal_id=goal_id,
                    agent_id=args.agent_id,
                    turn_id=turn_id,
                    heartbeat_receipt=receipt_view,
                    goal_ref=goal_ref,
                )
            )
    except (OSError, RuntimeError, ValueError) as exc:
        payload["error"] = str(exc)
    return payload


def render_quota_receipt(payload: dict[str, object]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)
