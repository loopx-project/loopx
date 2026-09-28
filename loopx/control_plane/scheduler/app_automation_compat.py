from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, Callable

from .state import CODEX_APP_STATEFUL_BACKOFF_STATE_KEY, CODEX_APP_SURFACE

CODEX_APP_STATEFUL_BACKOFF_SCHEMA_VERSION = "codex_app_stateful_backoff_v0"
CODEX_APP_SCHEDULER_ACK_HINT_SCHEMA_VERSION = "codex_app_scheduler_ack_hint_v0"
CODEX_APP_SCHEDULER_FAILURE_HINT_SCHEMA_VERSION = (
    "codex_app_scheduler_failure_hint_v0"
)


def build_codex_app_compatibility_projection(
    app_automation: dict[str, Any],
    *,
    goal_id: Any,
    agent_id: Any,
    available_capabilities: Any,
    scheduler_host_facts: Mapping[str, Any] | None,
    observed_host_rrule: Any,
    scheduler_before: Mapping[str, Any],
    automation_id: Any,
    build_ack_hint: Callable[..., dict[str, Any]],
    build_failure_hint: Callable[..., dict[str, Any]],
    build_fallback_hint: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Translate the App packet without changing its durable authority scope."""

    legacy = copy.deepcopy(app_automation)
    legacy["applicability"] = "applicable"
    legacy["rrule_source"] = (
        "scheduler_hint.codex_app.recommended_rrule"
        if legacy.get("recommended_rrule")
        else None
    )
    backoff = legacy.get("stateful_backoff")
    state_key = (
        backoff["state_key"]
        if isinstance(backoff, dict)
        else CODEX_APP_STATEFUL_BACKOFF_STATE_KEY
    )
    if isinstance(backoff, dict):
        backoff["schema_version"] = CODEX_APP_STATEFUL_BACKOFF_SCHEMA_VERSION
    legacy_facts = (
        {
            **scheduler_host_facts,
            "surface": CODEX_APP_SURFACE,
            "state_key": state_key,
        }
        if isinstance(scheduler_host_facts, Mapping)
        else None
    )
    if isinstance(legacy.get("failure_hint"), dict):
        failure_facts = (
            {
                **legacy_facts,
                "operation": "host_failure",
                "applied_rrule": observed_host_rrule,
                "observed_host_rrule": observed_host_rrule,
                "failure_kind": "host_tool_failure",
                "source": "quota_scheduler_host_update_failure",
                "host_match_observed": False,
            }
            if legacy_facts is not None
            else None
        )
        legacy["failure_hint"] = build_failure_hint(
            state_key=state_key,
            goal_id=goal_id,
            agent_id=agent_id,
            failed_rrule=legacy.get("recommended_rrule"),
            observed_host_rrule=observed_host_rrule,
            available_capabilities=available_capabilities,
            scheduler_host_facts=failure_facts,
            scheduler_before=scheduler_before,
        )
        if not legacy.get("execution_interval_policy"):
            legacy["fallback_hint"] = build_fallback_hint(
                goal_id=goal_id,
                agent_id=agent_id,
                automation_id=automation_id,
            )
    if isinstance(legacy.get("ack_hint"), dict):
        canonical_ack = app_automation["ack_hint"]
        canonical_args = (
            canonical_ack.get("args")
            if isinstance(canonical_ack.get("args"), dict)
            else {}
        )
        legacy["ack_hint"] = build_ack_hint(
            state_key=state_key,
            goal_id=goal_id,
            agent_id=agent_id,
            applied_rrule=canonical_args.get("applied_rrule"),
            reset_token=canonical_args.get("reset_token"),
            identity_signature=canonical_args.get("identity_signature"),
            available_capabilities=available_capabilities,
            after=canonical_ack.get("after", "automation_update_rrule_success"),
            host_match_observed=canonical_args.get("host_match_observed") is True,
            scheduler_host_facts=(
                {
                    **legacy_facts,
                    "operation": "ack",
                    "applied_rrule": canonical_args.get("applied_rrule"),
                    "observed_host_rrule": observed_host_rrule,
                    "source": "quota_scheduler_ack",
                    "host_match_observed": (
                        canonical_args.get("host_match_observed") is True
                    ),
                }
                if legacy_facts is not None
                else None
            ),
            scheduler_before=scheduler_before,
        )
    return legacy
