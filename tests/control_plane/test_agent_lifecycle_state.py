"""Exercise worker states through the public projection and real peer admission.

`executing` and `unknown` come from execution facts (Turn lane liveness,
delegation worker locks, task leases), never from a Todo timestamp. The facts
fixtures here are real: a lane held in-process, a holder record edited the
way a crash or another machine leaves it, a lease file, a delegation row lock.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from loopx.control_plane.agents import management_projection as projection
from loopx.control_plane.agents.execution_facts import collect_agent_execution_facts
from loopx.control_plane.collaboration.inbox import _hash as manager_context_hash
from loopx.control_plane.collaboration.inbox import _root as manager_context_root
from loopx.control_plane.quota.task_orchestration import apply_task_orchestration_contract
from loopx.control_plane.turn_driver.lane_fence import turn_lane_singleflight, turn_lane_target
from loopx.file_lock import exclusive_file_lock, lock_holder_path

NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
GOAL = "test-goal"


def build_projection(monkeypatch, *, age=None, binding=False, status="open",
                     task_class="advancement_task", has_todo=True, extra_todos=(),
                     facts=None, runtime_root=None, registered=("peer",)):
    monkeypatch.setattr(projection, "now_utc", lambda: NOW)
    todo = {"todo_id": "todo_peer", "goal_id": GOAL, "role": "agent",
            "claimed_by": "peer", "status": status, "task_class": task_class,
            "action_kind": "inspect", "text": "Inspect the public contract."}
    if age is not None:
        todo["updated_at"] = (NOW - timedelta(hours=age)).isoformat()
    todos = ([todo] if has_todo else []) + list(extra_todos)
    payload = {"goal_filter": GOAL, "run_history": {"goals": [{
        "id": GOAL, "coordination": {
            "registered_agents": list(registered),
            "thread_agent_bindings": [{"agent_id": "peer", "thread_id": "thread-peer",
                                        "host_surface": "codex-app"}] if binding else [],
        }}]}, "todo_index": {"items": todos}}
    if runtime_root is not None:
        facts = collect_agent_execution_facts(runtime_root=runtime_root, status_payload=payload)
    execution_facts = {"peer": facts} if facts is not None and runtime_root is None else facts
    return projection.build_agent_management_projection(payload, execution_facts=execution_facts), todo


@pytest.mark.parametrize("kwargs,expected", [
    ({"has_todo": False}, "registered"),
    ({"has_todo": False, "binding": True}, "addressable"),
    ({"status": "done"}, "registered"),
    ({"status": "done", "binding": True}, "addressable"),
    ({}, "launchable"),
    ({"binding": True}, "bound"),
    # A fresh Todo update is activity, not execution: nothing runs behind it.
    ({"age": 0}, "launchable"),
    ({"age": 1}, "launchable"),
    ({"age": 1, "binding": True}, "bound"),
    ({"age": 8}, "launchable"),
    ({"age": 9, "binding": True}, "bound"),
    ({"age": -1}, "launchable"),
    ({"age": 48}, "launchable"),
    ({"status": "blocked", "age": 1, "binding": True}, "blocked"),
    ({"task_class": "blocker", "age": 1}, "blocked"),
    ({"task_class": "continuous_monitor", "age": 1}, "monitoring"),
    ({"task_class": "continuous_monitor", "age": 48}, "monitoring"),
    ({"status": "deferred", "age": 1}, "waiting"),
    # Execution facts decide `executing` and `unknown`, whatever the Todo age.
    ({"age": 30, "facts": {"lane": "live"}}, "executing"),
    ({"age": 30, "facts": {"lane": "absent", "delegation_worker_active": True}}, "executing"),
    ({"age": 0, "facts": {"lane": "dead"}}, "launchable"),
    ({"age": 0, "binding": True, "facts": {"lane": "dead"}}, "bound"),
    ({"age": 0, "facts": {"lane": "released"}}, "launchable"),
    ({"age": 0, "facts": {"lane": "foreign_host"}}, "unknown"),
    ({"age": 0, "facts": {"lane": "unreadable"}}, "unknown"),
    ({"age": 0, "facts": {"lane": "absent", "lease": {"status": "active", "expired": True}}}, "unknown"),
    ({"age": 0, "facts": {"lane": "absent", "lease": {"status": "active", "expired": False}}}, "launchable"),
    ({"age": 0, "facts": {"lane": "absent", "lease": {"status": "released"}}}, "launchable"),
    ({"age": 0, "facts": {"lane": "live", "lease": {"status": "active", "expired": True}}}, "executing"),
    ({"age": 0, "facts": {"lane": "foreign_host", "delegation_worker_active": True}}, "executing"),
    ({"has_todo": False, "facts": {"lane": "live"}}, "executing"),
    ({"has_todo": False, "facts": {"lane": "foreign_host"}}, "unknown"),
    ({"status": "blocked", "facts": {"lane": "live"}}, "blocked"),
    ({"task_class": "continuous_monitor", "facts": {"lane": "live"}}, "monitoring"),
])
def test_projected_state(monkeypatch, kwargs, expected):
    packet, _ = build_projection(monkeypatch, **kwargs)
    row = packet["agents"][0]
    assert row["state"] == expected
    assert "lifecycle_state" not in row
    assert ("session_binding_candidates" in row) == kwargs.get("binding", False)
    assert ("execution" in row) == ("facts" in kwargs)
    assert packet["truth_contract"]["projection_is_writable"] is False
    assert not hasattr(projection, "EXECUTING_ACTIVITY_THRESHOLD_HOURS")


def test_execution_facts_are_projected_as_evidence_not_authority(monkeypatch):
    facts = {"lane": "foreign_host", "lane_holder": {"host": "elsewhere", "pid": 7, "acquired_at": "2026-09-18T11:00:00Z"},
             "lease": {"status": "active", "expired": True}, "delegation_worker_active": False}
    packet, _ = build_projection(monkeypatch, age=0, facts=facts)
    row = packet["agents"][0]
    assert row["state"] == "unknown"
    assert row["execution"] == {"lane": "foreign_host", "lane_holder": facts["lane_holder"],
                                "lease": {"status": "active", "expired": True}}
    assert packet["source_summary"]["execution_facts_collected"] is True
    without, _ = build_projection(monkeypatch, age=0)
    assert "execution_facts_collected" not in without["source_summary"]


def test_unrelated_blocked_activity_does_not_change_current_work(monkeypatch):
    other = {"todo_id": "todo_blocked", "goal_id": GOAL, "role": "agent",
             "claimed_by": "peer", "status": "blocked", "task_class": "blocker",
             "updated_at": NOW.isoformat()}
    packet, _ = build_projection(monkeypatch, extra_todos=[other])
    row = packet["agents"][0]
    assert row["current_todo"]["todo_id"] == "todo_peer"
    assert row["blocked_on"]["todo_id"] == "todo_blocked"
    assert row["state"] == "launchable"


# --- facts collected from the real runtime layout ---------------------------

def _lane(runtime_root: Path) -> Path:
    return turn_lane_target(runtime_root=runtime_root, goal_id=GOAL,
                            plan={"turn_envelope": {"agent_id": "peer"}})


def _rewrite_holder(target: Path, **changes) -> None:
    holder_path = lock_holder_path(target)
    record = json.loads(holder_path.read_text(encoding="utf-8"))
    record.pop("released_at", None)
    record.update(changes)
    holder_path.write_text(json.dumps(record), encoding="utf-8")


def _dead_pid() -> int:
    pid = os.getpid()
    while True:
        pid += 1
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return pid
        except OSError:
            continue


def _write_lease(runtime_root: Path, *, expires_in_hours: float, status: str = "active",
                 owner: str = "peer") -> None:
    now = datetime.now(timezone.utc)
    path = runtime_root / "goals" / GOAL / "task-leases" / "todo_peer.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema_version": "task_lease_v0", "goal_id": GOAL, "todo_id": "todo_peer", "owner": owner,
        "idempotency_key": "execution-peer", "version": 1, "lease_epoch": 1, "status": status,
        "write_scopes": ["src/**"], "acquire_ttl_seconds": 600, "acquired_at": now.isoformat(),
        "updated_at": now.isoformat(), "expires_at": (now + timedelta(hours=expires_in_hours)).isoformat(),
    }), encoding="utf-8")


def test_a_lane_held_in_process_makes_a_stale_todo_executing(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    plan = {"turn_envelope": {"agent_id": "peer"}}
    with turn_lane_singleflight(runtime_root=runtime_root, goal_id=GOAL, plan=plan) as held:
        assert held is not None
        packet, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root)
    row = packet["agents"][0]
    assert row["state"] == "executing"
    assert row["execution"]["lane"] == "live"
    assert row["execution"]["lane_holder"]["pid"] == os.getpid()
    assert "stale_claim_hint" not in row
    # Once the Turn settles cleanly the same stale Todo is only launchable.
    after, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root)
    assert after["agents"][0]["state"] == "launchable"
    assert after["agents"][0]["execution"]["lane"] == "released"


def test_a_dead_holder_behind_a_fresh_todo_is_not_executing(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    plan = {"turn_envelope": {"agent_id": "peer"}}
    with turn_lane_singleflight(runtime_root=runtime_root, goal_id=GOAL, plan=plan):
        pass
    _rewrite_holder(_lane(runtime_root), pid=_dead_pid())
    packet, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root)
    assert packet["agents"][0]["state"] == "launchable"
    assert packet["agents"][0]["execution"]["lane"] == "dead"
    bound, _ = build_projection(monkeypatch, age=0, binding=True, runtime_root=runtime_root)
    assert bound["agents"][0]["state"] == "bound"


def test_a_foreign_host_holder_is_unknown(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    plan = {"turn_envelope": {"agent_id": "peer"}}
    with turn_lane_singleflight(runtime_root=runtime_root, goal_id=GOAL, plan=plan):
        pass
    _rewrite_holder(_lane(runtime_root), host="another-machine")
    packet, _ = build_projection(monkeypatch, age=0, binding=True, runtime_root=runtime_root)
    row = packet["agents"][0]
    assert row["state"] == "unknown"
    assert row["execution"]["lane"] == "foreign_host"
    assert row["execution"]["lane_holder"]["host"] == "another-machine"


def test_an_expired_active_lease_with_nothing_live_is_unknown(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    _write_lease(runtime_root, expires_in_hours=-1)
    packet, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root)
    row = packet["agents"][0]
    assert row["state"] == "unknown"
    assert row["execution"] == {"lane": "absent", "lease": {"status": "active", "expired": True}}
    _write_lease(runtime_root, expires_in_hours=1)
    fresh, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root)
    assert fresh["agents"][0]["state"] == "launchable"
    assert fresh["agents"][0]["execution"]["lease"] == {"status": "active", "expired": False}
    _write_lease(runtime_root, expires_in_hours=-1, status="released")
    released, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root)
    assert released["agents"][0]["state"] == "launchable"
    assert released["agents"][0]["execution"]["lease"] == {"status": "released"}


def _delegation_row(runtime_root: Path, *, goal: str, requester: str, agent: str = "peer",
                    operation: str = "op-1", status: str = "running") -> tuple[Path, Path]:
    """A journal row where the delegation service keeps it, plus its execution slot."""
    store = manager_context_root(runtime_root)
    row_path = (store / "executions" / manager_context_hash([goal, requester])
                / (manager_context_hash(operation) + ".json"))
    row_path.parent.mkdir(parents=True, exist_ok=True)
    row_path.write_text(json.dumps({"identity": {"binding": {"id": "b1", "agent_id": agent, "todo_id": "todo_peer"},
                                                 "request_id": "r1", "operation_id": operation},
                                    "status": status}), encoding="utf-8")
    return row_path, store / "execution-slots" / manager_context_hash([goal, "todo_peer"])


_WORKER = """
import sys
from pathlib import Path
from loopx.file_lock import exclusive_file_lock
with exclusive_file_lock(Path(sys.argv[1])), exclusive_file_lock(Path(sys.argv[2])):
    print("held", flush=True)
    sys.stdin.readline()
"""


@contextmanager
def _worker_process(row_path: Path, slot: Path):
    """A separate process holding one operation lock and the Todo's slot, as a worker does."""
    worker = subprocess.Popen([sys.executable, "-c", _WORKER, str(row_path), str(slot)],
                              stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert worker.stdout.readline().strip() == "held"
        yield worker.pid
    finally:
        worker.stdin.close()
        worker.wait(timeout=30)


def _peer_row(packet):
    return next(row for row in packet["agents"] if row["agent_id"] == "peer")


def test_a_live_delegation_worker_is_executing(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    registered = ("coordinator", "peer")
    row_path, slot = _delegation_row(runtime_root, goal=GOAL, requester="coordinator")
    with exclusive_file_lock(row_path), exclusive_file_lock(slot):
        packet, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=registered)
    assert _peer_row(packet)["state"] == "executing"
    assert _peer_row(packet)["execution"] == {"lane": "absent", "delegation_worker_active": True}
    # The worker exited: its released locks are no evidence of execution.
    settled, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=registered)
    assert _peer_row(settled)["state"] == "launchable"
    assert _peer_row(settled)["execution"] == {"lane": "absent"}


def test_an_operation_lock_without_its_execution_slot_is_not_a_worker(monkeypatch, tmp_path):
    """Readers and result adoption also hold the row lock briefly; only the worker holds the slot."""
    runtime_root = tmp_path / "runtime"
    registered = ("coordinator", "peer")
    row_path, _slot = _delegation_row(runtime_root, goal=GOAL, requester="coordinator")
    with exclusive_file_lock(row_path):
        packet, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root, registered=registered)
    assert _peer_row(packet)["state"] == "launchable"
    # A worker executing under another Goal's journal is not this Goal's fact.
    other_row, other_slot = _delegation_row(runtime_root, goal="other-goal", requester="coordinator")
    with exclusive_file_lock(other_row), exclusive_file_lock(other_slot):
        scoped, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root, registered=registered)
    assert _peer_row(scoped)["state"] == "launchable"


REUSED = ("coordinator", "peer", "old-peer")


def test_a_historical_result_reader_does_not_borrow_the_current_workers_slot(monkeypatch, tmp_path):
    """The Todo was re-bound: old-peer's accepted operation is only being read while peer's worker runs."""
    runtime_root = tmp_path / "runtime"
    old_row, slot = _delegation_row(runtime_root, goal=GOAL, requester="coordinator", agent="old-peer",
                                    operation="op-old", status="accepted")
    new_row, _ = _delegation_row(runtime_root, goal=GOAL, requester="coordinator", operation="op-new")
    with _worker_process(new_row, slot) as worker_pid, exclusive_file_lock(old_row):
        assert worker_pid != os.getpid()
        packet, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=REUSED)
        facts = collect_agent_execution_facts(runtime_root=runtime_root, status_payload=_payload(REUSED))
    by_agent = {row["agent_id"]: row for row in packet["agents"]}
    assert by_agent["peer"]["state"] == "executing"
    assert by_agent["old-peer"]["state"] == "registered"
    assert facts["old-peer"]["delegation_worker_active"] is False


def test_an_operation_held_by_another_process_than_the_slot_is_not_that_workers(monkeypatch, tmp_path):
    """Two live holders are two facts: only the process holding the slot is the worker."""
    runtime_root = tmp_path / "runtime"
    # Still in flight, so only the holder identity can tell the two operations apart.
    old_row, slot = _delegation_row(runtime_root, goal=GOAL, requester="old-requester", agent="old-peer",
                                    operation="op-old", status="running")
    new_row, _ = _delegation_row(runtime_root, goal=GOAL, requester="coordinator", operation="op-new")
    registered = ("coordinator", "old-requester", "peer", "old-peer")
    with _worker_process(new_row, slot), exclusive_file_lock(old_row):
        packet, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=registered)
    by_agent = {row["agent_id"]: row for row in packet["agents"]}
    assert by_agent["peer"]["state"] == "executing"
    assert by_agent["old-peer"]["state"] == "registered"


def test_a_reader_that_locked_first_is_not_the_worker_of_an_unseen_operation(monkeypatch, tmp_path):
    """The reader took the old operation before the worker took the slot; order alone cannot tell them apart."""
    runtime_root = tmp_path / "runtime"
    old_row, slot = _delegation_row(runtime_root, goal=GOAL, requester="coordinator", agent="old-peer",
                                    operation="op-old", status="running")
    # The worker's own row is under a requester this Goal does not project.
    new_row, _ = _delegation_row(runtime_root, goal=GOAL, requester="unlisted", operation="op-new")
    with exclusive_file_lock(old_row), _worker_process(new_row, slot):
        packet, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=REUSED)
    by_agent = {row["agent_id"]: row for row in packet["agents"]}
    assert by_agent["old-peer"]["state"] == "registered"
    assert by_agent["peer"]["state"] == "launchable"


def test_a_crashed_operation_whose_pid_was_reused_does_not_share_the_slot(monkeypatch, tmp_path):
    """A slot names one worker: the latest in-flight operation its process took before the slot."""
    runtime_root = tmp_path / "runtime"
    old_row, slot = _delegation_row(runtime_root, goal=GOAL, requester="coordinator", agent="old-peer",
                                    operation="op-old", status="running")
    new_row, _ = _delegation_row(runtime_root, goal=GOAL, requester="coordinator", operation="op-new")
    with exclusive_file_lock(old_row):
        pass
    with _worker_process(new_row, slot) as worker_pid:
        # The old worker crashed mid-run and its pid now belongs to the new worker.
        _rewrite_holder(old_row, pid=worker_pid)
        packet, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=REUSED)
        by_agent = {row["agent_id"]: row for row in packet["agents"]}
        assert by_agent["peer"]["state"] == "executing"
        assert by_agent["old-peer"]["state"] == "registered"
        # When the records cannot say which operation came last, neither is named.
        new_acquired = json.loads(lock_holder_path(new_row).read_text(encoding="utf-8"))["acquired_at"]
        _rewrite_holder(old_row, pid=worker_pid, acquired_at=new_acquired)
        tied, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=REUSED)
        # The slot is taken inside its operation: a lock the process took after the slot is not its operation.
        slot_acquired = datetime.fromisoformat(
            json.loads(lock_holder_path(slot).read_text(encoding="utf-8"))["acquired_at"].replace("Z", "+00:00"))
        _rewrite_holder(old_row, pid=worker_pid, acquired_at=(slot_acquired + timedelta(seconds=1)).isoformat())
        later, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root, registered=REUSED)
    assert {row["agent_id"]: row["state"] for row in later["agents"]} == {
        "coordinator": "registered", "peer": "executing", "old-peer": "registered"}
    assert {row["agent_id"]: row["state"] for row in tied["agents"]} == {
        "coordinator": "registered", "peer": "launchable", "old-peer": "registered"}


def test_a_settled_operation_is_no_worker_even_under_the_slot_holder(monkeypatch, tmp_path):
    """An accepted or rejected operation has no further transition: its holder is a reader."""
    runtime_root = tmp_path / "runtime"
    for status in ("accepted", "rejected"):
        row_path, slot = _delegation_row(runtime_root, goal=GOAL, requester="coordinator", status=status)
        with exclusive_file_lock(row_path), exclusive_file_lock(slot):
            packet, _ = build_projection(monkeypatch, age=30, runtime_root=runtime_root,
                                         registered=("coordinator", "peer"))
        assert _peer_row(packet)["state"] == "launchable", status


def test_a_released_or_stale_worker_record_is_no_worker(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    registered = ("coordinator", "peer")
    row_path, slot = _delegation_row(runtime_root, goal=GOAL, requester="coordinator")
    with exclusive_file_lock(row_path), exclusive_file_lock(slot):
        pass
    released, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root, registered=registered)
    assert _peer_row(released)["state"] == "launchable"
    # A crashed worker leaves both records naming its pid; a dead pid executes nothing.
    dead = _dead_pid()
    _rewrite_holder(row_path, pid=dead)
    _rewrite_holder(slot, pid=dead)
    crashed, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root, registered=registered)
    assert _peer_row(crashed)["state"] == "launchable"


def test_one_agents_live_lane_is_not_another_agents_execution(monkeypatch, tmp_path):
    runtime_root = tmp_path / "runtime"
    plan = {"turn_envelope": {"agent_id": "old-peer"}}
    with turn_lane_singleflight(runtime_root=runtime_root, goal_id=GOAL, plan=plan) as held:
        assert held is not None
        packet, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root, registered=REUSED)
    by_agent = {row["agent_id"]: row for row in packet["agents"]}
    assert by_agent["old-peer"]["state"] == "executing"
    assert by_agent["peer"]["state"] == "launchable"
    assert by_agent["peer"]["execution"]["lane"] == "absent"


def test_a_lease_left_by_the_previous_claimant_stays_with_its_owner(monkeypatch, tmp_path):
    """A re-bound Todo's stale lease is the old owner's unverifiable fact, never the new claimant's."""
    runtime_root = tmp_path / "runtime"
    _write_lease(runtime_root, expires_in_hours=-1, owner="old-peer")
    packet, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root, registered=REUSED)
    by_agent = {row["agent_id"]: row for row in packet["agents"]}
    assert by_agent["peer"]["state"] == "launchable"
    assert "lease" not in by_agent["peer"]["execution"]
    assert by_agent["old-peer"]["state"] == "unknown"
    _write_lease(runtime_root, expires_in_hours=-1, owner="old-peer", status="released")
    released, _ = build_projection(monkeypatch, age=0, runtime_root=runtime_root, registered=REUSED)
    assert {row["agent_id"]: row["state"] for row in released["agents"]}["old-peer"] == "registered"


def _payload(registered):
    todo = {"todo_id": "todo_peer", "goal_id": GOAL, "role": "agent", "claimed_by": "peer", "status": "open"}
    return {"goal_filter": GOAL, "run_history": {"goals": [{
        "id": GOAL, "coordination": {"registered_agents": list(registered)}}]},
        "todo_index": {"items": [todo]}}


def test_no_runtime_root_means_no_facts_not_no_execution(monkeypatch):
    payload = {"goal_filter": GOAL, "run_history": {"goals": [{"id": GOAL, "coordination": {"registered_agents": ["peer"]}}]}}
    assert collect_agent_execution_facts(runtime_root=None, status_payload=payload) is None
    assert collect_agent_execution_facts(runtime_root="/nonexistent-loopx-runtime", status_payload=payload) is None
    packet, _ = build_projection(monkeypatch, age=0)
    assert "execution" not in packet["agents"][0]


# --- real projection to real peer admission ----------------------------------

@pytest.mark.parametrize("age,binding,facts,capability,resume_ready,reason", [
    (1, True, None, True, True, None),
    (1, False, None, True, True, None),
    (9, True, None, True, True, None),
    (9, False, None, True, True, None),
    (30, False, {"lane": "live"}, True, True, None),
    (48, True, None, True, True, "peer_runtime_stale"),
    (None, True, None, True, True, "peer_runtime_stale"),
    (1, True, None, False, True, "peer_agent_activation_unavailable"),
    (1, True, None, True, False, "peer_lane_not_resume_ready"),
    # Unknown liveness fails closed: no activation on a peer this machine cannot vouch for.
    (1, True, {"lane": "foreign_host"}, True, True, "peer_runtime_not_active"),
    (1, False, {"lane": "unreadable"}, True, True, "peer_runtime_not_active"),
    (1, True, {"lane": "absent", "lease": {"status": "active", "expired": True}}, True, True, "peer_runtime_not_active"),
])
def test_real_projection_to_peer_admission(monkeypatch, age, binding, facts, capability,
                                           resume_ready, reason):
    packet, todo = build_projection(monkeypatch, age=age, binding=binding, facts=facts)
    todo.update(resume_when="todo_done:todo_dependency", resume_ready=resume_ready)
    summary = {"items": [todo]}
    contract, lane = apply_task_orchestration_contract(
        fallback_work_lane_contract={"lane": "advancement_task"},
        goal_boundary={"peer_task_coordination": {
            "enabled": True, "coordinator_agent_id": "coordinator"}},
        agent_identity={"agent_id": "coordinator", "registered_agents": ["coordinator", "peer"]},
        agent_todo_summary=summary, raw_agent_todo_summary=summary,
        available_capabilities=["peer_agent_activation"] if capability else [],
        agent_management_projection=packet,
    )
    assert contract is not None
    assert contract["execution_state"] == ("blocked" if reason else "ready")
    if reason:
        assert contract["eligible_peer_lanes"] == []
        assert contract["blocked_peer_lanes"][0]["reason_codes"] == [reason]
    else:
        assert contract["eligible_peer_lanes"][0]["todo_id"] == "todo_peer"
        assert lane["lane"] == "task_orchestration"
