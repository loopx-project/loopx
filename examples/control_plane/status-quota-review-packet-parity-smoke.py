#!/usr/bin/env python3
"""Smoke-test status, quota, review-packet, and scheduler parity on one fixture."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]

GOAL_ID = "status-quota-review-parity"
OTHER_GOAL_ID = "status-quota-review-parity-other"
AGENT_ID = "codex-product-capability"
PRIMARY_AGENT_ID = "codex-main-control"
TODO_ID = "todo_status_quota_parity"
TODO_TEXT = "[P1] Add status/quota/review-packet parity fixture."


def write_state(project: Path, goal_id: str, *, claimed_by: str) -> str:
    state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    path = project / state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-07-02T00:00:00+00:00\n"
        "---\n\n"
        f"# {goal_id}\n\n"
        "## Next Action\n\n"
        "Keep the scoped read path stable before extraction.\n\n"
        "## Agent Todo\n\n"
        f"- [ ] {TODO_TEXT}\n"
        "  <!-- loopx:todo "
        f"todo_id={TODO_ID} "
        "status=open "
        "task_class=advancement_task "
        "action_kind=status_quota_parity_fixture "
        f"claimed_by={claimed_by} "
        "-->\n",
        encoding="utf-8",
    )
    return state_file


def write_registry(project: Path, runtime: Path) -> Path:
    registry_path = project / ".loopx" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    goals = [
        {
            "id": GOAL_ID,
            "domain": "control-plane-read-path",
            "status": "active",
            "repo": str(project),
            "state_file": write_state(project, GOAL_ID, claimed_by=AGENT_ID),
            "adapter": {"kind": "smoke_v0", "status": "connected-read-only"},
            "coordination": {
                "agent_model": "peer_v1",
                "registered_agents": [PRIMARY_AGENT_ID, AGENT_ID],
            },
            "authority_sources": [],
        },
        {
            "id": OTHER_GOAL_ID,
            "domain": "control-plane-read-path",
            "status": "active",
            "repo": str(project),
            "state_file": write_state(project, OTHER_GOAL_ID, claimed_by=PRIMARY_AGENT_ID),
            "adapter": {"kind": "smoke_v0", "status": "connected-read-only"},
            "coordination": {
                "agent_model": "peer_v1",
                "registered_agents": [PRIMARY_AGENT_ID, AGENT_ID],
            },
            "authority_sources": [],
        },
    ]
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "updated_at": "2026-07-02T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": goals,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return registry_path


def run_checked(command: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def create_agent_worktree(project: Path, root: Path) -> Path:
    (project / ".gitignore").write_text(".loopx/\n.codex/\n", encoding="utf-8")
    run_checked(["git", "init", "--initial-branch", "main"], cwd=project)
    run_checked(["git", "config", "user.email", "loopx-smoke@example.invalid"], cwd=project)
    run_checked(["git", "config", "user.name", "LoopX Smoke"], cwd=project)
    run_checked(["git", "add", ".gitignore", "PUBLIC.md"], cwd=project)
    run_checked(["git", "commit", "-m", "fixture"], cwd=project)
    worktree = root / "agent-worktree"
    run_checked(["git", "worktree", "add", "-b", "agent-lane", str(worktree)], cwd=project)
    return worktree


def run_cli(registry_path: Path, runtime: Path, cwd: Path, *args: str) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry_path),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=cwd,
        env=env,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return json.loads(result.stdout)


def assert_goal_scoped_status(payload: dict) -> None:
    assert payload["ok"] is True, payload
    assert payload["goal_filter"] == GOAL_ID, payload
    assert payload["attention_queue"]["goal_filter_applied"] is True, payload
    items = payload["attention_queue"]["items"]
    assert items and {item["goal_id"] for item in items} == {GOAL_ID}, items
    assert {item["goal_id"] for item in payload["todo_index"]["items"]} == {GOAL_ID}, payload
    assert OTHER_GOAL_ID not in json.dumps(payload, sort_keys=True), payload


def assert_quota_parity(payload: dict) -> None:
    assert payload["ok"] is True, payload
    assert payload["decision"] == "run", payload
    assert payload["effective_action"] == "normal_run", payload
    assert payload["interaction_contract"]["agent_channel"]["must_attempt"] is True, payload
    assert payload["interaction_contract"]["cli_channel"]["spend_after_validation"] is True, payload
    lane = payload["agent_lane_next_action"]
    assert lane["todo_id"] == TODO_ID, lane
    assert lane["claimed_by"] == AGENT_ID, lane
    assert lane["preserves_goal_next_action"] is True, lane

    scheduler = payload["scheduler_hint"]
    assert scheduler["action"] == "run_now", scheduler
    assert scheduler["cadence_class"] == "active_work", scheduler
    codex_app = scheduler["codex_app"]
    assert codex_app["recommended_rrule"] == "FREQ=MINUTELY;INTERVAL=3", scheduler
    assert codex_app["recommended_interval_minutes"] == 3, scheduler
    assert codex_app["no_spend_for_cadence_change"] is True, scheduler
    assert scheduler["reset_policy"]["app_automation_initial_rrule"] == "FREQ=MINUTELY;INTERVAL=3", scheduler


def assert_handoff_parity(payload: dict) -> None:
    assert payload["ok"] is True, payload
    assert payload["goal_id"] == GOAL_ID, payload
    assert payload["handoff_only"] is True, payload
    assert payload["project_agent_handoff"] == payload["handoff_text"], payload
    assert payload["within_budget"] is True, payload
    handoff = payload["handoff_text"]
    assert f"goal_id=`{GOAL_ID}`" in handoff, handoff
    assert TODO_TEXT in handoff, handoff
    assert "不要从旧聊天或旧 packet 拼当前状态" in handoff, handoff
    assert "packet" not in payload, payload
    assert OTHER_GOAL_ID not in json.dumps(payload, ensure_ascii=False, sort_keys=True), payload


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="loopx-status-quota-review-parity-") as tmp:
        root = Path(tmp)
        project = root / "project"
        runtime = root / "runtime"
        project.mkdir(parents=True)
        runtime.mkdir(parents=True)
        (project / "PUBLIC.md").write_text("# Public fixture\n", encoding="utf-8")
        registry_path = write_registry(project, runtime)
        agent_worktree = create_agent_worktree(project, root)

        status = run_cli(
            registry_path,
            runtime,
            agent_worktree,
            "status",
            "--goal-id",
            GOAL_ID,
            "--available-capability",
            "material_lifecycle",
        )
        quota = run_cli(
            registry_path,
            runtime,
            agent_worktree,
            "quota",
            "should-run",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--available-capability",
            "material_lifecycle",
            "--codex-app",
        )
        handoff = run_cli(
            registry_path,
            runtime,
            agent_worktree,
            "review-packet",
            "--goal-id",
            GOAL_ID,
            "--available-capability",
            "material_lifecycle",
            "--handoff-only",
        )

    assert_goal_scoped_status(status)
    assert_quota_parity(quota)
    assert_handoff_parity(handoff)
    print("status-quota-review-packet-parity-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
