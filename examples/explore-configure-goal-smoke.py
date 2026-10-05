#!/usr/bin/env python3
"""Smoke configure-goal as the Explore Harness opt-in authority."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
GOAL_ID = "explore-configure-fixture"


def run_cli(registry: Path, runtime_root: Path, *args: str, check: bool = True) -> dict:
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
            str(runtime_root),
            *args,
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if check and result.returncode:
        raise AssertionError(
            f"loopx CLI failed ({result.returncode}): {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return json.loads(result.stdout)


def write_fixture(root: Path) -> tuple[Path, Path]:
    project = root / "project"
    state_file = project / ".codex/goals/explore-configure-fixture/ACTIVE_GOAL_STATE.md"
    state_file.parent.mkdir(parents=True)
    state_file.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Explore Configure Fixture\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P0] Probe the alpha route.\n"
        "  <!-- loopx:todo todo_id=todo_probe_alpha status=open "
        "task_class=advancement_task required_write_scopes=artifacts/alpha/** -->\n",
        encoding="utf-8",
    )
    registry = root / "registry.json"
    registry.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(root / "runtime"),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "status": "active",
                        "repo": str(project),
                        "state_file": str(state_file.relative_to(project)),
                        "adapter": {"kind": "explore_result_layer", "status": "connected"},
                        "quota": {"compute": 1, "window_hours": 24},
                        "spawn_policy": {
                            "mode": "default",
                            "allowed": False,
                            "max_children": 0,
                            "policy_note": "preserve-me",
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry, root / "runtime"


def goal(registry: Path) -> dict:
    return json.loads(registry.read_text(encoding="utf-8"))["goals"][0]


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-explore-configure-") as tmp:
        registry, runtime_root = write_fixture(Path(tmp))
        original = registry.read_text(encoding="utf-8")

        disabled = run_cli(
            registry,
            runtime_root,
            "explore",
            "worker-branch-plan",
            "--goal-id",
            GOAL_ID,
        )
        assert disabled["orchestration_gate"]["state"] == "disabled", disabled

        graph_preview = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--explore-graph-enabled",
        )
        assert graph_preview["dry_run"] is True, graph_preview
        assert graph_preview["feature_summary"]["explore_graph"] == {
            "enabled": True
        }, graph_preview
        assert graph_preview["feature_summary"]["explore_harness"] == {
            "enabled": False
        }, graph_preview
        assert registry.read_text(encoding="utf-8") == original

        graph_applied = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--explore-graph-enabled",
            "--execute",
        )
        assert graph_applied["written"] is True, graph_applied
        assert goal(registry)["explore_graph"] == {"enabled": True}
        assert goal(registry)["spawn_policy"].get("explore_harness") is None
        graph_configured = registry.read_text(encoding="utf-8")

        clear_absent = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--clear-explore-harness-profile",
        )
        assert clear_absent["changed"] is False, clear_absent

        preview = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--explore-harness-enabled",
            "--explore-harness-profile",
            "moe-router",
        )
        assert preview["dry_run"] is True and preview["written"] is False, preview
        assert preview["after"]["orchestration"]["spawn_allowed"] is False, preview
        assert preview["feature_summary"]["explore_harness"] == {
            "enabled": True,
            "profile": "moe-router",
        }, preview
        assert registry.read_text(encoding="utf-8") == graph_configured

        applied = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--explore-harness-enabled",
            "--explore-harness-profile",
            "moe-router",
            "--execute",
        )
        assert applied["written"] is True, applied
        configured = goal(registry)["spawn_policy"]
        assert configured["policy_note"] == "preserve-me", configured
        assert configured["allowed"] is False, configured
        assert configured["explore_harness"] == {
            "enabled": True,
            "profile": "moe-router",
        }, configured

        quota = run_cli(
            registry,
            runtime_root,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            check=False,
        )
        assert quota["ok"] is False and quota["status_health_ok"] is False, quota
        boundary = quota["goal_boundary"]["orchestration"]
        assert boundary["explore_harness"] == configured["explore_harness"], boundary
        assert quota["goal_boundary"]["explore_graph"] == {"enabled": True}, quota

        worker = run_cli(
            registry,
            runtime_root,
            "explore",
            "worker-branch-plan",
            "--goal-id",
            GOAL_ID,
            "--harness-profile",
            "generic",
        )
        assert worker["orchestration_gate"]["state"] == "analysis_only", worker
        assert worker["orchestration_gate"]["effective_profile"] == "moe-router", worker
        assert worker["orchestration_gate"]["profile_source"] == "goal_boundary", worker

        todo = run_cli(
            registry,
            runtime_root,
            "explore",
            "todo-branch-plan",
            "--goal-id",
            GOAL_ID,
        )
        assert todo["orchestration_gate"]["state"] == "analysis_only", todo
        assert todo["orchestration_gate"]["goal_pinned_profile"] == "moe-router", todo

        cleared = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--clear-explore-harness-profile",
            "--execute",
        )
        assert cleared["written"] is True, cleared
        assert goal(registry)["spawn_policy"]["explore_harness"] == {"enabled": True}

        closed = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--no-explore-harness-enabled",
            "--execute",
        )
        assert closed["written"] is True, closed
        assert goal(registry)["spawn_policy"]["explore_harness"] == {"enabled": False}

        rejected_graphless_planning = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--no-explore-graph-enabled",
            "--explore-harness-enabled",
            "--execute",
            check=False,
        )
        assert rejected_graphless_planning["ok"] is False, rejected_graphless_planning
        assert "planning requires its evidence graph" in rejected_graphless_planning["error"]
        assert goal(registry)["explore_graph"] == {"enabled": True}
        assert goal(registry)["spawn_policy"]["explore_harness"] == {"enabled": False}
        unified = run_cli(registry, runtime_root, "configure-goal", "--goal-id", GOAL_ID,
                          "--explore-mode", "planning", "--execute")
        assert unified["ok"]
        assert goal(registry)["explore_graph"] == {"enabled": True}
        assert goal(registry)["spawn_policy"]["explore_harness"] == {"enabled": True}

        conflict = run_cli(
            registry,
            runtime_root,
            "configure-goal",
            "--goal-id",
            GOAL_ID,
            "--explore-harness-profile",
            "generic",
            "--clear-explore-harness-profile",
            check=False,
        )
        assert conflict["ok"] is False, conflict
        assert "cannot be combined" in conflict["error"], conflict

    print("explore-configure-goal-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
