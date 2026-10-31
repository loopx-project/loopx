"""Base-writer history remains auditable after stricter new-write attribution."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event, append_explore_result_events, build_explore_finding_event,
    build_explore_result_projection, explore_result_log_path, load_explore_result_events_strict,
)
from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.presentation.explore_results_api import _finding_rows, _history_rows

# Written by the real builders/append API at main 21367015cce45294dcf99f54a3211d66f5d842c5.
# Synthetic protocol records only: a node, mixed A/B finding, then A/A correction.
FIXTURE = Path(__file__).with_name("fixtures") / "explore_measurement_history_v0.jsonl"


def test_base_history_and_corrections_remain_readable_without_promoting_mixed_measurements(tmp_path):
    original = FIXTURE.read_bytes()
    assert hashlib.sha256(original).hexdigest() == "5473f0ef178e732be19dcc5fd6cb7c56de964a080bbe1fd7844c1dd4cd66a940"
    events = [json.loads(line) for line in original.splitlines()]
    runtime = tmp_path / "runtime"
    log = explore_result_log_path(runtime, "research")
    log.parent.mkdir(parents=True)
    prefix = b"\n".join(original.splitlines()[:2]) + b"\n"
    log.write_bytes(prefix)
    retained = load_explore_result_events_strict(log, goal_id="research")
    assert retained == events[:2]
    assert _history_rows(runtime, "research")[1]["measurements"][0]["evidence_ref"] == "result:b"
    [current] = _finding_rows(retained, [], goal_id="research")
    assert "measurements" not in current
    assert "executed candidate's result" in current["measurement_error"]
    projection = build_explore_result_projection(retained, goal_id="research")
    request = dict(goal_id="research", agent_id="worker", route=["loopx"], graph_enabled=True,
        harness_gate={"enabled": True}, projection=projection, plan={})
    turn = effect_runtime_result("explore.turn_context", request)["graph"]
    assert not turn.get("measurement_feedback")
    assert turn["writeback_results"][0]["measurement_error"] == current["measurement_error"]
    # New writes cannot bypass the builder by supplying a legacy canonical row.
    for append in (
        lambda: append_explore_result_event(log, events[1]),
        lambda: append_explore_result_events(log, [events[1]], expected_goal_id="research"),
    ):
        with pytest.raises(ValueError, match="executed candidate's result"):
            append()
        assert log.read_bytes() == prefix
    corrected = events[2]
    append_explore_result_event(log, corrected)
    assert log.read_bytes() == original, "new correction appends without rewriting the old evidence"
    assert load_explore_result_events_strict(log, goal_id="research") == events
    history = _history_rows(runtime, "research")
    assert len(history) == 3 and history[1]["measurements"][0]["evidence_ref"] == "result:b"
    [current] = _finding_rows(events, [], goal_id="research")
    assert "measurement_error" not in current
    assert current["measurements"][0]["evidence_ref"] == "result:a"
    request["projection"] = build_explore_result_projection(events, goal_id="research")
    feedback = effect_runtime_result("explore.turn_context", request)["graph"]["measurement_feedback"]
    assert feedback[0]["best"]["evidence_ref"] == "result:a"
    assert feedback[0]["measured_count"] == 1
    # Correcting an already corrected finding remains an ordinary strict write.
    fields = {key: corrected[key] for key in ("goal_id", "title", "node_id", "status", "evidence_refs",
        "execution_observation", "measurements")}
    again = build_explore_finding_event(**fields, finding_id="measured", recorded_at="2026-01-01T03:00:00Z")
    append_explore_result_event(log, again)
    assert len(load_explore_result_events_strict(log, goal_id="research")) == 4
    assert len(_history_rows(runtime, "research")) == 4
    assert log.read_bytes().startswith(original)
    # Explicit archival prefix replay retains old facts and the correction;
    # it grants neither execution nor quantitative validity to the old row.
    replay = tmp_path / "replayed.jsonl"
    restored = append_explore_result_events(replay, events, expected_goal_id="research", require_ordered_prefix=True)
    assert restored["appended_event_count"] == 3
    assert replay.read_bytes() == original
    repeated = append_explore_result_events(replay, events, expected_goal_id="research", require_ordered_prefix=True)
    assert repeated["appended_event_count"] == 0
    assert repeated["reused_event_count"] == 3


def test_historical_read_still_rejects_forged_canonical_records(tmp_path):
    event = json.loads(FIXTURE.read_text().splitlines()[1])
    event["event_id"] = "forged"
    log = tmp_path / "results.jsonl"
    log.write_text(json.dumps(event) + "\n")
    with pytest.raises(ValueError, match="canonical"):
        load_explore_result_events_strict(log, goal_id="research")
