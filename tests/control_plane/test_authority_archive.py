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
        occupied = cli("export", "--goal-id", goal, "--archive", str(archive), exit_code=1)
        assert occupied["status"] == "failed"
        for target_provider in ("file", "sqlite"):
            destination = tmp_path / f"restore-{target_provider}"
            arguments = ("restore", "--goal-id", goal, "--archive", str(archive),
                         "--archive-sha256", verified["archive"]["archive_sha256"],
                         "--destination", str(destination), "--provider", target_provider)
            preview = cli(*arguments)
            assert preview["status"] == "planned" and not destination.exists()
            restored = cli(*arguments, "--execute")
            assert restored["status"] == "restored" and not restored["destination_is_active"]
            assert not restored["execution_authority_granted"]
            assert restored["requires_separate_authority_cutover"]
            assert cli(*arguments, "--execute")["archive"] == restored["archive"]
            proof = json.loads((destination / "verified-restore.json").read_text())
            assert proof["archive_sha256"] == verified["archive"]["archive_sha256"]
            assert proof["target_store_identity"] != proof["source_store_identity"]
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
