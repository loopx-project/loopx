"""Production Turn supervision must be independently readable for a transfer."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from loopx.control_plane.turn_driver.turn_host_process import read_turn_host_processes
from loopx.control_plane.turn_driver.executor import run_loopx_turn_once
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_loopx_turn_driver import (
    _completion_host_and_validation_scripts,
    _turn_run_once_completion_argv,
    _write_live_fixture,
)


@pytest.mark.skipif(not hasattr(os, "killpg"), reason="transfer requires provable POSIX group drain")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_real_turn_cli_retains_original_host_group_without_record_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.delenv("LOOPX_HOST_PROCESS_RECORD", raising=False)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    host, validator = _completion_host_and_validation_scripts()
    command = [sys.executable, "-I", "-m", "loopx.cli",
               *_turn_run_once_completion_argv(project, runtime, registry, host, validator)]
    first = subprocess.run(command, cwd=project, capture_output=True, text=True, timeout=60)
    assert first.returncode == 0, first.stdout + first.stderr
    result = json.loads(first.stdout)
    assert result["status"] == "committed"
    assert (project / "completion-artifact.txt").read_text() == "completed"
    history = read_turn_host_processes(runtime.resolve(), "loopx-turn-fixture")
    assert len(history) == 1 and history[0]["attempt"] == 1
    assert history[0]["drain"] == "drained"
    assert history[0]["record_sha256"] and history[0]["supervisor_sha256"] is None
    journal = runtime / "goals/loopx-turn-fixture/turns" / (history[0]["turn_key"][7:] + ".json")
    snapshot = json.loads(journal.read_bytes())
    assert snapshot["status"] == "committed" and snapshot["host_attempt_count"] == 1
    records = list(journal.with_suffix(".hosts").glob("*.json"))
    original_bytes = {path: path.read_bytes() for path in records}
    def forbidden(*_args):
        pytest.fail("original settled Turn replay must not execute or settle twice")

    replay = run_loopx_turn_once(snapshot["plan"],
        host_argv=[sys.executable, "-c", "raise SystemExit(66)"],
        project=project, runtime_root=runtime, goal_id="loopx-turn-fixture",
        timeout_seconds=5, execute=True, writeback=forbidden, spend=forbidden, scheduler=forbidden)
    assert replay["status"] == "committed" and replay["replayed"] is True
    assert read_turn_host_processes(runtime.resolve(), "loopx-turn-fixture") == history
    assert {path: path.read_bytes() for path in records} == original_bytes


def test_host_history_refuses_linked_and_orphan_addresses(tmp_path: Path) -> None:
    root = tmp_path.resolve()
    turns = root / "goals/report/turns"
    turns.mkdir(parents=True)
    directory = turns / ("a" * 64 + ".hosts")
    directory.mkdir()
    outside = root / "outside.json"
    outside.write_text("{}")
    (directory / "attempt-1.json").symlink_to(outside)
    with pytest.raises(ValueError, match="unattributable"):
        read_turn_host_processes(root, "report")
    (directory / "attempt-1.json").unlink()
    (directory / "unowned.json").write_text("{}")
    with pytest.raises(ValueError, match="unattributable"):
        read_turn_host_processes(root, "report")
