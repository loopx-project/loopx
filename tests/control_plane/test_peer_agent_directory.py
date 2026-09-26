"""The local producer of `peer_agent_directory_v0` is bounded and non-mutating."""

from __future__ import annotations

from loopx.control_plane.agents.directory import (
    GAP_AUDIENCE_NOT_AUTHORIZED,
    LIMITATION_CALLER_IDENTITY_NOT_SUPPLIED,
    LIMITATION_LEASE_STATE_NOT_PROJECTED,
    LIMITATION_PRESENCE_PROVIDER_UNAVAILABLE,
    LIMITATION_ROWS_TRUNCATED,
    MAX_DIRECTORY_ROWS,
    PEER_AGENT_DIRECTORY_SCHEMA_VERSION,
    build_peer_agent_directory,
)


GOAL_ID = "peer-directory-fixture"
WORKING_AGENT = "agent-working"
IDLE_AGENT = "agent-idle"
STALE_AGENT = "agent-stale"
SUSPECTED_STALE_AT = "2020-01-01T00:00:00+08:00"


def _status_payload(agent_ids: list[str] | None = None) -> dict[str, object]:
    registered = agent_ids or [WORKING_AGENT, IDLE_AGENT, STALE_AGENT]
    return {
        "goal_filter": GOAL_ID,
        "run_history": {
            "goals": [
                {
                    "id": GOAL_ID,
                    "coordination": {"registered_agents": registered},
                }
            ]
        },
        "todo_index": {
            "items": [
                {
                    "role": "agent",
                    "todo_id": "todo_directory_blocked",
                    "goal_id": GOAL_ID,
                    "status": "blocked",
                    "priority": "P1",
                    "task_class": "advancement_task",
                    "action_kind": "implement",
                    "claimed_by": WORKING_AGENT,
                    "updated_at": SUSPECTED_STALE_AT,
                    "text": "Finish the bounded directory producer.",
                },
                {
                    "role": "agent",
                    "todo_id": "todo_directory_open",
                    "goal_id": GOAL_ID,
                    "status": "open",
                    "priority": "P2",
                    "task_class": "advancement_task",
                    "action_kind": "validate",
                    "claimed_by": STALE_AGENT,
                    "updated_at": SUSPECTED_STALE_AT,
                    "text": "Validate the directory packet against the contract.",
                },
            ]
        },
    }


def _rows_by_agent(packet: dict[str, object]) -> dict[str, dict[str, object]]:
    return {str(row["agent_id"]): row for row in packet["rows"]}  # type: ignore[index]


def test_directory_reports_registered_agents_without_inventing_presence() -> None:
    packet = build_peer_agent_directory(_status_payload(), caller_agent_id=WORKING_AGENT)

    assert packet["schema_version"] == PEER_AGENT_DIRECTORY_SCHEMA_VERSION
    assert packet["goal_id"] == GOAL_ID
    assert packet["scope"]["caller_membership"] == "registered_agent"  # type: ignore[index]
    assert packet["scope"]["gaps"] == []  # type: ignore[index]

    rows = _rows_by_agent(packet)
    assert set(rows) == {WORKING_AGENT, IDLE_AGENT, STALE_AGENT}
    # An Agent with no projected work still gets a row: identity is the registry's,
    # not the work item's.
    assert rows[IDLE_AGENT]["work"] is None
    working = rows[WORKING_AGENT]["work"]
    assert working["todo_id"] == "todo_directory_blocked"
    assert working["todo_status"] == "blocked"
    assert working["claimed"] is True
    assert working["claimed_by"] == WORKING_AGENT
    assert working["claim_age_state"] == "suspected_stale"
    # No provider is registered, so the packet names the gap instead of implying
    # that a registered Agent is running or stopped.
    assert "presence" not in rows[WORKING_AGENT]
    assert packet["presence_coverage"]["state"] == "unavailable"  # type: ignore[index]
    assert LIMITATION_PRESENCE_PROVIDER_UNAVAILABLE in packet["limitations"]  # type: ignore[operator]
    assert LIMITATION_LEASE_STATE_NOT_PROJECTED in packet["limitations"]  # type: ignore[operator]
    # Observation and delivery grant nothing.
    assert packet["authority"]["writes"] is False  # type: ignore[index]
    assert packet["authority"]["observation_grants"] == []  # type: ignore[index]


def test_rollup_orders_typed_attention_and_assigns_nothing() -> None:
    packet = build_peer_agent_directory(_status_payload())

    rollup = packet["rollup"]
    assert rollup["basis"] == "typed_work_state_only"
    assert rollup["assigns_work"] is False
    assert rollup["needs_decision"] == [WORKING_AGENT]
    # Row order is the projection's; the rollup adds no ranking of its own.
    assert set(rollup["stale_claims"]) == {WORKING_AGENT, STALE_AGENT}
    assert rollup["without_claim"] == [IDLE_AGENT]
    assert rollup["counts"] == {
        "needs_decision": 1,
        "stale_claims": 2,
        "without_claim": 1,
    }


def test_unregistered_caller_gets_a_scope_gap_instead_of_rows() -> None:
    packet = build_peer_agent_directory(_status_payload(), caller_agent_id="agent-outsider")

    assert packet["rows"] == []
    assert packet["row_count"] == 0
    gaps = packet["scope"]["gaps"]  # type: ignore[index]
    assert [gap["kind"] for gap in gaps] == [GAP_AUDIENCE_NOT_AUTHORIZED]
    assert gaps[0]["caller_agent_id"] == "agent-outsider"


def test_missing_caller_identity_is_declared_not_invented() -> None:
    packet = build_peer_agent_directory(_status_payload())

    assert packet["scope"]["caller_agent_id"] is None  # type: ignore[index]
    assert packet["scope"]["caller_membership"] == "local_surface"  # type: ignore[index]
    assert LIMITATION_CALLER_IDENTITY_NOT_SUPPLIED in packet["limitations"]  # type: ignore[operator]
    assert packet["row_count"] == 3


def test_truncated_directory_declares_the_rows_it_omitted() -> None:
    registered = [f"agent-{index:02d}" for index in range(MAX_DIRECTORY_ROWS + 2)]

    packet = build_peer_agent_directory(_status_payload(registered))

    # The projection's registered count is the union of declared registrations
    # and the Agents that claim projected work, so the invariant is checked
    # against that count rather than the fixture list.
    assert packet["registered_agent_count"] > len(registered)
    assert packet["row_count"] == MAX_DIRECTORY_ROWS
    assert (
        packet["row_count"] + packet["omitted_row_count"]
        == packet["registered_agent_count"]
    )
    assert packet["omitted_row_count"] > 0
    assert LIMITATION_ROWS_TRUNCATED in packet["limitations"]  # type: ignore[operator]


def _payload_with_bindings(bindings: list[dict[str, str]]) -> dict[str, object]:
    payload = _status_payload()
    goals = payload["run_history"]["goals"]  # type: ignore[index]
    goals[0]["coordination"]["thread_agent_bindings"] = bindings  # type: ignore[index]
    return payload


def test_directory_rows_publish_peer_route_candidates_without_selecting_one():
    payload = _payload_with_bindings(
        [
            {
                "thread_id": "thread-1",
                "host_surface": "codex-cli",
                "agent_id": WORKING_AGENT,
            },
            {
                "thread_id": "thread-2",
                "host_surface": "claude-code",
                "agent_id": WORKING_AGENT,
            },
        ]
    )

    packet = build_peer_agent_directory(payload)

    row = next(item for item in packet["rows"] if item["agent_id"] == WORKING_AGENT)
    route = row["peer_route"]
    assert route["schema_version"] == "loopx_agent_binding_route_summary_v0"
    assert route["outcome"] == "multiple_candidates"
    assert route["candidate_count"] == 2
    assert route["candidates"] == [
        {"thread_id": "thread-1", "host_surface": "codex-cli"},
        {"thread_id": "thread-2", "host_surface": "claude-code"},
    ]
    assert route["provenance"]["selects_route"] is False
    # The packet's existing non-authorization guarantees are unchanged.
    assert packet["authority"]["writes"] is False
    assert packet["scope"]["caller_membership"] == "local_surface"
    assert LIMITATION_LEASE_STATE_NOT_PROJECTED in packet["limitations"]


def test_directory_row_reports_no_candidate_for_an_agent_without_bindings():
    payload = _payload_with_bindings(
        [
            {
                "thread_id": "thread-1",
                "host_surface": "codex-cli",
                "agent_id": IDLE_AGENT,
            }
        ]
    )

    packet = build_peer_agent_directory(payload)

    row = next(item for item in packet["rows"] if item["agent_id"] == WORKING_AGENT)
    assert row["peer_route"]["outcome"] == "no_candidate"
    assert row["peer_route"]["candidates"] == []
    assert row["peer_route"]["candidate_count"] == 0
