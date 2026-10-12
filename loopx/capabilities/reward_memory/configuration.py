from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from copy import deepcopy
from pathlib import Path
from typing import Any

from ...control_plane.operator_inbox_binding import (
    local_private_config_digest,
    operator_inbox_binding,
)
from ...control_plane.reward_memory import (
    reward_memory_goal_policy,
    reward_memory_host_coverage,
)
from .experiment import (
    load_reward_memory_experiment_config,
    preflight_reward_memory_experiment_config,
    resolve_goal_reward_memory_experiment,
)


def reward_memory_goal_configuration_summary(
    goal: Mapping[str, Any],
) -> dict[str, Any]:
    """Reconcile the declared policy with live, read-only runtime admission.

    The configuration catalog and the settings summary need one projection that
    separates the desired automation and historical receipts from the effective
    binding. Effective availability reuses runtime admission (same digest,
    isolation and receipt checks) so a drifted or unverified config cannot be
    reported as currently verified. No provider is contacted and nothing is
    written.
    """

    policy = reward_memory_goal_policy(goal)
    binding_revision = ""
    if policy["config_path"] and policy["config_digest"]:
        binding_revision = "sha256:" + hashlib.sha256(
            (
                f"{policy['config_path']}\0{policy['config_digest']}\0"
                + "\0".join(policy["enabled_agents"])
            ).encode("utf-8")
        ).hexdigest()
    binding = operator_inbox_binding(
        project=str(goal.get("repo") or ""),
        config_path=policy["config_path"],
        expected_digest=policy["config_digest"],
    )
    recorded_verified_agents = sorted(
        agent_id
        for agent_id, receipt in policy["enablement_receipts"].items()
        if receipt.get("status") == "verified"
        and receipt.get("writability_verified") is True
        and receipt.get("exact_readback_verified") is True
    )
    effective_verified_agents: list[str] = []
    configured_automatic_ingest = False
    configured_automatic_recall = False
    for agent_id in recorded_verified_agents:
        try:
            status, _ = resolve_goal_reward_memory_experiment(
                goal=goal, agent_id=agent_id
            )
        except ValueError:
            continue
        if status.get("available") is True:
            effective_verified_agents.append(agent_id)
            configured_automatic_ingest = (
                configured_automatic_ingest
                or status.get("automatic_ingest") is True
            )
            configured_automatic_recall = (
                configured_automatic_recall
                or status.get("automatic_recall") is True
            )
    effective_available = bool(effective_verified_agents)
    return {
        "enabled": policy["enabled"],
        "binding_status": binding["status"],
        "effective_available": effective_available,
        "desired_automation": dict(policy["automation"]),
        "recorded_verified_agents": recorded_verified_agents,
        "experimental": policy["experimental"],
        "config_pointer_registered": bool(policy["config_path"]),
        "binding_revision": binding_revision,
        "automatic_ingest": policy["automation"].get("automatic_ingest") is True
        and configured_automatic_ingest,
        "automatic_recall": policy["automation"].get("automatic_recall") is True
        and configured_automatic_recall,
        "automation_intent": dict(policy["automation_intent"]),
        "host_coverage": reward_memory_host_coverage(),
        "enabled_agents": list(policy["enabled_agents"]),
        "enablement_verified_agents": effective_verified_agents,
    }


def reward_memory_automatic_ingest_for_agent(
    goal: Mapping[str, Any], agent_id: str | None
) -> bool:
    """Return whether one Agent may receive automatic-ingest prompt guidance.

    Desired policy is a cheap off switch. Active admission reuses the runtime
    resolver so the prompt requires this exact Agent's current config binding
    and verified enablement receipt. The resolver is read-only and never calls
    the configured provider.
    """

    policy = reward_memory_goal_policy(goal)
    if (
        not policy["enabled"]
        or policy["automation"].get("automatic_ingest") is not True
        or not isinstance(agent_id, str)
        or not agent_id.strip()
    ):
        return False
    try:
        status, _ = resolve_goal_reward_memory_experiment(
            goal=goal, agent_id=agent_id
        )
    except ValueError:
        # Legacy alias-only registrations and malformed identities fail closed.
        return False
    return status.get("available") is True and status.get("automatic_ingest") is True


def plan_reward_memory_goal_configuration(
    *,
    goal: Mapping[str, Any],
    goal_id: str,
    registered_agents: Sequence[str],
    requested_config_path: str | None,
    requested_agents: Sequence[str] | None,
    clear: bool,
    observed_at: str,
    execute: bool,
) -> dict[str, Any]:
    """Validate one Goal-local Reward Memory change before registry mutation."""

    existing = reward_memory_goal_policy(goal)
    agents = list(
        requested_agents if requested_agents is not None else existing["enabled_agents"]
    )
    change_requested = requested_config_path is not None or requested_agents is not None
    config_path = requested_config_path or existing["config_path"]
    if change_requested and not config_path:
        raise ValueError(
            "--reward-memory-agent requires an existing or supplied "
            "--reward-memory-config"
        )
    if change_requested and not agents:
        raise ValueError(
            "enabling Reward Memory requires at least one --reward-memory-agent"
        )
    remains_enabled = not clear and (existing["enabled"] or change_requested)
    unknown_agents = sorted(set(agents) - set(registered_agents))
    if remains_enabled and unknown_agents:
        raise ValueError(
            "Reward Memory agents must already be registered for this goal: "
            + ", ".join(unknown_agents)
        )

    preflight: dict[str, Any] | None = None
    policy: dict[str, Any] | None = None
    if change_requested and not clear:
        project = Path(str(goal.get("repo") or "")).expanduser()
        config = load_reward_memory_experiment_config(
            project=project,
            config_path=config_path,
        )
        config_digest = local_private_config_digest(
            project=project,
            config_path=config_path,
        )
        if not config_digest:
            raise ValueError(
                "Reward Memory config must remain readable through its exact "
                "repo-relative pointer during enablement"
            )
        preflight = preflight_reward_memory_experiment_config(
            config,
            goal_id=goal_id,
            agent_ids=agents,
            observed_at=observed_at,
            execute=execute,
        )
        if execute and not preflight["ok"]:
            reason_codes = sorted(
                {
                    reason
                    for receipt in preflight["agent_receipts"].values()
                    for reason in receipt.get("reason_codes") or []
                }
            )
            detail = ", ".join(reason_codes) or "provider_write_preflight_failed"
            raise ValueError(
                "Reward Memory enablement requires a verified provider write and "
                f"exact readback: {detail}"
            )
        policy = {
            "enabled": True,
            "experimental": True,
            "config_path": config_path,
            "config_digest": config_digest,
            "enabled_agents": agents,
            "automation": deepcopy(config["automation"]),
            "automation_intent": deepcopy(config["automation_intent"]),
        }
        if (
            not execute
            and existing["config_path"] == config_path
            and existing["config_digest"] == config_digest
        ):
            preserved_receipts = {
                agent_id: deepcopy(existing["enablement_receipts"][agent_id])
                for agent_id in agents
                if agent_id in existing["enablement_receipts"]
            }
            if preserved_receipts:
                policy["enablement_receipts"] = preserved_receipts
        if execute:
            for receipt in preflight["agent_receipts"].values():
                receipt["config_digest"] = config_digest
            policy.update(
                {
                    "config_digest": config_digest,
                    "enablement_receipts": deepcopy(preflight["agent_receipts"]),
                }
            )
    return {
        "change_requested": change_requested or clear,
        "clear": clear,
        "enabled_agents": agents,
        "preflight": preflight,
        "policy": policy,
    }


def apply_reward_memory_goal_configuration(
    goal: dict[str, Any], plan: Mapping[str, Any]
) -> None:
    """Apply a prevalidated plan to the in-memory Goal projection."""

    if not plan.get("change_requested"):
        return
    current = goal.get("control_plane")
    control_plane = dict(current) if isinstance(current, Mapping) else {}
    if plan.get("clear"):
        control_plane.pop("reward_memory", None)
    else:
        control_plane["reward_memory"] = deepcopy(plan["policy"])
    goal["control_plane"] = control_plane


def reward_memory_preflight_markdown_lines(value: object) -> list[str]:
    """Render compact shared CLI/Lark-safe enablement feedback."""

    if not isinstance(value, Mapping):
        return []
    verified = value.get("status") == "verified"
    return [
        f"- reward_memory_preflight: `{value.get('status')}`",
        f"- reward_memory_agent_count: `{value.get('agent_count')}`",
        "- reward_memory_writability: `verified`"
        if verified
        else "- reward_memory_writability: `not_yet_verified`",
    ]
