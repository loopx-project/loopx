"""Learned zero novelty reaches the public planner without becoming a prior."""
import json

from loopx.capabilities.explore.router_state import initial_router_state, observe_epoch
from loopx.control_plane.testing.canary_harness import import_fixture_authority, run_json_cli


def test_worker_plan_cli_preserves_depleted_evidence(tmp_path):
    goal_id = "worker-evidence-fixture"
    project = tmp_path / "project"
    project.mkdir()
    state_file = project / "ACTIVE_GOAL_STATE.md"
    state_file.write_text(
        "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        "# Synthetic exploration\n\n## Agent Todo\n\n"
        "- [ ] [P0] Inspect the fresh artifact.\n"
        "  <!-- loopx:todo todo_id=todo_fresh status=open task_class=advancement_task "
        "required_write_scopes=artifacts/fresh/** -->\n"
        "- [ ] [P1] Inspect the depleted artifact.\n"
        "  <!-- loopx:todo todo_id=todo_depleted status=open task_class=advancement_task "
        "required_write_scopes=artifacts/depleted/** -->\n",
        encoding="utf-8",
    )
    runtime_root = tmp_path / "runtime"
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({
        "schema_version": 1,
        "common_runtime_root": str(runtime_root),
        "goals": [{
            "id": goal_id, "status": "active", "repo": str(project),
            "state_file": str(state_file),
            "adapter": {"kind": "explore_result_layer", "status": "connected"},
            "quota": {"compute": 1, "window_hours": 24},
            "spawn_policy": {
                "mode": "default", "allowed": False, "max_children": 2,
                "explore_harness": {"enabled": True, "profile": "moe-router"},
            },
        }],
    }), encoding="utf-8")
    import_fixture_authority(registry_path, goal_id, provider="sqlite")
    router_path = tmp_path / "router.json"
    original_state = state_file.read_bytes()
    original_registry = registry_path.read_bytes()

    # Default exploration smoothing stays positive. An explicitly zero floor
    # may discount repeated evidence fully; it must not increase admission.
    for floor in [0.05, 0.0]:
        router = initial_router_state({"novelty_floor": floor})
        for epoch in [1, 2]:
            router = observe_epoch(router, epoch=epoch, probes=[
                {
                    "family": f"scope:artifacts/{family}", "duration_minutes": 1.0,
                    "observation_keys": [f"{family}-{epoch if family == 'fresh' else 1}"],
                    "weighted_flags": {}, "accepted": True,
                }
                for family in ["fresh", "depleted"]
            ])
        assert router["families"]["scope:artifacts/depleted"]["novelty_rate"] == 0.0
        router_path.write_text(json.dumps(router), encoding="utf-8")
        original_router = router_path.read_bytes()
        result = run_json_cli(
            "explore", "worker-branch-plan", "--goal-id", goal_id,
            "--worker-width", "2", "--max-todos-per-branch", "1",
            "--router-state", str(router_path),
            registry_path=registry_path, runtime_root=runtime_root,
        )
        branches = result["selected_worker_branches"] + result["rejected_worker_branches"]
        depleted = next(row for row in branches if row.get("affinity_key") == "scope:artifacts/depleted")
        step = next(row for row in result["scheduler"]["steps"] if row["todo_id"] == depleted["branch_id"])
        assert step["expected_evidence_units"] == depleted["novelty_adjusted_evidence_units"]
        assert (step["expected_evidence_units"] > 0.0) == (floor > 0.0)
        assert [row["todo_ids"] for row in result["selected_worker_branches"]] == [["todo_fresh"]]
        assert result["orchestration_gate"]["state"] == "analysis_only"
        assert all(not row["suggested_commands"] for row in branches)
        assert router_path.read_bytes() == original_router
    assert state_file.read_bytes() == original_state
    assert registry_path.read_bytes() == original_registry
