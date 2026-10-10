"""Lifecycle work precedence through the persisted session-runtime status path."""

from __future__ import annotations

import copy
import json

import pytest

from loopx.session_runtime import build_session_runtime_readonly_projection
from loopx.status import collect_status


@pytest.mark.parametrize("locator", [
    "~/evidence.txt", "path:/srv/evidence.txt", "file:///tmp/evidence.txt",
])
def test_persisted_local_evidence_is_omitted_from_public_lifecycle(tmp_path, locator):
    """Read real history through status; keep private source bytes untouched."""
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text("---\nstatus: active\n---\n\n# Goal\n", encoding="utf-8")
    registry = project / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(runtime),
        "goals": [{
            "id": "demo", "status": "active", "domain": "software",
            "repo": str(project), "state_file": state.name,
            "adapter": {"kind": "session_runtime", "status": "connected-read-only"},
        }],
    }), encoding="utf-8")
    runs = runtime / "goals" / "demo" / "runs"
    runs.mkdir(parents=True)
    run_path = runs / "run.json"
    record = {
        "goal_id": "demo", "generated_at": "2026-09-01T00:00:00+00:00",
        "classification": "state_refreshed", "delivery_outcome": "outcome_progress",
        "recommended_action": locator, "evidence_ref": locator,
        "json_path": str(run_path),
    }
    run_path.write_text(json.dumps(record), encoding="utf-8")
    index = runs / "index.jsonl"
    index.write_text(json.dumps(record) + "\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (state, registry, run_path, index)}

    result = collect_status(
        registry_path=registry, runtime_root_override=str(runtime),
        scan_roots=[], limit=5, include_public_boundary_scan=False,
    )
    assert result["ok"] is True
    lifecycle = result["run_history"]["goals"][0]["artifact_lifecycle"]
    milestone = lifecycle["milestones"][0]
    assert milestone["label"] == "outcome_progress"
    assert milestone["reached_evidence_refs"] == []
    assert locator not in json.dumps(lifecycle)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("display_limit", [0, 5])
@pytest.mark.parametrize("adapter_kind,include_work_projection", [
    ("session_runtime", True), ("session_runtime", False),
    ("harness_self_improvement", True), ("harness_self_improvement", False),
])
def test_persisted_work_observation_coverage_before_display_trimming(
    tmp_path, display_limit, adapter_kind, include_work_projection,
):
    from loopx.presentation.renderers.status_markdown import render_status_markdown

    project = tmp_path / "project"
    runtime = tmp_path / "runtime"
    project.mkdir()
    state = project / "ACTIVE_GOAL_STATE.md"
    state.write_text(
        "---\nstatus: active\n---\n\n# Goal\n\n## Agent Todo\n\n"
        "- [x] Implement the change\n"
        "  <!-- loopx:todo todo_id=todo_implemented status=done "
        "task_class=advancement_task claimed_by=agent-a -->\n\n## User Todo\n"
    )
    registry = project / "registry.json"
    registry.write_text(json.dumps({
        "schema_version": 1, "common_runtime_root": str(runtime),
        "goals": [{
            "id": "demo", "status": "active", "domain": "software", "repo": str(project),
            "state_file": state.name,
            "adapter": {"kind": adapter_kind, "status": "connected-read-only"},
        }],
    }))
    projection = build_session_runtime_readonly_projection(
        goal_id="demo",
        decision_results=[{"recommended_action": "Verify the remaining evidence"}],
    )
    runs = runtime / "goals" / "demo" / "runs"
    runs.mkdir(parents=True)
    run_path, markdown_path = runs / "run.json", runs / "run.md"
    record = {
        "goal_id": "demo", "generated_at": "2026-09-01T00:00:00+00:00",
        "classification": "state_refreshed",
        "delivery_outcome": "outcome_progress",
        "json_path": str(run_path), "markdown_path": str(markdown_path),
    }
    if include_work_projection:
        record["session_runtime_readonly_projection"] = projection
    run_path.write_text(json.dumps(record))
    markdown_path.write_text("# Compact session-runtime observation\n")
    (runs / "index.jsonl").write_text(json.dumps(record) + "\n")
    before = {path: path.read_bytes() for path in (state, registry, run_path, markdown_path, runs / "index.jsonl")}
    result = collect_status(
        registry_path=registry, runtime_root_override=str(runtime),
        scan_roots=[], limit=display_limit, include_public_boundary_scan=False,
    )
    assert result["ok"] is True
    goal = result["run_history"]["goals"][0]
    item = next(item for item in result["attention_queue"]["items"] if item["goal_id"] == "demo")
    assert item["agent_todos"]["open_count"] == 0
    assert "work_lane_contract" not in item
    if display_limit == 0:
        assert goal["latest_runs"] == []
    lifecycle = goal["artifact_lifecycle"]
    assert lifecycle["guards"] == []
    assert all(marker["reached"] for marker in lifecycle["milestones"])
    if include_work_projection:
        assert item["session_runtime_projection"]["work_lane_contract"]["must_attempt_work"] is True
        assert lifecycle["lifecycle_phase"] == "qualifying"
        assert lifecycle["next_transitions"] == [{
            "target_phase": "qualifying", "precondition": "Verify the remaining evidence",
            "reason_codes": ["work_lane_selected"],
        }]
        assert "Verify the remaining evidence" in render_status_markdown(result)
    else:
        # Adapter naming alone is not a work observation or proof of no work.
        assert "session_runtime_projection" not in item
        assert lifecycle["lifecycle_phase"] == "closing"
        assert lifecycle["next_transitions"][0]["target_phase"] == "closing"
        assert lifecycle["next_transitions"][0]["reason_codes"] == [
            "no_open_agent_work", "acceptance_unverified",
        ]
        assert "next: closed" not in render_status_markdown(result)
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("field,value", [
    ("goal_id", "other-goal"), ("goal_id", None), ("schema_version", "unknown_v0"),
])
def test_work_observation_rejects_foreign_or_untyped_projection(field, value):
    from loopx.control_plane.runtime.session_runtime import session_runtime_work_observation

    projection = build_session_runtime_readonly_projection(
        goal_id="demo", decision_results=[{"recommended_action": "Continue verification"}],
    )
    projection[field] = value
    before = copy.deepcopy(projection)
    assert session_runtime_work_observation(projection, goal_id="demo") is None
    assert projection == before


@pytest.mark.parametrize("required", [False, True, "false"])
def test_lane_owner_exposes_facts_without_creating_a_work_requirement(required):
    from loopx.control_plane.work_items.work_lane import observe_work_lane

    contract = {"lane": "continuous_monitor", "must_attempt_work": required,
                "obligation": "Inspect the current monitor receipt"}
    before = copy.deepcopy(contract)
    observation = observe_work_lane(contract, next_action="Fallback description")
    assert observation.lane == "continuous_monitor"
    assert observation.must_attempt is (required is True)
    assert observation.next_action == "Inspect the current monitor receipt"
    assert contract == before


def test_lifecycle_validates_work_observation_text_before_rendering():
    from loopx.control_plane.goals.artifact_lifecycle import build_goal_artifact_lifecycle_projection
    from loopx.control_plane.runtime.public_safety import validate_public_safe_value
    from loopx.control_plane.work_items.work_lane import WorkLaneObservation

    projection = build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={"status": "active"},
        work_observation=WorkLaneObservation(
            lane="advancement_task", must_attempt=True,
            # The payload has to stay credential-shaped at runtime so the
            # projection exercises its rejected-value path; it is split the
            # same way `loopx/contract.py` splits its own scan patterns, so the
            # committed source carries no literal credential assignment.
            next_action="x" * 500 + " tok" + "en=" + "synthetic" * 4,
        ),
    )
    assert projection["lifecycle_phase"] == "qualifying"
    assert projection["next_transitions"][0]["precondition"] == "advance the selected lane"
    validate_public_safe_value(projection)


@pytest.mark.parametrize("status", ["active", "closed"])
def test_lifecycle_v0_never_acquires_terminal_advice_from_acceptance_owner(monkeypatch, status):
    """A future producer change must not silently expand this v0 readout's scope."""
    from loopx.control_plane.goals import artifact_lifecycle

    original = artifact_lifecycle.build_goal_acceptance_observation

    def future_observation(*args, **kwargs):
        return {**original(*args, **kwargs), "acceptance_assessed": True,
                "coverage": "complete", "missing_sources": []}

    # Deliberately inject a verdict the bounded owner cannot currently produce.
    # This is a counterfactual, not a new supported acceptance contract.
    monkeypatch.setattr(artifact_lifecycle, "build_goal_acceptance_observation", future_observation)
    projection = artifact_lifecycle.build_goal_artifact_lifecycle_projection(
        goal_id="demo", goal={"status": status},
        user_todo_summary={"gate_open_items": []}, agent_todo_summary={"open_count": 0},
        run_history={"latest_runs": [{"delivery_outcome": "outcome_progress"}]},
    )
    if status == "closed":
        assert projection["lifecycle_phase"] == "closed"
        assert projection["next_transitions"] == []
    else:
        assert projection["lifecycle_phase"] == "closing"
        transition = projection["next_transitions"][0]
        assert transition["target_phase"] == "closing"
        assert transition["reason_codes"] == ["no_open_agent_work", "acceptance_unverified"]
