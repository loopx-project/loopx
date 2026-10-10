"""Private reconnect context through existing recall, quota and channel owners."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ...agent_registry import registered_agent_ids_from_registry
from ...capabilities.agent_turn_recall.runtime import run_configured_agent_turn_recall_fail_open
from ...capabilities.context_providers.base import ContextProvider
from ...capabilities.reward_memory.experiment import resolve_reward_memory_experiment, resolve_reward_memory_surface_config
from ...control_plane.collaboration.goal_instance_scope import collaboration_goal_scope
from ...control_plane.effect_runtime import effect_runtime_result
from ...control_plane.runtime.runtime_projection_route import resolve_goal_source_runtime_route
from ..runtime import default_extension_state_file, resolve_extension_activation
from . import LARK_EXTENSION_ID, LARK_GOAL_CHANNEL_PERMISSION
from .goal_channel_delivery_contract import goal_channel_binding_digest, goal_channel_delivery_route
from .goal_channel_message_delivery import GoalChannelMessageDeliverySession
from .goal_channel_work import _resolve_binding, run_goal_channel_work
from .presentation.kanban import CommandRunner, default_subprocess_runner

ROOM_RESUME_RESULT_SCHEMA = "loopx_goal_channel_resume_v0"


def run_room_resume(*, registry_path: Path, authority_root: Path, broker_root: Path,
    binding_path: Path, target_path: Path, goal_id: str, actor_id: str,
    quota_decision: Mapping[str, Any], turn_instance_id: str, execute: bool,
    read_current_quota: Callable[[], dict[str, Any]], artifact_refs: list[str] | None = None,
    runner: CommandRunner = default_subprocess_runner, provider: ContextProvider | None = None) -> dict[str, Any]:
    packet: dict[str, Any] = {"schema_version": ROOM_RESUME_RESULT_SCHEMA, "ok": False,
        "goal_id": goal_id, "actor_id": actor_id, "turn_instance_id": turn_instance_id,
        "status": "rejected", "visibility": "private", "private_context": None,
        "artifact_references": [], "provider_call_count": 0, "room_delivery_performed": False,
        "external_writes_performed": False, "quota_spend_performed": False,
        "execution_authority_granted": False}
    refs = artifact_refs or []

    def scope() -> tuple[dict[str, Any], str, list[str]]:
        route = resolve_goal_source_runtime_route(registry_path=broker_root / "registry.global.json", goal_id=goal_id)
        if (Path(route["source_registry"]).resolve() != registry_path.resolve() or
            Path(route["source_runtime_root"]).resolve() != authority_root.resolve()):
            raise ValueError("source route changed")
        with collaboration_goal_scope(registry_path, goal_id=goal_id, agents=(actor_id,), require_active=True) as current:
            if current.exact:
                raise ValueError("exact instance resume unqualified")
        if actor_id not in registered_agent_ids_from_registry(registry_path, goal_id):
            raise ValueError("actor scope revoked")
        binding = _resolve_binding(binding_path, target_path, goal_id, actor_id)
        status, config = resolve_reward_memory_experiment(registry_path=registry_path, goal_id=goal_id, agent_id=actor_id)
        digest = hashlib.sha256(json.dumps([goal_channel_binding_digest(binding), status, config],
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        roots = [str(item["provider_binding"]["scope_ref"]) for item in
            resolve_reward_memory_surface_config(config, "agent_workflow.turn_admission")["recall_corpora"]] if config else []
        if execute:
            resolve_extension_activation(LARK_EXTENSION_ID, state_file=default_extension_state_file(authority_root),
                required_permissions=(LARK_GOAL_CHANNEL_PERMISSION,))
            session = GoalChannelMessageDeliverySession(goal_id=goal_id, binding=binding,
                binding_lock_path=binding_path, target_lock_path=target_path,
                history_start_at="1970-01-01T00:00:00Z",
                resolve_current_binding=lambda: _resolve_binding(binding_path, target_path, goal_id, actor_id), runner=runner)
            if not session.verify(goal_channel_delivery_route(goal_id, session.resolve)):
                raise ValueError("provider identity unavailable")
        return binding, digest, roots

    def projection() -> dict[str, Any]:
        observed = run_goal_channel_work(registry_path=registry_path, runtime_root=authority_root,
            binding_path=binding_path, target_path=target_path, goal_id=goal_id,
            actor_id=actor_id, command="project", execute=False, runner=runner)
        if observed.get("ok") is not True:
            raise ValueError("canonical projection unavailable")
        return dict(observed["projection"])

    try:
        _, before_scope, artifact_scopes = scope()
        before_projection = projection()
        before_quota = read_current_quota()
        packet.update(current_quota=before_quota, work_projection=before_projection)
        checked = effect_runtime_result("goal_channel.work.resume_input", {"goal_id": goal_id, "actor_id": actor_id,
            "turn_instance_id": turn_instance_id, "quota": dict(quota_decision), "current_quota": before_quota,
            "artifact_refs": refs})
        if not checked["matches"]:
            return {**packet, "blocker": checked["reason_code"]}
        if not execute:
            return {**packet, "ok": True, "status": "planned", "requested_reference_count": len(refs)}
        recalled = run_configured_agent_turn_recall_fail_open(registry_path=registry_path,
            goal_id=goal_id, agent_id=actor_id, quota_decision=before_quota, turn_instance_id=turn_instance_id,
            execute=True, force_refresh=True, reconcile_pending_ingests=False, provider=provider)
        packet["provider_call_count"] = recalled.get("provider_call_count", 0)
        packet["context_status"] = recalled.get("status")
        _, after_scope, _ = scope()
        after_projection = projection()
        after_quota = read_current_quota()
        packet.update(current_quota=after_quota, work_projection=after_projection)
        readback = effect_runtime_result("goal_channel.work.resume_readback", {"goal_id": goal_id, "actor_id": actor_id,
            "before_scope": before_scope, "after_scope": after_scope, "before_projection": before_projection,
            "after_projection": after_projection, "before_quota": before_quota, "after_quota": after_quota,
            "recall": recalled, "artifact_refs": refs, "artifact_scope_refs": artifact_scopes})
        packet.update(readback=readback, artifact_references=readback["artifact_references"])
        if not readback["authority_observation_stable"]:
            return {**packet, "status": "authority_changed", "blocker": "authority_observation_changed"}
        packet.update(ok=True, status="restored" if readback["context_usable"] else "context_unavailable",
            private_context=recalled.get("context") if readback["context_usable"] else None)
        return packet
    except (OSError, RuntimeError, TypeError, ValueError):
        # Scope/readback failure invalidates every earlier observation, including
        # the private quota packet. Do not label a pre-retrieval read "current".
        packet.pop("current_quota", None)
        packet.pop("work_projection", None)
        packet.pop("readback", None)
        return {**packet, "ok": False, "status": "rejected", "private_context": None,
            "artifact_references": [], "blocker": "resume_scope_unavailable"}
