from pathlib import Path

from loopx import state_refresh


def test_refresh_output_projections_preserve_optional_run_contracts() -> None:
    delta_contract = {
        "schema_version": "repair_delta_contract_v0",
        "delta_present": True,
    }
    agent_vision = {
        "schema_version": "goal_vision_v0",
        "agent_id": "quality-agent",
        "state": "vision_active",
        "vision_patch": {"acceptance_summary": "Keep simplification measurable."},
        "todo_delta": ["Simplify the projection owner."],
        "vision_budget": {"status": "within_budget"},
        "fallback_declarations": None,
        "path_delta": {"changed": ["projection assembly"]},
        "validation": {"budget_checked": True},
    }
    record = {
        "generated_at": "2026-07-31T00:00:00+00:00",
        "goal_id": "projection-fixture",
        "classification": "validated_progress",
        "recommended_action": "continue the measured simplification",
        "recommended_action_source": "explicit_arg",
        "health_check": "state_file 1/1",
        "state": {
            "path": "/ignored/project/state.md",
            "sha256_16": "0123456789abcdef",
            "frontmatter": {
                "updated_at": "2026-07-30T23:59:00+00:00",
                "status": "active",
            },
            "next_action": ["continue"],
        },
        "runtime_projection_route": {"status": "resolved", "route_id": "route-1"},
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
        "delivery_workspace": {"workspace_kind": "independent_git_worktree"},
        "autonomous_replan_ack": {
            "recorded": True,
            "requested": True,
            "requested_classification": "repair_completed",
            "delta_contract": delta_contract,
        },
        "agent_vision": agent_vision,
        "vision_checkpoint": {"decision": "patched", "satisfied": True},
        "progress_scope": "agent_lane",
        "agent_id": "quality-agent",
        "agent_lane": "quality",
        "active_state_next_action_update": {"updated": True},
    }

    index_record, payload = state_refresh._build_state_refresh_output_projections(
        record=record,
        registry_path=Path("/registry.json"),
        runtime_root=Path("/runtime"),
        project=Path("/project"),
        json_path=Path("/runtime/run.json"),
        markdown_path=Path("/runtime/run.md"),
        index_path=Path("/runtime/index.jsonl"),
        dry_run=False,
        autonomous_replan_recorded_requested=True,
    )

    assert index_record["state"] == {
        "sha256_16": "0123456789abcdef",
        "frontmatter": {"updated_at": "2026-07-30T23:59:00+00:00"},
    }
    assert index_record["requested_classification"] == "repair_completed"
    expected_vision = {
        key: agent_vision[key]
        for key in (
            "schema_version",
            "agent_id",
            "state",
            "vision_patch",
            "todo_delta",
            "fallback_declarations",
            "vision_budget",
            "path_delta",
        )
    }
    # The run-index contract carries an explicit null for the optional,
    # default-off stride decision. All original fields remain unchanged.
    expected_vision["stride_decision"] = None
    assert index_record["agent_vision"] == expected_vision
    assert "validation" not in index_record["agent_vision"]
    for field in (
        "delivery_batch_scale",
        "delivery_outcome",
        "delivery_workspace",
        "autonomous_replan_ack",
        "vision_checkpoint",
        "progress_scope",
        "agent_id",
        "agent_lane",
    ):
        assert index_record[field] == record[field]

    assert payload["appended"] is True
    assert payload["autonomous_replan_recorded"] is True
    assert payload["autonomous_replan_recorded_requested"] is True
    assert payload["repair_delta_contract"] is delta_contract
    assert payload["active_state_next_action_update"] == {"updated": True}
    assert payload["agent_vision"] is agent_vision
    assert payload["state"] is record["state"]


def test_run_index_agent_vision_keeps_fallback_declarations(tmp_path):
    """The run index must persist typed fallback declarations for later reads."""

    from loopx.state_refresh import _build_state_refresh_output_projections

    record = {
        "schema_version": "loopx_goal_run_v0",
        "generated_at": "2026-09-07T00:00:00+00:00",
        "goal_id": "goal-1",
        "agent_id": "agent-a",
        "turn_instance_id": "turn-1",
        "observed_at": "2026-09-07T00:00:00Z",
        "classification": "validated_progress",
        "recommended_action": "continue",
        "recommended_action_source": "explicit_arg",
        "health_check": "state_file 1/1",
        "state": {"frontmatter": {"updated_at": "2026-09-06T23:59:00+00:00"}},
        "runtime_projection_route": {"status": "resolved", "route_id": "route-1"},
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "outcome_progress",
        "delivery_workspace": {"workspace_kind": "independent_git_worktree"},
        "agent_vision": {
            "schema_version": "loopx_goal_vision_packet_v0",
            "agent_id": "agent-a",
            "state": "active",
            "vision_patch": "Primary path; fallback direction declared.",
            "todo_delta": [],
            "fallback_declarations": [
                {"declaration_id": "decl_fallback_1", "target_todo_id": "todo_fb1"}
            ],
            "vision_budget": {},
        },
    }
    registry = tmp_path / "registry.global.json"
    registry.write_text("{}", encoding="utf-8")
    runtime_root = tmp_path / "runtime"
    json_path = runtime_root / "goals" / "goal-1" / "runs" / "run.json"
    markdown_path = runtime_root / "goals" / "goal-1" / "runs" / "run.md"
    index_path = runtime_root / "goals" / "goal-1" / "runs" / "index.jsonl"
    _record, index_record = _build_state_refresh_output_projections(
        record=record,
        registry_path=registry,
        runtime_root=runtime_root,
        project=tmp_path,
        json_path=json_path,
        markdown_path=markdown_path,
        index_path=index_path,
        dry_run=True,
        autonomous_replan_recorded_requested=False,
    )
    indexed_vision = index_record.get("agent_vision") or {}
    assert indexed_vision.get("fallback_declarations") == [
        {"declaration_id": "decl_fallback_1", "target_todo_id": "todo_fb1"}
    ]
