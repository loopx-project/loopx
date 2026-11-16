"""A failed optional lane read is visible without losing the base status."""

from copy import deepcopy

import pytest

from loopx.cli_commands import status
from loopx.control_plane.projection_envelope_facts import (
    render_projection_envelope_markdown,
    seal_projection_envelope,
    source_fact,
)
from loopx.control_plane.runtime.time import now_utc_iso


def _payload(*, stale: bool = False, omitted: list | None = None) -> dict:
    return {
        "ok": True,
        "contract_summary": {"errors": 0, "warnings": 0, "checks": 5},
        "attention_queue": {
            "items": [
                {"goal_id": goal, "recommended_action": "Keep the Goal action"}
                for goal in ("first", "second")
            ]
        },
        "projection_envelope": seal_projection_envelope(
            projection="status",
            observed_at=now_utc_iso(),
            sources=[source_fact(
                "registry", last_read_at="2020-01-01T00:00:00Z" if stale else now_utc_iso(),
                item_count=2,
            )],
            coverage={
                "scope": "goal", "expected_count": 2, "included_count": 2,
                "omitted": omitted or [], "shown_count": 2, "available_count": 2,
            },
        ),
    }


def _unavailable(*args, **kwargs):
    raise ValueError("private transport diagnostic must not enter display metadata")


def test_all_lane_reads_failed_preserves_base_and_reports_unavailable(monkeypatch) -> None:
    payload = _payload()
    original_items = deepcopy(payload["attention_queue"]["items"])
    monkeypatch.setattr(status, "build_quota_should_run", _unavailable)

    status.attach_agent_lane_next_actions(payload, agent_id="reader")

    assert payload["ok"] is True
    assert payload["contract_summary"] == {"errors": 0, "warnings": 0, "checks": 5}
    assert payload["attention_queue"]["items"] == original_items
    envelope = payload["projection_envelope"]
    assert envelope["complete"] is False
    assert envelope["fresh"] is False
    assert envelope["alert"] is True
    assert "unreadable_sources" in envelope["alert_reasons"]
    missing = envelope["coverage"]["omitted"][-1]
    assert missing["count"] == 2
    assert missing["refs"] == ["first", "second"]
    assert payload["agent_lane_next_action_projection"]["unavailable_count"] == 2
    rendered = "\n".join(render_projection_envelope_markdown(envelope))
    assert "incomplete_coverage" in rendered
    assert "private transport" not in str(payload)


def test_one_lane_failure_keeps_the_other_goal_action(monkeypatch) -> None:
    payload = _payload()
    action = {"schema_version": "agent_lane_next_action_v0", "action": "continue"}

    def guard(_payload, *, goal_id, agent_id):
        if goal_id == "first":
            return _unavailable()
        return {"agent_lane_next_action": action}

    monkeypatch.setattr(status, "build_quota_should_run", guard)
    status.attach_agent_lane_next_actions(payload, agent_id="reader")

    assert payload["attention_queue"]["items"][1]["agent_lane_next_action"] == action
    projection = payload["agent_lane_next_action_projection"]
    assert projection["attached_count"] == 1
    assert projection["unavailable_count"] == 1
    assert payload["projection_envelope"]["coverage"]["omitted"][-1]["refs"] == ["first"]


def test_failed_enrichment_does_not_refresh_stale_source(monkeypatch) -> None:
    omission = {"reason": "run_index_unreadable", "count": 1, "refs": ["second"]}
    payload = _payload(stale=True, omitted=[omission])
    original = deepcopy(payload["projection_envelope"])
    monkeypatch.setattr(status, "build_quota_should_run", _unavailable)
    status.attach_agent_lane_next_actions(payload, agent_id="reader")

    envelope = payload["projection_envelope"]
    registry = next(row for row in envelope["sources"] if row["source_id"] == "registry")
    assert registry["last_read_at"] == original["sources"][0]["last_read_at"]
    assert registry["status"] == "stale"
    assert envelope["coverage"]["omitted"][0] == omission
    assert {"stale_sources", "unreadable_sources"} <= set(envelope["alert_reasons"])


def test_many_failed_goals_keep_full_count_with_bounded_references(monkeypatch) -> None:
    payload = _payload()
    payload["attention_queue"]["items"] = [{"goal_id": f"goal-{index}"} for index in range(12)]
    monkeypatch.setattr(status, "build_quota_should_run", _unavailable)
    status.attach_agent_lane_next_actions(payload, agent_id="reader")
    omission = payload["projection_envelope"]["coverage"]["omitted"][-1]
    assert omission["count"] == 12
    assert len(omission["refs"]) == 8
    assert payload["agent_lane_next_action_projection"]["unavailable_count"] == 12


@pytest.mark.parametrize("agent_id", ["", "reader"])
def test_healthy_or_unrequested_lane_preserves_base_envelope(monkeypatch, agent_id) -> None:
    payload = _payload()
    original = deepcopy(payload["projection_envelope"])
    monkeypatch.setattr(status, "build_quota_should_run", lambda *a, **kw: {})
    status.attach_agent_lane_next_actions(payload, agent_id=agent_id)
    assert payload["projection_envelope"] == original
    assert "unavailable_count" not in payload.get("agent_lane_next_action_projection", {})


def test_a_fresh_successful_read_clears_previous_unavailability(monkeypatch) -> None:
    failed = _payload()
    monkeypatch.setattr(status, "build_quota_should_run", _unavailable)
    status.attach_agent_lane_next_actions(failed, agent_id="reader")
    assert failed["projection_envelope"]["alert"] is True

    refreshed = _payload()
    monkeypatch.setattr(status, "build_quota_should_run", lambda *a, **kw: {})
    status.attach_agent_lane_next_actions(refreshed, agent_id="reader")
    assert refreshed["projection_envelope"]["complete"] is True
    assert refreshed["projection_envelope"]["alert"] is False
