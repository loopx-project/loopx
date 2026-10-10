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


def workspace(tmp_path, monkeypatch):
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


def test_unconfigured_shadow_inspection_matches_full_cli_without_import(tmp_path, monkeypatch):
    _, state, _, _, runtime, receiver, env = workspace(tmp_path, monkeypatch)
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("old_producers_present", [True, False])
def test_cold_cli_import_and_source_free_original_recovery(
    tmp_path, monkeypatch, provider, old_producers_present,
):
    cli, state, backup, body, runtime, receiver, env = workspace(tmp_path, monkeypatch)
    if not old_producers_present:
        for path in ("todos.py", "bootstrap.py", "control_plane/coordination/runtime_shadow_writer_adapter.py",
                     "control_plane/coordination/local_authority_shadow_outbox.py"):
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
    child = subprocess.run([sys.executable, "-c",
        "import json,sys; from loopx.control_plane.effect_runtime import effect_runtime_result; "
        "print(json.dumps(effect_runtime_result('coordination.local_authority.todo_list',json.load(sys.stdin))))"],
        input=json.dumps({"schema_version": "loopx_local_coordination_todo_list_request_v0",
            "runtime_root": str(runtime), "goal_id": "cold", "role": None, "status": None,
            "todo_id": None, "agent_id": None, "limit": None}),
        cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=60)
    result = json.loads(child.stdout)
    assert result["source_authority"] == f"{provider}_v0"
    assert {row["todo_id"] for row in result["todos"]} == {"todo_current", "todo_archived"}
    assert next(row for row in result["todos"] if row["todo_id"] == "todo_current")["text"] == body
    assert next(row for row in result["todos"] if row["todo_id"] == "todo_archived")["evidence"] == "original"
    state.unlink()
    recovered = cli("recover-import", "--plan-sha256", digest, "--execute")["cold_import"]
    assert recovered["status"] == "replayed", recovered
    assert recovered["cursor"] == "1"


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
