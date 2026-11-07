"""Real Chat HTTP composition, three production owners and disposable state."""
import http.client
import importlib
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.chat_store import ChatSessionStore
from loopx.capabilities.manager_context.roundtrip import ReturnService
from loopx.extensions.lark.goal_topic_runtime_service import LarkGoalTopicRuntimeService
from loopx.presentation.background_services_api import register_chat_background_services
from loopx.presentation.codex_accounts_api import CodexAccountService


@pytest.fixture
def host(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] /
                                   "packages/loopx-codex-provider-routing/src"))
    native = importlib.import_module("loopx_codex_provider_routing.native")
    def transport(_adapter, operation, **parameters):
        assert operation == "status", "No native IO is authorized by this fixture"
        return {"slots": ["a"], "active_slot": "a", "selection_revision": "synthetic",
                "current_login_matches": True, "recovery_required": False}

    provider = native.NativeAccounts(tmp_path / "accounts", transport=transport)
    monkeypatch.setattr(native, "NativeAccounts", lambda _root: provider)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.codex_account_service = CodexAccountService(tmp_path, start_observer=False)
    server.manager_return_service = ReturnService(tmp_path / "runtime", tmp_path / "registry.json",
        ChatSessionStore(tmp_path / "runtime"), lambda *_: pytest.fail("No send is authorized"))
    server.lark_goal_topic_runtime = LarkGoalTopicRuntimeService(
        snapshot_provider=lambda: {"apps": [], "connections": [], "private_profiles": []},
        runtime_root=tmp_path / "runtime", runtime_controller=SimpleNamespace(), action_service=None)
    register_chat_background_services(server)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, provider, native
    finally:
        server.shutdown()
        thread.join(2)
        server.server_close()


def request(server, method="GET", body=None, origin=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    headers = {"Origin": origin or f"http://127.0.0.1:{server.server_port}", "Content-Type": "application/json"}
    connection.request(method, "/api/chat/background-services", body=json.dumps(body) if body else None, headers=headers)
    response = connection.getresponse()
    result = response.status, json.loads(response.read())
    connection.close()
    return result


def test_common_read_is_passive_and_default_off_stays_isolated(host):
    server, provider, _ = host
    for _ in range(2):
        status, result = request(server)
        assert status == 200 and len(result["tasks"]) == 4
        tasks = {item["id"]: item for item in result["tasks"]}
        assert tasks["codex-observation"]["state"] == "disabled"
        assert tasks["lark-delivery"]["state"] == "disabled"
        assert tasks["manager-returns"]["state"] == "stopped"
    assert not provider.root.exists(), "Reading the default-off path must not create native state"
    assert not server.manager_return_service.thread.is_alive()


def test_http_recheck_starts_existing_owners_once_and_preserves_disabled_features(host):
    server, provider, _ = host
    status, result = request(server, "POST", {"operation": "recheck"})
    assert status == 200
    returns = server.manager_return_service
    first = returns.thread
    first_execution = returns.execution_thread
    manager_tasks = ("manager-returns", "manager-executions")
    tasks = {item["id"]: item for item in result["tasks"]}
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if all(tasks[key]["last_success_at"] is not None for key in manager_tasks):
            break
        time.sleep(.02)
        status, result = request(server)
        assert status == 200
        tasks = {item["id"]: item for item in result["tasks"]}
    for key in manager_tasks:
        assert tasks[key]["last_success_at"] is not None, tasks[key]
        # A worker can begin its next scan before the other completes its first.
        assert tasks[key]["state"] in {"healthy", "checking"}, tasks[key]
    assert result["supervisor"]["state"] in {"healthy", "checking"}, "Recheck must restore the host as well"
    request(server, "POST", {"operation": "recheck"})
    assert returns.thread is first
    assert returns.execution_thread is first_execution
    assert not provider.root.exists() and not server.lark_goal_topic_runtime._delivery_thread
    server.background_services.close()
    request(server, "POST", {"operation": "recheck"})
    assert not first.is_alive() and not first_execution.is_alive(), "Recheck after shutdown cannot resurrect work"


def test_recheck_uses_the_existing_loopback_guard_and_rejects_new_settings(host):
    server, _, _ = host
    assert request(server, "POST", {"operation": "recheck"}, "https://external.invalid")[0] == 403
    assert request(server, "POST", {"operation": "recheck", "enabled": True})[0] == 400
    assert not server.manager_return_service.thread.is_alive()


@pytest.mark.skipif(os.name != "posix", reason="Native elected observation uses POSIX locks")
def test_common_native_health_uses_the_external_elected_owner_not_local_thread(host, monkeypatch):
    server, provider, native = host
    monkeypatch.setattr(native, "discover", lambda: {"synthetic": True})
    provider.configure(expected_revision=0, enabled=True, slots=["a"])
    # Real standalone lock election and heartbeat; the synthetic step never
    # reads native credentials, switches accounts or calls a model.
    monkeypatch.setattr(provider, "tick", lambda: {"retry_at": time.time() + 60})
    elected = native.Watcher(provider).start()
    try:
        deadline = time.monotonic() + 5
        while provider.observer_health(True)["state"] != "healthy" and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.codex_account_service.watcher.thread is None
        before = (provider.root / "observer.json").read_bytes()
        _, result = request(server)
        task = next(row for row in result["tasks"] if row["id"] == "codex-observation")
        assert task["state"] == "healthy"
        assert task["last_success_at"] is None, "A progress heartbeat is not an invented successful quota sample"
        assert before == (provider.root / "observer.json").read_bytes()
    finally:
        elected.close()


@pytest.mark.skipif(os.name != "posix", reason="Native elected observation uses POSIX locks")
def test_supervisor_restores_enabled_observer_without_a_status_poll_or_policy_edit(host, monkeypatch):
    server, provider, native = host
    monkeypatch.setattr(native, "discover", lambda: {"synthetic": True})
    monkeypatch.setattr(provider, "tick", lambda: {"retry_at": time.time() + 60})
    provider.configure(expected_revision=0, enabled=True, slots=["a"])
    saved = provider.path.read_bytes()
    server.background_services.start()
    deadline = time.monotonic() + 5
    while provider.observer_health(True)["state"] != "healthy" and time.monotonic() < deadline:
        time.sleep(.02)
    assert provider.observer_health(True)["state"] == "healthy"
    original = server.codex_account_service.watcher
    original.close()
    # Use the production 15s supervisor cadence. No status/recheck endpoint
    # starts the replacement during this wait.
    deadline = time.monotonic() + 20
    while server.codex_account_service.watcher is original and time.monotonic() < deadline:
        time.sleep(.05)
    assert server.codex_account_service.watcher is not original
    deadline = time.monotonic() + 5
    while provider.observer_health(True)["state"] != "healthy" and time.monotonic() < deadline:
        time.sleep(.02)
    assert provider.observer_health(True)["state"] == "healthy"
    assert provider.path.read_bytes() == saved


def test_http_pump_restores_an_unexpectedly_exited_supervisor_without_a_status_poll(host, monkeypatch):
    server, _, _ = host
    runtime = server.background_services
    original_target, original_reconcile = runtime._supervisor.target, runtime.reconcile
    exited, recovered = threading.Event(), threading.Event()
    original_hook = threading.excepthook

    def expected_exit(args):
        if args.thread.name == "loopx-background-services" and args.exc_type is SystemExit:
            exited.set()
        else:
            original_hook(args)

    def target():
        if not exited.is_set():
            raise SystemExit("synthetic host exit")
        original_target()

    def reconcile():
        original_reconcile()
        recovered.set()

    monkeypatch.setattr(threading, "excepthook", expected_exit)
    monkeypatch.setattr(runtime._supervisor, "target", target)
    monkeypatch.setattr(runtime, "reconcile", reconcile)
    runtime.start()
    assert exited.wait(2)
    # Only the real server's service_actions pump restores this thread.
    assert recovered.wait(3)
    assert runtime._supervisor.thread.is_alive()
