"""Actual operation-equipped CLI launch honors the original Goal admission."""
from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from loopx.chat_agent import CodexChatAgentSession
from loopx.cli import main as cli_main
from loopx.control_plane.effect_runtime import EffectRuntimeStartupError
from loopx.control_plane.goals import activation_service, first_party_host_admission
from loopx.control_plane.goals.first_party_host_admission import FirstPartyHostGoalAdmission
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
        "--host", "codex-cli", "--project", str(project),
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


def _configure_fixture_goal(registry: Path, runtime: Path, *settings: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "loopx.entrypoint", "--registry", str(registry),
         "--runtime-root", str(runtime), "--format", "json", "configure-goal",
         "--goal-id", "loopx-turn-fixture", *settings, "--execute"],
        capture_output=True, text=True, check=False,
    )
    payload = json.loads(result.stdout)
    assert result.returncode == 0 and payload["ok"] and payload["changed"], payload
    return payload


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
        original_input = codex_operation_host.prepare_codex_cli_input

        def stop_before_turn(request):
            stop_goal()
            return original_input(request)

        monkeypatch.setattr(codex_operation_host, "prepare_codex_cli_input", stop_before_turn)
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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("failure", ["stop", "inventory-unavailable", "authority-unavailable", "agent-revoked"])
def test_running_operation_stop_waits_for_owned_native_terminal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, failure: str,
) -> None:
    runtime, registry, _, turns, arguments = _fixture(tmp_path, monkeypatch, provider)
    executable = Path(arguments[arguments.index("--codex-bin") + 1])
    events = tmp_path / "native-control"
    authority_unavailable = threading.Event()
    original_effect_runtime_result = first_party_host_admission.effect_runtime_result

    def observe_authority(method, facts, **kwargs):
        if (authority_unavailable.is_set() and method == "goal.first_party_host_runtime.decide"
                and facts["operation"] == "require_current"):
            raise EffectRuntimeStartupError(
                "fixture authority runtime unavailable", diagnostic_code="fixture_unavailable",
            )
        return original_effect_runtime_result(method, facts, **kwargs)

    monkeypatch.setattr(first_party_host_admission, "effect_runtime_result", observe_authority)
    inventory_available = failure != "inventory-unavailable"
    server = executable.read_text()
    server = server.replace(
        'key = None',
        f'key = None\ninterrupted = False\nowned_live = True\ncontrol_log = {str(events)!r}\n'
        f'inventory_available = {inventory_available!r}',
        1,
    )
    after_start = '        emit({"id": row["id"], "result": {"turn": {"id": turn}}})'
    assert after_start in server
    server = server.replace(after_start, after_start + "\n        continue", 1)
    position = '    elif row.get("id") == 50:'
    assert position in server
    server = server.replace(position, """    elif method == "thread/turns/list":
        emit({"id": row["id"], "result": {"data": [
            {"id": turn, "status": "interrupted" if interrupted else "inProgress"}]}})
    elif method == "turn/interrupt":
        assert row["params"] == {"threadId": thread, "turnId": turn}
        interrupted = True
        with open(control_log, "a") as log: log.write("interrupt\\n")
        emit({"id": row["id"], "result": {}})
    elif method == "thread/items/list":
        assert interrupted and row["params"]["turnId"] == turn
        emit({"id": row["id"], "result": {"data": [{"turnId": turn,
            "item": {"id": "owned-item", "type": "commandExecution"}}]}})
    elif method == "thread/backgroundTerminals/list":
        if not inventory_available:
            emit({"id": row["id"], "error": {"code": -32603, "message": "fixture unavailable"}})
        else:
            terminals = [{"itemId": "other-item", "processId": "other-process"}]
            if owned_live:
                terminals.append({"itemId": "owned-item", "processId": "owned-process"})
            else:
                with open(control_log, "a") as log: log.write("owned-gone\\n")
            emit({"id": row["id"], "result": {"data": terminals}})
    elif method == "thread/backgroundTerminals/terminate":
        assert row["params"] == {"threadId": thread, "processId": "owned-process"}
        owned_live = False
        with open(control_log, "a") as log: log.write("terminate-owned\\n")
        emit({"id": row["id"], "result": {"terminated": True}})
""" + position, 1)
    executable.write_text(server)
    arguments[arguments.index("--timeout-seconds") + 1] = "12"

    with ThreadPoolExecutor(max_workers=1) as executor:
        host = executor.submit(_cli, [*arguments, "--execute"])
        deadline = time.monotonic() + 20
        while not turns.exists() and not host.done() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert turns.exists(), host.result() if host.done() else "native dispatch was not reached"
        if failure == "authority-unavailable":
            authority_unavailable.set()
        elif failure == "agent-revoked":
            _configure_fixture_goal(registry, runtime, "--registered-agent", "replacement-agent")
        else:
            stopped = activation_service.set_goal_activation_state(
                registry_path=registry, goal_id="loopx-turn-fixture", state="stopped",
                reason="Fixture Stop after native Turn dispatch.", actor_kind="owner", execute=True,
            )
            assert stopped["ok"] and stopped["readback"]["verified"], stopped
        code, payload = host.result(timeout=12)

    assert code == 1, payload
    expected = {
        "stop": "goal_stopped",
        "inventory-unavailable": "codex_operation_host_stop_unconfirmed",
        "authority-unavailable": "goal_authority_unavailable",
        "agent-revoked": "stale_goal_authority",
    }[failure]
    assert expected in json.dumps(payload), payload
    expected_events = ["interrupt", "terminate-owned", "owned-gone"] if inventory_available else ["interrupt"]
    assert events.read_text().splitlines() == expected_events
    index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("change", ["unrelated-registration", "review-order"])
def test_running_operation_survives_non_authority_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, change: str,
) -> None:
    runtime, registry, _, _, arguments = _fixture(tmp_path, monkeypatch, provider)
    executable = Path(arguments[arguments.index("--codex-bin") + 1])
    waiting, proceed = tmp_path / "native-waiting", tmp_path / "native-proceed"
    after_start = '        emit({"id": row["id"], "result": {"turn": {"id": turn}}})'
    server = executable.read_text()
    assert after_start in server
    executable.write_text(server.replace(
        after_start,
        after_start + f"\n        pathlib.Path({str(waiting)!r}).touch()"
        + f"\n        while not pathlib.Path({str(proceed)!r}).exists(): time.sleep(.01)",
        1,
    ))

    with ThreadPoolExecutor(max_workers=1) as executor:
        host = executor.submit(_cli, [*arguments, "--execute"])
        try:
            deadline = time.monotonic() + 20
            while not waiting.exists() and not host.done() and time.monotonic() < deadline:
                time.sleep(0.01)
            assert waiting.exists(), host.result() if host.done() else "native Turn was not acknowledged"
            if change == "review-order":
                configured = _configure_fixture_goal(registry, runtime, "--pr-review-order", "reverse")
                assert configured["after"]["pull_request_review"]["review_order"] == "reverse"
            else:
                with project_registry_transaction(registry, operation="register_other_fixture_goal") as transaction:
                    registry_payload = transaction.payload_copy()
                    registry_payload["goals"].append({"id": "other-goal", "status": "active"})
                    assert transaction.commit(registry_payload)
        finally:
            proceed.touch()
        code, payload = host.result(timeout=12)

    assert code == 0 and payload["ok"], payload
    assert payload["result_kind"] == "wait", payload


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


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("dispatch_window", ["write_backpressure", "admission_deadline"])
def test_native_turn_dispatch_failure_drains_before_stop_writer_is_released(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, dispatch_window: str,
) -> None:
    """A full stdin pipe cannot hold Stop or deliver a late native Turn."""
    runtime, registry, _, turns, arguments = _fixture(tmp_path, monkeypatch, provider)
    release = tmp_path / "release-native-input"
    executable = Path(arguments[arguments.index("--codex-bin") + 1])
    ready_response = '        emit({"id": row["id"], "result": {"thread": {"id": thread}, "model": "test-model", "reasoningEffort": "xhigh"}})'
    executable.write_text(executable.read_text().replace(
        ready_response,
        ready_response + "\n        import time\n"
        f"        while not pathlib.Path({str(release)!r}).exists(): time.sleep(.01)",
        1,
    ))
    arguments[arguments.index("--timeout-seconds") + 1] = "1"
    original_input = codex_operation_host.prepare_codex_cli_input

    def oversized_input(request):
        text, schema = original_input(request)
        return text + "\n" + "x" * (8 * 1024 * 1024), schema

    monkeypatch.setattr(codex_operation_host, "prepare_codex_cli_input", oversized_input)
    sessions, drained_before_release = [], []
    entered = threading.Event()
    original_start = CodexChatAgentSession.start
    original_lifetime = FirstPartyHostGoalAdmission.launch_lifetime

    def start(**kwargs):
        session = original_start(**kwargs)
        sessions.append(session)
        return session

    @contextlib.contextmanager
    def lifetime(self, *, operation):
        with original_lifetime(self, operation=operation):
            if operation != "codex_operation_turn_start":
                yield
                return
            entered.set()
            try:
                if dispatch_window == "admission_deadline":
                    # Expiry can occur in canonical admission before the first
                    # write. The Session owner still retires its original peer
                    # before releasing the fence; no helper-side partial write
                    # is needed to expose this boundary.
                    time.sleep(1.1)
                yield
            finally:
                drained_before_release.append(sessions[0].process.poll() is not None)

    monkeypatch.setattr(CodexChatAgentSession, "start", start)
    monkeypatch.setattr(FirstPartyHostGoalAdmission, "launch_lifetime", lifetime)
    with ThreadPoolExecutor(max_workers=2) as executor:
        host = executor.submit(_cli, [*arguments, "--execute"])
        try:
            assert entered.wait(timeout=30), host.result() if host.done() else "native launch not reached"
            began = time.monotonic()
            stop = executor.submit(
                activation_service.set_goal_activation_state,
                registry_path=registry, goal_id="loopx-turn-fixture", state="stopped",
                reason="Synthetic Stop while native stdin is backpressured.",
                actor_kind="owner", execute=True,
            )
            stopped = stop.result(timeout=5)
            code, payload = host.result(timeout=5)
            elapsed = time.monotonic() - began
            assert stopped["ok"] and stopped["readback"]["verified"], stopped
            assert code == 1 and "timeout" in json.dumps(payload), payload
            assert elapsed < 3, elapsed  # One-second send plus finite peer cleanup.
            assert drained_before_release == [True]
            assert sessions[0].process.poll() is not None
            assert not turns.exists() and not payload["effects"]["quota_spent"], payload
        finally:
            # This only unblocks a broken implementation's fixture teardown.
            # The passing path proved cancellation before the peer may read.
            release.touch()
            if sessions:
                sessions[0].close()
    assert not turns.exists()
    index = runtime / "goals/loopx-turn-fixture/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


def test_guarded_native_dispatch_preserves_large_unicode_input(tmp_path, monkeypatch):
    _, _, _, turns, arguments = _fixture(tmp_path, monkeypatch, "sqlite")
    observed = tmp_path / "accepted-native-input"
    monkeypatch.setenv("FAKE_OPERATION_PROMPT_FILE", str(observed))
    original_input = codex_operation_host.prepare_codex_cli_input
    supplement = "完整输入☃" * (1024 * 1024)

    def supplemented_input(request):
        text, schema = original_input(request)
        return text + "\n" + supplement, schema

    monkeypatch.setattr(codex_operation_host, "prepare_codex_cli_input", supplemented_input)
    code, payload = _cli([*arguments, "--execute"])
    assert code == 0 and payload["effects"]["host_invoked"], payload
    assert turns.read_text().splitlines() == ["owned-app-server-thread"]
    assert supplement in observed.read_text()
