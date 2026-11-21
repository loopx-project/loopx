from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from loopx.control_plane.testing.replan_vision_closeout_behavior import (
    VisionHostAdmissionRejected,
    dispatch_vision_closeout,
)


def test_successful_cli_output_without_durable_receipt_is_correctable(tmp_path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    source = project / "work.json"
    source.write_text("{}", encoding="utf-8")
    frontier = project / "frontier.json"
    frontier.write_text(
        json.dumps({"uncovered": [{"source_ref": "work.json", "evidence_id": "evidence-a"}]}),
        encoding="utf-8",
    )
    (project / "vision.json").write_text(
        json.dumps({"path_delta": {"evidence_refs": ["evidence-a"]}}),
        encoding="utf-8",
    )
    fixture = SimpleNamespace(
        project_root=project,
        runtime_root=tmp_path / "runtime",
        frontier_target=frontier,
        work_source_target=source,
    )
    state = SimpleNamespace(
        fixture=fixture,
        turn_instance_id="turn-a",
        work_source_read=True,
        quota_packet={
            "goal_id": "goal-a",
            "interaction_contract": {
                "cli_channel": {
                    "replan_settlement_contract": {
                        "settlement_binding": {"cli_argument": "--binding", "id": "obligation-a"}
                    }
                }
            },
        },
    )
    executed = []

    def execute(command, **_kwargs):
        executed.append(command)
        return "{}"

    with pytest.raises(
        VisionHostAdmissionRejected, match="vision_closeout_durable_writeback_missing"
    ):
        dispatch_vision_closeout(
            "loopx refresh-state --binding obligation-a --turn-instance-id turn-a "
            "--agent-vision-json vision.json",
            state,
            execute=execute,
        )
    assert len(executed) == 1


def _vision_closeout_state(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    source = project / "work.json"
    source.write_text("{}", encoding="utf-8")
    frontier = project / "frontier.json"
    frontier.write_text(
        json.dumps({"uncovered": [{"source_ref": "work.json", "evidence_id": "evidence-a"}]}),
        encoding="utf-8",
    )
    (project / "vision.json").write_text(
        json.dumps({"path_delta": {"evidence_refs": ["evidence-a"]}}),
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    runs_dir = runtime_root / "goals" / "goal-a" / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    expected_identity = {"goal_id": "goal-a", "agent_id": "agent-a", "turn_key": "sha256:" + "a" * 64}
    fixture = SimpleNamespace(
        project_root=project,
        runtime_root=runtime_root,
        frontier_target=frontier,
        work_source_target=source,
        quota_guard_command="loopx quota should-run --goal-id goal-a",
    )
    state = SimpleNamespace(
        fixture=fixture,
        turn_instance_id="turn-a",
        work_source_read=True,
        semantic_delta=None,
        vision_closeout=None,
        semantic_reentry_observation=None,
        quota_packet={
            "goal_id": "goal-a",
            "heartbeat_receipt": {"settlement_identity": expected_identity},
            "interaction_contract": {
                "cli_channel": {
                    "replan_settlement_contract": {
                        "settlement_binding": {"cli_argument": "--binding", "id": "obligation-a"}
                    }
                }
            },
        },
    )
    return state, runs_dir / "index.jsonl", expected_identity


def test_refresh_state_with_settle_turn_completes_vision_closeout_without_duplicate_spend(
    tmp_path,
) -> None:
    state, index_path, expected_identity = _vision_closeout_state(tmp_path)
    writeback_row = {
        "classification": "bounded_replan_progress",
        "autonomous_replan_ack": {"semantic_delta": {"accepted": True, "obligation_id": "obligation-a"}},
        "vision_checkpoint": {"satisfied": True},
        "settlement_identity": expected_identity,
    }
    spend_row = {"classification": "quota_slot_spent"}
    executed = []

    def execute(command, **_kwargs):
        executed.append(command)
        if "refresh-state" in command:
            index_path.write_text(
                json.dumps(writeback_row) + "\n" + json.dumps(spend_row) + "\n",
                encoding="utf-8",
            )
            return json.dumps({"ok": True, "appended": True})
        if "quota should-run" in command:
            return json.dumps({"ok": True, "effective_action": "heartbeat_settled_skip"})
        raise AssertionError(f"unexpected command: {command}")

    _, kind, completed = dispatch_vision_closeout(
        "loopx refresh-state --binding obligation-a --turn-instance-id turn-a "
        "--agent-vision-json vision.json --settle-turn",
        state,
        execute=execute,
    )
    assert kind == "semantic_replan_writeback"
    assert completed is True
    assert state.vision_closeout == {
        "checkpoint_satisfied": True,
        "bound_writeback": True,
        "settled": True,
        "spend_count": 1,
        "original_obligation_closed": True,
    }
    assert state.semantic_reentry_observation == {
        "effective_action": "heartbeat_settled_skip",
        "trigger_kinds": [],
    }
    assert len(executed) == 3

    with pytest.raises(ValueError, match="vision_closeout_already_settled"):
        dispatch_vision_closeout(
            "loopx quota spend-slot --binding obligation-a --turn-instance-id turn-a",
            state,
            execute=execute,
        )


def test_refresh_state_without_spend_waits_for_explicit_spend_slot(tmp_path) -> None:
    state, index_path, expected_identity = _vision_closeout_state(tmp_path)
    writeback_row = {
        "classification": "bounded_replan_progress",
        "autonomous_replan_ack": {"semantic_delta": {"accepted": True, "obligation_id": "obligation-a"}},
        "vision_checkpoint": {"satisfied": True},
        "settlement_identity": expected_identity,
    }
    spend_row = {"classification": "quota_slot_spent"}

    def execute(command, **_kwargs):
        if "refresh-state" in command:
            index_path.write_text(json.dumps(writeback_row) + "\n", encoding="utf-8")
            return json.dumps({"ok": True, "appended": True})
        if "spend-slot" in command:
            with index_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(spend_row) + "\n")
            return json.dumps({"ok": True})
        if "quota should-run" in command:
            return json.dumps({"ok": True, "effective_action": "heartbeat_settled_skip"})
        raise AssertionError(f"unexpected command: {command}")

    _, kind, completed = dispatch_vision_closeout(
        "loopx refresh-state --binding obligation-a --turn-instance-id turn-a "
        "--agent-vision-json vision.json",
        state,
        execute=execute,
    )
    assert kind == "semantic_replan_writeback"
    assert completed is False
    assert state.vision_closeout == {
        "checkpoint_satisfied": True,
        "bound_writeback": True,
        "settled": False,
    }

    _, spend_kind, spend_completed = dispatch_vision_closeout(
        "loopx quota spend-slot --binding obligation-a --turn-instance-id turn-a",
        state,
        execute=execute,
    )
    assert spend_kind == "quota_spend_slot"
    assert spend_completed is True
    assert state.vision_closeout["settled"] is True
