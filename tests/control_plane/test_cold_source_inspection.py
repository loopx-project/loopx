"""A cold source can be inventoried without manufacturing capture history."""
import json
import hashlib
from pathlib import Path
import subprocess
import sys

import pytest

from tests.control_plane.shadow_e2e_fixture import workspace


def capture_bytes(fixture):
    """Original history files; transient lock files are not receipt sources."""
    return {str(p.relative_to(fixture.runtime)): p.read_bytes()
            for root in (fixture.runtime / "authority-shadow", fixture.runtime / "authority-transition")
            for p in root.rglob("*") if p.is_file() and not p.name.endswith(".lock")}


def test_capture_inventory_keeps_original_history_and_outbox_bytes(tmp_path):
    fixture = workspace(tmp_path)
    fixture.add("Original captured operation")
    outbox = fixture.runtime / "authority-shadow" / "outbox" / fixture.goal / "todos"
    outbox.mkdir(exist_ok=True)
    residue = outbox / "unrecognized-original-receipt.json"
    residue.write_bytes(b"{malformed original bytes")
    before = capture_bytes(fixture)
    result = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]
    capture = result["capture"]
    assert capture["management_state"]["status"] == "active"
    assert capture["runtime_shadow_readback"]["status"] == "loaded"
    assert capture["runtime_shadow_readback"]["proof"]["last_applied_sequences"]["todos"] >= 1
    witness = capture["artifacts"]["runtime_store"]
    original = next(data for path, data in before.items()
                    if path == str(Path(witness["path"]).relative_to(fixture.runtime)))
    assert witness["sha256"] == "sha256:" + hashlib.sha256(original).hexdigest()
    entries = capture["artifacts"]["outbox"]["inventory"]["entries"]
    raw = next(e for e in entries if e["path"] == "todos/" + residue.name)
    assert raw["sha256"] == "sha256:" + hashlib.sha256(residue.read_bytes()).hexdigest()
    assert result["outbox_reconciliation_verified"] is False
    assert result["import_ready"] is False
    review = capture["outbox_review"]
    assert review["status"] == "failed"
    assert review["reason_code"] == "outbox_file_invalid"
    assert review["executed"] is False
    assert before == capture_bytes(fixture)
    assert not (fixture.runtime / "authority").exists()


def test_capture_inventory_keeps_rolled_back_operation_archives(tmp_path):
    fixture = workspace(tmp_path)
    fixture.add("Original operation before rollback")
    revision = fixture.cli("coordination-shadow", "inspect")["inspection"]["provider_revision"]
    rollback = fixture.cli("coordination-shadow", "rollback", "--provider-revision", revision, "--execute")
    assert rollback["rollback"]["status"] == "applied"
    before = capture_bytes(fixture)
    capture = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]["capture"]
    assert capture["management_state"]["status"] == "inactive"
    assert capture["artifacts"]["runtime_store"] is None
    assert capture["runtime_shadow_readback"] is None
    assert capture["outbox_review"] is None
    archives = capture["artifacts"]["rollback_archives"]
    assert len(archives) == 1
    assert archives[0]["path"] == rollback["rollback"]["candidate_archive_path"]
    assert archives[0]["sha256"] == "sha256:" + hashlib.sha256(Path(archives[0]["path"]).read_bytes()).hexdigest()
    assert before == capture_bytes(fixture)


@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
def test_original_lease_outbox_uses_its_own_partition_without_renewal_or_cleanup(tmp_path, window):
    fixture = workspace(tmp_path)
    todo_id = fixture.add("Original leased task")["todo_id"]
    fixture.crash(window, "task-lease", "acquire", "--todo-id", todo_id,
                  "--owner", "agent-a", "--idempotency-key", "original-lease", "--ttl-seconds", "120")
    before = capture_bytes(fixture)
    lease_path = fixture.runtime / "goals" / fixture.goal / "task-leases" / (todo_id + ".json")
    lease_bytes = lease_path.read_bytes()
    result = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]
    review = result["capture"]["outbox_review"]
    assert review["status"] == "planned" and review["executed"] is False
    leases = next(p for p in review["partitions"] if p["partition"] == "leases")
    assert len(leases["pending_entry_ids"]) == (1 if window == "before_commit" else 0)
    assert len(leases["reclaim_entry_ids"]) == (0 if window == "before_commit" else 1)
    assert leases["next_seq"] == (1 if window == "before_commit" else 2)
    assert todo_id in result["leases_requiring_settlement"]
    assert lease_path.read_bytes() == lease_bytes and before == capture_bytes(fixture)


@pytest.mark.parametrize("corrupt", [False, True])
def test_capture_inventory_reads_original_observation_through_existing_store(tmp_path, corrupt):
    fixture = workspace(tmp_path)
    source = fixture.runtime / "authority-shadow" / "file-v0"
    retained = fixture.runtime / "authority-shadow" / "file" / fixture.goal
    retained.mkdir(parents=True)
    # A synthetic historical copy of a real provider document, not a mock or
    # an import receipt. The historical reader owns its schema validation.
    for path in source.iterdir():
        if path.is_file() and not path.name.endswith(".lock"):
            (retained / path.name).write_bytes(path.read_bytes())
    if corrupt:
        next(retained.glob("authority-store-*.json")).write_text('{"invalid":true}')
    before = capture_bytes(fixture)
    result = fixture.cli("coordination-shadow", "inspect-source", success=not corrupt)
    if corrupt:
        assert result["source_inventory"]["status"] == "failed"
    else:
        capture = result["source_inventory"]["capture"]
        assert capture["legacy_observation_readback"]["status"] == "loaded"
        assert capture["artifacts"]["legacy_store"]["path"].startswith(str(retained))
        assert result["source_inventory"]["import_ready"] is False
    assert before == capture_bytes(fixture)


@pytest.mark.parametrize("unsafe", ["corrupt_history", "symlink_outbox", "symlink_shadow_root", "invalid_original_result"])
def test_capture_inventory_refuses_unreadable_or_unsafe_original_history(tmp_path, unsafe):
    fixture = workspace(tmp_path)
    if unsafe == "corrupt_history":
        store = next((fixture.runtime / "authority-shadow" / "file-v0").glob("authority-store-*.json"))
        store.write_text('{"invalid":true}')
    elif unsafe == "symlink_outbox":
        outbox = fixture.runtime / "authority-shadow" / "outbox" / fixture.goal
        (outbox / "external").symlink_to(fixture.state)
    elif unsafe == "symlink_shadow_root":
        shadow = fixture.runtime / "authority-shadow"
        original = tmp_path / "external-shadow"
        shadow.rename(original)
        shadow.symlink_to(original, target_is_directory=True)
    else:
        state = next((fixture.runtime / "authority-transition").rglob("state.json"))
        original = json.loads(state.read_text())
        original["result"]["primary_writeback_preserved"] = False
        state.write_text(json.dumps(original))
    before = capture_bytes(fixture)
    result = fixture.cli("coordination-shadow", "inspect-source", success=False)
    assert result["ok"] is False
    assert result["source_inventory"]["status"] == "failed"
    assert result["source_inventory"]["import_ready"] is False
    assert before == capture_bytes(fixture)


@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
def test_capture_inventory_does_not_drain_crashed_original_outbox(tmp_path, window):
    fixture = workspace(tmp_path)
    fixture.crash(window, "todo", "add", "--role", "agent", "--text", "Retained original operation")
    before = capture_bytes(fixture)
    result = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]
    entries = result["capture"]["artifacts"]["outbox"]["inventory"]["entries"]
    assert any(e["path"].endswith(".prepared.json") for e in entries)
    assert any(e["path"].endswith(".committed.json") for e in entries)
    assert result["import_ready"] is False and result["outbox_reconciliation_verified"] is False
    review = result["capture"]["outbox_review"]
    assert review["status"] == "planned" and review["executed"] is False
    plans = review["partitions"]
    todos = next(p for p in plans if p["partition"] == "todos")
    assert len(todos["pending_entry_ids"]) == (1 if window == "before_commit" else 0)
    assert len(todos["reclaim_entry_ids"]) == (0 if window == "before_commit" else 1)
    assert len(todos["replay_entries"]) == (0 if window == "before_commit" else 1)
    assert result["writer_stop_verified"] is False
    assert before == capture_bytes(fixture)


@pytest.mark.parametrize("defect", ["receipt_bytes", "orphan_marker", "foreign_lineage"])
def test_outbox_review_refuses_unproved_disposition_but_retains_original_bytes(tmp_path, defect):
    fixture = workspace(tmp_path)
    window = "after_commit" if defect == "receipt_bytes" else "before_commit"
    fixture.crash(window, "todo", "add", "--role", "agent", "--text", "Original operation")
    directory = fixture.runtime / "authority-shadow" / "outbox" / fixture.goal / "todos"
    prepared = next(directory.glob("*.prepared.json"))
    if defect == "orphan_marker":
        prepared.unlink()
    else:
        record = json.loads(prepared.read_text())
        if defect == "receipt_bytes":
            record["writer"]["operation_id"] = "different-original-operation"
        else:
            record["capture_lineage_id"] = "foreign-lineage"
        prepared.write_text(json.dumps(record))
    before = capture_bytes(fixture)
    result = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]
    review = result["capture"]["outbox_review"]
    assert review["status"] == "failed"
    assert review["reason_code"] == ("outbox_receipt_mismatch" if defect == "receipt_bytes" else "outbox_file_invalid")
    assert review["executed"] is False
    assert result["import_ready"] is False and result["outbox_reconciliation_verified"] is False
    assert before == capture_bytes(fixture)


@pytest.mark.parametrize("archive", ["candidate", "outbox"])
def test_capture_inventory_refuses_missing_terminal_rollback_archive(tmp_path, archive):
    fixture = workspace(tmp_path)
    revision = fixture.cli("coordination-shadow", "inspect")["inspection"]["provider_revision"]
    result = fixture.cli("coordination-shadow", "rollback", "--provider-revision", revision, "--execute")
    if archive == "candidate":
        Path(result["rollback"]["candidate_archive_path"]).unlink()
    else:
        Path(result["rollback"]["outbox_archive_path"], "manifest.json").unlink()
    before = capture_bytes(fixture)
    refused = fixture.cli("coordination-shadow", "inspect-source", success=False)
    assert refused["source_inventory"]["reason_code"] == "rollback_archive_readback_mismatch"
    assert before == capture_bytes(fixture)


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
    assert inventory["capture"]["outbox_review"] is None
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
    assert result["error"] == "source_lease_inventory_invalid"
    assert not (fixture.runtime / "authority").exists()
    assert not (fixture.runtime / "authority-shadow").exists()
