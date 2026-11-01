"""Real account HTTP/store with synthetic native process IO; no auth or model."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time

repo = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "packages/loopx-codex-provider-routing/src"))

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.presentation.codex_accounts_api import CodexAccountService


def main() -> None:
    native = importlib.import_module("loopx_codex_provider_routing.native")
    keys_module = importlib.import_module("loopx_codex_provider_routing.openai_keys")
    key_check = "models_readable"
    native.NativeAccounts.api_keys = lambda self: keys_module.OpenAIKeys(self, checker=lambda _: key_check)
    native.discover = lambda: {"synthetic": True}
    clients = [
        {"pid": 303, "kind": "unmanaged", "process": "virtual-machine", "restartable": False,
         "manager": {"pid": 304, "process": "limactl", "instance": "colima-example", "status": "Running"},
         "restart_blocker": "external_workload_unverified"},
        {"pid": 404, "kind": "unmanaged", "process": "standalone-client", "restartable": False,
         "restart_blocker": "manager_unverified"},
        {"pid": 202, "kind": "service", "process": "codex", "restartable": True,
         "service": "dev.example.renamed-chat-with-a-long-installed-service-label"},
        *[{"pid": 1000 + i, "kind": "app", "process": "codex" if i == 0 else "node",
           "restartable": True} for i in range(18)],
    ]
    mode, calls = "many", []
    api_fixture = os.environ.get("LOOPX_API_FALLBACK_FIXTURE") == "1"
    active, selection, api_slot, recovered, now = "a", "synthetic", None, False, time.time()
    slots, registration_busy = ["a", "b", "c"], False
    if api_fixture:
        relay_module = importlib.import_module("loopx_codex_provider_routing.openai_relay")
        relay_module.ensure = lambda _: {"slot": "openai-api", "api_key": "synthetic-relay-token", "base_url": "http://127.0.0.1:32100/v1"}

    def transport(adapter, operation, **parameters):
        nonlocal active, selection, api_slot, slots
        calls.append(operation)
        if operation == "status":
            return {"slots": slots, "active_slot": active, "selection_revision": selection,
                    "current_login_matches": True, "recovery_required": False,
                    "api_fallback_supported": api_fixture, "api_slot": api_slot}
        if operation == "enroll_current":
            assert parameters["expected_revision"] == selection and slots == []
            if registration_busy:
                raise native.HostError("waiting_for_idle")
            slots, active, selection = ["primary"], "primary", selection + "-registered"
            return transport(adapter, "status")
        if api_fixture and operation == "enroll_api":
            api_slot = "openai-api"
            return transport(adapter, "status")
        if api_fixture and operation == "observe":
            return {"observations": [{"slot": slot, "status": "ok", "observed_at": now,
                     "usage": {"rate_limit": {"primary_window": {"used_percent": 10 if recovered and slot == "b" else 100,
                                "reset_at": now + 3600}}, "credits": {"has_credits": False}}} for slot in parameters["slots"]]}
        if api_fixture and operation == "switch":
            assert parameters["expected_revision"] == selection
            active, selection = parameters["slot"], selection + "-switched"
            return transport(adapter, "status") | {"launch_failed": False}
        assert operation == "clients", "Reading settings cannot switch, signal, observe quota or recover"
        if mode == "unavailable":
            raise native.HostError("ownership_unverified")
        rows = [] if mode == "empty" else clients if mode in {"many", "partial"} else clients[:1]
        return {"complete": mode != "partial", "clients": rows}

    with tempfile.TemporaryDirectory(prefix="loopx-account-clients-") as directory:
        runtime = Path(directory).resolve()
        provider = native.NativeAccounts(runtime, transport=transport, **({"clock": lambda: now} if api_fixture else {}))
        provider.configure(expected_revision=0, enabled=False, slots=["a", "b", "c"], restart_clients=True)
        before = provider.path.read_bytes()
        calls.clear()
        service = CodexAccountService(runtime)
        service.provider = provider
        server = ChatHTTPServer(("127.0.0.1", 0), ChatRequestHandler)
        server.verbose = False
        server.codex_account_service = service
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(json.dumps({"origin": f"http://127.0.0.1:{server.server_port}"}), flush=True)
        try:
            for line in sys.stdin:
                command = line.strip()
                if command == "inspect":
                    print(json.dumps({"state_unchanged": provider.path.read_bytes() == before,
                                      "calls": calls, "watcher_started": service.watcher.thread is not None}), flush=True)
                elif command in {"many", "sparse", "empty", "partial", "unavailable"}:
                    mode = command
                    print(json.dumps({"mode": mode}), flush=True)
                elif command in {"key-models_readable", "key-authentication_failed", "key-permission_denied", "key-rate_limited", "key-unavailable"}:
                    key_check = command.removeprefix("key-")
                    print(json.dumps({"key_check": key_check}), flush=True)
                elif command == "subscription-recovered" and api_fixture:
                    recovered, now = True, now + 301
                    print(json.dumps({"subscription_recovered": True}), flush=True)
                elif command == "unregistered":
                    slots, active, selection, registration_busy = [], None, "unregistered", True
                    result = service.call("configure", {"expected_revision": provider.status()["revision"],
                                          "enabled": False, "slots": [], "api_fallback": False})
                    assert result["ok"]
                    print(json.dumps({"unregistered": True}), flush=True)
                elif command == "registration-ready":
                    registration_busy = False
                    print(json.dumps({"registration_ready": True}), flush=True)
                else:
                    raise ValueError("Unknown fixture command")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            service.close()


if __name__ == "__main__":
    main()
