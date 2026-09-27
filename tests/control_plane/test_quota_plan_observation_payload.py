"""Read-only plans are bounded displays with an explicit lossless detail path."""
from __future__ import annotations

import json
import subprocess
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

from loopx.cli_commands.quota_request import quota_detail_sections_from_args
from loopx.cli_runtime import _build_selected_parser
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.quota.cli_projection import compact_quota_plan_cli_payload
from loopx.control_plane.testing.canary_harness import write_fixture_registry

REPO = Path(__file__).resolve().parents[2]


def test_plan_projection_retains_decisions_health_and_input_without_mutation():
    items = [{"todo_id": str(i), "status": "open", "text": "Work", "note": "detail" * 100} for i in range(40)]
    agent = {"total_count": 40, "open_count": 40, "items": items,
             "first_executable_items": items[:4], "monitor_due_count": 5,
             "monitor_due_items": items[:5], "blocker_count": 2}
    row = {"goal_id": "goal", "quota": {"state": "eligible", "allowed_slots": 2},
           "agent_todos": agent, "user_todos": {"items": items, "open_count": 40}}
    payload = {"mode": "status", "ok": False, "groups": {"eligible": [row]},
               "next_automatic_turn": row, "health_items": [{"severity": "error"}],
               "summary": {"registered_goals": 1}, "status_projection_envelope": {"coverage": {"scope": "goal"}}}
    original = deepcopy(payload)
    compact = compact_quota_plan_cli_payload(payload)
    assert payload == original
    result = compact["groups"]["eligible"][0]
    assert "items" not in result["agent_todos"]
    assert result["agent_todos"]["open_count"] == 40
    assert result["agent_todos"]["monitor_due_count"] == 5
    assert result["agent_todos"]["blocker_count"] == 2
    assert result["agent_todos"]["payload_compaction"]["omitted_lanes"]["items"] == 40
    assert "quota status --include-detail agent-todos" == result["agent_todos"]["payload_compaction"]["full_detail_cold_path"]
    assert compact["next_automatic_turn"] == result
    for key in ("health_items", "summary", "status_projection_envelope", "ok"):
        assert compact[key] == payload[key]
    assert result["quota"] == row["quota"]
    assert compact_quota_plan_cli_payload(payload, detail_sections=frozenset({"agent-todos", "user-todos"})) == original
    assert compact_quota_plan_cli_payload({"mode": "should-run", "agent_todo_summary": agent}) == {"mode": "should-run", "agent_todo_summary": agent}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_cli_compact_and_full_detail_preserve_canonical_todos(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("---\nstatus: active\nwaiting_on: codex\n---\n# Example\n\n## Agent Todo\n")
    write_fixture_registry(project=tmp_path, runtime_root=runtime, registry_path=registry,
        goal_id="example", domain="engineering", adapter_kind="generic_project_goal_v0",
        state_file=str(state), registered_agents=["worker"])
    records = [{"schema_version": "todo_item_v0", "todo_id": f"work-{i:03}",
        "role": "agent" if i < 40 else "user", "status": "open" if i % 3 else "done",
        "done": i % 3 == 0, "text": f"Retained work {i}", "note": "exact metadata🙂" * 100,
        "archive_state": "active", "source_section": "Agent Todo" if i < 40 else "User Todo",
        "index": i + 1, "task_class": "advancement_task"} for i in range(60)]
    projection = build_todo_runtime_shadow_projection(goal_id="example", todos=records, leases=[], handoff_mode="soft_claim")
    initialize_canonical_authority(runtime, "example", projection, state_path=state, provider=provider)

    def call(mode, *details):
        process = subprocess.run([sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json", "quota", mode, "--goal-id", "example",
            "--scan-root", str(tmp_path), *details], cwd=REPO, text=True, capture_output=True, timeout=60)
        value = json.loads(process.stdout)
        assert process.returncode == 0, value
        return value

    try:
        for mode in ("status", "plan"):
            compact, full = call(mode), call(mode, "--include-detail", "all")
            compact_row = next(row for group in compact["groups"].values() for row in group)
            full_row = next(row for group in full["groups"].values() for row in group)
            assert compact["summary"] == full["summary"]
            for role, count in (("agent", 40), ("user", 20)):
                c, f = compact_row[f"{role}_todos"], full_row[f"{role}_todos"]
                assert c["total_count"] == f["total_count"] == count
                assert "items" not in c
                actual = {item["todo_id"]: item for item in f["items"]}
                for record in records:
                    if record["role"] == role:
                        assert actual[record["todo_id"]]["note"] == record["note"]
                        assert actual[record["todo_id"]]["status"] == record["status"]
            assert len(json.dumps(compact)) < len(json.dumps(full)) / 2
        assert not list((runtime / "goals" / "example" / "runs").glob("*.json*"))
    finally:
        assert restart_effect_runtime()["status"] in {"stopped", "not_running"}


def test_plan_all_only_expands_observation_sections():
    args = _build_selected_parser("quota").parse_args(["quota", "plan", "--include-detail", "all"])
    assert quota_detail_sections_from_args(args) == frozenset({"agent-todos", "user-todos"})
