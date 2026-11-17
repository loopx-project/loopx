"""Canonical CLI fixtures retain runtime isolation and actual backend identity."""
from __future__ import annotations

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
