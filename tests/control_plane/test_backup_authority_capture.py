"""The ordinary producer must retain an owner-exported canonical prefix."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from loopx.control_plane.state_recovery_io import verify_state_backup
from tests.control_plane.canonical_authority_fixture import promoted_create_fixture


pytestmark = pytest.mark.usefixtures("isolated_effect_runtime")


def cli(*args: object) -> dict:
    result = subprocess.run([sys.executable, "-I", "-m", "loopx.cli", "--format", "json", *map(str, args)],
                            capture_output=True, text=True, check=True, timeout=90)
    return json.loads(result.stdout)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_full_backup_retains_native_authority_export_and_reads_it_after_source_loss(tmp_path, provider):
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    cli("--registry", registry, "--runtime-root", runtime, "todo", "add", "--goal-id", "goal-a",
        "--role", "agent", "--claimed-by", "agent-a", "--operation-id", "retained-work",
        "--text", "Preserve unfinished work and its original operation")
    backup = execute_state_backup_plan(build_state_backup_plan(project=state.parents[3], runtime_root=runtime,
        registry_path=registry, output_dir=tmp_path / "backups", backup_id="native-capture",
        include_registry_projects=False, include_automations=False, include_skills=False))
    [capture] = backup["execution"]["authority_captures"]
    assert capture["state"] == "readable", capture
    assert capture["goal_id"] == "goal-a"
    assert capture["archive"]["source_provider"] == provider
    assert capture["archive"]["commits"] == "2"
    assert capture["started_at"] <= capture["completed_at"]
    assert capture["dependencies"]["writer_fence_sha256"]
    assert capture["configuration_sha256"] == backup["execution"]["configuration_backup"]["sha256"]
    assert bool(capture["dependencies"]["selector_sha256"]) is (provider == "sqlite")
    for source in (runtime, state.parents[3], registry):
        source.rename(source.with_name(f"lost-{source.name}"))
    destination = tmp_path / "inert"
    audit = verify_state_backup(archive=Path(backup["archive_path"]), manifest=Path(backup["manifest_path"]),
                                destination=destination, execute=True)
    [authority] = [row for row in audit["components"] if row["owner_kind"] == "coordination_authority"]
    assert authority["state"] == "readable", authority
    assert authority["facts"]["todo_count"] == 1
    assert authority["facts"]["history_commits"] == 2
    assert authority["facts"]["capture_started_at"] == capture["started_at"]
    assert authority["facts"]["capture_completed_at"] == capture["completed_at"]
    assert audit["status"] == "held" and audit["activation_eligible"] is False
    assert audit["execution_authority_granted"] is False
    artifact = destination / "data" / capture["archive_path"]
    verified = cli("authority-archive", "verify", "--archive", artifact)
    assert verified["archive"] == capture["archive"]
    restored = cli("authority-archive", "restore", "--goal-id", "goal-a", "--archive", artifact,
        "--archive-sha256", capture["archive"]["archive_sha256"], "--provider", provider,
        "--destination", tmp_path / "standalone", "--execute")
    assert restored["status"] == "restored"
    checked = cli("authority-archive", "audit", "--goal-id", "goal-a", "--archive", artifact,
        "--archive-sha256", capture["archive"]["archive_sha256"], "--destination", tmp_path / "standalone")
    assert checked["audit"]["status"] == "matched"
    assert checked["audit"]["compared_commits"] == "2"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_missing_canonical_owner_is_not_replaced_by_the_earlier_raw_copy(tmp_path, monkeypatch, provider):
    from loopx.capabilities import configuration_backup

    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    original_capture = configuration_backup.capture_configuration_backup

    def lose_authority(**kwargs):
        configuration = original_capture(**kwargs)
        (runtime / "authority").rename(tmp_path / "lost-authority")
        return configuration

    monkeypatch.setattr(configuration_backup, "capture_configuration_backup", lose_authority)
    backup = execute_state_backup_plan(build_state_backup_plan(project=state.parents[3], runtime_root=runtime,
        registry_path=registry, output_dir=tmp_path / "backups", include_registry_projects=False,
        include_automations=False, include_skills=False))
    [capture] = backup["execution"]["authority_captures"]
    assert capture["state"] == "unknown" and capture["archive_path"] is None
    assert not (runtime / "authority").exists(), "capture must not initialize a missing authority"
    audit = verify_state_backup(archive=Path(backup["archive_path"]), manifest=Path(backup["manifest_path"]),
                                destination=tmp_path / "inert", execute=True)
    [authority] = [row for row in audit["components"] if row["owner_kind"] == "coordination_authority"]
    assert authority["state"] == "unknown" and authority["reason_code"] == "authority_capture_unavailable"
    assert audit["status"] == "held" and audit["execution_authority_granted"] is False
