"""Quota monitor selection uses full source, one instant and existing fences."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.scheduler import monitor_todo
from loopx.control_plane.testing.canary_harness import write_fixture_registry, run_json_cli_result
from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
from loopx.control_plane.todos import quota_selection
from loopx.control_plane.todos.quota_summary import summarize_user_todos_for_quota
from loopx.todos import list_goal_todos
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID, GOAL_ID, _add_monitor, _write_fixture,
)

NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)


def monitor(todo_id: str, **fields: object) -> dict:
    return {"todo_id": todo_id, "text": "[P1] Observe external state", "index": 1,
            "status": "open", "task_class": "continuous_monitor", **fields}


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch) -> None:
    monkeypatch.setattr(monitor_todo, "now_utc", lambda: NOW)
    monkeypatch.setattr(quota_selection, "now_utc", lambda: NOW, raising=False)


@pytest.mark.parametrize("supported", [True, False])
def test_full_source_monitor_partitions_preserve_scope_and_capability_fences(supported) -> None:
    items = [monitor("todo_due", next_due_at="2030-01-01T08:00:00+08:00"),
             monitor("todo_watch", watch_only="true", next_due_at="2029-12-31T23:59:00Z"),
             monitor("todo_capacity", next_due_at="2030-01-01T00:00:00Z", required_capabilities=["compiler"]),
             monitor("todo_future", next_due_at="2030-01-01T00:00:01Z"),
             monitor("todo_expired", next_due_at="2029-12-31T23:59:00Z", expires_at="2030-01-01T08:00:00+08:00"),
             monitor("todo_peer", claimed_by="agent-b"),
             monitor("todo_excluded", claimed_by="agent-a", excluded_agents=["agent-a"]),
             monitor("todo_wait", resume_when="todo_done:todo_dependency", resume_ready=False),
             monitor("todo_watch_gap", watch_only="true"),
             monitor("todo_expired_gap", expires_at="2030-01-01T00:00:00Z"),
             monitor("todo_gap", next_due_at="invalid"), monitor("todo_gap_2")]
    source = {"items": items, "total_count": len(items), "open_count": len(items),
              "monitor_writeback": {"supported": supported}, "first_open_items": items[:1]}
    before = deepcopy(source)
    result = summarize_user_todos_for_quota(source, agent_identity={"agent_id": "agent-a"})
    assert result is not None
    assert result["monitor_due_count"] == (2 if supported else 0)
    assert [row["todo_id"] for row in result["monitor_due_items"]] == (["todo_watch"] if supported else [])
    assert result["watch_only_monitor_due_count"] == (1 if supported else 0)
    assert result["monitor_capability_blocked_due_count"] == (1 if supported else 0)
    assert result["monitor_schedule_gap_count"] == (2 if supported else 0)
    assert [row["todo_id"] for row in result["monitor_schedule_gap_items"]] == (["todo_gap"] if supported else [])
    assert source == before


def test_quota_monitor_selection_requires_one_clock_and_one_existing_batch(monkeypatch) -> None:
    calls, clocks = [], []
    original = quota_selection.effect_runtime_result

    def clock():
        clocks.append(True)
        return NOW

    def record(method, params):
        calls.append(method)
        return original(method, params)

    monkeypatch.setattr(quota_selection, "now_utc", clock, raising=False)
    monkeypatch.setattr(quota_selection, "effect_runtime_result", record)
    # Per-row wall-clock reads can cross expiry during one projection. The
    # declared observation still precedes expiry for every row in this source.
    reads = iter([NOW] + [NOW + timedelta(seconds=2)] * 100)
    monkeypatch.setattr(monitor_todo, "now_utc", lambda: next(reads))
    items = [monitor(f"todo_{i}", next_due_at="2030-01-01T00:00:00Z",
                     expires_at="2030-01-01T00:00:01Z") for i in range(25)]
    result = summarize_user_todos_for_quota({"items": items, "total_count": len(items)})
    assert result is not None and result["monitor_due_count"] == 25
    assert calls == ["todo.quota_planning.project"]
    assert clocks == [True]


def test_gap_presentation_order_does_not_inherit_claim_or_profile_order() -> None:
    items = [monitor("todo_unclaimed", index=1), monitor("todo_claimed", index=2, claimed_by="agent-a")]
    result = summarize_user_todos_for_quota({"items": items, "total_count": 2},
                                          agent_identity={"agent_id": "agent-a"})
    assert result is not None
    assert result["monitor_schedule_gap_count"] == 2
    assert result["monitor_schedule_gap_items"][0]["todo_id"] == "todo_unclaimed"


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_public_quota_gap_count_uses_real_full_source_without_writing_it(tmp_path, monkeypatch, provider) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("---\nstatus: active\n---\n# Goal\n## Objective\nObserve external state.\n\n## Agent Todo\n" +
        "".join(f"- [ ] [P1] Observe state {i}.\n  <!-- loopx:todo todo_id=todo_gap_{i} role=agent "
                "task_class=continuous_monitor status=open claimed_by=agent-a expires_at=2099-01-01T00:00:00Z -->\n"
                for i in range(25)) +
        "- [ ] [P1] Observe without a schedule.\n  <!-- loopx:todo todo_id=todo_watch role=agent "
        "task_class=continuous_monitor status=open claimed_by=agent-a watch_only=true -->\n"
        "- [ ] [P1] Observe for peer.\n  <!-- loopx:todo todo_id=todo_peer role=agent "
        "task_class=continuous_monitor status=open claimed_by=agent-b expires_at=2099-01-01T00:00:00Z -->\n",
        encoding="utf-8")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="goal-a", domain="monitor-clock", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["agent-a", "agent-b"], quota_allowed_slots=None)
    if provider != "legacy":
        goal = json.loads(registry.read_text())["goals"][0]
        fields = parse_active_state_todos(state.read_text(), goal=goal, item_limit=None)
        projection = build_todo_runtime_shadow_projection(goal_id="goal-a", todos=fields["agent_todos"]["items"],
                                                         handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, "goal-a", projection, state_path=state, provider=provider)
        state.unlink()
    before = state.read_bytes() if state.exists() else None
    code, packet = run_json_cli_result("quota", "should-run", "--goal-id", "goal-a", "--agent-id", "agent-a",
        "--include-detail", "agent-todos", "--scan-path", str(tmp_path), registry_path=registry, runtime_root=runtime)
    assert code == 0, packet
    summary = packet["agent_todo_summary"]
    assert summary["monitor_schedule_gap_count"] == 25
    assert len(summary["monitor_schedule_gap_items"]) == 1
    assert summary["monitor_due_count"] == 0
    assert (state.read_bytes() if state.exists() else None) == before
    code, records = run_json_cli_result("todo", "list", "--goal-id", "goal-a", "--role", "agent",
        "--limit", "50", registry_path=registry, runtime_root=runtime)
    assert code == 0 and records["todo_count"] == 27
    assert all(row.get("next_due_at") is None and row["status"] == "open" for row in records["todos"])


def test_missing_typed_quota_owner_cannot_reactivate_python_monitor_rules(monkeypatch) -> None:
    def unavailable(*_args, **_kwargs):
        raise RuntimeError("isolated typed owner unavailable")

    monkeypatch.setattr(quota_selection, "effect_runtime_result", unavailable)
    source = {"items": [monitor("todo_gap")], "total_count": 1}
    before = deepcopy(source)
    with pytest.raises(RuntimeError, match="typed owner unavailable"):
        summarize_user_todos_for_quota(source)
    assert source == before


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
def test_cli_due_order_and_successive_turns_preserve_exact_poll_settlement(
    tmp_path, monkeypatch, provider,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = _write_fixture(tmp_path)
    # Deliberately create out of deadline order, so display order cannot
    # accidentally satisfy the independently specified scheduling oracle.
    monitors = [
        _add_monitor(registry, text="[P1] Observe recent target", target_key="recent",
                     next_due_at="2002-01-01T00:00:00Z"),
        _add_monitor(registry, text="[P1] Observe oldest target", target_key="oldest",
                     next_due_at="2000-01-01T00:00:00Z"),
        _add_monitor(registry, text="[P1] Observe middle target", target_key="middle",
                     next_due_at="2001-01-01T00:00:00Z"),
    ]
    if provider != "legacy":
        rows = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, todos=rows, handoff_mode="soft_claim")
        initialize_canonical_authority(runtime, GOAL_ID, projection,
                                       state_path=state, provider=provider)
        state.unlink()  # Canonical authority, not a stale Markdown fallback.

    def call(*args):
        code, result = run_json_cli_result(
            *args, registry_path=registry, runtime_root=runtime)
        assert code == 0, result
        return result

    scope = ["--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
             "--runtime-profile", "codex_app_ssh_goal"]
    expected = [monitors[1], monitors[2], monitors[0]]
    previous_guard = None
    for ordinal, item in enumerate(expected):
        turn = f"monitor-order-{ordinal}"
        guard = ["quota", "should-run", *scope, "--turn-instance-id", turn,
                 "--available-capability", "network",
                 "--available-capability", "external_evidence_poll"]
        admitted = call(*guard)
        assert admitted["selected_todo"]["todo_id"] == item["todo_id"]
        lane = admitted["work_lane_contract"]
        assert lane["monitor_due_count"] == len(expected) - ordinal
        assert len(lane["monitor_due_items"]) == 1
        if previous_guard:
            assert call(*previous_guard)["should_run"] is False
        poll = ["quota", "monitor-poll", *scope, "--turn-instance-id", turn,
                "--todo-id", item["todo_id"], "--target-key", item["target_key"],
                "--result-hash", f"checked-{ordinal}",
                "--next-due-at", "2099-01-01T00:00:00Z", "--execute"]
        settled = call(*poll)
        assert settled["turn_continuation"]["current_turn_settled"] is True
        replay = call(*poll)
        assert replay["replayed"] is True and replay["appended"] is False
        closed = call(*guard)
        assert closed["effective_action"] == "heartbeat_settled_skip"
        assert closed["selected_todo"]["todo_id"] == item["todo_id"]
        assert closed["interaction_contract"]["agent_channel"]["must_attempt"] is False
        records = call("todo", "list", "--goal-id", GOAL_ID, "--role", "agent")["todos"]
        by_id = {row["todo_id"]: row for row in records}
        for unpolled in expected[ordinal + 1:]:
            assert by_id[unpolled["todo_id"]]["next_due_at"] == unpolled["next_due_at"]
            assert not by_id[unpolled["todo_id"]].get("last_checked_at")
        previous_guard = guard
    events = [json.loads(line) for line in
              (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text().splitlines()]
    assert sum(row["classification"] == "quota_monitor_poll" for row in events) == 3
    assert not any(row["classification"] == "quota_slot_spent" for row in events)
