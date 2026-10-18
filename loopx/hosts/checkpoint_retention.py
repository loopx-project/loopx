"""Explicit local checkpoint-bank transport; selection belongs to TypeScript.

No store/Goal or benchmark scoring authority. This only removes self-contained
artifact directories from an operator-selected bank. Native banks are separate.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import stat
import time
from time import monotonic, sleep

from ..control_plane.effect_runtime import effect_runtime_result
from ..file_lock import exclusive_file_lock, write_json_atomic

_STATE = ".checkpoint-retention"
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")


def _physical(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Checkpoint retention requires physical absolute paths")
    return path


def _state(bank: Path) -> Path:
    _physical(bank)
    if os.name != "posix" or not shutil.rmtree.avoids_symlink_attacks:
        raise ValueError("Checkpoint rotation requires a qualified POSIX descriptor-relative unlink transport")
    bank_info = bank.stat()
    if not stat.S_ISDIR(bank_info.st_mode) or bank_info.st_uid != os.getuid() or bank_info.st_mode & 0o077:
        raise ValueError("Checkpoint bank must be private and operator-owned before rotation")
    root = bank / _STATE
    root.mkdir(mode=0o700, exist_ok=True)
    info = _physical(root).stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Checkpoint retention state must be private and operator-owned")
    for name in ("receipts", "trash"):
        (root / name).mkdir(mode=0o700, exist_ok=True)
        member = _physical(root / name).stat()
        if not stat.S_ISDIR(member.st_mode) or member.st_uid != os.getuid() or member.st_mode & 0o077:
            raise ValueError("Checkpoint retention directories must be private and operator-owned")
    return root


@contextmanager
def checkpoint_bank_use(root: Path):
    """Serialize a supported reader against rotation only in a managed bank.

    Off-state is a no-op: no lock files or directory creation. Standalone Effect
    clients and older readers must pin the point before using a managed bank.
    """
    bank = next((p for p in Path(root).parents if (p / _STATE).exists()), None)
    if bank is None:
        yield
        return
    state = _state(bank)
    # Reuse the kernel-lock owner's descriptor/path checks and inode. Shared
    # reads permit same-point arms to restore concurrently; only the rotator
    # needs exclusivity. The POSIX-only transport was checked by _state.
    import fcntl
    from ..file_lock import _lock_path, _open_lock_descriptor
    descriptor = _open_lock_descriptor(_lock_path(state / "bank"), flags=os.O_CREAT | os.O_RDWR)
    try:
        deadline = monotonic() + 30
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_SH | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if monotonic() >= deadline:
                    raise TimeoutError("Checkpoint reader is waiting for storage rotation") from None
                sleep(0.05)
        yield
    finally:
        os.close(descriptor)


def _inventory(bank: Path) -> dict:
    return effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "retention_inventory", "bank": str(bank)}, large_local_snapshot=True)


def retention_policy_bytes(bank: Path, request_file: Path | None = None) -> bytes:
    """Read explicit/default bank configuration without creating preview state."""
    path = request_file or _physical(bank) / _STATE / "policy.json"
    if request_file is not None or path.exists() or path.is_symlink():
        return _physical(path).read_bytes()
    return b"null"  # The typed artifact owner supplies the default.


def retention_plan(bank: Path, policy: dict | None, *, now: float | None = None) -> dict:
    if policy is None:
        policy = json.loads(retention_policy_bytes(bank))
    inventory = _inventory(_physical(bank))
    plan = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "retention_plan", "input": {"policy": policy, "points": inventory["points"],
        "now": time.time() if now is None else now}}, large_local_snapshot=True)
    return {**plan, "incomplete_preserved": inventory["incomplete"]}


def _sync(path: Path) -> None:
    if os.name == "posix":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _receipt(path: Path, record: dict) -> None:
    if path.is_symlink():
        raise ValueError("Checkpoint retention receipt cannot be a symlink")
    write_json_atomic(path, record, mode=0o600, fsync_directory=True)


def _purge(trash: Path) -> None:
    # Never follow links, including chmod on read-only captured directories.
    _physical(trash)
    for directory, children, _ in os.walk(trash, followlinks=False):
        path = Path(directory)
        path.chmod(stat.S_IMODE(path.lstat().st_mode) | 0o700, follow_symlinks=False)
        children[:] = [name for name in children if not (path / name).is_symlink()]
    shutil.rmtree(trash)
    _sync(trash.parent)


def _recover_trash(bank: Path, state: Path) -> list[dict]:
    recovered = []
    for path in sorted((state / "receipts").glob("*.json")):
        record = json.loads(_physical(path).read_bytes())
        point = record["point"]
        if (not _NAME.fullmatch(point["id"]) or not _SHA.fullmatch(point["sha256"]) or
                path.name != point["id"] + "-" + point["sha256"] + ".json" or
                record["status"] not in {"planned", "quarantined", "deleted"}):
            raise ValueError("Malformed retention recovery identity")
        trash = state / "trash" / (point["id"] + "-" + point["sha256"])
        if trash.is_symlink():
            raise ValueError("Checkpoint quarantine cannot be a symlink")
        if record["status"] == "deleted":
            continue
        if trash.exists():
            if (bank / point["id"]).exists():
                raise ValueError("Retention source and quarantine both exist; reconcile original deletion")
            _purge(trash)
        elif record["status"] == "planned":
            # No committed move: recompute selection, never replay an old plan.
            continue
        elif record["status"] != "quarantined":
            raise ValueError("Unknown checkpoint retention recovery status")
        record = {**record, "status": "deleted"}
        _receipt(path, record)
        recovered.append(record)
    return recovered


def rotate_bank(bank: Path, policy: dict | None = None, *, execute: bool = False,
                expected_plan_sha256: str | None = None) -> dict:
    """Plan, then recheck and quarantine exact unprotected points under a lock.

    Preserve response/error sidecars, incomplete captures and unrecognized paths.
    A cleanup failure leaves an owned receipt and quarantine for the next call.
    """
    bank = _physical(bank)
    if not execute:
        return {**retention_plan(bank, policy), "dry_run": True}
    state = _state(bank)
    # Keep rotation/recovery single-flight while unlink runs outside the brief
    # reader lock. Never race another recovery against an in-progress purge.
    with exclusive_file_lock(state / "rotation", operation="checkpoint-rotation", timeout_seconds=1):
        return _rotate_locked(bank, state, policy, expected_plan_sha256)


def _rotate_locked(bank: Path, state: Path, policy: dict | None, expected_plan_sha256: str | None) -> dict:
    # One inode, never unlink the lock; existing reader/rotator waiters agree.
    with exclusive_file_lock(state / "bank", operation="checkpoint-rotate", timeout_seconds=1):
        # A newly declared missing/changed pin holds even recovery of an older
        # quarantine. Never finish unlinking before validating current pins.
        configured = retention_policy_bytes(bank) if policy is None else None
        plan = retention_plan(bank, policy)
        if expected_plan_sha256 is not None and plan["plan_sha256"] != expected_plan_sha256:
            raise ValueError("Checkpoint retention plan changed; review a fresh plan")
        verified = []
        if plan["removed"] or any((state / "trash").iterdir()):
            if not plan["retained"]:
                raise ValueError("No retained recovery point; reconcile quarantine before unlink")
            for point in plan["retained"]:
                effect_runtime_result("runtime.checkpoint_artifacts", {"action": "verify",
                    "root": str(bank / point["id"]), "expected_sha256": point["sha256"]},
                    timeout=120, large_local_snapshot=True)
                verified.append(point["id"])
        recovered = _recover_trash(bank, state)
        # A new publication or changed manifest invalidates this exact plan.
        current = sorted(_inventory(bank)["points"], key=lambda p: (p["sealed_at"], p["id"]))
        if current != plan["points"]:
            raise ValueError("Checkpoint bank moved before retention; retry with a fresh plan")
        if configured is not None and retention_policy_bytes(bank) != configured:
            raise ValueError("Checkpoint bank policy moved before retention; review a fresh plan")
        deleted = []
        for point in plan["removed"]:
            source = _physical(bank / point["id"])
            # Stale/replaced roots cannot be renamed under a previous identity.
            observed = json.loads(_physical(source / "checkpoint.json").read_bytes())
            if observed["sha256"] != point["sha256"]:
                raise ValueError("Checkpoint changed before quarantine")
            target = state / "trash" / (point["id"] + "-" + point["sha256"])
            path = state / "receipts" / (point["id"] + "-" + point["sha256"] + ".json")
            if target.exists() or target.is_symlink():
                raise ValueError("Checkpoint quarantine already exists; recover original deletion")
            record = {"point": point, "plan_sha256": plan["plan_sha256"], "status": "planned"}
            _receipt(path, record)
            source.rename(target)
            _sync(bank)
            _sync(target.parent)
            _receipt(path, {**record, "status": "quarantined"})
            # Release read serialization before the potentially long unlink.
            deleted.append((target, path, record))
    for target, path, record in deleted:
        _purge(target)
        _receipt(path, {**record, "status": "deleted"})
    return {**plan, "dry_run": False, "deleted": [p for p in plan["removed"]], "recovered": recovered,
            "retained_integrity_verified": verified}


def bank_generation(bank: Path, policy_bytes: bytes) -> tuple:
    """Cheap watch trigger; unchanged banks do not redo selection or write logs."""
    rows = []
    for root in _physical(bank).iterdir():
        if not _NAME.fullmatch(root.name):
            continue
        if root.is_symlink():
            raise ValueError("Checkpoint bank contains a symlink")
        manifest = root / "checkpoint.json"
        if manifest.exists():
            info = manifest.lstat()
            rows.append((root.name, info.st_ino, info.st_size, info.st_mtime_ns))
    return policy_bytes, tuple(sorted(rows))
