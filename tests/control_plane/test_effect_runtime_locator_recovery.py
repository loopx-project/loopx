"""A damaged locator must not hide or duplicate a live managed runtime."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from loopx.control_plane import effect_runtime
from loopx.control_plane.runtime.node_probe import NodeProbe, NodeProbeOutcome
from loopx.doctor import render_doctor_markdown


def _runtime_info_path() -> Path:
    fingerprint = effect_runtime._runtime_fingerprint()
    return effect_runtime._runtime_info_path(fingerprint)


def _isolate_runtime(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(effect_runtime, "_runtime_dir", lambda: tmp_path / "runtime")
    monkeypatch.setenv("LOOPX_EFFECT_RUNTIME_IDLE_MS", "60000")


def _shutdown_info(info: dict[str, object]) -> None:
    try:
        effect_runtime._request_with_info(
            info,
            request_id=uuid4().hex,
            method="runtime.shutdown",
            params={},
            timeout=2,
        )
    except (OSError, RuntimeError):
        return
    pid = info.get("pid")
    deadline = time.monotonic() + 5
    while effect_runtime._pid_is_alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)


def _shutdown_current_runtime() -> None:
    fingerprint = effect_runtime._runtime_fingerprint()
    info = effect_runtime._read_info(
        effect_runtime._runtime_info_path(fingerprint),
        fingerprint=fingerprint,
    )
    if info is not None:
        _shutdown_info(info)


def _damaged_locator(
    *,
    fingerprint: str,
    pid: int,
    token: str = "fixture-token",
) -> effect_runtime._RuntimeLocatorRead:
    return effect_runtime._RuntimeLocatorRead(
        info=None,
        present=True,
        diagnostic_code="runtime_locator_schema_mismatch",
        recorded_pid_alive=True,
        raw_payload={
            "schema_version": "loopx_effect_runtime_info_v1",
            "fingerprint": fingerprint,
            "pid": pid,
            "host": "127.0.0.1",
            "port": 12345,
            "token": token,
        },
    )


def _prepare_recovery(monkeypatch, *, fingerprint: str, pid: int) -> None:
    locator = _damaged_locator(fingerprint=fingerprint, pid=pid)
    monkeypatch.setattr(effect_runtime, "_read_info", lambda *args, **kwargs: None)
    monkeypatch.setattr(effect_runtime, "_inspect_info", lambda *args, **kwargs: locator)


def test_corrupt_schema_version_reuses_live_runtime_and_reports_its_liveness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    first = effect_runtime.effect_runtime_result("runtime.ping", {})
    info_path = _runtime_info_path()
    original = json.loads(info_path.read_text(encoding="utf-8"))
    try:
        info_path.write_text(
            json.dumps({**original, "schema_version": "loopx_effect_runtime_info_v1"}),
            encoding="utf-8",
        )

        readiness = effect_runtime.collect_effect_runtime_readiness()
        second = effect_runtime.effect_runtime_result("runtime.ping", {})

        lifecycle = readiness["runtime_lifecycle"]
        assert readiness["ready"] is True
        assert lifecycle["state"] == "running"
        assert lifecycle["locator_state"] == "invalid"
        assert lifecycle["recorded_pid_alive"] is True
        assert lifecycle["diagnostic_code"] == "runtime_locator_schema_mismatch"
        assert second["pid"] == first["pid"]
        rendered = render_doctor_markdown({"typescript_control_plane": readiness})
        assert "typescript_runtime_locator_state: `invalid`" in rendered
        assert "typescript_runtime_recorded_pid_alive: `True`" in rendered

        deep_readiness = effect_runtime.collect_effect_runtime_readiness(deep=True)
        deep_lifecycle = deep_readiness["runtime_lifecycle"]
        assert deep_readiness["semantic_probe"] == "passed"
        assert deep_lifecycle["state"] == "running"
        assert deep_lifecycle["locator_state"] == "invalid"
        assert deep_lifecycle["recorded_pid_alive"] is True
        assert deep_lifecycle["diagnostic_code"] == "runtime_locator_schema_mismatch"

        restarted = effect_runtime.restart_effect_runtime(wait_for_exit=True)
        assert restarted["status"] == "stopped"
        assert effect_runtime._pid_is_alive(first["pid"]) is False
    finally:
        _shutdown_info(original)
        if info_path.exists():
            try:
                replacement = json.loads(info_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                replacement = None
            if isinstance(replacement, dict):
                _shutdown_info(replacement)


def test_recovery_rejects_a_ping_from_a_different_pid(monkeypatch) -> None:
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    _prepare_recovery(monkeypatch, fingerprint=fingerprint, pid=os.getpid())
    monkeypatch.setattr(
        effect_runtime,
        "_request_with_info",
        lambda *args, **kwargs: {
            "result": {
                "ready": True,
                "pid": os.getpid() + 1,
                "fingerprint": fingerprint,
                "info_path": str(info_path),
            }
        },
    )

    assert effect_runtime._runtime_info_for_request(
        info_path, fingerprint=fingerprint
    ) is None


def test_recovery_rejects_a_token_the_daemon_does_not_accept(monkeypatch) -> None:
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    _prepare_recovery(monkeypatch, fingerprint=fingerprint, pid=os.getpid())

    def reject(*args, **kwargs):
        raise RuntimeError("runtime.ping rejected")

    monkeypatch.setattr(effect_runtime, "_request_with_info", reject)

    assert effect_runtime._runtime_info_for_request(
        info_path, fingerprint=fingerprint
    ) is None


def test_recovery_rejects_a_ping_with_a_different_fingerprint(monkeypatch) -> None:
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    _prepare_recovery(monkeypatch, fingerprint=fingerprint, pid=os.getpid())
    monkeypatch.setattr(
        effect_runtime,
        "_request_with_info",
        lambda *args, **kwargs: {
            "result": {
                "ready": True,
                "pid": os.getpid(),
                "fingerprint": "0" * 64,
                "info_path": str(info_path),
            }
        },
    )

    assert effect_runtime._runtime_info_for_request(
        info_path, fingerprint=fingerprint
    ) is None


def test_recovery_rejects_a_ping_published_at_a_different_locator_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    _prepare_recovery(monkeypatch, fingerprint=fingerprint, pid=os.getpid())
    monkeypatch.setattr(
        effect_runtime,
        "_request_with_info",
        lambda *args, **kwargs: {
            "result": {
                "ready": True,
                "pid": os.getpid(),
                "fingerprint": fingerprint,
                "info_path": str(tmp_path / "other-runtime.json"),
            }
        },
    )

    assert effect_runtime._runtime_info_for_request(
        info_path, fingerprint=fingerprint
    ) is None


def test_unreadable_locator_reports_a_diagnostic_instead_of_looking_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    info_path = _runtime_info_path()
    info_path.parent.mkdir(parents=True)
    info_path.write_text("not-json", encoding="utf-8")

    readiness = effect_runtime.collect_effect_runtime_readiness()

    lifecycle = readiness["runtime_lifecycle"]
    assert lifecycle["state"] == "stopped"
    assert lifecycle["locator_state"] == "invalid"
    assert lifecycle["recorded_pid_alive"] is None
    assert lifecycle["diagnostic_code"] == "runtime_locator_unreadable"


def test_invalid_utf8_locator_does_not_break_doctor(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    info_path = _runtime_info_path()
    info_path.parent.mkdir(parents=True)
    info_path.write_bytes(b"\xff\xfe\x00not-a-locator")

    readiness = effect_runtime.collect_effect_runtime_readiness()

    lifecycle = readiness["runtime_lifecycle"]
    assert lifecycle["state"] == "stopped"
    assert lifecycle["locator_state"] == "invalid"
    assert lifecycle["recorded_pid_alive"] is None
    assert lifecycle["diagnostic_code"] == "runtime_locator_unreadable"
    rendered = render_doctor_markdown({"typescript_control_plane": readiness})
    assert "typescript_runtime_locator_state: `invalid`" in rendered
    assert "typescript_runtime_diagnostic: `runtime_locator_unreadable`" in rendered


def test_oversized_locator_pid_does_not_escape_readiness(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    info_path.parent.mkdir(parents=True)
    info_path.write_text(
        json.dumps({
            "schema_version": "loopx_effect_runtime_info_v1",
            "fingerprint": fingerprint,
            "host": "127.0.0.1",
            "port": 1,
            "token": "fixture-token",
            "pid": 10**30,
        }),
        encoding="utf-8",
    )

    readiness = effect_runtime.collect_effect_runtime_readiness()

    lifecycle = readiness["runtime_lifecycle"]
    assert lifecycle["locator_state"] == "invalid"
    assert lifecycle["recorded_pid_alive"] is None
    assert lifecycle["diagnostic_code"] == "runtime_locator_schema_mismatch"
    rendered = render_doctor_markdown({"typescript_control_plane": readiness})
    assert "typescript_runtime_recorded_pid_alive: `None`" in rendered


def test_installation_doctor_keeps_json_for_oversized_locator_pid(
    tmp_path: Path,
) -> None:
    fingerprint = effect_runtime._runtime_fingerprint()
    owner = str(os.getuid()) if hasattr(os, "getuid") else str(Path.home())
    runtime_dir = tmp_path / (
        "loopx-effect-runtime-"
        + hashlib.sha256(owner.encode("utf-8")).hexdigest()[:12]
    )
    runtime_dir.mkdir()
    info_path = runtime_dir / f"runtime-{fingerprint[:16]}.json"
    info_path.write_text(
        json.dumps({
            "schema_version": "loopx_effect_runtime_info_v1",
            "fingerprint": fingerprint,
            "host": "127.0.0.1",
            "port": 1,
            "token": "fixture-token",
            "pid": 10**30,
        }),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "TMPDIR": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }

    loopx_cli = Path(sys.executable).with_name(
        "loopx.exe" if os.name == "nt" else "loopx"
    )
    completed = subprocess.run(
        [str(loopx_cli), "doctor", "--installation-only", "--format", "json"],
        cwd=Path(__file__).resolve().parents[2],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert completed.returncode in (0, 1), completed.stderr
    payload = json.loads(completed.stdout)
    lifecycle = payload["typescript_control_plane"]["runtime_lifecycle"]
    assert lifecycle["locator_state"] == "invalid"
    assert lifecycle["recorded_pid_alive"] is None
    assert "Traceback" not in completed.stderr


def test_dead_recorded_pid_keeps_a_liveness_diagnostic(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = process.pid
    process.wait(timeout=10)
    assert effect_runtime._pid_is_alive(dead_pid) is False

    info_path = _runtime_info_path()
    info_path.parent.mkdir(parents=True)
    info_path.write_text(
        json.dumps(
            {
                "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
                "fingerprint": effect_runtime._runtime_fingerprint(),
                "pid": dead_pid,
                "host": "127.0.0.1",
                "port": 1,
                "token": "stale-token",
            }
        ),
        encoding="utf-8",
    )

    readiness = effect_runtime.collect_effect_runtime_readiness()

    lifecycle = readiness["runtime_lifecycle"]
    assert lifecycle["state"] == "stopped"
    assert lifecycle["locator_state"] == "invalid"
    assert lifecycle["recorded_pid_alive"] is False
    assert lifecycle["diagnostic_code"] == "runtime_locator_pid_not_alive"


def test_deep_readiness_refreshes_locator_after_starting_runtime(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    try:
        readiness = effect_runtime.collect_effect_runtime_readiness(deep=True)

        lifecycle = readiness["runtime_lifecycle"]
        assert readiness["ready"] is True
        assert readiness["semantic_probe"] == "passed"
        assert lifecycle["state"] == "running"
        assert lifecycle["locator_state"] == "valid"
        assert lifecycle["recorded_pid_alive"] is True
        assert lifecycle["diagnostic_code"] is None
    finally:
        _shutdown_current_runtime()


def test_generic_locator_read_error_does_not_look_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    info_path = _runtime_info_path()
    original_read_text = Path.read_text

    def read(path, *args, **kwargs):
        if path == info_path:
            raise OSError(errno.EIO, "private read details")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read)
    readiness = effect_runtime.collect_effect_runtime_readiness()

    lifecycle = readiness["runtime_lifecycle"]
    assert lifecycle["state"] == "stopped"
    assert lifecycle["locator_state"] == "invalid"
    assert lifecycle["recorded_pid_alive"] is None
    assert lifecycle["diagnostic_code"] == "runtime_locator_unreadable"
    assert "private read details" not in json.dumps(readiness)


def test_readiness_reports_a_live_locator_when_node_is_unavailable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(
        effect_runtime, "_probe_node", lambda **_: NodeProbe(NodeProbeOutcome.MISSING),
    )
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    info_path.parent.mkdir(parents=True)
    info_path.write_text(
        json.dumps({
            "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "host": "127.0.0.1",
            "port": 1,
            "token": "fixture-token",
            "pid": os.getpid(),
        }),
        encoding="utf-8",
    )

    readiness = effect_runtime.collect_effect_runtime_readiness()

    lifecycle = readiness["runtime_lifecycle"]
    assert readiness["ready"] is False
    assert readiness["status"] == "missing"
    assert lifecycle["state"] == "unavailable"
    assert lifecycle["locator_state"] == "valid"
    assert lifecycle["recorded_pid_alive"] is True
    assert lifecycle["diagnostic_code"] == "node_unavailable"
