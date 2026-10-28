#!/usr/bin/env python3
"""Prove an active goal replans immediately after closing one stage vision."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))
from loopx.control_plane.testing.canary_harness import import_fixture_authority  # noqa: E402

GOAL_ID = "closed-vision-successor-fixture"
AGENT_ID = "fixture-agent"


def run_cli(registry: Path, runtime: Path, *args: str) -> dict[str, object]:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        check=False,
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr or completed.stdout)
    return json.loads(completed.stdout)


with tempfile.TemporaryDirectory(prefix="loopx-closed-vision-successor-") as raw_temp:
    root = Path(raw_temp)
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry = project / ".loopx" / "registry.json"
    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "---\n"
        "status: active\n"
        "updated_at: 2026-07-12T00:00:00Z\n"
        "---\n\n"
        "# Closed Vision Successor Fixture\n\n"
        "## Next Action\n\n"
        "- Continue the next implementation stage.\n\n"
        "## Agent Todo\n\n"
        "- [ ] Implement the already-planned next stage.\n"
        "  <!-- loopx:todo todo_id=todo_successor123 status=open "
        "task_class=advancement_task action_kind=implement_next_stage "
        f"claimed_by={AGENT_ID} -->\n",
        encoding="utf-8",
    )
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-07-12T00:00:00Z",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "closed-vision-successor-fixture",
                        "status": "active",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "fixture", "status": "connected-read-only"},
                        "authority_sources": [],
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    import_fixture_authority(registry, GOAL_ID, provider="sqlite")

    closed = run_cli(
        registry,
        runtime,
        "refresh-state",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
        "--classification",
        "fixture_stage_accepted",
        "--delivery-batch-scale",
        "single_surface",
        "--delivery-outcome",
        "primary_goal_outcome",
        "--vision-state",
        "vision_closed",
        "--vision-summary",
        "The current bounded stage is accepted.",
        "--vision-role-scope",
        "Own the next stage while the registry goal remains active.",
        "--vision-acceptance",
        "Current stage evidence is complete.",
        "--vision-advancement-policy",
        "repeat_until_closed",
        "--vision-last-patch",
        "Close only the current stage.",
        "--no-global-sync",
    )
    assert closed["agent_vision"]["state"] == "vision_closed", closed

    quota = run_cli(
        registry,
        runtime,
        "quota",
        "should-run",
        "--goal-id",
        GOAL_ID,
        "--agent-id",
        AGENT_ID,
    )
    gaps = quota["goal_frontier_projection"]["acceptance_gaps"]
    assert gaps[0]["kind"] == "vision_successor_required", quota
    assert quota["goal_frontier_projection"]["replan_required"] is True, quota
    assert quota["effective_action"] == "autonomous_replan_required", quota
    assert quota["interaction_contract"]["agent_channel"]["must_attempt"] is True, quota
    assert quota["selected_todo"]["todo_id"] == "todo_successor123", quota
    assert quota["autonomous_replan_obligation"]["triggers"][0]["kind"] == (
        "vision_successor_required"
    ), quota

print("closed vision successor replan smoke: ok")
