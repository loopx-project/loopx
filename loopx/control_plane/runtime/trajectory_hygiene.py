from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from typing import Any

from ..work_items.delivery_outcome import MATERIAL_DELIVERY_OUTCOMES


TRAJECTORY_HYGIENE_SCHEMA_VERSION = "trajectory_hygiene_summary_v0"
CONTROLLER_CLASSIFICATION_PREFIXES = ("quota_",)
CONTROLLER_CLASSIFICATIONS = {
    "state_refreshed",
}
def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes"}


def _compact_chars(run: Mapping[str, Any]) -> int:
    return len(json.dumps(dict(run), ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def compact_history_event_channel(run: Mapping[str, Any]) -> str:
    """Classify one public-safe compact history row without reading run artifacts."""

    if isinstance(run.get("human_reward"), Mapping) or isinstance(run.get("operator_gate"), Mapping):
        return "human_decision"

    classification = str(run.get("classification") or "").strip().lower()
    if classification in CONTROLLER_CLASSIFICATIONS or classification.startswith(
        CONTROLLER_CLASSIFICATION_PREFIXES
    ):
        return "controller"

    if str(run.get("delivery_outcome") or "").strip() in MATERIAL_DELIVERY_OUTCOMES:
        return "outcome"
    return "task_event"


def _material_transition(run: Mapping[str, Any], channel: str) -> bool:
    if _truthy(run.get("material_change")):
        return True
    if channel == "human_decision":
        return True
    return str(run.get("delivery_outcome") or "").strip() in MATERIAL_DELIVERY_OUTCOMES


def build_trajectory_hygiene_summary(history: Mapping[str, Any]) -> dict[str, Any]:
    """Build training-hygiene proxies from compact history metadata only.

    It measures controller density and attribution gaps so
    maintainers can decide whether a separate learning projection is warranted.
    """

    runs = [run for run in history.get("runs") or [] if isinstance(run, Mapping)]
    channel_counts: Counter[str] = Counter()
    classification_counts: Counter[str] = Counter()
    controller_chars = 0
    total_chars = 0
    non_material_count = 0
    learning_candidate_count = 0
    learning_action_count = 0
    learning_outcome_count = 0
    decision_anchor_count = 0
    learning_actions: list[str] = []

    for run in runs:
        channel = compact_history_event_channel(run)
        channel_counts[channel] += 1
        classification = str(run.get("classification") or "unknown").strip() or "unknown"
        classification_counts[classification] += 1

        char_count = _compact_chars(run)
        total_chars += char_count
        if channel == "controller":
            controller_chars += char_count
        if not _material_transition(run, channel):
            non_material_count += 1

        if channel == "controller":
            continue

        learning_candidate_count += 1
        action = " ".join(str(run.get("recommended_action") or "").split())
        outcome = str(run.get("delivery_outcome") or "").strip()
        if action:
            learning_action_count += 1
            learning_actions.append(action)
        if outcome:
            learning_outcome_count += 1
        if action and outcome:
            decision_anchor_count += 1

    action_counts = Counter(learning_actions)
    duplicate_action_count = sum(max(0, count - 1) for count in action_counts.values())
    total_count = len(runs)
    controller_count = channel_counts["controller"]

    return {
        "ok": True,
        "schema_version": TRAJECTORY_HYGIENE_SCHEMA_VERSION,
        "goal_filter": history.get("goal_filter"),
        "sample": {
            "compact_history_row_count": total_count,
            "goal_count": history.get("goal_count"),
            "source": "public_safe_compact_run_index",
        },
        "channel_counts": dict(sorted(channel_counts.items())),
        "classification_counts": dict(classification_counts.most_common()),
        "metrics": {
            "controller_event_ratio": _ratio(controller_count, total_count),
            "compact_controller_char_ratio": _ratio(controller_chars, total_chars),
            "non_material_event_ratio": _ratio(non_material_count, total_count),
            "learning_candidate_count": learning_candidate_count,
            "learning_action_coverage": _ratio(learning_action_count, learning_candidate_count),
            "learning_outcome_coverage": _ratio(learning_outcome_count, learning_candidate_count),
            "decision_anchor_coverage": _ratio(decision_anchor_count, learning_candidate_count),
            "duplicate_learning_action_ratio": _ratio(
                duplicate_action_count,
                learning_action_count,
            ),
        },
        "training_boundary": {
            "raw_session_read": False,
            "raw_trajectory_read": False,
            "run_artifact_read": False,
            "compact_index_only": True,
            "seed_model_training_eligible": False,
            "reason": (
                "compact history is an audit baseline, not a learning trajectory; "
                "export model-visible task turns separately and link controller events by stable ids"
            ),
        },
    }
