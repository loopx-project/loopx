"""Synthetic retained history through real File-runtime status and writeback."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from loopx.capabilities.progress_review.receipt import write_progress_review_receipt
from loopx.configure_goal import configure_goal
from loopx.status import autonomous_replan_obligation_from_runs, external_progress_review_context
from tests.control_plane.test_external_progress_review import receipt, run
from tests.control_plane.test_quota_settlement_cli import AGENT_ID, GOAL_ID, _write_fixture
from test_closed_loop import SOURCE, _find, _goal, _newest_first_runs, _refresh


@pytest.mark.parametrize("unbound_field", ["agent_id", "todo_id", "ack_agent_id"])
def test_unattributed_newer_result_cannot_hide_existing_obligation(tmp_path, unbound_field):
    project, runtime, registry = _write_fixture(tmp_path / "fixture")
    rows = [dict(run(n, turn=f"t{n}", agent=AGENT_ID), todo_id="todo-a") for n in (1, 2, 3)]
    for n, row in enumerate(rows, 1):
        row["progress_observation"]["evidence_ids"] = [f"evidence-{n}"]
    if unbound_field == "ack_agent_id":
        rows[-1].pop("agent_id")
        rows[-1]["autonomous_replan_ack"] = {"recorded": True, "semantic_delta": {"accepted": True}}
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    index.parent.mkdir(parents=True, exist_ok=True)
    index.write_text("".join(json.dumps(row) + "\n" for row in rows))
    for n in (1, 2, 3):
        positive = n == 3
        item = {
            **receipt(n, turn=f"t{n}", agent=AGENT_ID, todo="todo-a", noul=not positive, choice=not positive),
            "schema_version": "progress_review_receipt_v0",
            "signal_rule_version": "progress_review_signal_rule_v1", "goal_id": GOAL_ID,
            "question_version": "scoped-progress-sentinel-v2", "model": "fixture",
            "judgments": {"choice": {"relation": "on_goal" if positive else "off_goal",
                                     "increment": "new_evidence" if positive else "no_new_evidence"},
                          "noul": {name: .95 if positive else .05 for name in
                                   ("behavior_change", "serves_acceptance", "evidence_increment")}},
            "label_probability_threshold": .6, "recorded_at": float(n),
        }
        if n == 3:
            item["run"].pop("agent_id" if unbound_field == "ack_agent_id" else unbound_field)
        write_progress_review_receipt(runtime, GOAL_ID, item)
    configure_goal(registry_path=registry, goal_id=GOAL_ID, progress_review_mode="assist",
                   progress_review_contract_revision=hashlib.sha256(b"contract-1").hexdigest(), execute=True)
    context = external_progress_review_context(_goal(registry), runtime)
    obligation = autonomous_replan_obligation_from_runs(_newest_first_runs(runtime),
                    agent_todos=None, agent_id=AGENT_ID, external_progress_review=context)
    assert obligation and obligation["required"]
    assert obligation["triggers"][0]["unevaluated_transitions"]["by_reason"] == {"identity_conflict": 1}

    # The user and write-time validator observe the same still-open obligation.
    process = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--runtime-root", str(runtime),
         "--format", "json", "status", "--goal-id", GOAL_ID], cwd=SOURCE,
        env={**os.environ, "LOOPX_GLOBAL_REGISTRY": str(tmp_path / "global.json")},
        capture_output=True, text=True, timeout=180,
    )
    assert process.returncode == 0, process.stderr[-1000:]
    assert any(item.get("required") and any(t.get("kind") == "external_progress_review_drift" for t in item.get("triggers", []))
               for item in _find(json.loads(process.stdout), "autonomous_replan_obligation") if isinstance(item, dict))
    before = index.read_bytes()
    with pytest.raises(ValueError, match="already claimed in the obligation window"):
        _refresh(project, runtime, registry, autonomous_replan_recorded=True,
                 progress_observation=rows[0]["progress_observation"])
    assert index.read_bytes() == before
