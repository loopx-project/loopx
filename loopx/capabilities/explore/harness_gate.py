from __future__ import annotations

from typing import Any, Mapping, cast


from ...orchestration import compact_orchestration_policy


# The typed mode owner composes explicit Goal settings over the live machine
# preference. This gate receives the resulting orchestration projection, never
# reads settings itself, and preserves Goal-owned spawn/claim/lease authority.
EXPLORE_HARNESS_GATE_SCHEMA_VERSION = "loopx_explore_harness_gate_v0"
GATE_STATE_DISABLED = "disabled"
GATE_STATE_ANALYSIS_ONLY = "analysis_only"
GATE_STATE_COMMANDS_SUGGESTED = "commands_suggested"
MAX_BRANCH_WIDTH = 8
WORKER_HARNESS_PROFILE_SCHEMA_VERSION = "loopx_explore_worker_harness_profile_v0"
DEFAULT_TODOS_PER_WORKER_BRANCH = 3
MAX_TODOS_PER_WORKER_BRANCH = 8
DEFAULT_WORKER_HARNESS_PROFILE = "generic"

WORKER_HARNESS_PROFILES: dict[str, dict[str, Any]] = {
    "generic": {
        "schema_version": WORKER_HARNESS_PROFILE_SCHEMA_VERSION,
        "profile": "generic",
        "source": "loopx_explore_worker_branch_plan_default",
        "design_intent": "Generic dry-run worker-lane planning without runtime policy assumptions.",
        "branch_fill_policy": "bundle-by-affinity",
        "concurrency_policy": {
            "mode": "bounded_confidence_prefix",
            "fixed_worker_count": False,
            "does_not_force_requested_width": True,
        },
        "duration_guard": {
            "enabled": False,
            "controlled_by_planner": False,
        },
        "retry_policy": {
            "enabled": False,
            "controlled_by_planner": False,
        },
        "infra_cooldown": {
            "enabled": False,
            "controlled_by_planner": False,
        },
        "todo_batching_policy": {
            "mode": "fixed_safety_ceiling",
            "default_ceiling": DEFAULT_TODOS_PER_WORKER_BRANCH,
            "external_ceiling_role": "planner_batch_ceiling",
        },
    },
    "adaptive-resilient": {
        "schema_version": WORKER_HARNESS_PROFILE_SCHEMA_VERSION,
        "profile": "adaptive-resilient",
        "source": "generalized_from_long_horizon_calibration_evidence",
        "design_intent": (
            "Generalize the best observed harness traits without copying an experimental "
            "N value, duration limit, or saturated-fill policy. Lane width uses "
            "independent-lane admission -- the serial survival-product prefix was a "
            "structural mismatch for parallel lanes and under-filled requested width "
            "in early calibration."
        ),
        "branch_fill_policy": "value-first",
        "marginal_score_floor": 0.72,
        "todo_batching_policy": {
            "mode": "adaptive_value_scheduled",
            "default_ceiling": MAX_TODOS_PER_WORKER_BRANCH,
            "external_ceiling_required": False,
            "external_ceiling_role": "optional_safety_cap_only",
            "intent": (
                "Let the planner decide whether to bundle extra todos into a worker lane "
                "from marginal value, rather than forcing a per-worker todo count."
            ),
        },
        "concurrency_policy": {
            "mode": "independent_lane_admission",
            "fixed_worker_count": False,
            "does_not_force_requested_width": True,
            "worker_width_is_ceiling": True,
            "branch_count_is_value_scheduled": True,
        },
        "lane_start_stagger_seconds": 1.25,
        "retry_policy": {
            "enabled": True,
            "uses_backoff": True,
            "retryable_error_families": [
                "provider_service_unreachable",
                "transient_infra_failure",
            ],
        },
        "infra_cooldown": {
            "enabled": True,
            "failure_threshold": 2,
            "cooldown_epochs": 2,
            "strict_skip_when_healthy_width_available": True,
        },
        "duration_guard": {
            "enabled": False,
            "controlled_by_planner": False,
            "reason": "Duration limits are runner policy, not part of the generalized harness design.",
        },
        "coverage_floor": {
            "enabled": False,
            "reason": "An earlier coverage-floor calibration arm did not show enough value to become the default design.",
        },
    },
    "moe-router": {
        "schema_version": WORKER_HARNESS_PROFILE_SCHEMA_VERSION,
        "profile": "moe-router",
        "source": "moe_style_router_redesign",
        "design_intent": (
            "MoE-style value-first routing under a fixed worker ceiling: task families "
            "are the experts, todos are routed tokens. Width via independent-lane "
            "admission (interference-priced, never a fill rule); coverage via an "
            "aux-loss-free per-family bias that only reorders routing (DeepSeek-V3 "
            "invariant: bias never touches value bookkeeping); bundle length via the "
            "DSpark confident-prefix cutoff (arXiv:2607.05147) with per-family "
            "accept-rate calibration plus a wall-clock straggler guard."
        ),
        "branch_fill_policy": "confident-prefix",
        "marginal_score_floor": 0.72,
        "bundle_confidence_threshold": 0.6,
        # 2.5 x median permits a 2-deep bundle at uniform measured durations
        # while still fencing slow families; 1.25 would cap everything at 1
        # and make the confident-prefix rule unreachable.
        "bundle_straggler_factor": 2.5,
        "todo_batching_policy": {
            "mode": "confident_prefix_scheduled",
            "default_ceiling": MAX_TODOS_PER_WORKER_BRANCH,
            "external_ceiling_required": False,
            "external_ceiling_role": "optional_safety_cap_only",
            "intent": (
                "DSpark-faithful bundle sizing: append todos while calibrated per-todo "
                "acceptance confidence clears the threshold; truncate at the first "
                "below-threshold todo; cap the serial tail by predicted wall-clock."
            ),
        },
        "concurrency_policy": {
            "mode": "independent_lane_admission",
            "fixed_worker_count": False,
            "does_not_force_requested_width": True,
            "worker_width_is_ceiling": True,
            "branch_count_is_value_scheduled": True,
        },
        "opportunistic_lane_policy": {
            "enabled": True,
            "target_utilization_floor": 0.65,
            "min_lane_value_floor": 0.01,
            "min_lane_value_ratio": 0.2,
            "intent": (
                "Keep the high-confidence core from the theta peak, "
                "then admit additional positive-yield lanes up to a utilization floor "
                "so B does not waste worker capacity after proving active-lane quality."
            ),
        },
        "router_policy": {
            "enabled": True,
            "state_schema": "loopx_explore_router_state_v0",
            "bias_affects_routing_order_only": True,
            "admission_uses_unbiased_value": True,
            "experts": "affinity families (scope:artifacts/<family>) declared via work-item write scopes",
        },
        "lane_start_stagger_seconds": 1.25,
        "retry_policy": {
            "enabled": True,
            "uses_backoff": True,
            "retryable_error_families": [
                "provider_service_unreachable",
                "transient_infra_failure",
            ],
        },
        "infra_cooldown": {
            "enabled": True,
            "failure_threshold": 2,
            "cooldown_epochs": 2,
            "strict_skip_when_healthy_width_available": True,
        },
        "duration_guard": {
            "enabled": False,
            "controlled_by_planner": False,
            "reason": "Duration limits are runner policy, not part of the harness design.",
        },
        "coverage_floor": {
            "enabled": False,
            "reason": "Replaced by the aux-loss-free routing bias plus coverage recency bonus.",
        },
    },
}


def worker_harness_profile_names() -> tuple[str, ...]:
    """Return available worker-harness profile identifiers in canonical sorted order."""

    return tuple(sorted(WORKER_HARNESS_PROFILES))


def resolve_todo_branch_plan_gate(
    orchestration: Mapping[str, Any] | None,
    *,
    requested_width: int,
) -> dict[str, Any]:
    """Resolve the shared explore-harness gate with the branch planner's lane ceiling."""

    return resolve_explore_harness_gate(
        orchestration,
        requested_width=requested_width,
        max_lanes=MAX_BRANCH_WIDTH,
        max_lanes_label="max_branch_width",
    )


def explore_harness_required_contract(*, default_profile: str) -> dict[str, Any]:
    """The opt-in contract echoed back by a disabled planner packet.

    The shape shown is the registered goal's writable ``spawn_policy`` entry,
    which the quota/status pipeline projects into
    ``goal_boundary.orchestration``.
    """

    return {
        "spawn_policy": {
            "spawn_allowed": False,
            "max_children": 3,
            "explore_harness": {
                "enabled": True,
                "profile": default_profile,
            },
        },
        "note": (
            "Explore planning defaults to off. Choose planning in the machine "
            "settings or on this Goal; explicit Goal modes take precedence. "
            "The effective mode reaches goal_boundary.orchestration without "
            "granting spawn, claim, lease or quota authority."
        ),
    }


def resolve_explore_harness_gate(
    orchestration: Mapping[str, Any] | None,
    *,
    requested_width: int,
    max_lanes: int,
    max_lanes_label: str,
) -> dict[str, Any]:
    """Fold a goal's orchestration boundary into an explicit planner gate.

    ``orchestration`` is the registered goal's ``spawn_policy`` projected by
    quota/status into ``goal_boundary.orchestration``; ``None`` means no
    boundary is known for the goal, which is treated the same as an explicit
    opt-out. ``max_lanes`` is the calling planner's own width ceiling and
    ``max_lanes_label`` names it in the width-cap audit (e.g.
    ``max_worker_lanes``, ``max_branch_width``).
    """

    boundary_provided = isinstance(orchestration, Mapping) and bool(orchestration)
    compact = compact_orchestration_policy(dict(cast(Mapping[str, Any], orchestration)) if boundary_provided else None)
    harness_policy = (
        cast(dict[str, Any], compact.get("explore_harness")) if isinstance(compact.get("explore_harness"), dict) else {}
    )
    enabled = bool(harness_policy.get("enabled"))
    spawn_allowed = bool(compact.get("spawn_allowed"))
    max_children = max(0, int(compact.get("max_children") or 0))
    requested = max(1, int(requested_width or 1))
    gate: dict[str, Any] = {
        "schema_version": EXPLORE_HARNESS_GATE_SCHEMA_VERSION,
        "orchestration_boundary_provided": boundary_provided,
        "enabled": enabled,
        "spawn_allowed": spawn_allowed,
        "max_children": max_children,
        "requested_width": requested,
        "goal_pinned_profile": str(harness_policy.get("profile") or "") or None,
    }
    if not enabled:
        gate.update(
            {
                "state": GATE_STATE_DISABLED,
                "reason": "explore_harness_opt_in_required",
                "effective_width": 0,
                "width_cap_source": "gate_disabled",
            }
        )
        return gate
    width_caps = [(requested, "requested"), (max(1, int(max_lanes)), str(max_lanes_label))]
    if max_children > 0:
        width_caps.append((max_children, "max_children"))
    # On ties, report the boundary-owned cap so the audit names the authority.
    cap_priority = {"max_children": 0, str(max_lanes_label): 1, "requested": 2}
    effective_width, width_cap_source = min(
        width_caps, key=lambda cap: (cap[0], cap_priority[cap[1]])
    )
    if not spawn_allowed:
        state, reason = GATE_STATE_ANALYSIS_ONLY, "spawn_not_allowed_by_goal_boundary"
    elif max_children <= 0:
        state, reason = GATE_STATE_ANALYSIS_ONLY, "spawn_allowed_without_child_capacity"
    else:
        state, reason = GATE_STATE_COMMANDS_SUGGESTED, "goal_boundary_opt_in"
    gate.update(
        {
            "state": state,
            "reason": reason,
            "effective_width": max(1, int(effective_width)),
            "width_cap_source": width_cap_source,
        }
    )
    return gate
