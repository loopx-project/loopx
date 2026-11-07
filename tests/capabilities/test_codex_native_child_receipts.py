from __future__ import annotations

import json
from pathlib import Path

import pytest

from loopx.extensions.codex_native_child import (
    configured_native_child_limit, CodexNativeChildObserver, native_child_observer,
)
from loopx.capabilities.multi_subagent.native_child_receipts import load_native_child_activity, record_native_child
from loopx.rollout_event_log import load_rollout_events, rollout_event_log_path
from tests.capabilities.test_native_child_receipts import _admit, GOAL, AGENT, TURN


def _turn():
    return {"id": "host-turn-1", "itemsView": "full", "status": "completed", "items": [
        {"type": "collabAgentToolCall", "id": "call-1", "senderThreadId": "parent-1",
         "tool": "spawnAgent", "status": "completed", "receiverThreadIds": ["child-1"],
         "prompt": "private child instructions", "agentsStates": {"child-1": {"status": "running"}}},
        {"type": "collabAgentToolCall", "id": "wait-1", "senderThreadId": "parent-1",
         "tool": "wait", "status": "completed", "agentsStates": {
             "child-1": {"status": "completed", "message": "private child result"}}},
    ]}


def _observe(root, items, session_id="parent-1"):
    observer = CodexNativeChildObserver(runtime_root=root,
        lineage={"goal_id": GOAL, "agent_id": AGENT},
        turn_instance_id=TURN, configured_limit=3)
    for item in items:
        observer.observe(item, session_id=session_id, invocation_id="host-turn-1")


def test_host_spawn_result_parent_review_and_restart(tmp_path: Path):
    _admit(tmp_path)
    _observe(tmp_path, _turn()["items"])
    _observe(tmp_path, _turn()["items"])
    activity = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert activity["observation"] == "host_observed"
    assert activity["host_attested"] is True
    assert activity["launched_count"] == 1
    assert activity["parent_accepted_count"] == 0
    [operation] = activity["operations"]
    assert operation["result"] == "completed"
    record_native_child(runtime_root=tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3, operation_id=operation["operation_id"],
        stage="review", outcome="accepted", evidence_ref="evidence-1",
        validation_ref="validation-1", execute=True)
    readback = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert readback["parent_accepted_count"] == 1
    assert readback["host_attested"] is True
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    assert len(events) == 4
    assert "private child" not in json.dumps(events)
    assert all(event.get("event_kind") != "quota_spend" for event in events)


@pytest.mark.parametrize("items", [[], [{"type": "agentMessage", "text": "I spawned three children"}],
    [{**_turn()["items"][0], "status": "inProgress"}],
    [{**_turn()["items"][0], "senderThreadId": "historical-parent"}]])
def test_missing_or_unrelated_host_events_stay_unknown(tmp_path: Path, items):
    _admit(tmp_path)
    _observe(tmp_path, items)
    activity = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert activity["observation"] == "unknown"
    assert activity["launched_count"] == 0


def test_exec_casing_and_display_counter_replay_do_not_duplicate_spawn(tmp_path: Path):
    _admit(tmp_path)
    snake = {"type": "collab_tool_call", "id": "item_1", "sender_thread_id": "parent-1",
        "tool": "spawn_agent", "status": "completed", "receiver_thread_ids": ["child-1"],
        "agents_states": {"child-1": {"status": "completed", "message": "private content"}}}
    _observe(tmp_path, [snake, {**snake, "id": "item_7"}])
    activity = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert activity["host_attested"] is True
    assert activity["launched_count"] == 1
    assert activity["operations"][0]["result"] == "completed"
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL))) == 3


def test_host_failure_does_not_infer_capacity_from_prose(tmp_path: Path):
    _admit(tmp_path)
    failed = {**_turn()["items"][0], "status": "failed", "receiverThreadIds": [],
        "agentsStates": {"child-1": {"status": "errored", "message": "agent_thread_limit_reached"}}}
    _observe(tmp_path, [failed])
    activity = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert activity["host_attested"] is True
    assert activity["host_failed_count"] == 1
    assert activity["capacity_rejected_count"] == 0
    assert activity["retry_same_turn"] is False


def test_feature_off_has_no_observer_and_reports_cannot_attest_reviews(tmp_path: Path):
    assert native_child_observer({"turn_envelope": {}}, runtime_root=tmp_path,
        lineage={"goal_id": GOAL, "agent_id": AGENT}) is None
    assert configured_native_child_limit({"turn_envelope": {}}) is None
    assert configured_native_child_limit({"turn_envelope": {"agent_context": {
        "contributions": [{"capability_id": "multi_subagent", "facts": {"max_children": 0}}]}}}) is None
    with pytest.raises(ValueError, match="cannot attest"):
        record_native_child(runtime_root=tmp_path, goal_id=GOAL, agent_id=AGENT,
            turn_instance_id=TURN, configured_limit=3, operation_id="review-1", stage="review",
            outcome="accepted", evidence_ref="evidence-1", validation_ref="validation-1",
            execute=True, _host_observed=True)


def test_cli_host_collects_native_items_before_returning_parent_result(tmp_path: Path, monkeypatch):
    import sys
    from loopx.control_plane.turn_driver import codex_cli
    from tests.test_loopx_turn_codex_cli import _request

    _admit(tmp_path)
    request = _request()
    request["turn_instance_id"] = TURN
    request["host_attempt"] = 1
    request["turn_envelope"].update(goal_id=GOAL, agent_id=AGENT, agent_context={
        "contributions": [{"capability_id": "multi_subagent", "facts": {"max_children": 3}}]})
    request["turn_envelope"]["action"]["selected_todo"]["todo_id"] = "todo_native_1"

    def host(command, **kwargs):
        kwargs["on_stdout"](json.dumps({"type": "thread.started", "thread_id": "parent-1"}) + "\n")
        for item in _turn()["items"]:
            kwargs["on_stdout"](json.dumps({"type": "item.completed", "item": item}) + "\n")
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps({"parent_work": "preserved"}))
        return {"returncode": 0, "outcome": "exited", "output_complete": True}

    monkeypatch.setattr(codex_cli, "run_host_process", host)
    assert codex_cli.run_codex_cli_host(request, runtime_root=tmp_path, project=tmp_path,
        codex_bin=sys.executable) == {"parent_work": "preserved"}
    activity = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert activity["host_attested"] is True
    assert activity["launched_count"] == 1
    assert activity["operations"][0]["result"] == "completed"


def _real_cli_calls(tmp_path, monkeypatch, batches, *, host_attempts=None):
    import sys
    from loopx.control_plane.turn_driver import codex_cli
    from tests.test_loopx_turn_codex_cli import _request

    script = tmp_path / "host.py"
    script.write_text("""
import json, sys
from pathlib import Path
sys.stdin.read()
print(json.dumps({"type": "thread.started", "thread_id": "parent-1"}), flush=True)
for item in json.loads(Path(sys.argv[1]).read_text(encoding="utf-8")):
    print(json.dumps({"type": "item.completed", "item": item}), flush=True)
Path(sys.argv[2]).write_text(json.dumps({"parent_work": "preserved"}), encoding="utf-8")
""", encoding="utf-8")
    event_file = tmp_path / "events.json"
    sessions = []

    def command(**kwargs):
        sessions.append(kwargs["session_id"])
        return [sys.executable, str(script), str(event_file), str(kwargs["output_path"])]

    # Replace only executable selection; run the real process, stream parser,
    # session store, receipt admission and durable readback.
    monkeypatch.setattr(codex_cli, "_codex_command", command)
    for attempt, items in enumerate(batches, 1):
        request = _request(session_action="start_new" if attempt == 1 else "resume")
        request["turn_instance_id"] = TURN
        request["host_attempt"] = host_attempts[attempt - 1] if host_attempts else attempt
        request["turn_envelope"].update(goal_id=GOAL, agent_id=AGENT, agent_context={
            "contributions": [{"capability_id": "multi_subagent", "facts": {"max_children": 3}}]})
        request["turn_envelope"]["action"]["selected_todo"]["todo_id"] = "todo_native_1"
        event_file.write_text(json.dumps(items), encoding="utf-8")
        assert codex_cli.run_codex_cli_host(request, runtime_root=tmp_path, project=tmp_path,
            codex_bin=sys.executable) == {"parent_work": "preserved"}
    assert sessions == [None] + ["parent-1"] * (len(batches) - 1)
    return load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)


def test_real_cli_resume_wait_only_completes_original_spawn(tmp_path, monkeypatch):
    _admit(tmp_path)
    spawn, wait = _turn()["items"]
    activity = _real_cli_calls(tmp_path, monkeypatch, [[spawn], [wait, wait]])
    assert activity["launched_count"] == 1
    assert activity["operation_count"] == 1
    assert activity["operations"][0]["result"] == "completed"
    assert activity["quota_spend_slots"] == 0
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL))) == 3


def test_real_cli_resume_counter_reuse_and_exact_replay(tmp_path, monkeypatch):
    _admit(tmp_path)
    followup = {"type": "collab_tool_call", "id": "item_0", "sender_thread_id": "parent-1",
        "tool": "send_input", "status": "completed", "receiver_thread_ids": ["child-1"]}
    other_child = {**followup, "receiver_thread_ids": ["child-2"]}
    failed = {**followup, "tool": "spawn_agent", "status": "failed", "receiver_thread_ids": []}
    activity = _real_cli_calls(tmp_path, monkeypatch, [
        [followup, followup], [other_child, other_child], [followup, followup],
        [failed, failed], [failed, failed]])
    assert activity["operation_count"] == 5
    assert activity["attempted_count"] == 5
    assert activity["host_failed_count"] == 2
    assert activity["launched_count"] == 0
    assert activity["quota_spend_slots"] == 0
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    assert len(events) == 6
    assert "private child" not in json.dumps(events)


def test_resume_restores_spawn_outside_the_visible_window(tmp_path):
    _admit(tmp_path)
    spawn, wait = _turn()["items"]
    items = [{**spawn, "receiverThreadIds": [f"child-{i}"], "agentsStates": {}}
             for i in range(1, 11)]
    _observe(tmp_path, items)
    _observe(tmp_path, [wait])
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    assert sum(event["event_kind"] == "native_child_decision" for event in events) == 10
    assert sum(event["event_kind"] == "native_child_result" for event in events) == 1


def test_resume_does_not_attest_coordinator_reported_or_unknown_children(tmp_path):
    import hashlib
    _admit(tmp_path)
    operation = "codex-" + hashlib.sha256(b"child-1").hexdigest()[:32]
    record_native_child(runtime_root=tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3, operation_id=operation, stage="decision",
        operation="spawn", outcome="started", entrypoint_id="codex_native_tools", execute=True)
    _observe(tmp_path, [_turn()["items"][1]])
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    assert not any(event["event_kind"] == "native_child_result" for event in events)


def test_restart_replay_uses_the_original_invocation_binding(tmp_path):
    _admit(tmp_path)
    followup = {**_turn()["items"][0], "tool": "sendInput", "agentsStates": {}}
    _observe(tmp_path, [followup])
    _observe(tmp_path, [followup])
    assert len(load_rollout_events(rollout_event_log_path(tmp_path, GOAL))) == 2


@pytest.mark.parametrize('with_spawn', [False, True])
@pytest.mark.parametrize('terminal_status,expected', [('completed', 'completed'), ('errored', 'failed')])
def test_real_cli_resumed_followup_owns_its_result(tmp_path, monkeypatch, with_spawn, terminal_status, expected):
    _admit(tmp_path)
    spawn, wait = _turn()['items']
    followup = {**spawn, 'id': 'item_0', 'tool': 'sendInput', 'agentsStates': {}}
    terminal = {**wait, 'agentsStates': {'child-1': {'status': terminal_status}}}
    batches = ([[spawn, wait]] if with_spawn else []) + [[followup], [terminal, terminal], [terminal]]
    activity = _real_cli_calls(tmp_path, monkeypatch, batches,
        host_attempts=[*range(1, len(batches)), len(batches) - 1])
    followups = [row for row in activity['operations'] if row['operation'] == 'followup']
    assert len(followups) == 1 and followups[0]['result'] == expected
    assert activity['operation_count'] == 1 + int(with_spawn)
    assert activity['launched_count'] == int(with_spawn)
    assert activity['quota_spend_slots'] == 0
    if with_spawn:
        original = next(row for row in activity['operations'] if row['operation'] == 'spawn')
        assert original['result'] == 'completed'
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    assert sum(event['event_kind'] == 'native_child_result' for event in events) == 1 + int(with_spawn)
    assert '"child-1"' not in json.dumps(events) and 'private child' not in json.dumps(events)


def test_real_cli_consecutive_followups_restore_the_latest_owned_operation(tmp_path, monkeypatch):
    _admit(tmp_path)
    spawn, wait = _turn()['items']
    followup = {**spawn, 'id': 'item_0', 'tool': 'sendInput', 'agentsStates': {}}
    failed = {**wait, 'agentsStates': {'child-1': {'status': 'errored'}}}
    activity = _real_cli_calls(tmp_path, monkeypatch,
        [[spawn, wait], [followup], [wait], [followup], [failed, failed], [failed]])
    followups = [row for row in activity['operations'] if row['operation'] == 'followup']
    assert len(followups) == 2
    assert {row['result'] for row in followups} == {'completed', 'failed'}
    assert activity['operation_count'] == 3 and activity['launched_count'] == 1
    assert activity['quota_spend_slots'] == 0


@pytest.mark.parametrize('host_observed,stage,outcome', [(False, 'decision', 'started'), (True, 'result', 'completed')])
def test_child_correlation_cannot_attest_a_report_or_result(tmp_path, host_observed, stage, outcome):
    _admit(tmp_path)
    with pytest.raises(ValueError, match='child correlation requires'):
        record_native_child(runtime_root=tmp_path, goal_id=GOAL, agent_id=AGENT,
            turn_instance_id=TURN, configured_limit=3, operation_id='correlation-1',
            stage=stage, outcome=outcome, operation='followup' if stage == 'decision' else None,
            entrypoint_id='codex_native_tools' if stage == 'decision' else None,
            execute=True, _host_observed=host_observed, _host_child_refs=['codex-child-opaque'])
    assert not any(event['event_kind'] == 'native_child_decision'
                   for event in load_rollout_events(rollout_event_log_path(tmp_path, GOAL)))



@pytest.mark.parametrize("snake", [False, True])
@pytest.mark.parametrize("new_wait", [False, True])
def test_real_cli_old_spawn_snapshot_cannot_complete_a_later_followup(
    tmp_path, monkeypatch, snake, new_wait,
):
    _admit(tmp_path)
    spawn, wait = _turn()["items"]
    spawn = {**spawn, "agentsStates": {"child-1": {"status": "completed"}}}
    followup = {**spawn, "id": "item_0", "tool": "sendInput",
                "agentsStates": {"child-1": {"status": "running"}}}
    if snake:
        def exec_item(item):
            return {"type": "collab_tool_call", "id": item["id"],
                    "sender_thread_id": item["senderThreadId"],
                    "tool": {"spawnAgent": "spawn_agent", "sendInput": "send_input", "wait": "wait"}[item["tool"]],
                    "status": item["status"], "receiver_thread_ids": item.get("receiverThreadIds", []),
                    "agents_states": item["agentsStates"]}
        spawn, followup, wait = map(exec_item, (spawn, followup, wait))
    # The third invocation replays only the old spawn's terminal snapshot. It
    # contains no new wait or followup result and cannot prove future work done.
    batches = [[spawn], [followup], [spawn]] + ([[wait, wait]] if new_wait else [])
    activity = _real_cli_calls(tmp_path, monkeypatch, batches)
    assert activity["operation_count"] == 2 and activity["launched_count"] == 1
    original = next(row for row in activity["operations"] if row["operation"] == "spawn")
    later = next(row for row in activity["operations"] if row["operation"] == "followup")
    assert original["result"] == "completed"
    assert later.get("result") == ("completed" if new_wait else None)
    assert activity["parent_accepted_count"] == 0 and activity["quota_spend_slots"] == 0
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    assert sum(row["event_kind"] == "native_child_result" for row in events) == 1 + int(new_wait)


@pytest.mark.parametrize("tool", ["spawnAgent", "sendInput"])
def test_nonwait_terminal_snapshot_ignores_unrelated_receivers(tmp_path, tool):
    _admit(tmp_path)
    spawn = _turn()["items"][0]
    other = {**spawn, "receiverThreadIds": ["child-2"], "agentsStates": {}}
    unrelated_snapshot = {**spawn, "tool": tool,
                          "agentsStates": {"child-2": {"status": "completed"}}}
    _observe(tmp_path, [other, unrelated_snapshot])
    activity = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert activity["operation_count"] == 2
    assert all(row.get("result") is None for row in activity["operations"])


@pytest.mark.parametrize("snake", [False, True])
@pytest.mark.parametrize("restart", [False, True])
def test_real_cli_consumed_wait_replay_cannot_complete_a_later_followup(
    tmp_path, monkeypatch, snake, restart,
):
    _admit(tmp_path)
    spawn, wait = _turn()["items"]
    followup = {**spawn, "id": "followup-1", "tool": "sendInput",
                "agentsStates": {"child-1": {"status": "running"}}}
    if snake:
        def exec_item(item):
            return {"type": "collab_tool_call", "id": item["id"],
                    "sender_thread_id": item["senderThreadId"],
                    "tool": {"spawnAgent": "spawn_agent", "sendInput": "send_input", "wait": "wait"}[item["tool"]],
                    "status": item["status"], "receiver_thread_ids": item.get("receiverThreadIds", []),
                    "agents_states": item["agentsStates"]}
        spawn, followup, wait = map(exec_item, (spawn, followup, wait))
    # Distinct native IDs within one invocation exclude counter reuse. Restart
    # must preserve the original attempt for an exact replay of the old wait.
    batches = [[spawn, wait], [followup], [wait]] if restart else [[spawn, wait, followup, wait]]
    activity = _real_cli_calls(tmp_path, monkeypatch, batches,
                               host_attempts=[1, 2, 1] if restart else [1])
    original = next(row for row in activity["operations"] if row["operation"] == "spawn")
    later = next(row for row in activity["operations"] if row["operation"] == "followup")
    assert original["result"] == "completed"
    assert later.get("result") is None
    assert activity["launched_count"] == 1 and activity["operation_count"] == 2
    assert activity["parent_accepted_count"] == 0 and activity["quota_spend_slots"] == 0
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    assert sum(event["event_kind"] == "native_child_result" for event in events) == 1
    assert '"child-1"' not in json.dumps(events) and "private child" not in json.dumps(events)


@pytest.mark.parametrize("spawn_completed", [False, True])
def test_distinct_waits_keep_their_first_binding_and_reject_changed_outcomes(tmp_path, spawn_completed):
    _admit(tmp_path)
    spawn, wait = _turn()["items"]
    if spawn_completed:
        spawn = {**spawn, "agentsStates": {"child-1": {"status": "completed"}}}
    followup = {**spawn, "id": "followup-1", "tool": "sendInput", "agentsStates": {}}
    fresh_wait = {**wait, "id": "wait-2"}
    snapshot = {**spawn, "agentsStates": {"child-1": {"status": "completed"}}}
    _observe(tmp_path, [spawn, wait, followup, snapshot, wait])
    pending = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    later = next(row for row in pending["operations"] if row["operation"] == "followup")
    assert later.get("result") is None
    _observe(tmp_path, [fresh_wait, fresh_wait])
    completed = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert all(row["result"] == "completed" for row in completed["operations"])
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    with pytest.raises(ValueError, match="conflicting"):
        _observe(tmp_path, [{**wait, "agentsStates": {"child-1": {"status": "errored"}}}])
    assert load_rollout_events(rollout_event_log_path(tmp_path, GOAL)) == events
    assert completed["parent_accepted_count"] == 0 and completed["quota_spend_slots"] == 0


def test_wait_binding_is_per_child_and_cannot_be_reassigned(tmp_path):
    _admit(tmp_path)
    spawn, wait = _turn()["items"]
    other = {**spawn, "id": "spawn-2", "receiverThreadIds": ["child-2"], "agentsStates": {}}
    both = {**wait, "agentsStates": {"child-1": {"status": "completed"},
                                    "child-2": {"status": "shutdown"}}}
    followup = {**spawn, "id": "followup-1", "tool": "sendInput", "agentsStates": {}}
    _observe(tmp_path, [spawn, other, both, followup, both])
    activity = load_native_child_activity(tmp_path, goal_id=GOAL, agent_id=AGENT,
        turn_instance_id=TURN, configured_limit=3)
    assert [row.get("result") for row in activity["operations"] if row["operation"] == "followup"] == [None]
    events = load_rollout_events(rollout_event_log_path(tmp_path, GOAL))
    result = next(row for row in events if row["event_kind"] == "native_child_result")
    later = next(row for row in activity["operations"] if row["operation"] == "followup")
    with pytest.raises(ValueError, match="conflicting native child binding"):
        record_native_child(runtime_root=tmp_path, goal_id=GOAL, agent_id=AGENT,
            turn_instance_id=TURN, configured_limit=3, operation_id=later["operation_id"],
            stage="result", outcome="completed", execute=True, _host_observed=True,
            _host_wait_ref=result["details"]["host_wait_ref"])
    assert load_rollout_events(rollout_event_log_path(tmp_path, GOAL)) == events


@pytest.mark.parametrize("host_observed,stage", [(False, "result"), (True, "decision")])
def test_wait_correlation_requires_an_observed_result(tmp_path, host_observed, stage):
    _admit(tmp_path)
    with pytest.raises(ValueError, match="wait correlation requires"):
        record_native_child(runtime_root=tmp_path, goal_id=GOAL, agent_id=AGENT,
            turn_instance_id=TURN, configured_limit=3, operation_id="op-1", stage=stage,
            outcome="completed" if stage == "result" else "started",
            operation="spawn" if stage == "decision" else None,
            entrypoint_id="codex_native_tools" if stage == "decision" else None,
            execute=True, _host_observed=host_observed, _host_wait_ref="codex-wait-known")
