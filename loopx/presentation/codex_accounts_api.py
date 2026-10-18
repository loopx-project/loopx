"""Owner-local transport to the optional native Codex accounts provider.

No account implementation, credentials or selection rules live in Core.
Uninstalled/disabled providers start no observer and leave native Codex alone.
"""
from __future__ import annotations

from pathlib import Path
import importlib
import threading
from typing import Any, Protocol, cast

CODEX_ACCOUNTS_PATH = "/api/chat/codex-accounts"


class CodexAccountService:
    def __init__(self, runtime_root: Path):
        self.provider: Any = None
        self.watcher: Any = None
        self.mutex = threading.Lock()
        self.error: str | None = "extension_uninstalled"
        try:
            provider_module = importlib.import_module("loopx_codex_provider_routing.native")
        except ImportError:
            return
        self.provider = provider_module.NativeAccounts(runtime_root)
        self.watcher = provider_module.Watcher(self.provider)
        self.error = None
        # Rehydrate an explicitly enabled observer after an App restart. The
        # absent/default-off path neither creates state nor reads native auth.
        try:
            if self.provider.path.exists() and self.provider._load()["policy"]["enabled"]:
                self.watcher.start()
        except Exception:
            self.error = "state_unavailable"

    def call(self, operation: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if self.provider is None:
            return {"ok": True, "available": False, "code": self.error}
        try:
            if operation == "status":
                value = self.provider.status()
            elif operation == "configure":
                with self.mutex:
                    if parameters.get("enabled") is False:
                        self.watcher.stop.set()
                    value = self.provider.configure(**parameters)
                    if value["policy"]["enabled"]:
                        if self.watcher.stop.is_set():
                            module = importlib.import_module("loopx_codex_provider_routing.native")
                            self.watcher = module.Watcher(self.provider)
                        self.watcher.start()
                    else:
                        self.watcher.close()
            elif operation == "refresh":
                value = self.provider.tick(refresh=True)
            elif operation == "switch":
                if set(parameters) != {"manual_slot", "expected_selection", "operation_id"}:
                    raise ValueError("Invalid switch request")
                value = self.provider.tick(refresh=True, **parameters)
            elif operation in ("recover", "rollback"):
                value = self.provider.recover(rollback=operation == "rollback", **parameters)
            elif operation == "reopen":
                value = self.provider.reopen(**parameters)
            else:
                raise ValueError("Unsupported operation")
            return {"ok": True, **value}
        except Exception as error:
            # Optional provider diagnostics can include private native paths.
            code = getattr(error, "code", "native_unavailable")
            cached = {}
            try:
                cached = self.provider._projection(self.provider._load())
            except Exception:
                pass
            return {**cached, "ok": False, "available": True, "code": code, "error_code": code}

    def close(self) -> None:
        if self.watcher:
            self.watcher.close()


class _HTTP(Protocol):
    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None: ...

    def _read_json(self) -> dict[str, Any]: ...


class CodexAccountsRequestMixin:
    server: Any

    def _codex_accounts_read(self) -> None:
        cast(_HTTP, self)._send_json(self.server.codex_account_service.call("status", {}))

    def _codex_accounts_write(self) -> None:
        http = cast(_HTTP, self)
        try:
            body = http._read_json()
            if set(body) != {"operation", "parameters"} or not isinstance(body["parameters"], dict):
                raise ValueError("Invalid account operation")
            operation, parameters = body["operation"], body["parameters"]
            fields = {"configure": {"expected_revision", "enabled", "slots", "reconnect", "restart_clients"},
                      "switch": {"manual_slot", "expected_selection", "operation_id"},
                      "recover": {"expected_revision", "expected_selection"}, "rollback": {"expected_revision", "expected_selection"},
                      "reopen": {"expected_selection"}, "refresh": set()}
            if operation not in fields or set(parameters) - fields[operation]:
                raise ValueError("Invalid account parameters")
            result = self.server.codex_account_service.call(operation, parameters)
        except (TypeError, ValueError, KeyError):
            result = {"ok": False, "code": "invalid_request"}
        http._send_json(result, status=200 if result["ok"] else 409)
