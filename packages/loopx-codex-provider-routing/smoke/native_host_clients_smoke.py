"""Offline production-switch counterexamples for scoped client coordination."""
from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
import plistlib
from types import SimpleNamespace
import subprocess
import sys
import tempfile
from unittest.mock import patch

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PACKAGE_ROOT / "src"))
host_module = importlib.import_module("loopx_codex_provider_routing.native_host")
from loopx_codex_provider_routing.native import private_read

BUNDLE = "/Applications/Example.app"
EXE = BUNDLE + "/Contents/MacOS/Example"
HELPER = BUNDLE + "/Contents/Resources/codex"
LABEL = "com.loopx.personal-bots.chat"


class Fixture:
    def __init__(self, home):
        self.home, self.app_pid, self.active = home, 100, "a"
        self.revision, self.switches = "before", 0
        self.holders, self.killed, self.calls = [101, 200], [], []
        self.loaded, self.pause_error, self.resume_error = True, None, None
        self.records = {100: (1, EXE, 1000, 1), 101: (100, HELPER, 1001, 2),
                        200: (1, HELPER, 999, 3), 300: (1, "/test/chat", 900, 4),
                        301: (300, "/test/app-server", 901, 5)}
        self.records[os.getpid()] = (1, "/test/python", 800, 8)
        self.plist = home / "Library/LaunchAgents" / (LABEL + ".plist")
        self.plist.parent.mkdir(parents=True)
        self.plist.write_bytes(plistlib.dumps({"Label": LABEL, "ProgramArguments": ["/test/chat"]}))
        self.plist.chmod(0o600)
        self.root = home / "native-accounts"
        self.root.mkdir(mode=0o700)
        self.host = host_module.NativeHost.__new__(host_module.NativeHost)
        self.host.home = home
        self.host.spec = {"home": home}
        self.host.not_after, self.host.expected = None, "before"
        self.host.accounts = SimpleNamespace(private_dir=lambda p: p.mkdir(mode=0o700, exist_ok=True))
        self.host.apps = SimpleNamespace(app_bundle=lambda *_: Path(BUNDLE), executable=lambda *_: EXE,
                                         running=lambda *_: self.app_pid, open_app=self.open_app)
        self.host.revision = lambda: self.revision
        self.host.status = lambda: {"selection_revision": self.revision, "recovery_required": False,
                                    "current_login_matches": True, "active_slot": self.active,
                                    "client_running": self.app_pid is not None}
        self.host.manager = SimpleNamespace(root=self.root, idle=self.idle, switch=self.switch)
        self.host.close_client = self.close

    def close(self, _expected, **kwargs):
        assert kwargs["inspected"] == (100, self.records[100])
        self.app_pid = None
        self.records[101] = (1, *self.records[101][1:])
        return True

    def open_app(self, *_):
        self.app_pid = 400

    def idle(self):
        if self.holders:
            raise host_module.HostError("waiting_for_idle")

    def switch(self, _slot):
        self.idle()
        self.switches += 1
        self.active, self.revision = "b", "after"
        return {"history_untouched": True}

    def run(self, command, **_kwargs):
        self.calls.append(command)
        if command[0] == "/bin/ps":
            return SimpleNamespace(returncode=0, stdout="\n".join(map(str, self.records)))
        if command[0] == "/usr/sbin/lsof":
            return SimpleNamespace(returncode=0, stdout="".join(f"p{p}\n" for p in self.holders))
        verb = command[1]
        if verb == "print":
            return SimpleNamespace(returncode=0 if self.loaded else 113, stdout="pid = 300\n" if self.loaded else "")
        if verb == "bootout":
            self.loaded = False
            self.holders = [p for p in self.holders if p != 301]
            if self.pause_error:
                raise self.pause_error
        if verb == "bootstrap":
            if self.resume_error == "nonzero":
                return SimpleNamespace(returncode=5, stdout="")
            if self.resume_error == "timeout_no_effect":
                raise subprocess.TimeoutExpired(command, 20)
            self.loaded = True
            if self.resume_error == "timeout_after_effect":
                raise subprocess.TimeoutExpired(command, 20)
        return SimpleNamespace(returncode=0, stdout="")

    def kill(self, pid, _signal):
        if _signal == 0:
            if pid not in self.records:
                raise ProcessLookupError()
            return
        self.killed.append(pid)
        self.holders.remove(pid)

    def context(self):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(host_module.sys, "platform", "darwin"))
        stack.enter_context(patch.object(host_module, "process_identity", side_effect=lambda p: self.records.get(p)))
        stack.enter_context(patch.object(host_module.subprocess, "run", side_effect=self.run))
        stack.enter_context(patch.object(host_module.os, "kill", side_effect=self.kill))
        return stack

    def handoff(self, **kwargs):
        return self.host.switch("b", "before", reopen=True, restart_clients=True, **kwargs)


def error(code, operation):
    try:
        operation()
    except host_module.HostError as failure:
        assert failure.code == code, failure.code
    else:
        raise AssertionError("expected " + code)


with tempfile.TemporaryDirectory() as temporary:
    counter = 0
    def fixture():
        global counter
        counter += 1
        home = Path(temporary).resolve() / str(counter)
        home.mkdir(mode=0o700)
        return Fixture(home)

    # The same bundled binary does not establish launch ownership. Only the
    # captured descendant is eligible after it becomes orphaned.
    f = fixture()
    with f.context():
        inspected, helpers = f.host.inspect_client_helpers()
        assert set(helpers) == {101}
        f.close("before", inspected=inspected)
        f.host.retire_app_helpers(helpers)
    assert f.killed == [101] and f.holders == [200]

    f = fixture()
    with f.context():
        error("client_changed", lambda: host_module.NativeHost.close_client(
            f.host, "before", inspected=(100, (1, EXE, 99, 0))))

    # A remaining independent CLI blocks the full switch and leaves its work
    # untouched; the source App is reopened without changing the account.
    f = fixture()
    clock = iter(range(0, 1000, 20))
    with f.context(), patch.object(host_module.time, "monotonic", side_effect=lambda: next(clock)):
        error("other_clients_busy", f.handoff)
    assert f.killed == [101] and f.holders == [200] and f.switches == 0
    assert f.active == "a" and f.app_pid is not None

    # PID reuse, a prior orphan, a new launch and unprovable identity stay holds.
    for mutation in ("pid_reused", "unreadable", "new_launch", "selection_changed", "quota_stale"):
        f = fixture()
        with f.context():
            inspected, helpers = f.host.inspect_client_helpers()
            f.close("before", inspected=inspected)
            if mutation == "pid_reused":
                f.records[101] = (1, HELPER, 1002, 7)
            elif mutation == "unreadable":
                del f.records[101]
            elif mutation == "new_launch":
                f.app_pid = 400
            elif mutation == "selection_changed":
                f.revision = "manual"
            else:
                f.host.not_after = 0
            if mutation in ("new_launch", "selection_changed", "quota_stale"):
                error({"new_launch": "client_changed", "selection_changed": "selection_changed",
                       "quota_stale": "quota_stale"}[mutation], lambda: f.host.retire_app_helpers(helpers))
            else:
                f.host.retire_app_helpers(helpers)
        assert not f.killed

    # Exercise the production switch: a valid descendant and same-home Chat
    # recover, with one credential transaction and the original App reopened.
    f = fixture()
    f.holders = [101, 301]
    with f.context():
        result = f.handoff()
    assert f.killed == [101] and f.loaded and f.switches == 1
    assert result["active_slot"] == "b" and not result["launch_failed"]
    assert private_read(f.root / "chat-recovery.json")["agents"] == {}

    # Disabled restart retains the baseline: no signals or service effects.
    f = fixture()
    f.holders = []
    with f.context():
        f.host.switch("b", "before", reopen=False, restart_clients=False)
    assert not f.killed and not f.calls
    assert not (f.root / "chat-recovery.json").exists()

    # A product Chat label attached to another home is not paused.
    f = fixture()
    with f.context():
        f.host.pause_chat_agents()
    assert not any(c[1] == "bootout" for c in f.calls)
    assert not (f.root / "chat-recovery.json").exists()

    # bootout has already taken effect when its reply times out. finally must
    # still restore Chat and must not attempt the credential transaction.
    f = fixture()
    f.holders = [101, 301]
    f.pause_error = subprocess.TimeoutExpired("bootout", 20)
    with f.context():
        error("chat_pause_failed", f.handoff)
    assert f.loaded and f.switches == 0 and f.app_pid is not None
    assert private_read(f.root / "chat-recovery.json")["agents"] == {}

    for failure in ("nonzero", "timeout_no_effect"):
        f = fixture()
        f.holders = [101, 301]
        f.resume_error = failure
        with f.context():
            error("chat_resume_failed", f.handoff)
        assert not f.loaded and f.switches == 1
        assert f.app_pid is not None
        assert private_read(f.root / "chat-recovery.json")["agents"]
        # The next native request reconciles the existing intent. It does not
        # switch credentials again, and handles an already-restored service.
        f.resume_error = None
        with f.context():
            f.host.resume_chat_agents()
            f.host.resume_chat_agents()
        assert f.loaded and f.switches == 1
        assert private_read(f.root / "chat-recovery.json")["agents"] == {}

    f = fixture()
    f.holders = [101, 301]
    f.resume_error = "timeout_after_effect"
    with f.context():
        assert f.handoff()["active_slot"] == "b"
    assert f.loaded and f.switches == 1

    # Recovery cannot bootstrap a user-edited plist or follow an injected label.
    f = fixture()
    f.holders = [301]
    with f.context():
        f.host.pause_chat_agents()
        f.plist.write_text("changed by user")
        error("chat_resume_failed", f.host.resume_chat_agents)
    assert not f.loaded
    assert private_read(f.root / "chat-recovery.json")["agents"]

    # A separate status host cannot restore a live worker's paused services.
    # A dead/reused worker PID does permit bounded restoration of that intent.
    f = fixture()
    f.holders = [301]
    with f.context():
        f.host.pause_chat_agents()
        journal = f.root / "chat-recovery.json"
        recovery = private_read(journal)
        recovery["owner"]["pid"] = 500
        f.records[500] = (1, *recovery["owner"]["identity"])
        journal.write_text(json.dumps(recovery))
        count = len(f.calls)
        f.host.resume_chat_agents()
        assert not f.loaded and len(f.calls) == count
        error("chat_recovery_pending", f.handoff)
        assert f.switches == 0
        f.records[500] = None
        f.host.resume_chat_agents()
        assert not f.loaded  # Unknown identity of a live PID is not exit.
        f.records[500] = (1, "/test/python", 1200, 10)
        f.host.resume_chat_agents()
    assert f.loaded and private_read(journal)["agents"] == {}

    f = fixture()
    (f.root / "chat-recovery.json").symlink_to(f.plist)
    with f.context():
        error("state_untrusted", f.host.resume_chat_agents)
    assert not f.calls

    f = fixture()
    (f.root / "chat-recovery.json").write_text(json.dumps({
        "schema_version": "codex_native_accounts_v1", "agents": {"unrelated.service": "digest"}}))
    (f.root / "chat-recovery.json").chmod(0o600)
    with f.context():
        error("state_untrusted", f.host.resume_chat_agents)
    assert not f.calls

# Exercise the real Darwin kernel probe without inspecting another user's args.
if sys.platform == "darwin":
    identity = host_module.process_identity(os.getpid())
    assert identity and identity[2] > 0 and Path(identity[1]).is_absolute(), identity

print("native host client handoff smoke passed (identity, scope, switch and recovery)")
