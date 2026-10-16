"""Whole-source backup is distinct from canonical projection or reactivation."""
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    yield
    restart_effect_runtime()


@pytest.mark.parametrize("absolute_routes", [False, True])
@pytest.mark.parametrize("sqlite_history", [False, True])
def test_real_cli_retains_registered_cold_source_and_raw_history(
    tmp_path, absolute_routes, sqlite_history,
):
    project = tmp_path / "project"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    state = project / "history/OLD_GOAL.md"
    state.parent.mkdir()
    original_state = (
        "# Original Goal\n\n## Agent Todos\n- [ ] unfinished\n\n"
        "## Completed Work Archive\n- [x] unreferenced completion\n"
        "  metadata: {\"unknown\":null,\"disabled\":false}\n"
    ).encode()
    state.write_bytes(original_state)
    source_registry = project / "settings/source.json"
    source_registry.parent.mkdir()
    goal = {"id": "old-goal", "repo": str(project),
            "state_file": str(state) if absolute_routes else "history/OLD_GOAL.md",
            "extension": {"unknown": None, "disabled": False}}
    source_registry.write_text(json.dumps({"goals": [goal]}))
    registry.write_text(json.dumps({"goals": [{**goal, "source_registry":
        str(source_registry) if absolute_routes else "settings/source.json"}]}))
    runtime = tmp_path / "runtime"
    raw_files = {
        "leases/old-goal/orphan.json": b'{"todo_id":"unreferenced","status":"released"}',
        "outbox/old-goal/pending.json": b'{"status":"prepared","original_operation":"old"}',
        "receipts/old-goal/original.json": b'{"result":{"unknown":null},"original":true}',
    }
    for relative, content in raw_files.items():
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    database = runtime / "history.data"
    if sqlite_history:
        with sqlite3.connect(database) as writer:
            writer.execute("CREATE TABLE original_receipts(operation TEXT PRIMARY KEY, result TEXT)")
            writer.execute("INSERT INTO original_receipts VALUES('original', '{\"unknown\":null}')")
            writer.commit()
    args = [sys.executable, "-m", "loopx.cli", "--format", "json",
            "--runtime-root", str(runtime), "backup-state", "--project", str(project),
            "--output-dir", str(tmp_path / "backups"), "--backup-id", "cold-source",
            "--current-project-only", "--no-skills", "--no-automations", "--execute"]
    result = subprocess.run(args, env={**os.environ, "LOOPX_USAGE_PING": "0"},
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    # Independently extract inert bytes: no import, lease adoption or activation.
    restored = tmp_path / "isolated-copy"
    with tarfile.open(payload["archive_path"]) as archive:
        archive.extractall(restored, filter="data")
    expected = {"registry_active_state:old-goal": original_state,
                "registry_source_registry:old-goal": source_registry.read_bytes()}
    routes = {row["key"]: row["archive_path"] for row in payload["included"]}
    for key, content in expected.items():
        assert key in routes, f"Registered source omitted: {key}"
        assert (restored / routes[key]).read_bytes() == content
    for relative, content in raw_files.items():
        assert (restored / "runtime-root" / relative).read_bytes() == content
        assert (runtime / relative).read_bytes() == content
    if sqlite_history:
        with sqlite3.connect(restored / "runtime-root/history.data") as copy:
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert copy.execute("SELECT * FROM original_receipts").fetchall() == [
                ("original", '{"unknown":null}')]
    assert state.read_bytes() == original_state


def test_current_project_inventory_reports_missing_routes_without_following_other_projects(tmp_path):
    project, other = tmp_path / "selected", tmp_path / "other"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    other.mkdir()
    (other / "secret.md").write_text("belongs to another project")
    registry.write_text(json.dumps({"goals": [
        {"id": "selected", "repo": str(project), "state_file": "missing.md",
         "source_registry": "missing-registry.json"},
        {"id": "other", "repo": str(other), "state_file": "secret.md"},
    ]}))
    plan = build_state_backup_plan(project=project, runtime_root=tmp_path / "runtime",
        include_registry_projects=False, include_skills=False, include_automations=False)
    missing = {row["key"] for row in plan["missing"]}
    assert {"registry_active_state:selected", "registry_source_registry:selected"} <= missing
    assert not any(row["key"].endswith(":other") for row in plan["included"] + plan["missing"])


def test_global_inventory_still_follows_registered_other_project(tmp_path):
    project, other, runtime = tmp_path / "selected", tmp_path / "other", tmp_path / "runtime"
    other.mkdir()
    runtime.mkdir()
    state = other / "custom.md"
    state.write_text("registered on this host")
    (runtime / "registry.global.json").write_text(json.dumps({"goals": [
        {"id": "other", "repo": str(other), "state_file": str(state)}]}))
    plan = build_state_backup_plan(project=project, runtime_root=runtime,
        include_skills=False, include_automations=False)
    assert any(row["key"] == "registry_active_state:other" for row in plan["included"])


def test_backup_retains_original_registry_outside_conventional_directories(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    registry = tmp_path / "original-registry.json"
    original = b'{"goals": [], "common_runtime_root": "retained route"}\n'
    registry.write_bytes(original)
    result = execute_state_backup_plan(build_state_backup_plan(project=project, runtime_root=runtime,
        output_dir=tmp_path / "saved", backup_id="original-registry", include_automations=False,
        include_skills=False, include_registry_projects=False, registry_path=registry))
    assert result["ok"]
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    source = next(item for item in manifest["included"] if item["source_path"] == str(registry))
    with tarfile.open(result["archive_path"], "r:gz") as archive:
        assert archive.extractfile(source["archive_path"]).read() == original
