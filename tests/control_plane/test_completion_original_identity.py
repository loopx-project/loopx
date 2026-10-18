"""Replay a committed original completion without recreating its successor intent."""

from __future__ import annotations
import json
import subprocess
import sys
import pytest
from loopx.control_plane.goals.checkpoint_context_io import (
    completion_identity_for_original_turn,
)
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.chat_coordinator_checkpoint import CoordinatorCheckpoints
from loopx.todos import add_goal_todo, list_goal_todos
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.test_goal_mode_mcp_settlement import GOAL_ID, AGENT_ID


@pytest.fixture(autouse=True)
def _release_test_runtime(_isolated_default_runtime_routes, monkeypatch):
    # The default-route fixture gives every case a different runtime owner.
    # Stop that case's server before its HOME/TEMP patches are restored.
    yield
    from loopx.control_plane.effect_runtime import restart_effect_runtime

    restart_effect_runtime(timeout=5)


def _identity_fixture(key_kind):
    identity = SettlementIdentity(
        goal_id="replay-fixture",
        agent_id="agent-a",
        todo_id="todo_abc123",
        turn_instance_id="original-turn",
    )
    key = identity.turn_instance_id if key_kind == "managed" else identity.effect_id
    todo = dict(
        todo_id=identity.todo_id,
        status="done",
        claimed_by=identity.agent_id,
        completion_turn_key=key,
        completion_continuation="successor",
        successor_todo_ids=["todo_def456"],
        evidence="Candidate verified.",
        role="agent",
        completion_receipt_id="original-committed-receipt",
    )
    return identity, todo


def _resolve(identity, todo, candidate=None):
    return completion_identity_for_original_turn(
        todo=todo,
        identity=identity,
        goal_id=identity.goal_id,
        agent_id=identity.agent_id,
        todo_id=identity.todo_id,
        candidate={"evidence": "Candidate verified.", **(candidate or {})},
    )


@pytest.mark.parametrize("key_kind", ["managed", "interactive"])
@pytest.mark.parametrize("continuation", ["successor", "active_goal", "no_followup"])
def test_original_completion_read_only_replay(key_kind, continuation):
    identity, todo = _identity_fixture(key_kind)
    todo["completion_continuation"] = continuation
    todo["no_followup"] = continuation == "no_followup"
    if continuation != "successor":
        todo["successor_todo_ids"] = []
    result = _resolve(identity, todo)
    assert result["completion_turn_key"] == todo["completion_turn_key"]
    assert result["replay"]["idempotent_replay"] is True
    assert result["replay"]["changed"] is False
    assert result["replay"]["completion_receipt_id"] == todo["completion_receipt_id"]
    assert result["replay"]["completion_continuation"] == continuation


@pytest.mark.parametrize("key_kind", ["managed", "interactive"])
@pytest.mark.parametrize(
    "candidate",
    [
        {"evidence": "Changed evidence."},
        {"note": "Changed note."},
        {"reason": "Changed reason."},
        {"next_agent_todo": "Changed successor."},
        {"next_user_todo": "Changed user obligation."},
        {"successor_todo_ids": ["todo_new789"]},
        {"result_file": "different.json"},
        {"next_claimed_by": "different-agent"},
        {"next_task_class": "maintenance"},
        {"next_action_kind": "review"},
        {"next_task_repository": "different-repo"},
        {"next_required_capabilities": "network"},
        {"next_continuation_policy": "different"},
        {"next_excluded_agents": "different-agent"},
        {"next_user_task_class": "approval"},
        {"decision_outcome": "different"},
        {"clear_claim": True},
        {"self_merged": True},
        {"role": "user"},
    ],
)
def test_changed_completion_is_never_read_only_replay(key_kind, candidate):
    identity, todo = _identity_fixture(key_kind)
    assert _resolve(identity, todo, candidate)["replay"] is None


@pytest.mark.parametrize("key_kind", ["managed", "interactive"])
@pytest.mark.parametrize(
    "field,value",
    [
        ("status", "open"),
        ("claimed_by", "other-agent"),
        ("todo_id", "todo_different"),
        ("completion_turn_key", "different-turn"),
        ("completion_continuation", None),
    ],
)
def test_wrong_or_incomplete_original_completion_cannot_replay(key_kind, field, value):
    identity, todo = _identity_fixture(key_kind)
    todo[field] = value
    assert _resolve(identity, todo)["replay"] is None


@pytest.mark.parametrize("key_kind", ["managed", "interactive"])
def test_terminal_upgrade_cannot_remove_committed_successor(key_kind):
    identity, todo = _identity_fixture(key_kind)
    with pytest.raises(ValueError, match="successor"):
        _resolve(identity, todo, {"no_followup": True})


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("intent", ["generated", "linked", "terminal"])
def test_coordinator_resumes_original_completion_intent(
    provider, intent, tmp_path, monkeypatch
):
    project = tmp_path
    candidate = project / "candidate.txt"
    validator = project / "verify.py"
    validator.write_text(
        "from pathlib import Path\np=Path(__file__).parent\nassert (p/'candidate.txt').read_text()=='valid'\nwith (p/'validation.txt').open('a') as f: f.write('validated\\n')\n",
        encoding="utf-8",
    )
    candidate.write_text("broken", encoding="utf-8")
    assert (
        subprocess.run([sys.executable, str(validator)], capture_output=True).returncode
        != 0
    )
    assert not (project / "validation.txt").exists()
    registry, state, todo_id = _fixture(
        provider,
        tmp_path,
        monkeypatch,
        validation_command_json=json.dumps([sys.executable, str(validator)]),
    )
    candidate.write_text("valid", encoding="utf-8")
    if intent == "linked":
        successor = add_goal_todo(
            registry_path=registry,
            goal_id=GOAL_ID,
            role="agent",
            text="Verify linked remaining work.",
            task_class="advancement_task",
        )
        options = {"successor_todo_ids": [successor["todo_id"]]}
    elif intent == "generated":
        options = {"next_agent_todo": "Verify generated remaining work."}
    else:
        options = {"no_follow_up": True}
    control = CoordinatorCheckpoints(
        registry, tmp_path / "runtime", GOAL_ID, AGENT_ID
    ).control
    guard = json.loads(control.should_run(todo_id))
    assert guard["delivery_result_context"]["settlement_identity"]["todo_id"] == todo_id
    first = json.loads(
        control.complete_task(todo_id, AGENT_ID, "Candidate verified.", **options)
    )
    assert first.get("stage") == "direction_pending", first
    before = list_goal_todos(
        registry_path=registry,
        runtime_root_arg=str(tmp_path / "runtime"),
        goal_id=GOAL_ID,
        todo_id=todo_id,
    )["todo"]
    original_identity = first["settlement_identity"]
    assert (
        first["context"]["basis"]["todo"]["completion_receipt_id"]
        == before["completion_receipt_id"]
    )
    event_log = tmp_path / "runtime/goals" / GOAL_ID / "rollout-event-log.jsonl"
    original_events = event_log.read_bytes()
    assert original_events
    coordinator = CoordinatorCheckpoints(
        registry, tmp_path / "runtime", GOAL_ID, AGENT_ID
    )
    old = coordinator.read(todo_id)
    state.write_text(
        state.read_text(encoding="utf-8")
        + "\n## Acceptance\nA new current obligation.\n",
        encoding="utf-8",
    )
    stale = coordinator.resume(
        todo_id,
        {
            "read_context_id": old["read_context_id"],
            "vision_unchanged_reason": "The old acceptance still applies.",
        },
    )
    assert stale.get("ok") is False, stale
    fresh = coordinator.read(todo_id)
    assert fresh["read_context_id"] != old["read_context_id"]
    assert "new current obligation" in json.dumps(fresh["basis"])
    context_dir = tmp_path / "runtime/goals" / GOAL_ID / "checkpoint-contexts"
    bound_basis_fields = (
        "identity",
        "work_basis",
        "decision_basis",
        "versions",
        "provider_revision",
    )
    original_bases = {}
    for path in context_dir.glob("*.json"):
        record = json.loads(path.read_text(encoding="utf-8"))
        if "read_context_id" in record:
            original_bases[path] = {
                field: record.get(field) for field in bound_basis_fields
            }
    assert original_bases
    result = coordinator.resume(
        todo_id,
        {
            "read_context_id": fresh["read_context_id"],
            "vision_unchanged_reason": "The validated candidate and current remaining obligations preserve the route.",
        },
    )
    (tmp_path / "resume-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    assert result.get("ok") is True and result.get("settlement_complete") is True, (
        json.dumps(result)
    )
    assert result["settlement_identity"] == original_identity
    after = list_goal_todos(
        registry_path=registry,
        runtime_root_arg=str(tmp_path / "runtime"),
        goal_id=GOAL_ID,
        todo_id=todo_id,
    )["todo"]
    assert after["completion_turn_key"] == before["completion_turn_key"]
    assert after.get("successor_todo_ids") == before.get("successor_todo_ids")
    assert event_log.read_bytes().startswith(original_events)
    for path, basis in original_bases.items():
        current = json.loads(path.read_text(encoding="utf-8"))
        assert {field: current.get(field) for field in bound_basis_fields} == basis
    if intent == "terminal":
        assert before["completion_continuation"] == "active_goal"
        assert (
            after["completion_continuation"] == "no_followup"
            and after["no_followup"] is True
        )
        assert after["completion_recovery"] == "same_turn_terminal_closeout"
        assert after["completion_receipt_id"] != before["completion_receipt_id"]
    else:
        assert after["completion_receipt_id"] == before["completion_receipt_id"]
    assert candidate.read_text() == "valid"
    assert (project / "validation.txt").read_text().splitlines() == ["validated"]
    index = tmp_path / "runtime/goals" / GOAL_ID / "runs/index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    snapshot = index.read_bytes()
    cold = CoordinatorCheckpoints(registry, tmp_path / "runtime", GOAL_ID, AGENT_ID)
    again = cold.read(todo_id)
    (tmp_path / "cold-read.json").write_text(
        json.dumps(again, indent=2), encoding="utf-8"
    )
    assert again["ok"] is True and again["replayed"] is True
    assert again["status"] == "committed" and "read_context_id" not in again
    assert again["context"]["read_context_id"] == fresh["read_context_id"]
    assert again["settlement_identity"] == original_identity
    replay = cold.resume(
        todo_id,
        {
            "read_context_id": fresh["read_context_id"],
            "vision_unchanged_reason": "The validated candidate and current remaining obligations preserve the route.",
        },
    )
    assert replay.get("settlement_complete") is True, replay
    assert replay["settlement_identity"] == original_identity
    assert index.read_bytes() == snapshot
    assert (project / "validation.txt").read_text().splitlines() == ["validated"]

@pytest.mark.parametrize("key_kind", ["managed", "interactive"])
@pytest.mark.parametrize(
    "candidate",
    [
        {"task_lease_idempotency_key": "original-lease"},
        {"task_lease_idempotency_key": "replacement-lease"},
        {"task_lease_idempotency_key": ""},
        {"task_lease_expected_version": 1},
        {"task_lease_expected_version": 999},
        {"task_lease_expected_version": 0},
        {"task_lease_expected_version": False},
        {"task_lease_idempotency_key": "original-lease", "task_lease_expected_version": 1},
    ],
)
def test_supplied_lease_requires_canonical_request_verification(key_kind, candidate):
    identity, todo = _identity_fixture(key_kind)
    # The Todo projection cannot prove a supplied lease belongs to the original
    # request. Even plausible values must reach the canonical receipt owner.
    assert _resolve(identity, todo, candidate)["replay"] is None


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("closeout_source", ["turn", "lifecycle"])
def test_original_terminal_cold_replay_keeps_lease_fingerprint(
    tmp_path, monkeypatch, provider, closeout_source
):
    from tests.control_plane import test_declared_terminal_settlement_cli as terminal

    fixture = terminal._fixture
    captured = []

    def capture(*args, **kwargs):
        result = fixture(*args, **kwargs)
        captured.append(result)
        return result

    monkeypatch.setattr(terminal, "_fixture", capture)
    # Execute the four existing frozen native journeys without changing their
    # assertions, including RESULT, new direction, one debit and cold replay.
    terminal.test_default_declared_terminal_intent_recovers_original_result(
        tmp_path, monkeypatch, provider, closeout_source
    )
    _, _, runtime, _, run, lease, marker, _, _ = captured[0]
    cli = terminal.cli
    command = (
        "todo", "complete", "--goal-id", cli.GOAL_ID,
        "--agent-id", cli.AGENT_ID, "--todo-id", cli.TODO_ID,
        "--turn-instance-id", cli.TURN_ID, *lease, "--no-follow-up",
        "--evidence", "Original declared deliverable, then current post-result direction.",
    )
    code, before = run("todo", "list", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)
    assert code == 0, before
    ledger = runtime / "goals" / cli.GOAL_ID / "runs" / "index.jsonl"
    original_ledger, original_validation = ledger.read_bytes(), marker.read_bytes()
    for field, value in (
        ("--task-lease-idempotency-key", "replacement-lease"),
        ("--task-lease-expected-version", "999"),
    ):
        changed = list(command)
        changed[changed.index(field) + 1] = value
        code, rejected = run(*changed)
        assert code == 1 and rejected.get("ok") is False, rejected
        code, after = run("todo", "list", "--goal-id", cli.GOAL_ID, "--todo-id", cli.TODO_ID)
        assert code == 0 and after["todo"] == before["todo"], after
        assert ledger.read_bytes() == original_ledger
        assert marker.read_bytes() == original_validation
    code, exact = run(*command)
    assert code == 0 and exact["idempotent_replay"] is True, exact
    assert ledger.read_bytes() == original_ledger
    assert marker.read_bytes() == original_validation

