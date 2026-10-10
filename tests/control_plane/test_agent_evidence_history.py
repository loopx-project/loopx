from __future__ import annotations

import json

import pytest

from loopx.control_plane.runtime.agent_evidence_history import (
    build_agent_scoped_evidence_log,
    project_evidence_log_read_receipts,
)


@pytest.mark.parametrize(
    "unsafe_summary",
    [
        "access_key=should-not-surface",
        "secret-key:should-not-surface",
        "sk=should-not-surface",
        "/private/tmp/owner-only/evidence.json",
    ],
)
def test_evidence_log_reuses_the_shared_public_safety_boundary(
    unsafe_summary: str,
) -> None:
    payload = build_agent_scoped_evidence_log(
        goal_id="public-goal",
        agent_id="codex-agent",
        rollout_events=[
            {
                "agent_id": "codex-agent",
                "event_kind": "validation",
                "recorded_at": "2026-08-12T00:00:00Z",
                "summary": unsafe_summary,
            }
        ],
        history_runs=[],
    )

    assert unsafe_summary not in json.dumps(payload)
    assert payload["ledger"] == [
        {
            "source": "rollout_event_log",
            "recorded_at": "2026-08-12T00:00:00Z",
            "event_id": None,
            "event_kind": "validation",
            "agent_id": "codex-agent",
        }
    ]


def test_evidence_log_keeps_safe_compact_prose() -> None:
    payload = build_agent_scoped_evidence_log(
        goal_id="public-goal",
        agent_id="codex-agent",
        rollout_events=[
            {
                "agent_id": "codex-agent",
                "event_kind": "validation",
                "recorded_at": "2026-08-12T00:00:00Z",
                "summary": "authorization token label is safe as prose",
            }
        ],
        history_runs=[],
    )

    assert payload["ledger"][0]["summary"] == (
        "authorization token label is safe as prose"
    )


def test_history_todo_filter_avoids_prefix_collisions() -> None:
    payload = build_agent_scoped_evidence_log(
        goal_id="public-goal",
        agent_id="codex-agent",
        rollout_events=[],
        history_runs=[
            {
                "goal_id": "public-goal",
                "agent_id": "codex-agent",
                "generated_at": "2026-08-18T00:00:00Z",
                "todo_id": "todo_abcd",
                "classification": "todo_abc follow-up after todo_abcd",
            },
            {
                "goal_id": "public-goal",
                "agent_id": "codex-agent",
                "generated_at": "2026-08-18T01:00:00Z",
                "classification": "todo_abc completed",
            },
            {
                "goal_id": "public-goal",
                "agent_id": "codex-agent",
                "generated_at": "2026-08-18T02:00:00Z",
                "todo_id": "todo_abc",
                "classification": "todo_abc completed",
            },
        ],
        todo_id="todo_abc",
    )

    assert payload["run_history_ref_count"] == 2
    assert [row["classification"] for row in payload["ledger"]] == [
        "todo_abc completed",
        "todo_abc completed",
    ]


def test_chronology_normalizes_offsets_for_receipts_ledger_and_frontier() -> None:
    receipt_events = [
        {
            "event_kind": "evidence_log_read",
            "status": "completed",
            "goal_id": "public-goal",
            "agent_id": "codex-agent",
            "event_id": "read-early",
            "recorded_at": "2026-08-18T10:00:00+08:00",
            "details": {"command": "loopx evidence-log"},
        },
        {
            "event_kind": "evidence_log_read",
            "status": "completed",
            "goal_id": "public-goal",
            "agent_id": "codex-agent",
            "event_id": "read-late",
            "recorded_at": "2026-08-18T03:00:00Z",
            "details": {"command": "loopx evidence-log"},
        },
        {
            "event_kind": "evidence_log_read",
            "status": "completed",
            "goal_id": "public-goal",
            "agent_id": "codex-agent",
            "event_id": "read-overflow",
            "recorded_at": "9999-12-31T23:59:59-23:59",
            "details": {"command": "loopx evidence-log"},
        },
    ]
    receipts = project_evidence_log_read_receipts(receipt_events)
    assert [row["event_id"] for row in receipts] == [
        "read-late",
        "read-early",
        "read-overflow",
    ]

    payload = build_agent_scoped_evidence_log(
        goal_id="public-goal",
        agent_id="codex-agent",
        rollout_events=[],
        history_runs=[
            {
                "goal_id": "public-goal",
                "agent_id": "codex-agent",
                "generated_at": "2026-08-18T10:00:00+08:00",
                "classification": "current-early",
            },
            {
                "goal_id": "public-goal",
                "agent_id": "codex-agent",
                "generated_at": "2026-08-18T03:00:00Z",
                "classification": "current-late",
            },
            {
                "goal_id": "public-goal",
                "agent_id": "other-agent",
                "generated_at": "2026-08-18T10:00:00+08:00",
                "classification": "other-early",
            },
            {
                "goal_id": "public-goal",
                "agent_id": "other-agent",
                "generated_at": "2026-08-18T03:00:00Z",
                "classification": "other-late",
            },
            {
                "goal_id": "public-goal",
                "agent_id": "other-agent",
                "generated_at": "not-a-timestamp",
                "classification": "other-malformed",
            },
            {
                "goal_id": "public-goal",
                "agent_id": "codex-agent",
                "generated_at": "not-a-timestamp",
                "classification": "current-malformed",
            },
        ],
    )

    assert [row["classification"] for row in payload["ledger"]] == [
        "current-late",
        "current-early",
        "current-malformed",
    ]
    assert payload["other_agent_frontier"]["items"][0]["classification"] == (
        "other-late"
    )
