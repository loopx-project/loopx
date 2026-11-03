"""Service alignment preserves the real Chat admission boundary and manager route."""
from __future__ import annotations

import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import sys
import socket
import time
import urllib.request
import uuid

import pytest

from loopx import runtime_activation as activation
from loopx.managed_service_runtime import _retarget, align_managed_runtime
from loopx.chat_store import ChatSessionStore


@pytest.fixture
def managed(tmp_path, monkeypatch):
    monkeypatch.setattr("loopx.managed_service_runtime._verify_services", lambda *_: None)
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
            return subprocess.CompletedProcess(args, 0 if label in loaded else 1, text, "")
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
    monkeypatch.setattr("loopx.managed_service_runtime._verify_services", unavailable)
    with pytest.raises(RuntimeError, match="service_runtime_alignment_failed"):
        align_managed_runtime(str(target))
    assert {p: p.read_bytes() for p in before} == before
    assert loaded == {p.stem: plistlib.loads(content) for p, content in before.items()}


def test_process_interruption_recovers_before_reapplying(managed, monkeypatch):
    from loopx import managed_service_runtime as runtime

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
    installed = agents.parents[1] / ".local/bin/loopx"
    installed.parent.mkdir(parents=True)
    installed.symlink_to(target)
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
        assert str(installed) in loaded[f"com.loopx.{kind}"]["ProgramArguments"][2]


@pytest.mark.parametrize("shell", ["exec /old/loopx $(touch /tmp/no)",
    "exec /old/loopx chat | cat", "exec /old/loopx chat > out", "echo exec /old/loopx chat",
    'exec /old/loopx "$RUNTIME"', "exec /old/loopx `whoami`", "exec /old/loopx; echo done"])
def test_custom_shell_effects_are_not_rewritten(shell):
    with pytest.raises(ValueError):
        _retarget({"ProgramArguments": ["/bin/sh", "-c", shell]}, "/new/loopx", "/new/python")


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
