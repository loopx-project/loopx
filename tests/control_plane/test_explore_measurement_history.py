"""Base-writer history remains auditable after stricter new-write attribution."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
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
REGISTERED_FIXTURE = FIXTURE.with_name("explore_registered_measurement_history_v0.jsonl")
DELIVERY_FIXTURE = FIXTURE.with_name("explore_committed_measurement_delivery_v0.json")


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


def test_registered_history_recovers_eligible_subset_without_promoting_legacy_measurements(tmp_path):
    from loopx.capabilities.explore.source_history_reconcile import reconcile_explore_source_history

    # The real writer at 21367015 produced these bytes, including two old
    # contradictions and an independent valid finding. No head writer repairs them.
    original = REGISTERED_FIXTURE.read_bytes()
    assert hashlib.sha256(original).hexdigest() == "ef4d31cd7a71efb5b6745cea832de21c797dc58ece03eebb79aeb93f1c9e55eb"
    events = [json.loads(line) for line in original.splitlines()]
    candidate = tmp_path / "candidate.jsonl"
    candidate.write_bytes(original)
    canonical = tmp_path / "canonical.jsonl"
    prefix = original.splitlines(keepends=True)[0]
    canonical.write_bytes(prefix)
    args = dict(canonical_log_path=canonical, candidate_log_path=candidate, goal_id="research",
        registered_result_keys={"research:nodes:question-a", "research:findings:valid",
                                "research:findings:mixed", "research:findings:mixed-later"})
    preview = reconcile_explore_source_history(**args)
    assert preview["status"] == "would_reconcile_with_blocked_history"
    assert preview["projection_reconciliation"]["recovered_lost_history_count"] == 1
    assert preview["plan"]["blocked_event_count"] == 2
    assert canonical.read_bytes() == prefix
    receipt = reconcile_explore_source_history(**args, execute=True)
    assert receipt["status"] == "reconciled_with_blocked_history"
    assert receipt["writeback"]["appended_event_count"] == 1
    assert receipt["writeback"]["readback_verified"] is True
    restored = canonical.read_bytes()
    assert load_explore_result_events_strict(canonical, goal_id="research") == events[:2]
    assert restored.startswith(prefix)
    assert candidate.read_bytes() == original
    repeated = reconcile_explore_source_history(**args, execute=True)
    assert repeated["status"] == "blocked"
    assert repeated["writeback"]["performed"] is False
    projection = build_explore_result_projection(events, goal_id="research")
    assert sum("measurement_error" in row for row in projection["findings"]) == 2
    request = dict(goal_id="research", agent_id="worker", route=["loopx"], graph_enabled=True,
        harness_gate={"enabled": True}, projection=projection, plan={})
    feedback = effect_runtime_result("explore.turn_context", request)["graph"]["measurement_feedback"]
    assert feedback[0]["measured_count"] == 1
    assert feedback[0]["best"]["value"] == 8
    with pytest.raises(ValueError, match="executed candidate's result"):
        append_explore_result_events(canonical, events[1:], expected_goal_id="research")
    assert canonical.read_bytes() == restored
    corrected = {key: events[2][key] for key in ("goal_id", "title", "node_id", "status",
        "evidence_refs", "execution_observation")}
    correction = build_explore_finding_event(**corrected, finding_id="mixed",
        recorded_at="2026-01-01T04:00:00Z", measurements=[{**events[2]["measurements"][0],
            "value": 7, "evidence_ref": "result:a"}])
    append_explore_result_event(canonical, correction)
    assert canonical.read_bytes().startswith(restored)
    request["projection"] = build_explore_result_projection(
        load_explore_result_events_strict(canonical, goal_id="research"), goal_id="research")
    group = effect_runtime_result("explore.turn_context", request)["graph"]["measurement_feedback"][0]
    assert (group["measured_count"], group["best"]["value"], group["latest"]["measurement"]["value"],
            group["improvement"]) == (2, 8, 7, -1)


@pytest.mark.parametrize("damage", ["concurrent-event", "incomplete-tail"])
def test_registered_history_rechecks_canonical_snapshot_under_lock(tmp_path, monkeypatch, damage):
    from loopx.capabilities.explore import source_history_reconcile as owner

    candidate = tmp_path / "candidate.jsonl"
    candidate.write_bytes(REGISTERED_FIXTURE.read_bytes())
    canonical = tmp_path / "canonical.jsonl"
    prefix = candidate.read_bytes().splitlines(keepends=True)[0]
    canonical.write_bytes(prefix)
    append = owner._append_validated_explore_result_events
    raced = []
    def race(path, events, **kwargs):
        if damage == "concurrent-event":
            append_explore_result_event(path, build_explore_finding_event(goal_id="research",
                title="Concurrent independent result", finding_id="concurrent",
                node_id="question-a", recorded_at="2026-01-01T05:00:00Z"))
        else:
            path.write_bytes(prefix + b'{"incomplete":')
        raced.append(path.read_bytes())
        return append(path, events, **kwargs)
    monkeypatch.setattr(owner, "_append_validated_explore_result_events", race)
    with pytest.raises(ValueError):
        owner.reconcile_explore_source_history(canonical_log_path=canonical,
            candidate_log_path=candidate, goal_id="research", execute=True,
            registered_result_keys={"research:nodes:question-a", "research:findings:valid",
                                    "research:findings:mixed", "research:findings:mixed-later"})
    assert canonical.read_bytes() == raced[0]
    assert candidate.read_bytes() == REGISTERED_FIXTURE.read_bytes()


def _legacy_delivery(tmp_path, provider):
    from tests.capabilities.test_explore_turn_context import registry
    from loopx.todos import add_goal_todo, update_goal_todo

    raw = DELIVERY_FIXTURE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == "35566a01d64695661d8f2db38234f43d614894301054a9c277abecf4a940ae13"
    legacy = json.loads(raw)
    path = registry(tmp_path, graph=True, planning=True, provider=provider)
    todo = add_goal_todo(registry_path=path, goal_id="research", role="agent", agent_id="worker",
        claimed_by="worker", text="Recover the recorded candidate result",
        operation_id="explore-legacy-writeback-fixture")
    assert todo["todo_id"] == legacy["todo_id"]
    update_goal_todo(registry_path=path, goal_id="research", todo_id=todo["todo_id"],
        agent_id="worker", append_explore_result_node_refs=["question-a"])
    root = tmp_path / "runtime"
    log = explore_result_log_path(root, "research")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(legacy["events_jsonl"], encoding="utf-8")
    for relative, content in legacy["hook_files"].items():
        receipt = root / relative
        receipt.parent.mkdir(parents=True, exist_ok=True)
        receipt.write_text(content, encoding="utf-8")
    return legacy, dict(registry_path=path, runtime_root=root, goal_id="research", agent_id="worker",
        todo_id=todo["todo_id"], turn_instance_id=legacy["turn_instance_id"]), log


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_base_committed_delivery_replays_read_only(tmp_path, provider, monkeypatch):
    from loopx.capabilities.explore import result_writeback
    from loopx.todos import list_goal_todos

    legacy, args, log = _legacy_delivery(tmp_path, provider)
    before = log.read_bytes()
    todo_args = {key: args[key] for key in ("registry_path", "goal_id", "todo_id")}
    todo_before = list_goal_todos(**todo_args)["todo"]
    hook_before = {relative: (args["runtime_root"] / relative).read_bytes()
                   for relative in legacy["hook_files"]}
    def reject_mutation(**_):
        pytest.fail("completed delivery must not mutate the Todo or append graph events")
    monkeypatch.setattr(result_writeback, "update_goal_todo", reject_mutation)
    monkeypatch.setattr(result_writeback, "append_explore_result_events", reject_mutation)
    for supplement in (False, True):
        payload = deepcopy(legacy["payload"])
        if supplement:
            payload["explore_result_recorded_at"] = payload["generated_at"]
            payload["generated_at"] = "2026-01-02T00:00:00Z"
        replay = result_writeback.deliver_result_attachment(payload=payload, **args)
        assert replay["ok"] and replay["idempotent_replay"] and not replay["retryable"], replay
        assert replay["hook"]["invoked_count"] == 0
        assert replay["hook"]["replayed_hooks"] == ["explore.result_writeback"]
        assert log.read_bytes() == before
        assert list_goal_todos(**todo_args)["todo"] == todo_before
        assert {relative: (args["runtime_root"] / relative).read_bytes()
                for relative in hook_before} == hook_before


@pytest.mark.parametrize("mutation", ["receipt", "source", "time", "attachment", "canonical", "link"])
def test_legacy_replay_requires_exact_receipt_event_and_todo_link(tmp_path, mutation):
    from loopx.capabilities.explore import result_writeback
    from loopx.todos import update_goal_todo, list_goal_todos

    legacy, args, log = _legacy_delivery(tmp_path, "file")
    payload = deepcopy(legacy["payload"])
    if mutation == "receipt":
        for relative in legacy["hook_files"]:
            (args["runtime_root"] / relative).unlink()
    elif mutation == "source":
        payload["settlement_identity"]["effect_id"] = "different-source"
    elif mutation == "time":
        payload["generated_at"] = "2026-01-02T00:00:00Z"
    elif mutation == "attachment":
        payload["explore_result"]["interpretation"] = "A conflicting retry cannot rewrite the receipt."
    elif mutation == "canonical":
        log.write_text(legacy["events_jsonl"].splitlines()[0] + "\n")
    else:
        config = json.loads(args["registry_path"].read_text())
        config["goals"][0]["coordination"]["registered_agents"].append("other")
        args["registry_path"].write_text(json.dumps(config))
        update_goal_todo(registry_path=args["registry_path"], goal_id="research", agent_id="worker",
            todo_id=args["todo_id"], explore_result_node_refs=[])
        # An unfinished association must still obey current ownership.
        update_goal_todo(registry_path=args["registry_path"], goal_id="research", agent_id="worker",
            todo_id=args["todo_id"], claimed_by="other")
    before = log.read_bytes()
    todo_args = {key: args[key] for key in ("registry_path", "goal_id", "todo_id")}
    todo_before = list_goal_todos(**todo_args)["todo"]
    result = result_writeback.deliver_result_attachment(payload=payload, **args)
    assert not result["ok"] and result["primary_committed"], result
    assert log.read_bytes() == before
    assert list_goal_todos(**todo_args)["todo"] == todo_before
