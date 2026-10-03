"""Public quota should-run contract: prose never creates a stall signal.

Issue #4336: releases through 0.4.5 inferred autonomous-replan stalls from
free-form run summaries with a substring matcher, so the word `stalled`
inside `installed` turned successful progress records into a
`no_progress_streak` trigger. Main sources replan decisions from typed
progress observations only; these tests pin that contract through the real
public CLI rather than private helper state.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
GOAL_ID = "prose-stall-contract"
AGENT_ID = "codex-prose-contract"
TURN_ID = "turn-prose-contract-1"


def _write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(
        "---\n"
        "status: active-read-only\n"
        "owner_mode: goal\n"
        'objective: "Install the CLI and settle the delivery."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Prose Stall Contract Fixture\n\n"
        "## Objective\n\n"
        "Install the CLI and settle the delivery.\n\n"
        "## Next Action\n\n"
        "- Validate and settle the selected delivery.\n\n"
        "## Agent Todo\n\n"
        "- [ ] [P1] Validate and settle the selected delivery.\n"
        "  <!-- loopx:todo todo_id=todo_prose_contract status=open "
        "task_class=advancement_task action_kind=validate -->\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": "2026-01-01T00:00:00+00:00",
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "prose-stall-contract",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {
                            "kind": "read_only_project_map_v0",
                            "status": "connected-read-only",
                        },
                        "coordination": {
                            "registered_agents": [AGENT_ID],
                            "agent_model": "peer_v1",
                        },
                        "authority_sources": [],
                        "quota": {
                            "compute": 1.0,
                            "window_hours": 24,
                            "allowed_slots": 2,
                        },
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return project, runtime, registry_path


def _write_runs(runtime: Path, runs: list[dict[str, Any]]) -> None:
    runs_dir = runtime / "goals" / GOAL_ID / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    indexed: list[dict[str, Any]] = []
    for minute, run in enumerate(runs, start=1):
        run = {**run, "generated_at": f"2026-01-01T00:0{minute}:00+00:00"}
        json_path = runs_dir / f"run-{minute}.json"
        markdown_path = runs_dir / f"run-{minute}.md"
        json_path.write_text(json.dumps(run) + "\n", encoding="utf-8")
        markdown_path.write_text("# Run fixture\n", encoding="utf-8")
        indexed.append(
            {
                **run,
                "json_path": str(json_path),
                "markdown_path": str(markdown_path),
            }
        )
    (runs_dir / "index.jsonl").write_text(
        "".join(json.dumps(run) + "\n" for run in indexed),
        encoding="utf-8",
    )


def _run_should_run(
    registry_path: Path,
    runtime: Path,
    project: Path,
) -> tuple[int, dict[str, Any]]:
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
            "quota",
            "should-run",
            "--codex-app",
            "--goal-id",
            GOAL_ID,
            "--agent-id",
            AGENT_ID,
            "--turn-instance-id",
            TURN_ID,
            "--scan-path",
            str(project),
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    return result.returncode, json.loads(result.stdout)


_PROSE_SUMMARIES = (
    "CLI installed successfully and the tool is ready for use.",
    "package uninstalled; installation completed for the CLI tool.",
)

_TYPED_OBSERVATION = {
    "schema_version": "typed_progress_observation_v0",
    "result_class": "unchanged",
    "work_item_id": "todo_prose_contract",
    "surface_id": "surface-existing",
    "hypothesis_id": "hypothesis-existing",
    "probe_kind": "probe-existing",
    "evidence_ids": ["evidence-existing"],
}


def _run_base(**extra: Any) -> dict[str, Any]:
    return {
        "goal_id": GOAL_ID,
        "agent_id": AGENT_ID,
        "classification": "bounded_fixture_probe",
        "delivery_batch_scale": "single_surface",
        "delivery_outcome": "surface_only",
        **extra,
    }


def test_installed_wording_never_creates_a_no_progress_trigger(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _write_runs(
        runtime,
        [_run_base(summary=summary) for summary in _PROSE_SUMMARIES],
    )

    exit_code, payload = _run_should_run(registry_path, runtime, project)

    assert exit_code == 0, payload
    assert payload["decision"] != "autonomous_replan_required", payload
    assert payload["decision"] == "run", payload
    serialized = json.dumps(payload)
    assert "no_progress_streak" not in serialized
    assert "typed_progress_repeat" not in serialized


def test_typed_no_progress_observation_still_requires_replan(
    tmp_path: Path,
) -> None:
    project, runtime, registry_path = _write_fixture(tmp_path)
    _write_runs(
        runtime,
        [
            _run_base(
                summary=summary,
                progress_observation=dict(_TYPED_OBSERVATION),
            )
            for summary in _PROSE_SUMMARIES
        ],
    )

    exit_code, payload = _run_should_run(registry_path, runtime, project)

    assert exit_code == 0, payload
    assert payload["decision"] == "autonomous_replan_required", payload
    triggers = (
        payload.get("replan_obligation", {}).get("triggers")
        or payload.get("autonomous_replan_obligation", {}).get("triggers")
        or []
    )
    assert any(
        trigger.get("kind") == "typed_progress_repeat" for trigger in triggers
    ), payload
