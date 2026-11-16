"""Selection commands execute on their host and retain their original Turn."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys

import pytest

from loopx.project_prompt import shell_arg_for_host
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, ALTERNATIVE_TODO_ID, GOAL_ID, REPO_ROOT, TURN_ID,
    _configure_selectable_alternative, _heartbeat_receipt_count,
    _initialize_fixture_authority, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("custom_cli", [False, True])
def test_native_selection_route_keeps_paths_turn_and_reentry(tmp_path, monkeypatch, provider, custom_cli):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    root = tmp_path / "caller's Unicode 资料"
    root.mkdir()
    project, runtime, registry = _write_fixture(root)
    _configure_selectable_alternative(project)
    _initialize_fixture_authority(project, runtime, provider=provider)
    monkeypatch.setenv("PATH", str(sys.executable.rsplit(os.sep, 1)[0]) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("PYTHONPATH", str(REPO_ROOT))
    cli_args = []
    if custom_cli:
        launcher = root / ("loopx.ps1" if os.name == "nt" else "loopx")
        if os.name == "nt":
            launcher.write_text(f"& {shell_arg_for_host(sys.executable)} -m loopx.entrypoint @args\nexit $LASTEXITCODE\n", encoding="utf-8")
        else:
            launcher.write_text(f"#!/bin/sh\nexec {shlex.quote(sys.executable)} -m loopx.entrypoint \"$@\"\n")
            launcher.chmod(0o700)
        cli_args = ["--cli-bin", str(launcher)]
    code, initial = _run_cli(
        registry, runtime, *cli_args, "quota", "should-run", "--goal-id", GOAL_ID,
        "--agent-id", AGENT_ID, "--turn-instance-id", TURN_ID,
        "--context-delivery", "off", cwd=project,
    )
    assert code == 0, initial
    selection = initial["interaction_contract"]["cli_channel"]["selection_command"]
    command = selection["route_prefix"] + " " + selection["command_args_template"].replace("{todo_id}", ALTERNATIVE_TODO_ID)
    launcher_args = ["pwsh", "-NoLogo", "-NoProfile", "-Command", command] if os.name == "nt" else ["bash", "-c", command]
    selected = subprocess.run(launcher_args, cwd=project, capture_output=True, text=True, encoding="utf-8")
    assert selected.returncode == 0, selected.stdout + selected.stderr
    payload = json.loads(selected.stdout)
    assert payload["ok"]
    identity = payload["heartbeat_receipt"]["settlement_identity"]
    assert identity["turn_instance_id"] == TURN_ID and identity["todo_id"] == ALTERNATIVE_TODO_ID
    read_id = payload["delivery_result_context"]["read_context_id"]
    before = _heartbeat_receipt_count(runtime, TURN_ID)
    replay = subprocess.run(launcher_args, cwd=project, capture_output=True, text=True, encoding="utf-8")
    assert replay.returncode == 0, replay.stdout + replay.stderr
    repeated = json.loads(replay.stdout)
    assert repeated["heartbeat_receipt"]["settlement_identity"] == identity
    assert repeated["delivery_result_context"]["read_context_id"] == read_id
    assert _heartbeat_receipt_count(runtime, TURN_ID) == before
    assert _spend_run_count(runtime) == 0
