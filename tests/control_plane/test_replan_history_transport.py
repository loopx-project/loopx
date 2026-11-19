"""Production CLI settlement with a synthetic long-lived history, no live state."""
import json
import time
import pytest
from canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID as MONITOR_AGENT, GOAL_ID as MONITOR_GOAL, _write_fixture as write_monitor_fixture,
)
from loopx.todos import add_goal_todo, update_goal_todo

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count, _write_fixture,
    DUE_MONITOR_TODO_ID, _append_newly_due_monitor, _initialize_fixture_authority,
)


@pytest.mark.parametrize("watch_only", [True, False])
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cli_history_preserves_registered_watch_intent(tmp_path, watch_only, provider):
    project, runtime, registry = _write_fixture(tmp_path)
    _append_newly_due_monitor(project, watch_only=watch_only)
    _initialize_fixture_authority(project, runtime, provider=provider)
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    original = "".join(json.dumps({
        "agent_id": AGENT_ID, "classification": "quota_monitor_poll",
        "generated_at": f"2026-01-01T00:00:{n:02d}Z", "turn_instance_id": f"poll-{n}",
        "todo_id": DUE_MONITOR_TODO_ID, "target_key": "due-monitor-fixture",
        "monitor_target": {"target_id": "target-digest", "agent_id": AGENT_ID,
            "monitor_mode": "due_monitor_observed_without_material_transition"},
        "monitor_event": {"material_change": False},
    }) + "\n" for n in range(1, 7)).encode()
    index.write_bytes(original)
    code, guard = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "inspect-watch",
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        cwd=project)
    assert code == 0, guard
    kinds = [row["kind"] for row in (guard.get("autonomous_replan_obligation") or {}).get("triggers", [])]
    assert ("dead_monitor_repeat" in kinds) is (not watch_only)
    assert _spend_run_count(runtime) == 0
    assert index.read_bytes().startswith(original)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_unkeyed_watch_polls_do_not_trigger_replan(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = write_monitor_fixture(tmp_path, provider=provider)
    project = registry.parent.parent
    monitor = add_goal_todo(registry_path=registry, goal_id=MONITOR_GOAL, role="agent",
        text="Observe the existing public source", task_class="continuous_monitor", claimed_by=MONITOR_AGENT,
        monitor_metadata={"watch_only": "true", "cadence": "1h", "next_due_at": "2000-01-01T00:00:00Z"})
    todo_id = monitor["todo_id"]
    scope = ("--goal-id", MONITOR_GOAL, "--agent-id", MONITOR_AGENT, "--runtime-profile", "generic_cli",
             "--available-capability", "network", "--available-capability", "external_evidence_poll")
    for number in range(6):
        if number:
            # The production observation clock is second-granular. Give each
            # operation a distinct observed timestamp without rewriting history.
            time.sleep(1.01)
            update_goal_todo(registry_path=registry, goal_id=MONITOR_GOAL, todo_id=todo_id,
                agent_id=MONITOR_AGENT, monitor_metadata={"next_due_at": "2000-01-01T00:00:00Z"})
        turn = ("--turn-instance-id", f"unkeyed-watch-poll-{number}")
        code, guard = _run_cli(registry, runtime, "quota", "should-run", *scope, *turn, cwd=project)
        assert code == 0 and guard["should_run"], guard.get("reason")
        code, poll = _run_cli(registry, runtime, "quota", "monitor-poll", *scope, *turn,
            "--todo-id", todo_id, "--result-hash", "unchanged-source", "--execute", cwd=project)
        assert code == 0 and poll["appended"] and poll["target_key"] is None, poll.get("reason")
    index = runtime / "goals" / MONITOR_GOAL / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert len(rows) == 6
    assert all(row["classification"] == "quota_monitor_poll" and row["todo_id"] == todo_id
        and row["agent_id"] == MONITOR_AGENT and "target_key" not in row
        and row["monitor_target"]["schema_version"] == "quota_monitor_target_v0"
        for row in rows)
    def trigger_kinds(obligation):
        return [row["kind"] for row in (obligation or {}).get("triggers", [])]

    for agent_scope in ((), ("--agent-id", MONITOR_AGENT)):
        code, status = _run_cli(registry, runtime, "status", "--goal-id", MONITOR_GOAL, *agent_scope, cwd=project)
        assert code == 0 and status["ok"]
        item = next(row for row in status["attention_queue"]["items"] if row["goal_id"] == MONITOR_GOAL)
        for view in (item, item.get("project_asset") or {}):
            assert "dead_monitor_repeat" not in trigger_kinds(view.get("autonomous_replan_obligation"))
            assert "dead_monitor_repeat" not in trigger_kinds(
                (view.get("autonomous_replan_obligations_by_agent") or {}).get(MONITOR_AGENT))
    code, next_guard = _run_cli(registry, runtime, "quota", "should-run", *scope,
        "--turn-instance-id", "verify-watch", cwd=project)
    assert code == 0
    assert "dead_monitor_repeat" not in trigger_kinds(next_guard.get("autonomous_replan_obligation"))
    assert next_guard["effective_action"] != "autonomous_replan_required"


def test_large_history_refresh_replay_and_spend_once(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path, provider="sqlite")
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    code, baseline = _run_cli(registry, runtime, "refresh-state", "--goal-id", GOAL_ID,
                              "--agent-id", AGENT_ID, "--no-global-sync",
                              "--suppress-external-sinks", cwd=project)
    assert code == 0, baseline
    # Use a production-shaped healthy run, then model 12,000 retries of its
    # logical turn. They cannot be dropped merely to meet the wire limit.
    row = json.loads(index.read_text().splitlines()[-1])
    row["turn_instance_id"] = "old-retried-turn"
    original = "".join(json.dumps({**row,
        "generated_at": f"2026-01-01T00:00:00.{n:06d}Z"}) + "\n"
        for n in range(12000)).encode()
    index.write_bytes(original)
    binding = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
               "--todo-id", TODO_ID, "--turn-instance-id", "long-history-settlement")
    def call(*args):
        code, result = _run_cli(registry, runtime, *args, cwd=project)
        assert code == 0, json.dumps(result)
        assert result["ok"] is True, result
        return result

    guard = call("quota", "should-run", "--codex-app", *binding,
                 )
    assert guard["should_run"] is True
    refresh = ("refresh-state", *binding, "--classification", "validated_change",
               "--delivery-batch-scale", "implementation", "--delivery-outcome", "outcome_progress",
               "--no-global-sync", "--suppress-external-sinks")
    preview = call(*refresh, "--dry-run")
    assert preview["appended"] is False
    assert index.read_bytes() == original
    first = call(*refresh)
    assert first["appended"] is True
    after = index.read_bytes()
    assert after.startswith(original)
    replay = call(*refresh)
    assert replay["appended"] is False
    assert replay["idempotent_replay"] is True
    assert index.read_bytes() == after
    spend = ("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat",
             "--execute", )
    assert call(*spend)["appended"] is True
    assert call(*spend)["appended"] is False
    assert _spend_run_count(runtime) == 1
    assert index.read_bytes().startswith(original)
