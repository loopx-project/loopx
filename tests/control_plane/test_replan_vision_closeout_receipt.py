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
