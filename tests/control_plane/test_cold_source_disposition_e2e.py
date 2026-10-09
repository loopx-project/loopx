"""Retained originals are handled without bringing back their Python producers.

The original CLI creates real interrupted writes. A separate copied runtime
then dispatches the shipped TS effects with those producer files absent. The
OS-lock adapter remains: language retirement must preserve useful Host IO.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import loopx
import pytest

from loopx.control_plane.coordination.coordination_state_contract_generated import (
    LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
)
from tests.control_plane.shadow_e2e_fixture import workspace
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime


pytestmark = pytest.mark.stage2c_e2e


@pytest.fixture(scope="module")
def receiver_package(tmp_path_factory):
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
    return package


@pytest.fixture(scope="module")
def receiver(receiver_package):
    package = receiver_package
    root = package.parent
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


def cold_workspace(path):
    fixture = workspace(path, bootstrap=False)
    registry = fixture.state.parent / ".loopx/registry.json"
    registry.parent.mkdir()
    fixture.registry.rename(registry)
    fixture.registry = registry
    fixture.cli("coordination-shadow", "bootstrap", "--execute")
    return fixture


def cold_cli(fixture, receiver_package, action, *args, success=True):
    child = subprocess.run([sys.executable, "-c",
        "import loopx,sys;print(loopx.__file__,file=sys.stderr);"
        "from loopx.entrypoint import main;raise SystemExit(main())",
        *fixture.arguments("coordination-shadow", action, *map(str, args))],
        cwd=fixture.state.parent.parent,
        env={**os.environ, "PYTHONPATH": str(receiver_package.parent)},
        capture_output=True, text=True, timeout=60)
    assert str(receiver_package / "__init__.py") in child.stderr
    assert (child.returncode == 0) is success, child.stdout + child.stderr
    assert "Traceback" not in child.stderr
    return json.loads(child.stdout)["cold_import"]


def backup(fixture, name):
    child = subprocess.run([sys.executable, "-c",
        "import loopx,sys;print(loopx.__file__,file=sys.stderr);"
        "from loopx.entrypoint import main;raise SystemExit(main())",
        "--registry", str(fixture.registry), "--runtime-root", str(fixture.runtime),
        "--format", "json", "backup-state", "--project", str(fixture.state.parent),
        "--output-dir", str(fixture.state.parent.parent / "backups"), "--backup-id", name,
        "--current-project-only", "--no-skills", "--no-automations", "--execute"],
        cwd=fixture.state.parent.parent, capture_output=True, text=True, timeout=60)
    assert str(Path(loopx.__file__).resolve()) in child.stderr
    assert child.returncode == 0, child.stdout + child.stderr
    result = json.loads(child.stdout)
    assert result["ok"], result
    return result


def provider_todos(receiver, fixture):
    return receiver("coordination.local_authority.todo_list", {
        "schema_version": "loopx_local_coordination_todo_list_request_v0",
        "runtime_root": str(fixture.runtime), "goal_id": fixture.goal,
        "role": None, "status": None, "todo_id": None, "agent_id": None, "limit": None})


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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("window", ["before_commit", "after_commit"])
def test_original_lease_disposition_requires_release_before_cold_import(
    tmp_path, monkeypatch, receiver, receiver_package, provider, window,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = cold_workspace(tmp_path / "project")
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
    inventory = inspect(fixture)
    rolled = receiver("coordination.runtime_shadow.rollback", {
        "schema_version": "loopx_coordination_runtime_shadow_rollback_v0",
        "runtime_root": str(fixture.runtime), "goal_id": fixture.goal,
        "operation_id": "retain-lease-original", "expected_bootstrap_operation_id": None,
        "expected_provider_revision": inventory["capture"]["runtime_shadow_readback"]["provider_revision"],
        "projection": inventory["projection"], "source_snapshot": inventory["source_snapshot"]})
    assert rolled["status"] == "applied", rolled
    archived = originals(fixture.runtime / "authority-shadow")
    active_backup = backup(fixture, "inactive-capture-active-lease")
    before = originals(fixture.runtime)
    rejected = cold_cli(fixture, receiver_package, "prepare-import", "--operation-id", "lease-import",
        "--backup-manifest", active_backup["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease", success=False)
    assert rejected["reason_code"] == "cold_import_lease_requires_settlement", rejected
    assert originals(fixture.runtime) == before and path.read_bytes() == lease
    assert not list(fixture.runtime.rglob("writer-fence.json"))
    assert not (fixture.runtime / "authority").exists()
    # Capture disposition does not settle the live source lease. Its original
    # owner must release it; neither its old receipt nor import grants work.
    released = fixture.cli("task-lease", "release", "--todo-id", todo, "--owner", "agent-a",
        "--idempotency-key", "original", "--expected-version", str(json.loads(lease)["version"]))
    assert released["released"] and released["lease"]["status"] == "released", released
    settled = path.read_bytes()
    fresh_backup = backup(fixture, "released-before-import")
    plan = cold_cli(fixture, receiver_package, "prepare-import", "--operation-id", "lease-import",
        "--backup-manifest", fresh_backup["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease")
    assert plan["source_inventory"]["lease_count"] == 1
    applied = cold_cli(fixture, receiver_package, "apply-import", "--operation-id", "lease-import",
        "--plan-sha256", plan["plan_sha256"], "--writers-stopped", "--execute")
    assert applied["status"] == "applied" and applied["execution_authority_granted"] is False
    current_lease = fixture.cli("task-lease", "inspect", "--todo-id", todo)
    assert current_lease["source_authority"] == f"{provider}_v0"
    assert current_lease["lease"] == released["lease"]
    fixture.cli("todo", "add", "--role", "agent", "--text", "Later lease-import write",
        "--operation-id", "later-lease-import", "--claimed-by", "agent-a")
    current = provider_todos(receiver, fixture)
    assert {row["text"] for row in current["todos"]} == {"Original leased task", "Later lease-import write"}
    fixture.state.unlink()
    replay = cold_cli(fixture, receiver_package, "recover-import", "--operation-id", "lease-import",
        "--plan-sha256", plan["plan_sha256"], "--execute")
    assert replay["status"] == "replayed" and replay["operation_id"] == applied["operation_id"]
    assert provider_todos(receiver, fixture) == current
    assert fixture.cli("task-lease", "inspect", "--todo-id", todo)["lease"] == released["lease"]
    assert path.read_bytes() == settled and originals(fixture.runtime / "authority-shadow") == archived
    with tarfile.open(fresh_backup["archive_path"]) as archive:
        assert archive.extractfile("runtime-root/" + str(path.relative_to(fixture.runtime))).read() == settled
        for relative, data in archived.items():
            assert archive.extractfile("runtime-root/authority-shadow/" + relative).read() == data


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_unproved_original_archival_then_cold_import_keeps_history_inert(
    tmp_path, monkeypatch, receiver, receiver_package, provider,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = cold_workspace(tmp_path / "project")
    fixture.crash("before_marker", "handoff-mode", "set", "--mode", "soft_claim")
    fixture.cli("handoff-mode", "set", "--mode", "hard_lease")
    source_todo = fixture.add("Later source requirement")["todo_id"]
    pending_backup = backup(fixture, "ambiguous-original")
    before = originals(fixture.runtime)
    result = drain(receiver, fixture)
    assert result["ok"] is False and result["reason_code"] == "outbox_source_unproved"
    assert originals(fixture.runtime) == before
    inventory = inspect(fixture)
    source = fixture.state.read_bytes()
    artifacts = inventory["capture"]["artifacts"]
    candidate = Path(artifacts["runtime_store"]["path"]).read_bytes()
    pending = originals(Path(artifacts["outbox"]["path"]))
    rejected = cold_cli(fixture, receiver_package, "prepare-import", "--operation-id", "ambiguous-import",
        "--backup-manifest", pending_backup["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease", success=False)
    assert rejected["reason_code"] == "cold_import_capture_requires_disposition", rejected
    assert originals(fixture.runtime) == before and fixture.state.read_bytes() == source
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
    archived = originals(fixture.runtime / "authority-shadow")
    fresh_backup = backup(fixture, "archived-unproved-original")
    plan = cold_cli(fixture, receiver_package, "prepare-import", "--operation-id", "ambiguous-import",
        "--backup-manifest", fresh_backup["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease")
    applied = cold_cli(fixture, receiver_package, "apply-import", "--operation-id", "ambiguous-import",
        "--plan-sha256", plan["plan_sha256"], "--writers-stopped", "--execute")
    assert applied["status"] == "applied" and applied["execution_authority_granted"] is False
    imported = provider_todos(receiver, fixture)
    assert imported["source_authority"] == f"{provider}_v0"
    assert [(row["todo_id"], row["text"]) for row in imported["todos"]] == [(source_todo, "Later source requirement")]
    # The later current source, not the candidate or ambiguous queue, is the
    # import basis. Unproved entries remain archived and never become receipts.
    assert Path(rolled["candidate_archive_path"]).read_bytes() == candidate
    assert originals(Path(rolled["outbox_archive_path"])) == pending
    fixture.cli("todo", "add", "--role", "agent", "--text", "Later canonical write",
        "--operation-id", "after-ambiguous-import", "--claimed-by", "agent-a")
    current = provider_todos(receiver, fixture)
    assert {row["text"] for row in current["todos"]} == {"Later source requirement", "Later canonical write"}
    fixture.state.unlink()
    recovered = cold_cli(fixture, receiver_package, "recover-import", "--operation-id", "ambiguous-import",
        "--plan-sha256", plan["plan_sha256"], "--execute")
    assert recovered["status"] == "replayed" and recovered["operation_id"] == applied["operation_id"]
    assert provider_todos(receiver, fixture) == current
    assert originals(fixture.runtime / "authority-shadow") == archived
    assert originals(Path(rolled["outbox_archive_path"])) == pending
    for saved in (pending_backup, fresh_backup):
        with tarfile.open(saved["archive_path"]) as archive:
            for relative, data in pending.items():
                prefix = ("runtime-root/authority-shadow/outbox/" + fixture.goal if saved == pending_backup
                          else "runtime-root/" + str(Path(rolled["outbox_archive_path"]).relative_to(fixture.runtime)))
                assert archive.extractfile(prefix + "/" + relative).read() == data


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("window,committed", [
    ("before_replace", False), ("before_marker", True),
    ("before_commit", True), ("after_commit", True),
])
def test_original_outbox_disposition_then_cold_import_preserves_receipts_and_later_writes(
    tmp_path, monkeypatch, receiver, receiver_package, provider, window, committed,
):
    """Join original effect recovery to cold import, without replaying history.

    Crash only disposable source writers. The receiver uses the shipped owners
    with the four normal Python producers absent; the real CLI imports the
    settled source, including when the originating interpreter is a wheel.
    """
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = cold_workspace(tmp_path / "project")
    fixture.crash(window, "todo", "add", "--role", "agent", "--text", "Interrupted original")
    source = fixture.state.read_bytes()
    partition = fixture.runtime / "authority-shadow/outbox" / fixture.goal / "todos"
    prepared_path = next(partition.glob("*.prepared.json"))
    prepared_bytes = prepared_path.read_bytes()
    original_entry = json.loads(prepared_bytes)

    pending_backup = backup(fixture, "original-pending")
    before = originals(fixture.runtime)
    refused = cold_cli(fixture, receiver_package, "prepare-import", "--operation-id", "settled-import",
        "--backup-manifest", pending_backup["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease", success=False)
    assert refused["reason_code"] == "cold_import_capture_requires_disposition", refused
    assert originals(fixture.runtime) == before
    assert not list(fixture.runtime.rglob("writer-fence.json"))
    assert not (fixture.runtime / "authority").exists()

    drained = drain(receiver, fixture)
    assert drained["ok"], drained
    assert fixture.state.read_bytes() == source
    inventory = inspect(fixture)
    proof = receiver("coordination.runtime_shadow.outbox_read", {
        "schema_version": LOCAL_AUTHORITY_SHADOW_READ_REQUEST_SCHEMA,
        "runtime_root": str(fixture.runtime), "goal_id": fixture.goal, "store_kind": "runtime_shadow",
        "scan_after_cursor": None, "scan_limit": 0, "read_model": "proof",
        "receipt_operation_id": original_entry["entry_id"],
    })
    receipt = proof["proof"]["receipt"]["receipts"][0]
    assert receipt["entry_id"] == original_entry["entry_id"]
    assert receipt["no_op"] is (not committed)
    assert receipt["resolution"] == ({
        "before_replace": "abandoned", "before_marker": "committed_proven_by_readback",
        "before_commit": "committed", "after_commit": "committed",
    }[window])
    # Draining an empty queue is insufficient: capture must be stopped through
    # its own revision-bound lifecycle, retaining the original receipt bytes.
    settled_backup = backup(fixture, "drained-active")
    before = originals(fixture.runtime)
    refused = cold_cli(fixture, receiver_package, "prepare-import", "--operation-id", "settled-import",
        "--backup-manifest", settled_backup["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease", success=False)
    assert refused["reason_code"] == "cold_import_capture_requires_disposition", refused
    assert originals(fixture.runtime) == before
    request = {"schema_version": "loopx_coordination_runtime_shadow_rollback_v0",
        "runtime_root": str(fixture.runtime), "goal_id": fixture.goal,
        "operation_id": "retain-settled-original", "expected_bootstrap_operation_id": None,
        "expected_provider_revision": inventory["capture"]["runtime_shadow_readback"]["provider_revision"],
        "projection": inventory["projection"], "source_snapshot": inventory["source_snapshot"]}
    rolled = receiver("coordination.runtime_shadow.rollback", request)
    assert rolled["status"] == "applied", rolled
    candidate = Path(rolled["candidate_archive_path"]).read_bytes()
    retained = originals(fixture.runtime / "authority-shadow")
    assert receiver("coordination.runtime_shadow.rollback", request)["status"] == "replayed"
    assert originals(fixture.runtime / "authority-shadow") == retained
    final_backup = backup(fixture, "settled-before-import")
    plan = cold_cli(fixture, receiver_package, "prepare-import", "--operation-id", "settled-import",
        "--backup-manifest", final_backup["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease")
    assert plan["status"] == "prepared", plan
    applied = cold_cli(fixture, receiver_package, "apply-import", "--operation-id", "settled-import",
        "--plan-sha256", plan["plan_sha256"], "--writers-stopped", "--execute")
    assert applied["status"] == "applied" and applied["execution_authority_granted"] is False
    assert applied["operation_id"] != original_entry["entry_id"]
    assert fixture.state.read_bytes() == source
    later = fixture.cli("todo", "add", "--role", "agent", "--text", "Later canonical write",
        "--operation-id", "later-write", "--claimed-by", "agent-a")
    # Independent complete provider read, rather than the active-Todo CLI view.
    current = provider_todos(receiver, fixture)
    assert current["source_authority"] == f"{provider}_v0"
    assert {row["text"] for row in current["todos"]} == (
        {"Interrupted original", "Later canonical write"} if committed else {"Later canonical write"})
    assert len(current["todos"]) == (2 if committed else 1)
    assert later["todo_id"] in {row["todo_id"] for row in current["todos"]}
    fixture.state.unlink()
    replay = cold_cli(fixture, receiver_package, "recover-import", "--operation-id", "settled-import",
        "--plan-sha256", plan["plan_sha256"], "--execute")
    assert replay["status"] == "replayed" and replay["operation_id"] == applied["operation_id"]
    assert provider_todos(receiver, fixture) == current
    assert Path(rolled["candidate_archive_path"]).read_bytes() == candidate
    assert originals(fixture.runtime / "authority-shadow") == retained
    # The original queue and receipts have their own saved history; import does
    # not convert them into a new queue or silently discard the pending backup.
    with tarfile.open(pending_backup["archive_path"]) as archive:
        member = "runtime-root/" + str(prepared_path.relative_to(fixture.runtime))
        assert archive.extractfile(member).read() == prepared_bytes
    with tarfile.open(final_backup["archive_path"]) as archive:
        for relative, data in retained.items():
            assert archive.extractfile("runtime-root/authority-shadow/" + relative).read() == data
