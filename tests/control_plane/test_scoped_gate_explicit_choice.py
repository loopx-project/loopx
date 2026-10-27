"""An unrelated approval does not turn the fallback ranking into authority."""

import pytest

from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload,
    quota_todo_item,
)


AGENT = "scoped-choice-agent"
GOAL = "scoped-choice-goal"
SCOPE = {"kind": "production", "granularity": "action", "scope_key": "release"}


def choice_status(*, blocked=False, state="operator_gate"):
    items = [quota_todo_item(
        todo_id=name, index=index, priority=priority, claimed_by=AGENT,
        title="Validate the selected change.", action_kind="validate",
        required_decision_scopes=[SCOPE] if blocked and index == 2 else [],
    ) for index, (name, priority) in enumerate([
        ("todo_recommended", "P0"), ("todo_alternative", "P1")
    ], 1)]
    gate = quota_todo_item(
        todo_id="todo_release_gate", role="user", task_class="user_gate",
        title="Approve the release.", blocks_agent=AGENT, decision_scope=SCOPE,
    )
    return quota_status_payload(
        goal_id=GOAL, status="active", quota_state=state, safe_bypass=True,
        recommended_action="Validate an independent change.",
        agent_todo_items=items, user_todo_items=[gate],
        claim_scope_agent_id=AGENT,
        coordination={"agent_model": "peer_v1", "registered_agents": [AGENT]},
    )


def test_explicit_independent_choice_is_not_restricted_to_ranked_fallback():
    packet = build_quota_should_run(
        choice_status(), goal_id=GOAL, agent_id=AGENT,
        requested_action_todo_id="todo_alternative",
    )
    assert packet["action_selection_qualification"]["state"] == "qualified"
    assert packet["selected_todo"]["todo_id"] == "todo_alternative"
    assert packet["interaction_contract"]["agent_channel"]["delivery_allowed"]
    assert packet["requires_user_action"]  # The release approval remains open.


@pytest.mark.parametrize("blocked,state,selected", [
    (True, "operator_gate", "todo_alternative"),
    (False, "budget_exhausted", "todo_alternative"),
    (False, "operator_gate", "todo_missing"),
])
def test_explicit_choice_preserves_scope_budget_and_eligibility(blocked, state, selected):
    packet = build_quota_should_run(
        choice_status(blocked=blocked, state=state), goal_id=GOAL, agent_id=AGENT,
        requested_action_todo_id=selected,
    )
    assert packet["action_selection_qualification"]["state"] != "qualified"
    assert not packet["interaction_contract"]["agent_channel"]["delivery_allowed"]


def test_default_fallback_is_unchanged():
    packet = build_quota_should_run(choice_status(), goal_id=GOAL, agent_id=AGENT)
    assert packet["selected_todo"]["todo_id"] == "todo_recommended"


@pytest.mark.parametrize("provider", ["sqlite", "file"])
def test_cli_begin_selects_once_and_replay_keeps_original_identity(tmp_path, provider):
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, ALTERNATIVE_TODO_ID, GOAL_ID, TODO_ID,
        _configure_selectable_alternative, _initialize_fixture_authority,
        _run_cli, _write_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text() + (
        "\n## User Todo\n\n- [ ] [P0] Approve the release.\n"
        f"  <!-- loopx:todo todo_id=todo_release_gate status=open task_class=user_gate "
        f"blocks_agent={AGENT_ID} decision_scope=production:action:release -->\n"
    ))
    _initialize_fixture_authority(project, runtime, provider=provider)
    guard = ("quota", "should-run", "--runtime-profile", "codex_app_ssh_goal",
             "--goal-id", GOAL_ID, "--agent-id", AGENT_ID)
    rc, first = _run_cli(registry, runtime, *guard, "--begin-turn",
                         "--todo-id", ALTERNATIVE_TODO_ID)
    assert rc == 0, first.get("error")
    assert first["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    assert first["interaction_contract"]["agent_channel"]["delivery_allowed"]
    turn = first["heartbeat_receipt"]["turn_instance_id"]
    rc, replay = _run_cli(registry, runtime, *guard, "--turn-instance-id", turn,
                          "--todo-id", ALTERNATIVE_TODO_ID)
    assert rc == 0, replay.get("error")
    assert replay["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    rc, refused = _run_cli(registry, runtime, *guard, "--turn-instance-id", turn,
                           "--todo-id", TODO_ID)
    assert rc != 0
    assert refused["error_code"] == "heartbeat_receipt_identity_conflict"
