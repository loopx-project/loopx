from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from .progress_result import (
    PROGRESS_OBSERVATION_SCHEMA_VERSION,
    ProgressResultClass,
    normalize_progress_identifier,
)


class DeliveryOutcome(str, Enum):
    """Structured machine signal for what a delivery run actually advanced."""

    SURFACE_ONLY = "surface_only"
    OUTCOME_GAP = "outcome_gap"
    OUTCOME_PROGRESS = "outcome_progress"
    PRIMARY_GOAL_OUTCOME = "primary_goal_outcome"


class DeliveryTurnKind(str, Enum):
    """Compact public-safe classification for why a delivery turn counts."""

    CONTRACT_ONLY_PREPARATION = "contract_only_preparation"
    COMPACT_EVIDENCE = "compact_evidence"
    BLOCKER_WRITEBACK = "blocker_writeback"
    PRODUCT_PATH_EXECUTION = "product_path_execution"
    OUTCOME_GAP = "outcome_gap"
    UNKNOWN = "unknown"


DELIVERY_OUTCOME_CHOICES = tuple(outcome.value for outcome in DeliveryOutcome)
DELIVERY_TURN_KIND_CHOICES = tuple(kind.value for kind in DeliveryTurnKind)

MATERIAL_DELIVERY_OUTCOMES = frozenset(
    {
        DeliveryOutcome.OUTCOME_GAP,
        DeliveryOutcome.OUTCOME_PROGRESS,
        DeliveryOutcome.PRIMARY_GOAL_OUTCOME,
    }
)
ACCOUNTABLE_DELIVERY_OUTCOMES = frozenset(
    {
        DeliveryOutcome.OUTCOME_PROGRESS,
        DeliveryOutcome.PRIMARY_GOAL_OUTCOME,
    }
)
PROGRESS_DELIVERY_OUTCOMES = ACCOUNTABLE_DELIVERY_OUTCOMES


def qualifies_turn_scoped_blocker_settlement(
    delivery_outcome: Any,
    progress_observation: Mapping[str, Any] | None,
    *,
    work_item_id: str | None = None,
    replan_obligation_id: str | None = None,
) -> bool:
    """Return whether an outcome gap is a typed, attributable blocker receipt.

    ``outcome_gap`` remains outside the progress outcomes: it can settle one
    exact Turn only when the same writeback carries a blocked observation with
    a stable blocker, evidence, and matching Todo or replan-obligation identity.
    """

    if (
        normalize_delivery_outcome(delivery_outcome) != DeliveryOutcome.OUTCOME_GAP
        or not isinstance(progress_observation, Mapping)
        or progress_observation.get("schema_version")
        != PROGRESS_OBSERVATION_SCHEMA_VERSION
        or not isinstance(progress_observation.get("evidence_ids"), list)
        or bool(work_item_id) == bool(replan_obligation_id)
    ):
        return False
    observation = dict(progress_observation)
    normalized_work_item_id = normalize_progress_identifier(
        work_item_id or replan_obligation_id
    )
    if (
        normalized_work_item_id is None
        or observation.get("result_class") != ProgressResultClass.BLOCKED.value
        or normalize_progress_identifier(observation.get("blocker_id")) is None
        or normalize_progress_identifier(observation.get("work_item_id"))
        != normalized_work_item_id
    ):
        return False
    evidence_ids = observation.get("evidence_ids")
    return isinstance(evidence_ids, list) and bool(evidence_ids) and all(
        normalize_progress_identifier(evidence_id) is not None
        for evidence_id in evidence_ids
    )


def qualifies_turn_scoped_settlement(
    delivery_outcome: Any,
    progress_observation: Mapping[str, Any] | None,
    *,
    work_item_id: str | None = None,
    replan_obligation_id: str | None = None,
) -> bool:
    """Return whether one delivery record may satisfy a Turn settlement."""

    normalized = normalize_delivery_outcome(delivery_outcome)
    return normalized in ACCOUNTABLE_DELIVERY_OUTCOMES or (
        qualifies_turn_scoped_blocker_settlement(
            normalized,
            progress_observation,
            work_item_id=work_item_id,
            replan_obligation_id=replan_obligation_id,
        )
    )


TURN_SCOPED_SETTLEMENT_REQUIREMENT = (
    "turn-scoped refresh-state requires a progress outcome or a typed blocked "
    "outcome_gap settlement"
)
TURN_SCOPED_SETTLEMENT_GAP_FALLBACK = "the settlement names no typed outcome"


def explain_turn_scoped_settlement_gap(
    delivery_outcome: Any,
    progress_observation: Mapping[str, Any] | None,
    *,
    work_item_id: str | None = None,
    replan_obligation_id: str | None = None,
) -> str | None:
    """Name the missing typed input that keeps a Turn from settling.

    Refusing a settlement without naming the absent argument leaves the writer to
    guess, which is the failure mode this diagnosis removes. The caller keeps
    refusing; this helper only reports which input is missing or wrong, and
    returns ``None`` for a settlement that already qualifies.
    """

    if qualifies_turn_scoped_settlement(
        delivery_outcome,
        progress_observation,
        work_item_id=work_item_id,
        replan_obligation_id=replan_obligation_id,
    ):
        return None
    normalized = normalize_delivery_outcome(delivery_outcome)
    progress_choices = " or ".join(
        item.value for item in sorted(ACCOUNTABLE_DELIVERY_OUTCOMES, key=lambda item: item.value)
    )
    if normalized is None:
        return (
            "--delivery-outcome is required: name "
            f"{progress_choices}, or outcome_gap together with "
            "--progress-result-class blocked"
        )
    if normalized not in ACCOUNTABLE_DELIVERY_OUTCOMES:
        if normalized is not DeliveryOutcome.OUTCOME_GAP:
            return (
                f"--delivery-outcome {normalized.value} cannot settle a Turn: name "
                f"{progress_choices}, or outcome_gap together with "
                "--progress-result-class blocked"
            )
        observation = (
            progress_observation if isinstance(progress_observation, Mapping) else None
        )
        if (
            observation is None
            or observation.get("schema_version") != PROGRESS_OBSERVATION_SCHEMA_VERSION
        ):
            return (
                "--delivery-outcome outcome_gap also requires a typed blocked "
                "observation: send --progress-result-class blocked with "
                "--progress-blocker-id and --progress-evidence-id"
            )
        result_class = observation.get("result_class")
        if result_class != ProgressResultClass.BLOCKED.value:
            return (
                "--delivery-outcome outcome_gap requires --progress-result-class "
                f"blocked; result_class={result_class or 'missing'} cannot settle a Turn"
            )
        if normalize_progress_identifier(observation.get("blocker_id")) is None:
            return (
                "--delivery-outcome outcome_gap with a blocked observation also "
                "requires a stable --progress-blocker-id"
            )
        evidence_ids = observation.get("evidence_ids")
        if (
            not isinstance(evidence_ids, list)
            or not evidence_ids
            or not all(
                normalize_progress_identifier(evidence_id) is not None
                for evidence_id in evidence_ids
            )
        ):
            return (
                "--delivery-outcome outcome_gap with a blocked observation also "
                "requires --progress-evidence-id"
            )
        if bool(work_item_id) == bool(replan_obligation_id):
            return (
                "name exactly one settlement identity: --todo-id or "
                "--replan-obligation-id"
            )
        expected_work_item = normalize_progress_identifier(
            work_item_id or replan_obligation_id
        )
        if normalize_progress_identifier(observation.get("work_item_id")) != expected_work_item:
            return (
                "the blocked observation must carry the settlement work item; "
                f"work_item_id={observation.get('work_item_id') or 'missing'}"
            )
    return TURN_SCOPED_SETTLEMENT_GAP_FALLBACK


def normalize_delivery_outcome(value: Any) -> DeliveryOutcome | None:
    if isinstance(value, DeliveryOutcome):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return DeliveryOutcome(text)
    except ValueError:
        return None


def require_delivery_outcome(value: Any) -> DeliveryOutcome:
    outcome = normalize_delivery_outcome(value)
    if outcome is None:
        raise ValueError("delivery_outcome must be one of: " + ", ".join(DELIVERY_OUTCOME_CHOICES))
    return outcome


def normalize_delivery_turn_kind(value: Any) -> DeliveryTurnKind | None:
    if isinstance(value, DeliveryTurnKind):
        return value
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return DeliveryTurnKind(text)
    except ValueError:
        return None


def require_delivery_turn_kind(value: Any) -> DeliveryTurnKind:
    kind = normalize_delivery_turn_kind(value)
    if kind is None:
        raise ValueError("delivery_turn_kind must be one of: " + ", ".join(DELIVERY_TURN_KIND_CHOICES))
    return kind
