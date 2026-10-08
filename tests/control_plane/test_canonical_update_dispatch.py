"""Promoted updates stay with their typed owner, including rejected empty edits."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import isolate_sqlite_runtime, promoted_create_fixture
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.todos import add_goal_todo, update_goal_todo


@pytest.fixture(params=["file", "sqlite"])
def update_workspace(tmp_path, monkeypatch, request):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=request.param)
    created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
                            text="Keep the authoritative record", agent_id="agent-a",
                            note="Keep the original note")
    return registry, runtime, state, created["todo_id"]


@pytest.fixture(params=[False, True], ids=["writer-present", "writer-absent"])
def cli_update(tmp_path, monkeypatch, request, update_workspace):
    package_root = tmp_path / "package"
    package = package_root / "loopx"
    shutil.copytree(Path(__file__).resolve().parents[2] / "loopx", package,
                    ignore=shutil.ignore_patterns("__pycache__"))
    if request.param:
        (package / "control_plane/todos/line_update.py").unlink()
    provenance = tmp_path / "cli-origin.txt"
    (package_root / "sitecustomize.py").write_text(
        "import importlib.util\nfrom pathlib import Path\n"
        f"Path({str(provenance)!r}).write_text(importlib.util.find_spec('loopx').origin)\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(package_root))
    registry, _runtime, _state, todo_id = update_workspace

    def invoke(*args):
        provenance.unlink(missing_ok=True)
        result = subprocess.run(
            [sys.executable, "-m", "loopx.cli", "--format", "json", "--registry", str(registry),
             "todo", "update", "--goal-id", "goal-a", "--todo-id", todo_id,
             "--agent-id", "agent-a", *args],
            cwd=tmp_path, capture_output=True, text=True, timeout=45,
        )
        assert Path(provenance.read_text()) == package / "__init__.py"
        return result

    return invoke


def test_empty_promoted_edits_reject_without_the_source_writer(update_workspace, cli_update):
    _registry, runtime, state, _todo_id = update_workspace
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    display = state.read_bytes()
    # The CLI's existing missing-field diagnostic precedes provider dispatch.
    for args in [(), ("--note", ""), ("--note", "", "--update-operation-id", "empty-update")]:
        empty_cli = cli_update(*args)
        assert empty_cli.returncode != 0
        assert "todo update requires at least one mutable todo field" in empty_cli.stdout
    for args in [("--note", " \t\n"),
                 ("--note", " \t\n", "--update-operation-id", "empty-update")]:
        result = cli_update(*args)
        assert result.returncode != 0
        assert "Todo update requires a non-empty patch" in result.stdout + result.stderr
        assert "legacy coordination writer" not in result.stdout + result.stderr
        assert "No module named" not in result.stdout + result.stderr
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
        assert state.read_bytes() == display

    # A failed empty intent does not consume its operation identity. Empty note
    # remains omission when another field supplies a valid edit.
    result = cli_update("--text", "Updated authoritative record", "--note", "",
                        "--update-operation-id", "empty-update")
    assert result.returncode == 0, result.stdout + result.stderr
    first = json.loads(result.stdout)
    current = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert current["todos"][0]["note"] == "Keep the original note"
    assert current["todos"][0]["text"] == "Updated authoritative record"

    later = cli_update("--note", "Newer note", "--update-operation-id", "later-update")
    assert later.returncode == 0, later.stdout + later.stderr
    newer = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    replay = cli_update("--text", "Updated authoritative record", "--note", " \t\n",
                        "--update-operation-id", "empty-update")
    assert replay.returncode == 0, replay.stdout + replay.stderr
    recovered = json.loads(replay.stdout)
    assert recovered["status"] == "replayed"
    assert recovered["original_receipt"] == first["original_receipt"]
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == newer

    # Losing a selected provider never recreates it or routes back to Markdown.
    backend = runtime / "authority" / before["source_authority"].replace("_", "-")
    offline = backend.with_name(backend.name + "-offline")
    display = state.read_bytes()
    backend.rename(offline)
    try:
        unavailable = cli_update("--note", "Recovered provider edit",
                                 "--update-operation-id", "provider-recovery")
        assert unavailable.returncode != 0
        failure = json.loads(unavailable.stdout)
        assert failure["ok"] is False
        assert failure["source_authority"] == before["source_authority"]
        assert failure["legacy_fallback_used"] is False
        assert "legacy coordination writer" not in unavailable.stdout + unavailable.stderr
        assert not backend.exists() and state.read_bytes() == display
    finally:
        offline.rename(backend)
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == newer
    restored = cli_update("--note", "Recovered provider edit",
                          "--update-operation-id", "provider-recovery")
    assert restored.returncode == 0, restored.stdout + restored.stderr
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")["todos"][0]["note"] == "Recovered provider edit"


def test_public_facade_empty_edits_use_the_typed_decoder(update_workspace):
    registry, runtime, state, todo_id = update_workspace
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    display = state.read_bytes()
    for intent in [{}, {"note": ""}, {"note": " \t\n"},
                   {"note": "", "update_operation_id": "empty-edit"}]:
        with pytest.raises(LocalCoordinationAuthorityUnavailable,
                           match="Todo update requires a non-empty patch"):
            update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id=todo_id,
                             agent_id="agent-a", **intent)
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
        assert state.read_bytes() == display


def test_unpromoted_facade_preserves_empty_edit_no_change(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    state = project / "state.md"
    state.write_text("# Goal\n\n## Agent Todo\n")
    registry = tmp_path / "registry.json"
    registry.write_text(json.dumps({"schema_version": 1, "common_runtime_root": str(tmp_path / "runtime"),
        "goals": [{"id": "goal-a", "repo": str(project), "state_file": "state.md",
                   "coordination": {"registered_agents": ["agent-a"]}}]}))
    created = add_goal_todo(registry_path=registry, goal_id="goal-a", role="agent",
                            text="Keep legacy compatibility", agent_id="agent-a", note="Keep the note")
    before = state.read_bytes()
    for intent in [{}, {"note": ""}, {"note": " \t\n"}]:
        result = update_goal_todo(registry_path=registry, goal_id="goal-a", todo_id=created["todo_id"],
                                 agent_id="agent-a", **intent)
        assert result["ok"] and result["changed"] is False
        assert state.read_bytes() == before
