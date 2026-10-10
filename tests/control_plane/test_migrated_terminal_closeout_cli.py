"""A legacy completion stays settled across real reviewed provider promotion."""

import json
import sys

import pytest

from tests.control_plane.test_reviewed_promotion_cli import prepare


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_completed_legacy_todo_closes_after_promotion_without_revalidation(tmp_path, provider):
    ws, _, _ = prepare(tmp_path, provider, source_mode="legacy")
    marker = tmp_path / "validation-count"
    validator = tmp_path / "validate.py"
    validator.write_text(f"from pathlib import Path\np=Path({str(marker)!r})\np.write_text(p.read_text()+'run\\n' if p.exists() else 'run\\n')\n")
    todo_id = ws.cli("todo", "add", "--role", "agent", "--text", "Preserve the completed migration work",
                     "--validation-command-json", json.dumps([sys.executable, str(validator)]),
                     "--validation-timeout-seconds", "5")["todo_id"]
    completed = ws.cli("todo", "complete", "--todo-id", todo_id, "--agent-id", "agent-a",
                       "--evidence", "Original check passed")
    assert completed["ok"], completed
    assert marker.read_text() == "run\n"
    before = ws.cli("todo", "list", "--todo-id", todo_id)["todo"]
    assert before["completion_continuation"] == "active_goal"
    key = before["completion_turn_key"]
    assert ws.drain(budget_seconds="60")["ok"]
    preview = ws.cli("coordination-shadow", "promote")
    saved = tmp_path / "completed-review.json"
    saved.write_text(json.dumps(preview), encoding="utf-8")
    promoted = ws.cli("coordination-shadow", "promote", "--reviewed-plan", str(saved), "--execute")
    assert promoted["ok"], promoted
    # This intentionally minimal migration fixture is not a healthy runnable
    # Goal. Its guard still exposes the canonical lifecycle diagnostic.
    guard_args = ("quota", "should-run", "--codex-app", "--agent-id", "agent-a",
                  "--turn-instance-id", "migration-readback")
    guard_before = ws.cli(*guard_args, success=False)
    assert guard_before["agent_todo_summary"]["todo_succession_warning"]["count"] == 1
    validator.unlink()  # A metadata closeout must not need the old validator.
    closeout_args = ("todo", "complete", "--todo-id", todo_id, "--agent-id", "agent-a",
                     "--completion-identity-key", key, "--evidence", "Original check passed", "--no-follow-up")
    closed = ws.cli(*closeout_args)
    assert closed["ok"] and closed["changed"], closed
    after = ws.cli("todo", "list", "--todo-id", todo_id)["todo"]
    assert after["no_followup"] is True
    assert after["completion_continuation"] == "no_followup"
    assert after["completion_recovery"] == "lifecycle_reentry_terminal_closeout"
    for field in ("completed_at", "completion_turn_key", "completion_receipt_id", "evidence"):
        assert after[field] == before[field]
    replay = ws.cli(*closeout_args)
    assert replay["ok"] and replay["idempotent_replay"], replay
    assert ws.cli("todo", "list", "--todo-id", todo_id)["todo"] == after
    assert marker.read_text() == "run\n"
    guard_after = ws.cli(*guard_args, success=False)
    assert "todo_succession_warning" not in guard_after["agent_todo_summary"]
