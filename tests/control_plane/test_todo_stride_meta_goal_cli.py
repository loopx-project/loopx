"""A Todo's evidence must survive normal Turns and bind its next Stride action."""

from copy import deepcopy
import json
from pathlib import Path

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.quota.turn_envelope import build_turn_envelope
from loopx.control_plane.work_items.replan_context_codec import read_todo_stride_meta_goal
from research_comparison_fixture import vision
import test_quota_settlement_cli as fixture


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_todo_meta_goal_survives_turns_and_links_decisive_evidence(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project, runtime, registry = fixture._write_fixture(tmp_path)
    config = json.loads(registry.read_text())
    config["goals"][0]["quota"]["allowed_slots"] = 12
    registry.write_text(json.dumps(config))
    fixture._initialize_fixture_authority(project, runtime, provider=provider)
    goal, agent, todo = fixture.GOAL_ID, fixture.AGENT_ID, fixture.TODO_ID
    identity = ("--goal-id", goal, "--agent-id", agent)
    journal = runtime / "goals" / goal / "runs/index.jsonl"

    def cli(*arguments, expected=0):
        code, packet = fixture._run_cli(registry, runtime, *arguments, cwd=project)
        assert code == expected, packet.get("error") or packet.get("reason") or packet
        return packet

    def cadence(count):
        cli("configure-goal", "--goal-id", goal, "--execution-stride-strategy", "evidence_conditioned_v1",
            "--execution-replan-after-turns", str(count), "--execute")

    def guard(turn, task=None):
        return cli("quota", "should-run", *identity, "--codex-app", "--turn-instance-id", turn,
            *(("--todo-id", task) if task else ()))

    def spend(turn, task):
        cli("quota", "spend-slot", *identity, "--todo-id", task, "--turn-instance-id", turn,
            "--slots", "1", "--source", "heartbeat", "--execute")

    def work(turn, task, times, *, meta_decision=None, expect_gap=False):
        packet = guard(turn, task)
        assert packet["should_run"] and packet["selected_todo"]["todo_id"] == task
        work_context = packet["interaction_contract"]["agent_channel"].get("work_context", {})
        if meta_decision is None:
            assert "stride_meta_goal" not in work_context
        else:
            assert work_context["stride_meta_goal"]["decision_ref"] == meta_decision
            assert build_turn_envelope(packet)["work_context"]["stride_meta_goal"]["decision_ref"] == meta_decision
        # Use an actual deterministic input/result pair and independently derive the verdict.
        worst = max(times)
        (project / f"{turn}.json").write_text(json.dumps({"samples": times, "worst_seconds": worst}))
        assert (worst >= 30) == expect_gap
        cli("refresh-state", *identity, "--todo-id", task, "--turn-instance-id", turn,
            "--classification", "fixture_peak_measurement", "--delivery-batch-scale", "implementation",
            "--delivery-outcome", "outcome_progress",
            "--delivery-boundary", "in_flight_continuation",
            "--progress-result-class", "unchanged" if expect_gap else "advanced",
            "--progress-surface-id", turn, "--progress-probe-kind", "peak-measurement",
            "--progress-evidence-id", f"measurement:{turn}", "--recommended-action", f"Worst sample was {worst} seconds.",
            "--no-global-sync", "--suppress-external-sinks")
        spend(turn, task)

    def review(turn, *, scope=todo):
        guard(turn)
        packet = guard(turn, scope)
        return (packet["autonomous_replan_obligation"]["replan_context"],
                deepcopy(packet["replan_action_packet"]["stride_control"]["minimal_example"]))

    def store(turn, packet, *, expected=0):
        path = project / "stride-vision.json"
        path.write_text(json.dumps(packet))
        return cli("refresh-state", *identity, "--todo-id", todo, "--turn-instance-id", turn,
            "--classification", "fixture_stride_decision", "--delivery-batch-scale", "implementation",
            "--delivery-outcome", "outcome_progress", "--agent-vision-json", str(path),
            "--no-global-sync", "--suppress-external-sinks", expected=expected)

    def task_record():
        return cli("todo", "list", "--goal-id", goal, "--todo-id", todo)["todo"]["stride_meta_goal"]

    def proposal(context, stride):
        return {**vision({"objective": "Preserve the original Todo acceptance."},
            context["evidence"][0]["evidence_ref"]), "stride_decision": stride, "todo_delta": [f"retain:{todo}"]}

    cadence(1)
    other = cli("todo", "add", "--goal-id", goal, "--role", "agent", "--claimed-by", agent,
        "--text", "Measure a different work item.", "--task-class", "advancement_task",
        "--action-kind", "validate", "--target-key", "independent-peak-observation")["todo_id"]
    work("peak-baseline", todo, [16, 19])
    context, stride = review("review-initial-question")
    meta = {"todo_id": todo, "objective": "Does the candidate still meet the peak time limit?",
        "resolution_criterion": "Compare the worst measured sample with the 30 second limit.", "evidence_refs": []}
    stride["meta_goal"] = meta
    stride["next_probe"]["scope"] = "Compare the incumbent and candidate on the same representative peak samples."
    packet = proposal(context, stride)
    wrong_todo = deepcopy(packet)
    wrong_todo["stride_decision"]["meta_goal"]["todo_id"] = other
    wrong_todo["todo_delta"] = [f"retain:{other}"]
    before = journal.read_bytes()
    assert "open Agent Todo" in store("review-initial-question", wrong_todo, expected=1)["error"]
    assert journal.read_bytes() == before
    store("review-initial-question", packet)
    initial = task_record()
    assert initial["meta_goal"] == meta and initial["action"] == "verify"
    spend("review-initial-question", todo)

    cadence(3)
    work("peak-representative", todo, [17, 25], meta_decision=initial["decision_ref"])
    work("peak-unrelated", other, [6, 8])
    work("peak-tail", todo, [22, 32], meta_decision=initial["decision_ref"], expect_gap=True)
    context, stride = review("review-peak-result")
    [basis] = context["todo_meta_goals"]
    assert basis["decision_ref"] == initial["decision_ref"]
    assert len(basis["evidence"]) == 2
    tail = next(row for row in basis["evidence"] if row["result_class"] == "unchanged")
    foreign = next(row for row in context["decision_followthrough"]["observations"] if row["todo_id"] == other)
    stride.update(action="switch", meta_goal={**meta, "previous_decision_ref": initial["decision_ref"],
        "resolution": "The tail exceeded the limit at 32 seconds. Compare a different mechanism next.",
        "evidence_refs": [foreign["evidence_ref"]]})
    stride["next_probe"]["scope"] = "Test the streaming mechanism on the 32-second tail workload."
    packet = proposal(context, stride)
    before = journal.read_bytes()
    assert "subsequent settled work on the same Todo" in store("review-peak-result", packet, expected=1)["error"]
    assert journal.read_bytes() == before
    stride["meta_goal"]["evidence_refs"] = [tail["evidence_ref"]]
    accepted = store("review-peak-result", packet)
    assert accepted["autonomous_replan_ack"]["semantic_delta"]["accepted"]
    assert json.loads(Path(accepted["json_path"]).read_text())["agent_vision"]["stride_decision"]["meta_goal"] == stride["meta_goal"]
    current = task_record()
    assert current["action"] == "switch" and current["meta_goal"]["previous_decision_ref"] == initial["decision_ref"]
    assert current["next_probe"]["scope"] == "Test the streaming mechanism on the 32-second tail workload."
    assert current["evidence"][0]["evidence_ref"] == tail["evidence_ref"]
    exact = cli("history", *identity, "--evidence-ref", tail["evidence_ref"])["evidence"]
    assert exact["progress_observation"]["evidence_ids"] == ["measurement:peak-tail"]
    prior = cli("history", *identity, "--evidence-ref", initial["decision_ref"])["evidence"]
    assert prior["stride_decision"]["meta_goal"] == meta
    spend("review-peak-result", todo)
    later = guard("peak-after-resolution", todo)
    assert later["selected_todo"]["todo_id"] == todo
    assert "stride_meta_goal" not in later["interaction_contract"]["agent_channel"]["work_context"]
    cli("configure-goal", "--goal-id", goal, "--execution-stride-strategy", "off", "--execute")
    assert task_record() == current

    # Damage in the shared journal should affect only detail reads that have
    # a recorded source in this scope. The fixture is isolated from live state.
    journal_contents = journal.read_bytes()
    with journal.open("ab") as stream:
        stream.write(b'{"incomplete":"\xe2')
    unrelated = cli("todo", "list", "--goal-id", goal, "--todo-id", other)["todo"]
    assert unrelated["todo_id"] == other
    assert "stride_meta_goal" not in unrelated
    assert read_todo_stride_meta_goal(runtime_root=runtime, goal_id=goal, todo_id=todo,
        agent_id="different-agent") is None
    assert read_todo_stride_meta_goal(runtime_root=runtime, goal_id=goal, todo_id=todo,
        goal_ref={"goal_id": goal, "goal_instance_id": fixture.INSTANCE_A}) is None
    assert task_record() == {"unavailable": True}
    journal.write_bytes(journal_contents)
    assert task_record() == current
