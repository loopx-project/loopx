"""Service alignment preserves the real Chat admission boundary and manager route."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import socket
import time
from types import SimpleNamespace
import urllib.request
import uuid

import pytest

from loopx import runtime_activation as activation
from loopx.hosts import managed_service_runtime as managed_runtime
from loopx.hosts.managed_service_runtime import _retarget, align_managed_runtime
from loopx.chat_store import ChatSessionStore


@pytest.fixture
def managed(tmp_path, monkeypatch):
    if sys.platform == "win32":
        pytest.skip("macOS launchd fixture executes POSIX launchers")
    monkeypatch.setattr("loopx.hosts.managed_service_runtime._verify_services", lambda *_: None)
    monkeypatch.setattr(Path, "home", staticmethod(lambda: tmp_path))
    root = tmp_path / "original data"
    agents = tmp_path / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    target = tmp_path / "new runtime's $literal" / "loopx"
    target.parent.mkdir()
    identity = {"schema_version": "loopx_runtime_identity_v1", "package_version": "2.2.0",
        "release_id": "new", "source_revision": "new-revision"}
    doctor = {"ok": True, "scope": "installation_only", "service_runtime_identity": identity,
        "python": {"executable": sys.executable}}
    target.write_text(f"#!{sys.executable}\nimport json,os,sys\n"
        f"print({json.dumps(json.dumps(doctor))} if 'doctor' in sys.argv else json.dumps({{'args':sys.argv[1:],'home':os.getenv('CODEX_HOME'),'python':os.getenv('LOOPX_PYTHON')}}))\n")
    target.chmod(0o700)
    loaded, calls = {}, []
    for kind in ("status", "chat"):
        label = f"com.loopx.{kind}"
        args = ["/old/bin/loopx", "--registry", str(tmp_path / "registry"),
            "--runtime-root", str(root), "chat" if kind == "chat" else "serve-status",
            "--host", "127.0.0.1", "--port", "8767" if kind == "chat" else "8766"]
        shell = "export CODEX_HOME='/original account'; export LOOPX_PYTHON='/old/python'; exec " + shlex.join(args)
        payload = {"Label": label, "ProgramArguments": ["/bin/sh", "-c", shell],
            "KeepAlive": True, "ThrottleInterval": 10, "StandardErrorPath": str(tmp_path / f"{kind}.err")}
        (agents / f"{label}.plist").write_bytes(plistlib.dumps(payload))
        loaded[label] = payload
    original_run = subprocess.run
    fail = []

    def launchctl(args, **kwargs):
        if args[0] != "launchctl":
            return original_run(args, **kwargs)
        verb = args[1]
        label = Path(args[-1]).stem if verb == "bootstrap" else args[-1].split("/")[-1]
        if verb == "print":
            text = "arguments = {\n" + "\n".join(loaded[label]["ProgramArguments"]) + "\n}\npid = 900\n" if label in loaded else ""
            return subprocess.CompletedProcess(args, 0 if label in loaded else 113, text, "")
        calls.append((verb, label))
        if fail and (verb, label) == fail[0]:
            fail.pop(0)
            return subprocess.CompletedProcess(args, 1, b"", b"fixture failure")
        if verb == "bootout":
            loaded.pop(label)
        elif verb == "bootstrap":
            loaded[label] = plistlib.loads(Path(args[-1]).read_bytes())
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(activation.subprocess, "run", launchctl)
    return root, agents, target, loaded, calls, fail


def test_pair_adopts_target_without_changing_routes_or_cli_and_is_idempotent(managed):
    root, agents, target, loaded, calls, _ = managed
    before = {p.name: plistlib.loads(p.read_bytes()) for p in agents.glob("*.plist")}
    result = align_managed_runtime(str(target))
    assert result["status"] == "aligned"
    assert calls == [("bootout", "com.loopx.status"), ("bootout", "com.loopx.chat"),
        ("bootstrap", "com.loopx.status"), ("bootstrap", "com.loopx.chat")]
    for name, payload in loaded.items():
        assert payload["LoopXRuntimeBinding"]["schema_version"] == "loopx_managed_runtime_binding_v1"
        assert {k: v for k, v in payload.items() if k not in {"ProgramArguments", "LoopXRuntimeBinding"}} == {
            k: v for k, v in before[f"{name}.plist"].items() if k != "ProgramArguments"}
        observed = json.loads(subprocess.check_output(payload["ProgramArguments"], text=True))
        assert observed["home"] == "/original account"
        assert observed["python"] == sys.executable
        assert observed["args"][observed["args"].index("--runtime-root") + 1] == str(root)
    calls.clear()
    assert align_managed_runtime(str(target))["restarted_services"] == []
    assert calls == []


@pytest.fixture
def managed_with_observer(managed, monkeypatch):
    root, agents, target, loaded, calls, fail = managed
    # Chat's message store and the shared native account owner are different.
    account = agents.parent.parent / "shared native accounts"
    (account / "extensions/codex-native-accounts").mkdir(parents=True)
    (agents.parent.parent / "registry").write_text(json.dumps({
        "goals": [], "common_runtime_root": str(account),
    }))
    core = target.parent
    (core / "scripts").mkdir()
    target = target.rename(core / "scripts/loopx")
    (core / "loopx/extensions").mkdir(parents=True)
    (core / "loopx/extensions/chat_service_guard.py").touch()
    label = "com.loopx.codex-native-accounts"
    path = agents / f"{label}.plist"
    payload = {"Label": label, "ProgramArguments": ["/old/python", "-m",
        "loopx_codex_provider_routing.native", "watch", "--runtime-root", str(account)],
        "KeepAlive": True, "EnvironmentVariables": {"PATH": "/original/bin", "PYTHONPATH": "/old/core",
            "PYTHONHOME": "/old/python/home"}}
    path.write_bytes(plistlib.dumps(payload))
    loaded[label] = payload
    state = {"intent": None}
    held = set()

    class Owner:
        def __init__(self, runtime_root):
            assert runtime_root == account
            self.root = runtime_root / "extensions/codex-native-accounts"

        @contextmanager
        def _lock(self, *, name="lock"):
            assert name not in held
            held.add(name)
            try:
                yield
            finally:
                held.remove(name)

        def _load(self):
            assert held == {"lock", "api-relay-state.lock"}
            return state

    def observer_heartbeat(*_, **__):
        now = time.time()
        candidate = loaded[label]["ProgramArguments"][0] == sys.executable
        return {"build": "candidate" if candidate else "old",
            "relay_build": "candidate-relay" if candidate else "old-relay",
            "checked_at": now, "deadline_at": now + 60, "phase": "retrying"}

    native = SimpleNamespace(NativeAccounts=Owner, HostError=RuntimeError,
        OBSERVER_SCHEMA="observer-fixture", private_read=observer_heartbeat,
        observer_build_identity=lambda: {"build": "candidate", "relay_build": "candidate-relay"})
    monkeypatch.setitem(sys.modules, "loopx_codex_provider_routing.native", native)
    monkeypatch.setattr(managed_runtime, "_provider_pinned", lambda _: True)
    monkeypatch.setattr(managed_runtime, "_native_package", lambda _: native)
    monkeypatch.setattr(managed_runtime, "_observer_lock_pid", lambda _: 900 if label in loaded else None)
    return agents, target, loaded, calls, fail, state, held, path


def test_provider_bound_core_adopts_original_observer_and_keeps_its_route(managed_with_observer):
    _, target, loaded, calls, _, _, held, path = managed_with_observer
    original = plistlib.loads(path.read_bytes())
    result = align_managed_runtime(str(target))
    assert result["readiness_verified"]
    assert calls == [("bootout", "com.loopx.status"), ("bootout", "com.loopx.chat"),
        ("bootout", path.stem), ("bootstrap", path.stem),
        ("bootstrap", "com.loopx.status"), ("bootstrap", "com.loopx.chat")]
    observer = loaded[path.stem]
    assert observer["ProgramArguments"] == [sys.executable, *original["ProgramArguments"][1:]]
    env = observer["EnvironmentVariables"]
    assert env["PYTHONPATH"] == env["LOOPX_RELEASE_ROOT"] == str(target.parent.parent)
    assert env["PATH"] == "/original/bin" and "PYTHONHOME" not in env
    assert not held
    calls.clear()
    assert align_managed_runtime(str(target))["restarted_services"] == []
    assert calls == []


@pytest.mark.parametrize("blocker", ["active_intent", "foreign_owner"])
def test_native_observer_hold_leaves_all_services_running(managed_with_observer, monkeypatch, blocker):
    agents, target, loaded, calls, _, state, held, path = managed_with_observer
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    if blocker == "active_intent":
        state["intent"] = {"target": "fixture"}
        expected = activation.RuntimeActivationDeferred
        reason = "native_account_work_pending"
    else:
        monkeypatch.setattr(managed_runtime, "_observer_lock_pid", lambda _: 901)
        expected = ValueError
        reason = "native_observer_owner_unverified"
    with pytest.raises(expected, match=reason):
        align_managed_runtime(str(target))
    assert set(loaded) == {"com.loopx.status", "com.loopx.chat", path.stem}
    assert not calls and not held
    assert {p: p.read_bytes() for p in before} == before


def test_core_only_activation_does_not_retarget_standalone_observer(managed_with_observer, monkeypatch):
    _, target, _, calls, _, _, _, path = managed_with_observer
    original = path.read_bytes()
    monkeypatch.setattr(managed_runtime, "_provider_pinned", lambda _: False)
    align_managed_runtime(str(target))
    assert path.read_bytes() == original
    assert all(label != path.stem for _, label in calls)


def test_provider_activation_leaves_another_account_root_alone(managed_with_observer):
    agents, target, _, calls, _, _, _, path = managed_with_observer
    registry = agents.parent.parent / "registry"
    registry.write_text(json.dumps({"goals": [], "common_runtime_root": str(agents / "other owner")}))
    original = path.read_bytes()
    align_managed_runtime(str(target))
    assert path.read_bytes() == original
    assert all(label != path.stem for _, label in calls)


def test_handoff_reads_relay_build_even_when_observer_build_matches(managed_with_observer, monkeypatch):
    _, target, _, _, _, _, _, _ = managed_with_observer
    native = sys.modules["loopx_codex_provider_routing.native"]
    read_heartbeat = native.private_read
    builds = []

    def readback(*args, **kwargs):
        value = read_heartbeat(*args, **kwargs)
        assert value["build"] == "candidate"
        if not builds:
            value["relay_build"] = "old-relay"
        builds.append(value["relay_build"])
        return value

    monkeypatch.setattr(native, "private_read", readback)
    assert align_managed_runtime(str(target))["readiness_verified"]
    assert builds[0] == "old-relay" and builds[1:] and set(builds[1:]) == {"candidate-relay"}


def test_failed_chat_readiness_rolls_back_observer_and_core(managed_with_observer, monkeypatch):
    agents, target, loaded, calls, _, _, held, path = managed_with_observer
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    def unavailable(*_):
        raise RuntimeError("service_runtime_alignment_failed")
    monkeypatch.setattr(managed_runtime, "_verify_services", unavailable)
    with pytest.raises(RuntimeError, match="service_runtime_alignment_failed"):
        align_managed_runtime(str(target))
    assert {p: p.read_bytes() for p in before} == before
    assert loaded == {p.stem: plistlib.loads(content) for p, content in before.items()}
    assert calls[-3:] == [("bootstrap", path.stem), ("bootstrap", "com.loopx.status"),
        ("bootstrap", "com.loopx.chat")]
    assert not held and not (agents / ".loopx-runtime-activation.json").exists()


def test_interrupted_observer_handoff_restores_original_routes(managed_with_observer, monkeypatch):
    agents, target, loaded, calls, _, _, held, path = managed_with_observer
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    write = managed_runtime.write_bytes_atomic
    def crash(plist, content, **kwargs):
        write(plist, content, **kwargs)
        if plist == path:
            raise KeyboardInterrupt("fixture process interruption")
    monkeypatch.setattr(managed_runtime, "write_bytes_atomic", crash)
    with pytest.raises(KeyboardInterrupt):
        align_managed_runtime(str(target))
    journal = agents / ".loopx-runtime-activation.json"
    assert journal.exists() and not loaded and not held
    monkeypatch.setattr(managed_runtime, "write_bytes_atomic", write)
    managed_runtime._recover_alignment(journal)
    assert {p: p.read_bytes() for p in before} == before
    assert loaded == {p.stem: plistlib.loads(content) for p, content in before.items()}
    assert calls[-3:] == [("bootstrap", path.stem), ("bootstrap", "com.loopx.status"),
        ("bootstrap", "com.loopx.chat")]
    assert not held and not journal.exists()


def test_mutable_cli_alias_cannot_hide_a_new_runtime_from_the_pair(managed):
    _, agents, target, loaded, calls, _ = managed
    alias = target.parent.parent / "installed alias" / "loopx"
    alias.parent.mkdir()
    alias.symlink_to(target)
    for path in agents.glob("*.plist"):
        payload = _retarget(plistlib.loads(path.read_bytes()), str(alias), sys.executable)
        path.write_bytes(plistlib.dumps(payload))
        loaded[path.stem] = payload
    original_routes = {label: managed_runtime._launcher(payload)[0][1:] for label, payload in loaded.items()}

    result = align_managed_runtime(str(alias))

    assert result["readiness_verified"] is True
    assert calls == [("bootout", "com.loopx.status"), ("bootout", "com.loopx.chat"),
        ("bootstrap", "com.loopx.status"), ("bootstrap", "com.loopx.chat")]
    for label, payload in loaded.items():
        executable, *args = managed_runtime._launcher(payload)[0]
        assert executable == str(target.resolve())
        assert args == original_routes[label]
    assert alias.is_symlink() and alias.resolve() == target.resolve()
    calls.clear()
    assert align_managed_runtime(str(alias))["restarted_services"] == []
    assert calls == []


def test_relative_alias_does_not_gain_activation_authority(managed, monkeypatch):
    _, agents, target, loaded, calls, _ = managed
    alias = target.parent.parent / "relative-alias"
    alias.symlink_to(target)
    before = {path: path.read_bytes() for path in agents.glob("*.plist")}
    monkeypatch.chdir(alias.parent)
    with pytest.raises(ValueError, match="service_runtime_unverified"):
        align_managed_runtime(alias.name)
    assert not calls and len(loaded) == 2
    assert {path: path.read_bytes() for path in before} == before


@pytest.mark.parametrize("rollback", [False, True])
def test_async_bootout_finishes_before_replacement_or_rollback(managed, monkeypatch, rollback):
    _, agents, target, loaded, _, _ = managed
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    run = subprocess.run
    unloading = {}
    observations = []

    def launchctl(args, **kwargs):
        if args[0] != "launchctl":
            return run(args, **kwargs)
        verb = args[1]
        label = Path(args[-1]).stem if verb == "bootstrap" else args[-1].split("/")[-1]
        if verb == "bootout":
            payload = loaded[label]
            result = run(args, **kwargs)
            loaded[label] = payload
            unloading[label] = 2
            return result
        if verb == "print" and label in unloading:
            observations.append(label)
            if unloading[label]:
                unloading[label] -= 1
            else:
                unloading.pop(label)
                loaded.pop(label)
        if verb == "bootstrap" and label in loaded:
            return subprocess.CompletedProcess(args, 5, b"", b"job still unloading")
        return run(args, **kwargs)

    monkeypatch.setattr(activation.subprocess, "run", launchctl)
    if rollback:
        def unavailable(*_):
            raise RuntimeError("service_runtime_alignment_failed")
        monkeypatch.setattr(managed_runtime, "_verify_services", unavailable)
        with pytest.raises(RuntimeError, match="service_runtime_alignment_failed"):
            align_managed_runtime(str(target))
        assert {p: p.read_bytes() for p in before} == before
        assert loaded == {p.stem: plistlib.loads(content) for p, content in before.items()}
    else:
        assert align_managed_runtime(str(target))["status"] == "aligned"
    assert len(observations) == (12 if rollback else 6)
    assert not unloading
    assert not (agents / ".loopx-runtime-activation.json").exists()


@pytest.mark.parametrize("returncode", [1, 0])
def test_unload_requires_proved_absence_and_has_a_deadline(monkeypatch, returncode):
    monkeypatch.setattr(activation.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a, returncode, b"", b"unproved"))
    clock = iter([0, 10])
    monkeypatch.setattr(managed_runtime.time, "monotonic", lambda: next(clock))
    with pytest.raises(RuntimeError, match="service_runtime_alignment_failed"):
        managed_runtime._wait_unloaded("gui/1/com.loopx.chat")


def test_pending_chat_prevents_both_service_changes(managed):
    root, agents, target, loaded, calls, _ = managed
    store = ChatSessionStore(root)
    sid = store.create_session(goal_id="fixture", agent_id="fixture", adapter_kind="fake",
        upstream_thread_id="fixture")["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="pending", message="Keep accepted work.")
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    with pytest.raises(activation.RuntimeActivationDeferred, match="chat_work_pending"):
        align_managed_runtime(str(target))
    assert not calls and len(loaded) == 2
    assert {p: p.read_bytes() for p in before} == before
    assert store.load_turn(sid, turn["turn_id"])["status"] == "queued"


@pytest.mark.parametrize("shell", [False, True])
@pytest.mark.parametrize("readiness_fails", [False, True])
@pytest.mark.parametrize("route", ["wrong_role", "option_value", "foreign_executable"])
def test_custom_status_route_keeps_its_accepted_chat_and_has_no_effects(
    managed, monkeypatch, shell, readiness_fails, route,
):
    root, agents, target, loaded, calls, _ = managed
    custom_root = root.parent / "custom chat"
    store = ChatSessionStore(custom_root)
    sid = store.create_session(goal_id="fixture", agent_id="fixture", adapter_kind="fake",
        upstream_thread_id="fixture")["session_id"]
    turn, _ = store.create_turn(sid, client_turn_id="pending", message="Keep accepted work.")
    args = ["/old/bin/loopx", "--runtime-root", str(custom_root)]
    if route == "option_value":
        args += ["--registry", "serve-status"]
    args += ["chat", "--host", "127.0.0.1", "--port", "8766"]
    if route == "foreign_executable":
        args[0], args[3] = "/custom/server", "serve-status"
    path = agents / "com.loopx.status.plist"
    payload = plistlib.loads(path.read_bytes())
    payload["ProgramArguments"] = ["/bin/sh", "-c", "exec " + shlex.join(args)] if shell else args
    path.write_bytes(plistlib.dumps(payload))
    loaded[path.stem] = payload
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    def readiness(*_):
        if readiness_fails:
            raise RuntimeError("service_runtime_alignment_failed")
        pytest.fail("unverified source reached readiness after mutation")
    monkeypatch.setattr(managed_runtime, "_verify_services", readiness)
    with pytest.raises(ValueError, match="service_runtime_route_unverified"):
        align_managed_runtime(str(target))
    assert calls == []
    assert {p: p.read_bytes() for p in before} == before
    assert loaded == {p.stem: plistlib.loads(content) for p, content in before.items()}
    assert store.load_turn(sid, turn["turn_id"])["status"] == "queued"
    assert not (agents / ".loopx-runtime-activation.json").exists()


def test_recovery_rejects_a_journal_with_changed_role_or_data_route(managed):
    import base64

    _, agents, target, loaded, calls, _ = managed
    path = agents / "com.loopx.status.plist"
    original = path.read_bytes()
    candidate = plistlib.loads(original)
    candidate["ProgramArguments"][2] = candidate["ProgramArguments"][2].replace("serve-status", "chat")
    journal = agents / ".loopx-runtime-activation.json"
    journal.write_text(json.dumps({"schema_version": "loopx_managed_runtime_handoff_v1",
        "services": {path.stem: {"original": base64.b64encode(original).decode(),
            "candidate": base64.b64encode(plistlib.dumps(candidate)).decode()}}}))
    with pytest.raises(ValueError, match="service_runtime_route_unverified"):
        align_managed_runtime(str(target))
    assert calls == [] and len(loaded) == 2
    assert path.read_bytes() == original and journal.exists()


def test_recovery_reads_a_utf8_journal_under_a_locale_codec(tmp_path):
    journal = tmp_path / "handoff.json"
    journal.write_text(
        json.dumps(
            {
                "schema_version": "loopx_managed_runtime_handoff_v1",
                "note": "继续授权的工作",
                "services": {},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    script = '''
import sys
from pathlib import Path
from loopx.hosts.managed_service_runtime import _recover_alignment

try:
    _recover_alignment(Path(sys.argv[1]))
except ValueError as exc:
    raise SystemExit(0 if str(exc) == "service_runtime_route_unverified" else 1)
raise SystemExit(2)
'''
    environment = {
        **os.environ,
        "PYTHONIOENCODING": "gbk",
        "PYTHONUTF8": "0",
        "PYTHONCOERCECLOCALE": "0",
        "LC_ALL": "C",
        "LANG": "C",
    }
    result = subprocess.run(
        [sys.executable, "-c", script, str(journal)],
        cwd=Path(__file__).resolve().parents[1],
        env=environment,
        capture_output=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")


def test_failed_second_bootstrap_restores_original_loaded_pair(managed):
    _, agents, target, loaded, calls, fail = managed
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    fail.append(("bootstrap", "com.loopx.chat"))
    with pytest.raises(RuntimeError, match="service_runtime_alignment_failed"):
        align_managed_runtime(str(target))
    assert {p: p.read_bytes() for p in before} == before
    assert loaded == {p.stem: plistlib.loads(content) for p, content in before.items()}
    assert calls[-2:] == [("bootstrap", "com.loopx.status"), ("bootstrap", "com.loopx.chat")]


def test_startup_readback_failure_restores_both_original_services(managed, monkeypatch):
    _, agents, target, loaded, _, _ = managed
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    def unavailable(*_):
        raise RuntimeError("service_runtime_alignment_failed")
    monkeypatch.setattr("loopx.hosts.managed_service_runtime._verify_services", unavailable)
    with pytest.raises(RuntimeError, match="service_runtime_alignment_failed"):
        align_managed_runtime(str(target))
    assert {p: p.read_bytes() for p in before} == before
    assert loaded == {p.stem: plistlib.loads(content) for p, content in before.items()}


def test_process_interruption_recovers_before_reapplying(managed, monkeypatch):
    from loopx.hosts import managed_service_runtime as runtime

    _, agents, target, loaded, calls, _ = managed
    original_write = runtime.write_bytes_atomic
    interrupted = False

    def crash(path, content, **kwargs):
        nonlocal interrupted
        original_write(path, content, **kwargs)
        if path.suffix == ".plist" and not interrupted:
            interrupted = True
            raise KeyboardInterrupt("fixture process interruption")

    monkeypatch.setattr(runtime, "write_bytes_atomic", crash)
    with pytest.raises(KeyboardInterrupt):
        align_managed_runtime(str(target))
    assert (agents / ".loopx-runtime-activation.json").exists()
    assert not loaded
    monkeypatch.setattr(runtime, "write_bytes_atomic", original_write)
    calls.clear()
    assert align_managed_runtime(str(target))["status"] == "aligned"
    assert calls[:2] == [("bootstrap", "com.loopx.status"), ("bootstrap", "com.loopx.chat")]
    assert len(loaded) == 2
    assert not (agents / ".loopx-runtime-activation.json").exists()


def test_edited_manager_source_prevents_signals_and_writes(managed):
    _, agents, target, loaded, calls, _ = managed
    changed = agents / "com.loopx.chat.plist"
    payload = plistlib.loads(changed.read_bytes())
    payload["ProgramArguments"] = ["/some/other/loopx", "chat"]
    changed.write_bytes(plistlib.dumps(payload))
    before = {p: p.read_bytes() for p in agents.glob("*.plist")}
    with pytest.raises(ValueError, match="service_runtime_route_unverified"):
        align_managed_runtime(str(target))
    assert not calls and len(loaded) == 2
    assert {p: p.read_bytes() for p in before} == before


@pytest.mark.parametrize("saved", [[], {"schema_version": "unknown"}])
def test_damaged_handoff_stops_before_any_service_mutation(managed, saved, monkeypatch):
    _, agents, target, loaded, calls, _ = managed
    monkeypatch.setattr(activation.sys, "platform", "darwin")
    journal = agents / ".loopx-runtime-activation.json"
    journal.write_text(json.dumps(saved))
    result = activation.activate_runtime_services(runtime_executable=str(target))
    assert result["ok"] is False
    assert result["reason"] == "service_runtime_route_unverified"
    assert not calls and len(loaded) == 2
    assert journal.exists()


def test_cli_update_moves_bound_pair_and_still_restarts_other_services(managed, monkeypatch):
    _, agents, target, loaded, calls, _ = managed
    align_managed_runtime(str(target))
    replacement = target.parent.parent / "next release" / "loopx"
    replacement.parent.mkdir()
    replacement.write_text(target.read_text().replace("new-revision", "next-revision"))
    replacement.chmod(0o700)
    installed = agents.parents[1] / ".local/bin/loopx"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(replacement)
    other = agents / "com.loopx.project.status.plist"
    other.write_bytes(plistlib.dumps({"Label": other.stem, "ProgramArguments": ["/project/loopx", "serve-status"]}))
    loaded[other.stem] = plistlib.loads(other.read_bytes())
    monkeypatch.setattr(activation.sys, "platform", "darwin")
    calls.clear()
    result = activation.restart_services_for_runtime_activation(
        changes_applied=True, runtime_ready=True, installed_bin=installed)
    assert result["activation_pending"] is False
    assert set(result["restarted_services"]) == {"com.loopx.status", "com.loopx.chat", other.stem}
    assert calls[-1] == ("kickstart", other.stem)
    assert loaded[other.stem]["ProgramArguments"] == ["/project/loopx", "serve-status"]
    for kind in ("status", "chat"):
        assert str(replacement.resolve()) in loaded[f"com.loopx.{kind}"]["ProgramArguments"][2]


@pytest.mark.parametrize("shell", ["exec /old/loopx $(touch /tmp/no)",
    "exec /old/loopx chat | cat", "exec /old/loopx chat > out", "echo exec /old/loopx chat",
    'exec /old/loopx "$RUNTIME"', "exec /old/loopx `whoami`", "exec /old/loopx; echo done"])
def test_custom_shell_effects_are_not_rewritten(shell):
    with pytest.raises(ValueError):
        _retarget({"ProgramArguments": ["/bin/sh", "-c", shell]}, "/new/loopx", "/new/python")


@pytest.mark.skipif(sys.platform == "win32", reason="macOS launcher uses POSIX paths")
def test_direct_launcher_retains_environment_and_arguments():
    original = {"ProgramArguments": ["/old/loopx", "chat", "--runtime-root", "/original"],
        "EnvironmentVariables": {"CODEX_HOME": "/account"}}
    updated = _retarget(original, "/new/loopx", "/new/python")
    assert updated["ProgramArguments"] == ["/new/loopx", "chat", "--runtime-root", "/original"]
    assert updated["EnvironmentVariables"] == {"CODEX_HOME": "/account", "LOOPX_PYTHON": "/new/python"}
    assert "LOOPX_PYTHON" not in original["EnvironmentVariables"]


@pytest.mark.skipif(sys.platform != "darwin" or not os.environ.get("LOOPX_TEST_OLD_RUNTIME"),
    reason="opt-in real launchd test requires a selected old Core; see Desktop README")
def test_real_manager_switches_core_and_preserves_original_workspace(tmp_path, monkeypatch):
    """Only labels are redirected; manager, processes, HTTP and stores are real."""
    old = Path(os.environ["LOOPX_TEST_OLD_RUNTIME"]).resolve()
    target = Path(sys.executable).parent / "loopx"
    assert old.is_file() and target.is_file()
    home = tmp_path / "home"
    agents = home / "Library/LaunchAgents"
    agents.mkdir(parents=True)
    root = tmp_path / "original workspace"
    root.mkdir()
    registry = root / "registry.json"
    registry.write_text('{"goals":[]}')
    monkeypatch.setattr(Path, "home", staticmethod(lambda: home))
    alias = f"com.loopx.qa.{uuid.uuid4().hex}"
    domain = f"gui/{os.getuid()}"
    original_run = subprocess.run
    ports = {}
    for kind in ("status", "chat"):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            ports[kind] = listener.getsockname()[1]
    assert ports["status"] != ports["chat"]

    def launchctl(args, **kwargs):
        if args[0] != "launchctl":
            return original_run(args, **kwargs)
        args = list(args)
        if args[1] in {"print", "bootout", "kickstart"}:
            for kind in ("status", "chat"):
                if args[-1] == f"{domain}/com.loopx.{kind}":
                    args[-1] = f"{domain}/{alias}.{kind}"
        if args[1] == "bootstrap":
            source = Path(args[-1])
            kind = source.stem.rsplit(".", 1)[-1]
            payload = plistlib.loads(source.read_bytes())
            payload["Label"] = f"{alias}.{kind}"
            mapped = tmp_path / f"{alias}.{kind}.plist"
            mapped.write_bytes(plistlib.dumps(payload))
            args[-1] = str(mapped)
        return original_run(args, **kwargs)

    monkeypatch.setattr(activation.subprocess, "run", launchctl)
    for kind in ("status", "chat"):
        args = [str(old), "--registry", str(registry), "--runtime-root", str(root),
            "serve-status" if kind == "status" else "chat", "--host", "127.0.0.1",
            "--port", str(ports[kind]), "--scan-path", str(root)]
        if kind == "chat":
            args += ["--no-open"]
        payload = {"Label": f"com.loopx.{kind}", "ProgramArguments": args, "RunAtLoad": True,
            "KeepAlive": True, "EnvironmentVariables": {"HOME": str(home), "LOOPX_USAGE_PING": "0",
                "CODEX_HOME": str(home / ".codex"), "PATH": os.environ.get("PATH", "")},
            "StandardErrorPath": str(tmp_path / f"{kind}.log"),
            "StandardOutPath": str(tmp_path / f"{kind}.out.log")}
        (agents / f"com.loopx.{kind}.plist").write_bytes(plistlib.dumps(payload))

    def read(kind, path, *, timeout=2):
        with urllib.request.urlopen(f"http://127.0.0.1:{ports[kind]}{path}", timeout=timeout) as response:
            return json.load(response)

    def ready():
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                status = read("status", "/?readiness=1")
                chat = read("chat", "/api/chat/capabilities")
                assert status["readiness"]["state"] == "ready"
                return status["runtime_identity"], chat["runtime_identity"]
            except (OSError, ValueError):
                time.sleep(0.1)
        pytest.fail("isolated real manager did not become ready")

    try:
        for kind in ("status", "chat"):
            result = launchctl(["launchctl", "bootstrap", domain, str(agents / f"com.loopx.{kind}.plist")], capture_output=True)
            assert result.returncode == 0, result.stderr
        before = ready()
        # A transient manager failure must restore the running original pair;
        # the next guarded attempt can then recover without data/config edits.
        launch = managed_runtime._launchctl
        interrupted = False

        def fail_once(*args):
            nonlocal interrupted
            if args[0] == "bootstrap" and args[-1].endswith("com.loopx.chat.plist") and not interrupted:
                interrupted = True
                raise RuntimeError("service_runtime_alignment_failed")
            return launch(*args)

        monkeypatch.setattr(managed_runtime, "_launchctl", fail_once)
        with pytest.raises(RuntimeError, match="service_runtime_alignment_failed"):
            align_managed_runtime(str(target))
        assert ready() == before
        started = time.monotonic()
        result = align_managed_runtime(str(target))
        after = ready()
        alignment_seconds = time.monotonic() - started
        assert before[0] != after[0]
        assert after == (result["runtime_identity"], result["runtime_identity"])
        assert read("chat", "/api/chat/sessions")["sessions"] == []
        # This full cold projection checks data routing, after readiness was
        # qualified separately. Its provider cost is not the startup budget.
        projection_started = time.monotonic()
        assert read("status", "/status.json", timeout=15)["registry"] == str(registry)
        projection_seconds = time.monotonic() - projection_started
        pids = [activation._managed_service_pid(agents / f"com.loopx.{k}.plist") for k in ports]
        for _ in range(3):
            assert align_managed_runtime(str(target))["restarted_services"] == []
            assert ready() == after
        assert pids == [activation._managed_service_pid(agents / f"com.loopx.{k}.plist") for k in ports]
        print(json.dumps({"alignment_seconds": round(alignment_seconds, 3),
            "cold_projection_seconds": round(projection_seconds, 3),
            "including_three_repeats_seconds": round(time.monotonic() - started, 3),
            "old_version": before[0]["package_version"], "new_version": after[0]["package_version"],
            "repeat_alignment_restarts": 0, "original_registry_retained": True}))
    finally:
        for kind in ports:
            original_run(["launchctl", "bootout", f"{domain}/{alias}.{kind}"], capture_output=True)
