"""Capability evidence must stay bound to the validated completion settlement."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.turn_driver import journal_store
from loopx.control_plane.turn_driver import turn_journal_runtime
from loopx.control_plane.turn_driver.journal_store import (
    turn_journal_observed_capabilities,
    write_turn_journal_checkpoint,
)


GOAL_ID = "journal-capability-goal"
AGENT_ID = "codex-journal-capability"
OTHER_AGENT_ID = "codex-other-actor"
TODO_ID = "todo_report_stage"
OTHER_TODO_ID = "todo_other_stage"
TURN_ID = "turn-journal-evidence-7"
OTHER_TURN = "turn-journal-unrelated-8"
EFFECT_ID = f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{TURN_ID}"
TURN_KEY = "sha256:" + "c" * 64
TRANSACTION_PHASES = [
    "host_execute",
    "typed_result",
    "validation",
    "durable_writeback",
    "quota_spend",
    "scheduler_apply",
    "scheduler_ack",
]
# The TS journal writer only accepts this committed-phase ladder.
_COMMIT_LADDER = [
    ("in_progress", 0),
    ("in_progress", 2),
    ("in_progress", 3),
    ("in_progress", 4),
    ("in_progress", 5),
    ("committed", 7),
]


def _completion_identity(
    *,
    goal_id: str = GOAL_ID,
    agent_id: str = AGENT_ID,
    todo_id: str = TODO_ID,
    turn_instance_id: str = TURN_ID,
    effect_id: str | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "quota_settlement_identity_v0",
        "effect_id": effect_id
        or f"{goal_id}:{agent_id}:{todo_id}:{turn_instance_id}",
        "goal_id": goal_id,
        "agent_id": agent_id,
        "todo_id": todo_id,
        "turn_instance_id": turn_instance_id,
    }


def _journal(
    *,
    boundary: dict[str, object] | None = None,
    capability_gate: dict[str, object] | None = None,
    agent_id: str = AGENT_ID,
    selected_todo_id: str = TODO_ID,
    settlement_agent_id: str | None = None,
    settlement_todo_id: str | None = None,
    settlement_turn_instance_id: str | None = TURN_ID,
    settlement_effect_id: str | None = None,
    with_settlement_plan: bool = True,
) -> dict[str, Any]:
    """Build a committed journal whose binding fields are adjustable."""

    envelope: dict[str, Any] = {
        "goal_id": GOAL_ID,
        "agent_id": agent_id,
        "action": {"selected_todo": {"todo_id": selected_todo_id}},
    }
    if boundary is not None:
        envelope["boundary"] = boundary
    if capability_gate is not None:
        envelope["capability_gate"] = capability_gate
    identity_agent = settlement_agent_id or agent_id
    identity_todo = settlement_todo_id or selected_todo_id
    identity_turn = settlement_turn_instance_id or TURN_ID
    transaction: dict[str, Any] = {
        "turn_key": TURN_KEY,
        "turn_instance_id": TURN_ID,
    }
    if with_settlement_plan:
        transaction["settlement_plan"] = {
            "schema_version": "quota_settlement_plan_v1",
            "identity": {
                "schema_version": "quota_settlement_identity_v0",
                "effect_id": settlement_effect_id
                or f"{GOAL_ID}:{identity_agent}:{identity_todo}:{identity_turn}",
                "goal_id": GOAL_ID,
                "agent_id": identity_agent,
                "todo_id": identity_todo,
                "turn_instance_id": identity_turn,
            },
        }
    return {
        "schema_version": "loopx_turn_journal_v0",
        "turn_key": TURN_KEY,
        "goal_id": GOAL_ID,
        "status": "committed",
        "completed_phases": list(TRANSACTION_PHASES),
        "plan": {"transaction": transaction, "turn_envelope": envelope},
    }


def _write_journal(
    runtime: Path,
    journal: dict[str, Any],
    *,
    digest: str = "c" * 64,
) -> Path:
    turns = runtime / "goals" / GOAL_ID / "turns"
    turns.mkdir(parents=True, exist_ok=True)
    path = turns / f"{digest}.json"
    path.write_text(json.dumps(journal), encoding="utf-8")
    return path


def _commit_journal_via_typescript_writer(
    runtime: Path, journal: dict[str, Any]
) -> Path:
    """Produce the journal through the TS semantic writer, not a hand dump."""

    turns = runtime / "goals" / GOAL_ID / "turns"
    turns.mkdir(parents=True, exist_ok=True)
    path = turns / f"{TURN_KEY.removeprefix('sha256:')}.json"
    for status, phase_count in _COMMIT_LADDER:
        write_turn_journal_checkpoint(
            path,
            {
                **journal,
                "status": status,
                "completed_phases": list(TRANSACTION_PHASES[:phase_count]),
            },
        )
    return path


def _observed(
    runtime: Path,
    *,
    completion: dict[str, Any] | None = None,
) -> list[str] | None:
    return turn_journal_observed_capabilities(
        runtime, settlement_identity=completion or _completion_identity()
    )


def test_typescript_committed_journal_lends_boundary_capabilities(
    tmp_path: Path,
) -> None:
    journal = _journal(
        boundary={"available_capabilities": ["network", "lark_bot_message_write"]}
    )
    path = _commit_journal_via_typescript_writer(tmp_path, journal)
    assert path.exists()

    assert _observed(tmp_path) == ["network", "lark_bot_message_write"]


def test_read_gate_proven_capabilities_when_none_missing(tmp_path: Path) -> None:
    _write_journal(
        tmp_path,
        _journal(
            capability_gate={
                "required_capabilities": ["network"],
                "missing_capabilities": [],
            }
        ),
    )

    assert _observed(tmp_path) == ["network"]


def test_missing_gate_capabilities_stay_unproven(tmp_path: Path) -> None:
    _write_journal(
        tmp_path,
        _journal(
            capability_gate={
                "required_capabilities": ["network", "lark_bot_message_write"],
                "missing_capabilities": ["network"],
            }
        ),
    )

    assert _observed(tmp_path) == []


def test_other_agent_journal_with_same_turn_id_provides_no_evidence(
    tmp_path: Path,
) -> None:
    _write_journal(
        tmp_path, _journal(agent_id=OTHER_AGENT_ID, settlement_agent_id=OTHER_AGENT_ID)
    )

    inspection = turn_journal_runtime.interpret_turn_journal_projection(
        _journal(agent_id=OTHER_AGENT_ID, settlement_agent_id=OTHER_AGENT_ID),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_key=TURN_KEY,
    )
    assert inspection["decision"] == "replay_blocked"
    assert "owner_mismatch" in inspection["violations"]
    assert _observed(tmp_path) is None


def test_other_todo_journal_with_same_turn_id_provides_no_evidence(
    tmp_path: Path,
) -> None:
    # The journal is internally TS-legal and shares this goal/agent/turn id,
    # but its settlement binds another Todo, so it cannot lend capabilities.
    other_todo = _journal(
        boundary={"available_capabilities": ["network"]},
        selected_todo_id=OTHER_TODO_ID,
        settlement_todo_id=OTHER_TODO_ID,
    )
    _write_journal(tmp_path, other_todo)

    inspection = turn_journal_runtime.interpret_turn_journal_projection(
        other_todo,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_key=TURN_KEY,
    )
    assert inspection["decision"] == "replay_legal"
    assert _observed(tmp_path) is None


def test_transaction_settlement_turn_conflict_provides_no_evidence(
    tmp_path: Path,
) -> None:
    _write_journal(
        tmp_path, _journal(settlement_turn_instance_id=OTHER_TURN)
    )

    inspection = turn_journal_runtime.interpret_turn_journal_projection(
        _journal(settlement_turn_instance_id=OTHER_TURN),
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_key=TURN_KEY,
    )
    assert inspection["decision"] == "replay_blocked"
    assert "settlement_turn_instance_mismatch" in inspection["violations"]
    assert _observed(tmp_path) is None


def test_journal_without_settlement_plan_provides_no_evidence(
    tmp_path: Path,
) -> None:
    journal = _journal(
        boundary={"available_capabilities": ["network"]},
        with_settlement_plan=False,
    )
    _write_journal(tmp_path, journal)

    inspection = turn_journal_runtime.interpret_turn_journal_projection(
        journal,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        turn_key=TURN_KEY,
    )
    assert inspection["decision"] == "replay_blocked"
    assert "settlement_identity_invalid" in inspection["violations"]
    assert _observed(tmp_path) is None


def test_settlement_effect_id_mismatch_provides_no_evidence(
    tmp_path: Path,
) -> None:
    _write_journal(
        tmp_path,
        _journal(
            settlement_effect_id=f"{GOAL_ID}:{AGENT_ID}:{TODO_ID}:{OTHER_TURN}"
        ),
    )

    assert _observed(tmp_path) is None


def test_malformed_completion_identity_fails_closed(tmp_path: Path) -> None:
    _write_journal(
        tmp_path,
        _journal(boundary={"available_capabilities": ["network"]}),
    )
    incomplete = _completion_identity()
    incomplete.pop("effect_id")

    assert _observed(tmp_path, completion=incomplete) is None


@pytest.mark.parametrize("turn_instance_id", [OTHER_TURN, ""])
def test_unmatched_turn_returns_none(tmp_path: Path, turn_instance_id: str) -> None:
    _write_journal(tmp_path, _journal(boundary={"available_capabilities": ["network"]}))

    assert (
        _observed(
            tmp_path,
            completion=_completion_identity(turn_instance_id=turn_instance_id),
        )
        is None
    )


def test_missing_or_unreadable_journals_fail_closed(tmp_path: Path) -> None:
    assert _observed(tmp_path) is None

    turns = tmp_path / "goals" / GOAL_ID / "turns"
    turns.mkdir(parents=True)
    (turns / "corrupt.json").write_text("{not json", encoding="utf-8")
    (turns / f"{'d' * 64}.json").write_text(
        json.dumps({"schema_version": "unsupported_v9"}),
        encoding="utf-8",
    )

    assert _observed(tmp_path) is None


def test_foreign_goal_journal_provides_no_evidence(tmp_path: Path) -> None:
    turns = tmp_path / "goals" / GOAL_ID / "turns"
    turns.mkdir(parents=True)
    foreign = _journal(boundary={"available_capabilities": ["network"]})
    foreign["goal_id"] = "journal-other-goal"
    (turns / f"{TURN_KEY.removeprefix('sha256:')}.json").write_text(
        json.dumps(foreign), encoding="utf-8"
    )

    assert _observed(tmp_path) is None


def test_typescript_replay_block_provides_no_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_journal(
        tmp_path,
        _journal(boundary={"available_capabilities": ["network"]}),
    )

    def blocked(*_args: object, **_kwargs: object) -> dict[str, object]:
        return {"observed_capabilities": None}

    monkeypatch.setattr(journal_store, "effect_runtime_result", blocked)

    assert _observed(tmp_path) is None


def test_typescript_runtime_failure_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_journal(
        tmp_path,
        _journal(boundary={"available_capabilities": ["network"]}),
    )

    def unavailable(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("TypeScript Effect runtime request failed")

    monkeypatch.setattr(
        journal_store, "effect_runtime_result", unavailable
    )

    assert _observed(tmp_path) is None


def test_ambiguous_duplicate_bound_journals_fail_closed(tmp_path: Path) -> None:
    # Two distinct files both claim the same settlement effect: the reader
    # must not resolve the ambiguity by returning the first sorted match.
    _write_journal(
        tmp_path,
        _journal(boundary={"available_capabilities": ["network"]}),
        digest="c" * 64,
    )
    duplicate = _journal(boundary={"available_capabilities": ["network"]})
    duplicate["turn_key"] = "sha256:" + "e" * 64
    duplicate["plan"]["transaction"]["turn_key"] = duplicate["turn_key"]
    _write_journal(tmp_path, duplicate, digest="e" * 64)

    assert _observed(tmp_path) is None


@pytest.mark.parametrize("damage", ["unreadable", "owner", "phase", "binding"])
def test_recovery_lookup_never_turns_uncertain_history_into_a_new_turn(
    tmp_path: Path, damage: str,
) -> None:
    journal = _journal()
    if damage == "owner":
        journal["plan"]["turn_envelope"]["agent_id"] = OTHER_AGENT_ID
    elif damage == "phase":
        journal["completed_phases"] = ["quota_spend"]
    elif damage == "binding":
        journal["plan"]["transaction"]["settlement_plan"]["identity"]["binding_id"] = OTHER_TODO_ID
    path = _write_journal(tmp_path, journal)
    if damage == "unreadable":
        path.write_text("{interrupted", encoding="utf-8")
    with pytest.raises((ValueError, RuntimeError), match="journal|Journal"):
        journal_store.find_loopx_turn_key_by_settlement_identity(
            tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID,
            todo_id=TODO_ID, turn_instance_id=TURN_ID,
        )


@pytest.mark.parametrize("status,phase_count", _COMMIT_LADDER)
def test_recovery_lookup_characterizes_every_durable_prefix(
    tmp_path: Path, status: str, phase_count: int,
) -> None:
    journal = _journal(boundary={"available_capabilities": ["network"]})
    journal.update(status=status, completed_phases=TRANSACTION_PHASES[:phase_count])
    _write_journal(tmp_path, journal)
    assert journal_store.find_loopx_turn_key_by_settlement_identity(
        tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=TODO_ID, turn_instance_id=TURN_ID,
    ) == TURN_KEY
    # Recoverability of an in-flight journal does not grant completed evidence.
    assert _observed(tmp_path) == (["network"] if status == "committed" else None)


def test_both_readers_use_one_native_query_even_with_same_turn_foreign_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    for index in range(32):
        other = _journal(selected_todo_id=f"todo_other_{index}")
        digest = f"{index:064x}"
        other["turn_key"] = other["plan"]["transaction"]["turn_key"] = f"sha256:{digest}"
        _write_journal(tmp_path, other, digest=digest)
    _write_journal(tmp_path, _journal(boundary={"available_capabilities": ["network"]}))
    actual = journal_store.effect_runtime_result
    calls: list[str] = []

    def counted(method: str, *args: Any, **kwargs: Any) -> Any:
        calls.append(method)
        return actual(method, *args, **kwargs)

    monkeypatch.setattr(journal_store, "effect_runtime_result", counted)
    assert _observed(tmp_path) == ["network"]
    assert calls == ["turn_journal.observed_capabilities"]
    calls.clear()
    assert journal_store.find_loopx_turn_key_by_settlement_identity(
        tmp_path, goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=TODO_ID, turn_instance_id=TURN_ID,
    ) == TURN_KEY
    assert calls == ["turn_journal.find_settlement"]


def test_unreadable_neighbor_cannot_lend_capabilities(tmp_path: Path) -> None:
    _write_journal(tmp_path, _journal(boundary={"available_capabilities": ["network"]}))
    bad = _write_journal(tmp_path, _journal(), digest="f" * 64)
    bad.write_text("{lost", encoding="utf-8")
    assert _observed(tmp_path) is None
