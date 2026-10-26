"""Opt-in native Codex account host; one policy for CLI and the LoopX App.

The managed permissionless qualification protocol never imports this module.
Python owns private file/process IO; native_policy.ts owns availability/order.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid

from .native_host import HostError, discover

SCHEMA = "codex_native_accounts_v1"


def private_read(path: Path) -> dict:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise HostError("state_untrusted")
    if path.stat().st_mode & 0o077 or path.stat().st_uid != os.getuid():
        raise HostError("state_untrusted")
    value = json.loads(path.read_text())
    if value.get("schema_version") != SCHEMA:
        raise HostError("state_incompatible")
    return value


def atomic(path: Path, value: dict) -> None:
    if any(p.is_symlink() for p in (path, *path.parents)):
        raise HostError("state_untrusted")
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        Path(temporary).unlink(missing_ok=True)


def policy(request: dict) -> dict:
    node = shutil.which("node")
    if not node:
        raise HostError("node_unavailable")
    result = subprocess.run([node, "--no-warnings", "--experimental-strip-types",
                             str(Path(__file__).with_name("native_policy_bridge.ts"))],
                            input=json.dumps(request), capture_output=True, text=True, timeout=10)
    if result.returncode or len(result.stdout) > 131072:
        raise HostError("policy_unavailable")
    return json.loads(result.stdout)


def host(adapter: dict, operation: str, **parameters) -> dict:
    receipt = parameters.pop("handoff_receipt", None)
    command = [sys.executable, "-m", "loopx_codex_provider_routing.native_host"]
    request = json.dumps({"adapter": adapter, "operation": operation, **parameters})
    if receipt:
        # Closing the calling native App must not close this continuation or
        # its result channel. All native sub-operations have bounded waits.
        process = subprocess.Popen([*command, "--result-file", receipt], stdin=subprocess.PIPE,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                   text=True, start_new_session=True)
        try:
            process.communicate(request, timeout=150)
        except subprocess.TimeoutExpired:
            # Do not kill a credential transaction. A separate reaper owns only
            # this child; the receipt fences retries even if its caller exits.
            threading.Thread(target=process.communicate, daemon=True).start()
            raise HostError("handoff_pending") from None
        packet = private_read(Path(receipt))
        if packet.get("ok") is not True:
            raise HostError(packet.get("code", "native_unavailable"))
        return packet["result"]
    result = subprocess.run(command, input=request, capture_output=True, text=True, timeout=150)
    if len(result.stdout) > 131072:
        raise HostError("native_unavailable")
    try:
        packet = json.loads(result.stdout)
    except ValueError:
        raise HostError("native_unavailable") from None
    if packet.get("ok") is not True:
        raise HostError(packet.get("code", "native_unavailable"))
    return packet["result"]


class NativeAccounts:
    def __init__(self, runtime_root: Path, *, transport=host, clock=time.time):
        self.root = Path(runtime_root) / "extensions" / "codex-native-accounts"
        self.path = self.root / "state.json"
        self.transport, self.clock = transport, clock
        self.switch_allowed = lambda: True

    def _load(self) -> dict:
        if self.path.exists():
            return private_read(self.path)
        return {"schema_version": SCHEMA, "revision": 0,
                "policy": {"enabled": False, "slots": [], "cooldown_seconds": 300,
                           "restart_clients": False, "force_quit_clients": False},
                "observations": [], "last_switch_at": None, "action": "disabled"}

    @contextmanager
    def _lock(self, *, wait_seconds: float = 0):
        if os.name != "posix":
            raise HostError("platform_unavailable")
        import fcntl

        if any(p.is_symlink() for p in (self.root, *self.root.parents)):
            raise HostError("state_untrusted")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.root.stat().st_uid != os.getuid() or self.root.stat().st_mode & 0o077:
            raise HostError("state_untrusted")
        path = self.root / "lock"
        if path.is_symlink():
            raise HostError("state_untrusted")
        with open(path, "a", opener=lambda p, flags: os.open(p, flags, 0o600)) as stream:
            deadline = time.monotonic() + wait_seconds
            while True:
                try:
                    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            yield

    def _projection(self, state: dict, native: dict | None = None) -> dict:
        native = native or state.get("native", {})
        return {"schema_version": SCHEMA, "available": True, "revision": state["revision"],
                "policy": state["policy"], "slots": native.get("slots", []),
                "active_slot": native.get("active_slot"),
                "selection_revision": native.get("selection_revision"),
                "login_matches": native.get("current_login_matches"),
                "recovery_required": native.get("recovery_required", False) or state.get("action") == "handoff_interrupted",
                "action": state.get("action", "disabled"), "target": state.get("target"),
                "retry_at": state.get("retry_at"), "last_checked_at": state.get("last_checked_at"),
                "last_switch_at": state.get("last_switch_at"), "accounts": state.get("accounts", []),
                "code": state.get("code"),
                "launch_failed": state.get("launch_failed", False) and native.get("client_running") is not True,
                "client_running": native.get("client_running"),
                "automatic_hot_swap": False}

    def status(self) -> dict:
        state = self._load()
        adapter = state.get("adapter") or discover()
        native = self.transport(adapter, "status")
        return self._projection(state, native)

    def clients(self) -> dict:
        state = self._load()
        return self.transport(state.get("adapter") or discover(), "clients")

    def configure(self, *, expected_revision: int, enabled: bool, slots: list[str], reconnect: bool = False,
                  restart_clients: bool | None = None, force_quit_clients: bool | None = None) -> dict:
        if type(enabled) is not bool or not isinstance(slots, list) or len(slots) > 16 or len(slots) != len(set(slots)):
            raise HostError("invalid_policy")
        if restart_clients is not None and type(restart_clients) is not bool:
            raise HostError("invalid_policy")
        if force_quit_clients is not None and type(force_quit_clients) is not bool:
            raise HostError("invalid_policy")
        # Stop requests fence new effects at the Watcher first, then persist
        # after an already dispatched atomic switch finishes. Other mutations
        # retain their immediate conflict behavior.
        with self._lock(wait_seconds=155 if not enabled else 0):
            state = self._load()
            if state["revision"] != expected_revision:
                raise HostError("configuration_changed")
            pending = state.get("intent") or {}
            if pending.get("handoff_receipt") and enabled:
                raise HostError("handoff_pending")
            # Disabling works offline and never needs credentials or a client.
            if enabled or reconnect:
                adapter = discover() if reconnect or "adapter" not in state else state["adapter"]
                native = self.transport(adapter, "status")
                if reconnect and not slots and not enabled:
                    slots = native["slots"]
                if not slots or not set(slots) <= set(native["slots"]):
                    raise HostError("invalid_slots")
                state.update(adapter=adapter, native=native)
            state.update(policy={"enabled": enabled, "slots": slots, "cooldown_seconds": 300,
                                 "restart_clients": restart_clients if restart_clients is not None
                                 else state["policy"].get("restart_clients", False),
                                 "force_quit_clients": force_quit_clients if force_quit_clients is not None
                                 else state["policy"].get("force_quit_clients", False)},
                         revision=state["revision"] + 1, retry_at=None, code=None,
                         intent=pending if pending.get("handoff_receipt") else None,
                         action="checking" if enabled else "disabled", failure_count=0)
            atomic(self.path, state)
            return self._projection(state)

    def tick(self, *, refresh: bool = False, manual_slot: str | None = None,
             expected_selection: str | None = None, operation_id: str | None = None) -> dict:
        if manual_slot is not None and (not expected_selection or not isinstance(operation_id, str) or not 1 <= len(operation_id) <= 128):
            raise HostError("invalid_request")
        with self._lock():
            state = self._load()
            if operation_id and operation_id == state.get("last_operation_id"):
                return self._projection(state)
            now = self.clock()
            if not state["policy"]["enabled"] and not refresh and not manual_slot and not state.get("intent"):
                return self._projection(state)
            if not refresh and not manual_slot and now < (state.get("retry_at") or 0):
                return self._projection(state)
            if refresh or manual_slot:
                # A deliberate check/choice starts a new retry budget; healthy
                # quota alone does not prove that a handoff succeeded.
                state["failure_count"] = 0
            if "adapter" not in state:
                # An explicit check/manual choice can connect existing slots
                # without turning automatic effects on first.
                state["adapter"] = discover()
                native = self.transport(state["adapter"], "status")
                state.update(native=native, policy={**state["policy"], "slots": native["slots"]},
                             revision=state["revision"] + 1)
            try:
                native = self.transport(state["adapter"], "status")
                if expected_selection is not None and expected_selection != native["selection_revision"]:
                    raise HostError("selection_changed")
                previous = state.get("native", {}).get("selection_revision")
                intent = state.get("intent")
                receipt = None
                if intent and intent.get("handoff_receipt"):
                    path = Path(intent["handoff_receipt"])
                    if path.exists():
                        receipt = private_read(path)
                    else:
                        # A detached switch may still be exiting/reopening the
                        # native client. Never launch a second switch on retry.
                        interrupted = self.clock() - intent["started_at"] > 180
                        state.update(native=native, action="handoff_interrupted" if interrupted else "restarting",
                                     retry_at=self.clock() + 5, code="handoff_interrupted" if interrupted else None)
                        atomic(self.path, state)
                        return self._projection(state)
                    if receipt.get("ok") is not True:
                        state["intent"] = None
                        raise HostError(receipt.get("code", "native_unavailable"))
                if intent and native["active_slot"] == intent["target"] and not native["recovery_required"] and native["current_login_matches"] is True:
                    # An interrupted caller may have lost the successful reply.
                    # Read back selection; never replay a switch or a model turn.
                    state.update(native=native, intent=None, last_switch_at=now, action="selected", target=None,
                                 retry_at=now + 300, code=None, failure_count=0,
                                 launch_failed=receipt["result"].get("launch_failed", False) if receipt
                                 else native.get("client_running") is False)
                    atomic(self.path, state)
                    return self._projection(state)
                state["native"] = native
                if not manual_slot and previous and previous != native["selection_revision"]:
                    # A native/manual choice outside this host wins. Observe it
                    # after a cooldown instead of immediately undoing it.
                    state.update(intent=None, last_switch_at=now, action="selection_changed", target=None,
                                 accounts=[], retry_at=now + 300, code=None, failure_count=0)
                    atomic(self.path, state)
                    return self._projection(state)
                if native["recovery_required"]:
                    state.update(action="recovery_required", retry_at=None)
                    atomic(self.path, state)
                    return self._projection(state)
                if native["current_login_matches"] is not True:
                    raise HostError("selection_changed")
                # A paused effect must still notice the native owner's later
                # selection. Otherwise a manual change leaves the observer
                # permanently attached to the account that failed shutdown.
                # Only status/readback runs while paused; unchanged selection
                # must not retry usage refresh, client shutdown or switching.
                if state.get("failure_count", 0) >= 3 and not refresh and not manual_slot:
                    return self._projection(state)
                # Inspect the active account even when it is not an authorized
                # destination. Candidates stay strictly inside the allowlist.
                slots = list(dict.fromkeys([native["active_slot"], *state["policy"]["slots"]]))
                failures = {} if refresh or manual_slot else {
                    row["slot"]: {"revision": row["credential_revision"], "observed_at": row["observed_at"]}
                    for row in state.get("observations", [])
                    if row["status"] == "auth_required" and "credential_revision" in row
                }
                observed = self.transport(state["adapter"], "observe", slots=slots,
                                          expected_revision=native["selection_revision"], auth_failures=failures)
                public_observations = [{k: v for k, v in row.items() if k != "credential_revision"}
                                       for row in observed["observations"]]
                decision = policy({"now": self.clock(), "policy": state["policy"], "active": native["active_slot"],
                                   "observations": public_observations, "last_switch_at": state["last_switch_at"],
                                   "recovery_required": False, "login_matches": True,
                                   **({"manual_slot": manual_slot} if manual_slot else {})})
                state.update(observations=observed["observations"], accounts=decision["accounts"],
                             last_checked_at=self.clock(), action=decision["action"], target=decision["target"],
                             retry_at=decision["retry_at"] or self.clock() + 60, code=None)
                if decision["action"] == "switch":
                    if not manual_slot and not self.switch_allowed():
                        raise HostError("observer_stopped")
                    # Durable before the effect, with source selection and target.
                    # Native owner rechecks the same fence inside its transaction.
                    state["intent"] = {"id": operation_id or uuid.uuid4().hex,
                                       "source_revision": native["selection_revision"], "target": decision["target"]}
                    effects = {"restart_clients": decision["restart_clients"],
                               "force_quit_clients": decision["force_quit_clients"],
                               "not_after": decision["valid_until"]}
                    if decision["restart_clients"]:
                        directory = self.root / "handoffs"
                        if directory.is_symlink():
                            raise HostError("state_untrusted")
                        directory.mkdir(mode=0o700, exist_ok=True)
                        receipt_path = directory / (uuid.uuid4().hex + ".json")
                        state["intent"].update(handoff_receipt=str(receipt_path), started_at=self.clock())
                        state["action"] = "restarting"
                        effects["handoff_receipt"] = str(receipt_path)
                    atomic(self.path, state)
                    try:
                        result = self.transport(state["adapter"], "switch", slot=decision["target"],
                                                expected_revision=native["selection_revision"], reopen=True, **effects)
                    except Exception as error:
                        if getattr(error, "code", None) != "handoff_pending":
                            # A returned failure means this worker has stopped.
                            # A lost caller instead leaves its durable receipt.
                            state["intent"] = None if decision["restart_clients"] else state["intent"]
                        raise
                    state.update(native=result, intent=None, last_switch_at=self.clock(), action="selected",
                                 target=None, retry_at=self.clock() + 300, launch_failed=result["launch_failed"], failure_count=0)
                if operation_id:
                    state["last_operation_id"] = operation_id
            except (HostError, OSError, ValueError, subprocess.SubprocessError) as error:
                code = error.code if isinstance(error, HostError) else "native_unavailable"
                # Unrelated/remaining owners still require a safe-boundary wait.
                # Other repeated failures stop automatic attempts until Refresh.
                failures = state.get("failure_count", 0) + (code not in ("waiting_for_idle", "handoff_pending"))
                if code in ("other_clients_busy", "client_quit_refused", "client_quit_timeout"):
                    failures = 3  # Do not repeatedly close/reopen a refused client.
                state.update(action=code if code in ("waiting_for_idle", "selection_changed", "adapter_changed", "client_quit_refused", "client_quit_timeout", "client_changed", "handoff_pending") else "unavailable",
                             code=code, retry_at=self.clock() + (5 if code == "handoff_pending" else min(300, 60 * 2 ** min(failures, 3))), failure_count=failures)
                if manual_slot:
                    state["last_operation_id"] = operation_id
            atomic(self.path, state)
            return self._projection(state)

    def recover(self, *, rollback: bool, expected_revision: int, expected_selection: str) -> dict:
        with self._lock():
            state = self._load()
            if state["revision"] != expected_revision or "adapter" not in state:
                raise HostError("configuration_changed")
            native = self.transport(state["adapter"], "status")
            intent = state.get("intent") or {}
            if intent.get("handoff_receipt") and not native["recovery_required"] and not rollback:
                if native["selection_revision"] != expected_selection:
                    raise HostError("selection_changed")
                path = Path(intent["handoff_receipt"])
                if not path.exists():
                    marker = path.with_suffix(".pid.json")
                    if not marker.exists():
                        raise HostError("handoff_pending")
                    try:
                        os.kill(private_read(marker)["pid"], 0)
                    except ProcessLookupError:
                        pass
                    else:
                        raise HostError("handoff_pending")
            else:
                native = self.transport(state["adapter"], "rollback" if rollback else "recover", expected_revision=expected_selection)
            state.update(native=native, intent=None, action="disabled", retry_at=None, code=None,
                         policy={**state["policy"], "enabled": False}, revision=state["revision"] + 1, failure_count=0)
            atomic(self.path, state)
            return self._projection(state)

    def reopen(self, *, expected_selection: str) -> dict:
        with self._lock():
            state = self._load()
            if "adapter" not in state or state.get("intent"):
                raise HostError("handoff_pending")
            native = self.transport(state["adapter"], "reopen", expected_revision=expected_selection)
            state.update(native=native, launch_failed=False, code=None)
            atomic(self.path, state)
            return self._projection(state)


class Watcher:
    """Owned by the serving App (or explicit CLI watch), never an import effect."""
    def __init__(self, provider: NativeAccounts):
        self.provider = provider
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None
        self.provider.switch_allowed = lambda: not self.stop.is_set()

    def start(self):
        if self.thread is None:
            self.thread = threading.Thread(target=self.run, daemon=True, name="codex-account-observer")
            self.thread.start()
        return self

    def run(self):
        while not self.stop.is_set():
            delay = 60.0
            try:
                value = self.provider.tick()
                due = value.get("retry_at")
                delay = max(5, min(300, due - time.time())) if due else 60
            except (HostError, OSError, ValueError, subprocess.SubprocessError):
                delay = 60
            self.stop.wait(delay)

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser(prog="loopx-codex-accounts")
    parser.add_argument("operation", choices=("status", "clients", "configure", "refresh", "switch", "recover", "rollback", "reopen", "watch"))
    parser.add_argument("--runtime-root", type=Path, default=Path.home() / ".loopx")
    args = parser.parse_args()
    provider = NativeAccounts(args.runtime_root)
    try:
        if args.operation == "watch":
            watcher = Watcher(provider)
            try:
                watcher.run()
            except KeyboardInterrupt:
                watcher.close()
            return 0
        request = {} if args.operation in ("status", "clients", "refresh") else json.load(sys.stdin)
        if args.operation == "status":
            result = provider.status()
        elif args.operation == "clients":
            result = provider.clients()
        elif args.operation == "configure":
            result = provider.configure(**request)
        elif args.operation in ("refresh", "switch"):
            result = provider.tick(refresh=True, **request)
        elif args.operation == "reopen":
            result = provider.reopen(**request)
        else:
            result = provider.recover(rollback=args.operation == "rollback", **request)
        print(json.dumps({"ok": True, **result}))
        return 0
    except Exception as error:
        print(json.dumps({"ok": False, "code": error.code if isinstance(error, HostError) else "native_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
