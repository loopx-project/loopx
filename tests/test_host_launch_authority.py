"""Actual CLI/native launch boundaries; disposable Goals, no live model calls."""

from __future__ import annotations

import contextlib
import io
import json
import sys
import threading
from pathlib import Path

import pytest

from loopx.cli import main as cli_main
from loopx.cli_commands import lark_inbox, turn_decision
from loopx.configure_goal import configure_goal
from loopx.control_plane.goals import activation_service
from loopx.control_plane.projects.registry_codec import (
    project_registry_transaction,
    source_session_registry_transaction,
)
from loopx.control_plane.turn_driver import codex_cli, codex_operation_host
from tests.control_plane.canonical_authority_fixture import isolate_sqlite_runtime
from tests.control_plane.test_effect_turn_live_quota_decision import (
    _material_review_urgency,
    _turn_start_dispatch,
)
from test_codex_operation_host import FAKE_SERVER
from test_loopx_turn_codex_cli import _fake_codex, _session_files, _source_admission
from test_loopx_turn_driver import _write_live_fixture


GOAL = "loopx-turn-fixture"
AGENT = "codex-fixture"


def _cli(registry: Path, runtime: Path, *arguments: str):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        code = cli_main([
            "--registry", str(registry), "--runtime-root", str(runtime),
            "--format", "json", *arguments,
        ])
    return code, json.loads(output.getvalue())


def _mirror(source: Path, runtime: Path) -> Path:
    registry = runtime / "registry.global.json"
    payload = json.loads(source.read_text())
    payload["registry_role"] = "global-local"
    payload["goals"][0]["source_registry"] = str(source)
    registry.write_text(json.dumps(payload))
    return registry


def _host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation_tools: bool):
    native_start, turn_start = tmp_path / "native-start", tmp_path / "turn-start"
    if operation_tools:
        executable = tmp_path / "fake-codex-operation"
        executable.write_text(FAKE_SERVER.replace(
            "import json, sys", "import json, sys, pathlib\n"
            f"pathlib.Path({str(native_start)!r}).write_text('started')", 1,
        ).replace(
            'elif method == "turn/start":', 'elif method == "turn/start":\n'
            f"        pathlib.Path({str(turn_start)!r}).write_text('started')", 1,
        ))
        executable.chmod(0o700)
    else:
        executable, turn_start = _fake_codex(tmp_path)
        executable.write_text(executable.read_text().replace(
            "args = sys.argv[1:]",
            f"pathlib.Path({str(native_start)!r}).write_text('started')\nargs = sys.argv[1:]", 1,
        ))
        monkeypatch.setenv("FAKE_CODEX_LOG", str(turn_start))
    arguments = ["--codex-bin", str(executable)]
    if operation_tools:
        arguments.extend(["--codex-operation-tools", "--codex-model", "test-model",
                          "--codex-reasoning-effort", "xhigh"])
    return arguments, native_start, turn_start


def _run(registry: Path, runtime: Path, project: Path, host_arguments: list[str]):
    return _cli(registry, runtime,
        "turn", "run-once", "--goal-id", GOAL, "--agent-id", AGENT,
        "--host", "codex-cli", "--project", str(project), "--scan-root", str(project),
        "--codex-sandbox", "read-only", "--no-global-sync", "--iteration-context", "fresh",
        "--turn-instance-id", "launch-authority-fixture",
        "--validation-command-json", json.dumps([sys.executable, "-c", "pass"]),
        *host_arguments, "--execute",
    )


def _assert_no_settlement(runtime: Path, payload: dict):
    assert not payload["effects"]["state_written"], payload
    assert not payload["effects"]["quota_spent"], payload
    index = runtime / f"goals/{GOAL}/runs/index.jsonl"
    assert not index.exists() or "quota_slot_spent" not in index.read_text()


@pytest.mark.parametrize("provider,route", [("file", "global"), ("sqlite", "project"), ("sqlite", "global")])
@pytest.mark.parametrize("boundary", ["process", "turn"])
def test_operation_cli_observes_verified_stop_at_each_native_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, route: str, boundary: str,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, source = _write_live_fixture(tmp_path, provider=provider)
    registry = _mirror(source, runtime) if route == "global" else source
    host_arguments, native_start, turn_start = _host(tmp_path, monkeypatch, True)
    stops = []

    def stop():
        result = activation_service.set_goal_activation_state(
            registry_path=registry, runtime_root_override=str(runtime), goal_id=GOAL,
            state="stopped", reason="Synthetic owner Stop before native dispatch.",
            actor_kind="owner", execute=True,
        )
        assert result["readback"]["verified"] is True, result
        stops.append(result)

    if boundary == "process":
        start = codex_operation_host.CodexChatAgentSession.start

        def stopped_start(**arguments):
            stop()
            return start(**arguments)

        monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, "start", stopped_start)
    else:
        prompt = codex_operation_host._prompt

        def stopped_prompt(request):
            # App-server/thread initialization already completed, but the
            # request must never cross the native model Turn boundary.
            assert native_start.exists()
            stop()
            return prompt(request)

        monkeypatch.setattr(codex_operation_host, "_prompt", stopped_prompt)
    code, payload = _run(registry, runtime, project, host_arguments)
    assert stops and code == 1 and "goal_stopped" in json.dumps(payload), payload
    assert native_start.exists() is (boundary == "turn")
    assert not turn_start.exists(), payload
    if boundary == "process":
        assert not _session_files(runtime)
    _assert_no_settlement(runtime, payload)


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("operation_tools", [False, True])
@pytest.mark.parametrize("drift", ["source_quota", "source_health", "global_health", "local_health"])
def test_actual_launch_consumes_current_source_quota_and_health(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
    operation_tools: bool, drift: str,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, source = _write_live_fixture(tmp_path, provider=provider)
    mirror = _mirror(source, runtime) if drift != "local_health" else None
    registry = source if drift in {"source_health", "local_health"} else mirror
    assert registry is not None
    host_arguments, native_start, turn_start = _host(tmp_path, monkeypatch, operation_tools)
    before_registry = registry.read_bytes()

    if drift == "source_quota":
        configure_goal(registry_path=source, goal_id=GOAL, quota_compute=0, execute=True)
        assert json.loads(source.read_text())["goals"][0]["quota"]["compute"] == 0
        assert registry.read_bytes() == before_registry
        code, current = _cli(source, runtime, "quota", "should-run", "--goal-id", GOAL,
                             "--agent-id", AGENT, "--runtime-profile", "generic_cli")
        assert code == 0 and current["should_run"] is False
    else:
        state = project / f".codex/goals/{GOAL}/ACTIVE_GOAL_STATE.md"

        def remove_state():
            state.rename(state.with_suffix(".removed"))
            assert registry.read_bytes() == before_registry

        if operation_tools:
            start = codex_operation_host.CodexChatAgentSession.start

            def missing_state_start(**arguments):
                remove_state()
                return start(**arguments)

            monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, "start", missing_state_start)
        else:
            prompt = codex_cli._prompt

            def missing_state_prompt(request):
                assert request["turn_envelope"]["action"]["selected_todo"]["todo_id"] == "todo_fixture0001"
                remove_state()
                return prompt(request)

            monkeypatch.setattr(codex_cli, "_prompt", missing_state_prompt)

    code, payload = _run(registry, runtime, project, host_arguments)
    assert code == 1 and "goal_launch_not_allowed" in json.dumps(payload), payload
    assert not native_start.exists() and not turn_start.exists(), payload
    assert not _session_files(runtime)
    _assert_no_settlement(runtime, payload)
    if drift == "local_health":
        assert not (runtime / "registry.global.json").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_first_normal_turn_with_declared_state_needs_no_global_registry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    host_arguments, native_start, turn_start = _host(tmp_path, monkeypatch, False)
    assert not (runtime / "registry.global.json").exists()
    assert not _session_files(runtime)
    code, payload = _run(registry, runtime, project, host_arguments)
    assert code == 0 and payload["status"] == "committed", payload
    assert native_start.exists() and turn_start.exists(), payload
    assert payload["effects"]["quota_spent"] is True, payload
    assert not (runtime / "registry.global.json").exists()


@pytest.mark.parametrize("provider", ["file", "sqlite"])
@pytest.mark.parametrize("operation_tools", [False, True])
@pytest.mark.parametrize("late_material", [False, True])
def test_actual_launch_preserves_same_turn_inbox_read_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, operation_tools: bool,
    late_material: bool,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    state = project / f".codex/goals/{GOAL}/ACTIVE_GOAL_STATE.md"
    original_state = state.read_bytes()
    with project_registry_transaction(registry, operation="synthetic_fresh_inbox") as tx:
        current = tx.payload_copy()
        current["goals"][0].setdefault("control_plane", {})["lark_event_inbox"] = {
            "enabled": True, "config_path": ".loopx/config/inbox.json",
        }
        tx.commit(current)
    monkeypatch.setattr(
        "loopx.control_plane.quota.goal_boundary.operator_inbox_binding",
        lambda **_kwargs: {"status": "verified", "attention_required": False},
    )
    pending_count = 1

    def urgency(**_kwargs):
        return _material_review_urgency(pending_count=pending_count)

    monkeypatch.setattr(turn_decision, "build_lark_operator_inbox_urgency_projector",
                        lambda **_kwargs: urgency)
    dispatches = []

    def sync(**_kwargs):
        dispatch = _turn_start_dispatch(commands=(f"loopx inbox drain --goal-id {GOAL}",))
        dispatches.append(dispatch)
        return dispatch

    monkeypatch.setattr(lark_inbox, "dispatch_goal_lark_turn_start_hooks", sync)
    host_arguments, native_start, turn_start = _host(tmp_path, monkeypatch, operation_tools)
    if late_material:
        owner = codex_operation_host if operation_tools else codex_cli
        prompt = owner._prompt

        def changed_inbox_prompt(request):
            nonlocal pending_count
            # A later message was not in this Turn's initial required read.
            # Operation Host is already open here, before its model Turn.
            pending_count = 2
            return prompt(request)

        monkeypatch.setattr(owner, "_prompt", changed_inbox_prompt)
    code, payload = _run(registry, runtime, project, host_arguments)
    assert len(dispatches) == 1
    if late_material:
        assert code == 1 and "goal_launch_not_allowed" in json.dumps(payload), payload
        assert native_start.exists() is operation_tools
        assert not turn_start.exists(), payload
        # Keep the already performed inbox sync visible even when launch is
        # refused. It cannot stand in for work writeback or quota settlement.
        assert payload["effects"]["state_written"] is True, payload
        assert not payload["effects"]["quota_spent"], payload
        # Failed execution may keep an effect unknown; inspect its actual
        # business state and spend history instead of upgrading that readback.
        assert state.read_bytes() == original_state
        index = runtime / f"goals/{GOAL}/runs/index.jsonl"
        assert not index.exists() or "quota_slot_spent" not in index.read_text()
        return
    assert code == 0, payload
    # The shared operation server requests confirmation and returns a wait;
    # retaining fresh reads grants no additional operation or settlement.
    assert payload["status"] == ("stopped" if operation_tools else "committed"), payload
    assert native_start.exists() and turn_start.exists(), payload
    assert payload["effects"]["host_invoked"] is True, payload
    # New material belongs to this Turn's required reads. Launch checks must
    # retain that context without synchronizing the external source again.
    assert payload["effects"]["quota_spent"] is (not operation_tools), payload
    index = runtime / f"goals/{GOAL}/runs/index.jsonl"
    rows = [json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
    assert sum(row.get("classification") == "quota_slot_spent" for row in rows) == int(not operation_tools)


@pytest.mark.parametrize("operation_tools", [False, True])
def test_source_session_cli_keeps_lifecycle_only_execution_denial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation_tools: bool,
):
    admission = _source_admission(tmp_path)
    with source_session_registry_transaction(
        admission.registry_path, operation="fixture_cli_source_goal",
    ) as transaction:
        registry = transaction.payload_copy()
        registry["goals"][0]["id"] = GOAL
        transaction.commit(registry)
    before = admission.registry_path.read_bytes()
    runtime = tmp_path / "runtime"
    host_arguments, native_start, turn_start = _host(tmp_path, monkeypatch, operation_tools)
    code, payload = _run(admission.registry_path, runtime, tmp_path, host_arguments)
    assert code == 1 and "lifecycle-only profile source_session_v1" in json.dumps(payload), payload
    assert not native_start.exists() and not turn_start.exists()
    assert not _session_files(runtime)
    assert admission.registry_path.read_bytes() == before


@pytest.mark.parametrize("provider,operation_tools", [("file", True), ("sqlite", False), ("sqlite", True)])
def test_actual_legacy_active_variant_still_launches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str, operation_tools: bool,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, registry = _write_live_fixture(tmp_path, provider=provider)
    payload = json.loads(registry.read_text())
    payload["goals"][0]["status"] = "active-read-only"
    registry.write_text(json.dumps(payload))
    host_arguments, native_start, turn_start = _host(tmp_path, monkeypatch, operation_tools)
    code, payload = _run(registry, runtime, project, host_arguments)
    assert code == 0, payload
    assert native_start.exists() and turn_start.exists(), payload
    assert _session_files(runtime)
    assert payload["effects"]["host_invoked"] is True, payload
    if operation_tools:
        # The shared fake operation server returns a wait, never an outcome.
        _assert_no_settlement(runtime, payload)
    else:
        assert payload["status"] == "committed" and payload["effects"]["quota_spent"], payload


@pytest.mark.parametrize("provider", ["file", "sqlite"])
def test_operation_launch_releases_registry_locks_before_native_response_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, provider: str,
):
    isolate_sqlite_runtime(tmp_path, monkeypatch)
    project, runtime, source = _write_live_fixture(tmp_path, provider=provider)
    registry = _mirror(source, runtime)
    host_arguments, native_start, turn_start = _host(tmp_path, monkeypatch, True)
    original_write = codex_operation_host.CodexChatAgentSession._write
    stopped = []

    def stop_during_response_wait():
        stopped.append(activation_service.set_goal_activation_state(
            registry_path=registry, runtime_root_override=str(runtime), goal_id=GOAL,
            state="stopped", reason="Synthetic owner Stop after native Turn dispatch.",
            actor_kind="owner", execute=True,
        ))

    def observed_write(session, packet, **options):
        original_write(session, packet, **options)
        if packet.get("method") == "turn/start":
            # The RPC response has not been read yet. A lifecycle writer must
            # already be able to acquire both source and mirror writer locks.
            writer = threading.Thread(target=stop_during_response_wait, daemon=True)
            writer.start()
            writer.join(timeout=5)
            assert not writer.is_alive(), "native response wait retained registry locks"
            assert stopped[0]["readback"]["verified"] is True, stopped

    monkeypatch.setattr(codex_operation_host.CodexChatAgentSession, "_write", observed_write)
    _code, payload = _run(registry, runtime, project, host_arguments)
    assert stopped and stopped[0]["readback"]["verified"] is True, payload
    assert native_start.exists() and turn_start.exists(), payload
    _assert_no_settlement(runtime, payload)
