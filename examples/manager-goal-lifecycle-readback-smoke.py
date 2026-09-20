#!/usr/bin/env python3
"""Fixture smoke for the Goal lifecycle readback the steward reads.

The manager answer must lead with where a Goal stands: its reached milestones
and lifecycle phase. Those already exist in the status collection, but a
manager evidence page that silently omits them is indistinguishable from a Goal
with no milestones at all.

This smoke runs the real operator readback
(`loopx goal-portfolio --manager-view portfolio`) against a fixture registry and
proves the contract end to end: a Goal with recorded material progress reaches
the page with that milestone marked reached and its evidence ref, a Goal with no
recorded delivery still answers with its own phase and an empty milestone list
rather than a missing field, and every row answers with a schema the manager can
branch on.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

PROJECTION_SCHEMA = "goal_artifact_lifecycle_projection_v0"
READBACK_SCHEMA = "goal_lifecycle_readback_v0"

# A public evidence page must not carry local paths or provider tokens.
FORBIDDEN_LEAKS = ("/Users/", "sk-", "ghp_", "BEGIN PRIVATE KEY")


def write_state(project: Path, goal_id: str, title: str) -> str:
    state_file = f".codex/goals/{goal_id}/ACTIVE_GOAL_STATE.md"
    path = project / state_file
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\nstatus: active-read-only\nowner_mode: goal\n"
        f'objective: "{title}"\n---\n\n## Agent Todo\n\n- [ ] {title}\n',
        encoding="utf-8",
    )
    return state_file


def write_run_index(runtime: Path, goal_id: str, rows: list[dict]) -> None:
    path = runtime / "goals" / goal_id / "runs" / "index.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8"
    )


def build_fixture(root: Path) -> Path:
    runtime = root / "runtime"
    goals = []
    for goal_id, title in (("alpha", "alpha goal"), ("beta", "beta goal")):
        goals.append(
            {
                "id": goal_id,
                "domain": title,
                "status": "active-read-only",
                "repo": str(root / goal_id),
                "state_file": write_state(root / goal_id, goal_id, title),
                "coordination": {
                    "registered_agents": ["worker"],
                    "agent_model": "peer_v1",
                },
            }
        )
    registry_path = root / "registry.json"
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "common_runtime_root": str(runtime),
                "goals": goals,
            }
        ),
        encoding="utf-8",
    )
    # Only alpha records a canonical material delivery outcome, so only alpha
    # has a milestone the readback can evidence.
    write_run_index(
        runtime,
        "alpha",
        [
            {
                "generated_at": "2026-09-19T02:00:00+00:00",
                "goal_id": "alpha",
                "agent_id": "worker",
                "todo_id": "todo_fixture",
                "classification": "validated_progress",
                "delivery_outcome": "outcome_progress",
                "progress_observation": {
                    "schema_version": "typed_progress_observation_v0",
                    "result_class": "advanced",
                    "work_item_id": "todo_fixture",
                    "evidence_ids": ["fixture-evidence"],
                },
                "recommended_action": "shipped the first bounded slice",
            }
        ],
    )
    return registry_path


def read_page(registry_path: Path) -> dict:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "--registry",
            str(registry_path),
            "goal-portfolio",
            "--manager-view",
            "portfolio",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    assert completed.returncode == 0, (completed.returncode, completed.stderr[-2000:])
    return json.loads(completed.stdout)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        registry_path = build_fixture(Path(tmp))
        page = read_page(registry_path)
    assert page.get("ok") is True, page
    assert page.get("schema_version") == "manager_evidence_page_v1", page
    rows = {row["goal_id"]: row for row in page["rows"]}
    assert set(rows) == {"alpha", "beta"}, rows

    # The floor: every Goal on the page answers the lifecycle question, so the
    # manager never has to read an absent list as "no milestones".
    for goal_id, row in rows.items():
        readback = row.get("goal_lifecycle")
        assert isinstance(readback, dict), (goal_id, row)
        assert readback.get("schema_version") in {
            PROJECTION_SCHEMA,
            READBACK_SCHEMA,
        }, (goal_id, readback)
        assert readback.get("goal_id") == goal_id, (goal_id, readback)

    derived = rows["alpha"]["goal_lifecycle"]
    assert derived["schema_version"] == PROJECTION_SCHEMA, derived
    assert derived["lifecycle_phase"] == "qualifying", derived
    milestone = next(m for m in derived["milestones"] if m["id"] == "outcome_progress")
    assert milestone["reached"] is True, derived
    assert milestone["reached_evidence_refs"], derived
    assert "shipped the first bounded slice" in milestone["label"], derived

    # A Goal with no recorded delivery still answers with its own projection:
    # an evidenced phase and an empty milestone list are not a missing read.
    empty = rows["beta"]["goal_lifecycle"]
    assert empty["schema_version"] == PROJECTION_SCHEMA, empty
    assert empty["milestones"] == [], empty
    assert empty["lifecycle_phase"], empty

    rendered = json.dumps(page, ensure_ascii=False)
    for token in FORBIDDEN_LEAKS:
        assert token not in rendered, (token, rendered[:2000])
    print("manager goal lifecycle readback smoke: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
