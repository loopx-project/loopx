"""Host-owned, positive-only projection of SForge's automatic evaluations.

Provider policy only: SForge still captures, evaluates and selects final scores.
The worker receives an allowlisted notification and its own submitted source,
never judge reports, credentials, scores or negative-result metadata.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import threading
import time
from pathlib import Path, PurePosixPath

import requests
from sforge.harness.selection import select_best

FEEDBACK_MODES = ("native", "blind", "best-only")
FEEDBACK_ROOT = PurePosixPath("/opt/edgebench-feedback")
FEEDBACK_FILE = FEEDBACK_ROOT / "latest.json"
_MESSAGE = (
    "This evaluated snapshot strictly improved the best valid score observed so far. "
    "It may differ from your current files; keep using local validation."
)


def prepare_feedback_root(backend, handle):
    """Prepare only the directory explicitly disclosed to the ordinary worker."""
    result = backend.exec_run(handle, ["/bin/sh", "-c",
        f"mkdir -p {FEEDBACK_ROOT} && chmod 0755 {FEEDBACK_ROOT}"], user="root")
    if result.exit_code:
        raise RuntimeError("Could not prepare best-only feedback directory")


def validate_best_only(task, interval):
    if task.judge.selection not in {"score_first", "valid_then_score"}:
        raise ValueError("best-only requires score_first or valid_then_score selection; use native or blind")
    if task.judge.score_direction not in {"maximize", "minimize"}:
        raise ValueError("best-only requires a known score direction")
    if interval <= 0:
        raise ValueError("best-only requires periodic auto-evaluation (--eval-interval > 0)")


class BestOnlyFeedback:
    """One publisher per trial; native outer resume reuses the same publisher.

    Candidate selection and public delivery commit together: a failed copy is
    retried, while repeating an already delivered maximum produces no event.
    Host state is durable, but a new runner invocation must still use a new run ID.
    """

    def __init__(self, *, trial: Path, run_id: str, task_id: str, direction: str,
                 judge_url: str, admin_secret: str, logger, sampler):
        self.trial, self.run_id, self.task_id = trial, run_id, task_id
        self.direction, self.judge_url = direction, judge_url.rstrip("/")
        self.admin_secret, self.logger = admin_secret, logger
        self.directory = trial / "best-only-host"
        self.directory.mkdir(exist_ok=False)
        self.score = None
        self.notifications = 0
        self.errors = 0
        self.sampler = sampler
        self.token = None
        self.thread = None
        self.active = threading.Event()
        self.stop_event = threading.Event()
        self.session = requests.Session()
        self.session.trust_env = False

    def bind(self, environment):
        token = (environment or {}).get("SFORGE_TOKEN")
        if not token:
            raise ValueError("best-only requires a native host registration token")
        if self.token is not None and token != self.token:
            raise ValueError("Cannot rebind feedback to another registration")
        self.token = token  # Never serialize the token or the admin secret.

    def start(self, backend, handle):
        if self.stop_event.is_set():
            raise RuntimeError("Feedback publisher is closed")
        self.active.set()
        if self.thread is not None:
            self.sampler.start(backend, handle, self.token)
            return
        if self.token is None or self.stop_event.is_set():
            raise RuntimeError("Feedback registration is missing or already closed")
        self._publish_json(backend, handle, {"schema_version": "edgebench_best_feedback_v1", "latest": None})
        self._record()
        self.sampler.start(backend, handle, self.token)
        self.thread = threading.Thread(target=self._loop, args=(backend, handle), daemon=True)
        self.thread.start()

    def pause(self):
        self.active.clear()
        self.sampler.pause()

    def close(self):
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                raise RuntimeError("Feedback publisher did not stop before container cleanup")
        self.session.close()
        self.sampler.close()

    def _candidate(self, history):
        if history.get("run_id") != self.run_id or not isinstance(history.get("entries"), list):
            raise ValueError("Feedback history does not match this trial")
        eligible = []
        admitted = self.sampler.admitted()
        for entry in history["entries"]:
            score = entry.get("score")
            if entry.get("task_id") != self.task_id:
                raise ValueError("Feedback history contains another task")
            admission = admitted.get(entry.get("submission_id"))
            if admission is None or admission["round_id"] != entry.get("round"):
                continue
            if (entry.get("type") != "submission" or entry.get("status") != "completed"
                    or entry.get("valid", True) is not True
                    or type(score) not in (int, float) or not math.isfinite(score)
                    or not re.fullmatch(r"auto-[0-9]+", entry.get("round") or "")):
                continue
            eligible.append(entry)
        if not eligible:
            return None
        if self.score is None:
            # The first observed valid result establishes a silent baseline.
            self.score = eligible[0]["score"]
            self._record()
        best = select_best(eligible, self.direction, "score_first")
        score = best["best_score"]
        better = score > self.score if self.direction == "maximize" else score < self.score
        if not better:
            return None
        return next(entry for entry in eligible if entry["round"] == best["best_round"])

    def update(self, history, backend, handle):
        """Project one complete native history read; useful for real transport qualification."""
        if self.stop_event.is_set():
            return
        candidate = self._candidate(history)
        if candidate is None:
            return
        snapshot = candidate["round"]
        # Read only the agent's original submitted archive, never judge artifacts.
        archive = self.trial / "submissions" / snapshot / "submission.tar.gz"
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        admission = self.sampler.admitted()[candidate["submission_id"]]
        if admission["source_sha256"] != digest:
            raise ValueError("Native archive differs from admitted online capture")
        remote = FEEDBACK_ROOT / f"{snapshot}-{digest}.tar.gz"
        prepare_feedback_root(backend, handle)
        backend.copy_to_container(handle, archive, remote)
        # Native Docker copy preserves host uid/mode, including private 0600
        # archives. Only this disclosed copy becomes public; verify as the
        # actual SForge worker before publishing or advancing the incumbent.
        permission = backend.exec_run(handle, ["chmod", "0644", str(remote)], user="root")
        if permission.exit_code:
            raise RuntimeError("Could not prepare best-only source checkpoint")
        readable = backend.exec_run(handle, ["sha256sum", str(remote)], user="agent")
        if readable.exit_code or readable.output.split()[:1] != [digest]:
            raise RuntimeError("Could not verify best-only source checkpoint for worker")
        packet = {
            "schema_version": "edgebench_best_feedback_v1",
            "latest": {"kind": "new_best", "snapshot_id": snapshot,
                       "source_sha256": digest, "source_archive": str(remote),
                       "message": _MESSAGE},
        }
        if self.stop_event.is_set():
            return
        self._publish_json(backend, handle, packet)
        self.score = candidate["score"]
        self.notifications += 1
        self._record(packet)

    def _record(self, packet=None):
        # Host-only health/evidence. Do not mount this directory into the worker.
        value = {"best_score": self.score, "notifications": self.notifications, "errors": self.errors}
        path = self.directory / "state.json"
        pending = path.with_suffix(".tmp")
        pending.write_text(json.dumps(value, allow_nan=False))
        pending.replace(path)
        if packet is not None:
            with (self.directory / "delivered.jsonl").open("a") as stream:
                stream.write(json.dumps({"published_at": time.time(), "packet": packet}) + "\n")

    def _publish_json(self, backend, handle, packet):
        prepare_feedback_root(backend, handle)
        local = self.directory / "public.json"
        local.write_text(json.dumps(packet, allow_nan=False) + "\n")
        pending = FEEDBACK_ROOT / ".latest.pending"
        backend.copy_to_container(handle, local, pending)
        result = backend.exec_run(handle, ["/bin/sh", "-c",
            f"chmod 0644 {pending} && mv -f {pending} {FEEDBACK_FILE}"], user="root")
        if result.exit_code:
            raise RuntimeError("Could not publish best-only notification")

    def _loop(self, backend, handle):
        while not self.stop_event.wait(10):
            if not self.active.is_set():
                continue
            try:
                response = self.session.get(f"{self.judge_url}/api/v1/history",
                    params={"token": self.token, "admin_secret": self.admin_secret}, timeout=(3, 5))
                response.raise_for_status()
                history = response.json()
                if self.active.is_set():
                    self.sampler.tick(history)
                    self.update(history, backend, handle)
            except Exception as error:
                # Do not leak report bodies/URLs/credentials through exception text.
                self.errors += 1
                self._record()
                self.logger.warning("Best-only feedback unavailable (%s)", type(error).__name__)
