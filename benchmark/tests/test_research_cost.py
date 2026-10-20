"""Passive cost readback uses captured transport facts and the real TS owner."""
from __future__ import annotations

import copy
import json

import pytest

from benchmark.edgebench.research_cost import summarize_research_cost
from benchmark.edgebench.research_feedback import SCHEMA


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _lines(path, events):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _collected(trial, names=("runtime", "native-sessions", "worker-sessions")):
    _write(trial / "collected/artifact-collection.json",
           [{"artifact": name, "collected": True} for name in names])


def _session(session_id):
    def usage(i, c, o):
        return {"input_tokens": i, "cached_input_tokens": c, "output_tokens": o}

    rows = [
        {"type": "session_meta", "payload": {"id": session_id}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "a"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": usage(100, 60, 10), "last_token_usage": usage(100, 60, 10)}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "a"}},
        {"type": "event_msg", "payload": {"type": "task_started", "turn_id": "b"}},
        {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": usage(140, 80, 15), "last_token_usage": usage(40, 20, 5)}}},
        {"type": "event_msg", "payload": {"type": "task_complete", "turn_id": "b"}},
    ]
    for index, row in enumerate(rows):
        row["timestamp"] = f"2026-01-01T00:00:0{index}Z"
    return rows


def test_missing_captures_are_unknown_and_wall_is_not_a_sum_of_stages(tmp_path):
    report = summarize_research_cost(tmp_path, 123.5)
    assert report["runner_wall_seconds"] == 123.5
    assert report["stage_seconds"] is None and report["billing_usd"] is None
    assert report["provider_requests"]["capture_coverage"] == "unknown"
    assert report["provider_requests"]["captured_distinct_requests"] is None
    assert report["provider_requests"]["forwarded"] is None
    assert report["tokens"]["known_captured_totals"] is None
    assert report["tokens"]["capture_coverage"] == "unknown"
    assert report["evaluations"]["manual_accepted_count"] is None
    assert report["evaluations"]["native_total_rounds"] is None
    assert summarize_research_cost(tmp_path, float("nan"))["runner_wall_seconds"] is None


def test_gateway_replicas_dedup_and_interrupted_dispatch_remains_unknown(tmp_path):
    _collected(tmp_path)
    events = [
        {"request_id": "private-forwarded", "event": "dispatch", "forwarded": None},
        {"request_id": "private-forwarded", "event": "finished", "forwarded": True},
        {"request_id": "private-refused", "event": "finished", "forwarded": False},
        {"request_id": "private-lost", "event": "dispatch", "forwarded": None},
        {"request_id": "private-maybe", "event": "dispatch", "forwarded": None},
        {"request_id": "private-maybe", "event": "finished", "forwarded": None},
        {"request_id": "private-conflict", "event": "finished", "forwarded": True},
        {"request_id": "private-conflict", "event": "finished", "forwarded": False},
    ]
    _lines(tmp_path / "collected/runtime/agent/first/provider-requests.jsonl", events)
    _lines(tmp_path / "collected/runtime/agent/replica/provider-requests.jsonl", events)
    result = summarize_research_cost(tmp_path, 1)
    counts = result["provider_requests"]
    assert counts["captured_distinct_requests"] == 5
    assert (counts["forwarded"], counts["not_forwarded"], counts["send_unknown"]) == (1, 1, 3)
    assert "private-" not in json.dumps(result)


def test_unreadable_capture_is_not_zero_but_an_observed_empty_log_can_be(tmp_path):
    _collected(tmp_path)
    source = tmp_path / "outside.jsonl"
    _lines(source, [{"request_id": "outside", "event": "finished", "forwarded": True}])
    path = tmp_path / "collected/runtime/agent/provider-requests.jsonl"
    path.parent.mkdir(parents=True)
    path.symlink_to(source)
    counts = summarize_research_cost(tmp_path, 1)["provider_requests"]
    assert counts["capture_coverage"] == "unknown"
    assert counts["forwarded"] is None
    path.unlink()
    _lines(path, [])
    counts = summarize_research_cost(tmp_path, 1)["provider_requests"]
    assert counts["capture_coverage"] == "complete" and counts["forwarded"] == 0


def test_real_usage_owner_dedups_same_and_prefix_captures_across_runtime_homes(tmp_path):
    _collected(tmp_path)
    session = _session("private-native-session")
    # A retained prefix, worker home and copied runtime are the same session.
    _lines(tmp_path / "collected/native-sessions/sessions/old.jsonl", session[:4])
    _lines(tmp_path / "collected/worker-sessions/sessions/full.jsonl", session)
    _lines(tmp_path / "collected/runtime/agent/loopx/sessions/replica.jsonl", session)
    _lines(tmp_path / "collected/runtime/agent/other/sessions/independent.jsonl",
           _session("private-independent-session")[:4])
    # Non-session runtime JSONL is not interpreted as a token capture.
    _lines(tmp_path / "collected/runtime/agent/decisions.jsonl", [{"not": "a rollout"}])
    report = summarize_research_cost(tmp_path, 420)
    tokens = report["tokens"]
    assert tokens["capture_coverage"] == "complete"
    assert tokens["whole_run_coverage"] == "unknown"
    assert tokens["qualified_sessions"] == 2 and tokens["duplicate_or_prefix_captures"] == 2
    assert tokens["known_captured_totals"] == {
        "input_tokens": 240, "cached_input_tokens": 140, "uncached_input_tokens": 100,
        "output_tokens": 25, "input_plus_output_tokens": 265,
    }
    assert "private-" not in json.dumps(report) and str(tmp_path) not in json.dumps(report)
    _collected(tmp_path, ("runtime",))
    assert summarize_research_cost(tmp_path, 420)["tokens"]["capture_coverage"] == "partial"


@pytest.mark.parametrize("case", ["divergent", "forked", "unfinished", "counter_gap"])
def test_ambiguous_or_incomplete_session_never_adds_an_unproven_subtotal(tmp_path, case):
    _collected(tmp_path)
    rows = _session("ambiguous-session")
    if case == "divergent":
        other = copy.deepcopy(rows)
        other[-1]["timestamp"] = "2026-01-01T00:00:07Z"
        _lines(tmp_path / "collected/worker-sessions/sessions/divergence.jsonl", other)
    elif case == "forked":
        rows[0]["payload"]["forked_from_id"] = "parent-session"
    elif case == "unfinished":
        # The longest capture is unfinished; do not fall back to an older
        # completed prefix and call the missing second Turn free.
        _lines(tmp_path / "collected/worker-sessions/sessions/older.jsonl", rows[:4])
        rows = rows[:-1]
    else:
        rows[5]["payload"]["info"]["total_token_usage"]["input_tokens"] = 999
    _lines(tmp_path / "collected/native-sessions/sessions/unknown.jsonl", rows)
    _lines(tmp_path / "collected/runtime/agent/sessions/known.jsonl", _session("known")[:4])
    tokens = summarize_research_cost(tmp_path, 1)["tokens"]
    assert tokens["capture_coverage"] == "partial" and tokens["qualified_sessions"] == 1
    assert tokens["unknown_sessions_or_files"] == 1
    assert tokens["known_captured_totals"]["input_tokens"] == 100
    assert tokens["known_captured_totals"]["input_plus_output_tokens"] == 110


def test_native_acceptance_order_precedes_release_order_and_total_uses_native_report(tmp_path):
    first = {"submission_id": "private-first", "round_id": "agent-1", "accepted_record": True,
             "status": "running", "attribution_status": "verified"}
    second = {"submission_id": "private-second", "round_id": "agent-2", "accepted_record": True,
              "status": "completed", "attribution_status": "verified", "result_ref": "native-result:abc",
              "result_summary": {"score": 4}}
    packet = {"schema_version": SCHEMA, "run_id": "private-run", "task_id": "private-task",
              "records": [second, first, second]}
    target = tmp_path / "native-research-feedback/native.json"
    _write(target, packet)
    _write(tmp_path / "final_result.json", {"total_rounds": 5, "private": "not part of cost"})
    evaluations = summarize_research_cost(tmp_path, 1)["evaluations"]
    assert evaluations["manual_accepted_count"] == 2 and evaluations["native_total_rounds"] == 5
    assert evaluations["first_accepted"] == {"acceptance_ordinal": 1, "released": False, "result_summary": None}
    first.update(status="completed", result_ref="native-result:def", result_summary={
        "score": 0, "valid": False, "feedback": "private model feedback", "passed": 0})
    _write(target, packet)
    report = summarize_research_cost(tmp_path, 1)
    assert report["evaluations"]["first_accepted"]["result_summary"] == {"score": 0, "valid": False, "passed": 0}
    assert "private" not in json.dumps(report)
    # A disagreement about an accepted identity must not double count it or
    # select the earlier-released second candidate as the first submission.
    packet["records"].append({**first, "round_id": "agent-3"})
    _write(target, packet)
    evaluations = summarize_research_cost(tmp_path, 1)["evaluations"]
    assert evaluations["capture_coverage"] == "partial" and evaluations["manual_accepted_count"] == 1
    assert evaluations["first_accepted"] is None


def test_bad_native_scopes_and_round_collisions_do_not_invent_counts(tmp_path):
    target = tmp_path / "native-research-feedback/native.json"
    _write(target, {"schema_version": SCHEMA, "run_id": 123, "task_id": "t", "records": []})
    assert summarize_research_cost(tmp_path, 1)["evaluations"]["manual_accepted_count"] is None
    _write(target, {"schema_version": SCHEMA, "run_id": "r", "task_id": "t", "records": [
        {"accepted_record": True, "submission_id": "a", "round_id": "agent-1"},
        {"accepted_record": True, "submission_id": "b", "round_id": "agent-1"},
    ]})
    result = summarize_research_cost(tmp_path, 1)["evaluations"]
    assert result["capture_coverage"] == "partial" and result["first_accepted"] is None
