from __future__ import annotations

from pathlib import Path

import pytest

from loopx.control_plane.todos.durable_completion import (
    project_durable_completion_intent,
    project_durable_completion_outcome,
    project_durable_terminal_completion_readback,
    read_persisted_todo_record,
    read_persisted_todo_record_with_source,
)

TODO_DONE = {
    "todo_id": "todo_fixture0001",
    "status": "done",
    "completion_continuation": "active_goal",
}


def test_projects_explicit_active_goal_continuation() -> None:
    outcome = project_durable_completion_outcome(
        todo=TODO_DONE,
        expected_todo_id="todo_fixture0001",
    )
    assert outcome == {
        "todo_id": "todo_fixture0001",
        "continuation": "active_goal",
    }


def test_projects_declared_successor_continuation() -> None:
    todo = {
        **TODO_DONE,
        "completion_continuation": "successor",
        "successor_todo_ids": ["todo_fixture0002", "todo_fixture0003"],
    }
    outcome = project_durable_completion_outcome(
        todo=todo,
        expected_todo_id="todo_fixture0001",
        existing_todo_ids={"todo_fixture0001", "todo_fixture0002", "todo_fixture0003"},
    )
    assert outcome == {
        "todo_id": "todo_fixture0001",
        "continuation": "successor",
        "successor_todo_ids": ["todo_fixture0002", "todo_fixture0003"],
    }


def test_projects_durable_no_followup_continuation() -> None:
    outcome = project_durable_completion_outcome(
        todo={
            **TODO_DONE,
            "completion_continuation": "no_followup",
            "no_followup": True,
        },
        expected_todo_id="todo_fixture0001",
    )
    assert outcome == {
        "todo_id": "todo_fixture0001",
        "continuation": "no_followup",
    }


def test_projects_terminal_intent_without_premature_completion() -> None:
    outcome = project_durable_completion_intent(
        todo={
            "todo_id": "todo_fixture0001",
            "status": "open",
            "no_followup": True,
        },
        expected_todo_id="todo_fixture0001",
    )
    assert outcome == {
        "todo_id": "todo_fixture0001",
        "continuation": "no_followup",
    }


@pytest.mark.parametrize(
    ("status", "projection_source"),
    [
        ("blocked", "materialized"),
        ("deferred", "materialized"),
    ],
)
def test_terminal_readback_does_not_treat_ambiguous_state_as_absent(
    status: str,
    projection_source: str,
) -> None:
    with pytest.raises(ValueError, match="not safely replayable"):
        project_durable_terminal_completion_readback(
            todo={
                "todo_id": "todo_fixture0001",
                "status": status,
                "no_followup": True,
            },
            expected_todo_id="todo_fixture0001",
            expected_completion_turn_key="sha256:current-turn",
            projection_source=projection_source,
        )


def test_terminal_readback_reports_explicitly_open_state_absent() -> None:
    assert project_durable_terminal_completion_readback(
        todo={
            "todo_id": "todo_fixture0001",
            "status": "open",
            "no_followup": True,
        },
        expected_todo_id="todo_fixture0001",
        expected_completion_turn_key="sha256:current-turn",
        projection_source="materialized",
    ) == {"kind": "absent"}


def test_terminal_readback_commits_same_turn_no_followup() -> None:
    assert project_durable_terminal_completion_readback(
        todo={
            "todo_id": "todo_fixture0001",
            "status": "done",
            "completion_continuation": "no_followup",
            "completion_turn_key": "sha256:current-turn",
            "no_followup": True,
        },
        expected_todo_id="todo_fixture0001",
        expected_completion_turn_key="sha256:current-turn",
        projection_source="materialized",
    ) == {
        "kind": "committed",
        "completion": {
            "todo_id": "todo_fixture0001",
            "continuation": "no_followup",
        },
    }


def test_terminal_readback_allows_same_turn_terminal_upgrade() -> None:
    assert project_durable_terminal_completion_readback(
        todo={
            "todo_id": "todo_fixture0001",
            "status": "done",
            "completion_continuation": "active_goal",
            "completion_turn_key": "sha256:current-turn",
        },
        expected_todo_id="todo_fixture0001",
        expected_completion_turn_key="sha256:current-turn",
        projection_source="materialized",
    ) == {"kind": "absent"}


def test_no_followup_without_existing_ids_needs_no_successor_verification() -> None:
    outcome = project_durable_completion_outcome(
        todo={
            **TODO_DONE,
            "completion_continuation": "no_followup",
            "no_followup": True,
        },
        expected_todo_id="todo_fixture0001",
        existing_todo_ids=set(),
    )
    assert outcome["continuation"] == "no_followup"


def test_fails_closed_on_no_followup_and_successor_contradiction() -> None:
    todo = {
        **TODO_DONE,
        "completion_continuation": "no_followup",
        "no_followup": True,
        "successor_todo_ids": ["todo_fixture0002"],
    }
    with pytest.raises(
        ValueError,
        match="both no_followup and successor_todo_ids",
    ):
        project_durable_completion_outcome(
            todo=todo,
            expected_todo_id="todo_fixture0001",
            existing_todo_ids={"todo_fixture0002"},
        )


def test_fails_closed_on_dangling_declared_successor() -> None:
    todo = {
        **TODO_DONE,
        "completion_continuation": "successor",
        "successor_todo_ids": ["todo_fixture0002", "todo_missing999"],
    }
    with pytest.raises(
        ValueError,
        match="declares missing successor Todo ids: todo_missing999",
    ):
        project_durable_completion_outcome(
            todo=todo,
            expected_todo_id="todo_fixture0001",
            existing_todo_ids={"todo_fixture0001", "todo_fixture0002"},
        )


def test_fails_closed_when_durable_todo_id_mismatches_selected() -> None:
    with pytest.raises(
        ValueError,
        match="does not match the selected Todo",
    ):
        project_durable_completion_outcome(
            todo={**TODO_DONE, "todo_id": "todo_fixture0009"},
            expected_todo_id="todo_fixture0001",
        )


def test_fails_closed_when_done_todo_omits_completion_continuation() -> None:
    with pytest.raises(
        ValueError,
        match="missing completion_continuation",
    ):
        project_durable_completion_outcome(
            todo={"todo_id": "todo_fixture0001", "status": "done"},
            expected_todo_id="todo_fixture0001",
        )


def test_fails_closed_when_recovery_is_not_terminal() -> None:
    with pytest.raises(
        ValueError,
        match="completion_recovery requires a no_followup continuation",
    ):
        project_durable_completion_outcome(
            todo={
                **TODO_DONE,
                "completion_continuation": "active_goal",
                "completion_recovery": "same_turn_terminal_closeout",
            },
            expected_todo_id="todo_fixture0001",
        )


def test_fails_closed_when_todo_is_not_durably_done() -> None:
    with pytest.raises(
        ValueError,
        match="durably done",
    ):
        project_durable_completion_outcome(
            todo={"todo_id": "todo_fixture0001", "status": "open"},
            expected_todo_id="todo_fixture0001",
        )


def test_read_persisted_todo_record_projects_declared_successor(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "updated_at: 2026-01-01T00:00:00+00:00",
                "---",
                "",
                "# Fixture",
                "",
                "## Agent Todo",
                "",
                "- [x] Advance one public fixture.",
                "  <!-- loopx:todo todo_id=todo_fixture0001 status=done task_class=advancement_task successor_todo_ids=todo_fixture0002 completion_continuation=successor -->",
                "- [ ] Continue the public fixture.",
                "  <!-- loopx:todo todo_id=todo_fixture0002 status=open task_class=advancement_task -->",
                "",
            ]
        ),
        encoding="utf-8",
    )
    todo, existing_todo_ids, projection_source = read_persisted_todo_record_with_source(
        state_file,
        todo_id="todo_fixture0001",
    )
    assert projection_source == "materialized"
    assert existing_todo_ids == {"todo_fixture0001", "todo_fixture0002"}
    outcome = project_durable_completion_outcome(
        todo=todo,
        expected_todo_id="todo_fixture0001",
        existing_todo_ids=existing_todo_ids,
    )
    assert outcome == {
        "todo_id": "todo_fixture0001",
        "continuation": "successor",
        "successor_todo_ids": ["todo_fixture0002"],
    }


def test_read_persisted_todo_record_fails_closed_when_todo_is_missing(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "\n".join(
            [
                "---",
                "status: active",
                "---",
                "",
                "## Agent Todo",
                "",
                "- [x] Some other fixture.",
                "  <!-- loopx:todo todo_id=todo_other0001 status=done -->",
                "",
            ]
        ),
        encoding="utf-8",
    )
    with pytest.raises(
        ValueError,
        match="was not found in persisted Todo lifecycle state",
    ):
        read_persisted_todo_record(
            state_file,
            todo_id="todo_fixture0001",
        )
