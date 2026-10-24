from __future__ import annotations

from typing import Any, Mapping, Sequence


SPECULATIVE_SCHEDULER_SCHEMA_VERSION = "loopx_explore_speculative_scheduler_v0"
INDEPENDENT_LANE_SCHEDULER_SCHEMA_VERSION = "loopx_explore_independent_lane_scheduler_v0"
BRANCH_PLAN_AB_RESULT_SCHEMA_VERSION = "loopx_explore_branch_plan_ab_result_v0"
ACCEPT_REJECT_EVENT_SCHEMA_VERSION = "loopx_explore_accept_reject_event_v0"
LOAD_PROFILE_SCHEMA_VERSION = "loopx_explore_load_profile_v0"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def step_throughput(batch_size: int, *, load_factor: float) -> float:
    """Small load-aware throughput curve for speculative branch verification."""

    batch = max(1, int(batch_size))
    load = _clamp(float(load_factor), 0.0, 1.0)
    return 1.0 / (1.0 + load * max(0, batch - 1) ** 1.18)


def calibrate_load_factor(
    profile: Mapping[str, Any] | None,
    *,
    fallback_load_factor: float,
) -> dict[str, Any]:
    """Estimate verification load from observed parallel branch timings.

    Sparse observations are shrunk toward the configured fallback so a single
    unusually clean run does not make the scheduler over-issue work.
    """

    fallback = _clamp(float(fallback_load_factor), 0.0, 1.0)
    if not isinstance(profile, Mapping):
        return {
            "schema_version": LOAD_PROFILE_SCHEMA_VERSION,
            "source": "fallback",
            "load_factor": round(fallback, 4),
            "fallback_load_factor": round(fallback, 4),
        }
    wall = float(profile.get("parallel_wall_minutes") or profile.get("wall_minutes") or 0.0)
    max_branch = float(profile.get("max_branch_minutes") or 0.0)
    branch_count = max(1, int(profile.get("branch_count") or 1))
    if branch_count <= 1 or wall <= 0 or max_branch <= 0:
        return {
            "schema_version": LOAD_PROFILE_SCHEMA_VERSION,
            "source": "fallback_insufficient_profile",
            "branch_count": branch_count,
            "load_factor": round(fallback, 4),
            "fallback_load_factor": round(fallback, 4),
        }
    overhead_ratio = max(0.0, wall / max_branch - 1.0)
    measured = _clamp(overhead_ratio / max(1.0, (branch_count - 1) ** 1.18), 0.0, 1.0)
    measurement_weight = min(0.45, max(0.15, branch_count / 10.0))
    load_factor = (1.0 - measurement_weight) * fallback + measurement_weight * measured
    return {
        "schema_version": LOAD_PROFILE_SCHEMA_VERSION,
        "source": str(profile.get("source") or "observed_parallel_profile"),
        "branch_count": branch_count,
        "parallel_wall_minutes": round(wall, 4),
        "max_branch_minutes": round(max_branch, 4),
        "parallel_overhead_ratio": round(overhead_ratio, 4),
        "measured_load_factor": round(measured, 4),
        "measurement_weight": round(measurement_weight, 4),
        "load_factor": round(load_factor, 4),
        "fallback_load_factor": round(fallback, 4),
    }


def normalize_dependency_ids(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [part.strip() for part in value.replace(";", ",").split(",")]
        return [part for part in parts if part]
    if isinstance(value, Sequence):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def build_accept_reject_event(
    *,
    lane: str,
    event_type: str,
    todo_id: str,
    reason: str,
    verification_wave: int = 0,
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    event = {
        "schema_version": ACCEPT_REJECT_EVENT_SCHEMA_VERSION,
        "lane": lane,
        "event_type": event_type,
        "todo_id": todo_id,
        "verification_wave": int(verification_wave),
        "reason": reason,
    }
    if payload:
        event.update(dict(payload))
    return event


def partition_invalidated_successors(
    branches: Sequence[Mapping[str, Any]],
    *,
    selected_ids: Sequence[str] | None = None,
    accepted_ids: Sequence[str] | None = None,
    rejected_ids: Sequence[str] | None = None,
    lane: str = "branch_plan",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    selected = set(str(item) for item in (selected_ids or []))
    accepted = set(str(item) for item in (accepted_ids or []))
    rejected = set(str(item) for item in (rejected_ids or []))
    if not selected:
        selected = {str(branch.get("todo_id") or "") for branch in branches if branch.get("todo_id")}
    if not accepted:
        accepted = set(selected)

    valid: list[dict[str, Any]] = []
    invalidated: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    for branch in branches:
        todo_id = str(branch.get("todo_id") or "")
        deps = normalize_dependency_ids(
            branch.get("depends_on")
            or branch.get("dependency_todo_ids")
            or branch.get("blocked_by_todo_ids")
        )
        missing = [dep for dep in deps if dep not in selected]
        failed = [dep for dep in deps if dep in rejected]
        not_yet_accepted = [dep for dep in deps if dep in selected and dep not in accepted]
        if missing or failed or not_yet_accepted:
            if missing:
                reason = "dependency_not_selected:" + ",".join(missing)
            elif failed:
                reason = "dependency_rejected:" + ",".join(failed)
            else:
                reason = "dependency_not_accepted:" + ",".join(not_yet_accepted)
            invalid_branch = {
                **dict(branch),
                "selection_status": "invalidated_dependency",
                "invalidation_reason": reason,
            }
            invalidated.append(invalid_branch)
            events.append(
                build_accept_reject_event(
                    lane=lane,
                    event_type="invalidated",
                    todo_id=todo_id,
                    reason=reason,
                    payload={"depends_on": deps},
                )
            )
        else:
            valid.append(dict(branch))
    return valid, invalidated, events


def conditional_branch_confidence(
    raw_confidence: float,
    *,
    previous_confidence: float,
) -> float:
    """Blend a branch's confidence with the preceding prefix confidence.

    NOTE: this smoothing is a loopx heuristic, not a DSpark mechanism. In the
    actual DSpark paper (arXiv:2607.05147) "semi-autoregressive" refers to the
    Markov/RNN head that conditions each drafted token on the previously
    sampled token; DSpark's confidence head predicts independent per-step
    acceptance probabilities and does not smooth them across positions.
    Kept for the serial todo-branch scheduler where later branches loosely
    depend on the preceding accepted prefix.
    """

    raw = _clamp(float(raw_confidence), 0.0, 1.0)
    previous = _clamp(float(previous_confidence), 0.0, 1.0)
    return round(0.82 * raw + 0.18 * previous, 3)


def _expected_evidence_units(candidate: Mapping[str, Any]) -> float:
    # Zero is a known estimate; only a missing estimate uses the prior.
    value = candidate.get("expected_evidence_units")
    return 1.0 if value is None else float(value)


def prefix_metrics(
    candidates: Sequence[Mapping[str, Any]],
    *,
    prefix_length: int,
    load_factor: float,
) -> dict[str, Any]:
    limit = max(0, min(len(candidates), int(prefix_length or 0)))
    if limit == 0:
        return {
            "prefix_length": 0,
            "expected_evidence": 0.0,
            "theta": 0.0,
            "step_throughput": 0.0,
            "prefix_survival": 0.0,
        }
    survival = 1.0
    expected_evidence = 0.0
    previous_confidence = 1.0
    for candidate in candidates[:limit]:
        confidence = conditional_branch_confidence(
            float(candidate.get("confidence") or 0.05),
            previous_confidence=previous_confidence,
        )
        previous_confidence = confidence
        survival *= confidence
        expected_evidence += survival * _expected_evidence_units(candidate)
    throughput = step_throughput(limit, load_factor=load_factor)
    theta = expected_evidence * throughput
    return {
        "prefix_length": limit,
        "expected_evidence": round(expected_evidence, 3),
        "theta": round(theta, 3),
        "step_throughput": round(throughput, 3),
        "prefix_survival": round(survival, 3),
    }


def schedule_confidence_prefix(
    candidates: Sequence[Mapping[str, Any]],
    *,
    max_width: int,
    max_branch_width: int,
    load_factor: float,
) -> dict[str, Any]:
    """Choose a verification prefix for serially dependent exploration branches.

    Loosely inspired by DSpark (arXiv:2607.05147) but NOT a transcription of
    it: real DSpark truncates a draft block at the first per-step confidence
    below a fixed threshold, and uses the cumulative product of confidences
    only for calibration diagnostics. This scheduler instead maximizes
    theta(k) = prefix-survival-weighted evidence x a throughput curve -- a
    loopx-specific model that assumes branch k is worthless unless branches
    1..k-1 all succeed. Only apply it where that serial-chain assumption
    holds (e.g. dependent todo branches); for independent parallel worker
    lanes use ``schedule_independent_lanes``.
    """

    budget_limit = max(1, min(max_branch_width, int(max_width or 1)))
    safe_candidates = list(candidates[:budget_limit])
    survival = 1.0
    expected_evidence = 0.0
    best_theta = 0.0
    best_budget = 1 if safe_candidates else 0
    previous_confidence = 1.0
    steps: list[dict[str, Any]] = []
    for index, candidate in enumerate(safe_candidates, 1):
        raw_confidence = _clamp(float(candidate.get("confidence") or 0.05), 0.0, 1.0)
        confidence = conditional_branch_confidence(
            raw_confidence,
            previous_confidence=previous_confidence,
        )
        previous_confidence = confidence
        survival *= confidence
        expected_units = _expected_evidence_units(candidate)
        expected_evidence += survival * expected_units
        throughput = step_throughput(index, load_factor=load_factor)
        theta = expected_evidence * throughput
        steps.append(
            {
                "prefix_length": index,
                "todo_id": candidate.get("todo_id"),
                "raw_confidence": round(raw_confidence, 3),
                "conditional_confidence": confidence,
                "prefix_survival": round(survival, 3),
                "expected_evidence_units": round(expected_units, 3),
                "batch_size": index,
                "step_throughput": round(throughput, 3),
                "expected_evidence": round(expected_evidence, 3),
                "theta": round(theta, 3),
            }
        )
        if theta > best_theta:
            best_theta = theta
            best_budget = index

    baseline = prefix_metrics(safe_candidates, prefix_length=1, load_factor=load_factor)
    selected = prefix_metrics(safe_candidates, prefix_length=best_budget, load_factor=load_factor)
    baseline_theta = max(0.001, float(baseline.get("theta") or 0.0))
    return {
        "schema_version": SPECULATIVE_SCHEDULER_SCHEMA_VERSION,
        "strategy": "dspark_confidence_scheduled_prefix",
        "load_factor": round(_clamp(float(load_factor), 0.0, 1.0), 3),
        "max_width": budget_limit,
        "selected_prefix_length": best_budget,
        "baseline_theta": baseline.get("theta"),
        "best_theta": round(best_theta, 3),
        "estimated_speedup_vs_baseline": round(best_theta / baseline_theta, 3),
        "steps": steps,
        "ab_comparison": {
            "baseline_serial": baseline,
            "dspark_selected": selected,
        },
        "source": {
            "paper": "loosely inspired by DSpark (arXiv:2607.05147); the theta = survival x throughput optimizer is loopx-specific and does not appear in DSpark, which truncates at the first below-threshold per-step confidence",
            "mapping": "draft token -> exploration todo branch; target verification -> bounded experiment execution",
            "applicability": "serially dependent branch chains only; use schedule_independent_lanes for parallel worker lanes",
        },
    }


def confident_bundle_prefix_length(
    confidences: Sequence[float],
    *,
    threshold: float,
    max_length: int,
) -> int:
    """DSpark-faithful bundle sizing: cut at the first below-threshold step.

    This is the direct analog of DSpark's ``_confident_prefix_length``
    (arXiv:2607.05147, DeepSpec reference implementation): a lane's serial
    todo bundle plays the role of the semi-autoregressive draft block, the
    per-todo acceptance confidence plays the role of the per-step confidence
    head output, and the bundle is truncated at the first todo whose
    confidence drops below ``threshold``. ``threshold <= 0`` submits the whole
    block, exactly like DSpark. Callers layer harness-specific costs (serial
    wall-clock straggler guards) on top -- an honest divergence from DSpark,
    where drafting is nearly free and only verification is paid.
    """

    limit = max(0, int(max_length))
    cutoff = min(limit, len(confidences))
    if float(threshold) <= 0.0:
        return cutoff
    for index in range(cutoff):
        if _clamp(float(confidences[index] or 0.0), 0.0, 1.0) < float(threshold):
            return index
    return cutoff


def schedule_independent_lanes(
    candidates: Sequence[Mapping[str, Any]],
    *,
    max_width: int,
    load_factor: float,
    min_marginal_theta: float = 0.0,
    opportunistic_utilization_floor: float = 0.0,
    opportunistic_lane_value_floor: float = 0.0,
    opportunistic_lane_value_ratio: float = 0.0,
) -> dict[str, Any]:
    """Choose a lane count for independent parallel worker lanes.

    ``schedule_confidence_prefix`` models a serially dependent speculative
    chain: survival is a running *product* of conditional confidences, so a
    later branch only counts if every earlier branch survives. Worker lanes
    are independent worker processes -- one lane failing does not invalidate
    its neighbours -- so expected evidence must be *additive*:

        theta(k) = [sum_{i<=k} confidence_i * eeu_i] * step_throughput(k)

    The only cross-lane cost is measured parallel interference, expressed by
    ``load_factor`` through the same throughput curve. The core width is
    argmax theta. Profiles may then enable an opportunistic expansion floor:
    extra lanes are admitted only while each lane's independent value clears a
    calibrated floor. That preserves "worker width is a ceiling" semantics
    without regressing to blind fill.
    """

    budget_limit = max(1, int(max_width or 1))
    safe_candidates = list(candidates[:budget_limit])
    load = _clamp(float(load_factor), 0.0, 1.0)
    expected_evidence = 0.0
    best_theta = 0.0
    best_budget = 1 if safe_candidates else 0
    previous_theta = 0.0
    steps: list[dict[str, Any]] = []
    for index, candidate in enumerate(safe_candidates, 1):
        confidence = _clamp(float(candidate.get("confidence") or 0.05), 0.0, 1.0)
        expected_units = _expected_evidence_units(candidate)
        expected_evidence += confidence * expected_units
        throughput = step_throughput(index, load_factor=load)
        lane_value = confidence * expected_units * throughput
        theta = expected_evidence * throughput
        marginal_theta = theta - previous_theta
        previous_theta = theta
        steps.append(
            {
                "lane_index": index,
                "todo_id": candidate.get("todo_id"),
                "confidence": round(confidence, 3),
                "expected_evidence_units": round(expected_units, 3),
                "expected_evidence": round(expected_evidence, 3),
                "lane_value": round(lane_value, 4),
                "step_throughput": round(throughput, 3),
                "theta": round(theta, 3),
                "marginal_theta": round(marginal_theta, 4),
            }
        )
        if theta > best_theta:
            best_theta = theta
            best_budget = index
    queue_exhausted = len(safe_candidates) < budget_limit
    core_budget = best_budget
    target_utilization = _clamp(float(opportunistic_utilization_floor or 0.0), 0.0, 1.0)
    target_budget = min(
        len(safe_candidates),
        budget_limit,
        int((budget_limit * target_utilization) + 0.999),
    )
    first_lane_value = float(steps[0]["lane_value"]) if steps else 0.0
    value_floor = max(
        0.0,
        float(opportunistic_lane_value_floor or 0.0),
        first_lane_value * _clamp(float(opportunistic_lane_value_ratio or 0.0), 0.0, 1.0),
    )
    opportunistic_budget = core_budget
    if target_budget > core_budget and value_floor > 0.0:
        for step in steps[core_budget:target_budget]:
            if float(step.get("lane_value") or 0.0) < value_floor:
                break
            opportunistic_budget = int(step.get("lane_index") or opportunistic_budget)
    best_budget = max(core_budget, opportunistic_budget)
    refusals = [
        {
            "todo_id": step.get("todo_id"),
            "lane_index": step.get("lane_index"),
            "reason": (
                "opportunistic_lane_value_below_floor"
                if int(step.get("lane_index") or 0) <= target_budget
                and float(step.get("lane_value") or 0.0) < value_floor
                else "interference_marginal_theta_nonpositive"
                if float(step.get("marginal_theta") or 0.0) <= min_marginal_theta
                else "beyond_opportunistic_target"
                if int(step.get("lane_index") or 0) > target_budget
                else "beyond_theta_peak"
            ),
            "marginal_theta": step.get("marginal_theta"),
            "lane_value": step.get("lane_value"),
        }
        for step in steps[best_budget:]
    ]
    single_lane_theta = float(steps[0]["theta"]) if steps else 0.0
    return {
        "schema_version": INDEPENDENT_LANE_SCHEDULER_SCHEMA_VERSION,
        "strategy": "independent_lane_admission",
        "load_factor": round(load, 4),
        "max_width": budget_limit,
        "min_marginal_theta": round(float(min_marginal_theta), 4),
        "selected_prefix_length": best_budget,
        "core_selected_prefix_length": core_budget,
        "baseline_theta": round(single_lane_theta, 3),
        "best_theta": round(best_theta, 3),
        "estimated_speedup_vs_baseline": round(best_theta / max(0.001, single_lane_theta), 3),
        "steps": steps,
        "admission_audit": {
            "queue_exhausted": queue_exhausted,
            "admitted_lane_count": best_budget,
            "core_lane_count": core_budget,
            "opportunistic_admitted_count": max(0, best_budget - core_budget),
            "opportunistic_target_lane_count": target_budget,
            "opportunistic_utilization_floor": round(target_utilization, 3),
            "opportunistic_lane_value_floor": round(value_floor, 4),
            "opportunistic_lane_value_ratio": round(float(opportunistic_lane_value_ratio or 0.0), 3),
            "refusals": refusals,
        },
        "source": {
            "model": "independent parallel lanes; additive expected evidence, no cross-lane survival product",
            "interference": "load_factor from calibrate_load_factor observations, not a hardcoded prior",
            "opportunistic_expansion": "positive-yield lanes may extend beyond theta peak up to a utilization floor; never blind fill",
        },
    }


def build_branch_plan_ab_result(
    *,
    scheduler: Mapping[str, Any],
    selected: Sequence[Mapping[str, Any]],
    rejected: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    comparison = scheduler.get("ab_comparison") if isinstance(scheduler.get("ab_comparison"), Mapping) else {}
    baseline = comparison.get("baseline_serial") if isinstance(comparison.get("baseline_serial"), Mapping) else {}
    dspark = comparison.get("dspark_selected") if isinstance(comparison.get("dspark_selected"), Mapping) else {}
    baseline_theta = max(0.001, float(baseline.get("theta") or 0.0))
    selected_expected_evidence = round(
        sum(float(item.get("expected_evidence_units") or 0.0) for item in selected),
        3,
    )
    hazard_rejections = [
        item
        for item in rejected
        if str(item.get("selection_status") or "") in {"rejected_hazard", "blocked_claimed_by_other"}
    ]
    return {
        "schema_version": BRANCH_PLAN_AB_RESULT_SCHEMA_VERSION,
        "metric": "estimated_evidence_throughput_theta",
        "baseline_serial_theta": round(baseline_theta, 3),
        "dspark_selected_theta": dspark.get("theta"),
        "estimated_speedup_vs_baseline": round(float(dspark.get("theta") or 0.0) / baseline_theta, 3),
        "selected_expected_evidence_units": selected_expected_evidence,
        "predicted_prefix_length": scheduler.get("selected_prefix_length"),
        "actual_selected_count_after_hazards": len(selected),
        "hazard_rejected_count": len(hazard_rejections),
        "interpretation": (
            "Dry-run A/B estimate only: compares serial primary todo against "
            "DSpark-style confidence-scheduled prefix before any worker starts."
        ),
    }
