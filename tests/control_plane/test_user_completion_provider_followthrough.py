"""The public completion caller must carry dependent effects across promotion."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
from test_todo_decision_scope_lifecycle import (
    AGENT_ID, GOAL_ID, PUBLISH_SCOPE, _add_target_and_gate, _write_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.todos import add_goal_todo, complete_goal_todo, list_goal_todos, update_goal_todo


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("outcome", ["approve", "reject", "cancel"])
@pytest.mark.parametrize("source_status", ["open", "deferred"])
def test_public_completion_commits_linked_decision(
    tmp_path: Path, monkeypatch, provider, outcome, source_status,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    _, state, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["common_runtime_root"] = str(tmp_path / "runtime")
    registry.write_text(json.dumps(config))
    target, gate = _add_target_and_gate(
        registry, required_scopes=[PUBLISH_SCOPE], target_status="blocked",
    )
    if source_status == "deferred":
        update_goal_todo(
            registry_path=registry, goal_id=GOAL_ID, todo_id=gate["todo_id"],
            status="deferred", resume_when="capacity_available:owner_review", agent_id=AGENT_ID,
        )
    if provider != "legacy":
        source = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode="soft_claim", todos=source,
        )
        initialize_canonical_authority(tmp_path / "runtime", GOAL_ID, projection,
                                       state_path=state, provider=provider)
    result = complete_goal_todo(
        registry_path=registry, goal_id=GOAL_ID, todo_id=gate["todo_id"],
        role="user", agent_id=AGENT_ID, decision_outcome=outcome,
        evidence="Synthetic owner decision for this exact action",
    )
    rows = {row["todo_id"]: row for row in list_goal_todos(
        registry_path=registry, goal_id=GOAL_ID)["todos"]}
    assert result["decision_outcome"] == outcome
    assert rows[gate["todo_id"]]["status"] == "done"
    dependent = rows[target["todo_id"]]
    assert dependent["claimed_by"] == AGENT_ID
    if outcome == "approve":
        assert dependent["status"] == "open"
        assert not dependent.get("required_decision_scopes")
        assert result["unblock_resume"]["state"] == "resumed"
        assert result["decision_scope_resolution"]["state"] == "resolved"
    else:
        assert dependent["status"] == "blocked"
        assert dependent["required_decision_scopes"]
        assert dependent["decision_scope_outcomes"][0]["outcome"] == outcome
        assert result["unblock_resume"]["state"] == (
            "decision_rejected" if outcome == "reject" else "decision_cancelled")


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("cancel", [False, True])
def test_public_action_closure_and_cli_cancel(tmp_path: Path, monkeypatch, provider, cancel):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    repo, state, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["common_runtime_root"] = str(tmp_path / "runtime")
    registry.write_text(json.dumps(config))
    target = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Continue after the user's observation", status="blocked", claimed_by=AGENT_ID)
    action = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="user",
        text="Read the observation request", task_class="user_action", bound_agent=AGENT_ID,
        unblocks_todo_id=target["todo_id"])
    if provider != "legacy":
        config["goals"][0]["coordination"]["handoff_mode"] = "hard_lease"
        registry.write_text(json.dumps(config))
        source = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode="hard_lease", todos=source)
        initialize_canonical_authority(tmp_path / "runtime", GOAL_ID, projection,
                                       state_path=state, provider=provider)
    base = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
            "todo", "complete", "--goal-id", GOAL_ID, "--todo-id", action["todo_id"], "--role", "user"]
    for actor, outcome in [("codex-review", "cancel"), (AGENT_ID, "approve"), (AGENT_ID, "reject")]:
        rejected = subprocess.run([*base, "--agent-id", actor, "--decision-outcome", outcome],
                                  capture_output=True, text=True, timeout=45, cwd=repo)
        assert rejected.returncode != 0, rejected.stdout
        if actor == AGENT_ID:
            assert "decision_outcome is only valid" in rejected.stdout
            assert "handler failed unexpectedly" not in rejected.stdout
    command = [*base, "--agent-id", AGENT_ID, "--evidence", "Synthetic ordinary observation"]
    if cancel:
        command += ["--decision-outcome", "cancel"]
    result = subprocess.run(command, capture_output=True, text=True, timeout=45, cwd=repo)
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    rows = {row["todo_id"]: row for row in list_goal_todos(
        registry_path=registry, goal_id=GOAL_ID)["todos"]}
    assert rows[action["todo_id"]]["status"] == "done"
    assert rows[target["todo_id"]]["status"] == ("blocked" if cancel else "open")
    assert rows[target["todo_id"]]["claimed_by"] == AGENT_ID
    assert not rows[target["todo_id"]].get("decision_scope_outcomes")
    assert payload["unblock_resume"]["state"] == ("decision_cancelled" if cancel else "resumed")
    replay = subprocess.run(command, capture_output=True, text=True, timeout=45, cwd=repo)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert rows == {row["todo_id"]: row for row in list_goal_todos(
        registry_path=registry, goal_id=GOAL_ID)["todos"]}


@pytest.mark.parametrize("provider", ["legacy", "file", "sqlite"])
@pytest.mark.parametrize("method", ["complete", "update"])
def test_public_gate_closure_without_decision_preserves_existing_contract(
    tmp_path: Path, monkeypatch, provider, method,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    repo, state, registry = _write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["common_runtime_root"] = str(tmp_path / "runtime")
    registry.write_text(json.dumps(config))
    target, gate = _add_target_and_gate(
        registry, required_scopes=[PUBLISH_SCOPE], target_status="blocked",
    )
    if provider != "legacy":
        config["goals"][0]["coordination"]["handoff_mode"] = "hard_lease"
        registry.write_text(json.dumps(config))
        rows = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
        projection = build_todo_runtime_shadow_projection(
            goal_id=GOAL_ID, handoff_mode="hard_lease", todos=rows,
        )
        initialize_canonical_authority(tmp_path / "runtime", GOAL_ID, projection,
                                       state_path=state, provider=provider)
    before = list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"]
    cli = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry), "todo"]
    identity = ["--goal-id", GOAL_ID, "--todo-id", gate["todo_id"], "--agent-id", AGENT_ID]
    command = [*cli, method, *identity]
    if method == "complete":
        command += ["--role", "user", "--evidence", "Record closure without a decision"]
    else:
        command += ["--status", "done", "--no-follow-up", "--note", "Record closure without a decision"]
    closed = subprocess.run(command, capture_output=True, text=True, timeout=45, cwd=repo)
    if provider == "legacy" and method == "complete":
        # Preserve this older adapter's explicit-decision rule, not impose it on native callers.
        assert closed.returncode != 0, closed.stdout
        assert "user_gate completion requires decision_outcome" in json.loads(closed.stdout)["error"]
        assert "handler failed unexpectedly" not in closed.stdout
        assert list_goal_todos(registry_path=registry, goal_id=GOAL_ID)["todos"] == before
        return
    assert closed.returncode == 0, closed.stdout + closed.stderr
    payload = json.loads(closed.stdout)
    assert payload.get("decision_outcome") is None
    assert payload.get("decision_scope_resolution") is None
    assert payload.get("unblock_resume") is None
    after = {row["todo_id"]: row for row in list_goal_todos(
        registry_path=registry, goal_id=GOAL_ID)["todos"]}
    assert after[gate["todo_id"]]["status"] == "done"
    assert after[target["todo_id"]] == next(row for row in before if row["todo_id"] == target["todo_id"])
    replay = subprocess.run(command, capture_output=True, text=True, timeout=45, cwd=repo)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert after == {row["todo_id"]: row for row in list_goal_todos(
        registry_path=registry, goal_id=GOAL_ID)["todos"]}
