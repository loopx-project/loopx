#!/usr/bin/env python3
"""Smoke-test refresh-state path uniqueness on same-second collisions."""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import loopx.state_refresh as state_refresh


GOAL_ID = "refresh-state-unique-path-goal"
GENERATED_AT = "2026-01-01T00:00:00+00:00"


def write_fixture(root: Path) -> tuple[Path, Path, Path]:
    project = root / "project"
    runtime = root / "runtime"
    state_file = f".codex/goals/{GOAL_ID}/ACTIVE_GOAL_STATE.md"
    state_path = project / state_file
    registry_path = project / ".loopx" / "registry.json"
    runs_dir = runtime / "goals" / GOAL_ID / "runs"

    state_path.parent.mkdir(parents=True)
    state_path.write_text(
        "---\n"
        "status: active-read-only\n"
        "owner_mode: goal\n"
        'objective: "Keep refresh-state append-only."\n'
        "updated_at: 2026-01-01T00:00:00+00:00\n"
        "---\n\n"
        "# Refresh State Unique Path Goal\n\n"
        "## Next Action\n\n"
        "- Append a state refresh without overwriting an existing run artifact.\n\n"
        "## Progress Ledger\n\n"
        "- Fixture initialized.\n",
        encoding="utf-8",
    )
    registry_path.parent.mkdir(parents=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": "0.1",
                "updated_at": GENERATED_AT,
                "common_runtime_root": str(runtime),
                "goals": [
                    {
                        "id": GOAL_ID,
                        "domain": "refresh-state-fixture",
                        "status": "active-read-only",
                        "repo": str(project),
                        "state_file": state_file,
                        "adapter": {"kind": "fixture", "status": "connected-read-only"},
                        "authority_sources": [],
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    runs_dir.mkdir(parents=True)
    existing_json = runs_dir / "2026-01-01T00-00-00-00-00.json"
    existing_markdown = runs_dir / "2026-01-01T00-00-00-00-00.md"
    existing_json.write_text('{"existing": true}\n', encoding="utf-8")
    existing_markdown.write_text("# Existing Run\n", encoding="utf-8")
    (runs_dir / "index.jsonl").write_text(
        json.dumps(
            {
                "generated_at": GENERATED_AT,
                "goal_id": GOAL_ID,
                "classification": "evidence_observation_v0",
                "recommended_action": "preserve existing artifact",
                "health_check": "compact evidence event public-safe",
                "json_path": str(existing_json),
                "markdown_path": str(existing_markdown),
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    from loopx.control_plane.testing.canary_harness import import_fixture_authority
    import_fixture_authority(registry_path, GOAL_ID, provider="sqlite")
    return registry_path, runtime, project


def main() -> None:
    original_now_local = state_refresh.now_local
    try:
        state_refresh.now_local = lambda: GENERATED_AT
        with tempfile.TemporaryDirectory() as raw_tmp:
            registry_path, runtime, project = write_fixture(Path(raw_tmp))
            payload = state_refresh.refresh_state_run(
                registry_path=registry_path,
                runtime_root_override=str(runtime),
                goal_id=GOAL_ID,
                project=project,
                state_file=None,
                classification="state_refreshed",
                recommended_action="append refresh-state collision-free",
                delivery_batch_scale="single_surface",
                delivery_outcome="outcome_progress",
                dry_run=False,
                sync_global=False,
            )
            assert payload["appended"] is True, payload
            json_path = Path(payload["json_path"])
            markdown_path = Path(payload["markdown_path"])
            assert json_path.name == "2026-01-01T00-00-00-00-00-2.json", payload
            assert markdown_path.name == "2026-01-01T00-00-00-00-00-2.md", payload
            assert (runtime / "goals" / GOAL_ID / "runs" / "2026-01-01T00-00-00-00-00.json").read_text(
                encoding="utf-8"
            ) == '{"existing": true}\n'
            records = [
                json.loads(line)
                for line in (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").read_text(
                    encoding="utf-8"
                ).splitlines()
            ]
            assert len(records) == 2, records
            assert records[-1]["json_path"] == str(json_path), records[-1]
    finally:
        state_refresh.now_local = original_now_local

    print("refresh-state-unique-run-path-smoke ok")


if __name__ == "__main__":
    main()
