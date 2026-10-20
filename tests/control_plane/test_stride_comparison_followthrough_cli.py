"""Exact comparison provenance is planning intent, not execution authority.

Exercise a real canonical B successor beside a selected/retained A decision.
Subsequent B work must remain B work and cannot substantiate execution of A.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
import research_comparison_fixture as actor
import test_quota_settlement_cli as fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_current_source_replay_and_different_successor_do_not_certify_selected_work(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    monkeypatch.setenv("PYTHONPATH", str(fixture.REPO_ROOT))
    project, runtime, registry = fixture._write_fixture(tmp_path)
    monkeypatch.chdir(project)
    config = json.loads(registry.read_text())
    config["goals"][0]["quota"]["allowed_slots"] = 12
    registry.write_text(json.dumps(config))
    fixture._initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*arguments, expected=0):
        code, packet = fixture._run_cli(registry, runtime, *arguments, cwd=project)
        assert code == expected, json.dumps(packet)
        return packet

    identity = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)
    cli("configure-goal", "--goal-id", fixture.GOAL_ID, "--explore-mode", "planning",
        "--execution-replan-after-turns", "1", "--execution-stride-strategy", "evidence_conditioned_v1", "--execute")
    cli("todo", "update", *identity, "--todo-id", fixture.TODO_ID,
        "--explore-result-node-ref", "comparison-retained")
    training = project / "training.json"
    training.write_text(json.dumps([{"x": x, "y": y, "observed": x * y} for x, y in [(-2, -1), (1, -2)]]))
    request = {"project": str(project), "registry": str(registry), "runtime": str(runtime),
        "goal_id": fixture.GOAL_ID, "agent_id": fixture.AGENT_ID, "todo_id": fixture.TODO_ID,
        "objective": "Keep the comparison's current source distinct from later unrelated work.",
        "training": str(training), "budget": 125, "seed": 17}
    actor.probe(request, "retained")

    def guard(turn, todo=None):
        return cli("quota", "should-run", *identity, "--codex-app", "--scan-path", str(project),
            "--turn-instance-id", turn, *(("--todo-id", todo) if todo else ()))

    turn = "comparison-a-plan-b-successor"
    initial = guard(turn)
    obligation = initial["autonomous_replan_obligation"]
    assert {row["kind"] for row in obligation["triggers"]} == {"periodic_review_due"}
    old_choice = {"todo_id": fixture.TODO_ID,
        **initial["replan_action_packet"]["stride_control"]["explore_basis"]["comparison_candidates"][0]["outcomes"][0]}
    # This is an actual current-source update through the public Explore owner,
    # with the same finding id. A previously read hash must not survive it.
    cli("explore", "finding", *identity, "--finding-id", old_choice["finding_id"],
        "--node", old_choice["node_id"], "--title", "The retained program fails the recorded interaction cases.",
        "--summary", "Updated applicability: only these measured inputs; a later independent task is not this experiment.",
        "--status", "refuted", "--tag", "writeback-result", "--evidence-ref", "measured:retained-inputs")
    successor = cli("todo", "add", "--goal-id", fixture.GOAL_ID, "--role", "agent",
        "--claimed-by", fixture.AGENT_ID, "--text", "Independently measure another input relation.",
        "--task-class", "advancement_task", "--action-kind", "validate", "--target-key", "independent-b",
        "--replan-obligation-id", obligation["obligation_id"])
    other_id = successor["todo_id"]
    assert other_id != fixture.TODO_ID
    bound = guard(turn, fixture.TODO_ID)
    assert bound["heartbeat_receipt"]["settlement_identity"]["todo_id"] == fixture.TODO_ID
    assert bound["autonomous_replan_obligation"]["obligation_id"] == obligation["obligation_id"]
    control = bound["replan_action_packet"]["stride_control"]
    card = next(card for card in control["explore_basis"]["comparison_candidates"] if card["todo_id"] == fixture.TODO_ID)
    current_choice = {"todo_id": fixture.TODO_ID, **card["outcomes"][0]}
    assert current_choice["source_sha256"] != old_choice["source_sha256"]
    decision = deepcopy(control["minimal_example"])
    decision["explore_review"] = {"revision": control["explore_basis"]["revision"], "disposition": "used",
        "considered_refs": [current_choice["finding_id"]], "selected_candidate": old_choice,
        "reason": "Keep exact source provenance for the retained A plan."}
    packet = actor.vision(request, current_choice["finding_id"], outcome="replan")
    packet.update(stride_decision=decision, todo_delta=["retain:" + fixture.TODO_ID])
    packet["path_delta"]["changed"] = ["A remains the declared next probe; the separate B task does not count as its execution."]
    path = project / "review-a.json"
    index = runtime / "goals" / fixture.GOAL_ID / "runs/index.jsonl"

    def review(value, turn_id=turn, *, expected=0):
        path.write_text(json.dumps(value))
        return cli("refresh-state", *identity, "--todo-id", fixture.TODO_ID, "--turn-instance-id", turn_id,
            "--classification", "source_bound_comparison", "--delivery-batch-scale", "implementation",
            "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(path),
            "--no-global-sync", "--suppress-external-sinks", expected=expected)

    before = index.read_bytes()
    refused = review(packet, expected=1)
    assert not refused.get("appended")
    assert "exact scoped result" in refused["error"], refused
    assert index.read_bytes() == before
    packet["stride_decision"]["explore_review"]["selected_candidate"] = current_choice
    accepted = review(packet)
    delta = accepted["autonomous_replan_ack"]["semantic_delta"]
    assert delta["accepted"] and "fresh_vision_path_outcome" in delta["satisfying_outcomes"]
    persisted = json.loads(Path(accepted["json_path"]).read_text())
    assert persisted["agent_vision"]["todo_delta"] == ["retain:" + fixture.TODO_ID]
    assert persisted["agent_vision"]["stride_decision"]["explore_review"]["selected_candidate"] == current_choice
    cli("quota", "spend-slot", *identity, "--todo-id", fixture.TODO_ID, "--turn-instance-id", turn,
        "--slots", "1", "--source", "heartbeat", "--execute")

    # B receives its own normal admission, performs actual work, then settles.
    # The optional A selection never reserves or silently redirects execution.
    actual_turn = "actual-independent-b"
    actual = guard(actual_turn, other_id)
    assert actual["should_run"] and actual["selected_todo"]["todo_id"] == other_id
    import subprocess
    artifact = project / "actual-b.json"
    subprocess.run([sys.executable, "-c", "import json,sys;from pathlib import Path;"
        "Path(sys.argv[1]).write_text(json.dumps({'measured_sum':sum(x*x for x in [-2,0,3])}))", str(artifact)], check=True)
    assert json.loads(artifact.read_text())["measured_sum"] == 13
    cli("refresh-state", *identity, "--todo-id", other_id, "--turn-instance-id", actual_turn,
        "--classification", "independent_b_experiment", "--delivery-batch-scale", "implementation",
        "--delivery-outcome", "outcome_progress", "--delivery-boundary", "in_flight_continuation",
        "--progress-result-class", "advanced", "--progress-surface-id", "independent-b",
        "--progress-probe-kind", "sum-of-squares", "--progress-evidence-id", "measured:actual-b",
        "--no-global-sync", "--suppress-external-sinks")
    cli("quota", "spend-slot", *identity, "--todo-id", other_id, "--turn-instance-id", actual_turn,
        "--slots", "1", "--source", "heartbeat", "--execute")
    next_turn = "review-a-execution-claim"
    guard(next_turn)
    next_guard = guard(next_turn, fixture.TODO_ID)
    next_control = next_guard["replan_action_packet"]["stride_control"]
    follow = next_control["decision_followthrough"]
    assert follow["previous_decision"]["stride_decision"]["explore_review"]["selected_candidate"] == current_choice
    assert follow["execution_and_goal_gain"] == "unknown"
    [planned] = follow["planned_work"]
    assert planned["todo_id"] == fixture.TODO_ID
    assert planned["observation_refs"] == [] and not planned.get("ordinary_evidence_refs")
    observed_b = next(row for row in follow["observations"] if row["todo_id"] == other_id)
    next_packet = actor.vision(request, observed_b["evidence_ref"], outcome="replan")
    next_decision = deepcopy(next_control["minimal_example"])
    next_decision["previous_review"] = {"reference_decision_ref": follow["previous_decision"]["evidence_ref"],
        "adoption": "executed", "information_gain": "new_evidence", "goal_gain": "unknown",
        "evidence_refs": [observed_b["evidence_ref"]], "lesson": "Attempt to pass off B's measurement as A's execution."}
    next_packet.update(stride_decision=next_decision, todo_delta=["retain:" + fixture.TODO_ID])
    before = index.read_bytes()
    refused = review(next_packet, next_turn, expected=1)
    assert "declared work" in refused["error"] and not refused.get("appended"), refused
    assert index.read_bytes() == before
