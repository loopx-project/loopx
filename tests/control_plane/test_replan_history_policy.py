"""Independent invariants for the public history-to-replan boundary."""

from __future__ import annotations

from loopx.status import (
    autonomous_replan_obligation_from_runs,
    autonomous_replan_periodic_review_from_runs,
)
from loopx.control_plane.work_items.progress_observation import typed_progress_repeat_trigger

AGENT = "history-worker"


def run(n: int, *, turn: str | None = None, agent: str = AGENT) -> dict:
    value = {
        "agent_id": agent,
        "generated_at": f"2026-09-22T00:00:{n:02d}Z",
        "classification": "bounded_delivery",
    }
    if turn is not None:
        value["turn_instance_id"] = turn
    return value


def observation() -> dict:
    return {"schema_version": "typed_progress_observation_v0",
            "result_class": "unchanged", "surface_id": "surface-fixture",
            "evidence_ids": ["evidence-fixture"]}


def monitor(n: int, *, turn: str | None = None, agent: str = AGENT) -> dict:
    return {**run(n, turn=turn, agent=agent), "classification": "quota_monitor_poll",
            "todo_id": "todo_monitor_fixture",
            "monitor_target": {"target_id": "target-fixture", "agent_id": agent,
                               "monitor_mode": "due_monitor_observed_without_material_transition"}}


def obligation(rows: list[dict]) -> dict | None:
    return autonomous_replan_obligation_from_runs(rows, agent_todos=None, agent_id=AGENT)


def test_periodic_review_counts_work_turns_not_retry_records() -> None:
    assert obligation([run(n, turn="one-turn") for n in range(30, 0, -1)]) is None


def test_monitor_repeat_counts_work_turns_not_retry_records() -> None:
    assert obligation([monitor(n, turn="one-poll") for n in range(6, 0, -1)]) is None


def test_progress_ack_ends_repeat_window() -> None:
    ack = {**run(3), "autonomous_replan_ack": {
        "recorded": True, "semantic_delta": {"accepted": True}}}
    rows = [ack, {**run(2), "progress_observation": observation()},
            {**run(1), "progress_observation": observation()}]
    assert typed_progress_repeat_trigger(rows, agent_id=AGENT) is None
    assert obligation(rows) is None


def test_neutral_accounting_does_not_interrupt_equivalent_progress() -> None:
    rows = [{**run(3), "progress_observation": observation()},
            {**run(2), "classification": "quota_slot_voided"},
            {**run(1), "progress_observation": observation()}]
    result = obligation(rows)
    assert result is not None
    assert result["triggers"][0]["kind"] == "typed_progress_repeat"


def test_peer_ack_does_not_discharge_current_lane() -> None:
    rows = [{**run(3, agent="peer"), "autonomous_replan_ack": {
        "recorded": True, "semantic_delta": {"accepted": True}}},
        {**run(2), "progress_observation": observation()},
        {**run(1), "progress_observation": observation()}]
    assert obligation(rows) is not None


def test_twenty_distinct_turns_retain_periodic_identity() -> None:
    rows = [run(n, turn=f"turn-{n}") for n in range(20, 0, -1)]
    first = obligation(rows)
    assert first is not None
    trigger = first["triggers"][0]
    assert trigger["kind"] == "periodic_review_due"
    assert trigger["run_count"] == 20
    assert trigger["latest_generated_at"] == rows[0]["generated_at"]
    assert trigger["oldest_counted_generated_at"] == rows[-1]["generated_at"]
    assert obligation(rows) == first


def test_periodic_review_ignores_quiet_polls_but_counts_material_transitions() -> None:
    quiet = [
        {**monitor(n, turn=f"quiet-{n}"),
         "monitor_target": {"target_id": "watch", "agent_id": AGENT,
                            "monitor_mode": ("due_monitor_observed_without_material_transition"
                                             if n > 38 else "monitor_quiet_until_material_transition")},
         "monitor_event": {"material_change": False}}
        for n in range(40, 20, -1)
    ]
    work = [run(n, turn=f"work-{n}") for n in range(19, 0, -1)]

    def periodic(rows: list[dict]) -> dict | None:
        return autonomous_replan_periodic_review_from_runs(rows, agent_todos=None)
    assert periodic(quiet) is None
    assert periodic(quiet + work) is None
    material = {**monitor(41, turn="material-41"),
                "monitor_target": {"target_id": "watch", "agent_id": AGENT,
                                   "monitor_mode": "due_monitor_material_transition"},
                "monitor_event": {"material_change": True}}
    assert periodic([material] + quiet + work)["triggers"][0]["kind"] == "periodic_review_due"


def test_six_distinct_polls_retain_dead_monitor_contract() -> None:
    result = obligation([monitor(n, turn=f"poll-{n}") for n in range(6, 0, -1)])
    assert result is not None
    assert result["triggers"][0]["kind"] == "dead_monitor_repeat"
    assert result["dead_monitor_detector"]["run_count"] == 6


def test_genuinely_unknown_work_still_breaks_progress_streak() -> None:
    rows = [{**run(3), "progress_observation": observation()}, run(2),
            {**run(1), "progress_observation": observation()}]
    assert obligation(rows) is None


def test_ack_needs_accepted_semantic_evidence() -> None:
    ack = {**run(3), "autonomous_replan_ack": {"recorded": True}}
    rows = [ack, {**run(2), "progress_observation": observation()},
            {**run(1), "progress_observation": observation()}]
    assert obligation(rows) is not None


def test_retry_identity_is_scoped_to_agent() -> None:
    rows = [{**run(3, turn="shared", agent="peer"), "progress_observation": observation()},
            {**run(2, turn="shared"), "progress_observation": observation()},
            {**run(1, turn="prior"), "progress_observation": observation()}]
    result = obligation(rows)
    assert result is not None and result["agent_id"] == AGENT


def test_future_monitor_expiry_must_cover_the_due_instant() -> None:
    from test_goal_vision_blocked_successor import (
        _blocked_wait_polls, _monitor_blocked_advancement_items, _quota_with_replan_runs,
    )
    for expiry, suppressed in [
        ("2026-07-16T00:01:00Z", False),
        ("2099-01-01T00:00:00Z", False),
        ("2099-01-01T00:00:00.000001Z", True),
    ]:
        items = _monitor_blocked_advancement_items(next_due_at="2099-01-01T00:00:00Z")
        items[0]["expires_at"] = expiry
        guard = _quota_with_replan_runs(_blocked_wait_polls(), extra_agent_items=items)
        kinds = [row["kind"] for row in (guard.get("autonomous_replan_obligation") or {}).get("triggers", [])]
        assert ("blocked_successor_no_progress_repeat" not in kinds) is suppressed


def test_codec_keeps_prose_out_of_the_history_decision_request(monkeypatch) -> None:
    import json
    from loopx.control_plane.work_items import replan_history_codec as replan_history

    requests = []
    def capture(method, params):
        requests.append((method, params))
        return {"schema_version": "replan_history_result_v0", "trigger": None}
    monkeypatch.setattr(replan_history, "effect_runtime_result", capture)
    prose = "private-unconsumed-prose" * 10000
    rows = [{**monitor(2), "prompt": prose, "summary": prose}, monitor(1)]
    replan_history.project_replan_history(rows, agent_id=AGENT)
    assert len(requests) == 1
    assert requests[0][0] == "work_item.replan_history.project"
    encoded = json.dumps(requests[0][1])
    assert prose not in encoded
    assert len(encoded) < 5000
    assert requests[0][1]["todos"]["resume"] is None


def test_long_history_keeps_evidence_beyond_rpc_limit() -> None:
    # A retry flood is not twenty turns. The second distinct observation is
    # deliberately older than the entire flood; tail/window truncation loses it.
    repeated = {**run(2, turn="retried-turn"), "progress_observation": observation()}
    rows = [repeated] * 12000 + [{**run(1, turn="older-turn"),
                                "progress_observation": observation()}]
    result = obligation(rows)
    assert result is not None
    trigger = result["triggers"][0]
    assert trigger["kind"] == "typed_progress_repeat"
    assert trigger["run_count"] == 2
    assert trigger["oldest_counted_generated_at"] == rows[-1]["generated_at"]


def test_snapshot_transport_is_private_compact_and_always_removed(monkeypatch) -> None:
    import json
    import os
    from pathlib import Path
    import pytest
    from loopx.control_plane.work_items import replan_history_codec as codec
    from loopx.control_plane.effect_runtime import EffectRuntimeRejected

    original = codec.effect_runtime_result
    snapshots = []
    def inspect(method, params):
        assert method == "work_item.replan_history.project_snapshot"
        assert len(json.dumps(params)) < 1024
        path = Path(params["path"])
        snapshots.append(path)
        assert path.is_file()
        if os.name != "nt":
            assert path.stat().st_mode & 0o077 == 0
            assert path.parent.stat().st_mode & 0o077 == 0
        return original(method, params)
    # Force both request forms over the exact same semantic fixture.
    rows = [{**run(2), "progress_observation": observation()},
            {**run(1), "progress_observation": observation()}]
    expected = codec.project_replan_history(rows, agent_id=AGENT)
    monkeypatch.setattr(codec, "MAX_REQUEST_BYTES", 1)
    monkeypatch.setattr(codec, "effect_runtime_result", inspect)
    assert codec.project_replan_history(rows, agent_id=AGENT) == expected
    assert all(not path.parent.exists() for path in snapshots)

    def reject(method, params):
        snapshots.append(Path(params["path"]))
        raise EffectRuntimeRejected("synthetic rejection")
    monkeypatch.setattr(codec, "effect_runtime_result", reject)
    with pytest.raises(ValueError, match="synthetic rejection"):
        codec.project_replan_history(rows, agent_id=AGENT)
    assert all(not path.parent.exists() for path in snapshots)
