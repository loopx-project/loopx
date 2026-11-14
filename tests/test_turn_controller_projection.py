"""Producer/consumer qualification and real managed CLI routing cost boundary."""
from __future__ import annotations

import pytest

from loopx.control_plane.turn_driver.managed_step import decide_managed_step
from tests.test_loopx_turn_managed_step import (
    AGENT_ID, GOAL_ID, TODO_ID, _decide, _failed_journal,
)


@pytest.mark.parametrize("user_action_required", [False, True])
def test_managed_current_source_uses_one_controller_call_and_rejects_modified_content(
    monkeypatch, user_action_required,
) -> None:
    from copy import deepcopy

    from loopx.control_plane.quota import turn_envelope
    from loopx.control_plane.turn_driver import driver, loop_controller
    from tests.test_turn_route_action_classification import _run_decision

    decision = _run_decision(goal_id=GOAL_ID, agent_id=AGENT_ID,
        agent_identity={"agent_id": AGENT_ID},
        selected_todo={"todo_id": TODO_ID, "text": "Advance."})
    decision["interaction_contract"]["user_channel"]["action_required"] = user_action_required
    expected_source = turn_envelope.compose_turn_decision(decision)
    calls = []
    original = turn_envelope._turn_envelope_result

    def record(operation, **params):
        calls.append(operation)
        return original(operation, **params)

    monkeypatch.setattr(turn_envelope, "_turn_envelope_result", record)
    monkeypatch.setattr(driver, "_turn_envelope_result", record)
    current = turn_envelope.compose_turn_decision(decision)
    assert current == expected_source
    assert calls == ["compose_complete"]

    # Integrity is checked at the real consumer. A mutable transport copy
    # cannot attach the qualification of the original complete source.
    changed = deepcopy(current)
    changed["interaction_contract"]["agent_channel"]["delivery_allowed"] = False
    assert current == expected_source
    journal = _failed_journal()
    calls.clear()
    controller_calls = []
    original_runtime = loop_controller.effect_runtime_projection_result

    def record_runtime(method, params, **options):
        controller_calls.append((method, params["operation"]))
        return original_runtime(method, params, **options)

    monkeypatch.setattr(loop_controller, "effect_runtime_projection_result", record_runtime)
    actual = decide_managed_step(journal, current, goal_id=GOAL_ID,
        agent_id=AGENT_ID, turn_key=journal["turn_key"])
    assert actual["disposition"] == ("user_action_required" if user_action_required else "wait")
    assert actual["lineage"] == {"goal_id": GOAL_ID, "agent_id": AGENT_ID, "todo_id": TODO_ID}
    assert calls == []
    assert controller_calls == [("turn.controller", "managed")]
    with pytest.raises(ValueError, match="integrity"):
        _decide(journal, envelope=changed)
    assert calls == []
    assert controller_calls == [("turn.controller", "managed")] * 2


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
        assert calls.count("compose_complete") == 1
        assert "build_controller_input" not in calls and "build" not in calls
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
