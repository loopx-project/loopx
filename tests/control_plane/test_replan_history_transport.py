"""Production CLI settlement with a synthetic long-lived history, no live state."""
import json

from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count, _write_fixture,
)


def test_large_history_refresh_replay_and_spend_once(tmp_path):
    project, runtime, registry = _write_fixture(tmp_path)
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
