from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from loopx.chat_goal_configuration_api import _goal_capability_options
from loopx.configure_goal import configure_goal
from loopx.control_plane.goals.goal_frontier.outcome_continuity import (
    acceptance_gaps_from_todo_completion_checkpoint,
)
from loopx.execution_profile import (
    build_execution_profile,
    compact_execution_profile,
    execution_profile_with_turn_granularity,
)


@pytest.mark.parametrize("invalid", [0, -1, 6, True, 2.5, "3", None])
def test_invalid_cadence_is_rejected_at_profile_and_dashboard_boundaries(invalid):
    with pytest.raises(ValueError, match="integer from 1 to 5"):
        compact_execution_profile({"replan_after_completed_todos": invalid})
    with pytest.raises(ValueError, match="integer from 1 to 5"):
        _goal_capability_options("todo_replan_cadence", {"completed_todos": invalid})


def test_cadence_configuration_previews_persists_and_restores_default(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "example", "status": "active"}]}))
    original = registry.read_bytes()
    options = _goal_capability_options("todo_replan_cadence", {"completed_todos": 3})
    preview = configure_goal(registry_path=registry, goal_id="example", **options)
    assert registry.read_bytes() == original
    assert preview["changed_fields"] == ["execution_profile"]
    configure_goal(registry_path=registry, goal_id="example", execute=True, **options)
    persisted = json.loads(registry.read_text())["goals"][0]["execution_profile"]
    assert persisted["replan_after_completed_todos"] == 3
    for mode in ["fine", "standard"]:
        persisted = execution_profile_with_turn_granularity(persisted, mode)
        assert persisted["replan_after_completed_todos"] == 3
    readback = configure_goal(registry_path=registry, goal_id="example")
    catalog = readback["configuration_catalog"]["capability_catalog"]
    feature = next(
        c
        for c in catalog["capabilities"]
        if c["capability_id"] == "todo_replan_cadence"
    )
    assert feature["current"] == {"count_unit": "completed_todos", "count": 3}
    editor = feature["configuration_editor"]
    assert editor["writable_scopes"] == ["machine", "goal"]
    assert editor["fields"][0]["input_kind"] == "select"
    assert editor["fields"][1]["input_kind"] == "number"
    assert editor["fields"][1]["minimum"] == 1
    assert editor["fields"][1]["maximum"] == 5
    configure_goal(
        registry_path=registry,
        goal_id="example",
        execute=True,
        clear_execution_replan_after_todos=True,
    )
    assert (
        json.loads(registry.read_text())["goals"][0]["execution_profile"]
        == build_execution_profile()
    )


def test_clearing_an_absent_cadence_override_is_a_noop(tmp_path):
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "example", "status": "active"}]}))

    result = configure_goal(
        registry_path=registry,
        goal_id="example",
        clear_execution_replan_after_todos=True,
        execute=True,
    )

    assert result["changed"] is False
    assert "execution_profile" not in json.loads(registry.read_text())["goals"][0]


def test_cli_cadence_roundtrip_syncs_to_an_isolated_runtime(tmp_path):
    root = Path(__file__).resolve().parents[2]
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"goals": [{"id": "example", "status": "active"}]}))
    result = subprocess.run(
        [
            str(root / "scripts/loopx"),
            "--registry",
            str(registry),
            "--format",
            "json",
            "--runtime-root",
            str(tmp_path / "runtime"),
            "configure-goal",
            "--goal-id",
            "example",
            "--execution-replan-after-todos",
            "2",
            "--execute",
        ],
        cwd=root,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(result.stdout)["changed_fields"] == ["execution_profile"]
    assert (
        json.loads(registry.read_text())["goals"][0]["execution_profile"][
            "replan_after_completed_todos"
        ]
        == 2
    )


@pytest.mark.parametrize("threshold", [2, 3])
def test_earlier_review_still_requires_scoped_completions_and_qualified_checkpoint(
    threshold,
):
    items = [
        {
            "todo_id": f"work-{i}",
            "claimed_by": "agent-a",
            "completed_at": f"2026-01-01T00:0{i}:00Z",
        }
        for i in range(1, threshold + 1)
    ]
    kwargs = {
        "agent_todo_summary": {"recent_completed_advancement_items": items},
        "agent_id": "agent-a",
        "completed_todo_threshold": threshold,
    }
    vision = {"state": "active", "vision_patch": {}}
    assert acceptance_gaps_from_todo_completion_checkpoint(vision, None, **kwargs)
    # Another Agent's work cannot bring this Agent across the threshold.
    items[-1]["claimed_by"] = "agent-b"
    assert acceptance_gaps_from_todo_completion_checkpoint(vision, None, **kwargs) == []
    items[-1]["claimed_by"] = "agent-a"
    # A no-op/satisfied receipt without material outcome evidence cannot reset it.
    checkpoint = {
        "generated_at": "2026-01-01T00:10:00Z",
        "satisfied": True,
        "triggers": [],
    }
    assert acceptance_gaps_from_todo_completion_checkpoint(vision, checkpoint, **kwargs)
    checkpoint["triggers"] = [
        {"kind": "material_delivery_outcome", "delivery_outcome": "outcome_progress"}
    ]
    # Material progress alone still lacks a claim, fresh path decision, and evidence.
    assert acceptance_gaps_from_todo_completion_checkpoint(vision, checkpoint, **kwargs)
    vision["generated_at"] = checkpoint["generated_at"]
    vision["vision_patch"] = {
        "acceptance_summary": "The required output remains valid."
    }
    vision["path_delta"] = {
        "outcome": "continue",
        "evidence_refs": ["artifact:acceptance-check"],
    }
    checkpoint["decision"] = "patched"
    assert (
        acceptance_gaps_from_todo_completion_checkpoint(vision, checkpoint, **kwargs)
        == []
    )
    # Only completions after the covering checkpoint enter the next window.
    checkpoint["generated_at"] = "2026-01-01T00:01:00Z"
    vision["generated_at"] = checkpoint["generated_at"]
    assert (
        acceptance_gaps_from_todo_completion_checkpoint(vision, checkpoint, **kwargs)
        == []
    )
    items.append(
        {
            "todo_id": "next-work",
            "claimed_by": "agent-a",
            "completed_at": "2026-01-01T00:09:00Z",
        }
    )
    assert acceptance_gaps_from_todo_completion_checkpoint(vision, checkpoint, **kwargs)


@pytest.mark.parametrize("threshold", [2, 3, 5])
@pytest.mark.parametrize("paused", [False, True])
def test_quota_uses_cadence_without_bypassing_a_goal_pause(threshold, paused):
    from loopx.control_plane.scheduler.execution_context import (
        scheduler_execution_context_for_runtime_profile,
    )
    from loopx.control_plane.testing.quota_fixtures import (
        quota_status_payload,
        quota_todo_item,
    )
    from loopx.quota import build_quota_should_run

    completed = [
        quota_todo_item(
            todo_id=f"todo_work_{i}",
            title="Validated work",
            status="done",
            claimed_by="agent-a",
            completed_at=f"2026-01-01T00:0{i}:00Z",
            successor_todo_ids=["todo_next"],
        )
        for i in range(1, 4)
    ]
    status = quota_status_payload(
        goal_id="example",
        status="active",
        recommended_action="Continue the goal.",
        claim_scope_agent_id="agent-a",
        coordination={"agent_model": "peer_v1", "registered_agents": ["agent-a"]},
        agent_todo_items=[
            *completed,
            quota_todo_item(todo_id="todo_next", title="Next work", claimed_by="agent-a"),
        ],
        project_asset_extra={
            "execution_profile": {"replan_after_completed_todos": threshold},
            **({"quota": {"compute": 0}} if paused else {}),
        },
    )
    result = build_quota_should_run(
        status,
        goal_id="example",
        agent_id="agent-a",
        scheduler_execution_context=scheduler_execution_context_for_runtime_profile(
            "codex_app_heartbeat"
        ),
    )
    if paused:
        assert result["should_run"] is False
        assert result["normal_delivery_allowed"] is False
    else:
        gaps = result["goal_frontier_projection"]["acceptance_gaps"]
        completed_gaps = [
            g for g in gaps if g.get("source") == "recent_completed_advancement_todo"
        ]
        assert bool(completed_gaps) is (threshold <= 3)
        if completed_gaps:
            assert completed_gaps[0]["completed_todo_threshold"] == threshold
            assert result["goal_frontier_projection"]["replan_required"] is True
