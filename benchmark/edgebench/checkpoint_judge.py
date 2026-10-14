"""Opt-in read-only checkpoint participant over the native SForge judge.

Native admission, grading, history and ranking retain their owners. This
adapter exports a quiescent registration's public history and opportunity
clock; it cannot register, submit, restore or extend a trial.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import time
import uuid
from dataclasses import replace
from pathlib import Path
from benchmark.runtime.sforge_checkpoint import ARCHIVE_ROUTE, ROUTE, SCHEMA


def _read_archive(root, run, task, round_id):
    """Read an opaque native archive, never extract or inspect its members."""
    root = root.resolve(strict=True)
    path = root / "runs" / run / task / "submissions" / round_id / "submission.tar.gz"
    if not path.is_relative_to(root) or path.resolve(strict=True) != path:
        raise ValueError("Native incumbent archive must be a physical file below the log owner")
    with os.fdopen(os.open(path, os.O_RDONLY | os.O_NOFOLLOW), "rb") as source:
        before = os.fstat(source.fileno())
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Native incumbent archive must be a regular file")
        raw = source.read()
        after = os.fstat(source.fileno())
    observed = path.stat(follow_symlinks=False)
    def signature(value):
        return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
    if (signature(before) != signature(after) or signature(after) != signature(observed) or
            path.resolve(strict=True) != path or len(raw) != before.st_size):
        raise ValueError("Native incumbent archive moved during capture")
    return raw


def create_app(config, *, checkpoint_capture=False):
    if type(checkpoint_capture) is not bool:
        raise ValueError("checkpoint_capture must be an explicit boolean")
    from sforge.harness.judge_server import SubmissionStatus, create_app as native_app
    app = native_app(config)
    if not checkpoint_capture:
        return app  # Preserve the native route/admission surface when disabled.
    from fastapi import HTTPException
    from fastapi.responses import Response
    from pydantic import BaseModel, ConfigDict
    import sforge

    state = getattr(app.state, "judge", None)
    if state is None or not all(hasattr(state, key) for key in (
            "_admission_lock", "_outstanding_by_run", "_tokens_lock", "_history_lock")):
        raise ValueError("Checkpoint capture requires a pinned SForge with its native admission owner exposed")
    epoch = uuid.uuid4().hex
    source = hashlib.sha256()
    source_root = Path(sforge.__file__).parent
    for path in sorted(source_root.rglob("*.py")):
        source.update(path.relative_to(source_root).as_posix().encode() + b"\0")
        source.update(hashlib.sha256(path.read_bytes()).digest())
    tasks = {key: {
        "native_source_sha256": source.hexdigest(),
        "task_spec_sha256": hashlib.sha256((config.tasks_dir / f"{key}.json").read_bytes()).hexdigest(),
        "judge_image_key": task.judge_image_key,
        "selection": task.judge.selection, "score_direction": task.judge.score_direction,
    } for key, task in state.tasks.items()}

    class ReadRequest(BaseModel):
        model_config = ConfigDict(extra="forbid")
        token: str

    class ArchiveRequest(ReadRequest):
        capture_binding_sha256: str

    # FastAPI resolves annotations in module globals; keep this factory's
    # private request type concrete rather than a forward reference.
    def snapshot_locked(token):
        from sforge.harness.selection import select_best

        # Reuse the native lock order and outstanding admission counter. A
        # pending grade or unexplained consumed round is never a complete point.
        # Called only under the native admission -> token -> history locks.
        info = state.tokens.get(token)
        if info is None:
            raise HTTPException(403, "Unknown registration")
        run, task = info["run_id"], info["task_id"]
        if state._closed or state.tasks[task].game_mode:
            raise HTTPException(409, "Checkpoint requires an open non-game registration")
        if state._outstanding_by_run.get((run, task), 0):
            raise HTTPException(409, "Accepted evaluation is still outstanding")
        if info["next_auto"] != 1:
            raise HTTPException(409, "This checkpoint profile requires no automatic evaluations")
        history = state.run_history.get(state._run_key(run, task), [])
        rounds = [entry.get("round") for entry in history]
        expected = {f"agent-{index}" for index in range(1, info["next_agent"])}
        if (len(rounds) != len(expected) or set(rounds) != expected or
                any(entry.get("status") not in {SubmissionStatus.COMPLETED, SubmissionStatus.ERROR} for entry in history)):
            raise HTTPException(409, "Consumed rounds and released history are not reconciled")
        results = {}
        for entry in history:
            identifier = entry.get("submission_id")
            native = state.submissions.get(identifier)
            if (native is None or native.get("run_id") != run or native.get("task_id") != task or
                    native.get("round") != entry["round"] or native.get("status") != entry["status"]):
                raise HTTPException(409, "Released result details are unavailable or differ")
            results[identifier] = {key: native.get(key) for key in ("status", "report", "error")}
        # Reuse native ranking, including validity, direction and tie rules.
        # The working candidate can differ from this older selected archive.
        best = select_best(history, state.tasks[task].judge.score_direction,
                           state.tasks[task].judge.selection)
        incumbent = None
        raw = None
        if best["best_round"]:
            selected = next(entry for entry in history if entry["round"] == best["best_round"])
            try:
                raw = _read_archive(config.log_dir, run, task, selected["round"])
            except (OSError, ValueError) as error:
                raise HTTPException(409, "Native selected incumbent archive is unavailable or moved") from error
            incumbent = {"round": selected["round"], "submission_id": selected["submission_id"],
                         "archive_sha256": hashlib.sha256(raw).hexdigest(), "archive_bytes": len(raw)}
        observed = time.time()
        last = info["last_agent_submit_at"]
        cooldown = info["submission_cooldown"]
        remaining = max(0, last + (cooldown or 0) - observed) if last else 0
        packet = {
            "schema_version": SCHEMA, "judge_epoch": epoch,
            "run_id": run, "task_id": task, "evaluator": tasks[task],
            "observed_at_epoch": observed, "last_agent_submit_at_epoch": last,
            "submission_cooldown_seconds": cooldown,
            "remaining_cooldown_seconds": remaining,
            "max_agent_submissions": info["max_agent_submissions"],
            "next_agent_round": info["next_agent"], "next_auto_round": info["next_auto"],
            "released_history": history, "released_results": results, "outstanding_evaluations": 0,
            "native_incumbent": incumbent,
            "execution_authority_granted": False,
        }
        # Bind both reads to the same released state, not a wall clock. A new
        # accepted round, source epoch or changed archive invalidates download.
        binding = {key: value for key, value in packet.items()
                   if key not in {"observed_at_epoch", "remaining_cooldown_seconds"}}
        packet["capture_binding_sha256"] = hashlib.sha256(
            json.dumps(binding, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return json.loads(json.dumps(packet)), raw  # No live references or tokens leave the lock.

    def capture(request):
        with state._admission_lock, state._tokens_lock, state._history_lock:
            return snapshot_locked(request.token)[0]

    def archive(request):
        with state._admission_lock, state._tokens_lock, state._history_lock:
            packet, raw = snapshot_locked(request.token)
            incumbent = packet["native_incumbent"]
            if packet["capture_binding_sha256"] != request.capture_binding_sha256 or incumbent is None:
                raise HTTPException(409, "Native checkpoint basis moved or has no selected incumbent")
            return Response(raw, media_type="application/octet-stream")
    capture.__annotations__["request"] = ReadRequest
    archive.__annotations__["request"] = ArchiveRequest
    app.post(ROUTE)(capture)
    app.post(ARCHIVE_ROUTE)(archive)
    return app


def main(argv=None):
    import uvicorn
    from sforge.harness.config import load_config
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint-capture", action="store_true")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--tasks-dir", type=Path)
    parser.add_argument("--log-dir", type=Path)
    args = parser.parse_args(argv)
    config = load_config()
    overrides = {key: getattr(args, key) for key in ("tasks_dir", "log_dir") if getattr(args, key) is not None}
    uvicorn.run(create_app(replace(config, **overrides), checkpoint_capture=args.checkpoint_capture),
                host=args.host, port=args.port, access_log=False)


if __name__ == "__main__":
    main()
