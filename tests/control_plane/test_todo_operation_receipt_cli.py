"""The public CLI can inspect an exact canonical operation without retrying it."""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
    promoted_create_fixture,
)
from loopx.control_plane.coordination.local_authority import read_canonical_todos_if_promoted
from loopx.control_plane.coordination.runtime_shadow import (
    build_todo_runtime_shadow_projection,
)
from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous
from loopx.control_plane.todos import provider_terminal_lifecycle
from loopx.entrypoint import main
from loopx.todos import add_goal_todo


def test_todo_receipt_cli_reads_history_without_writing_an_event(tmp_path: Path) -> None:
    runtime_root = tmp_path / "runtime"
    project = tmp_path / "project"
    project.mkdir()
    state_path = project / "ACTIVE_GOAL_STATE.md"
    state_path.write_text("# Goal\n\n## Agent Todo\n", encoding="utf-8")
    registry_path = tmp_path / "registry.json"
    registry_path.write_text(json.dumps({"schema_version": 1,
        "common_runtime_root": str(runtime_root), "goals": [{"id": "goal-a",
        "repo": str(project), "state_file": state_path.name,
        "coordination": {"registered_agents": ["agent-a"]}}]}), encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(
        goal_id="goal-a", todos=[], handoff_mode="soft_claim",
    )
    initialize_canonical_authority(runtime_root, "goal-a", projection, state_path=state_path)

    base = [sys.executable, "-m", "loopx.cli", "--format", "json",
        "--registry", str(registry_path), "todo", "receipt", "--goal-id", "goal-a"]
    found = subprocess.run([*base, "--operation-id", "canonical-fixture"],
        capture_output=True, text=True, timeout=30)
    assert found.returncode == 0, found.stdout + found.stderr
    payload = json.loads(found.stdout)
    assert payload["status"] == "found"
    assert payload["operation_id"] == "canonical-fixture"
    assert payload["source_authority"] == "file_v0"
    assert payload["decision_read_from_provider"] is True
    assert payload["receipts"] == []  # Bootstrap had no business receipt.

    missing = subprocess.run([*base, "--operation-id", "other-operation"],
        capture_output=True, text=True, timeout=30)
    assert missing.returncode == 0, missing.stdout + missing.stderr
    assert json.loads(missing.stdout)["status"] == "missing"
    assert not (runtime_root / "goals" / "goal-a" / "runs" / "index.jsonl").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("committed", [False, True])
@pytest.mark.parametrize("monitor", [False, True], ids=["todo", "monitor"])
def test_ambiguous_validated_completion_returns_exact_receipt_lookup(
    tmp_path: Path, monkeypatch, capsys, provider: str, committed: bool, monitor: bool,
) -> None:
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    registry, runtime, state = promoted_create_fixture(tmp_path, provider=provider)
    permission = tmp_path / "permit"
    permission.touch()
    calls = tmp_path / "calls"
    validator = (
        "from pathlib import Path; "
        f"Path({str(calls)!r}).open('a').write('x'); "
        f"raise SystemExit(not Path({str(permission)!r}).exists())"
    )
    task = {"task_class": "advancement_task"}
    if monitor:
        task = {"task_class": "continuous_monitor", "action_kind": "monitor",
                "monitor_metadata": {"target_key": "test-observation", "watch_only": "true",
                                     "cadence": "30m", "next_due_at": "2099-01-01T00:00:00Z"}}
    todo = add_goal_todo(
        registry_path=registry, goal_id="goal-a", role="agent", **task,
        text="Record the validated result", claimed_by="agent-a",
        validation_command_json=json.dumps([sys.executable, "-c", validator]),
    )
    todo_id = todo["todo_id"]
    selectors = ["--format", "json", "--registry", str(registry), "--runtime-root", str(runtime)]
    command = ["todo", "complete", "--goal-id", "goal-a", "--todo-id", todo_id,
               "--agent-id", "agent-a", "--evidence", "Checked the result."]
    if monitor:
        command.append("--no-follow-up")
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    display = state.read_bytes()
    dispatch = provider_terminal_lifecycle.effect_runtime_result

    def lose_validation_response(method, request, **kwargs):
        receipt = request.get("validation_receipt")
        if receipt and receipt["passed"]:
            if committed:
                assert dispatch(method, request, **kwargs)["status"] == "applied"
            raise EffectRuntimeResponseAmbiguous(method, timeout=kwargs["timeout"])
        return dispatch(method, request, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(provider_terminal_lifecycle, "effect_runtime_result", lose_validation_response)
        assert main([*selectors, *command]) == 1
    error = json.loads(capsys.readouterr().out)
    assert error["error_code"] == "runtime_response_ambiguous"
    lookup = error["recovery_command"]
    assert lookup == [
        "loopx", *selectors, "todo", "receipt", "--goal-id", "goal-a",
        "--operation-id", error["operation_id"],
    ]
    assert state.read_bytes() == display
    interrupted = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    assert (interrupted != before) is committed
    assert calls.read_text() == "x"

    assert main(lookup[1:]) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["status"] == ("found" if committed else "missing")
    if committed:
        assert receipt["receipts"][0]["todo_id"] == todo_id
        assert receipt["receipts"][0]["operation_id"] == error["operation_id"]
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == interrupted
    assert state.read_bytes() == display
    assert calls.read_text() == "x"

    permission.unlink()
    if not committed:
        assert main([*selectors, *command]) == 1
        assert json.loads(capsys.readouterr().out)["validation_blocked_completion"] is True
        assert calls.read_text() == "xx"
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
        permission.touch()
    assert main([*selectors, *command]) == 0
    retry = json.loads(capsys.readouterr().out)
    assert retry["provider_status"] == ("replayed" if committed else "applied")
    assert retry["projection_delivery"] == "delivered"
    assert calls.read_text() == ("x" if committed else "xxx")


def test_receipt_lookup_uses_selected_cli_in_markdown(tmp_path, monkeypatch, capsys) -> None:
    registry, runtime, _ = promoted_create_fixture(tmp_path)
    todo = add_goal_todo(
        registry_path=registry, goal_id="goal-a", role="agent", claimed_by="agent-a",
        text="Complete validated work", task_class="advancement_task",
        validation_command_json=json.dumps([sys.executable, "-c", "raise SystemExit(0)"]),
    )
    dispatch = provider_terminal_lifecycle.effect_runtime_result

    def lose_response(method, request, **kwargs):
        if request.get("validation_receipt"):
            raise EffectRuntimeResponseAmbiguous(method, timeout=kwargs["timeout"])
        return dispatch(method, request, **kwargs)

    monkeypatch.setattr(provider_terminal_lifecycle, "effect_runtime_result", lose_response)
    cli_bin = str(tmp_path / "selected CLI")
    assert main(["--cli-bin", cli_bin, "--format", "markdown", "--registry", str(registry),
                 "--runtime-root", str(runtime), "todo", "complete", "--goal-id", "goal-a",
                 "--todo-id", todo["todo_id"], "--agent-id", "agent-a"]) == 1
    command = capsys.readouterr().out.split("```sh\n", 1)[1].splitlines()[0]
    lookup = shlex.split(command)
    if os.name == "nt":
        assert lookup.pop(0) == "&"
    assert lookup[:3] == [cli_bin, "--cli-bin", cli_bin]
    assert lookup[3:-1] == ["--format", "json", "--registry", str(registry), "--runtime-root",
                            str(runtime), "todo", "receipt", "--goal-id", "goal-a", "--operation-id"]
