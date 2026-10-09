"""Retained originals are handled without bringing back their Python producers.

The original CLI creates real interrupted writes. A separate copied runtime
then dispatches the shipped TS effects with those producer files absent. The
OS-lock adapter remains: language retirement must preserve useful Host IO.
"""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import loopx
import pytest

from loopx.control_plane.coordination.coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
)
from tests.control_plane.shadow_e2e_fixture import workspace


pytestmark = pytest.mark.stage2c_e2e


@pytest.fixture(scope="module")
def receiver(tmp_path_factory):
    root = tmp_path_factory.mktemp("receiver")
    package = root / "loopx"
    shutil.copytree(Path(loopx.__file__).parent, package,
                    ignore=shutil.ignore_patterns("__pycache__", "testing", "*.pyc"))
    removed = ["todos.py", "bootstrap.py",
               "control_plane/coordination/runtime_shadow_writer_adapter.py",
               "control_plane/coordination/local_authority_shadow_outbox.py"]
    for relative in removed:
        (package / relative).unlink()
    assert all(not (package / relative).exists() for relative in removed)
    assert (package / "control_plane/coordination/shadow_lock_host.py").is_file()
    module = package / "control_plane/effect_runtime_handlers.ts"
    script = (
        f"import {{createEffectRuntimeHandlers, dispatchEffectRuntimeMethod}} from {json.dumps(module.as_uri())};"
        "let raw=''; for await (const chunk of process.stdin) raw+=chunk;"
        "const {method,request}=JSON.parse(raw);"
        "const handlers=createEffectRuntimeHandlers({fingerprint:'disposable',requestShutdown:()=>{}});"
        "console.log(JSON.stringify(await dispatchEffectRuntimeMethod(handlers,method,request)));"
    )

    def dispatch(method, request):
        result = subprocess.run(
            ["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
            input=json.dumps({"method": method, "request": request}),
            cwd=root, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)

    return dispatch


def drain(receiver, fixture):
    return receiver("coordination.runtime_shadow.drain", {
        "schema_version": "loopx_shadow_drain_v0", "runtime_root": str(fixture.runtime),
        "goal_id": fixture.goal, "python_executable": sys.executable, "config_enabled": True,
        "max_entries": 32, "budget_seconds": 10, "lock_timeout_seconds": 2,
    })


def inspect(fixture):
    return fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]


def originals(path):
    return {str(p.relative_to(path)): p.read_bytes() for p in path.rglob("*")
            if p.is_file() and not p.name.endswith(".lock")}


@pytest.mark.parametrize("window,resolution,no_op", [
    ("before_replace", "abandoned", True),
    ("before_marker", "committed_proven_by_readback", False),
    ("before_commit", "committed", False),
    ("after_commit", "committed", False),
])
def test_stopped_original_todo_recovers_once_without_python_producer(
    tmp_path, receiver, window, resolution, no_op,
):
    fixture = workspace(tmp_path)
    fixture.crash(window, "todo", "add", "--role", "agent", "--text", "Interrupted original")
    source = fixture.state.read_bytes()
    before = inspect(fixture)
    partition = fixture.runtime / "authority-shadow/outbox" / fixture.goal / "todos"
    prepared = json.loads(next(partition.glob("*.prepared.json")).read_bytes())
    initial = before["capture"]["runtime_shadow_readback"]["proof"]["last_applied_sequences"]["todos"]
    result = drain(receiver, fixture)
    assert result["ok"] is True, result
    assert result["replayed"] == (1 if window == "after_commit" else 0)
    assert result["delivered"] + result["reconciled"] == (0 if window == "after_commit" else 1)
    assert fixture.state.read_bytes() == source
    after = inspect(fixture)
    view = after["capture"]["runtime_shadow_readback"]
    receipt_view = receiver("coordination.runtime_shadow.outbox_read", {
        "schema_version": LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
        "runtime_root": str(fixture.runtime), "goal_id": fixture.goal, "store_kind": "runtime_shadow",
        "scan_after_cursor": None, "scan_limit": 0, "read_model": "proof",
        "receipt_operation_id": prepared["entry_id"],
    })
    assert receipt_view["status"] == "loaded", receipt_view
    receipt = receipt_view["proof"]["receipt"]["receipts"][0]
    assert receipt["entry_id"] == prepared["entry_id"]
    assert receipt["resolution"] == resolution and receipt["no_op"] is no_op
    assert receipt["seq"] == 1 and view["proof"]["last_sequences"]["todos"] == 1
    assert view["proof"]["last_applied_sequences"]["todos"] == (0 if no_op else 1)
    assert initial == (1 if window == "after_commit" else 0)
    assert [p.name for p in partition.iterdir()] == ["drain-cursor.json"]
    retained = originals(fixture.runtime)
    assert drain(receiver, fixture)["outcome"] == "nothing_pending"
    assert originals(fixture.runtime) == retained
    # Successful original disposition is not stopped-Host/import qualification.
    assert after["import_ready"] is False and after["writer_stop_verified"] is False
    assert not (fixture.runtime / "authority").exists()


@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
def test_original_lease_receipt_never_grants_or_releases_work(tmp_path, receiver, window):
    fixture = workspace(tmp_path)
    todo = fixture.add("Original leased task")["todo_id"]
    fixture.crash(window, "task-lease", "acquire", "--todo-id", todo, "--owner", "agent-a",
                  "--idempotency-key", "original", "--ttl-seconds", "120")
    path = fixture.runtime / "goals" / fixture.goal / "task-leases" / (todo + ".json")
    lease = path.read_bytes()
    assert drain(receiver, fixture)["ok"] is True
    assert path.read_bytes() == lease
    after = inspect(fixture)
    assert after["leases_requiring_settlement"] == [todo]
    assert after["retained_leases"][0]["status"] == "active"
    assert after["import_ready"] is False
    retained = originals(fixture.runtime)
    assert drain(receiver, fixture)["outcome"] == "nothing_pending"
    assert originals(fixture.runtime) == retained and path.read_bytes() == lease


def test_unproved_original_stays_intact_and_rolls_back_same_operation(tmp_path, receiver):
    fixture = workspace(tmp_path)
    fixture.crash("before_marker", "handoff-mode", "set", "--mode", "soft_claim")
    fixture.cli("handoff-mode", "set", "--mode", "hard_lease")
    before = originals(fixture.runtime)
    result = drain(receiver, fixture)
    assert result["ok"] is False and result["reason_code"] == "outbox_source_unproved"
    assert originals(fixture.runtime) == before
    inventory = inspect(fixture)
    source = fixture.state.read_bytes()
    artifacts = inventory["capture"]["artifacts"]
    candidate = Path(artifacts["runtime_store"]["path"]).read_bytes()
    pending = originals(Path(artifacts["outbox"]["path"]))
    request = {"schema_version": "loopx_coordination_runtime_shadow_rollback_v0",
               "runtime_root": str(fixture.runtime), "goal_id": fixture.goal,
               "operation_id": "retain-original", "expected_bootstrap_operation_id": None,
               "expected_provider_revision": inventory["capture"]["runtime_shadow_readback"]["provider_revision"],
               "projection": inventory["projection"], "source_snapshot": inventory["source_snapshot"]}
    rolled = receiver("coordination.runtime_shadow.rollback", request)
    assert rolled["status"] == "applied", rolled
    assert Path(rolled["candidate_archive_path"]).read_bytes() == candidate
    assert originals(Path(rolled["outbox_archive_path"])) == pending
    retained = originals(fixture.runtime)
    replay = receiver("coordination.runtime_shadow.rollback", request)
    assert replay["status"] == "replayed" and replay["operation_id"] == rolled["operation_id"]
    assert originals(fixture.runtime) == retained and fixture.state.read_bytes() == source
    refused = drain(receiver, fixture)
    assert refused["ok"] is False and refused["outcome"] == "stopped"
    assert refused["delivered"] == 0 and refused["replayed"] == 0
    assert originals(fixture.runtime) == retained
    assert inspect(fixture)["capture"]["outbox_review"] is None
    assert not (fixture.runtime / "authority").exists()
