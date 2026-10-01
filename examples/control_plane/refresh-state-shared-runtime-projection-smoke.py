#!/usr/bin/env python3
"""Keep project-local refresh-state visible to its registered shared runtime."""

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

from loopx.control_plane.runtime.shared_runtime_refresh_projection import (  # noqa: E402
    build_shared_runtime_projection,
    write_shared_runtime_projection,
)
from loopx.control_plane.runtime import runtime_projection_route as route_module  # noqa: E402
from loopx.control_plane.runtime.runtime_projection_route import (  # noqa: E402
    compact_runtime_projection_route,
    resolve_runtime_projection_route,
)
from loopx import doctor as doctor_module  # noqa: E402
from loopx import paths as paths_module  # noqa: E402
from loopx.paths import global_registry_path  # noqa: E402
from loopx.presentation.renderers.status_markdown import (  # noqa: E402
    render_status_markdown,
)


AGENT_ID = "codex-shared-runtime-smoke"
GOAL_ID = "refresh-state-shared-runtime-smoke"
VISION_ACCEPTANCE = "Shared quota reads the newest project-local agent vision."


def run_cli(*args: str, cwd: Path, shared_runtime: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "LOOPX_RUNTIME_ROOT": str(shared_runtime),
        },
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), payload
    return payload


def run_cli_failure(*args: str, cwd: Path, shared_runtime: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--format", "json", *args],
        cwd=cwd,
        check=False,
        text=True,
        capture_output=True,
        env={
            **os.environ,
            "PYTHONPATH": str(REPO_ROOT),
            "LOOPX_RUNTIME_ROOT": str(shared_runtime),
        },
    )
    assert result.returncode != 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert isinstance(payload, dict), payload
    return payload


def write_fixture(root: Path) -> tuple[Path, Path, Path, Path]:
    project = root / "project"
    project_runtime = project / ".loopx" / "runtime"
    shared_runtime = root / "shared-runtime"
    source_registry = project / ".loopx" / "registry.json"
    shared_registry = shared_runtime / "registry.global.json"
    state_file = project / ".loopx" / "goals" / GOAL_ID / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        f"# {GOAL_ID}\n\n## Agent Todo\n\n"
        "- [ ] [P1] Verify the shared-runtime refresh projection.\n",
        encoding="utf-8",
    )
    goal = {
        "id": GOAL_ID,
        "domain": "refresh-state-shared-runtime-smoke",
        "status": "active",
        "repo": str(project),
        "state_file": str(state_file.relative_to(project)),
        "adapter": {"kind": "fixture", "status": "connected-read-only"},
        "coordination": {
            "registered_agents": [AGENT_ID],
            "agent_model": "peer_v1",
        },
        "quota": {"compute": 1.0, "window_hours": 24},
    }
    source_registry.parent.mkdir(parents=True, exist_ok=True)
    source_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(project_runtime),
                "goals": [goal],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    shared_registry.parent.mkdir(parents=True)
    shared_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "registry_role": "global-local",
                "common_runtime_root": str(shared_runtime),
                "goals": [
                    {
                        **goal,
                        "source_registry": str(source_registry.resolve()),
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, project_runtime, source_registry, shared_registry


def write_route_source(
    root: Path,
    *,
    name: str,
    source_runtime: Path,
) -> tuple[Path, str]:
    goal_id = f"runtime-route-{name}"
    registry = root / name / "registry.json"
    registry.parent.mkdir(parents=True, exist_ok=True)
    state_file = registry.parent / "goals" / goal_id / "ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        "---\nstatus: active\nupdated_at: 2026-01-01T00:00:00+00:00\n---\n\n"
        f"# {goal_id}\n\n## Agent Todo\n\n- [ ] [P1] Verify route behavior.\n",
        encoding="utf-8",
    )
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(source_runtime),
                "goals": [
                    {
                        "id": goal_id,
                        "status": "active",
                        "repo": str(registry.parent),
                        "state_file": str(state_file.relative_to(registry.parent)),
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry, goal_id


def write_route_target(
    target_runtime: Path,
    *,
    source_registry: Path,
    goal_id: str,
    include_route: bool = True,
) -> None:
    target_registry = target_runtime / "registry.global.json"
    target_registry.parent.mkdir(parents=True, exist_ok=True)
    goals = (
        [
            {
                "id": goal_id,
                "status": "active",
                "source_registry": str(source_registry.resolve()),
            }
        ]
        if include_route
        else []
    )
    target_registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "registry_role": "global-local",
                "common_runtime_root": str(target_runtime),
                "goals": goals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="loopx-refresh-shared-runtime-") as tmp:
        project, project_runtime, source_registry, shared_registry = write_fixture(
            Path(tmp)
        )
        shared_runtime = shared_registry.parent
        refresh = run_cli(
            "--registry",
            str(source_registry),
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--progress-scope",
            "agent_lane",
            "--classification",
            "shared_runtime_projection_verified",
            "--recommended-action",
            "Inspect PRIVATE_LOCAL_ACTION_MARKER in the source runtime only.",
            "--delivery-batch-scale",
            "implementation",
            "--delivery-outcome",
            "outcome_progress",
            "--vision-state",
            "active",
            "--vision-summary",
            "Project-local material refresh should remain visible globally.",
            "--vision-acceptance",
            VISION_ACCEPTANCE,
            "--suppress-external-sinks",
            cwd=project,
            shared_runtime=shared_runtime,
        )

        assert Path(refresh["runtime_root"]).resolve() == project_runtime.resolve()
        assert Path(refresh["global_sync"]["global_registry"]).resolve() == shared_registry.resolve()
        projection = refresh["shared_runtime_projection"]
        assert projection["ok"] is True, projection
        assert projection["status"] == "projected", projection
        assert projection["raw_artifacts_copied"] is False, projection
        assert projection["recommended_action_copied"] is False, projection
        assert projection["readback_verified"] is True, projection
        route = refresh["runtime_projection_route"]
        assert route["schema_version"] == "runtime_projection_route_v0", route
        assert route["status"] == "resolved", route
        assert route["projection_required"] is True, route
        assert route["projection_enabled"] is True, route
        assert str(project) not in json.dumps(route), route
        assert not (project_runtime / "registry.global.json").exists()

        shared_index = shared_runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        rows = [json.loads(line) for line in shared_index.read_text().splitlines()]
        latest = rows[-1]
        assert latest["classification"] == "shared_runtime_projection_verified"
        assert latest["agent_vision"]["vision_patch"]["acceptance_summary"] == VISION_ACCEPTANCE
        shared_record = Path(latest["json_path"]).read_text(encoding="utf-8")
        assert "PRIVATE_LOCAL_ACTION_MARKER" not in shared_record
        assert str(project) not in shared_record

        source_record = json.loads(Path(refresh["json_path"]).read_text(encoding="utf-8"))
        replay_record, replay_index = build_shared_runtime_projection(record=source_record)
        replay = write_shared_runtime_projection(
            shared_runtime_root=shared_runtime,
            goal_id=GOAL_ID,
            record=replay_record,
            index_record=replay_index,
            dry_run=False,
        )
        assert replay["status"] == "already_current", replay
        assert len(shared_index.read_text().splitlines()) == len(rows)

        no_sync_unqualified = run_cli_failure(
            "--registry",
            str(source_registry),
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--progress-scope",
            "agent_lane",
            "--classification",
            "shared_runtime_projection_suppressed",
            "--recommended-action",
            "Keep this source-local checkpoint out of the shared runtime.",
            "--no-global-sync",
            "--suppress-external-sinks",
            cwd=project,
            shared_runtime=shared_runtime,
        )
        assert "open autonomous replan obligation requires a typed semantic delta" in (
            no_sync_unqualified["error"]
        ), no_sync_unqualified

        no_change_vision = Path(tmp) / "no-change-vision.json"
        no_change_vision.write_text(
            json.dumps(
                {
                    "schema_version": "goal_vision_replan_contract_v0",
                    "goal_id": GOAL_ID,
                    "agent_id": AGENT_ID,
                    "state": "active",
                    "vision_patch": {
                        "acceptance_summary": VISION_ACCEPTANCE,
                    },
                    "path_delta": {
                        "schema_version": "goal_path_delta_v0",
                        "outcome": "no_change",
                        "prior_assumption": (
                            "The project-local material refresh should stay visible "
                            "to the shared runtime."
                        ),
                        "observed_reality": (
                            "The source-local checkpoint was verified and kept out "
                            "of the shared runtime without a sync."
                        ),
                        "retained": ["Keep the shared-runtime projection boundary."],
                        "evidence_refs": ["evidence:shared-runtime-projection-verified"],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        no_sync = run_cli(
            "--registry",
            str(source_registry),
            "refresh-state",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--progress-scope",
            "agent_lane",
            "--classification",
            "shared_runtime_projection_suppressed",
            "--recommended-action",
            "Keep this source-local checkpoint out of the shared runtime.",
            "--agent-vision-json",
            str(no_change_vision),
            "--no-global-sync",
            "--suppress-external-sinks",
            cwd=project,
            shared_runtime=shared_runtime,
        )
        assert no_sync["runtime_projection_route"]["projection_enabled"] is False
        assert no_sync["global_sync"]["enabled"] is False
        assert no_sync["shared_runtime_projection"]["status"] == "disabled"
        assert no_sync["autonomous_replan_ack"]["recorded"] is True, no_sync
        assert no_sync["autonomous_replan_ack"]["semantic_delta"]["accepted"] is True, (
            no_sync
        )
        assert len(shared_index.read_text().splitlines()) == len(rows)

        source_record["state"]["frontmatter"]["updated_at"] = str(project)
        boundary_record, _ = build_shared_runtime_projection(record=source_record)
        assert boundary_record["state"]["frontmatter"]["updated_at"] is None
        assert str(project) not in json.dumps(boundary_record)

        status = run_cli(
            "--registry",
            str(source_registry),
            "status",
            "--goal-id",
            GOAL_ID,
            "--limit",
            "5",
            cwd=project,
            shared_runtime=shared_runtime,
        )
        route_diagnostics = status["runtime_projection_routes"]
        assert route_diagnostics["healthy"] is True, route_diagnostics
        assert route_diagnostics["goal_count"] == 1, route_diagnostics
        assert "runtime_projection_routes: healthy=True" in render_status_markdown(status)
        assert "goals=1" in render_status_markdown(status), render_status_markdown(status)

        source_index = project_runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
        source_rows = [
            json.loads(line)
            for line in source_index.read_text(encoding="utf-8").splitlines()
        ]
        enabled_route_row = next(
            row
            for row in reversed(source_rows)
            if (row.get("runtime_projection_route") or {}).get("projection_enabled")
            is True
        )
        lagging_row = {
            **enabled_route_row,
            "generated_at": "2026-01-01T00:00:01+00:00",
            "classification": "shared_runtime_projection_lag_fixture",
        }
        with source_index.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(lagging_row, ensure_ascii=False) + "\n")
        lagging_status = run_cli(
            "--registry",
            str(source_registry),
            "status",
            "--goal-id",
            GOAL_ID,
            "--limit",
            "5",
            cwd=project,
            shared_runtime=shared_runtime,
        )
        lagging_routes = lagging_status["runtime_projection_routes"]
        assert lagging_routes["healthy"] is False, lagging_routes
        assert lagging_routes["goal_count"] == 1, lagging_routes
        lagging_markdown = render_status_markdown(lagging_status)
        assert "runtime_projection_routes: healthy=False" in lagging_markdown
        assert "details=loopx doctor" in lagging_markdown
        default_runtime_root = paths_module.DEFAULT_RUNTIME_ROOT
        legacy_runtime_root = paths_module.LEGACY_RUNTIME_ROOT
        paths_module.DEFAULT_RUNTIME_ROOT = shared_runtime
        paths_module.LEGACY_RUNTIME_ROOT = Path(tmp) / "isolated-legacy-runtime"
        try:
            doctor = doctor_module.collect_doctor()
        finally:
            paths_module.DEFAULT_RUNTIME_ROOT = default_runtime_root
            paths_module.LEGACY_RUNTIME_ROOT = legacy_runtime_root
        doctor_routes = doctor["runtime_projection_routes"]
        assert doctor_routes["healthy"] is False, doctor_routes
        assert doctor_routes["counts"]["lagging"] == 1, doctor_routes
        assert any(
            item.get("goal_id") == GOAL_ID and item.get("status") == "lagging"
            for item in doctor_routes["items"]
        ), doctor_routes
        # Same selected routes yield the same health even through different registries.
        assert doctor_routes["healthy"] is lagging_routes["healthy"]
        assert doctor_routes["goal_count"] == lagging_routes["goal_count"] == 1
        assert doctor_routes["registry"] == str(global_registry_path(shared_runtime).resolve())
        assert lagging_status["registry"] == str(source_registry)
        assert doctor_routes["registry"] != lagging_status["registry"]
        doctor_markdown = doctor_module.render_doctor_markdown(doctor)
        assert f"registry=`{doctor_routes['registry']}`" in doctor_markdown
        assert f"registry: `{source_registry}`" in lagging_markdown
        assert f"goal_filter: `{GOAL_ID}`" in lagging_markdown
        assert "global details=loopx doctor" in lagging_markdown

        # A healthy unrelated registry can have the same count as the lagging one.
        other_project, other_runtime, other_registry, other_global = write_fixture(Path(tmp) / "other")
        other_status = run_cli("--registry", str(other_registry), "status", "--goal-id", GOAL_ID,
                               cwd=other_project, shared_runtime=other_global.parent)
        assert other_status["runtime_projection_routes"]["goal_count"] == doctor_routes["goal_count"]
        assert other_status["runtime_projection_routes"]["healthy"] is True
        assert other_status["registry"] != doctor_routes["registry"]
        assert f"registry: `{other_registry}`" in render_status_markdown(other_status)

        single_runtime = Path(tmp) / "single-runtime"
        single_registry, single_goal = write_route_source(
            Path(tmp),
            name="single",
            source_runtime=single_runtime,
        )
        write_route_target(
            single_runtime,
            source_registry=single_registry,
            goal_id=single_goal,
        )
        single_route = resolve_runtime_projection_route(
            registry_path=single_registry,
            goal_id=single_goal,
            source_runtime_root=single_runtime,
            candidate_roots=[single_runtime],
        )
        assert single_route["status"] == "single_runtime", single_route
        assert single_route["projection_required"] is False, single_route

        mirrored_source_runtime = Path(tmp) / "mirrored-source-runtime"
        mirrored_target_runtime = Path(tmp) / "mirrored-target-runtime"
        mirrored_registry, mirrored_goal = write_route_source(
            Path(tmp),
            name="mirrored",
            source_runtime=mirrored_source_runtime,
        )
        write_route_target(
            mirrored_source_runtime,
            source_registry=mirrored_registry,
            goal_id=mirrored_goal,
        )
        write_route_target(
            mirrored_target_runtime,
            source_registry=mirrored_registry,
            goal_id=mirrored_goal,
        )
        mirrored_route = resolve_runtime_projection_route(
            registry_path=mirrored_registry,
            goal_id=mirrored_goal,
            source_runtime_root=mirrored_source_runtime,
            candidate_roots=[mirrored_target_runtime],
        )
        assert mirrored_route["status"] == "resolved", mirrored_route
        assert mirrored_route["projection_required"] is True, mirrored_route
        assert mirrored_route["target_runtime_root"] == str(
            mirrored_target_runtime.resolve()
        ), mirrored_route
        assert mirrored_route["match_count"] == 1, mirrored_route
        assert mirrored_route["source_mirror_match_count"] == 1, mirrored_route

        standalone_runtime = Path(tmp) / "standalone-runtime"
        standalone_registry, standalone_goal = write_route_source(
            Path(tmp),
            name="standalone",
            source_runtime=standalone_runtime,
        )
        unrelated_runtime = Path(tmp) / "unrelated-default-runtime"
        write_route_target(
            unrelated_runtime,
            source_registry=standalone_registry,
            goal_id="unrelated-goal",
        )
        original_default_runtime = route_module.select_default_runtime_root
        route_module.select_default_runtime_root = lambda: unrelated_runtime
        try:
            standalone_route = resolve_runtime_projection_route(
                registry_path=standalone_registry,
                goal_id=standalone_goal,
                source_runtime_root=standalone_runtime,
            )
        finally:
            route_module.select_default_runtime_root = original_default_runtime
        assert standalone_route["status"] == "single_runtime", standalone_route
        assert standalone_route["declaration_source"] == "source_runtime_fallback"

        missing_source_runtime = Path(tmp) / "missing-source-runtime"
        missing_target_runtime = Path(tmp) / "missing-target-runtime"
        missing_registry, missing_goal = write_route_source(
            Path(tmp),
            name="missing",
            source_runtime=missing_source_runtime,
        )
        write_route_target(
            missing_target_runtime,
            source_registry=missing_registry,
            goal_id=missing_goal,
            include_route=False,
        )
        missing_route = resolve_runtime_projection_route(
            registry_path=missing_registry,
            goal_id=missing_goal,
            source_runtime_root=missing_source_runtime,
            candidate_roots=[missing_target_runtime],
        )
        assert missing_route["status"] == "missing", missing_route
        (missing_target_runtime / "registry.global.json").write_text(
            "{invalid-json\n",
            encoding="utf-8",
        )
        unreadable_route = resolve_runtime_projection_route(
            registry_path=missing_registry,
            goal_id=missing_goal,
            source_runtime_root=missing_source_runtime,
            candidate_roots=[missing_target_runtime],
        )
        assert unreadable_route["status"] == "missing", unreadable_route
        assert unreadable_route["unreadable_target_count"] == 1, unreadable_route
        missing_refresh = run_cli_failure(
            "--registry",
            str(missing_registry),
            "refresh-state",
            "--goal-id",
            missing_goal,
            "--classification",
            "runtime_projection_route_missing",
            "--recommended-action",
            "Repair the declared runtime projection route before retrying sync.",
            "--suppress-external-sinks",
            cwd=missing_registry.parent,
            shared_runtime=missing_target_runtime,
        )
        assert missing_refresh["ok"] is False, missing_refresh
        assert missing_refresh["runtime_projection_route"]["status"] == "missing"
        assert missing_refresh["global_sync"]["enabled"] is False
        assert missing_refresh["shared_runtime_projection"]["status"] == "route_missing"
        assert not (missing_source_runtime / "registry.global.json").exists()

        ambiguous_source_runtime = Path(tmp) / "ambiguous-source-runtime"
        ambiguous_registry, ambiguous_goal = write_route_source(
            Path(tmp),
            name="ambiguous",
            source_runtime=ambiguous_source_runtime,
        )
        ambiguous_targets = [
            Path(tmp) / "ambiguous-target-a",
            Path(tmp) / "ambiguous-target-b",
        ]
        for target in ambiguous_targets:
            write_route_target(
                target,
                source_registry=ambiguous_registry,
                goal_id=ambiguous_goal,
            )
        write_route_target(
            ambiguous_source_runtime,
            source_registry=ambiguous_registry,
            goal_id=ambiguous_goal,
        )
        ambiguous_route = resolve_runtime_projection_route(
            registry_path=ambiguous_registry,
            goal_id=ambiguous_goal,
            source_runtime_root=ambiguous_source_runtime,
            candidate_roots=ambiguous_targets,
        )
        assert ambiguous_route["status"] == "ambiguous", ambiguous_route
        assert ambiguous_route["match_count"] == 2, ambiguous_route
        assert ambiguous_route["source_mirror_match_count"] == 1, ambiguous_route
        compact_ambiguous = compact_runtime_projection_route(ambiguous_route)
        assert str(Path(tmp)) not in json.dumps(compact_ambiguous), compact_ambiguous

        quota = run_cli(
            "--registry",
            str(shared_registry),
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "shell",
            "--include-detail",
            "vision",
            cwd=project,
            shared_runtime=shared_runtime,
        )
        audit = quota["interaction_contract"]["agent_channel"][
            "vision_continuation_audit"
        ]
        assert audit["acceptance_gaps"][0]["acceptance_summary"] == VISION_ACCEPTANCE

    print("refresh-state shared-runtime projection smoke passed")


if __name__ == "__main__":
    main()
