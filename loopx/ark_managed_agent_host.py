"""Thin one-shot goal activation contract for Ark Managed Agent."""

from __future__ import annotations

from typing import Any

from .control_plane.scheduler.execution_context import (
    GOAL_RUNTIME_CONTINUATION_SCHEMA_VERSION,
)
from .control_plane.work_items.runtime_capability_reentry import (
    RUNTIME_CAPABILITY_REENTRY_SCHEMA_VERSION,
)

ARK_MANAGED_AGENT_HOST = "ark-managed-agent"
ARK_MANAGED_AGENT_HOST_CONTRACT_SCHEMA_VERSION = (
    "loopx_ark_managed_agent_goal_host_v0"
)
ARK_MANAGED_AGENT_PROMPT_FAMILY = "loopx_goal_prompt_v0"


def build_ark_managed_agent_host_contract() -> dict[str, Any]:
    """Describe transport-neutral ownership for one goal prompt activation."""

    return {
        "schema_version": ARK_MANAGED_AGENT_HOST_CONTRACT_SCHEMA_VERSION,
        "host_kind": ARK_MANAGED_AGENT_HOST,
        "activation_mode": "goal_once",
        "prompt_family": ARK_MANAGED_AGENT_PROMPT_FAMILY,
        "policy_source": "quota_should_run.interaction_contract",
        "transport_contract": "goal_prompt_v0",
        "goal_runtime_owns_continuation": True,
        "goal_lifecycle_scope": "registered_goal_until_terminal",
        "phase_handoff_allowed": False,
        "loopx_turn_driver_required": False,
        "session_state_authoritative": False,
        "goal_runtime_continuation": {
            "source_ref": "quota_should_run.scheduler_hint.goal_runtime_continuation",
            "packet_schema_version": GOAL_RUNTIME_CONTINUATION_SCHEMA_VERSION,
            "dispositions": ["continue_now", "defer", "complete"],
            "defer_wake_policy": "state_change_or_deadline",
            "reason_source_ref": "quota_should_run.scheduler_hint.reason_code",
            "state_identity_source_ref": (
                "quota_should_run.scheduler_hint.reset_policy"
            ),
            "goal_prompt_mutated": False,
        },
        "runtime_capability_reentry": {
            "source_ref": (
                "quota_should_run.interaction_contract.cli_channel."
                "runtime_capability_reentry"
            ),
            "cli_projection_ref": "quota_should_run.runtime_capability_reentry",
            "packet_schema_version": RUNTIME_CAPABILITY_REENTRY_SCHEMA_VERSION,
            "delivery_channel": "quota_tool_result",
            "goal_prompt_mutated": False,
            "session_scoped": True,
            "durable_grant_written": False,
        },
    }
