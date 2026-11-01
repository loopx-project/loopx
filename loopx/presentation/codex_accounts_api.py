"""Owner-local transport to the optional native Codex accounts provider.

No account implementation, credentials or selection rules live in Core.
Uninstalled/disabled providers start no observer and leave native Codex alone.
"""
from __future__ import annotations

from pathlib import Path
import importlib
import threading
from typing import Any, Protocol, cast

from ..extensions.background_runtime import HostedWorker

CODEX_ACCOUNTS_PATH = "/api/chat/codex-accounts"


class CodexAccountService:
    def __init__(self, runtime_root: Path, *, start_observer: bool = True):
        self.provider: Any = None
        self.watcher: Any = None
        self.mutex = threading.Lock()
        self.closed = False
        self.error: str | None = "extension_uninstalled"
        try:
            provider_module = importlib.import_module("loopx_codex_provider_routing.native")
        except ImportError:
            return
        self.provider = provider_module.NativeAccounts(runtime_root)
        self.watcher = self._new_observer(provider_module)
        self.error = None
        # Rehydrate an explicitly enabled observer after an App restart. The
        # absent/default-off path neither creates state nor reads native auth.
        try:
            if start_observer:
                self._reconcile_observer()
        except Exception:
            self.error = "state_unavailable"

    def _new_observer(self, module):
        watcher = module.Watcher(self.provider)
        host = getattr(watcher, "hosted_by", None)
        # Already-installed older optional providers retain their existing
        # entrypoint until upgrade; no credential or decision owner is copied.
        return host(HostedWorker) if callable(host) else watcher

    def _reconcile_observer(self) -> None:
        if self.closed:
            return
        if self.provider.path.exists() and self.provider._load()["policy"]["enabled"]:
            if self.watcher.stop.is_set():
                module = importlib.import_module("loopx_codex_provider_routing.native")
                self.watcher = self._new_observer(module)
            self.watcher.start()
        else:
            self.watcher.close()

    def call(self, operation: str, parameters: dict[str, Any]) -> dict[str, Any]:
        if self.provider is None:
            return {"ok": True, "available": False, "code": self.error}
        try:
            if operation.startswith("keys-"):
                if operation not in {"keys-status", "keys-add", "keys-remove", "keys-check"}:
                    raise ValueError("Unsupported key operation")
                value = getattr(self.provider.api_keys(), operation.removeprefix("keys-"))(**parameters)
            elif operation == "status":
                with self.mutex:
                    self._reconcile_observer()
                value = self.provider.status()
            elif operation == "clients":
                value = self.provider.clients()
            elif operation == "enroll-current":
                value = self.provider.enroll_current(**parameters)
            elif operation == "configure":
                with self.mutex:
                    if parameters.get("enabled") is False:
                        self.watcher.stop.set()
                    try:
                        value = self.provider.configure(**parameters)
                    finally:
                        # A stale/failed disable did not revoke the saved opt-in.
                        # Restore that policy's observer rather than leaving a
                        # stopped worker behind an enabled switch.
                        self._reconcile_observer()
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
                cached = (self.provider.api_keys().status() if operation.startswith("keys-")
                          else self.provider._projection(self.provider._load()))
            except Exception:
                pass
            return {**cached, "ok": False, "available": True, "code": code, "error_code": code}

    def reconcile_background(self) -> None:
        with self.mutex:
            self._reconcile_observer()

    def background_observation(self) -> dict:
        if self.provider is None:
            return {"available": False, "enabled": None}
        enabled = self.provider.path.exists() and self.provider._load()["policy"]["enabled"]
        observe = getattr(self.provider, "observer_health", None)
        # The provider observes its elected lock owner, which can be another
        # process. The passive Core contender's thread is not that observation.
        probe = observe(enabled) if callable(observe) else {"state": "unverified"}
        return {"available": True, "enabled": enabled, "probe": {
            "state": "unverified" if probe["state"] == "upgrade_required" else probe["state"],
            "checked_at": probe.get("checked_at"), "next_check_at": probe.get("next_check_at"),
        }}

    def close(self) -> None:
        self.closed = True
        with self.mutex:
            if self.watcher:
                self.watcher.close()


class _HTTP(Protocol):
    def _send_json(self, payload: dict[str, Any], *, status: int = 200) -> None: ...

    def _read_json(self) -> dict[str, Any]: ...


class CodexAccountsRequestMixin:
    server: Any

    def _codex_accounts_read(self) -> None:
        cast(_HTTP, self)._send_json(self.server.codex_account_service.call("status", {}))

    def _codex_account_clients(self) -> None:
        cast(_HTTP, self)._send_json(self.server.codex_account_service.call("clients", {}))

    def _codex_api_keys_read(self) -> None:
        cast(_HTTP, self)._send_json(self.server.codex_account_service.call("keys-status", {}))

    def _codex_api_keys_write(self) -> None:
        http = cast(_HTTP, self)
        try:
            body = http._read_json()
            fields = {"keys-add": {"expected_revision", "label", "api_key"},
                      "keys-remove": {"expected_revision", "key_id"},
                      "keys-check": {"expected_revision", "key_id"}}
            if (set(body) != {"operation", "parameters"} or not isinstance(body["operation"], str)
                    or body["operation"] not in fields or not isinstance(body["parameters"], dict)
                    or set(body["parameters"]) != fields[body["operation"]]):
                raise ValueError("Invalid key operation")
            result = self.server.codex_account_service.call(body["operation"], body["parameters"])
        except (TypeError, ValueError, KeyError):
            result = {"ok": False, "code": "invalid_request"}
        http._send_json(result, status=200 if result["ok"] else 409)

    def _codex_accounts_write(self) -> None:
        http = cast(_HTTP, self)
        try:
            body = http._read_json()
            if set(body) != {"operation", "parameters"} or not isinstance(body["parameters"], dict):
                raise ValueError("Invalid account operation")
            operation, parameters = body["operation"], body["parameters"]
            fields = {"configure": {"expected_revision", "enabled", "slots", "reconnect", "restart_clients", "force_quit_clients", "api_fallback"},
                      "enroll-current": {"expected_revision", "expected_selection"},
                      "switch": {"manual_slot", "expected_selection", "operation_id"},
                      "recover": {"expected_revision", "expected_selection"}, "rollback": {"expected_revision", "expected_selection"},
                      "reopen": {"expected_selection"}, "refresh": set()}
            if operation not in fields or set(parameters) - fields[operation]:
                raise ValueError("Invalid account parameters")
            result = self.server.codex_account_service.call(operation, parameters)
        except (TypeError, ValueError, KeyError):
            result = {"ok": False, "code": "invalid_request"}
        http._send_json(result, status=200 if result["ok"] else 409)
