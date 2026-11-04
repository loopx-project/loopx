from __future__ import annotations

from typing import Any, cast

from collections.abc import Callable
from dataclasses import dataclass

from .. import compact_control_plane_policy
from ..goals.goal_frontier import (
    build_autonomous_replan_recommendation,
    select_autonomous_replan_obligation,
)
from ..work_items.delivery_outcome import DeliveryOutcome, normalize_delivery_outcome
from ..work_items.work_lane_context import (
    build_work_lane_context_contract,
    latest_run_progress_scope,
)
from ..todos.user_gate import (
    open_todo_count as _open_todo_count,
    open_todo_notify_reason,
)


HEARTBEAT_READ_ONLY_MAP_ADAPTER_SUFFIX = "_read_only_map_v0"
HEARTBEAT_HANDOFF_READINESS_COMPACT_FIELDS = (
    "ready",
    "codex_ready",
    "source",
    "quota_state",
    "handoff_status",
    "post_handoff_run_seen",
    "post_handoff_small_scale_streak",
    "post_handoff_outcome_gap_streak",
    "handoff_interface_budget",
)
HEARTBEAT_POST_HANDOFF_RUN_COMPACT_FIELDS = (
    "generated_at",
    "classification",
    "progress_scope",
    "delivery_batch_scale",
    "delivery_outcome",
    "delivery_turn_kind",
    "health_check",
    "json_exists",
    "markdown_exists",
)


def _supports_read_only_project_map(adapter_kind: Any) -> bool:
    kind = str(adapter_kind or "").strip()
    return kind == "read_only_project_map_v0" or kind.endswith(
        HEARTBEAT_READ_ONLY_MAP_ADAPTER_SUFFIX
    )


def _text_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple, set)):
        values: list[str] = []
        for item in value:
            values.extend(_text_values(item))
        return values
    return [str(value)]


def _has_lifecycle_marker(*values: Any, marker: str) -> bool:
    target = marker.strip().lower()
    for value in values:
        for text in _text_values(value):
            if text.strip().lower() == target:
                return True
    return False


def _compact_latest_run(run: dict[str, Any]) -> dict[str, Any]:
    return {
        key: run.get(key)
        for key in HEARTBEAT_POST_HANDOFF_RUN_COMPACT_FIELDS
        if run.get(key) is not None
    }


def _control_plane_post_handoff_observation_hint(item: dict[str, Any]) -> dict[str, Any] | None:
    control_plane = compact_control_plane_policy(item.get("control_plane"))
    self_repair = (
        cast(dict[str, Any], control_plane.get("self_repair"))
        if isinstance(control_plane.get("self_repair"), dict)
        else {}
    )
    if self_repair.get("enabled") is not True:
        return None
    if str(item.get("adapter_kind") or "") != "harness_self_improvement":
        return None
    if item.get("agent_command"):
        return None

    handoff_readiness = (
        cast(dict[str, Any], item.get("handoff_readiness"))
        if isinstance(item.get("handoff_readiness"), dict)
        else {}
    )
    latest_run = (
        cast(dict[str, Any], handoff_readiness.get("post_handoff_latest_run"))
        if isinstance(handoff_readiness.get("post_handoff_latest_run"), dict)
        else {}
    )
    if handoff_readiness.get("post_handoff_run_seen") is not True:
        return None
    if str(handoff_readiness.get("handoff_status") or "") != "post_handoff_run_seen":
        return None
    if normalize_delivery_outcome(latest_run.get("delivery_outcome")) != DeliveryOutcome.PRIMARY_GOAL_OUTCOME:
        return None

    return {
        "source": "quota.should-run",
        "recommended_mode": "post_handoff_observe_if_unchanged",
        "stop_if_unchanged": True,
        "notify": "DONT_NOTIFY",
        "reason": (
            "control-plane self-repair is enabled and the latest post-handoff "
            "implementation run already reached the primary outcome; inspect "
            "registry/status/run history/repo state, then stay quiet if no new "
            "evidence or concrete safe work is found"
        ),
        "spend_policy": (
            "do not append quota spend for an unchanged post-handoff observation; "
            "spend only after a new validated artifact, repair, or durable state "
            "writeback advances the control-plane contract"
        ),
        "latest_run": _compact_latest_run(latest_run),
    }


_BASE_RECOMMENDATION: dict[str, Any] = {
    "source": "quota.should-run",
    "recommended_mode": "skip",
    "notify": "DONT_NOTIFY",
    "spend_policy": (
        "do not append quota spend unless a completed bounded progress segment "
        "produced substantive progress"
    ),
}

_CAPABILITY_GATE_RECOMMENDATION_OVERRIDES: dict[str, dict[str, Any]] = {
    "repair_bridge": {
        "recommended_mode": "repair_capability_bridge",
        "notify": "DONT_NOTIFY",
        "spend_policy": (
            "append exactly one quota spend only after a validated bridge "
            "repair, todo rewrite, or compact blocker writeback"
        ),
    },
    "ask_owner": {
        "recommended_mode": "ask_owner_for_capability",
        "notify": "NOTIFY",
        "spend_policy": "do not append quota spend while asking for missing capability",
    },
    "skip": {
        "recommended_mode": "capability_skip",
        "notify": "DONT_NOTIFY",
        "spend_policy": (
            "do not append quota spend while all executable todos lack "
            "current capabilities"
        ),
    },
}


def refine_heartbeat_recommendation(
    recommendation: dict[str, Any],
    *,
    should_run: bool,
    capability_gate: dict[str, Any] | None,
    capability_monitor_fallback: dict[str, Any] | None,
    workspace_guard: dict[str, Any] | None,
    automation_prompt_upgrade: dict[str, Any] | None,
    automation_prompt_upgrade_required: bool,
    blocked_priority_fallback: dict[str, Any] | None,
) -> dict[str, Any]:
    """Apply the ordered execution-guard refinements to heartbeat guidance."""

    refined = dict(recommendation)
    capability_action = (
        str(capability_gate.get("action") or "")
        if capability_gate and not capability_monitor_fallback
        else ""
    )
    capability_override = _CAPABILITY_GATE_RECOMMENDATION_OVERRIDES.get(
        capability_action
    )
    if capability_override:
        refined.update(capability_override)
        refined["reason"] = cast(dict[str, Any], capability_gate).get("reason") or refined.get("reason")

    if workspace_guard:
        refined.update(
            {
                "recommended_mode": "repair_agent_workspace",
                "notify": "DONT_NOTIFY",
                "reason": workspace_guard.get("reason") or refined.get("reason"),
                "spend_policy": (
                    "do not append quota spend for workspace relocation; rerun quota "
                    "from the independent worktree before delivery"
                ),
            }
        )

    if automation_prompt_upgrade_required:
        upgrade = automation_prompt_upgrade or {}
        refined.update(
            {
                "recommended_mode": "automation_prompt_upgrade",
                "notify": "DONT_NOTIFY",
                "reason": upgrade.get("reason") or refined.get("reason"),
                "spend_policy": (
                    "do not append quota spend for stale/unscoped automation; "
                    "rerun quota should-run from an identity-scoped prompt"
                ),
            }
        )

    if blocked_priority_fallback and should_run:
        refined["blocked_priority_fallback"] = blocked_priority_fallback
        if blocked_priority_fallback.get("notify_user") is True:
            refined["notify"] = "NOTIFY"
            refined["reason"] = (
                blocked_priority_fallback.get("reason") or refined.get("reason")
            )

    return refined


@dataclass(frozen=True)
class _HeartbeatRecommendationFacts:
    item: dict[str, Any]
    goal_id: str
    state: str
    should_run: bool
    status: str
    waiting_on: str
    adapter_kind: str
    lifecycle_phase: Any
    lifecycle_flags: Any
    quota: dict[str, Any]
    replan_obligation: dict[str, Any] | None
    has_user_todos: bool
    has_agent_todos: bool
    work_lane_contract: dict[str, Any]
    stall_self_repair: dict[str, Any] | None


_HeartbeatRecommendationRule = Callable[
    [_HeartbeatRecommendationFacts],
    Any,
]


def _recommendation(*parts: dict[str, Any]) -> dict[str, Any]:
    payload = dict(_BASE_RECOMMENDATION)
    for part in parts:
        payload.update(part)
    return payload


def build_action_selection_recovery_recommendation(
    *, reason: str,
) -> dict[str, Any]:
    """Project closed heartbeat guidance after a typed selection refusal."""

    return _recommendation(
        {
            "source": "action_selection_recovery",
            "recommended_mode": "quota_skip",
            "notify": "DONT_NOTIFY",
            "spend_policy": "no quota spend until an eligible Todo is selected",
            "reason": reason,
            "agent_must_attempt": False,
        }
    )


def _stall_self_repair_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    repair = facts.stall_self_repair
    if not repair or not repair.get("allowed"):
        return None
    if (
        facts.state == "operator_gate"
        and repair.get("trigger") != "user_gate_scope_projection_drift"
    ):
        return None
    return _recommendation(
        {
            "recommended_mode": repair.get("recommended_mode")
            or "repair_control_plane_stall",
            "notify": repair.get("notify") or "DONT_NOTIFY",
            "spend_policy": repair.get("spend_policy")
            or _BASE_RECOMMENDATION["spend_policy"],
            "reason": repair.get("reason")
            or "control-plane stall requires bounded repair",
            "repair_focus": repair.get("repair_focus"),
        }
    )


def _operator_gate_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    if facts.state != "operator_gate":
        return None
    return _recommendation(
        {
            "recommended_mode": "ask_operator_gate",
            "notify": "NOTIFY",
            "spend_policy": "do not append quota spend while asking the operator gate",
            "reason": "operator gate blocks the gated delivery path",
        }
    )


def _autonomous_replan_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    obligation = facts.replan_obligation
    if (
        not facts.should_run
        or not obligation
        or not obligation.get("required")
    ):
        return None
    return _recommendation(build_autonomous_replan_recommendation(obligation))


def _waiting_user_todo_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    if facts.state not in {"focus_wait", "waiting"} or not facts.has_user_todos:
        return None
    return _recommendation(
        {
            "recommended_mode": "blocker_push_notify",
            "notify": "NOTIFY",
            "repeat_notification_required": True,
            "spend_policy": "do not append quota spend for the blocker-push turn",
            "reason": open_todo_notify_reason(
                state=facts.state,
                waiting_on=facts.waiting_on,
            ),
        }
    )


def _outcome_floor_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    quota = facts.quota
    if facts.state != "focus_wait" or not quota.get("handoff_outcome_floor_block"):
        return None
    if quota.get("outcome_floor_blocker_projected"):
        return _recommendation(
            {
                "recommended_mode": "outcome_floor_blocker_projected_noop",
                "notify": "DONT_NOTIFY",
                "spend_policy": (
                    "do not append quota spend while the same concrete outcome-floor "
                    "blocker is already projected and no executable agent todo exists"
                ),
                "reason": str(
                    quota.get("reason")
                    or "outcome-floor blocker is already projected; wait for fresh outcome evidence"
                ),
            }
        )
    if quota.get("safe_bypass_allowed"):
        return _recommendation(
            {
                "recommended_mode": "outcome_floor_recovery",
                "notify": "DONT_NOTIFY",
                "spend_policy": (
                    "append exactly one quota spend only after a validated "
                    "ranker/cross-domain evidence artifact or concrete blocker writeback; "
                    "do not spend for another surface-only report"
                ),
                "reason": str(
                    quota.get("reason") or "handoff outcome floor is not met"
                ),
            }
        )
    return _recommendation(
        {
            "recommended_mode": "report_handoff_outcome_blocker",
            "notify": "NOTIFY",
            "spend_policy": (
                "do not append quota spend while reporting the handoff "
                "outcome-floor blocker"
            ),
            "reason": str(quota.get("reason") or "handoff outcome floor is not met"),
        }
    )


def _quota_skip_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    if facts.should_run:
        return None
    return _recommendation(
        {
            "recommended_mode": "quota_skip",
            "reason": f"quota state is {facts.state}; skip delivery compute",
        }
    )


def _agent_command_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    command = facts.item.get("agent_command")
    if not command:
        return None
    return _recommendation(
        {
            "recommended_mode": "run_agent_command",
            "command": str(command),
            "notify": "DONT_NOTIFY",
            "spend_policy": (
                "append exactly one heartbeat spend after the command completes "
                "and validation/writeback are saved"
            ),
            "reason": "current status exposes an approved project-agent command",
        }
    )


def _monitor_lane_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    lane = facts.work_lane_contract
    if lane.get("lane") != "continuous_monitor":
        return None
    must_attempt_work = lane.get("must_attempt_work")
    if must_attempt_work is False:
        if facts.has_user_todos:
            return _recommendation(
                {
                    "recommended_mode": "monitor_quiet_until_material_transition",
                    "spend_policy": (
                        "do not append quota spend or repeat a blocker notification "
                        "for a monitor-only poll; keep the open user todo visible in "
                        "the payload and wait for a material monitor transition"
                    ),
                    "reason": (
                        "monitor-only polling has no material transition to write "
                        "back; the current open user todo is already part of the "
                        "durable active state"
                    ),
                }
            )
        non_runnable_non_monitor_count = max(
            0,
            int(lane.get("non_runnable_non_monitor_count") or 0),
        )
        if non_runnable_non_monitor_count:
            reason = (
                "no executable advancement todo is runnable; "
                f"{non_runnable_non_monitor_count} non-monitor todo(s) are "
                "blocked or otherwise non-executable, and the remaining "
                "schedulable work is non-due monitor-class"
            )
        else:
            reason = (
                "all visible open agent todos are monitor-class work with no "
                "material transition to record"
            )
        return _recommendation(
            {
                "recommended_mode": "monitor_quiet_until_material_transition",
                "spend_policy": (
                    "do not append quota spend until a material monitor transition, "
                    "regression, or concrete blocker is validated and written back"
                ),
                "reason": reason,
            }
        )
    if must_attempt_work is not True or facts.has_user_todos:
        return None

    latest_run: dict[str, Any] = {}
    handoff_readiness = (
        cast(dict[str, Any], facts.item.get("handoff_readiness"))
        if isinstance(facts.item.get("handoff_readiness"), dict)
        else {}
    )
    latest_handoff_run = (
        cast(dict[str, Any], handoff_readiness.get("post_handoff_latest_run"))
        if isinstance(handoff_readiness.get("post_handoff_latest_run"), dict)
        else {}
    )
    if latest_handoff_run:
        latest_run = _compact_latest_run(latest_handoff_run)
        latest_run["progress_scope"] = latest_run_progress_scope(latest_handoff_run)
    payload = _recommendation(
        {
            "recommended_mode": "follow_work_lane_contract",
            "spend_policy": (
                "follow work_lane_contract.obligation; spend only after validated "
                "advancement, concrete blocker writeback, or a material monitor transition"
            ),
            "reason": (
                "work_lane_contract is the machine contract for "
                "monitor-vs-advancement routing"
            ),
        }
    )
    if latest_run:
        payload["latest_run"] = latest_run
    return payload


def _first_read_only_map_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    if facts.status != "connected_without_run" or not _supports_read_only_project_map(
        facts.adapter_kind
    ):
        return None
    return _recommendation(
        {
            "recommended_mode": "run_first_read_only_map",
            "command": f"loopx read-only-map --goal-id {facts.goal_id}",
            "notify": "NOTIFY",
            "spend_policy": (
                "append exactly one heartbeat spend after the read-only map "
                "run is saved and validated"
            ),
            "reason": "connected read-only project has no saved compact run yet",
        }
    )


def _mapped_noop_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    mapped = facts.status == "read_only_project_map" or _has_lifecycle_marker(
        facts.lifecycle_phase,
        facts.lifecycle_flags,
        marker="mapped",
    )
    if (
        not mapped
        or facts.has_user_todos
        or facts.has_agent_todos
        or facts.item.get("agent_command")
    ):
        return None
    return _recommendation(
        {
            "recommended_mode": "mapped_noop_if_unchanged",
            "stop_if_unchanged": True,
            "spend_policy": (
                "do not run another dry-run or append quota spend when the latest "
                "read-only map is still current and there is no new user instruction, "
                "owner evidence, agent todo, stale source, or safe handoff"
            ),
            "reason": (
                "latest compact read-only map already exists; wait for new "
                "evidence or a concrete safe action"
            ),
        }
    )


def _post_handoff_observation_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    post_handoff = _control_plane_post_handoff_observation_hint(facts.item)
    if not post_handoff or facts.has_user_todos:
        return None
    latest_run = (
        cast(dict[str, Any], post_handoff.get("latest_run"))
        if isinstance(post_handoff.get("latest_run"), dict)
        else {}
    )
    progress_scope = latest_run_progress_scope(latest_run)
    if latest_run:
        latest_run.setdefault("progress_scope", progress_scope)
    active_observation = {
        key: value for key, value in post_handoff.items() if key != "stop_if_unchanged"
    }
    if facts.has_agent_todos and progress_scope == "dependency_observation":
        return _recommendation(
            active_observation,
            {
                "recommended_mode": "follow_work_lane_contract",
                "spend_policy": (
                    "follow work_lane_contract.obligation; spend only after validated "
                    "advancement, concrete blocker writeback, or a material monitor transition"
                ),
                "reason": (
                    "latest post-handoff run was dependency observation; the work "
                    "lane contract decides whether to advance backlog or record a "
                    "material transition"
                ),
            },
        )
    if facts.has_agent_todos:
        return _recommendation(
            active_observation,
            {
                "recommended_mode": "post_handoff_observe_then_backlog_step",
                "spend_policy": (
                    "observe registry/status/run history/repo state first; if unchanged, "
                    "advance scope-bounded agent-todo work and append "
                    "quota spend only after validation and durable writeback"
                ),
                "reason": (
                    "latest post-handoff implementation reached the primary outcome, "
                    "but an open agent todo remains; observe for new blockers first, "
                    "then advance scope-bounded backlog work instead of quiet idling"
                ),
            },
        )
    return _recommendation(post_handoff)


def _default_rule(
    facts: _HeartbeatRecommendationFacts,
) -> dict[str, Any] | None:
    return _recommendation(
        {
            "recommended_mode": "steering_audit_then_one_step",
            "spend_policy": (
                "append exactly one heartbeat spend only after scope-bounded work "
                "is validated and written back; one_step: mode, not an operation limit"
            ),
            "reason": (
                "eligible Codex-ready goal requires the standard steering audit "
                "before delivery"
            ),
        }
    )


_HEARTBEAT_RECOMMENDATION_RULES: tuple[_HeartbeatRecommendationRule, ...] = (
    _stall_self_repair_rule,
    _operator_gate_rule,
    _autonomous_replan_rule,
    _waiting_user_todo_rule,
    _outcome_floor_rule,
    _quota_skip_rule,
    _agent_command_rule,
    _monitor_lane_rule,
    _first_read_only_map_rule,
    _mapped_noop_rule,
    _post_handoff_observation_rule,
    _default_rule,
)


def _build_recommendation_facts(
    item: dict[str, Any],
    *,
    goal_id: str,
    state: str,
    should_run: bool,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None,
    stall_self_repair: dict[str, Any] | None,
    replan_obligation: dict[str, Any] | None,
    select_replan_obligation: bool,
    monitor_due_item_limit: int,
) -> _HeartbeatRecommendationFacts:
    project_asset = (
        cast(dict[str, Any], item.get("project_asset"))
        if isinstance(item.get("project_asset"), dict)
        else {}
    )
    resolved_replan_obligation = (
        replan_obligation
        if isinstance(replan_obligation, dict)
        else select_autonomous_replan_obligation(item, project_asset)
        if select_replan_obligation
        else None
    )
    resolved_work_lane = (
        work_lane_contract
        or build_work_lane_context_contract(
            item,
            agent_todo_summary=agent_todo_summary,
            monitor_due_item_limit=monitor_due_item_limit,
        )
        or {}
    )
    return _HeartbeatRecommendationFacts(
        item=item,
        goal_id=goal_id,
        state=state,
        should_run=should_run,
        status=str(item.get("status") or ""),
        waiting_on=str(item.get("waiting_on") or ""),
        adapter_kind=str(item.get("adapter_kind") or ""),
        lifecycle_phase=item.get("lifecycle_phase"),
        lifecycle_flags=item.get("lifecycle_flags"),
        quota=cast(dict[str, Any], item.get("quota")) if isinstance(item.get("quota"), dict) else {},
        replan_obligation=resolved_replan_obligation,
        has_user_todos=_open_todo_count(user_todo_summary) > 0,
        has_agent_todos=_open_todo_count(agent_todo_summary) > 0,
        work_lane_contract=resolved_work_lane,
        stall_self_repair=stall_self_repair,
    )


def build_heartbeat_recommendation(
    item: dict[str, Any],
    *,
    goal_id: str,
    state: str,
    should_run: bool,
    user_todo_summary: dict[str, Any] | None,
    agent_todo_summary: dict[str, Any] | None,
    work_lane_contract: dict[str, Any] | None = None,
    stall_self_repair: dict[str, Any] | None = None,
    replan_obligation: dict[str, Any] | None = None,
    select_replan_obligation: bool = True,
    monitor_due_item_limit: int = 1,
) -> dict[str, Any]:
    facts = _build_recommendation_facts(
        item,
        goal_id=goal_id,
        state=state,
        should_run=should_run,
        user_todo_summary=user_todo_summary,
        agent_todo_summary=agent_todo_summary,
        work_lane_contract=work_lane_contract,
        stall_self_repair=stall_self_repair,
        replan_obligation=replan_obligation,
        select_replan_obligation=select_replan_obligation,
        monitor_due_item_limit=monitor_due_item_limit,
    )
    for rule in _HEARTBEAT_RECOMMENDATION_RULES:
        recommendation = rule(facts)
        if recommendation is not None:
            return cast(dict[str, Any], recommendation)
    raise AssertionError("heartbeat recommendation rule table requires a fallback")
