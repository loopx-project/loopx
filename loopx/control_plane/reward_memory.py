from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .todos.contract import normalize_todo_claimed_by


def reward_memory_automation_enabled(
    payload: Mapping[str, Any] | None,
    *,
    operation: str,
) -> bool:
    """Project verified capability automation into the host IO contract.

    The experiment resolver owns enablement, config/receipt verification and
    provider policy. A recall packet's presence or old context is not opt-in.
    """
    if not isinstance(payload, Mapping):
        return False
    recall = payload.get("reward_memory_recall")
    if not isinstance(recall, Mapping):
        return False
    experiment = recall.get("experiment")
    envelope = payload.get("turn_envelope")
    if (
        not isinstance(experiment, Mapping)
        or experiment.get("enabled") is not True
        or experiment.get("available") is not True
        or experiment.get("configured_for_agent") is not True
        or experiment.get(operation) is not True
    ):
        return False
    if isinstance(envelope, Mapping):
        boundary = envelope.get("boundary")
    elif "turn_envelope" not in payload:
        # App guards carry the same verified capability projection directly;
        # they are not host requests and do not contain a TurnEnvelope.
        receipt = payload.get("heartbeat_receipt")
        identity = payload.get("agent_identity")
        if (
            payload.get("ok") is not True
            or payload.get("should_run") is not True
            or not isinstance(receipt, Mapping)
            or receipt.get("status") not in {"committed", "replayed"}
            or not receipt.get("turn_instance_id")
            or not isinstance(identity, Mapping)
            or identity.get("registered") is not True
        ):
            return False
        # Reuse the typed quota projection rather than interpreting another
        # copy of the legacy quota boundary in this compatibility adapter.
        from .quota.turn_envelope import quota_action_signature_document

        boundary = quota_action_signature_document(payload).get("boundary")
    else:
        return False
    try:
        from .capability_hooks import turn_context_source

        source = turn_context_source(payload)
    except ValueError:
        return False
    capabilities = (
        boundary.get("capabilities") if isinstance(boundary, Mapping) else None
    )
    memory = (
        capabilities.get("reward_memory") if isinstance(capabilities, Mapping) else None
    )
    return (
        isinstance(memory, Mapping)
        and memory.get(operation) is True
        # Recall resolves the binding again after admission. A stale envelope
        # must not resurrect a now unavailable or disabled binding.
        and bool(source.get("goal_id"))
        and experiment.get("goal_id") == source.get("goal_id")
        and bool(source.get("agent_id"))
        and experiment.get("agent_id") == source.get("agent_id")
    )


def reward_memory_host_coverage() -> list[dict[str, str]]:
    """Project source lifecycle wiring, not Agent enablement or provider qualification."""

    return [
        {
            "host_id": "codex_cli_turn",
            "automatic_recall": "connected",
            "automatic_ingest": "connected_post_settlement",
        },
        {
            "host_id": "generic_cli_turn",
            "automatic_recall": "connected",
            "automatic_ingest": "connected_post_settlement",
        },
        {
            "host_id": "dsh_turn",
            "automatic_recall": "connected",
            "automatic_ingest": "connected_post_settlement",
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
    "reward_memory_automation_enabled",
    "reward_memory_goal_policy",
    "reward_memory_host_coverage",
]
