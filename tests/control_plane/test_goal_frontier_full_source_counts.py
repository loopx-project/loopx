"""Frontier facts must survive summary display limits without inventing work."""
from __future__ import annotations

import pytest

from loopx.control_plane.goals.goal_frontier import (
    build_goal_frontier_projection_from_summaries,
)
from loopx.control_plane.todos.quota_summary import summarize_user_todos_for_quota
from loopx.control_plane.todos.todo_summary import compact_todo_group


def _row(index, **fields):
    return {"todo_id": f"todo_frontier_{index}", "text": f"Work {index}",
            "role": "agent", "task_class": "advancement_task", "status": "open",
            "index": index if isinstance(index, int) else 0,
            "source_section": "Agent Todo", **fields}


def _frontier(summary, *, agent_id="agent-a"):
    return build_goal_frontier_projection_from_summaries(
        goal_id="frontier-counts", agent_id=agent_id, user_todo_summary=None,
        agent_todo_summary=summary, work_lane_contract=None, replan_obligation=None,
    )


@pytest.mark.parametrize("count", [0, 1, 8, 12, 32])
@pytest.mark.parametrize("quota", [False, True])
def test_advancement_frontier_uses_full_source_counts(count, quota):
    summary = compact_todo_group(
        [_row(i) for i in range(count)], role="agent", source_section="Agent Todo",
        include_empty_source=True, item_limit=None if quota else 1,
    )
    if quota:
        summary = summarize_user_todos_for_quota(summary, agent_identity={"agent_id": "agent-a"})
    assert len(summary["executable_backlog_items"]) <= 8
    assert _frontier(summary)["normalized_progress"]["agent_advancement_open_count"] == count


@pytest.mark.parametrize("ready,blocked", [(1, 0), (8, 0), (12, 0), (9, 3), (0, 12)])
@pytest.mark.parametrize("quota", [False, True])
def test_deferred_frontier_counts_ready_and_blocked_before_display(ready, blocked, quota):
    items = [_row(i, status="deferred", claimed_by="agent-a" if i % 2 == 0 else "agent-b",
                  resume_when=f"todo_done:todo_frontier_{'done' if i < ready else 'pending'}")
             for i in range(ready + blocked)]
    dependencies = [_row("done", status="done", no_followup=True), _row("pending")]
    summary = compact_todo_group(
        items, role="agent", source_section="Agent Todo", resume_source_items=[*items, *dependencies],
        item_limit=None if quota else 1,
    )
    if quota:
        summary = summarize_user_todos_for_quota(summary, agent_identity={"agent_id": "agent-a"})
    result = _frontier(summary)["deferred_successors"]
    assert result["ready_count"] == ready
    assert result["blocked_count"] == blocked
    assert result["current_agent_ready_count"] == len(range(0, ready, 2))
    assert result["unknown_count"] == 0
    assert result["counts_complete"] is True
    assert len(result["ready_todo_ids"]) <= 5


def test_partial_deferred_source_does_not_classify_hidden_work_as_blocked():
    ready = _row(1, status="deferred", resume_ready=True, claimed_by="agent-a")
    blocked = _row(2, status="deferred", resume_ready=False)
    result = _frontier({"deferred_count": 12, "deferred_items": [ready, blocked],
                        "deferred_resume_candidates": [ready]})["deferred_successors"]
    assert result["ready_count"] == 1
    assert result["blocked_count"] == 1
    assert result["unknown_count"] == 10
    assert result["counts_complete"] is False


def test_undeclared_deferred_total_remains_unknown():
    ready = _row(1, status="deferred", resume_ready=True)
    result = _frontier({"deferred_items": [ready],
                        "deferred_resume_candidates": [ready]})["deferred_successors"]
    assert result["ready_count"] == 1
    assert result["blocked_count"] == 0
    assert result["unknown_count"] is None
    assert result["counts_complete"] is False


@pytest.mark.parametrize("conflict", [{"resume_ready": False}, {"claimed_by": "agent-b"}, {"status": "open"}])
def test_conflicting_historical_fragments_cannot_certify_readiness(conflict):
    ready = _row(1, status="deferred", resume_ready=True, claimed_by="agent-a")
    result = _frontier({"deferred_count": 1, "deferred_items": [{**ready, **conflict}],
                        "deferred_resume_candidates": [ready]})["deferred_successors"]
    assert result["ready_count"] == 0
    assert result["blocked_count"] == 0
    assert result["current_agent_ready_count"] == 0
    assert result["unknown_count"] == 1
    assert result["counts_complete"] is False


def test_partial_advancement_source_preserves_observed_count_and_coverage():
    summary = {"open_count": 12, "work_counts": {"schema_version": "todo_work_counts_v0",
               "open": 12, "advancement": 2, "monitor": 0, "hidden": 10,
               "complete": False, "agent_id": "agent-a"},
               "claim_scope": {"agent_id": "agent-a"},
               "executable_backlog_items": [_row(1)], "deferred_count": 0}
    result = _frontier(summary)["normalized_progress"]
    assert result["agent_advancement_open_count"] == 2
    assert result["agent_counts_complete"] is False
    assert result["agent_unclassified_open_count"] == 10


def test_frontier_rejects_work_counts_from_a_different_agent_scope():
    summary = compact_todo_group([_row(1)], role="agent", source_section="Agent Todo")
    summary["claim_scope"] = {"agent_id": "agent-b"}
    with pytest.raises(ValueError, match="differently scoped"):
        _frontier(summary)


@pytest.mark.parametrize("counts", [[], "bad", 4])
def test_present_malformed_work_counts_cannot_enter_historical_fallback(counts):
    with pytest.raises(ValueError, match="Todo work counts"):
        _frontier({"open_count": 12, "work_counts": counts, "executable_backlog_items": []})


def test_absent_deferred_source_does_not_start_a_runtime(monkeypatch):
    from loopx.control_plane.goals.goal_frontier import _deferred_successors
    from loopx.control_plane.todos import resume_planning
    monkeypatch.setattr(resume_planning, "effect_runtime_result",
                        lambda *args: pytest.fail("absent source started a runtime"))
    for summary in (None, {}):
        result = _deferred_successors(summary, agent_id=None)
        assert result["unknown_count"] is None and result["counts_complete"] is False


@pytest.mark.parametrize("counts", [None, {"ready": -1}, {"complete": "true"},
    {"unknown": None}, {"ready_by_agent": {"agent-a": 13}}, {"ready": 13},
    {"ready": 2**53 + 1, "ready_by_agent": {"agent-a": 2**53 + 1}},
    {"blocked": 2**53 + 1}, {"unknown": 2**53 + 1, "complete": False}])
def test_frontier_rejects_malformed_deferred_count_carriers(counts):
    valid = {"ready": 12, "blocked": 0, "unknown": 0, "complete": True,
             "ready_by_agent": {"agent-a": 12}}
    supplied = [] if counts is None else {**valid, **counts}
    with pytest.raises(ValueError, match="counts"):
        _frontier({"deferred_count": 12, "deferred_work_counts": supplied})


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_cli_frontier_preserves_canonical_counts_after_display_loss(tmp_path, monkeypatch, provider):
    import json

    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.effect_runtime import restart_effect_runtime
    from loopx.control_plane.testing.canary_harness import run_json_cli_result, write_fixture_registry
    from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nDeliver checked work.\n\n## Agent Todo\n" +
        "".join(f"- [ ] Work {i}\n  <!-- loopx:todo todo_id=todo_work_{i} role=agent "
                "task_class=advancement_task status=open claimed_by=agent-a -->\n" for i in range(12)) +
        "".join(f"- [x] Wait {i}\n  <!-- loopx:todo todo_id=todo_wait_{i} role=agent "
                "task_class=advancement_task status=deferred claimed_by=agent-a "
                "resume_when=todo_done:todo_dependency -->\n" for i in range(12)) +
        "- [x] Dependency complete\n  <!-- loopx:todo todo_id=todo_dependency role=agent "
        "task_class=advancement_task status=done no_followup=true -->\n", encoding="utf-8")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-count", domain="frontier-counts", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a"], quota_allowed_slots=None)
    goal = json.loads(registry.read_text())["goals"][0]
    fields = parse_active_state_todos(state.read_text(), goal=goal, item_limit=None)
    initialize_canonical_authority(runtime, goal["id"], build_todo_runtime_shadow_projection(
        goal_id=goal["id"], todos=fields["agent_todos"]["items"], handoff_mode="soft_claim"),
        state_path=state, provider=provider)
    original_state = state.read_bytes()
    quota_args = ("quota", "should-run", "--goal-id", goal["id"],
        "--agent-id", "agent-a", "--scan-path", str(tmp_path), "--include-detail", "agent-todos")
    try:
        code, intact = run_json_cli_result(*quota_args, registry_path=registry, runtime_root=runtime)
        assert code == 0, intact
        state.unlink()
        code, listed = run_json_cli_result("todo", "list", "--goal-id", goal["id"], "--role", "agent",
            "--limit", "1", "--thin", registry_path=registry, runtime_root=runtime)
        assert code == 0, listed
        assert len(listed["todos"]) == 1
        assert listed["agent_todos"]["deferred_work_counts"]["ready"] == 12
        code, packet = run_json_cli_result(*quota_args, registry_path=registry, runtime_root=runtime)
        # Canonical work survives display loss, but counts cannot supply the
        # missing Goal intent or authorize execution.
        assert code == 1, packet
        assert packet["status_health_ok"] is False
        assert packet["should_run"] is False
        assert packet["normal_delivery_allowed"] is False
        frontier = packet["goal_frontier_projection"]
        assert frontier["normalized_progress"]["agent_advancement_open_count"] == 12
        assert frontier["deferred_successors"]["ready_count"] == 12
        assert frontier["deferred_successors"]["blocked_count"] == 0
        assert frontier["deferred_successors"]["counts_complete"] is True
        for field in ("normalized_progress", "deferred_successors"):
            assert frontier[field] == intact["goal_frontier_projection"][field]
        assert not state.exists()
        state.write_bytes(original_state)
        code, recovered = run_json_cli_result(*quota_args, registry_path=registry, runtime_root=runtime)
        assert code == 0, recovered
        assert state.read_bytes() == original_state
        for field in ("normalized_progress", "deferred_successors"):
            assert recovered["goal_frontier_projection"][field] == frontier[field]
        state.unlink()
        authority = runtime / "authority" / f"{provider}-v0"
        unavailable = runtime / "unavailable-provider"
        authority.rename(unavailable)
        code, failed = run_json_cli_result("todo", "list", "--goal-id", goal["id"],
            registry_path=registry, runtime_root=runtime)
        assert code != 0
        assert failed["legacy_fallback_used"] is False
        assert not state.exists()
        unavailable.rename(authority)
        code, restored = run_json_cli_result("todo", "list", "--goal-id", goal["id"], "--role", "agent",
            "--limit", "1", "--thin", registry_path=registry, runtime_root=runtime)
        assert code == 0, restored
        assert len(restored["todos"]) == 1
        assert restored["agent_todos"]["deferred_work_counts"]["ready"] == 12
        assert not state.exists()
    finally:
        restart_effect_runtime()
