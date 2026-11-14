"""Fresh replan work needs remaining compute; settlement retains its own owner."""
import json
from pathlib import Path

import pytest

from loopx.control_plane.quota.live_decision import build_live_quota_should_run_decision
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item


GOAL = "quota-replan-fixture"
AGENT = "fixture-agent"


def _status(*, state="throttled", allowed=1, spent=1, compute=1):
    return quota_status_payload(
        goal_id=GOAL, status="active", recommended_action="Wait for the dependency.",
        agent_todo_items=[quota_todo_item(
            index=1, todo_id="todo_blocked_work", text="Wait for the dependency.",
            status="open", resume_when="todo_done:todo_missing_dependency",
            claimed_by=AGENT, task_class="advancement_task",
        )],
        quota_state=state,
        quota_extra={"compute": compute, "allowed_slots": allowed, "spent_slots": spent},
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT]},
        latest_runs=[{
            "classification": "state_refreshed", "generated_at": "2026-07-04T00:05:00Z",
            "agent_id": AGENT, "progress_scope": "agent_lane",
            "delivery_outcome": "outcome_progress",
            "vision_checkpoint": {
                "schema_version": "vision_checkpoint_v0", "agent_id": AGENT,
                "required": True, "satisfied": False, "decision": "missing_required",
                "triggers": [{"kind": "material_delivery_outcome", "delivery_outcome": "outcome_progress"}],
                "required_resolution": ["write_agent_vision_patch", "record_unchanged_reason"],
            },
        }],
    )


def _read(status, tmp_path, **kwargs):
    return build_live_quota_should_run_decision(
        status, goal_id=GOAL, agent_id=AGENT, available_capabilities=["shell"],
        include_scheduler_detail=False, codex_app_current_rrule=None,
        registry_path=tmp_path / "registry.json", runtime_root=tmp_path / "runtime",
        scheduler_execution_context={"host_surface": "generic_cli",
            "scheduler_owner": "agent_cli_loop", "execution_mode": "interactive"},
        **kwargs,
    )


@pytest.mark.parametrize("state", ["throttled", "focus_wait", "waiting"])
def test_exhausted_window_does_not_admit_fresh_replan(tmp_path: Path, state: str):
    for _ in range(2):
        packet = _read(_status(state=state), tmp_path)
        assert packet["should_run"] is False
        assert packet["effective_action"] == "throttled_skip"
        assert "replan" in packet["reason"] and "zero" in packet["reason"]
        assert packet["scheduler_hint"]["action"] != "run_now"
        assert packet["interaction_contract"]["agent_channel"]["must_attempt"] is False
        assert packet["interaction_contract"]["agent_channel"]["delivery_allowed"] is False


@pytest.mark.parametrize("allowed,spent", [(1, 0), (2, 1)])
def test_window_reset_or_owner_increase_reopens_replan(tmp_path: Path, allowed: int, spent: int):
    assert _read(_status(), tmp_path)["should_run"] is False
    packet = _read(_status(state="eligible", allowed=allowed, spent=spent), tmp_path)
    assert packet["should_run"] is True
    assert packet["effective_action"] == "autonomous_replan_required"
    assert packet["scheduler_hint"]["action"] == "run_now"


def test_zero_compute_remains_an_owner_pause(tmp_path: Path):
    packet = _read(_status(state="paused", compute=0, allowed=0), tmp_path)
    assert packet["should_run"] is False
    assert packet["scheduler_hint"]["action"] == "stop_until_explicit_resume"


@pytest.mark.parametrize("spent,expected", [(0, True), (1, False)])
def test_should_run_cli_uses_the_shared_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
    spent: int, expected: bool,
):
    from loopx.cli import main
    from loopx.cli_commands import quota

    status = _status(state="eligible" if expected else "throttled", spent=spent)
    monkeypatch.setattr(quota, "collect_status", lambda **kwargs: status)
    (tmp_path / "registry.json").write_text(json.dumps({"goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": "fixture.md",
    }]}), encoding="utf-8")
    (tmp_path / "fixture.md").write_text("# Synthetic Goal\n", encoding="utf-8")
    code = main([
        "--registry", str(tmp_path / "registry.json"),
        "--runtime-root", str(tmp_path / "runtime"), "--format", "json",
        "quota", "should-run", "--goal-id", GOAL, "--agent-id", AGENT,
        "-H", "generic_cli", "-O", "agent_cli_loop", "-M", "interactive",
    ])
    assert code == 0
    packet = json.loads(capsys.readouterr().out)
    assert packet["should_run"] is expected
    assert packet["effective_action"] == (
        "autonomous_replan_required" if expected else "throttled_skip"
    )


def test_exhaustion_preserves_exact_owed_turn_recovery(tmp_path: Path):
    from canonical_authority_fixture import initialize_canonical_source_fixture
    from loopx.rollout_event_log import build_rollout_event

    runtime = tmp_path / "runtime"
    state = tmp_path / "fixture.md"
    state.write_text(
        "# Synthetic Goal\n\n## Agent Todo\n\n- [ ] Wait for the dependency.\n"
        f"  <!-- loopx:todo todo_id=todo_blocked_work status=open claimed_by={AGENT} "
        "task_class=advancement_task resume_when=todo_done:todo_missing_dependency -->\n",
        encoding="utf-8",
    )
    (tmp_path / "registry.json").write_text(json.dumps({"goals": [{
        "id": GOAL, "repo": str(tmp_path), "state_file": str(state),
    }]}), encoding="utf-8")
    initialize_canonical_source_fixture(runtime, GOAL, state_path=state, provider="sqlite")
    event = build_rollout_event(
        goal_id=GOAL, event_kind="quota_should_run", agent_id=AGENT,
        todo_id="todo_blocked_work", run_id="prior-turn", status="normal_run",
        summary="Original Turn requires settlement.", details={
            "todo_id": "todo_blocked_work", "closeout_required": True,
            "settlement_effect_id": f"{GOAL}:{AGENT}:todo_blocked_work:prior-turn",
        },
    )
    log = runtime / "goals" / GOAL / "rollout-event-log.jsonl"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(event) + "\n", encoding="utf-8")
    packet = _read(_status(), tmp_path, turn_instance_id="current-turn")
    assert packet["effective_action"] == "unsettled_host_turn_recovery"
    assert packet["unsettled_host_turn_recovery"]["prior_turn_instance_id"] == "prior-turn"
    assert packet["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert packet["interaction_contract"]["cli_channel"]["spend_after_validation"] is False
