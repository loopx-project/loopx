"""Local Agent CLI composition of canonical work and existing Goal Channel delivery.

No IM principal, recalled text, or displayed card can supply an Agent identity.
Python owns provider IO; the typed Todo owner owns every state transition.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import ExitStack
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_from_registry
from ...control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable, claim_canonical_todo_if_promoted,
    read_canonical_todos_if_promoted,
)
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.runtime.public_safety import validate_public_safe_value
from ...control_plane.runtime.time import now_local_iso
from ...file_lock import exclusive_file_lock
from ..runtime import default_extension_state_file, resolve_extension_activation
from . import LARK_EXTENSION_ID, LARK_GOAL_CHANNEL_PERMISSION
from .goal_channel_contracts import binding_for_goal, read_goal_channel_binding
from .goal_channel_delivery_contract import goal_channel_delivery_route
from .goal_channel_message_delivery import (
    GoalChannelDeliveryStageError, GoalChannelMessageDeliverySession, resolve_bound_goal_channel,
)
from .presentation.kanban import CommandRunner, default_subprocess_runner

GOAL_CHANNEL_WORK_RESULT_SCHEMA = "loopx_goal_channel_work_result_v0"


def _resolve_binding(binding_path: Path, target_path: Path, goal_id: str, actor: str) -> dict[str, Any]:
    # The delivery helper supports default routes; this work facade requires an
    # exact Agent route and must never fall back to a sibling or default lane.
    raw = binding_for_goal(read_goal_channel_binding(binding_path), goal_id, agent_id=actor)
    if raw is None or raw.get("agent_id") != actor or raw.get("enabled") is not True:
        raise ValueError("exact enabled Agent channel required")
    resolved = resolve_bound_goal_channel(binding_path=binding_path, target_path=target_path,
        goal_id=goal_id, agent_id=actor)
    goal_channel_delivery_route(goal_id, lambda _: resolved)
    return resolved


def build_work_card(packet: dict[str, Any]) -> dict[str, Any]:
    """Only content-minimal, validated projection/receipt fields reach the room."""
    validate_public_safe_value(packet)
    projection = packet.get("projection") or {}
    receipt = packet.get("receipt")
    selected = projection.get("selected_todo") or {}
    counts = projection.get("counts") or {}
    body = (f"Goal: {packet['goal_id']}\nAgent: {packet['actor_id']}\n"
        f"Available tasks: {counts.get('unclaimed', 0)} · User gates: {counts.get('user_gates', 0)}\n"
        f"Selected task: {selected.get('todo_id') or 'none'}\n"
        f"Revision: {projection.get('source_revision') or 'unavailable'}")
    if receipt:
        # Replays publish the same semantic acceptance card and provider key.
        body += f"\nHistorical claim receipt: {receipt['acceptance']} · Task: {receipt['todo_id']}\nCurrent owner: {receipt.get('current_owner') or 'none'}\nReceipt: {receipt['receipt_id']}"
    body += "\nRead-only orientation. Claim through the scoped Agent CLI; recheck current authority after reconnect."
    return {"config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": "LoopX work"}},
        "elements": [{"tag": "div", "text": {"tag": "plain_text", "content": body}}]}


def run_goal_channel_work(*, registry_path: Path, runtime_root: Path, binding_path: Path,
    target_path: Path, goal_id: str, actor_id: str, command: str, execute: bool,
    todo_id: str | None = None, expected_revision: str | None = None,
    idempotency_key: str | None = None, runner: CommandRunner = default_subprocess_runner,
    publish_receipt: bool = True) -> dict[str, Any]:
    packet: dict[str, Any] = {"schema_version": GOAL_CHANNEL_WORK_RESULT_SCHEMA, "ok": False, "goal_id": goal_id,
        "actor_id": actor_id, "operation": f"work_{command}", "execute": execute,
        "status": "failed", "external_write_performed": False, "readback_verified": False,
        "canonical_claim_accepted": False, "execution_authority_granted": False, "public_summary": "Room work is unavailable."}
    stage = "scope"
    try:
        validate_public_safe_value({"goal_id": goal_id, "actor_id": actor_id})
        if command not in {"project", "claim"}:
            raise ValueError("unsupported work command")
        if command == "claim" and (not todo_id or not expected_revision or not idempotency_key):
            raise ValueError("claim identity is incomplete")
        registered = registered_agent_ids_from_registry(registry_path, goal_id)
        if actor_id not in registered:
            raise ValueError("actor is not registered")
        def current_binding() -> dict[str, Any]:
            if actor_id not in registered_agent_ids_from_registry(registry_path, goal_id):
                raise ValueError("actor scope was revoked")
            if execute:
                resolve_extension_activation(LARK_EXTENSION_ID,
                    state_file=default_extension_state_file(runtime_root),
                    required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,))
            return _resolve_binding(binding_path, target_path, goal_id, actor_id)

        binding = current_binding()
        session = GoalChannelMessageDeliverySession(goal_id=goal_id, binding=binding,
            binding_lock_path=binding_path, target_lock_path=target_path,
            history_start_at=str(binding.get("created_at") or "1970-01-01T00:00:00Z"),
            resolve_current_binding=current_binding,
            runner=runner)
        route = goal_channel_delivery_route(goal_id, session.resolve)
        if execute:
            stage = "delivery_preflight"
            activation = resolve_extension_activation(LARK_EXTENSION_ID,
                state_file=default_extension_state_file(runtime_root),
                required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,))
            if activation.get("enabled") is not True or not session.verify(route):
                raise ValueError("delivery is unavailable")
        if command == "claim":
            stage = "claim"
            # Binding revocation and re-targeting serialize with the canonical
            # write. Registry identity is separately witnessed by the Todo owner.
            with ExitStack() as locks:
                locks.enter_context(exclusive_file_lock(binding_path, operation="room_work_claim"))
                if target_path != binding_path:
                    locks.enter_context(exclusive_file_lock(target_path, operation="room_work_claim"))
                if current_binding() != binding:
                    raise ValueError("binding changed")
                result = claim_canonical_todo_if_promoted(registry_path=registry_path,
                    runtime_root=runtime_root, goal_id=goal_id, todo_id=str(todo_id), role="agent",
                    claimed_by=actor_id, actor_agent_id=actor_id, dry_run=not execute,
                    operation_id=str(idempotency_key), expected_provider_revision=str(expected_revision))
            if result is None:
                raise ValueError("canonical promotion required")
            accepted = result.get("status") in {"applied", "replayed", "recovered", "no_change"}
            packet["canonical_claim_accepted"] = accepted
            packet["status"] = ("already_applied" if result.get("status") in {"replayed", "recovered"}
                else "applied" if accepted else "planned")
            # Receipts never copy arbitrary provider payloads, original requests,
            # lease secrets, validation paths or exception text to the room.
            packet["receipt"] = {"acceptance": "accepted" if accepted else "planned",
                "todo_id": str(todo_id), "actor_id": actor_id, "scope": "historical_acceptance_only",
                "receipt_id": "sha256:" + hashlib.sha256(str(idempotency_key).encode()).hexdigest()}
        stage = "projection"
        snapshot = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id=goal_id)
        if snapshot is None:
            raise ValueError("canonical promotion required")
        packet["projection"] = effect_runtime_result("goal_channel.work.project", {
            "goal_id": goal_id, "actor_id": actor_id, "registered_agents": registered,
            "snapshot": snapshot, "observed_at": now_local_iso()})
        if command == "claim":
            current: dict[str, Any] = next((todo for todo in snapshot["todos"] if todo["todo_id"] == todo_id), {})
            packet["receipt"]["current_owner"] = current.get("claimed_by")
            packet["receipt"]["current_claim_matches_actor"] = current.get("claimed_by") == actor_id
        if command == "project":
            packet["status"] = "projected"
        packet["public_summary"] = "Canonical room work readback is ready."
        validate_public_safe_value(packet)
        if execute and publish_receipt:
            stage = "delivery"
            # Stable card identity permits lost-response recovery independently
            # of the accepted state transition. No new claim is minted here.
            card = build_work_card(packet)
            key = "room-work:" + hashlib.sha256(json.dumps(card, sort_keys=True).encode()).hexdigest()
            sent = session.send(card, key, route)
            packet["external_write_performed"] = sent.get("external_write_performed") is True
            readback = session.readback(str(sent["message_id"]))
            packet["readback_verified"] = readback.get("verified") is True
            if not packet["readback_verified"]:
                raise ValueError("delivery readback failed")
        packet["ok"] = True
        return packet
    except LocalCoordinationAuthorityUnavailable as exc:
        packet["status"] = "conflict" if exc.code == "provider_revision_mismatch" else "rejected" if exc.payload.get("failure_kind") == "decision_rejection" else "failed"
        packet["blocker"] = packet["status"]
    except GoalChannelDeliveryStageError as exc:
        packet["external_write_performed"] = exc.external_write_performed is not False
        packet["blocker"] = exc.blocker
    except Exception:
        packet["blocker"] = f"{stage}_unavailable"
    packet["failure_stage"] = stage
    packet["public_summary"] = ("Claim accepted; room delivery/readback needs recovery using the same key."
        if packet["canonical_claim_accepted"] else "No claim acceptance is reported; refresh scope and canonical state.")
    try:
        validate_public_safe_value(packet)
    except ValueError:
        return {"schema_version": GOAL_CHANNEL_WORK_RESULT_SCHEMA, "ok": False, "status": "failed",
            "blocker": "public_projection_invalid", "canonical_claim_accepted": packet["canonical_claim_accepted"],
            "external_write_performed": packet["external_write_performed"], "readback_verified": False}
    return packet
