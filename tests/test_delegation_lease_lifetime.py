"""Actual Delegations -> CLI -> Turn -> managed Host with isolated providers."""
from __future__ import annotations

# Pytest discovers the imported service fixture; test parameters intentionally shadow it.
# ruff: noqa: F811

import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest

from test_local_delegation import HOST, brief, service  # noqa: F401
from loopx.control_plane.collaboration.inbox import _read
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from tests.control_plane.host_process_fixture import COUNTER_PROCESS_SOURCE


def prepare_lease(root, runner, monkeypatch, *, ttl=20):
    monkeypatch.setattr(runner, "_spawn", lambda _: None)
    runner.start("analysis", "lease-lifetime", brief())
    row = _read(runner.path("lease-lifetime"))
    # Only a brand-new disposable fixture. Do not migrate an active Goal or
    # weaken the public quiescent mode-change contract to set up a test.
    program = '''
import {openLocalAuthorityStore} from "./loopx/control_plane/coordination/local_authority_provider.ts";
const [root,goal] = process.argv.slice(1);
const store = await openLocalAuthorityStore(root,goal);
const state = await store.loadAuthority();
const committed = await store.commitAuthority({expected_provider_revision:state.provider_revision,
 operation_id:"fixture-lease",events:[],receipts:[],
 next_projection:{...state.head,handoff_mode:"hard_lease"}});
if(committed.status!=="applied") throw new Error(JSON.stringify(committed));
'''
    prepared = subprocess.run(["node", "--no-warnings", "--experimental-sqlite", "--experimental-strip-types",
                    "--input-type=module", "-e", program, str(runner.root), runner.goal_id],
                   capture_output=True, text=True, timeout=30)
    assert prepared.returncode == 0, prepared.stderr
    binding = runner.binding("analysis")
    runner._acquire_delegation_lease(runner.path("lease-lifetime"), row, binding)
    lease = row["task_lease"]["lease"]
    renewed = runner._cli(binding, "task-lease", "renew", "--goal-id", runner.goal_id,
        "--todo-id", binding["todo_id"], "--owner", binding["agent_id"],
        "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]),
        "--ttl-seconds", str(ttl))
    return renewed["lease"]


def inspect(runner):
    return runner._cli(runner.binding("analysis"), "task-lease", "inspect", "--goal-id", runner.goal_id,
                       "--todo-id", "todo_analyst-initial")


def await_started(root, future, runner):
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and not (root / "host-started").exists():
        if future.done():
            future.result()
            pytest.fail(str(_read(runner.path("lease-lifetime"))))
        time.sleep(0.1)
    assert (root / "host-started").exists()


@pytest.mark.parametrize("lost_completion_reply", [False, True])
def test_real_delegation_renews_original_execution_then_completes_and_settles(service, monkeypatch, lost_completion_reply):
    root, runner = service
    original = prepare_lease(root, runner, monkeypatch)
    if lost_completion_reply:
        cli = runner._cli
        dropped = False
        def lose_reply(binding, *args, **kwargs):
            nonlocal dropped
            result = cli(binding, *args, **kwargs)
            if args[:2] == ("todo", "complete") and not dropped:
                dropped = True
                raise ValueError("fixture dropped a committed completion reply")
            return result
        monkeypatch.setattr(runner, "_cli", lose_reply)
    (root / "hold").touch()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.execute, "lease-lifetime")
        await_started(root, future, runner)
        # Deliberately cross the original deadline, including CLI/Turn work.
        delay = max(0, datetime.fromisoformat(original["expires_at"].replace("Z", "+00:00")).timestamp()
                    - time.time() + 2)
        time.sleep(delay)
        current = inspect(runner)
        assert current["active"] is True
        assert current["lease"]["version"] > original["version"]
        assert current["lease"]["lease_epoch"] == original["lease_epoch"]
        assert current["lease"]["idempotency_key"] == original["idempotency_key"]
        (root / "release").touch()
        future.result(timeout=60)
    if lost_completion_reply:
        uncertain = _read(runner.path("lease-lifetime"))
        assert uncertain["status"] != "accepted"
        assert "dropped" in uncertain["error"]
        assert uncertain["completion_lease_version"] > original["version"]
        runner.execute("lease-lifetime")
    row = _read(runner.path("lease-lifetime"))
    assert row["status"] == "accepted", row
    assert row["turn_result"]["status"] == "committed"
    assert row["turn_result"]["result_kind"] == "validated_progress"
    snapshot = read_canonical_todos_if_promoted(runtime_root=runner.root, goal_id=runner.goal_id, include_leases=True)
    todo = next(item for item in snapshot["todos"] if item["todo_id"] == "todo_analyst-initial")
    assert todo["done"] is True
    assert inspect(runner)["lease"]["status"] == "released"
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
    assert not (root / "analyst" / "initial" / "DELEGATION.json").exists()


@pytest.mark.skipif(os.name == "nt", reason="POSIX nested managed process-group qualification")
@pytest.mark.parametrize("reclaim", [False, True])
def test_real_revocation_or_new_execution_stops_nested_host_without_acceptance(service, monkeypatch, reclaim):
    root, runner = service
    original = prepare_lease(root, runner, monkeypatch, ttl=20)
    marker, pid = root / "writes", root / "descendant-pid"
    child = [sys.executable, "-c", COUNTER_PROCESS_SOURCE, str(marker), str(pid), "0.02"]
    (root / "fixture-host.py").write_text(HOST.replace("counter = workspace / 'host-invocations'",
        f"import subprocess\nsubprocess.Popen({child!r})\ncounter = workspace / 'host-invocations'"))
    (root / "hold").touch()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(runner.execute, "lease-lifetime")
        await_started(root, future, runner)
        current = inspect(runner)["lease"]
        binding = runner.binding("analysis")
        runner._cli(binding, "task-lease", "release", "--goal-id", runner.goal_id,
                    "--todo-id", "todo_analyst-initial", "--owner", "analyst",
                    "--idempotency-key", original["idempotency_key"], "--expected-version", str(current["version"]))
        if reclaim:
            acquired = runner._cli(binding, "task-lease", "acquire", "--goal-id", runner.goal_id,
                "--todo-id", "todo_analyst-initial", "--owner", "analyst", "--idempotency-key", "new-execution",
                "--expected-version", str(current["version"]))
            assert acquired["lease"]["lease_epoch"] > original["lease_epoch"]
        future.result(timeout=40)
    row = _read(runner.path("lease-lifetime"))
    assert row["status"] != "accepted"
    assert "lease supervision stopped" in row["error"], row
    before = marker.read_bytes()
    time.sleep(0.2)
    assert marker.read_bytes() == before, "nested Host descendants must stop before the worker returns"
    snapshot = read_canonical_todos_if_promoted(runtime_root=runner.root, goal_id=runner.goal_id)
    todo = next(item for item in snapshot["todos"] if item["todo_id"] == "todo_analyst-initial")
    assert todo["done"] is False
    assert not (root / "analyst" / "initial" / "DELEGATION.json").exists()
    # Retry the same operation: it may reconcile receipts, but never reacquire
    # the old execution or restart model work under the new epoch.
    runner.execute("lease-lifetime")
    assert _read(runner.path("lease-lifetime"))["status"] != "accepted"
    assert (root / "analyst" / "initial" / "host-invocations").read_text() == "1"
