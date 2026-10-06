"""Optional enrollment must not obstruct ordinary terminal commits or replay."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.goals import checkpoint_context_io as context_io
from loopx.control_plane.quota.settlement import SettlementIdentity
from tests.control_plane.test_checkpoint_provider_fence import fixture
from tests.control_plane.checkpoint_process import refresh
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, _run_cli, _spend_run_count


def _complete(project, runtime, registry, *extra):
    return _run_cli(registry, runtime, "todo", "complete", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
        "--task-lease-idempotency-key", f"checkpoint-{TODO_ID}", "--task-lease-expected-version", "1",
        "--note", "Validated the selected output.", *extra, cwd=project)


def _authority(runtime):
    return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=GOAL_ID)


def _identity():
    return SettlementIdentity(goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("history", ["none", "supplement", "damaged", "damaged_after_commit", "damaged_after_commit_missing_writeback"])
def test_ordinary_completion_and_cold_replay_survive_optional_history(tmp_path, monkeypatch, provider, history):
    project, runtime, registry, _, read, original = fixture(tmp_path, monkeypatch, provider)
    receipt = context_io._receipt_path(runtime, _identity())
    if history != "none":
        read()
    if history == "damaged":
        receipt.write_text("{", encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before_index = index.read_bytes()
    rc, saved = _complete(project, runtime, registry)
    assert rc == 0 and saved["completed"], json.dumps(saved)
    committed = _authority(runtime)
    assert next(todo for todo in committed["todos"] if todo["todo_id"] == TODO_ID)["status"] == "done"
    if history.startswith("damaged_after_commit"):
        receipt.write_text("{", encoding="utf-8")
    if history == "damaged_after_commit_missing_writeback":
        Path(original["json_path"]).unlink()
    # Every CLI call is a fresh process against the same real provider/files.
    rc, replay = _complete(project, runtime, registry)
    assert rc == 0 and replay["idempotent_replay"], replay
    assert _authority(runtime) == committed
    assert index.read_bytes() == before_index
    assert _spend_run_count(runtime) == 0
    if history.startswith("damaged"):
        assert receipt.read_text(encoding="utf-8") == "{"
    rc, conflict = _complete(project, runtime, registry, "--task-lease-idempotency-key", "different-request")
    assert rc == 1, conflict
    assert _authority(runtime) == committed


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("enrollment", ["direction", "result", "both"])
def test_unreadable_enrollment_without_ordinary_writeback_stays_closed(tmp_path, monkeypatch, provider, enrollment):
    project, runtime, registry, state, read, _ = fixture(tmp_path, monkeypatch, provider, first_delivery=True)
    if enrollment != "result":
        read()
        context_io._receipt_path(runtime, _identity()).write_text("{", encoding="utf-8")
    if enrollment != "direction":
        context_io.read_checkpoint_context(registry_path=registry, runtime_root_override=str(runtime),
            goal_id=GOAL_ID, agent_id=AGENT_ID, todo_id=TODO_ID, turn_instance_id=TURN_ID, purpose="delivery_result")
        context_io._receipt_path(runtime, _identity(), "delivery_result").write_text("{", encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = (_authority(runtime), state.read_bytes(), index.read_bytes())
    rc, rejected = _complete(project, runtime, registry)
    assert rc == 1, rejected
    assert rejected["error_code"] == "checkpoint_commit_unknown", rejected
    assert (_authority(runtime), state.read_bytes(), index.read_bytes()) == before
    assert _spend_run_count(runtime) == 0


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("uncertainty", ["writeback_damaged", "result_empty", "result_damaged", "enrolled_writeback"])
def test_unreadable_enrollment_requires_verified_ordinary_history(tmp_path, monkeypatch, provider, uncertainty):
    enrolled = uncertainty == "enrolled_writeback"
    project, runtime, registry, state, read, original = fixture(tmp_path, monkeypatch, provider, first_delivery=enrolled)
    token = read()["read_context_id"]
    if enrolled:
        refresh(registry, runtime, token, first_delivery=True)
    context_io._receipt_path(runtime, _identity()).write_text("{", encoding="utf-8")
    if uncertainty == "writeback_damaged":
        Path(original["json_path"]).write_text("{", encoding="utf-8")
    elif uncertainty.startswith("result_"):
        context_io._receipt_path(runtime, _identity(), "delivery_result").write_text(
            "" if uncertainty == "result_empty" else "{", encoding="utf-8")
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = (_authority(runtime), state.read_bytes(), index.read_bytes())
    rc, rejected = _complete(project, runtime, registry)
    assert rc == 1 and rejected["error_code"] == "checkpoint_commit_unknown", rejected
    assert (_authority(runtime), state.read_bytes(), index.read_bytes()) == before
    assert _spend_run_count(runtime) == 0
