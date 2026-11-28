from __future__ import annotations

import json

import pytest

from loopx.capabilities.periodic_report import build_periodic_report_run
from loopx.capabilities.periodic_report.runtime_producer import (
    build_periodic_report_runtime_trigger_decision,
)
from loopx.cli import main
from loopx.rollout_event_log import append_rollout_event, build_rollout_event


def _request(*, threshold: int = 2, promote_replan: bool = True) -> dict[str, object]:
    return {
        "schema_version": "periodic_report_runtime_trigger_request_v0",
        "evaluated_at": "2026-08-24T12:00:00Z",
        "goal_id": "long-research",
        "profile": {
            "profile_id": "weekly_progress",
            "profile_version": "v1",
        },
        "trigger_policy": {
            "enabled_kinds": ["bounded_segment_milestone"],
            "minimum_interval_seconds": 0,
            "aggregation": {
                "window_seconds": 604800,
                "todo_completed_threshold": threshold,
                "promote_replan": promote_replan,
                "stage_completion_required": True,
            },
        },
        "segment": {
            "segment_ref": "week-2026-w34",
            "start_at": "2026-08-18T12:00:00Z",
            "end_at": "2026-08-24T12:00:00Z",
            "remaining_todo_count": 12,
        },
    }


def _event(
    kind: str,
    *,
    event_at: str,
    todo_id: str | None = None,
    replan: bool = False,
    stage_transition: str | None = None,
    stage_identity: str = "stage-closed-vision-frontier",
    stage_acceptance: str = "validated",
    outcome_checkpoint_satisfied: bool = True,
    durable_writeback_required: bool = True,
    event_status: str | None = None,
) -> dict[str, object]:
    details: dict[str, object] = {"autonomous_replan_recorded": replan}
    if stage_transition:
        details.update(
            {
                "stage_completion_schema": (
                    "periodic_report_stage_completion_receipt_v0"
                ),
                "stage_identity": stage_identity,
                "closed_vision_revision": "2026-08-21T08:59:00Z",
                "frontier_identity": "frontier-next-analysis",
                "stage_transition": stage_transition,
                "stage_acceptance": stage_acceptance,
                "stage_completed_at": "2026-08-21T09:00:00Z",
                "stage_outcome_checkpoint_satisfied": (
                    outcome_checkpoint_satisfied
                ),
                "stage_durable_writeback_required": durable_writeback_required,
            }
        )
    return build_rollout_event(
        goal_id="long-research",
        event_kind=kind,
        todo_id=todo_id,
        recorded_at=event_at,
        status=(
            event_status
            or (
                "completed"
                if kind == "todo_complete"
                else "normal_run"
                if kind == "quota_should_run"
                else "appended"
            )
        ),
        details=details,
    )


def _run_request(trigger: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "periodic_report_run_request_v0",
        "generated_at": "2026-08-24T12:05:00Z",
        "period_window": {
            "start_at": "2026-08-18T12:00:00Z",
            "end_at": "2026-08-24T12:00:00Z",
        },
        "profile": {
            "profile_id": "weekly_progress",
            "profile_version": "v1",
        },
        "trigger_receipt": trigger,
        "source_snapshots": [
            {
                "source_id": "project_progress",
                "source_kind": "validated_project_progress",
                "status": "complete",
                "observed_at": "2026-08-24T12:00:00Z",
                "snapshot_digest": "sha256:progress",
            }
        ],
        "artifact_receipt": {
            "artifact_id": "weekly_progress",
            "renderer_id": "markdown_v0",
            "renderer_kind": "markdown",
            "status": "pending",
        },
        "sink_receipts": [
            {
                "sink_id": "archive",
                "sink_kind": "resource_store",
                "sink_role": "archive",
                "status": "pending",
            },
            {
                "sink_id": "delivery",
                "sink_kind": "message_channel",
                "sink_role": "delivery",
                "status": "pending",
            },
        ],
    }


def test_authoritative_stage_completion_promotes_and_composes_run() -> None:
    events = [
        _event("todo_complete", event_at="2026-08-20T09:00:00Z", todo_id="todo-a"),
        _event(
            "refresh_state",
            event_at="2026-08-21T09:00:00Z",
            stage_transition="successor_frontier_settled",
        ),
        _event("todo_complete", event_at="2026-08-22T09:00:00Z", todo_id="todo-c"),
    ]

    decision = build_periodic_report_runtime_trigger_decision(
        _request(), rollout_events=events
    )
    run = build_periodic_report_run(_run_request(decision))

    assert decision["eligible"] is True
    assert decision["selected_trigger_kind"] == "bounded_segment_milestone"
    assert (
        decision["producer_receipt"]["reason"]
        == "authoritative_stage_completion_observed"
    )
    assert len(decision["producer_receipt"]["contributing_event_ids"]) == 1
    assert decision["producer_receipt"]["selected_stage_completion"] == {
        "stage_identity": "stage-closed-vision-frontier",
        "closed_vision_revision": "2026-08-21T08:59:00Z",
        "frontier_identity": "frontier-next-analysis",
        "transition": "successor_frontier_settled",
        "completed_at": "2026-08-21T09:00:00Z",
        "completion_receipt_ref": "stage:stage-closed-vision-frontier",
    }
    assert run["trigger_receipt"]["trigger_policy"]["aggregation"] == {
        "window_seconds": 604800,
        "promote_replan": True,
        "stage_completion_required": True,
        "todo_completed_threshold": 2,
    }


def test_durable_replan_and_ordinary_todos_do_not_promote() -> None:
    duplicate = _event(
        "todo_complete", event_at="2026-08-20T09:00:00Z", todo_id="todo-a"
    )
    events = [
        duplicate,
        dict(duplicate),
        _event(
            "refresh_state",
            event_at="2026-08-21T10:00:00Z",
            replan=True,
        ),
    ]

    decision = build_periodic_report_runtime_trigger_decision(
        _request(threshold=4), rollout_events=events
    )

    assert decision["eligible"] is False
    assert decision["producer_receipt"]["transition"] is None
    assert decision["producer_receipt"]["todo_completed_count"] == 1
    assert decision["producer_receipt"]["replan_event_count"] == 1
    assert decision["producer_receipt"]["reason"] == "authoritative_stage_completion_missing"


def test_quota_projection_replays_deduplicate_by_stage_identity() -> None:
    first = _event(
        "quota_should_run",
        event_at="2026-08-21T10:00:00Z",
        stage_transition="successor_frontier_settled",
    )
    replay = _event(
        "quota_should_run",
        event_at="2026-08-22T10:00:00Z",
        stage_transition="successor_frontier_settled",
    )

    decision = build_periodic_report_runtime_trigger_decision(
        _request(threshold=64), rollout_events=[first, replay]
    )

    assert decision["eligible"] is True
    assert decision["producer_receipt"]["stage_completion_count"] == 1
    assert decision["producer_receipt"]["contributing_event_ids"] == [
        first["event_id"]
    ]
    assert decision["producer_receipt"]["selected_stage_completion"][
        "completion_receipt_ref"
    ] == "stage:stage-closed-vision-frontier"


@pytest.mark.parametrize(
    "stage_overrides",
    [
        {"stage_acceptance": "pending"},
        {"outcome_checkpoint_satisfied": False},
        {"durable_writeback_required": False},
        {"event_status": "pending"},
        {"stage_transition": "generic_replan"},
    ],
)
def test_incomplete_stage_contract_fails_closed(
    stage_overrides: dict[str, object],
) -> None:
    event_options: dict[str, object] = {
        "stage_transition": "successor_frontier_settled",
        **stage_overrides,
    }
    event = _event(
        "refresh_state",
        event_at="2026-08-20T09:00:00Z",
        **event_options,
    )

    decision = build_periodic_report_runtime_trigger_decision(
        _request(threshold=1), rollout_events=[event]
    )

    assert decision["eligible"] is False
    assert decision["producer_receipt"]["stage_completion_count"] == 0
    assert decision["producer_receipt"]["reason"] == (
        "authoritative_stage_completion_missing"
    )


def test_ordinary_todo_threshold_never_creates_stage_completion() -> None:
    decision = build_periodic_report_runtime_trigger_decision(
        _request(threshold=3, promote_replan=False),
        rollout_events=[
            _event(
                "todo_complete",
                event_at="2026-08-20T09:00:00Z",
                todo_id="todo-a",
            )
        ],
    )

    assert decision["eligible"] is False
    assert decision["producer_receipt"]["status"] == "not_promoted"
    assert (
        decision["producer_receipt"]["reason"]
        == "authoritative_stage_completion_missing"
    )
    assert decision["suppressed_triggers"][0]["trigger_kind"] == "state_refreshed"


def test_runtime_producer_rejects_unproven_public_boundary() -> None:
    event = _event(
        "todo_complete", event_at="2026-08-20T09:00:00Z", todo_id="todo-a"
    )
    event["boundary"]["raw_logs_recorded"] = True

    with pytest.raises(ValueError, match="public-safe event"):
        build_periodic_report_runtime_trigger_decision(
            _request(threshold=1), rollout_events=[event]
        )


def test_runtime_producer_cli_reads_durable_rollout_log(tmp_path, capsys) -> None:
    request_path = tmp_path / "request.json"
    event_log = tmp_path / "rollout-event-log.jsonl"
    request_path.write_text(json.dumps(_request(threshold=1)), encoding="utf-8")
    append_rollout_event(
        event_log,
        _event(
            "refresh_state",
            event_at="2026-08-20T09:00:00Z",
            stage_transition="goal_terminal",
        ),
    )

    assert main(
        [
            "--format",
            "json",
            "periodic-report",
            "evaluate-runtime-trigger",
            "--request-json",
            str(request_path),
            "--rollout-events-jsonl",
            str(event_log),
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["eligible"] is True
    assert payload["producer_receipt"]["status"] == "promoted"


@pytest.mark.parametrize("reverse_log_order", [False, True])
def test_runtime_producer_cli_uses_chronological_stage_boundary(
    tmp_path, capsys, reverse_log_order: bool
) -> None:
    request = _request()
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    event_log = tmp_path / "rollout-event-log.jsonl"
    first_stage = _event(
        "refresh_state",
        event_at="2026-08-21T09:00:00Z",
        stage_transition="successor_frontier_settled",
    )
    same_instant = _event(
        "todo_complete", event_at="2026-08-21T10:00:00+01:00", todo_id="on-boundary"
    )
    events = [
        first_stage,
        same_instant,
        _event("todo_complete", event_at="2026-08-21T09:00:00.25Z", todo_id="later"),
        _event(
            "refresh_state",
            event_at="2026-08-21T09:00:00.5Z",
            stage_transition="goal_terminal",
            stage_identity="later-stage",
        ),
    ]
    for event in reversed(events) if reverse_log_order else events:
        append_rollout_event(event_log, event)

    assert main(
        [
            "--format",
            "json",
            "periodic-report",
            "evaluate-runtime-trigger",
            "--request-json",
            str(request_path),
            "--rollout-events-jsonl",
            str(event_log),
        ]
    ) == 0
    decision = json.loads(capsys.readouterr().out)
    receipt = decision["producer_receipt"]
    assert receipt["contributing_event_ids"] == [first_stage["event_id"]]
    assert receipt["stage_completion_count"] == 2
    assert receipt["todo_completed_count"] == 2
    at_boundary = build_periodic_report_runtime_trigger_decision(
        request, rollout_events=[first_stage, same_instant]
    )
    without_completions = build_periodic_report_runtime_trigger_decision(
        request, rollout_events=[first_stage]
    )
    # The decision ID includes the milestone's delivered_count.
    assert decision["decision_id"] == at_boundary["decision_id"]
    assert decision["decision_id"] != without_completions["decision_id"]


def test_runtime_producer_cli_streams_large_history_before_bounded_window(
    tmp_path, capsys
) -> None:
    request_path = tmp_path / "request.json"
    event_log = tmp_path / "rollout-event-log.jsonl"
    request_path.write_text(json.dumps(_request(threshold=1)), encoding="utf-8")
    old_event = _event(
        "quota_should_run",
        event_at="2026-08-17T09:00:00Z",
    )
    current_event = _event(
        "refresh_state",
        event_at="2026-08-20T09:00:00Z",
        stage_transition="successor_frontier_settled",
    )
    rows = [
        json.dumps({**old_event, "event_id": f"old-{index}"})
        for index in range(4096)
    ]
    rows.append(json.dumps(current_event))
    event_log.write_text("\n".join(rows) + "\n", encoding="utf-8")

    assert main(
        [
            "--format",
            "json",
            "periodic-report",
            "evaluate-runtime-trigger",
            "--request-json",
            str(request_path),
            "--rollout-events-jsonl",
            str(event_log),
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["eligible"] is True
    assert payload["producer_receipt"]["todo_completed_count"] == 0


@pytest.mark.parametrize(
    ("start_at", "allowed"),
    [
        pytest.param("2026-08-17T12:00:00.000001Z", True, id="one-microsecond-inside"),
        pytest.param("2026-08-17T20:00:00+08:00", True, id="exactly-at-limit"),
        pytest.param("2026-08-17T11:59:59.999999Z", False, id="one-microsecond-over"),
        pytest.param("2026-08-17T11:59:59Z", False, id="one-second-over"),
    ],
)
def test_runtime_producer_cli_enforces_exact_window_limit(
    tmp_path, capsys, start_at: str, allowed: bool
) -> None:
    request = _request()
    segment = request["segment"]
    assert isinstance(segment, dict)
    segment["start_at"] = start_at
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")
    event_log = tmp_path / "rollout-event-log.jsonl"
    append_rollout_event(
        event_log,
        _event(
            "refresh_state",
            event_at="2026-08-21T09:00:00Z",
            stage_transition="goal_terminal",
        ),
    )

    code = main([
        "--format", "json", "periodic-report", "evaluate-runtime-trigger",
        "--request-json", str(request_path), "--rollout-events-jsonl", str(event_log),
    ])
    decision = json.loads(capsys.readouterr().out)
    assert code == (0 if allowed else 1)
    assert decision["ok"] is allowed
    if allowed:
        assert decision["eligible"] is True
        assert decision["producer_receipt"]["status"] == "promoted"
    else:
        assert decision["error"] == (
            "segment window exceeds trigger_policy.aggregation.window_seconds"
        )


def test_runtime_producer_rejects_relevant_window_over_capacity() -> None:
    event = _event(
        "todo_complete",
        event_at="2026-08-20T09:00:00Z",
        todo_id="todo-template",
    )
    events = (
        {
            **event,
            "event_id": f"current-{index}",
            "todo_id": f"todo-{index}",
        }
        for index in range(4097)
    )

    with pytest.raises(ValueError, match="4096 relevant window items"):
        build_periodic_report_runtime_trigger_decision(
            _request(threshold=1),
            rollout_events=events,
        )


def test_runtime_producer_cli_rejects_malformed_durable_row(tmp_path, capsys) -> None:
    request_path = tmp_path / "request.json"
    event_log = tmp_path / "rollout-event-log.jsonl"
    request_path.write_text(json.dumps(_request(threshold=1)), encoding="utf-8")
    event_log.write_text("{not-json}\n", encoding="utf-8")

    assert main(
        [
            "--format",
            "json",
            "periodic-report",
            "evaluate-runtime-trigger",
            "--request-json",
            str(request_path),
            "--rollout-events-jsonl",
            str(event_log),
        ]
    ) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["error"] == "rollout event log line 1 must contain valid JSON"
