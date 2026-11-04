from __future__ import annotations

from typing import Any, cast
from ..quota.effective_action import EffectiveAction


from ..agents.agent_scope_frontier import (
    AgentScopeFrontierAction,
    agent_scope_frontier_action as _agent_scope_frontier_action,
)
from ..goals.goal_frontier import AUTONOMOUS_REPLAN_REQUIRED_MODE


AUTOMATION_LIVENESS_SCHEMA_VERSION = "automation_liveness_v0"


def build_automation_liveness(payload: dict[str, Any]) -> dict[str, Any]:
    heartbeat_recommendation = (
        cast(dict[str, Any], payload.get("heartbeat_recommendation"))
        if isinstance(payload.get("heartbeat_recommendation"), dict)
        else {}
    )
    execution_obligation = (
        cast(dict[str, Any], payload.get("execution_obligation"))
        if isinstance(payload.get("execution_obligation"), dict)
        else {}
    )
    effective_action = str(payload.get("effective_action") or "")
    recommended_mode = str(heartbeat_recommendation.get("recommended_mode") or "")
    must_attempt_work = bool(execution_obligation.get("must_attempt_work"))

    base = {
        "schema_version": AUTOMATION_LIVENESS_SCHEMA_VERSION,
        "keep_active": True,
        "pause_allowed": False,
        "pause_policy": (
            "pause/delete only after a bounded self-repair or replan path is itself "
            "stuck for two more eligible turns"
        ),
    }
    if recommended_mode == "goal_stopped":
        return {
            **base,
            "keep_active": False,
            "pause_allowed": True,
            "pause_policy": (
                "pause or delete the recurring automation now; only an explicit "
                "Goal lifecycle resume should recreate or resume it"
            ),
            "automation_action": "stop_goal_stopped",
            "reason": "Goal lifecycle is stopped by owner",
            "next_trigger": "explicit Goal lifecycle resume",
            "spend_policy": "no quota spend for stopped-Goal automation shutdown",
        }
    if recommended_mode == "quota_paused":
        return {
            **base,
            "keep_active": False,
            "pause_allowed": True,
            "pause_policy": (
                "pause or delete the recurring automation now; only an explicit "
                "quota resume with quota.compute > 0 should recreate or resume it"
            ),
            "automation_action": "stop_quota_paused",
            "reason": "Goal-level compute quota is paused",
            "next_trigger": "explicit quota resume with quota.compute > 0",
            "spend_policy": "no quota spend for paused automation shutdown",
        }
    if effective_action == EffectiveAction.AGENT_MONITOR_ONLY.value:
        return {
            **base,
            "keep_active": True,
            "pause_allowed": False,
            "pause_policy": (
                "keep a quiet monitor/reply poll; do not start advancement, "
                "autonomous replan, repair, or fallback work until mode changes"
            ),
            "automation_action": "keep_active_monitor_only",
            "reason": "agent monitor-only mode suppresses advancement work",
            "next_trigger": (
                "due monitor, material monitor transition, verified direct operator "
                "reply, or explicit work-mode change"
            ),
            "spend_policy": "no quota spend without a validated material transition",
        }
    if effective_action == EffectiveAction.TERMINAL_NO_FOLLOWUP.value:
        return {
            **base,
            "keep_active": False,
            "pause_allowed": True,
            "pause_policy": (
                "pause or delete the recurring automation now; only an explicit "
                "goal resume or new projected work should recreate it"
            ),
            "automation_action": "stop_terminal_no_followup",
            "reason": (
                "validated closure evidence derives no-follow-up from complete todo "
                "sources and an empty normalized frontier"
            ),
            "next_trigger": "explicit goal resume or newly projected work",
            "spend_policy": "no quota spend for terminal automation shutdown",
        }
    if effective_action == EffectiveAction.PEER_COORDINATION_BLOCKED.value:
        return {
            **base,
            "automation_action": "keep_active_peer_coordination_backoff",
            "reason": (
                "peer coordination is recoverable from asynchronous peer, runtime, "
                "configuration, or local-frontier changes"
            ),
            "next_trigger": (
                "peer activation capability, peer runtime readiness, coordinator "
                "configuration, or newly projected local work"
            ),
            "spend_policy": "no quota spend while explicit peer coordination is blocked",
        }
    if (
        effective_action == EffectiveAction.MONITOR_QUIET_SKIP.value
        or recommended_mode == "monitor_quiet_until_material_transition"
    ):
        return {
            **base,
            "automation_action": "keep_active_quiet",
            "reason": (
                "monitor-only quiet skip is a liveness-preserving no-op, not a "
                "self-stop signal"
            ),
            "next_trigger": (
                "material monitor transition, regression, concrete blocker, or "
                f"{AUTONOMOUS_REPLAN_REQUIRED_MODE}"
            ),
            "spend_policy": "no quota spend for unchanged monitor-only polls",
        }
    if effective_action == EffectiveAction.HEARTBEAT_SETTLED_SKIP.value:
        return {
            **base,
            "automation_action": "keep_active_quiet",
            "reason": (
                "the current heartbeat identity is fully settled; keep the "
                "automation active so a new turn can select the successor"
            ),
            "next_trigger": "next heartbeat turn with a fresh turn identity",
            "spend_policy": "no quota spend for an already-settled heartbeat turn",
        }
    if effective_action == EffectiveAction.AUTOMATION_PROMPT_UPGRADE_REQUIRED.value:
        return {
            **base,
            "automation_action": "repair_automation_prompt_identity",
            "reason": (
                "the installed automation is stale or unscoped; keep the automation "
                "active but block delivery until it reruns with a registered agent id"
            ),
            "spend_policy": "no quota spend for identity prompt upgrade preflight",
        }
    if effective_action == AgentScopeFrontierAction.SUCCESSOR_REPLAN_REQUIRED.value:
        agent_scope_frontier = (
            cast(dict[str, Any], payload.get("agent_scope_frontier"))
            if isinstance(payload.get("agent_scope_frontier"), dict)
            else {}
        )
        if agent_scope_frontier.get("monitor_blocked_resume_candidates"):
            return {
                **base,
                "automation_action": "execute_bounded_work",
                "reason": (
                    "a current-agent advancement todo is gated by an open standing "
                    "monitor; repair the gate model before another quiet no-op"
                ),
                "next_trigger": "standing monitor gate repair writeback or fresh quota guard",
                "spend_policy": "spend once only after validated gate repair/todo writeback",
            }
        return {
            **base,
            "automation_action": "execute_bounded_work",
            "reason": (
                "a ready deferred successor is visible to this agent; run a bounded "
                "successor replan or write a no-follow-up rationale before another quiet no-op"
            ),
            "next_trigger": "deferred successor replan writeback or fresh quota guard",
            "spend_policy": "spend once only after validated successor replan/todo writeback",
        }
    if _agent_scope_frontier_action(effective_action) is not None:
        return {
            **base,
            "automation_action": "keep_active_quiet",
            "reason": (
                "the current agent has no in-scope runnable candidate; this is a "
                "liveness-preserving no-op until work is reassigned or projected"
            ),
            "next_trigger": (
                "handoff owner progress, reassignment, or a current-agent/unclaimed "
                "advancement todo"
            ),
            "spend_policy": "no quota spend for agent-scoped no-candidate checks",
        }
    if must_attempt_work or recommended_mode == AUTONOMOUS_REPLAN_REQUIRED_MODE:
        return {
            **base,
            "automation_action": "execute_bounded_work",
            "reason": (
                "execution_obligation requires a bounded progress or replan segment "
                "before another quiet no-op"
            ),
            "spend_policy": "spend once only after validation and durable writeback",
        }
    if recommended_mode == "mapped_noop_if_unchanged":
        return {
            **base,
            "automation_action": "keep_active_noop_if_unchanged",
            "reason": (
                "unchanged mapped state should stay quiet and active until new evidence "
                "or a concrete safe handoff appears"
            ),
            "spend_policy": "no quota spend for unchanged mapped no-op checks",
        }
    return {
        **base,
        "automation_action": "keep_active",
        "reason": "heartbeat liveness should be preserved unless the repair path is stuck",
        "spend_policy": (
            "follow heartbeat_recommendation; spend only after validated delivery or "
            "safe-bypass writeback"
        ),
    }
