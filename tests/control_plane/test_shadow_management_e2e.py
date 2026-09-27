"""Public CLI and real Python -> TypeScript management interleavings.

All goals and providers are disposable. The scheduling seam delays a real RPC;
it never supplies a substitute provider result or edits an active user goal.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import select
import subprocess
import sys
import time

import pytest

from loopx.control_plane.coordination.shadow_management import read_shadow_management_state
from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
from loopx.control_plane.coordination.coordination_state_contract_generated import (
    COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
)
from loopx.control_plane.effect_runtime import effect_runtime_result

REPO_ROOT = Path(__file__).resolve().parents[2]


pytestmark = pytest.mark.stage2c_e2e


def _workspace(tmp_path: Path) -> tuple[Path, Path]:
    runtime = tmp_path / "runtime"
    goals = []
    for goal_id in ("goal-a", "goal-b"):
        repo = tmp_path / goal_id
        repo.mkdir()
        (repo / "ACTIVE_GOAL_STATE.md").write_text(
            f"---\ngoal_id: {goal_id}\nhandoff_mode: hard_lease\n"
            "updated_at: 2026-01-01T00:00:00+00:00\n---\n\n## Agent Todo\n\n"
        )
        goals.append({
            "id": goal_id, "status": "active", "repo": str(repo),
            "state_file": "ACTIVE_GOAL_STATE.md",
            "coordination": {
                "agent_model": "peer_v1", "registered_agents": ["agent-a", "agent-b"],
                "runtime_shadow": {
                    "schema_version": "loopx_coordination_runtime_shadow_config_v0",
                    "enabled": True, "provider": "file_v0",
                },
            },
        })
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": goals}))
    return registry, runtime


def _arguments(registry: Path, runtime: Path, *args: str) -> list[str]:
    return ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args]


def _cli(registry: Path, runtime: Path, *args: str, success: bool = True) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", *_arguments(registry, runtime, *args)],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=30,
    )
    if success:
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


def _candidate(runtime: Path, goal: str) -> Path:
    digest = hashlib.sha256(goal.encode()).hexdigest()[:16]
    return runtime / "authority-shadow" / "file-v0" / f"authority-store-{digest}.json"


def _bootstrap(registry: Path, runtime: Path, goal: str = "goal-a") -> dict:
    return _cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", goal, "--execute")["bootstrap"]


def test_public_rollback_preserves_other_goal_and_replays_after_primary_changes(tmp_path: Path) -> None:
    registry, runtime = _workspace(tmp_path)
    first = _bootstrap(registry, runtime)
    _bootstrap(registry, runtime, "goal-b")
    other = _candidate(runtime, "goal-b").read_bytes()
    identity = (runtime / "authority-shadow" / "file-v0" / "store-identity").read_bytes()
    result = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                  "--provider-revision", first["provider_revision"], "--execute")["rollback"]
    assert result["status"] == "applied"
    _cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent",
         "--text", "Write primary while awaiting bootstrap", "--claimed-by", "agent-a")
    assert not _candidate(runtime, "goal-a").exists()
    second = _bootstrap(registry, runtime)
    assert first["capture_lineage_id"] != second["capture_lineage_id"]
    current = _candidate(runtime, "goal-a").read_bytes()
    historical = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                      "--provider-revision", first["provider_revision"], "--execute")["rollback"]
    assert historical["status"] == "replayed"
    assert historical["current_capture_lineage_id"] == second["capture_lineage_id"]
    assert _candidate(runtime, "goal-a").read_bytes() == current
    assert _candidate(runtime, "goal-b").read_bytes() == other
    assert (runtime / "authority-shadow" / "file-v0" / "store-identity").read_bytes() == identity


_DRAIN_PHASE = {"before": "before_commit", "after": "after_commit"}

# The public write keeps its own inline drain, but the pause now happens inside
# the real native batch: the private fault driver runs the same
# ``drainShadowOutbox`` the production RPC handler runs and stops at the actual
# commit boundary. Releasing it lets that same batch continue, so a commit or a
# cursor update attempted after the generation changed is still real.
_DELAYED_WRITER = """
import json, pathlib, subprocess, sys, time
from loopx.control_plane.coordination import local_authority_shadow_adapter as adapter
from loopx.cli import main
barrier, release, phase = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
actual = adapter.effect_runtime_result
def delayed(method, request, **kwargs):
    if method != 'coordination.runtime_shadow.drain':
        return actual(method, request, **kwargs)
    child = subprocess.Popen(
        ['node', '--no-warnings', '--experimental-strip-types',
         'loopx/control_plane/testing/shadow_drain_fault_process.ts', phase, str(release)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    child.stdin.write(json.dumps(request))
    child.stdin.close()
    for line in child.stdout:
        if line.startswith('BARRIER '):
            barrier.write_text(line.removeprefix('BARRIER '))
            break
    else:
        raise RuntimeError('native barrier missing: ' + child.stderr.read())
    deadline = time.monotonic() + 60
    while not release.exists():
        if time.monotonic() > deadline:
            child.kill(); child.wait()
            raise RuntimeError('test scheduling barrier timed out')
        time.sleep(.01)
    remaining, errors = child.stdout.read(), child.stderr.read()
    if child.wait() != 0:
        raise RuntimeError(errors or remaining)
    return json.loads(remaining.strip().splitlines()[-1])
adapter.effect_runtime_result = delayed
raise SystemExit(main(sys.argv[4:]))
"""


def _paused_writer(tmp_path: Path, registry: Path, runtime: Path, timing: str) -> tuple[subprocess.Popen, Path, dict]:
    barrier = tmp_path / "commit-barrier.json"
    release = tmp_path / "commit-release"
    args = _arguments(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent",
                      "--text", "Transaction across a management boundary", "--claimed-by", "agent-a")
    child = subprocess.Popen(
        [sys.executable, "-c", _DELAYED_WRITER, str(barrier), str(release), _DRAIN_PHASE[timing], *args],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    deadline = time.monotonic() + 20
    while not barrier.exists():
        if child.poll() is not None:
            stdout, stderr = child.communicate()
            pytest.fail(f"public writer exited before real commit RPC: {stdout}\n{stderr}")
        if time.monotonic() > deadline:
            child.kill()
            stdout, stderr = child.communicate()
            pytest.fail(f"public writer did not reach real commit RPC: {stdout}\n{stderr}")
        time.sleep(.01)
    # Atomic JSON is unnecessary for authority here; retry the test notification only.
    for _ in range(100):
        try:
            return child, release, json.loads(barrier.read_text())
        except json.JSONDecodeError:
            time.sleep(.01)
    child.kill()
    raise AssertionError("incomplete test barrier")


def _release(child: subprocess.Popen, path: Path) -> dict:
    path.write_text("continue")
    try:
        stdout, stderr = child.communicate(timeout=30)
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise
    assert child.returncode == 0, f"{stdout}\n{stderr}"
    return json.loads(stdout)


@pytest.mark.parametrize("timing", ["before", "after"])
def test_late_real_commit_cannot_cross_rollback_and_rebootstrap(tmp_path: Path, timing: str) -> None:
    registry, runtime = _workspace(tmp_path)
    first = _bootstrap(registry, runtime)
    _bootstrap(registry, runtime, "goal-b")
    other = _candidate(runtime, "goal-b").read_bytes()
    child, release, barrier = _paused_writer(tmp_path, registry, runtime, timing)
    try:
        request = barrier["request"]
        assert request["schema_version"] == "loopx_shadow_drain_v0"
        assert request["goal_id"] == "goal-a"
        assert request["runtime_root"] == str(runtime.resolve())
        # The paused batch still runs under the generation that is about to be
        # superseded, and the revision it targets is durable readback.
        management = read_shadow_management_state(runtime, "goal-a")
        assert management is not None and management["status"] == "active"
        assert management["binding"]["capture_lineage_id"] == first["capture_lineage_id"]
        revision = json.loads(_candidate(runtime, "goal-a").read_text())["provider_revision"]
        if timing == "before":
            assert revision == first["provider_revision"]
        else:
            assert revision != first["provider_revision"]
        rollback = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                        "--provider-revision", revision, "--execute")["rollback"]
        archived = Path(rollback["outbox_archive_path"])
        retained = {str(path.relative_to(archived)): path.read_bytes() for path in archived.rglob("*") if path.is_file()}
        assert any(name.endswith(".prepared.json") for name in retained)
        second = _bootstrap(registry, runtime)
        assert second["capture_lineage_id"] != first["capture_lineage_id"]
        candidate = _candidate(runtime, "goal-a").read_bytes()
        payload = _release(child, release)
        # The resumed batch reports this write from its own drain evidence; it
        # must never claim a delivery into the generation that replaced it.
        assert payload["coordination_runtime_shadow"]["outcome"] != "delivered"
        assert _candidate(runtime, "goal-a").read_bytes() == candidate
        assert _candidate(runtime, "goal-b").read_bytes() == other
        assert {str(path.relative_to(archived)): path.read_bytes() for path in archived.rglob("*") if path.is_file()} == retained
        active_outbox = runtime / "authority-shadow" / "outbox" / "goal-a"
        assert not list(active_outbox.rglob("drain-cursor.json"))
        assert not list(active_outbox.rglob("*.prepared.json"))
        # An explicit retry of the superseded batch reads durable receipts and
        # still cannot deliver into the generation that replaced it.
        late = effect_runtime_result("coordination.runtime_shadow.drain", request)
        assert late["outcome"] not in {"delivered", "replayed", "reconciled"}
        assert late["delivered"] == 0 and late["replayed"] == 0
        assert _candidate(runtime, "goal-a").read_bytes() == candidate
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


def test_corrupt_history_after_real_commit_cannot_authorize_cursor_cleanup(tmp_path: Path) -> None:
    registry, runtime = _workspace(tmp_path)
    first = _bootstrap(registry, runtime)
    child, release, barrier = _paused_writer(tmp_path, registry, runtime, "after")
    try:
        # The commit already happened: the durable candidate moved past bootstrap.
        assert barrier["request"]["goal_id"] == "goal-a"
        candidate = _candidate(runtime, "goal-a")
        assert json.loads(candidate.read_text())["provider_revision"] != first["provider_revision"]
        directory = runtime / "authority-shadow" / "outbox" / "goal-a" / "todos"
        entries = {path.name: path.read_bytes() for path in directory.glob("*.json")}
        assert any(name.endswith(".prepared.json") for name in entries)
        record = json.loads(candidate.read_text())
        record["committed"][0]["provider_revision"] = "file:1:" + "0" * 24
        candidate.write_text(json.dumps(record))
        corrupt = candidate.read_bytes()
        _release(child, release)
        assert {path.name: path.read_bytes() for path in directory.glob("*.json")} == entries
        assert candidate.read_bytes() == corrupt
        assert not (directory / "drain-cursor.json").exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()


@pytest.mark.parametrize("phase", ["bootstrap_prepared", "bootstrap_candidate_committed", "bootstrap_outbox_ready"])
def test_public_bootstrap_operation_selector_aborts_real_crash(tmp_path: Path, phase: str) -> None:
    registry, runtime = _workspace(tmp_path)
    _bootstrap(registry, runtime, "goal-b")
    other = _candidate(runtime, "goal-b").read_bytes()
    goal = json.loads(registry.read_text())["goals"][0]
    state_path = Path(goal["repo"]) / goal["state_file"]
    original = state_path.read_bytes()
    projection, snapshot = build_runtime_shadow_source_snapshot(
        goal=goal, runtime_root=runtime, state_path=state_path, registry_path=registry,
    )
    operation = "bootstrap:public-crash"
    request = {"schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
               "runtime_root": str(runtime), "goal_id": "goal-a", "operation_id": operation,
               "source_version": "source:before-crash", "projection": projection, "source_snapshot": snapshot}
    worker = REPO_ROOT / "tests" / "control_plane_ts" / "fixtures" / "shadow_management_crash_worker.ts"
    child = subprocess.Popen(
        ["node", "--no-warnings", "--experimental-strip-types", str(worker), "bootstrap-public", json.dumps(request), phase],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        ready, _, _ = select.select([child.stdout], [], [], 10)
        assert ready, "bootstrap did not reach the real filesystem barrier"
        line = child.stdout.readline()
        assert line.strip() == f"ready:{phase}", line
        child.kill()
        child.communicate(timeout=10)
        pending = read_shadow_management_state(runtime, "goal-a")
        assert pending is not None and pending["status"] == "bootstrapping"
        blocked = _cli(registry, runtime, "todo", "add", "--goal-id", "goal-a", "--role", "agent",
                       "--text", "Must not write while bootstrap is pending", "--claimed-by", "agent-a", success=False)
        assert blocked["ok"] is False
        assert state_path.read_bytes() == original
        stopped = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                       "--bootstrap-operation-id", operation, "--execute")["rollback"]
        assert stopped["status"] == "applied"
        assert read_shadow_management_state(runtime, "goal-a")["status"] == "inactive"
        assert _candidate(runtime, "goal-b").read_bytes() == other
        current = _bootstrap(registry, runtime)
        assert current["status"] == "applied"
        assert current["capture_lineage_id"] != stopped["capture_lineage_id"]
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate()
