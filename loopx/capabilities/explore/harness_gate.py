from __future__ import annotations

from typing import Any, Mapping

from ...orchestration import compact_orchestration_policy


# The typed mode owner composes explicit Goal settings over the live machine
# preference. This gate receives the resulting orchestration projection, never
# reads settings itself, and preserves Goal-owned spawn/claim/lease authority.
EXPLORE_HARNESS_GATE_SCHEMA_VERSION = "loopx_explore_harness_gate_v0"
GATE_STATE_DISABLED = "disabled"
GATE_STATE_ANALYSIS_ONLY = "analysis_only"
GATE_STATE_COMMANDS_SUGGESTED = "commands_suggested"


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
    compact = compact_orchestration_policy(dict(orchestration) if boundary_provided else None)
    harness_policy = (
        compact.get("explore_harness") if isinstance(compact.get("explore_harness"), dict) else {}
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
