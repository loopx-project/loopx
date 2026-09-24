import pytest

from loopx.control_plane.quota.blocked_retry import (
    overlay_active_turn_retries,
    require_blocked_retry_wait,
)
from loopx.control_plane.quota.projection_repair import (
    build_state_projection_gap_repair_hint,
)
from loopx.control_plane.runtime.run_context_retention import (
    compact_goal_semantic_history,
    goal_semantic_history_from_runs,
)


def test_deferred_retry_suppresses_only_its_own_projection_gap() -> None:
    gap = {
        "requires_todo_expansion": True,
        "evidence_count": 1,
        "first_evidence": [
            {
                "kind": "next_action_executable_without_agent_todo",
                "target_role": "agent",
                "text": "Validate and settle the selected delivery.",
            }
        ],
    }
    summary = {
        "open_count": 0,
        "deferred_items": [
            {
                "task_class": "advancement_task",
                "status": "deferred",
                "text": "[P1] Validate and settle the selected delivery.",
                "resume_when": "resume_at:2026-09-24T14:30:00Z",
                "resume_ready": False,
            }
        ],
    }
    kwargs = {
        "candidate_should_run": True,
        "user_todo_summary": {"open_count": 0},
        "work_lane_contract": None,
    }
    assert (
        build_state_projection_gap_repair_hint(
            gap, agent_todo_summary=summary, **kwargs
        )
        is None
    )

    unrelated = {
        **gap,
        "first_evidence": [
            {
                **gap["first_evidence"][0],
                "text": "Review a separate blocked source.",
            }
        ],
    }
    repair = build_state_projection_gap_repair_hint(
        unrelated, agent_todo_summary=summary, **kwargs
    )
    assert repair is not None
    assert repair["trigger"] == "state_projection_gap"


def test_blocked_retry_requires_the_exact_pending_todo_and_bounded_due_time() -> None:
    def fields(due: str) -> dict:
        resume = f"resume_at:{due}"
        return {
            "agent_todos": {
                "items": [
                    {
                        "todo_id": "todo_current001",
                        "status": "deferred",
                        "task_class": "advancement_task",
                        "resume_when": resume,
                        "resume_ready": False,
                        "resume_condition": {
                            "kind": "resume_at",
                            "resume_when": resume,
                            "satisfied": False,
                        },
                    }
                ]
            }
        }

    observed = "2026-09-24T14:00:00Z"
    valid = require_blocked_retry_wait(
        fields("2026-09-24T14:05:00Z"),
        todo_id="todo_current001",
        observed_at=observed,
    )
    assert valid["due_at"] == "2026-09-24T14:05:00Z"
    with pytest.raises(ValueError, match="same unfinished advancement Todo"):
        require_blocked_retry_wait(
            fields("2026-09-24T14:05:00Z"),
            todo_id="todo_other",
            observed_at=observed,
        )
    with pytest.raises(ValueError, match="1–30 minutes"):
        require_blocked_retry_wait(
            fields("2026-09-24T14:31:00Z"),
            todo_id="todo_current001",
            observed_at=observed,
        )


def test_turn_owned_retry_is_selection_only_and_expires() -> None:
    retry = {
        "schema_version": "quota_blocked_retry_v0",
        "source": "turn_settlement",
        "todo_id": "todo_current001",
        "resume_when": "resume_at:2026-09-24T14:05:00Z",
        "observed_at": "2026-09-24T14:00:00Z",
        "due_at": "2026-09-24T14:05:00Z",
    }
    original = {
        "todo_id": "todo_current001",
        "task_class": "advancement_task",
        "status": "open",
    }
    status = {
        "run_history": {
            "goals": [
                {
                    "id": "goal-a",
                    "latest_runs": [
                        {
                            "agent_id": "agent-a",
                            "todo_id": "todo_current001",
                            "turn_instance_id": "turn-a",
                            "delivery_outcome": "outcome_gap",
                            "progress_observation": {"result_class": "blocked"},
                            "blocked_retry": retry,
                        }
                    ],
                }
            ]
        },
        "attention_queue": {
            "items": [
                {
                    "goal_id": "goal-a",
                    "agent_todos": {
                        "items": [original],
                    },
                }
            ]
        },
    }
    waiting = overlay_active_turn_retries(
        status,
        goal_id="goal-a",
        agent_id="agent-a",
        observed_at="2026-09-24T14:02:00Z",
    )
    projected = waiting["attention_queue"]["items"][0]["agent_todos"]["items"][0]
    assert projected["resume_when"] == retry["resume_when"]
    assert projected["resume_ready"] is False
    assert "resume_when" not in original
    new_subject = {
        **status,
        "attention_queue": {
            "items": [
                {
                    **status["attention_queue"]["items"][0],
                    "agent_todos": {
                        "items": [
                            original,
                            {
                                "todo_id": "todo_created_after_block",
                                "task_class": "advancement_task",
                                "status": "open",
                            },
                        ]
                    },
                }
            ],
        },
    }
    new_subject_rows = overlay_active_turn_retries(
        new_subject,
        goal_id="goal-a",
        agent_id="agent-a",
        observed_at="2026-09-24T14:02:00Z",
    )["attention_queue"]["items"][0]["agent_todos"]["items"]
    assert new_subject_rows[0]["resume_ready"] is False
    assert "resume_when" not in new_subject_rows[1]
    assert (
        overlay_active_turn_retries(
            status,
            goal_id="goal-a",
            agent_id="agent-a",
            observed_at="2026-09-24T14:05:00Z",
        )
        is status
    )
    assert (
        overlay_active_turn_retries(
            status,
            goal_id="goal-a",
            agent_id="agent-b",
            observed_at="2026-09-24T14:02:00Z",
        )
        is status
    )
    superseded = {
        **status,
        "run_history": {
            "goals": [
                {
                    "id": "goal-a",
                    "latest_runs": [
                        {
                            "agent_id": "agent-a",
                            "todo_id": "todo_current001",
                            "delivery_outcome": "outcome_progress",
                        },
                        status["run_history"]["goals"][0]["latest_runs"][0],
                    ],
                }
            ]
        },
    }
    assert (
        overlay_active_turn_retries(
            superseded,
            goal_id="goal-a",
            agent_id="agent-a",
            observed_at="2026-09-24T14:02:00Z",
        )
        is superseded
    )


def test_blocked_retry_survives_recent_run_display_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "loopx.control_plane.runtime.run_context_retention.now_utc_iso",
        lambda: "2026-09-24T14:02:00Z",
    )
    blocked = {
        "agent_id": "agent-a",
        "todo_id": "todo_current001",
        "turn_instance_id": "turn-a",
        "delivery_outcome": "outcome_gap",
        "progress_observation": {"result_class": "blocked"},
        "blocked_retry": {
            "schema_version": "quota_blocked_retry_v0",
            "source": "turn_settlement",
            "todo_id": "todo_current001",
            "resume_when": "resume_at:2026-09-24T14:05:00Z",
            "observed_at": "2026-09-24T14:00:00Z",
            "due_at": "2026-09-24T14:05:00Z",
        },
    }
    newer = [
        {"agent_id": "agent-a", "todo_id": f"todo_other{i:03d}"} for i in range(40)
    ]
    semantic = compact_goal_semantic_history(
        goal_semantic_history_from_runs([*newer, blocked]),
        compact_run=dict,
    )
    assert semantic is not None
    assert semantic["active_blocked_retry_runs"] == [blocked]
    status = {
        "run_history": {
            "goals": [
                {
                    "id": "goal-a",
                    "latest_runs": newer[:3],
                    "semantic_history": semantic,
                }
            ]
        },
        "attention_queue": {
            "items": [
                {
                    "goal_id": "goal-a",
                    "agent_todos": {
                        "items": [
                            {
                                "todo_id": "todo_current001",
                                "task_class": "advancement_task",
                                "status": "open",
                            }
                        ],
                    },
                }
            ]
        },
    }
    projected = overlay_active_turn_retries(
        status,
        goal_id="goal-a",
        agent_id="agent-a",
        observed_at="2026-09-24T14:02:00Z",
    )
    assert (
        projected["attention_queue"]["items"][0]["agent_todos"]["items"][0][
            "resume_ready"
        ]
        is False
    )
    assert (
        "resume_when"
        not in status["attention_queue"]["items"][0]["agent_todos"]["items"][0]
    )

    superseded = goal_semantic_history_from_runs(
        [
            {
                "agent_id": "agent-a",
                "todo_id": "todo_current001",
                "delivery_outcome": "outcome_progress",
            },
            *newer,
            blocked,
        ]
    )
    assert superseded["active_blocked_retry_runs"] == []
