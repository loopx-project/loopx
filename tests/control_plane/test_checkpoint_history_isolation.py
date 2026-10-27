"""Indexed settled history cannot poison another Agent's original delivery."""
from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from loopx.control_plane.goals.checkpoint_context_io import _checkpoint_effect, _receipt_path, pending_first_delivery_progress
from loopx.control_plane.quota.settlement import SettlementIdentity
from loopx.goal_mode_mcp import GoalModeMCPConfig, GoalModeMCPControlPlane
from loopx.todos import add_goal_todo
from tests.control_plane.test_default_checkpoint_continuation import _fixture
from tests.test_goal_mode_mcp_settlement import (
    AGENT_ID, GOAL_ID, _complete_default_task, _control, _run_cli,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("purpose", ["first_delivery", "delivery_result"])
def test_settled_peer_read_damage_preserves_independent_delivery(provider, purpose, tmp_path, monkeypatch):
    registry, _, first_todo = _fixture(provider, tmp_path, monkeypatch)
    peer = "independent-peer"
    registration = json.loads(registry.read_text(encoding="utf-8"))
    registration["goals"][0]["coordination"]["registered_agents"].append(peer)
    registry.write_text(json.dumps(registration), encoding="utf-8")
    first = _control(registry)
    assert json.loads(first.should_run()).get("delivery_result_context")
    settled = json.loads(_complete_default_task(first, first_todo, AGENT_ID,
        "Independent candidate verified.", next_agent_todo="Verify remaining independent work."))
    assert settled.get("settlement_complete") is True, settled
    runtime = tmp_path / "runtime"
    old = SettlementIdentity.from_runtime_payload(settled["settlement_identity"])
    proof = _checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
        # Todo work reads are committed by the Todo owner; its later indexed
        # direction proves the completed history for either disposable read.
        "runtime_root": str(runtime), "identity": old.as_dict(), "purpose": "first_delivery",
    })
    assert proof["status"] == "committed", proof
    artifacts = {Path(proof[key]): Path(proof[key]).read_bytes() for key in ("json_path", "markdown_path")}
    damaged = _receipt_path(runtime, old, purpose)
    damaged.write_text("{", encoding="utf-8")
    # This is the shared status and packaged UI readback, not only admission.
    assert pending_first_delivery_progress(runtime, GOAL_ID, peer, registry_path=registry) is None
    assert pending_first_delivery_progress(runtime, GOAL_ID, AGENT_ID, registry_path=registry) is None

    todo = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent", claimed_by=peer,
        text="Verify the independent peer candidate.", task_class="advancement_task",
        continuation_policy="same_agent_non_delivery")["todo_id"]
    rc, baseline = _run_cli(registry, "refresh-state", "--goal-id", GOAL_ID, "--agent-id", peer,
        "--vision-summary", "Verify the peer route.", "--vision-acceptance", "Verified peer candidate.",
        "--no-global-sync", "--suppress-external-sinks")
    assert rc == 0, baseline
    args = ("quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", peer,
            "--todo-id", todo, "--runtime-profile", "claude_code")
    rc, guard = _run_cli(registry, *args)
    assert rc == 0 and guard.get("delivery_result_context"), guard
    identity = guard["delivery_result_context"]["settlement_identity"]
    rc, selected = _run_cli(registry, *args, "--turn-instance-id", identity["turn_instance_id"])
    assert rc == 0 and selected["interaction_contract"]["agent_channel"]["delivery_allowed"] is True, selected
    control = GoalModeMCPControlPlane(GoalModeMCPConfig(server_name="fixture-peer",
        runtime_profile="claude_code", legacy_host_surface="claude_code"),
        lambda: {"goal_id": GOAL_ID, "registry": str(registry), "agent_id": peer})
    control.command_prefix = lambda: [sys.executable, "-m", "loopx.cli"]
    result = json.loads(_complete_default_task(control, todo, peer, "Peer candidate verified.",
        next_agent_todo="Verify remaining peer work."))
    assert result.get("settlement_complete") is True, result
    assert result["settlement_identity"] == identity
    assert pending_first_delivery_progress(runtime, GOAL_ID, peer, registry_path=registry) is None
    assert damaged.read_text(encoding="utf-8") == "{"
    assert all(path.read_bytes() == contents for path, contents in artifacts.items())
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    spends = [row for row in rows if row.get("classification") == "quota_slot_spent"]
    assert len(spends) == 2
    assert sum(row["settlement_identity"]["effect_id"] == identity["effect_id"] for row in spends) == 1


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("loss", ["damaged", "deleted"])
def test_recovered_history_retains_original_unspent_accounting(provider, loss, tmp_path, monkeypatch):
    registry, _, todo = _fixture(provider, tmp_path, monkeypatch)
    control = _control(registry)
    guard = json.loads(control.should_run())
    identity = SettlementIdentity.from_runtime_payload(guard["delivery_result_context"]["settlement_identity"])
    run_cli = control.run_cli

    def stop_before_spend(args, **kwargs):
        if args[:2] == ["quota", "spend-slot"]:
            return json.dumps({"ok": False, "error": "Synthetic stop before accounting."})
        return run_cli(args, **kwargs)

    control.run_cli = stop_before_spend
    result = json.loads(_complete_default_task(control, todo, AGENT_ID, "Candidate verified.",
        next_agent_todo="Verify remaining independent work."))
    assert result.get("settlement_complete") is not True, result
    runtime = tmp_path / "runtime"
    proof = _checkpoint_effect("goal.checkpoint_read_context.inspect_attempt", {
        "runtime_root": str(runtime), "identity": identity.as_dict(), "purpose": "first_delivery"})
    assert proof["status"] == "committed", proof
    for purpose in ("first_delivery", "delivery_result"):
        path = _receipt_path(runtime, identity, purpose)
        if loss == "deleted":
            path.unlink()
        else:
            path.write_text("{", encoding="utf-8")
    progress = pending_first_delivery_progress(runtime, GOAL_ID, AGENT_ID, registry_path=registry)
    assert progress["stage"] == "settlement_pending", progress
    assert progress["settlement_identity"] == identity.as_dict()
    assert progress["result_committed"] is True and progress["quota_spent"] is False
    rows = [json.loads(line) for line in (runtime / "goals" / GOAL_ID / "runs/index.jsonl").read_text().splitlines()]
    assert not any(row.get("classification") == "quota_slot_spent" for row in rows)
