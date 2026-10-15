"""Explicit controller-owned Docker writer barrier for inert checkpoint export.

This provider adapts Docker pause/archive; the existing native and typed artifact
owners still own evaluator state and sealing. No code runs inside a frozen worker.
It does not capture process memory or qualify a native conversation cursor.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tarfile
import tempfile
import time
from pathlib import Path, PurePosixPath

from loopx.control_plane.effect_runtime import effect_runtime_result
from loopx.file_lock import exclusive_file_lock
from benchmark.runtime.checkpoint import _participant_id

from .checkpoint_controller import _controller, _physical, _remaining, _phase_budget, capture


def _request(path: Path) -> dict:
    value = json.loads(_physical(path).read_bytes())
    if (not isinstance(value, dict) or
            set(value) not in ({"container_id", "components", "phase_deadline_path"},
                              {"container_id", "components", "phase_deadline_path", "handoff"}) or
            not re.fullmatch(r"[0-9a-f]{64}", value.get("container_id", "")) or
            not isinstance(value["components"], list) or not value["components"]):
        raise ValueError("Worker request requires a full container ID, explicit components and phase_deadline_path")
    ids = set()
    paths = []
    for entry in value["components"]:
        if (not isinstance(entry, dict) or set(entry) != {"id", "source_path"} or
                not isinstance(entry["id"], str) or
                not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,63}", entry["id"]) or entry["id"] in ids):
            raise ValueError("Worker components require unique IDs")
        ids.add(entry["id"])
        paths.append(_guest_path(entry["source_path"]))
    paths.append(_guest_path(value["phase_deadline_path"]))
    if "handoff" in value:
        handoff = value["handoff"]
        if (not isinstance(handoff, dict) or set(handoff) != {"source_path", "sha256"} or
                not re.fullmatch(r"[0-9a-f]{64}", handoff.get("sha256", ""))):
            raise ValueError("Worker handoff requires the exact private request digest")
        paths.append(_guest_path(handoff["source_path"]))
    if any(a == b or a in b.parents or b in a.parents
           for index, a in enumerate(paths) for b in paths[index + 1:]):
        raise ValueError("Worker components and phase clock must not overlap")
    return value


def _guest_path(value: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("Worker paths must be normalized absolute paths below root")
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or value == "/" or ".." in path.parts:
        raise ValueError("Worker paths must be normalized absolute paths below root")
    return path


def _archive(container, remote: str, destination: Path) -> Path:
    """Read through the daemon, rejecting links/devices and archive traversal.

    Docker's archive reader works while paused, unlike exec-based file readers.
    Keep only the declared object, not parent filesystem or process credentials.
    """
    stream, stat = container.get_archive(remote)
    if stat.get("linkTarget"):
        raise ValueError("Worker component must not be a symlink")
    destination.mkdir(mode=0o700)
    with tempfile.TemporaryFile() as raw:
        for chunk in stream:
            raw.write(chunk)
        raw.seek(0)
        with tarfile.open(fileobj=raw) as archive:
            members = archive.getmembers()
            root = PurePosixPath(remote).name
            names = set()
            for member in members:
                path = PurePosixPath(member.name)
                if (path.is_absolute() or ".." in path.parts or not path.parts or
                        path.parts[0] != root or str(path) in names or
                        not (member.isfile() or member.isdir())):
                    raise ValueError("Worker archive contains undeclared, linked or special members")
                names.add(str(path))
            archive.extractall(destination, members=members, filter="data")
            # The data filter intentionally drops directory modes. This is a
            # private raw transport witness, so restore only ordinary permission
            # bits after safe extraction; never restore owners or special bits.
            # Otherwise a private 0700 authority directory becomes 0755 and a
            # matching frozen source is falsely rejected as having changed.
            for member in sorted(members, key=lambda entry: len(PurePosixPath(entry.name).parts), reverse=True):
                (destination / member.name).chmod(member.mode & 0o777)
    return _physical(destination / root)


def _archive_component(container, remote: str, destination: Path) -> Path:
    """Transport a standalone SQLite participant's committed storage too.

    A paused process can still have committed data only in its WAL. Reading
    the main file alone silently exports an older, internally valid database.
    The typed artifact owner's SQLite backup still decides the committed
    snapshot; this Docker adapter only transports its physical input while
    the same writer barrier is held. A directory archive already includes it.
    """
    from docker.errors import NotFound

    copied = _archive(container, remote, destination)
    if not copied.is_file():
        return copied
    with copied.open("rb") as source:
        if source.read(16) != b"SQLite format 3\0":
            return copied
    # SHM is a reader index, rebuilt locally. WAL and rollback journals can
    # affect committed state and must not be dropped or treated as optional
    # when transport, permissions or link validation fails.
    for suffix in ("-wal", "-journal"):
        with tempfile.TemporaryDirectory(prefix=".sqlite-transport-", dir=destination) as stage:
            try:
                companion = _archive(container, remote + suffix, Path(stage) / "member")
            except NotFound:
                continue
            if not companion.is_file():
                raise ValueError("SQLite companion must be a physical regular file")
            companion.rename(copied.with_name(copied.name + suffix))
    return copied


def _worker_lock(client, container_id: str) -> Path:
    # Controller banks and endpoint aliases may differ for the same daemon.
    # Coordinate captures by this user on this host, not by artifact location.
    daemon_id = client.info().get("ID")
    if not isinstance(daemon_id, str) or not daemon_id:
        raise ValueError("Docker daemon identity is required for worker capture")
    root = Path.home() / ".cache" / "loopx" / "checkpoint-workers"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    _physical(root)
    info = root.stat()
    if info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("Worker capture lock directory must be private and owned by the current user")
    daemon_key = hashlib.sha256(daemon_id.encode()).hexdigest()
    return root / f"{daemon_key}-{container_id}"


def capture_worker(controller_file: Path, *, token: str, capture_id: str,
                   destination: Path, request_file: Path, timeout: float = 30,
                   seal_within_phase: bool = False) -> dict:
    """Freeze one explicitly owned worker and seal its files with native state.

    A fresh identity is required. Failed or uncertain attempts never recapture a
    later worker into an earlier evaluator point. The original worker is resumed
    before publishing the sealed result; an unsuccessful unpause is an error.
    The supervisor can explicitly seal the private exported files after release
    within their original Host phase. The prepared deadline still bounds every
    live-source/native capture step; ordinary callers retain the total timeout.
    """
    import docker

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Worker capture timeout must be finite and positive")
    deadline = time.monotonic() + timeout
    request = _request(request_file)
    if seal_within_phase and "handoff" not in request:
        raise ValueError("Post-release sealing requires an exact prepared handoff")
    controller = _controller(controller_file)
    destination = Path(destination)
    _physical(destination.parent)
    if (destination.resolve() != destination or destination.exists() or destination.is_symlink() or
            not re.fullmatch(r"[0-9a-f]{32}", capture_id)):
        raise ValueError("Worker capture requires a fresh physical destination and 32-hex identity")
    if (controller["bank_root"] / capture_id).exists():
        raise ValueError("Existing native point cannot acquire later worker files")
    attempts = controller["bank_root"] / ".loopx-worker-attempts"
    attempts.mkdir(exist_ok=True, mode=0o700)
    _physical(attempts)
    (attempts / capture_id).mkdir(mode=0o700)  # First attempt, including failures, wins.
    client = docker.from_env(timeout=_remaining(deadline))
    try:
        with exclusive_file_lock(_worker_lock(client, request["container_id"]), timeout_seconds=_remaining(deadline),
                                 operation="checkpoint_worker_capture"):
            container = client.containers.get(request["container_id"])
            container.reload()
            state = container.attrs["State"]
            if container.id != request["container_id"] or not state["Running"] or state["Paused"]:
                raise ValueError("Worker must be the exact running, unpaused container owned by this controller")
            with tempfile.TemporaryDirectory(prefix=".worker-capture-", dir=destination.parent) as directory:
                stage = Path(directory)
                (stage / "workers").mkdir(mode=0o700)
                components = []
                prepared = None
                pause_may_have_committed = True
                try:
                    try:
                        container.pause()
                    except docker.errors.APIError as exc:
                        # Docker's conflict response rejects this pause. The
                        # observed pause belongs to another actor; leave it alone.
                        if exc.response is not None and exc.response.status_code == 409:
                            pause_may_have_committed = False
                        raise
                    container.reload()
                    if not container.attrs["State"]["Paused"]:
                        raise RuntimeError("Worker writer barrier was not established")
                    if "handoff" in request:
                        held = _archive(container, request["handoff"]["source_path"], stage / "held")
                        raw = held.read_bytes()
                        if hashlib.sha256(raw).hexdigest() != request["handoff"]["sha256"]:
                            raise ValueError("Worker handoff changed before its writer barrier")
                        prepared = json.loads(raw)
                        if (set(prepared) != {"capture_id", "components", "point_metadata", "source_sha256", "source_witness", "deadline_epoch"} or
                                prepared["capture_id"] != capture_id or prepared["components"] != request["components"] or
                                not isinstance(prepared["point_metadata"], dict) or not isinstance(prepared["source_witness"], dict) or
                                type(prepared["deadline_epoch"]) not in (int, float) or
                                not math.isfinite(prepared["deadline_epoch"]) or time.time() >= prepared["deadline_epoch"]):
                            raise ValueError("Worker handoff is expired or differs from the original capture")
                    if prepared is not None:
                        deadline = min(deadline, time.monotonic() + prepared["deadline_epoch"] - time.time())
                    for entry in request["components"]:
                        client.api.timeout = _remaining(deadline)
                        copied = _archive_component(container, entry["source_path"], stage / "workers" / entry["id"])
                        components.append({"id": entry["id"], "source_path": str(copied)})
                    if prepared is not None:
                        observed = effect_runtime_result("runtime.checkpoint_artifacts", {
                            "action": "probe", "components": components,
                        }, timeout=_remaining(deadline), retry_safe=False)
                        if observed["source_sha256"] != prepared["source_sha256"]:
                            changed = []
                            for key, rows in observed["source_witness"].items():
                                before = {row["path"]: row for row in prepared["source_witness"].get(key, [])}
                                after = {row["path"]: row for row in rows}
                                changed.extend(key + ":" + path for path in sorted(before.keys() | after.keys())
                                               if before.get(path) != after.get(path))
                            raise ValueError("Worker source changed after native/research preparation: " + ", ".join(changed))
                        if time.time() >= prepared["deadline_epoch"]:
                            raise TimeoutError("Worker handoff expired before evaluator capture")
                    phase = _archive(container, request["phase_deadline_path"], stage / "phase")
                    native = capture(controller_file, token=token, capture_id=capture_id,
                        destination=stage / "native", timeout=_remaining(deadline), phase_deadline_file=phase)
                    container.reload()
                    if not container.attrs["State"]["Running"] or not container.attrs["State"]["Paused"]:
                        raise RuntimeError("Worker writer barrier changed during capture")
                    _remaining(deadline)  # Late native/barrier completion cannot gain sealing time.
                finally:
                    # An uncertain/lost reply keeps this original attempt's
                    # cleanup obligation. Its identity cannot be retried later.
                    if pause_may_have_committed:
                        # Cleanup retains a bounded transport allowance even if
                        # the capture window is exhausted; never strand our writer.
                        client.api.timeout = timeout
                        container.reload()
                        if container.attrs["State"]["Paused"]:
                            container.unpause()
                            container.reload()
                        if container.attrs["State"]["Paused"] or not container.attrs["State"]["Running"]:
                            raise RuntimeError("Worker was not restored to running; inspect the original container before retry")
                if seal_within_phase:
                    # Only immutable controller-owned exports remain. Do not
                    # spend the held-decision window hashing them after source
                    # release, or grant a new trial budget/cooldown. Reuse the
                    # original phase owner and its captured physical clock.
                    remaining_phase = _phase_budget(phase.read_bytes(), {
                        "captured_at_epoch": time.time()})["remaining_seconds"]
                    deadline = time.monotonic() + remaining_phase
                sealing_epoch_ms = (time.time() + _remaining(deadline)) * 1000
                native_component = _participant_id(components, "native_capsule")
                components.append({"id": native_component, "source_path": str(stage / "native")})
                metadata = {**(prepared["point_metadata"] if prepared is not None else {}), "worker_file_snapshot": {
                    "container_id": container.id, "image_id": container.attrs["Image"],
                    "components": request["components"], "phase_deadline_path": request["phase_deadline_path"],
                    "native_capsule_sha256": native["sha256"],
                    "native_capsule_component_id": native_component,
                    "writer_barrier": "docker_pause", "process_memory_captured": False,
                    "native_cursor_qualified": False,
                    **({"handoff_sha256": request["handoff"]["sha256"],
                        "prepared_source_verified": True} if prepared is not None else {}),
                }}
                if prepared is not None:
                    metadata["writer_barrier"] = "docker_pause"
                probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components,
                    "deadline_epoch_ms": sealing_epoch_ms},
                                              timeout=_remaining(deadline), retry_safe=False)
                return effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "capture", "components": components, "destination": str(destination),
                    "expected_source_sha256": probe["source_sha256"], "point_metadata": metadata,
                    "deadline_epoch_ms": sealing_epoch_ms,
                }, timeout=_remaining(deadline), retry_safe=False)
    finally:
        client.close()
