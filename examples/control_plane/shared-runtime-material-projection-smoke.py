#!/usr/bin/env python3
"""Exercise material event projection into a registered shared runtime."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from loopx import dreaming  # noqa: E402
from loopx import operator_gate as operator_gate_module  # noqa: E402
from loopx import project_map  # noqa: E402
from loopx.control_plane.runtime import runtime_projection_route as route_module  # noqa: E402
from loopx.control_plane.runtime.shared_runtime_material_projection import (  # noqa: E402
    build_shared_runtime_material_projection,
    write_shared_runtime_material_projection,
)


GOAL_ID = "shared-material-projection-smoke"
AGENT_ID = "codex-shared-material-smoke"


def write_source(
    root: Path,
    *,
    name: str,
    runtime: Path,
    registry_is_global: bool = False,
) -> tuple[Path, Path, dict]:
    project = root / name
    registry = (
        runtime / "registry.global.json"
        if registry_is_global
        else project / ".loopx" / "registry.json"
    )
    state_file = project / ".codex" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        f"# {GOAL_ID}\n\n## Agent Todo\n\n"
        "- [ ] [P1] Keep material control-plane events visible.\n",
        encoding="utf-8",
    )
    goal = {
        "id": GOAL_ID,
        "domain": "shared-material-projection-smoke",
        "status": "active",
        "repo": str(project),
        "state_file": str(state_file.relative_to(project)),
        "adapter": {
            "kind": "read_only_project_map_v0",
            "status": "connected-read-only",
        },
        "coordination": {
            "registered_agents": [AGENT_ID],
            "agent_model": "peer_v1",
        },
        "quota": {"compute": 1.0, "window_hours": 24, "allowed_slots": 5},
    }
    registry.parent.mkdir(parents=True, exist_ok=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "registry_role": "global-local" if registry_is_global else "project-local",
                "common_runtime_root": str(runtime),
                "goals": [goal],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, registry, goal


def write_target(runtime: Path, *, source_registry: Path, goal: dict, routed: bool = True) -> Path:
    registry = runtime / "registry.global.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    goals = [{**goal, "source_registry": str(source_registry.resolve())}] if routed else []
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "registry_role": "global-local",
                "common_runtime_root": str(runtime),
                "goals": goals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry


def read_rows(runtime: Path) -> list[dict]:
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    if not index.exists():
        return []
    return [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]


def run_cli(registry: Path, runtime: Path, *args: str) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            *args,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "LOOPX_RUNTIME_ROOT": str(runtime)},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def append_dreaming_proposal(runtime: Path) -> str:
    proposal_id = "dreaming_shared_material_fixture"
    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    row = {
        "generated_at": "2026-01-01T00:03:00+00:00",
        "goal_id": GOAL_ID,
        "classification": "dreaming_refactor_warning",
        "recommended_action": "PRIVATE_LOCAL_ACTION_MARKER",
        "summary": "Repeated control-plane evidence suggests one bounded follow-up.",
        "dreaming": {
            "schema_version": "dreaming_proposal_v0",
            "proposal_id": proposal_id,
            "proposal_type": "refactor_warning",
            "evidence_window": "last_3_non_neutral_runs",
        },
    }
    with (runs_dir / "index.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row) + "\n")
    return proposal_id


def record_operator_gate(registry: Path, runtime: Path, *, sync_global: bool) -> dict:
    return operator_gate_module.record_operator_gate(
        registry_path=registry,
        runtime_root_override=str(runtime),
        goal_id=GOAL_ID,
        gate="release_review",
        decision="defer",
        operator_question="Should this bounded release proceed?",
        reason_summary="Wait for one more public validation signal.",
        follow_up="Re-evaluate after the validation signal lands.",
        agent_command=None,
        recommended_action="PRIVATE_LOCAL_ACTION_MARKER",
        recorded_at=None,
        dry_run=False,
        sync_global=sync_global,
    )


def assert_public_projection(rows: list[dict], kind: str) -> dict:
    row = rows[-1]
    marker = row.get("shared_runtime_material_projection") or {}
    assert marker.get("projection_kind") == kind, row
    assert marker.get("raw_artifacts_copied") is False, row
    assert marker.get("recommended_action_copied") is False, row
    encoded = json.dumps(row, ensure_ascii=False)
    assert "recommended_action" not in row, row
    assert "PRIVATE_LOCAL_ACTION_MARKER" not in encoded, row
    return row


def exercise_split_runtime(root: Path) -> None:
    source_runtime = root / "split-source-runtime"
    shared_runtime = root / "split-shared-runtime"
    project, source_registry, goal = write_source(
        root,
        name="split-project",
        runtime=source_runtime,
    )
    shared_registry = write_target(
        shared_runtime,
        source_registry=source_registry,
        goal=goal,
    )
    original_runtime = os.environ.get("LOOPX_RUNTIME_ROOT")
    os.environ["LOOPX_RUNTIME_ROOT"] = str(shared_runtime)
    try:
        mapped = project_map.read_only_project_map_run(
            registry_path=source_registry,
            runtime_root_override=str(source_runtime),
            goal_id=GOAL_ID,
            project=project,
            state_file=None,
            classification="read_only_project_map",
            recommended_action="PRIVATE_LOCAL_ACTION_MARKER",
            dry_run=False,
            sync_global=True,
        )
        assert mapped["shared_runtime_material_projection"]["status"] == "projected", mapped
        map_row = assert_public_projection(read_rows(shared_runtime), "read_only_project_map")
        assert isinstance(map_row.get("project_map"), dict), map_row
        map_status = run_cli(shared_registry, shared_runtime, "status", "--goal-id", GOAL_ID)
        assert map_status["run_history"]["goals"][0]["latest_status_run"][
            "classification"
        ] == "read_only_project_map"

        gate = record_operator_gate(source_registry, source_runtime, sync_global=True)
        assert gate["shared_runtime_material_projection"]["readback_verified"] is True, gate
        gate_row = assert_public_projection(read_rows(shared_runtime), "operator_gate_decision")
        assert gate_row["operator_gate"]["decision"] == "defer", gate_row
        gate_status = run_cli(shared_registry, shared_runtime, "status", "--goal-id", GOAL_ID)
        latest_gate = gate_status["run_history"]["goals"][0]["latest_status_run"]
        assert latest_gate["classification"] == "operator_gate_deferred", latest_gate
        assert latest_gate["operator_gate"]["operator_question"], latest_gate
        gate_quota = run_cli(
            shared_registry,
            shared_runtime,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
        )
        assert gate_quota["state"] == "operator_gate", gate_quota

        proposal_id = append_dreaming_proposal(source_runtime)
        decision = dreaming.record_dreaming_proposal_decision(
            registry_path=source_registry,
            runtime_root_override=str(source_runtime),
            goal_id=GOAL_ID,
            proposal_id=proposal_id,
            decision="defer",
            reason_summary="Keep this proposal advisory until stronger evidence arrives.",
            todo_text=None,
            claimed_by=None,
            dry_run=False,
            sync_global=True,
        )
        assert decision["shared_runtime_material_projection"]["status"] == "projected", decision
        dream_row = assert_public_projection(read_rows(shared_runtime), "dreaming_decision")
        assert dream_row["dreaming_decision"]["decision"] == "defer", dream_row
        assert dream_row["source_dreaming_proposal"]["proposal_id"] == proposal_id, dream_row
        dream_status = run_cli(shared_registry, shared_runtime, "status", "--goal-id", GOAL_ID)
        assert dream_status["run_history"]["goals"][0]["latest_status_run"][
            "classification"
        ] == "dreaming_proposal_deferred"
        route_health = dream_status["runtime_projection_routes"]
        assert route_health["healthy"] is True, route_health
        assert route_health["goal_count"] >= 1, route_health

        source_row = read_rows(source_runtime)[-1]
        replay_record, replay_index = build_shared_runtime_material_projection(
            source_row=source_row,
            projection_kind="dreaming_decision",
        )
        before_replay = len(read_rows(shared_runtime))
        replay = write_shared_runtime_material_projection(
            shared_runtime_root=shared_runtime,
            goal_id=GOAL_ID,
            record=replay_record,
            index_record=replay_index,
            dry_run=False,
        )
        assert replay["status"] == "already_current", replay
        assert len(read_rows(shared_runtime)) == before_replay

        no_sync = record_operator_gate(source_registry, source_runtime, sync_global=False)
        assert no_sync["shared_runtime_material_projection"]["status"] == "disabled", no_sync
        assert len(read_rows(shared_runtime)) == before_replay
    finally:
        if original_runtime is None:
            os.environ.pop("LOOPX_RUNTIME_ROOT", None)
        else:
            os.environ["LOOPX_RUNTIME_ROOT"] = original_runtime


def exercise_route_edges(root: Path) -> None:
    single_runtime = root / "single-runtime"
    _, single_registry, _ = write_source(
        root,
        name="single-project",
        runtime=single_runtime,
        registry_is_global=True,
    )
    single = record_operator_gate(single_registry, single_runtime, sync_global=True)
    assert single["runtime_projection_route"]["status"] == "single_runtime", single
    assert single["shared_runtime_material_projection"]["status"] == "not_required", single
    assert len(read_rows(single_runtime)) == 1

    missing_source = root / "missing-source-runtime"
    missing_target = root / "missing-target-runtime"
    project, missing_registry, goal = write_source(
        root,
        name="missing-project",
        runtime=missing_source,
    )
    write_target(missing_target, source_registry=missing_registry, goal=goal, routed=False)
    original_runtime = os.environ.get("LOOPX_RUNTIME_ROOT")
    os.environ["LOOPX_RUNTIME_ROOT"] = str(missing_target)
    missing = project_map.read_only_project_map_run(
        registry_path=missing_registry,
        runtime_root_override=str(missing_source),
        goal_id=GOAL_ID,
        project=project,
        state_file=None,
        classification="read_only_project_map",
        recommended_action=None,
        dry_run=False,
        sync_global=True,
    )
    assert missing["ok"] is False and missing["partial_write"] is True, missing
    assert missing["shared_runtime_material_projection"]["status"] == "route_missing", missing
    assert len(read_rows(missing_source)) == 1
    assert read_rows(missing_target) == []

    ambiguous_source = root / "ambiguous-source-runtime"
    target_a = root / "ambiguous-target-a"
    target_b = root / "ambiguous-target-b"
    _, ambiguous_registry, ambiguous_goal = write_source(
        root,
        name="ambiguous-project",
        runtime=ambiguous_source,
    )
    write_target(target_a, source_registry=ambiguous_registry, goal=ambiguous_goal)
    write_target(target_b, source_registry=ambiguous_registry, goal=ambiguous_goal)
    os.environ["LOOPX_RUNTIME_ROOT"] = str(target_a)
    original_default = route_module.select_default_runtime_root
    route_module.select_default_runtime_root = lambda: target_b
    try:
        ambiguous = record_operator_gate(
            ambiguous_registry,
            ambiguous_source,
            sync_global=True,
        )
    finally:
        route_module.select_default_runtime_root = original_default
        if original_runtime is None:
            os.environ.pop("LOOPX_RUNTIME_ROOT", None)
        else:
            os.environ["LOOPX_RUNTIME_ROOT"] = original_runtime
    assert ambiguous["ok"] is False and ambiguous["partial_write"] is True, ambiguous
    assert ambiguous["shared_runtime_material_projection"]["status"] == "route_ambiguous"
    assert len(read_rows(ambiguous_source)) == 1
    assert read_rows(target_a) == [] and read_rows(target_b) == []


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-shared-material-projection-") as tmp:
        root = Path(tmp)
        exercise_split_runtime(root)
        exercise_route_edges(root)
    print("shared-runtime-material-projection-smoke passed")


if __name__ == "__main__":
    main()
