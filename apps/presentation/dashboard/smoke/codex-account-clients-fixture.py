"""Real account HTTP/store with synthetic native process IO; no auth or model."""
from __future__ import annotations

import importlib
import json
from pathlib import Path
import sys
import tempfile
import threading

repo = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(repo))
sys.path.insert(0, str(repo / "packages/loopx-codex-provider-routing/src"))

from loopx.chat_server import ChatHTTPServer, ChatRequestHandler
from loopx.presentation.codex_accounts_api import CodexAccountService


def main() -> None:
    native = importlib.import_module("loopx_codex_provider_routing.native")
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

    def transport(adapter, operation, **parameters):
        calls.append(operation)
        if operation == "status":
            return {"slots": ["a", "b", "c"], "active_slot": "a", "selection_revision": "synthetic",
                    "current_login_matches": True, "recovery_required": False}
        assert operation == "clients", "Reading settings cannot switch, signal, observe quota or recover"
        if mode == "unavailable":
            raise native.HostError("ownership_unverified")
        rows = [] if mode == "empty" else clients if mode in {"many", "partial"} else clients[:1]
        return {"complete": mode != "partial", "clients": rows}

    with tempfile.TemporaryDirectory(prefix="loopx-account-clients-") as directory:
        runtime = Path(directory).resolve()
        provider = native.NativeAccounts(runtime, transport=transport)
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
                else:
                    raise ValueError("Unknown fixture command")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            service.close()


if __name__ == "__main__":
    main()
