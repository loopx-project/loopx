"""An exact admission read leaves quota and the original selection owner alone."""
from pathlib import Path

import pytest

from loopx.rollout_event_log import rollout_event_log_path
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_quota_settlement_cli import (
    AGENT_ID, GOAL_ID, TODO_ID, _run_cli, _spend_run_count, _write_fixture,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_read_original_quota_receipt_before_and_after_selection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_fixture(tmp_path, claimed=False, provider=provider)
    log = rollout_event_log_path(runtime, GOAL_ID)
    scope = ("--goal-id", GOAL_ID, "--agent-id", AGENT_ID, "--turn-instance-id", "host-original")

    rc, result = _run_cli(registry, runtime, "quota", "receipt", *scope, cwd=project)
    assert rc == 0 and result["heartbeat_receipt"] is None and not log.exists(), result

    guard = ("quota", "should-run", "--runtime-profile", "generic_cli", *scope)
    rc, original = _run_cli(registry, runtime, *guard, cwd=project)
    assert rc == 0 and original["interaction_contract"]["cli_channel"]["selection_required"], original
    before = log.read_bytes()
    rc, result = _run_cli(registry, runtime, "quota", "receipt", *scope, cwd=project)
    assert rc == 0 and result["heartbeat_receipt"] == original["heartbeat_receipt"], result
    assert result["heartbeat_receipt"]["selection_required"] is True
    assert result["heartbeat_receipt"]["settlement_binding_owed"] is True
    assert log.read_bytes() == before

    rc, claim = _run_cli(registry, runtime, "todo", "claim", "--goal-id", GOAL_ID,
                        "--agent-id", AGENT_ID, "--todo-id", TODO_ID, "--claimed-by", AGENT_ID)
    assert rc == 0 and claim["ok"], claim
    rc, selected = _run_cli(registry, runtime, *guard, "--todo-id", TODO_ID, cwd=project)
    assert rc == 0 and selected["heartbeat_receipt"]["settlement_identity"]["todo_id"] == TODO_ID, selected
    before = log.read_bytes()
    rc, result = _run_cli(registry, runtime, "quota", "receipt", *scope, cwd=project)
    assert rc == 0 and result["heartbeat_receipt"]["settlement_identity"] == (
        selected["heartbeat_receipt"]["settlement_identity"]
    ), result
    assert "selection_required" not in result["heartbeat_receipt"]
    rc, other = _run_cli(registry, runtime, "quota", "receipt", "--goal-id", GOAL_ID,
                        "--agent-id", "peer-agent", "--turn-instance-id", "host-original")
    assert rc == 0 and other["heartbeat_receipt"] is None, other
    assert log.read_bytes() == before and _spend_run_count(runtime) == 0
