from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
from threading import Event, Lock
from typing import Any

import pytest

from loopx.capabilities.periodic_report import incremental
from loopx.capabilities.periodic_report.incremental import (
    build_periodic_report_publication_candidate,
    commit_periodic_report_publication_cursor,
    periodic_report_incremental_baseline,
    read_periodic_report_goal_publication_cursors,
    read_periodic_report_publication_cursor,
    select_incremental_project_progress,
)
from loopx.capabilities.periodic_report.post_writeback_hook import (
    evaluate_periodic_report_trigger_evaluation_intent,
    periodic_report_post_writeback_hook,
)
from loopx.capabilities.periodic_report.project_progress_snapshot import (
    build_project_progress_snapshot_from_state,
)


GOAL_ID = "example-goal"
AGENT_ID = "example-agent"


def _item(
    source_ref: str,
    *,
    title: str,
    summary: str,
    content_kind: str = "outcome",
) -> dict[str, object]:
    return {
        "item_id": source_ref.replace(":", "_"),
        "title": title,
        "summary": summary,
        "content_kind": content_kind,
        "source_ref": source_ref,
        "completed_at": "2026-08-01T08:00:00Z",
    }


def _snapshot(items: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": "periodic_report_project_progress_projection_v0",
        "goal_id": GOAL_ID,
        "observed_at": "2026-08-01T08:00:00Z",
        "language": "zh-CN",
        "items": items,
    }


def _trigger(trigger_id: str) -> dict[str, object]:
    return {
        "coalesced_trigger_ids": [trigger_id],
    }


def test_two_cycle_increment_reports_only_new_and_changed_facts(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    first = select_incremental_project_progress(
        _snapshot(
            [
                _item("todo:a", title="A completed", summary="A is done."),
                _item(
                    "todo:b",
                    title="B started",
                    summary="B is open.",
                    content_kind="next_action",
                ),
            ]
        ),
        cursor=None,
    )
    assert first is not None
    candidate_one = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_first",
        trigger_receipt=_trigger("trigger_first"),
        facts=first["items"],
        baseline=None,
    )
    cursor_one = commit_periodic_report_publication_cursor(
        runtime_root=runtime,
        candidate=candidate_one,
        publication_id="goal-channel:first",
        delivered_at="2026-08-01T09:00:00Z",
        covered_until="2026-08-01T08:00:00Z",
    )
    replayed_cursor_one = commit_periodic_report_publication_cursor(
        runtime_root=runtime,
        candidate=candidate_one,
        publication_id="goal-channel:first",
        delivered_at="2026-08-01T10:00:00Z",
        covered_until="2026-08-01T08:00:00Z",
    )
    assert replayed_cursor_one == cursor_one

    second = select_incremental_project_progress(
        _snapshot(
            [
                _item("todo:a", title="A completed", summary="A is done."),
                _item("todo:b", title="B completed", summary="B is now done."),
                _item("todo:c", title="C completed", summary="C is new."),
            ]
        ),
        cursor=cursor_one,
    )
    assert second is not None
    by_ref = {item["source_ref"]: item for item in second["items"]}
    assert "todo:a" not in by_ref
    assert by_ref["todo:b"]["change_kind"] == "changed"
    assert by_ref["todo:b"]["previous_status"] == "open"
    assert by_ref["todo:c"]["change_kind"] == "added"
    assert second["incremental_baseline"] == periodic_report_incremental_baseline(
        cursor_one
    )

    candidate_two = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_second",
        trigger_receipt=_trigger("trigger_second"),
        facts=second["items"],
        baseline=second["incremental_baseline"],
    )
    cursor_two = commit_periodic_report_publication_cursor(
        runtime_root=runtime,
        candidate=candidate_two,
        publication_id="goal-channel:second",
        delivered_at="2026-08-08T09:00:00Z",
        covered_until="2026-08-08T08:00:00Z",
    )
    assert cursor_two["predecessor_publication_id"] == "goal-channel:first"
    assert cursor_two["covered_trigger_ids"] == ["trigger_first", "trigger_second"]
    assert (
        select_incremental_project_progress(
            _snapshot(
                [
                    _item("todo:a", title="A completed", summary="A is done."),
                    _item("todo:b", title="B completed", summary="B is now done."),
                    _item("todo:c", title="C completed", summary="C is new."),
                ]
            ),
            cursor=cursor_two,
        )
        is None
    )


def test_generation_or_failed_delivery_does_not_advance_publication_cursor(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    first = select_incremental_project_progress(
        _snapshot([_item("todo:a", title="A completed", summary="A is done.")]),
        cursor=None,
    )
    assert first is not None
    candidate = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_unpublished",
        trigger_receipt=_trigger("trigger_unpublished"),
        facts=first["items"],
        baseline=None,
    )

    assert candidate["generation_id"] == "report_generation_unpublished"
    assert (
        read_periodic_report_publication_cursor(
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
        )
        is None
    )
    retry = select_incremental_project_progress(
        deepcopy(_snapshot(first["items"])), cursor=None
    )
    assert retry is not None
    assert [item["source_ref"] for item in retry["items"]] == ["todo:a"]


def test_candidate_rejects_an_untyped_incremental_baseline() -> None:
    with pytest.raises(ValueError, match="incremental baseline must use"):
        build_periodic_report_publication_candidate(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            generation_id="report_generation_invalid",
            trigger_receipt=_trigger("trigger_invalid"),
            facts=[_item("todo:a", title="A completed", summary="A is done.")],
            baseline={
                "cursor_id": "report_cursor_example",
                "predecessor_generation_id": "report_generation_example",
                "predecessor_publication_id": "goal-channel:example",
                "delivered_at": "2026-08-01T09:00:00Z",
                "covered_until": "2026-08-01T08:00:00Z",
            },
        )


def test_stale_candidate_cannot_overwrite_a_newer_publication_cursor(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    fact = _item("todo:a", title="A completed", summary="A is done.")
    first = select_incremental_project_progress(_snapshot([fact]), cursor=None)
    assert first is not None
    candidate_one = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_first",
        trigger_receipt=_trigger("trigger_first"),
        facts=first["items"],
        baseline=None,
    )
    cursor = commit_periodic_report_publication_cursor(
        runtime_root=runtime,
        candidate=candidate_one,
        publication_id="goal-channel:first",
        delivered_at="2026-08-01T09:00:00Z",
        covered_until="2026-08-01T08:00:00Z",
    )
    stale = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_stale",
        trigger_receipt=_trigger("trigger_stale"),
        facts=first["items"],
        baseline=None,
    )
    with pytest.raises(ValueError, match="baseline does not match"):
        commit_periodic_report_publication_cursor(
            runtime_root=runtime,
            candidate=stale,
            publication_id="goal-channel:stale",
            delivered_at="2026-08-01T10:00:00Z",
            covered_until="2026-08-01T08:30:00Z",
        )
    assert (
        read_periodic_report_publication_cursor(
            runtime_root=runtime,
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
        )
        == cursor
    )


def test_concurrent_publications_compare_and_swap_under_one_cursor_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / "runtime"
    fact = _item("todo:a", title="A completed", summary="A is done.")
    first = select_incremental_project_progress(_snapshot([fact]), cursor=None)
    assert first is not None
    candidates = [
        build_periodic_report_publication_candidate(
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            generation_id=f"report_generation_{suffix}",
            trigger_receipt=_trigger(f"trigger_{suffix}"),
            facts=first["items"],
            baseline=None,
        )
        for suffix in ("one", "two")
    ]
    first_acquired = Event()
    second_attempted = Event()
    counter_lock = Lock()
    attempts = 0
    original_lock = incremental.exclusive_file_lock

    @contextmanager
    def ordered_lock(*args: Any, **kwargs: Any):
        nonlocal attempts
        with counter_lock:
            attempts += 1
            attempt = attempts
        if attempt == 1:
            with original_lock(*args, **kwargs) as lock_path:
                first_acquired.set()
                assert second_attempted.wait(timeout=2)
                yield lock_path
            return
        assert first_acquired.wait(timeout=2)
        second_attempted.set()
        with original_lock(*args, **kwargs) as lock_path:
            yield lock_path

    monkeypatch.setattr(incremental, "exclusive_file_lock", ordered_lock)

    def commit(index: int) -> tuple[str, str]:
        try:
            cursor = commit_periodic_report_publication_cursor(
                runtime_root=runtime,
                candidate=candidates[index],
                publication_id=f"goal-channel:{index}",
                delivered_at=f"2026-08-01T{index + 9:02d}:00:00Z",
                covered_until="2026-08-01T08:00:00Z",
            )
        except ValueError as exc:
            return "rejected", str(exc)
        return "committed", str(cursor["generation_id"])

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(commit, (0, 1)))

    assert sorted(result[0] for result in results) == ["committed", "rejected"]
    assert next(value for status, value in results if status == "rejected") == (
        "publication candidate baseline does not match the current cursor"
    )
    cursor = read_periodic_report_publication_cursor(
        runtime_root=runtime,
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
    )
    assert cursor is not None
    assert cursor["generation_id"] == next(
        value for status, value in results if status == "committed"
    )


def test_published_stage_trigger_is_suppressed_until_a_new_stage_exists() -> None:
    hook = periodic_report_post_writeback_hook(
        profile_ref={
            "profile_id": "weekly_progress",
            "profile_version": "v1",
        },
        trigger_policy={
            "enabled_kinds": ["bounded_segment_milestone"],
            "minimum_interval_seconds": 0,
            "aggregation": {
                "window_seconds": 604800,
                "stage_completion_required": True,
            },
        },
    )
    stage = {
        "schema_version": "periodic_report_stage_completion_receipt_v0",
        "stage_identity": "stage-first",
        "agent_id": AGENT_ID,
        "closed_vision_revision": "vision-first",
        "frontier_identity": "frontier-first",
        "transition": "successor_frontier_settled",
        "completed_at": "2026-08-01T08:00:00Z",
        "acceptance": "validated",
        "outcome_checkpoint_satisfied": True,
        "durable_writeback_required": True,
    }
    initial = hook.producer(
        {
            "receipt": {"event_id": "event-first"},
            "projection": {"stage_completion": stage},
        }
    )
    first_decision = evaluate_periodic_report_trigger_evaluation_intent(
        initial["intent"]
    )
    assert first_decision["eligible"] is True

    repeated = hook.producer(
        {
            "receipt": {"event_id": "event-replayed"},
            "projection": {
                "stage_completion": stage,
                "last_report": {
                    "delivered_at": "2026-08-01T09:00:00Z",
                    "covered_trigger_ids": first_decision["coalesced_trigger_ids"],
                },
            },
        }
    )
    repeated_decision = evaluate_periodic_report_trigger_evaluation_intent(
        repeated["intent"]
    )
    assert repeated_decision["eligible"] is False
    assert repeated_decision["suppressed_triggers"][0]["reason"] == "already_covered"


def test_snapshot_applies_cursor_before_the_six_item_report_limit(
    tmp_path: Path,
) -> None:
    state_items = []
    for index in range(7):
        state_items.append(
            "\n".join(
                [
                    f"- [x] Completed item {index}.",
                    "  <!-- loopx:todo "
                    f"todo_id=todo_{index} status=done task_class=advancement_task "
                    f"claimed_by={AGENT_ID} updated_at=2026-08-01T0{index}:00:00Z -->",
                ]
            )
        )
    state = "# Goal\n\n## User Todo\n\n## Agent Todo\n\n" + "\n".join(state_items)
    first = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert first is not None
    assert len(first["items"]) == 6
    candidate = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        generation_id="report_generation_first_six",
        trigger_receipt=_trigger("trigger_first_six"),
        facts=first["items"],
        baseline=None,
    )
    cursor = commit_periodic_report_publication_cursor(
        runtime_root=tmp_path / "runtime",
        candidate=candidate,
        publication_id="goal-channel:first-six",
        delivered_at="2026-08-01T09:00:00Z",
        covered_until="2026-08-01T08:00:00Z",
    )
    second = build_project_progress_snapshot_from_state(
        state_text=state,
        goal={"id": GOAL_ID},
        state_path=tmp_path / "goal.md",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        completed_at="2026-08-01T10:00:00Z",
        publication_cursor=cursor,
    )
    assert second is not None
    assert len(second["items"]) == 1
    assert second["items"][0]["title"] == "Completed item 0."


def test_snapshot_skips_done_items_without_valid_completion_timestamps(
    tmp_path: Path,
) -> None:
    state = (
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [x] Valid outcome with receipt.\n"
        "  <!-- loopx:todo todo_id=todo_valid status=done task_class=advancement_task "
        f"claimed_by={AGENT_ID} updated_at=2026-08-01T07:00:00Z -->\n"
        "- [x] Handwritten outcome without a timestamp.\n"
        "  <!-- loopx:todo todo_id=todo_handwritten status=done"
        f" task_class=advancement_task claimed_by={AGENT_ID} -->\n"
        "- [x] Naive timestamp outcome.\n"
        "  <!-- loopx:todo todo_id=todo_naive status=done task_class=advancement_task "
        f"claimed_by={AGENT_ID} updated_at=2026-08-01T07:30:00 -->\n"
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )

    assert snapshot is not None
    assert [item["source_ref"] for item in snapshot["items"]] == ["todo:todo_valid"]
    assert snapshot["items"][0]["completed_at"] == "2026-08-01T07:00:00Z"

    handwritten_only = (
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [x] Handwritten outcome without a timestamp.\n"
        "  <!-- loopx:todo todo_id=todo_handwritten status=done"
        f" task_class=advancement_task claimed_by={AGENT_ID} -->\n"
    )
    assert (
        build_project_progress_snapshot_from_state(
            state_text=handwritten_only,
            goal={"id": GOAL_ID},
            state_path=tmp_path / "goal.md",
            goal_id=GOAL_ID,
            agent_id=AGENT_ID,
            completed_at="2026-08-01T08:00:00Z",
        )
        is None
    )


def test_snapshot_accepts_offset_and_z_suffix_timestamps_with_subsecond_precision(
    tmp_path: Path,
) -> None:
    state = (
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [x] Completed exactly at the stage boundary.\n"
        "  <!-- loopx:todo todo_id=todo_boundary status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T08:00:00Z"
        " completed_at=2026-08-01T08:00:00Z -->\n"
        "- [x] Offset-suffix outcome with microseconds.\n"
        "  <!-- loopx:todo todo_id=todo_offset status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:30:00.123456+00:00"
        " completed_at=2026-08-01T07:30:00.123456+00:00 -->\n"
        "- [x] Z-suffix outcome with milliseconds.\n"
        "  <!-- loopx:todo todo_id=todo_zulu status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:00:00.500Z"
        " completed_at=2026-08-01T07:00:00.500Z -->\n"
    )
    snapshot = build_project_progress_snapshot_from_state(
        state_text=state,
        goal={"id": GOAL_ID},
        state_path=tmp_path / "goal.md",
        goal_id=GOAL_ID,
        agent_id=AGENT_ID,
        completed_at="2026-08-01T08:00:00+00:00",
    )

    assert snapshot is not None
    assert [item["source_ref"] for item in snapshot["items"]] == [
        "todo:todo_boundary",
        "todo:todo_offset",
        "todo:todo_zulu",
    ]
    assert [item["completed_at"] for item in snapshot["items"]] == [
        "2026-08-01T08:00:00Z",
        "2026-08-01T07:30:00.123456+00:00",
        "2026-08-01T07:00:00.500Z",
    ]


def test_snapshot_skips_done_items_completed_after_the_stage_boundary(
    tmp_path: Path,
) -> None:
    state = (
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [x] Completion stamped after the stage.\n"
        "  <!-- loopx:todo todo_id=todo_future status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:00:00Z"
        " completed_at=2026-08-01T09:30:00Z -->\n"
        "- [x] Whole item updated after the stage.\n"
        "  <!-- loopx:todo todo_id=todo_late status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T09:00:00Z"
        " completed_at=2026-08-01T09:00:00Z -->\n"
        "- [x] Completion stamped inside the stage.\n"
        "  <!-- loopx:todo todo_id=todo_instage status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:10:00Z"
        " completed_at=2026-08-01T07:10:00Z -->\n"
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )

    assert snapshot is not None
    assert [item["source_ref"] for item in snapshot["items"]] == [
        "todo:todo_instage"
    ]
    assert snapshot["items"][0]["completed_at"] == "2026-08-01T07:10:00Z"


def test_snapshot_falls_back_to_updated_at_and_skips_unusable_timestamps(
    tmp_path: Path,
) -> None:
    state = (
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [x] Outcome relying on the updated_at fallback.\n"
        "  <!-- loopx:todo todo_id=todo_fallback status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:00:00Z -->\n"
        "- [x] Outcome with neither timestamp present.\n"
        "  <!-- loopx:todo todo_id=todo_missing status=done"
        f" task_class=advancement_task claimed_by={AGENT_ID} -->\n"
        "- [x] Outcome with both timestamps unreadable.\n"
        "  <!-- loopx:todo todo_id=todo_garbage status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=not-a-date"
        " completed_at=garbage -->\n"
        "- [x] Non-date updated_at with a readable completed_at.\n"
        "  <!-- loopx:todo todo_id=todo_numeric status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=12345"
        " completed_at=2026-08-01T07:20:00Z -->\n"
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )

    assert snapshot is not None
    assert [item["source_ref"] for item in snapshot["items"]] == [
        "todo:todo_fallback"
    ]
    assert snapshot["items"][0]["completed_at"] == "2026-08-01T07:00:00Z"


def test_snapshot_rejects_invalid_stage_completion_timestamps(
    tmp_path: Path,
) -> None:
    state = (
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [x] Valid outcome that must not be reached.\n"
        "  <!-- loopx:todo todo_id=todo_valid status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:00:00Z -->\n"
    )
    for invalid_stage in ("2026-08-01T08:00:00", "not-a-timestamp"):
        with pytest.raises(ValueError, match="stage completion timestamp"):
            build_project_progress_snapshot_from_state(
                state_text=state,
                goal={"id": GOAL_ID},
                state_path=tmp_path / "goal.md",
                goal_id=GOAL_ID,
                agent_id=AGENT_ID,
                completed_at=invalid_stage,
            )


def test_snapshot_keeps_valid_item_order_and_ids_when_invalid_items_are_skipped(
    tmp_path: Path,
) -> None:
    state = (
        "# Goal\n\n## User Todo\n\n## Agent Todo\n\n"
        "- [x] Valid outcome finished first.\n"
        "  <!-- loopx:todo todo_id=todo_valid_late status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:30:00Z -->\n"
        "- [x] Naive timestamp outcome sorted first.\n"
        "  <!-- loopx:todo todo_id=todo_naive_first status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:50:00 -->\n"
        "- [x] Valid completion stamped after the stage.\n"
        "  <!-- loopx:todo todo_id=todo_future_mid status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:20:00Z"
        " completed_at=2026-08-01T09:00:00Z -->\n"
        "- [x] Valid outcome finished last.\n"
        "  <!-- loopx:todo todo_id=todo_valid_early status=done"
        " task_class=advancement_task"
        f" claimed_by={AGENT_ID} updated_at=2026-08-01T07:10:00Z -->\n"
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )

    assert snapshot is not None
    assert [item["source_ref"] for item in snapshot["items"]] == [
        "todo:todo_valid_late",
        "todo:todo_valid_early",
    ]
    assert [item["item_id"] for item in snapshot["items"]] == [
        "completed_1",
        "completed_3",
    ]
    assert [item["value_rank"] for item in snapshot["items"]] == [10, 12]


def _todo(marker_text: str, attributes: str) -> str:
    return f"{marker_text}\n  <!-- loopx:todo {attributes} -->"


def _agent_todo_state(todo_lines: list[str]) -> str:
    return "# Goal\n\n## User Todo\n\n## Agent Todo\n\n" + "\n".join(todo_lines)


def _next_action_refs(snapshot: dict[str, object]) -> list[str]:
    return [
        str(item["source_ref"])
        for item in snapshot["items"]
        if isinstance(item, dict) and item.get("content_kind") == "next_action"
    ]


def _snapshot_call(tmp_path: Path, state: str) -> dict[str, object]:
    return {
        "state_text": state,
        "goal": {"id": GOAL_ID},
        "state_path": tmp_path / "goal.md",
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "completed_at": "2026-08-01T08:00:00Z",
    }


def test_snapshot_next_action_skips_resume_gated_open_todo(tmp_path: Path) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Resume the gated follow-up when network capacity returns.",
                "todo_id=todo_gated status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=gated_work "
                "resume_when=capacity_available:network",
            ),
            _todo(
                "- [ ] Continue the ordinary advancement work.",
                "todo_id=todo_plain status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=plain_work",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is not None
    next_actions = [
        item for item in snapshot["items"] if item.get("content_kind") == "next_action"
    ]
    assert [item["source_ref"] for item in next_actions] == ["todo:todo_plain"]


def test_snapshot_next_action_requires_an_actionable_open_todo(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Resume the gated follow-up when network capacity returns.",
                "todo_id=todo_gated status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=gated_work "
                "resume_when=capacity_available:network",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is None


def test_snapshot_next_action_selects_capacity_gated_todo_with_turn_capabilities(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Resume the follow-up once network capacity returns.",
                "todo_id=todo_capacity status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=gated_work "
                "resume_when=capacity_available:network",
            ),
        ]
    )
    satisfied = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state),
        available_capabilities=["network"],
    )
    assert satisfied is not None
    assert _next_action_refs(satisfied) == ["todo:todo_capacity"]

    without_evidence = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert without_evidence is None


def test_snapshot_next_action_selects_pr_merged_todo_with_rollout_evidence(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Resume the follow-up once the pull request merges.",
                "todo_id=todo_prwait status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=pr_followup_work "
                "resume_when=pr_merged:owner/repo#42",
            ),
        ]
    )
    satisfied = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state),
        rollout_events=[
            {
                "event_id": "merge-42",
                "event_kind": "pr_merged",
                "recorded_at": "2026-08-01T07:00:00Z",
                "code_refs": {"pr_ref": "owner/repo#42"},
            }
        ],
    )
    assert satisfied is not None
    assert _next_action_refs(satisfied) == ["todo:todo_prwait"]

    without_evidence = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert without_evidence is None


def test_snapshot_next_action_selects_a_resume_ready_gated_todo(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [x] Finish the prerequisite step.",
                "todo_id=todo_prereq status=done task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=prereq_work "
                "updated_at=2026-08-01T07:00:00Z",
            ),
            _todo(
                "- [ ] Resume the follow-up once the prerequisite is done.",
                "todo_id=todo_waiting status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=waiting_work "
                "resume_when=todo_done:todo_prereq",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is not None
    assert _next_action_refs(snapshot) == ["todo:todo_waiting"]
    assert [str(item.get("content_kind")) for item in snapshot["items"]] == [
        "outcome",
        "next_action",
    ]


def test_snapshot_next_action_skips_blocked_and_deferred_status_todos(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Continue while the dependency is blocked.",
                "todo_id=todo_blocked status=blocked task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=blocked_work",
            ),
            _todo(
                "- [ ] Continue the deferred cleanup later.",
                "todo_id=todo_deferred status=deferred task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=deferred_work",
            ),
            _todo(
                "- [ ] Keep advancing the ordinary work item.",
                "todo_id=todo_plain status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=plain_work",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is not None
    assert _next_action_refs(snapshot) == ["todo:todo_plain"]


def test_snapshot_next_action_ignores_done_markers_and_report_meta_kinds(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [x] Finish the reported stage work.",
                "todo_id=todo_reported status=done task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=reported_work "
                "updated_at=2026-08-01T07:00:00Z",
            ),
            _todo(
                "- [ ] Consume the periodic report intent.",
                "todo_id=todo_consume status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=consume_periodic_report_intent",
            ),
            _todo(
                "- [ ] Repair the consumed report intent.",
                "todo_id=todo_repair status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} "
                "action_kind=repair_periodic_report_intent_consumption",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is not None
    assert _next_action_refs(snapshot) == []
    assert [str(item.get("source_ref")) for item in snapshot["items"]] == [
        "todo:todo_reported"
    ]


def test_snapshot_next_action_prefers_the_reporting_agent_within_the_stage_window(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Continue the other agent's work.",
                "todo_id=todo_other status=open task_class=advancement_task "
                "claimed_by=other-agent action_kind=other_work",
            ),
            _todo(
                "- [ ] Continue the work updated after the stage.",
                "todo_id=todo_future status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=future_work "
                "updated_at=2026-08-01T09:00:00Z",
            ),
            _todo(
                "- [ ] Continue the work with a malformed timestamp.",
                "todo_id=todo_malformed status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=malformed_work "
                "updated_at=not-a-timestamp",
            ),
            _todo(
                "- [ ] Continue the work updated exactly at the stage.",
                "todo_id=todo_boundary status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=boundary_work "
                "updated_at=2026-08-01T08:00:00Z",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is not None
    assert _next_action_refs(snapshot) == ["todo:todo_boundary"]


def test_snapshot_next_action_prefers_the_first_actionable_todo(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Continue the first advancement work.",
                "todo_id=todo_first status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=first_work",
            ),
            _todo(
                "- [ ] Continue the second advancement work.",
                "todo_id=todo_second status=open task_class=advancement_task "
                f"claimed_by={AGENT_ID} action_kind=second_work",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is not None
    assert _next_action_refs(snapshot) == ["todo:todo_first"]


def test_snapshot_next_action_excludes_continuous_monitor_but_keeps_blocker_class(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [ ] Keep watching the monitored dependency.",
                "todo_id=todo_watch status=open task_class=continuous_monitor "
                f"claimed_by={AGENT_ID} action_kind=watch_work",
            ),
            _todo(
                "- [ ] Clear the blocker blocking the advancement lane.",
                f"todo_id=todo_blocker status=open task_class=blocker claimed_by={AGENT_ID} "
                "action_kind=blocker_work",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )
    assert snapshot is not None
    assert _next_action_refs(snapshot) == ["todo:todo_blocker"]


def _outcome_refs(snapshot: dict[str, object]) -> list[str]:
    return [
        str(item["source_ref"])
        for item in snapshot["items"]
        if isinstance(item, dict) and item.get("content_kind") == "outcome"
    ]


def test_snapshot_reports_peer_outcomes_and_keeps_unowned_rows_out(
    tmp_path: Path,
) -> None:
    """A Goal-level report must not silently drop another lane's progress."""

    state = _agent_todo_state(
        [
            _todo(
                "- [x] Landed the reporting lane's outcome.",
                "todo_id=todo_own status=done task_class=advancement_task "
                f"claimed_by={AGENT_ID} updated_at=2026-08-01T07:00:00Z "
                "completed_at=2026-08-01T07:00:00Z",
            ),
            _todo(
                "- [x] Landed a peer lane's outcome.",
                "todo_id=todo_peer status=done task_class=advancement_task "
                "claimed_by=peer-agent updated_at=2026-08-01T07:30:00Z "
                "completed_at=2026-08-01T07:30:00Z",
            ),
            _todo(
                "- [x] Landed an unclaimed outcome.",
                "todo_id=todo_unclaimed status=done task_class=advancement_task "
                "updated_at=2026-08-01T07:40:00Z completed_at=2026-08-01T07:40:00Z",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )

    assert snapshot is not None
    assert _outcome_refs(snapshot) == ["todo:todo_own", "todo:todo_peer"]


def test_snapshot_ranks_peer_outcomes_after_the_reporter_and_honors_the_stage_window(
    tmp_path: Path,
) -> None:
    state = _agent_todo_state(
        [
            _todo(
                "- [x] Older outcome by the reporting lane.",
                "todo_id=todo_own_older status=done task_class=advancement_task "
                f"claimed_by={AGENT_ID} updated_at=2026-08-01T06:00:00Z "
                "completed_at=2026-08-01T06:00:00Z",
            ),
            _todo(
                "- [x] Newer outcome by a peer lane.",
                "todo_id=todo_peer_newer status=done task_class=advancement_task "
                "claimed_by=peer-agent updated_at=2026-08-01T07:50:00Z "
                "completed_at=2026-08-01T07:50:00Z",
            ),
            _todo(
                "- [x] Peer outcome completed after the stage boundary.",
                "todo_id=todo_peer_future status=done task_class=advancement_task "
                "claimed_by=peer-agent updated_at=2026-08-01T09:00:00Z "
                "completed_at=2026-08-01T09:00:00Z",
            ),
            _todo(
                "- [x] Peer report-intent bookkeeping.",
                "todo_id=todo_peer_meta status=done task_class=advancement_task "
                "claimed_by=peer-agent action_kind=consume_periodic_report_intent "
                "updated_at=2026-08-01T07:10:00Z completed_at=2026-08-01T07:10:00Z",
            ),
        ]
    )
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, state)
    )

    assert snapshot is not None
    assert _outcome_refs(snapshot) == [
        "todo:todo_own_older",
        "todo:todo_peer_newer",
    ]


def test_snapshot_outcome_cap_keeps_every_reporting_agent_outcome(
    tmp_path: Path,
) -> None:
    own_rows = [
        _todo(
            f"- [x] Reporting outcome {index}.",
            f"todo_id=todo_own_{index} status=done task_class=advancement_task "
            f"claimed_by={AGENT_ID} updated_at=2026-08-01T06:0{index}:00Z "
            f"completed_at=2026-08-01T06:0{index}:00Z",
        )
        for index in range(5)
    ]
    peer_rows = [
        _todo(
            f"- [x] Peer outcome {index}.",
            f"todo_id=todo_peer_{index} status=done task_class=advancement_task "
            f"claimed_by=peer-agent updated_at=2026-08-01T07:0{index}:00Z "
            f"completed_at=2026-08-01T07:0{index}:00Z",
        )
        for index in range(4)
    ]
    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(tmp_path, _agent_todo_state(own_rows + peer_rows))
    )

    assert snapshot is not None
    refs = _outcome_refs(snapshot)
    assert len(refs) == 6
    assert refs[:5] == [f"todo:todo_own_{index}" for index in range(4, -1, -1)]
    assert refs[5:] == ["todo:todo_peer_3"]


def test_snapshot_next_action_prefers_the_peer_lane_over_an_unowned_row(
    tmp_path: Path,
) -> None:
    """A row no Agent claimed keeps frontier ownership, and reports nothing."""

    snapshot = build_project_progress_snapshot_from_state(
        **_snapshot_call(
            tmp_path,
            _agent_todo_state(
                [
                    _todo(
                        "- [ ] Continue the unclaimed work.",
                        "todo_id=todo_unclaimed status=open"
                        " task_class=advancement_task",
                    ),
                    _todo(
                        "- [ ] Continue the peer lane's work.",
                        "todo_id=todo_peer status=open"
                        " task_class=advancement_task claimed_by=peer-agent",
                    ),
                ]
            ),
        )
    )

    assert snapshot is not None
    assert _next_action_refs(snapshot) == ["todo:todo_peer"]


PEER_AGENT_ID = "peer-agent"


def _commit(
    runtime: Path,
    *,
    agent_id: str,
    facts: list[dict[str, object]],
    generation_id: str,
) -> dict[str, object]:
    candidate = build_periodic_report_publication_candidate(
        goal_id=GOAL_ID,
        agent_id=agent_id,
        generation_id=generation_id,
        trigger_receipt=_trigger(f"trigger_{generation_id}"),
        facts=facts,
        baseline=None,
    )
    return commit_periodic_report_publication_cursor(
        runtime_root=runtime,
        candidate=candidate,
        publication_id=f"goal-channel:{generation_id}",
        delivered_at="2026-08-01T09:00:00Z",
        covered_until="2026-08-01T08:00:00Z",
    )


def test_goal_cursors_drop_a_fact_another_lane_already_published(
    tmp_path: Path,
) -> None:
    """One Goal announces each fact once, whoever the reporting lane is."""

    runtime = tmp_path / "runtime"
    peer = _commit(
        runtime,
        agent_id=PEER_AGENT_ID,
        generation_id="generation_peer",
        facts=[_item("todo:a", title="A completed", summary="A is done.")],
    )
    selected = select_incremental_project_progress(
        _snapshot(
            [
                _item("todo:a", title="A completed", summary="A is done."),
                _item("todo:z", title="Z completed", summary="Z is new."),
            ]
        ),
        cursor=None,
        goal_cursors=[peer],
    )

    assert selected is not None
    assert [item["source_ref"] for item in selected["items"]] == ["todo:z"]
    assert selected["items"][0]["change_kind"] == "added"


def test_goal_cursors_keep_a_fact_a_peer_published_with_different_content(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    peer = _commit(
        runtime,
        agent_id=PEER_AGENT_ID,
        generation_id="generation_peer_text",
        facts=[_item("todo:a", title="A completed", summary="First wording.")],
    )
    selected = select_incremental_project_progress(
        _snapshot([_item("todo:a", title="A completed", summary="Second wording.")]),
        cursor=None,
        goal_cursors=[peer],
    )

    assert selected is not None
    assert [item["source_ref"] for item in selected["items"]] == ["todo:a"]


def test_goal_cursors_do_not_hide_this_lanes_own_changed_fact(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    own = _commit(
        runtime,
        agent_id=AGENT_ID,
        generation_id="generation_own",
        facts=[_item("todo:a", title="A completed", summary="A is done.")],
    )
    peer = _commit(
        runtime,
        agent_id=PEER_AGENT_ID,
        generation_id="generation_peer_again",
        facts=[_item("todo:a", title="A completed", summary="A is done.")],
    )
    selected = select_incremental_project_progress(
        _snapshot([_item("todo:a", title="A completed", summary="A reopened.")]),
        cursor=own,
        goal_cursors=[own, peer],
    )

    assert selected is not None
    assert selected["items"][0]["change_kind"] == "changed"
    assert selected["items"][0]["previous_fact_fingerprint"] == own["fact_states"][0][
        "fact_fingerprint"
    ]


def test_goal_cursor_reader_returns_every_lane_and_fails_closed(
    tmp_path: Path,
) -> None:
    runtime = tmp_path / "runtime"
    assert (
        read_periodic_report_goal_publication_cursors(
            runtime_root=runtime, goal_id=GOAL_ID
        )
        == []
    )

    _commit(
        runtime,
        agent_id=AGENT_ID,
        generation_id="generation_reader_own",
        facts=[_item("todo:a", title="A completed", summary="A is done.")],
    )
    _commit(
        runtime,
        agent_id=PEER_AGENT_ID,
        generation_id="generation_reader_peer",
        facts=[_item("todo:b", title="B completed", summary="B is done.")],
    )
    cursors = read_periodic_report_goal_publication_cursors(
        runtime_root=runtime, goal_id=GOAL_ID
    )
    assert [cursor["agent_id"] for cursor in cursors] == [AGENT_ID, PEER_AGENT_ID]

    sibling = runtime / "goals" / GOAL_ID / "periodic_reports" / "publication-cursors"
    corrupt = sibling / "third-agent.json"
    corrupt.write_text('{"schema_version": "unexpected"}', encoding="utf-8")
    with pytest.raises(ValueError, match="publication cursor must use"):
        read_periodic_report_goal_publication_cursors(
            runtime_root=runtime, goal_id=GOAL_ID
        )
