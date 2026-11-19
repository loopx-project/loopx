"""A foreign User gate must not become the admitted Agent's stop instruction."""

from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

from loopx.control_plane.goals.activation import build_goal_activation
from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.status.monitor_display_projection import build_project_asset
from loopx.control_plane.testing.quota_fixtures import quota_status_payload, quota_todo_item
from tests.control_plane.codex_checkpoint_fixture import read_cli_policy_payload
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _initialize_fixture_authority, _run_cli,
    _spend_run_count, _write_fixture,
)


PEER = "boundary-peer"


def _fixture(tmp_path, provider, gate_scope):
    project, runtime, registry = _write_fixture(tmp_path)
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["registered_agents"].append(PEER)
    registry.write_text(json.dumps(data))
    scope = "global_gate=true" if gate_scope == "global" else (
        f"blocks_agent={AGENT_ID if gate_scope == 'current' else PEER}"
    )
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text() + (
        "\n## User Todo\n\n- [ ] [P0] Decide the protected operation.\n"
        "  <!-- loopx:todo todo_id=todo_boundary_gate role=user status=open "
        f"task_class=user_gate {scope} -->\n"
    ))
    _initialize_fixture_authority(project, runtime, provider=provider)
    return project, runtime, registry


def _guard(project, runtime, registry, delivery, turn):
    rc, result = _run_cli(
        registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--codex-app", "--turn-instance-id", turn,
        "--context-delivery", delivery, cwd=project,
    )
    assert rc == 0, result
    return read_cli_policy_payload(json.dumps(result), returncode=rc)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("delivery", ["off", "auto"])
@pytest.mark.parametrize("gate_scope", ["other", "current", "global"])
def test_native_guard_scopes_stop_with_its_execution_lane(
    tmp_path, provider, delivery, gate_scope,
):
    project, runtime, registry = _fixture(tmp_path, provider, gate_scope)
    rc, before = _run_cli(registry, runtime, "status", "--goal-id", GOAL_ID)
    assert rc == 0, before
    original = before["attention_queue"]["items"][0]["project_asset"]
    result = _guard(project, runtime, registry, delivery, "boundary-" + gate_scope)

    if gate_scope == "other":
        assert result["normal_delivery_allowed"]
        assert result["selected_todo"]["todo_id"] == TODO_ID
        assert result["waiting_on"] == "codex"
        assert result["goal_boundary"]["stop_condition"] != original["stop_condition"]
        assert "gate approval" in result["goal_boundary"]["stop_condition"]
        summary = result["user_todo_summary"]
        assert summary["open_count"] == 0
        assert summary["other_agent_scoped_items"][0]["blocks_agent"] == PEER
    else:
        assert not result["normal_delivery_allowed"]
        assert not result["interaction_contract"]["agent_channel"]["delivery_allowed"]
        assert result["goal_boundary"]["stop_condition"] == original["stop_condition"]
        assert "agent_scoped_user_gate_override" not in result

    # The scoped view cannot change the original global status or close the gate.
    rc, after = _run_cli(registry, runtime, "status", "--goal-id", GOAL_ID)
    assert rc == 0, after
    actual = after["attention_queue"]["items"][0]["project_asset"]
    for field in ("owner", "gate", "support_mode", "stop_condition"):
        assert actual[field] == original[field]
    rc, todos = _run_cli(registry, runtime, "todo", "list", "--goal-id", GOAL_ID,
                         "--todo-id", "todo_boundary_gate")
    assert rc == 0 and todos["todo"]["status"] == "open", todos
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("control", ["stopped", "exhausted"])
def test_foreign_gate_cannot_override_stop_or_quota(tmp_path, provider, control):
    project, runtime, registry = _fixture(tmp_path, provider, "other")
    data = json.loads(registry.read_text())
    if control == "stopped":
        data["goals"][0]["activation"] = build_goal_activation(
            state="stopped", updated_at=datetime.now(timezone.utc).isoformat(),
            reason="Stop the isolated boundary fixture.",
        )
    else:
        data["goals"][0]["quota"]["allowed_slots"] = 0
    registry.write_text(json.dumps(data))
    result = _guard(project, runtime, registry, "off", "boundary-" + control)
    assert not result["should_run"]
    assert not result["normal_delivery_allowed"]
    assert not result["interaction_contract"]["agent_channel"]["delivery_allowed"]
    if control == "stopped":
        assert "agent_scoped_user_gate_override" not in result
    else:
        assert result["state"] == "throttled"
        diagnostic = result["agent_scoped_user_gate_override"]
        assert diagnostic["to_state"] == "throttled"
        assert "selected_action" not in diagnostic
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize(("explicit_stop", "opaque_stop"), [
    (None, None),
    ("Stop at the accepted handoff boundary.", None),
    ("stop until the controller or owner resolves this gate", None),
    (None, "Stop if private material would be exposed."),
])
def test_scoped_projection_retains_explicit_stop_and_input_snapshot(explicit_stop, opaque_stop):
    action = "Validate the independent change."
    asset = build_project_asset(
        status="active_state_user_gate", waiting_on="controller",
        recommended_action="Decide the protected operation.",
        operator_question=None, agent_command=None, missing_gates=None,
        next_handoff_condition=explicit_stop,
    )
    if opaque_stop:
        asset["stop_condition"] = opaque_stop
    status = quota_status_payload(
        goal_id="boundary-fixture", status="active_state_user_gate",
        quota_state="operator_gate", recommended_action=asset["next_action"],
        agent_todo_items=[quota_todo_item(
            todo_id="todo_boundary_work", claimed_by=AGENT_ID, title=action,
        )], user_todo_items=[quota_todo_item(
            todo_id="todo_boundary_gate", role="user", task_class="user_gate",
            blocks_agent=PEER, title="Decide the protected operation.",
        )], claim_scope_agent_id=AGENT_ID,
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT_ID, PEER]},
        item_extra={"next_handoff_condition": explicit_stop},
        project_asset_extra=asset,
    )
    before = deepcopy(status)
    result = build_quota_should_run(status, goal_id="boundary-fixture", agent_id=AGENT_ID)
    assert result["normal_delivery_allowed"]
    assert result["waiting_on"] == "codex"
    if explicit_stop or opaque_stop:
        assert result["goal_boundary"]["stop_condition"] == (explicit_stop or opaque_stop)
    else:
        assert result["goal_boundary"]["stop_condition"] != asset["stop_condition"]
    assert status == before
