from __future__ import annotations

import copy
import json

import pytest

from loopx.capabilities.periodic_report import (
    build_periodic_report_run,
    build_periodic_report_trigger_decision,
)
from loopx.cli import main


def _candidate(
    trigger_kind: str,
    *,
    source_ref: str,
    evidence_digest: str,
    facts: dict[str, object],
    observed_at: str = "2026-07-20T00:30:00Z",
) -> dict[str, object]:
    return {
        "trigger_kind": trigger_kind,
        "observed_at": observed_at,
        "source_ref": source_ref,
        "evidence_digest": evidence_digest,
        "facts": facts,
    }


def _trigger_request(*candidates: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "periodic_report_trigger_request_v0",
        "evaluated_at": "2026-07-20T01:00:00Z",
        "profile": {
            "profile_id": "project_digest",
            "profile_version": "v1",
            "profile_ref": "profile:project-digest/v1",
        },
        "trigger_policy": {
            "enabled_kinds": [
                "bounded_segment_milestone",
                "cadence_due",
                "manual",
                "material_blocker",
                "material_decision",
                "material_recovery",
                "primary_goal_outcome",
                "vision_closed",
            ],
            "minimum_interval_seconds": 21600,
        },
        "candidates": list(candidates),
    }


def _run_request(trigger_receipt: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": "periodic_report_run_request_v0",
        "generated_at": "2026-07-20T01:05:00Z",
        "period_window": {
            "start_at": "2026-07-13T00:00:00Z",
            "end_at": "2026-07-20T00:00:00Z",
        },
        "profile": {
            "profile_id": "project_digest",
            "profile_version": "v1",
            "profile_ref": "profile:project-digest/v1",
        },
        "trigger_receipt": trigger_receipt,
        "source_snapshots": [
            {
                "source_id": "goal_activity",
                "source_kind": "project_activity",
                "status": "complete",
                "observed_at": "2026-07-20T01:00:00Z",
                "snapshot_digest": "sha256:activity",
            }
        ],
        "artifact_receipt": {
            "artifact_id": "progress_digest",
            "renderer_id": "structured_markdown",
            "renderer_kind": "markdown",
            "status": "rendered",
            "artifact_digest": "sha256:artifact",
            "artifact_ref": "artifact:progress/2026-07-20",
        },
        "sink_receipts": [
            {
                "sink_id": "project_archive",
                "sink_kind": "resource_store",
                "sink_role": "archive",
                "status": "pending",
            },
            {
                "sink_id": "team_delivery",
                "sink_kind": "message_channel",
                "sink_role": "delivery",
                "status": "pending",
            },
        ],
        "retry_policy": {"attempt": 1, "max_attempts": 3},
    }


def test_cadence_due_is_selected_while_control_plane_noise_is_suppressed() -> None:
    request = _trigger_request(
        _candidate(
            "cadence_due",
            source_ref="scheduler:weekly-window/2026-w29",
            evidence_digest="sha256:weekly-due",
            facts={"due": True},
        ),
        _candidate(
            "surface_only",
            source_ref="run:surface-only",
            evidence_digest="sha256:surface-only",
            facts={},
        ),
        _candidate(
            "todo_completed",
            source_ref="todo:minor-cleanup",
            evidence_digest="sha256:minor-cleanup",
            facts={},
        ),
    )
    first = build_periodic_report_trigger_decision(request)
    reordered = copy.deepcopy(request)
    reordered["candidates"].reverse()  # type: ignore[union-attr]
    second = build_periodic_report_trigger_decision(reordered)

    assert first == second
    assert first["eligible"] is True
    assert first["selected_trigger_kind"] == "cadence_due"
    assert first["report_kind"] == "cadence_digest"
    assert {item["reason"] for item in first["suppressed_triggers"]} == {
        "non_material_control_plane_event"
    }


def test_future_fractional_trigger_is_rejected_chronologically() -> None:
    request = _trigger_request(
        _candidate(
            "manual",
            source_ref="manual:future",
            evidence_digest="sha256:future",
            facts={"authorized": True},
            observed_at="2026-07-20T01:00:00.100000Z",
        )
    )

    with pytest.raises(ValueError, match="must not be in the future"):
        build_periodic_report_trigger_decision(request)


def test_equal_priority_triggers_are_sorted_by_parsed_timestamp() -> None:
    early = _candidate(
        "manual",
        source_ref="manual:early",
        evidence_digest="sha256:early",
        facts={"authorized": True},
        observed_at="2026-07-20T00:00:00Z",
    )
    later = _candidate(
        "manual",
        source_ref="manual:later",
        evidence_digest="sha256:later",
        facts={"authorized": True},
        observed_at="2026-07-20T00:00:00.100000Z",
    )
    early_only = build_periodic_report_trigger_decision(_trigger_request(early))
    combined = build_periodic_report_trigger_decision(_trigger_request(later, early))

    assert combined["selected_trigger_id"] == early_only["selected_trigger_id"]


def test_decision_identity_binds_normalized_candidate_facts() -> None:
    authorized = _candidate(
        "manual",
        source_ref="manual:review",
        evidence_digest="sha256:review",
        facts={"authorized": True},
    )
    unauthorized = {**authorized, "facts": {"authorized": False}}

    accepted = build_periodic_report_trigger_decision(_trigger_request(authorized))
    rejected = build_periodic_report_trigger_decision(_trigger_request(unauthorized))

    assert accepted["eligible"] is True
    assert rejected["eligible"] is False
    assert accepted["decision_id"] != rejected["decision_id"]


def test_fractional_trigger_policy_integer_is_rejected() -> None:
    request = _trigger_request(
        _candidate(
            "cadence_due",
            source_ref="scheduler:weekly-window/2026-w29",
            evidence_digest="sha256:weekly-due",
            facts={"due": True},
        )
    )
    request["trigger_policy"]["minimum_interval_seconds"] = 0.9  # type: ignore[index]

    with pytest.raises(ValueError, match="must be an integer"):
        build_periodic_report_trigger_decision(request)


def test_vision_requires_validated_closure_and_continuation() -> None:
    incomplete = build_periodic_report_trigger_decision(
        _trigger_request(
            _candidate(
                "vision_closed",
                source_ref="vision:quarterly-plan",
                evidence_digest="sha256:checkpoint",
                facts={
                    "transition": "vision_checkpoint",
                    "acceptance": "validated",
                    "continuation": "successor_established",
                },
            )
        )
    )
    assert incomplete["eligible"] is False
    assert incomplete["reason"] == "no_material_trigger"
    assert incomplete["suppressed_triggers"][0]["reason"] == "vision_not_closed"

    closed = _trigger_request(
        _candidate(
            "vision_closed",
            source_ref="vision:quarterly-plan",
            evidence_digest="sha256:closed",
            facts={
                "transition": "vision_closed",
                "acceptance": "validated",
                "continuation": "successor_established",
            },
        )
    )
    payload = build_periodic_report_trigger_decision(closed)
    assert payload["eligible"] is True
    assert payload["report_kind"] == "milestone_update"


def _completed_stage_facts(**overrides: object) -> dict[str, object]:
    facts: dict[str, object] = {
        "segment_ref": "week-2026-w29",
        "transition": "segment_completed",
        "delivered_count": 4,
        "remaining_todo_count": 12,
        "durable_writeback": True,
        "acceptance": "validated",
        "completed_at": "2026-07-20T00:30:00Z",
        "completion_receipt_ref": "event-analysis-complete",
        "stage_identity": "stage-closed-vision-frontier",
        "closed_vision_revision": "2026-07-20T00:00:00Z",
        "frontier_identity": "frontier-next-analysis",
        "stage_transition": "successor_frontier_settled",
        "outcome_checkpoint_satisfied": True,
        "status": "completed",
    }
    facts.update(overrides)
    return facts


def test_authoritative_stage_completion_with_remaining_todos_triggers() -> None:
    request = _trigger_request(
        _candidate(
            "cadence_due",
            source_ref="scheduler:weekly-window/2026-w29",
            evidence_digest="sha256:weekly-due",
            facts={"due": True},
        ),
        _candidate(
            "bounded_segment_milestone",
            source_ref="segment:2026-w29",
            evidence_digest="sha256:segment-completed",
            facts=_completed_stage_facts(),
        ),
    )

    payload = build_periodic_report_trigger_decision(request)
    run = build_periodic_report_run(_run_request(payload))

    assert payload["eligible"] is True
    assert payload["selected_trigger_kind"] == "bounded_segment_milestone"
    assert payload["report_kind"] == "milestone_update"
    assert len(payload["coalesced_trigger_ids"]) == 2
    assert run["trigger_receipt"]["report_kind"] == "milestone_update"


@pytest.mark.parametrize(
    ("facts", "reason"),
    [
        (
            {"transition": "vision_checkpoint", "durable_writeback": True},
            "segment_checkpoint_only",
        ),
        (
            {"transition": "replan_entered", "durable_writeback": False},
            "segment_writeback_pending",
        ),
        (
            {"transition": "replan_entered", "durable_writeback": True},
            "stage_completion_not_proven",
        ),
        (
            {"acceptance": "pending"},
            "stage_completion_not_proven",
        ),
    ],
)
def test_bounded_segment_requires_authoritative_stage_completion(
    facts: dict[str, object], reason: str
) -> None:
    candidate_facts = _completed_stage_facts(**facts)
    payload = build_periodic_report_trigger_decision(
        _trigger_request(
            _candidate(
                "bounded_segment_milestone",
                source_ref="segment:2026-w29",
                evidence_digest="sha256:stage-check",
                facts=candidate_facts,
            )
        )
    )

    assert payload["eligible"] is False
    assert payload["suppressed_triggers"][0]["reason"] == reason


@pytest.mark.parametrize(
    "facts, error",
    [
        (
            {
                "transition": "segment_completed",
                "durable_writeback": True,
            },
            "segment_ref is required",
        ),
        (
            {
                "segment_ref": "week-2026-w29",
                "transition": "unknown_transition",
                "durable_writeback": True,
            },
            "transition is invalid",
        ),
        (
            {
                "segment_ref": "week-2026-w29",
                "transition": "segment_completed",
                "remaining_todo_count": -1,
                "durable_writeback": True,
            },
            "remaining_todo_count must be between",
        ),
    ],
)
def test_bounded_segment_facts_are_strictly_validated(
    facts: dict[str, object], error: str
) -> None:
    with pytest.raises(ValueError, match=error):
        build_periodic_report_trigger_decision(
            _trigger_request(
                _candidate(
                    "bounded_segment_milestone",
                    source_ref="segment:2026-w29",
                    evidence_digest="sha256:invalid-segment",
                    facts=facts,
                )
            )
        )


def test_urgent_blocker_bypasses_cooldown_and_coalesces_material_decision() -> None:
    request = _trigger_request(
        _candidate(
            "material_decision",
            source_ref="decision:route-change",
            evidence_digest="sha256:route-change",
            facts={
                "decision_outcome": "approve",
                "route_changed": True,
                "durable_writeback": True,
            },
        ),
        _candidate(
            "material_blocker",
            source_ref="blocker:primary-path",
            evidence_digest="sha256:blocker-opened",
            facts={
                "severity": "p0",
                "transition": "opened",
                "blocks_primary_path": True,
            },
        ),
    )
    request["last_report"] = {
        "delivered_at": "2026-07-20T00:00:00Z",
        "covered_trigger_ids": [],
    }
    payload = build_periodic_report_trigger_decision(request)

    assert payload["eligible"] is True
    assert payload["selected_trigger_kind"] == "material_blocker"
    assert payload["report_kind"] == "exception_update"
    assert len(payload["coalesced_trigger_ids"]) == 2
    assert payload["cooldown"]["active"] is True
    assert payload["cooldown"]["bypassed"] is True


def test_nonurgent_trigger_waits_for_cooldown_and_covered_events_dedupe() -> None:
    request = _trigger_request(
        _candidate(
            "material_recovery",
            source_ref="blocker:primary-path",
            evidence_digest="sha256:blocker-resolved",
            facts={
                "transition": "resolved",
                "primary_path_reopened": True,
                "validated": True,
            },
        )
    )
    initial = build_periodic_report_trigger_decision(request)
    trigger_id = initial["selected_trigger_id"]

    cooldown = copy.deepcopy(request)
    cooldown["last_report"] = {
        "delivered_at": "2026-07-20T00:00:00Z",
        "covered_trigger_ids": [],
    }
    delayed = build_periodic_report_trigger_decision(cooldown)
    assert delayed["eligible"] is False
    assert delayed["reason"] == "cooldown_active"
    assert delayed["cooldown"]["next_eligible_at"] == "2026-07-20T06:00:00Z"

    covered = copy.deepcopy(request)
    covered["last_report"] = {
        "delivered_at": "2026-07-19T00:00:00Z",
        "covered_trigger_ids": [trigger_id],
    }
    duplicate = build_periodic_report_trigger_decision(covered)
    assert duplicate["eligible"] is False
    assert duplicate["suppressed_triggers"][0]["reason"] == "already_covered"


@pytest.mark.parametrize(
    ("delivered_at", "cutoff"),
    [
        pytest.param(
            "2500-07-20T00:00:00.000001Z", "2500-07-20T00:00:01.000001Z", id="rounded-later"
        ),
        pytest.param(
            "2500-07-20T00:00:00.000012Z", "2500-07-20T00:00:01.000012Z", id="rounded-earlier"
        ),
        pytest.param(
            "9999-12-31T23:59:58.999999Z", "9999-12-31T23:59:59.999999Z", id="maximum-cutoff"
        ),
    ],
)
@pytest.mark.parametrize("at_cutoff", [False, True], ids=["before", "at"])
def test_trigger_cli_cooldown_advertises_exact_cutoff(
    tmp_path, capsys, delivered_at: str, cutoff: str, at_cutoff: bool
) -> None:
    from datetime import datetime, timedelta

    request = _trigger_request(_candidate(
        "cadence_due", source_ref="scheduler:example-window", evidence_digest="sha256:calendar",
        facts={"due": True}, observed_at=delivered_at,
    ))
    policy = request["trigger_policy"]
    assert isinstance(policy, dict)
    policy["minimum_interval_seconds"] = 1
    request["last_report"] = {"delivered_at": delivered_at, "covered_trigger_ids": []}
    cutoff_time = datetime.fromisoformat(cutoff)
    request["evaluated_at"] = cutoff if at_cutoff else (
        cutoff_time - timedelta(microseconds=1)
    ).isoformat().replace("+00:00", "Z")
    request_path = tmp_path / "request.json"
    request_path.write_text(json.dumps(request), encoding="utf-8")

    code = main([
        "--format", "json", "periodic-report", "evaluate-trigger",
        "--request-json", str(request_path),
    ])
    decision = json.loads(capsys.readouterr().out)
    assert code == 0, decision
    assert decision["eligible"] is at_cutoff
    assert decision["cooldown"]["active"] is (not at_cutoff)
    assert decision["cooldown"]["next_eligible_at"] == (None if at_cutoff else cutoff)


def test_trigger_receipt_participates_in_run_identity_and_cli(tmp_path, capsys) -> None:
    cadence = build_periodic_report_trigger_decision(
        _trigger_request(
            _candidate(
                "cadence_due",
                source_ref="scheduler:weekly-window/2026-w29",
                evidence_digest="sha256:weekly-due",
                facts={"due": True},
            )
        )
    )
    milestone = build_periodic_report_trigger_decision(
        _trigger_request(
            _candidate(
                "primary_goal_outcome",
                source_ref="run:validated-delivery",
                evidence_digest="sha256:primary-outcome",
                facts={
                    "delivery_outcome": "primary_goal_outcome",
                    "validated": True,
                    "durable_writeback": True,
                },
            )
        )
    )
    cadence_run = build_periodic_report_run(_run_request(cadence))
    milestone_run = build_periodic_report_run(_run_request(milestone))

    assert cadence_run["run_id"] != milestone_run["run_id"]
    assert cadence_run["trigger_receipt"]["report_kind"] == "cadence_digest"
    assert milestone_run["trigger_receipt"]["report_kind"] == "milestone_update"

    unfinished_cadence = _run_request(cadence)
    unfinished_cadence["generated_at"] = "2026-07-19T23:00:00Z"
    with pytest.raises(ValueError, match="cadence_digest.*end_at"):
        build_periodic_report_run(unfinished_cadence)

    # A material event and a due calendar window share one publication decision;
    # the urgent event wins the presentation kind without creating a duplicate
    # weekly report.
    coalesced = _trigger_request(
        _candidate(
            "cadence_due",
            source_ref="scheduler:weekly-window/2026-w29",
            evidence_digest="sha256:weekly-due",
            facts={"due": True},
        ),
        _candidate(
            "material_blocker",
            source_ref="blocker:primary-path",
            evidence_digest="sha256:blocker-opened",
            facts={
                "severity": "p0",
                "transition": "opened",
                "blocks_primary_path": True,
            },
        ),
    )
    coalesced_decision = build_periodic_report_trigger_decision(coalesced)
    assert coalesced_decision["report_kind"] == "exception_update"
    assert len(coalesced_decision["coalesced_trigger_ids"]) == 2

    mismatched = _run_request(cadence)
    mismatched["profile"] = {
        "profile_id": "another_project",
        "profile_version": "v1",
        "profile_ref": "profile:another-project/v1",
    }
    with pytest.raises(ValueError, match="must match the run profile"):
        build_periodic_report_run(mismatched)

    stale_key = _run_request({**cadence, "report_key": "report_stale"})
    with pytest.raises(ValueError, match="does not match trigger identity"):
        build_periodic_report_run(stale_key)

    request_path = tmp_path / "trigger.json"
    request_path.write_text(
        json.dumps(
            _trigger_request(
                _candidate(
                    "cadence_due",
                    source_ref="scheduler:weekly-window/2026-w29",
                    evidence_digest="sha256:weekly-due",
                    facts={"due": True},
                )
            )
        ),
        encoding="utf-8",
    )
    assert (
        main(
            [
                "--format",
                "json",
                "periodic-report",
                "evaluate-trigger",
                "--request-json",
                str(request_path),
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["schema_version"] == "periodic_report_trigger_decision_v0"
    assert payload["eligible"] is True
