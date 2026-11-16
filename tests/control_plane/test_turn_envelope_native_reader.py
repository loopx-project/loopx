"""The saved-observation reader returns the original bytes on its native shell."""
from __future__ import annotations

import hashlib
import os
import subprocess

import pytest

from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, TURN_ID,
    _heartbeat_receipt_count, _initialize_fixture_authority, _run_cli,
    _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_captured_envelope_native_reader_preserves_exact_bytes_and_effects(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = tmp_path / "reader's Unicode 资料"
    root.mkdir()
    project, runtime, registry = _write_fixture(root)
    _initialize_fixture_authority(project, runtime, provider=provider)
    capture = root / "capture"
    code, envelope = _run_cli(
        registry, runtime, "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--turn-instance-id", TURN_ID,
        "--codex-app", "--turn-envelope", "--decision-output-dir", str(capture), cwd=project,
    )
    assert code == 0, envelope
    saved = capture / "decision.json"
    original = saved.read_bytes()
    digest = hashlib.sha256(original).hexdigest()
    before = _heartbeat_receipt_count(runtime, TURN_ID), _spend_run_count(runtime)
    command = envelope["detail_ref"]["full_decision"]
    launcher = ["pwsh", "-NoLogo", "-NoProfile", "-Command", command] if os.name == "nt" else ["bash", "-c", command]
    readback = subprocess.run(launcher, cwd=project, capture_output=True, check=True)
    assert readback.stdout == original
    assert hashlib.sha256(readback.stdout).hexdigest() == digest
    assert saved.read_bytes() == original
    assert (_heartbeat_receipt_count(runtime, TURN_ID), _spend_run_count(runtime)) == before
