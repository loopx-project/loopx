"""Generated Windows settlement commands use the existing private result reader."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from loopx.control_plane.quota.effect_program import build_turn_scoped_cli_settlement_plan
from loopx.control_plane.quota.settlement import SettlementStepKind, settlement_step_command
from loopx.control_plane.runtime.private_permissions import require_private_path
from loopx.control_plane.turn_driver.decision_capture import read_context_read_reference
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_checkpoint_read_context import _missing
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, TODO_ID, TURN_ID, REPO_ROOT


@pytest.mark.skipif(os.name != "nt", reason="Exercises native Windows private IO and physical CLI output")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_generated_windows_spend_bounds_rejection_without_advancing(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _, _, retained = _missing(tmp_path, provider=provider)
    index = runtime / "goals" / GOAL_ID / "runs/index.jsonl"
    before = index.read_bytes()
    plan = build_turn_scoped_cli_settlement_plan(goal_id=GOAL_ID, agent_id=AGENT_ID,
        todo_id=TODO_ID, turn_instance_id=TURN_ID, scoped_cli_args="", lifecycle_actor_args="").as_dict()
    command = settlement_step_command(plan, SettlementStepKind.QUOTA_SPEND)
    assert command and "--max-page-output-bytes" in shlex.split(command)
    result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), *shlex.split(command)[1:]], cwd=project,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "LOOPX_USAGE_PING": "0"},
        capture_output=True, timeout=60)
    assert result.returncode == 1, result.stderr
    assert len(result.stdout) <= 64000
    view = json.loads(result.stdout)
    assert not view["ok"] and view["appended"] is False
    assert view["registry_mutated"] is False and index.read_bytes() == before
    reference, descriptor = read_context_read_reference(view["result_details"]["reference_file"])
    require_private_path(reference)
    captured = Path(descriptor["path"])
    require_private_path(captured)
    raw = captured.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == descriptor["sha256"]
    complete = json.loads(raw)
    assert complete["reason"] == view["reason"] and complete["before"] is not None
    assert complete["after"] is None and retained["settlement_identity"]["turn_instance_id"] == TURN_ID
    assert index.read_bytes() == before
