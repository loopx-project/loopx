from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .todos.contract import normalize_todo_claimed_by


def reward_memory_host_coverage() -> list[dict[str, str]]:
    """Project actual host lifecycle wiring without inferring parity."""

    return [
        {
            "host_id": "codex_cli_turn",
            "automatic_recall": "connected",
            "automatic_ingest": "connected_post_settlement",
        },
        {
            "host_id": "generic_cli_turn",
            "automatic_recall": "connected",
            "automatic_ingest": "uncovered",
        },
        {
            "host_id": "dsh_turn",
            "automatic_recall": "connected_task_body",
            "automatic_ingest": "uncovered",
        },
        {
            "host_id": "codex_app_quota",
            "automatic_recall": "connected",
            "automatic_ingest": "connected_refresh_spend_post_settlement",
        },
        {
            "host_id": "lark",
            "automatic_recall": "status_projection_only",
            "automatic_ingest": "uncovered",
        },
    ]


def reward_memory_goal_policy(
    goal: Mapping[str, Any], *, agent_id: str | None = None
) -> dict[str, Any]:
    """Normalize the binding selected by the typed owner, without provider IO.

    The legacy/default-off path does not cross the effect bridge. Exact Agent
    overrides are independent policies; even invalid entries cannot fall back.
    """

    control_plane = (
        goal.get("control_plane")
        if isinstance(goal.get("control_plane"), Mapping)
        else {}
    )
    raw = (
        control_plane.get("reward_memory")
        if isinstance(control_plane.get("reward_memory"), Mapping)
        else {}
    )
    binding_agent_id = None
    binding_revision = None
    if agent_id is not None and "agent_bindings" in raw:
        from .effect_runtime import effect_runtime_result

        selected = effect_runtime_result("reward_memory.binding.select", {
            "policy": dict(raw), "agent_id": agent_id,
        })
        raw = selected["policy"]
        binding_agent_id = selected.get("binding_agent_id")
        binding_revision = selected.get("binding_revision")
    enabled_agents: list[str] = []
    for value in raw.get("enabled_agents") or []:
        enabled_agent_id = normalize_todo_claimed_by(value)
        if enabled_agent_id and enabled_agent_id not in enabled_agents:
            enabled_agents.append(enabled_agent_id)
    experimental = raw.get("experimental") is True
    enablement_receipts: dict[str, dict[str, Any]] = {}
    raw_receipts = raw.get("enablement_receipts")
    if isinstance(raw_receipts, Mapping):
        for agent_id in enabled_agents:
            receipt = raw_receipts.get(agent_id)
            if not isinstance(receipt, Mapping):
                continue
            enablement_receipts[agent_id] = {
                key: receipt[key]
                for key in (
                    "schema_version",
                    "status",
                    "goal_id",
                    "agent_id",
                    "config_digest",
                    "provider_id",
                    "isolation_mode",
                    "actor_binding_verified",
                    "writability_verified",
                    "exact_readback_verified",
                    "probe_count",
                    "write_count",
                    "external_writes_performed",
                    "observed_at",
                    "provider_preflight_performed",
                    "reason_codes",
                )
                if key in receipt
            }
    policy = {
        "enabled": raw.get("enabled") is True and experimental,
        "experimental": experimental,
        "config_path": str(raw.get("config_path") or "").strip(),
        "config_digest": str(raw.get("config_digest") or "").strip(),
        "enabled_agents": enabled_agents,
        "enablement_receipts": enablement_receipts,
        "automation": (
            dict(raw["automation"])
            if isinstance(raw.get("automation"), Mapping)
            else {}
        ),
        "automation_intent": (
            dict(raw["automation_intent"])
            if isinstance(raw.get("automation_intent"), Mapping)
            else {}
        ),
    }
    if binding_agent_id is not None:
        policy["binding_agent_id"] = binding_agent_id
        policy["binding_revision"] = binding_revision
    return policy


__all__ = [
    "reward_memory_goal_policy",
    "reward_memory_host_coverage",
]
