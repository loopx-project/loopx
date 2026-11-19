"""Blocker explanations name the work actually selected by the native guard."""
from __future__ import annotations

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.quota.blocked_transition_notice import collect_blocked_transition_notices
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("selection", ["first", "explicit", "continuity"])
def test_native_fallback_notice_tracks_final_selection(tmp_path, monkeypatch, provider, selection):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    goal, agent = "selected-fallback", "agent-a"
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nDeliver the compatible export.\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id=goal, domain="software", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=[agent], quota_allowed_slots=None)
    first_text, chosen_text = "Prepare the independent export.", "Validate the selected recovery path."
    todos = [{"schema_version": "todo_item_v0", "todo_id": todo, "role": "agent", "text": text,
        "status": status, "done": False, "priority": priority, "task_class": "advancement_task",
        "action_kind": "implement", "index": index, "source_section": "Agent Todo",
        "archive_state": "active", "claimed_by": agent, **extra}
        for index, (todo, text, status, priority, extra) in enumerate([
            ("todo_primary", "Wait for the original input.", "blocked", "P0",
                {"reason": "The external input is unavailable."}),
            ("todo_first", first_text, "open", "P1", {}),
            ("todo_selected", chosen_text, "open", "P1", {}),
        ], 1)]
    initialize_canonical_authority(runtime, goal,
        build_todo_runtime_shadow_projection(goal_id=goal, todos=todos, handoff_mode="soft_claim"),
        state_path=state, provider=provider)

    def cli(*arguments):
        code, result = run_json_cli_result(*arguments, registry_path=registry, runtime_root=runtime)
        assert code == 0, result
        return result

    before = cli("todo", "list", "--goal-id", goal)
    provisional = cli("quota", "should-run", "--goal-id", goal, "--agent-id", agent)
    chosen = "todo_first" if selection == "first" else "todo_selected"
    text, absent = (first_text, chosen_text) if selection == "first" else (chosen_text, first_text)
    turn = f"fallback-{selection}"
    args = ("quota", "should-run", "--goal-id", goal, "--agent-id", agent,
        "--codex-app", "--turn-instance-id", turn)
    packet = cli(*args, "--todo-id", chosen)

    def assert_notice(observed):
        assert observed["selected_todo"]["todo_id"] == chosen
        fallback = observed["blocked_priority_fallback"]
        assert fallback["selected_executable"]["todo_id"] == chosen
        assert [row["todo_id"] for row in fallback["blocked_items"]] == ["todo_primary"]
        notice = fallback["blocked_transition_notices"][0]
        assert text in notice["impact"] and absent not in notice["impact"]
        assert text in notice["next_action"] and absent not in notice["next_action"]
        assert notice["owner_must_know"] is True and notice["owner_must_act"] is False
        assert "external input" in notice["cause"]
        interaction = observed["interaction_contract"]
        assert interaction["agent_channel"]["delivery_allowed"] is True, {
            "mode": interaction["mode"], "reason": observed.get("reason"),
            "pending": interaction["agent_channel"].get("required_reads"),
        }
        assert interaction["user_channel"]["action_required"] is False
        assert text in interaction["user_channel"]["reason"]
        assert absent not in interaction["user_channel"]["reason"]
        envelope = build_turn_envelope(observed)
        assert envelope["action"]["selected_todo"]["todo_id"] == chosen
        assert text in envelope["user"]["reason"]
        notices, _ = collect_blocked_transition_notices(
            {"attention_queue": {"items": [{"goal_id": goal, "agent_todos": {"items": todos}}]}},
            goal, observed)
        assert notices == [notice]
        return notice

    original = assert_notice(packet)
    prior = provisional["blocked_priority_fallback"]["blocked_transition_notices"][0]
    assert {key: value for key, value in original.items() if key not in {"impact", "next_action"}} == {
        key: value for key, value in prior.items() if key not in {"impact", "next_action"}}
    # Same immutable Turn, full/compact projections, and sink collection keep
    # the same blocker identity/cause while describing its actual fallback.
    for options in [(), ("--include-detail", "all")]:
        repeated = cli(*args, *options)
        assert assert_notice(repeated)["blocker_revision"] == original["blocker_revision"]
    if selection == "continuity":
        cli("refresh-state", "--goal-id", goal, "--agent-id", agent, "--todo-id", chosen,
            "--turn-instance-id", turn, "--classification", "validated_progress",
            "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation", "--delivery-workspace-path", str(tmp_path),
            "--no-global-sync", "--suppress-external-sinks")
        cli("quota", "spend-slot", "--goal-id", goal, "--agent-id", agent, "--todo-id", chosen,
            "--turn-instance-id", turn, "--slots", "1", "--source", "heartbeat", "--execute")
        continued = cli("quota", "should-run", "--goal-id", goal, "--agent-id", agent,
            "--codex-app", "--turn-instance-id", "fallback-next-turn")
        assert continued["selected_todo"]["selected_by"] == "in_flight_todo"
        # Continuity advises the next choice; a fresh managed Turn must still
        # bind that eligible work before delivery is admitted.
        assert continued["interaction_contract"]["cli_channel"]["selection_required"] is True
        continued = cli("quota", "should-run", "--goal-id", goal, "--agent-id", agent,
            "--codex-app", "--turn-instance-id", "fallback-next-turn", "--todo-id", chosen)
        assert assert_notice(continued)["blocker_revision"] == original["blocker_revision"]
    assert cli("todo", "list", "--goal-id", goal)["todos"] == before["todos"]
