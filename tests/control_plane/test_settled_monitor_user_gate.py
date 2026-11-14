"""Exact monitor closeout wins over an unrelated scoped user gate."""
from __future__ import annotations

from canonical_authority_fixture import assert_todo_read_unchanged

import json

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_monitor_followthrough_contract import (
    AGENT_ID, GOAL_ID, _add_monitor, _write_fixture,
)
from loopx.control_plane.testing.canary_harness import run_json_cli
from loopx.control_plane.testing.canary_harness import run_json_cli_result
from loopx.todos import add_goal_todo, list_goal_todos, update_goal_todo
from loopx.control_plane.effect_program import ReceiptBoundMonitorPhase
from loopx.control_plane.quota.should_run import build_quota_should_run
from loopx.control_plane.testing.quota_fixtures import (
    quota_status_payload, quota_todo_item, quota_todo_summary,
)


def test_incomplete_frontier_cannot_erase_settled_monitor_receipt():
    monitor = quota_todo_item(todo_id="todo_monitor", title="Observe a public target",
        task_class="continuous_monitor", claimed_by="agent-a",
        next_due_at="2099-01-01T00:00:00Z", watch_only=True)
    summary = quota_todo_summary([monitor], claim_scope_agent_id="agent-a")
    # An incomplete legacy source cannot establish a monitor-only schedule.
    # This changes aggregate coverage, not the exact persisted poll receipt.
    summary["work_counts"]["complete"] = False
    status = quota_status_payload(goal_id="compact-monitor", status="active",
        quota_state="operator_gate", recommended_action="Wait for an owner decision",
        agent_todos=summary, user_todo_items=[quota_todo_item(
            todo_id="todo_peer_gate", title="Choose peer destination", role="user",
            task_class="user_gate", blocks_agent="agent-b")],
        coordination={"agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"]})
    replay = build_quota_should_run(status, goal_id="compact-monitor", agent_id="agent-a",
        receipt_bound_todo_id="todo_monitor",
        receipt_bound_monitor_phase=ReceiptBoundMonitorPhase.SETTLED)
    assert replay["selected_todo"]["todo_id"] == "todo_monitor"
    assert replay["should_run"] is False
    assert replay["effective_action"] == "heartbeat_settled_skip"
    assert replay["safe_bypass_allowed"] is False
    assert replay["execution_obligation"]["must_attempt_work"] is False
    assert replay["interaction_contract"]["agent_channel"]["delivery_allowed"] is False


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("material", [False, True])
def test_scoped_gate_cannot_reopen_an_exact_settled_monitor(tmp_path, monkeypatch, provider, material):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _state = _write_fixture(tmp_path, provider=provider)
    gated = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Work awaiting an owner decision", task_class="advancement_task",
        status="blocked", claimed_by=AGENT_ID, agent_id=AGENT_ID)
    add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="user",
        text="Approve the gated work", task_class="user_gate", bound_agent=AGENT_ID,
        blocks_agent=AGENT_ID, unblocks_todo_id=gated["todo_id"], agent_id=AGENT_ID)
    monitor = _add_monitor(registry, text="Observe an independent public target",
        target_key="public-target", next_due_at="2000-01-01T00:00:00Z")
    turn = "monitor-scoped-gate-turn"
    guard_args = ["quota", "should-run", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll"]
    def call(*args):
        return run_json_cli(*args, registry_path=registry, runtime_root=runtime)
    admitted = call(*guard_args)
    assert admitted["selected_todo"]["todo_id"] == monitor["todo_id"]
    assert admitted["execution_obligation"]["must_attempt_work"] is True
    poll_args = ["quota", "monitor-poll", "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        "--todo-id", monitor["todo_id"], "--target-key", "public-target", "--result-hash", "observed-head",
        *(["--material-change"] if material else []), "--execute"]
    poll = call(*poll_args)
    assert poll["turn_continuation"]["current_turn_settled"] is True
    successor = add_goal_todo(registry_path=registry, goal_id=GOAL_ID, role="agent",
        text="Validate an independent result", task_class="advancement_task",
        claimed_by=AGENT_ID, agent_id=AGENT_ID)
    replay = call(*guard_args)
    assert replay["heartbeat_receipt"]["settlement_identity"] == admitted["heartbeat_receipt"]["settlement_identity"]
    assert replay["selected_todo"]["todo_id"] == monitor["todo_id"]
    assert replay["agent_lane_next_action"]["receipt_bound_monitor_phase"] == "settled"
    assert replay["should_run"] is False
    assert replay["safe_bypass_allowed"] is False
    assert replay["execution_obligation"]["must_attempt_work"] is False
    interaction = replay["interaction_contract"]
    assert interaction["agent_channel"]["must_attempt"] is False
    assert interaction["agent_channel"]["delivery_allowed"] is False
    assert not any(action.startswith("loopx ") for action in interaction["cli_channel"].get("next_cli_actions", []))
    assert interaction["mode"] == "heartbeat_settled_skip"
    assert interaction["cli_channel"].get("spend_after_validation") is not True
    assert replay["automation_liveness"]["automation_action"] == "keep_active_quiet"
    duplicate = call(*poll_args)
    assert duplicate["replayed"] is True and duplicate["appended"] is False
    next_args = ["next-monitor-scoped-gate-turn" if x == turn else x for x in guard_args]
    fresh = call(*next_args)
    assert fresh["selected_todo"]["todo_id"] == successor["todo_id"]
    assert fresh["execution_obligation"]["must_attempt_work"] is True
    assert fresh["interaction_contract"]["mode"] == "scoped_user_gate_fallback"
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines()]
    assert sum(row["classification"] == "quota_monitor_poll" for row in rows) == 1
    assert not any(row["classification"] in {"state_refreshed", "quota_slot_spent"} for row in rows)


def _primary_monitor_fixture(
    tmp_path, monkeypatch, provider, route="source", *,
    resume_when=None, handoff_mode="soft_claim",
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    source, runtime, _state = _write_fixture(
        tmp_path, provider=provider, handoff_mode=handoff_mode)
    gated = add_goal_todo(registry_path=source, goal_id=GOAL_ID, role="agent",
        text="Await a scoped owner decision", task_class="advancement_task",
        status="blocked", claimed_by=AGENT_ID, agent_id=AGENT_ID)
    gate = add_goal_todo(registry_path=source, goal_id=GOAL_ID, role="user",
        text="Decide the dependent work", task_class="user_gate", bound_agent=AGENT_ID,
        blocks_agent=AGENT_ID, unblocks_todo_id=gated["todo_id"], agent_id=AGENT_ID)
    monitor = add_goal_todo(registry_path=source, goal_id=GOAL_ID, role="agent",
        text="Observe a synthetic public target", task_class="continuous_monitor",
        claimed_by=AGENT_ID, resume_when=resume_when,
        monitor_metadata={"target_key": "primary-target", "cadence": "1h",
            "next_due_at": "2000-01-01T00:00:00Z", "watch_only": "true"})
    registry = source
    if route == "global":
        registry = runtime / "registry.global.json"
        payload = json.loads(source.read_text())
        payload["registry_role"] = "global-local"
        payload["goals"][0]["source_registry"] = str(source)
        registry.write_text(json.dumps(payload))
    return source, registry, runtime, gated, gate, monitor


def _primary_monitor_args(command, turn, monitor):
    return ["quota", command, "--goal-id", GOAL_ID, "--agent-id", AGENT_ID,
        "--runtime-profile", "generic_cli", "--turn-instance-id", turn,
        "--available-capability", "network", "--available-capability", "external_evidence_poll",
        *(["--todo-id", monitor["todo_id"], "--target-key", "primary-target",
           "--result-hash", "primary-observation", "--execute"] if command == "monitor-poll" else [])]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("route", ["source", "global"])
@pytest.mark.parametrize("condition", [
    "resume_at:1999-01-01T00:00:00Z",
    "resume_at:2099-01-01T00:00:00Z",
    "todo_done:todo_missing",
], ids=["elapsed", "pending", "unresolved"])
def test_primary_monitor_poll_uses_canonical_resume_readiness(
    tmp_path, monkeypatch, provider, route, condition,
):
    elapsed = "resume_at:1999-01-01T00:00:00Z"
    source, registry, runtime, _gated, gate, monitor = _primary_monitor_fixture(
        tmp_path, monkeypatch, provider, route, resume_when=elapsed, handoff_mode="hard_lease")
    turn = "primary-monitor-resume-readiness"

    def call(*args):
        return run_json_cli_result(*args, registry_path=registry, runtime_root=runtime)

    rc, admitted = call(*_primary_monitor_args("should-run", turn, monitor))
    binding = admitted["heartbeat_receipt"]["settlement_identity"]
    assert rc == 0 and binding["todo_id"] == monitor["todo_id"]
    assert admitted["safe_bypass_kind"] == "scoped_user_gate_fallback"
    rc, acquired = call("task-lease", "acquire", "--goal-id", GOAL_ID,
        "--todo-id", monitor["todo_id"], "--owner", AGENT_ID, "--idempotency-key", "monitor-resume-lease")
    assert rc == 0 and acquired["acquired"], acquired
    lease = acquired["lease"]
    before = list_goal_todos(registry_path=source, goal_id=GOAL_ID, todo_id=monitor["todo_id"])["todo"]
    assert before["resume_ready"] is True
    if condition != before["resume_when"]:
        update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=monitor["todo_id"],
            agent_id=AGENT_ID, resume_when=condition,
            task_lease_idempotency_key=lease["idempotency_key"], task_lease_expected_version=lease["version"])
    before = list_goal_todos(registry_path=source, goal_id=GOAL_ID)
    row = next(item for item in before["todos"] if item["todo_id"] == monitor["todo_id"])
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    args = _primary_monitor_args("monitor-poll", turn, monitor)
    # Cover the exact-id source route and target-key-only global route.
    if route == "global":
        pos = args.index("--todo-id")
        del args[pos:pos + 2]
    if condition == elapsed:
        rc, denied = call(*args)
        assert rc != 0 and "lease proof" in denied["reason"], denied
        assert_todo_read_unchanged(list_goal_todos(registry_path=source, goal_id=GOAL_ID), before)
        assert not index.exists()
    args += ["--task-lease-idempotency-key", lease["idempotency_key"],
        "--task-lease-expected-version", str(lease["version"])]
    rc, result = call(*args)
    if condition != elapsed:
        assert row["resume_ready"] is False
        assert rc != 0 and result["appended"] is False, result
        assert_todo_read_unchanged(list_goal_todos(registry_path=source, goal_id=GOAL_ID), before)
        assert not index.exists()
        return

    assert rc == 0 and result["appended"], result
    assert result["settlement_todo_id"] == binding["todo_id"]
    assert result["turn_continuation"]["current_turn_settled"] is True
    committed = list_goal_todos(registry_path=source, goal_id=GOAL_ID)
    after = {item["todo_id"]: item for item in committed["todos"]}
    assert after[monitor["todo_id"]]["result_hash"] == "primary-observation"
    assert after[monitor["todo_id"]]["resume_when"] == condition
    assert after[gate["todo_id"]]["status"] == "open"
    ledger = index.read_bytes()
    rc, replay = call(*args)
    assert rc == 0 and replay["replayed"] and not replay["appended"], replay
    assert index.read_bytes() == ledger
    assert [json.loads(line)["classification"] for line in ledger.splitlines()] == ["quota_monitor_poll"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("route", ["source", "global"])
@pytest.mark.parametrize("gate_kind", ["linked", "unknown", "global"])
def test_primary_monitor_gate_rejects_new_write_but_preserves_committed_replay(
    tmp_path, monkeypatch, provider, route, gate_kind,
):
    source, registry, runtime, gated, gate, monitor = _primary_monitor_fixture(
        tmp_path, monkeypatch, provider, route)
    turn = "primary-monitor-gate-turn"
    def call(command):
        return run_json_cli_result(*_primary_monitor_args(command, turn, monitor),
            registry_path=registry, runtime_root=runtime)
    rc, admitted = call("should-run")
    assert rc == 0 and admitted["should_run"] is True
    assert admitted["heartbeat_receipt"]["settlement_identity"]["todo_id"] == monitor["todo_id"]
    if gate_kind == "unknown":
        add_goal_todo(registry_path=source, goal_id=GOAL_ID, role="user",
            text="Decide an unspecified dependency", task_class="user_gate",
            bound_agent=AGENT_ID, blocks_agent=AGENT_ID, agent_id=AGENT_ID)
    else:
        patch = {"unblocks_todo_id": monitor["todo_id"]} if gate_kind == "linked" else {
            "global_gate": True, "clear_blocks_agent": True}
        update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
            agent_id=AGENT_ID, **patch)
    rc, blocked = call("should-run")
    assert rc == 0 and blocked["should_run"] is False
    assert blocked["interaction_contract"]["agent_channel"]["delivery_allowed"] is False
    before = list_goal_todos(registry_path=source, goal_id=GOAL_ID)
    rc, denied = call("monitor-poll")
    assert rc == 1 and denied["error_code"] == "monitor_poll_admission_rejected", denied
    assert_todo_read_unchanged(list_goal_todos(registry_path=source, goal_id=GOAL_ID), before)
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    assert not index.exists()
    if gate_kind != "linked":
        return
    update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
        agent_id=AGENT_ID, unblocks_todo_id=gated["todo_id"], clear_global_gate=True)
    rc, observed = call("monitor-poll")
    assert rc == 0 and observed["ok"] is True, observed
    update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"],
        agent_id=AGENT_ID, unblocks_todo_id=monitor["todo_id"])
    committed = list_goal_todos(registry_path=source, goal_id=GOAL_ID)
    rc, replay = call("monitor-poll")
    assert rc == 0 and replay["replayed"] is True and replay["appended"] is False
    assert_todo_read_unchanged(list_goal_todos(registry_path=source, goal_id=GOAL_ID), committed)
    rows = [json.loads(line) for line in index.read_text().splitlines()]
    assert [row["classification"] for row in rows] == ["quota_monitor_poll"]


def _primary_record_invocation(registry, runtime, monitor, turn):
    from loopx.quota import record_quota_monitor_poll
    from loopx.status import collect_status
    run_json_cli(*_primary_monitor_args("should-run", turn, monitor),
        registry_path=registry, runtime_root=runtime)
    status = collect_status(registry_path=registry, runtime_root_override=str(runtime),
        limit=20,
        goal_id=GOAL_ID)
    return lambda: record_quota_monitor_poll(status, goal_id=GOAL_ID,
        registry_path=registry, execute=True, agent_id=AGENT_ID,
        todo_id=monitor["todo_id"], target_key="primary-target",
        result_hash="primary-observation", cadence="1h", turn_instance_id=turn,
        receipt_bound_todo_id=monitor["todo_id"],
        available_capabilities=["network", "external_evidence_poll"])


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_primary_monitor_gate_change_after_preflight_prevents_business_write(
    tmp_path, monkeypatch, provider,
):
    from loopx.control_plane.quota import monitor_poll
    source, registry, runtime, _gated, gate, monitor = _primary_monitor_fixture(
        tmp_path, monkeypatch, provider, resume_when="resume_at:1999-01-01T00:00:00Z")
    invoke = _primary_record_invocation(registry, runtime, monitor, "primary-gate-race")
    original = monitor_poll._provider_writeback
    def interleaved(plan, **kwargs):
        update_goal_todo(registry_path=source, goal_id=GOAL_ID,
            todo_id=gate["todo_id"], agent_id=AGENT_ID,
            unblocks_todo_id=monitor["todo_id"])
        return original(plan, **kwargs)
    monkeypatch.setattr(monitor_poll, "_provider_writeback", interleaved)
    denied = invoke()
    assert denied["ok"] is False and denied["error_code"] == "monitor_poll_rejected", denied
    assert "current User gate dependencies" in denied["reason"]
    current = list_goal_todos(registry_path=source, goal_id=GOAL_ID,
        todo_id=monitor["todo_id"])["todo"]
    assert current.get("result_hash") is None
    assert current["next_due_at"] == "2000-01-01T00:00:00Z"
    assert not (runtime / "goals" / GOAL_ID / "runs" / "index.jsonl").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("merge_repository", [None, "other/monitor", "example/monitor"],
    ids=["pending", "wrong-repo", "merged"])
def test_primary_monitor_rechecks_pr_wait_before_commit(
    tmp_path, monkeypatch, capsys, provider, merge_repository,
):
    from loopx.cli import main
    from loopx.control_plane.quota import monitor_poll
    from loopx.rollout_event_log import append_rollout_event, build_rollout_event, rollout_event_log_path

    source, registry, runtime, _gated, gate, monitor = _primary_monitor_fixture(
        tmp_path, monkeypatch, provider, resume_when="resume_at:1999-01-01T00:00:00Z",
        handoff_mode="hard_lease")
    todo_id = monitor["todo_id"]
    turn = "primary-monitor-pr-wait-race"
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "off")
    monkeypatch.chdir(tmp_path / "project")

    def call(*args):
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args])
        return code, json.loads(capsys.readouterr().out)

    def read():
        return list_goal_todos(registry_path=source, goal_id=GOAL_ID, todo_id=todo_id)["todo"]

    def record_merge(repository):
        append_rollout_event(rollout_event_log_path(runtime, GOAL_ID), build_rollout_event(
            goal_id=GOAL_ID, event_kind="pr_merge", pr_ref=f"{repository}#42",
            status="merged", recorded_at="2001-01-01T00:00:00Z"))

    rc, admitted = call(*_primary_monitor_args("should-run", turn, monitor))
    assert rc == 0 and admitted["heartbeat_receipt"]["settlement_identity"]["todo_id"] == todo_id
    assert admitted["safe_bypass_kind"] == "scoped_user_gate_fallback"
    rc, acquired = call("task-lease", "acquire", "--goal-id", GOAL_ID,
        "--todo-id", todo_id, "--owner", AGENT_ID, "--idempotency-key", "monitor-pr-wait-lease")
    assert rc == 0 and acquired["acquired"], acquired
    lease = acquired["lease"]
    args = [*_primary_monitor_args("monitor-poll", turn, monitor),
        "--task-lease-idempotency-key", lease["idempotency_key"],
        "--task-lease-expected-version", str(lease["version"])]

    def change_wait(wait):
        updated = update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=todo_id,
            agent_id=AGENT_ID, resume_when=wait, task_lease_idempotency_key=lease["idempotency_key"],
            task_lease_expected_version=lease["version"])
        assert updated["ok"] and updated["changed"], updated

    if merge_repository is not None:
        record_merge(merge_repository)
    matching_merge = merge_repository == "example/monitor"
    original = monitor_poll._provider_writeback
    commit_snapshot = []

    def interleaved(plan, **kwargs):
        change_wait("pr_merged:example/monitor#42")
        commit_snapshot.append(read())
        assert commit_snapshot[0]["resume_ready"] is matching_merge
        return original(plan, **kwargs)

    monkeypatch.setattr(monitor_poll, "_provider_writeback", interleaved)
    rc, result = call(*args)
    monkeypatch.setattr(monitor_poll, "_provider_writeback", original)
    assert len(commit_snapshot) == 1
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    if not matching_merge:
        assert rc == 1 and result["error_code"] == "monitor_poll_rejected" and not result["appended"], result
        assert read() == commit_snapshot[0] and not index.exists()
        record_merge("example/monitor")
        rc, result = call(*args)

    assert rc == 0 and result["appended"], result
    ledger = index.read_bytes()
    assert [json.loads(line)["classification"] for line in ledger.splitlines()] == ["quota_monitor_poll"]
    change_wait("pr_merged:example/monitor#43")
    committed = read()
    assert committed["resume_ready"] is False and committed["result_hash"] == "primary-observation"
    rc, replay = call(*args)
    assert rc == 0 and replay["replayed"] and not replay["appended"], replay
    assert read() == committed and index.read_bytes() == ledger
    assert list_goal_todos(registry_path=source, goal_id=GOAL_ID, todo_id=gate["todo_id"])["todo"]["status"] == "open"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("route", ["source", "global"])
@pytest.mark.parametrize("has_capacity", [False, True], ids=["missing", "available"])
def test_primary_monitor_rechecks_capacity_wait_before_commit(
    tmp_path, monkeypatch, capsys, provider, route, has_capacity,
):
    from loopx.cli import main
    from loopx.control_plane.quota import monitor_poll

    elapsed = "resume_at:1999-01-01T00:00:00Z"
    source, registry, runtime, _gated, _gate, monitor = _primary_monitor_fixture(
        tmp_path, monkeypatch, provider, route, resume_when=elapsed, handoff_mode="hard_lease")
    todo_id = monitor["todo_id"]
    turn = "primary-monitor-capacity-wait-race"
    monkeypatch.setenv("LOOPX_CONTEXT_DELIVERY", "off")
    monkeypatch.chdir(tmp_path / "project")

    def call(*args):
        code = main(["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args])
        return code, json.loads(capsys.readouterr().out)

    def read():
        return list_goal_todos(registry_path=source, goal_id=GOAL_ID, todo_id=todo_id)["todo"]

    rc, admitted = call(*_primary_monitor_args("should-run", turn, monitor))
    assert rc == 0 and admitted["heartbeat_receipt"]["settlement_identity"]["todo_id"] == todo_id
    rc, acquired = call("task-lease", "acquire", "--goal-id", GOAL_ID,
        "--todo-id", todo_id, "--owner", AGENT_ID, "--idempotency-key", "monitor-capacity-wait-lease")
    assert rc == 0 and acquired["acquired"], acquired
    lease = acquired["lease"]
    args = _primary_monitor_args("monitor-poll", turn, monitor)
    if route == "global":
        pos = args.index("--todo-id")
        del args[pos:pos + 2]
    args += ["--task-lease-idempotency-key", lease["idempotency_key"],
        "--task-lease-expected-version", str(lease["version"])]

    def change_wait(wait):
        updated = update_goal_todo(registry_path=source, goal_id=GOAL_ID, todo_id=todo_id,
            agent_id=AGENT_ID, resume_when=wait, task_lease_idempotency_key=lease["idempotency_key"],
            task_lease_expected_version=lease["version"])
        assert updated["ok"] and updated["changed"], updated

    original = monitor_poll._provider_writeback
    commit_snapshot = []
    capacity_wait = "capacity_available:benchmark_runner"

    def interleaved(plan, **kwargs):
        change_wait(capacity_wait)
        commit_snapshot.append(read())
        assert commit_snapshot[0]["resume_ready"] is False
        return original(plan, **kwargs)

    with monkeypatch.context() as interleaving:
        interleaving.setattr(monitor_poll, "_provider_writeback", interleaved)
        rc, denied = call(*args, *(["--available-capability", "benchmark_runner"] if has_capacity else []))
    assert len(commit_snapshot) == 1
    assert rc == 1 and denied["error_code"] == "monitor_poll_rejected" and not denied["appended"], denied
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    assert read() == commit_snapshot[0] and not index.exists()
    rc, denied = call(*args)
    assert rc == 1 and not denied["appended"] and not index.exists(), denied

    change_wait(elapsed)
    rc, applied = call(*args)
    assert rc == 0 and applied["appended"], applied
    ledger = index.read_bytes()
    assert [json.loads(line)["classification"] for line in ledger.splitlines()] == ["quota_monitor_poll"]

    change_wait("capacity_available:cli_bridge")
    committed = read()
    rc, replay = call(*args)
    assert rc == 0 and replay["replayed"] and not replay["appended"], replay
    assert read() == committed and index.read_bytes() == ledger


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("business_committed", [False, True])
def test_old_primary_monitor_plan_keeps_identity_without_new_gate_authority(
    tmp_path, monkeypatch, provider, business_committed,
):
    from loopx.control_plane.quota import monitor_poll
    source, registry, runtime, _gated, gate, monitor = _primary_monitor_fixture(
        tmp_path, monkeypatch, provider)
    invoke = _primary_record_invocation(registry, runtime, monitor, "primary-gate-recovery")
    original = monitor_poll._provider_writeback
    def interrupted(plan, **kwargs):
        if business_committed:
            original(plan, **kwargs)
        pending = list((runtime / "goals" / GOAL_ID / "runs/.transactions/quota-monitor-poll").glob("*.json"))
        assert len(pending) == 1
        # Compatibility fixture for a pre-guard primary Monitor reservation.
        receipt = json.loads(pending[0].read_text())
        receipt["provider_plan"].pop("gate_scope_guard")
        pending[0].write_text(json.dumps(receipt))
        raise ValueError("synthetic response interruption")
    monkeypatch.setattr(monitor_poll, "_provider_writeback", interrupted)
    assert invoke()["ok"] is False
    monkeypatch.setattr(monitor_poll, "_provider_writeback", original)
    update_goal_todo(registry_path=source, goal_id=GOAL_ID,
        todo_id=gate["todo_id"], agent_id=AGENT_ID, unblocks_todo_id=monitor["todo_id"])
    recovered = invoke()
    if business_committed:
        assert recovered["ok"] is True, recovered
        assert invoke()["replayed"] is True
    else:
        assert recovered["ok"] is False and recovered["error_code"] == "monitor_poll_rejected", recovered
        assert "current User gate dependencies" in recovered["reason"]
        current = list_goal_todos(registry_path=source, goal_id=GOAL_ID,
            todo_id=monitor["todo_id"])["todo"]
        assert current.get("result_hash") is None
    index = runtime / "goals" / GOAL_ID / "runs" / "index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
    assert [row["classification"] for row in rows] == (["quota_monitor_poll"] if business_committed else [])
