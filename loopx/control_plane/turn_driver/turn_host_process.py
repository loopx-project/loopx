"""Bind the existing process supervisor to one canonical Turn attempt.

This is an IO/transport adapter, not another Host or migration authority. The
native Goal runtime fence admits launch under maintenance; the original process
owner still records, terminates and observes its own groups. SDK runners that
do not use that supervisor cannot acquire a drain proof from this scope.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...file_lock import cross_runtime_lock_witness, exclusive_cross_runtime_file_lock
from ...runtime import validate_goal_id_path_segment
from ..coordination.shadow_management import shadow_maintenance_lock_target
from ..effect_runtime import effect_runtime_result
from .journal_store import turn_journal_path


@contextmanager
def goal_runtime_write(runtime_root: Path, goal_id: str) -> Iterator[None]:
    """Adapt IO through the native fence; missing legacy fences keep parity."""
    from ..coordination.legacy_writer_fence import legacy_coordination_writer_fence_path

    goal_id = validate_goal_id_path_segment(goal_id)
    root = runtime_root.expanduser().resolve()
    fence = legacy_coordination_writer_fence_path(runtime_root=root, goal_id=goal_id)
    if not fence.exists() and not fence.is_symlink():
        yield
        return
    target = shadow_maintenance_lock_target(root, goal_id)
    with exclusive_cross_runtime_file_lock(target, operation="goal_runtime_io"):
        result = effect_runtime_result("coordination.planned_transfer.runtime_write_check", {
            "runtime_root": str(root), "goal_id": goal_id,
            "maintenance_lock": cross_runtime_lock_witness(target),
        })
        if result != {"allowed": True}:
            raise RuntimeError("Goal runtime did not admit this IO")
        yield


@dataclass(frozen=True, slots=True)
class TurnHostProcess:
    runtime_root: Path
    goal_id: str
    record_path: Path

    @contextmanager
    def admit_launch(self) -> Iterator[None]:
        """Hold only the request publication, never model execution or callbacks."""
        target = shadow_maintenance_lock_target(self.runtime_root, self.goal_id)
        with exclusive_cross_runtime_file_lock(target, operation="turn_host_launch"):
            result = effect_runtime_result("coordination.planned_transfer.runtime_write_check", {
                "runtime_root": str(self.runtime_root), "goal_id": self.goal_id,
                "maintenance_lock": cross_runtime_lock_witness(target),
            })
            if result != {"allowed": True}:
                raise RuntimeError("Goal runtime did not admit this Host launch")
            if self.record_path.exists() or self.record_path.is_symlink():
                raise ValueError("Host attempt already has evidence; recover its original Turn")
            if self.record_path.resolve() != self.record_path:
                raise ValueError("Host attempt requires a physical record route")
            yield


_CURRENT: ContextVar[TurnHostProcess | None] = ContextVar("loopx_turn_host_process", default=None)


def current_turn_host_process() -> TurnHostProcess | None:
    return _CURRENT.get()


@contextmanager
def turn_host_process_scope(
    runtime_root: Path, *, goal_id: str, turn_key: str, attempt: int,
) -> Iterator[None]:
    if type(attempt) is not int or attempt < 1:
        raise ValueError("Host process scope requires the original positive attempt")
    goal_id = validate_goal_id_path_segment(goal_id)
    journal = turn_journal_path(runtime_root.resolve(), goal_id=goal_id, turn_key=turn_key)
    scope = TurnHostProcess(runtime_root.resolve(), goal_id,
                           journal.with_suffix(".hosts") / f"attempt-{attempt}.json")
    token = _CURRENT.set(scope)
    try:
        yield
    finally:
        _CURRENT.reset(token)


def _record_digest(path: Path) -> str | None:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise ValueError("Host evidence is not a physical regular file")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(64 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def read_turn_host_processes(runtime_root: Path, goal_id: str) -> list[dict[str, Any]]:
    """Read actual original groups; an absent record proves no execution state.

    The native journal owner compares this complete address inventory against
    every durable attempt. It interprets missing, orphan or unavailable evidence
    as a hold. A record changing during OS readback is retried, not certified.
    No signal or repair is performed by this reader.
    """
    from .host_process_transport import execution_host_drain, host_process_supervisor_record

    if (not runtime_root.is_absolute() or runtime_root.resolve() != runtime_root
            or not goal_id or goal_id in {".", ".."} or any(c in goal_id for c in "/\\\0")):
        raise ValueError("Host readback requires a physical Goal runtime route")
    turns = runtime_root / "goals" / goal_id / "turns"
    if turns.is_symlink():
        raise ValueError("Host evidence contains a linked ancestor")
    if not turns.exists():
        return []
    if turns.resolve() != turns:
        raise ValueError("Host evidence contains a linked ancestor")
    observed = []
    for directory in sorted(turns.iterdir()):
        if not directory.name.endswith(".hosts"):
            continue
        if (not re.fullmatch(r"[a-f0-9]{64}\.hosts", directory.name)
                or directory.is_symlink() or not directory.is_dir()):
            raise ValueError("Host history contains an unattributable attempt directory")
        attempts = set()
        for path in directory.iterdir():
            # Retained atomic-write temporaries do not identify an execution.
            if path.name.startswith(".") and path.name.endswith(".tmp"):
                continue
            match = re.fullmatch(r"attempt-([1-9][0-9]*)\.(?:json|cli\.host\.json)", path.name)
            if not match or path.is_symlink() or not path.is_file():
                raise ValueError("Host history contains unattributable process evidence")
            attempts.add(int(match[1]))
        for attempt in sorted(attempts):
            path = directory / f"attempt-{attempt}.json"
            peer = host_process_supervisor_record(path)
            before = (_record_digest(path), _record_digest(peer))
            drain = execution_host_drain(path, launch_possible=True)
            if before != (_record_digest(path), _record_digest(peer)):
                raise ValueError("Host evidence changed during process-group readback; retry the original operation")
            observed.append({"turn_key": "sha256:" + directory.name[:-6], "attempt": attempt,
                             "record_sha256": before[0], "supervisor_sha256": before[1], "drain": drain})
    return observed


if __name__ == "__main__":
    # Fixed read-only bridge invoked by the native journal owner. The caller's
    # selected interpreter/source is inherited; the request cannot choose code.
    import sys

    if len(sys.argv) != 3:
        raise ValueError("Host history reader requires runtime and Goal")
    print(json.dumps(read_turn_host_processes(Path(sys.argv[1]), sys.argv[2]), separators=(",", ":")))
