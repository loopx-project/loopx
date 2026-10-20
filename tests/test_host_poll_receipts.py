from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from loopx.control_plane.quota.host_poll_receipts import (
    HOST_POLL_RECEIPT_DIRNAME,
    HOST_POLL_RECEIPT_SCHEMA_VERSION,
    receipt_path_for_goal,
    read_host_poll_receipt,
    read_host_poll_receipt_observation,
    read_host_worker_observations,
    record_host_poll_receipt,
    stale_host_poll_risk,
)


def terminal_decision() -> dict[str, Any]:
    return {
        "should_run": False,
        "effective_action": "terminal_no_followup",
        "goal_frontier_projection": {
            "terminal_state": {"kind": "no_followup"},
        },
    }


def run_now_decision() -> dict[str, Any]:
    return {"should_run": True, "effective_action": "normal_run"}


def test_receipt_path_sits_beside_the_goal_state_file() -> None:
    goal = {"id": "goal/one", "state_file": "state/goal-one.md", "repo": "/repo"}
    path = receipt_path_for_goal(goal, Path("/repo/state/goal-one.md"))
    assert path is not None
    assert path.parent.name == HOST_POLL_RECEIPT_DIRNAME
    assert path.parent.parent == Path("/repo/state")
    assert "goal_one" in path.name


def test_receipt_path_fails_closed_without_state_path() -> None:
    assert receipt_path_for_goal({"id": "goal-1"}, None) is None
    assert receipt_path_for_goal({"state_file": "x.md"}, Path("/tmp/x.md")) is None


def test_record_and_read_receipt_round_trip(tmp_path: Path) -> None:
    state_path = tmp_path / "goals" / "goal-1.md"
    goal = {"id": "goal-1", "state_file": "goals/goal-1.md", "repo": str(tmp_path)}
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    receipt = record_host_poll_receipt(
        goal,
        state_path,
        agent_id="agent-1",
        decision=run_now_decision(),
        now=now,
    )
    assert receipt is not None
    assert receipt["schema_version"] == HOST_POLL_RECEIPT_SCHEMA_VERSION
    assert receipt["expected_continuation"] is True
    assert receipt["poll_count"] == 1
    path = receipt_path_for_goal(goal, state_path)
    assert path is not None and path.exists()
    reread = read_host_poll_receipt(path)
    assert reread == receipt


def test_receipt_increments_poll_count_and_marks_terminal_stops(tmp_path: Path) -> None:
    state_path = tmp_path / "goals" / "goal-1.md"
    goal = {"id": "goal-1", "state_file": "goals/goal-1.md", "repo": str(tmp_path)}
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    first = record_host_poll_receipt(
        goal, state_path, agent_id=None, decision=run_now_decision(), now=now
    )
    second = record_host_poll_receipt(
        goal,
        state_path,
        agent_id=None,
        decision=terminal_decision(),
        now=now + timedelta(minutes=1),
    )
    assert first is not None and second is not None
    assert second["poll_count"] == 2
    assert second["expected_continuation"] is False
    assert second["last_decision_action"] == "terminal_no_followup"


def test_read_receipt_rejects_foreign_schemas(tmp_path: Path) -> None:
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps({"schema_version": "other"}), encoding="utf-8")
    assert read_host_poll_receipt(path) is None
    path.write_text("{broken", encoding="utf-8")
    assert read_host_poll_receipt(path) is None
    assert read_host_poll_receipt(tmp_path / "missing.json") is None


@pytest.mark.parametrize("damage", [b"{broken", b"\xff", b'{"schema_version":"other"}',
    b'{"schema_version":"host_poll_receipt_v0","goal_id":"other"}'])
def test_poll_never_erases_unreadable_source(tmp_path, damage):
    goal, state = {"id": "goal-1"}, tmp_path / "state.md"
    original = record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(),
        worker_observation={"status": "replan_outcome_unknown", "obligation_id": "replan-original",
            "turn_instance_id": "original", "registry_goal_digest": "current-goal",
            "runtime_root": str(tmp_path.resolve())})
    path = receipt_path_for_goal(goal, state)
    path.write_bytes(damage)
    for observation in ({}, {"worker_observation": None}):
        with pytest.raises(ValueError, match="unreadable"):
            record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(), **observation)
        assert path.read_bytes() == damage
        assert read_host_worker_observations(goal, state, registry_goal_digest="current-goal",
            runtime_root=tmp_path)["read_status"] == "unreadable"
    # A source repair, not an ordinary liveness poll, restores safe updates.
    path.write_text(json.dumps(original))
    restored = record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision())
    assert restored["worker_observations"] == original["worker_observations"]


def test_read_receipt_observation_distinguishes_missing_and_unreadable(
    tmp_path: Path,
) -> None:
    path = tmp_path / "receipt.json"
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "missing",
        "receipt": None,
    }

    path.write_text("{broken", encoding="utf-8")
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "unreadable",
        "receipt": None,
    }

    path.write_text(
        json.dumps(
            {
                "schema_version": HOST_POLL_RECEIPT_SCHEMA_VERSION,
                "goal_id": "goal-1",
                "last_poll_at": "not-a-timestamp",
            }
        ),
        encoding="utf-8",
    )
    assert read_host_poll_receipt_observation(path) == {
        "read_status": "unreadable",
        "receipt": None,
    }


def test_stale_host_poll_risk_only_for_quiet_expecting_loops() -> None:
    now = datetime(2026, 8, 13, 10, 0, 0, tzinfo=timezone.utc)
    stale = {
        "schema_version": HOST_POLL_RECEIPT_SCHEMA_VERSION,
        "goal_id": "goal-1",
        "agent_id": "agent-1",
        "poll_count": 7,
        "last_poll_at": (now - timedelta(hours=6)).isoformat(),
        "expected_continuation": True,
    }
    risk = stale_host_poll_risk(stale, now=now)
    assert risk is not None
    assert risk["kind"] == "stale_host_poll"
    assert risk["age_minutes"] == 360.0
    fresh = {**stale, "last_poll_at": (now - timedelta(minutes=5)).isoformat()}
    assert stale_host_poll_risk(fresh, now=now) is None
    stopped = {**stale, "expected_continuation": False}
    assert stale_host_poll_risk(stopped, now=now) is None
    assert stale_host_poll_risk({"expected_continuation": True, "last_poll_at": "not-a-date"}, now=now) is None


def test_worker_observations_survive_polls_and_only_their_agent_can_clear(tmp_path):
    goal, state = {"id": "goal-1"}, tmp_path / "state.md"
    runtime = tmp_path / "runtime"
    observed = {
        "status": "replan_outcome_unknown", "obligation_id": "replan-original",
        "turn_instance_id": "original-turn", "registry_goal_digest": "current-goal",
        "runtime_root": str(runtime.resolve()),
    }
    first = record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(),
                                     worker_observation=observed)
    assert first["expected_continuation"] is False
    record_host_poll_receipt(goal, state, agent_id="agent-2", decision=run_now_decision(),
                             worker_observation={**observed, "status": "replan_paused"})
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision())
    def read():
        return read_host_worker_observations(goal, state, registry_goal_digest="current-goal", runtime_root=runtime)

    rows = read()["rows"]
    assert [row["agent_id"] for row in rows] == ["agent-1", "agent-2"]
    assert rows[0]["observed_at"] == first["last_poll_at"]
    assert all("runtime_root" not in row and "registry_goal_digest" not in row for row in rows)
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(), worker_observation=None)
    assert [row["agent_id"] for row in read()["rows"]] == ["agent-2"]
    record_host_poll_receipt(goal, state, agent_id="agent-2", decision=run_now_decision(), worker_observation=None)
    assert read() is None


@pytest.mark.parametrize("damage", ["wrong-goal", "wrong-runtime", "wrong-digest", "bad-status", "bad-map", "bad-time", "invalid-utf8", "invalid-json", "wrong-schema"])
def test_worker_observation_scope_and_damage_remain_visible_unknown(tmp_path, damage):
    goal, state = {"id": "goal-1"}, tmp_path / "state.md"
    runtime = tmp_path / "runtime"
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(),
        worker_observation={"status": "replan_paused", "obligation_id": "replan-original",
            "turn_instance_id": None, "registry_goal_digest": "current-goal", "runtime_root": str(runtime.resolve())})
    path = receipt_path_for_goal(goal, state)
    receipt = json.loads(path.read_text())
    row = receipt["worker_observations"]["agent-1"]
    expected = "unreadable"
    if damage == "wrong-goal":
        receipt["goal_id"] = "different-goal"
    elif damage == "wrong-runtime":
        row["runtime_root"] = str(tmp_path / "different-runtime")
        expected = "stale"
    elif damage == "wrong-digest":
        row["registry_goal_digest"] = "old-goal"
        expected = "stale"
    elif damage == "bad-status":
        row["status"] = ["replan_paused"]
    elif damage == "bad-map":
        receipt["worker_observations"] = ["damaged"]
    elif damage == "bad-time":
        row["observed_at"] = "2026-01-01T00:00:00"
    elif damage == "wrong-schema":
        receipt["schema_version"] = "unrecognized-receipt"
    contents = b"\xff" if damage == "invalid-utf8" else b"{broken" if damage == "invalid-json" else json.dumps(receipt).encode()
    path.write_bytes(contents)
    read = read_host_worker_observations(goal, state, registry_goal_digest="current-goal", runtime_root=runtime)
    assert read == {"goal_id": "goal-1", "read_status": expected, "rows": []}
    if damage in {"wrong-goal", "invalid-utf8", "invalid-json", "wrong-schema"}:
        with pytest.raises(ValueError, match="unreadable"):
            record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision())
    else:
        record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision())
    assert read_host_worker_observations(goal, state, registry_goal_digest="current-goal", runtime_root=runtime) == read
    if damage in {"wrong-goal", "invalid-utf8", "invalid-json", "wrong-schema"}:
        assert path.read_bytes() == contents, "A liveness poll cannot repair an unreadable source"


def test_new_unknown_source_does_not_fall_back_to_legacy_goal_bytes(tmp_path):
    goal, state, runtime = {"id": "goal-1"}, tmp_path / "state.md", tmp_path / "runtime"
    worker = {"status": "replan_paused", "obligation_id": "replan-original",
        "turn_instance_id": None, "registry_goal_digest": "current-goal", "runtime_root": str(runtime)}
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(), worker_observation=worker)
    kwargs = {"registry_goal_digest": "current-goal", "runtime_root": runtime, "source_goal_observation_digest": None}
    legacy = read_host_worker_observations(goal, state, **kwargs)
    assert legacy["read_status"] == "read"  # Existing records retain their narrower original association.
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(),
        worker_observation={**worker, "source_goal_observation_digest": None})
    assert read_host_worker_observations(goal, state, **kwargs) == {"goal_id": "goal-1", "read_status": "stale", "rows": []}


def test_poll_observation_keys_reuse_the_core_agent_normalizer(tmp_path):
    goal, state = {"id": "goal-1"}, tmp_path / "state.md"
    observed = {"status": "replan_outcome_unknown", "obligation_id": "replan-original"}
    first = record_host_poll_receipt(goal, state, agent_id="AGENT-1", decision=run_now_decision(),
                                     worker_observation=observed)
    assert first["agent_id"] == "agent-1"
    assert set(first["worker_observations"]) == {"agent-1"}
    polled = record_host_poll_receipt(goal, state, agent_id="AGENT-1", decision=run_now_decision())
    assert polled["expected_continuation"] is False
    assert polled["worker_observations"] == first["worker_observations"]


def test_ordinary_receipts_do_not_read_worker_sources(tmp_path):
    goal, state = {"id": "goal-1"}, tmp_path / "state.md"

    def unexpected_source_read():
        pytest.fail("a receipt without worker rows must not read another registry")

    assert read_host_worker_observations(goal, state, registry_goal_digest="unused",
        source_goal_observation_digest=unexpected_source_read, runtime_root=tmp_path) is None
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision())
    assert read_host_worker_observations(goal, state, registry_goal_digest="unused",
        source_goal_observation_digest=unexpected_source_read, runtime_root=tmp_path) is None


@pytest.mark.parametrize("clear_attention", [False, True])
def test_existing_sync_materialization_keeps_the_same_source_witness(tmp_path, clear_attention):
    from loopx.global_registry import merge_goal_entries, sanitize_goal_for_global
    from loopx.history import host_worker_goal_observation_digest

    source, mirror = tmp_path / "registry.json", tmp_path / "global.json"
    goal = {"id": "goal-1", "quota": {"compute": 1}}
    if clear_attention:
        goal["clear_attention_override"] = True
    source.write_text(json.dumps({"goals": [goal]}))
    witness = host_worker_goal_observation_digest(goal, registry_path=source)
    rows = []
    for timestamp in ("first-sync", "next-sync"):
        rows, *_ = merge_goal_entries(rows, [sanitize_goal_for_global(
            goal, source_registry=source, synced_at=timestamp)])
        assert host_worker_goal_observation_digest(rows[0], registry_path=mirror) == witness


def test_worker_source_witness_rejects_a_chain_of_registry_projections(tmp_path):
    from loopx.global_registry import sanitize_goal_for_global
    from loopx.history import host_worker_goal_observation_digest

    origin, source, mirror = (tmp_path / name for name in ("origin.json", "source.json", "global.json"))
    goal = {"id": "goal-1", "quota": {"compute": 1}}
    origin.write_text(json.dumps({"goals": [goal]}))
    projected_source = sanitize_goal_for_global(goal, source_registry=origin, synced_at="first")
    source.write_text(json.dumps({"goals": [projected_source]}))
    twice_projected = sanitize_goal_for_global(projected_source, source_registry=source, synced_at="second")
    assert host_worker_goal_observation_digest(twice_projected, registry_path=mirror) is None


@pytest.mark.parametrize("change", ["source-config", "source-instance", "source-route", "source-missing", "source-unreadable", "mirror-config", "runtime", "missing-witness"])
def test_worker_observation_never_reuses_a_changed_source_or_scope(tmp_path, change):
    from loopx.global_registry import sanitize_goal_for_global
    from loopx.history import goal_registry_digest, host_worker_goal_observation_digest

    source, mirror = tmp_path / "registry.json", tmp_path / "global.json"
    runtime, state = tmp_path / "runtime", tmp_path / "state.md"
    goal = {"id": "goal-1", "goal_instance_id": "ginst_" + "a" * 32,
            "repo": str(tmp_path), "state_file": state.name, "quota": {"compute": 1}}

    def write_source():
        source.write_text(json.dumps({"goals": [goal]}))

    write_source()
    witness = host_worker_goal_observation_digest(goal, registry_path=source)
    assert witness
    materialized = sanitize_goal_for_global(goal, source_registry=source, synced_at="first-sync")
    assert host_worker_goal_observation_digest(materialized, registry_path=mirror) == witness
    record_host_poll_receipt(goal, state, agent_id="agent-1", decision=run_now_decision(),
        worker_observation={"status": "replan_outcome_unknown", "obligation_id": "replan-original",
                            "turn_instance_id": "original-turn", "registry_goal_digest": goal_registry_digest(goal),
                            "source_goal_observation_digest": witness,
                            "runtime_root": str(runtime)})
    if change == "source-config":
        goal["quota"]["compute"] = 0
        write_source()
        # Even a newly refreshed mirror must reject the original observation.
        materialized = sanitize_goal_for_global(goal, source_registry=source, synced_at="next-sync")
    elif change == "source-instance":
        goal["goal_instance_id"] = "ginst_" + "b" * 32
        write_source()
    elif change == "source-route":
        other_source = tmp_path / "different-source.json"
        other_source.write_bytes(source.read_bytes())
        materialized = sanitize_goal_for_global(goal, source_registry=other_source, synced_at="next-sync")
    elif change == "source-missing":
        source.unlink()
    elif change == "source-unreadable":
        source.write_bytes(b"{broken")
    elif change == "mirror-config":
        materialized["quota"]["compute"] = 0
    elif change == "runtime":
        runtime = tmp_path / "different-runtime"
    digest = None if change == "missing-witness" else host_worker_goal_observation_digest(materialized, registry_path=mirror)
    assert read_host_worker_observations(materialized, state, registry_goal_digest=goal_registry_digest(materialized),
        source_goal_observation_digest=digest, runtime_root=runtime) == {
        "goal_id": "goal-1", "read_status": "stale", "rows": []}
