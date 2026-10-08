"""A cold source can be inventoried without manufacturing capture history."""
import json
import subprocess
import sys

import pytest

from tests.control_plane.shadow_e2e_fixture import workspace


def cold_workspace(tmp_path):
    fixture = workspace(tmp_path, bootstrap=False)
    registry = json.loads(fixture.registry.read_text())
    registry["goals"][0]["coordination"].pop("runtime_shadow")
    fixture.registry.write_text(json.dumps(registry))
    fixture.state.write_text(
        "---\ngoal_id: goal-e2e\nhandoff_mode: soft_claim\n---\n"
        "## Agent Todo\n- [ ] Keep active\n"
        "  <!-- loopx:todo todo_id=todo_active role=agent status=open claimed_by=agent-a -->\n"
        "## Todo Archive\n- [x] Keep unrelated archived evidence\n"
        "  <!-- loopx:todo todo_id=todo_archived role=agent status=done note=kept watch_only=false -->\n"
    )
    return fixture


def test_inventory_preserves_unreferenced_archive_without_shadow_or_effects(tmp_path):
    fixture = cold_workspace(tmp_path)
    before = {p: p.read_bytes() for p in (fixture.registry, fixture.state)}
    result = fixture.cli("coordination-shadow", "inspect-source")
    inventory = result["source_inventory"]
    assert inventory["status"] == "inspected"
    assert inventory["active_todo_count"] == 1
    assert inventory["archived_todo_count"] == 1
    archived = next(t for t in inventory["projection"]["todos"] if t["todo_id"] == "todo_archived")
    assert archived["text"] == "Keep unrelated archived evidence"
    assert archived["note"] == "kept" and archived["watch_only"] == "false"
    assert inventory["import_ready"] is False
    assert inventory["writer_stop_verified"] is False
    assert inventory["outbox_reconciliation_verified"] is False
    assert result["executed"] is False
    assert not (fixture.runtime / "authority-shadow").exists()
    assert not (fixture.runtime / "authority").exists()
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize("original, todo_id, heading", [
    ("Keep unrelated archived evidence", "todo_archived", "Todo Archive"),
    ("Keep active", "todo_active", "Agent Todo"),
])
def test_source_text_is_not_an_attention_summary(tmp_path, original, todo_id, heading):
    fixture = cold_workspace(tmp_path)
    full_text = "Preserve " + "explicit recovery requirement " * 70
    fixture.state.write_text(fixture.state.read_text().replace(original, full_text))
    inventory = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]
    archive = next(t for t in inventory["projection"]["todos"] if t["todo_id"] == todo_id)
    assert archive["text"] == full_text.strip()
    assert archive["source_section"] == heading


def test_stale_source_is_rejected_by_native_owner(tmp_path):
    from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
    from loopx.control_plane.coordination.local_authority_shadow_projection import source_effect_runtime_result

    fixture = cold_workspace(tmp_path)
    goal = json.loads(fixture.registry.read_text())["goals"][0]
    projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=fixture.runtime,
        state_path=fixture.state, registry_path=fixture.registry, include_all_archived_todos=True)
    fixture.state.write_text(fixture.state.read_text() + "\nNew source bytes\n")
    result = source_effect_runtime_result("coordination.source.inspect", {
        "schema_version": "loopx_cold_source_inspection_request_v0", "goal_id": fixture.goal,
        "runtime_root": str(fixture.runtime), "projection": projection, "source_snapshot": snapshot})
    assert result["status"] == "failed" and result["reason_code"] == "source_changed_retry"
    assert result["import_ready"] is False


def test_selected_canonical_source_is_not_reinterpreted_as_markdown(tmp_path):
    import hashlib

    fixture = cold_workspace(tmp_path)
    marker = fixture.runtime / "authority" / ("provider-" + hashlib.sha256(fixture.goal.encode()).hexdigest() + ".json")
    marker.parent.mkdir(parents=True)
    marker.write_text('{"provider":"sqlite"}')
    result = fixture.cli("coordination-shadow", "inspect-source", success=False)
    assert result["source_inventory"]["reason_code"] == "cold_source_canonical_authority_present"
    assert marker.read_text() == '{"provider":"sqlite"}'


def test_existing_capture_still_excludes_unreferenced_archives(tmp_path):
    from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot

    fixture = cold_workspace(tmp_path)
    goal = json.loads(fixture.registry.read_text())["goals"][0]
    projection, _ = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=fixture.runtime,
        state_path=fixture.state, registry_path=fixture.registry)
    assert [t["todo_id"] for t in projection["todos"]] == ["todo_active"]


def test_expired_orphan_active_lease_still_requires_settlement(tmp_path):
    fixture = cold_workspace(tmp_path)
    directory = fixture.runtime / "goals" / fixture.goal / "task-leases"
    directory.mkdir(parents=True)
    lease = {"schema_version": "task_lease_v0", "goal_id": fixture.goal,
             "todo_id": "removed", "owner": "agent-a", "status": "active",
             "idempotency_key": "old-work", "version": 4, "lease_epoch": 2,
             "expires_at": "2000-01-01T00:00:00Z", "write_scopes": []}
    path = directory / "removed.json"
    path.write_text(json.dumps(lease))
    before = path.read_bytes()
    inventory = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]
    assert inventory["lease_file_count"] == 1
    assert inventory["leases_requiring_settlement"] == ["removed"]
    assert inventory["retained_leases"] == [lease]
    assert inventory["projection"]["leases"] == []
    assert inventory["import_ready"] is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("corruption", ["duplicate_archive", "unknown_archive_role", "invalid_orphan_lease"])
def test_ambiguous_or_unsupported_history_is_not_an_empty_source(tmp_path, corruption):
    fixture = cold_workspace(tmp_path)
    if corruption == "duplicate_archive":
        fixture.state.write_text(fixture.state.read_text().replace("todo_id=todo_archived", "todo_id=todo_active"))
    elif corruption == "unknown_archive_role":
        fixture.state.write_text(fixture.state.read_text().replace("todo_id=todo_archived role=agent", "todo_id=todo_archived"))
    else:
        directory = fixture.runtime / "goals" / fixture.goal / "task-leases"
        directory.mkdir(parents=True)
        (directory / "removed.json").write_text(json.dumps({"goal_id": fixture.goal, "todo_id": "removed"}))
    result = fixture.cli("coordination-shadow", "inspect-source", success=False)
    assert result["ok"] is False
    assert not (fixture.runtime / "authority").exists()


def test_source_inspection_has_no_execute_switch(tmp_path):
    fixture = cold_workspace(tmp_path)
    result = subprocess.run([sys.executable, "-m", "loopx.cli",
                             *fixture.arguments("coordination-shadow", "inspect-source", "--execute")],
                            capture_output=True, text=True)
    assert result.returncode == 2 and "unrecognized arguments" in result.stderr


@pytest.mark.parametrize("kind", ["unsupported_name", "directory", "symlink"])
def test_unsafe_lease_inventory_is_not_silently_omitted(tmp_path, kind):
    fixture = cold_workspace(tmp_path)
    directory = fixture.runtime / "goals" / fixture.goal / "task-leases"
    directory.mkdir(parents=True)
    if kind == "unsupported_name":
        (directory / "历史.json").write_text("{}")
    elif kind == "directory":
        (directory / "todo_removed.json").mkdir()
    else:
        target = tmp_path / "retained.json"
        target.write_text("{}")
        (directory / "todo_removed.json").symlink_to(target)
    result = fixture.cli("coordination-shadow", "inspect-source", success=False)
    assert result["ok"] is False
    assert "regular files" in result.get("error", "") or "unsupported filename" in result.get("error", "")
