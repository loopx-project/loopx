from __future__ import annotations

import inspect
import hashlib
from pathlib import Path
import plistlib
import signal
import subprocess
import threading

import pytest

from loopx.chat_server import serve_chat
from loopx.cli import build_parser
from loopx.cli_commands import support_control
from loopx.cli_commands.support_control_chat import chat_server_options
from loopx.chat_store import ChatSessionStore
from loopx import dashboard_launcher, runtime_activation


@pytest.fixture
def managed_listener(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(runtime_activation.sys, "platform", "darwin")
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    runtime = tmp_path / "original history with spaces"
    plist = agents / "com.loopx.chat.plist"
    plist.write_bytes(plistlib.dumps({"ProgramArguments": [
        "loopx", "--runtime-root", str(runtime), "chat",
    ]}))
    store = ChatSessionStore(runtime)
    sid = store.create_session(goal_id="fixture", agent_id="fixture", adapter_kind="fake",
        upstream_thread_id="fixture", upstream_mode="managed")["session_id"]
    listeners, signals, starts = [4321], [], []
    monkeypatch.setattr(dashboard_launcher, "_probe_existing_chat", lambda *_args: "stale")
    monkeypatch.setattr(dashboard_launcher, "_listener_pids", lambda _port: list(listeners))
    monkeypatch.setattr(dashboard_launcher, "_is_same_user_loopx_chat_process", lambda pid: pid == 4321)
    monkeypatch.setattr(runtime_activation, "_managed_service_pid", lambda _plist: 4321, raising=False)

    def stop(pid, sig):
        if sig != 0:  # File-lock liveness probes are reads, not a stop attempt.
            signals.append((pid, sig))
            listeners.clear()

    monkeypatch.setattr(runtime_activation.os, "kill", stop)
    monkeypatch.setattr(support_control, "serve_chat", lambda **options: starts.append(options))

    def invoke():
        # This fresh empty directory must never mask the listener's original work.
        args = build_parser().parse_args(["--runtime-root", str(tmp_path / "new empty root"),
            "chat", "--replace-existing-loopx-chat", "--no-open"])
        payloads = []
        result = support_control.handle_support_control_command(args, registry_path=tmp_path / "registry.json",
            registry_was_supplied=True, print_payload=lambda payload, *_args: payloads.append(payload),
            output_format=lambda *_args: "json")
        return result, payloads

    return store, sid, plist, listeners, signals, starts, invoke


def history_bytes(store):
    return {str(p.relative_to(store.root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in store.root.rglob("*") if p.suffix in {".json", ".jsonl"}}


@pytest.mark.parametrize("state", ["queued", "starting", "running", "completing", "interrupting"])
def test_explicit_replacement_preserves_all_accepted_work(managed_listener, state):
    store, sid, _plist, _listeners, signals, starts, invoke = managed_listener
    turn, _ = store.create_turn(sid, client_turn_id="accepted", message="keep original input")
    store.update_turn(sid, turn["turn_id"], status=state)
    before = history_bytes(store)
    result, payloads = invoke()
    assert result == 1 and payloads[0]["error"] == "chat_work_pending"
    assert signals == starts == [] and history_bytes(store) == before


def test_explicit_replacement_waits_for_orphaned_queue_then_replaces_same_pid(managed_listener):
    store, sid, _plist, _listeners, signals, starts, invoke = managed_listener
    turn, _ = store.create_queued_turn(sid, client_turn_id="queued", message="original queue")
    assert not store.load_session(sid).get("active_turn_id")
    assert invoke()[0] == 1 and not signals
    store.update_turn(sid, turn["turn_id"], status="completed")
    before = history_bytes(store)
    assert invoke() == (0, [])
    assert signals == [(4321, signal.SIGTERM)] and len(starts) == 1
    assert history_bytes(store) == before


@pytest.mark.parametrize("damage", ["missing_route", "relative_route", "bad_plist", "bad_session", "bad_turn", "missing_active", "unknown_state", "unknown_manager"])
def test_explicit_replacement_keeps_unverified_state_untouched(managed_listener, monkeypatch, damage):
    store, sid, plist, _listeners, signals, starts, invoke = managed_listener
    turn, _ = store.create_turn(sid, client_turn_id="accepted", message="preserve")
    store.update_turn(sid, turn["turn_id"], status="completed")
    if damage == "missing_route":
        plist.write_bytes(plistlib.dumps({"ProgramArguments": ["loopx", "chat"]}))
    elif damage == "relative_route":
        plist.write_bytes(plistlib.dumps({"ProgramArguments": ["loopx", "--runtime-root", "relative", "chat"]}))
    elif damage == "bad_plist":
        plist.write_text("{broken")
    elif damage == "bad_session":
        store._session_path(sid).write_text("{broken")
    elif damage == "bad_turn":
        store._turn_path(sid, turn["turn_id"]).write_text("{broken")
    elif damage == "missing_active":
        store.update_turn(sid, turn["turn_id"], status="running")
        store._turn_path(sid, turn["turn_id"]).unlink()
    elif damage == "unknown_state":
        store.update_turn(sid, turn["turn_id"], status="unknown")
    else:
        monkeypatch.setattr(runtime_activation, "_managed_service_pid", lambda _plist: None)
    before = history_bytes(store)
    result, payloads = invoke()
    assert result == 1 and payloads[0]["error"] == "chat_state_unverified"
    assert not signals and not starts and history_bytes(store) == before


@pytest.mark.parametrize("drift", ["manager", "listener", "route", "identity"])
def test_explicit_replacement_rechecks_the_pinned_target_under_fence(managed_listener, monkeypatch, drift):
    store, _sid, plist, listeners, signals, starts, invoke = managed_listener
    if drift == "manager":
        pids = iter([4321, 9876])
        monkeypatch.setattr(runtime_activation, "_managed_service_pid", lambda _plist: next(pids))
    elif drift == "listener":
        reads = iter([[4321], [9876]])
        monkeypatch.setattr(dashboard_launcher, "_listener_pids", lambda _port: next(reads))
    elif drift == "identity":
        reads = iter([True, False])
        monkeypatch.setattr(dashboard_launcher, "_is_same_user_loopx_chat_process", lambda _pid: next(reads))
    else:
        original = runtime_activation._chat_runtime_root
        reads = iter([store.root.parent, store.root.parent / "different"])
        monkeypatch.setattr(runtime_activation, "_chat_runtime_root", lambda _plist: next(reads))
        assert original(plist) == store.root.parent
    assert invoke()[0] == 1 and not signals and not starts


def test_explicit_replacement_holds_the_real_ingress_fence_through_stop(managed_listener, monkeypatch):
    store, sid, _plist, listeners, signals, _starts, invoke = managed_listener
    stopping, release, attempted, admitted = [threading.Event() for _ in range(4)]
    results, errors = [], []

    def stop(pid, sig):
        if sig == 0:
            return
        signals.append((pid, sig))
        stopping.set()
        assert release.wait(5)
        listeners.clear()

    def accept():
        attempted.set()
        try:
            store.create_queued_turn(sid, client_turn_id="during-replacement", message="keep next input")
            admitted.set()
        except Exception as error:
            errors.append(error)

    monkeypatch.setattr(runtime_activation.os, "kill", stop)
    replacer = threading.Thread(target=lambda: results.append(invoke()))
    writer = threading.Thread(target=accept)
    replacer.start()
    try:
        assert stopping.wait(3)
        writer.start()
        assert attempted.wait(2)
        assert not admitted.wait(0.15)
        assert store.turn_for_client(sid, "during-replacement") is None
    finally:
        release.set()
        replacer.join(5)
        if writer.ident is not None:
            writer.join(5)
    assert not replacer.is_alive() and not writer.is_alive() and not errors
    assert results == [(0, [])] and admitted.is_set()
    assert len(signals) == 1


def test_managed_service_pid_requires_one_exact_native_pid_field(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_activation.os, "getuid", lambda: 501, raising=False)
    args = "arguments = {\n loopx\n chat\n}\n"
    outputs = iter([args + "pid = 4321", args + "other_pid = 4321", args + "pid = 4321\npid = 4321",
        "arguments = {\n loopx\n other\n}\npid = 4321"])
    monkeypatch.setattr(runtime_activation.subprocess, "run", lambda args, **kwargs:
        subprocess.CompletedProcess(args, 0, next(outputs), ""))
    plist = tmp_path / "com.loopx.chat.plist"
    plist.write_bytes(plistlib.dumps({"ProgramArguments": ["loopx", "chat"]}))
    assert [runtime_activation._managed_service_pid(plist) for _ in range(4)] == [4321, None, None, None]


@pytest.mark.parametrize("probe, expected", [("unavailable", 0), ("foreign", 1)])
def test_explicit_replacement_keeps_free_port_and_foreign_service_behavior(managed_listener, monkeypatch, probe, expected):
    _store, _sid, _plist, _listeners, signals, starts, invoke = managed_listener
    monkeypatch.setattr(dashboard_launcher, "_probe_existing_chat", lambda *_args: probe)
    assert invoke()[0] == expected and not signals
    assert len(starts) == (expected == 0)


def test_explicit_replacement_ignores_another_managed_services_busy_store(managed_listener, monkeypatch):
    store, _sid, plist, _listeners, signals, starts, invoke = managed_listener
    other = plist.with_name("com.loopx.other.chat.plist")
    root = store.root.parent.parent / "other busy store"
    other.write_bytes(plistlib.dumps({"ProgramArguments": ["loopx", "--runtime-root", str(root), "chat"]}))
    busy = ChatSessionStore(root)
    sid = busy.create_session(goal_id="other", agent_id="other", adapter_kind="fake",
        upstream_thread_id="other", upstream_mode="managed")["session_id"]
    busy.create_turn(sid, client_turn_id="other", message="leave another service running")
    monkeypatch.setattr(runtime_activation, "_managed_service_pid", lambda selected: 4321 if selected == plist else 9876)
    before = history_bytes(busy)
    assert invoke() == (0, []) and len(signals) == len(starts) == 1
    assert history_bytes(busy) == before


def test_explicit_replacement_never_escalates_a_stop_timeout(managed_listener, monkeypatch):
    _store, _sid, _plist, _listeners, signals, _starts, _invoke = managed_listener
    monkeypatch.setattr(runtime_activation.os, "kill", lambda pid, sig: signals.append((pid, sig)) if sig else None)
    with pytest.raises(RuntimeError, match="no stronger signal"):
        runtime_activation.replace_managed_loopx_chat("127.0.0.1", 8767, timeout_seconds=0.1)
    assert signals == [(4321, signal.SIGTERM)]


@pytest.mark.parametrize("configured, expected", [(None, None), ("0", 0.1), ("12.5", 12.5), ("45", 45.0)])
def test_shared_startup_options_preserve_adapter_defaults_and_explicit_limits(tmp_path, configured, expected):
    tail = [] if configured is None else ["--startup-timeout-seconds", configured]
    args = build_parser().parse_args(["chat", "--no-open", *tail])
    options = chat_server_options(args, registry_path=tmp_path / "registry.json")
    assert options["startup_timeout_sec"] == expected
    assert options["idle_timeout_sec"] == 180.0
    assert options["hard_timeout_sec"] == 900.0
    inspect.signature(serve_chat).bind(**options)


def test_shared_options_keep_workspace_permissions_and_explicit_timeout_floors(tmp_path):
    args = build_parser().parse_args([
        "chat", "--project-workspace-grant", "workspace_read",
        "--project-filesystem-scope", "workspace_only", "--no-private-reactions",
        "--idle-timeout-seconds", "0", "--hard-timeout-seconds", "0", "--limit", "-1",
    ])
    options = chat_server_options(args, registry_path=tmp_path / "registry.json")
    assert options["project_workspace_grant"] == "workspace_read"
    assert options["project_filesystem_scope"] == "workspace_only"
    assert options["private_reactions"] is False
    assert options["idle_timeout_sec"] == options["hard_timeout_sec"] == 0.1
    assert options["limit"] == 0


def test_shared_options_resolve_global_registry_and_expand_selected_paths(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    runtime = tmp_path / "runtime"
    args = build_parser().parse_args([
        "--runtime-root", str(runtime), "chat", "--global-registry",
        "--scan-path", "~/first", "--scan-path", "~/second", "--assets-dir", "~/bundle",
    ])
    options = chat_server_options(args, registry_path=tmp_path / "absent.json")
    assert options["registry_path"] == runtime / "registry.global.json"
    assert options["scan_roots"] == [tmp_path / "first", tmp_path / "second"]
    assert options["assets_dir"] == tmp_path / "bundle"
    assert not runtime.exists()


def test_shared_options_keep_scan_root_fallback_and_reject_another_command(tmp_path):
    args = build_parser().parse_args(["chat", "--scan-root", str(tmp_path)])
    assert chat_server_options(args, registry_path=tmp_path / "registry.json")["scan_roots"] == [tmp_path]
    args = build_parser().parse_args(["dashboard"])
    with pytest.raises(ValueError, match="require the chat command"):
        chat_server_options(args, registry_path=tmp_path / "registry.json")


def test_cli_uses_shared_options_before_replacing_or_starting_a_server(tmp_path, monkeypatch):
    args = build_parser().parse_args(["chat", "--no-open", "--replace-existing-loopx-chat"])
    registry = tmp_path / "registry.json"
    calls = []

    def compose(received, *, registry_path):
        assert received is args and registry_path == registry
        calls.append("compose")
        return chat_server_options(received, registry_path=registry_path)

    monkeypatch.setattr(support_control, "chat_server_options", compose)
    monkeypatch.setattr(support_control, "replace_existing_loopx_chat", lambda *args: calls.append("replace"))
    monkeypatch.setattr(support_control, "serve_chat", lambda **kwargs: calls.append(kwargs))
    result = support_control.handle_support_control_command(args, registry_path=registry,
        registry_was_supplied=True, print_payload=lambda *args: None, output_format=lambda *args: "json")
    assert result == 0
    assert calls[:2] == ["compose", "replace"]
    assert calls[2]["startup_timeout_sec"] is None
    assert calls[2]["registry_path"] == registry
    assert calls[2]["open_browser"] is False


def test_failed_option_composition_does_not_replace_a_running_server(tmp_path, monkeypatch):
    args = build_parser().parse_args(["chat", "--replace-existing-loopx-chat"])
    calls = []
    payloads = []

    def reject(*args, **kwargs):
        raise ValueError("invalid fixture options")

    monkeypatch.setattr(support_control, "chat_server_options", reject)
    monkeypatch.setattr(support_control, "replace_existing_loopx_chat", lambda *args: calls.append("replace"))
    monkeypatch.setattr(support_control, "serve_chat", lambda **kwargs: calls.append("serve"))
    result = support_control.handle_support_control_command(args, registry_path=tmp_path / "registry.json",
        registry_was_supplied=True, print_payload=lambda payload, *args: payloads.append(payload),
        output_format=lambda *args: "json")
    assert result == 1 and calls == []
    assert payloads[0]["ok"] is False
    assert payloads[0]["schema_version"] == "loopx_chat_start_v0"
