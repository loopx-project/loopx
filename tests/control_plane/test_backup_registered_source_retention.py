"""Whole-source backup is distinct from canonical projection or reactivation."""
import json
import hashlib
import os
import sqlite3
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest
import loopx

from loopx.control_plane.effect_runtime import restart_effect_runtime
from loopx.control_plane.coordination.local_authority_shadow_adapter import read_local_authority_shadow
from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.shadow_e2e_fixture import workspace


def extract_verified_backup(archive_path, destination):
    """Check the saved member witness independently before using copied bytes."""
    with tarfile.open(archive_path) as archive:
        archive.extractall(destination, filter="data")
    manifest = json.loads((destination / "manifest.json").read_bytes())
    for member in manifest["execution"]["file_members"]:
        data = (destination / member["archive_path"]).read_bytes()
        assert len(data) == member["size_bytes"]
        assert hashlib.sha256(data).hexdigest() == member["sha256"]


@pytest.fixture(autouse=True)
def isolated_runtime(tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    yield
    restart_effect_runtime()


@pytest.mark.parametrize("absolute_routes", [False, True])
@pytest.mark.parametrize("sqlite_history", [False, True])
def test_real_cli_retains_registered_cold_source_and_raw_history(
    tmp_path, absolute_routes, sqlite_history,
):
    project = tmp_path / "project"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    state = project / "history/OLD_GOAL.md"
    state.parent.mkdir()
    original_state = (
        "# Original Goal\n\n## Agent Todos\n- [ ] unfinished\n\n"
        "## Completed Work Archive\n- [x] unreferenced completion\n"
        "  metadata: {\"unknown\":null,\"disabled\":false}\n"
    ).encode()
    state.write_bytes(original_state)
    source_registry = project / "settings/source.json"
    source_registry.parent.mkdir()
    goal = {"id": "old-goal", "repo": str(project),
            "state_file": str(state) if absolute_routes else "history/OLD_GOAL.md",
            "extension": {"unknown": None, "disabled": False}}
    source_registry.write_text(json.dumps({"goals": [goal]}))
    registry.write_text(json.dumps({"goals": [{**goal, "source_registry":
        str(source_registry) if absolute_routes else "settings/source.json"}]}))
    runtime = tmp_path / "runtime"
    raw_files = {
        "leases/old-goal/orphan.json": b'{"todo_id":"unreferenced","status":"released"}',
        "outbox/old-goal/pending.json": b'{"status":"prepared","original_operation":"old"}',
        "receipts/old-goal/original.json": b'{"result":{"unknown":null},"original":true}',
    }
    for relative, content in raw_files.items():
        path = runtime / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    database = runtime / "history.data"
    if sqlite_history:
        with sqlite3.connect(database) as writer:
            writer.execute("CREATE TABLE original_receipts(operation TEXT PRIMARY KEY, result TEXT)")
            writer.execute("INSERT INTO original_receipts VALUES('original', '{\"unknown\":null}')")
            writer.commit()
    args = [sys.executable, "-m", "loopx.cli", "--format", "json",
            "--runtime-root", str(runtime), "backup-state", "--project", str(project),
            "--output-dir", str(tmp_path / "backups"), "--backup-id", "cold-source",
            "--current-project-only", "--no-skills", "--no-automations", "--execute"]
    result = subprocess.run(args, env={**os.environ, "LOOPX_USAGE_PING": "0"},
                            capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    # Independently extract inert bytes: no import, lease adoption or activation.
    restored = tmp_path / "isolated-copy"
    with tarfile.open(payload["archive_path"]) as archive:
        archive.extractall(restored, filter="data")
    expected = {"registry_active_state:old-goal": original_state,
                "registry_source_registry:old-goal": source_registry.read_bytes()}
    routes = {row["key"]: row["archive_path"] for row in payload["included"]}
    for key, content in expected.items():
        assert key in routes, f"Registered source omitted: {key}"
        assert (restored / routes[key]).read_bytes() == content
    for relative, content in raw_files.items():
        assert (restored / "runtime-root" / relative).read_bytes() == content
        assert (runtime / relative).read_bytes() == content
    if sqlite_history:
        with sqlite3.connect(restored / "runtime-root/history.data") as copy:
            assert copy.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert copy.execute("SELECT * FROM original_receipts").fetchall() == [
                ("original", '{"unknown":null}')]
    assert state.read_bytes() == original_state


def test_current_project_inventory_reports_missing_routes_without_following_other_projects(tmp_path):
    project, other = tmp_path / "selected", tmp_path / "other"
    registry = project / ".loopx/registry.json"
    registry.parent.mkdir(parents=True)
    other.mkdir()
    (other / "secret.md").write_text("belongs to another project")
    registry.write_text(json.dumps({"goals": [
        {"id": "selected", "repo": str(project), "state_file": "missing.md",
         "source_registry": "missing-registry.json"},
        {"id": "other", "repo": str(other), "state_file": "secret.md"},
    ]}))
    plan = build_state_backup_plan(project=project, runtime_root=tmp_path / "runtime",
        include_registry_projects=False, include_skills=False, include_automations=False)
    missing = {row["key"] for row in plan["missing"]}
    assert {"registry_active_state:selected", "registry_source_registry:selected"} <= missing
    assert not any(row["key"].endswith(":other") for row in plan["included"] + plan["missing"])


def test_global_inventory_still_follows_registered_other_project(tmp_path):
    project, other, runtime = tmp_path / "selected", tmp_path / "other", tmp_path / "runtime"
    other.mkdir()
    runtime.mkdir()
    state = other / "custom.md"
    state.write_text("registered on this host")
    (runtime / "registry.global.json").write_text(json.dumps({"goals": [
        {"id": "other", "repo": str(other), "state_file": str(state)}]}))
    plan = build_state_backup_plan(project=project, runtime_root=runtime,
        include_skills=False, include_automations=False)
    assert any(row["key"] == "registry_active_state:other" for row in plan["included"])


def test_backup_retains_original_registry_outside_conventional_directories(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    registry = tmp_path / "original-registry.json"
    original = b'{"goals": [], "common_runtime_root": "retained route"}\n'
    registry.write_bytes(original)
    result = execute_state_backup_plan(build_state_backup_plan(project=project, runtime_root=runtime,
        output_dir=tmp_path / "saved", backup_id="original-registry", include_automations=False,
        include_skills=False, include_registry_projects=False, registry_path=registry))
    assert result["ok"]
    manifest = json.loads(Path(result["manifest_path"]).read_text())
    source = next(item for item in manifest["included"] if item["source_path"] == str(registry))
    with tarfile.open(result["archive_path"], "r:gz") as archive:
        assert archive.extractfile(source["archive_path"]).read() == original


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cold_import_then_new_writes_full_backup_and_independent_history_readback(tmp_path, provider):
    """Recover inert bytes and real provider history, without activating a copy.

    The old source includes actual capture/rollback and released lease receipts.
    A canonical archive alone cannot witness those original files. All commands
    run against disposable Goals, including when invoked by a wheel interpreter.
    """
    fixture = workspace(tmp_path / "source", bootstrap=False)
    project_registry = fixture.state.parent / ".loopx/registry.json"
    project_registry.parent.mkdir()
    fixture.registry.rename(project_registry)
    fixture.registry = project_registry
    fixture.cli("coordination-shadow", "bootstrap", "--execute")
    todo_id = fixture.add("Original requirement before cold import")["todo_id"]
    lease = fixture.cli("task-lease", "acquire", "--todo-id", todo_id, "--owner", "agent-a",
                        "--idempotency-key", "old-execution")["lease"]
    released = fixture.cli("task-lease", "release", "--todo-id", todo_id, "--owner", "agent-a",
                           "--idempotency-key", "old-execution", "--expected-version", str(lease["version"]))["lease"]
    revision = fixture.cli("coordination-shadow", "inspect")["inspection"]["provider_revision"]
    fixture.cli("coordination-shadow", "rollback", "--provider-revision", revision, "--execute")
    archived_body = "完整且未被引用的历史要求 " * 100
    with fixture.state.open("a", encoding="utf-8") as stream:
        stream.write("\n## Completed Work Archive\n\n- [x] " + archived_body + "\n"
                     "  <!-- loopx:todo todo_id=todo_unreferenced role=agent evidence=original note=retained -->\n")
    registry_document = json.loads(fixture.registry.read_text())
    registry_document["goals"][0]["extension"] = {"unknown": None, "disabled": False, "number": 1}
    fixture.registry.write_text(json.dumps(registry_document))
    original_state = fixture.state.read_bytes()
    historical = {str(path.relative_to(fixture.runtime)): path.read_bytes()
                  for root in (fixture.runtime / "authority-shadow", fixture.runtime / "authority-transition",
                               fixture.runtime / "goals/goal-e2e/task-leases")
                  for path in root.rglob("*") if path.is_file() and not path.name.endswith(".lock")}
    assert historical and any("rollback" in path for path in historical)

    def command(*args, runtime=fixture.runtime, registry=fixture.registry, success=True):
        child = subprocess.run([sys.executable, "-c",
            "import loopx,runpy,sys; print(loopx.__file__,file=sys.stderr); runpy.run_module('loopx.cli',run_name='__main__')",
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *map(str, args)],
            cwd=tmp_path, env={**os.environ, "LOOPX_USAGE_PING": "0"},
            capture_output=True, text=True, timeout=90)
        assert str(Path(loopx.__file__).resolve()) in child.stderr
        assert (child.returncode == 0) is success, child.stdout + child.stderr
        return json.loads(child.stdout)

    def backup(name):
        return command("backup-state", "--project", fixture.state.parent, "--output-dir", tmp_path / "backups",
                       "--backup-id", name, "--current-project-only", "--no-skills", "--no-automations", "--execute")

    def authority(root):
        # Fresh selected provider, no registry adoption or Markdown projection.
        module = Path(loopx.__file__).parent / "control_plane/coordination/local_authority_provider.ts"
        script = (f"import {{openLocalAuthorityStoreHandle}} from {json.dumps(module.as_uri())};"
                  f"const {{store}}=await openLocalAuthorityStoreHandle({json.dumps(str(root))},'goal-e2e');"
                  "console.log(JSON.stringify({head:await store.loadAuthority(), history:await store.scanCommitted(null,1000)}));")
        result = subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e", script],
                                cwd=tmp_path, capture_output=True, text=True, check=True, timeout=45)
        return json.loads(result.stdout)

    saved = backup("before-import")
    prepared = fixture.cli("coordination-shadow", "prepare-import", "--operation-id", "cold-original",
        "--backup-manifest", saved["manifest_path"], "--provider", provider,
        "--target-handoff-mode", "hard_lease")["cold_import"]
    assert prepared["status"] == "prepared", prepared
    applied = fixture.cli("coordination-shadow", "apply-import", "--operation-id", "cold-original",
        "--plan-sha256", prepared["plan_sha256"], "--writers-stopped", "--execute")["cold_import"]
    assert applied["status"] == "applied" and not applied["execution_authority_granted"]
    later = fixture.cli("todo", "add", "--role", "agent", "--text", "New canonical data after import",
                        "--operation-id", "after-import", "--claimed-by", "agent-a")
    assert later["ok"] is True
    observed = authority(fixture.runtime)
    expected = {row["todo_id"]: row for row in observed["head"]["head"]["todos"]}
    assert {todo_id, "todo_unreferenced", later["todo_id"]} <= expected.keys()
    assert expected["todo_unreferenced"]["text"] == archived_body.strip()
    assert expected["todo_unreferenced"]["evidence"] == "original"
    assert fixture.cli("task-lease", "inspect", "--todo-id", todo_id)["lease"] == released

    authority_archive = tmp_path / "canonical.ndjson"
    command("authority-archive", "export", "--goal-id", fixture.goal, "--archive", authority_archive)
    verified = command("authority-archive", "verify", "--archive", authority_archive)["archive"]
    assert int(verified["commits"]) >= 2
    post_import_state = fixture.state.read_bytes()
    saved = backup("after-import")
    assert saved["ok"] is True
    copy = tmp_path / "independent-empty-recovery"
    assert not copy.exists()
    extract_verified_backup(saved["archive_path"], copy)
    routes = {row["key"]: row["archive_path"] for row in saved["included"]}
    assert (copy / routes["registry_active_state:goal-e2e"]).read_bytes() == post_import_state
    assert (copy / routes["configuration_source_registry"]).read_bytes() == fixture.registry.read_bytes()
    configuration = json.loads((copy / "configuration-backup.json").read_bytes())
    assert configuration["data"]["goals"][0]["goal_configuration"]["extension"] == registry_document["goals"][0]["extension"]
    for relative, data in historical.items():
        assert (copy / "runtime-root" / relative).read_bytes() == data
    # The original source and its later projection have separate saved copies.
    with tarfile.open(Path(saved["archive_path"]).parent / "loopx-state-before-import.tar.gz") as archive:
        original_manifest = json.load(archive.extractfile("manifest.json"))
        original_route = next(row["archive_path"] for row in original_manifest["included"]
                              if row["key"] == "registry_active_state:goal-e2e")
        assert archive.extractfile(original_route).read() == original_state

    # Remove access to every source route: the recovery cannot accidentally read
    # the live registry, original store or old absolute paths in historical data.
    fixture.state.parent.rename(tmp_path / "unavailable-original")
    restored_runtime = copy / "runtime-root"
    recovered_registry = copy / routes["configuration_source_registry"]
    audited = command("authority-archive", "audit", "--goal-id", fixture.goal, "--archive", authority_archive,
        "--archive-sha256", verified["archive_sha256"], runtime=restored_runtime, registry=recovered_registry)
    assert audited["audit"]["status"] == "matched" and audited["audit"]["scope"] == "exact"
    assert audited["audit"]["compared_commits"] == verified["commits"]
    recovered = authority(restored_runtime)
    assert recovered == observed
    assert len(recovered["history"]["transactions"]) == int(verified["commits"])
    assert next(row for row in recovered["head"]["head"]["leases"] if row["todo_id"] == todo_id) == released
    assert recovered["history"]["transactions"][0]["operation_id"] == applied["operation_id"]
    # This is isolated data recovery, not an alternate live writer or Host grant.
    assert not fixture.state.parent.exists()
    selected_directory = restored_runtime / "authority" / f"{provider}-v0"
    selected_directory.rename(tmp_path / "unavailable-selected-provider")
    rejected = command("authority-archive", "audit", "--goal-id", fixture.goal, "--archive", authority_archive,
        "--archive-sha256", verified["archive_sha256"], runtime=restored_runtime,
        registry=recovered_registry, success=False)
    assert rejected["status"] == "failed" and rejected["authority_changed"] is False
    assert not selected_directory.exists()
    if provider == "sqlite":
        assert not (restored_runtime / "authority/file-v0").exists()


@pytest.mark.parametrize(
    "partition,policy,window",
    [
        ("todos", policy, window)
        for policy in ("soft_claim", "hard_lease")
        for window in (
            "before_replace",
            "before_marker",
            "before_commit",
            "after_commit",
        )
    ]
    + [
        ("leases", "hard_lease", window) for window in ("before_commit", "after_commit")
    ],
)
@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_restored_source_outbox_resumes_once_before_cold_import(
    tmp_path, partition, policy, window, provider
):
    """Restore only the owned runtime at its original binding before import.

    Original source paths remain; this does not reactivate a whole Goal.
    """
    fixture = workspace(tmp_path / "project", bootstrap=False)
    registry = fixture.state.parent / ".loopx/registry.json"
    registry.parent.mkdir()
    fixture.registry.rename(registry)
    fixture.registry = registry
    fixture.state.write_text(
        fixture.state.read_text().replace(
            "handoff_mode: hard_lease", f"handoff_mode: {policy}"
        )
    )
    fixture.cli("coordination-shadow", "bootstrap", "--execute")
    first = fixture.add("Keep the original requirement")["todo_id"]
    source_before = fixture.state.read_bytes()
    lease_identity = (
        "--owner",
        "agent-a",
        "--idempotency-key",
        "original-source-lease",
    )
    interrupted = (
        (
            "task-lease",
            "acquire",
            "--todo-id",
            first,
            *lease_identity,
            "--ttl-seconds",
            "3600",
        )
        if partition == "leases"
        else (
            "todo",
            "add",
            "--role",
            "agent",
            "--text",
            "Retain the interrupted original operation",
        )
    )
    fixture.crash(window, *interrupted)
    source_after = fixture.state.read_bytes()
    source_unchanged = partition == "leases" or window == "before_replace"
    assert (source_after == source_before) == source_unchanged
    lease_path = (
        fixture.runtime / "goals" / fixture.goal / "task-leases" / (first + ".json")
    )
    lease_bytes = lease_path.read_bytes() if partition == "leases" else None
    original_lease = json.loads(lease_bytes) if lease_bytes is not None else None
    outbox = fixture.runtime / "authority-shadow/outbox" / fixture.goal / partition
    prepared = next(outbox.glob("*.prepared.json"))
    operation = json.loads(prepared.read_bytes())["entry_id"]
    original = {
        str(path.relative_to(fixture.runtime)): path.read_bytes()
        for path in fixture.runtime.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    }
    registry_bytes = fixture.registry.read_bytes()
    inventory = fixture.cli("coordination-shadow", "inspect-source")["source_inventory"]
    assert (
        inventory["outbox_reconciliation_verified"] is False
        and inventory["writer_stop_verified"] is False
    )
    assert inventory["capture"]["management_state"]["status"] == "active"
    assert original == {
        str(path.relative_to(fixture.runtime)): path.read_bytes()
        for path in fixture.runtime.rglob("*")
        if path.is_file() and not path.name.endswith(".lock")
    }

    def backup(name):
        child = subprocess.run(
            [
                *fixture.command,
                "--registry",
                str(fixture.registry),
                "--runtime-root",
                str(fixture.runtime),
                "--format",
                "json",
                "backup-state",
                "--project",
                str(fixture.state.parent),
                "--output-dir",
                str(tmp_path / "saved"),
                "--backup-id",
                name,
                "--current-project-only",
                "--no-skills",
                "--no-automations",
                "--execute",
            ],
            cwd=fixture.cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert child.returncode == 0, child.stdout + child.stderr
        return json.loads(child.stdout)

    saved = backup("interrupted-source")
    copy = tmp_path / "independent-copy"
    extract_verified_backup(saved["archive_path"], copy)
    for relative, data in original.items():
        assert (copy / "runtime-root" / relative).read_bytes() == data
    routes = {row["key"]: row["archive_path"] for row in saved["included"]}
    assert (
        copy / routes["registry_active_state:goal-e2e"]
    ).read_bytes() == source_after
    assert (
        copy / routes["configuration_source_registry"]
    ).read_bytes() == registry_bytes

    # The killed writer is owned by this fixture. Stop its remaining transport
    # before replacing only its disposable runtime at the original binding path.
    restart_effect_runtime()
    fixture.runtime.rename(tmp_path / "quarantined-original-runtime")
    shutil.copytree(copy / "runtime-root", fixture.runtime)
    assert (
        fixture.state.read_bytes() == source_after
        and fixture.registry.read_bytes() == registry_bytes
    )
    resumed = fixture.drain()
    assert resumed["ok"] is True and resumed["outcome"] == "drained", resumed
    assert resumed["pending_after"] == 0 and resumed["prepared_only_after"] == 0
    assert resumed["no_op"] == (1 if window == "before_replace" else 0)
    assert not list(outbox.glob("*.prepared.json")) and not list(
        outbox.glob("*.committed.json")
    )
    proof = read_local_authority_shadow(
        runtime_root=fixture.runtime, goal_id=fixture.goal, scan_limit=100
    )["proof"]
    transactions = proof["transactions"]
    # Bootstrap, original requirement and exactly one interrupted position.
    assert len(transactions) == 3, transactions
    assert transactions[-1]["receipts"][0]["entry_id"] == operation
    assert transactions[-1]["receipts"][0]["no_op"] == (window == "before_replace")
    head = transactions[-1]["projection"]
    todos = {row["todo_id"]: row for row in head["todos"]}
    assert first in todos and len(todos) == (1 if source_unchanged else 2)
    if partition == "leases":
        assert head["leases"] == [original_lease]
        assert lease_path.read_bytes() == lease_bytes
        recovered_lease = fixture.cli(
            "task-lease",
            "acquire",
            "--todo-id",
            first,
            *lease_identity,
            "--ttl-seconds",
            "3600",
        )
        assert (
            recovered_lease["idempotent"] and recovered_lease["lease"] == original_lease
        )
        assert lease_path.read_bytes() == lease_bytes
    assert fixture.state.read_bytes() == source_after
    revision = fixture.cli("coordination-shadow", "inspect")["inspection"][
        "provider_revision"
    ]
    again = fixture.drain()
    assert again["ok"] and again["outcome"] == "nothing_pending"
    assert (
        read_local_authority_shadow(
            runtime_root=fixture.runtime, goal_id=fixture.goal, scan_limit=100
        )["proof"]["transactions"]
        == transactions
    )

    rollback = fixture.cli(
        "coordination-shadow", "rollback", "--provider-revision", revision, "--execute"
    )["rollback"]
    assert rollback["status"] == "applied"
    if partition == "leases":
        # Recovering an outbox neither releases nor renews its execution grant.
        # A separate native owner settlement is required before cold import.
        unsettled = backup("unsettled-lease")
        refused = fixture.cli(
            "coordination-shadow",
            "prepare-import",
            "--operation-id",
            "unsettled-import",
            "--backup-manifest",
            unsettled["manifest_path"],
            "--provider",
            provider,
            "--target-handoff-mode",
            policy,
            success=False,
        )["cold_import"]
        assert refused["reason_code"] == "cold_import_lease_requires_settlement"
        assert not list(fixture.runtime.rglob("writer-fence.json"))
        assert lease_path.read_bytes() == lease_bytes
        released = fixture.cli(
            "task-lease",
            "release",
            "--todo-id",
            first,
            *lease_identity,
            "--expected-version",
            str(original_lease["version"]),
        )["lease"]
        assert released["status"] == "released"
    settled = backup("settled-source")
    plan = fixture.cli(
        "coordination-shadow",
        "prepare-import",
        "--operation-id",
        "original-reviewed-import",
        "--backup-manifest",
        settled["manifest_path"],
        "--provider",
        provider,
        "--target-handoff-mode",
        policy,
    )["cold_import"]
    assert (
        plan["status"] == "prepared" and plan["complete_goal_backup_verified"] is False
    )
    digest = plan["plan_sha256"]
    imported = fixture.cli(
        "coordination-shadow",
        "apply-import",
        "--operation-id",
        "original-reviewed-import",
        "--plan-sha256",
        digest,
        "--writers-stopped",
        "--execute",
    )["cold_import"]
    assert (
        imported["status"] == "applied"
        and imported["execution_authority_granted"] is False
    )
    if partition == "leases":
        assert (
            fixture.cli("task-lease", "inspect", "--todo-id", first)["lease"]
            == released
        )
        fresh_lease = fixture.cli(
            "task-lease",
            "acquire",
            "--todo-id",
            first,
            "--owner",
            "agent-a",
            "--idempotency-key",
            "fresh-canonical-lease",
            "--expected-version",
            str(released["version"]),
            "--ttl-seconds",
            "3600",
        )["lease"]
        assert fresh_lease["version"] == released["version"] + 1
        assert fresh_lease["lease_epoch"] == released["lease_epoch"] + 1
    created = fixture.cli(
        "todo",
        "add",
        "--role",
        "agent",
        "--text",
        "New canonical data after recovered source",
        "--operation-id",
        "later-new-data",
        "--claimed-by",
        "agent-a",
    )
    assert created["ok"]
    before = fixture.cli("todo", "list")["todos"]
    recovered = fixture.cli(
        "coordination-shadow",
        "recover-import",
        "--operation-id",
        "original-reviewed-import",
        "--plan-sha256",
        digest,
        "--execute",
    )["cold_import"]
    assert (
        recovered["status"] == "replayed"
        and recovered["execution_authority_granted"] is False
    )
    if partition == "leases":
        assert (
            fixture.cli("task-lease", "inspect", "--todo-id", first)["lease"]
            == fresh_lease
        )
    assert fixture.cli("todo", "list")["todos"] == before
    assert first in {row["todo_id"] for row in before} and created["todo_id"] in {
        row["todo_id"] for row in before
    }
