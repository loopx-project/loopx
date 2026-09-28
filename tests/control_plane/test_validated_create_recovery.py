"""Lost responses must not orphan validated Todos or roll back newer validators."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable, read_canonical_todos_if_promoted,
)
from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous
from loopx.control_plane.todos import provider_create, provider_update
from loopx.control_plane.todos.completion_validation_store import (
    completion_validation_declaration_path, read_completion_validation_declaration,
    prepare_completion_validation_declaration,
)
from loopx.todos import add_goal_todo, update_goal_todo


@pytest.fixture(params=["file", "sqlite"])
def promoted(request, tmp_path, monkeypatch):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    return promoted_create_fixture(tmp_path, provider=request.param)


def read(runtime):
    return read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")


def intent(registry):
    return dict(registry_path=registry, goal_id="goal-a", role="agent",
                text="Validate a durable artifact", claimed_by="agent-a", agent_id="agent-a",
                operation_id="create-durable-artifact", validation_label="artifact check",
                validation_command_json=json.dumps([sys.executable, "-c", "raise SystemExit(0)"]))


def project(registry, runtime):
    before = read(runtime)
    result = subprocess.run(
        [sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
         "todo", "project-markdown", "--goal-id", "goal-a", "--provider-revision",
         before["provider_revision"], "--execute"], capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert read(runtime) == before


def test_lost_response_replays_exact_create_after_another_edit(promoted, monkeypatch):
    registry, runtime, state = promoted
    real = provider_create.effect_runtime_result
    def lose_response(method, request, **kwargs):
        real(method, request, **kwargs)
        raise EffectRuntimeResponseAmbiguous(method, timeout=1)
    monkeypatch.setattr(provider_create, "effect_runtime_result", lose_response)
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as error:
        add_goal_todo(**intent(registry))
    assert error.value.code == "todo_create_response_ambiguous"
    assert error.value.payload["operation_id"] == "create-durable-artifact"
    committed = read(runtime)
    assert len(committed["todos"]) == 1
    todo = committed["todos"][0]
    alias = completion_validation_declaration_path(runtime_root=runtime, goal_id="goal-a", todo_id=todo["todo_id"])
    assert not alias.exists()
    declaration = read_completion_validation_declaration(
        runtime_root=runtime, goal_id="goal-a", todo_id=todo["todo_id"],
        expected_digest=todo["completion_validation_sha256"],
    )
    assert declaration["validation_command_argv"][-1] == "raise SystemExit(0)"
    project(registry, runtime)  # Recovery works in another process before retrying creation.
    assert todo["todo_id"] in state.read_text()
    monkeypatch.setattr(provider_create, "effect_runtime_result", real)
    update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id=todo["todo_id"],
                     role="agent", agent_id="agent-a", text="A subsequent work declaration")
    before_retry = read(runtime)
    replay = add_goal_todo(**intent(registry))
    assert replay["status"] == "replayed"
    assert replay["todo_id"] == todo["todo_id"]
    assert read(runtime) == before_retry  # No new commit or stale declaration overwrite.
    with pytest.raises(LocalCoordinationAuthorityUnavailable):
        add_goal_todo(**{**intent(registry), "text": "Different intent with the same operation"})
    assert read(runtime) == before_retry


def test_create_replay_preserves_revised_validator(promoted):
    registry, runtime, _ = promoted
    created = add_goal_todo(**intent(registry))
    todo_id = created["todo_id"]
    update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id=todo_id,
                     role="agent", agent_id="agent-a", update_operation_id="revise-check",
                     update_expected_provider_revision=read(runtime)["provider_revision"],
                     validation_command_json=json.dumps([sys.executable, "-c", "raise SystemExit(7)"]),
                     validation_label="revised artifact check")
    revised = read(runtime)
    add_goal_todo(**intent(registry))
    assert read(runtime) == revised
    for digest in (None, revised["todos"][0]["completion_validation_sha256"]):
        stored = read_completion_validation_declaration(
            runtime_root=runtime, goal_id="goal-a", todo_id=todo_id, expected_digest=digest)
        assert stored["validation_command_argv"][-1] == "raise SystemExit(7)"
    project(registry, runtime)


def test_revision_lost_response_still_projects_exact_new_declaration(promoted, monkeypatch):
    registry, runtime, _ = promoted
    created = add_goal_todo(**intent(registry))
    real = provider_update.effect_runtime_result
    def lose_response(method, request, **kwargs):
        real(method, request, **kwargs)
        raise EffectRuntimeResponseAmbiguous(method, timeout=1)
    monkeypatch.setattr(provider_update, "effect_runtime_result", lose_response)
    with pytest.raises(EffectRuntimeResponseAmbiguous):
        update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id=created["todo_id"],
                         role="agent", agent_id="agent-a", update_operation_id="revise-after-loss",
                         update_expected_provider_revision=read(runtime)["provider_revision"],
                         validation_command_json=json.dumps([sys.executable, "-c", "raise SystemExit(3)"]))
    project(registry, runtime)
    canonical = read(runtime)["todos"][0]
    stored = read_completion_validation_declaration(runtime_root=runtime, goal_id="goal-a",
             todo_id=created["todo_id"], expected_digest=canonical["completion_validation_sha256"])
    assert stored["validation_command_argv"][-1] == "raise SystemExit(3)"


def test_prepare_failure_prevents_commit_and_dry_run_publishes_nothing(promoted, monkeypatch):
    registry, runtime, _ = promoted
    before = read(runtime)
    add_goal_todo(**intent(registry), dry_run=True)
    assert not (runtime / "goals/goal-a/todo-validation-declarations").exists()
    def fail(**kwargs):
        raise OSError("private storage unavailable")
    monkeypatch.setattr(provider_create, "prepare_completion_validation_declaration", fail)
    with pytest.raises(OSError, match="private storage"):
        add_goal_todo(**intent(registry))
    assert read(runtime) == before


@pytest.mark.parametrize("corruption", ["content", "identity"])
def test_selected_blob_corruption_cannot_fall_back_to_alias(promoted, corruption):
    registry, runtime, _ = promoted
    todo_id = add_goal_todo(**intent(registry))["todo_id"]
    todo = read(runtime)["todos"][0]
    alias = completion_validation_declaration_path(runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)
    blob = alias.parent / "blobs" / (todo["completion_validation_sha256"] + ".json")
    if os.name == "posix":
        assert blob.stat().st_mode & 0o777 == 0o600
    payload = json.loads(blob.read_text())
    if corruption == "content":
        payload["declaration"]["validation_command_argv"] = ["false"]
    else:
        payload["goal_id"] = "another-goal"
    blob.write_text(json.dumps(payload))
    with pytest.raises(ValueError, match="digest or identity"):
        read_completion_validation_declaration(runtime_root=runtime, goal_id="goal-a", todo_id=todo_id,
                                               expected_digest=todo["completion_validation_sha256"])


def test_unselected_prepared_content_does_not_change_canonical_authority(promoted):
    registry, runtime, _ = promoted
    before = read(runtime)
    prepare_completion_validation_declaration(runtime_root=runtime, goal_id="goal-a",
                                             declaration={"validation_command_argv": ["false"]})
    project(registry, runtime)
    assert read(runtime) == before


def test_process_exit_after_commit_recovers_through_public_cli(promoted):
    registry, runtime, _ = promoted
    script = '''
import json, os, sys
from pathlib import Path
from loopx.control_plane.todos import provider_create
from loopx.todos import add_goal_todo
real = provider_create.effect_runtime_result
def exit_after_commit(method, request, **kwargs):
    real(method, request, **kwargs)
    os._exit(77)
provider_create.effect_runtime_result = exit_after_commit
add_goal_todo(registry_path=Path(sys.argv[1]), goal_id="goal-a", role="agent",
    text="Survive process exit", claimed_by="agent-a", agent_id="agent-a",
    operation_id="process-exit-create", validation_command_json='["python3", "-c", "pass"]')
'''
    child = subprocess.run([sys.executable, "-c", script, str(registry)],
                           capture_output=True, text=True, timeout=60)
    assert child.returncode == 77, child.stdout + child.stderr
    before = read(runtime)
    assert len(before["todos"]) == 1
    project(registry, runtime)
    retried = subprocess.run([
        sys.executable, "-m", "loopx.cli", "--registry", str(registry), "--format", "json",
        "todo", "add", "--goal-id", "goal-a", "--role", "agent", "--text", "Survive process exit",
        "--claimed-by", "agent-a", "--operation-id", "process-exit-create",
        "--validation-command-json", '["python3", "-c", "pass"]',
    ], capture_output=True, text=True, timeout=60)
    assert retried.returncode == 0, retried.stdout + retried.stderr
    result = json.loads(retried.stdout)
    assert result["status"] == "replayed"
    assert result["todo_id"] == before["todos"][0]["todo_id"]
    assert read(runtime) == before
