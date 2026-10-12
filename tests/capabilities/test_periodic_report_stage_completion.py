from __future__ import annotations

from copy import deepcopy

import pytest

from loopx.capabilities.periodic_report.stage_completion import (
    derive_periodic_report_stage_completion,
    derive_periodic_report_stage_completion_from_runs,
    project_periodic_report_stage_completion_event_details,
)


def _vision(*, state: str, generated_at: str) -> dict[str, object]:
    return {
        "schema_version": "goal_vision_replan_contract_v0",
        "agent_id": "case-analyst",
        "state": state,
        "generated_at": generated_at,
        "vision_patch": {"acceptance_summary": "The bounded analysis is accepted."},
    }


def _checkpoint() -> dict[str, object]:
    return {
        "schema_version": "vision_checkpoint_v0",
        "satisfied": True,
        "decision": "patched",
        "triggers": [
            {
                "kind": "material_delivery_outcome",
                "delivery_outcome": "outcome_progress",
            }
        ],
    }


def _successor_inputs() -> dict[str, object]:
    return {
        "replan_obligation": {
            "frontier_identity": "frontier-analysis-v2",
            "triggers": [{"kind": "vision_successor_required"}],
        },
        "replan_ack": {
            "recorded": True,
            "frontier_identity": "frontier-analysis-v2",
            "semantic_delta": {
                "accepted": True,
                "outcomes": ["fresh_vision_path_outcome"],
                "trigger_kinds": ["vision_successor_required"],
                "obligation_id": "replan-analysis-v2",
            },
        },
        "successor_vision": _vision(
            state="active", generated_at="2026-08-29T11:00:00Z"
        ),
        "successor_frontier": {
            "schema_version": "goal_frontier_projection_v0",
            "replan_required": False,
            "remaining_advancement_frontier": {
                "current_agent_claimed_advancement_count": 1,
                "unclaimed_advancement_count": 0,
                "other_agent_claimed_advancement_count": 0,
            },
        },
    }


def test_successor_replan_settlement_derives_stage_completion() -> None:
    receipt = derive_periodic_report_stage_completion(
        closed_vision=_vision(
            state="vision_closed", generated_at="2026-08-29T10:00:00Z"
        ),
        outcome_checkpoint=_checkpoint(),
        **_successor_inputs(),
    )

    assert receipt is not None
    assert receipt["transition"] == "successor_frontier_settled"
    assert receipt["frontier_identity"] == "frontier-analysis-v2"
    assert receipt["stage_identity"].startswith("stage-")


@pytest.mark.parametrize("ack_obligation_id", ["replan-1234567890abcdef", "replan-fedcba0987654321", "", None])
def test_native_successor_requires_the_same_accepted_obligation(ack_obligation_id) -> None:
    values = _successor_inputs()
    values["replan_obligation"].pop("frontier_identity")
    values["replan_obligation"]["obligation_id"] = "replan-1234567890abcdef"
    values["replan_ack"].pop("frontier_identity")
    values["replan_ack"]["semantic_delta"]["obligation_id"] = ack_obligation_id
    receipt = derive_periodic_report_stage_completion(
        closed_vision=_vision(state="vision_closed", generated_at="2026-08-29T10:00:00Z"),
        outcome_checkpoint=_checkpoint(),
        **values,
    )
    if ack_obligation_id == "replan-1234567890abcdef":
        assert receipt is not None
        assert receipt["frontier_identity"] == ack_obligation_id
    else:
        assert receipt is None


def test_other_agent_only_frontier_cannot_settle_current_agent_stage() -> None:
    values = _successor_inputs()
    values["successor_frontier"] = {
        "schema_version": "goal_frontier_projection_v0",
        "replan_required": False,
        "remaining_advancement_frontier": {
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_advancement_count": 0,
            "other_agent_claimed_advancement_count": 1,
        },
    }

    assert (
        derive_periodic_report_stage_completion(
            closed_vision=_vision(
                state="vision_closed", generated_at="2026-08-29T10:00:00Z"
            ),
            outcome_checkpoint=_checkpoint(),
            **values,
        )
        is None
    )


def test_unclaimed_frontier_can_settle_current_agent_stage() -> None:
    values = _successor_inputs()
    values["successor_frontier"] = {
        "schema_version": "goal_frontier_projection_v0",
        "replan_required": False,
        "remaining_advancement_frontier": {
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_advancement_count": 1,
            "other_agent_claimed_advancement_count": 0,
        },
    }

    receipt = derive_periodic_report_stage_completion(
        closed_vision=_vision(
            state="vision_closed", generated_at="2026-08-29T10:00:00Z"
        ),
        outcome_checkpoint=_checkpoint(),
        **values,
    )

    assert receipt is not None
    assert receipt["transition"] == "successor_frontier_settled"


def test_validated_terminal_goal_derives_stage_completion() -> None:
    receipt = derive_periodic_report_stage_completion(
        closed_vision=_vision(
            state="vision_closed", generated_at="2026-08-29T10:00:00Z"
        ),
        outcome_checkpoint=_checkpoint(),
        goal_terminal_state={
            "schema_version": "goal_terminal_state_v0",
            "kind": "no_followup",
            "derived": True,
            "source": "validated_goal_closure",
        },
    )

    assert receipt is not None
    assert receipt["transition"] == "goal_terminal"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda values: values["closed_vision"].update(state="active"),
        lambda values: values["outcome_checkpoint"].update(satisfied=False),
        lambda values: values["replan_obligation"].update(
            triggers=[{"kind": "long_todo_chain"}]
        ),
        lambda values: values["replan_ack"]["semantic_delta"].update(
            accepted=False
        ),
        lambda values: values["successor_frontier"].update(
            remaining_advancement_frontier={
                "current_agent_claimed_advancement_count": 0,
                "unclaimed_advancement_count": 0,
                "other_agent_claimed_advancement_count": 0,
            }
        ),
    ],
)
def test_incomplete_or_generic_replan_fails_closed(mutate) -> None:
    values = {
        "closed_vision": _vision(
            state="vision_closed", generated_at="2026-08-29T10:00:00Z"
        ),
        "outcome_checkpoint": _checkpoint(),
        **_successor_inputs(),
    }
    mutate(values)

    assert derive_periodic_report_stage_completion(**deepcopy(values)) is None


def test_stage_identity_is_replay_stable() -> None:
    values = {
        "closed_vision": _vision(
            state="vision_closed", generated_at="2026-08-29T10:00:00Z"
        ),
        "outcome_checkpoint": _checkpoint(),
        **_successor_inputs(),
    }

    first = derive_periodic_report_stage_completion(**deepcopy(values))
    second = derive_periodic_report_stage_completion(**deepcopy(values))

    assert first == second


def test_durable_run_history_derives_successor_boundary() -> None:
    successor = _vision(state="active", generated_at="2026-08-29T11:00:00Z")
    closed = _vision(
        state="vision_closed", generated_at="2026-08-29T10:00:00Z"
    )
    values = _successor_inputs()

    receipt = derive_periodic_report_stage_completion_from_runs(
        latest_runs=[
            {"agent_vision": successor},
            {"agent_vision": closed, "vision_checkpoint": _checkpoint()},
        ],
        agent_id="case-analyst",
        goal_frontier_projection=values["successor_frontier"],
        settled_replan_obligation=values["replan_obligation"],
        settled_replan_ack=values["replan_ack"],
    )

    assert receipt is not None
    assert receipt["transition"] == "successor_frontier_settled"
    assert receipt["completed_at"] == "2026-08-29T11:00:00Z"


@pytest.mark.parametrize("later_generated_at", [None, "2026-08-29T11:00:00Z", "2026-08-29T12:00:00Z"])
def test_native_ack_is_bound_to_its_successor_vision(later_generated_at) -> None:
    values = _successor_inputs()
    values["replan_obligation"].pop("frontier_identity")
    values["replan_obligation"]["obligation_id"] = "replan-1234567890abcdef"
    values["replan_ack"].pop("frontier_identity")
    values["replan_ack"]["semantic_delta"]["obligation_id"] = "replan-1234567890abcdef"
    runs = [
        {"agent_vision": values["successor_vision"], "autonomous_replan_ack": values["replan_ack"]},
        {"agent_vision": _vision(state="vision_closed", generated_at="2026-08-29T10:00:00Z"),
         "vision_checkpoint": _checkpoint()},
    ]
    if later_generated_at is not None:
        later_vision = _vision(state="active", generated_at=later_generated_at)
        later_vision["vision_patch"] = {"acceptance_summary": "A separate unacknowledged edit."}
        runs.insert(0, {"agent_vision": later_vision})
    receipt = derive_periodic_report_stage_completion_from_runs(
        latest_runs=runs, agent_id="case-analyst",
        goal_frontier_projection=values["successor_frontier"],
        settled_replan_obligation=values["replan_obligation"],
        settled_replan_ack=values["replan_ack"],
    )
    assert (receipt is None) is (later_generated_at is not None)


@pytest.mark.parametrize("foreign_evidence", ["different_delta", "run_agent", "ack_agent"])
def test_native_successor_cannot_borrow_another_writebacks_ack(foreign_evidence) -> None:
    from copy import deepcopy

    values = _successor_inputs()
    values["replan_obligation"].pop("frontier_identity")
    values["replan_obligation"]["obligation_id"] = "replan-1234567890abcdef"
    values["replan_ack"].pop("frontier_identity")
    values["replan_ack"]["semantic_delta"]["obligation_id"] = "replan-1234567890abcdef"
    later = {
        "agent_vision": _vision(state="active", generated_at="2026-08-29T12:00:00Z"),
        "autonomous_replan_ack": deepcopy(values["replan_ack"]),
    }
    if foreign_evidence == "different_delta":
        later["autonomous_replan_ack"]["semantic_delta"].update(
            trigger_kinds=["periodic_review_due"], outcomes=["new_surface"],
        )
    elif foreign_evidence == "run_agent":
        later["agent_id"] = "another-agent"
    else:
        later["autonomous_replan_ack"]["agent_id"] = "another-agent"
    assert derive_periodic_report_stage_completion_from_runs(
        latest_runs=[
            later,
            {"agent_vision": values["successor_vision"], "autonomous_replan_ack": values["replan_ack"]},
            {"agent_vision": _vision(state="vision_closed", generated_at="2026-08-29T10:00:00Z"),
             "vision_checkpoint": _checkpoint()},
        ],
        agent_id="case-analyst",
        goal_frontier_projection=values["successor_frontier"],
        settled_replan_obligation=values["replan_obligation"],
        settled_replan_ack=values["replan_ack"],
    ) is None


def test_durable_run_history_derives_terminal_boundary_without_successor() -> None:
    projection = {
        "terminal_state": {
            "schema_version": "goal_terminal_state_v0",
            "kind": "no_followup",
            "derived": True,
            "source": "validated_goal_closure",
        }
    }

    receipt = derive_periodic_report_stage_completion_from_runs(
        latest_runs=[
            {
                "agent_vision": _vision(
                    state="vision_closed",
                    generated_at="2026-08-29T10:00:00Z",
                ),
                "vision_checkpoint": _checkpoint(),
            }
        ],
        agent_id="case-analyst",
        goal_frontier_projection=projection,
    )

    assert receipt is not None
    assert receipt["transition"] == "goal_terminal"


@pytest.mark.parametrize(
    ("newer_agent", "newer_at", "expected"),
    [
        ("case-analyst", "2026-08-29T11:00:00Z", False),
        ("case-analyst", "2026-08-29T10:00:00Z", False),
        ("peer-analyst", "2026-08-29T11:00:00Z", True),
    ],
)
def test_terminal_stage_requires_the_current_agents_closed_vision(
    newer_agent: str, newer_at: str, expected: bool,
) -> None:
    newer = _vision(state="active", generated_at=newer_at)
    newer["agent_id"] = newer_agent
    receipt = derive_periodic_report_stage_completion_from_runs(
        latest_runs=[
            {"agent_vision": newer},
            {"agent_vision": _vision(
                state="vision_closed", generated_at="2026-08-29T10:00:00Z"
            ), "vision_checkpoint": _checkpoint()},
        ],
        agent_id="case-analyst",
        goal_frontier_projection={"terminal_state": {
            "schema_version": "goal_terminal_state_v0", "kind": "no_followup",
            "derived": True, "source": "validated_goal_closure",
        }},
    )
    assert (receipt is not None) is expected


def test_active_vision_cannot_settle_an_older_terminal_stage() -> None:
    projection = {
        "terminal_state": {
            "schema_version": "goal_terminal_state_v0",
            "kind": "no_followup",
            "derived": True,
            "source": "validated_goal_closure",
        }
    }
    assert derive_periodic_report_stage_completion_from_runs(
        latest_runs=[
            {"agent_vision": _vision(
                state="active", generated_at="2026-08-29T11:00:00Z"
            )},
            {"agent_vision": _vision(
                state="vision_closed", generated_at="2026-08-29T10:00:00Z"
            ), "vision_checkpoint": _checkpoint()},
        ],
        agent_id="case-analyst",
        goal_frontier_projection=projection,
    ) is None


@pytest.mark.parametrize("newer_at", ["2026-08-29T10:00:00Z", "2026-08-29T12:00:00Z"])
@pytest.mark.parametrize("satisfied", [False, True])
def test_terminal_stage_cannot_skip_a_newer_nonmaterial_closed_vision(
    newer_at: str, satisfied: bool,
) -> None:
    receipt = derive_periodic_report_stage_completion_from_runs(
        latest_runs=[
            {"agent_vision": _vision(state="vision_closed", generated_at=newer_at),
             "vision_checkpoint": {"satisfied": satisfied, "decision": "patched", "triggers": []}},
            {"agent_vision": _vision(state="vision_closed", generated_at="2026-08-29T10:00:00Z"),
             "vision_checkpoint": _checkpoint()},
        ],
        agent_id="case-analyst",
        goal_frontier_projection={"terminal_state": {
            "schema_version": "goal_terminal_state_v0", "kind": "no_followup",
            "derived": True, "source": "validated_goal_closure",
        }},
    )
    assert receipt is None


def test_stage_receipt_flattens_to_public_rollout_details() -> None:
    receipt = derive_periodic_report_stage_completion(
        closed_vision=_vision(
            state="vision_closed", generated_at="2026-08-29T10:00:00Z"
        ),
        outcome_checkpoint=_checkpoint(),
        goal_terminal_state={
            "schema_version": "goal_terminal_state_v0",
            "kind": "no_followup",
            "derived": True,
            "source": "validated_goal_closure",
        },
    )

    details = project_periodic_report_stage_completion_event_details(receipt)

    assert details["stage_completion_schema"] == (
        "periodic_report_stage_completion_receipt_v0"
    )
    assert details["stage_transition"] == "goal_terminal"
    assert details["stage_completed_at"] == "2026-08-29T10:00:00Z"


def test_cross_agent_ack_or_obligation_fails_closed() -> None:
    values = _successor_inputs()
    values["replan_ack"] = {
        **dict(values["replan_ack"]),  # type: ignore[arg-type]
        "agent_id": "other-agent",
    }
    assert (
        derive_periodic_report_stage_completion(
            closed_vision=_vision(
                state="vision_closed", generated_at="2026-08-29T10:00:00Z"
            ),
            outcome_checkpoint=_checkpoint(),
            **values,  # type: ignore[arg-type]
        )
        is None
    )


def test_other_agent_claimed_frontier_fails_closed() -> None:
    values = _successor_inputs()
    values["successor_frontier"] = {
        "schema_version": "goal_frontier_projection_v0",
        "replan_required": False,
        "remaining_advancement_frontier": {
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_advancement_count": 0,
            "other_agent_claimed_advancement_count": 1,
        },
    }
    assert (
        derive_periodic_report_stage_completion(
            closed_vision=_vision(
                state="vision_closed", generated_at="2026-08-29T10:00:00Z"
            ),
            outcome_checkpoint=_checkpoint(),
            **values,  # type: ignore[arg-type]
        )
        is None
    )


def test_unclaimed_advancement_frontier_settles_successor() -> None:
    values = _successor_inputs()
    values["successor_frontier"] = {
        "schema_version": "goal_frontier_projection_v0",
        "replan_required": False,
        "remaining_advancement_frontier": {
            "current_agent_claimed_advancement_count": 0,
            "unclaimed_advancement_count": 1,
            "other_agent_claimed_advancement_count": 0,
        },
    }
    receipt = derive_periodic_report_stage_completion(
        closed_vision=_vision(
            state="vision_closed", generated_at="2026-08-29T10:00:00Z"
        ),
        outcome_checkpoint=_checkpoint(),
        **values,  # type: ignore[arg-type]
    )
    assert receipt is not None
    assert receipt["transition"] == "successor_frontier_settled"
