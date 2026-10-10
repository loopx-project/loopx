"""Provider migration separates an observed Host exit from lease settlement.

Real disposable providers, CLI processes and the existing supervised POSIX Host;
no active Goal, model provider or automatic Host discovery is exercised.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from test_cold_source_import_host import HOST, SUPERVISOR
from test_local_provider_settlement_journey import REPO
from test_quota_authority_settlement_journey import _source
import test_quota_settlement_cli as settlement


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group stop proof")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_provider_cutover_preserves_stopped_host_lineage_and_requires_fresh_execution(
    tmp_path, monkeypatch, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry, _, _ = _source(
        tmp_path, provider=provider, handoff_mode="hard_lease",
        extra=f"claimed_by={settlement.AGENT_ID}",
    )
    goal, actor, todo = settlement.GOAL_ID, settlement.AGENT_ID, settlement.TODO_ID
    env = {**os.environ, "PYTHONPATH": str(REPO)}
    base = [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
            "--runtime-root", str(runtime), "--format", "json"]
    selected = ["--goal-id", goal, "--todo-id", todo]
    target = "sqlite" if provider == "file" else "file"
    owners = []

    def cli(*args, expected=0):
        result = subprocess.run([*base, *map(str, args)], cwd=project, env=env,
                                capture_output=True, text=True, timeout=90)
        assert result.returncode == expected, result.stdout + result.stderr
        return json.loads(result.stdout)

    def plan(destination, name):
        path = tmp_path / f"{name}.json"
        result = cli("authority-archive", "plan-migration", "--goal-id", goal,
                     "--provider", destination, "--plan", path)
        return ["authority-archive", "migrate", "--goal-id", goal, "--plan", path,
                "--plan-sha256", result["plan_sha256"], "--execute"]

    def lease(action, key, *args):
        return cli("task-lease", action, *selected, "--owner", actor,
                   "--idempotency-key", key, *args)

    def launch(current, key, name):
        marker, record = tmp_path / f"{name}.pid", tmp_path / f"{name}.json"
        context = {"lease": current, "ttl_seconds": 120,
                   "read_argv": [*base, "task-lease", "inspect", *selected],
                   "renew_argv": [*base, "task-lease", "renew", *selected,
                                  "--owner", actor, "--idempotency-key", key]}
        child = subprocess.Popen([sys.executable, "-c", SUPERVISOR,
            json.dumps([sys.executable, "-c", HOST, str(marker)]), str(project), json.dumps(context)],
            cwd=project, env={**env, "LOOPX_HOST_PROCESS_RECORD": str(record)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        owners.append(child)
        return child, marker, record

    def live(child, marker):
        deadline = time.monotonic() + 60
        while not marker.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker.exists(), child.communicate(timeout=10)
        pid = int(marker.read_text())
        os.kill(pid, 0)
        return pid

    def stop(child, pid, record):
        child.terminate()
        out, err = child.communicate(timeout=30)
        assert child.returncode == 0 and json.loads(out) == {"operator_stopped": True}, (out, err)
        from loopx.control_plane.turn_driver.host_process_transport import execution_host_drain
        assert execution_host_drain(record) == "drained"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)

    try:
        old_plan = plan(target, "before-host")
        original = lease("acquire", "before-cutover", "--ttl-seconds", 120)["lease"]
        child, marker, record = launch(original, "before-cutover", "original-host")
        pid = live(child, marker)
        rejected = cli(*old_plan, expected=1)
        assert rejected["authority_changed"] is False
        os.kill(pid, 0)  # Refused migration never stopped the running Host.
        blocked_plan = tmp_path / "active-host.json"
        blocked_args = ("authority-archive", "plan-migration", "--goal-id", goal,
                        "--provider", target, "--plan", blocked_plan)
        blocked = cli(*blocked_args, expected=1)
        assert "settled task leases" in blocked["reason"] and not blocked_plan.exists()
        stop(child, pid, record)
        assert cli("task-lease", "inspect", *selected)["lease"] == original
        # Actual process exit still does not settle its active grant.
        assert "settled task leases" in cli(*blocked_args, expected=1)["reason"]
        assert not blocked_plan.exists()
        assert cli(*old_plan, expected=1)["authority_changed"] is False
        settled = lease("release", "before-cutover", "--expected-version", original["version"])["lease"]
        assert cli(*old_plan, expected=1)["authority_changed"] is False
        migration = plan(target, "after-settlement")
        assert cli(*migration)["selected_provider"] == target

        stale, stale_marker, stale_record = launch(original, "before-cutover", "retired-proof")
        out, err = stale.communicate(timeout=60)
        assert stale.returncode == 0, (out, err)
        observed = json.loads(out)
        assert observed["outcome"] == "cancelled"
        assert observed["lease_failure"] == {"reason": "lease_inactive", "boundary": "initial_proof"}
        assert not stale_marker.exists() and json.loads(stale_record.read_text())["phase"] == "not_launched"

        fresh = lease("acquire", "after-cutover", "--expected-version", settled["version"],
                      "--ttl-seconds", 120)["lease"]
        assert fresh["version"] == settled["version"] + 1
        assert fresh["lease_epoch"] == settled["lease_epoch"] + 1
        restarted, marker, record = launch(fresh, "after-cutover", "fresh-host")
        pid = live(restarted, marker)
        assert cli(*migration)["status"] == "already_applied"
        assert cli("task-lease", "inspect", *selected)["lease"] == fresh
        os.kill(pid, 0)  # Historical migration replay cannot revoke fresh work.
        stop(restarted, pid, record)
        retained = lease("release", "after-cutover", "--expected-version", fresh["version"])["lease"]
        assert cli(*plan(provider, "return-with-new-lineage"))["selected_provider"] == provider
        assert cli("task-lease", "inspect", *selected)["lease"] == retained
        assert cli(*migration, expected=1)["authority_changed"] is False
        assert cli("task-lease", "inspect", *selected)["lease"] == retained
    finally:
        for child in owners:
            if child.poll() is None:
                child.terminate()
                child.communicate(timeout=30)
