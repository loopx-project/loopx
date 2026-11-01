"""Real observer lifecycle and private files; no native credentials or effects."""
import importlib
import json
import os
from pathlib import Path
import time
import threading

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Native observer ownership uses POSIX locks")


@pytest.fixture
def service(monkeypatch, tmp_path):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] /
                                   "packages/loopx-codex-provider-routing/src"))
    native = importlib.import_module("loopx_codex_provider_routing.native")
    monkeypatch.setattr(native, "discover", lambda: {"synthetic": True})

    def transport(_adapter, operation, **parameters):
        if operation == "status":
            return {"slots": ["a"], "active_slot": "a", "selection_revision": "synthetic",
                    "current_login_matches": True, "recovery_required": False}
        if operation == "observe":
            return {"observations": [{"slot": slot, "status": "ok", "observed_at": time.time(),
                                      "usage": {"rate_limit": {"primary_window": {"used_percent": 20,
                                                                                 "reset_at": time.time() + 3600}},
                                                "credits": {"has_credits": False}}}
                                     for slot in parameters["slots"]]}
        raise AssertionError("No native effects are authorized: " + operation)

    provider = native.NativeAccounts(tmp_path, transport=transport)
    provider.configure(expected_revision=0, enabled=True, slots=["a"])
    monkeypatch.setattr(native, "NativeAccounts", lambda _root: provider)
    from loopx.presentation.codex_accounts_api import CodexAccountService
    owner = CodexAccountService(tmp_path)
    try:
        yield owner, provider, native
    finally:
        owner.close()


def test_failed_disable_restores_the_persisted_enabled_observer(service):
    owner, provider, _ = service
    result = owner.call("configure", {"expected_revision": 0, "enabled": False, "slots": ["a"]})
    assert result["ok"] is False and result["code"] == "configuration_changed"
    assert provider._load()["policy"]["enabled"] is True
    assert owner.watcher.stop.is_set() is False, "A refused edit cannot silently stop authorized observation"


def test_status_recovers_a_stopped_observer_without_a_policy_write(service):
    owner, provider, _ = service
    original = owner.watcher
    original.close()
    revision = provider._load()["revision"]
    result = owner.call("status", {})
    assert result["ok"] is True
    assert owner.watcher.stop.is_set() is False
    assert owner.watcher.thread.is_alive()
    assert provider._load()["revision"] == revision
    assert provider._load()["policy"]["slots"] == ["a"]


def test_service_shutdown_does_not_reactivate_the_observer(service):
    owner, _, _ = service
    owner.close()
    owner.call("status", {})
    assert owner.watcher.stop.is_set()


def test_health_requires_a_live_lock_and_does_not_reuse_a_stale_success(service):
    owner, provider, native = service
    deadline = time.monotonic() + 5
    while provider.observer_health(True)["state"] != "healthy" and time.monotonic() < deadline:
        time.sleep(.02)
    assert provider.observer_health(True)["state"] == "healthy"
    marker = provider.root / "observer.json"
    assert marker.stat().st_mode & 0o777 == 0o600
    original = marker.read_bytes()
    owner.close()
    assert provider.observer_health(True)["state"] == "stopped"
    assert marker.read_bytes() == original, "A health read must not refresh its own heartbeat"
    with provider._lock(name="observer.lock"):
        native.atomic(marker, {"schema_version": native.OBSERVER_SCHEMA, "build": native.OBSERVER_BUILD,
                              "checked_at": provider.clock() - 100, "deadline_at": provider.clock() - 1,
                              "phase": "waiting"})
        assert provider.observer_health(True)["state"] == "overdue"
        native.atomic(marker, {"schema_version": native.OBSERVER_SCHEMA, "build": "old-build",
                              "checked_at": provider.clock(), "deadline_at": provider.clock() + 75,
                              "phase": "waiting"})
        assert provider.observer_health(True)["state"] == "upgrade_required"
        marker.unlink()
        assert provider.observer_health(True)["state"] == "unverified"
        marker.write_text('["private synthetic marker detail"]')
        marker.chmod(0o600)
        assert provider.observer_health(True)["state"] == "unavailable"
        assert "private synthetic marker detail" not in json.dumps(provider.observer_health(True))


def test_unexpected_tick_failure_retries_and_progresses_without_leaking_details(service, monkeypatch):
    owner, provider, native = service
    owner.watcher.close()
    watcher = native.Watcher(provider)
    owner.watcher = watcher
    original_tick, original_atomic, original_wait = provider.tick, native.atomic, watcher.stop.wait
    phases, calls = [], []

    def tick():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("private synthetic provider detail")
        return original_tick()

    def write(path, value):
        if path.name == "observer.json":
            phases.append(value["phase"])
        return original_atomic(path, value)

    monkeypatch.setattr(provider, "tick", tick)
    monkeypatch.setattr(native, "atomic", write)
    # Shorten only the synthetic observer's waits, preserving its real loop,
    # lock, heartbeat writer, policy and retry path.
    monkeypatch.setattr(watcher.stop, "wait", lambda seconds: original_wait(min(seconds, .01)))
    watcher.start()
    deadline = time.monotonic() + 5
    while "waiting" not in phases and time.monotonic() < deadline:
        time.sleep(.02)
    watcher.close()
    assert "retrying" in phases and "waiting" in phases and len(calls) >= 2
    assert "private synthetic provider detail" not in (provider.root / "observer.json").read_text()
    assert json.loads((provider.root / "observer.json").read_text())["build"] == native.OBSERVER_BUILD


def test_start_replaces_a_dead_thread_but_preserves_explicit_stop(service):
    owner, provider, native = service
    owner.watcher.close()
    owner.watcher = native.Watcher(provider)
    dead = threading.Thread(target=lambda: None)
    dead.start()
    dead.join()
    owner.watcher.thread = dead
    owner.watcher.start()
    assert owner.watcher.thread is not dead and owner.watcher.thread.is_alive()
    owner.watcher.close()
    stopped = owner.watcher.thread
    owner.watcher.start()
    assert owner.watcher.thread is stopped and owner.watcher.stop.is_set()


def test_service_restart_rehydrates_health_with_the_same_saved_permissions(service):
    original, provider, _ = service
    revision, policy = provider._load()["revision"], provider._load()["policy"]
    original.close()
    from loopx.presentation.codex_accounts_api import CodexAccountService
    restarted = CodexAccountService(provider.root.parents[1])
    try:
        deadline = time.monotonic() + 5
        while provider.observer_health(True)["state"] != "healthy" and time.monotonic() < deadline:
            time.sleep(.02)
        assert restarted.watcher.thread.is_alive()
        assert provider.observer_health(True)["state"] == "healthy"
        assert provider._load()["revision"] == revision and provider._load()["policy"] == policy
    finally:
        restarted.close()
