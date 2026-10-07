from __future__ import annotations

from pathlib import Path
import hashlib
import json
import subprocess
import sys

import pytest

from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_finding_event,
    build_explore_edge_event,
    build_explore_node_event,
    build_explore_result_projection,
    load_explore_result_events_strict,
    validate_explore_result_event,
)
from loopx.capabilities.explore.source_history_reconcile import (
    reconcile_explore_source_history,
)


GOAL_ID = "source-history-fixture"


def _rehash(event):
    stable = {key: value for key, value in event.items() if key != "event_id"}
    event["event_id"] = hashlib.sha256(
        json.dumps(stable, sort_keys=True, ensure_ascii=True).encode()
    ).hexdigest()[:16]
    return event


def _persisted_event(kind, length):
    common = dict(goal_id=GOAL_ID, recorded_at="2026-01-01T00:00:00Z")
    if kind == "edge":
        event = build_explore_edge_event(
            **common, from_node="scope", to_node="next", edge_type="refutes"
        )
    elif kind == "node":
        event = build_explore_node_event(
            **common, node_id="scope", title="Scoped result", status="blocked",
            blocked_reason="Needs independent validation",
        )
    else:
        event = build_explore_finding_event(
            **common, node_id="scope", title="Scoped counterexample", status="refuted"
        )
    tail = " Do not transfer beyond the tested input."
    event["summary"] = "x" * (length - len(tail)) + tail
    if kind == "node":
        event["blocked_reason"] = event["summary"]
    return _rehash(event)


@pytest.mark.parametrize("kind", ["node", "edge", "finding"])
@pytest.mark.parametrize("length", [1200, 1202, 1275, 1968, 2000, 2002])
def test_persisted_summary_budget_does_not_recompact_source_truth(tmp_path, kind, length):
    event = _persisted_event(kind, length)
    log = tmp_path / "results.jsonl"
    original = json.dumps(event) + "\n"
    log.write_text(original)
    assert load_explore_result_events_strict(log, goal_id=GOAL_ID) == [event]
    assert log.read_text() == original
    assert event["summary"].endswith("Do not transfer beyond the tested input.")


@pytest.mark.parametrize("kind", ["node", "edge", "finding"])
def test_persisted_text_compatibility_is_independent_of_writer_budget(monkeypatch, kind):
    from loopx.capabilities.explore import result_log

    event = _persisted_event(kind, 2002)
    monkeypatch.setattr(result_log, "SUMMARY_LIMIT", 1200)
    assert validate_explore_result_event(event, expected_goal_id=GOAL_ID) == event


def test_current_writer_overflow_is_readable_without_rewriting(tmp_path):
    event = build_explore_node_event(
        goal_id=GOAL_ID, title="Bounded source", status="blocked",
        summary="x" * 2003, blocked_reason="y" * 2003,
        recorded_at="2026-01-01T00:00:00Z",
    )
    assert event["summary"] == "x" * 1999 + "..."
    assert event["blocked_reason"] == "y" * 1999 + "..."
    log = tmp_path / "results.jsonl"
    original = json.dumps(event) + "\n"
    log.write_text(original)
    assert load_explore_result_events_strict(log, goal_id=GOAL_ID) == [event]
    assert log.read_text() == original


@pytest.mark.parametrize("damage", ["over-budget", "private-tail", "whitespace",
                                  "unknown-field", "forged-boundary", "stale-id"])
def test_expanded_persisted_reader_still_rejects_invalid_events(damage):
    event = _persisted_event("finding", 1968)
    if damage == "over-budget":
        event["summary"] = "x" * 2003
    elif damage == "private-tail":
        event["summary"] += " to" + "ken=" + "synthetic-value"
    elif damage == "whitespace":
        event["summary"] += "\n"
    elif damage == "unknown-field":
        event["raw_transcript"] = "not permitted"
    elif damage == "forged-boundary":
        event["boundary"]["raw_logs_recorded"] = True
    else:
        event["event_id"] = "stale"
    if damage != "stale-id":
        _rehash(event)
    with pytest.raises(ValueError):
        validate_explore_result_event(event, expected_goal_id=GOAL_ID)


def test_long_persisted_result_survives_real_cli_readback(tmp_path):
    from tests.capabilities.test_explore_turn_context import registry
    from loopx.capabilities.explore.result_log import explore_result_log_path
    from loopx.todos import add_goal_todo, update_goal_todo

    path = registry(tmp_path, graph=True, planning=True)
    root = tmp_path / "runtime"
    todo = add_goal_todo(
        registry_path=path, goal_id="research", text="Qualify the transfer route",
        role="agent", claimed_by="worker", agent_id="worker",
    )
    update_goal_todo(
        registry_path=path, goal_id="research", todo_id=todo["todo_id"],
        agent_id="worker", explore_result_node_refs=["scope"],
    )
    node = build_explore_node_event(
        goal_id="research", node_id="scope", title="Scoped result",
    )
    finding = _persisted_event("finding", 2002)
    finding.update(goal_id="research", agent_id="worker", tags=["writeback-result"])
    _rehash(finding)
    log = explore_result_log_path(root, "research")
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(json.dumps(node) + "\n" + json.dumps(finding) + "\n")
    original = log.read_bytes()
    prefix = [sys.executable, "-m", "loopx.cli", "--registry", str(path),
              "--runtime-root", str(root), "--format", "json", "explore"]
    for command in ["summary", "turn-context"]:
        argv = [*prefix, command, "--goal-id", "research"]
        if command == "turn-context":
            argv += ["--agent-id", "worker"]
        result = subprocess.run(argv, capture_output=True, text=True)
        assert result.returncode == 0, result.stdout
        packet = json.loads(result.stdout)
        assert packet["ok"]
        if command == "summary":
            assert finding["summary"] in result.stdout
        else:
            assert packet["graph"]["writeback_results"][0]["summary"] == finding["summary"][:2000]
            assert "summary" in packet["graph"]["summary_command"]
        assert log.read_bytes() == original


def _append(path: Path, *events: dict[str, object]) -> None:
    for event in events:
        append_explore_result_event(path, event)


def _projection_keys(path: Path) -> set[str]:
    events = load_explore_result_events_strict(path, goal_id=GOAL_ID)
    projection = build_explore_result_projection(
        events,
        goal_id=GOAL_ID,
        finding_limit=len(events),
        mermaid_node_limit=max(1, len(events)),
    )
    keys: set[str] = set()
    for table, id_key in (
        ("nodes", "node_id"),
        ("edges", "edge_id"),
        ("findings", "finding_id"),
    ):
        keys.update(f"{GOAL_ID}:{table}:{row[id_key]}" for row in projection[table])
    return keys


def test_event_validation_rejects_unknown_private_fields() -> None:
    event = build_explore_node_event(
        goal_id=GOAL_ID,
        node_id="node_one",
        title="Public fixture node",
    )
    event["raw_transcript"] = "must not cross the boundary"

    with pytest.raises(ValueError, match="not canonical"):
        validate_explore_result_event(event, expected_goal_id=GOAL_ID)


def test_reconcile_recovers_lost_results_and_classifies_stale_parent_edge(
    tmp_path: Path,
) -> None:
    canonical = tmp_path / "canonical.jsonl"
    candidate = tmp_path / "candidate.jsonl"
    parent_old = build_explore_node_event(
        goal_id=GOAL_ID,
        node_id="parent_old",
        title="Earlier parent",
        recorded_at="2026-01-01T00:00:00Z",
    )
    parent_new = build_explore_node_event(
        goal_id=GOAL_ID,
        node_id="parent_new",
        title="Current parent",
        recorded_at="2026-01-01T00:00:01Z",
    )
    child_old = build_explore_node_event(
        goal_id=GOAL_ID,
        node_id="child",
        title="Moved child",
        parent_id="parent_old",
        recorded_at="2026-01-01T00:00:02Z",
    )
    child_new = build_explore_node_event(
        goal_id=GOAL_ID,
        node_id="child",
        title="Moved child",
        parent_id="parent_new",
        recorded_at="2026-01-02T00:00:00Z",
    )
    lost_node = build_explore_node_event(
        goal_id=GOAL_ID,
        node_id="lost_node",
        title="Public-safe lost node",
        parent_id="parent_old",
        recorded_at="2026-01-01T00:00:03Z",
    )
    lost_finding = build_explore_finding_event(
        goal_id=GOAL_ID,
        finding_id="lost_finding",
        node_id="lost_node",
        title="Public-safe lost finding",
        recorded_at="2026-01-01T00:00:04Z",
    )
    _append(canonical, parent_old, parent_new, child_new)
    _append(
        candidate,
        parent_old,
        parent_new,
        child_old,
        lost_node,
        lost_finding,
    )
    registered = _projection_keys(canonical) | _projection_keys(candidate)

    preview = reconcile_explore_source_history(
        canonical_log_path=canonical,
        candidate_log_path=candidate,
        goal_id=GOAL_ID,
        registered_result_keys=registered,
    )

    assert preview["status"] == "would_reconcile_with_remote_orphans"
    assert preview["plan"]["selected_event_count"] == 2
    assert preview["plan"]["raw_history_copied"] is False
    assert preview["projection_reconciliation"]["recovered_lost_history_count"] == 3
    assert preview["projection_reconciliation"]["remaining_registered_result_count"] == 1
    assert preview["classification"]["stale_materialized_parent_edge_count"] == 1
    assert preview["classification"]["remote_deletion_performed"] is False
    assert len(load_explore_result_events_strict(canonical, goal_id=GOAL_ID)) == 3

    executed = reconcile_explore_source_history(
        canonical_log_path=canonical,
        candidate_log_path=candidate,
        goal_id=GOAL_ID,
        registered_result_keys=registered,
        execute=True,
    )

    assert executed["status"] == "reconciled_with_remote_orphans"
    assert executed["writeback"] == {
        "performed": True,
        "requested_event_count": 2,
        "appended_event_count": 2,
        "reused_event_count": 0,
        "readback_verified": True,
    }
    assert len(load_explore_result_events_strict(canonical, goal_id=GOAL_ID)) == 5

    repeated = reconcile_explore_source_history(
        canonical_log_path=canonical,
        candidate_log_path=candidate,
        goal_id=GOAL_ID,
        registered_result_keys=registered,
        execute=True,
    )

    assert repeated["status"] == "already_reconciled_with_remote_orphans"
    assert repeated["plan"]["selected_event_count"] == 0
    assert repeated["writeback"]["performed"] is False
    assert len(load_explore_result_events_strict(canonical, goal_id=GOAL_ID)) == 5
