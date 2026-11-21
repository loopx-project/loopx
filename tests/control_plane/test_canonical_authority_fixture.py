"""Canonical CLI fixtures retain runtime isolation and actual backend identity."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.effect_runtime import _runtime_dir


@pytest.mark.parametrize("cached", [False, True])
def test_runtime_isolation_covers_process_and_cli_temp_directories(tmp_path, monkeypatch, cached):
    shared = tmp_path / "shared"
    shared.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(shared) if cached else None)

    isolate_sqlite_runtime(tmp_path, monkeypatch)

    assert tempfile.gettempdir() == str(tmp_path)
    assert _runtime_dir().parent == tmp_path
    child_root = subprocess.check_output(
        [sys.executable, "-c", "import tempfile; print(tempfile.gettempdir())"], text=True,
    ).strip()
    assert child_root == str(tmp_path)


def test_runtime_isolation_restores_the_process_cache(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    monkeypatch.setattr(tempfile, "tempdir", str(shared))

    with monkeypatch.context() as isolated:
        isolate_sqlite_runtime(tmp_path, isolated)
        assert _runtime_dir().parent == tmp_path

    assert tempfile.tempdir == str(shared)
    assert _runtime_dir().parent == shared


@pytest.mark.parametrize("provider", ["postgresql", "unknown"])
def test_local_fixture_rejects_unsupported_provider_without_writing(tmp_path, monkeypatch, provider):
    import test_quota_settlement_cli as fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, _ = fixture._write_fixture(tmp_path)
    state = project / f".codex/goals/{fixture.GOAL_ID}/ACTIVE_GOAL_STATE.md"
    original = state.read_bytes()

    with pytest.raises(ValueError, match="File/SQLite"):
        fixture._initialize_fixture_authority(project, runtime, provider=provider)

    assert not runtime.exists()  # No authority store, selector or writer fence.
    assert state.read_bytes() == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_local_fixture_reads_its_actual_provider_through_cli(tmp_path, monkeypatch, provider):
    import test_quota_settlement_cli as fixture

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = fixture._write_fixture(tmp_path)
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    code, result = fixture._run_cli(registry, runtime, "todo", "list",
        "--goal-id", fixture.GOAL_ID, "--todo-id", fixture.TODO_ID, cwd=project)

    assert code == 0, result
    assert result["source"] == provider + "_v0"
    assert result["todo"]["todo_id"] == fixture.TODO_ID
    assert result["authority_read"]["decision_read_from_provider"]
    assert not result["authority_read"]["legacy_fallback_used"]


def test_session_effect_runtime_sets_private_node_compile_cache(tmp_path):
    cache = os.environ.get("NODE_COMPILE_CACHE")
    if os.environ.get("NODE_DISABLE_COMPILE_CACHE") == "1" or os.environ.get("NODE_V8_COVERAGE"):
        pytest.skip("compile cache disabled by caller environment")
    assert cache
    cache_path = Path(cache)
    assert cache_path.is_dir()
    assert cache_path.name == "node-compile-cache"
    assert cache_path.stat().st_mode & 0o077 == 0


def test_persistent_canonical_worker_reuses_and_auto_respawns_once(tmp_path, monkeypatch):
    import canonical_authority_fixture as fixture_mod
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime = tmp_path / "runtime"
    state = tmp_path / "state.md"
    state.write_text("# Goal\n", encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(goal_id="goal-respawn", todos=[], handoff_mode="soft_claim")
    first = fixture_mod.initialize_canonical_authority(runtime, "goal-respawn", projection, state_path=state, provider="sqlite")
    assert first["status"] == "applied"

    worker = fixture_mod._get_canonical_authority_worker(Path(fixture_mod.loopx.__file__).resolve().parent)
    original_pid = worker._process.pid
    worker._process.kill()
    worker._process.wait(timeout=5)

    second = fixture_mod.advance_canonical_fixture_authority(runtime, "goal-respawn", projection, operation_id="after-kill")
    assert second["status"] == "applied"
    assert worker._process.pid != original_pid
    assert worker._process.poll() is None


def test_canonical_worker_reaps_silent_child_without_retry(tmp_path, monkeypatch):
    import canonical_authority_fixture as fixture_mod

    worker_type = fixture_mod._CanonicalAuthorityWorker
    monkeypatch.setattr(fixture_mod, "_WORKER_REQUEST_TIMEOUT_SECONDS", 0.25)
    monkeypatch.setattr(worker_type, "_build_script", staticmethod(lambda _: "process.stdin.resume();"))
    worker = worker_type((os.getpid(), str(tmp_path), False))
    original_process = worker._process
    try:
        with pytest.raises(TimeoutError, match="timed out waiting for response"):
            worker.request({"op": "silent"})
        assert worker._process is original_process
        assert original_process.poll() is not None
    finally:
        worker.close()
