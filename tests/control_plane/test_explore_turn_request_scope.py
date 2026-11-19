"""Explore reads share one runtime revision, never one cross-request result."""
import json

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime

from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_finding_event,
    build_explore_node_event,
    explore_result_log_path,
)
from loopx.capabilities.explore.turn_context import explore_turn_context
from loopx.control_plane import effect_runtime
from tests.control_plane.test_native_todo_planning_update import fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("focused", [False, True])
def test_explore_read_pins_runtime_once_and_rechecks_after_success_or_failure(
    tmp_path, monkeypatch, provider, focused,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, _ = fixture(tmp_path, provider)
    config = json.loads(registry.read_text())
    config["goals"][0].update(explore_graph={"enabled": True}, spawn_policy={
        "allowed": False, "explore_harness": {"enabled": True},
    })
    registry.write_text(json.dumps(config))
    runtime = tmp_path / "runtime"
    log = explore_result_log_path(runtime, "goal-a")
    append_explore_result_event(log, build_explore_node_event(
        goal_id="goal-a", node_id="question-a", title="Synthetic question",
        node_kind="question", agent_id="agent-a",
    ))

    def finding(result_id):
        append_explore_result_event(log, build_explore_finding_event(
            goal_id="goal-a", node_id="question-a", finding_id=result_id,
            title="Observed result", status="tentative", agent_id="agent-a",
        ))

    original = effect_runtime._runtime_fingerprint
    snapshots = []

    def observe_snapshot():
        value = original()
        snapshots.append(value)
        return value

    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", observe_snapshot)

    def read(expected_ids):
        before = len(snapshots)
        result = explore_turn_context(registry_path=registry, runtime_root=runtime,
                                      goal_id="goal-a", agent_id="agent-a",
                                      **({"todo_id": "todo_target"} if focused else {}))
        assert {row["finding_id"] for row in result["graph"]["recent_findings"]} == expected_ids
        assert len(snapshots) - before == 1
        assert effect_runtime._REQUEST_RUNTIME_REVISION.get() is None

    finding("first")
    read({"first"})
    finding("second")
    read({"first", "second"})
    healthy = log.read_bytes()
    log.write_bytes(healthy + b"{broken\n")
    with pytest.raises(ValueError, match="invalid Explore result JSON"):
        explore_turn_context(registry_path=registry, runtime_root=runtime,
                             goal_id="goal-a", agent_id="agent-a")
    assert effect_runtime._REQUEST_RUNTIME_REVISION.get() is None
    log.write_bytes(healthy)
    read({"first", "second"})
