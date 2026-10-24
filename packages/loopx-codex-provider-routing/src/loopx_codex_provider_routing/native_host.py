"""IO adapter to the installed Personal Apps native account owner.

No copied account writer: the installed owner's locks, idle check, transaction,
readback and recovery remain authoritative. Paths/pins and credentials stay here,
outside policy input and browser projections. Run only through the opt-in host.
"""
from __future__ import annotations

import hashlib
import ctypes
import importlib
import json
import math
import os
from pathlib import Path
import plistlib
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse


class HostError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


# LoopX chat surfaces keep a Codex app-server attached to the GPT home while
# idle. They are product-owned clients of that home, so an unattended handoff
# pauses them instead of treating them as an unrelated CLI hold.
CHAT_AGENTS = ("com.loopx.personal-bots.chat",)


def process_identity(pid: int) -> tuple[int, str, int, int] | None:
    """Kernel executable/start identity; command text is not process ownership."""
    class BSDInfo(ctypes.Structure):
        _fields_ = ([(name, ctypes.c_uint32) for name in (
            "flags", "status", "xstatus", "pid", "ppid", "uid", "gid", "ruid",
            "rgid", "svuid", "svgid", "reserved")]
            + [("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32)]
            + [("tail", ctypes.c_uint32 * 6), ("seconds", ctypes.c_uint64),
               ("microseconds", ctypes.c_uint64)])
    try:
        lib = ctypes.CDLL("/usr/lib/libproc.dylib")
        info, path = BSDInfo(), ctypes.create_string_buffer(4096)
        if (lib.proc_pidinfo(pid, 3, 0, ctypes.byref(info), ctypes.sizeof(info)) != ctypes.sizeof(info)
                or info.pid != pid or info.uid != os.getuid()
                or lib.proc_pidpath(pid, path, len(path)) <= 0):
            return None
        return info.ppid, os.fsdecode(path.value), info.seconds, info.microseconds
    except (OSError, ValueError):
        return None


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
  var started = Number(app.launchDate.timeIntervalSince1970);
  if (argv.length > 2 && argv[2] !== "unknown" && started !== Number(argv[2])) throw Error("Client launch changed");
  return JSON.stringify({launch_time: started, accepted: Boolean(app.terminate)});
}'''

FORCE_QUIT = QUIT.replace(
    'return JSON.stringify({launch_time: started, accepted: Boolean(app.terminate)});',
    'if (!app.forceTerminate) throw Error("Client force quit failed");',
)

# Freeze launch identity before graceful quit so a refused/timed-out quit can
# escalate only against that same instance, without another confirmation.
PROBE = QUIT.replace(
    'return JSON.stringify({launch_time: started, accepted: Boolean(app.terminate)});',
    'return JSON.stringify({launch_time: started});',
)


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
        if sys.platform == "darwin" and (self.manager.root / "chat-recovery.json").exists():
            self.resume_chat_agents()

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

    def close_client(self, expected: str, *, timeout: float = 25, force: bool = False,
                     inspected: tuple | None = None) -> bool:
        exe = self.apps.executable(self.apps.app_bundle(self.spec, "gpt", self.home))
        pid = self.apps.running(exe, self.spec)
        if inspected is not None and (pid != inspected[0] or
                (pid is not None and process_identity(pid) != inspected[1])):
            raise HostError("client_changed")
        if self.revision() != expected:
            raise HostError("selection_changed")
        if pid is None:
            return False
        # Recheck exact ownership immediately before requesting a quit.
        if self.apps.running(exe, self.spec) != pid:
            raise HostError("client_changed")
        if force:
            try:
                result = subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-", str(pid), str(exe)],
                                        input=PROBE, text=True, check=True, capture_output=True, timeout=10)
                receipt = json.loads(result.stdout)
                launch_time = receipt["launch_time"]
                # Launch Services can report a nil launchDate for an App started
                # outside the normal Finder/open registration path. Fall back to
                # the inspected PID + executable identity instead of stalling.
                if launch_time is not None and (type(launch_time) not in (int, float)
                                               or not math.isfinite(launch_time) or launch_time <= 0):
                    raise ValueError()
            except (OSError, subprocess.SubprocessError, AttributeError, KeyError, TypeError, ValueError, OverflowError):
                if self.apps.running(exe, self.spec) is None:
                    return True
                raise HostError("client_quit_refused") from None
            if self.revision() != expected:
                raise HostError("selection_changed")
            current = self.apps.running(exe, self.spec)
            if current is None:
                return True
            if current != pid:
                raise HostError("client_changed")
            if self.not_after is not None and time.time() >= self.not_after:
                raise HostError("quota_stale")
            identity = str(launch_time) if launch_time is not None else "unknown"
            try:
                return self._graceful_quit(pid, exe, timeout, identity=identity)
            except HostError as error:
                if error.code not in ("client_quit_refused", "client_quit_timeout"):
                    raise
            # Opt-in covers fallback only; a quit dialog must not bypass a
            # later selection/process/quota change before force termination.
            if self.revision() != expected:
                raise HostError("selection_changed")
            current = self.apps.running(exe, self.spec)
            if current is None:
                return True
            if current != pid:
                raise HostError("client_changed")
            if self.not_after is not None and time.time() >= self.not_after:
                raise HostError("quota_stale")
            try:
                subprocess.run(["/usr/bin/osascript", "-l", "JavaScript", "-", str(pid), str(exe), identity],
                               input=FORCE_QUIT, text=True, check=True, capture_output=True, timeout=10)
            except (OSError, subprocess.SubprocessError):
                if self.apps.running(exe, self.spec) is None:
                    return True
                raise HostError("client_quit_refused") from None
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                current = self.apps.running(exe, self.spec)
                if current is None:
                    return True
                if current != pid:
                    raise HostError("client_changed")
                time.sleep(0.1)
            raise HostError("client_quit_timeout")
        return self._graceful_quit(pid, exe, timeout)

    def _graceful_quit(self, pid: int, exe: Path, timeout: float, *, identity: str | None = None) -> bool:
        command = ["/usr/bin/osascript", "-l", "JavaScript", "-", str(pid), str(exe)]
        if identity is not None:
            command.append(identity)
        try:
            result = subprocess.run(command,
                                    input=QUIT, text=True, check=True, capture_output=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            raise HostError("client_quit_refused") from None
        accepted = True
        if getattr(result, "stdout", ""):
            try:
                receipt = json.loads(result.stdout)
                accepted = receipt["accepted"]
                if type(accepted) is not bool:
                    raise ValueError()
            except (AttributeError, KeyError, TypeError, ValueError):
                raise HostError("client_changed") from None
        if not accepted:
            raise HostError("client_quit_refused")
        deadline = time.monotonic() + timeout
        while accepted and time.monotonic() < deadline:
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

    def _home_owners(self) -> list[int]:
        result = subprocess.run(["/usr/sbin/lsof", "-Fp", "+d", str(self.spec["home"])],
                                capture_output=True, text=True, timeout=10)
        if result.returncode not in (0, 1):
            raise HostError("ownership_unverified")
        return [int(line[1:]) for line in result.stdout.splitlines()
                if line.startswith("p") and line[1:].isdigit()]

    def inspect_client_helpers(self) -> tuple[tuple, dict]:
        """Freeze descendants of the inspected launch BEFORE requesting quit."""
        bundle = str(self.apps.app_bundle(self.spec, "gpt", self.home))
        exe = self.apps.executable(Path(bundle))
        pid = self.apps.running(exe, self.spec)
        if pid is None:
            return (None, None), {}
        root = process_identity(pid)
        if root is None or root[1] != str(exe):
            raise HostError("ownership_unverified")
        result = subprocess.run(["/bin/ps", "-axo", "pid="],
                                capture_output=True, text=True, check=True, timeout=5)
        processes = {int(p): process_identity(int(p)) for p in result.stdout.split() if p.isdigit()}
        descendants, helpers = {pid}, {}
        while True:
            added = {p for p, identity in processes.items()
                     if identity is not None and identity[0] in descendants and p not in descendants}
            if not added:
                break
            descendants.update(added)
        for child in descendants - {pid}:
            identity = processes[child]
            if identity[1].startswith(bundle + "/"):
                helpers[child] = identity[1:]  # Reparenting after quit is expected.
        if process_identity(pid) != root or self.apps.running(exe, self.spec) != pid:
            raise HostError("client_changed")
        return (pid, root), helpers

    def retire_app_helpers(self, helpers: dict) -> None:
        """Retire only still-identical, previously observed launch descendants."""
        exe = self.apps.executable(self.apps.app_bundle(self.spec, "gpt", self.home))
        if self.apps.running(exe, self.spec) is not None:
            raise HostError("client_changed")
        for pid in self._home_owners():
            if pid not in helpers:
                continue
            identity = process_identity(pid)
            if identity is None or identity[1:] != helpers[pid]:
                continue
            if self.revision() != self.expected:
                raise HostError("selection_changed")
            if self.not_after is not None and time.time() >= self.not_after:
                raise HostError("quota_stale")
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    def _chat_recovery(self) -> tuple[Path, dict]:
        from .native import SCHEMA, private_read
        path = self.manager.root / "chat-recovery.json"
        value = private_read(path) if path.exists() else {"schema_version": SCHEMA, "agents": {}}
        if (not isinstance(value.get("agents"), dict) or
                any(label not in CHAT_AGENTS or not isinstance(digest, str)
                    for label, digest in value["agents"].items())):
            raise HostError("state_untrusted")
        if value["agents"]:
            owner = value.get("owner", {})
            identity = owner.get("identity") if isinstance(owner, dict) else None
            if (not isinstance(owner, dict) or type(owner.get("pid")) is not int or owner["pid"] <= 1 or
                    not isinstance(identity, list) or len(identity) != 3 or
                    not isinstance(identity[0], str) or
                    any(type(v) is not int or v < 0 for v in identity[1:])):
                raise HostError("state_untrusted")
        return path, value

    def _chat_definition(self, plist: Path, label: str) -> str:
        raw = trusted_file(plist).read_bytes()
        try:
            value = plistlib.loads(raw)
            if not isinstance(value, dict) or value.get("Label") != label:
                raise ValueError()
        except (ValueError, plistlib.InvalidFileException):
            raise HostError("state_untrusted") from None
        return hashlib.sha256(raw).hexdigest()

    def pause_chat_agents(self) -> None:
        """Unload known chat agents that keep this home attached while idle."""
        from .native import atomic
        self.accounts.private_dir(self.manager.root)
        path, recovery = self._chat_recovery()
        if recovery["agents"]:
            raise HostError("chat_recovery_pending")
        for label in CHAT_AGENTS:
            plist = self.home / "Library/LaunchAgents" / f"{label}.plist"
            if not plist.is_file():
                continue
            target = f"gui/{os.getuid()}/{label}"
            loaded = subprocess.run(["/bin/launchctl", "print", target],
                                    capture_output=True, text=True, timeout=10)
            if loaded.returncode != 0:
                continue
            match = re.search(r"^\s*pid = (\d+)\s*$", loaded.stdout, re.MULTILINE)
            if match is None:
                continue
            service_pid = int(match[1])
            service = process_identity(service_pid)
            if service is None:
                continue
            # A product label alone is not enough: this loaded instance must
            # actually own the inspected home, possibly through app-server.
            owns_home = False
            for owner in self._home_owners():
                seen = set()
                while owner > 1 and owner not in seen:
                    seen.add(owner)
                    identity = process_identity(owner)
                    if identity is None:
                        break
                    if owner == service_pid:
                        owns_home |= identity == service
                        break
                    owner = identity[0]
            if not owns_home:
                continue
            digest = self._chat_definition(plist, label)
            current = subprocess.run(["/bin/launchctl", "print", target],
                                     capture_output=True, text=True, timeout=10)
            if (current.returncode or process_identity(service_pid) != service or
                    re.search(r"^\s*pid = " + str(service_pid) + r"\s*$", current.stdout, re.MULTILINE) is None):
                raise HostError("client_changed")
            if self.revision() != self.expected:
                raise HostError("selection_changed")
            if self.not_after is not None and time.time() >= self.not_after:
                raise HostError("quota_stale")
            # Retain recovery BEFORE an operation which can take effect and
            # then time out. The detached worker's finally or the next native
            # request reconciles this same intent, never another account swap.
            recovery["agents"][label] = digest
            owner = process_identity(os.getpid())
            if owner is None:
                raise HostError("ownership_unverified")
            recovery["owner"] = {"pid": os.getpid(), "identity": list(owner[1:])}
            atomic(path, recovery)
            try:
                result = subprocess.run(["/bin/launchctl", "bootout", target],
                                        capture_output=True, text=True, timeout=20)
                if result.returncode or self._chat_loaded(target):
                    raise HostError("chat_pause_failed")
            except (OSError, subprocess.SubprocessError):
                raise HostError("chat_pause_failed") from None

    def _chat_loaded(self, target: str) -> bool:
        return subprocess.run(["/bin/launchctl", "print", target],
                              capture_output=True, text=True, timeout=10).returncode == 0

    def resume_chat_agents(self) -> None:
        from .native import atomic
        path, recovery = self._chat_recovery()
        if recovery["agents"]:
            owner = recovery["owner"]
            current = process_identity(owner["pid"])
            if owner["pid"] != os.getpid():
                if current is not None and list(current[1:]) == owner["identity"]:
                    return  # A concurrent status read must not undo a live handoff.
                if current is None:
                    try:
                        os.kill(owner["pid"], 0)
                    except ProcessLookupError:
                        pass
                    except OSError:
                        return
                    else:
                        return  # Unreadable identity does not prove worker exit.
        failed = False
        for label, digest in list(recovery["agents"].items()):
            plist = self.home / "Library/LaunchAgents" / f"{label}.plist"
            target = f"gui/{os.getuid()}/{label}"
            try:
                if self._chat_definition(plist, label) != digest:
                    raise HostError("chat_resume_failed")
                if not self._chat_loaded(target):
                    try:
                        subprocess.run(["/bin/launchctl", "bootstrap", f"gui/{os.getuid()}", str(plist)],
                                       capture_output=True, text=True, timeout=20)
                    except (OSError, subprocess.SubprocessError):
                        if not self._chat_loaded(target):
                            raise HostError("chat_resume_failed") from None
                    # A timeout/nonzero reply is ambiguous. Read the actual
                    # service state before accepting or retrying restoration.
                if not self._chat_loaded(target):
                    raise HostError("chat_resume_failed")
            except (HostError, OSError, subprocess.SubprocessError):
                failed = True
                continue  # Attempt every outstanding restoration.
            del recovery["agents"][label]
            atomic(path, recovery)
        if failed:
            raise HostError("chat_resume_failed")

    def switch(self, slot: str, expected: str, *, reopen: bool, restart_clients: bool = False,
               force_quit_clients: bool = False, not_after: float | None = None) -> dict:
        if sys.platform != "darwin":
            raise HostError("platform_unavailable")
        self.resume_chat_agents()
        if self._chat_recovery()[1]["agents"]:
            raise HostError("chat_recovery_pending")
        self.expected = expected
        self.not_after = not_after
        before = self.status()
        if before["selection_revision"] != expected or before["recovery_required"] or before["current_login_matches"] is not True:
            raise HostError("selection_changed")
        # idle() is rechecked by the native owner under launch/account locks,
        # and immediately before its first mutation. This is the CAS fence,
        # including against manual selections outside LoopX.
        result, restore_error = None, None
        try:
            try:
                if restart_clients:
                    if not_after is not None and time.time() >= not_after:
                        raise HostError("quota_stale")
                    inspected, helpers = self.inspect_client_helpers()
                    self.close_client(expected, inspected=inspected,
                                      **({"force": True} if force_quit_clients else {}))
                    # Orphaned App helpers and idle chat transports are
                    # product-owned clients of this home. Retire or pause them;
                    # an unrelated CLI/file owner remains a hold, never a kill
                    # target.
                    self.retire_app_helpers(helpers)
                    self.pause_chat_agents()
                    deadline = time.monotonic() + 15
                    while True:
                        try:
                            self.manager.idle()
                            break
                        except HostError as error:
                            if error.code != "waiting_for_idle":
                                raise
                            if time.monotonic() >= deadline:
                                raise HostError("other_clients_busy") from None
                            self.retire_app_helpers(helpers)
                            time.sleep(0.2)
                result = self.manager.switch(slot)
            except Exception:
                # A refused/failed transaction must not unnecessarily leave a
                # normally closed source App down. Native preflight blocks
                # opening an incomplete credential journal; a later manual
                # choice wins.
                if restart_clients and before.get("client_running"):
                    try:
                        if self.status().get("client_running") is False:
                            self.reopen(expected)
                    except (HostError, OSError, ValueError, subprocess.SubprocessError):
                        pass
                raise
        finally:
            # Restore product-owned transports even when the handoff fails.
            try:
                self.resume_chat_agents()
            except HostError as error:
                if result is None:
                    raise
                # A committed account still needs its App reopened. Report the
                # retained service recovery after that original continuation.
                restore_error = error
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
        if restore_error is not None:
            raise restore_error
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
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
                encoded = response.read(131073)
        except urllib.error.HTTPError:
            raise  # Authentication/HTTP denials do not change transport.
        except OSError:
            encoded = socks_usage(token, account_id)
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


def socks_usage(token: str, account_id: str) -> bytes:
    """Use an already configured SOCKS route after an HTTP transport failure.

    urllib has no SOCKS transport. Keep credentials on stdin, verified HTTPS,
    the fixed quota endpoint, no redirects and the original response bound.
    A missing route/tool stays unknown; this never configures a proxy.
    """
    proxies = urllib.request.getproxies()
    proxy = proxies.get("all") or proxies.get("socks", "")
    if urllib.parse.urlsplit(proxy).scheme == "socks":
        proxy = "socks5h:" + proxy.split(":", 1)[1]
    binary = shutil.which("curl")
    if not binary or urllib.parse.urlsplit(proxy).scheme not in {"socks5", "socks5h"}:
        raise HostError("quota_unavailable")
    headers = ["Authorization: Bearer " + token, "chatgpt-account-id: " + account_id,
               "User-Agent: codex-cli", "Accept: application/json"]
    if any("\n" in value or "\r" in value for value in headers):
        raise HostError("auth_required")
    config = "\n".join(["proxy = " + json.dumps(proxy),
                         *("header = " + json.dumps(value) for value in headers)]) + "\n"
    result = subprocess.run([str(trusted_file(Path(binary))), "-q", "--config", "-",
        "--url", "https://chatgpt.com/backend-api/wham/usage", "--proto", "=https",
        "--max-time", "10", "--max-filesize", "131072", "--silent", "--show-error",
        "--write-out", "\n%{http_code}"], input=config.encode(), capture_output=True, timeout=12)
    status = result.stdout[-3:]
    if status in {b"401", b"403"}:
        raise HostError("auth_required")
    if result.returncode or status != b"200" or len(result.stdout) > 131076:
        raise HostError("quota_unavailable")
    return result.stdout[:-4]


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
                                restart_clients=request.get("restart_clients", False),
                                force_quit_clients=request.get("force_quit_clients", False), not_after=request.get("not_after"))
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
