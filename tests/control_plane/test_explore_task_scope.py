"""Admitted work consumes its explicit question links, independently of planning."""
import json

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
import test_quota_settlement_cli as fixture
from loopx.capabilities.explore.result_log import (
    append_explore_result_event,
    build_explore_finding_event,
    build_explore_node_event,
    explore_result_log_path,
)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_bound_quota_context_and_cli_pages_follow_current_task_without_selecting_it(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    monkeypatch.setenv("LOOPX_USAGE_PING", "0")
    project, runtime, registry = fixture._write_fixture(tmp_path)
    monkeypatch.chdir(project)
    fixture._initialize_fixture_authority(project, runtime, provider=provider)

    def cli(*args, expected=0):
        code, packet = fixture._run_cli(registry, runtime, *args, cwd=project)
        assert code == expected, json.dumps(packet)
        return packet

    identity = ("--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID)
    cli("configure-goal", "--goal-id", fixture.GOAL_ID, "--explore-mode", "planning", "--execute")
    cli("todo", "update", *identity, "--todo-id", fixture.TODO_ID,
        "--priority", "P2", "--explore-result-node-ref", "current-question")
    for i in range(4):
        other = cli("todo", "add", "--goal-id", fixture.GOAL_ID, "--role", "agent", "--priority", "P1",
            "--claimed-by", fixture.AGENT_ID, "--text", f"Validate independent artifact {i}.",
            "--task-class", "advancement_task", "--action-kind", "validate", "--target-key", f"other-{i}")
        cli("todo", "update", *identity, "--todo-id", other["todo_id"],
            "--explore-result-node-ref", "other-question")
    log = explore_result_log_path(runtime, fixture.GOAL_ID)
    for question in ["current-question", "other-question"]:
        append_explore_result_event(log, build_explore_node_event(
            goal_id=fixture.GOAL_ID, agent_id=fixture.AGENT_ID, node_id=question,
            title=question, node_kind="question", status="exploring"))
    for i, (node, status, value) in enumerate([
        ("current-question", "refuted", 4), ("current-question", "confirmed", 8),
        ("other-question", "refuted", 2), ("other-question", "confirmed", 7),
    ]):
        evidence = f"result:case-{i}"
        append_explore_result_event(log, build_explore_finding_event(
            goal_id=fixture.GOAL_ID, agent_id=fixture.AGENT_ID, node_id=node,
            finding_id=f"case-{i}", title=f"Observed case {i}", status=status,
            summary=f"Applies only to case {i}'s recorded inputs.", evidence_refs=[evidence],
            tags=["writeback-result"], recorded_at=f"2026-01-0{i + 1}T00:00:00Z",
            measurements=[dict(metric="quality", value=value, unit="points", higher_is_better=True,
                comparison_scope=f"validation:{node}", input_revision="inputs-v1",
                evaluation_ref="evaluator:v1", candidate_ref=f"candidate:case-{i}", evidence_ref=evidence)]))
    source = log.read_bytes()
    turn = "bound-task-evidence"
    cli("quota", "should-run", *identity, "--codex-app", "--turn-instance-id", turn)
    guard = cli("quota", "should-run", *identity, "--codex-app", "--turn-instance-id", turn,
        "--todo-id", fixture.TODO_ID)
    assert guard["selected_todo"]["todo_id"] == fixture.TODO_ID
    contexts = guard["interaction_contract"]["agent_channel"]["work_context"]["sources"]
    context = next(row["content"] for row in contexts if row.get("kind") == "explore_turn_context")
    assert context["graph"]["writeback_results"][0]["finding_id"] == "case-0"
    assert context["graph"]["measurement_feedback"][0]["node_id"] == "current-question"
    assert context["todo_id"] == fixture.TODO_ID
    assert fixture.TODO_ID not in {row["todo_id"] for row in context["harness"]["selected_branches"]}
    # The explicit task changes source relevance, not the independent planner.
    unscoped = cli("explore", "turn-context", *identity)
    assert unscoped["harness"]["selected_branches"] == context["harness"]["selected_branches"]
    assert unscoped["graph"]["writeback_results"][0]["node_id"] == "other-question"
    page = cli("explore", "turn-context", *identity, "--todo-id", fixture.TODO_ID, "--result-limit", "1")
    next_args = page["graph"]["result_page"]["next_command"]
    assert next_args[next_args.index("--todo-id") + 1] == fixture.TODO_ID
    # Execute the supplied command against the same real backend and reader.
    after = cli(*next_args[next_args.index("explore"):])
    assert after["graph"]["writeback_results"][0]["finding_id"] == "case-1"
    full = cli("explore", "turn-context", *identity, "--todo-id", fixture.TODO_ID, "--result-limit", "20")
    assert {row["finding_id"] for row in full["graph"]["writeback_results"]} == {f"case-{i}" for i in range(4)}
    assert full["graph"]["measurement_feedback"][0]["best"]["value"] == 8
    assert log.read_bytes() == source
    cli("todo", "update", *identity, "--todo-id", fixture.TODO_ID,
        "--explore-result-node-ref", "other-question")
    stale = cli(*next_args[next_args.index("explore"):], expected=1)
    assert "changed" in stale["error"] and log.read_bytes() == source
    cli("explore", "turn-context", *identity, "--todo-id", "todo_missing", expected=1)
