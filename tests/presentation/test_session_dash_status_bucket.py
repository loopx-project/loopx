from __future__ import annotations

import pytest

from loopx.presentation.projections.session_dash import (
    _status_bucket,
    build_session_dash_projection,
)

# Statuses the runtime actually writes, plus the words the buckets document.
# Every one of these has to keep the bucket it has today.
DOCUMENTED_STATUSES = [
    ("done", "done"),
    ("complete", "done"),
    ("completed", "done"),
    ("closed", "done"),
    ("shipped", "done"),
    ("archived", "done"),
    ("success", "done"),
    ("blocked", "blocked"),
    ("blocked_by_safety", "blocked"),
    ("stalled", "blocked"),
    ("stuck", "blocked"),
    ("failed", "blocked"),
    ("compose_setup_blocked_before_agent_rounds", "blocked"),
    ("active", "active"),
    ("active-progress", "active"),
    ("in_progress", "active"),
    ("running", "active"),
    ("keepalive", "active"),
    ("relaunched", "active"),
    ("planned", "other"),
    ("unknown", "other"),
    ("no_change", "other"),
    ("unregistered_runtime_goal", "other"),
    ("operator_gate_approved", "other"),
]

# A marker is a word, not a substring: these read as the *opposite* of the
# marker they contain. #4336 is the same defect one layer down, where a prose
# matcher read `installed` as `stalled`.
MARKER_INSIDE_A_LONGER_WORD = [
    ("installed", "other"),
    ("uninstalled", "other"),
    ("reinstalled", "other"),
    ("incomplete", "other"),
    ("undone", "other"),
    ("unsuccessful", "other"),
    ("not_completed", "other"),
    ("not_shipped", "other"),
]


def test_documented_statuses_keep_their_bucket() -> None:
    for status, expected in DOCUMENTED_STATUSES:
        assert _status_bucket(status=status, waiting_on=None) == expected, status


def test_a_marker_inside_a_longer_word_does_not_set_the_bucket() -> None:
    for status, expected in MARKER_INSIDE_A_LONGER_WORD:
        assert _status_bucket(status=status, waiting_on=None) == expected, status


@pytest.mark.parametrize(
    "waiting_on",
    ["user", "controller", "user_or_controller", "operator", "human", "owner", "user_gate"],
)
def test_a_human_gate_is_needs_user(waiting_on: str) -> None:
    assert _status_bucket(status="unregistered_runtime_goal", waiting_on=waiting_on) == "needs_user"


@pytest.mark.parametrize("waiting_on", ["agent", "codex", "auto", "external_evidence"])
def test_an_agent_or_evidence_gate_is_not_needs_user(waiting_on: str) -> None:
    assert _status_bucket(status="unregistered_runtime_goal", waiting_on=waiting_on) == "other"


def test_a_blocked_status_outranks_the_waiting_reason() -> None:
    assert _status_bucket(status="blocked_by_safety", waiting_on="controller") == "blocked"


def test_projection_counts_a_controller_gate_as_needs_user() -> None:
    """``session_runtime`` emits ``waiting_on="controller"``; the page's
    needs-you column has to count it the same way it counts
    ``user_or_controller``."""

    def projection_for(waiting_on: str) -> dict[str, object]:
        return build_session_dash_projection(
            status_payload={
                "attention_queue": {
                    "items": [
                        {
                            "goal_id": "runtime-goal",
                            "status": "unregistered_runtime_goal",
                            "waiting_on": waiting_on,
                        }
                    ]
                }
            },
            run_history={"goals": [{"id": "runtime-goal", "display_name": "runtime goal"}]},
        )

    controller = projection_for("controller")
    user_or_controller = projection_for("user_or_controller")

    expected = {"active": 0, "needs_user": 1, "blocked": 0, "done": 0, "other": 0}
    assert controller["overview"]["goals_by_status"] == expected
    assert user_or_controller["overview"]["goals_by_status"] == expected


def test_projection_does_not_fold_a_healthy_install_goal_into_blocked() -> None:
    projection = build_session_dash_projection(
        run_history={
            "goals": [{"id": "install-goal", "display_name": "installer", "status": "installed"}]
        },
    )

    assert projection["overview"]["goals_by_status"]["blocked"] == 0
    entry = projection["unassigned_goals"][0]
    assert entry["status"] == "installed"
    assert entry["status_bucket"] == "other"
