"""The registry that selected backup dependencies must agree with copied bytes."""
import json
import os
import subprocess
import sys
import tarfile

import pytest

from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    yield
    restart_effect_runtime()


def _declare(registry, name):
    data = json.loads(registry.read_text())
    data["goals"][0]["authority_sources"] = [{"kind": "goal_doc", "path": name}]
    registry.write_text(json.dumps(data))


def _plan(project, runtime, registry, output):
    return build_state_backup_plan(project=project, runtime_root=runtime, registry_path=registry,
        output_dir=output, backup_id="capture", include_registry_projects=False,
        include_skills=False, include_automations=False)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_moved_required_document_refuses_publication_then_replans_after_source_loss(tmp_path, provider):
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    project = state.parents[3]
    (project / "INPUT.md").write_text("Original requirement")
    later = project / "LATER.md"
    later_bytes = "# New required material\n\n完整验收 🧭\n".encode()
    later.write_bytes(later_bytes)
    _declare(registry, "INPUT.md")
    output = tmp_path / "backups"
    frozen = _plan(project, runtime, registry, output)
    # Keep the exact JSON plan: private raw registry bytes are not a plan API.
    frozen = json.loads(json.dumps(frozen))
    _declare(registry, "LATER.md")
    with pytest.raises(ValueError, match="discovery source differs from copied bytes"):
        execute_state_backup_plan(frozen)
    assert not list(output.iterdir()), "a stale dependency plan must publish neither output"
    saved = execute_state_backup_plan(_plan(project, runtime, registry, output))
    route = next(row["archive_path"] for row in saved["included"] if row["source_path"] == str(later))
    for path in (project, runtime):
        path.rename(path.with_name("unavailable-" + path.name))
    destination = tmp_path / "inert"
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
        "backup-state", "verify", "--archive", saved["archive_path"], "--manifest", saved["manifest_path"],
        "--destination", str(destination), "--execute"], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    audit = json.loads(result.stdout)
    assert (destination / "data" / route).read_bytes() == later_bytes
    assert audit["capture_profile"] == "legacy_best_effort" and audit["status"] == "held"
    assert audit["activation_eligible"] is False and audit["execution_authority_granted"] is False


def test_discovery_witness_is_from_decoded_bytes_not_a_later_path_read(tmp_path, monkeypatch):
    from loopx import state_backup

    project, runtime = tmp_path / "project", tmp_path / "runtime"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    (project / "INPUT.md").write_text("original requirement")
    (project / "LATER.md").write_text("later requirement")
    registry.write_text(json.dumps({"goals": [{"id": "fixture", "repo": str(project)}]}))
    _declare(registry, "INPUT.md")
    original_load = state_backup.load_registry
    reads = []

    def change_after_decode(path, **kwargs):
        data = original_load(path, **kwargs)
        reads.append(path)
        _declare(registry, "LATER.md")
        return data

    monkeypatch.setattr(state_backup, "load_registry", change_after_decode)
    plan = _plan(project, runtime, registry, tmp_path / "backups")
    assert reads == [registry]
    assert any(row["source_path"] == str(project / "INPUT.md") for row in plan["included"])
    with pytest.raises(ValueError, match="discovery source differs from copied bytes"):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


def test_global_source_route_and_declarations_share_the_same_registry_read(tmp_path, monkeypatch):
    from loopx.control_plane.projects import registry_codec

    project, runtime, other_runtime = tmp_path / "project", tmp_path / "runtime", tmp_path / "other-runtime"
    source = project / ".loopx/registry.json"
    source.parent.mkdir(parents=True)
    runtime.mkdir()
    other_runtime.mkdir()
    for name in ("INPUT.md", "LATER.md"):
        (project / name).write_text(name)
    source.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": "fixture", "repo": str(project)}]}))
    _declare(source, "INPUT.md")
    global_registry = runtime / "registry.global.json"
    global_registry.write_text(json.dumps({"registry_role": "global-local", "goals": [
        {"id": "fixture", "repo": str(project), "source_registry": str(source)}]}))
    original_decode = registry_codec.decode_registry_snapshot
    decodes = []

    def change_after_route_read(path, raw):
        data = original_decode(path, raw)
        if path == source and not decodes:
            decodes.append(True)
            changed = json.loads(source.read_text())
            changed["common_runtime_root"] = str(other_runtime)
            changed["goals"][0]["authority_sources"][0]["path"] = "LATER.md"
            source.write_text(json.dumps(changed))
        return data

    monkeypatch.setattr(registry_codec, "decode_registry_snapshot", change_after_route_read)
    plan = build_state_backup_plan(project=project, runtime_root=runtime,
        output_dir=tmp_path / "backups", include_skills=False, include_automations=False)
    assert decodes == [True]
    assert any(row["source_path"] == str(project / "INPUT.md") for row in plan["included"])
    assert not any(row["source_path"] == str(other_runtime) for row in plan["included"])
    with pytest.raises(ValueError, match="discovery source differs from copied bytes"):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


@pytest.mark.skipif(os.name == "nt", reason="native Windows symlink privilege is not qualified")
def test_frozen_archive_routes_cannot_be_rebound_to_another_registry_alias(tmp_path):
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    directory = project / ".loopx"
    directory.mkdir(parents=True)
    first, second, registry = directory / "first.json", directory / "second.json", directory / "registry.json"
    for path, name in ((first, "INPUT.md"), (second, "LATER.md")):
        (project / name).write_text(name)
        path.write_text(json.dumps({"goals": [{"id": "fixture", "repo": str(project),
            "authority_sources": [{"kind": "goal_doc", "path": name}]}]}))
    registry.symlink_to(first.name)
    plan = _plan(project, runtime, registry, tmp_path / "backups")
    registry.unlink()
    registry.symlink_to(second.name)
    # Both regular files are captured independently; a fresh path resolution
    # would incorrectly let the unchanged first file witness the new alias.
    with pytest.raises(ValueError, match="discovery source differs from copied bytes"):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


def test_registry_absence_cannot_be_replaced_by_a_new_definition(tmp_path):
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    plan = _plan(project, runtime, registry, tmp_path / "backups")
    registry.write_text(json.dumps({"goals": []}))
    with pytest.raises(ValueError, match="discovery source absence differs from copied bytes"):
        execute_state_backup_plan(plan)
    assert not list((tmp_path / "backups").iterdir())


def test_real_cli_reports_changed_discovery_and_preserves_output_directory(tmp_path, monkeypatch, capsys):
    from loopx.cli import main
    from loopx.cli_commands import support_control_backup

    project, runtime = tmp_path / "project", tmp_path / "runtime"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"goals": [{"id": "fixture", "repo": str(project)}]}))
    for name in ("INPUT.md", "LATER.md"):
        (project / name).write_text(name)
    _declare(registry, "INPUT.md")
    original_plan = support_control_backup.build_state_backup_plan

    def change_after_planning(**kwargs):
        plan = original_plan(**kwargs)
        _declare(registry, "LATER.md")
        return plan

    monkeypatch.setattr(support_control_backup, "build_state_backup_plan", change_after_planning)
    output = tmp_path / "backups"
    assert main(["--format", "json", "--runtime-root", str(runtime), "backup-state",
        "--project", str(project), "--output-dir", str(output), "--current-project-only",
        "--no-skills", "--no-automations", "--execute"]) == 1
    refusal = json.loads(capsys.readouterr().out)
    assert refusal["ok"] is False
    assert refusal["error"] == "backup discovery source differs from copied bytes; retry the backup"
    assert "preserve existing archive" in refusal["recommended_action"]
    assert not list(output.iterdir())


@pytest.mark.parametrize("historical_plan", [False, True])
def test_unchanged_definition_does_not_freeze_unrelated_material_bytes(tmp_path, historical_plan):
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"goals": [{"id": "fixture", "repo": str(project)}]}))
    _declare(registry, "INPUT.md")
    document = project / "INPUT.md"
    document.write_text("first content")
    plan = _plan(project, runtime, registry, tmp_path / "backups")
    if historical_plan:
        plan["registry_discovery"].pop("source_capture")
    document.write_text("content present when copied")
    saved = execute_state_backup_plan(plan)
    route = next(row["archive_path"] for row in saved["included"] if row["source_path"] == str(document))
    with tarfile.open(saved["archive_path"]) as archive:
        assert archive.extractfile(route).read() == b"content present when copied"
    assert bool(saved["execution"].get("discovery_source_revisions")) is not historical_plan
