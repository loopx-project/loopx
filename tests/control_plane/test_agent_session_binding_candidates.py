"""Pin the session-binding candidate summary on an Agent management row.

A peer can keep several historical thread bindings. The row must carry them as
a bounded summary plus the observed count instead of letting the binding walked
last replace the earlier ones: one surviving row invites a reader to treat it as
the only route to that peer, which is how a live peer review request got
substituted for a temporary host sub-agent.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from loopx.control_plane.agents import management_projection as projection

NOW = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


def _binding(
    thread_id: str,
    *,
    agent_id: str = "peer",
    host_surface: str = "codex-app",
) -> dict[str, str]:
    return {
        "agent_id": agent_id,
        "thread_id": thread_id,
        "host_surface": host_surface,
    }


def _row(
    monkeypatch: pytest.MonkeyPatch,
    goals: list[list[dict[str, str]]],
) -> dict[str, Any]:
    monkeypatch.setattr(projection, "now_utc", lambda: NOW)
    payload: dict[str, Any] = {
        "goal_filter": "test-goal",
        "run_history": {
            "goals": [
                {
                    "id": f"test-goal-{index}",
                    "coordination": {
                        "registered_agents": ["peer", "reviewer"],
                        "thread_agent_bindings": bindings,
                    },
                }
                for index, bindings in enumerate(goals)
            ]
        },
        "todo_index": {"items": []},
    }
    packet = projection.build_agent_management_projection(payload)
    rows = {row["agent_id"]: row for row in packet["agents"]}
    assert "peer" in rows
    return rows["peer"]


def test_a_later_binding_does_not_replace_an_earlier_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(
        monkeypatch,
        [[_binding("thread-old"), _binding("thread-new", host_surface="codex-cli")]],
    )

    assert row["session_binding_count"] == 2
    assert row["session_binding_candidates"] == [
        {"thread_id": "thread-old", "host_surface": "codex-app"},
        {"thread_id": "thread-new", "host_surface": "codex-cli"},
    ]
    assert "session_binding" not in row


def test_a_republished_binding_counts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(
        monkeypatch,
        [
            [_binding("thread-shared")],
            [_binding("thread-shared"), _binding("thread-extra")],
        ],
    )

    assert row["session_binding_count"] == 2
    assert [item["thread_id"] for item in row["session_binding_candidates"]] == [
        "thread-shared",
        "thread-extra",
    ]


def test_the_candidate_list_is_bounded_without_losing_the_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bindings = [_binding(f"thread-{index}") for index in range(5)]

    row = _row(monkeypatch, [bindings])

    assert row["session_binding_count"] == 5
    assert len(row["session_binding_candidates"]) == (
        projection.MAX_SESSION_BINDING_CANDIDATES
    )
    assert [item["thread_id"] for item in row["session_binding_candidates"]] == [
        "thread-0",
        "thread-1",
        "thread-2",
    ]


def test_candidates_do_not_leak_across_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(
        monkeypatch,
        [[_binding("thread-peer"), _binding("thread-reviewer", agent_id="reviewer")]],
    )

    assert row["session_binding_count"] == 1
    assert row["session_binding_candidates"] == [
        {"thread_id": "thread-peer", "host_surface": "codex-app"}
    ]


def test_several_candidates_keep_the_unbound_lifecycle_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    single = _row(monkeypatch, [[_binding("thread-one")]])
    several = _row(monkeypatch, [[_binding("thread-one"), _binding("thread-two")]])
    none = _row(monkeypatch, [[]])

    assert single["state"] == "addressable"
    assert several["state"] == single["state"]
    assert none["state"] == "registered"
    assert "session_binding_candidates" not in none


def test_two_long_thread_ids_sharing_a_visible_prefix_are_two_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The display budget is 120 characters; the owner accepts 128.

    Two identifiers that differ only past the visible edge collapse into the
    same rendered text, so counting rendered text would report one route where
    the registry holds two.
    """

    prefix = "t" * 120
    row = _row(
        monkeypatch,
        [
            [
                _binding(prefix + "AAA"),
                _binding(prefix + "BBB"),
            ]
        ],
    )

    assert row["session_binding_count"] == 2
    assert len(row["session_binding_candidates"]) == 2
    assert (
        row["session_binding_candidates"][0]["thread_id"]
        == row["session_binding_candidates"][1]["thread_id"]
    )
    # Counting uses the full identity; rendering stays inside the display budget.
    assert len(row["session_binding_candidates"][0]["thread_id"]) == 120


def test_two_long_host_surfaces_sharing_a_visible_prefix_are_two_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    row = _row(
        monkeypatch,
        [
            [
                _binding("thread-one", host_surface="s" * 60 + "X"),
                _binding("thread-one", host_surface="s" * 60 + "Y"),
            ]
        ],
    )

    assert row["session_binding_count"] == 2
    assert len(row["session_binding_candidates"]) == 2


def test_an_identical_binding_republished_under_a_long_id_still_counts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = "t" * 120
    row = _row(
        monkeypatch,
        [
            [_binding(prefix + "AAA")],
            [_binding(prefix + "AAA"), _binding(prefix + "BBB")],
        ],
    )

    assert row["session_binding_count"] == 2


def test_padding_a_thread_id_does_not_create_a_second_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The owner strips a thread identifier before matching it.

    Keying the count on the untouched value would therefore split one binding
    into two candidates and offer a route that no resolver would confirm.
    """

    row = _row(
        monkeypatch,
        [[_binding("thread-one")], [_binding("  thread-one  ")]],
    )

    assert row["session_binding_count"] == 1
    assert row["session_binding_candidates"] == [
        {"thread_id": "thread-one", "host_surface": "codex-app"}
    ]
