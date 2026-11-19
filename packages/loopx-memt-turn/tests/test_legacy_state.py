"""Alias-format compatibility: changing a directory key cannot clear unknown effects."""
from __future__ import annotations

import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import subprocess
import sys

import pytest

from loopx_memt_turn import host
from loopx_memt_turn.config import AdapterError, digest

from memt_fixtures import turn, with_tools


def legacy_directory(config):
    # Frozen pre-instance layout, independent of the current scope_path.
    return Path(config.state_dir) / digest([
        str(Path(config.workspace).resolve()), "public-goal", "analyst",
    ])


def invoke(config, tmp_path, request):
    path = tmp_path / "operator.json"
    path.write_text(json.dumps(asdict(config)))
    return subprocess.run([sys.executable, "-m", "loopx_memt_turn.cli", "--config", str(path)],
                          input=json.dumps(request), text=True, capture_output=True,
                          timeout=30, cwd=config.workspace)


@pytest.mark.parametrize("state", ["pending", "running", "orphaned", "empty_head"])
def test_unattributed_legacy_effects_block_before_io_and_preserve_evidence(config, provider, state):
    directory = legacy_directory(config)
    directory.mkdir(parents=True)
    if state != "orphaned":
        head = {} if state == "empty_head" else {"pending_turn": turn()["turn_key"] if state == "pending" else None}
        (directory / "head.json").write_text(json.dumps(head))
    if state != "empty_head":
        (directory / ("1" * 64 + ".json")).write_text(json.dumps({
            "stage": "completed" if state == "orphaned" else "running",
        }))
    before = {path: path.read_bytes() for path in directory.glob("*.json")}
    with pytest.raises(AdapterError, match="legacy_memory_requires_operator_reconciliation"):
        asyncio.run(host.run(turn(2), config))
    assert provider.requests == []
    assert all(path.read_bytes() == content for path, content in before.items())
    assert not host.scope_path(config, host.work_identity(turn(2))).exists()


def test_actual_tool_effect_in_legacy_layout_is_not_repeated_by_upgraded_cli(config, provider, tmp_path, monkeypatch):
    config = with_tools(config)
    directory = legacy_directory(config)
    directory.mkdir(parents=True)
    provider.replies = ["<tool_call><function=record_observation><parameter=observation>first</parameter></function></tool_call>"]
    provider.fail_action = "export"
    # Exercise the real writer/SDK/MCP in the historical layout, then restore
    # the current selector. The CLI under test is never patched.
    with monkeypatch.context() as patch:
        patch.setattr(host, "scope_path", lambda *_: directory)
        with pytest.raises(ExceptionGroup):
            asyncio.run(host.execute(turn(), config, host.work_identity(turn())))
    output = Path(config.workspace) / "observations.txt"
    assert output.read_text() == "first\n"
    before = {path: path.read_bytes() for path in directory.glob("*.json")}
    calls = len(provider.requests)
    provider.fail_action = None
    result = invoke(config, tmp_path, turn(2))
    assert result.returncode == 1, result.stderr
    assert json.loads(result.stdout)["error"] == "legacy_memory_requires_operator_reconciliation"
    assert len(provider.requests) == calls
    assert output.read_text() == "first\n"
    assert all(path.read_bytes() == content for path, content in before.items())


def test_completed_legacy_state_is_preserved_without_importing_alias_memory(config, provider):
    directory = legacy_directory(config)
    directory.mkdir(parents=True)
    (directory / "head.json").write_text(json.dumps({"pending_turn": None, "snapshot": "legacy.safetensors"}))
    (directory / ("1" * 64 + ".json")).write_text(json.dumps({"stage": "completed"}))
    before = {path: path.read_bytes() for path in directory.glob("*.json")}
    assert asyncio.run(host.run(turn(2), config))["result_kind"] == "validated_progress"
    assert not any(body.get("action") == "import" for _, body in provider.requests)
    assert all(path.read_bytes() == content for path, content in before.items())


def test_legacy_worker_lock_blocks_upgrade_before_provider_io(config, provider, tmp_path):
    directory = legacy_directory(config)
    directory.mkdir(parents=True)
    program = (
        "import sys; from loopx.file_lock import exclusive_file_lock,LockAcquisitionPolicy; "
        "from pathlib import Path; "
        "lock=exclusive_file_lock(Path(sys.argv[1]),policy=LockAcquisitionPolicy.SINGLE_FLIGHT); "
        "lock.__enter__(); print('locked',flush=True); sys.stdin.read(); lock.__exit__(None,None,None)"
    )
    with subprocess.Popen([sys.executable, "-c", program, str(directory / "head.json")],
                          stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True) as worker:
        try:
            assert worker.stdout.readline().strip() == "locked"
            result = invoke(config, tmp_path, turn(2))
            assert result.returncode == 1
            assert provider.requests == []
        finally:
            worker.stdin.close()
            worker.wait(timeout=10)
