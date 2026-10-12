from __future__ import annotations

import pytest

from loopx.control_plane.quota.automation_cadence_readback import (
    automation_cadence_readback,
    unavailable_automation_cadence_readback,
)
from loopx.control_plane.quota.should_run_packet import (
    _apply_automatic_cadence_wait_precedence,
)


def owner_result(*, state: str = "waiting") -> dict[str, object]:
    eligibility: dict[str, object] = {
        "state": state,
        "reason": "minimum_interval_wait",
        "eligible_now": False,
        "next_eligible_at_ms": 9_000,
    }
    if state == "eligible":
        eligibility.update(
            reason="owner_minimum_interval",
            eligible_now=True,
            next_eligible_at_ms=8_000,
        )
    return {
        "schema_version": "automation_cadence_result_v1",
        "ok": True,
        "goal_id": "goal",
        "agent_id": "agent",
        "automation_id": "daily",
        "configuration_revision": 4,
        "min_interval_minutes": 60,
        "eligibility": eligibility,
        "sources": [
            {
                "agent_id": "agent",
                "automation_id": "daily",
                "min_interval_minutes": 60,
                "revision": 4,
                "owner_reference": "private owner note",
            }
        ],
    }


def runnable_payload() -> dict[str, object]:
    return {
        "decision": "run",
        "state": "active",
        "should_run": True,
        "normal_delivery_allowed": True,
        "recovery_delivery_allowed": False,
        "self_repair_allowed": False,
        "capability_repair_allowed": False,
        "workspace_repair_allowed": False,
        "effective_action": "normal_run",
        "actionable_by_codex": True,
        "requires_user_action": False,
        "safe_bypass_allowed": True,
        "safe_bypass_kind": "bounded_work",
        "safe_bypass_policy": {"allowed": True},
        "selected_todo": {"todo_id": "todo"},
        "todo_write_hint": {"path": "private"},
        "work_lane_contract": {"obligation": "deliver"},
        "task_orchestration_contract": {"execution_state": "ready"},
        "agent_command": {"command": "run"},
    }


def test_readback_validates_scope_and_omits_private_owner_data() -> None:
    readback = automation_cadence_readback(
        owner_result(),
        expected_goal_id="goal",
        expected_agent_id="agent",
        expected_automation_id="daily",
    )
    assert readback == {
        "goal_id": "goal",
        "agent_id": "agent",
        "automation_id": "daily",
        "configuration_revision": 4,
        "min_interval_minutes": 60,
        "eligibility": {
            "state": "waiting",
            "reason": "minimum_interval_wait",
            "eligible_now": False,
            "next_eligible_at_ms": 9_000,
        },
    }
    assert "owner_reference" not in repr(readback)

    with pytest.raises(ValueError, match="scope mismatch"):
        automation_cadence_readback(
            owner_result(),
            expected_goal_id="other",
            expected_agent_id="agent",
            expected_automation_id="daily",
        )
    malformed = owner_result()
    malformed["eligibility"] = {
        "state": "waiting",
        "reason": "minimum_interval_wait",
        "eligible_now": False,
        "next_eligible_at_ms": None,
    }
    with pytest.raises(ValueError, match="next_eligible_at_ms"):
        automation_cadence_readback(
            malformed,
            expected_goal_id="goal",
            expected_agent_id="agent",
            expected_automation_id="daily",
        )


def test_unavailable_readback_never_claims_ready() -> None:
    assert unavailable_automation_cadence_readback(
        goal_id="goal",
        agent_id="agent",
        automation_id=None,
        reason="owner_read_failed",
    ) == {
        "goal_id": "goal",
        "agent_id": "agent",
        "automation_id": None,
        "configuration_revision": None,
        "min_interval_minutes": None,
        "eligibility": {
            "state": "unavailable",
            "reason": "owner_read_failed",
            "eligible_now": None,
            "next_eligible_at_ms": None,
        },
    }


def test_hosted_app_wait_demotes_only_an_otherwise_runnable_decision() -> None:
    readback = automation_cadence_readback(
        owner_result(),
        expected_goal_id="goal",
        expected_agent_id="agent",
        expected_automation_id="daily",
    )
    payload = runnable_payload()
    _apply_automatic_cadence_wait_precedence(
        payload,
        readback=readback,
        app_automation_applicable=True,
    )
    assert payload["decision"] == "wait"
    assert payload["state"] == "waiting"
    assert payload["effective_action"] == "blocked_wait"
    assert payload["should_run"] is False
    assert payload["normal_delivery_allowed"] is False
    assert payload["requires_user_action"] is False
    assert payload["heartbeat_recommendation"] == {
        "source": "quota.should-run",
        "recommended_mode": "blocked_wait",
        "notify": "DONT_NOTIFY",
        "reason": "automatic execution minimum interval has not elapsed",
        "spend_policy": "no quota spend while automatic execution waits for the owner minimum interval",
        "agent_must_attempt": False,
    }
    assert payload["execution_obligation"]["must_attempt_work"] is False
    assert payload["execution_obligation"]["delivery_allowed"] is False
    for key in (
        "selected_todo",
        "todo_write_hint",
        "work_lane_contract",
        "task_orchestration_contract",
        "agent_command",
    ):
        assert key not in payload


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("should_run", False),
        ("normal_delivery_allowed", False),
        ("recovery_delivery_allowed", True),
        ("self_repair_allowed", True),
        ("capability_repair_allowed", True),
        ("workspace_repair_allowed", True),
        ("requires_user_action", True),
    ],
)
def test_cadence_wait_preserves_stronger_or_nonrunnable_states(
    field: str, value: object
) -> None:
    payload = runnable_payload()
    payload[field] = value
    before = dict(payload)
    _apply_automatic_cadence_wait_precedence(
        payload,
        readback=automation_cadence_readback(
            owner_result(),
            expected_goal_id="goal",
            expected_agent_id="agent",
            expected_automation_id="daily",
        ),
        app_automation_applicable=True,
    )
    assert payload == before


def test_non_app_and_nonwaiting_reads_remain_observation_only() -> None:
    for readback, applicable in (
        (
            automation_cadence_readback(
                owner_result(),
                expected_goal_id="goal",
                expected_agent_id="agent",
                expected_automation_id="daily",
            ),
            False,
        ),
        (
            automation_cadence_readback(
                owner_result(state="eligible"),
                expected_goal_id="goal",
                expected_agent_id="agent",
                expected_automation_id="daily",
            ),
            True,
        ),
    ):
        payload = runnable_payload()
        before = dict(payload)
        _apply_automatic_cadence_wait_precedence(
            payload,
            readback=readback,
            app_automation_applicable=applicable,
        )
        assert payload == before
