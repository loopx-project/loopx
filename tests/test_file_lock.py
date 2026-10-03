from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import pytest

import loopx.file_lock as file_lock
from loopx.file_lock import (
    LOCK_ACQUIRE_TIMEOUT_ERROR_CODE,
    LockAcquireTimeoutError,
    _safe_label,
    exclusive_cross_runtime_file_lock,
    exclusive_file_lock,
    fcntl,
    lock_holder_path,
    lock_incident_path,
    msvcrt,
    try_exclusive_file_lock,
)
from loopx.presentation.markdown import append_operator_action_markdown


pytestmark = pytest.mark.skipif(
    fcntl is None and msvcrt is None,
    reason="a supported kernel file-lock backend is required",
)


def _start_stalled_holder(target: Path) -> subprocess.Popen[str]:
    script = """
import sys
import time
from pathlib import Path
from loopx.file_lock import exclusive_file_lock

with exclusive_file_lock(
    Path(sys.argv[1]),
    timeout_seconds=1.0,
    agent_id="holder-agent",
    operation="stalled-holder",
):
    print("ready", flush=True)
    time.sleep(30)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"
    return process


def _stop(process: subprocess.Popen[str]) -> None:
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _acquire_and_release_cross_runtime_lock(
    target: Path,
    *,
    timeout_seconds: float | None = None,
    operation: str | None = None,
) -> None:
    with exclusive_cross_runtime_file_lock(
        target,
        timeout_seconds=timeout_seconds,
        operation=operation,
    ):
        pass


def test_exclusive_lock_persists_public_safe_holder_metadata(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    with exclusive_file_lock(
        target,
        agent_id="agent-a",
        operation="todo-update",
    ) as lock_path:
        holder_path = lock_holder_path(target)
        holder = json.loads(holder_path.read_text(encoding="utf-8"))
        assert holder["pid"] > 0
        # The record names its own machine, so a reader on another host that
        # shares this runtime root never treats the pid as local.
        assert holder["host"] == _safe_label(socket.gethostname(), fallback="unknown")
        assert holder["agent_id"] == "agent-a"
        assert holder["operation"] == "todo-update"
        assert holder["acquired_at"].endswith("Z")
        assert "released_at" not in holder

    released = json.loads(holder_path.read_text(encoding="utf-8"))
    assert released["released_at"].endswith("Z")
    assert lock_path.exists()
    assert holder_path.exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires privileges")
def test_exclusive_lock_rejects_a_symlinked_lock_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged\n", encoding="utf-8")
    target.with_name(f"{target.name}.lock").symlink_to(victim)

    with pytest.raises(OSError):
        with exclusive_file_lock(target):
            pytest.fail("symlinked lock file was accepted")

    assert victim.read_text(encoding="utf-8") == "unchanged\n"


def test_exclusive_lock_rejects_a_hard_linked_lock_file(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged\n", encoding="utf-8")
    os.link(victim, target.with_name(f"{target.name}.lock"))

    with pytest.raises(OSError):
        with exclusive_file_lock(target):
            pytest.fail("hard-linked lock file was accepted")

    assert victim.read_text(encoding="utf-8") == "unchanged\n"


def test_stalled_holder_times_out_and_records_independent_incident(tmp_path: Path) -> None:
    target = tmp_path / "todos.md"
    process = _start_stalled_holder(target)
    try:
        with pytest.raises(LockAcquireTimeoutError) as raised:
            with exclusive_file_lock(
                target,
                timeout_seconds=0.15,
                poll_interval_seconds=0.02,
                agent_id="waiter-agent",
                operation="todo-add",
            ):
                pytest.fail("waiter unexpectedly acquired the stalled lock")

        error = raised.value
        payload = error.to_payload()
        assert payload["error_code"] == LOCK_ACQUIRE_TIMEOUT_ERROR_CODE
        assert payload["incident_recorded"] is True
        incident = payload["lock_timeout"]
        assert incident["holder"]["pid"] == process.pid
        assert incident["holder"]["host"] == _safe_label(
            socket.gethostname(), fallback="unknown"
        )
        assert incident["holder"]["agent_id"] == "holder-agent"
        assert incident["waiter"]["agent_id"] == "waiter-agent"
        assert incident["waiter"]["waited_seconds"] >= 0.1
        # The refused waiter learns which machine holds the pid, so the operator
        # looks for it on the right host instead of assuming it is local.
        assert (
            incident["operator_action"]["holder_host"]
            == incident["holder"]["host"]
        )
        assert incident["operator_action"]["retry_mode"] == (
            "manual_after_holder_inspection"
        )
        markdown_lines: list[str] = []
        append_operator_action_markdown(markdown_lines, payload)
        markdown = "\n".join(markdown_lines)
        assert "error_code: `lock_acquire_timeout`" in markdown
        assert f"holder_pid={process.pid}" in markdown
        assert "Do not delete the lock file" in markdown

        rows = lock_incident_path(target).read_text(encoding="utf-8").splitlines()
        recorded = json.loads(rows[-1])
        assert recorded["error_code"] == LOCK_ACQUIRE_TIMEOUT_ERROR_CODE
        assert recorded["lock_id"] == incident["lock_id"]
        assert str(target) not in rows[-1]
    finally:
        _stop(process)

    assert target.with_name(f"{target.name}.lock").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlink creation requires privileges")
def test_lock_timeout_does_not_follow_a_symlinked_incident_file(
    tmp_path: Path,
) -> None:
    target = tmp_path / "todos.md"
    victim = tmp_path / "victim.txt"
    victim.write_text("unchanged\n", encoding="utf-8")
    process = _start_stalled_holder(target)
    try:
        lock_incident_path(target).symlink_to(victim)
        with pytest.raises(LockAcquireTimeoutError) as raised:
            with exclusive_file_lock(
                target,
                timeout_seconds=0.05,
                poll_interval_seconds=0.01,
            ):
                pytest.fail("waiter unexpectedly acquired the stalled lock")

        assert raised.value.incident_recorded is False
        assert victim.read_text(encoding="utf-8") == "unchanged\n"
    finally:
        _stop(process)


def test_single_flight_returns_none_without_timeout_incident(tmp_path: Path) -> None:
    target = tmp_path / "sync.json"
    process = _start_stalled_holder(target)
    try:
        with try_exclusive_file_lock(target, operation="duplicate-sync") as lock_path:
            assert lock_path is None
        assert not lock_incident_path(target).exists()
    finally:
        _stop(process)


def test_cross_runtime_lock_publishes_the_typescript_owner_file(tmp_path: Path) -> None:
    target = tmp_path / ".task-leases"
    effect_lock = Path(f"{target}.ts-effect.lock")

    with exclusive_cross_runtime_file_lock(target, operation="task-lease-renew"):
        owner = json.loads(effect_lock.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert isinstance(owner["token"], str)
        assert target.with_name(f"{target.name}.lock").exists()

    assert not effect_lock.exists()


def test_cross_runtime_lock_respects_a_live_typescript_holder(tmp_path: Path) -> None:
    target = tmp_path / ".task-leases"
    effect_lock = Path(f"{target}.ts-effect.lock")
    effect_lock.write_text(
        json.dumps({"pid": os.getpid(), "token": "typescript-holder"}),
        encoding="utf-8",
    )

    with pytest.raises(LockAcquireTimeoutError):
        _acquire_and_release_cross_runtime_lock(
            target,
            timeout_seconds=0,
            operation="task-lease-release",
        )


def test_cross_runtime_windows_pid_probe_is_non_signaling(monkeypatch) -> None:
    calls: list[int] = []

    def probe(pid: int) -> bool:
        calls.append(pid)
        return True

    monkeypatch.setattr(file_lock, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(file_lock, "_windows_process_is_alive", probe)

    assert file_lock._effect_mutation_process_is_alive(1234) is True
    assert calls == [1234]


def test_cross_runtime_lock_reclaims_a_dead_typescript_holder(tmp_path: Path) -> None:
    target = tmp_path / ".task-leases"
    effect_lock = Path(f"{target}.ts-effect.lock")
    effect_lock.write_text(
        json.dumps({"pid": 2_147_483_647, "token": "dead-holder"}),
        encoding="utf-8",
    )

    with exclusive_cross_runtime_file_lock(
        target,
        timeout_seconds=0.2,
        operation="task-lease-transfer",
    ):
        owner = json.loads(effect_lock.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()

    assert not effect_lock.exists()


def test_cross_runtime_lock_reclaims_a_stale_malformed_holder(tmp_path: Path) -> None:
    target = tmp_path / ".task-leases"
    effect_lock = Path(f"{target}.ts-effect.lock")
    effect_lock.write_text(
        json.dumps({"pid": os.getpid(), "token": "   "}),
        encoding="utf-8",
    )
    stale = effect_lock.stat().st_mtime - 60.0
    os.utime(effect_lock, (stale, stale))

    with exclusive_cross_runtime_file_lock(
        target,
        timeout_seconds=0.2,
        operation="task-lease-release",
    ):
        owner = json.loads(effect_lock.read_text(encoding="utf-8"))
        assert owner["pid"] == os.getpid()
        assert owner["token"] != "   "

    assert not effect_lock.exists()


def test_cross_runtime_release_does_not_remove_a_replacement_token(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".task-leases"
    effect_lock = Path(f"{target}.ts-effect.lock")
    effect_lock.parent.mkdir(parents=True, exist_ok=True)
    effect_lock.write_text(
        json.dumps({"pid": os.getpid(), "token": "replacement-token"}),
        encoding="utf-8",
    )

    assert file_lock._release_effect_mutation_lock(
        effect_lock,
        "old-token",
    ) is False
    assert effect_lock.exists()
    assert json.loads(effect_lock.read_text(encoding="utf-8"))["token"] == (
        "replacement-token"
    )
    effect_lock.unlink()


def test_cross_runtime_recovery_releases_only_the_owned_token(tmp_path: Path) -> None:
    target = tmp_path / ".task-leases"
    effect_lock = Path(f"{target}.ts-effect.lock")
    effect_lock.write_text(
        json.dumps({"pid": os.getpid(), "token": "held-token"}),
        encoding="utf-8",
    )

    assert not file_lock.release_cross_runtime_mutation_lock(
        target,
        **{"token": "replacement-token"},
    )
    assert effect_lock.exists()
    assert file_lock.release_cross_runtime_mutation_lock(
        target,
        **{"token": "held-token"},
    )
    assert not effect_lock.exists()


def test_cross_runtime_lock_cleans_up_a_failed_owner_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ".task-leases"
    effect_lock = Path(f"{target}.ts-effect.lock")

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("simulated owner publication failure")

    monkeypatch.setattr(os, "fsync", fail_fsync)
    with pytest.raises(OSError, match="owner publication failure"):
        _acquire_and_release_cross_runtime_lock(target)

    assert not effect_lock.exists()


def test_cross_runtime_identity_fails_closed_for_ambiguous_or_reused_files() -> None:
    assert not file_lock._same_effect_file_identity(
        (0, 0, 0, 0),
        (0, 0, 0, 0),
    )
    assert not file_lock._same_effect_file_identity(
        (7, 11, 100, 200),
        (7, 11, 101, 200),
    )
    assert file_lock._same_effect_file_identity(
        (7, 11, 100, 200),
        (7, 11, 100, 999),
    )
    assert file_lock._same_effect_file_identity(
        (7, 11, 0, 200),
        (7, 11, 0, 201),
    )


def test_cross_runtime_claim_cleanup_removes_own_corrupted_claim(
    tmp_path: Path,
) -> None:
    target = tmp_path / ".task-leases"
    claim = file_lock._claim_effect_mutation_lock(target, "claim-token")
    assert claim is not None
    claim.path.write_text("not-json", encoding="utf-8")

    file_lock._release_effect_mutation_claim(claim)

    assert not claim.path.exists()


def test_cross_runtime_cleanup_failure_cannot_replace_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / ".task-leases"
    calls: list[bool] = []

    def fake_release(*args: object, **kwargs: object) -> bool:
        calls.append(kwargs.get("suppress_errors") is True)
        return False

    monkeypatch.setattr(file_lock, "_release_effect_mutation_lock", fake_release)
    with exclusive_cross_runtime_file_lock(target, timeout_seconds=0.2):
        pass

    assert calls == [True]


@pytest.mark.skipif(os.name != "nt", reason="Win32 extended path regression")
def test_long_lock_paths_release_and_keep_one_identity(tmp_path):
    from loopx.control_plane.runtime.file_paths import windows_extended_path

    target = tmp_path / ("nested-" * 12) / ("a" * 64 + ".json")
    extended = windows_extended_path(target)
    assert file_lock._lock_id(target) == file_lock._lock_id(extended)
    with exclusive_cross_runtime_file_lock(target):
        assert file_lock.cross_runtime_lock_witness(target)["token"]
    # Long claim-file cleanup must succeed while this process is still alive.
    with exclusive_cross_runtime_file_lock(extended, timeout_seconds=0):
        with pytest.raises(LockAcquireTimeoutError):
            with exclusive_cross_runtime_file_lock(target, timeout_seconds=0):
                pytest.fail("normal and extended addresses acquired two locks")
    assert not file_lock._effect_mutation_lock_path(target).exists()


@pytest.mark.skipif(os.name != "nt", reason="Win32 extended path regression")
def test_long_lock_paths_exclude_other_process_and_release_while_holder_alive(tmp_path):
    from loopx.control_plane.runtime.file_paths import windows_extended_path

    target = tmp_path / ("nested-" * 24) / ("a" * 64 + ".json")
    assert len(str(target)) > 260
    extended = windows_extended_path(target)
    script = """
import sys
from pathlib import Path
from loopx.file_lock import exclusive_cross_runtime_file_lock
with exclusive_cross_runtime_file_lock(Path(sys.argv[1])):
    print("ready", flush=True)
    sys.stdin.readline()
print("released", flush=True)
sys.stdin.readline()
"""
    holder = subprocess.Popen([sys.executable, "-c", script, str(target)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert holder.stdout is not None and holder.stdin is not None
        assert holder.stdout.readline().strip() == "ready"
        with pytest.raises(LockAcquireTimeoutError):
            with exclusive_cross_runtime_file_lock(extended, timeout_seconds=0):
                pytest.fail("extended address bypassed another process's lock")
        holder.stdin.write("release\n")
        holder.stdin.flush()
        assert holder.stdout.readline().strip() == "released"
        assert holder.poll() is None
        with exclusive_cross_runtime_file_lock(extended, timeout_seconds=0):
            assert file_lock._lock_id(target) == file_lock._lock_id(extended)
        assert not file_lock._effect_mutation_lock_path(target).exists()
    finally:
        _stop(holder)
