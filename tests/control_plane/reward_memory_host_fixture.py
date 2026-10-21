"""Disposable verified memory bindings for host contract tests."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

def enable_plan_memory(plan: dict) -> None:
    """Explicit verified participation for pre-existing host wiring fixtures."""
    envelope = plan["turn_envelope"]
    plan["reward_memory_recall"] = {"experiment": {
        "goal_id": envelope["goal_id"], "agent_id": envelope["agent_id"],
        "enabled": True, "available": True, "configured_for_agent": True,
        "automatic_recall": True, "automatic_ingest": True,
    }}
    envelope.setdefault("boundary", {})["capabilities"] = {"reward_memory": {
        "automatic_recall": True, "automatic_ingest": True,
    }}
    register_plan_memory(plan)


def register_plan_memory(plan: dict) -> None:
    """Exercise the actual registered context dispatcher over an explicit fixture."""
    from loopx.control_plane.capability_hooks import TurnContextHookRegistration
    from loopx.control_plane.turn_driver.capability_context import dispatch_turn_context_hooks
    from loopx.capabilities.reward_memory.turn_hooks import memory_context_result

    recall = plan.pop("reward_memory_recall")
    plan.pop("turn_context_contributions", None)
    result = memory_context_result(plan, recall)
    dispatch_turn_context_hooks(plan, (TurnContextHookRegistration(
        "reward-memory.turn-context", "reward-memory", ("reward_memory_recall",), lambda: result,
    ),))


def enable_live_memory(registry: Path, *, recall: bool = True, ingest: bool = True, validated_ingest: bool = False) -> Path:
    # Synthetic pre-verified binding in a disposable project. Runtime resolution
    # still checks the exact digest, actor scope and receipt; no live promotion.
    from tests.capabilities.test_agent_turn_recall import raw_config

    payload = json.loads(registry.read_text())
    goal = payload["goals"][0]
    agent = goal["coordination"]["registered_agents"][0]
    config = raw_config(peer_ref=f"agent:{agent}", goal_id=goal["id"], agent_id=agent)
    config["automation"].update(automatic_recall=recall, automatic_ingest=ingest)
    if validated_ingest:
        entry = config["corpora"][0]
        entry["standing_policy"]["allowed_actor_roles"] = ["validated_goal_agent"]
        entry["standing_policy"]["allowed_source_kinds"] = ["engineering_review"]
        entry["corpus"]["maintenance"]["writeback_triggers"] = ["engineering_review"]
    config["project_provider_binding"]["provider_binary"] = "fixture-unavailable-memory-provider"
    path = Path(goal["repo"]) / ".loopx/config/reward-memory/fixture.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config), encoding="utf-8")
    digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    goal["control_plane"] = {"reward_memory": {
        "enabled": True, "experimental": True, "enabled_agents": [agent],
        "config_path": str(path.relative_to(goal["repo"])), "config_digest": digest,
        "enablement_receipts": {agent: {
            "schema_version": "reward_memory_enablement_receipt_v0", "status": "verified",
            "goal_id": goal["id"], "agent_id": agent, "config_digest": digest,
            "provider_id": "openviking", "isolation_mode": "goal_scoped_agent_private",
            "actor_binding_verified": True, "writability_verified": True,
            "exact_readback_verified": True,
        }},
    }}
    registry.write_text(json.dumps(payload), encoding="utf-8")
    return path
