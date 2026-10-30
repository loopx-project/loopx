"""Python transport for the TS-owned managed Host process lifecycle."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import AbstractContextManager, ExitStack
from pathlib import Path
from typing import Any

from ...file_lock import lock_holder_host_label
from ..effect_runtime import _node_executable
from .host_bridge_transport import GuardedHostBridge

# Keep Python's Windows executable/batch launcher compatibility. No timeout,
# buffering or lifecycle decision lives in this transport-only child.
_WINDOWS_COMMAND_RELAY = "import subprocess,sys;sys.exit(subprocess.call(sys.argv[1:]))"


# A launching owner that must later prove its Host drained names a record path
# here. The transport consumes it: the Host never inherits it, so a nested
# LoopX run inside the Host cannot overwrite its parent's record.
HOST_PROCESS_RECORD_ENV = "LOOPX_HOST_PROCESS_RECORD"
HOST_PROCESS_PARENT_ENV = "LOOPX_HOST_PROCESS_PARENT"
HOST_PROCESS_RECORD_SCHEMA_VERSION = "loopx_host_process_record_v0"
# Drain facts read back from a record; the caller's typed decision interprets them.
HOST_PROCESS_NOT_LAUNCHED = "not_launched"
HOST_PROCESS_DRAINED = "drained"
HOST_PROCESS_DRAINING = "draining"
HOST_PROCESS_UNATTRIBUTABLE = "unattributable"
HOST_PROCESS_UNSUPPORTED_PLATFORM = "unsupported_platform"
# Each record says which group its supervisor owns: the actual Host, or a leased
# CLI that supervises a nested Host recorded under the owner's record path.
HOST_PROCESS_SUPERVISES_HOST = "host"
HOST_PROCESS_SUPERVISES_NESTED_HOST = "nested_host"
# When one execution's records disagree, the first fact present wins: a group
# still seen running outranks a missing proof, which outranks a proven exit.
_DRAIN_PRECEDENCE = (HOST_PROCESS_UNSUPPORTED_PLATFORM, HOST_PROCESS_DRAINING,
                     HOST_PROCESS_UNATTRIBUTABLE, HOST_PROCESS_DRAINED, HOST_PROCESS_NOT_LAUNCHED)


def host_process_drain_supported() -> bool:
    """Whether this platform can prove an owned Host group has exited.

    The TS supervisor cleans up a process group on POSIX and a process tree
    best-effort on Windows. Only the group gives the stop a fact it can prove,
    so the caller must say so rather than reporting a settlement it cannot
    support.
    """

    return hasattr(os, "killpg")


def _write_host_process_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(record, handle, separators=(",", ":"))
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def prepare_host_process_record(record_path: Path) -> None:
    """Record non-execution before a new operation can launch, never on replay.

    The operation creator holds its dispatch lock. Retain any existing evidence;
    initialization must not erase a prior launch, including interrupted setup.
    """
    if not record_path.exists():
        _write_host_process_record(record_path, {
            "schema_version": HOST_PROCESS_RECORD_SCHEMA_VERSION,
            "host": lock_holder_host_label(), "supervises": HOST_PROCESS_SUPERVISES_HOST,
            "supervision": "direct", "phase": HOST_PROCESS_NOT_LAUNCHED, "process_group": None,
        })


def _process_group_present(pgid: int) -> bool | None:
    """Return liveness, or unknown when the stored id cannot reach the OS."""

    try:
        os.killpg(pgid, 0)
    except (OverflowError, ValueError):
        return None
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # it exists; it is simply not ours to signal
    return True


def host_process_supervisor_record(record_path: Path) -> Path:
    """Where a leased CLI's own supervisor records its group, beside the owner's record."""
    return record_path.with_suffix(".cli.host.json")


def _read_host_process_record(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return {}  # Unreadable evidence is different from an absent record.
    return record if isinstance(record, dict) else {}


def _host_process_drain(record: dict[str, Any] | None, *, supervises: str,
                        expected: bool) -> str:
    """Read one owner's facts without interpreting missing expected evidence as exit."""
    if record is None:
        return HOST_PROCESS_UNATTRIBUTABLE if expected else HOST_PROCESS_NOT_LAUNCHED
    if (not isinstance(record, dict) or record.get("schema_version") != HOST_PROCESS_RECORD_SCHEMA_VERSION
            or record.get("host") != lock_holder_host_label() or record.get("supervises") != supervises
            or record.get("supervision") not in {"direct", "leased"}
            or (supervises == HOST_PROCESS_SUPERVISES_NESTED_HOST and record["supervision"] != "leased")
            or record.get("phase") not in {HOST_PROCESS_NOT_LAUNCHED, "launching", "spawned", "finished"}):
        return HOST_PROCESS_UNATTRIBUTABLE
    if record.get("phase") == HOST_PROCESS_NOT_LAUNCHED:
        # Written before execution can start; its transport replaces this with
        # a launching record before sending any Host request.
        return (HOST_PROCESS_NOT_LAUNCHED if record.get("process_group") is None
                and "bridge_pid" not in record else HOST_PROCESS_UNATTRIBUTABLE)
    if not hasattr(os, "killpg"):
        if sys.platform == "win32" and "windows_job" in record:
            from .windows_host_job import active_job_processes, valid_job_name
            if not valid_job_name(record["windows_job"]):
                return HOST_PROCESS_UNATTRIBUTABLE
            try:
                active = active_job_processes(record["windows_job"])
            except OSError:
                return HOST_PROCESS_UNATTRIBUTABLE
            return HOST_PROCESS_DRAINING if active else HOST_PROCESS_DRAINED
        # A launched Host on a platform without process groups is never proven
        # drained. This is a platform boundary, not an attribution failure.
        return HOST_PROCESS_UNSUPPORTED_PLATFORM
    bridge, group = record.get("bridge_pid"), record.get("process_group")
    if record.get("phase") != "finished":
        if not isinstance(bridge, int) or bridge <= 1:
            return HOST_PROCESS_UNATTRIBUTABLE
        bridge_present = _process_group_present(bridge)
        if bridge_present is None:
            return HOST_PROCESS_UNATTRIBUTABLE
        if bridge_present:  # the bridge leads its own session
            return HOST_PROCESS_DRAINING
    if group is None:
        # The supervisor left before reporting a group it may already have spawned.
        return HOST_PROCESS_UNATTRIBUTABLE if record.get("phase") == "launching" else HOST_PROCESS_DRAINED
    if not isinstance(group, int) or group <= 1:
        return HOST_PROCESS_UNATTRIBUTABLE
    group_present = _process_group_present(group)
    if group_present is None:
        return HOST_PROCESS_UNATTRIBUTABLE
    return HOST_PROCESS_DRAINING if group_present else HOST_PROCESS_DRAINED


def execution_host_drain(record_path: Path, *, launch_possible: bool = False) -> str:
    """Whether everything an owner launched under one record has exited.

    The owner names one record. A plain run's supervisor writes it for the
    actual Host. A leased run's supervisor owns the private leased CLI, which
    exits on its own clock and supervises the actual Host in another session:
    that supervisor records its group beside the owner's record, and the Host
    the CLI starts writes the owner's record. Both must have exited; an outer
    exit proves nothing about the nested Host. A record that does not say what
    it supervises, such as one written before records carried that fact,
    attributes nothing. The caller supplies whether its launch owner may still
    start a Host; on unsupported platforms an absent record cannot close that
    pre-record launch window. Absence of the primary record is never proof of
    non-execution; the creator records that fact before its first launch.
    """

    # Each side identifies the same leased execution. Either surviving record
    # therefore requires its peer; loss of the outer record cannot erase an
    # independently running CLI while the nested Host has not started or exited.
    owned_record = _read_host_process_record(record_path)
    supervisor_record = _read_host_process_record(host_process_supervisor_record(record_path))
    leased = supervisor_record is not None or (owned_record or {}).get("supervision") == "leased"
    supervisor = _host_process_drain(supervisor_record, supervises=HOST_PROCESS_SUPERVISES_NESTED_HOST,
                                     expected=leased)
    host = _host_process_drain(owned_record, supervises=HOST_PROCESS_SUPERVISES_HOST,
                               expected=True)
    drain = next(state for state in _DRAIN_PRECEDENCE if state in {supervisor, host})
    assigned_windows_job = sys.platform == "win32" and any(
        "windows_job" in item for item in (owned_record or {}, supervisor_record or {})
    )
    if (not assigned_windows_job and not host_process_drain_supported()
            and (launch_possible or drain != HOST_PROCESS_NOT_LAUNCHED)):
        return HOST_PROCESS_UNSUPPORTED_PLATFORM
    return drain


def require_execution_host_drain_supported(drain: str) -> None:
    """Reject cancellation when the Host boundary cannot prove execution drain."""
    if drain == HOST_PROCESS_UNSUPPORTED_PLATFORM:
        raise ValueError(
            "cannot prove the launched Host drained on this platform: "
            "process groups are unavailable, so the Host supervisor is best-effort. "
            "Stop the Host through its own supervisor and re-read its execution state."
        )


class HostOutputLines:
    """Frame LF records without retaining raw trajectories or an unbounded line."""

    def __init__(self, consume: Callable[[str], None], max_chars: int = 1_048_576):
        self.consume = consume
        self.max_chars = max_chars
        self.pending = ""
        self.dropping = False
        self.complete = True

    def feed(self, text: str) -> None:
        pieces = text.split("\n")
        for index, piece in enumerate(pieces):
            if not self.dropping:
                if len(self.pending) + len(piece) > self.max_chars:
                    self.complete = False
                    self.pending = ""
                    self.dropping = True
                else:
                    self.pending += piece
            if index < len(pieces) - 1:
                if not self.dropping:
                    self.consume(self.pending)
                self.pending = ""
                self.dropping = False

    def finish(self) -> None:
        if self.pending and not self.dropping:
            self.consume(self.pending)
        self.pending = ""


def _begin_host_process_record(record_path: Path | None, *, record_value: str,
                               supervises: str, supervision: str, bridge_pid: int) -> dict[str, Any] | None:
    if record_path is None:
        return None
    # Written before the request, so no Host exists that the record
    # does not name; a record that cannot be written launches nothing.
    record = {"schema_version": HOST_PROCESS_RECORD_SCHEMA_VERSION,
              "host": lock_holder_host_label(), "owner_pid": os.getpid(),
              "supervises": supervises, "supervision": supervision, "bridge_pid": bridge_pid,
              "phase": "launching", "process_group": None}
    if supervises == HOST_PROCESS_SUPERVISES_NESTED_HOST:
        # Positive evidence for a CLI that exits before starting its
        # Host. Missing evidence must never mean "not launched".
        # Both writes precede the request that can start the CLI.
        _write_host_process_record(Path(record_value), {
            "schema_version": HOST_PROCESS_RECORD_SCHEMA_VERSION,
            "host": lock_holder_host_label(), "owner_pid": os.getpid(),
            "supervises": HOST_PROCESS_SUPERVISES_HOST, "supervision": "leased",
            "phase": HOST_PROCESS_NOT_LAUNCHED, "process_group": None,
        })
    _write_host_process_record(record_path, record)
    return record


def run_host_process(
    argv: Sequence[str],
    *,
    project: Path,
    input_text: str,
    timeout_seconds: float | None,
    stdout_limit_bytes: int | None = None,
    drain_timeout_seconds: float = 2,
    on_stdout: Callable[[str], None] | None = None,
    on_stderr: Callable[[str], None] | None = None,
    delegated_lease: dict[str, Any] | None = None,
    environment: dict[str, str] | None = None,
    launch_guard: Callable[[], AbstractContextManager[None]] | None = None,
) -> dict[str, Any]:
    """Keep the control pipe open until exit; EOF cancels the owned process group.

    Callbacks observe transient chunks. They must not persist raw Host output.
    TS owns deadlines, byte budgets, termination and the final observation.
    An owning admission guard covers request delivery through spawn observation,
    then releases before Host output; it must never lock the entire Host run.
    """
    command = list(argv)
    if sys.platform == "win32":
        command = [sys.executable, "-c", _WINDOWS_COMMAND_RELAY, *command]
    request: dict[str, Any] = {
        "argv": command,
        "cwd": str(project),
        "input": input_text,
        "timeout_ms": None if timeout_seconds is None else max(1.0, timeout_seconds) * 1000,
        "drain_timeout_ms": drain_timeout_seconds * 1000,
        "stdout_limit_bytes": stdout_limit_bytes,
    }
    if delegated_lease is not None:
        request["delegated_lease"] = delegated_lease
    bridge = Path(__file__).with_name("host_process_bridge.ts")
    bridge_environment, record_value, record_path, supervision, supervises = _bridge_record_scope(environment, delegated_lease)
    launch_budget = max(1.0, timeout_seconds) if timeout_seconds is not None else 30.0
    launch_deadline = time.monotonic() + launch_budget if launch_guard is not None else None
    launch_arguments = (["--guarded-launch", str((time.time() + launch_budget) * 1000)]
                        if launch_guard is not None else [])
    with ExitStack() as containment:
        windows_job = None
        if sys.platform == "win32":
            from .windows_host_job import WindowsHostJob
            windows_job = containment.enter_context(WindowsHostJob())
        proc = subprocess.Popen(
            [
                _node_executable(),
                "--no-warnings",
                "--experimental-strip-types",
                str(bridge),
                *launch_arguments,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="strict",
            start_new_session=True,
            env=bridge_environment,
        )
        assert proc.stdin is not None and proc.stdout is not None
        result: dict[str, Any] | None = None
        record = None
        launch_scope = ExitStack()
        guarded = (GuardedHostBridge(proc, deadline=launch_deadline,
                   cleanup_timeout=8 if delegated_lease is not None else 5)
                   if launch_deadline is not None else None)
        try:
            if windows_job is not None:
                windows_job.assign(proc)
            if launch_guard is not None:
                launch_scope.enter_context(launch_guard())
            record = _begin_host_process_record(record_path, record_value=record_value,
                supervises=supervises, supervision=supervision, bridge_pid=proc.pid)
            if record is not None and windows_job is not None:
                record["windows_job"] = windows_job.name
                _write_host_process_record(record_path, record)
            frame = json.dumps(request, ensure_ascii=False, separators=(",", ":")) + "\n"
            if guarded is not None:
                guarded.send(frame)
            else:
                proc.stdin.write(frame)
                proc.stdin.flush()
            lines = iter(proc.stdout)
            while True:
                event = (guarded.next(deadline=launch_deadline) if guarded is not None
                         else json.loads(line) if (line := next(lines, None)) is not None else None)
                if event is None:
                    break
                kind = event.get("kind")
                if kind == "spawned" and isinstance(event.get("pid"), int):
                    if record is not None:
                        assert record_path is not None
                        group = event.get("process_group")
                        record.update(phase="spawned", host_pid=event["pid"],
                                      process_group=group if isinstance(group, int) else None)
                        _write_host_process_record(record_path, record)
                    launch_deadline = None
                    launch_scope.close()
                elif kind in {"stdout", "stderr"} and isinstance(event.get("text"), str):
                    consume = on_stdout if kind == "stdout" else on_stderr
                    if consume is not None:
                        consume(event["text"])
                elif kind == "result" and event.get("outcome") in {
                    "exited",
                    "timeout",
                    "cancelled",
                    "output_limit",
                    "spawn_failed",
                }:
                    result = event
                    launch_deadline = None
                    launch_scope.close()
                elif kind == "not_launched" and guarded is not None:
                    guarded.not_launched = True
                    raise TimeoutError("managed Host bridge launch deadline expired")
                else:
                    raise RuntimeError("Invalid managed Host process observation")
        finally:
            try:
                if guarded is not None and (launch_deadline is not None or sys.exc_info()[0] is not None):
                    # A failed handoff still owns the Goal fence. The TS bridge must
                    # report no launch or finish its Host cleanup before it releases.
                    guarded.cancel()
                    if record is not None and guarded.not_launched:
                        assert record_path is not None
                        record.update(phase=HOST_PROCESS_NOT_LAUNCHED, process_group=None)
                        record.pop("bridge_pid", None)
                        _write_host_process_record(record_path, record)
                else:
                    # EOF asks the TS supervisor to clean its Host, also on a
                    # callback failure. Leased cleanup retains its original budget.
                    proc.stdin.close()
                    try:
                        proc.wait(timeout=8 if delegated_lease is not None else 5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()
            finally:
                try:
                    if windows_job is not None:
                        windows_job.finish()
                finally:
                    try:
                        launch_scope.close()
                    finally:
                        if guarded is not None:
                            guarded.close_output()
                        else:
                            proc.stdout.close()
        if record is not None and result is not None and proc.returncode == 0:
            assert record_path is not None
            # The supervisor returned only after cleaning its group; the group is still re-read.
            record["phase"] = "finished"
            _write_host_process_record(record_path, record)
        if proc.returncode != 0 or result is None:
            raise RuntimeError("Managed Host process supervision returned no result")
        return result


def _bridge_record_scope(environment, delegated_lease):
    """Separate the supervisor's private record namespace from its child Host."""
    # The bridge runs on the caller's environment, which may pin the release the
    # Host must run and name the record its supervisor owns. Copy it instead of
    # replacing it with the ambient one, and consume the record env from the
    # copy only: the Host below must not inherit a marker for its supervisor's
    # record, and the caller's mapping is never mutated.
    bridge_environment = os.environ.copy() if environment is None else dict(environment)
    record_value = bridge_environment.pop(HOST_PROCESS_RECORD_ENV, "")
    supervision = bridge_environment.pop(HOST_PROCESS_PARENT_ENV, "direct")
    if supervision not in {"direct", "leased"}:
        raise ValueError("invalid managed Host supervision scope")
    record_path = Path(record_value) if record_value else None
    supervises = HOST_PROCESS_SUPERVISES_HOST
    if record_path is not None and delegated_lease is not None:
        # A leased run starts the private leased CLI, which records the actual
        # Host it starts under the owner's record. This supervisor records its
        # own group beside that record, so readback can prove both exited.
        record_path = host_process_supervisor_record(record_path)
        supervises = HOST_PROCESS_SUPERVISES_NESTED_HOST
        supervision = "leased"
    return bridge_environment, record_value, record_path, supervision, supervises
