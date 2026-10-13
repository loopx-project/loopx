"""Asynchronous facts may arrive after closeout, but cannot authorize new work."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from test_native_child_replan_guard_cli import AGENT, GOAL, ROOT, TODO, TURN, _admitted_guard, _fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("result_outcome", ["completed", "failed", "cancelled"])
def test_closed_replan_only_accepts_existing_native_operations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, result_outcome: str,
) -> None:
    call, runtime, index = _fixture(tmp_path, monkeypatch, provider, True)
    guard = _admitted_guard(call, True)
    original = guard["heartbeat_receipt"]["settlement_identity"]
    base = ("native-child", "--goal-id", GOAL, "--agent-id", AGENT, "--turn-instance-id", TURN)

    def reject(*args: str, reason: str) -> None:
        result = subprocess.run([sys.executable, "-m", "loopx.cli", "--registry",
            str(tmp_path / "registry.json"), "--runtime-root", str(runtime), "--format", "json",
            *args], cwd=ROOT, capture_output=True, text=True, timeout=60)
        assert result.returncode == 1, (result.stdout, result.stderr)
        assert reason in json.loads(result.stdout)["error"]

    decision = (*base, "record", "--operation-id", "op-original", "--stage", "decision",
                "--operation", "spawn", "--outcome", "started", "--entrypoint-id", "generic_host",
                "--execute")
    first = call(*decision)
    added = call("todo", "add", "--goal-id", GOAL, "--role", "agent", "--claimed-by", AGENT,
        "--text", "Independently validate the source artifact", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-source-artifact",
        "--operation-id", "native-late-source-successor",
        "--replan-obligation-id", guard["autonomous_replan_obligation"]["obligation_id"])
    assert added["replan_transition"]["recorded"] is True
    binding = ("--goal-id", GOAL, "--agent-id", AGENT, "--todo-id", TODO, "--turn-instance-id", TURN)
    vision = tmp_path / "replan-vision.json"
    vision.write_text(json.dumps({
        "schema_version": "goal_vision_replan_contract_v0", "state": "vision_patch_proposed",
        "vision_patch": {
            "acceptance_summary": "Validate each source slice before dependent work proceeds.",
            "advancement_policy": "as_needed",
        },
        "path_delta": {
            "schema_version": "goal_path_delta_v0", "outcome": "replan",
            "prior_assumption": "The long source chain needed a bounded review.",
            "observed_reality": "The reviewed chain has an independent source validation slice.",
            "retained": ["Validate the original source"],
            "changed": ["Proceed with the independent validation slice"],
            "evidence_refs": ["evidence:source-audit"],
        },
    }))
    # Long-chain replans require an evidence-linked path; unchanged prose cannot ACK them.
    refreshed = call("refresh-state", *binding, "--classification", "bounded_replan_progress",
        "--delivery-batch-scale", "single_surface", "--delivery-outcome", "outcome_progress",
        "--agent-vision-json", str(vision),
        "--no-global-sync", "--suppress-external-sinks")
    assert refreshed["settlement_progress"]["state"] == "spend_required"
    ack = json.loads(Path(refreshed["json_path"]).read_text())["autonomous_replan_ack"]
    assert ack["recorded"] is True
    # The preceding Todo add durably acknowledged the runnable successor.
    # Refresh reads that original ACK rather than substituting a later path outcome.
    assert "new_runnable_successor" in ack["semantic_delta"]["outcomes"]
    reject(*base, "record", "--operation-id", "op-new", "--stage", "decision",
        "--operation", "spawn", "--outcome", "started", "--entrypoint-id", "generic_host",
        "--execute", reason="open, work-admitted")
    pending_replay = call(*decision)
    assert pending_replay["appended"] is False
    assert pending_replay["receipt"]["event_id"] == first["receipt"]["event_id"]
    result = (*base, "record", "--operation-id", "op-original", "--stage", "result",
              "--operation", "spawn", "--entrypoint-id", "generic_host",
              "--outcome", result_outcome, "--reason-code", "bounded_result_observed", "--execute")
    if result_outcome == "completed":
        assert call(*result)["appended"] is True
    spent = call("quota", "spend-slot", *binding, "--slots", "1", "--source", "heartbeat", "--execute")
    assert spent["settlement_progress"]["state"] == "settled"
    closed_index = index.read_bytes()
    if result_outcome != "completed":
        assert call(*result)["appended"] is True
        reject(*base, "record", "--operation-id", "op-original", "--stage", "review",
            "--outcome", "accepted", "--evidence-ref", "evidence-source",
            "--validation-ref", "validation-source", "--execute", reason="completed")
    reject(*base, "record", "--operation-id", "op-new-skip", "--stage", "decision",
        "--operation", "skip", "--outcome", "skipped", "--entrypoint-id", "generic_host",
        "--reason-code", "parent_work_priority", "--execute", reason="open, work-admitted")
    reject(*base, "record", "--operation-id", "op-unknown", "--stage", "result",
        "--outcome", "completed", "--execute", reason="started")
    review = (*base, "record", "--operation-id", "op-original", "--stage", "review",
        "--operation", "spawn", "--entrypoint-id", "generic_host", "--outcome",
        "accepted" if result_outcome == "completed" else "rejected",
        *(["--evidence-ref", "evidence-source", "--validation-ref", "validation-source"]
          if result_outcome == "completed" else []), "--execute")
    accepted = call(*review)
    assert accepted["appended"] is True
    assert call(*review)["appended"] is False
    assert call(*result)["appended"] is False
    reject(*base, "record", "--operation-id", "op-original", "--stage", "result",
        "--outcome", "failed" if result_outcome != "failed" else "completed",
        "--execute", reason="conflicting")
    assert accepted["native_child_activity"]["parent_accepted_count"] == (result_outcome == "completed")
    assert accepted["native_child_activity"]["quota_spend_slots"] == 0
    cold = call(*base, "read")["native_child_activity"]
    assert cold == accepted["native_child_activity"]
    context = call("agent-context", "--goal-id", GOAL, "--agent-id", AGENT,
                   "--phase", "after_delegate_result", "--turn-instance-id", TURN)
    assert context["native_child_activity"] == cold
    assert index.read_bytes() == closed_index
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == 1
    assert sum(row.get("autonomous_replan_ack", {}).get("recorded") is True for row in rows) == 1
    assert call("todo", "list", "--goal-id", GOAL, "--todo-id", TODO)["todo"]["status"] == "open"
    assert call("todo", "list", "--goal-id", GOAL, "--todo-id", added["todo_id"])["todo"]["status"] == "open"
    assert original.get("todo_id") == TODO
