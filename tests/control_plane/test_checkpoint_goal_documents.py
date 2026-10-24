"""Primary Goal documents remain independent of derived Agent state."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import subprocess
import sys

import pytest

from tests.control_plane.test_default_result_freshness import _default_fixture
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_owner_change_survives_agent_state_rollback(provider, tmp_path, monkeypatch):
    project, runtime, registry, state = _default_fixture(provider, tmp_path, monkeypatch, handoff_mode="hard_lease")
    owner = project / "GOAL.md"
    owner.write_text("Export records without an envelope.\n", encoding="utf-8")
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["authority_sources"] = [
        {"kind": "goal_doc", "path": "GOAL.md", "role": "primary_goal_document"}]
    registry.write_text(json.dumps(registration), encoding="utf-8")
    rc, lease = _run_cli(registry, runtime, "task-lease", "acquire", "--goal-id", GOAL_ID,
        "--todo-id", TODO_ID, "--owner", AGENT_ID, "--idempotency-key", "owner-document-work",
        "--ttl-seconds", "600", cwd=project)
    assert rc == 0, lease
    identity = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID)
    rc, admitted = _run_cli(registry, runtime, "quota", "should-run", "--codex-app", *identity, cwd=project)
    assert rc == 0, admitted
    original = admitted["delivery_result_context"]
    assert original["basis"]["goal"]["primary_documents"] == [
        {"path": str(owner.resolve()), "sha256": hashlib.sha256(owner.read_text(encoding="utf-8").encode()).hexdigest()}]
    assert original["basis"]["goal"]["prose"] == owner.read_text(encoding="utf-8")
    turn = original["settlement_identity"]["turn_instance_id"]
    command = ["loopx", "todo", "complete", *identity, "--turn-instance-id", turn,
        "--delivery-read-context", original["read_context_id"],
        "--task-lease-idempotency-key", "owner-document-work", "--task-lease-expected-version", "1"]

    # Only the owner document changes. No Todo/provider mutation can explain
    # this stale result, and an old Agent plan must not hide the new source.
    updated = "Export versioned event envelopes, including initialization.\n"
    owner.write_text(updated, encoding="utf-8")
    rc, stale = _run_cli(registry, runtime, *command[1:], "--evidence", "Old candidate", cwd=project)
    assert rc != 0 and stale["error_code"] == "checkpoint_read_context_stale", stale
    assert stale["checkpoint_read_context"]["changed_components"] == ["goal"]
    assert _spend_run_count(runtime) == 0

    # An Agent can still edit a writable state file; doing so cannot replace
    # the separately registered owner's requirements in the next read.
    state.write_text(state.read_text(encoding="utf-8") + "\nAgent plan: export records without an envelope.\n", encoding="utf-8")
    assert "without an envelope" in state.read_text(encoding="utf-8")
    rc, current = _run_cli(registry, runtime, "checkpoint-context", *identity,
        "--turn-instance-id", turn, "--purpose", "delivery_result", cwd=project)
    assert rc == 0, current
    assert current["basis"]["goal"]["prose"] == updated
    assert "without an envelope" not in current["basis"]["goal"]["prose"]
    assert isinstance(current["basis"]["goal"]["state_prose"], str)
    assert current["read_context_id"] != original["read_context_id"]
    assert current["settlement_identity"] == original["settlement_identity"]
    assert owner.read_text(encoding="utf-8") == updated
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_missing_primary_document_cannot_be_rebuilt_from_agent_state(provider, tmp_path, monkeypatch):
    project, runtime, registry, state = _default_fixture(provider, tmp_path, monkeypatch)
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["authority_sources"] = [
        {"kind": "goal_doc", "path": "missing-goal.md", "role": "primary_goal_document"}]
    registry.write_text(json.dumps(registration), encoding="utf-8")
    before = state.read_bytes()
    rc, rejected = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, cwd=project)
    assert rc != 0 and rejected["error_code"] == "checkpoint_goal_source_unavailable", rejected
    assert state.read_bytes() == before
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_owner_change_during_native_capture_is_rechecked_at_provider_fence(provider, tmp_path, monkeypatch):
    project, runtime, registry, _ = _default_fixture(provider, tmp_path, monkeypatch)
    owner = project / "GOAL.md"
    owner.write_text("Initial requirement.\n", encoding="utf-8")
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["authority_sources"] = [
        {"kind": "goal_doc", "path": "GOAL.md", "role": "primary_goal_document"}]
    registry.write_text(json.dumps(registration), encoding="utf-8")
    rc, admitted = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
        "--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID, cwd=project)
    assert rc == 0, admitted
    wrapper = project / "capture_race.py"
    wrapper.write_text(f'''
from pathlib import Path
from loopx.control_plane.goals import checkpoint_context_io
original = checkpoint_context_io.delivery_result_context_input
def race(**kwargs):
    captured = original(**kwargs)
    Path({str(owner)!r}).write_text("Changed owner requirement.\\n", encoding="utf-8")
    Path("capture_race_seen").touch()
    return captured
checkpoint_context_io.delivery_result_context_input = race
from loopx.cli import main
raise SystemExit(main())
''', encoding="utf-8")
    original = admitted["delivery_result_context"]
    complete = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime),
        "todo", "complete", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--todo-id", TODO_ID, "--turn-instance-id", original["settlement_identity"]["turn_instance_id"],
        "--delivery-read-context", original["read_context_id"]]
    result = subprocess.run([sys.executable, str(wrapper), *complete,
        "--evidence", "Candidate from before owner change"], cwd=project, capture_output=True,
        text=True, env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])})
    assert result.stdout, result.stderr
    rc, rejected = result.returncode, json.loads(result.stdout)
    assert (project / "capture_race_seen").exists()
    assert rc != 0 and rejected["error_code"] == "checkpoint_read_context_stale", rejected
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_independent_owner_change_is_checked_on_each_inflight_turn(provider, tmp_path, monkeypatch):
    project, runtime, registry, _ = _default_fixture(provider, tmp_path, monkeypatch)
    owner = project / "GOAL.md"
    owner.write_text("Export direct records.\n", encoding="utf-8")
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["authority_sources"] = [{"kind": "goal_doc", "path": "GOAL.md"}]
    registry.write_text(json.dumps(registration), encoding="utf-8")
    identity = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--todo-id", TODO_ID)
    def guard(turn):
        rc, result = _run_cli(registry, runtime, "quota", "should-run", "--codex-app",
            *identity, "--turn-instance-id", turn, cwd=project)
        assert rc == 0, result
        return result["delivery_result_context"]
    def writeback(turn):
        return _run_cli(registry, runtime, "refresh-state", *identity, "--turn-instance-id", turn,
            "--classification", "verified_inflight_candidate", "--delivery-outcome", "outcome_progress",
            "--delivery-batch-scale", "implementation", "--delivery-boundary", "in_flight_continuation",
            "--delivery-workspace-path", str(project), "--no-global-sync", "--suppress-external-sinks", cwd=project)
    first = guard("owner-first-turn")
    rc, committed = writeback("owner-first-turn")
    assert rc == 0 and committed["ok"], committed
    rc, spent = _run_cli(registry, runtime, "quota", "spend-slot", *identity,
        "--turn-instance-id", "owner-first-turn", "--slots", "1", "--source", "heartbeat", "--execute", cwd=project)
    assert rc == 0 and spent["ok"], spent
    second = guard("owner-second-turn")
    assert second["read_context_id"] != first["read_context_id"]
    owner.write_text("Export versioned event envelopes.\n", encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    rc, stale = writeback("owner-second-turn")
    assert rc != 0 and stale["error_code"] == "checkpoint_read_context_stale", stale
    assert index.read_bytes() == before
    assert _spend_run_count(runtime) == 1
    rc, reread = _run_cli(registry, runtime, "checkpoint-context", *identity,
        "--turn-instance-id", "owner-second-turn", "--purpose", "delivery_result", cwd=project)
    assert rc == 0 and reread["basis"]["goal"]["prose"] == owner.read_text(encoding="utf-8"), reread
    rc, saved = writeback("owner-second-turn")
    assert rc == 0 and saved["ok"], saved
    assert saved["result_work_context"]["read_context_id"] == reread["read_context_id"]
    assert saved["vision_checkpoint"]["required"] is False
