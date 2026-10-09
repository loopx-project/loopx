"""Dedicated, finite-cohort online admission over the unmodified native judge.

This EdgeBench provider owns scheduling only; native task loaders, grading,
reports and score selection retain authority. Use a separate process/log root
for offline backfill. The cohort reserves one evaluator for each admitted run.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import re
import threading
import uuid
from contextlib import contextmanager
from dataclasses import replace
from enum import StrEnum
from pathlib import Path

from fastapi import File, Form, HTTPException, Query, UploadFile
from sforge.harness.config import load_config
from sforge.harness.judge_server import (
    RegisterRequest, create_app as native_app,
)

POLICY = "edgebench_online_cohort_v2"


class RegistrationState(StrEnum):
    ACTIVE = "active"
    DRAINING = "draining"
    RELEASED = "released"


def resource_preflight(client, slots, *, worker_cpu=4, judge_cpu=4,
                       worker_memory=16 << 30, judge_memory=8 << 30,
                       allow_resource_overcommit=False, shared_startup_memory_gib=None):
    """Reserve the whole finite cohort, retaining 2 CPUs and 4 GiB host headroom.

    Existing containers count at their hard limits. An unlimited container makes
    capacity unknown; admission fails rather than estimating usage from a tick.
    This is an operator-owned dedicated pool, not a Docker resource scheduler.
    """
    if type(slots) is not int or slots < 1:
        raise ValueError("Online slots must be a positive integer")
    if shared_startup_memory_gib is not None:
        if not allow_resource_overcommit:
            raise ValueError("A shared startup floor requires explicit resource overcommit")
        minimum_gib = (judge_memory + min(worker_memory, 4 << 30) + (4 << 30) + (1 << 30) - 1) >> 30
        if type(shared_startup_memory_gib) is not int or shared_startup_memory_gib < minimum_gib:
            raise ValueError(f"Shared startup floor must be at least {minimum_gib} GiB")
    info = client.info()
    used_cpu, used_memory = 0.0, 0
    for container in client.containers.list():
        host = container.attrs["HostConfig"]
        cpu = host.get("NanoCpus", 0) / 1e9
        if not cpu and host.get("CpuQuota", 0) > 0:
            cpu = host["CpuQuota"] / (host.get("CpuPeriod") or 100000)
        memory = host.get("Memory", 0)
        if not cpu or not memory:
            raise ValueError("Cannot reserve online capacity beside an unbounded container")
        used_cpu += cpu
        used_memory += memory
    required_cpu = slots * (worker_cpu + judge_cpu)
    required_memory = slots * (worker_memory + judge_memory)
    if allow_resource_overcommit:
        # Docker quotas are ceilings, not exclusive reservations. An operator
        # may choose a monitored shared pool; retain bounded containers and
        # enough currently available memory for judges and worker startup.
        available = int(next(line.split()[1] for line in
            Path("/proc/meminfo").read_text().splitlines()
            if line.startswith("MemAvailable:"))) * 1024
        minimum = slots * (judge_memory + min(worker_memory, 4 << 30)) + (4 << 30)
        if shared_startup_memory_gib is not None:
            minimum = shared_startup_memory_gib << 30
        if available < minimum:
            raise ValueError("Insufficient available memory for shared-pool startup")
        return dict(slots=slots, reserved_cpu=0, reserved_memory=0,
                    requested_cpu_ceiling=required_cpu, requested_memory_ceiling=required_memory,
                    observed_other_cpu=used_cpu, observed_other_memory=used_memory,
                    observed_available_memory=available, startup_memory_floor=minimum,
                    exclusive_resources=False, operator_resource_monitor_required=True)
    if required_cpu + used_cpu + 2 > info["NCPU"] or required_memory + used_memory + (4 << 30) > info["MemTotal"]:
        raise ValueError("Insufficient CPU/memory for solver plus one evaluator per online slot")
    return dict(slots=slots, reserved_cpu=required_cpu, reserved_memory=required_memory,
                observed_other_cpu=used_cpu, observed_other_memory=used_memory,
                exclusive_resources=True, operator_resource_monitor_required=False)


def create_app(config, *, slots, reservation):
    """Isolated online-only native service with explicitly released run slots.

    Restart requires a new cohort. Tokens and epoch are process-scoped, so a
    client must fail closed on restart instead of replaying a possibly graded
    archive into an unrelated registration. Native spool/capture files survive.
    """
    try:
        from sforge.harness.judge_server import JudgeCapacityExceeded
    except ImportError as error:
        raise ValueError("Online judge requires a pinned SForge with bounded asynchronous capacity") from error
    if slots < 1 or reservation.get("slots") != slots:
        raise ValueError("A matching capacity reservation is required")
    app = native_app(replace(config, judge_max_concurrent=slots, judge_max_pending=0))
    state = app.state.judge
    # Bind disclosed results to the native implementation and task configuration
    # loaded by this service. This is provenance, never a second score owner.
    import sforge
    source_root = Path(sforge.__file__).parent
    source_digest = hashlib.sha256()
    for path in sorted(source_root.rglob("*.py")):
        source_digest.update(path.relative_to(source_root).as_posix().encode() + b"\0")
        source_digest.update(hashlib.sha256(path.read_bytes()).digest())
    evaluators = {task_id: dict(
        native_source_sha256=source_digest.hexdigest(),
        task_spec_sha256=hashlib.sha256((config.tasks_dir / f"{task_id}.json").read_bytes()).hexdigest(),
        judge_image_key=task.judge_image_key, selection=task.judge.selection,
        score_direction=task.judge.score_direction,
    ) for task_id, task in state.tasks.items()}
    epoch = uuid.uuid4().hex
    lock = threading.Lock()
    runs, registrations, submissions, active = {}, {}, {}, {}
    # Replace only admission. Native history/result/grading/lifespan are reused.
    # Drop game routes so the dedicated pool cannot be occupied by another lane.
    app.router.routes[:] = [r for r in app.router.routes if getattr(r, "path", "") not in {
        "/api/v1/register", "/api/v1/submit"} and "/game/" not in getattr(r, "path", "")]

    def authorize(secret):
        if secret != state.admin_secret:
            raise HTTPException(403, "Host authorization required")

    def running(token):
        identifier = active.get(token)
        if identifier is None:
            return False
        result = state.get_result(identifier)
        # Missing native evidence cannot release reserved evaluator capacity.
        return result is None or result["status"] not in ("completed", "error")

    def occupied_slots():
        for token, status in registrations.items():
            if status == RegistrationState.DRAINING and not running(token):
                registrations[token] = RegistrationState.RELEASED
        return sum(status != RegistrationState.RELEASED for status in registrations.values())

    @app.get("/api/v1/best-only/admission")
    def admission(admin_secret: str = Query("")):
        authorize(admin_secret)
        with lock:
            return dict(policy=POLICY, epoch=epoch, slots=slots, admitted=occupied_slots(),
                        registrations_total=len(runs), max_running_per_run=1,
                        resource_reservation=reservation, evaluators=evaluators)

    @app.post("/api/v1/register")
    def register(req: RegisterRequest):
        authorize(req.admin_secret)
        task = state.tasks.get(req.task_id)
        if task is None:
            raise HTTPException(404, "Unknown task")
        if task.game_mode or task.internet or req.max_agent_submissions != 0 or req.backend not in (None, "docker"):
            raise HTTPException(400, "Online cohort accepts isolated best-only Docker trials")
        if req.judge_cpu_limit not in (None, config.judge_cpu_limit) or req.judge_mem_limit not in (None, config.judge_mem_limit):
            raise HTTPException(400, "Judge resource request differs from reserved pool")
        key = (req.run_id, req.task_id)
        with lock:
            if key in runs:
                raise HTTPException(409, "Run already registered; use its original token")
            if occupied_slots() >= slots:
                raise HTTPException(503, "Online cohort full; defer solver admission")
            token = state.register_session(req.task_id, req.run_id,
                judge_cpu_limit=config.judge_cpu_limit, judge_mem_limit=config.judge_mem_limit,
                max_agent_submissions=0, submission_cooldown=req.submission_cooldown)
            runs[key] = token
            registrations[token] = RegistrationState.ACTIVE
        return {"token": token}

    @app.post("/api/v1/best-only/release")
    def release(run_id: str = Form(...), task_id: str = Form(...),
                epoch_id: str = Form(...), admin_secret: str = Form("")):
        authorize(admin_secret)
        if epoch_id != epoch:
            raise HTTPException(409, "Online judge restarted; reconcile the original registration")
        with lock:
            token = runs.get((run_id, task_id))
            if token is None:
                raise HTTPException(404, "Unknown online registration")
            if registrations[token] == RegistrationState.ACTIVE:
                registrations[token] = RegistrationState.DRAINING
            occupied_slots()
            return dict(epoch=epoch, run_id=run_id, task_id=task_id,
                        state=registrations[token])

    @app.post("/api/v1/submit")
    def deny_shared_submission():
        raise HTTPException(403, "Online cohort requires an idempotent capture; offline submission is disabled")

    @app.post("/api/v1/best-only/submit")
    def submit(token: str = Form(...), epoch_id: str = Form(...),
               capture_id: str = Form(...), admin_secret: str = Form(""),
               archive: UploadFile = File(...)):
        authorize(admin_secret)
        if epoch_id != epoch:
            raise HTTPException(409, "Online judge restarted; stop and reconcile this trial")
        if not re.fullmatch(r"capture-[0-9]+", capture_id):
            raise HTTPException(400, "Invalid capture identity")
        data = archive.file.read()
        digest = hashlib.sha256(data).hexdigest()
        key = (token, capture_id)
        with lock:
            if token not in registrations:
                raise HTTPException(401, "Unknown online registration")
            if key in submissions:
                receipt = submissions[key]
                if receipt["source_sha256"] != digest:
                    raise HTTPException(409, "Capture identity reused for different source")
                return receipt
            if registrations[token] != RegistrationState.ACTIVE:
                raise HTTPException(410, "Online registration closed; new captures are disabled")
            if running(token):
                raise HTTPException(503, "This run already has one evaluation in flight")
            try:
                identifier, round_id, _ = state.submit_for_token(token, data, "auto")
            except JudgeCapacityExceeded as error:
                raise HTTPException(503, str(error)) from error
            receipt = dict(submission_id=identifier, round_id=round_id, capture_id=capture_id,
                           source_sha256=digest, epoch=epoch)
            submissions[key] = receipt
            active[token] = identifier
            return receipt

    return app


def main():
    import docker
    import uvicorn
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slots", required=True, type=int)
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--allow-resource-overcommit", action="store_true",
                        help="Use an operator-monitored shared pool; container ceilings are not reserved")
    parser.add_argument("--shared-startup-memory-gib", type=int,
                        help="Explicit shared-pool startup floor; requires overcommit and ongoing load monitoring")
    args = parser.parse_args()
    config = load_config()
    if config.backend != "docker":
        parser.error("Online resource admission currently requires Docker")
    # Match the runner's current worker/judge limits; no task can enlarge these.
    config = replace(config, judge_cpu_limit=4, judge_mem_limit="8g")
    try:
        with cohort_lock():
            client = docker.from_env()
            try:
                reservation = resource_preflight(client, args.slots,
                    allow_resource_overcommit=args.allow_resource_overcommit,
                    shared_startup_memory_gib=args.shared_startup_memory_gib)
            finally:
                client.close()
            uvicorn.run(create_app(config, slots=args.slots, reservation=reservation),
                        host="0.0.0.0", port=args.port)
    except BlockingIOError:
        parser.error("This host already owns an online cohort; reuse its slots or defer")


@contextmanager
def cohort_lock():
    # One operator-owned Docker pool: online and offline cannot reserve the
    # same resources by using different task or log directories.
    path = Path.home() / ".cache" / "edgebench-online-cohort.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


if __name__ == "__main__":
    main()
