"""A shell scheduler observes admission; the admitted native wake owns context."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import external_scheduler_worker as worker
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _heartbeat_receipt_count, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("native_context", ["on", "off"])
@pytest.mark.skipif(os.name != "posix", reason="Exercises the POSIX shell scheduler and private native files")
def test_scheduler_observation_preserves_native_wake_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, native_context: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", native_context)
    monkeypatch.setenv("LOOPX_EXECUTION_MODE", "heartbeat")
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "native-home"))
    repository = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("PYTHONPATH", str(repository))
    project, runtime, registry = _write_fixture(tmp_path, provider=provider)
    cli = tmp_path / "loopx-cli"
    cli.write_text(f"#!{sys.executable}\nfrom loopx.entrypoint import main\nraise SystemExit(main())\n")
    cli.chmod(0o700)
    args = argparse.Namespace(cli_bin=str(cli), registry=str(registry),
        runtime_root=str(runtime), goal_id=GOAL_ID, agent_id=AGENT_ID,
        runtime_profile="generic_cli")

    # This process has no native Turn/session. Its current decision must remain
    # directly consumable by the scheduler even when its future wake uses on.
    original_environment = dict(os.environ)
    command = worker.build_should_run_command(args)
    assert command[-1] == "--scheduler-observation"
    assert "--context-delivery" not in command
    decision = worker.run_quota_should_run(command, timeout_seconds=30)
    assert decision["ok"] and worker.parse_tick(decision).should_run
    assert decision["selected_todo"] == {"todo_id": TODO_ID}
    assert set(decision["interaction_contract"]) == {"agent_channel"}
    assert not {"goal_boundary", "replan_action_packet", "reference_file"}.intersection(decision)
    legacy = worker.run_quota_should_run(
        [*command[:-1], "--include-detail", "scheduler", "--context-delivery", "off"],
        timeout_seconds=30,
    )
    assert worker.parse_tick(decision) == worker.parse_tick(legacy)
    assert worker._admission_scope(decision, args) == worker._admission_scope(legacy, args)
    assert len(json.dumps(decision).encode()) < len(json.dumps(legacy).encode())
    assert not (runtime / "context-delivery").exists()
    assert dict(os.environ) == original_environment
    assert _spend_run_count(runtime) == 0
    for extra in (
        ("--turn-instance-id", "observer-must-not-admit"),
        ("--begin-turn",),
        ("--decision-output-root", str(tmp_path)),
    ):
        rejected = subprocess.run([*command, *extra], cwd=project,
            capture_output=True, text=True, timeout=30, check=False)
        assert rejected.returncode != 0
        failure = json.loads(rejected.stdout)
        assert failure["ok"] is False
        assert "--scheduler-observation" in failure["reason"]
    for extra in (
        ("--capture-file", str(tmp_path / "capture.json")),
        ("--initial-read",),
        ("--list",),
        ("--list", "--list-depth", "2"),
        ("--select", "/interaction_contract"),
        ("--reference-file", str(tmp_path / "reader.json")),
    ):
        rejected = subprocess.run([*command, *extra], cwd=project,
            capture_output=True, text=True, timeout=30, check=False)
        assert rejected.returncode != 0
        failure = json.loads(rejected.stdout)
        assert failure["ok"] is False
        assert "selected_todo" not in failure
    assert _heartbeat_receipt_count(runtime, "observer-must-not-admit") == 0
    assert not list(tmp_path.glob("decision-*"))

    # The native wake still captures its own exact Turn. Observation
    # must never become a global opt-out or mutate the child environment.
    turn = "native-wake-after-observation"
    command = [str(cli), "--format", "json", "--registry", str(registry),
        "--runtime-root", str(runtime), "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--runtime-profile", "generic_cli",
        "--turn-instance-id", turn, "--context-delivery", native_context]
    if native_context == "on":
        from loopx.control_plane.turn_driver.codex_context import codex_context_hook

        (tmp_path / "captures").mkdir(mode=0o700)
        (tmp_path / "context").mkdir(mode=0o700)
        codex_context_hook(str(tmp_path / "context"), {"hook_event_name": "SessionStart",
            "source": "startup", "session_id": "native-context-wake", "cwd": str(project)})
        command += ["--decision-output-root", str(tmp_path / "captures"),
            "--codex-context-dir", str(tmp_path / "context")]
    result = subprocess.run(command, cwd=project, capture_output=True, text=True,
        env=os.environ.copy(), timeout=30, check=False)
    assert result.returncode == 0, result.stderr or result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"], json.dumps({k: payload.get(k) for k in ("reason", "error_code", "state")})
    if native_context == "on":
        assert payload["schema_version"] == "context_capture_navigation_v1"
        assert Path(payload["reference_file"]).is_file()
    else:
        assert payload["selected_todo"]["todo_id"] == TODO_ID
    assert _heartbeat_receipt_count(runtime, turn) == 1
    assert _spend_run_count(runtime) == 0
