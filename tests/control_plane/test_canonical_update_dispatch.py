"""Promoted updates stay with their typed owner, including rejected empty edits."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from canonical_authority_fixture import (
    initialize_canonical_authority,
    isolate_sqlite_runtime,
    promoted_create_fixture,
)
from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
from loopx.control_plane.effect_runtime import (
    CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS,
    EffectRuntimeHostPermissionError,
    EffectRuntimeResponseAmbiguous,
)
from loopx.control_plane.todos import provider_update
from loopx.entrypoint import main
from loopx.control_plane.coordination.local_authority import (
    LocalCoordinationAuthorityUnavailable,
    read_canonical_todos_if_promoted,
)
from loopx.todos import add_goal_todo, update_goal_todo


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("operation_id", [None, "caller-owned-update"], ids=["generated", "explicit"])
@pytest.mark.parametrize("commit", [True, False], ids=["committed", "no-receipt"])
def test_ambiguous_cli_update_recovers_exact_receipt_without_another_write(
    tmp_path, monkeypatch, provider, operation_id, commit,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    runtime, state, registry = tmp_path / "runtime", tmp_path / "state.md", tmp_path / "registry.json"
    goal, target = "update-recovery", "todo_recovery"
    state.write_text("# Synthetic update recovery\n\n## Agent Todo\n", encoding="utf-8")
    registry.write_text(json.dumps({"common_runtime_root": str(runtime), "goals": [{
        "id": goal, "repo": str(tmp_path), "state_file": state.name,
        "coordination": {"registered_agents": ["agent-a"]},
    }]}), encoding="utf-8")
    projection = build_todo_runtime_shadow_projection(
        goal_id=goal, handoff_mode="hard_lease", leases=[], todos=[{
            "schema_version": "todo_item_v0", "todo_id": target, "role": "agent",
            "status": "open", "done": False, "text": "Keep the original work",
            "note": "Keep the original note", "archive_state": "active",
            "source_section": "Agent Todo", "index": 1,
            "task_class": "advancement_task", "claimed_by": "agent-a",
        }],
    )
    initialize_canonical_authority(runtime, goal, projection, state_path=state, provider=provider)
    common = ["loopx", "--format", "json", "--registry", str(registry),
              "--runtime-root", str(runtime)]

    def cli(argv, *, script=None):
        entry = ["-c", script] if script else ["-m", "loopx.entrypoint"]
        return subprocess.run([sys.executable, *entry, *argv[1:]],
                              capture_output=True, text=True, encoding="utf-8", timeout=60)

    try:
        acquired = cli([*common, "task-lease", "acquire", "--goal-id", goal,
                        "--todo-id", target, "--owner", "agent-a",
                        "--idempotency-key", "update-proof", "--expected-version", "0"])
        assert acquired.returncode == 0, acquired.stdout + acquired.stderr
        lease = json.loads(acquired.stdout)["lease"]
        before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        display = state.read_bytes()
        dispatched = tmp_path / "dispatched.json"
        # Inject response loss at the transport boundary, after the real typed
        # owner has committed. The alternative represents an ambiguous request
        # with no durable receipt; neither observation authorizes a new write.
        script = (
            "import json\nfrom pathlib import Path\n"
            "from loopx.control_plane.todos import provider_update as adapter\n"
            "from loopx.control_plane.effect_runtime import EffectRuntimeResponseAmbiguous\n"
            "from loopx.entrypoint import main\noriginal = adapter.effect_runtime_result\n"
            "def lose_response(method, request, **kwargs):\n"
            f"    result = original(method, request, **kwargs) if {commit!r} else None\n"
            f"    Path({str(dispatched)!r}).write_text(json.dumps({{'request':request,'result':result}}), encoding='utf-8')\n"
            "    raise EffectRuntimeResponseAmbiguous(method, timeout=kwargs['timeout'])\n"
            "adapter.effect_runtime_result = lose_response\nraise SystemExit(main())\n"
        )
        update = [*common, "todo", "update", "--goal-id", goal, "--todo-id", target,
                  "--agent-id", "agent-a", "--note", "Committed note 完整 🧭",
                  "--task-lease-idempotency-key", "update-proof",
                  "--task-lease-expected-version", str(lease["version"])]
        if operation_id:
            update += ["--update-operation-id", operation_id]
        lost = cli(update, script=script)
        assert lost.returncode == 1, lost.stdout + lost.stderr
        observed = json.loads(dispatched.read_text(encoding="utf-8"))
        current = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal)
        inspected = cli([*common, "task-lease", "inspect", "--goal-id", goal, "--todo-id", target])
        assert inspected.returncode == 0, inspected.stdout + inspected.stderr
        assert json.loads(inspected.stdout)["lease"] == lease
        if commit:
            assert observed["result"]["status"] == "applied"
            assert current["cursor"] == str(int(before["cursor"]) + 1)
            assert current["todos"][0]["note"] == "Committed note 完整 🧭"
        else:
            assert current == before
        # No projection is fabricated from an unverifiable response.
        assert state.read_bytes() == display
        failure = json.loads(lost.stdout)
        assert failure["error_code"] == "runtime_response_ambiguous"
        assert "missing receipt does not prove the original writer has stopped" in failure["error"]
        assert failure["operation_id"] == observed["request"]["operation_id"]
        if operation_id:
            assert failure["operation_id"] == operation_id
        else:
            assert failure["operation_id"].startswith("todo-update:")
        assert failure["recovery_command"] == [
            *common, "todo", "receipt", "--goal-id", goal,
            "--operation-id", failure["operation_id"],
        ]
        recovered = cli(failure["recovery_command"])
        assert recovered.returncode == 0, recovered.stdout + recovered.stderr
        receipt = json.loads(recovered.stdout)
        assert receipt["status"] == ("found" if commit else "missing")
        if commit:
            assert observed["result"]["original_receipt"] in receipt["receipts"]
        assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id=goal) == current
        assert state.read_bytes() == display
    finally:
        subprocess.run([sys.executable, "-c", "from loopx.control_plane.effect_runtime import effect_runtime_result; effect_runtime_result('runtime.shutdown',{},retry_safe=False)"],
                       capture_output=True, text=True, encoding="utf-8", timeout=30, check=True)


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


@pytest.mark.parametrize("error", [EffectRuntimeHostPermissionError(), RuntimeError("ordinary failure")])
def test_nonambiguous_cli_failure_does_not_offer_commit_recovery(
    update_workspace, monkeypatch, capsys, error,
):
    registry, runtime, state, todo_id = update_workspace
    before = read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a")
    display = state.read_bytes()

    def reject(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(provider_update, "effect_runtime_result", reject)
    assert main(["--format", "json", "--registry", str(registry), "todo", "update",
                 "--goal-id", "goal-a", "--todo-id", todo_id, "--agent-id", "agent-a",
                 "--note", "Rejected edit"]) == 1
    failure = json.loads(capsys.readouterr().out)
    assert failure["error"] == str(error)
    assert "operation_id" not in failure and "recovery_command" not in failure
    assert read_canonical_todos_if_promoted(runtime_root=runtime, goal_id="goal-a") == before
    assert state.read_bytes() == display


def test_completion_validation_second_dispatch_preserves_original_identity(
    update_workspace, monkeypatch,
):
    registry, runtime, _state, todo_id = update_workspace
    calls = []

    def dispatch(method, request, **kwargs):
        assert method == "coordination.local_authority.todo_update"
        assert kwargs["timeout"] == CANONICAL_AUTHORITY_WRITE_TIMEOUT_SECONDS
        calls.append(dict(request))
        if len(calls) == 1:
            return {"status": "execute_validation", "provider_revision": "validation-revision"}
        raise EffectRuntimeResponseAmbiguous(method, timeout=kwargs["timeout"])

    monkeypatch.setattr(provider_update, "effect_runtime_result", dispatch)
    monkeypatch.setattr(provider_update, "execute_completion_validation_effects",
                        lambda *_args, **_kwargs: {"validation_passed": True})
    with pytest.raises(LocalCoordinationAuthorityUnavailable) as caught:
        provider_update.update_canonical_todo_if_promoted(
            registry_path=registry, runtime_root=runtime, goal_id="goal-a", todo_id=todo_id,
            actor_agent_id="agent-a", role="agent", text=None, note=None, dry_run=False,
            planning_intent={"status": "done"},
        )
    assert len(calls) == 2
    assert caught.value.code == "runtime_response_ambiguous"
    assert caught.value.payload["operation_id"] == calls[0]["operation_id"] == calls[1]["operation_id"]
    assert caught.value.payload["recovery_command"][-1] == calls[0]["operation_id"]


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
