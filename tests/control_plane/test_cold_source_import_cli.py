"""Real cold import CLI with native backup, source adapter and File/SQLite.

Synthetic disposable Goals; the cold-import command runs with the old normal
producers physically absent. This does not qualify attached-Host stop, removal
of their remaining callers, or full Goal history recovery.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime
from loopx.control_plane.coordination.cold_source_backup import read_cold_source_backup
from loopx.state_backup import build_state_backup_plan, execute_state_backup_plan

REPO = Path(__file__).resolve().parents[2]


def canonical_todos(runtime, receiver, env, cwd):
    child = subprocess.run([sys.executable, "-c",
        "import loopx,json,sys; print(loopx.__file__,file=sys.stderr); "
        "from loopx.control_plane.effect_runtime import effect_runtime_result; "
        "print(json.dumps(effect_runtime_result('coordination.local_authority.todo_list',json.load(sys.stdin))))"],
        input=json.dumps({"schema_version": "loopx_local_coordination_todo_list_request_v0",
            "runtime_root": str(runtime), "goal_id": "cold", "role": None, "status": None,
            "todo_id": None, "agent_id": None, "limit": None, "include_leases": True}),
        cwd=cwd, env=env, capture_output=True, text=True, check=True, timeout=60)
    assert str(receiver / "loopx/__init__.py") in child.stderr
    result = json.loads(child.stdout)
    assert result["status"] == "loaded", result
    return result


def workspace(tmp_path, monkeypatch, *, storage_target=None):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime = tmp_path / "project", tmp_path / "runtime"
    registry = project / ".loopx/registry.json"
    state = project / ".local/goals/cold/ACTIVE_GOAL_STATE.md"
    state.parent.mkdir(parents=True)
    registry.parent.mkdir(parents=True)
    body = " ".join(["Complete cold source requirement"] * 25)
    state.write_text("---\ngoal_id: cold\nhandoff_mode: legacy\n---\n\n## Agent Todo\n\n"
        f"- [ ] {body}\n  <!-- loopx:todo todo_id=todo_current role=agent task_class=advancement_task claimed_by=agent-a note=retained -->\n\n"
        "## Completed Work Archive\n\n- [x] Independently retained archived requirement\n"
        "  <!-- loopx:todo todo_id=todo_archived role=agent task_class=advancement_task evidence=original -->\n")
    (state.parent / "GOAL.md").write_text("Original human objective, never a new execution grant.\n")
    goal = {"id": "cold", "repo": str(project), "state_file": str(state.relative_to(project)),
        "coordination": {"registered_agents": ["agent-a"]}}
    if storage_target is not None:
        goal["coordination"]["storage_target"] = storage_target
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [goal]}))
    runtime.mkdir()
    backup = execute_state_backup_plan(build_state_backup_plan(project=project, runtime_root=runtime,
        output_dir=tmp_path / "backups", backup_id="cold-original", include_automations=False,
        include_skills=False, include_registry_projects=False, registry_path=registry))
    assert backup["ok"]
    receiver = tmp_path / "receiver"
    package = Path(os.environ.get("LOOPX_COLD_IMPORT_PACKAGE", str(REPO / "loopx")))
    shutil.copytree(package, receiver / "loopx", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    env = {**os.environ, "PYTHONPATH": str(receiver)}

    def cli(action, *args, success=True, output_format="json", runtime_override=None):
        child = subprocess.run([sys.executable, "-c",
            "import loopx,runpy,sys; print(loopx.__file__,file=sys.stderr); runpy.run_module('loopx.cli',run_name='__main__')",
            "--registry", str(registry), "--runtime-root", str(runtime_override or runtime), "--format", output_format,
            "coordination-shadow", action, "--goal-id", "cold", "--operation-id", "cold-original", *map(str, args)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert str(receiver / "loopx/__init__.py") in child.stderr
        assert "Traceback" not in child.stderr, child.stderr
        assert (child.returncode == 0) is success, child.stdout + child.stderr
        return json.loads(child.stdout) if output_format == "json" else child.stdout

    return cli, state, backup, body, runtime, receiver, env


@pytest.mark.parametrize("old_producers_present", [True, False])
@pytest.mark.parametrize("configuration", ["absent", "disabled", "retired"])
def test_unpromoted_default_off_lease_lifecycle_without_shadow_producers(
    tmp_path, monkeypatch, old_producers_present, configuration,
):
    _, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    registry = tmp_path / "project/.loopx/registry.json"
    data = json.loads(registry.read_text())
    coordination = data["goals"][0]["coordination"]
    coordination["registered_agents"] = ["agent-a", "agent-b"]
    if configuration == "disabled":
        coordination["runtime_shadow"] = {
            "schema_version": "loopx_coordination_runtime_shadow_config_v0",
            "enabled": False, "provider": "file_v0",
        }
    elif configuration == "retired":
        coordination["authority_shadow"] = {
            "schema_version": "loopx_local_authority_shadow_config_v0",
            "mode": "file_one_way",
        }
    registry.write_text(json.dumps(data))
    state.write_text(state.read_text().replace("handoff_mode: legacy", "handoff_mode: hard_lease")
                     .replace(" claimed_by=agent-a", ""))
    original_state, original_registry = state.read_bytes(), registry.read_bytes()
    if not old_producers_present:
        for name in ("runtime_shadow.py", "runtime_shadow_writer_adapter.py",
                     "local_authority_shadow_adapter.py", "local_authority_shadow_outbox.py"):
            (receiver / "loopx/control_plane/coordination" / name).unlink()

    def lease(action, *args, owner="agent-a", key="original", success=True):
        child = subprocess.run([sys.executable, "-c",
            "import loopx,runpy,sys; print(loopx.__file__,file=sys.stderr); runpy.run_module('loopx.cli',run_name='__main__')",
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "task-lease", action, "--goal-id", "cold", "--todo-id", "todo_current",
            "--owner", owner, "--idempotency-key", key, *map(str, args)],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert str(receiver / "loopx/__init__.py") in child.stderr
        assert "Traceback" not in child.stderr, child.stderr
        assert (child.returncode == 0) is success, child.stdout + child.stderr
        result = json.loads(child.stdout)
        assert "coordination_runtime_shadow_capture" not in result
        assert "coordination_runtime_shadow" not in result
        assert state.read_bytes() == original_state
        assert registry.read_bytes() == original_registry
        assert not (runtime / "authority-shadow").exists()
        assert not any(path.is_file() for path in (runtime / "authority-transition").rglob("*"))
        return result

    acquired = lease("acquire", "--ttl-seconds", 120, "--write-scope", "docs/**")
    assert acquired["acquired"] is True
    version = acquired["lease"]["version"]
    path = Path(acquired["lease_path"])
    original_lease = path.read_bytes()
    replay = lease("acquire", "--ttl-seconds", 120, "--write-scope", "docs/**")
    assert replay["idempotent"] is True and replay["lease"] == acquired["lease"]
    assert path.read_bytes() == original_lease
    rejected = lease("renew", "--expected-version", version + 1, "--ttl-seconds", 180, success=False)
    assert rejected["error_code"] == "version_mismatch"
    assert path.read_bytes() == original_lease
    renewed = lease("renew", "--expected-version", version, "--ttl-seconds", 180)
    assert renewed["lease"]["version"] == version + 1
    assert renewed["lease"]["lease_epoch"] == acquired["lease"]["lease_epoch"]
    transferred = lease("transfer", "--expected-version", version + 1,
                        "--new-owner", "agent-b", "--new-idempotency-key", "receiver",
                        "--ttl-seconds", 180)
    assert transferred["lease"]["owner"] == "agent-b"
    assert transferred["lease"]["version"] == version + 2
    assert transferred["lease"]["lease_epoch"] == acquired["lease"]["lease_epoch"] + 1
    released = lease("release", "--expected-version", version + 2, owner="agent-b", key="receiver")
    assert released["released"] is True and released["lease"]["status"] == "released"
    assert released["lease"]["write_scopes"] == ["docs/**"]
    assert released["lease"]["lease_epoch"] == transferred["lease"]["lease_epoch"]
    retained = path.read_bytes()
    assert lease("release", "--expected-version", version + 2,
                 owner="agent-b", key="receiver")["idempotent"] is True
    assert path.read_bytes() == retained


def test_coordination_command_help_and_rejections_match_full_cli(tmp_path):
    # Independently compare the existing compatibility parser/handler with the
    # selected dispatcher, including diagnostics that deliberately fall back.
    arguments = [["coordination-shadow", "--help"],
        ["coordination-shadow", "prepare-import", "--help"],
        ["coordination-shadow", "apply-import", "--help"],
        ["coordination-shadow", "recover-import", "--help"],
        ["coordination-shadow", "promote", "--help"],
        ["coordination-shadow", "unknown-action"],
        ["coordination-shadow", "prepare-import"],
        ["coordination-shadow", "prepare-import", "--goal-id", "cold", "--operation-id", "original",
         "--backup-manifest", "backup.json", "--provider", "sqlite", "--target-handoff-mode", "legacy"],
        ["coordination-shadow", "recover-import", "--goal-id", "cold", "--operation-id", "original",
         "--plan-sha256", "abc", "--exec"]]
    observations = []
    package = Path(os.environ.get("LOOPX_COLD_IMPORT_PACKAGE", str(REPO / "loopx")))
    for module in ("loopx.entrypoint", "loopx.cli"):
        script = f"""
import contextlib,io,json,sys,loopx
from {module} import main
sys.argv[0] = 'loopx'
observations = []
for argv in {arguments!r}:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = main(argv)
        except SystemExit as exc:
            code = exc.code
    observations.append([code, out.getvalue(), err.getvalue()])
print(json.dumps({{'package': loopx.__file__, 'observations': observations}}))
"""
        child = subprocess.run([sys.executable, "-c", script], cwd=tmp_path,
            env={**os.environ, "PYTHONPATH": str(package.parent)}, capture_output=True, text=True, check=True, timeout=60)
        result = json.loads(child.stdout)
        assert Path(result["package"]).parent == package
        observations.append(result["observations"])
    assert observations[0] == observations[1]
    assert all(row[0] == 0 for row in observations[0][:5])
    assert all(row[0] == 2 for row in observations[0][5:])


@pytest.mark.parametrize("old_producers_present", [True, False])
def test_unconfigured_shadow_inspection_matches_full_cli_without_import(tmp_path, monkeypatch, old_producers_present):
    _, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    if not old_producers_present:
        for name in ("runtime_shadow.py", "runtime_shadow_writer_adapter.py",
                     "local_authority_shadow_adapter.py", "local_authority_shadow_outbox.py"):
            (receiver / "loopx/control_plane/coordination" / name).unlink()
    registry = tmp_path / "project/.loopx/registry.json"
    original = state.read_bytes()
    observations = []
    for module in ("loopx.entrypoint", "loopx.cli"):
        child = subprocess.run([sys.executable, "-c",
            f"import loopx,sys; print(loopx.__file__,file=sys.stderr); from {module} import main; raise SystemExit(main())",
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "coordination-shadow", "inspect", "--goal-id", "cold"],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert str(receiver / "loopx/__init__.py") in child.stderr
        assert "Traceback" not in child.stderr
        assert child.returncode == 1
        observations.append(child.stdout)
    assert observations[0] == observations[1]
    result = json.loads(observations[0])
    assert result["error_code"] == "coordination_shadow_not_enabled"
    assert result["executed"] is False
    assert state.read_bytes() == original
    assert not (runtime / "authority-transition").exists()


@pytest.mark.parametrize("command,action,missing,expected_ok", [
    ("coordination-shadow", "inspect", "runtime_shadow", False),
    ("authority-shadow", "status", "local_authority_shadow_adapter", True),
    ("authority-shadow", "status", "local_authority_shadow_readback", False),
    ("authority-shadow", "drain", "local_authority_shadow_adapter", False),
])
def test_explicit_shadow_action_uses_only_its_required_modules_without_writing(
    tmp_path, monkeypatch, command, action, missing, expected_ok,
):
    _, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    registry = tmp_path / "project/.loopx/registry.json"
    data = json.loads(registry.read_text())
    data["goals"][0]["coordination"]["runtime_shadow"] = {
        "schema_version": "loopx_coordination_runtime_shadow_config_v0",
        "enabled": True, "provider": "file_v0",
    }
    registry.write_text(json.dumps(data))
    (receiver / "loopx/control_plane/coordination" / f"{missing}.py").unlink()
    before = {str(path.relative_to(tmp_path)): path.read_bytes()
              for root in (registry.parent, state.parent, runtime)
              for path in root.rglob("*") if path.is_file()}
    child = subprocess.run([sys.executable, "-c",
        "import loopx,sys; print(loopx.__file__,file=sys.stderr); "
        "from loopx.entrypoint import main; raise SystemExit(main())",
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        command, action, "--goal-id", "cold"], cwd=tmp_path, env=env,
        capture_output=True, text=True, timeout=60)
    assert str(receiver / "loopx/__init__.py") in child.stderr
    assert "Traceback" not in child.stderr, child.stderr
    assert child.returncode == (0 if expected_ok else 1)
    result = json.loads(child.stdout)
    assert result["ok"] is expected_ok
    if not expected_ok:
        assert missing in result["error"]
    after = {str(path.relative_to(tmp_path)): path.read_bytes()
             for root in (registry.parent, state.parent, runtime)
             for path in root.rglob("*") if path.is_file()}
    assert after == before


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("old_producers_present", [True, False])
def test_cold_cli_import_and_source_free_original_recovery(
    tmp_path, monkeypatch, provider, old_producers_present,
):
    cli, state, backup, body, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    if not old_producers_present:
        for path in ("todos.py", "bootstrap.py", "control_plane/coordination/runtime_shadow_writer_adapter.py",
                     "control_plane/coordination/local_authority_shadow_outbox.py",
                     "control_plane/coordination/runtime_shadow.py",
                     "control_plane/coordination/local_authority_shadow_adapter.py",
                     "control_plane/todos/legacy_mutation.py", "control_plane/todos/line_update.py"):
            (receiver / "loopx" / path).unlink()
    original = state.read_bytes()
    prepared = cli("prepare-import", "--backup-manifest", backup["manifest_path"],
        "--provider", provider, "--target-handoff-mode", "hard_lease")["cold_import"]
    assert prepared["status"] == "prepared", prepared
    assert prepared["coordination_source_backup_verified"] is True
    assert prepared["complete_goal_backup_verified"] is False
    digest = prepared["plan_sha256"]
    rendered = cli("prepare-import", "--backup-manifest", backup["manifest_path"],
        "--provider", provider, "--target-handoff-mode", "hard_lease", output_format="markdown")
    assert digest in rendered and prepared["plan_path"] in rendered
    assert Path(prepared["plan_path"]).is_file()
    applied = cli("apply-import", "--plan-sha256", digest, "--writers-stopped", "--execute")["cold_import"]
    assert applied["status"] == "applied", applied
    assert applied["target_provider"] == provider
    assert applied["execution_authority_granted"] is False
    assert state.read_bytes() == original
    # Read the real native owner independently through the receiver package.
    result = canonical_todos(runtime, receiver, env, tmp_path)
    assert result["source_authority"] == f"{provider}_v0"
    assert {row["todo_id"] for row in result["todos"]} == {"todo_current", "todo_archived"}
    assert next(row for row in result["todos"] if row["todo_id"] == "todo_current")["text"] == body
    assert next(row for row in result["todos"] if row["todo_id"] == "todo_archived")["evidence"] == "original"
    state.unlink()
    recovered = cli("recover-import", "--plan-sha256", digest, "--execute")["cold_import"]
    assert recovered["status"] == "replayed", recovered
    assert recovered["cursor"] == "1"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("old_producers_present", [True, False])
@pytest.mark.parametrize("old_target", [False, True], ids=["no-creation-target", "v0-opposite-target"])
def test_cold_import_recovery_preserves_later_canonical_writes(
    tmp_path, monkeypatch, provider, old_producers_present, old_target,
):
    target = ({"schema_version": "loopx_new_goal_storage_target_v0",
        "provider": "sqlite" if provider == "file" else "file"} if old_target else None)
    cli, state, backup, body, runtime, receiver, env = workspace(tmp_path, monkeypatch, storage_target=target)
    if not old_producers_present:
        for path in ("control_plane/todos/legacy_mutation.py", "control_plane/todos/line_update.py",
                     "control_plane/coordination/runtime_shadow.py",
                     "control_plane/coordination/runtime_shadow_writer_adapter.py",
                     "control_plane/coordination/local_authority_shadow_adapter.py",
                     "control_plane/coordination/local_authority_shadow_outbox.py"):
            (receiver / "loopx" / path).unlink()
    prepared = cli("prepare-import", "--backup-manifest", backup["manifest_path"],
        "--provider", provider, "--target-handoff-mode", "hard_lease")["cold_import"]
    digest = prepared["plan_sha256"]
    imported = cli("apply-import", "--plan-sha256", digest,
        "--writers-stopped", "--execute")["cold_import"]
    assert imported["status"] == "applied"
    registry = tmp_path / "project/.loopx/registry.json"

    def run(*args, succeeds=True):
        child = subprocess.run([sys.executable, "-c",
            "import loopx,runpy,sys; print(loopx.__file__,file=sys.stderr); runpy.run_module('loopx.cli',run_name='__main__')",
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json", *args],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=60)
        assert str(receiver / "loopx/__init__.py") in child.stderr
        assert "Traceback" not in child.stderr, child.stderr
        assert (child.returncode == 0) is succeeds, child.stdout + child.stderr
        return json.loads(child.stdout)

    def listing():
        return run("todo", "list", "--goal-id", "cold")

    before = listing()
    update = ["todo", "update", "--goal-id", "cold", "--todo-id", "todo_current",
        "--agent-id", "agent-a", "--note", "Work retained after migration",
        "--update-operation-id", "later-update",
        "--update-expected-provider-revision", before["authority_read"]["provider_revision"]]
    # Importing source history grants no lease to mutate the imported work.
    denied = run(*update, succeeds=False)
    assert denied["error_code"] == "handoff_mode_requires_lease"
    assert listing()["todos"] == before["todos"]
    acquired = run("task-lease", "acquire", "--goal-id", "cold", "--todo-id", "todo_current",
        "--owner", "agent-a", "--idempotency-key", "later-delivery", "--expected-version", "0",
        "--ttl-seconds", "3600", "--write-scope", "tests/**")
    assert acquired["acquired"] is True
    # Lease acquisition advances authority; keep the update CAS current.
    update[-1] = listing()["authority_read"]["provider_revision"]
    proof = ["--task-lease-idempotency-key", "later-delivery",
        "--task-lease-expected-version", str(acquired["lease"]["version"])]
    assert run(*update, *proof)["ok"] is True
    add = ["todo", "add", "--goal-id", "cold", "--role", "agent",
        "--text", "New work after migration", "--operation-id", "later-create"]
    created = run(*add)
    assert created["ok"] is True
    after = listing()
    complete_after = canonical_todos(runtime, receiver, env, tmp_path)
    records = {row["todo_id"]: row for row in complete_after["todos"]}
    assert set(records) == {"todo_current", "todo_archived", created["todo_id"]}
    assert records["todo_current"]["text"] == body
    assert records["todo_current"]["note"] == "Work retained after migration"
    assert records["todo_archived"]["evidence"] == "original"
    assert records[created["todo_id"]]["text"] == "New work after migration"
    assert len(complete_after["leases"]) == 1
    assert complete_after["leases"][0]["todo_id"] == "todo_current"
    assert complete_after["leases"][0]["owner"] == "agent-a"
    reconnect = ["bootstrap", "--project", str(tmp_path / "project"), "--goal-id", "cold",
        "--objective", "Reconnect without replacing imported work", "--no-global-sync"]
    source_bytes = state.read_bytes()
    connected = run(*reconnect)
    assert connected["state_action"] == "kept-existing"
    assert connected["storage_selection"] is None
    assert connected["authority_read"]["provider_revision"] == complete_after["provider_revision"]
    assert connected["authority_read"]["source_authority"] == f"{provider}_v0"
    assert connected["authority_read"]["legacy_fallback_used"] is False
    assert state.read_bytes() == source_bytes
    assert canonical_todos(runtime, receiver, env, tmp_path) == complete_after
    registered = registry.read_bytes()
    registered_goal = json.loads(registered)["goals"][0]
    assert registered_goal["coordination"].get("storage_target") == target
    assert "creation_operation_id" not in registered_goal
    preview = run(*reconnect, "--dry-run")
    assert preview["authority_read"] == connected["authority_read"]
    assert registry.read_bytes() == registered and state.read_bytes() == source_bytes
    for extra in (["--force"], ["--force", "--preserve-todos"],
                  ["--state-file", str(tmp_path / "other-state.md")]):
        refused = run(*reconnect, *extra, succeeds=False)
        assert refused["ok"] is False
        assert registry.read_bytes() == registered and state.read_bytes() == source_bytes
        assert not (tmp_path / "other-state.md").exists()
        assert canonical_todos(runtime, receiver, env, tmp_path) == complete_after
    state.unlink()
    assert run(*reconnect)["authority_read"] == connected["authority_read"]
    assert not state.exists()
    recovered = cli("recover-import", "--plan-sha256", digest, "--execute")["cold_import"]
    # This is the original import receipt, not the current provider revision.
    assert recovered["status"] == "replayed" and recovered["cursor"] == imported["cursor"]
    current = listing()
    assert current["todos"] == after["todos"]
    assert canonical_todos(runtime, receiver, env, tmp_path) == complete_after
    assert current["authority_read"]["provider_revision"] == after["authority_read"]["provider_revision"]
    assert run(*add)["todo_id"] == created["todo_id"]
    assert run(*update, *proof)["ok"] is True
    assert listing()["todos"] == after["todos"]
    assert canonical_todos(runtime, receiver, env, tmp_path) == complete_after

    # Replaying the committed Todo update may repair its display projection.
    # Lose that projection again before testing provider unavailability.
    state.unlink(missing_ok=True)
    backend = runtime / "authority" / f"{provider}-v0"
    offline = backend.with_name(backend.name + "-offline")
    backend.rename(offline)
    try:
        refused = run(*reconnect, succeeds=False)
        assert refused["ok"] is False
        assert registry.read_bytes() == registered
        assert not backend.exists() and not state.exists()
        state.write_bytes(source_bytes)
        refused = run(*reconnect, "--dry-run", succeeds=False)
        assert refused["ok"] is False
        assert state.read_bytes() == source_bytes and registry.read_bytes() == registered
        assert not backend.exists()
        state.unlink()
        refused = cli("recover-import", "--plan-sha256", digest, "--execute", success=False)["cold_import"]
        assert refused["ok"] is False and refused["status"] == "failed"
        assert refused["executed"] is False and refused["legacy_fallback_used"] is False
        assert not backend.exists() and not state.exists()
    finally:
        offline.rename(backend)
    assert cli("recover-import", "--plan-sha256", digest, "--execute")["cold_import"]["status"] == "replayed"
    current = listing()
    assert current["todos"] == after["todos"]
    assert canonical_todos(runtime, receiver, env, tmp_path) == complete_after
    assert current["authority_read"]["provider_revision"] == after["authority_read"]["provider_revision"]


@pytest.mark.parametrize("artifact", ["archive_path", "manifest_path"])
def test_changed_reviewed_backup_cannot_fence_or_import(tmp_path, monkeypatch, artifact):
    cli, _, backup, _, runtime, _, _ = workspace(tmp_path, monkeypatch)
    prepared = cli("prepare-import", "--backup-manifest", backup["manifest_path"],
        "--provider", "sqlite", "--target-handoff-mode", "soft_claim")["cold_import"]
    Path(backup[artifact]).write_bytes(b"Changed saved bytes after review")
    refused = cli("apply-import", "--plan-sha256", prepared["plan_sha256"],
        "--writers-stopped", "--execute", success=False)["cold_import"]
    assert refused["reason_code"] == "cold_import_backup_changed"
    assert refused["legacy_writer_fenced"] is False
    assert not list((runtime / "authority-transition").rglob("writer-fence.json"))


def test_archive_source_map_cannot_be_forged_in_external_manifest(tmp_path, monkeypatch):
    _, _, backup, _, _, _, _ = workspace(tmp_path, monkeypatch)
    path = Path(backup["manifest_path"])
    manifest = json.loads(path.read_text())
    manifest["included"][0]["source_path"] = str(tmp_path / "wrong-source")
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="cold_import_backup_source_map_changed"):
        read_cold_source_backup(path)


def test_runtime_path_alias_uses_the_same_backed_up_source(tmp_path, monkeypatch):
    cli, state, backup, _, runtime, _, _ = workspace(tmp_path, monkeypatch)
    alias = tmp_path / "runtime-alias"
    alias.symlink_to(runtime, target_is_directory=True)
    prepared = cli("prepare-import", "--backup-manifest", backup["manifest_path"],
        "--provider", "sqlite", "--target-handoff-mode", "soft_claim",
        runtime_override=alias)["cold_import"]
    assert prepared["status"] == "prepared", prepared
    applied = cli("apply-import", "--plan-sha256", prepared["plan_sha256"],
        "--writers-stopped", "--execute", runtime_override=alias)["cold_import"]
    assert applied["status"] == "applied", applied
    state.unlink()
    recovered = cli("recover-import", "--plan-sha256", prepared["plan_sha256"],
        "--execute", runtime_override=alias)["cold_import"]
    assert recovered["status"] == "replayed", recovered
