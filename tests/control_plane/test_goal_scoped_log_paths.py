from __future__ import annotations

from pathlib import Path

import pytest

from loopx.control_plane.agents.supervisor_events import (
    build_supervisor_proposal_event,
    supervisor_event_log_path,
)
from loopx.rollout_event_log import build_rollout_event, rollout_event_log_path


UNSAFE_GOAL_IDS = ("../escape", "a/b", "/tmp/outside", "..", ".", r"..\escape")


@pytest.mark.parametrize("path_builder", [rollout_event_log_path, supervisor_event_log_path])
@pytest.mark.parametrize("goal_id", UNSAFE_GOAL_IDS)
def test_goal_scoped_log_paths_reject_path_traversal(
    tmp_path: Path,
    path_builder,
    goal_id: str,
) -> None:
    with pytest.raises(ValueError, match="single path segment"):
        path_builder(tmp_path, goal_id)

    assert not (tmp_path / "goals").exists()


@pytest.mark.parametrize(
    ("path_builder", "file_name"),
    [
        (rollout_event_log_path, "rollout-event-log.jsonl"),
        (supervisor_event_log_path, "supervisor-events.jsonl"),
    ],
)
def test_goal_scoped_log_paths_preserve_valid_goal_layout(
    tmp_path: Path,
    path_builder,
    file_name: str,
) -> None:
    assert path_builder(tmp_path, "goal-123") == (
        tmp_path / "goals" / "goal-123" / file_name
    )


def test_rollout_event_builder_rejects_unsafe_goal_id() -> None:
    with pytest.raises(ValueError, match="single path segment"):
        build_rollout_event(goal_id="../escape", event_kind="validation")


def test_supervisor_event_builder_rejects_unsafe_goal_id() -> None:
    with pytest.raises(ValueError, match="single path segment"):
        build_supervisor_proposal_event(
            goal_id="../escape",
            supervisor={"agent_id": "supervisor", "supervised_agents": []},
            decision={
                "kind": "observe",
                "decision_id": "decision-1",
                "reason_codes": ["inspect"],
                "evidence_refs": ["run:1"],
            },
        )


def test_history_evidence_cli_rejects_unsafe_goal_without_writing_outside_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import json

    from loopx.cli import main

    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(
        json.dumps({"common_runtime_root": str(runtime_root), "goals": []}) + "\n",
        encoding="utf-8",
    )

    exit_code = main(
        [
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime_root),
            "--format",
            "json",
            "history",
            "--evidence-ref",
            "replan-evidence-missing",
            "--goal-id",
            "../escape",
            "--agent-id",
            "agent-a",
        ]
    )

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "goal id must be a single path segment"
    assert not (runtime_root / "escape").exists()


def test_cli_rollout_event_does_not_create_path_outside_goals_root(tmp_path: Path) -> None:
    from loopx.cli_rollout import append_cli_rollout_event

    runtime_root = tmp_path / "runtime"
    payload = append_cli_rollout_event(
        {"ok": True, "goal_id": "../escape", "runtime_root": str(runtime_root)},
        registry_path=tmp_path / "registry.json",
        runtime_root_arg=None,
        event_kind="validation",
        summary="path boundary regression",
    )

    assert payload["rollout_event_log_error"]["error_type"] == "ValueError"
    assert not (runtime_root / "escape").exists()
