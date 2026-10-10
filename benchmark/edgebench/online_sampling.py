"""Durable captures with one in-flight evaluation and one replaceable pending.

Provider-specific transport: archives and native submission identities are the
source of truth. Coalescing only affects online scheduling, never archival
coverage. Unscored captures (including final) require separate offline scoring.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
import time
from enum import StrEnum
from pathlib import Path

import requests
from sforge.harness.run_agent import _extract_archive_from_container
from .online_judge import POLICY, RegistrationState


class CaptureStatus(StrEnum):
    PENDING = "pending"
    SUPERSEDED = "superseded_online"
    DISPATCHING = "dispatching"
    SUBMITTED = "submitted"
    COMPLETED = "completed"
    ERROR = "error"
    FINAL = "final_offline"


class OnlineSampler:
    def __init__(self, *, trial, task, interval, judge_url, secret, logger, task_sha256=None):
        self.trial, self.task, self.interval = trial, task, interval
        self.url, self.secret, self.logger = judge_url.rstrip("/"), secret, logger
        self.directory = trial / "online-captures"
        self.directory.mkdir(exist_ok=False)
        self.lock = threading.Lock()
        self.records = []
        self.pending = self.inflight = None
        self.stop = threading.Event()
        self.active = threading.Event()
        self.thread = None
        self.session = requests.Session()
        self.session.trust_env = False
        self.token = None
        self.epoch = None
        self.evaluators = {}
        self.task_sha256 = task_sha256

    def qualify(self):
        response = self.session.get(self.url + "/api/v1/best-only/admission",
                                   params={"admin_secret": self.secret}, timeout=(3, 5))
        response.raise_for_status()
        value = response.json()
        if value.get("policy") != POLICY or value.get("max_running_per_run") != 1 or not value.get("epoch"):
            raise ValueError("Best-only needs a capacity-reserved online judge")
        self.epoch = value["epoch"]
        self.evaluators = value.get("evaluators", {})
        if self.task is not None:
            evaluator = self.evaluators.get(self.task.task_id, {})
            expected = dict(judge_image_key=self.task.judge_image_key,
                            selection=self.task.judge.selection,
                            score_direction=self.task.judge.score_direction)
            if self.task_sha256 is not None:
                expected["task_spec_sha256"] = self.task_sha256
            if (any(evaluator.get(key) != item for key, item in expected.items())
                    or any(not isinstance(evaluator.get(key), str)
                           or not re.fullmatch(r"[0-9a-f]{64}", evaluator[key])
                           for key in ("native_source_sha256", "task_spec_sha256"))):
                raise ValueError("Online evaluator differs from the admitted task configuration")
        (self.directory / "admission.json").write_text(json.dumps(value))

    def start(self, backend, handle, token):
        if self.stop.is_set():
            raise RuntimeError("Online sampler is closed; reconcile the attempt")
        self.active.set()
        if self.thread is not None:
            return
        if not self.epoch:
            raise RuntimeError("Online capacity was not qualified before solver admission")
        self.token = token
        self.thread = threading.Thread(target=self._capture_loop, args=(backend, handle), daemon=True)
        self.thread.start()

    def pause(self):
        self.active.clear()

    def capture(self, archive, *, final=False):
        with self.lock:
            identifier = f"capture-{len(self.records) + 1}"
            path = self.directory / (identifier + ".tar.gz")
            temporary = path.with_suffix(".pending")
            temporary.write_bytes(archive)
            temporary.replace(path)
            record = dict(capture_id=identifier, captured_at=time.time(),
                          source_sha256=hashlib.sha256(archive).hexdigest(),
                          status=CaptureStatus.FINAL if final else CaptureStatus.PENDING)
            if not final:
                if self.pending is not None:
                    self.pending["status"] = CaptureStatus.SUPERSEDED
                self.pending = record
            self.records.append(record)
            self._save()
            return dict(record)

    def _save(self):
        target = self.directory / "index.json"
        pending = target.with_suffix(".tmp")
        pending.write_text(json.dumps(dict(epoch=self.epoch, captures=self.records), allow_nan=False))
        pending.replace(target)

    def tick(self, history):
        """Retire a terminal result, then dispatch at most one latest capture.

        HTTP uncertainty retains the same in-flight identity. The server's
        idempotent submit receipt prevents a lost response from duplicating work.
        """
        if self.stop.is_set():
            return
        with self.lock:
            current = self.inflight
            if current and current["status"] == CaptureStatus.SUBMITTED:
                result = next((row for row in history["entries"]
                               if row.get("submission_id") == current["submission_id"]), None)
                if result and result.get("status") in ("completed", "error"):
                    current.update(status=result["status"], result_observed_at=time.time())
                    self.inflight = None
                    current = None
                    self._save()
            if current is None and self.pending is not None:
                current = self.pending
                self.pending, self.inflight = None, current
                current.update(status=CaptureStatus.DISPATCHING, dispatch_started_at=time.time())
                self._save()
            if current is None or current["status"] != CaptureStatus.DISPATCHING:
                return
            identifier = current["capture_id"]
        # No lock across network/capture: newer sampling remains bounded and live.
        with (self.directory / (identifier + ".tar.gz")).open("rb") as source:
            response = self.session.post(self.url + "/api/v1/best-only/submit", data={
                "token": self.token, "admin_secret": self.secret, "epoch_id": self.epoch,
                "capture_id": identifier}, files={"archive": ("source.tar.gz", source)}, timeout=(3, 10))
        if response.status_code == 409:
            self.stop.set()
            raise RuntimeError("Online admission epoch/identity changed; reconcile before restarting")
        response.raise_for_status()
        receipt = response.json()
        if (receipt.get("epoch") != self.epoch or receipt.get("capture_id") != identifier
                or receipt.get("source_sha256") != current["source_sha256"]):
            self.stop.set()
            raise ValueError("Online receipt does not match the captured source")
        with self.lock:
            current.update(status=CaptureStatus.SUBMITTED, accepted_at=time.time(),
                           submission_id=receipt["submission_id"], round_id=receipt["round_id"])
            self._save()

    def admitted(self):
        with self.lock:
            return {record["submission_id"]: dict(record) for record in self.records
                    if "submission_id" in record}

    def _capture_loop(self, backend, handle):
        while not self.stop.wait(self.interval):
            if not self.active.is_set():
                continue
            try:
                archive = _extract_archive_from_container(backend, handle, self.task, shutdown_event=self.stop)
                if not self.stop.is_set() and self.active.is_set():
                    self.capture(archive)
            except Exception as error:
                # No source, hidden report or credentials in diagnostic text.
                self.logger.warning("Online capture unavailable (%s)", type(error).__name__)
                with (self.directory / "capture-errors.jsonl").open("a") as stream:
                    stream.write(json.dumps(dict(at=time.time(), error_kind=type(error).__name__)) + "\n")

    def close(self):
        self.stop.set()
        if self.thread is not None:
            self.thread.join(timeout=330)
            if self.thread.is_alive():
                raise RuntimeError("Online capture did not stop before worker cleanup")
        self.session.close()
        final = self.trial / "final_archive.tar.gz"
        if final.is_file() and not any(r["status"] == CaptureStatus.FINAL for r in self.records):
            self.capture(final.read_bytes(), final=True)

    def release_registration(self, *, run_id, task_id):
        """Call only after verified worker removal; retain ambiguous releases for retry."""
        if not self.stop.is_set():
            raise RuntimeError("Stop online sampling before releasing its registration")
        with requests.Session() as session:
            session.trust_env = False
            response = session.post(self.url + "/api/v1/best-only/release", data={
                "run_id": run_id, "task_id": task_id, "epoch_id": self.epoch,
                "admin_secret": self.secret}, timeout=(3, 10))
            response.raise_for_status()
            receipt = response.json()
        if (receipt.get("epoch") != self.epoch or receipt.get("run_id") != run_id
                or receipt.get("task_id") != task_id
                or receipt.get("state") not in (RegistrationState.DRAINING, RegistrationState.RELEASED)):
            raise ValueError("Online release receipt does not match the original registration")
        target = self.directory / "release.json"
        temporary = target.with_suffix(".tmp")
        temporary.write_text(json.dumps({key: receipt[key] for key in
            ("epoch", "run_id", "task_id", "state")}))
        temporary.replace(target)
        return receipt
