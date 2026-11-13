from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.configure_goal import configure_goal
from loopx.control_plane.scheduler.execution_context import (
    GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    SchedulerRuntimeProfile,
    scheduler_execution_context_for_runtime_profile,
)
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)
from loopx.quota import build_quota_should_run


GOAL_ID = "paused-quota-precedence-fixture"
AGENT_ID = "codex-paused-agent"


def _paused_status(*, required_capabilities: list[str] | None = None) -> dict:
    todo = quota_todo_item(
        todo_id="todo_paused_delivery",
        title="Advance paused delivery.",
        claimed_by=AGENT_ID,
        required_capabilities=required_capabilities or [],
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Advance the delivery.",
        agent_todo_items=[todo],
        quota_state="paused",
        quota_extra={"compute": 0, "reason": "paused fixture"},
        coordination={
            "agent_model": "peer_v1",
            "registered_agents": [AGENT_ID],
        },
    )


def _find_execution_signals(obj: object, path: str = "") -> list[tuple[str, object]]:
    """Collect any nested field that would assert executable work or an active run.

    A hard-paused Goal must not carry a contradicting execution signal anywhere in
    the payload: no lane may leave `must_attempt_work=true`, no channel may allow
    delivery, and no scheduler field may request `run_now`.
    """

    hits: list[tuple[str, object]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            here = f"{path}/{key}"
            if key == "must_attempt_work" and value is True:
                hits.append((here, value))
            if key == "delivery_allowed" and value is True:
                hits.append((here, value))
            if key in {"action", "recommended_mode", "mode"} and value == "run_now":
                hits.append((here, value))
            hits.extend(_find_execution_signals(value, here))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            hits.extend(_find_execution_signals(value, f"{path}[{index}]"))
    return hits


def _assert_authoritatively_paused(payload: dict) -> None:
    assert payload["state"] == "paused"
    assert payload["decision"] == "skip"
    assert payload["should_run"] is False
    assert payload["effective_action"] == "quota_skip"
    assert payload["actionable_by_codex"] is False
    assert payload["normal_delivery_allowed"] is False
    assert payload["recovery_delivery_allowed"] is False
    assert payload["self_repair_allowed"] is False
    assert payload["capability_repair_allowed"] is False
    assert payload["workspace_repair_allowed"] is False
    assert payload["requires_user_action"] is False
    assert payload["heartbeat_recommendation"]["recommended_mode"] == "quota_paused"
    assert payload["heartbeat_recommendation"]["notify"] == "DONT_NOTIFY"
    assert payload["execution_obligation"]["must_attempt_work"] is False
    assert payload["interaction_contract"]["mode"] == "skip"
    assert payload["automation_liveness"]["keep_active"] is False
    assert payload["automation_liveness"]["pause_allowed"] is True
    assert payload["automation_liveness"]["automation_action"] == "stop_quota_paused"
    assert payload["scheduler_hint"]["action"] == "stop_until_explicit_resume"
    assert payload["scheduler_hint"]["cadence_class"] == "quota_paused"
    # No selector lane should have been constructed under the pause.
    for lane_key in (
        "capability_gate",
        "workspace_guard",
        "work_lane_contract",
        "autonomous_replan_obligation",
        "autonomous_replan_decision",
        "scoped_user_gate_fallback",
    ):
        assert lane_key not in payload, f"paused payload leaked lane `{lane_key}`"
    # And nothing nested may contradict the pause with an execution signal.
    contradictions = _find_execution_signals(payload)
    assert contradictions == [], (
        f"paused payload has execution signals: {contradictions}"
    )


def test_paused_quota_preempts_capability_bridge_repair() -> None:
    payload = build_quota_should_run(
        _paused_status(required_capabilities=["network"]),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=[],
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )

    _assert_authoritatively_paused(payload)
    assert "capability_gate" not in payload


def test_paused_quota_preempts_workspace_repair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.quota.should_run.build_agent_workspace_guard",
        lambda *args, **kwargs: {
            "schema_version": "agent_workspace_guard_v1",
            "reason": "workspace fixture requires relocation",
            "required_action": "rerun from the assigned worktree",
        },
    )

    payload = build_quota_should_run(
        _paused_status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )

    _assert_authoritatively_paused(payload)
    assert "workspace_guard" not in payload


def test_paused_quota_stops_codex_app_heartbeat_until_explicit_resume() -> None:
    payload = build_quota_should_run(
        _paused_status(),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        codex_app_current_rrule="FREQ=MINUTELY;INTERVAL=30",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            SchedulerRuntimeProfile.CODEX_APP_HEARTBEAT
        ),
    )

    _assert_authoritatively_paused(payload)
    liveness = payload["automation_liveness"]
    assert liveness["next_trigger"] == "explicit quota resume with quota.compute > 0"
    assert liveness["spend_policy"] == "no quota spend for paused automation shutdown"

    scheduler = payload["scheduler_hint"]
    assert scheduler["reason_code"] == "quota_paused"
    codex_app = scheduler["codex_app"]
    assert codex_app["applicability"] == "applicable"
    assert codex_app["apply"] == "pause_or_delete_current_heartbeat_if_possible"
    assert codex_app["host_action"] == "pause_or_delete_current_heartbeat"
    assert codex_app["host_action_required"] is True
    assert codex_app["ack_required"] is False
    assert codex_app["resume_trigger"] == "explicit quota resume with quota.compute > 0"
    assert "recommended_rrule" not in codex_app


def test_paused_quota_preserves_unhealthy_status_fact() -> None:
    status_payload = _paused_status()
    status_payload["contract"] = {"error_diagnostics": []}
    status_payload["global_registry"] = {"ok": False}

    payload = build_quota_should_run(
        status_payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )

    _assert_authoritatively_paused(payload)
    assert payload["ok"] is False
    assert payload["status_health_ok"] is False


@pytest.mark.parametrize("pause", ["compute", "owner", None])
def test_live_pause_preempts_prior_turn_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pause: str | None,
) -> None:
    from loopx.control_plane.quota import live_decision, unsettled_host_turn

    status = quota_status_payload(
        goal_id=GOAL_ID, status="active", recommended_action="Continue work.",
        agent_todo_items=[], quota_state="eligible" if pause is None else "paused",
        quota_extra={
            "compute": 0 if pause == "compute" else 1,
            **({"goal_activation_state": "stopped", "pause_cause": "goal_stopped"} if pause == "owner" else {}),
        },
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT_ID]},
    )
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": []}), encoding="utf-8")
    recovery = []

    def project_recovery(**kwargs):
        recovery.append(True)
        return None

    monkeypatch.setattr(unsettled_host_turn, "_unsettled_host_turn_recovery", project_recovery)
    payload = live_decision.build_live_quota_should_run_decision(
        status, goal_id=GOAL_ID, agent_id=AGENT_ID,
        available_capabilities=None, include_scheduler_detail=True,
        codex_app_current_rrule=None, registry_path=registry, runtime_root=tmp_path,
        turn_instance_id="paused-turn", scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )
    assert recovery == ([True] if pause is None else [])
    if pause is not None:
        assert payload["should_run"] is False
        assert payload["scheduler_hint"]["action"] == "stop_until_explicit_resume"
        assert _find_execution_signals(payload) == []


def _paused_status_with_due_monitor() -> dict:
    monitor_todo = quota_todo_item(
        todo_id="todo_paused_monitor",
        title="Poll the paused monitor.",
        task_class="monitor_task",
        claimed_by=AGENT_ID,
        next_due_at="2000-01-01T00:00:00+00:00",
        expires_at="2000-01-02T00:00:00+00:00",
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Poll the monitor.",
        agent_todo_items=[monitor_todo],
        quota_state="paused",
        quota_extra={"compute": 0, "reason": "paused fixture"},
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT_ID]},
    )


def _paused_status_with_user_gate() -> dict:
    gate_todo = quota_todo_item(
        todo_id="todo_paused_user_gate",
        role="user",
        title="Owner must approve the paused delivery.",
        blocks_agent=AGENT_ID,
    )
    delivery_todo = quota_todo_item(
        todo_id="todo_paused_delivery",
        title="Advance paused delivery.",
        claimed_by=AGENT_ID,
    )
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Advance the delivery.",
        agent_todo_items=[delivery_todo],
        user_todo_items=[gate_todo],
        quota_state="paused",
        quota_extra={"compute": 0, "reason": "paused fixture"},
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT_ID]},
    )


def _paused_status_no_runnable_candidate() -> dict:
    return quota_status_payload(
        goal_id=GOAL_ID,
        status="active",
        recommended_action="Nothing runnable.",
        agent_todo_items=[],
        quota_state="paused",
        quota_extra={"compute": 0, "reason": "paused fixture"},
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT_ID]},
    )


@pytest.mark.parametrize(
    "lane",
    [
        "capability_gap",
        "due_monitor",
        "user_gate",
        "no_runnable_candidate",
        "baseline",
    ],
)
def test_paused_quota_is_authoritative_across_lanes(lane: str) -> None:
    """A hard-paused Goal preempts every selector lane with one consistent contract.

    Whatever capability gap, due monitor, user gate, or empty-frontier fallback the
    inputs would otherwise route to, the paused Goal short-circuits before any lane
    is constructed, so all authority fields agree on the terminal skip.
    """

    if lane == "capability_gap":
        status_payload = _paused_status(required_capabilities=["network"])
        available_capabilities: list[str] | None = []
    elif lane == "due_monitor":
        status_payload = _paused_status_with_due_monitor()
        available_capabilities = None
    elif lane == "user_gate":
        status_payload = _paused_status_with_user_gate()
        available_capabilities = None
    elif lane == "no_runnable_candidate":
        status_payload = _paused_status_no_runnable_candidate()
        available_capabilities = None
    else:
        status_payload = _paused_status()
        available_capabilities = None

    payload = build_quota_should_run(
        status_payload,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        available_capabilities=available_capabilities,
        scheduler_execution_context=GENERIC_CLI_OUTER_CONTROLLER_SCHEDULER_CONTEXT,
    )

    _assert_authoritatively_paused(payload)


def test_configure_goal_accepts_zero_compute_as_pause(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "goals": [
                    {
                        "id": GOAL_ID,
                        "repo": str(tmp_path),
                        "quota": {"compute": 1.0, "window_hours": 24},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = configure_goal(
        registry_path=registry,
        goal_id=GOAL_ID,
        quota_compute=0,
        execute=True,
    )

    assert result["written"] is True
    stored = json.loads(registry.read_text(encoding="utf-8"))
    assert stored["goals"][0]["quota"]["compute"] == 0


def test_configure_goal_rejects_negative_compute(tmp_path: Path) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"goals": [{"id": GOAL_ID, "repo": str(tmp_path)}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="greater than or equal to 0"):
        configure_goal(
            registry_path=registry,
            goal_id=GOAL_ID,
            quota_compute=-0.1,
            execute=False,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("quota_compute", True),
        ("quota_compute", float("nan")),
        ("quota_window_hours", float("inf")),
        ("max_children", True),
        ("max_children", 1.5),
    ],
)
def test_configure_goal_rejects_invalid_numeric_values(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    registry = tmp_path / "registry.json"
    registry.write_text(
        json.dumps({"goals": [{"id": GOAL_ID, "repo": str(tmp_path)}]}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=field):
        configure_goal(
            registry_path=registry,
            goal_id=GOAL_ID,
            execute=False,
            **{field: value},
        )
