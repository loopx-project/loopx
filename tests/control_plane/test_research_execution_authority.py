"""Execution evidence uses the real Todo provider, never a stale display row."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.capabilities.explore.research_evidence import append_research_observation
from loopx.capabilities.explore.result_log import (
    append_explore_result_event, build_explore_edge_event, build_explore_node_event,
    build_explore_result_projection, explore_result_log_path, load_explore_result_events_strict,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.todos import complete_goal_todo
from loopx.capabilities.explore.research_frontier import build_research_composition_frontier, prepare_research_replan_evidence


def observation(node: str) -> dict:
    return {
        "schema_version": "typed_research_observation_v0", "explore_node_id": node,
        "progress": {"schema_version": "typed_progress_observation_v0", "work_item_id": f"todo_{node}",
                     "result_class": "exploration_exhausted", "coverage_scope_id": f"scope-{node}",
                     "coverage_complete": True, "evidence_ids": [f"ev-{node}"]},
        "closure_basis": {"schema_version": "research_closure_basis_v0", "disposition": "bounded",
                          "constraints": [{"kind": "invariant", "id": "boundary", "role": "decisive"}],
                          "evidence_ids": [f"ev-{node}"]},
    }


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("canonical_owner", ["fixture-agent", "other-agent"])
def test_execution_attribution_reads_promoted_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, canonical_owner: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    try:
        goal = "research-provider-fixture"
        state = tmp_path / "ACTIVE_GOAL_STATE.md"
        state.write_text("# Goal\n\n## Agent Todo\n\n")
        runtime = tmp_path / "runtime"
        registry = tmp_path / "registry.json"
        registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
            "id": goal, "repo": str(tmp_path), "state_file": state.name, "status": "active",
            "coordination": {"agent_model": "peer_v1", "registered_agents": ["fixture-agent", "other-agent"]},
        }]}))
        task = {
            "schema_version": "todo_item_v0", "todo_id": "todo_joint", "index": 1,
            "role": "agent", "status": "open", "done": False, "text": "Run the bounded joint experiment.",
            "archive_state": "active", "source_section": "Agent Todo", "priority": "P1",
            "claimed_by": canonical_owner, "task_class": "advancement_task", "action_kind": "joint_probe",
            "target_key": "joint", "explore_result_node_refs": ["joint"],
            "replan_obligation_id": "replan-0123456789abcdef",
        }
        initialize_canonical_authority(runtime, goal,
            build_todo_runtime_shadow_projection(goal_id=goal, todos=[task]), state_path=state, provider=provider)
        # Even a plausible display claim is not the promoted source of truth.
        state.write_text("# Goal\n\n## Agent Todo\n\n- [ ] Stale display\n"
                         "  <!-- loopx:todo todo_id=todo_joint status=open claimed_by=fixture-agent -->\n")
        log = explore_result_log_path(runtime, goal)
        for name in ["a", "b", "joint"]:
            append_explore_result_event(log, build_explore_node_event(
                goal_id=goal, node_id=name, title=f"Research {name}", status="resolved",
                node_kind="experiment" if name == "joint" else "hypothesis"))
        append_research_observation(log, goal_id=goal, observation=observation("b"))
        source = observation("a")
        source["composition_candidates"] = [{"basis": "explicit", "target_node_id": "b",
                                             "interaction_kind": "state_interference", "evidence_ids": ["ev-a", "ev-b"]}]
        append_research_observation(log, goal_id=goal, observation=source)
        for name in ["a", "b"]:
            append_explore_result_event(log, build_explore_edge_event(
                goal_id=goal, from_node="joint", to_node=name, edge_type="depends_on"))
        view = build_explore_result_projection(load_explore_result_events_strict(log, goal_id=goal), goal_id=goal)
        gap = view["research_frontier"]["gaps"][0]
        result = observation("joint")
        result["input_observations"] = gap["input_observations"]
        result["execution_lineage"] = {
            "schema_version": "research_execution_lineage_v0", "goal_id": goal, "gap_id": gap["gap_id"],
            "replan_obligation_id": task["replan_obligation_id"], "successor_todo_id": "todo_joint", "agent_id": "fixture-agent",
        }
        before = log.read_bytes(), state.read_bytes()
        if canonical_owner == "fixture-agent":
            receipt = append_research_observation(log, goal_id=goal, observation=result, agent_id="fixture-agent",
                                                 registry_path=registry, runtime_root=runtime)
            assert receipt["written"]
        else:
            with pytest.raises(ValueError, match="same-agent runnable"):
                append_research_observation(log, goal_id=goal, observation=result, agent_id="fixture-agent",
                                           registry_path=registry, runtime_root=runtime)
            assert log.read_bytes() == before[0]
        assert state.read_bytes() == before[1]
    finally:
        restart_effect_runtime()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("resolution", ["observed", "dismissed", "deferred"])
@pytest.mark.parametrize("diagnostic_timing", [None, "before_activation", "after_activation"])
def test_native_completion_refuses_missing_result_and_accepts_exact_observation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, resolution: str, diagnostic_timing: str | None,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    try:
        goal, agent = "research-native-fixture", "fixture-agent"
        state = tmp_path / "ACTIVE_GOAL_STATE.md"
        state.write_text("# Goal\n\n## Agent Todo\n\n")
        runtime, registry = tmp_path / "runtime", tmp_path / "registry.json"
        harness = {"enabled": True, "composition_mode": "explicit_only", "composition_scope_id": "scope-joint"}
        registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
            "id": goal, "repo": str(tmp_path), "state_file": state.name, "status": "active",
            "spawn_policy": {"explore_harness": {"enabled": True} if diagnostic_timing == "before_activation" else harness},
            "coordination": {"agent_model": "peer_v1", "registered_agents": [agent]},
        }]}))
        def cli(*args: str, success: bool = True) -> dict:
            process = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--format", "json",
                "--registry", str(registry), "--runtime-root", str(runtime), *args],
                cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=60)
            assert (process.returncode == 0) is success, process.stdout + process.stderr
            return json.loads(process.stdout)

        packet = tmp_path / "observation.json"
        def record(value: dict, *, success: bool = True) -> dict:
            packet.write_text(json.dumps(value))
            return cli("explore", "observe", "--goal-id", goal, "--agent-id", agent,
                       "--observation-json", str(packet), success=success)

        log = explore_result_log_path(runtime, goal)
        for name in ["a", "b", "joint"]:
            append_explore_result_event(log, build_explore_node_event(
                goal_id=goal, node_id=name, title=f"Research {name}",
                status="open" if name == "joint" else "resolved",
                node_kind="experiment" if name == "joint" else "hypothesis"))
        append_research_observation(log, goal_id=goal, observation=observation("b"))
        source = observation("a")
        source["composition_candidates"] = [{"basis": "explicit", "target_node_id": "b",
            "interaction_kind": "state_interference", "evidence_ids": ["ev-a", "ev-b"]}]
        append_research_observation(log, goal_id=goal, observation=source)
        for name in ["a", "b"]:
            append_explore_result_event(log, build_explore_edge_event(
                goal_id=goal, from_node="joint", to_node=name, edge_type="depends_on"))
        if diagnostic_timing:
            append_explore_result_event(log, build_explore_node_event(goal_id=goal, node_id="diagnostic",
                title="Retained diagnostic", node_kind="experiment", status="resolved"))
            for name in ["a", "b"]:
                append_explore_result_event(log, build_explore_edge_event(goal_id=goal,
                    from_node="diagnostic", to_node=name, edge_type="depends_on"))
            cold = build_explore_result_projection(load_explore_result_events_strict(log, goal_id=goal), goal_id=goal)
            diagnostic = observation("diagnostic")
            diagnostic["input_observations"] = cold["research_frontier"]["gaps"][0]["input_observations"]
            assert record(diagnostic)["written"]
            if diagnostic_timing == "before_activation":
                assert cli("configure-goal", "--goal-id", goal, "--explore-composition-mode", "explicit_only",
                           "--explore-composition-scope-id", "scope-joint", "--execute")["ok"]
        events = load_explore_result_events_strict(log, goal_id=goal)
        projection = build_explore_result_projection(events, goal_id=goal)
        frontier = build_research_composition_frontier(projection,
            candidate_sources=[{"node_id": e["result_id"], "research_observation": e["research_observation"]}
                               for e in events if e.get("research_observation")],
            harness=harness, todos=[], agent_id=agent)
        gap = frontier["selected_gap"]
        assert gap["status"] == "pending"
        if diagnostic_timing:
            assert projection["research_frontier"]["observed_count"] == 1
            assert frontier["observed_count"] == 0
        task = {"schema_version": "todo_item_v0", "todo_id": "todo_joint", "index": 1,
                "role": "agent", "status": "open", "done": False, "text": "Run the bounded joint experiment.",
                "archive_state": "active", "source_section": "Agent Todo", "priority": "P1",
                "claimed_by": agent, "task_class": "advancement_task", "action_kind": "joint_probe",
                "target_key": "joint", "explore_result_node_refs": ["joint"], "replan_obligation_id": gap["obligation_id"]}
        initialize_canonical_authority(runtime, goal,
            build_todo_runtime_shadow_projection(goal_id=goal, todos=[task]), state_path=state, provider=provider)
        live = cli("explore", "summary", "--goal-id", goal, "--agent-id", agent)["research_execution_frontier"]
        assert live["scheduled_count"] == 1 and live["observed_count"] == 0
        from loopx.control_plane.work_items.task_lease import acquire_task_lease
        acquired = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id=goal,
            todo_id="todo_joint", owner=agent, idempotency_key="native-research-proof", ttl_seconds=300)
        assert acquired["ok"]
        from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted, LocalCoordinationAuthorityUnavailable
        from loopx.control_plane.todos import provider_terminal_lifecycle as terminal_adapter
        requests = []
        native_call = terminal_adapter.effect_runtime_result
        def capture_native(method, params, **kwargs):
            if method == "coordination.local_authority.todo_terminal":
                requests.append(json.loads(json.dumps(params)))
            return native_call(method, params, **kwargs)
        monkeypatch.setattr(terminal_adapter, "effect_runtime_result", capture_native)
        before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        with pytest.raises(ValueError, match="current typed experiment observation"):
            complete_goal_todo(registry_path=registry, goal_id=goal, todo_id="todo_joint",
                               agent_id=agent, claimed_by=agent, no_followup=True, evidence="Synthetic result required.",
                               task_lease_idempotency_key="native-research-proof", task_lease_expected_version=acquired["lease"]["version"])
        forged = {**requests[-1], "operation_identity": {"kind": "explicit", "operation_id": "native-forged-approval"},
                  "capability_completion_evidence": {"approved": True}}
        direct = native_call("coordination.local_authority.todo_terminal", forged)
        assert direct["changed"] is False
        assert direct["reason_code"] == "research_experiment_result_required"
        retired = native_call("coordination.local_authority.todo_terminal", {**forged, "command": "supersede",
            "operation_identity": {"kind": "explicit", "operation_id": "native-supersede-without-result"},
            "requested_no_followup": False, "reason": "Attempt to close through another verb.", "completion_policy_request": None})
        assert retired["changed"] is False
        assert retired["reason_code"] == "research_experiment_result_required"
        after = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        assert after["provider_revision"] == before["provider_revision"]
        assert after["todos"][0]["status"] == "open"
        append_explore_result_event(log, build_explore_node_event(goal_id=goal, node_id="joint",
                                    title="Research joint", node_kind="experiment",
                                    status={"observed": "resolved", "dismissed": "dead_end", "deferred": "blocked"}[resolution],
                                    blocked_reason="An identified dependency remains open." if resolution == "deferred" else None))
        result = observation("joint")
        result["input_observations"] = gap["input_observations"]
        result["execution_lineage"] = {"schema_version": "research_execution_lineage_v0", "goal_id": goal,
            "gap_id": gap["gap_id"], "replan_obligation_id": gap["obligation_id"], "successor_todo_id": "todo_joint", "agent_id": agent}
        if resolution == "dismissed":
            result["progress"]["result_class"] = "no_followup"
            result["closure_basis"]["disposition"] = "no_followup"
            result["composition_resolution"] = {"schema_version": "research_composition_resolution_v0",
                "disposition": "dismissed", "basis": "outside_scope", "evidence_ids": ["ev-joint"]}
        elif resolution == "deferred":
            from loopx.todos import add_goal_todo
            blocker = add_goal_todo(registry_path=registry, goal_id=goal, role="agent",
                text="Resolve the bounded dependency.", task_class="blocker", action_kind="investigate",
                claimed_by=agent, agent_id=agent, unblocks_todo_id="todo_joint")
            result["progress"].update(result_class="blocked", coverage_complete=False, blocker_id=blocker["todo_id"])
            result["closure_basis"] = None
            result["composition_resolution"] = {"schema_version": "research_composition_resolution_v0",
                "disposition": "deferred", "evidence_ids": ["ev-joint"]}
        recorded = record(result)
        written = log.read_bytes()
        replay = record(result)
        assert replay["replayed"] and not replay["written"]
        assert log.read_bytes() == written
        if resolution != "deferred":
            changed = json.loads(json.dumps(result))
            changed["progress"]["evidence_ids"].append("ev-new")
            assert "pending gap" in json.dumps(record(changed, success=False))
            assert log.read_bytes() == written
        if resolution == "deferred":
            from loopx.todos import update_goal_todo
            from loopx.capabilities.explore.composition_frontier import project_live_explore_composition_frontier
            from loopx.control_plane.work_items.semantic_replan_writeback import qualify_replan_writeback
            from loopx.control_plane.work_items.task_lease import release_task_lease
            with pytest.raises(LocalCoordinationAuthorityUnavailable, match="Release the active execution lease"):
                update_goal_todo(registry_path=registry, goal_id=goal, todo_id="todo_joint", agent_id=agent,
                    status="blocked", resume_when=f"todo_done:{blocker['todo_id']}", reason="Await the bounded dependency.")
            released = release_task_lease(registry_path=registry, runtime_root=runtime, goal_id=goal,
                todo_id="todo_joint", owner=agent, idempotency_key="native-research-proof",
                expected_version=acquired["lease"]["version"])
            assert released["ok"]
            update_goal_todo(registry_path=registry, goal_id=goal, todo_id="todo_joint", agent_id=agent,
                status="blocked", resume_when=f"todo_done:{blocker['todo_id']}", reason="Await the bounded dependency.")
            source = json.loads(registry.read_text())["goals"][0]
            def live_deferred():
                return project_live_explore_composition_frontier(runtime_root=runtime, goal_id=goal,
                    agent_id=agent, status_payload={"run_history": {"goals": [source]}})
            assert live_deferred()["deferred_count"] == 1
            _, delta = qualify_replan_writeback(newest_first_runs=[], state_text=state.read_text(), agent_id=agent,
                goal_id=goal, registry_goal=source, guard_scoped=True,
                capability_evidence=prepare_research_replan_evidence(runtime_root=runtime, goal_id=goal,
                    agent_id=agent, registry_goal=source, state_text=state.read_text()),
                guard_semantic_replan_obligation_id=gap["obligation_id"], progress_observation=recorded["observation"]["progress"],
                guard_capability={"capability_id": "explore", "gap_id": gap["gap_id"], "frontier_revision": gap["frontier_revision"]})
            assert delta["capability_outcome"] == "composition_temporarily_deferred"
            revision = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)["provider_revision"]
            with pytest.raises(ValueError):
                complete_goal_todo(registry_path=registry, goal_id=goal, todo_id="todo_joint", agent_id=agent,
                    no_followup=True, task_lease_idempotency_key="native-research-proof",
                    task_lease_expected_version=acquired["lease"]["version"])
            assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)["provider_revision"] == revision
            dependency_lease = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id=goal,
                todo_id=blocker["todo_id"], owner=agent, idempotency_key="dependency-resolved", ttl_seconds=300)
            complete_goal_todo(registry_path=registry, goal_id=goal, todo_id=blocker["todo_id"], agent_id=agent,
                no_followup=True, evidence="Dependency resolved in the synthetic fixture.",
                task_lease_idempotency_key="dependency-resolved", task_lease_expected_version=dependency_lease["lease"]["version"])
            assert live_deferred()["deferred_count"] == 0
            assert live_deferred()["pending_count"] == 1
            update_goal_todo(registry_path=registry, goal_id=goal, todo_id="todo_joint", agent_id=agent,
                status="open", clear_resume_when=True, reason="Dependency resolved; resume this experiment.")
            append_explore_result_event(log, build_explore_node_event(goal_id=goal, node_id="joint",
                title="Research joint", node_kind="experiment", status="open"))
            assert live_deferred()["scheduled_count"] == 1
            assert live_deferred()["observed_count"] == 0
            reacquired = acquire_task_lease(registry_path=registry, runtime_root=runtime, goal_id=goal,
                todo_id="todo_joint", owner=agent, idempotency_key="native-research-resumed", ttl_seconds=300)
            assert reacquired["ok"]
            assert reacquired["lease"]["version"] > acquired["lease"]["version"]
            return
        accepted = complete_goal_todo(registry_path=registry, goal_id=goal, todo_id="todo_joint",
                                     agent_id=agent, claimed_by=agent, clear_claim=True, no_followup=True, evidence="Typed synthetic result recorded.",
                                     task_lease_idempotency_key="native-research-proof", task_lease_expected_version=acquired["lease"]["version"])
        assert accepted["completed"]
        assert accepted["capability_completion_evidence"]["experiment_node_id"] == "joint"
        completed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        assert completed["todos"][0]["status"] == "done"
        assert completed["todos"][0].get("claimed_by") is None
        from loopx.control_plane.todos.provider_terminal_lifecycle import archive_canonical_todos_if_promoted
        archived = archive_canonical_todos_if_promoted(registry_path=registry, runtime_root=runtime,
            goal_id=goal, role="agent", max_active_done=0, dry_run=False)
        assert archived["moved_todo_ids"] == ["todo_joint"]
        completed = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        assert completed["todos"][0]["archive_state"] == "archive"
        from loopx.capabilities.explore.composition_frontier import project_live_explore_composition_frontier
        def live():
            return project_live_explore_composition_frontier(runtime_root=runtime, goal_id=goal,
                agent_id=agent, status_payload={"run_history": {"goals": json.loads(registry.read_text())["goals"]}})
        assert live()[f"{resolution}_count"] == 1
        assert live()["scheduled_count"] == 0
        cli_view = cli("explore", "summary", "--goal-id", goal, "--agent-id", agent)["research_execution_frontier"]
        assert cli_view[f"{resolution}_count"] == 1 and cli_view["scheduled_count"] == 0
        append_explore_result_event(log, build_explore_node_event(goal_id=goal, node_id="a", title="Research a", status="open"))
        assert live()["observed_count"] == 0
        replay = complete_goal_todo(registry_path=registry, goal_id=goal, todo_id="todo_joint",
            agent_id=agent, claimed_by=agent, clear_claim=True, no_followup=True, evidence="Historical typed result remains immutable.",
            task_lease_idempotency_key="native-research-proof", task_lease_expected_version=acquired["lease"]["version"])
        assert replay["provider_status"] == "replayed"
        assert replay["capability_completion_evidence"] == accepted["capability_completion_evidence"]
        latest = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        assert latest["provider_revision"] == completed["provider_revision"]
    finally:
        restart_effect_runtime()
