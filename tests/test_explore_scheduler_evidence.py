"""Expected evidence distinguishes an unknown estimate from a measured zero."""
from functools import partial

import pytest

from loopx.capabilities.explore.speculative_scheduler import (
    prefix_metrics,
    schedule_confidence_prefix,
    schedule_independent_lanes,
)


@pytest.fixture(params=["independent", "serial"])
def scheduler(request):
    if request.param == "serial":
        return partial(schedule_confidence_prefix, max_branch_width=8)
    return schedule_independent_lanes


@pytest.mark.parametrize("estimate", [{}, {"expected_evidence_units": None}])
def test_missing_estimate_keeps_the_evidence_prior(scheduler, estimate):
    plan = scheduler([{"confidence": 1.0, **estimate}], max_width=1, load_factor=0.25)

    assert plan["selected_prefix_length"] == 1
    assert plan["steps"][0]["expected_evidence_units"] == 1.0
    assert plan["best_theta"] == 1.0


@pytest.mark.parametrize("zero", [0, 0.0])
def test_zero_gain_keeps_one_seed_without_inventing_evidence(scheduler, zero):
    candidates = [{"confidence": 1.0, "expected_evidence_units": zero}] * 3
    plan = scheduler(candidates, max_width=3, load_factor=0.25)

    assert plan["selected_prefix_length"] == 1
    assert plan["best_theta"] == plan["baseline_theta"] == 0.0
    assert all(step["expected_evidence_units"] == 0.0 for step in plan["steps"])
    if "ab_comparison" in plan:
        assert plan["ab_comparison"]["dspark_selected"]["expected_evidence"] == 0.0


def test_zero_tail_does_not_inflate_the_selected_width(scheduler):
    candidates = [
        {"confidence": 1.0, "expected_evidence_units": 2.0},
        {"confidence": 1.0, "expected_evidence_units": 0.0},
    ]
    plan = scheduler(candidates, max_width=2, load_factor=0.25)

    # The second lane adds no evidence but reduces throughput to 1 / 1.25.
    assert plan["steps"][1]["expected_evidence"] == 2.0
    assert plan["steps"][1]["theta"] == 1.6
    assert plan["selected_prefix_length"] == 1


def test_zero_estimate_is_not_a_candidate_filter(scheduler):
    candidates = [
        {"todo_id": "seed", "confidence": 1.0, "expected_evidence_units": 0.0},
        {"todo_id": "useful", "confidence": 1.0, "expected_evidence_units": 2.0},
    ]
    plan = scheduler(candidates, max_width=2, load_factor=0.25)

    assert plan["selected_prefix_length"] == 2
    assert [step["todo_id"] for step in plan["steps"]] == ["seed", "useful"]
    assert plan["steps"][0]["expected_evidence"] == 0.0
    assert plan["best_theta"] == 1.6


def test_empty_queue_does_not_create_an_exploration_seed(scheduler):
    plan = scheduler([], max_width=3, load_factor=0.25)

    assert plan["selected_prefix_length"] == 0
    assert plan["steps"] == []


def test_prefix_diagnostics_preserve_zero_evidence():
    candidates = [
        {"confidence": 1.0, "expected_evidence_units": 0.0},
        {"confidence": 1.0, "expected_evidence_units": 2.0},
    ]
    first = prefix_metrics(candidates, prefix_length=1, load_factor=0.25)
    both = prefix_metrics(candidates, prefix_length=2, load_factor=0.25)

    assert first["expected_evidence"] == first["theta"] == 0.0
    assert both["expected_evidence"] == 2.0
    assert both["theta"] == 1.6


def test_opportunistic_expansion_requires_positive_value_but_keeps_low_gain():
    def plan(tail_units):
        return schedule_independent_lanes(
            [{"confidence": 1.0, "expected_evidence_units": 0.05}]
            + [{"confidence": 1.0, "expected_evidence_units": tail_units}] * 7,
            max_width=8,
            load_factor=1.0,
            opportunistic_utilization_floor=0.65,
            opportunistic_lane_value_floor=0.001,
        )

    depleted = plan(0.0)
    assert depleted["selected_prefix_length"] == 1
    assert depleted["admission_audit"]["opportunistic_admitted_count"] == 0
    low_gain = plan(0.05)
    assert low_gain["core_selected_prefix_length"] == 1
    assert low_gain["selected_prefix_length"] == 6
    assert low_gain["admission_audit"]["opportunistic_admitted_count"] == 5
