"""Current source quota and health must still allow the actual native launch."""
from __future__ import annotations

import json
import textwrap
from contextlib import nullcontext
from pathlib import Path
import sys

import pytest

from loopx.configure_goal import configure_goal
from loopx.control_plane.turn_driver import codex_cli, codex_operation_host
from loopx.global_registry import sync_project_registry_to_global
from tests.control_plane.test_codex_operation_launch import _bindings, _cli, _fixture
from tests.control_plane.host_process_fixture import (
    WAIT_FOR_FIXTURE_LEASE_RELEASE,
    expect_lease_release_after_dispatch,
)
from tests.test_loopx_turn_codex_cli import _fake_codex


def _quota(registry: Path, runtime: Path, project: Path) -> dict:
    _, payload = _cli([
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "quota", "should-run", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--host-surface", "generic_cli", "--scheduler-owner", "outer_controller",
        "--execution-mode", "isolated_headless", "--context-delivery", "off",
    ])
    return payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("refusal", ["source_compute_zero", "missing_state"])
@pytest.mark.parametrize("host", ["exec", "operation"])
def test_current_source_admission_preempts_stale_mirror_native_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, refusal: str, host: str,
) -> None:
    runtime, source, native, model, arguments = _fixture(tmp_path, monkeypatch, provider)
    project = source.parent.parent
    synchronized = sync_project_registry_to_global(
        registry_path=source, runtime_root_override=str(runtime), goal_id="loopx-turn-fixture",
    )
    assert synchronized["ok"], synchronized
    mirror = runtime / "registry.global.json"
    before_mirror = mirror.read_bytes()
    source_payload = json.loads(source.read_text())
    state = project / source_payload["goals"][0]["state_file"]
    original_state = state.read_bytes()
    if host == "exec":
        executable, model = _fake_codex(tmp_path)
        native = tmp_path / "native-process"
        executable.write_text(executable.read_text().replace(
            "args = sys.argv[1:]", f"pathlib.Path({str(native)!r}).write_text('native')\nargs = sys.argv[1:]", 1,
        ))
        monkeypatch.setenv("FAKE_CODEX_LOG", str(model))
        arguments.remove("--codex-operation-tools")
        arguments[arguments.index("--codex-bin") + 1] = str(executable)
    arguments[arguments.index("--registry") + 1] = str(mirror)
    observations = []
    if refusal == "source_compute_zero":
        configured = configure_goal(registry_path=source, goal_id="loopx-turn-fixture", quota_compute=0, execute=True)
        assert configured["written"], configured
        current = _quota(source, runtime, project)
        assert current["should_run"] is False and "compute_quota_zero" in json.dumps(current), current
        assert _quota(mirror, runtime, project)["should_run"] is True
        observations.append(current)
    else:
        def lose_state_after_selection():
            state.rename(state.with_suffix(".saved"))
            for registry in (source, mirror):
                current = _quota(registry, runtime, project)
                assert current["should_run"] is False and current["status_health_ok"] is False, current
                assert current["state"] == "blocked_health", current
                observations.append(current)
        if host == "exec":
            original_input = codex_cli.prepare_codex_cli_input

            def lose_state_before_prompt(request):
                lose_state_after_selection()
                return original_input(request)

            monkeypatch.setattr(codex_cli, "prepare_codex_cli_input", lose_state_before_prompt)
        else:
            original_start = codex_operation_host.CodexChatAgentSession.start

            def lose_state_before_start(**kwargs):
                lose_state_after_selection()
                return original_start(**kwargs)

            monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, "start", lose_state_before_start)
    before_bindings = _bindings(runtime)
    code, payload = _cli([*arguments, "--execute"])
    assert observations and mirror.read_bytes() == before_mirror
    assert not native.exists() and not model.exists(), payload
    assert code == 1 and "goal_launch_not_allowed" in json.dumps(payload), payload
    assert _bindings(runtime) == before_bindings
    if refusal == "source_compute_zero":
        assert state.read_bytes() == original_state
    else:
        assert not state.exists()
    index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["task_text", "write_scope", "progress_note"])
@pytest.mark.parametrize("host", ["exec", "operation"])
def test_native_launch_rechecks_same_todo_after_planning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, change: str, host: str,
) -> None:
    runtime, registry, native, model, arguments = _fixture(tmp_path, monkeypatch, provider)
    project = registry.parent.parent
    if host == "exec":
        executable, model = _fake_codex(tmp_path)
        executable.write_text(executable.read_text().replace(
            "args = sys.argv[1:]", f"pathlib.Path({str(native)!r}).write_text('native')\nargs = sys.argv[1:]", 1,
        ))
        monkeypatch.setenv("FAKE_CODEX_LOG", str(model))
        arguments.remove("--codex-operation-tools")
        arguments[arguments.index("--codex-bin") + 1] = str(executable)
    old = _quota(registry, runtime, project)
    assert old["should_run"] is True, old
    todo_id = old["selected_todo"]["todo_id"]
    mutation = {
        "task_text": ["--text", "[P0] Verify the replacement fixture before any work."],
        "write_scope": ["--required-write-scope", "docs/evidence/**"],
        "progress_note": ["--note", "Independent observation received."],
    }[change]
    host_module = codex_cli if host == "exec" else codex_operation_host
    original_input = host_module.prepare_codex_cli_input
    readbacks = []

    def edit_after_planning(request):
        code, result = _cli([
            "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
            "todo", "update", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
            "--todo-id", todo_id, *mutation,
        ])
        assert code == 0 and result["ok"], result
        current = _quota(registry, runtime, project)
        assert current["should_run"] is True and current["effective_action"] == old["effective_action"], current
        assert current["selected_todo"]["todo_id"] == todo_id
        readbacks.append((result, _bindings(runtime)))
        return original_input(request)

    monkeypatch.setattr(host_module, "prepare_codex_cli_input", edit_after_planning)
    code, payload = _cli([*arguments, "--execute"])
    assert len(readbacks) == 1
    if change == "progress_note":
        assert native.exists() and model.exists(), payload
    else:
        # Operation mode already started app-server; it must not start a
        # model Turn or publish its Session binding on the stale request.
        assert native.exists() is (host == "operation") and not model.exists(), payload
        assert code == 1 and "current_turn_work_changed" in json.dumps(payload), payload
        assert _bindings(runtime) == readbacks[0][1]
        index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
        assert not index.exists() or "quota_slot_spent" not in index.read_text()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["unchanged", "text", "renew_lease", "replace_lease", "release_during_dispatch"])
def test_stdio_retry_checks_original_task_before_launch(tmp_path, monkeypatch, provider, change):
    from loopx.control_plane.coordination.runtime_shadow import build_todo_runtime_shadow_projection
    from loopx.control_plane.todos.active_state_todo_parser import parse_active_state_todos
    from tests.control_plane.canonical_authority_fixture import initialize_canonical_authority, isolate_sqlite_runtime
    from tests.control_plane.test_turn_host_decision_capture import _fixture_module

    isolate_sqlite_runtime(tmp_path, monkeypatch)
    fixture = _fixture_module()
    project, runtime, workspace, registry = fixture._write_fixture(tmp_path, turn_count=2)
    state = project / ".codex/goals" / fixture.GOAL_ID / "ACTIVE_GOAL_STATE.md"
    todos = parse_active_state_todos(state.read_text(), state_path=state, item_limit=None)
    uses_lease = change in {"renew_lease", "replace_lease", "release_during_dispatch"}
    initialize_canonical_authority(runtime, fixture.GOAL_ID,
        build_todo_runtime_shadow_projection(goal_id=fixture.GOAL_ID,
            handoff_mode="hard_lease" if uses_lease else "soft_claim",
            todos=todos["agent_todos"]["items"]), state_path=state, provider=provider)
    marker = workspace / "process-count"
    runner = tmp_path / "runner.py"
    runner.write_text(f'''import json
from pathlib import Path

def run_dsh_turn(**options):
    workspace = Path(options["workspace"])
    marker = workspace / "process-count"
    first = not marker.exists()
    marker.write_text("1" if first else "2")
    if first:
        raise TimeoutError("Fixture failed before producing a result")
{textwrap.indent(WAIT_FOR_FIXTURE_LEASE_RELEASE, "    ")}
    (workspace / {fixture.MARKER_NAME!r}).write_text({fixture._marker_value(1)!r})
    return json.dumps({{"result_kind": "validated_progress", "classification": "checked_work",
        "summary": "The original work passed independent validation.", "next_action": "Continue.",
        "work_evidence": ["artifact:marker"], "validation_evidence": ["fixture:validate-marker"]}})
''', encoding="utf-8")
    prefix = ["--registry", str(registry), "--runtime-root", str(runtime), "--format", "json"]
    subject = ["--goal-id", fixture.GOAL_ID, "--agent-id", fixture.AGENT_ID]

    def lease(action, key, version, *options):
        code, payload = fixture._run_cli([
            *prefix, "task-lease", action, "--goal-id", fixture.GOAL_ID,
            "--todo-id", fixture.TODO_ID, "--owner", fixture.AGENT_ID,
            "--idempotency-key", key, "--expected-version", str(version), *options,
        ])
        assert code == 0, payload
        return payload["lease"]

    original = lease("acquire", "original-attempt", 0, "--ttl-seconds", "300") if uses_lease else None
    adapter = [sys.executable, "-m", "loopx.dsh_goal_mode", "--workspace", str(workspace),
        "--dsh-runner", str(runner), "--dsh-home", str(tmp_path / "dsh-home")]
    validator = [sys.executable, "-c", "from pathlib import Path; import json,sys; json.load(sys.stdin); "
        f"assert Path({fixture.MARKER_NAME!r}).read_text() == {fixture._marker_value(1)!r}"]
    argv = [*prefix, "turn", "run-once", *subject, "--host", "generic-cli", "--project", str(workspace),
        "--execution-mode", "isolated-headless", "--no-global-sync", "--timeout-seconds", "20",
        "--host-adapter-request-schema", "loopx_turn_host_request_v1",
        "--host-adapter-command-json", json.dumps(adapter),
        "--validation-command-json", json.dumps(validator), "--execute"]
    code, first = fixture._run_cli([*argv, "--turn-instance-id", "bound-stdio-retry"])
    assert code == 1 and first["status"] == "failed", first
    assert marker.read_text() == "1" and fixture._quota_spend_count(runtime) == 0
    if change == "text":
        binding = [*subject, "--todo-id", fixture.TODO_ID]
        code, before = fixture._run_cli([*prefix, "todo", "list", *binding])
        assert code == 0
        code, changed = fixture._run_cli([*prefix, "todo", "update", *binding, "--execute",
            "--text", before["todo"]["text"] + " Follow the replacement requirement.",
            "--update-operation-id", "change-captured-work",
            "--update-expected-provider-revision", before["authority_read"]["provider_revision"]])
        assert code == 0 and changed["changed"], changed
    if change == "renew_lease":
        assert original is not None
        renewed = lease("renew", original["idempotency_key"], original["version"], "--ttl-seconds", "600")
        assert renewed["lease_epoch"] == original["lease_epoch"]
    if change == "replace_lease":
        assert original is not None
        released = lease("release", original["idempotency_key"], original["version"])
        replacement = lease("acquire", "replacement-attempt", released["version"], "--ttl-seconds", "300")
        assert replacement["lease_epoch"] > original["lease_epoch"]
    recovery = [*argv, "--resume-turn-key", first["resume_turn_key"]]
    dispatch = nullcontext()
    if change == "release_during_dispatch":
        dispatch = expect_lease_release_after_dispatch(monkeypatch, operation="turn_external_host_launch",
            registry=registry, runtime=runtime, goal_id=fixture.GOAL_ID, todo_id=fixture.TODO_ID,
            lease=original, dispatch_marker=marker)
    with dispatch:
        code, result = fixture._run_cli([*recovery, "--retry-failed-turn"])
    refused = change in {"text", "replace_lease"}
    if refused:
        assert code == 1 and result["error_code"] == "current_turn_work_changed", result
        assert result["host_failure"]["kind"] == "contract_rejected", result
        assert not any(result["effects"].values())
    else:
        assert code == 0 and result["status"] == "committed", result
    _, replay = fixture._run_cli(recovery)
    assert replay["replayed"] and not any(replay["effects"].values())
    assert marker.read_text() == ("1" if refused else "2")
    assert fixture._quota_spend_count(runtime) == (0 if refused else 1)
