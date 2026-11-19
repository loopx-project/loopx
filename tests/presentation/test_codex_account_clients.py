"""The settings inventory reads the optional provider without account effects."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.presentation.codex_accounts_api import CodexAccountService


@pytest.fixture
def inventory_server(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] /
                                   "packages/loopx-codex-provider-routing/src"))
    native = importlib.import_module("loopx_codex_provider_routing.native")
    calls = []
    clients = [
        {"pid": 101, "kind": "app", "process": "codex", "restartable": True},
        {"pid": 202, "kind": "service", "process": "codex", "restartable": True,
         "service": "dev.example.renamed-chat"},
        {"pid": 303, "kind": "unmanaged", "process": "virtual-machine", "restartable": False,
         "manager": {"pid": 304, "process": "limactl", "instance": "colima-example", "status": "Running"},
         "restart_blocker": "external_workload_unverified"},
    ]
    fault = []

    def transport(adapter, operation, **parameters):
        calls.append((operation, parameters))
        assert operation == "clients", "Inventory must not observe quota, reconcile, switch or reopen"
        if fault:
            raise native.HostError("client_changed")
        return {"complete": True, "clients": clients}

    monkeypatch.setattr(native, "discover", lambda: {"synthetic": True})
    service = CodexAccountService(tmp_path / "runtime")
    service.provider = native.NativeAccounts(tmp_path / "runtime", transport=transport)
    server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
    server.verbose = False
    server.codex_account_service = service
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", service, clients, calls, fault
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        service.close()


def test_clients_read_uses_original_owner_and_preserves_default_off(inventory_server):
    origin, service, clients, calls, _ = inventory_server
    assert not service.provider.root.exists()
    with urllib.request.urlopen(origin + "/api/chat/codex-accounts/clients") as response:
        result = json.load(response)
    assert result == {"ok": True, "complete": True, "clients": clients}
    assert calls == [("clients", {})]
    assert not service.provider.root.exists()
    assert service.watcher.thread is None


def test_clients_failure_is_unknown_and_can_recover_without_effects(inventory_server):
    origin, service, clients, calls, fault = inventory_server
    fault.append(True)
    with urllib.request.urlopen(origin + "/api/chat/codex-accounts/clients") as response:
        result = json.load(response)
    assert result["ok"] is False and result["code"] == "client_changed"
    assert "clients" not in result and result.get("complete") is not True
    fault.clear()
    with urllib.request.urlopen(origin + "/api/chat/codex-accounts/clients") as response:
        assert json.load(response)["clients"] == clients
    assert calls == [("clients", {}), ("clients", {})]
    assert not service.provider.root.exists()


def test_clients_route_does_not_accept_effect_parameters(inventory_server):
    origin, _, _, calls, _ = inventory_server
    request = urllib.request.Request(origin + "/api/chat/codex-accounts", method="POST",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"operation": "clients", "parameters": {"force_quit_clients": True}}).encode())
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(request)
    assert error.value.code == 409
    assert calls == []
