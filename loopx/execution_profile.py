from __future__ import annotations

import re
from typing import Any

from .control_plane.goals.goal_vision_policy import (
    completed_todo_replan_threshold,
    normalize_completed_todo_replan_threshold,
    normalize_effective_turn_replan_threshold,
)
from .control_plane.work_items.delivery_outcome import DeliveryOutcome

DEFAULT_EXECUTION_PROFILE: dict[str, Any] = {
    "cadence": "bounded_progress_segment",
    "minimum_scale": "multi_surface_or_implementation",
    "must_include": [
        "coherent_artifact",
        "targeted_validation",
        "state_writeback",
    ],
    "spend_rule": "spend_only_after_artifact_validation_writeback",
    "outcome_floor": {
        "required_when": "after_surface_progress_streak",
        "surface_streak_threshold": 3,
        "outcome_markers": [],
        "surface_only_hints": [],
        "must_advance": [
            DeliveryOutcome.PRIMARY_GOAL_OUTCOME.value,
        ],
        "avoid": [
            "surface_only_progress_loop",
        ],
        "if_unavailable": "report_blocker_without_spend",
    },
    "degradation_policy": {
        "small_scale_streak_threshold": 2,
        "on_degradation": "require_blocker_or_expand_next_batch",
    },
}

TURN_GRANULARITY_STANDARD = "standard"
TURN_GRANULARITY_FINE = "fine"
TURN_GRANULARITY_CHOICES = (
    TURN_GRANULARITY_STANDARD,
    TURN_GRANULARITY_FINE,
)
FINE_GRAINED_SMALL_SCALE_STREAK_THRESHOLD = 5
FINE_GRAINED_PLANNING_GRANULARITY = "fine_checkpoint"
FINE_GRAINED_TURN_WORK_BUDGET = "coherent_slice"
FINE_GRAINED_CHECKPOINT_ACCOUNTING = "advancement_only"

_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,79}$")


def _label(value: Any, fallback: str) -> str:
    text = str(value or "").strip()
    if _LABEL_PATTERN.match(text):
        return text
    return fallback


def _label_list(value: Any, fallback: list[str]) -> list[str]:
    if not isinstance(value, list):
        return list(fallback)
    labels = []
    for item in value:
        label = _label(item, "")
        if label:
            labels.append(label)
    return labels or list(fallback)


def _positive_int(value: Any, fallback: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed > 0 else fallback


def _apply_fine_grained_contract(profile: dict[str, Any]) -> None:
    profile["turn_granularity"] = TURN_GRANULARITY_FINE
    profile["planning_granularity"] = FINE_GRAINED_PLANNING_GRANULARITY
    profile["turn_work_budget"] = FINE_GRAINED_TURN_WORK_BUDGET
    profile["checkpoint_accounting"] = FINE_GRAINED_CHECKPOINT_ACCOUNTING
    profile["minimum_scale"] = "single_surface"
    profile["degradation_policy"] = {
        "small_scale_streak_threshold": FINE_GRAINED_SMALL_SCALE_STREAK_THRESHOLD,
        "on_degradation": "review_direction_after_bounded_chain",
    }


def build_execution_profile(
    *,
    minimum_scale: str | None = None,
    must_include: list[str] | None = None,
    small_scale_streak_threshold: int | None = None,
    outcome_markers: list[str] | None = None,
    surface_only_hints: list[str] | None = None,
    surface_streak_threshold: int | None = None,
    outcome_must_advance: list[str] | None = None,
    turn_granularity: str | None = None,
) -> dict[str, Any]:
    profile = compact_execution_profile(None)
    if minimum_scale:
        profile["minimum_scale"] = _label(minimum_scale, str(profile["minimum_scale"]))
    if must_include:
        profile["must_include"] = _label_list(must_include, list(profile["must_include"]))
    if small_scale_streak_threshold is not None:
        policy = dict(profile["degradation_policy"])
        policy["small_scale_streak_threshold"] = _positive_int(
            small_scale_streak_threshold,
            int(policy["small_scale_streak_threshold"]),
        )
        profile["degradation_policy"] = policy
    floor = dict(profile["outcome_floor"])
    if outcome_markers:
        floor["outcome_markers"] = _label_list(outcome_markers, list(floor["outcome_markers"]))
    if surface_only_hints:
        floor["surface_only_hints"] = _label_list(surface_only_hints, list(floor["surface_only_hints"]))
    if surface_streak_threshold is not None:
        floor["surface_streak_threshold"] = _positive_int(
            surface_streak_threshold,
            int(floor["surface_streak_threshold"]),
        )
    if outcome_must_advance:
        floor["must_advance"] = _label_list(outcome_must_advance, list(floor["must_advance"]))
    profile["outcome_floor"] = floor
    if turn_granularity is not None:
        normalized_turn_granularity = normalize_turn_granularity(turn_granularity)
        if normalized_turn_granularity == TURN_GRANULARITY_FINE:
            _apply_fine_grained_contract(profile)
    return profile


def compact_execution_profile(value: Any) -> dict[str, Any]:
    defaults = DEFAULT_EXECUTION_PROFILE
    profile = {
        "cadence": defaults["cadence"],
        "minimum_scale": defaults["minimum_scale"],
        "must_include": list(defaults["must_include"]),
        "spend_rule": defaults["spend_rule"],
        "outcome_floor": {
            "required_when": defaults["outcome_floor"]["required_when"],
            "surface_streak_threshold": defaults["outcome_floor"]["surface_streak_threshold"],
            "outcome_markers": list(defaults["outcome_floor"]["outcome_markers"]),
            "surface_only_hints": list(defaults["outcome_floor"]["surface_only_hints"]),
            "must_advance": list(defaults["outcome_floor"]["must_advance"]),
            "avoid": list(defaults["outcome_floor"]["avoid"]),
            "if_unavailable": defaults["outcome_floor"]["if_unavailable"],
        },
        "degradation_policy": dict(defaults["degradation_policy"]),
    }
    if not isinstance(value, dict):
        return profile

    if "replan_after_effective_turns" in value and "replan_after_completed_todos" in value:
        raise ValueError("choose one review cadence unit")
    if "replan_after_effective_turns" in value:
        profile["replan_after_effective_turns"] = normalize_effective_turn_replan_threshold(
            value["replan_after_effective_turns"]
        )

    if "replan_after_completed_todos" in value:
        # Retain explicit legacy-unit overrides, including five. Omitting one
        # now means inheriting the settled-Turn default.
        profile["replan_after_completed_todos"] = completed_todo_replan_threshold(value)

    if "turn_granularity" in value:
        turn_granularity = normalize_turn_granularity(value.get("turn_granularity"))
        if turn_granularity == TURN_GRANULARITY_FINE:
            profile["turn_granularity"] = turn_granularity

    profile["cadence"] = _label(value.get("cadence"), str(profile["cadence"]))
    profile["minimum_scale"] = _label(value.get("minimum_scale"), str(profile["minimum_scale"]))
    profile["must_include"] = _label_list(value.get("must_include"), list(profile["must_include"]))
    profile["spend_rule"] = _label(value.get("spend_rule"), str(profile["spend_rule"]))

    raw_policy = value.get("degradation_policy") if isinstance(value.get("degradation_policy"), dict) else {}
    policy = dict(profile["degradation_policy"])
    policy["small_scale_streak_threshold"] = _positive_int(
        raw_policy.get("small_scale_streak_threshold"),
        int(policy["small_scale_streak_threshold"]),
    )
    policy["on_degradation"] = _label(raw_policy.get("on_degradation"), str(policy["on_degradation"]))
    profile["degradation_policy"] = policy

    raw_floor = value.get("outcome_floor") if isinstance(value.get("outcome_floor"), dict) else {}
    floor = dict(profile["outcome_floor"])
    floor["required_when"] = _label(raw_floor.get("required_when"), str(floor["required_when"]))
    floor["surface_streak_threshold"] = _positive_int(
        raw_floor.get("surface_streak_threshold"),
        int(floor["surface_streak_threshold"]),
    )
    floor["outcome_markers"] = _label_list(raw_floor.get("outcome_markers"), list(floor["outcome_markers"]))
    floor["surface_only_hints"] = _label_list(
        raw_floor.get("surface_only_hints"),
        list(floor["surface_only_hints"]),
    )
    floor["must_advance"] = _label_list(raw_floor.get("must_advance"), list(floor["must_advance"]))
    floor["avoid"] = _label_list(raw_floor.get("avoid"), list(floor["avoid"]))
    floor["if_unavailable"] = _label(raw_floor.get("if_unavailable"), str(floor["if_unavailable"]))
    profile["outcome_floor"] = floor
    if execution_profile_is_fine_grained(profile):
        _apply_fine_grained_contract(profile)
    return profile


def configure_execution_profile(
    profile: Any,
    *,
    turn_granularity: str | None = None,
    replan_after_completed_todos: int | None = None,
) -> dict[str, Any]:
    normalized = compact_execution_profile(profile)
    if turn_granularity is not None:
        normalized = execution_profile_with_turn_granularity(normalized, turn_granularity)
    requested_threshold = (
        replan_after_completed_todos
        if replan_after_completed_todos is not None
        else profile.get("replan_after_completed_todos") if isinstance(profile, dict) else None
    )
    configured_threshold = (
        normalize_completed_todo_replan_threshold(requested_threshold)
        if requested_threshold is not None
        else None
    )
    if configured_threshold is not None:
        normalized["replan_after_completed_todos"] = configured_threshold
    configured = compact_execution_profile(normalized)
    if configured_threshold is not None:
        # The default value still represents an explicit Goal override. Keep the
        # field so a machine default can change without silently changing this Goal.
        configured["replan_after_completed_todos"] = configured_threshold
    return configured


def apply_goal_execution_profile_change(
    goal: dict[str, Any],
    *,
    turn_granularity: str | None,
    replan_after_completed_todos: int | None,
    clear_replan_after_completed_todos: bool,
    replan_after_effective_turns: int | None = None,
    clear_replan_after_effective_turns: bool = False,
) -> None:
    if (
        clear_replan_after_completed_todos
        and replan_after_completed_todos is not None
    ):
        raise ValueError(
            "--clear-execution-replan-after-todos cannot be combined with "
            "--execution-replan-after-todos"
        )
    if replan_after_effective_turns is not None and (
        replan_after_completed_todos is not None or clear_replan_after_effective_turns
    ):
        raise ValueError("choose one review cadence override or clear operation")
    raw = goal.get("execution_profile")
    profile = dict(raw) if isinstance(raw, dict) else {}
    if (
        turn_granularity is None
        and replan_after_completed_todos is None
        and replan_after_effective_turns is None
        and not clear_replan_after_effective_turns
        and not (clear_replan_after_completed_todos and "replan_after_completed_todos" in profile)
    ):
        return
    if clear_replan_after_effective_turns or replan_after_completed_todos is not None:
        profile.pop("replan_after_effective_turns", None)
    if replan_after_effective_turns is not None:
        profile["replan_after_effective_turns"] = replan_after_effective_turns
        profile.pop("replan_after_completed_todos", None)
    if clear_replan_after_completed_todos:
        profile.pop("replan_after_completed_todos", None)
    goal["execution_profile"] = configure_execution_profile(
        profile,
        turn_granularity=turn_granularity,
        replan_after_completed_todos=replan_after_completed_todos,
    )


def normalize_turn_granularity(value: Any) -> str:
    candidate = str(value or TURN_GRANULARITY_STANDARD).strip().lower().replace("-", "_")
    if candidate not in TURN_GRANULARITY_CHOICES:
        raise ValueError(
            "turn_granularity must be one of: "
            + ", ".join(TURN_GRANULARITY_CHOICES)
        )
    return candidate


def execution_profile_turn_granularity(profile: dict[str, Any] | None) -> str:
    if not isinstance(profile, dict) or "turn_granularity" not in profile:
        return TURN_GRANULARITY_STANDARD
    return normalize_turn_granularity(profile.get("turn_granularity"))


def execution_profile_is_fine_grained(profile: dict[str, Any] | None) -> bool:
    return execution_profile_turn_granularity(profile) == TURN_GRANULARITY_FINE


def execution_profile_with_turn_granularity(
    profile: dict[str, Any] | None,
    turn_granularity: str,
) -> dict[str, Any]:
    normalized = compact_execution_profile(profile)
    mode = normalize_turn_granularity(turn_granularity)
    if mode == TURN_GRANULARITY_FINE:
        _apply_fine_grained_contract(normalized)
        return normalized
    was_fine = execution_profile_is_fine_grained(normalized)
    normalized.pop("turn_granularity", None)
    if was_fine:
        normalized.pop("planning_granularity", None)
        normalized.pop("turn_work_budget", None)
        normalized.pop("checkpoint_accounting", None)
        normalized["minimum_scale"] = DEFAULT_EXECUTION_PROFILE["minimum_scale"]
        normalized["degradation_policy"] = dict(
            DEFAULT_EXECUTION_PROFILE["degradation_policy"]
        )
    return normalized


def execution_profile_threshold(profile: dict[str, Any] | None) -> int:
    normalized = compact_execution_profile(profile)
    policy = normalized.get("degradation_policy") if isinstance(normalized.get("degradation_policy"), dict) else {}
    return _positive_int(
        policy.get("small_scale_streak_threshold"),
        int(DEFAULT_EXECUTION_PROFILE["degradation_policy"]["small_scale_streak_threshold"]),
    )


def execution_profile_outcome_floor(profile: dict[str, Any] | None) -> dict[str, Any]:
    normalized = compact_execution_profile(profile)
    floor = normalized.get("outcome_floor") if isinstance(normalized.get("outcome_floor"), dict) else {}
    return dict(floor)


def outcome_floor_threshold(profile: dict[str, Any] | None) -> int:
    floor = execution_profile_outcome_floor(profile)
    return _positive_int(
        floor.get("surface_streak_threshold"),
        int(DEFAULT_EXECUTION_PROFILE["outcome_floor"]["surface_streak_threshold"]),
    )


def execution_profile_summary(profile: dict[str, Any] | None) -> str:
    normalized = compact_execution_profile(profile)
    policy = normalized.get("degradation_policy") if isinstance(normalized.get("degradation_policy"), dict) else {}
    floor = normalized.get("outcome_floor") if isinstance(normalized.get("outcome_floor"), dict) else {}
    floor_suffix = ""
    outcome_markers = floor.get("outcome_markers") if isinstance(floor.get("outcome_markers"), list) else []
    surface_hints = floor.get("surface_only_hints") if isinstance(floor.get("surface_only_hints"), list) else []
    if outcome_markers or surface_hints:
        floor_suffix = (
            " "
            f"outcome_floor=threshold:{floor.get('surface_streak_threshold')},"
            f"markers:{','.join(outcome_markers)},"
            f"surface:{','.join(surface_hints)}"
        )
    turn_suffix = (
        " turn_granularity=fine planning_granularity=fine_checkpoint "
        "turn_work_budget=coherent_slice checkpoint_accounting=advancement_only"
        if execution_profile_is_fine_grained(normalized)
        else ""
    )
    return (
        f"cadence={normalized.get('cadence')} "
        f"minimum={normalized.get('minimum_scale')} "
        f"include={','.join(normalized.get('must_include') or [])} "
        f"spend_rule={normalized.get('spend_rule')} "
        f"small_streak_threshold={policy.get('small_scale_streak_threshold')}"
        f"{floor_suffix}"
        f"{turn_suffix}"
        + (
            f" replan_after_completed_todos={normalized['replan_after_completed_todos']}"
            if "replan_after_completed_todos" in normalized else ""
        )
        + (f" replan_after_effective_turns={normalized['replan_after_effective_turns']}"
           if "replan_after_effective_turns" in normalized else "")
    )
