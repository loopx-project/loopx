"""Real source -> quota CLI -> fallback, with permanent display non-authoritative."""
import json

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime, empty_todo_display
from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result


@pytest.mark.parametrize("provider", ["markdown", "file", "sqlite"])
@pytest.mark.parametrize("explicit_target", [False, True, None])
def test_quota_fallback_uses_real_source_and_explicit_relations(tmp_path, monkeypatch, provider, explicit_target):
    if provider == "sqlite":
        isolate_sqlite_runtime(tmp_path, monkeypatch)
    state, runtime, registry = tmp_path / "STATE.md", tmp_path / "runtime", tmp_path / "registry.json"
    state.write_text(
        "# Goal\n\n## Agent Todo\n\n"
        "- [ ] [P1] Inspect the report\n"
        "  <!-- loopx:todo todo_id=todo_inspect role=agent status=open task_class=advancement_task claimed_by=agent-a action_kind=inspect_report -->\n"
        "- [ ] [P0] Publish the report\n"
        "  <!-- loopx:todo todo_id=todo_publish role=agent status=open task_class=advancement_task claimed_by=agent-a action_kind=publish_report -->\n"
        "\n## User Todo / Owner Review Reading Queue\n\n"
        "- [ ] Review publication\n"
        "  <!-- loopx:todo todo_id=todo_gate role=user status=open task_class=user_gate blocks_agent=agent-a action_kind=publish_report "
        + ("" if explicit_target is None else
           "unblocks_todo_id=todo_inspect " if explicit_target else "unblocks_todo_id=todo_publish ") + "-->\n"
    )
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry, goal_id="goal-a",
                           domain="software", adapter_kind="generic_project_goal_v0", state_file=str(state),
                           registered_agents=["agent-a"], quota_allowed_slots=None)
    if provider != "markdown":
        goal = json.loads(registry.read_text())["goals"][0]
        projection, _ = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime,
                                                             state_path=state, registry_path=registry)
        initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
        empty_todo_display(state)
    before = state.read_bytes() if state.exists() else None
    authority_before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
                                        registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    fallback = packet.get("scoped_user_gate_fallback")
    # The explicit target wins even when the two action labels disagree.
    if explicit_target is None:
        # Different labels cannot prove independence from an unscoped approval.
        assert fallback is None
        assert packet["should_run"] is False
    else:
        expected = "todo_publish" if explicit_target else "todo_inspect"
        assert fallback["selected_executable"]["todo_id"] == expected
        assert fallback["blocked_agent_items"][0]["todo_id"] != expected
    assert packet["requires_user_action"] is True
    assert (state.read_bytes() if state.exists() else None) == before
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == authority_before
