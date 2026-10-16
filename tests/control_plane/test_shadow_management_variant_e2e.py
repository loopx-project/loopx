"""Management composition invariants through real CLI, RPC and file stores.

Corruption is restricted to disposable fixtures. The crash seam pauses after
the actual rename; it never substitutes a management or provider result.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import select
import subprocess
import sys

import pytest

from loopx.cli_commands.coordination_shadow import _projection_version
from loopx.control_plane.coordination.coordination_state_contract_generated import (
    COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
    COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA,
)
from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot
from loopx.control_plane.coordination.shadow_management import read_shadow_management_state
from loopx.control_plane.effect_runtime import effect_runtime_result

pytestmark = pytest.mark.stage2c_e2e
REPO_ROOT = Path(__file__).resolve().parents[2]


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
            "id": goal_id,
            "status": "active",
            "repo": str(repo),
            "state_file": "ACTIVE_GOAL_STATE.md",
            "coordination": {
                "agent_model": "peer_v1",
                "registered_agents": ["agent-a", "agent-b"],
                "runtime_shadow": {
                    "schema_version": "loopx_coordination_runtime_shadow_config_v0",
                    "enabled": True,
                    "provider": "file_v0",
                },
            },
        })
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({
        "common_runtime_root": str(runtime),
        "goals": goals,
    }))
    return registry, runtime


def _cli(
    registry: Path,
    runtime: Path,
    *args: str,
    success: bool = True,
) -> dict:
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--registry",
            str(registry),
            "--runtime-root",
            str(runtime),
            "--format",
            "json",
            *args,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if success:
        assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    return json.loads(result.stdout)


def _candidate(runtime: Path, goal: str) -> Path:
    digest = hashlib.sha256(goal.encode()).hexdigest()[:16]
    return runtime / "authority-shadow" / "file-v0" / f"authority-store-{digest}.json"


def _bootstrap(
    registry: Path,
    runtime: Path,
    goal: str = "goal-a",
) -> dict:
    return _cli(
        registry,
        runtime,
        "coordination-shadow",
        "bootstrap",
        "--goal-id",
        goal,
        "--execute",
    )["bootstrap"]


_CRASH_RPC = r"""
import fs from 'node:fs';
import { syncBuiltinESMExports } from 'node:module';
import { pathToFileURL } from 'node:url';
const [modulePath, action, raw, target] = process.argv.slice(1);
const request = JSON.parse(raw);
const actualRename = fs.promises.rename;
fs.promises.rename = async (source, destination) => {
  const result = await actualRename(source, destination);
  const reached = action === 'bootstrap'
    ? String(destination) === target : String(source) === target;
  if (reached) {
    process.stdout.write('BARRIER ' + JSON.stringify({source: String(source), destination: String(destination)}) + '\n');
    await new Promise(() => { setInterval(() => {}, 1000); });
  }
  return result;
};
syncBuiltinESMExports();
const runtime = await import(pathToFileURL(modulePath).href);
const result = action === 'bootstrap'
  ? await runtime.bootstrapCoordinationRuntimeShadow(request)
  : await runtime.rollbackCoordinationRuntimeShadow(request);
process.stdout.write(JSON.stringify(result) + '\n');
"""


def _kill_rpc(action: str, request: dict, target: Path) -> dict:
    child = subprocess.Popen(
        ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
         _CRASH_RPC, str(REPO_ROOT / "loopx/control_plane/coordination/runtime_shadow.ts"),
         action, json.dumps(request), str(target)],
        cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    assert child.stdout is not None
    try:
        ready, _, _ = select.select([child.stdout], [], [], 30)
        assert ready, "real management RPC did not reach its rename window"
        line = child.stdout.readline()
        if not line.startswith("BARRIER "):
            child.kill()
            stdout, stderr = child.communicate(timeout=10)
            raise AssertionError(f"RPC exited before rename: {line}{stdout}\n{stderr}")
        child.kill()
        child.communicate(timeout=10)
        assert child.returncode == -9
        return json.loads(line.removeprefix("BARRIER "))
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=10)


def _source(registry: Path, runtime: Path, goal_id: str = "goal-a") -> tuple[dict, Path]:
    goal = next(goal for goal in json.loads(registry.read_text())["goals"] if goal["id"] == goal_id)
    state = Path(goal["repo"]) / goal["state_file"]
    projection, snapshot = build_runtime_shadow_source_snapshot(
        goal=goal, runtime_root=runtime, state_path=state, registry_path=registry,
    )
    return {
        "schema_version": COORDINATION_RUNTIME_SHADOW_BOOTSTRAP_REQUEST_SCHEMA,
        "runtime_root": str(runtime), "goal_id": goal_id,
        "operation_id": "bootstrap:variant", "projection": projection,
        "source_version": f"legacy-projection:{_projection_version(projection)}",
        "source_snapshot": snapshot,
    }, state


def _rollback_request(runtime: Path, state: Path, revision: str, goal_id: str = "goal-a") -> dict:
    return {
        "schema_version": COORDINATION_RUNTIME_SHADOW_ROLLBACK_REQUEST_SCHEMA,
        "runtime_root": str(runtime), "goal_id": goal_id,
        "operation_id": f"shadow-rollback:{goal_id}:{revision}",
        "expected_provider_revision": revision, "expected_bootstrap_operation_id": None,
        "projection": {}, "source_snapshot": {"state_path": str(state)},
    }


def _management(runtime: Path, goal: str = "goal-a") -> Path:
    digest = hashlib.sha256(goal.encode()).hexdigest()[:16]
    return runtime / "authority-transition/file-v0" / f"shadow-management-{digest}"


def _durable_files(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and not any(part.endswith(".lock") for part in path.parts)
    }


def test_changed_source_after_bootstrap_commit_requires_abort_before_new_baseline(tmp_path: Path) -> None:
    registry, runtime = _workspace(tmp_path)
    request, state = _source(registry, runtime)
    _kill_rpc("bootstrap", request, _candidate(runtime, "goal-a"))
    state.write_text(state.read_text().replace("handoff_mode: hard_lease", "handoff_mode: soft_claim"))
    before = _durable_files(runtime)
    rejected = _cli(registry, runtime, "coordination-shadow", "bootstrap", "--goal-id", "goal-a",
                    "--execute", success=False)
    assert rejected["ok"] is False, rejected
    assert _durable_files(runtime) == before
    assert read_shadow_management_state(runtime, "goal-a")["status"] == "bootstrapping"
    aborted = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                   "--bootstrap-operation-id", request["operation_id"], "--execute")["rollback"]
    current = _bootstrap(registry, runtime)
    assert current["capture_lineage_id"] != aborted["capture_lineage_id"]
    inspected = _cli(registry, runtime, "coordination-shadow", "inspect", "--goal-id", "goal-a")
    assert inspected["inspection"]["status"] == "matched", inspected
    candidate = json.loads(_candidate(runtime, "goal-a").read_text())
    assert candidate["head"]["handoff_mode"] == "soft_claim"


def test_same_rpc_operation_ids_are_scoped_to_each_goal_across_rollback_and_replay(tmp_path: Path) -> None:
    registry, runtime = _workspace(tmp_path)
    requests = {goal: _source(registry, runtime, goal)[0] for goal in ("goal-a", "goal-b")}
    results = {goal: effect_runtime_result("coordination.runtime_shadow.bootstrap", request)
               for goal, request in requests.items()}
    assert all(value["status"] == "applied" for value in results.values()), results
    assert results["goal-a"]["capture_lineage_id"] != results["goal-b"]["capture_lineage_id"]
    identity_path = runtime / "authority-shadow/file-v0/store-identity"
    identity = identity_path.read_bytes()
    rollbacks = {}
    for goal in requests:
        request = _rollback_request(runtime, Path(requests[goal]["source_snapshot"]["state_path"]),
                                    results[goal]["provider_revision"], goal)
        request["operation_id"] = "rollback:shared-operation-id"
        rollbacks[goal] = request
        other = "goal-b" if goal == "goal-a" else "goal-a"
        other_before = _durable_files(_management(runtime, other))
        retired = effect_runtime_result("coordination.runtime_shadow.rollback", request)
        assert retired["status"] == "applied", retired
        assert retired["capture_lineage_id"] == results[goal]["capture_lineage_id"]
        assert _durable_files(_management(runtime, other)) == other_before
    current = {goal: _bootstrap(registry, runtime, goal) for goal in requests}
    before = _durable_files(runtime)
    for goal, request in rollbacks.items():
        replayed = effect_runtime_result("coordination.runtime_shadow.rollback", request)
        assert replayed["status"] == "replayed", replayed
        assert replayed["capture_lineage_id"] == results[goal]["capture_lineage_id"]
        assert replayed["current_capture_lineage_id"] == current[goal]["capture_lineage_id"]
    assert _durable_files(runtime) == before
    assert identity_path.read_bytes() == identity


def test_wrong_abort_selector_and_late_aborted_bootstrap_cannot_replace_same_data_new_lineage(tmp_path: Path) -> None:
    registry, runtime = _workspace(tmp_path)
    request, state = _source(registry, runtime)
    primary = state.read_bytes()
    _kill_rpc("bootstrap", request, _candidate(runtime, "goal-a"))
    before = _durable_files(runtime)
    wrong = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                 "--bootstrap-operation-id", "bootstrap:unrelated", "--execute", success=False)
    assert wrong["ok"] is False, wrong
    assert wrong["rollback"]["reason_code"] == "bootstrap_operation_not_pending", wrong
    assert _durable_files(runtime) == before
    stopped = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                   "--bootstrap-operation-id", request["operation_id"], "--execute")["rollback"]
    current = _bootstrap(registry, runtime)
    assert state.read_bytes() == primary
    assert current["capture_lineage_id"] != stopped["capture_lineage_id"]
    before = _durable_files(runtime)
    delayed = effect_runtime_result("coordination.runtime_shadow.bootstrap", request)
    assert delayed["reason_code"] == "bootstrap_aborted", delayed
    assert delayed["current_capture_lineage_id"] == current["capture_lineage_id"]
    assert _durable_files(runtime) == before
    assert read_shadow_management_state(runtime, "goal-a")["binding"]["capture_lineage_id"] == current["capture_lineage_id"]
    rollback_manifest = (_management(runtime) / "operations"
                         / hashlib.sha256(stopped["operation_id"].encode()).hexdigest() / "manifest.json")
    original = rollback_manifest.read_bytes()
    damaged = json.loads(original)
    damaged["capture_lineage_id"] = "changed-rollback-evidence"
    rollback_manifest.write_text(json.dumps(damaged))
    before = _durable_files(runtime)
    rejected = effect_runtime_result("coordination.runtime_shadow.bootstrap", request)
    assert rejected["status"] == "failed", rejected
    assert rejected["reason_code"] == "shadow_management_manifest_invalid", rejected
    assert _durable_files(runtime) == before
    rollback_manifest.write_bytes(original)
    assert effect_runtime_result("coordination.runtime_shadow.bootstrap", request)["reason_code"] == "bootstrap_aborted"


@pytest.mark.parametrize("storage", ["current", "historical"])
def test_rollback_result_cannot_borrow_another_goals_archive_evidence(tmp_path: Path, storage: str) -> None:
    registry, runtime = _workspace(tmp_path)
    first = {goal: _bootstrap(registry, runtime, goal) for goal in ("goal-a", "goal-b")}
    retired = {
        goal: _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", goal,
                   "--provider-revision", initial["provider_revision"], "--execute")["rollback"]
        for goal, initial in first.items()
    }
    other_result = json.loads((_management(runtime, "goal-b") / "state.json").read_text())["result"]
    current = {goal: _bootstrap(registry, runtime, goal) for goal in first} if storage == "historical" else {}
    operation = retired["goal-a"]["operation_id"]
    cache = (_management(runtime) / "state.json" if storage == "current" else
             _management(runtime) / "operations" / hashlib.sha256(operation.encode()).hexdigest() / "result.json")
    record = json.loads(cache.read_text())
    original_cache = cache.read_bytes()
    # Keep A's outer request and manifest bindings while damaging the cached
    # historical result with B's structurally valid, actually executed result.
    record["result"] = other_result
    cache.write_text(json.dumps(record))
    before = _durable_files(runtime)
    result = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                  "--provider-revision", first["goal-a"]["provider_revision"], "--execute", success=False)
    assert result["ok"] is False, json.dumps(result, indent=2)
    assert _durable_files(runtime) == before
    for goal in current:
        assert read_shadow_management_state(runtime, goal)["binding"]["capture_lineage_id"] == current[goal]["capture_lineage_id"]
    cache.write_bytes(original_cache)
    replayed = _cli(registry, runtime, "coordination-shadow", "rollback", "--goal-id", "goal-a",
                    "--provider-revision", first["goal-a"]["provider_revision"], "--execute")["rollback"]
    assert replayed["status"] == "replayed", replayed
    assert replayed["capture_lineage_id"] == first["goal-a"]["capture_lineage_id"]
    assert replayed["current_capture_lineage_id"] == (current["goal-a"]["capture_lineage_id"] if current else None)
