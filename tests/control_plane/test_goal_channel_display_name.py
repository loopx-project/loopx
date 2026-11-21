"""Goal channels retain the registry-owned name through the scoped status CLI."""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from loopx.control_plane.goals.goal_channel_projection import build_goal_channel_projection
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from test_goal_channel_hard_lease_visibility import GOAL_ID, _write_status_workspace


@pytest.mark.parametrize(
    ("registered_name", "status_item", "expected"),
    [
        ("Release readiness", {}, "Release readiness"),
        ("Current name", {"display_name": "Old row name", "project_asset": {"display_name": "Old asset name"}}, "Current name"),
        (None, {"display_name": "Row name", "project_asset": {"display_name": "Asset name"}}, "Asset name"),
        ("  ", {"display_name": "Row name"}, "Row name"),
        (None, {"domain": "Public domain"}, "Public domain"),
        (None, {}, GOAL_ID),
        ("secret-value", {"display_name": "Safe name"}, "Safe name"),
    ],
)
def test_channel_name_uses_registry_authority_and_preserves_safe_fallbacks(
    tmp_path, registered_name, status_item, expected,
):
    projection = build_goal_channel_projection(
        goal_id=GOAL_ID,
        status_item=status_item,
        run_history_goal={"id": GOAL_ID, "display_name": registered_name},
        runtime_root=tmp_path,
    )
    assert projection["display_name"] == expected
    assert projection["goal_id"] == GOAL_ID


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_scoped_status_cli_reads_registry_name_and_rename(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, repo, runtime = _write_status_workspace(tmp_path)
    state = repo / "ACTIVE_GOAL_STATE.md"
    projection = build_todo_runtime_shadow_projection(goal_id=GOAL_ID, handoff_mode="legacy", todos=[])
    initialize_canonical_authority(runtime, GOAL_ID, projection, state_path=state, provider=provider)
    original_state = state.read_bytes()
    config = json.loads(registry.read_text(encoding="utf-8"))

    for name in ["Release readiness", "Release verification"]:
        config["goals"][0]["display_name"] = name
        registry.write_text(json.dumps(config), encoding="utf-8")
        original_registry = registry.read_bytes()
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
             "status", "--goal-id", GOAL_ID, "--limit", "1"],
            capture_output=True, text=True, timeout=90,
        )
        assert result.returncode == 0, result.stderr
        payload = json.loads(result.stdout)
        assert payload["goal_filter"] == GOAL_ID
        goal = next(row for row in payload["run_history"]["goals"] if row["id"] == GOAL_ID)
        item = next(row for row in payload["attention_queue"]["items"] if row["goal_id"] == GOAL_ID)
        assert goal["display_name"] == name
        assert item["goal_channel_projection"]["display_name"] == name
        assert registry.read_bytes() == original_registry
        assert state.read_bytes() == original_state
