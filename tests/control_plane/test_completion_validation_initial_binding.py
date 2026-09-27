from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture

from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.todos import provider_update
from loopx.control_plane.todos.completion_validation_store import (
    completion_validation_declaration_path,
    read_completion_validation_declaration,
)
from loopx.todos import add_goal_todo, complete_goal_todo, list_goal_todos, update_goal_todo


def canonical(runtime_root: Path) -> dict:
    result = read_canonical_todos_if_promoted(runtime_root=runtime_root, goal_id="goal-a")
    assert result is not None
    return result


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_public_cli_first_binding_replays_and_still_runs_real_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
                            text="Check independently before completing", claimed_by="agent-a", agent_id="agent-a")
    todo_id = created["todo_id"]
    before = canonical(runtime)
    assert "completion_validation_sha256" not in before["todos"][0]
    # A marker proves binding/preview/replay never executes the validation command.
    marker = tmp_path / "validator-executed"
    argv = [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch(); raise SystemExit(4)"]
    command = [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
               "--runtime-root", str(runtime), "todo", "update", "--goal-id", "goal-a", "--todo-id", todo_id,
               "--agent-id", "agent-a", "--update-operation-id", "first-bind",
               "--update-expected-provider-revision", before["provider_revision"],
               "--validation-command-json", json.dumps(argv), "--validation-label", "Independent check"]

    def run(extra: list[str] | None = None) -> dict:
        proc = subprocess.run(command + (extra or []), capture_output=True, text=True, timeout=45)
        assert proc.returncode == 0, proc.stderr + proc.stdout
        return json.loads(proc.stdout)

    assert run(["--dry-run"])["status"] == "planned"
    assert canonical(runtime) == before
    assert not marker.exists()
    assert run()["status"] == "applied"
    bound = canonical(runtime)
    work = bound["todos"][0]
    assert work["status"] == "open"
    assert work["completion_validation_required"] is True
    first = work["completion_validation_revision_history"][0]
    assert first["schema_version"] == "loopx_todo_completion_validation_revision_receipt_v1"
    assert first["previous_declaration_sha256"] is None
    public_work = next(row for row in list_goal_todos(registry_path=registry, goal_id="goal-a")["todos"]
                       if row["todo_id"] == todo_id)
    assert public_work["completion_validation_revision_history"] == work["completion_validation_revision_history"]
    assert public_work["completion_validation_sha256"] == work["completion_validation_sha256"]
    assert "validation_command_argv" not in public_work
    assert "validation_command" not in public_work
    assert read_completion_validation_declaration(runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)["validation_command_argv"] == argv
    assert run()["status"] == "replayed"
    assert canonical(runtime) == bound
    assert not marker.exists()

    failed = complete_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a",
                                todo_id=todo_id, role="agent", agent_id="agent-a", claimed_by="agent-a", no_followup=True)
    assert failed["status"] == "failed"
    assert failed["validation_blocked_completion"] is True
    assert marker.exists()
    assert canonical(runtime)["todos"][0]["status"] == "open"
    current = canonical(runtime)
    replacement = [sys.executable, "-c", "raise SystemExit(0)"]
    assert update_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a",
                           todo_id=todo_id, role="agent", agent_id="agent-a", update_operation_id="replace",
                           update_expected_provider_revision=current["provider_revision"],
                           validation_command_json=json.dumps(replacement), validation_label="Independent check")["status"] == "applied"
    replaced = canonical(runtime)
    assert replaced["todos"][0]["completion_validation_revision"] == 2
    # The original first-binding replay is historical, not permission to restore old bytes.
    replay = subprocess.run(command, capture_output=True, text=True, timeout=45)
    assert replay.returncode != 0
    assert "publication_mismatch" in replay.stdout + replay.stderr
    assert canonical(runtime) == replaced
    completed = complete_goal_todo(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a",
                                   todo_id=todo_id, role="agent", agent_id="agent-a", claimed_by="agent-a", no_followup=True)
    assert completed["status"] == "done"
    assert completed["validation_receipt"]["passed"] is True
    assert completed["validation_receipt"]["validation_declaration_sha256"] == replaced["todos"][0]["completion_validation_sha256"]


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_first_binding_recovers_lost_private_publication_with_same_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, _ = promoted_create_fixture(tmp_path, provider=provider)
    created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
                            text="Recover declaration publication", claimed_by="agent-a", agent_id="agent-a")
    todo_id = created["todo_id"]
    before = canonical(runtime)
    args = dict(registry_path=registry, runtime_root_arg=str(runtime), goal_id="goal-a", todo_id=todo_id,
                role="agent", agent_id="agent-a", update_operation_id="bind-recover",
                update_expected_provider_revision=before["provider_revision"],
                validation_command_json=json.dumps([sys.executable, "-c", "raise SystemExit(0)"]))
    publish = provider_update.persist_completion_validation_declaration

    def lose_publication(**_kwargs):
        raise OSError("Synthetic post-commit publication failure")

    monkeypatch.setattr(provider_update, "persist_completion_validation_declaration", lose_publication)
    with pytest.raises(OSError, match="post-commit"):
        update_goal_todo(**args)
    committed = canonical(runtime)
    assert committed["todos"][0]["completion_validation_revision"] == 1
    sidecar = completion_validation_declaration_path(runtime_root=runtime, goal_id="goal-a", todo_id=todo_id)
    assert not sidecar.exists()
    monkeypatch.setattr(provider_update, "persist_completion_validation_declaration", publish)
    assert update_goal_todo(**args)["status"] == "replayed"
    assert canonical(runtime) == committed
    assert sidecar.exists()
    assert read_completion_validation_declaration(runtime_root=runtime, goal_id="goal-a", todo_id=todo_id) is not None
