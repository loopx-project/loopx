"""Host-only adapter for the worker's explicitly enabled checkpoint mailbox."""
from __future__ import annotations

import hashlib
import io
import json
import re
import tarfile
import tempfile
import threading
import time
from pathlib import Path

from .checkpoint_controller import _controller
from .checkpoint_worker import _archive, capture_worker


class CheckpointSupervisor:
    def __init__(self, controller_file: Path, destination: Path):
        _controller(controller_file)  # Fail before native registration.
        self.controller_file, self.destination = controller_file, destination
        self.token = None
        self.stop = threading.Event()
        self.thread = None
        self.failure = None

    def bind(self, environment):
        if self.token is not None or not (environment or {}).get("SFORGE_TOKEN"):
            raise ValueError("Checkpoint controller requires one native worker registration")
        self.token = environment["SFORGE_TOKEN"]

    def start(self, container):
        if self.thread is not None or self.token is None:
            raise ValueError("Checkpoint supervisor requires one bound worker execution")
        self.destination.mkdir(mode=0o700, parents=True, exist_ok=False)
        self.container = container
        self.thread = threading.Thread(target=self._run, args=(container,), daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(60)
            if self.thread.is_alive():
                raise RuntimeError("Checkpoint writer barrier is still active; preserve the worker for recovery")
        if self.failure is not None:
            # A missed optional point does not cancel otherwise useful source
            # work. An uncertain/unreleased writer barrier still needs recovery.
            self.container.reload()
            if self.container.attrs["State"]["Paused"]:
                raise RuntimeError("Checkpoint writer barrier requires controller recovery") from self.failure

    def _reply(self, container, directory, nonce, response):
        container.reload()
        if container.attrs["State"]["Paused"] or not container.attrs["State"]["Running"]:
            raise RuntimeError("Checkpoint reply requires the released, running source worker")
        raw = json.dumps(response).encode()
        pending, final = nonce + ".pending", nonce + ".json"
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode="w") as archive:
            entry = tarfile.TarInfo(pending)
            entry.size, entry.mode = len(raw), 0o444
            archive.addfile(entry, io.BytesIO(raw))
        if not container.put_archive(directory, stream.getvalue()):
            raise RuntimeError("Checkpoint reply transport was not accepted")
        result = container.exec_run(["/bin/mv", directory + "/" + pending, directory + "/" + final])
        if result.exit_code:
            raise RuntimeError("Checkpoint reply could not be published atomically")

    def _run(self, container):
        from docker.errors import NotFound

        mailbox = "/logs/agent/checkpoint-controller"
        remote = mailbox + "/ready.json"
        seen = set()
        read_failures = 0
        try:
            while not self.stop.is_set():
                with tempfile.TemporaryDirectory(prefix=".controller-poll-", dir=self.destination) as temp:
                    stage = Path(temp)
                    try:
                        raw = _archive(container, remote, stage / "ready").read_bytes()
                    except NotFound:
                        read_failures = 0
                        self.stop.wait(5)
                        continue
                    except tarfile.ReadError:
                        # ready.json can disappear between Docker's stat and
                        # archive stream as the source closes its handoff. Only
                        # retry the read, never a capture identity or mutation.
                        read_failures += 1
                        if read_failures >= 3:
                            raise
                        self.stop.wait(5)
                        continue
                    read_failures = 0
                    digest = hashlib.sha256(raw).hexdigest()
                    if digest in seen:
                        self.stop.wait(5)
                        continue
                    seen.add(digest)  # Never retry a later state under an earlier point.
                    value = json.loads(raw)
                    nonce = value.get("capture_id")
                    if not isinstance(nonce, str) or not re.fullmatch(r"[0-9a-f]{32}", nonce):
                        raise ValueError("Malformed checkpoint request identity")
                    response = {"request_sha256": digest, "ok": False}
                    try:
                        request = stage / "request.json"
                        request.write_text(json.dumps({"container_id": container.id,
                            "components": value["components"],
                            "phase_deadline_path": "/opt/loopx-benchmark/control/phase-deadline",
                            "handoff": {"source_path": remote, "sha256": digest}}))
                        remaining = value["deadline_epoch"] - time.time()
                        if remaining <= 0:
                            raise TimeoutError("Checkpoint handoff deadline expired")
                        captured = capture_worker(self.controller_file, token=self.token, capture_id=nonce,
                            destination=self.destination / nonce, request_file=request, timeout=remaining,
                            seal_within_phase=True)
                        response.update(ok=True, capture=captured)
                    except Exception as error:
                        # Optional capture failure resumes the original source;
                        # credentials and raw provider errors never enter it.
                        response["error_kind"] = type(error).__name__
                        diagnostic = self.destination / (nonce + ".error.txt")
                        diagnostic.write_text(str(error).replace(self.token, "[redacted]")[:2000])
                        diagnostic.chmod(0o600)
                    (self.destination / (nonce + ".json")).write_text(json.dumps(response))
                    self._reply(container, mailbox, nonce, response)
        except Exception as error:
            self.failure = error
            (self.destination / "supervisor-failure.json").write_text(json.dumps({"error_kind": type(error).__name__}))
