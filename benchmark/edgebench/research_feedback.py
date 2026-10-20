"""Read native accepted snapshots and released feedback for an explicit research run.

SForge retains admission, evaluation, scoring and opportunity ownership. This
adapter uses only the token-visible history/results and the native submitted
source archive; it never reads judge artifacts or initiates an evaluation.
"""
from __future__ import annotations

from datetime import datetime, timezone
import gzip
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import tarfile
import threading
import uuid
import zlib

SCHEMA = "edgebench_native_feedback_v1"
MAX_ARCHIVE_BYTES = 256 * 1024 * 1024
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_ROUND = re.compile(r"agent-[1-9][0-9]*\Z")


def canonical_json(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode()


def write_bytes_atomic(path: Path, raw: bytes, *, create_only=False):
    """Commit one private provider receipt before reporting the associated fact."""
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


def read_archive(path: Path, *, root: Path) -> bytes:
    """Read one physical native source file; detect movement during capture."""
    root = root.resolve(strict=True)
    path = path.absolute()
    if not path.is_relative_to(root) or path.resolve(strict=True) != path:
        raise ValueError("native_archive_not_physical")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as stream:
        before = os.fstat(stream.fileno())
        if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_ARCHIVE_BYTES:
            raise ValueError("native_archive_size_or_type")
        raw = stream.read(MAX_ARCHIVE_BYTES + 1)
        after = os.fstat(stream.fileno())
    current = path.stat(follow_symlinks=False)
    def signature(row):
        return row.st_dev, row.st_ino, row.st_size, row.st_mtime_ns, row.st_ctime_ns
    if (signature(before) != signature(after) or signature(after) != signature(current)
            or len(raw) != before.st_size or path.resolve(strict=True) != path):
        raise ValueError("native_archive_moved")
    return raw


def archive_manifest(raw: bytes, selected_paths: list[str] | None = None):
    """Use exactly research_probe's sorted selected-file manifest, not tar bytes.

    Nothing is extracted. Ambiguous member names, links and oversized sources
    remain unqualified, including when the current workspace looks plausible.
    """
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise ValueError("native_archive_too_large")
    requested = None if selected_paths is None else set(selected_paths)
    if requested is not None and (not 1 <= len(requested) <= 32 or len(requested) != len(selected_paths)):
        raise ValueError("invalid_selected_files")
    members, files, total = set(), [], 0
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        for member in archive:
            name = member.name
            while name.startswith("./"):
                name = name[2:]
            if member.isdir() and name in ("", "."):
                continue
            path = PurePosixPath(name)
            if (path.is_absolute() or ".." in path.parts or not name or "\\" in name
                    or path.as_posix() != name.rstrip("/") or name in members):
                raise ValueError("ambiguous_archive_member")
            members.add(name)
            if member.isdir():
                continue
            if not member.isfile():
                raise ValueError("non_regular_archive_member")
            total += member.size
            if len(members) > 100000 or total > MAX_UNPACKED_BYTES:
                raise ValueError("native_archive_expansion_limit")
            if requested is not None and name not in requested:
                continue
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("unreadable_archive_member")
            with stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            files.append({"path": name, "sha256": digest})
    if requested is not None and {row["path"] for row in files} != requested:
        raise ValueError("selected_files_not_in_accepted_archive")
    if not 1 <= len(files) <= 32:
        raise ValueError("select_one_to_32_files")
    files.sort(key=lambda row: row["path"])
    # research_probe uses json.dumps' default ensure_ascii=True.
    encoded = json.dumps(files, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest(), files


def validate_packet(packet):
    if (not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA
            or not _ID.fullmatch(packet.get("run_id") or "")
            or not _ID.fullmatch(packet.get("task_id") or "")
            or not isinstance(packet.get("records"), list)):
        raise ValueError("invalid_native_feedback_packet")
    return packet


class NativeResearchFeedback:
    """One low-cost publisher scoped to the registration, surviving native wakes.

    ``after_publish(backend, handle, packet)`` invokes the installed normal
    Explore/Todo consumer. Publication is retried until that consumer succeeds;
    neither a copied file nor an unchanged poll is a control-plane settlement.
    """
    def __init__(self, *, trial: Path, run_id: str, task_id: str, judge_url: str,
                 remote_root: str, logger=None, session=None, poll_seconds: float = 5):
        if not _ID.fullmatch(run_id) or not _ID.fullmatch(task_id) or poll_seconds <= 0:
            raise ValueError("invalid_native_feedback_scope")
        self.trial, self.run_id, self.task_id = Path(trial), run_id, task_id
        self.directory = self.trial / "native-research-feedback"
        self.judge_url, self.remote_root = judge_url.rstrip("/"), PurePosixPath(remote_root)
        self.logger, self.poll_seconds = logger, poll_seconds
        self.token, self.thread, self.after_publish = None, None, None
        self._stop, self._active = threading.Event(), threading.Event()
        self._lock = threading.RLock()
        self._published, self._packet = None, None
        self._observed_at = {}
        previous = self.directory / "native.json"
        if previous.exists():
            packet = validate_packet(json.loads(previous.read_bytes()))
            if (packet["run_id"], packet["task_id"]) != (run_id, task_id):
                raise ValueError("native_feedback_saved_scope_mismatch")
            self._observed_at = {(row["submission_id"], row.get("result_sha256")): row["result_observed_at"]
                                 for row in packet["records"] if row.get("result_observed_at")}
        self._artifacts = {}
        if session is None:
            import requests
            session = requests.Session()
            session.trust_env = False  # Host path to its declared native judge.
        self.session = session

    def bind(self, environment):
        token = (environment or {}).get("SFORGE_TOKEN")
        if not token or (self.token is not None and self.token != token):
            raise ValueError("native_feedback_registration_mismatch")
        self.token = token

    def _get(self, path, **kwargs):
        response = self.session.get(self.judge_url + path, timeout=(3, 5), **kwargs)
        response.raise_for_status()
        return response.json()

    def poll(self):
        if self.token is None:
            raise ValueError("native_feedback_registration_missing")
        history = self._get("/api/v1/history", params={"token": self.token})
        if history.get("run_id") != self.run_id or not isinstance(history.get("entries"), list):
            raise ValueError("native_feedback_history_scope_mismatch")
        entries = [row for row in history["entries"] if row.get("type") == "submission"
                   and isinstance(row.get("round"), str) and row["round"].startswith("agent-")]
        ids = [row.get("submission_id") for row in entries]
        rounds = [row.get("round") for row in entries]
        if any(row.get("task_id") != self.task_id for row in entries):
            raise ValueError("native_feedback_task_scope_mismatch")
        records, artifacts = [], {}
        for entry in entries:
            sid, round_id, status = entry.get("submission_id"), entry.get("round"), entry.get("status")
            if not _ID.fullmatch(sid or "") or not _ROUND.fullmatch(round_id or ""):
                raise ValueError("native_feedback_identity_invalid")
            row = {"accepted_record": True, "submission_id": sid, "round_id": round_id,
                   "status": status, "attribution_status": "unknown"}
            records.append(row)
            if ids.count(sid) != 1 or rounds.count(round_id) != 1:
                row["accepted_record"] = False
                row["reason"] = "ambiguous_native_identity"
                continue
            if status not in {"queued", "running", "completed", "error"}:
                row["accepted_record"] = False
                row["reason"] = "unknown_native_status"
                continue
            try:
                source = self.trial / "submissions" / round_id / "submission.tar.gz"
                raw = read_archive(source, root=self.trial)
                digest = hashlib.sha256(raw).hexdigest()
                self._bind_archive(sid, round_id, digest, len(raw))
                archive_name = f"{sid}-{digest}.tar.gz"
                row.update(archive_sha256=digest, archive_bytes=len(raw),
                           archive_path=str(self.remote_root / "archives" / archive_name))
                artifacts["archives/" + archive_name] = raw
                # Large/multi-file submissions remain usable for selected probe
                # manifests. Do not confuse an absent full-manifest digest with
                # an absent exact native archive binding.
                try:
                    row["candidate_sha256"], _ = archive_manifest(raw)
                except (EOFError, ValueError, gzip.BadGzipFile, tarfile.TarError, zlib.error):
                    pass
                row["attribution_status"] = "verified"
                if status not in {"completed", "error"}:
                    continue
                result = self._get("/api/v1/result/" + sid)
                self._validate_result(result, entry)
                result_bytes = canonical_json(result)
                result_sha = hashlib.sha256(result_bytes).hexdigest()
                identity = dict(run_id=self.run_id, task_id=self.task_id, submission_id=sid,
                                round_id=round_id, archive_sha256=digest, result_sha256=result_sha)
                ref = "native-result:" + hashlib.sha256(canonical_json(identity)).hexdigest()
                result_name = f"{sid}-{result_sha}.json"
                artifacts["results/" + result_name] = result_bytes
                report = result.get("report") or {}
                observed_at = self._observed_at.setdefault((sid, result_sha),
                    datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
                row.update(result_ref=ref, result_sha256=result_sha, result_observed_at=observed_at,
                           result_path=str(self.remote_root / "results" / result_name),
                           result_summary={key: report.get(key) for key in (
                               "score", "pass_rate", "valid", "passed", "failed", "total_tests")})
            except (OSError, ValueError, tarfile.TarError) as error:
                row["attribution_status"] = "unknown"
                row["reason"] = str(error) if isinstance(error, ValueError) else type(error).__name__
                row.pop("result_ref", None)
                row.pop("result_sha256", None)
        packet = {"schema_version": SCHEMA, "run_id": self.run_id, "task_id": self.task_id,
                  "records": records}
        with self._lock:
            self._artifacts, self._packet = artifacts, packet
        return packet

    def _bind_archive(self, sid, round_id, digest, size):
        """A native submission can never silently move to a different archive."""
        binding = dict(run_id=self.run_id, task_id=self.task_id, submission_id=sid,
                       round_id=round_id, archive_sha256=digest, archive_bytes=size)
        path = self.directory / "bindings" / (sid + ".json")
        try:
            write_bytes_atomic(path, canonical_json(binding), create_only=True)
        except FileExistsError:
            if json.loads(path.read_bytes()) != binding:
                raise ValueError("native_accepted_archive_changed")

    def _validate_result(self, result, entry):
        if (not isinstance(result, dict) or result.get("submission_id") != entry["submission_id"]
                or result.get("status") != entry["status"]):
            raise ValueError("released_result_identity_mismatch")
        report = result.get("report")
        if entry["status"] == "error":
            if report is not None:
                raise ValueError("native_error_report_ambiguous")
            return
        if (not isinstance(report, dict) or result.get("error") is not None
                or report.get("submission_id") != entry["submission_id"]
                or report.get("task_id") != self.task_id):
            raise ValueError("released_result_scope_mismatch")
        if any(report.get(key) != entry.get(key) for key in ("score", "pass_rate", "valid")):
            raise ValueError("released_result_history_mismatch")

    def update(self, backend, handle):
        packet = self.poll()
        encoded = canonical_json(packet)
        revision = hashlib.sha256(encoded).hexdigest()
        if revision == self._published:
            return packet
        self.directory.mkdir(parents=True, exist_ok=True)
        for name, raw in {**self._artifacts, "native.json": encoded}.items():
            local = self.directory / name
            local.parent.mkdir(parents=True, exist_ok=True)
            write_bytes_atomic(local, raw)
            # Copy to a fresh sibling, then atomically publish the complete file.
            remote = self.remote_root / name
            temporary = str(remote) + "." + uuid.uuid4().hex + ".tmp"
            result = backend.exec_run(handle, ["mkdir", "-p", str(remote.parent)], user="root")
            if result.exit_code:
                raise RuntimeError("native_feedback_directory_failed")
            backend.copy_to_container(handle, local, PurePosixPath(temporary))
            result = backend.exec_run(handle, ["chmod", "0644", temporary], user="root")
            if result.exit_code:
                raise RuntimeError("native_feedback_permissions_failed")
            result = backend.exec_run(handle, ["mv", "-f", temporary, str(remote)], user="root")
            if result.exit_code:
                raise RuntimeError("native_feedback_publish_failed")
        if self.after_publish is not None:
            self.after_publish(backend, handle, packet)
        self._published = revision
        return packet

    def snapshot(self):
        with self._lock:
            return json.loads(canonical_json(self._packet)) if self._packet is not None else None

    def start(self, backend, handle):
        if self._stop.is_set():
            raise RuntimeError("native_feedback_already_closed")
        # Establish the scope packet before any worker submission can happen.
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
                # Never log a native URL containing a token or raw feedback.
                if self.logger:
                    self.logger.warning("Native research observation failed: %s", type(error).__name__)

    def pause(self):
        self._active.clear()

    def close(self):
        self._stop.set()
        if self.thread is not None:
            # Let a bounded consumer finish or kill its process group before
            # the native runner removes the container and collects artifacts.
            self.thread.join(timeout=90)
            if self.thread.is_alive():
                raise RuntimeError("native_feedback_publisher_did_not_stop")
        self.session.close()
