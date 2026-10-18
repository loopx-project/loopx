"""Account handoff outcomes, using the shipped TS decision and private IO store.

Synthetic native transport isolates model/network/auth effects. Installed native
transaction qualification is a separate opt-in drill, never a live-account test.
"""
import importlib
import json
import os
import plistlib
import shutil
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native slot effects use the POSIX account owner")


@pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None,
                    reason="Actual AppKit registration requires macOS and Swift")
def test_normal_quit_waits_for_owned_cold_app_registration(native, tmp_path):
    """A PID can exist before NSRunningApplication supplies executableURL."""
    bundle = tmp_path / "ColdFixture.app"
    executable = bundle / "Contents/MacOS/ColdFixture"
    executable.parent.mkdir(parents=True)
    (bundle / "Contents/Info.plist").write_bytes(plistlib.dumps({
        "CFBundleExecutable": "ColdFixture", "CFBundleIdentifier": "dev.loopx.cold-fixture",
        "CFBundleName": "ColdFixture", "CFBundlePackageType": "APPL",
    }))
    source = tmp_path / "fixture.swift"
    source.write_text('''import Cocoa
Thread.sleep(forTimeInterval: 1.0)
let app = NSApplication.shared
app.setActivationPolicy(.accessory)
app.run()
''')
    subprocess.run(["swiftc", str(source), "-framework", "Cocoa", "-o", str(executable)],
                   check=True, capture_output=True, timeout=30)
    host_type = importlib.import_module("loopx_codex_provider_routing.native_host").NativeHost
    host = object.__new__(host_type)
    host.home, host.spec = tmp_path, {}
    host.revision = lambda: "fixture-selection"
    host.apps = SimpleNamespace(
        app_bundle=lambda *_: bundle, executable=lambda _: executable,
        running=lambda *_: process.pid if process.poll() is None else None,
    )
    process = subprocess.Popen([str(executable)], start_new_session=True)
    try:
        assert host.close_client("fixture-selection", timeout=10) is True
        assert process.wait(timeout=5) == 0
    finally:
        # Only this test's Popen child may be cleaned up after a failed oracle.
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


@pytest.fixture
def native(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "packages" /
                                   "loopx-codex-provider-routing" / "src"))
    module = importlib.import_module("loopx_codex_provider_routing.native")
    monkeypatch.setattr(module, "discover", lambda: {"synthetic": True})
    return module


class Clock:
    now = 1000

    def __call__(self):
        return self.now


class NativeTransport:
    def __init__(self, clock):
        self.clock = clock
        self.active = "a"
        self.revision = "initial"
        self.calls = []
        self.busy = False
        self.pending = False
        self.bad_quota = False
        self.committed_without_reply = False
        self.entered = self.release = None

    def __call__(self, adapter, operation, **parameters):
        from loopx_codex_provider_routing.native_host import HostError

        self.calls.append((operation, parameters))
        if operation == "status":
            return {"slots": ["a", "b"], "active_slot": self.active, "selection_revision": self.revision,
                    "current_login_matches": True, "recovery_required": self.pending}
        if operation == "observe":
            if self.entered:
                self.entered.set()
                assert self.release.wait(10)
            assert parameters["expected_revision"] == self.revision
            return {"observations": [{"slot": slot, "status": "unavailable" if self.bad_quota else "ok",
                                      "observed_at": self.clock(), "usage": {
                                          "rate_limit": {"primary_window": {"used_percent": 100 if slot == "a" else 12,
                                                                              "reset_at": 8000}},
                                          "credits": {"has_credits": False},
                                      }} for slot in parameters["slots"]]}
        if operation == "switch":
            if parameters["expected_revision"] != self.revision:
                raise HostError("selection_changed")
            if self.busy:
                raise HostError("waiting_for_idle")
            self.active, self.revision = parameters["slot"], "selected"
            if self.committed_without_reply:
                raise HostError("native_unavailable")
            return self(adapter, "status") | {"launch_failed": False, "history_untouched": True}
        if operation in ("recover", "rollback"):
            assert parameters["expected_revision"] == self.revision
            self.pending = False
            return self(adapter, "status")
        if operation == "reopen":
            assert parameters["expected_revision"] == self.revision
            return self(adapter, "status") | {"client_running": True}
        raise AssertionError(operation)


def configured(native, tmp_path):
    clock = Clock()
    transport = NativeTransport(clock)
    provider = native.NativeAccounts(tmp_path, transport=transport, clock=clock)
    provider.configure(expected_revision=0, enabled=True, slots=["a", "b"])
    return provider, transport, clock


def test_default_off_has_no_native_effect_or_startup_observer(native, tmp_path):
    from loopx.presentation.codex_accounts_api import CodexAccountService

    assert not native.NativeAccounts(tmp_path).path.exists()
    service = CodexAccountService(tmp_path)
    assert service.watcher.thread is None
    assert not native.NativeAccounts(tmp_path).root.exists()
    service.close()


def test_exhaustion_selects_once_and_manual_retry_is_idempotent(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    result = provider.tick(manual_slot="b", expected_selection="initial", operation_id="once")
    assert result["active_slot"] == "b"
    assert result["action"] == "selected"
    assert provider.tick(manual_slot="b", expected_selection="initial", operation_id="once")["active_slot"] == "b"
    assert len([call for call in transport.calls if call[0] == "switch"]) == 1
    assert provider.path.stat().st_mode & 0o077 == 0
    assert "access_token" not in json.dumps(result)
    clock.now += 301
    assert provider.tick()["action"] == "ready"


def test_unknown_quota_is_not_exhaustion(native, tmp_path):
    provider, transport, _ = configured(native, tmp_path)
    transport.bad_quota = True
    assert provider.tick()["action"] == "quota_unknown"
    assert not [call for call in transport.calls if call[0] == "switch"]


def test_busy_waits_then_switches_at_safe_boundary(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    transport.busy = True
    result = provider.tick()
    assert result["action"] == "waiting_for_idle" and result["active_slot"] == "a"
    assert json.loads(provider.path.read_text())["intent"]["target"] == "b"
    transport.busy = False
    clock.now += 61
    result = provider.tick()
    assert result["active_slot"] == "b" and result["action"] == "selected"
    assert all(call[0] not in ("kill", "terminate") for call in transport.calls)


def test_manual_native_selection_wins_over_queued_automatic_choice(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    transport.busy = True
    provider.tick()
    transport.revision = "external-choice"
    clock.now += 61
    before = len(transport.calls)
    assert provider.tick()["action"] == "selection_changed"
    assert all(call[0] != "switch" for call in transport.calls[before:])
    result = provider.tick(manual_slot="b", expected_selection="initial", operation_id="stale")
    assert result["action"] == "selection_changed"
    assert transport.active == "a"


def test_lost_reply_recovers_readback_without_replaying_switch(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    transport.committed_without_reply = True
    assert provider.tick()["action"] == "unavailable"
    restarted = native.NativeAccounts(tmp_path, transport=transport, clock=clock)
    result = restarted.tick(refresh=True)
    assert result["active_slot"] == "b" and result["action"] == "selected"
    assert len([call for call in transport.calls if call[0] == "switch"]) == 1


def test_recovery_stops_auto_and_preserves_revision_fence(native, tmp_path):
    provider, transport, _ = configured(native, tmp_path)
    transport.pending = True
    assert provider.tick()["action"] == "recovery_required"
    result = provider.recover(rollback=False, expected_revision=1, expected_selection="initial")
    assert result["policy"]["enabled"] is False
    assert not result["recovery_required"]
    with pytest.raises(Exception, match="configuration_changed"):
        provider.configure(expected_revision=1, enabled=True, slots=["a", "b"])


def test_two_hosts_cannot_switch_concurrently(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    transport.entered, transport.release = threading.Event(), threading.Event()
    outcome = []
    thread = threading.Thread(target=lambda: outcome.append(provider.tick()))
    thread.start()
    assert transport.entered.wait(5)
    contender = native.NativeAccounts(tmp_path, transport=transport, clock=clock)
    try:
        with pytest.raises(BlockingIOError):
            contender.tick()
    finally:
        transport.release.set()
        thread.join(10)
    assert outcome[0]["active_slot"] == "b"
    assert len([call for call in transport.calls if call[0] == "switch"]) == 1


def test_disable_and_stop_before_effect_preserve_login(native, tmp_path):
    provider, transport, _ = configured(native, tmp_path)
    provider.switch_allowed = lambda: False
    provider.tick()
    assert transport.active == "a"
    provider.configure(expected_revision=1, enabled=False, slots=["a", "b"])
    before = len(transport.calls)
    assert provider.tick()["action"] == "disabled"
    assert len(transport.calls) == before
    # Turning automatic effects off does not revoke an explicit manual choice.
    result = provider.tick(manual_slot="b", expected_selection="initial", operation_id="manual-after-disable")
    assert result["active_slot"] == "b" and result["policy"]["enabled"] is False


def test_first_explicit_check_does_not_enable_automatic_switches(native, tmp_path):
    clock = Clock()
    transport = NativeTransport(clock)
    provider = native.NativeAccounts(tmp_path, transport=transport, clock=clock)
    result = provider.tick(refresh=True)
    assert result["policy"]["enabled"] is False and result["accounts"]
    assert not [call for call in transport.calls if call[0] == "switch"]


def test_unknown_upstream_fields_do_not_reach_policy_or_persistence(native):
    from loopx_codex_provider_routing.native_host import quota_fields

    result = quota_fields({"access_token": "synthetic-only", "email": "person@example.invalid",
                           "rate_limit": {"primary_window": {"used_percent": 10, "secret": "synthetic-only"}},
                           "credits": {"has_credits": False, "refresh_token": "synthetic-only"}})
    assert "synthetic-only" not in json.dumps(result)
    assert result["rate_limit"]["primary_window"]["used_percent"] == 10


def test_rejected_inactive_login_is_not_repeatedly_refreshed(native, tmp_path, monkeypatch):
    from loopx_codex_provider_routing import native_host as module

    active, inactive = tmp_path / "active", tmp_path / "inactive"
    active.write_bytes(b"synthetic-active")
    inactive.write_bytes(b"synthetic-expired")
    adapter = module.NativeHost.__new__(module.NativeHost)
    adapter.manager = SimpleNamespace(auth=active, slot=lambda _: inactive)
    adapter.accounts = SimpleNamespace(read_private=lambda path: path.read_bytes())
    adapter.status = lambda: {"slots": ["a", "b"], "active_slot": "a",
                              "selection_revision": "stable", "recovery_required": False}
    calls = []

    def usage(raw):
        if raw == b"synthetic-active":
            return {}
        raise module.HostError("auth_required")

    def refresh(*args):
        calls.append(args)
        raise module.HostError("auth_required")

    monkeypatch.setattr(module, "read_usage", usage)
    adapter.refresh_inactive = refresh
    result = adapter.observe(["a", "b"], "stable")
    failed = result["observations"][1]
    cached = {"b": {"revision": failed["credential_revision"], "observed_at": failed["observed_at"]}}
    assert adapter.observe(["a", "b"], "stable", cached)["observations"][1] == failed
    assert len(calls) == 1
    inactive.write_bytes(b"synthetic-replaced-login")
    adapter.observe(["a", "b"], "stable", cached)
    assert len(calls) == 2


def test_source_pins_and_symlink_state_are_not_silently_adopted(native, tmp_path):
    from loopx_codex_provider_routing.native_host import NativeHost, HostError

    (tmp_path / "personal_apps.py").write_text("# synthetic owner\n")
    with pytest.raises(HostError, match="adapter_changed"):
        NativeHost({"source": str(tmp_path), "pins": {"personal_apps.py": "bad"}, "codex": "unused"})
    provider = native.NativeAccounts(tmp_path)
    provider.root.mkdir(parents=True)
    provider.path.symlink_to(tmp_path / "personal_apps.py")
    with pytest.raises(HostError, match="state_untrusted"):
        provider._load()


def test_http_adapter_rejects_secret_and_arbitrary_command_fields(native, tmp_path):
    from loopx.presentation.codex_accounts_api import CodexAccountsRequestMixin

    class Handler(CodexAccountsRequestMixin):
        def _read_json(self):
            return {"operation": "configure", "parameters": {"access_token": "synthetic-only"}}

        def _send_json(self, payload, *, status=200):
            self.sent, self.code = payload, status

    handler = Handler()
    handler._codex_accounts_write()
    assert handler.code == 409 and handler.sent["code"] == "invalid_request"
    assert "synthetic-only" not in json.dumps(handler.sent)


def test_client_restart_requires_explicit_opt_in_and_old_policy_stays_off(native, tmp_path):
    provider, transport, _ = configured(native, tmp_path)
    assert provider.tick()["action"] == "selected"
    first = next(args for op, args in transport.calls if op == "switch")
    assert first["restart_clients"] is False and "handoff_receipt" not in first
    assert first["not_after"] == 1120
    # Older saved policies without this new permission remain wait-only.
    state = provider._load()
    state["policy"].pop("restart_clients")
    native.atomic(provider.path, state)
    assert provider.configure(expected_revision=1, enabled=True, slots=["a", "b"])["policy"]["restart_clients"] is False
    with pytest.raises(Exception, match="invalid_policy"):
        provider.configure(expected_revision=2, enabled=True, slots=["a", "b"], restart_clients="yes")


def test_opted_in_handoff_has_private_receipt_and_no_replayed_switch(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    provider.configure(expected_revision=1, enabled=True, slots=["a", "b"], restart_clients=True)
    original = transport.__call__
    effect = {}

    def pending(adapter, operation, **parameters):
        if operation == "switch":
            effect.update(parameters)
            raise native.HostError("handoff_pending")
        return original(adapter, operation, **parameters)

    provider.transport = pending
    assert provider.tick()["action"] == "handoff_pending"
    assert effect["restart_clients"] is True
    assert Path(effect["handoff_receipt"]).parent.stat().st_mode & 0o077 == 0
    clock.now += 61
    assert provider.tick()["action"] == "restarting"
    # Only the completed worker receipt can reconcile the pending operation.
    transport.active, transport.revision = "b", "selected"
    native.atomic(Path(effect["handoff_receipt"]), {"schema_version": native.SCHEMA,
                  "ok": True, "result": {"launch_failed": True}})
    result = provider.tick(refresh=True)
    assert result["action"] == "selected" and result["launch_failed"]
    before = len(transport.calls)
    provider.transport = transport
    assert provider.reopen(expected_selection="selected")["launch_failed"] is False
    assert [op for op, _ in transport.calls[before:]] == ["reopen", "status"]


def test_unresolved_worker_never_replays_or_reconfigures_an_enabled_handoff(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    provider.configure(expected_revision=1, enabled=True, slots=["a", "b"], restart_clients=True)
    original = transport.__call__
    switches = []

    def pending(adapter, operation, **parameters):
        if operation == "switch":
            switches.append(parameters)
            raise native.HostError("handoff_pending")
        return original(adapter, operation, **parameters)

    provider.transport = pending
    provider.tick()
    clock.now += 181
    assert provider.tick(refresh=True)["recovery_required"]
    assert len(switches) == 1
    with pytest.raises(Exception, match="handoff_pending"):
        provider.configure(expected_revision=2, enabled=True, slots=["a", "b"], reconnect=True)
    # Disabling cannot discard a worker that may be committing native state.
    provider.configure(expected_revision=2, enabled=False, slots=["a", "b"])
    assert provider._load()["intent"]["handoff_receipt"] == switches[0]["handoff_receipt"]
    assert provider.tick(refresh=True)["action"] == "handoff_interrupted"
    with pytest.raises(Exception, match="handoff_pending"):
        provider.recover(rollback=False, expected_revision=3, expected_selection="initial")
    # Recovery can retire a proven exited worker without inventing a native
    # credential rollback when the worker never began a credential journal.
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait(timeout=5)
    marker = Path(switches[0]["handoff_receipt"]).with_suffix(".pid.json")
    native.atomic(marker, {"schema_version": native.SCHEMA, "pid": child.pid})
    result = provider.recover(rollback=False, expected_revision=3, expected_selection="initial")
    assert result["policy"]["enabled"] is False and provider._load()["intent"] is None


def test_refused_client_is_not_repeatedly_closed_in_the_background(native, tmp_path):
    provider, transport, clock = configured(native, tmp_path)
    provider.configure(expected_revision=1, enabled=True, slots=["a", "b"], restart_clients=True)
    calls = []

    def refused(adapter, operation, **parameters):
        if operation == "switch":
            calls.append(parameters)
            raise native.HostError("client_quit_refused")
        return transport(adapter, operation, **parameters)

    provider.transport = refused
    assert provider.tick()["action"] == "client_quit_refused"
    clock.now += 1000
    provider.tick()
    assert len(calls) == 1 and transport.active == "a"
    provider.tick(refresh=True)
    assert len(calls) == 2  # A deliberate check authorizes one new attempt.


def test_disable_waits_for_owned_transaction_then_persists_off(native, tmp_path):
    provider, transport, _ = configured(native, tmp_path)
    transport.entered, transport.release = threading.Event(), threading.Event()
    active = threading.Thread(target=provider.tick)
    active.start()
    assert transport.entered.wait(5)
    result, errors = [], []

    def disable():
        try:
            result.append(provider.configure(expected_revision=1, enabled=False, slots=["a", "b"]))
        except Exception as error:
            errors.append(error)

    request = threading.Thread(target=disable)
    request.start()
    assert request.is_alive()
    transport.release.set()
    active.join(10)
    request.join(10)
    assert not errors and result[0]["policy"]["enabled"] is False
    before = len(transport.calls)
    assert provider.tick()["action"] == "disabled" and len(transport.calls) == before


def client_host(native, monkeypatch):
    from loopx_codex_provider_routing import native_host as module

    monkeypatch.setattr(module.sys, "platform", "darwin")
    adapter = module.NativeHost.__new__(module.NativeHost)
    state = {"pid": 11, "active": "a", "revision": "initial", "quit": [], "switch": 0, "open": 0}
    adapter.home, adapter.spec = Path("/synthetic"), {}
    adapter.revision = lambda: state["revision"]
    adapter.status = lambda: {"active_slot": state["active"], "selection_revision": state["revision"],
                              "current_login_matches": True, "recovery_required": False,
                              "client_running": state["pid"] is not None}

    def idle():
        if state["pid"] is not None:
            raise module.HostError("waiting_for_idle")
        if adapter.revision() != adapter.expected:
            raise module.HostError("selection_changed")

    def switch(slot):
        idle()
        state.update(active=slot, revision="selected", switch=state["switch"] + 1)
        return {"history_untouched": True}

    def reopen(*_):
        state.update(pid=22, open=state["open"] + 1)

    def quit(command, **parameters):
        state["quit"].append(command)
        assert "app.terminate" in parameters["input"] and "forceTerminate" not in parameters["input"]
        assert command[-2:] == ["11", "/synthetic/GPT.app/Contents/MacOS/GPT"]
        state["pid"] = None

    adapter.manager = SimpleNamespace(switch=switch, idle=idle)
    adapter.apps = SimpleNamespace(executable=lambda _: Path("/synthetic/GPT.app/Contents/MacOS/GPT"),
                                   app_bundle=lambda *_: None, running=lambda *_: state["pid"], open_app=reopen)
    monkeypatch.setattr(module.subprocess, "run", quit)
    return adapter, state, module


def test_normal_quit_precedes_native_idle_transaction_and_same_route_reopen(native, monkeypatch):
    adapter, state, _ = client_host(native, monkeypatch)
    result = adapter.switch("b", "initial", reopen=True, restart_clients=True)
    assert state["quit"] and state["switch"] == 1 and state["open"] == 1
    assert result["active_slot"] == "b" and result["client_running"] is True


def test_wait_only_mode_does_not_quit_a_client(native, monkeypatch):
    adapter, state, _ = client_host(native, monkeypatch)
    with pytest.raises(Exception, match="waiting_for_idle"):
        adapter.switch("b", "initial", reopen=True)
    assert not state["quit"] and state["active"] == "a"


def test_quit_refusal_and_timeout_do_not_mutate_credentials(native, monkeypatch):
    adapter, state, module = client_host(native, monkeypatch)

    def refuse(*_, **__):
        raise module.subprocess.CalledProcessError(1, "synthetic")

    monkeypatch.setattr(module.subprocess, "run", refuse)
    with pytest.raises(Exception, match="client_quit_refused"):
        adapter.switch("b", "initial", reopen=True, restart_clients=True)
    assert state["switch"] == 0 and state["open"] == 0
    monkeypatch.setattr(module.subprocess, "run", lambda *_, **__: None)
    with pytest.raises(Exception, match="client_quit_timeout"):
        adapter.close_client("initial", timeout=0)
    assert state["active"] == "a"


def test_manual_selection_and_new_client_prevent_closing_the_wrong_process(native, monkeypatch):
    adapter, state, _ = client_host(native, monkeypatch)
    with pytest.raises(Exception, match="selection_changed"):
        adapter.switch("b", "stale", reopen=True, restart_clients=True)
    assert not state["quit"]
    owners = iter([11, 33])
    adapter.apps.running = lambda *_: next(owners)
    with pytest.raises(Exception, match="client_changed"):
        adapter.switch("b", "initial", reopen=True, restart_clients=True)
    assert not state["quit"] and state["switch"] == 0 and state["open"] == 0


def test_native_switch_failure_reopens_source_without_replaying_account_selection(native, monkeypatch):
    adapter, state, module = client_host(native, monkeypatch)
    adapter.manager.switch = lambda _: (_ for _ in ()).throw(module.HostError("readback_failed"))
    with pytest.raises(Exception, match="readback_failed"):
        adapter.switch("b", "initial", reopen=True, restart_clients=True)
    assert state["active"] == "a" and state["open"] == 1


def test_manual_selection_during_quit_wins_before_any_credential_write(native, monkeypatch):
    adapter, state, module = client_host(native, monkeypatch)
    quit = module.subprocess.run

    def manual_choice(*args, **kwargs):
        quit(*args, **kwargs)
        state.update(active="c", revision="manual")

    monkeypatch.setattr(module.subprocess, "run", manual_choice)
    with pytest.raises(Exception, match="selection_changed"):
        adapter.switch("b", "initial", reopen=True, restart_clients=True)
    assert state["active"] == "c" and state["switch"] == 0 and state["open"] == 0


def test_expired_automatic_evidence_does_not_even_quit_the_client(native, monkeypatch):
    adapter, state, _ = client_host(native, monkeypatch)
    with pytest.raises(Exception, match="quota_stale"):
        adapter.switch("b", "initial", reopen=True, restart_clients=True, not_after=time.time() - 1)
    assert not state["quit"] and not state["switch"]
