"""Display pressure must not change the current lane's replan commitments."""
import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.presentation.renderers.quota_markdown import render_quota_should_run_markdown
from test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, _configure_selected_todo_replan_fixture, _run_cli, _write_fixture,
)


@pytest.mark.parametrize("peers,reverse", [(0, False), (20, False), (20, True)])
@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_real_cli_owned_chain_is_independent_of_display_pressure(tmp_path, monkeypatch, peers, reverse, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path)
    _configure_selected_todo_replan_fixture(project, registry)
    state = project / f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    prefix, rows = state.read_text().split("## Agent Todo\n\n", 1)
    lines = rows.splitlines(keepends=True)
    pairs = ["".join(lines[i:i + 2]) for i in range(0, len(lines), 2)]
    peer_rows = "".join(
        f"- [ ] [P2] Independent peer work {i}.\n"
        f"  <!-- loopx:todo todo_id=todo_peer_{i:012d} status=open "
        "task_class=advancement_task action_kind=validate claimed_by=peer-agent -->\n"
        for i in range(peers)
    )
    state.write_text(prefix + "## Agent Todo\n\n" + "".join(reversed(pairs) if reverse else pairs) + peer_rows)
    try:
        if provider != "legacy":
            fields = parse_active_state_todos(state.read_text(), item_limit=None)
            projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID,
                todos=fields["agent_todos"]["items"], handoff_mode="soft_claim")
            initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
        rc, result = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
            "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
            "--turn-instance-id", "turn-owned-count", "--scan-path", str(project))
        assert rc == 0
        # The source contains exactly 15 owned advancement commitments in every
        # arm. The fixed replan threshold remains 15, not the display's length.
        assert result["goal_frontier_projection"]["remaining_advancement_frontier"]["current_agent_claimed_advancement_count"] == 15
        assert "current_agent_advancement=15" in render_quota_should_run_markdown(result)
        obligation = result["autonomous_replan_obligation"]
        trigger = next(row for row in obligation["triggers"] if row["kind"] == "long_todo_chain")
        assert trigger["trigger_count"] == 15
        assert trigger["threshold"] == 15
        assert result["normal_delivery_allowed"] is False
    finally:
        restart_effect_runtime()
