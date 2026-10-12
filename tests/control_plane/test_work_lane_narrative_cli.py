"""Real quota CLI: a run label cannot change a typed work obligation."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


GOAL_ID = "work-lane-narrative-fixture"


def _guard(
    root: Path, *, classification: str, delivery_outcome: str | None,
    authored_scope: str | None,
) -> dict:
    project = root / "project"
    runtime = root / "runtime"
    state = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    registry = project / ".loopx" / "registry.json"
    runs = runtime / "goals" / GOAL_ID / "runs"
    state.parent.mkdir(parents=True)
    registry.parent.mkdir(parents=True)
    runs.mkdir(parents=True)
    state.write_text(
        "---\nstatus: active\nowner_mode: goal\nobjective: Advance fixture.\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        "# Fixture\n\n## Objective\nAdvance fixture.\n\n"
        "## Next Action\nAdvance one bounded segment.\n\n"
        "## Agent Todo\n- [ ] [P1] Advance one bounded segment.\n"
        "  <!-- loopx:todo todo_id=todo_scope_audit status=open "
        "task_class=advancement_task priority=P1 -->\n",
        encoding="utf-8",
    )
    registry.write_text(json.dumps({
        "schema_version": "0.1",
        "common_runtime_root": str(runtime),
        "goals": [{
            "id": GOAL_ID, "domain": "public-fixture", "status": "active",
            "repo": str(project), "state_file": str(state.relative_to(project)),
            "adapter": {"kind": "read_only_project_map_v0", "status": "connected-read-only"},
            "authority_sources": [],
            "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 10},
        }],
    }), encoding="utf-8")
    run = {
        "generated_at": "2026-01-01T00:01:00+00:00",
        "goal_id": GOAL_ID,
        "classification": classification,
        "recommended_action": "Advance one bounded segment.",
        "health_check": "public fixture healthy",
    }
    if authored_scope:
        run["progress_scope"] = authored_scope
    if delivery_outcome:
        run.update(delivery_outcome=delivery_outcome, delivery_batch_scale="implementation")
    run_path = runs / "fixture-run.json"
    run_path.write_text(json.dumps(run), encoding="utf-8")
    markdown_path = runs / "fixture-run.md"
    markdown_path.write_text("# Public fixture run\n", encoding="utf-8")
    (runs / "index.jsonl").write_text(json.dumps({
        **run, "json_path": str(run_path), "markdown_path": str(markdown_path),
    }) + "\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", "from loopx.cli import main; raise SystemExit(main())",
         "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
         "quota", "should-run", "--goal-id", GOAL_ID, "--runtime-profile", "generic_cli"],
        cwd=project, capture_output=True, text=True, check=True,
        env={**os.environ, "LOOPX_USAGE_PING": "0"},
    )
    return json.loads(result.stdout)


@pytest.mark.parametrize("delivery_outcome", [None, "surface_only"])
@pytest.mark.parametrize("authored_scope", [None, "goal"])
def test_run_classification_cannot_change_quota_obligation(
    tmp_path: Path, delivery_outcome: str | None, authored_scope: str | None,
) -> None:
    plain = _guard(
        tmp_path / "plain", classification="routine_progress",
        delivery_outcome=delivery_outcome, authored_scope=authored_scope,
    )
    narrative = _guard(
        tmp_path / "narrative",
        classification="routine_progress_dependency_observation_note",
        delivery_outcome=delivery_outcome,
        authored_scope=authored_scope,
    )
    obligation = (
        "advance_primary_outcome_or_write_blocker"
        if delivery_outcome else "advance_one_bounded_segment"
    )
    for guard in (plain, narrative):
        assert guard["should_run"] is True
        assert guard["effective_action"] == "normal_run"
        assert guard["work_lane_contract"]["lane"] == "advancement_task"
        assert guard["work_lane_contract"]["obligation"] == obligation
        assert guard["heartbeat_recommendation"]["recommended_mode"] == "steering_audit_then_one_step"
        if delivery_outcome:
            assert guard["work_lane_contract"]["outcome_followthrough"]["required"] is True
