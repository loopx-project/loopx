"""Pause a Docker worker and seal its prepared files with native state.

The benchmark supplies the native capture operation. Docker owns the writer
barrier, and the typed artifact owner verifies and seals the copied bytes.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import closing
from copy import copy
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
from benchmark.runtime.checkpoint_handoff import controller_completion_deadline

from .scheduler import read_phase_deadline


def _physical(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Controller paths must be normalized, physical and absolute")
    return path


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Checkpoint capture window closed")
    return remaining


_COMPONENT_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")


def capture_failure_fields(error: Exception) -> dict[str, str]:
    """Keep a bounded diagnostic for a declared component's failed Docker read."""
    fields = {"error_kind": type(error).__name__}
    component_id = getattr(error, "checkpoint_component_id", None)
    if isinstance(component_id, str) and _COMPONENT_ID.fullmatch(component_id):
        from docker.errors import NotFound

        if isinstance(error, NotFound):
            fields["missing_component_id"] = component_id
    return fields


def qualify_export_storage(destination: Path) -> None:
    """Check filename fidelity before registration, without reserving a run.

    Linux source trees can contain case-only or Unicode-normalization-only
    siblings. A shared host volume may silently merge these during extraction
    or sealing, even when the controller itself runs on Linux.
    """
    if not destination.is_absolute() or destination.resolve() != destination:
        raise ValueError("Checkpoint export destination must be normalized, physical and absolute")
    if destination.exists():
        raise ValueError("Checkpoint export destination must be fresh; preserve the existing bank")
    parent = destination.parent
    while not parent.exists():
        parent = parent.parent
    _physical(parent)
    names = ("Candidate", "candidate", "caf\u00e9", "cafe\u0301")
    with tempfile.TemporaryDirectory(prefix=".checkpoint-storage-", dir=parent) as directory:
        root = Path(directory)
        try:
            for index, name in enumerate(names):
                with (root / name).open("xb") as stream:
                    stream.write(bytes([index]))
        except FileExistsError as error:
            raise ValueError("Checkpoint export storage must preserve case and Unicode filename distinctions; "
                             "select a compatible volume with --checkpoint-export-root") from error
        if (set(entry.name for entry in root.iterdir()) != set(names) or
                any((root / name).read_bytes() != bytes([index]) for index, name in enumerate(names))):
            raise ValueError("Checkpoint export storage changed filename identities or bytes")


def validate_worker_request(value: dict, *, allow_open_phase: bool = False) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Worker request requires a full container ID, explicit components and phase_deadline_path")
    # Snapshot caller-owned mutable containers before validating so concurrent
    # mutations cannot pair checked fields with different later values.
    raw = {key: item for key, item in dict.items(value)}
    expected_keys = {"container_id", "components", "phase_deadline_path"}
    if set(raw) not in (expected_keys, expected_keys | {"handoff"}):
        raise ValueError("Worker request requires a full container ID, explicit components and phase_deadline_path")
    container_id = raw["container_id"]
    raw_components = raw["components"]
    if (not isinstance(container_id, str) or not re.fullmatch(r"[0-9a-f]{64}", container_id) or
            not isinstance(raw_components, list) or not raw_components):
        raise ValueError("Worker request requires a full container ID, explicit components and phase_deadline_path")
    raw_components = list(raw_components)
    ids: set[str] = set()
    paths: list[PurePosixPath] = []
    components: list[dict[str, str]] = []
    for item in raw_components:
        if not isinstance(item, dict):
            raise ValueError("Worker components require unique IDs")
        entry = {key: val for key, val in dict.items(item)}
        if (set(entry) != {"id", "source_path"} or
                not isinstance(entry["id"], str) or
                not _COMPONENT_ID.fullmatch(entry["id"]) or entry["id"] in ids):
            raise ValueError("Worker components require unique IDs")
        component_id = str(entry["id"])
        source_path = entry["source_path"]
        ids.add(component_id)
        paths.append(_guest_path(source_path))
        components.append({"id": component_id, "source_path": str(source_path)})
    phase_deadline_path = raw["phase_deadline_path"]
    if phase_deadline_path is not None or not allow_open_phase:
        paths.append(_guest_path(phase_deadline_path))
    snapshot: dict = {
        "container_id": str(container_id),
        "components": components,
        "phase_deadline_path": None if phase_deadline_path is None else str(phase_deadline_path),
    }
    if "handoff" in raw:
        raw_handoff = raw["handoff"]
        if not isinstance(raw_handoff, dict):
            raise ValueError("Worker handoff requires the exact private request digest")
        handoff = {key: val for key, val in dict.items(raw_handoff)}
        sha256 = handoff.get("sha256")
        if (set(handoff) != {"source_path", "sha256"} or
                not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)):
            raise ValueError("Worker handoff requires the exact private request digest")
        handoff_source = handoff["source_path"]
        paths.append(_guest_path(handoff_source))
        snapshot["handoff"] = {"source_path": str(handoff_source), "sha256": str(sha256)}
    if any(a == b or a in b.parents or b in a.parents
           for index, a in enumerate(paths) for b in paths[index + 1:]):
        raise ValueError("Worker components and phase clock must not overlap")
    return snapshot


def _guest_path(value: str) -> PurePosixPath:
    if not isinstance(value, str):
        raise ValueError("Worker paths must be normalized absolute paths below root")
    path = PurePosixPath(value)
    if not path.is_absolute() or str(path) != value or value == "/" or ".." in path.parts:
        raise ValueError("Worker paths must be normalized absolute paths below root")
    return path


class _ArchiveReader:
    """Check the shared clock during tar headers and large-file copy reads."""
    def __init__(self, raw, deadline):
        self.raw, self.deadline = raw, deadline

    def __getattr__(self, name):
        return getattr(self.raw, name)

    def read(self, *args):
        _remaining(self.deadline)
        block = self.raw.read(*args)
        _remaining(self.deadline)
        return block


def _archive(container, remote: str, destination: Path, *, deadline: float | None = None) -> Path:
    """Read through the daemon, rejecting symlinks/devices and archive traversal.

    Docker's archive reader works while paused, unlike exec-based file readers.
    Keep only the declared object, not parent filesystem or process credentials.
    """
    from docker.utils import decode_json_header

    def check():
        if deadline is not None:
            _remaining(deadline)

    check()
    # The SDK's get_archive hides its Response behind a lazy generator. Closing
    # that generator alone need not close an unread HTTP body. Own the response
    # so every early exit releases the daemon reader before writer cleanup.
    api = container.client.api
    response = api._get(api._url("/containers/{0}/archive", container.id),
        params={"path": remote}, stream=True, headers={"Accept-Encoding": "identity"},
        **({"timeout": _remaining(deadline)} if deadline is not None else {}))
    with closing(response):
        check()
        api._raise_for_status(response)
        stat = decode_json_header(response.headers["x-docker-container-path-stat"])
        if stat.get("linkTarget"):
            raise ValueError("Worker component must not be a symlink")
        destination.mkdir(mode=0o700)
        # The explicitly provisioned export volume owns both the raw transfer
        # and extracted files; a full ambient /tmp must not defeat that choice.
        with tempfile.TemporaryFile(dir=destination) as raw:
            # _stream_raw_result is Docker's long-running-process reader: it
            # disables socket timeouts. Archive IO must keep the request's read
            # timeout as well as check the shared absolute clock on progress.
            for chunk in response.iter_content(64 * 1024, decode_unicode=False):
                check()
                raw.write(chunk)
            check()
            raw.seek(0)
            with tarfile.open(fileobj=_ArchiveReader(raw, deadline) if deadline is not None else raw) as archive:
                members = []
                root = PurePosixPath(remote).name
                names = {}
                for member in archive:
                    check()
                    path = PurePosixPath(member.name)
                    if (path.is_absolute() or ".." in path.parts or not path.parts or
                            path.parts[0] != root or str(path) in names or
                            not (member.isfile() or member.isdir() or member.islnk())):
                        raise ValueError("Worker archive contains undeclared, linked or special members")
                    names[str(path)] = member
                    members.append(member)

                # The typed artifact owner observes regular files, not inode
                # sharing. Docker encodes a second name for the same inode as a
                # tar hardlink (common in build outputs). Materialize its bytes
                # from an inventoried regular member; never resolve a link on
                # the host filesystem or permit a link chain/symlink escape.
                extracted = []
                for member in members:
                    check()
                    if member.islnk():
                        target_path = PurePosixPath(member.linkname)
                        target = names.get(str(target_path))
                        if (target_path.is_absolute() or ".." in target_path.parts or
                                target is None or not target.isfile()):
                            raise ValueError("Worker archive contains undeclared, linked or special members")
                        regular = copy(target)
                        regular.name, regular.mode = member.name, member.mode
                        extracted.append(regular)
                    else:
                        extracted.append(member)

                def filtered(member, path):
                    check()
                    result = tarfile.data_filter(member, path)
                    check()
                    return result

                archive.extractall(destination, members=extracted, filter=filtered)
                # The data filter intentionally drops directory modes. This is a
                # private raw transport witness, so restore only ordinary permission
                # bits after safe extraction; never restore owners or special bits.
                # Otherwise a private 0700 authority directory becomes 0755 and a
                # matching frozen source is falsely rejected as having changed.
                for member in sorted(members, key=lambda entry: len(PurePosixPath(entry.name).parts), reverse=True):
                    check()
                    (destination / member.name).chmod(member.mode & 0o777)
    check()
    return _physical(destination / root)


def _archive_component(container, remote: str, destination: Path, *, deadline: float | None = None) -> Path:
    """Transport a standalone SQLite participant's committed storage too.

    A paused process can still have committed data only in its WAL. Reading
    the main file alone silently exports an older, internally valid database.
    The typed artifact owner's SQLite backup still decides the committed
    snapshot; this Docker adapter only transports its physical input while
    the same writer barrier is held. A directory archive already includes it.
    """
    from docker.errors import NotFound

    copied = _archive(container, remote, destination, deadline=deadline)
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
                companion = _archive(container, remote + suffix, Path(stage) / "member", deadline=deadline)
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


def capture_docker_worker(
    *, capture_id: str, destination: Path, request: dict, native_bank: Path,
    capture_native: Callable[[Path, Path | None, float], dict],
    timeout: float = 30, seal_within_phase: bool = False, allow_open_phase: bool = False,
    components_at_barrier: Callable | None = None, docker_host: str | None = None,
) -> dict:
    """Freeze the selected worker, check its handoff and seal one fresh point.

    The callback runs while the exact worker is paused. It must materialize its
    native capsule at the given path and return its verified artifact digest.
    Capture is bounded even when the source has no solver deadline.
    """
    import docker

    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Worker capture timeout must be finite and positive")
    deadline = time.monotonic() + timeout
    snapshot = validate_worker_request(request, allow_open_phase=allow_open_phase)
    container_id = snapshot["container_id"]
    requested_components = snapshot["components"]
    phase_deadline_path = snapshot["phase_deadline_path"]
    handoff = snapshot.get("handoff")
    if seal_within_phase and handoff is None:
        raise ValueError("Post-release sealing requires an exact prepared handoff")
    if components_at_barrier is not None and handoff is not None:
        raise ValueError("Terminal phase discovery cannot change a prepared handoff")
    native_bank = _physical(native_bank)
    destination = Path(destination)
    _physical(destination.parent)
    if (destination.resolve() != destination or destination.exists() or destination.is_symlink() or
            not re.fullmatch(r"[0-9a-f]{32}", capture_id)):
        raise ValueError("Worker capture requires a fresh physical destination and 32-hex identity")
    if (native_bank / capture_id).exists():
        raise ValueError("Existing native point cannot acquire later worker files")
    qualify_export_storage(destination)
    attempts = native_bank / ".loopx-worker-attempts"
    attempts.mkdir(exist_ok=True, mode=0o700)
    _physical(attempts)
    (attempts / capture_id).mkdir(mode=0o700)  # First attempt, including failures, wins.
    client = (
        docker.from_env(timeout=_remaining(deadline)) if docker_host is None
        else docker.DockerClient(base_url=docker_host, timeout=_remaining(deadline))
    )
    try:
        with exclusive_file_lock(_worker_lock(client, container_id), timeout_seconds=_remaining(deadline),
                                 operation="checkpoint_worker_capture"):
            container = client.containers.get(container_id)
            container.reload()
            state = container.attrs["State"]
            if container.id != container_id or not state["Running"] or state["Paused"]:
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
                    if components_at_barrier is not None:
                        barrier_components = components_at_barrier(
                            container, [dict(entry) for entry in requested_components], stage, deadline=deadline)
                        snapshot = validate_worker_request({
                            "container_id": container_id,
                            "components": barrier_components,
                            "phase_deadline_path": phase_deadline_path,
                        }, allow_open_phase=allow_open_phase)
                        requested_components = snapshot["components"]
                    if handoff is not None:
                        held = _archive(container, handoff["source_path"], stage / "held", deadline=deadline)
                        raw = held.read_bytes()
                        if hashlib.sha256(raw).hexdigest() != handoff["sha256"]:
                            raise ValueError("Worker handoff changed before its writer barrier")
                        prepared = json.loads(raw)
                        if (set(prepared) != {"capture_id", "components", "point_metadata", "source_sha256", "source_witness", "deadline_epoch"} or
                                prepared["capture_id"] != capture_id or prepared["components"] != requested_components or
                                not isinstance(prepared["point_metadata"], dict) or not isinstance(prepared["source_witness"], dict) or
                                type(prepared["deadline_epoch"]) not in (int, float) or
                                not math.isfinite(prepared["deadline_epoch"]) or time.time() >= prepared["deadline_epoch"]):
                            raise ValueError("Worker handoff is expired or differs from the original capture")
                    if prepared is not None:
                        deadline = min(deadline, time.monotonic() + prepared["deadline_epoch"] - time.time())
                    for entry in requested_components:
                        client.api.timeout = _remaining(deadline)
                        try:
                            copied = _archive_component(
                                container, entry["source_path"], stage / "workers" / entry["id"], deadline=deadline)
                        except docker.errors.NotFound as error:
                            # Keep the provider exception for direct capture callers.
                            error.checkpoint_component_id = entry["id"]
                            raise
                        components.append({"id": entry["id"], "source_path": str(copied)})
                    if prepared is not None:
                        observed = effect_runtime_result("runtime.checkpoint_artifacts", {
                            "action": "probe", "components": components,
                        }, timeout=_remaining(deadline), retry_safe=False, large_local_snapshot=True)
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
                    phase = _archive(container, phase_deadline_path, stage / "phase", deadline=deadline) if phase_deadline_path else None
                    native_root = stage / "native"
                    native = capture_native(native_root, phase, _remaining(deadline))
                    if not isinstance(native, dict):
                        raise ValueError("Native capture callback must return a verified checkpoint receipt")
                    raw_native = {key: value for key, value in dict.items(native)}
                    raw_native_sha256 = raw_native.get("sha256")
                    if (type(raw_native_sha256) is not str or
                            not re.fullmatch(r"[0-9a-f]{64}", raw_native_sha256) or
                            raw_native.get("integrity_verified") is not True):
                        raise ValueError("Native capture receipt must carry an integrity-verified 64-hex digest")
                    # Copy the callback digest into an immutable string at the
                    # barrier before unpausing or sealing touches shared state.
                    native_capsule_sha256 = str(raw_native_sha256)
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
                    # The released source is no longer read. The same capture
                    # request owns the sealing window and any original phase.
                    phase_end = read_phase_deadline(phase.read_bytes()) if phase is not None else None
                    completion = controller_completion_deadline(prepared["deadline_epoch"], phase_end)
                    deadline = time.monotonic() + completion - time.time()
                verified_native = effect_runtime_result(
                    "runtime.checkpoint_artifacts",
                    {"action": "verify", "root": str(native_root), "expected_sha256": native_capsule_sha256},
                    timeout=_remaining(deadline), retry_safe=False, large_local_snapshot=True,
                )
                if (not isinstance(verified_native, dict) or
                        verified_native.get("integrity_verified") is not True or
                        verified_native.get("sha256") != native_capsule_sha256):
                    raise ValueError("Materialized native capsule does not match the barrier callback receipt")
                sealing_epoch_ms = (time.time() + _remaining(deadline)) * 1000
                native_component = _participant_id(components, "native_capsule")
                components.append({"id": native_component, "source_path": str(native_root)})
                metadata = {**(prepared["point_metadata"] if prepared is not None else {}), "worker_file_snapshot": {
                    "container_id": container_id, "image_id": container.attrs["Image"],
                    "components": [dict(entry) for entry in requested_components],
                    "phase_deadline_path": phase_deadline_path,
                    "native_capsule_sha256": native_capsule_sha256,
                    "native_capsule_component_id": native_component,
                    "writer_barrier": "docker_pause", "process_memory_captured": False,
                    "native_cursor_qualified": False,
                    **({"handoff_sha256": handoff["sha256"],
                        "prepared_source_verified": True} if prepared is not None else {}),
                }}
                if prepared is not None:
                    metadata["writer_barrier"] = "docker_pause"
                probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components,
                    "deadline_epoch_ms": sealing_epoch_ms},
                                              timeout=_remaining(deadline), retry_safe=False, large_local_snapshot=True)
                return effect_runtime_result("runtime.checkpoint_artifacts", {
                    "action": "capture", "components": components, "destination": str(destination),
                    "expected_source_sha256": probe["source_sha256"], "point_metadata": metadata,
                    "deadline_epoch_ms": sealing_epoch_ms,
                }, timeout=_remaining(deadline), retry_safe=False, large_local_snapshot=True)
    finally:
        client.close()
