"""Cold-import stop ordering with native leases and real owned Host processes.

Disposable unpromoted sources only. This qualifies the operator-led POSIX stop
and fresh canonical-lease reactivation journey, not automatic Host discovery,
outbox disposition, model execution or whole-Goal restore.
"""
from __future__ import annotations

from datetime import datetime
import json
import os
import subprocess
import sys
import time

import pytest

from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from test_cold_source_import_cli import workspace


HOST = """import os,sys,time
from pathlib import Path
Path(sys.argv[1]).write_text(str(os.getpid()))
while True: time.sleep(.05)
"""

# Use the receiver's production transport and typed supervisor, including its
# native CLI proof. TERM unwinds the control pipe before the owner returns.
SUPERVISOR = """import json,signal,sys
from pathlib import Path
from loopx.control_plane.turn_driver.host_process_transport import run_host_process
def stop(*_): raise KeyboardInterrupt
signal.signal(signal.SIGTERM,stop)
try:
    result=run_host_process(json.loads(sys.argv[1]),project=Path(sys.argv[2]),
        input_text='',timeout_seconds=None,delegated_lease=json.loads(sys.argv[3]))
    print(json.dumps(result))
except KeyboardInterrupt:
    print(json.dumps({'operator_stopped':True}))
"""


def source_execution(tmp_path, monkeypatch, ttl=120):
    cli, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    # A supported old Markdown source can have explicit hard leases without
    # already possessing canonical or shadow authority.
    state.write_text(state.read_text().replace("handoff_mode: legacy", "handoff_mode: hard_lease"))
    registry = tmp_path / "project/.loopx/registry.json"
    argv = [sys.executable, "-m", "loopx.cli", "--registry", str(registry),
        "--runtime-root", str(runtime), "--format", "json", "task-lease"]
    selected = ["--goal-id", "cold", "--todo-id", "todo_current"]

    def command(action, *args):
        child = subprocess.run([*argv, action, *selected, *map(str, args)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert child.returncode == 0, child.stdout + child.stderr
        return json.loads(child.stdout)

    identity = ["--owner", "agent-a", "--idempotency-key", "old-execution"]
    acquired = command("acquire", *identity, "--ttl-seconds", ttl)
    assert acquired["ok"] and acquired["lease"]["status"] == "active", acquired
    context = {"lease": acquired["lease"], "ttl_seconds": ttl,
        "read_argv": [*argv, "inspect", *selected],
        "renew_argv": [*argv, "renew", *selected, *identity]}

    def release():
        current = command("inspect")
        result = command("release", *identity, "--expected-version", current["lease"]["version"])
        assert result["ok"] and result["lease"]["status"] == "released", result
        return result["lease"]

    def prepare(provider, success=True):
        backup = execute_state_backup_plan(build_state_backup_plan(
            project=registry.parents[1], runtime_root=runtime, output_dir=tmp_path / "settled-backups",
            backup_id=f"source-{time.monotonic_ns()}", include_automations=False,
            include_skills=False, include_registry_projects=False, registry_path=registry))
        assert backup["ok"]
        return cli("prepare-import", "--backup-manifest", backup["manifest_path"],
            "--provider", provider, "--target-handoff-mode", "hard_lease", success=success)["cold_import"]

    def drain(record):
        child = subprocess.run([sys.executable, "-c",
            "import sys;from pathlib import Path;from loopx.control_plane.turn_driver.host_process_transport import execution_host_drain;print(execution_host_drain(Path(sys.argv[1])))",
            str(record)], cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60, check=True)
        return child.stdout.strip()

    def launch(context, marker, record):
        return subprocess.Popen([sys.executable, "-c", SUPERVISOR,
            json.dumps([sys.executable, "-c", HOST, str(marker)]), str(tmp_path), json.dumps(context)],
            cwd=tmp_path, env={**env, "LOOPX_HOST_PROCESS_RECORD": str(record)},
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    return cli, command, release, prepare, drain, launch, context, runtime, receiver


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group stop proof")
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cold_import_settlement_and_fresh_canonical_host_reactivation(tmp_path, monkeypatch, provider):
    cli, command, release, prepare, drain, launch, context, runtime, _ = source_execution(tmp_path, monkeypatch)
    marker, record = tmp_path / "host.pid", tmp_path / "owned-host.json"
    owner = launch(context, marker, record)
    try:
        deadline = time.monotonic() + 60
        while not marker.exists() and owner.poll() is None and time.monotonic() < deadline:
            time.sleep(.05)
        assert marker.exists(), owner.communicate(timeout=10)
        pid = int(marker.read_text())
        os.kill(pid, 0)
        assert drain(record) == "draining"
        refused = prepare(provider, success=False)
        assert refused["reason_code"] == "cold_import_lease_requires_settlement", refused
        assert not list(runtime.rglob("writer-fence.json"))
        # Neither prepare nor its rejection stops somebody else's process.
        os.kill(pid, 0)
        owner.terminate()
        out, err = owner.communicate(timeout=30)
        assert owner.returncode == 0 and json.loads(out) == {"operator_stopped": True}, (out, err)
        assert drain(record) == "drained"
        with pytest.raises(ProcessLookupError):
            os.kill(pid, 0)
        still_active = command("inspect")["lease"]
        assert still_active["status"] == "active"
        assert still_active["idempotency_key"] == "old-execution"
        assert prepare(provider, success=False)["reason_code"] == "cold_import_lease_requires_settlement"
        settled = release()
        prepared = prepare(provider)
        assert prepared["status"] == "prepared" and prepared["source_inventory"]["lease_count"] == 1
        result = cli("apply-import", "--plan-sha256", prepared["plan_sha256"], "--writers-stopped", "--execute")["cold_import"]
        assert result["status"] == "applied" and result["execution_authority_granted"] is False
        # The original lease is preserved as released history under the new
        # provider. Its old grant cannot launch another Host after cutover.
        current = command("inspect")
        assert current["lease"] == settled
        assert current["source_authority"] == f"{provider}_v0"
        restarted_marker, restarted_record = tmp_path / "restarted.pid", tmp_path / "restarted-host.json"
        restarted = launch(context, restarted_marker, restarted_record)
        out, err = restarted.communicate(timeout=60)
        assert restarted.returncode == 0, (out, err)
        observation = json.loads(out)
        assert observation["outcome"] == "cancelled"
        assert observation["lease_failure"] == {"reason": "lease_inactive", "boundary": "initial_proof"}
        assert not restarted_marker.exists()
        # The proof CLI did run and drain; the nested actual Host did not.
        assert json.loads(restarted_record.read_text())["phase"] == "not_launched"
        assert drain(restarted_record) == "drained"
        recovered = cli("recover-import", "--plan-sha256", prepared["plan_sha256"], "--execute")["cold_import"]
        assert recovered["status"] == "replayed" and recovered["cursor"] == "1"
        assert command("inspect")["lease"] == settled
        # Import does not itself activate execution. A new native acquisition
        # must advance the retained token and can launch the real supervised
        # Host under the selected canonical provider.
        fresh_key = "canonical-execution"
        acquired = command("acquire", "--owner", "agent-a", "--idempotency-key", fresh_key,
            "--expected-version", settled["version"], "--ttl-seconds", 120)
        fresh = acquired["lease"]
        assert acquired["source_authority"] == f"{provider}_v0"
        assert fresh["status"] == "active" and fresh["idempotency_key"] == fresh_key
        assert fresh["version"] == settled["version"] + 1
        assert fresh["lease_epoch"] == settled["lease_epoch"] + 1
        fresh_context = {**context, "lease": fresh,
            "renew_argv": [*context["renew_argv"][:-1], fresh_key]}
        fresh_marker, fresh_record = tmp_path / "canonical.pid", tmp_path / "canonical-host.json"
        fresh_owner = launch(fresh_context, fresh_marker, fresh_record)
        try:
            deadline = time.monotonic() + 60
            while not fresh_marker.exists() and fresh_owner.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            assert fresh_marker.exists(), fresh_owner.communicate(timeout=10)
            fresh_pid = int(fresh_marker.read_text())
            os.kill(fresh_pid, 0)
            assert drain(fresh_record) == "draining"
            # Source-free original-operation replay is historical readback; it
            # must not reinstall the released lease over a fresh execution.
            state = tmp_path / "project/.local/goals/cold/ACTIVE_GOAL_STATE.md"
            state.unlink()
            replay = cli("recover-import", "--plan-sha256", prepared["plan_sha256"], "--execute")["cold_import"]
            assert replay["status"] == "replayed" and replay["execution_authority_granted"] is False
            assert command("inspect")["lease"] == fresh
            os.kill(fresh_pid, 0)
            # An old token is not revived just because the Todo now has a
            # current active lease. Reject it before starting a second Host.
            stale_marker, stale_record = tmp_path / "stale.pid", tmp_path / "stale-host.json"
            stale = launch(context, stale_marker, stale_record)
            out, err = stale.communicate(timeout=60)
            assert stale.returncode == 0, (out, err)
            observation = json.loads(out)
            assert observation["outcome"] == "cancelled"
            assert observation["lease_failure"] == {"reason": "execution_identity_changed", "boundary": "initial_proof"}
            assert not stale_marker.exists()
            assert json.loads(stale_record.read_text())["phase"] == "not_launched"
            assert drain(stale_record) == "drained"
            assert command("inspect")["lease"] == fresh
        finally:
            if fresh_owner.poll() is None:
                fresh_owner.terminate()
            out, err = fresh_owner.communicate(timeout=30)
        assert fresh_owner.returncode == 0 and json.loads(out) == {"operator_stopped": True}, (out, err)
        assert drain(fresh_record) == "drained"
        with pytest.raises(ProcessLookupError):
            os.kill(fresh_pid, 0)
        # Host exit remains separate from settlement after migration as well.
        assert command("inspect")["lease"] == fresh
        released = command("release", "--owner", "agent-a", "--idempotency-key", fresh_key,
            "--expected-version", fresh["version"])["lease"]
        assert released["status"] == "released"
        assert released["idempotency_key"] == fresh_key
        assert cli("recover-import", "--plan-sha256", prepared["plan_sha256"],
            "--execute")["cold_import"]["status"] == "replayed"
        assert command("inspect")["lease"] == released
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.communicate(timeout=30)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_elapsed_source_lease_requires_native_release_before_import(tmp_path, monkeypatch, provider):
    _, command, release, prepare, _, _, context, runtime, _ = source_execution(tmp_path, monkeypatch, ttl=1)
    expires = datetime.fromisoformat(context["lease"]["expires_at"].replace("Z", "+00:00")).timestamp()
    time.sleep(max(0, expires - time.time()) + .05)
    current = command("inspect")["lease"]
    assert current["status"] == "active" and current["expires_at"] == context["lease"]["expires_at"]
    assert prepare(provider, success=False)["reason_code"] == "cold_import_lease_requires_settlement"
    assert not list(runtime.rglob("writer-fence.json"))
    release()
    assert prepare(provider)["status"] == "prepared"
