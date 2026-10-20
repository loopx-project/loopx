"""Actual operation-equipped CLI launch honors the original Goal admission."""
from __future__ import annotations

import contextlib
import io
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.chat_agent import CodexChatAgentSession
from loopx.cli import main as cli_main
from loopx.control_plane.goals import activation_service
from loopx.control_plane.projects.registry_codec import project_registry_transaction
from loopx.control_plane.turn_driver import codex_operation_host
from loopx.global_registry import sync_project_registry_to_global
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.test_codex_operation_host import FAKE_SERVER
from tests.test_loopx_turn_codex_cli import _fake_codex, _request, _session_files
from tests.test_loopx_turn_driver import _write_live_fixture


def _fixture(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    native, turns = tmp_path / "native-starts", tmp_path / "native-turns"
    executable = tmp_path / "fake-codex-operation"
    executable.write_text(FAKE_SERVER.replace(
        "import json, sys", "import json, sys, pathlib\n"
        f"with pathlib.Path({str(native)!r}).open('a') as marker: marker.write('native\\n')", 1,
    ).replace(
        'elif method == "turn/start":', 'elif method == "turn/start":\n'
        f"        with pathlib.Path({str(turns)!r}).open('a') as marker: marker.write(row['params']['threadId'] + '\\n')", 1,
    ))
    executable.chmod(0o700)
    arguments = [
        "--registry", str(registry), "--runtime-root", str(runtime), "--format", "json",
        "turn", "run-once", "--goal-id", "loopx-turn-fixture", "--agent-id", "codex-fixture",
        "--host", "codex-cli", "--project", str(project), "--scan-root", str(project),
        "--no-global-sync", "--codex-operation-tools", "--codex-bin", str(executable),
        "--codex-model", "test-model", "--codex-reasoning-effort", "xhigh",
        "--codex-sandbox", "read-only", "--timeout-seconds", "30",
        "--turn-instance-id", "operation-launch-fixture",
        "--validation-command-json", json.dumps([sys.executable, "-c", "pass"]),
    ]
    return runtime, registry, native, turns, arguments


def _cli(arguments: list[str]) -> tuple[int, dict]:
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main(arguments)
    return code, json.loads(output.getvalue())


def _bindings(runtime: Path) -> dict[str, bytes]:
    return {str(path.relative_to(runtime)): path.read_bytes() for path in _session_files(runtime)}


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("boundary", ["process", "turn"])
@pytest.mark.parametrize("route", ["source", "mirror", "resume"])
def test_operation_cli_stop_preempts_actual_native_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, boundary: str, route: str,
) -> None:
    runtime, registry, native, turns, arguments = _fixture(tmp_path, monkeypatch, provider)
    source = registry
    original_mirror = None
    if route == "mirror":
        sync = sync_project_registry_to_global(
            registry_path=source, runtime_root_override=str(runtime), goal_id="loopx-turn-fixture",
        )
        assert sync["ok"], sync
        registry = runtime / "registry.global.json"
        original_mirror = registry.read_bytes()
        arguments[arguments.index("--registry") + 1] = str(registry)
    elif route == "resume":
        # Establish the original native binding through the production owned
        # transport; the later Stop still enters through actual CLI options.
        request = _request()
        request["turn_envelope"]["goal_id"] = "loopx-turn-fixture"
        seeded = codex_operation_host.run_codex_operation_host(
            request, runtime_root=runtime, registry_path=registry, project=source.parent.parent,
            codex_bin=arguments[arguments.index("--codex-bin") + 1],
            model="test-model", reasoning_effort="xhigh", timeout_seconds=30,
        )
        assert seeded["result_kind"] == "wait" and _bindings(runtime)
    before_native = len(native.read_text().splitlines()) if native.exists() else 0
    before_turns = turns.read_bytes() if turns.exists() else None
    stopped = []
    original_bindings = []

    def stop_goal() -> None:
        original_bindings.append(_bindings(runtime))
        with monkeypatch.context() as fault:
            if route == "mirror":
                fault.setattr(activation_service, "sync_project_registry_to_global", lambda **kwargs: {
                    "ok": False, "error": "fixture mirror sync failed after source Stop committed",
                })
            result = activation_service.set_goal_activation_state(
                registry_path=registry, goal_id="loopx-turn-fixture", state="stopped",
                reason="Owner stopped the admitted Goal before its native boundary.",
                actor_kind="owner", execute=True,
            )
        if route == "mirror":
            assert result["written"] is True and result["partial_write"] is True, result
            assert registry.read_bytes() == original_mirror
        else:
            assert result["ok"] and result["readback"]["verified"], result
        assert json.loads(source.read_text())["goals"][0]["activation"]["state"] == "stopped"
        stopped.append(result)

    if boundary == "process":
        original_start = CodexChatAgentSession.start

        def stop_before_start(**kwargs):
            stop_goal()
            return original_start(**kwargs)

        monkeypatch.setattr(CodexChatAgentSession, "start", stop_before_start)
    else:
        original_prompt = codex_operation_host._prompt

        def stop_before_turn(request):
            stop_goal()
            return original_prompt(request)

        monkeypatch.setattr(codex_operation_host, "_prompt", stop_before_turn)
    code, payload = _cli([*arguments, "--execute"])
    assert len(stopped) == 1, payload
    assert (turns.read_bytes() if turns.exists() else None) == before_turns, payload
    native_count = len(native.read_text().splitlines()) if native.exists() else 0
    assert native_count == before_native + (boundary == "turn"), payload
    assert code == 1 and "goal_stopped" in json.dumps(payload), payload
    assert _bindings(runtime) == original_bindings[0]
    index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_legacy_active_variant_cli_preview_and_real_launch_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    runtime, registry, _, _, arguments = _fixture(tmp_path, monkeypatch, provider)
    with project_registry_transaction(registry, operation="fixture_active_registration") as transaction:
        payload = transaction.payload_copy()
        payload["goals"][0]["status"] = "active-read-only"
        transaction.commit(payload)
    executable, native_log = _fake_codex(tmp_path)
    monkeypatch.setenv("FAKE_CODEX_LOG", str(native_log))
    arguments.remove("--codex-operation-tools")
    arguments[arguments.index("--codex-bin") + 1] = str(executable)
    code, preview = _cli(arguments)
    assert code == 0 and preview["effects"]["host_invoked"] is False, preview
    code, executed = _cli([*arguments, "--execute"])
    assert code == 0 and executed["effects"]["host_invoked"] is True, executed
    assert native_log.exists() and _bindings(runtime)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_operation_dispatch_releases_goal_lock_before_native_response_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
) -> None:
    runtime, registry, native, turns, arguments = _fixture(tmp_path, monkeypatch, provider)
    release = tmp_path / "release-native-response"
    executable = Path(arguments[arguments.index("--codex-bin") + 1])
    executable.write_text(executable.read_text().replace(
        '        assert row["params"]["outputSchema"]["type"] == "object"',
        f"        import time\n        while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)\n"
        '        assert row["params"]["outputSchema"]["type"] == "object"', 1,
    ))
    with ThreadPoolExecutor(max_workers=2) as executor:
        host = executor.submit(_cli, [*arguments, "--execute"])
        try:
            deadline = time.monotonic() + 20
            while not turns.exists() and not host.done() and time.monotonic() < deadline:
                time.sleep(.01)
            assert turns.exists(), host.result() if host.done() else "native dispatch was not reached"
            bindings = _bindings(runtime)
            stop = executor.submit(
                activation_service.set_goal_activation_state,
                registry_path=registry, goal_id="loopx-turn-fixture", state="stopped",
                reason="Owner Stop must complete while the original native response is pending.",
                actor_kind="owner", execute=True,
            )
            stopped = stop.result(timeout=5)
            assert stopped["ok"] and stopped["readback"]["verified"], stopped
            assert not host.done() and not release.exists()
        finally:
            release.write_text("continue the original fake native response")
        host.result(timeout=30)
    assert len(native.read_text().splitlines()) == 1
    assert turns.read_text().splitlines() == ["owned-app-server-thread"]
    assert _bindings(runtime) == bindings
    index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


def test_native_process_is_reaped_if_launch_guard_exit_is_interrupted(tmp_path, monkeypatch):
    import loopx.chat_agent as chat_agent

    executable = tmp_path / "fake-native-waiter"
    executable.write_text(f"#!{sys.executable}\nimport sys\nsys.stdin.read()\n")
    executable.chmod(0o700)
    started = []
    popen = chat_agent.subprocess.Popen

    def observe_popen(*args, **kwargs):
        process = popen(*args, **kwargs)
        started.append(process)
        return process

    @contextlib.contextmanager
    def guard():
        yield
        raise KeyboardInterrupt("fixture interrupted launch guard cleanup")

    monkeypatch.setattr(chat_agent.subprocess, "Popen", observe_popen)
    with pytest.raises(KeyboardInterrupt, match="fixture interrupted"):
        CodexChatAgentSession.start(
            codex_bin=str(executable), work_dir=tmp_path, goal_id=None, objective="Synthetic cleanup",
            isolate_process_tree=True, launch_guard=guard,
        )
    assert len(started) == 1 and started[0].poll() is not None
