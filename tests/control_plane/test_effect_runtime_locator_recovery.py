"""A damaged locator must not hide or duplicate a live managed runtime."""
from __future__ import annotations

import errno
import hashlib
import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
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


def test_linux_zombie_thread_group_is_reported_not_alive_during_locator_recovery(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    monkeypatch.setattr(effect_runtime.sys, "platform", "linux")
    monkeypatch.setattr(effect_runtime, "process_is_alive", lambda pid: pid == 424242)
    zombie = True
    monkeypatch.setattr(effect_runtime, "_linux_thread_group_is_zombie", lambda pid: zombie and pid == 424242)

    assert effect_runtime._pid_is_alive(424242) is False
    fingerprint = effect_runtime._runtime_fingerprint()
    info_path = effect_runtime._runtime_info_path(fingerprint)
    info_path.parent.mkdir(parents=True)
    info_path.write_text(
        json.dumps({
            "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
            "fingerprint": fingerprint,
            "host": "127.0.0.1",
            "port": 1,
            "token": "stale-token",
            "pid": 424242,
        }),
        encoding="utf-8",
    )

    readiness = effect_runtime.collect_effect_runtime_readiness()
    lifecycle = readiness["runtime_lifecycle"]
    assert lifecycle["state"] == "stopped"
    assert lifecycle["recorded_pid_alive"] is False
    assert lifecycle["diagnostic_code"] == "runtime_locator_pid_not_alive"

    zombie = False
    assert effect_runtime._pid_is_alive(424242) is True


def test_cleanup_stale_runtime_descriptors_respects_liveness_grace_locks_and_bounds(
    tmp_path: Path,
    monkeypatch,
) -> None:
    _isolate_runtime(tmp_path, monkeypatch)
    runtime_dir = effect_runtime._runtime_dir()
    runtime_dir.mkdir(parents=True, mode=0o700)

    dead_proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = dead_proc.pid
    dead_proc.wait(timeout=10)
    assert effect_runtime._pid_is_alive(dead_pid) is False

    stale_old = time.time() - 3600.0
    fingerprints = {
        "dead_clean_a": "a" * 64,
        "dead_clean_b": "b" * 64,
        "dead_recent": "c" * 64,
        "dead_start_lock": "d" * 64,
        "dead_ts_lock": "e" * 64,
        "dead_claim_lock": "f" * 64,
        "alive_old": "1" * 64,
        "oversized_pid": "2" * 64,
    }

    def write_descriptor(fp: str, pid: object, *, mtime: float | None = stale_old) -> Path:
        path = runtime_dir / f"runtime-{fp[:16]}.json"
        path.write_text(
            json.dumps({
                "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
                "fingerprint": fp,
                "host": "127.0.0.1",
                "port": 1,
                "token": f"token-{fp[:8]}",
                "pid": pid,
            }),
            encoding="utf-8",
        )
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    dead_a = write_descriptor(fingerprints["dead_clean_a"], dead_pid)
    dead_b = write_descriptor(fingerprints["dead_clean_b"], dead_pid)
    dead_recent = write_descriptor(fingerprints["dead_recent"], dead_pid, mtime=None)
    dead_start = write_descriptor(fingerprints["dead_start_lock"], dead_pid)
    dead_ts = write_descriptor(fingerprints["dead_ts_lock"], dead_pid)
    dead_claim = write_descriptor(fingerprints["dead_claim_lock"], dead_pid)
    alive_old = write_descriptor(fingerprints["alive_old"], os.getpid())
    oversized_pid = write_descriptor(fingerprints["oversized_pid"], 10**30)
    unrelated_json = runtime_dir / "other.json"
    unrelated_json.write_text("{}", encoding="utf-8")
    os.utime(unrelated_json, (stale_old, stale_old))
    symlink_dead = runtime_dir / f"runtime-{'3' * 16}.json"
    symlink_dead.symlink_to(dead_a)

    start_lock = runtime_dir / f"start-{fingerprints['dead_start_lock'][:16]}.lock"
    start_lock.write_text(f"{os.getpid()}\n", encoding="utf-8")
    os.utime(start_lock, (stale_old, stale_old))

    ts_lock = Path(f"{dead_ts}.ts-effect.lock")
    ts_lock.write_text(json.dumps({"pid": os.getpid(), "token": "live"}), encoding="utf-8")
    os.utime(ts_lock, (stale_old, stale_old))

    claim_lock = Path(f"{dead_claim}.ts-effect.lock.claim.{'0' * 64}")
    claim_lock.write_text(json.dumps({"pid": dead_pid, "token": "claim"}), encoding="utf-8")
    os.utime(claim_lock, (stale_old, stale_old))

    # The caller's own target descriptor stays intact so readiness keeps its diagnostic.
    current_fp = effect_runtime._runtime_fingerprint()
    current_path = write_descriptor(current_fp, dead_pid)

    readiness = effect_runtime.collect_effect_runtime_readiness()
    assert readiness["runtime_lifecycle"]["diagnostic_code"] == "runtime_locator_pid_not_alive"

    assert not dead_a.exists()
    assert not dead_b.exists()
    assert dead_recent.exists()
    assert dead_start.exists()
    assert dead_ts.exists()
    assert dead_claim.exists()
    assert alive_old.exists()
    assert oversized_pid.exists()
    assert unrelated_json.exists()
    assert symlink_dead.is_symlink()
    assert current_path.exists()

    # Publication (_start_runtime) also performs bounded stale descriptor cleanup.
    numbered_fps = [f"{idx:016x}{'0' * 48}" for idx in range(10)]
    for fp in numbered_fps:
        write_descriptor(fp, dead_pid)

    class _DummyProcess:
        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            return None

    monkeypatch.setattr(effect_runtime, "_node_executable", lambda: "node")
    monkeypatch.setattr(effect_runtime, "_read_info", lambda *_a, **_k: None)
    monkeypatch.setattr(effect_runtime.subprocess, "Popen", lambda *_a, **_k: _DummyProcess())
    monkeypatch.setattr(
        effect_runtime,
        "_await_startup_publication",
        lambda *_a, **_k: {"ready": True, "pid": os.getpid()},
    )
    effect_runtime._start_runtime(fingerprint=current_fp, info_path=current_path)
    remaining_numbered = [
        p for fp in numbered_fps if (p := runtime_dir / f"runtime-{fp[:16]}.json").exists()
    ]
    assert remaining_numbered == []

    # Bounded removal limit caps per-pass unlinks.
    try:
        extra_fps = [f"{100 + idx:016x}{'0' * 48}" for idx in range(40)]
        for fp in extra_fps:
            write_descriptor(fp, dead_pid)
        removed = effect_runtime._cleanup_stale_runtime_descriptors(
            runtime_dir, exclude_path=current_path, max_inspect=64, max_removals=5,
        )
        assert removed == 5
        still_present = sum(1 for fp in extra_fps if (runtime_dir / f"runtime-{fp[:16]}.json").exists())
        assert still_present == 35
    finally:
        effect_runtime.close_stale_cleanup_sweeps()


def test_cold_miss_cleanup_bounds_scandir_and_advances_across_240_descriptors(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dead_proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = dead_proc.pid
    dead_proc.wait(timeout=10)
    assert effect_runtime._pid_is_alive(dead_pid) is False

    stale_old = time.time() - 3600.0
    max_inspect = effect_runtime._STALE_RUNTIME_DESCRIPTOR_MAX_INSPECT
    real_scandir = effect_runtime.os.scandir

    def make_runtime_dir(name: str) -> Path:
        root = tmp_path / name
        root.mkdir(parents=True, mode=0o700)
        return root

    def write_desc(directory: Path, fp: str, pid: int, *, mtime: float | None) -> Path:
        path = directory / f"runtime-{fp[:16]}.json"
        path.write_text(
            json.dumps({
                "schema_version": effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION,
                "fingerprint": fp,
                "host": "127.0.0.1",
                "port": 1,
                "token": f"tok-{fp[:8]}",
                "pid": pid,
            }),
            encoding="utf-8",
        )
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    # 1. Cold miss on 240 fresh descriptors stops scandir at <= max_inspect entries.
    fresh_dir = make_runtime_dir("fresh_240")
    for idx in range(240):
        write_desc(fresh_dir, f"{0x1000 + idx:016x}{'0' * 48}", os.getpid(), mtime=None)
    fresh_target_fp = "f" * 64
    fresh_target = fresh_dir / f"runtime-{fresh_target_fp[:16]}.json"

    stale_dir = make_runtime_dir("stale_240")
    stale_paths = [
        write_desc(stale_dir, f"{0x2000 + idx:016x}{'0' * 48}", dead_pid, mtime=stale_old)
        for idx in range(240)
    ]
    stale_target_fp = "e" * 64
    stale_target = stale_dir / f"runtime-{stale_target_fp[:16]}.json"

    mixed_dir = make_runtime_dir("mixed_240")
    alive_paths = [
        write_desc(mixed_dir, f"{0x3000 + idx:016x}{'0' * 48}", os.getpid(), mtime=stale_old)
        for idx in range(80)
    ]
    dead_beyond = [
        write_desc(mixed_dir, f"{0x4000 + idx:016x}{'0' * 48}", dead_pid, mtime=stale_old)
        for idx in range(159)
    ]
    locked_beyond = write_desc(mixed_dir, f"{0x5000:016x}{'0' * 48}", dead_pid, mtime=stale_old)
    locked_ts = Path(f"{locked_beyond}.ts-effect.lock")
    locked_ts.write_text(json.dumps({"pid": os.getpid(), "token": "live"}), encoding="utf-8")
    os.utime(locked_ts, (stale_old, stale_old))
    mixed_target_fp = "d" * 64
    mixed_target = mixed_dir / f"runtime-{mixed_target_fp[:16]}.json"

    # Real >512 directory with 600 non-removable leading entries and 8 stale tail entries
    # placed at actual os.scandir() indices >= 512 (no synthetic sorting).
    tail_alive_dir = make_runtime_dir("tail_alive_608")
    for idx in range(608):
        write_desc(tail_alive_dir, f"{0x6000 + idx:016x}{'0' * 48}", os.getpid(), mtime=stale_old)
    with real_scandir(tail_alive_dir) as raw_entries:
        actual_order = [entry.name for entry in raw_entries]
    assert len(actual_order) == 608
    tail_stale_names = set(actual_order[600:608])
    tail_alive_paths = [tail_alive_dir / name for name in actual_order[:600]]
    tail_stale_paths = [tail_alive_dir / name for name in actual_order[600:608]]
    for stale_path in tail_stale_paths:
        existing_payload = json.loads(stale_path.read_text(encoding="utf-8"))
        stale_path.write_text(
            json.dumps({**existing_payload, "pid": dead_pid}),
            encoding="utf-8",
        )
        os.utime(stale_path, (stale_old, stale_old))
    with real_scandir(tail_alive_dir) as raw_after:
        stale_positions = [
            idx for idx, entry in enumerate(raw_after) if entry.name in tail_stale_names
        ]
    assert len(stale_positions) == 8 and min(stale_positions) >= 512
    tail_alive_target_fp = "c" * 64
    tail_alive_target = tail_alive_dir / f"runtime-{tail_alive_target_fp[:16]}.json"

    # >512 directory with 600 non-runtime files preceding 8 stale runtime descriptors.
    tail_nonruntime_dir = make_runtime_dir("tail_nonruntime_608")
    misc_paths = []
    for idx in range(600):
        misc_path = tail_nonruntime_dir / f"misc-{idx:04d}.tmp"
        misc_path.write_text("{}", encoding="utf-8")
        misc_paths.append(misc_path)
    nonruntime_stale_paths = [
        write_desc(tail_nonruntime_dir, f"{0x7000 + idx:016x}{'0' * 48}", dead_pid, mtime=stale_old)
        for idx in range(8)
    ]
    nonruntime_target_fp = "b" * 64
    nonruntime_target = tail_nonruntime_dir / f"runtime-{nonruntime_target_fp[:16]}.json"

    target_dirs = {
        fresh_dir.resolve(),
        stale_dir.resolve(),
        mixed_dir.resolve(),
        tail_alive_dir.resolve(),
        tail_nonruntime_dir.resolve(),
    }
    scandir_opens: dict[Path, int] = {}
    scandir_closes: dict[Path, int] = {}
    total_yields = [0]

    def make_counted_scandir(*, sort_dirs: set[Path] | None = None):
        resolved_sort = {p.resolve() for p in (sort_dirs or set())}

        def counted_scandir(path=None):
            if path is None:
                return real_scandir()
            try:
                resolved = Path(path).resolve()
            except OSError:
                return real_scandir(path)
            if resolved not in target_dirs:
                return real_scandir(path)

            cm = real_scandir(path)
            scandir_opens[resolved] = scandir_opens.get(resolved, 0) + 1
            should_sort = resolved in resolved_sort

            class _BoundedScanCM:
                def __init__(self) -> None:
                    self._entered = False
                    self._iter: Iterator[os.DirEntry[str]] | None = None

                def _ensure_iter(self) -> Iterator[os.DirEntry[str]]:
                    if self._iter is None:
                        inner = cm.__enter__()
                        self._entered = True
                        self._iter = (
                            iter(sorted(inner, key=lambda entry: entry.name))
                            if should_sort
                            else iter(inner)
                        )
                    return self._iter

                def __enter__(self) -> _BoundedScanCM:
                    self._ensure_iter()
                    return self

                def __exit__(self, exc_type, exc, tb):
                    return self.close(exc_type, exc, tb)

                def __iter__(self) -> _BoundedScanCM:
                    self._ensure_iter()
                    return self

                def __next__(self) -> os.DirEntry[str]:
                    item = next(self._ensure_iter())
                    total_yields[0] += 1
                    return item

                def close(self, exc_type=None, exc=None, tb=None):
                    if self._entered:
                        self._entered = False
                        scandir_closes[resolved] = scandir_closes.get(resolved, 0) + 1
                        return cm.__exit__(exc_type, exc, tb)
                    return False

            return _BoundedScanCM()

        return counted_scandir

    def run_cold_miss(target: Path, fp: str) -> int:
        before = total_yields[0]
        assert effect_runtime._runtime_info_for_request(target, fingerprint=fp) is None
        return total_yields[0] - before

    try:
        effect_runtime.close_stale_cleanup_sweeps()

        # 1. Cold miss on 240 fresh descriptors stops scandir at <= max_inspect entries.
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.os, "scandir", make_counted_scandir())
            first_yields = run_cold_miss(fresh_target, fresh_target_fp)
        assert first_yields <= max_inspect
        assert scandir_opens[fresh_dir.resolve()] == 1
        assert sum(1 for _ in fresh_dir.glob("runtime-*.json")) == 240

        # 2. Repeated cold misses on 240 stale descriptors reclaim all 240 via 1 resumable sweep.
        per_call_yields: list[int] = []
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.os, "scandir", make_counted_scandir())
            for _ in range(8):
                per_call_yields.append(run_cold_miss(stale_target, stale_target_fp))
        assert len(per_call_yields) == 8
        assert max(per_call_yields) <= max_inspect
        assert scandir_opens[stale_dir.resolve()] == 1
        assert scandir_closes[stale_dir.resolve()] == 1
        assert all(not p.exists() for p in stale_paths)

        # 3. Advancing sweep skips >64 leading alive entries and reclaims stale entries behind them,
        #    while protecting active lock files checked directly per candidate.
        per_call_inspections: list[set[Path]] = []
        orig_lock_check = effect_runtime._runtime_descriptor_has_active_lock

        def counting_lock_check(info_path, *args, **kwargs):
            per_call_inspections[-1].add(info_path)
            return orig_lock_check(info_path, *args, **kwargs)

        per_call_yields.clear()
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime, "_runtime_descriptor_has_active_lock", counting_lock_check)
            mp.setattr(effect_runtime.os, "scandir", make_counted_scandir(sort_dirs={mixed_dir}))
            for _ in range(7):
                per_call_inspections.append(set())
                per_call_yields.append(run_cold_miss(mixed_target, mixed_target_fp))
        assert len(per_call_inspections[0]) == max_inspect
        assert all(len(seen) <= max_inspect for seen in per_call_inspections)
        assert per_call_yields[0] == max_inspect
        assert max(per_call_yields) <= max_inspect
        assert all(p.exists() for p in alive_paths)
        assert all(not p.exists() for p in dead_beyond)
        assert locked_beyond.exists()

        # 4. Real >512 directory (actual os.scandir order): 600 non-removable leading entries + 8 stale
        #    descriptors past raw index 512 get reached and reclaimed on the 10th call with <=64 yields/call.
        per_call_inspections.clear()
        per_call_yields.clear()
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime, "_runtime_descriptor_has_active_lock", counting_lock_check)
            mp.setattr(effect_runtime.os, "scandir", make_counted_scandir())
            for _ in range(10):
                per_call_inspections.append(set())
                per_call_yields.append(run_cold_miss(tail_alive_target, tail_alive_target_fp))
        assert max(per_call_yields) <= max_inspect
        assert scandir_opens[tail_alive_dir.resolve()] == 1
        assert scandir_closes[tail_alive_dir.resolve()] == 1
        assert all(len(seen) <= max_inspect for seen in per_call_inspections)
        assert all(p.exists() for p in tail_alive_paths)
        assert all(not p.exists() for p in tail_stale_paths)

        # 5. >512 non-runtime prefix: hits 512 window_steps on first cold miss, resumes past 512 on second.
        per_call_yields.clear()
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.os, "scandir", make_counted_scandir(sort_dirs={tail_nonruntime_dir}))
            per_call_yields.append(run_cold_miss(nonruntime_target, nonruntime_target_fp))
            assert per_call_yields[0] == effect_runtime._STALE_RUNTIME_DESCRIPTOR_MAX_SCAN_STEPS
            assert all(p.exists() for p in nonruntime_stale_paths)
            per_call_yields.append(run_cold_miss(nonruntime_target, nonruntime_target_fp))
        assert per_call_yields[1] == 96
        assert scandir_opens[tail_nonruntime_dir.resolve()] == 1
        assert scandir_closes[tail_nonruntime_dir.resolve()] == 1
        assert all(p.exists() for p in misc_paths)
        assert all(not p.exists() for p in nonruntime_stale_paths)
    finally:
        effect_runtime.close_stale_cleanup_sweeps()
    assert effect_runtime.os.scandir is real_scandir


def _make_tracked_scandir(
    counted_dirs: set[Path],
    real_scandir,
    scandir_opens: dict[Path, int],
    scandir_closes: dict[Path, int],
    total_yields: list[int],
):
    def tracked_scandir(path=None):
        if path is None:
            return real_scandir()
        try:
            resolved = Path(path).resolve()
        except OSError:
            return real_scandir(path)
        if resolved not in counted_dirs:
            return real_scandir(path)
        cm = real_scandir(path)
        scandir_opens[resolved] = scandir_opens.get(resolved, 0) + 1

        class _TrackedScanCM:
            def __init__(self) -> None:
                self._entered = False
                self._iter: Iterator[os.DirEntry[str]] | None = None

            def _ensure_iter(self) -> Iterator[os.DirEntry[str]]:
                if self._iter is None:
                    self._iter = iter(cm.__enter__())
                    self._entered = True
                return self._iter

            def __enter__(self) -> _TrackedScanCM:
                self._ensure_iter()
                return self

            def __exit__(self, exc_type, exc, tb):
                return self.close(exc_type, exc, tb)

            def __iter__(self) -> _TrackedScanCM:
                self._ensure_iter()
                return self

            def __next__(self) -> os.DirEntry[str]:
                item = next(self._ensure_iter())
                total_yields[0] += 1
                return item

            def close(self, exc_type=None, exc=None, tb=None):
                if self._entered:
                    self._entered = False
                    scandir_closes[resolved] = scandir_closes.get(resolved, 0) + 1
                    return cm.__exit__(exc_type, exc, tb)
                return False

        return _TrackedScanCM()

    return tracked_scandir


def test_cold_miss_resumable_sweep_reclaims_large_tail_and_closes_handles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dead_proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = dead_proc.pid
    dead_proc.wait(timeout=10)
    assert effect_runtime._pid_is_alive(dead_pid) is False

    stale_old = time.time() - 3600.0
    max_inspect = effect_runtime._STALE_RUNTIME_DESCRIPTOR_MAX_INSPECT
    real_scandir = effect_runtime.os.scandir

    def seed_real_tail_dir(
        name: str,
        prefix_count: int,
        *,
        expire_prefix: bool,
    ) -> tuple[Path, Path, str, list[Path], list[Path]]:
        root = tmp_path / name
        root.mkdir(parents=True, mode=0o700)
        alive_pid = os.getpid()
        schema_json = json.dumps(effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION)
        total = prefix_count + 8
        for idx in range(total):
            fp = f"{idx:016x}{'0' * 48}"
            payload = (
                f'{{"schema_version":{schema_json},'
                f'"fingerprint":"{fp}","host":"127.0.0.1","port":1,"token":"t-{idx}","pid":{alive_pid}}}'
            )
            desc_path = root / f"runtime-{fp[:16]}.json"
            desc_path.write_text(payload, encoding="utf-8")
            if expire_prefix:
                os.utime(desc_path, (stale_old, stale_old))
        with real_scandir(root) as raw_entries:
            actual_order = [entry.name for entry in raw_entries]
        assert len(actual_order) == total
        prefix_paths = [root / entry_name for entry_name in actual_order[:prefix_count]]
        tail_paths = [root / entry_name for entry_name in actual_order[prefix_count:]]
        tail_names = {p.name for p in tail_paths}
        for stale_path in tail_paths:
            existing = json.loads(stale_path.read_text(encoding="utf-8"))
            stale_path.write_text(json.dumps({**existing, "pid": dead_pid}), encoding="utf-8")
            os.utime(stale_path, (stale_old, stale_old))
        with real_scandir(root) as raw_after:
            stale_positions = [
                idx for idx, entry in enumerate(raw_after) if entry.name in tail_names
            ]
        assert len(stale_positions) == 8 and min(stale_positions) >= prefix_count
        target_fp = "f" * 64
        target = root / f"runtime-{target_fp[:16]}.json"
        return root, target, target_fp, prefix_paths, tail_paths

    dir_16k, target_16k, fp_16k, prefix_16k, tail_16k = seed_real_tail_dir(
        "tail_16384", 16384, expire_prefix=False,
    )
    dir_17k, target_17k, fp_17k, prefix_17k, tail_17k = seed_real_tail_dir(
        "tail_17000", 17000, expire_prefix=True,
    )

    schema_json = json.dumps(effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION)
    lru_dirs: list[tuple[Path, Path, str]] = []
    for lru_idx in range(effect_runtime._STALE_CLEANUP_SWEEP_LIMIT + 2):
        lru_dir = tmp_path / f"lru_{lru_idx}"
        lru_dir.mkdir(parents=True, mode=0o700)
        for idx in range(max_inspect + 8):
            fp = f"{0x9000 + lru_idx * 1000 + idx:016x}{'0' * 48}"
            payload = (
                f'{{"schema_version":{schema_json},'
                f'"fingerprint":"{fp}","host":"127.0.0.1","port":1,"token":"t-{idx}","pid":{os.getpid()}}}'
            )
            (lru_dir / f"runtime-{fp[:16]}.json").write_text(payload, encoding="utf-8")
        lru_target_fp = f"{lru_idx + 1:064x}"
        lru_dirs.append((lru_dir, lru_dir / f"runtime-{lru_target_fp[:16]}.json", lru_target_fp))

    counted_dirs = {
        dir_16k.resolve(),
        dir_17k.resolve(),
        *(d.resolve() for d, _, _ in lru_dirs),
    }
    scandir_opens: dict[Path, int] = {}
    scandir_closes: dict[Path, int] = {}
    total_yields = [0]
    counted_scandir = _make_tracked_scandir(
        counted_dirs, real_scandir, scandir_opens, scandir_closes, total_yields,
    )

    try:
        effect_runtime.close_stale_cleanup_sweeps()

        # Real 16,384-prefix dir (old raw_offset wrap point): 257 calls, <= 64 raw scandir yields per call.
        yields_16k: list[int] = []
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.os, "scandir", counted_scandir)
            for _ in range(257):
                before = total_yields[0]
                assert effect_runtime._runtime_info_for_request(target_16k, fingerprint=fp_16k) is None
                yields_16k.append(total_yields[0] - before)
        assert len(yields_16k) == 257
        assert max(yields_16k) <= max_inspect
        assert scandir_opens[dir_16k.resolve()] == 1
        assert scandir_closes[dir_16k.resolve()] == 1
        assert all(not p.exists() for p in tail_16k)
        assert all(p.exists() for p in prefix_16k)

        # Real 17,000-prefix dir (beyond old 16,384 wrap point): 266 calls (< 285), <= 64 yields/call.
        yields_17k: list[int] = []
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.os, "scandir", counted_scandir)
            for _ in range(266):
                before = total_yields[0]
                assert effect_runtime._runtime_info_for_request(target_17k, fingerprint=fp_17k) is None
                yields_17k.append(total_yields[0] - before)
        assert len(yields_17k) == 266
        assert max(yields_17k) <= max_inspect
        assert scandir_opens[dir_17k.resolve()] == 1
        assert scandir_closes[dir_17k.resolve()] == 1
        assert all(not p.exists() for p in tail_17k)
        assert all(p.exists() for p in prefix_17k)

        # LRU eviction, runtime stop, & explicit close release open scandir handles without leaks.
        stop_dir, stop_target, stop_fp = lru_dirs[0]
        stop_resolved = stop_dir.resolve()
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.os, "scandir", counted_scandir)
            for _, lru_target, lru_fp in lru_dirs:
                assert effect_runtime._runtime_info_for_request(lru_target, fingerprint=lru_fp) is None
            assert len(effect_runtime._stale_cleanup_sweeps) <= effect_runtime._STALE_CLEANUP_SWEEP_LIMIT
            effect_runtime.close_stale_cleanup_sweeps()
            assert effect_runtime._stale_cleanup_sweeps == {}
            assert effect_runtime._runtime_info_for_request(stop_target, fingerprint=stop_fp) is None
            assert effect_runtime._cleanup_cursor_key(stop_dir) in effect_runtime._stale_cleanup_sweeps
            stop_result = effect_runtime._stop_effect_runtime(stop_target, None, timeout=0.05)
            assert stop_result["status"] == "not_running"
            assert effect_runtime._cleanup_cursor_key(stop_dir) not in effect_runtime._stale_cleanup_sweeps
        for lru_dir, _, _ in lru_dirs:
            resolved = lru_dir.resolve()
            expected_count = 2 if resolved == stop_resolved else 1
            assert scandir_opens[resolved] == expected_count
            assert scandir_closes[resolved] == expected_count
    finally:
        effect_runtime.close_stale_cleanup_sweeps()
    assert effect_runtime.os.scandir is real_scandir


def test_cold_miss_resumable_sweep_spans_idle_timeout_cross_dir_and_inode_swap(
    tmp_path: Path,
    monkeypatch,
) -> None:
    dead_proc = subprocess.Popen([sys.executable, "-c", "pass"])
    dead_pid = dead_proc.pid
    dead_proc.wait(timeout=10)
    assert effect_runtime._pid_is_alive(dead_pid) is False

    stale_old = time.time() - 3600.0
    max_inspect = effect_runtime._STALE_RUNTIME_DESCRIPTOR_MAX_INSPECT
    real_scandir = effect_runtime.os.scandir
    schema_json = json.dumps(effect_runtime.EFFECT_RUNTIME_INFO_SCHEMA_VERSION)

    def write_descriptor(
        directory: Path,
        fp: str,
        pid: int,
        *,
        mtime: float | None,
    ) -> Path:
        path = directory / f"runtime-{fp[:16]}.json"
        path.write_text(
            f'{{"schema_version":{schema_json},"fingerprint":"{fp}",'
            f'"host":"127.0.0.1","port":1,"token":"t-{fp[:8]}","pid":{pid}}}',
            encoding="utf-8",
        )
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    # 1. Low-frequency cold misses spaced 301s apart (> 300s idle timeout) across 600 protected
    #    prefix entries (alive PID, active lock, and within-grace descriptor) + 8 dead-PID tail
    #    descriptors finish in 10 calls with <= 64 raw scandir yields/call and clean EOF close.
    low_freq_dir = tmp_path / "low_freq_608"
    low_freq_dir.mkdir(parents=True, mode=0o700)
    for idx in range(608):
        write_descriptor(low_freq_dir, f"{0x10000 + idx:016x}{'0' * 48}", os.getpid(), mtime=stale_old)
    with real_scandir(low_freq_dir) as raw_entries:
        actual_order = [entry.name for entry in raw_entries]
    assert len(actual_order) == 608
    alive_prefix = [low_freq_dir / name for name in actual_order[:598]]
    locked_prefix = low_freq_dir / actual_order[598]
    grace_prefix = low_freq_dir / actual_order[599]
    tail_stale_names = set(actual_order[600:608])
    tail_stale_paths = [low_freq_dir / name for name in actual_order[600:608]]

    # Convert prefix[598] to dead-PID guarded by active .ts-effect.lock, prefix[599] to fresh grace,
    # and the final 8 entries to expired dead-PID descriptors at real scandir indices >= 600.
    locked_payload = json.loads(locked_prefix.read_text(encoding="utf-8"))
    locked_prefix.write_text(json.dumps({**locked_payload, "pid": dead_pid}), encoding="utf-8")
    os.utime(locked_prefix, (stale_old, stale_old))
    locked_ts = Path(f"{locked_prefix}.ts-effect.lock")
    locked_ts.write_text(json.dumps({"pid": os.getpid(), "token": "live"}), encoding="utf-8")
    os.utime(locked_ts, (stale_old, stale_old))

    grace_payload = json.loads(grace_prefix.read_text(encoding="utf-8"))
    grace_prefix.write_text(json.dumps({**grace_payload, "pid": dead_pid}), encoding="utf-8")

    for stale_path in tail_stale_paths:
        existing = json.loads(stale_path.read_text(encoding="utf-8"))
        stale_path.write_text(json.dumps({**existing, "pid": dead_pid}), encoding="utf-8")
        os.utime(stale_path, (stale_old, stale_old))

    with real_scandir(low_freq_dir) as raw_after:
        stale_positions = [
            idx for idx, entry in enumerate(raw_after) if entry.name in tail_stale_names
        ]
    assert len(stale_positions) == 8 and min(stale_positions) >= 600

    low_freq_target_fp = "a" * 64
    low_freq_target = low_freq_dir / f"runtime-{low_freq_target_fp[:16]}.json"

    # 2. Cross-directory idle eviction + directory identity replacement fixtures.
    idle_dir_a = tmp_path / "idle_dir_a"
    idle_dir_a.mkdir(parents=True, mode=0o700)
    for idx in range(max_inspect + 12):
        write_descriptor(idle_dir_a, f"{0x20000 + idx:016x}{'0' * 48}", os.getpid(), mtime=stale_old)
    idle_a_target_fp = "b" * 64
    idle_a_target = idle_dir_a / f"runtime-{idle_a_target_fp[:16]}.json"

    idle_dir_b = tmp_path / "idle_dir_b"
    idle_dir_b.mkdir(parents=True, mode=0o700)
    for idx in range(max_inspect + 12):
        write_descriptor(idle_dir_b, f"{0x30000 + idx:016x}{'0' * 48}", os.getpid(), mtime=stale_old)
    idle_b_target_fp = "c" * 64
    idle_b_target = idle_dir_b / f"runtime-{idle_b_target_fp[:16]}.json"

    replace_dir = tmp_path / "replace_dir"
    replace_dir.mkdir(parents=True, mode=0o700)
    for idx in range(max_inspect + 12):
        write_descriptor(replace_dir, f"{0x40000 + idx:016x}{'0' * 48}", os.getpid(), mtime=stale_old)
    replace_target_fp = "d" * 64
    replace_target = replace_dir / f"runtime-{replace_target_fp[:16]}.json"

    counted_dirs = {
        low_freq_dir.resolve(),
        idle_dir_a.resolve(),
        idle_dir_b.resolve(),
        replace_dir.resolve(),
    }
    scandir_opens: dict[Path, int] = {}
    scandir_closes: dict[Path, int] = {}
    total_yields = [0]
    monotonic_clock = [10000.0]
    tracked_scandir = _make_tracked_scandir(
        counted_dirs, real_scandir, scandir_opens, scandir_closes, total_yields,
    )

    try:
        effect_runtime.close_stale_cleanup_sweeps()

        # Low-frequency 301s steps complete sweep across 608+ entries in 10 calls with <= 64 yields/call.
        low_freq_yields: list[int] = []
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.time, "monotonic", lambda: monotonic_clock[0])
            mp.setattr(effect_runtime.os, "scandir", tracked_scandir)
            for call_idx in range(10):
                if call_idx > 0:
                    monotonic_clock[0] += effect_runtime._STALE_CLEANUP_SWEEP_IDLE_SECONDS + 1.0
                before = total_yields[0]
                assert effect_runtime._runtime_info_for_request(
                    low_freq_target, fingerprint=low_freq_target_fp,
                ) is None
                low_freq_yields.append(total_yields[0] - before)
        assert len(low_freq_yields) == 10
        assert max(low_freq_yields) <= max_inspect + 1
        assert sum(low_freq_yields) == 609
        assert scandir_opens[low_freq_dir.resolve()] == 1
        assert scandir_closes[low_freq_dir.resolve()] == 1
        assert all(p.exists() for p in alive_prefix)
        assert locked_prefix.exists()
        assert grace_prefix.exists()
        assert all(not p.exists() for p in tail_stale_paths)

        # Cross-directory idle eviction: dir_A stays open until dir_B checks out >= 300s later.
        idle_a_key = effect_runtime._cleanup_cursor_key(idle_dir_a)
        idle_b_key = effect_runtime._cleanup_cursor_key(idle_dir_b)
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.time, "monotonic", lambda: monotonic_clock[0])
            mp.setattr(effect_runtime.os, "scandir", tracked_scandir)
            assert effect_runtime._runtime_info_for_request(idle_a_target, fingerprint=idle_a_target_fp) is None
            assert scandir_opens[idle_dir_a.resolve()] == 1
            assert scandir_closes.get(idle_dir_a.resolve(), 0) == 0
            assert idle_a_key in effect_runtime._stale_cleanup_sweeps

            monotonic_clock[0] += effect_runtime._STALE_CLEANUP_SWEEP_IDLE_SECONDS + 1.0
            assert effect_runtime._runtime_info_for_request(idle_b_target, fingerprint=idle_b_target_fp) is None
            assert scandir_closes[idle_dir_a.resolve()] == 1
            assert idle_a_key not in effect_runtime._stale_cleanup_sweeps
            assert idle_b_key in effect_runtime._stale_cleanup_sweeps
        effect_runtime.close_stale_cleanup_sweeps()
        assert scandir_closes[idle_dir_b.resolve()] == 1

        # Directory identity change (path recreated with a new inode) closes stale sweep and starts fresh.
        replace_resolved = replace_dir.resolve()
        old_stat = os.lstat(replace_dir)
        with monkeypatch.context() as mp:
            mp.setattr(effect_runtime.time, "monotonic", lambda: monotonic_clock[0])
            mp.setattr(effect_runtime.os, "scandir", tracked_scandir)
            assert effect_runtime._runtime_info_for_request(replace_target, fingerprint=replace_target_fp) is None
            assert scandir_opens[replace_resolved] == 1
            assert scandir_closes.get(replace_resolved, 0) == 0

            old_backup = tmp_path / "replace_dir_old"
            replace_dir.rename(old_backup)
            replace_dir.mkdir(parents=True, mode=0o700)
            new_stat = os.lstat(replace_dir)
            assert (new_stat.st_dev, new_stat.st_ino) != (old_stat.st_dev, old_stat.st_ino)
            fresh_stale = [
                write_descriptor(replace_dir, f"{0x50000 + idx:016x}{'0' * 48}", dead_pid, mtime=stale_old)
                for idx in range(4)
            ]
            assert effect_runtime._runtime_info_for_request(replace_target, fingerprint=replace_target_fp) is None
        assert scandir_opens[replace_resolved] == 2
        assert scandir_closes[replace_resolved] == 2
        assert all(not p.exists() for p in fresh_stale)
    finally:
        effect_runtime.close_stale_cleanup_sweeps()
    assert effect_runtime.os.scandir is real_scandir
