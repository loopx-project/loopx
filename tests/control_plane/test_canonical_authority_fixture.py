"""Canonical CLI tests must not reuse a host's warmed Effect runtime."""
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
