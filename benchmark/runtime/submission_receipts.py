"""Publish native acceptance identities for an explicit submission recovery run.

SForge owns admission, evaluation and results. This transport helper only joins
token-visible submission history to the exact archive retained by the native
host. It never submits, reads results, unpacks archives or drives LoopX work.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import threading
import uuid

SCHEMA = "edgebench_native_submission_receipts_v1"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ROUND = re.compile(r"agent-[1-9][0-9]*\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def write_bytes_atomic(path: Path, raw: bytes, *, create_only=False):
    """Persist one receipt before reporting the associated fact."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if create_only:
            os.link(temporary, path)
        else:
            temporary.replace(path)
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        temporary.unlink(missing_ok=True)


def _signature(row):
    return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns


def _archive_stat(path: Path, root: Path):
    if not path.is_relative_to(root) or path.resolve(strict=True) != path:
        raise ValueError("native_archive_not_physical")
    current = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or current.st_size > MAX_ARCHIVE_BYTES:
        raise ValueError("native_archive_size_or_type")
    return current


def read_archive(path: Path, *, root: Path) -> bytes:
    """Read one physical source file, rejecting aliases and movement in capture."""
    root, path = root.resolve(strict=True), path.absolute()
    _archive_stat(path, root)
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("native_archive_size_or_type")
        raw = stream.read(MAX_ARCHIVE_BYTES + 1)
        after = os.fstat(stream.fileno())
    current = _archive_stat(path, root)
    if (_signature(before) != _signature(after) or _signature(after) != _signature(current)
            or len(raw) != before.st_size):
        raise ValueError("native_archive_moved")
    return raw


def _matches(pattern, value):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def validate_packet(packet):
    """Reject malformed or ambiguous matchable identities at the guest boundary."""
    if (not isinstance(packet, dict)
            or set(packet) != {"schema_version", "run_id", "task_id", "records"}
            or packet["schema_version"] != SCHEMA
            or not _matches(_ID, packet["run_id"]) or not _matches(_ID, packet["task_id"])
            or not isinstance(packet["records"], list)):
        raise ValueError("invalid_native_submission_receipts")
    fields = {"accepted_record", "submission_id", "round_id", "archive_sha256", "archive_bytes", "reason"}
    for row in packet["records"]:
        if (not isinstance(row, dict) or not set(row) <= fields
                or not isinstance(row.get("accepted_record"), bool)
                or not _matches(_ID, row.get("submission_id"))
                or not _matches(_ROUND, row.get("round_id"))):
            raise ValueError("invalid_native_submission_receipt")
        if "archive_sha256" in row or "archive_bytes" in row:
            if (row["accepted_record"] is not True or "reason" in row
                    or not _matches(_SHA256, row.get("archive_sha256"))
                    or type(row.get("archive_bytes")) is not int
                    or not 0 <= row["archive_bytes"] <= MAX_ARCHIVE_BYTES):
                raise ValueError("invalid_native_submission_archive_receipt")
        elif not _matches(_ID, row.get("reason")):
            raise ValueError("native_submission_receipt_has_no_archive_or_reason")
    ids = Counter(row["submission_id"] for row in packet["records"])
    rounds = Counter(row["round_id"] for row in packet["records"])
    for row in packet["records"]:
        if (ids[row["submission_id"]] > 1 or rounds[row["round_id"]] > 1) and row["accepted_record"]:
            raise ValueError("ambiguous_native_submission_receipt")
    return packet


class NativeSubmissionReceipts:
    """Publish a small acceptance packet; the guest uses native result retrieval."""

    def __init__(self, *, trial: Path, run_id: str, task_id: str, judge_url: str,
                 remote_root: str, logger=None, session=None, poll_seconds: float = 5):
        if not _matches(_ID, run_id) or not _matches(_ID, task_id) or poll_seconds <= 0:
            raise ValueError("invalid_native_submission_scope")
        self.trial, self.run_id, self.task_id = Path(trial).resolve(), run_id, task_id
        self.directory = self.trial / "native-submission-recovery"
        self.judge_url, self.remote_root = judge_url.rstrip("/"), PurePosixPath(remote_root)
        self.logger, self.poll_seconds = logger, poll_seconds
        self.token, self.thread = None, None
        self._stop, self._active = threading.Event(), threading.Event()
        self._lock, self._published = threading.RLock(), None
        self._archive_cache, self._bound_rounds = {}, {}
        previous = self.directory / "native.json"
        if previous.exists():
            packet = validate_packet(json.loads(previous.read_bytes()))
            if (packet["run_id"], packet["task_id"]) != (run_id, task_id):
                raise ValueError("native_submission_saved_scope_mismatch")
        for path in (self.directory / "bindings").glob("*.json"):
            binding = json.loads(path.read_bytes())
            row = {key: value for key, value in binding.items() if key not in {"run_id", "task_id"}}
            validate_packet(dict(schema_version=SCHEMA, run_id=binding.get("run_id"),
                                 task_id=binding.get("task_id"), records=[dict(accepted_record=True, **row)]))
            if ((binding["run_id"], binding["task_id"]) != (run_id, task_id)
                    or binding["submission_id"] != path.stem
                    or binding["round_id"] in self._bound_rounds):
                raise ValueError("native_submission_saved_binding_mismatch")
            self._bound_rounds[binding["round_id"]] = binding["submission_id"]
        if session is None:
            import requests
            session = requests.Session()
            session.trust_env = False  # Host path to its declared native judge.
        self.session = session

    def bind(self, environment):
        token = (environment or {}).get("SFORGE_TOKEN")
        if not isinstance(token, str) or not token or (self.token is not None and self.token != token):
            raise ValueError("native_submission_registration_mismatch")
        self.token = token

    def poll(self):
        with self._lock:
            return self._poll()

    def _poll(self):
        if self.token is None:
            raise ValueError("native_submission_registration_missing")
        response = self.session.get(self.judge_url + "/api/v1/history",
                                    params={"token": self.token}, timeout=(3, 5))
        response.raise_for_status()
        history = response.json()
        if (not isinstance(history, dict) or history.get("run_id") != self.run_id
                or not isinstance(history.get("entries"), list)
                or any(not isinstance(row, dict) for row in history["entries"])):
            raise ValueError("native_submission_history_scope_mismatch")
        entries = [row for row in history["entries"] if row.get("type") == "submission"
                   and not str(row.get("round", "")).startswith("auto-")]
        for entry in entries:
            if entry.get("task_id") != self.task_id:
                raise ValueError("native_submission_task_scope_mismatch")
            if not _matches(_ID, entry.get("submission_id")) or not _matches(_ROUND, entry.get("round")):
                raise ValueError("native_submission_identity_invalid")
        ids, rounds = Counter(row["submission_id"] for row in entries), Counter(row["round"] for row in entries)
        records = []
        for entry in entries:
            sid, round_id = entry["submission_id"], entry["round"]
            row = dict(accepted_record=True, submission_id=sid, round_id=round_id)
            records.append(row)
            if ids[sid] != 1 or rounds[round_id] != 1:
                row.update(accepted_record=False, reason="ambiguous_native_identity")
            elif entry.get("status") in {"queued", "running"}:
                # Native judge_submission writes the final archive path after
                # publishing running. A stable stat can still describe a
                # prefix; bind only once that native evaluation has finished.
                row["reason"] = "native_archive_pending"
            elif entry.get("status") not in {"completed", "error"}:
                row.update(accepted_record=False, reason="unknown_native_status")
            else:
                try:
                    digest, size = self._archive_identity(sid, round_id)
                    row.update(archive_sha256=digest, archive_bytes=size)
                except (OSError, ValueError) as error:
                    row["reason"] = str(error) if isinstance(error, ValueError) else type(error).__name__
        return validate_packet(dict(schema_version=SCHEMA, run_id=self.run_id,
                                    task_id=self.task_id, records=records))

    def _archive_identity(self, sid, round_id):
        source = self.trial / "submissions" / round_id / "submission.tar.gz"
        witness = _signature(_archive_stat(source, self.trial))
        cached = self._archive_cache.get((sid, round_id))
        if cached is not None and cached[0] == witness:
            return cached[1], cached[2]
        raw = read_archive(source, root=self.trial)
        if witness != _signature(_archive_stat(source, self.trial)):
            raise ValueError("native_archive_moved")
        digest, size = hashlib.sha256(raw).hexdigest(), len(raw)
        binding = dict(run_id=self.run_id, task_id=self.task_id, submission_id=sid,
                       round_id=round_id, archive_sha256=digest, archive_bytes=size)
        if round_id in self._bound_rounds and self._bound_rounds[round_id] != sid:
            raise ValueError("native_accepted_round_changed")
        path = self.directory / "bindings" / (sid + ".json")
        try:
            write_bytes_atomic(path, canonical_json(binding), create_only=True)
        except FileExistsError:
            if json.loads(path.read_bytes()) != binding:
                raise ValueError("native_accepted_archive_changed")
        self._bound_rounds[round_id] = sid
        self._archive_cache[(sid, round_id)] = witness, digest, size
        return digest, size

    def update(self, backend, handle):
        with self._lock:
            packet = self._poll()
            encoded = canonical_json(packet)
            revision = hashlib.sha256(encoded).hexdigest()
            if revision == self._published:
                return packet
            local, remote = self.directory / "native.json", self.remote_root / "native.json"
            write_bytes_atomic(local, encoded)
            temporary = str(remote) + "." + uuid.uuid4().hex + ".tmp"
            result = backend.exec_run(handle, ["mkdir", "-p", str(remote.parent)], user="root")
            if result.exit_code:
                raise RuntimeError("native_submission_directory_failed")
            backend.copy_to_container(handle, local, PurePosixPath(temporary))
            result = backend.exec_run(handle, ["chmod", "0644", temporary], user="root")
            if result.exit_code:
                raise RuntimeError("native_submission_permissions_failed")
            result = backend.exec_run(handle, ["mv", "-f", temporary, str(remote)], user="root")
            if result.exit_code:
                raise RuntimeError("native_submission_publish_failed")
            self._published = revision
            return packet

    def start(self, backend, handle):
        if self._stop.is_set():
            raise RuntimeError("native_submission_publisher_closed")
        # Re-publish the scope when a native container starts or resumes.
        with self._lock:
            self._published = None
            self.update(backend, handle)
        self._active.set()
        if self.thread is None:
            self.thread = threading.Thread(target=self._loop, args=(backend, handle), daemon=True)
            self.thread.start()

    def _loop(self, backend, handle):
        while not self._stop.wait(self.poll_seconds):
            if not self._active.is_set():
                continue
            try:
                self.update(backend, handle)
            except Exception as error:
                if self.logger:
                    self.logger.warning("Native submission observation failed: %s", type(error).__name__)

    def pause(self):
        self._active.clear()

    def close(self):
        self._stop.set()
        if self.thread is not None:
            self.thread.join(timeout=15)
            if self.thread.is_alive():
                raise RuntimeError("native_submission_publisher_did_not_stop")
        self.session.close()
