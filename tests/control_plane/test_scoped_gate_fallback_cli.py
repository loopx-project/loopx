"""Author a gate from the CLI hint and admit only independent work."""
import shlex

import pytest
from canonical_authority_fixture import (
    empty_todo_display,
    initialize_canonical_source_fixture,
    isolate_sqlite_runtime,
)
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize(
    ("target", "publish_status"),
    [("todo_publish", "open"), ("todo_publish", "blocked"), ("todo_inspect", "open"), (None, "open")],
)
def test_quota_gate_authoring_and_continuation(
    tmp_path, monkeypatch, provider, target, publish_status,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    state, runtime, registry = tmp_path / "STATE.md", tmp_path / "runtime", tmp_path / "registry.json"
    state.write_text(
        "---\nstatus: active\n---\n# Goal\n\n## Objective\n"
        "Inspect and publish the approved report.\n\n## Agent Todo\n\n"
        "- [ ] [P1] Inspect the report\n"
        "  <!-- loopx:todo todo_id=todo_inspect role=agent status=open task_class=advancement_task claimed_by=agent-a action_kind=inspect_report -->\n"
        "- [ ] [P0] Publish the report\n"
        f"  <!-- loopx:todo todo_id=todo_publish role=agent status={publish_status} task_class=advancement_task claimed_by=agent-a action_kind=publish_report -->\n"
    )
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry, goal_id="goal-a",
                           domain="software", adapter_kind="generic_project_goal_v0", state_file=str(state),
                           registered_agents=["agent-a"], quota_allowed_slots=None)
    initialize_canonical_source_fixture(runtime, "goal-a", state_path=state, provider=provider)
    empty_todo_display(state)
    route = {"registry_path": registry, "runtime_root": runtime}
    guard_args = ("quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a")
    code, initial = run_json_cli_result(*guard_args, **route)
    assert code == 0, initial
    template = initial["todo_write_hint"]["user_gate_command_template"]
    command = shlex.split(
        template.replace("<agent-id>", "agent-a")
        .replace("<todo-id>", target or "todo_publish")
        .replace("<blocking user action and verification>", "Configure publication access and confirm setup")
    )
    if target is None:
        # Model a legacy lane-only gate for a caller that still omits the target.
        command = [arg for arg in command if arg not in {"--unblocks-todo-id", "todo_publish"}]
    code, change = run_json_cli_result(*command[1:], "--action-kind", "publish_report", **route)
    assert code == 0, change
    gate_id = change["todo_id"]
    before = state.read_bytes() if state.exists() else None
    authority_before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    code, packet = run_json_cli_result(*guard_args, **route)
    assert code == 0, packet
    fallback = packet.get("scoped_user_gate_fallback")
    # The explicit target wins even when the two action labels disagree.
    if target is None:
        # Different labels cannot prove independence from an unscoped approval.
        assert fallback is None
        assert packet["should_run"] is False
    else:
        expected = "todo_publish" if target == "todo_inspect" else "todo_inspect"
        assert fallback["selected_executable"]["todo_id"] == expected
        assert fallback["blocked_user_gate"]["todo_id"] == gate_id
        assert packet["selected_todo"]["todo_id"] == expected
        assert packet["interaction_contract"]["agent_channel"]["must_attempt"] is True, {
            key: packet.get(key) for key in (
                "state", "reason", "decision", "effective_action", "execution_obligation",
                "action_selection", "agent_workspace_guard", "interaction_contract",
            )
        }
        assert packet["interaction_contract"]["user_channel"]["notify"] == "NOTIFY"
        assert packet["interaction_contract"]["agent_channel"]["delivery_allowed"] is True
        denied_code, denied = run_json_cli_result(
            *guard_args, "--turn-instance-id", "try-gated-todo", "--todo-id", target, **route,
        )
        assert denied_code == 1, denied
        # The still-open target is deferred by admission; an already blocked
        # target is ineligible for selection. Neither can dispatch work.
        expected_error = (
            "quota_action_selection_rejected" if publish_status == "blocked"
            else "quota_action_selection_deferred"
        )
        assert denied["error_code"] == expected_error
        assert denied["execution_obligation"]["must_attempt_work"] is False
    assert packet["requires_user_action"] is True
    assert (state.read_bytes() if state.exists() else None) == before
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == authority_before
