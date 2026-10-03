"""Public administrative journey: snapshot, verify, preview, restore and reopen.

The source is canonical fixture state; promotion itself is qualified separately.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime

REPO = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_archive_cli_complete_isolated_recovery(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    goal = "archive-goal"
    state.write_text("# Synthetic canonical projection\n")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [
        {"id": goal, "repo": str(tmp_path), "state_file": state.name}]}))
    projection = {"goal_id": goal, "retained": [{"id": "archived", "lease_epoch": 7}], "text": "中文原文"}
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    registry_before, state_before = registry.read_bytes(), state.read_bytes()
    # File bytes across the active tree prove no fence/selector/registry rewrite.
    def files():
        return {str(p.relative_to(runtime)): p.read_bytes() for p in runtime.rglob("*")
                if p.is_file() and p.suffix not in {"-wal", "-shm"} and not p.name.endswith(("-wal", "-shm"))}
    before = files()

    def cli(*args, exit_code=0):
        process = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                                  "--format", "json", "authority-archive", *args],
                                 cwd=REPO, capture_output=True, text=True, timeout=90, check=False)
        assert process.returncode == exit_code, process.stdout + process.stderr
        return json.loads(process.stdout)

    archive = tmp_path / "backup.ndjson"
    try:
        exported = cli("export", "--goal-id", goal, "--archive", str(archive))
        assert exported["status"] == "exported"
        assert not exported["authority_changed"]
        verified = cli("verify", "--archive", str(archive))
        assert verified["archive"] == exported["archive"]
        assert verified["archive"]["commits"] == "1"
        audit_args = ("audit", "--goal-id", goal, "--archive", str(archive),
                      "--archive-sha256", verified["archive"]["archive_sha256"])
        audited = cli(*audit_args)
        assert audited["status"] == "audited"
        assert audited["audit"]["scope"] == "exact"
        assert audited["audit"]["compared_commits"] == "1"
        assert not audited["authority_changed"]
        occupied = cli("export", "--goal-id", goal, "--archive", str(archive), exit_code=1)
        assert occupied["status"] == "failed"
        for target_provider in ("file", "sqlite"):
            destination = tmp_path / f"restore-{target_provider}"
            arguments = ("restore", "--goal-id", goal, "--archive", str(archive),
                         "--archive-sha256", verified["archive"]["archive_sha256"],
                         "--destination", str(destination), "--provider", target_provider)
            preview = cli(*arguments)
            assert preview["status"] == "planned" and not destination.exists()
            observation_args = ("restore-receipt", "--goal-id", goal, "--archive-sha256",
                                verified["archive"]["archive_sha256"], "--destination", str(destination),
                                "--provider", target_provider)
            assert cli(*observation_args)["status"] == "receipt_missing"
            assert not destination.exists()
            restored = cli(*arguments, "--execute")
            assert restored["status"] == "restored" and not restored["destination_is_active"]
            assert not restored["execution_authority_granted"]
            assert restored["requires_separate_authority_cutover"]
            assert cli(*arguments, "--execute")["archive"] == restored["archive"]
            proof = json.loads((destination / "verified-restore.json").read_text())
            observed = cli(*observation_args)
            assert observed["status"] == "receipt_found"
            assert observed["receipt"] == proof
            assert observed["current_integrity_verified"] is False
            assert observed["worker_liveness"] == "unknown"
            assert proof["archive_sha256"] == verified["archive"]["archive_sha256"]
            assert proof["target_store_identity"] != proof["source_store_identity"]
            audited = cli(*audit_args, "--destination", str(destination))
            assert audited["audit"]["status"] == "matched"
            assert audited["audit"]["target_store_identity"] == proof["target_store_identity"]
            assert cli(*audit_args, "--allow-newer-head")["audit"]["scope"] == "retained_prefix"
            # Reopen the actual restored backend in a separate process.
            module = REPO / f"loopx/control_plane/coordination/{target_provider}_authority_store.ts"
            class_name = "FileAuthorityStore" if target_provider == "file" else "SqliteAuthorityStore"
            script = (f"import {{{class_name}}} from {json.dumps(module.as_uri())};"
                      f"const store=new {class_name}({json.dumps(str(destination / 'store'))},{json.dumps(goal)});"
                      "process.stdout.write(JSON.stringify(await store.loadAuthority()));")
            readback = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
                                      capture_output=True, text=True, timeout=30, check=True)
            assert json.loads(readback.stdout)["head"] == projection
        # An arbitrary occupied directory, including the active runtime, is never reused.
        rejected = cli("restore", "--goal-id", goal, "--archive", str(archive), "--archive-sha256",
                       verified["archive"]["archive_sha256"], "--destination", str(runtime),
                       "--provider", provider, "--execute", exit_code=1)
        assert rejected["status"] == "failed"
        assert registry.read_bytes() == registry_before and state.read_bytes() == state_before
        assert files() == before
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
                       cwd=REPO, capture_output=True, text=True, timeout=30, check=True)


def test_upgrade_cli_requires_migration_and_keeps_verified_backup(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / "registry.json", tmp_path / "state.md"
    state.write_text("# Synthetic state\n")
    goal = "upgrade-cli-goal"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": []}))
    initialize_canonical_authority(runtime, goal, {"goal_id": goal, "value": 1}, state_path=state, provider="file")
    store = next((runtime / "authority" / "file-v0").glob("authority-store-*.json"))
    document = json.loads(store.read_text())
    # Single-commit legacy envelope, independently expressed on disk.
    row = document["committed"][0]
    row["projection"] = row.pop("state")["projection"]
    document["schema_version"] = "loopx_file_authority_store_v0"
    store.write_text(json.dumps(document))
    before = store.read_bytes()
    identified = subprocess.run([sys.executable, "-m", "loopx.cli", "--format", "json",
        "authority-archive", "inspect", "--source", str(store)], capture_output=True, text=True,
        check=True, timeout=60)
    inspection = json.loads(identified.stdout)["inspection"]
    assert inspection["artifact_kind"] == "authority_store"
    assert inspection["upgrade_required"] is True
    assert inspection["verification"] == "metadata_only"
    command = [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
               "--runtime-root", str(runtime), "--format", "json", "authority-archive", "upgrade"]
    checked = subprocess.run([*command, "--require-current"], capture_output=True, text=True, timeout=60)
    assert checked.returncode == 1
    assert store.read_bytes() == before
    preview = subprocess.run(command, capture_output=True, text=True, check=True, timeout=60)
    assert json.loads(preview.stdout)["results"][0]["status"] == "planned"
    executed = subprocess.run([*command, "--execute"], capture_output=True, text=True, check=True, timeout=60)
    result = json.loads(executed.stdout)
    assert result["status"] == "upgraded"
    backup = Path(result["results"][0]["backup_directory"])
    assert (backup / "source.json").read_bytes() == before
    assert json.loads((backup / "manifest.json").read_text())["cursor"] == document["cursor"]
    assert json.loads(store.read_text())["provider_revision"] == document["provider_revision"]
    subprocess.run([*command, "--require-current"], capture_output=True, text=True, check=True, timeout=60)


@pytest.mark.parametrize("legacy_runtime", [False, True])
def test_all_known_upgrade_roots_are_registry_owned_and_do_not_create_stores(tmp_path, monkeypatch, legacy_runtime):
    from loopx import paths
    from loopx.cli_commands import authority_archive

    current = tmp_path / "home" / ".loopx"
    legacy = tmp_path / "home" / ".codex" / "loopx"
    common = legacy if legacy_runtime else current
    project = tmp_path / "project"
    common.mkdir(parents=True)
    (project / ".loopx").mkdir(parents=True)
    project_registry = project / ".loopx" / "registry.json"
    project_registry.write_text(json.dumps({"common_runtime_root": ".loopx/runtime", "goals": [
        {"id": "markdown-only"}, {"id": "another-goal"}]}))
    global_registry = common / "registry.global.json"
    global_registry.write_text(json.dumps({"goals": [
        {"id": "markdown-only", "source_registry": str(project_registry)},
        {"id": "another-goal", "source_registry": str(project_registry)},
        {"id": "disconnected", "source_registry": str(tmp_path / "removed" / "registry.json")},
    ]}))
    monkeypatch.delenv("LOOPX_RUNTIME_ROOT", raising=False)
    monkeypatch.setattr(paths, "DEFAULT_RUNTIME_ROOT", current)
    monkeypatch.setattr(paths, "LEGACY_RUNTIME_ROOT", legacy)
    before = {p: p.read_bytes() for p in (global_registry, project_registry)}
    roots = authority_archive.authority_upgrade_roots(project_registry, None, all_known=True)
    assert roots == sorted(map(str, [common, project / ".loopx/runtime"]))
    assert authority_archive.authority_upgrade_roots(project_registry, None, all_known=False) == [str(project / ".loopx/runtime")]
    assert all(p.read_bytes() == data for p, data in before.items())
    assert not (project / ".loopx/runtime").exists()
    assert not (common / "authority").exists()
    other = current if legacy_runtime else legacy
    other.mkdir(parents=True)
    (other / "registry.global.json").write_text('{"goals": []}', encoding="utf-8")
    with pytest.raises(ValueError, match="Both default LoopX registries exist"):
        authority_archive.authority_upgrade_roots(project_registry, None, all_known=True)
    assert authority_archive.authority_upgrade_roots(project_registry, str(common), all_known=True) == roots


@pytest.mark.parametrize("initial,target", [("file", "sqlite"), ("sqlite", "file")])
def test_provider_migration_cli_preserves_readback_and_rollback(tmp_path, monkeypatch, initial, target):
    from canonical_authority_fixture import promoted_create_fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=initial)
    before = state.read_bytes()

    def cli(*args, expected=0):
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
                                 "--format", "json", *args], cwd=REPO, capture_output=True, text=True, timeout=90)
        assert result.returncode == expected, result.stdout + result.stderr
        return json.loads(result.stdout)

    try:
        for index, destination in enumerate((target, initial)):
            plan = tmp_path / f"plan-{index}.json"
            planned = cli("authority-archive", "plan-migration", "--goal-id", "goal-a", "--provider", destination, "--plan", str(plan))
            arguments = ("authority-archive", "migrate", "--goal-id", "goal-a", "--plan", str(plan), "--plan-sha256", planned["plan_sha256"])
            assert cli(*arguments)["status"] == "planned"
            migrated = cli(*arguments, "--execute")
            assert migrated["status"] == "migrated", migrated
            assert migrated["selected_provider"] == destination
            assert migrated["audit"]["compared_commits"] == "1"
            assert cli(*arguments, "--execute")["status"] == "already_applied"
            # Independent production CLI reads must follow the published selector.
            snapshot = cli("authority-archive", "export", "--goal-id", "goal-a", "--archive", str(tmp_path / f"after-{index}.jsonl"))
            assert snapshot["archive"]["source_provider"] == destination
        assert state.read_bytes() == before
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
                       cwd=REPO, capture_output=True, text=True, timeout=30, check=True)


@pytest.mark.parametrize("execute", [False, True])
def test_migration_transport_loss_never_asserts_that_execution_did_not_publish(tmp_path, monkeypatch, execute):
    from argparse import Namespace
    from loopx.cli_commands import authority_archive

    def disconnected(*args, **kwargs):
        assert kwargs["retry_safe"] is False
        raise RuntimeError("Connection lost after request delivery")

    monkeypatch.setattr(authority_archive, "effect_runtime_result", disconnected)
    registry = tmp_path / "registry.json"
    registry.write_text("{}")
    args = Namespace(command="authority-archive", authority_archive_action="migrate", goal_id="example",
                     plan=tmp_path / "plan.json", plan_sha256="a" * 64, execute=execute)
    outputs = []
    status = authority_archive.handle_authority_archive_command(
        args, registry_path=registry, runtime_root_arg=str(tmp_path / "runtime"),
        print_payload=lambda payload, *_: outputs.append(payload), output_format=lambda _: "json")
    assert status == 1
    assert outputs[0]["authority_changed"] is (None if execute else False)
    if execute:
        assert outputs[0]["requires_same_plan_retry"] is True
        assert outputs[0]["reason_code"] == "migration_outcome_unknown"


@pytest.mark.parametrize("execute,ambiguous", [(True, True), (False, True), (True, False)])
def test_restore_transport_loss_points_to_read_only_observation_without_retry(tmp_path, monkeypatch, execute, ambiguous):
    from argparse import Namespace
    from loopx.cli_commands import authority_archive
    from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous

    calls = []

    def disconnected(method, request, **kwargs):
        calls.append(request)
        assert kwargs["retry_safe"] is False
        assert kwargs["timeout"] == 300.0
        if ambiguous:
            raise EffectRuntimeResponseAmbiguous(method, timeout=300.0)
        raise RuntimeError("Invalid runtime before request delivery")

    monkeypatch.setattr(authority_archive, "effect_runtime_result", disconnected)
    args = Namespace(command="authority-archive", authority_archive_action="restore", goal_id="example",
                     archive=tmp_path / "archive", archive_sha256="a" * 64, provider="sqlite",
                     destination=tmp_path / "destination with spaces", execute=execute)
    outputs = []
    status = authority_archive.handle_authority_archive_command(
        args, registry_path=tmp_path / "unused-registry", runtime_root_arg=None,
        print_payload=lambda payload, *_: outputs.append(payload), output_format=lambda _: "json")
    assert status == 1 and len(calls) == 1
    result = outputs[0]
    assert result["authority_changed"] is False
    if execute and ambiguous:
        assert result["status"] == "outcome_unknown"
        assert result["reason_code"] == "restore_outcome_unknown"
        assert result["automatic_retry_performed"] is False
        assert result["recovery_command"] == [
            "loopx", "--format", "json", "authority-archive", "restore-receipt", "--goal-id", "example",
            "--destination", str(args.destination), "--provider", "sqlite", "--archive-sha256", "a" * 64]
    else:
        assert result["status"] == "failed" and "recovery_command" not in result
