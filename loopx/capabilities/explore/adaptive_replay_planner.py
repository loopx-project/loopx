"""Domain-neutral evidence model for adaptive ReplayPoint placement."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Sequence

from .replay_runtime import ReplayFidelity, fidelity_meets
from .trace_runtime import TraceLog, require_nonempty_text


def _nonnegative(value: float, field_name: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a finite non-negative number")
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be a finite non-negative number"
        ) from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise ValueError(f"{field_name} must be a finite non-negative number")
    return normalized


@dataclass(frozen=True)
class ReplayPositionCost:
    """Public-safe Adapter estimate for one addressable trace boundary."""

    after_event_id: str | None
    next_sequence: int
    achievable_fidelity: ReplayFidelity
    capture_minutes: float
    restore_minutes: float

    def __post_init__(self) -> None:
        if self.after_event_id is not None:
            object.__setattr__(
                self,
                "after_event_id",
                require_nonempty_text(self.after_event_id, "after_event_id"),
            )
        if int(self.next_sequence) < 0:
            raise ValueError("next_sequence must be non-negative")
        object.__setattr__(self, "next_sequence", int(self.next_sequence))
        object.__setattr__(
            self,
            "achievable_fidelity",
            ReplayFidelity(self.achievable_fidelity),
        )
        if self.achievable_fidelity == ReplayFidelity.NON_REPLAYABLE:
            raise ValueError("ReplayPositionCost must be replayable")
        object.__setattr__(
            self,
            "capture_minutes",
            _nonnegative(self.capture_minutes, "capture_minutes"),
        )
        object.__setattr__(
            self,
            "restore_minutes",
            _nonnegative(self.restore_minutes, "restore_minutes"),
        )


@dataclass(frozen=True)
class FailureReplayObservation:
    """Observed failure locality and baseline replay cost."""

    first_effect_sequence: int
    terminal_sequence: int
    full_replay_minutes: float
    minimum_fidelity: ReplayFidelity
    weight: float = 1.0

    def __post_init__(self) -> None:
        first = int(self.first_effect_sequence)
        terminal = int(self.terminal_sequence)
        if first < 0:
            raise ValueError("first_effect_sequence must be non-negative")
        if terminal <= first:
            raise ValueError("terminal_sequence must be after first_effect_sequence")
        object.__setattr__(self, "first_effect_sequence", first)
        object.__setattr__(self, "terminal_sequence", terminal)
        object.__setattr__(
            self,
            "full_replay_minutes",
            _nonnegative(self.full_replay_minutes, "full_replay_minutes"),
        )
        object.__setattr__(
            self,
            "minimum_fidelity",
            ReplayFidelity(self.minimum_fidelity),
        )
        if self.minimum_fidelity == ReplayFidelity.NON_REPLAYABLE:
            raise ValueError("failure observations require replayable fidelity")
        normalized_weight = float(self.weight)
        if normalized_weight <= 0:
            raise ValueError("weight must be positive")
        object.__setattr__(self, "weight", normalized_weight)


@dataclass(frozen=True)
class AdaptiveReplayPointProposal:
    after_event_id: str | None
    next_sequence: int
    achievable_fidelity: ReplayFidelity
    score_minutes: float
    expected_saved_minutes: float
    expected_suffix_events: float
    eligible_observation_weight: float
    capture_minutes: float
    restore_minutes: float

    def to_record(self) -> dict[str, Any]:
        return {
            "after_event_id": self.after_event_id,
            "next_sequence": self.next_sequence,
            "achievable_fidelity": self.achievable_fidelity.value,
            "score_minutes": self.score_minutes,
            "expected_saved_minutes": self.expected_saved_minutes,
            "expected_suffix_events": self.expected_suffix_events,
            "eligible_observation_weight": self.eligible_observation_weight,
            "capture_minutes": self.capture_minutes,
            "restore_minutes": self.restore_minutes,
        }


def _validate_position(trace: TraceLog, candidate: ReplayPositionCost) -> None:
    if candidate.next_sequence > len(trace.events):
        raise ValueError("candidate next_sequence exceeds the trace head")
    if candidate.next_sequence == 0:
        if candidate.after_event_id is not None:
            raise ValueError("origin candidate must not name after_event_id")
        return
    if candidate.after_event_id is None:
        raise ValueError("non-origin candidate requires after_event_id")
    event = trace.event(candidate.after_event_id)
    if event.sequence + 1 != candidate.next_sequence:
        raise ValueError(
            "candidate after_event_id does not match next_sequence boundary"
        )


def plan_adaptive_replay_points(
    trace: TraceLog,
    candidates: Sequence[ReplayPositionCost],
    observations: Sequence[FailureReplayObservation],
    *,
    max_proposals: int = 3,
    minimum_score_minutes: float = 0.0,
) -> tuple[AdaptiveReplayPointProposal, ...]:
    """Rank replay boundaries without capturing Adapter-owned state."""

    normalized_candidates = tuple(candidates)
    normalized_observations = tuple(observations)
    if not normalized_candidates:
        return ()
    if not normalized_observations:
        return ()
    if not all(
        isinstance(candidate, ReplayPositionCost) for candidate in normalized_candidates
    ):
        raise TypeError("candidates must contain ReplayPositionCost values")
    if not all(
        isinstance(observation, FailureReplayObservation)
        for observation in normalized_observations
    ):
        raise TypeError("observations must contain FailureReplayObservation values")
    if int(max_proposals) <= 0:
        raise ValueError("max_proposals must be positive")
    threshold = float(minimum_score_minutes)
    for candidate in normalized_candidates:
        _validate_position(trace, candidate)
    for observation in normalized_observations:
        if observation.terminal_sequence > len(trace.events):
            raise ValueError("observation terminal_sequence exceeds the trace head")

    proposals: list[AdaptiveReplayPointProposal] = []
    for candidate in normalized_candidates:
        eligible_weight = 0.0
        expected_saved = 0.0
        weighted_suffix_events = 0.0
        for observation in normalized_observations:
            if candidate.next_sequence > observation.first_effect_sequence:
                continue
            if not fidelity_meets(
                candidate.achievable_fidelity,
                observation.minimum_fidelity,
            ):
                continue
            eligible_weight += observation.weight
            reused_fraction = candidate.next_sequence / observation.terminal_sequence
            saved_minutes = max(
                0.0,
                observation.full_replay_minutes * reused_fraction
                - candidate.restore_minutes,
            )
            expected_saved += observation.weight * saved_minutes
            weighted_suffix_events += observation.weight * (
                observation.terminal_sequence - candidate.next_sequence
            )
        if eligible_weight == 0:
            continue
        score = expected_saved - candidate.capture_minutes
        if score < threshold:
            continue
        proposals.append(
            AdaptiveReplayPointProposal(
                after_event_id=candidate.after_event_id,
                next_sequence=candidate.next_sequence,
                achievable_fidelity=candidate.achievable_fidelity,
                score_minutes=score,
                expected_saved_minutes=expected_saved,
                expected_suffix_events=(weighted_suffix_events / eligible_weight),
                eligible_observation_weight=eligible_weight,
                capture_minutes=candidate.capture_minutes,
                restore_minutes=candidate.restore_minutes,
            )
        )
    proposals.sort(
        key=lambda proposal: (
            -proposal.score_minutes,
            -proposal.eligible_observation_weight,
            -proposal.next_sequence,
            proposal.after_event_id or "",
        )
    )
    return tuple(proposals[: int(max_proposals)])
