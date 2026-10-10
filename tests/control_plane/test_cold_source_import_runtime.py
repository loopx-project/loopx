"""Real typed runtime/backend with the old normal producers physically absent.

This qualifies the coordination transaction, not the installed CLI/App journey
or complete backup coverage. The source adapter remains separately owned.
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
from loopx.control_plane.coordination.runtime_shadow import build_runtime_shadow_source_snapshot

REPO = Path(__file__).resolve().parents[2]
METHOD = "coordination.cold_source.import"
SCHEMA = "loopx_cold_source_import_request_v0"


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_cold_import_real_runtime_without_original_producers(tmp_path, monkeypatch, provider):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, registry, state = tmp_path / "runtime", tmp_path / ".loopx/registry.json", tmp_path / ".local/goals/cold-goal/state.md"
    registry.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    runtime.mkdir()
    body = " ".join(["Complete cold source body"] * 30)
    state.write_text(
        "---\ngoal_id: cold-goal\nhandoff_mode: legacy\n---\n\n## Agent Todo\n\n"
        f"- [ ] {body}\n  <!-- loopx:todo todo_id=todo_current role=agent task_class=advancement_task claimed_by=agent-a note=retained -->\n\n"
        "## Completed Work Archive\n\n- [x] Complete archived source body\n"
        "  <!-- loopx:todo todo_id=todo_archived role=agent task_class=advancement_task evidence=original -->\n",
        encoding="utf-8",
    )
    goal = {"id": "cold-goal", "repo": str(tmp_path), "state_file": str(state.relative_to(tmp_path)),
            "coordination": {"registered_agents": ["agent-a"]}}
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(runtime), "goals": [goal]}))
    # Full persisted records are assembled by the shipped source adapter.
    projection, snapshot = build_runtime_shadow_source_snapshot(goal=goal, runtime_root=runtime,
        state_path=state, registry_path=registry, include_all_archived_todos=True)
    assert {row["todo_id"] for row in projection["todos"]} == {"todo_current", "todo_archived"}
    backup = execute_state_backup_plan(build_state_backup_plan(project=tmp_path, runtime_root=runtime,
        output_dir=tmp_path / "backup", backup_id="original", include_automations=False,
        include_skills=False, include_registry_projects=False, registry_path=registry))
    source_backup = read_cold_source_backup(Path(backup["manifest_path"]))
    request = {"schema_version": SCHEMA, "runtime_root": str(runtime), "goal_id": "cold-goal", "operation_id": "cold:original"}
    receiver = tmp_path / "receiver"
    package = Path(os.environ.get("LOOPX_COLD_IMPORT_PACKAGE", str(REPO / "loopx")))
    shutil.copytree(package, receiver / "loopx", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for path in ("todos.py", "bootstrap.py", "control_plane/coordination/runtime_shadow_writer_adapter.py",
                 "control_plane/coordination/local_authority_shadow_outbox.py"):
        (receiver / "loopx" / path).unlink()
    env = {**os.environ, "PYTHONPATH": str(receiver)}

    def call(method, fields):
        child = subprocess.run([sys.executable, "-c",
            "import json,sys,loopx; from loopx.control_plane.effect_runtime import effect_runtime_result; "
            "v=json.load(sys.stdin); print(json.dumps({'package':loopx.__file__,'result':effect_runtime_result(v['method'],v['request'],retry_safe=False)},ensure_ascii=False))"],
            input=json.dumps({"method": method, "request": fields}), cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=60, check=True)
        output = json.loads(child.stdout)
        assert Path(output["package"]).parent == receiver / "loopx"
        return output["result"]

    if provider == "sqlite":
        module = receiver / "loopx/control_plane/coordination/local_authority_provider.ts"
        subprocess.run(["node", "--no-warnings", "--experimental-strip-types", "--input-type=module", "-e",
            f"import {{selectLocalAuthorityTarget}} from {json.dumps(module.as_uri())}; "
            f"await selectLocalAuthorityTarget({json.dumps(str(runtime))},'cold-goal','sqlite',true);"],
            cwd=tmp_path, env=env, capture_output=True, text=True, check=True, timeout=30)
    original_bytes = state.read_bytes()
    prepared = call(METHOD, {**request, "action": "prepare", "projection": projection,
        "source_snapshot": snapshot, "target_handoff_mode": "soft_claim", "target_provider": provider, "source_backup": source_backup})
    assert prepared["status"] == "prepared", prepared
    applied = call(METHOD, {**request, "action": "apply", "expected_plan_sha256": prepared["plan_sha256"], "writers_stopped": True})
    assert applied["status"] == "applied", applied
    assert applied["execution_authority_granted"] is False
    assert applied["complete_goal_backup_verified"] is False
    assert state.read_bytes() == original_bytes
    # Fresh Python process; the carrier and original receipt outlive Markdown.
    state.unlink()
    replayed = call(METHOD, {**request, "action": "recover", "expected_plan_sha256": prepared["plan_sha256"]})
    assert replayed["status"] == "replayed", replayed
    observed = call("coordination.local_authority.todo_list", {
        "schema_version": "loopx_local_coordination_todo_list_request_v0", "runtime_root": str(runtime),
        "goal_id": "cold-goal", "role": None, "status": None, "todo_id": None, "agent_id": None, "limit": None,
    })
    assert observed["status"] == "loaded", observed
    assert observed["cursor"] == "1"
    assert observed["source_authority"] == f"{provider}_v0"
    assert {row["todo_id"] for row in observed["todos"]} == {"todo_current", "todo_archived"}
    assert next(row for row in observed["todos"] if row["todo_id"] == "todo_current")["text"] == body
    assert next(row for row in observed["todos"] if row["todo_id"] == "todo_archived")["evidence"] == "original"
