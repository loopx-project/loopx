"""A compact candidate view cannot replace an admitted Turn's identity."""
import copy
import json
from pathlib import Path

import pytest

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID,
    GOAL_ID,
    TODO_ID,
    _configure_autonomous_replan_fixture,
    _run_cli,
    _write_fixture,
)


@pytest.mark.parametrize("hidden_count", [0, 60])
@pytest.mark.parametrize("checked", [False, True])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_deferred_replay_keeps_original_binding_during_replan(
    tmp_path: Path, hidden_count: int, checked: bool, provider: str,
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture,
) -> None:
    if provider == "sqlite":
        from canonical_authority_fixture import isolate_sqlite_runtime

        isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    turn = "turn-original-deferred"
    guard = ("quota", "should-run", "--runtime-profile", "codex_app_ssh_goal",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--turn-instance-id", turn, "--todo-id", TODO_ID)
    rc, original = _run_cli(registry, runtime, *guard)
    assert rc == 0, original
    identity = original["heartbeat_receipt"]["settlement_identity"]
    assert identity["todo_id"] == TODO_ID

    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    text = state.read_text().replace("status: active-read-only", "status: active")
    # A new replan is legitimately due, but does not own the old Turn.
    _configure_autonomous_replan_fixture(project, runtime, registry)
    text = text.replace(f"todo_id={TODO_ID} status=open",
        f"todo_id={TODO_ID} status=deferred resume_when=todo_done:todo_prior")
    if checked:
        text = text.replace("- [ ] [P1] Validate", "- [x] [P1] Validate")
    text += ("\n- [x] [P1] Prior dependency is complete.\n"
        f"  <!-- loopx:todo todo_id=todo_prior status=done claimed_by={AGENT_ID} "
        "task_class=advancement_task -->\n")
    rows = "".join(f"- [ ] [P0] Independent waiting candidate {i}.\n"
        f"  <!-- loopx:todo todo_id=todo_candidate_{i} status=deferred claimed_by={AGENT_ID} "
        "resume_when=todo_done:todo_prior task_class=advancement_task action_kind=validate -->\n"
        for i in range(hidden_count))
    text = text.replace("## Agent Todo\n\n", "## Agent Todo\n\n" + rows)
    state.write_text(text)
    from canonical_authority_fixture import advance_canonical_fixture_authority
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

    todos = parse_active_state_todos(text, item_limit=None)["agent_todos"]["items"]
    advance_canonical_fixture_authority(runtime, GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=GOAL_ID,
            todos=todos, handoff_mode="soft_claim", leases=[]),
        operation_id="deferred-candidate-frontier")

    from loopx.cli import main
    from loopx.cli_commands import quota

    live = quota.build_live_quota_should_run_decision

    def compact_status(status, **kwargs):
        # The display snapshot can omit the original row; the provider still
        # owns it. Exercise the real reader, router, and receipt replay.
        def compact(value):
            if isinstance(value, list):
                return [compact(row) for row in value
                    if not isinstance(row, dict) or row.get("todo_id") != TODO_ID]
            if isinstance(value, dict):
                return {key: compact(row) for key, row in value.items()}
            return value
        return live(compact(copy.deepcopy(status)) if hidden_count else status, **kwargs)

    monkeypatch.setattr(quota, "build_live_quota_should_run_decision", compact_status)
    capsys.readouterr()
    rc = main(["--format", "json", "--registry", str(registry),
        "--runtime-root", str(runtime), *guard, ])
    replay = json.loads(capsys.readouterr().out)
    assert rc == 0, replay
    assert replay["heartbeat_receipt"]["settlement_identity"] == identity
    channel = replay["interaction_contract"]["cli_channel"]
    plan = channel.get("settlement_plan")
    assert plan is None or plan["identity"]["todo_id"] == TODO_ID, channel
    assert replay["should_run"] is False, replay.get("effective_action")
    assert replay["normal_delivery_allowed"] is False
    assert channel["spend_after_validation"] is False
