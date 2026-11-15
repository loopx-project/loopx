"""Fresh replan work needs remaining compute; settlement retains its own owner."""
from datetime import datetime, timezone
import json
import shlex
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
        "--context-delivery", "off",
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_exhausted_window_preserves_only_the_admitted_replan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
):
    from test_replan_successor_guard_reentry import (
        AGENT as CLI_AGENT,
        GOAL as CLI_GOAL,
        TURN,
        _add,
        _fixture,
        _guard,
    )

    call, _, index = _fixture(tmp_path, monkeypatch, provider)
    registry = tmp_path / "registry.json"
    config = json.loads(registry.read_text())
    config["goals"][0]["quota"]["allowed_slots"] = 1
    registry.write_text(json.dumps(config))

    original = _guard(call)
    assert original["effective_action"] == "autonomous_replan_required"
    identity = original["heartbeat_receipt"]["settlement_identity"]
    assert original["quota"]["spent_slots"] == 0

    # Another lane spends the remaining slot after the guard is committed.
    # Seed an ordinary ledger entry and let the production CLI account for it.
    timestamp = datetime.now(timezone.utc).isoformat()
    spend = {
        "classification": "quota_slot_spent", "generated_at": timestamp,
        "goal_id": CLI_GOAL, "agent_id": "different-agent",
        "quota_event": {"event_type": "quota_slot_spent", "slots": 1},
    }
    with index.open("a", encoding="utf-8") as ledger:
        ledger.write(json.dumps(spend) + "\n")

    fresh_turn = "turn-after-compute-exhaustion"
    for _ in range(2):
        fresh = _guard(call, fresh_turn)
        assert (fresh["quota"]["allowed_slots"], fresh["quota"]["spent_slots"]) == (1, 1)
        assert fresh["effective_action"] == "throttled_skip"
        assert fresh["should_run"] is False
        assert not fresh["interaction_contract"]["cli_channel"].get("settlement_plan")

    replay = _guard(call, TURN)
    assert (replay["quota"]["allowed_slots"], replay["quota"]["spent_slots"]) == (1, 1)
    assert replay["heartbeat_receipt"]["event_id"] == original["heartbeat_receipt"]["event_id"]
    assert replay["effective_action"] == "autonomous_replan_required"
    assert replay["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    assert replay["interaction_contract"]["cli_channel"]["settlement_plan"]["identity"] == identity

    plan = call("turn", "plan", "--goal-id", CLI_GOAL, "--agent-id", CLI_AGENT,
                "--turn-instance-id", TURN, "--host", "codex-cli")
    assert plan["route"]["kind"] == "replan_required"

    config["goals"][0]["quota"]["compute"] = 0
    registry.write_text(json.dumps(config))
    paused = _guard(call, TURN)
    assert paused["should_run"] is False
    assert paused["scheduler_hint"]["action"] == "stop_until_explicit_resume"
    config["goals"][0]["quota"]["compute"] = 1
    registry.write_text(json.dumps(config))

    _add(call, identity["replan_obligation_id"])
    recovered = _guard(call, TURN)
    assert recovered["replan_action_packet"]["settlement_only"] is True
    actions = recovered["interaction_contract"]["cli_channel"]["next_cli_actions"]
    refreshed = call(*shlex.split(actions[0])[1:], "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"

    pending = _guard(call, TURN)
    cli = pending["interaction_contract"]["cli_channel"]
    assert cli["settlement_plan"]["identity"] == identity
    assert [step["kind"] for step in cli["settlement_plan"]["ordered_steps"]] == ["quota_spend"]
    assert cli["next_cli_actions"] == [actions[1]]
    debit = shlex.split(actions[1])[1:]
    denied = call(*debit, expected_code=1)
    assert denied["appended"] is False
    assert denied["quota_rejection_action"] == "throttled_skip"

    # The spend owner still enforces its existing policy. An owner increase
    # restores the allowance, then the same owed debit can settle exactly once.
    config["goals"][0]["quota"]["allowed_slots"] = 2
    registry.write_text(json.dumps(config))
    assert call(*debit)["appended"] is True
    assert call(*debit)["appended"] is False
    settled = _guard(call, TURN)
    assert settled["effective_action"] == "heartbeat_settled_skip"
    assert settled["heartbeat_receipt"]["settlement_identity"] == identity
    assert settled["should_run"] is False
