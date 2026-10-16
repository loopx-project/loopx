"""Unmigrated Goals cannot grant new Todo claim authority."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.control_plane.coordination.local_authority import CanonicalAuthorityRequired
from loopx.todos import update_goal_todo


@pytest.mark.parametrize("dry_run", [False, True], ids=["execute", "dry-run"])
def test_unpromoted_claim_requires_canonical_authority_before_writes(
    tmp_path: Path, dry_run: bool,
) -> None:
    state = tmp_path / "ACTIVE_GOAL_STATE.md"
    state.write_text("# Goal\n\n## Agent Todo\n\n", encoding="utf-8")
    runtime = tmp_path / "runtime"
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": [{"id": "goal-a", "status": "active", "repo": str(tmp_path),
                   "state_file": state.name, "coordination": {
                       "registered_agents": ["agent-a", "agent-b"],
                   }}],
    }), encoding="utf-8")
    before = state.read_bytes()
    with pytest.raises(CanonicalAuthorityRequired) as error:
        update_goal_todo(
            registry_path=registry,
            goal_id="goal-a",
            todo_id="todo_existing",
            claimed_by="agent-a",
            agent_id="agent-a",
            claim_only=True,
            claim_operation_id="explicit-retry",
            dry_run=dry_run,
        )
    assert error.value.code == "canonical_authority_required"
    assert error.value.payload["operation"] == "todo_claim"
    assert error.value.payload["write_admitted"] is False
    assert error.value.payload["decision_read_from_provider"] is False
    assert error.value.payload["legacy_fallback_used"] is False
    assert error.value.payload["recovery"] == {
        "action": "migrate_goal_authority",
        "backup_required": True,
    }
    assert state.read_bytes() == before
    assert not (runtime / "authority").exists()
