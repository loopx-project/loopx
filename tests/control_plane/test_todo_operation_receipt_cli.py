"""The public CLI can inspect an exact canonical operation without retrying it."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from canonical_authority_fixture import initialize_canonical_authority
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)


def test_todo_receipt_cli_reads_history_without_writing_an_event(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    state_path = project / "ACTIVE_GOAL_STATE.md"
    state_path.write_text("# Goal\n\n## Agent Todo\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"schema_version": 1,
        "common_runtime_root": str(runtime_root), "goals": [{"id": "goal-a",
        "repo": str(project), "state_file": state_path.name,
        "coordination": {"registered_agents": ["agent-a"]}}]}), encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=[], handoff_mode="soft_claim",
    )
    initialize_canonical_authority(runtime_root, "goal-a", projection, state_path=state_path)

    base = [sys.executable, "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry_path), "todo", "receipt", "--goal-id", "goal-a"]
    found = subprocess.run([*base, "--operation-id", "canonical-fixture"],
        capture_output=True, text=True, timeout=30)
    assert found.returncode == 0, found.stdout + found.stderr
    payload = json.loads(found.stdout)
    assert payload["status"] == "found"
    assert payload["operation_id"] == "canonical-fixture"
    assert payload["source_authority"] == "file_v0"
    assert payload["decision_read_from_provider"] is True
    assert payload["receipts"] == []  # Bootstrap had no business receipt.

    missing = subprocess.run([*base, "--operation-id", "other-operation"],
        capture_output=True, text=True, timeout=30)
    assert missing.returncode == 0, missing.stdout + missing.stderr
    assert json.loads(missing.stdout)["status"] == "missing"
    assert not (runtime_root / "goals" / "goal-a" / "runs" / "index.jsonl").exists()
