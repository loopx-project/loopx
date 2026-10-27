"""An unrelated approval does not turn the fallback ranking into authority."""

import json
import shlex
import subprocess
import sys

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


@pytest.mark.parametrize("provider", ["sqlite", "file"])
def test_rejected_turn_recovers_to_validated_work_and_one_spend(tmp_path, provider):
    """A real scoped rejection must recover progress, not just an admission packet."""
    from tests.control_plane.test_quota_settlement_cli import (
        AGENT_ID, ALTERNATIVE_TODO_ID, GOAL_ID, SECOND_ALTERNATIVE_TODO_ID,
        TODO_ID, TURN_ID, _configure_selectable_alternative,
        _heartbeat_receipt_count, _initialize_fixture_authority,
        _run_cli, _run_generated_cli, _spend_run_count, _write_fixture,
    )

    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selectable_alternative(project)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state.write_text(state.read_text().replace(
        "Advance the independent alternative delivery.",
        "Compute and validate the input count and total.",
    ).replace(
        f"todo_id={TODO_ID} status=open",
        f"todo_id={TODO_ID} required_decision_scopes=production:action:release status=open",
    ) + (
        "\n## User Todo\n\n- [ ] [P0] Approve the release.\n"
        f"  <!-- loopx:todo todo_id=todo_release_gate status=open task_class=user_gate "
        f"blocks_agent={AGENT_ID} decision_scope=production:action:release -->\n"
    ))
    initial = _initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*args):
        return _run_cli(registry, runtime, *args, cwd=project)

    def projected(command):
        return _run_generated_cli(command, registry_path=registry, cwd=project)

    def read_todos():
        rc, result = cli("todo", "list", "--goal-id", GOAL_ID)
        assert rc == 0, result
        return result

    # The validator independently derives acceptance from input, not a supplied
    # result hash. Its arguments and all state are confined to this fixture.
    (project / "counts.json").write_text("[3, 5, 7]\n")
    artifact = project / "summary.json"
    validator = [sys.executable, "-c", (
        "import json,sys; from pathlib import Path; "
        "x=json.loads(Path(sys.argv[1]).read_text()); "
        "y=json.loads(Path(sys.argv[2]).read_text()); "
        "assert y == {'count':len(x),'total':sum(x)}"
    ), str(project / "counts.json"), str(artifact)]
    rc, configured = cli(
        "todo", "update", "--goal-id", GOAL_ID, "--todo-id", ALTERNATIVE_TODO_ID,
        "--agent-id", AGENT_ID,
        "--validation-command-json", json.dumps(validator),
        "--update-operation-id", "configure-independent-validation",
        "--update-expected-provider-revision", initial["provider_revision"],
    )
    assert rc == 0, json.dumps(configured)
    before = read_todos()
    guard = ("quota", "should-run", "--runtime-profile", "codex_app_heartbeat",
             "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--turn-instance-id", TURN_ID)
    rc, rejected = cli(*guard, "--todo-id", TODO_ID)
    assert rc == 1, rejected
    assert rejected["error_code"] == "quota_action_selection_deferred"
    assert rejected["action_selection_qualification"]["reason"] == "delivery_not_allowed"
    assert rejected["heartbeat_receipt"]["status"] == "not_committed"
    assert _heartbeat_receipt_count(runtime, TURN_ID) == 0
    assert _spend_run_count(runtime) == 0
    reentry = rejected["interaction_contract"]["cli_channel"]["next_cli_actions"][0]
    args = shlex.split(reentry)
    assert args[args.index("--turn-instance-id") + 1] == TURN_ID
    assert "--todo-id" not in args and "--begin-turn" not in args
    rc, recovered = projected(reentry)
    assert rc == 0, recovered
    # Scoped fallback selects independent work on reentry. Explicit replay must
    # retain that same selection, rather than create or silently rebind a Turn.
    assert recovered["selected_todo"]["todo_id"] == ALTERNATIVE_TODO_ID
    rc, selected = cli(*guard, "--todo-id", ALTERNATIVE_TODO_ID)
    assert rc == 0, selected
    identity = selected["heartbeat_receipt"]["settlement_identity"]
    assert identity == recovered["heartbeat_receipt"]["settlement_identity"]
    assert identity["turn_instance_id"] == TURN_ID
    assert selected["interaction_contract"]["agent_channel"]["delivery_allowed"]
    assert selected["selected_todo"]["claim_required_before_work"]
    rc, claim = cli("todo", "claim", "--goal-id", GOAL_ID,
                    "--todo-id", ALTERNATIVE_TODO_ID,
                    "--agent-id", AGENT_ID, "--claimed-by", AGENT_ID)
    assert rc == 0 and claim["claimed_by"] == AGENT_ID, claim

    artifact.write_text('{"count":3,"total":14}\n')
    assert subprocess.run(validator, cwd=project, capture_output=True).returncode != 0
    artifact.write_text('{"count":3,"total":15}\n')
    verified = subprocess.run(validator, cwd=project, capture_output=True)
    assert verified.returncode == 0, verified.stderr
    actions = selected["interaction_contract"]["cli_channel"]["next_cli_actions"]
    refresh_command = next(command for command in actions if " refresh-state " in command)
    refresh_command = (refresh_command.replace("<validated_progress>", "validated_progress")
                       .replace("<scale>", "implementation")
                       .replace("<outcome>", "outcome_progress"))
    rc, refresh = projected(refresh_command + (
        " --delivery-boundary in_flight_continuation --delivery-workspace-path "
        + shlex.quote(str(project)) + " --no-global-sync --suppress-external-sinks"
    ))
    assert rc == 0, refresh
    assert refresh["settlement_identity"] == identity
    assert refresh["settlement_progress"]["state"] == "spend_required"
    spend_command = refresh["settlement_owed"]["command"]
    args = shlex.split(spend_command)
    assert args[args.index("--source") + 1] == "heartbeat"
    assert "--execute" in args
    rc, spend = projected(spend_command)
    assert rc == 0, spend
    assert spend["settlement_identity"] == identity
    assert spend["delivery_workspace_validated"]
    assert spend["settlement_progress"]["state"] == "settled"
    rc, retried = projected(spend_command)
    assert rc == 0 and retried["appended"] is False, retried
    assert _spend_run_count(runtime) == 1

    rc, completed = cli(
        "todo", "complete", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", ALTERNATIVE_TODO_ID, "--turn-instance-id", TURN_ID,
        "--evidence", "Independent validator checked summary.json against counts.json.",
        "--no-follow-up",
    )
    assert rc == 0 and completed["completed"], json.dumps(completed)
    assert completed["validation_receipt"]["passed"] is True
    after = read_todos()
    # These independent CLI reads include the canonical records, not Markdown
    # written by the work producer. The original gate and covered Todo survive.
    def rows(payload):
        return {row["todo_id"]: row for row in payload["todos"]}

    old_rows, new_rows = rows(before), rows(after)
    for todo_id in (TODO_ID, "todo_release_gate"):
        assert new_rows[todo_id] == old_rows[todo_id]
    assert new_rows[ALTERNATIVE_TODO_ID]["status"] == "done"
    assert subprocess.run(validator, cwd=project, capture_output=True).returncode == 0
    rc, historical = cli(*guard)
    assert rc == 0, historical
    assert historical["heartbeat_receipt"]["settlement_identity"] == identity
    assert not historical["interaction_contract"]["agent_channel"]["delivery_allowed"]
    assert historical["interaction_contract"]["mode"] == "heartbeat_settled_skip"
    new_guard = tuple("turn-next-independent" if arg == TURN_ID else arg for arg in guard)
    rc, fresh = cli(*new_guard, "--todo-id", ALTERNATIVE_TODO_ID)
    assert rc == 1 and fresh["error_code"] == "quota_action_selection_rejected", fresh
    rc, continuation = projected(
        fresh["interaction_contract"]["cli_channel"]["next_cli_actions"][0])
    assert rc == 0, continuation
    assert continuation["selected_todo"]["todo_id"] == SECOND_ALTERNATIVE_TODO_ID
    assert continuation["interaction_contract"]["agent_channel"]["delivery_allowed"]
    assert _spend_run_count(runtime) == 1
