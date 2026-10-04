"""Stop ordering against native completion of a validated, unsettled Turn."""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from contextlib import contextmanager
from threading import Event, get_ident

import pytest

from test_local_delegation import brief, demo, service as service
from loopx import collaboration_mcp as delegation
from loopx.control_plane.collaboration import delegation_stop_lease as stop_lease
from loopx.control_plane.collaboration.inbox import _read
from loopx.control_plane.collaboration.peers import returns
from loopx.file_lock import exclusive_file_lock, lock_holder_host_label


def test_missing_cutover_marker_does_not_erase_an_unannotated_lease(service, monkeypatch):
    from test_delegation_lease_lifetime import inspect, prepare_lease
    from loopx.control_plane.collaboration.inbox import _write
    from loopx.control_plane.coordination.legacy_writer_fence import legacy_coordination_writer_fence_path

    root, runner = service
    operation = "authority-evidence-loss"
    with monkeypatch.context() as setup:
        original_lease = prepare_lease(root, runner, setup, ttl=None, operation_id=operation)
    path = runner.path(operation)
    row = _read(path)
    row.pop("task_lease")  # Acquisition committed before its annotation survived.
    _write(path, row)
    fence = legacy_coordination_writer_fence_path(runtime_root=runner.root, goal_id=runner.goal_id)
    original_fence = fence.read_bytes()
    fence.unlink()
    try:
        receipt = runner.stop(operation, execute=True)
        assert receipt["phase"] == "acknowledged", receipt
        assert receipt["stop"]["lease"]["state"] == "obligation_unproven", receipt
        assert not fence.exists(), "stop must not recreate authority evidence"
    finally:
        fence.write_bytes(original_fence)
    held = inspect(runner)
    assert held["active"] and held["lease"]["idempotency_key"] == original_lease["idempotency_key"]
    recovered = runner.stop(operation, execute=True)
    assert recovered["phase"] == "settled", recovered
    assert recovered["stop"]["stop_id"] == receipt["stop"]["stop_id"]
    assert recovered["stop"]["lease"]["state"] == "released"
    assert inspect(runner)["lease"]["status"] == "released"


def test_never_promoted_authority_needs_no_delegation_lease(service):
    from types import SimpleNamespace

    root, runner = service
    unused_runtime = root / "never-promoted"
    observer = SimpleNamespace(root=unused_runtime, goal_id=runner.goal_id)
    row = {"identity": {"binding": {"todo_id": "todo_analyst-initial"}}}
    assert stop_lease.obligation(observer, row) is None
    assert not unused_runtime.exists(), "absence inspection must not create an authority"


def recoverable_boundary(service, monkeypatch):
    root, runner = service
    # The Host adopts and supplies an artifact, but only delegation publishes
    # the result. Keep that effect independently observable in this fixture.
    host = root / "fixture-host.py"
    host.write_text("\n".join(line for line in host.read_text().splitlines()
                              if not line.startswith("    return_result(")))
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "analysis-recovery", brief())
    module_command = delegation._python_module_command
    # Exit the real native CLI after its validated checkpoint is persisted,
    # before any settlement callback runs. Host output and task validation are
    # real; completion, settlement and authority are never replaced.
    interruption = """
import os, runpy
from loopx.control_plane.turn_driver import executor
persist = executor._write_journal
def checkpoint(path, snapshot, **kwargs):
    persist(path, snapshot, **kwargs)
    if snapshot.get('completed_phases') == ['host_execute', 'typed_result', 'validation']:
        os._exit(86)
executor._write_journal = checkpoint
runpy.run_module('loopx.cli', run_name='__main__')
"""
    with monkeypatch.context() as setup:
        setup.setattr(delegation, "_python_module_command", lambda module:
                      [sys.executable, "-c", interruption] if module == "loopx.cli"
                      else module_command(module))
        runner.execute("analysis-recovery")
    path = runner.path("analysis-recovery")
    row = _read(path)
    assert row["status"] == "running" and row.get("error")
    # The interrupted CLI did not return an accepted result. Record that
    # observation through the owner, then use public same-operation resume;
    # the typed recovery transition accepts only a rejected observation.
    runner._observe(path, row, "rejected")
    assert runner.resume("analysis-recovery")["status"] == "turn_returned"
    row = _read(path)
    journal = runner._validated_turn_journal(row, runner.binding("analysis"))
    assert journal["task_validation"]["ok"] is True
    assert journal["host_result"]["result_kind"] == "validated_progress"
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert returns(runner.root, runner.goal_id, "lead")["items"] == []
    # The test process owns the operation; it must never signal its own group.
    monkeypatch.setattr(runner, "_signal_worker", lambda *_: None)
    return root, runner


def test_stop_wins_before_recovered_completion(service, monkeypatch):
    root, runner = recoverable_boundary(service, monkeypatch)
    validated = runner._validated_turn_journal
    receipts = []

    def stop_after_validation(row, binding):
        journal = validated(row, binding)
        assert journal is not None
        receipts.append(runner.stop("analysis-recovery", execute=True))
        return journal

    monkeypatch.setattr(runner, "_validated_turn_journal", stop_after_validation)
    runner.execute("analysis-recovery")
    assert len(receipts) == 1 and receipts[0]["phase"] == "requested"
    receipt = runner.stop("analysis-recovery", execute=True)
    assert receipt["phase"] == "settled" and receipt["status"] == "stopped"
    assert not demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert returns(runner.root, runner.goal_id, "lead")["items"] == []
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    with pytest.raises(ValueError, match="start a new operation id"):
        runner.resume("analysis-recovery")


def test_recovered_completion_wins_over_a_concurrent_stop(service, monkeypatch):
    root, runner = recoverable_boundary(service, monkeypatch)
    complete = runner._complete_delegated_todo
    main_thread = get_ident()
    attempted = Event()
    dispatch = runner._dispatch_lock(runner.path("analysis-recovery"))
    futures = []

    @contextmanager
    def observed_lock(target, **kwargs):
        if target == dispatch and get_ident() != main_thread:
            attempted.set()
            kwargs["timeout_seconds"] = 30
        with exclusive_file_lock(target, **kwargs) as held:
            yield held

    monkeypatch.setattr(delegation, "exclusive_file_lock", observed_lock)
    with ThreadPoolExecutor(max_workers=1) as pool:
        def concurrent_stop(row, binding):
            future = pool.submit(runner.stop, "analysis-recovery", execute=True)
            futures.append(future)
            assert attempted.wait(10)
            # Give stop the chance to persist if completion has no fence.
            # Do not assert the lock implementation: run native completion and
            # judge the final canonical state and public receipt instead.
            try:
                future.result(timeout=0.3)
            except FutureTimeout:
                pass
            complete(row, binding)

        monkeypatch.setattr(runner, "_complete_delegated_todo", concurrent_stop)
        runner.execute("analysis-recovery")
        assert len(futures) == 1
        receipt = futures[0].result(timeout=30)
    receipt = runner.stop("analysis-recovery", execute=True)
    assert demo.canonical_tasks(root)["todo_analyst-initial"]["done"]
    assert receipt["phase"] == "noop" and receipt["status"] == "accepted", receipt
    assert runner.read("analysis-recovery")["status"] == "accepted"
    assert len(returns(runner.root, runner.goal_id, "lead")["items"]) == 1
    assert not runner._stop_path(runner.path("analysis-recovery")).exists()
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"


def canonical_lease_at_the_native_edge(service, monkeypatch, operation_id, *,
                                        owner="analyst", key_suffix="", version=4, active=True):
    """Build the acquisition-before-annotation window at the native lease edge.

    `_acquire_delegation_lease` claims natively and only then saves the record
    that annotates the lease. A stop that wins in between, followed by process
    loss after the durable ACK, leaves an active canonical lease the operation
    record never names. This reconstructs exactly that disk state and hands the
    new service instance a native boundary that really holds the lease, so the
    Python reconciliation runs for real instead of being stubbed out.
    """
    from loopx.control_plane.collaboration.inbox import _write as write_inbox

    root, runner = service
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", operation_id, brief())
    path = runner.path(operation_id)
    row = _read(path)
    assert "task_lease" not in row
    request_id = row["identity"]["request_id"]
    lease_key = str(row.get("turn_instance_id") or "delegation-" + request_id[:32])
    stopped = {**row, "status": "stopped"}
    runner._fenced_write(path, stopped)
    write_inbox(runner._stop_path(path), {
        **runner._new_stop_record(stopped, requested_by=runner.agent_id, worker=None),
        "phase": "acknowledged",
        # The ACK was durable and the process was lost before any release result.
        "ack": {"pid": os.getpid(), "host": lock_holder_host_label(), "at": time.time(),
                "source": "requester", "observed_status": "stopped", "turn_key": None},
    })
    monkeypatch.setattr(stop_lease, "local_authority_is_promoted", lambda **kwargs: True)
    monkeypatch.setattr(stop_lease, "inspect_task_lease", lambda **kwargs: {
        "ok": True, "action": "inspect", "active": active, "legacy_fallback_used": False,
        "lease": {"owner": owner, "idempotency_key": lease_key + key_suffix,
                  "status": "active", "version": version},
    })
    fresh = delegation.Delegations(runner.root, runner.registry, runner.goal_id,
                                   runner.agent_id, runner.config)
    recovered = _read(path)
    assert recovered["status"] == "stopped"
    assert not isinstance(recovered.get("task_lease"), dict), recovered.get("task_lease")
    return root, fresh, lease_key


def test_a_lease_the_record_never_annotated_still_blocks_settlement(service, monkeypatch):
    """An unproven release of a live lease is not a settlement.

    The member really holds the hard lease its own key acquired under, and the
    release surface is unavailable. `settled` would tell the owner the member is
    stopped while the Todo stays locked to the lease TTL, with resume already
    refused. The receipt must stay open instead.
    """
    root, runner, lease_key = canonical_lease_at_the_native_edge(
        service, monkeypatch, "analysis-lease-window")

    def unavailable(**kwargs):
        raise RuntimeError("authority unavailable")

    monkeypatch.setattr(stop_lease, "release_task_lease", unavailable)
    receipt = runner.stop("analysis-lease-window", execute=True)
    assert receipt["phase"] == "acknowledged", receipt
    assert receipt["stop"]["reason"] == "required_lease_release_unproven"
    assert receipt["stop"]["lease"]["state"] == "release_unproven"
    with pytest.raises(ValueError, match="start a new operation id"):
        runner.resume("analysis-lease-window")


def test_a_recovered_lease_obligation_settles_only_after_its_exact_release(service, monkeypatch):
    """The unannotated obligation is released by its own identity, then settles.

    Discovery is not a substitute for the release: the receipt reports released
    only once the exact owner and execution key this operation acquired under
    were released through the existing authority path.
    """
    root, runner, lease_key = canonical_lease_at_the_native_edge(
        service, monkeypatch, "analysis-lease-recover")
    releases = []
    monkeypatch.setattr(stop_lease, "release_task_lease",
                        lambda **kwargs: releases.append(kwargs) or {"released": True})

    receipt = runner.stop("analysis-lease-recover", execute=True)
    assert receipt["phase"] == "settled", receipt
    assert receipt["stop"]["lease"]["state"] == receipt["stop"]["settled"]["lease"] == "released"
    assert releases == [{"runtime_root": runner.root, "goal_id": runner.goal_id,
                         "todo_id": "todo_analyst-initial", "owner": "analyst",
                         "idempotency_key": lease_key,
                         "expected_version": 4, "registry_path": runner.registry}]
    assert runner.stop("analysis-lease-recover", execute=True) == receipt
    assert len(releases) == 1


def test_a_foreign_lease_generation_is_not_this_stops_obligation(service, monkeypatch):
    """A lease under another execution key is never released by this stop.

    A recreated operation on the same Todo acquires a new execution key, and an
    earlier generation still holding the old one is not this operation's to
    retire. Releasing it would end work this stop never owned; leaving it is a
    release this receipt never owed.
    """
    root, runner, lease_key = canonical_lease_at_the_native_edge(
        service, monkeypatch, "analysis-lease-foreign", key_suffix="-older")
    releases = []
    monkeypatch.setattr(stop_lease, "release_task_lease",
                        lambda **kwargs: releases.append(kwargs) or {"released": True})

    receipt = runner.stop("analysis-lease-foreign", execute=True)
    assert releases == [], "a foreign lease generation was released"
    assert receipt["phase"] == "settled", receipt
    assert receipt["stop"]["settled"]["lease"] == "not_owed"


def test_an_unreadable_lease_obligation_keeps_the_stop_open(service, monkeypatch):
    """A failure to even read the obligation is not a release.

    Reconciliation can fail before it names any lease. Reporting that as a
    settled stop would trade a wrong terminal receipt for a crash, so the stop
    stays open and the next read retries the authority instead.
    """
    root, runner, lease_key = canonical_lease_at_the_native_edge(
        service, monkeypatch, "analysis-lease-unreadable")

    def unreadable(**kwargs):
        raise RuntimeError("native authority store unavailable")

    monkeypatch.setattr(stop_lease, "inspect_task_lease", unreadable)
    releases = []
    monkeypatch.setattr(stop_lease, "release_task_lease",
                        lambda **kwargs: releases.append(kwargs) or {"released": True})

    receipt = runner.stop("analysis-lease-unreadable", execute=True)
    assert receipt["phase"] == "acknowledged", receipt
    assert receipt["stop"]["reason"] == "lease_obligation_unproven"
    assert releases == [], "an unnamed obligation must not be released"
    # The reason survives on the receipt instead of becoming a crash.
    assert receipt["stop"]["lease"]["state"] == "obligation_unproven"
    assert "unreadable" in receipt["stop"]["lease"]["error"]
