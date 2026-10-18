"""IO adapter to the installed Personal Apps native account owner.

No copied account writer: the installed owner's locks, idle check, transaction,
readback and recovery remain authoritative. Paths/pins and credentials stay here,
outside policy input and browser projections. Run only through the opt-in host.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request


class HostError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# Address one inspected process, not an App bundle id shared by other routes.
# terminate() requests the normal AppKit quit lifecycle and can be refused.
QUIT = '''ObjC.import("AppKit");
function run(argv) {
  var app;
  // Process discovery can precede Launch Services registration. Do not read
  // executableURL.path until registration supplies the inspected executable.
  var deadline = Date.now() + 5000;
  do {
    app = $.NSRunningApplication.runningApplicationWithProcessIdentifier(Number(argv[0]));
    if (app && app.isTerminated) return;
    if (app && app.executableURL && app.isFinishedLaunching) break;
    delay(0.05);
  } while (Date.now() < deadline);
  if (!app || !app.executableURL || !app.isFinishedLaunching) throw Error("Client startup is still incomplete");
  if (ObjC.unwrap(app.executableURL.path) !== argv[1]) throw Error("Client executable changed");
  if (!app.terminate) throw Error("Client refused normal quit");
}'''


def trusted_file(path: Path) -> Path:
    path = path.resolve(strict=True)
    info = path.stat()
    if not path.is_file() or info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022:
        raise HostError("adapter_untrusted")
    return path


def discover() -> dict:
    command = shutil.which("personal-apps")
    codex = shutil.which("codex")
    if not command or not codex:
        raise HostError("adapter_unavailable")
    source = trusted_file(Path(command)).parent
    pins = {name: hashlib.sha256(trusted_file(source / name).read_bytes()).hexdigest()
            for name in ("personal_apps.py", "personal_accounts.py")}
    return {"source": str(source), "pins": pins, "codex": str(trusted_file(Path(codex)))}


class NativeHost:
    def __init__(self, adapter: dict, *, user_home: Path | None = None):
        source = Path(adapter["source"])
        for name in ("personal_apps.py", "personal_accounts.py"):
            if hashlib.sha256(trusted_file(source / name).read_bytes()).hexdigest() != adapter["pins"][name]:
                raise HostError("adapter_changed")
        self.codex = trusted_file(Path(adapter["codex"]))
        # This adapter runs in its own process; module names cannot collide with
        # an earlier owner version loaded by another request.
        sys.path.insert(0, str(source))
        self.apps = importlib.import_module("personal_apps")
        self.accounts = importlib.import_module("personal_accounts")
        self.home = user_home or Path.home()
        self.spec = self.apps.route(self.home, "gpt")
        self.expected = None
        self.not_after = None

        def idle():
            if self.not_after is not None and time.time() >= self.not_after:
                raise HostError("quota_stale")
            try:
                self.apps.account_idle(self.home, self.spec)
            except (ValueError, OSError, subprocess.SubprocessError):
                raise HostError("waiting_for_idle") from None
            if self.expected is not None and self.revision() != self.expected:
                raise HostError("selection_changed")

        self.manager = self.accounts.Accounts(
            self.home, self.spec, idle, lambda: str(self.codex),
            lambda selected: self.apps.clean_env(selected, self.home),
        )

    def revision(self) -> str:
        # last.json also changes for a manual re-selection of the same slot.
        values = [self.accounts.read_private(path) if path.exists() else b""
                  for path in (self.manager.active, self.manager.root / "last.json")]
        return hashlib.sha256(b"\0".join(values)).hexdigest()

    def status(self) -> dict:
        self.apps.check_provider(self.spec, "gpt")
        value = self.manager.status()
        result = {key: value[key] for key in (
            "slots", "active_slot", "current_login_matches", "recovery_required"
        )} | {"selection_revision": self.revision()}
        if sys.platform == "darwin":
            exe = self.apps.executable(self.apps.app_bundle(self.spec, "gpt", self.home))
            result["client_running"] = self.apps.running(exe, self.spec) is not None
        return result

    def close_client(self, expected: str, *, timeout: float = 25) -> bool:
        exe = self.apps.executable(self.apps.app_bundle(self.spec, "gpt", self.home))
        pid = self.apps.running(exe, self.spec)
        if self.revision() != expected:
            raise HostError("selection_changed")
        if pid is None:
            return False
        # Recheck exact ownership immediately before requesting a normal quit.
        if self.apps.running(exe, self.spec) != pid:
            raise HostError("client_changed")
        try:
            subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-", str(pid), str(exe)],
                           input=QUIT, text=True, check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            raise HostError("client_quit_refused") from None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            current = self.apps.running(exe, self.spec)
            if current is None:
                return True
            if current != pid:
                raise HostError("client_changed")
            time.sleep(0.1)
        raise HostError("client_quit_timeout")

    def reopen(self, expected: str) -> dict:
        value = self.status()
        if value["selection_revision"] != expected or value["recovery_required"] or value["current_login_matches"] is not True:
            raise HostError("selection_changed")
        self.apps.open_app(self.home, "gpt", [])
        return self.status()

    def switch(self, slot: str, expected: str, *, reopen: bool, restart_clients: bool = False,
               not_after: float | None = None) -> dict:
        if sys.platform != "darwin":
            raise HostError("platform_unavailable")
        self.expected = expected
        self.not_after = not_after
        before = self.status()
        if before["selection_revision"] != expected or before["recovery_required"] or before["current_login_matches"] is not True:
            raise HostError("selection_changed")
        # idle() is rechecked by the native owner under launch/account locks,
        # and immediately before its first mutation. This is the CAS fence,
        # including against manual selections outside LoopX.
        try:
            if restart_clients:
                if not_after is not None and time.time() >= not_after:
                    raise HostError("quota_stale")
                self.close_client(expected)
                # Children may flush and exit just after their App root. An
                # unrelated CLI/file owner remains a hold, never a kill target.
                deadline = time.monotonic() + 10
                while True:
                    try:
                        self.manager.idle()
                        break
                    except HostError as error:
                        if error.code != "waiting_for_idle":
                            raise
                        if time.monotonic() >= deadline:
                            raise HostError("other_clients_busy") from None
                        time.sleep(0.2)
            result = self.manager.switch(slot)
        except Exception as error:
            # A refused/failed transaction must not unnecessarily leave a
            # normally closed source App down. Native preflight blocks opening
            # an incomplete credential journal; a later manual choice wins.
            if restart_clients and before.get("client_running") and getattr(error, "code", None) != "client_changed":
                try:
                    if self.status().get("client_running") is False:
                        self.reopen(expected)
                except (HostError, OSError, ValueError, subprocess.SubprocessError):
                    pass
            raise
        value = self.status()
        if value["active_slot"] != slot or value["current_login_matches"] is not True:
            raise HostError("readback_failed")
        launch_failed = False
        if reopen:
            try:
                self.apps.open_app(self.home, "gpt", [])
            except (OSError, ValueError, subprocess.SubprocessError):
                launch_failed = True
        after = self.status()
        if after["selection_revision"] != value["selection_revision"]:
            raise HostError("selection_changed")
        value = after
        return value | {"history_untouched": result["history_untouched"], "launch_failed": launch_failed}

    def recover(self, rollback: bool, expected: str) -> dict:
        if sys.platform != "darwin":
            raise HostError("platform_unavailable")
        self.expected = expected
        self.manager.recover(rollback)
        return self.status()

    def observe(self, slots: list[str], expected: str, auth_failures: dict | None = None) -> dict:
        before = self.status()
        if before["selection_revision"] != expected or before["recovery_required"]:
            raise HostError("selection_changed")
        if not set(slots) <= set(before["slots"]) or len(slots) > 16:
            raise HostError("invalid_slots")
        rows = []
        for slot in slots:
            path = self.manager.auth if slot == before["active_slot"] else self.manager.slot(slot)
            try:
                raw = self.accounts.read_private(path)
                revision = hashlib.sha256(raw).hexdigest()
                failed = (auth_failures or {}).get(slot, {})
                if slot != before["active_slot"] and failed.get("revision") == revision:
                    rows.append({"slot": slot, "observed_at": failed["observed_at"],
                                 "status": "auth_required", "credential_revision": revision})
                    continue
                usage = read_usage(raw)
            except HostError as error:
                if error.code == "auth_required" and slot != before["active_slot"]:
                    try:
                        usage = self.refresh_inactive(slot, expected)
                    except (HostError, ValueError, OSError, subprocess.SubprocessError) as refresh_error:
                        auth_required = not isinstance(refresh_error, HostError) or refresh_error.code == "auth_required"
                        rows.append({"slot": slot, "observed_at": time.time(),
                                     "status": "auth_required" if auth_required else "unavailable",
                                     **({"credential_revision": revision} if auth_required else {})})
                        continue
                else:
                    rows.append({"slot": slot, "observed_at": time.time(),
                                 "status": "auth_required" if error.code == "auth_required" else "unavailable"})
                    continue
            rows.append({"slot": slot, "observed_at": time.time(), "status": "ok", "usage": usage})
        after = self.status()
        if after["selection_revision"] != expected:
            raise HostError("selection_changed")
        return {"observations": rows, "native": after}

    def refresh_inactive(self, slot: str, expected: str) -> dict:
        # Never refresh a copy of the active account: its running native client
        # owns refresh-token rotation. Distinct inactive accounts can refresh in
        # isolation, serialized with the native launch/enrol/switch owner.
        with self.manager.lock():
            status = self.status()
            if status["selection_revision"] != expected or slot == status["active_slot"]:
                raise HostError("selection_changed")
            target = self.manager.slot(slot)
            original = self.accounts.read_private(target)
            active = self.accounts.read_private(self.manager.auth)
            if self.accounts.identity(original) == self.accounts.identity(active):
                raise HostError("duplicate_account")
            self.accounts.private_dir(self.manager.root / "quota-probes")
            with tempfile.TemporaryDirectory(dir=self.manager.root / "quota-probes") as temp:
                home = Path(temp)
                self.accounts.atomic(home / "auth.json", original)
                self.accounts.atomic(home / "config.toml", b'cli_auth_credentials_store="file"\nforced_login_method="chatgpt"\n')
                usage = app_server_usage(str(self.codex), self.apps.clean_env(dict(self.spec, home=home), self.home))
                refreshed = self.accounts.read_private(home / "auth.json")
                if self.accounts.identity(original) != self.accounts.identity(refreshed):
                    raise HostError("auth_identity_changed")
                if self.accounts.read_private(target) != original or self.revision() != expected:
                    raise HostError("selection_changed")
                if refreshed != original:
                    self.accounts.atomic(target, refreshed)
                    if self.accounts.read_private(target) != refreshed:
                        raise HostError("readback_failed")
                return usage


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        raise HostError("quota_unavailable")


def quota_fields(value: dict) -> dict:
    """Allowlist transport fields before private persistence/TS invocation."""
    def limits(raw):
        if not isinstance(raw, dict):
            return None
        result = {k: raw[k] for k in ("allowed", "limit_reached") if k in raw}
        for key in ("primary", "secondary", "primary_window", "secondary_window"):
            item = raw.get(key)
            if isinstance(item, dict):
                result[key] = {k: item[k] for k in ("usedPercent", "resetsAt", "used_percent", "reset_at") if k in item}
        if isinstance(raw.get("credits"), dict):
            result["credits"] = {k: raw["credits"][k] for k in
                                 ("hasCredits", "has_credits", "balance", "unlimited") if k in raw["credits"]}
        return result

    result = {k: limits(value[k]) for k in ("rateLimits", "rate_limit") if k in value}
    if isinstance(value.get("rateLimitsByLimitId"), dict):
        result["rateLimitsByLimitId"] = {"codex": limits(value["rateLimitsByLimitId"].get("codex"))}
    if isinstance(value.get("credits"), dict):
        result["credits"] = limits({"credits": value["credits"]})["credits"]
    return result


def read_usage(raw: bytes) -> dict:
    """Native Codex usage observation only. Never send prompts or model calls."""
    try:
        data = json.loads(raw)
        tokens = data["tokens"]
        if data.get("auth_mode") not in (None, "chatgpt") or data.get("OPENAI_API_KEY"):
            raise HostError("auth_required")
        token, account_id = tokens["access_token"], tokens["account_id"]
        if not isinstance(token, str) or not isinstance(account_id, str) or not token or not account_id:
            raise HostError("auth_required")
        request = urllib.request.Request("https://chatgpt.com/backend-api/wham/usage", headers={
            "Authorization": "Bearer " + token, "chatgpt-account-id": account_id,
            "User-Agent": "codex-cli", "Accept": "application/json",
        })
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
            encoded = response.read(131073)
        if len(encoded) > 131072:
            raise HostError("quota_unavailable")
        value = json.loads(encoded)
        if not isinstance(value, dict):
            raise HostError("quota_unavailable")
        # Discard account identity and unrelated product/organization metadata.
        return quota_fields(value)
    except urllib.error.HTTPError as error:
        raise HostError("auth_required" if error.code in (401, 403) else "quota_unavailable") from None
    except (KeyError, TypeError, ValueError, OSError):
        raise HostError("quota_unavailable") from None


def app_server_usage(binary: str, env: dict) -> dict:
    process = subprocess.Popen([binary, "app-server"], env=env, stdin=subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    selector = selectors.DefaultSelector()
    assert process.stdout and process.stdin
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = b""
    deadline = time.monotonic() + 25
    total = 0

    def call(index, method, params):
        nonlocal pending, total
        process.stdin.write(json.dumps({"id": index, "method": method, "params": params}).encode() + b"\n")
        process.stdin.flush()
        while time.monotonic() < deadline:
            if not selector.select(max(0, deadline - time.monotonic())):
                break
            chunk = os.read(process.stdout.fileno(), 65536)
            total += len(chunk)
            if not chunk or total > 1048576:
                break
            pending += chunk
            while b"\n" in pending:
                line, pending = pending.split(b"\n", 1)
                packet = json.loads(line)
                if packet.get("id") == index:
                    if "error" in packet:
                        raise HostError("auth_required")
                    return packet["result"]
        raise HostError("quota_unavailable")

    try:
        call(1, "initialize", {"clientInfo": {"name": "loopx_native_accounts", "version": "1"}})
        process.stdin.write(b'{"method":"initialized"}\n')
        process.stdin.flush()
        call(2, "account/read", {"refreshToken": True})
        return quota_fields(call(3, "account/rateLimits/read", {}))
    finally:
        selector.close()
        process.stdin.close()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.terminate()  # Only this disposable observer process.
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdout.close()


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--result-file":
        from .native import SCHEMA, atomic
        atomic(Path(sys.argv[2]).with_suffix(".pid.json"),
               {"schema_version": SCHEMA, "pid": os.getpid()})
    try:
        request = json.load(sys.stdin)
        host = NativeHost(request["adapter"])
        operation = request["operation"]
        if operation == "status":
            value = host.status()
        elif operation == "observe":
            value = host.observe(request["slots"], request["expected_revision"], request.get("auth_failures"))
        elif operation == "switch":
            value = host.switch(request["slot"], request["expected_revision"], reopen=request["reopen"],
                                restart_clients=request.get("restart_clients", False), not_after=request.get("not_after"))
        elif operation == "reopen":
            value = host.reopen(request["expected_revision"])
        elif operation in ("recover", "rollback"):
            value = host.recover(operation == "rollback", request["expected_revision"])
        else:
            raise HostError("unsupported_operation")
        packet = {"ok": True, "result": value}
    except Exception as error:  # Native diagnostics can contain paths/identities.
        packet = {"ok": False, "code": error.code if isinstance(error, HostError) else "native_unavailable"}
    if len(sys.argv) == 3 and sys.argv[1] == "--result-file":
        # A detached worker completes even if its calling Codex App goes away.
        # The private receipt lets a restarted observer reconcile, not replay.
        from .native import SCHEMA, atomic
        atomic(Path(sys.argv[2]), {"schema_version": SCHEMA, **packet})
    else:
        print(json.dumps(packet))
    return 0 if packet["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
