"""Canonical archives retain promoted history, not the complete old source.

This characterizes the existing qualified shadow route. It cannot qualify a
cold Markdown import or authorize removal of historical source readers.
"""
from __future__ import annotations

import json
import subprocess
import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from shadow_e2e_fixture import REPO, workspace
from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.rollout_event_log import ROLLOUT_EVENT_SCHEMA_VERSION


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_promoted_archive_scope_and_later_write_recovery(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    ws = workspace(tmp_path / "source", bootstrap=False)
    ws.state.write_text(
        "---\ngoal_id: goal-e2e\nhandoff_mode: hard_lease\n---\n\n## Agent Todo\n"
        "- [ ] Continue work\n"
        "  <!-- loopx:todo todo_id=todo_active task_class=advancement_task resume_when=todo_done:todo_prior -->\n"
        "- [x] Retained work\n"
        "  <!-- loopx:todo todo_id=todo_retained task_class=advancement_task completion_receipt_id=receipt-retained -->\n"
        "\n## Completed Work Archive\n"
        "- [x] Referenced work\n"
        "  <!-- loopx:todo todo_id=todo_prior role=agent task_class=advancement_task completion_receipt_id=receipt-prior -->\n"
        "- [x] Unreferenced historical result\n"
        "  <!-- loopx:todo todo_id=todo_unrelated role=agent task_class=advancement_task completion_receipt_id=receipt-unrelated -->\n"
    )
    terminal = {
        "schema_version": "task_lease_v0", "goal_id": ws.goal,
        "todo_id": "todo_retained", "owner": "agent-a", "version": 2,
        "lease_epoch": 1, "status": "released",
        "released_at": "2026-09-01T00:00:00Z", "idempotency_key": "old-retained",
        "future_extension": {"missing_value": None, "flag": False},
    }
    orphan = {**terminal, "todo_id": "todo_unrelated", "idempotency_key": "old-orphan"}
    directory = ws.runtime / "goals" / ws.goal / "task-leases"
    directory.mkdir(parents=True)
    for record in (terminal, orphan):
        (directory / (record["todo_id"] + ".json")).write_text(json.dumps(record))
    lease_bytes = {path: path.read_bytes() for path in directory.glob("*.json")}
    rollout = directory.parent / "rollout-event-log.jsonl"
    original_receipt = json.dumps({
        "schema_version": ROLLOUT_EVENT_SCHEMA_VERSION, "kind": "todo_complete",
        "goal_id": ws.goal, "todo_id": "todo_unrelated",
        "timestamp": "2026-09-01T00:00:00Z", "receipt_id": "original-unrelated-receipt",
    }) + "\n"
    rollout.write_text(original_receipt)
    if provider == "sqlite":
        selected = subprocess.run([
            "node", "--no-warnings", "--experimental-strip-types", "--experimental-sqlite",
            str(REPO / "loopx/control_plane/coordination/local_authority_provider.ts"),
            "--runtime-root", str(ws.runtime), "--goal-id", ws.goal, "--execute",
        ], cwd=REPO, capture_output=True, text=True, timeout=45, check=True)
        assert json.loads(selected.stdout)["provider"] == "sqlite"

    assert ws.cli("coordination-shadow", "bootstrap", "--execute")["bootstrap"]["status"] == "applied"
    cold_bytes = ws.state.read_bytes()
    refused = ws.cli("coordination-shadow", "promote", success=False)
    assert refused["ok"] is False
    assert refused["promotion"]["reason_code"] == "local_authority_shadow_not_qualified"
    assert ws.state.read_bytes() == cold_bytes

    # Real warm-profile operations are not evidence of a cold import.
    captured = {ws.add(f"Independent qualification work {index}")["todo_id"] for index in range(3)}
    assert ws.drain(budget_seconds="60")["ok"] is True
    preview = ws.cli("coordination-shadow", "promote")
    assert preview["promotion"]["status"] == "preview_ready"
    reviewed = tmp_path / "reviewed.json"
    reviewed.write_text(json.dumps(preview))
    source_bytes = ws.state.read_bytes()
    promoted = ws.cli("coordination-shadow", "promote", "--reviewed-plan", str(reviewed), "--execute")
    assert promoted["promotion"]["canonical_authority"] == f"{provider}_v0"
    assert ws.state.read_bytes() == source_bytes
    assert {path: path.read_bytes() for path in lease_bytes} == lease_bytes
    assert rollout.read_text().startswith(original_receipt)
    assert ws.cli("task-lease", "inspect", "--todo-id", "todo_retained")["lease"] == terminal

    later = ws.add("Later canonical work")
    ws.state.unlink()  # Only the disposable source: canonical recovery must not need it.
    assert restart_effect_runtime()["status"] in {"stopped", "not_running"}
    recovered = ws.cli("coordination-shadow", "recover-promotion", "--reviewed-plan", str(reviewed), "--execute")
    assert recovered["promotion"]["status"] == "replayed"
    current = ws.cli("todo", "list", "--todo-id", later["todo_id"])
    assert current["todo"]["text"] == "Later canonical work"
    assert current["authority_read"]["legacy_fallback_used"] is False
    assert not ws.state.exists()

    archive = tmp_path / "authority.ndjson"
    exported = ws.cli("authority-archive", "export", "--archive", str(archive))
    assert exported["status"] == "exported"
    assert "original-unrelated-receipt" not in archive.read_text()
    digest = exported["archive"]["archive_sha256"]
    for target in ("file", "sqlite"):
        destination = tmp_path / f"restore-{target}"
        restored = ws.cli("authority-archive", "restore", "--archive", str(archive),
            "--archive-sha256", digest, "--destination", str(destination), "--provider", target, "--execute")
        assert restored["status"] == "restored"
        assert restored["execution_authority_granted"] is False
        assert restored["requires_separate_authority_cutover"] is True
        audit = ws.cli("authority-archive", "audit", "--archive", str(archive),
            "--archive-sha256", digest, "--destination", str(destination))
        assert audit["audit"]["status"] == "matched"

        # Independently reopen the real restored store, including the other provider.
        module = REPO / f"loopx/control_plane/coordination/{target}_authority_store.ts"
        owner = "FileAuthorityStore" if target == "file" else "SqliteAuthorityStore"
        script = (
            f"import {{{owner}}} from {json.dumps(module.as_uri())};"
            f"const store=new {owner}({json.dumps(str(destination / 'store'))},{json.dumps(ws.goal)});"
            "process.stdout.write(JSON.stringify(await store.loadAuthority()));"
        )
        process = subprocess.run([
            "node", "--no-warnings", "--experimental-strip-types", "--experimental-sqlite",
            "--input-type=module", "-e", script,
        ], capture_output=True, text=True, timeout=45, check=True)
        head = json.loads(process.stdout)["head"]
        assert {row["todo_id"] for row in head["todos"]} == {
            "todo_active", "todo_retained", "todo_prior", later["todo_id"],
        } | captured
        prior = next(row for row in head["todos"] if row["todo_id"] == "todo_prior")
        assert prior["archive_state"] == "archive"
        assert prior["completion_receipt_id"] == "receipt-prior"
        assert head["leases"] == [terminal]
