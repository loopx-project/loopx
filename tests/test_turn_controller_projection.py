"""Producer/consumer qualification and real managed CLI routing cost boundary."""
from __future__ import annotations

import pytest

from loopx.control_plane.turn_driver.managed_step import decide_managed_step
from tests.test_loopx_turn_managed_step import (
    AGENT_ID, GOAL_ID, TODO_ID, _decide, _failed_journal,
)


@pytest.mark.parametrize("user_action_required", [False, True])
def test_managed_projection_reuses_route_without_trusting_a_mutable_wire(
    monkeypatch, user_action_required,
) -> None:
    from dataclasses import FrozenInstanceError, replace

    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.turn_driver import driver, loop_controller
    from tests.test_turn_route_action_classification import _run_decision

    decision = _run_decision(goal_id=GOAL_ID, agent_id=AGENT_ID,
        agent_identity={"agent_id": AGENT_ID},
        selected_todo={"todo_id": TODO_ID, "text": "Advance."})
    decision["interaction_contract"]["user_channel"]["action_required"] = user_action_required
    expected_envelope = turn_envelope.build_turn_envelope(decision)
    calls = []
    original = turn_envelope._turn_envelope_result

    def record(operation, **params):
        calls.append(operation)
        return original(operation, **params)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", record)
    monkeypatch.setattr(driver, "_turn_envelope_result", record)
    current = turn_envelope.build_turn_controller_input(decision)
    assert current.envelope() == expected_envelope
    assert calls == ["build_controller_input"]
    assert not hasattr(current, "__dict__")
    with pytest.raises(FrozenInstanceError):
        current.route = "ready_for_host"
    with pytest.raises(TypeError):
        turn_envelope.TurnControllerInput(current.envelope_json, "ready_for_host")
    with pytest.raises(TypeError):
        replace(current, route="ready_for_host")
    with pytest.raises(TypeError, match="original quota source"):
        turn_envelope.TurnControllerInput()

    # A returned object may be edited for another purpose; it cannot edit the
    # producer's snapshot or attach its route qualification to that copy.
    changed = current.envelope()
    changed["action"]["delivery_allowed"] = False
    changed["action_signature"]["matches"] = False
    changed["route"] = current.route
    assert current.envelope() == expected_envelope
    journal = _failed_journal()
    expected = _decide(journal, envelope=expected_envelope)
    assert expected["disposition"] == ("user_action_required" if user_action_required else "wait")
    calls.clear()
    with monkeypatch.context() as patch:
        patch.setattr(loop_controller, "_typed_route", lambda *_: pytest.fail("redundant route RPC"))
        actual = decide_managed_step(journal, current, goal_id=GOAL_ID,
            agent_id=AGENT_ID, turn_key=journal["turn_key"])
    assert actual == expected
    assert calls == []
    with pytest.raises(ValueError, match="shared envelope contract"):
        _decide(journal, envelope=changed)
    assert calls == ["driver_route"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_managed_cli_reuses_fresh_route_and_preserves_failed_turn(
    tmp_path, monkeypatch, provider,
) -> None:
    import contextlib
    import io
    import json

    from loopx.cli import main as cli_main
    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.turn_driver import driver, turn_journal_path
    from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
    from tests.test_loopx_turn_driver import _write_live_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    lineage = {"goal_id": "loopx-turn-fixture", "agent_id": "codex-fixture", "todo_id": "todo_fixture0001"}
    journal = _failed_journal(lineage=lineage)
    path = turn_journal_path(runtime, goal_id=lineage["goal_id"], turn_key=journal["turn_key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(journal))
    before = path.read_bytes()
    calls = []
    original = turn_envelope._turn_envelope_result

    def record(operation, **params):
        calls.append(operation)
        return original(operation, **params)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", record)
    monkeypatch.setattr(driver, "_turn_envelope_result", record)
    argv = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "managed-step", "--goal-id", lineage["goal_id"], "--agent-id", lineage["agent_id"],
        "--turn-key", journal["turn_key"], "--host", "dsh", "--execution-mode", "isolated-headless",
        "--observed-attempt", "1", "--observed-max-attempts", "3"]
    for _ in range(2):
        calls.clear()
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli_main(argv)
        result = json.loads(output.getvalue())
        assert code == 0 and result["disposition"] == "wait", result
        assert result["retry_continuation"]["retry_after_seconds"] == 30
        assert result["retry_continuation"]["max_attempts"] == 3
        assert calls.count("build_controller_input") == 1
        assert "driver_route" not in calls
        assert path.read_bytes() == before
    # The observation discrepancy must still fail without offering a retry.
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main([*argv[:-4], "--observed-attempt", "2", "--observed-max-attempts", "3"])
    rejected = json.loads(output.getvalue())
    assert code == 1 and rejected["ok"] is False
    assert "retry_continuation" not in rejected
    assert path.read_bytes() == before
    index = runtime / "goals" / lineage["goal_id"] / "runs/index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)
