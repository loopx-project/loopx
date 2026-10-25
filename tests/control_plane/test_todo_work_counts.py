"""Semantic counts survive source selection and every presentation budget."""
from __future__ import annotations

from loopx.control_plane.todos.todo_summary import compact_todo_group
from loopx.control_plane.todos.todo_semantics import todo_summary_open_task_counts
from loopx.control_plane.todos.quota_summary import (
    summarize_user_todos_for_quota, compact_quota_todo_summary_for_payload,
)


def work(index, **fields):
    return {"todo_id": f"todo_work_{index:03}", "text": f"[P1] Work {index}",
            "status": "open", "task_class": "advancement_task", "role": "agent",
            "index": index + 1, "source_section": "Agent Todo", **fields}


def test_complete_counts_do_not_equal_backlog_display_length():
    summary = compact_todo_group([work(i) for i in range(21)], source_section="Agent Todo", role="agent")
    assert len(summary["executable_backlog_items"]) == 8
    assert todo_summary_open_task_counts(summary)["advancement"] == 21


def test_scoped_counts_survive_quota_payload_compaction():
    records = [work(i, claimed_by="agent-a" if i < 21 else "agent-b") for i in range(35)]
    summary = compact_todo_group(records, source_section="Agent Todo", role="agent", item_limit=None)
    scoped = summarize_user_todos_for_quota(summary, agent_identity={"agent_id": "agent-a"})
    compact = compact_quota_todo_summary_for_payload(scoped)
    assert len(compact["executable_backlog_items"]) == 2
    assert todo_summary_open_task_counts(compact)["advancement"] == 21


def test_incomplete_legacy_summary_cannot_invent_hidden_advancement():
    summary = {"open_count": 10, "first_open_items": [work(0, task_class="blocker", status="blocked")]}
    counts = todo_summary_open_task_counts(summary)
    assert counts["advancement"] == 0
    assert counts["complete"] is False


def test_status_compactor_preserves_semantic_counts():
    from loopx.control_plane.todos.quota_summary import _compact_agent_lane_status_todo_summary

    summary = compact_todo_group([work(i) for i in range(21)], source_section="Agent Todo", role="agent")
    projected = _compact_agent_lane_status_todo_summary(summary, role="agent")
    assert todo_summary_open_task_counts(projected)["advancement"] == 21
    assert projected["work_counts"]["complete"] is True


def test_incomplete_scoped_snapshot_does_not_certify_monitor_only():
    from loopx.control_plane.scheduler.external_evidence_observation import scoped_monitor_watch_without_advancement
    from loopx.control_plane.todos.todo_semantics import todo_summary_has_only_future_scoped_monitor_work

    monitor = work(0, task_class="continuous_monitor", claimed_by="agent-a", next_due_at="2099-01-01T00:00:00Z")
    scoped = summarize_user_todos_for_quota({"schema_version": "todo_summary_v0", "open_count": 12,
        "items": [monitor]}, agent_identity={"agent_id": "agent-a"})
    assert scoped["work_counts"]["complete"] is False
    assert scoped_monitor_watch_without_advancement(scoped) is False
    assert todo_summary_has_only_future_scoped_monitor_work(scoped) is False
    repeated = summarize_user_todos_for_quota(scoped, agent_identity={"agent_id": "agent-a"})
    assert repeated["work_counts"]["complete"] is False
    assert todo_summary_has_only_future_scoped_monitor_work(repeated) is False
    complete = summarize_user_todos_for_quota({"schema_version": "todo_summary_v0", "open_count": 1,
        "items": [monitor]}, agent_identity={"agent_id": "agent-a"})
    assert todo_summary_has_only_future_scoped_monitor_work(complete) is True


def test_wrong_scope_count_envelope_is_not_reused():
    import pytest
    summary = compact_todo_group([work(0)], source_section="Agent Todo", role="agent")
    summary["claim_scope"] = {"schema_version": "agent_claim_scope_v0", "agent_id": "agent-b"}
    with pytest.raises(ValueError, match="differently scoped"):
        todo_summary_open_task_counts(summary)


def test_real_cli_count_contract_after_promoted_display_loss(tmp_path, monkeypatch):
    import json
    from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.testing.canary_harness import run_json_cli

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    for provider in ("file", "sqlite"):
        root = tmp_path / provider
        root.mkdir()
        state = root / "state.md"
        state.write_text("# Independent narrative\n\n## Agent Todo\n- [ ] Stale display\n")
        runtime = root / "runtime"
        registry = root / "registry.json"
        registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
            {"id": "goal-count", "repo": str(root), "state_file": str(state)}]}))
        records = [work(i, schema_version="todo_item_v0", archive_state="active", done=False) for i in range(29)]
        projection = build_todo_runtime_shadow_projection(goal_id="goal-count", todos=records)
        initialize_canonical_authority(runtime, "goal-count", projection, state_path=state, provider=provider)
        state.unlink()
        listed = run_json_cli("todo", "list", "--goal-id", "goal-count", "--role", "agent", "--limit", "1", "--thin",
            registry_path=registry, runtime_root=runtime)
        assert len(listed["todos"]) == 1
        assert listed["agent_todos"]["work_counts"]["advancement"] == 29
        assert listed["agent_todos"]["work_counts"]["complete"] is True
        assert not state.exists()
