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

from test_local_delegation import HOST, brief, demo, service  # noqa: F401
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
    if ttl is None:
        return lease
    renewed = runner._cli(binding, "task-lease", "renew", "--goal-id", runner.goal_id,
        "--todo-id", binding["todo_id"], "--owner", binding["agent_id"],
        "--idempotency-key", lease["idempotency_key"], "--expected-version", str(lease["version"]),
        "--ttl-seconds", str(ttl))
    return renewed["lease"]


def inspect(runner):
    return runner._cli(runner.binding("analysis"), "task-lease", "inspect", "--goal-id", runner.goal_id,
                       "--todo-id", "todo_analyst-initial")


@pytest.fixture(params=["file", "sqlite"])
def completion_service(tmp_path, request, monkeypatch):
    """Pin a real slow final acceptance command before preparing authority."""
    validator = tmp_path / "completion-validator.py"
    validator.write_text('''import sys, time, runpy
from datetime import datetime
from pathlib import Path
root = Path(sys.argv[1])
counter = root / 'validator-calls'
calls = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(calls))
if calls == 2:
    deadline = datetime.fromisoformat((root / 'completion-prior-expiry').read_text())
    time.sleep(max(0, deadline.timestamp() - time.time()) + 0.2)
    (root / 'completion-validation-ended').touch()
actual = root / 'project/validation/acceptance.py'
sys.path.insert(0, str(actual.parent))
sys.argv[0] = str(actual)
runpy.run_path(str(actual), run_name='__main__')
''')
    write = demo.write

    def configure(path, value):
        if path.name == "bootstrap.json":
            for criterion in value["document"]["criteria"]:
                criterion["validation_timeout_seconds"] = 1
                if criterion["id"] == "analyst-initial":
                    # 21 + four one-second criteria stays within the public
                    # 25-second completion budget, including a 20s deadline.
                    criterion["validation_timeout_seconds"] = 21
                    criterion["validation_argv"][1] = str(validator)
        return write(path, value)

    monkeypatch.setattr(demo, "write", configure)
    return service.__wrapped__(tmp_path, request, monkeypatch)


@pytest.mark.parametrize("lost_reply", [None, "renewal", "completion"])
def test_completion_renews_before_validation_and_replays_each_intent(completion_service, monkeypatch, lost_reply):
    root, runner = completion_service
    # This case shortens the lease at the completion boundary below. An
    # unrelated short Host deadline can cancel execution before that boundary
    # under load; the running-Host cases separately exercise that deadline.
    original = prepare_lease(root, runner, monkeypatch, ttl=None)
    complete, cli = runner._complete_delegated_todo, runner._cli
    shortened = None
    renewal_calls, completion_calls = [], []
    dropped = False

    def enter_completion(row, binding):
        nonlocal shortened
        if shortened is None:
            current = inspect(runner)["lease"]
            # Fix the phase boundary, independent of whether the Host happened
            # to cross its earlier renewal timer. Use the real canonical API.
            shortened = cli(binding, "task-lease", "renew", "--goal-id", runner.goal_id,
                "--todo-id", binding["todo_id"], "--owner", binding["agent_id"],
                "--idempotency-key", current["idempotency_key"],
                "--expected-version", str(current["version"]), "--ttl-seconds", "20")["lease"]
            # Cross the actual pre-renewal deadline, not an assumed amount of
            # CLI startup time. Allow cold claim/renew commands to reach the
            # boundary; the independent validator still outlives that lease.
            (root / "completion-prior-expiry").write_text(shortened["expires_at"])
        return complete(row, binding)

    def observe_reply(binding, *args, **kwargs):
        nonlocal dropped
        result = cli(binding, *args, **kwargs)
        phase = None
        if args[:2] == ("task-lease", "renew"):
            renewal_calls.append((args, result))
            phase = "renewal"
        elif args[:2] == ("todo", "complete"):
            completion_calls.append((args, result))
            phase = "completion"
        if phase == lost_reply and phase is not None and not dropped:
            dropped = True
            raise ValueError("fixture dropped the committed " + phase + " reply")
        return result

    monkeypatch.setattr(runner, "_complete_delegated_todo", enter_completion)
    monkeypatch.setattr(runner, "_cli", observe_reply)
    runner.execute("lease-lifetime")
    if lost_reply:
        uncertain = _read(runner.path("lease-lifetime"))
        assert uncertain["status"] == "turn_returned", uncertain
        assert "fixture dropped" in uncertain["error"]
        assert uncertain["completion_lease_renewal_version"] == shortened["version"]
        assert ("completion_lease_version" in uncertain) == (lost_reply == "completion")
        runner.execute("lease-lifetime")
    row = _read(runner.path("lease-lifetime"))
    assert row["status"] == "accepted", row
    assert row["turn_result"]["result_kind"] == "validated_progress"
    assert (root / "completion-validation-ended").exists()
    assert time.time() > datetime.fromisoformat(shortened["expires_at"].replace("Z", "+00:00")).timestamp()
    assert (root / "analyst/initial/host-invocations").read_text() == "1"
    final = inspect(runner)["lease"]
    assert final["status"] == "released"
    assert final["lease_epoch"] == original["lease_epoch"]
    assert final["idempotency_key"] == original["idempotency_key"]
    assert final["version"] == shortened["version"] + 1
    assert len(renewal_calls) == (2 if lost_reply == "renewal" else 1)
    assert len(completion_calls) == (2 if lost_reply == "completion" else 1)
    for calls in (renewal_calls, completion_calls):
        assert all(args == calls[0][0] for args, _ in calls)
        assert all(result["provider_revision"] == calls[0][1]["provider_revision"] for _, result in calls)


@pytest.mark.parametrize("authority_loss", ["expiry", "replacement"])
def test_completion_renewal_receipt_cannot_revive_lost_execution(service, monkeypatch, authority_loss):
    root, runner = service
    prepare_lease(root, runner, monkeypatch)
    cli = runner._cli
    dropped = False
    completions = []

    def lose_renewal_reply(binding, *args, **kwargs):
        nonlocal dropped
        if args[:2] == ("todo", "complete"):
            completions.append(args)
        result = cli(binding, *args, **kwargs)
        if args[:2] == ("task-lease", "renew") and not dropped:
            dropped = True
            raise ValueError("fixture lost renewal response before terminal intent")
        return result

    monkeypatch.setattr(runner, "_cli", lose_renewal_reply)
    runner.execute("lease-lifetime")
    row = _read(runner.path("lease-lifetime"))
    assert row["status"] == "turn_returned", row
    assert "completion_lease_renewal_version" in row
    assert "completion_lease_version" not in row
    current = inspect(runner)["lease"]
    binding = runner.binding("analysis")
    args = ("--goal-id", runner.goal_id, "--todo-id", binding["todo_id"],
            "--owner", binding["agent_id"], "--idempotency-key", current["idempotency_key"],
            "--expected-version", str(current["version"]))
    if authority_loss == "expiry":
        expired = cli(binding, "task-lease", "renew", *args, "--ttl-seconds", "1")["lease"]
        time.sleep(max(0, datetime.fromisoformat(expired["expires_at"].replace("Z", "+00:00")).timestamp()
                       - time.time()) + 0.1)
    else:
        assert cli(binding, "task-lease", "release", *args)["ok"] is True
        replacement = cli(binding, "task-lease", "acquire", "--goal-id", runner.goal_id,
            "--todo-id", binding["todo_id"], "--owner", binding["agent_id"],
            "--idempotency-key", "replacement", "--expected-version", str(current["version"]))
        assert replacement["lease"]["lease_epoch"] > current["lease_epoch"]
    runner.execute("lease-lifetime")
    rejected = _read(runner.path("lease-lifetime"))
    assert rejected["status"] != "accepted", rejected
    assert "completion_lease_version" not in rejected
    assert not completions
    assert (root / "analyst/initial/host-invocations").read_text() == "1"
    snapshot = read_canonical_todos_if_promoted(runtime_root=runner.root, goal_id=runner.goal_id)
    todo = next(item for item in snapshot["todos"] if item["todo_id"] == binding["todo_id"])
    assert todo["done"] is False


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
        binding = runner.binding("analysis")
        # The live supervisor may renew between inspection and revocation.
        # Retry only that CAS race, never a replacement execution or rejection.
        for _ in range(5):
            current = inspect(runner)["lease"]
            assert current["lease_epoch"] == original["lease_epoch"]
            assert current["idempotency_key"] == original["idempotency_key"]
            try:
                runner._cli(binding, "task-lease", "release", "--goal-id", runner.goal_id,
                            "--todo-id", "todo_analyst-initial", "--owner", "analyst",
                            "--idempotency-key", original["idempotency_key"],
                            "--expected-version", str(current["version"]))
                break
            except ValueError as exc:
                if str(exc) != "canonical task lease release rejected: version_mismatch":
                    raise
        else:
            pytest.fail("could not revoke the original execution during concurrent renewal")
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
