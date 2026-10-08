"""A failed version observation is not evidence of an unsupported runtime."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from loopx.cli_commands.start_goal import _effect_runtime_startup_failure_payload
from loopx.control_plane import effect_runtime
from loopx.control_plane.runtime import node_probe


@pytest.mark.parametrize(
    ("failure", "code", "action"),
    [
        (subprocess.TimeoutExpired("node", 15), "node_probe_timeout", "host load"),
        (OSError("private executable path"), "node_probe_launch_failed", "launcher"),
        (PermissionError("private executable path"), "runtime_host_permission_denied", "host-approved"),
        (SimpleNamespace(returncode=1, stdout="v24.0.0\n"), "node_probe_exit_failed", "launcher"),
        (SimpleNamespace(returncode=0, stdout="private malformed output"), "node_probe_invalid_version", "launcher"),
    ],
)
def test_node_failure_survives_startup_and_readiness_projection(monkeypatch, failure, code, action):
    monkeypatch.setattr(node_probe.shutil, "which", lambda _: "node")

    def run(*_args, **_kwargs):
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(effect_runtime.subprocess, "run", run)
    with pytest.raises(effect_runtime.EffectRuntimeStartupError) as raised:
        effect_runtime._node_executable()
    assert raised.value.diagnostic_code == code
    assert "requires Node.js" not in str(raised.value)
    assert "private" not in str(raised.value)
    readiness = effect_runtime.collect_effect_runtime_readiness(deep=True)
    assert readiness["status"] == "probe_failed"
    assert readiness["semantic_probe"] == "not_run"
    assert readiness["runtime_lifecycle"]["diagnostic_code"] == code
    assert action in str(readiness["recommended_action"])
    assert "private" not in str(readiness)
    guided = _effect_runtime_startup_failure_payload(raised.value)
    assert guided["diagnostic_code"] == code
    assert action in str(guided["recommended_action"])


def test_node_probe_uses_the_existing_bounded_startup_budget(monkeypatch):
    observed = []
    monkeypatch.setattr(node_probe.shutil, "which", lambda _: "node")

    def run(*_args, **kwargs):
        observed.append(kwargs["timeout"])
        return SimpleNamespace(returncode=0, stdout="v22.22.3\n")

    monkeypatch.setattr(effect_runtime.subprocess, "run", run)
    assert effect_runtime._node_executable() == "node"
    assert observed == [effect_runtime.STARTUP_READY_TIMEOUT_SECONDS]


@pytest.mark.parametrize("failure", [
    subprocess.TimeoutExpired("node", 15),
    SimpleNamespace(returncode=1, stdout=""),
    SimpleNamespace(returncode=0, stdout="invalid"),
    SimpleNamespace(returncode=0, stdout="v20.0.0"),
])
def test_request_does_not_repeat_a_failed_pre_dispatch_node_probe(tmp_path, monkeypatch, failure):
    calls = []
    monkeypatch.setattr(node_probe.shutil, "which", lambda _: "node")
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path)
    monkeypatch.setattr(effect_runtime, "_runtime_fingerprint", lambda: "fixture")

    def run(*_args, **_kwargs):
        calls.append("probe")
        if isinstance(failure, Exception):
            raise failure
        return failure

    monkeypatch.setattr(node_probe.subprocess, "run", run)
    monkeypatch.setattr(effect_runtime, "_request_with_info", lambda **_: pytest.fail("no dispatch"))
    with pytest.raises(effect_runtime.EffectRuntimeNodeProbeError):
        effect_runtime.effect_runtime_result("runtime.ping", {})
    assert calls == ["probe"]
    assert not list(tmp_path.glob("start-*.lock"))


def test_probe_cancellation_is_not_converted_into_version_failure(monkeypatch):
    monkeypatch.setattr(node_probe.shutil, "which", lambda _: "node")

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(effect_runtime.subprocess, "run", interrupt)
    with pytest.raises(KeyboardInterrupt):
        effect_runtime._node_executable()


def _launcher(tmp_path: Path, body: str) -> Path:
    launcher = tmp_path / "node"
    launcher.write_text(f"#!{sys.executable}\n" + body, encoding="utf-8")
    launcher.chmod(0o700)
    return launcher


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable launcher fixture")
def test_slow_compatible_node_serves_real_requests_and_deep_readiness(tmp_path, monkeypatch):
    actual_node = shutil.which("node")
    assert actual_node is not None
    launcher = _launcher(tmp_path, (
        "import os, sys, time\ntime.sleep(2.6)\n"
        f"os.execv({actual_node!r}, [{actual_node!r}, *sys.argv[1:]])\n"
    ))
    monkeypatch.setattr(node_probe.shutil, "which", lambda _: str(launcher))
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path / "runtime")
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(tmp_path))
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "60000")
    started = False
    try:
        ping = effect_runtime.effect_runtime_result("runtime.ping", {})
        started = True
        assert ping["ready"] is True
        assert effect_runtime.effect_runtime_result("runtime.ping", {})["pid"] == ping["pid"]
        readiness = effect_runtime.collect_effect_runtime_readiness(deep=True)
        assert readiness["ready"] is True
        assert readiness["semantic_probe"] == "passed"
        assert readiness["runtime_lifecycle"]["state"] == "running"
        assert readiness["runtime_lifecycle"]["diagnostic_code"] is None
        assert readiness["runtime_identity"]["sqlite_authority_qualified"] is True
    finally:
        stopped = effect_runtime.restart_effect_runtime()
        if started:
            assert stopped["stopped"] is True


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable launcher fixture")
def test_timed_out_probe_reaps_child_and_can_recover(tmp_path, monkeypatch):
    actual_node = shutil.which("node")
    assert actual_node is not None
    pid_file = tmp_path / "pid"
    launcher = _launcher(tmp_path, (
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n"
    ))
    monkeypatch.setattr(node_probe.shutil, "which", lambda _: str(launcher))
    probe = node_probe.probe_node(timeout=5)
    assert probe.diagnostic_code == "node_probe_timeout"
    assert pid_file.exists()
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    _launcher(tmp_path, f"import os, sys\nos.execv({actual_node!r}, [{actual_node!r}, *sys.argv[1:]])\n")
    assert node_probe.probe_node().ready


@pytest.mark.skipif(os.name == "nt", reason="POSIX executable launcher fixture")
def test_cancelling_real_startup_reaps_probe_and_releases_lock(tmp_path):
    pid_file = tmp_path / "pid"
    launcher = _launcher(tmp_path, (
        "import os, time\nfrom pathlib import Path\n"
        f"Path({str(pid_file)!r}).write_text(str(os.getpid()))\ntime.sleep(30)\n"
    ))
    program = (
        "import os, signal, threading, time\n"
        "from pathlib import Path\n"
        "from loopx.control_plane import effect_runtime\n"
        "from loopx.control_plane.runtime import node_probe\n"
        f"node_probe.shutil.which = lambda _: {str(launcher)!r}\n"
        f"effect_runtime._runtime_dir = lambda: Path({str(tmp_path / 'runtime')!r})\n"
        "def cancel():\n"
        f" while not Path({str(pid_file)!r}).exists(): time.sleep(0.01)\n"
        " os.kill(os.getpid(), signal.SIGINT)\n"
        "threading.Thread(target=cancel, daemon=True).start()\n"
        "try: effect_runtime.effect_runtime_result('runtime.ping', {})\n"
        "except KeyboardInterrupt: pass\n"
        "else: raise AssertionError('cancellation swallowed')\n"
    )
    subprocess.run([sys.executable, "-c", program], check=True, timeout=10)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid_file.read_text()), 0)
    assert not list((tmp_path / "runtime").glob("start-*.lock"))
    assert not list((tmp_path / "runtime").glob("runtime-*.json"))
