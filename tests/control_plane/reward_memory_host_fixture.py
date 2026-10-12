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


def enable_live_memory(registry: Path, *, recall: bool = True, ingest: bool = True) -> Path:
    # Synthetic pre-verified binding in a disposable project. Runtime resolution
    # still checks the exact digest, actor scope and receipt; no live promotion.
    from tests.capabilities.test_agent_turn_recall import raw_config

    payload = json.loads(registry.read_text())
    goal = payload["goals"][0]
    agent = goal["coordination"]["registered_agents"][0]
    config = raw_config(peer_ref=f"agent:{agent}", goal_id=goal["id"], agent_id=agent)
    config["automation"].update(automatic_recall=recall, automatic_ingest=ingest)
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
