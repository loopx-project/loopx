"""Private observation/worker IO for the existing update command.

An observer leaving does not cancel an installer. A kernel lock, held by the
worker (not the observer), prevents a reconnect from launching another update.
No Goal/Agent state is copied here. Installation decisions remain in self_update.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
from uuid import uuid4

from ..file_lock import exclusive_file_lock, process_is_alive
from .update_artifact import atomic_json


def operation_root() -> Path:
    root = Path(os.environ.get("LOOPX_UPDATE_STATE_DIR") or Path.home() / ".local/state/loopx/updates")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    return root


def installation_key() -> str:
    # Two registries updating the same executable must still share one lock.
    from ..self_update import _user_loopx_bin
    package_root = Path(__file__).resolve().parents[2]
    if (package_root / "release.json").is_file():
        entry = Path(os.environ.get("LOOPX_BIN_DIR") or _user_loopx_bin().parent) / "loopx"
        identity = str(entry.absolute())
    else:
        identity = str(Path(sys.executable).absolute()) + ":" + str(package_root)
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def read_operation(operation_id: str | None = None) -> dict:
    root = operation_root()
    if operation_id is None:
        try:
            operation_id = json.loads((root / f"{installation_key()}.active.json").read_text())["operation_id"]
        except (OSError, ValueError, KeyError):
            return {"ok": True, "status": "none", "operation_id": None}
    if not re.fullmatch(r"[a-f0-9]{32}", operation_id):
        raise ValueError("Invalid update operation id")
    folder = root / operation_id
    try:
        state = json.loads((folder / "status.json").read_text())
    except (OSError, ValueError):
        state = {"operation_id": operation_id, "status": "unavailable", "ok": False}
    result_file = folder / "result.json"
    if result_file.is_file():
        try:
            result = json.loads(result_file.read_text())
            state.update(status="succeeded" if result.get("ok") else "failed", ok=bool(result.get("ok")), result=result)
        except (OSError, ValueError):
            state.update(status="interrupted", ok=False)
    elif state.get("status") in {"running", "starting"}:
        # PID alone is not sufficient proof of work after a reboot/PID reuse.
        try:
            worker = json.loads((folder / "worker.json").read_text())
        except (OSError, ValueError):
            worker = {}
        observed = process_is_alive(state.get("pid") or worker.get("pid")) and time.time() - state.get("updated_at", 0) < 30
        state.update(ok=observed, status="running" if observed else "interrupted")
    return state


def _argv(args: argparse.Namespace, registry_path: Path, registry_was_supplied: bool) -> list[str]:
    argv = ["--format", "json"]
    if registry_was_supplied:
        argv += ["--registry", str(registry_path)]
    if args.runtime_root:
        argv += ["--runtime-root", str(args.runtime_root)]
    argv += [args.command, "apply", "--timeout-seconds", str(args.timeout_seconds)]
    for name in ("repo", "ref", "archive_url"):
        if getattr(args, name, None):
            argv += ["--" + name.replace("_", "-"), str(getattr(args, name))]
    for name in ("source", "allow_downgrade"):
        if getattr(args, name, False):
            argv.append("--" + name.replace("_", "-"))
    return argv


def start_or_observe(args: argparse.Namespace, registry_path: Path, *, registry_was_supplied: bool = False) -> dict:
    root = operation_root()
    key = installation_key()
    with exclusive_file_lock(root / (key + ".launch"), timeout_seconds=5, operation="update_launch"):
        current = read_operation()
        if current.get("status") == "running":
            return dict(current, reattached=True)
        try:
            with exclusive_file_lock(root / (key + ".worker"), timeout_seconds=0, operation="update_probe"):
                pass
        except TimeoutError:
            # A stale observation is not permission to replay an installation.
            return dict(current, ok=True, status="running", reattached=True,
                observation_unavailable=True)
        operation_id = uuid4().hex
        folder = root / operation_id
        folder.mkdir(mode=0o700)
        atomic_json(folder / "request.json", {"argv": _argv(args, registry_path, registry_was_supplied), "installation_key": key})
        atomic_json(folder / "status.json", {"schema_version": "loopx_update_progress_v1",
            "operation_id": operation_id, "status": "starting", "stage": "resolve", "updated_at": time.time()})
        # Separate process/session and files survive an Agent, terminal or HTTP
        # observer disconnect. No pipe backpressure can block the installation.
        with (folder / "worker.log").open("wb") as log:
            os.chmod(folder / "worker.log", 0o600)
            options = {"start_new_session": True} if os.name != "nt" else {
                "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
            child = subprocess.Popen([sys.executable, "-m", __name__, "--worker", operation_id],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
        atomic_json(root / f"{key}.active.json", {"operation_id": operation_id, "pid": child.pid})
        atomic_json(folder / "worker.json", {"pid": child.pid})
        return {"ok": True, "status": "running", "stage": "resolve", "operation_id": operation_id,
            "pid": child.pid, "reattached": False}


def observe(operation_id: str, *, json_output: bool = False) -> dict:
    previous = None
    while True:
        state = read_operation(operation_id)
        marker = (state.get("updated_at"), state.get("status"))
        if marker != previous:
            try:
                if json_output:
                    print(json.dumps({k: v for k, v in state.items() if k != "result"}), file=sys.stderr, flush=True)
                else:
                    received, total = state.get("received"), state.get("total")
                    size = f" · {received}/{total if total is not None else '?'} bytes" if received is not None else ""
                    waiting = " · no new progress for 10s" if state.get("stalled") else ""
                    print(f"LoopX [{operation_id[:8]}]: {state.get('stage', state['status'])}{size}{waiting}", file=sys.stderr, flush=True)
            except (OSError, BrokenPipeError):
                return {"ok": True, "operation_id": operation_id, "status": "running", "observer_disconnected": True}
            previous = marker
        if state.get("status") not in {"running", "starting"}:
            result = state.get("result") or {"ok": False, "error": "Update worker interrupted; inspect this operation before retrying"}
            return dict(result, operation_id=operation_id, operation_status=state["status"])
        time.sleep(.25)


def worker(operation_id: str) -> int:
    folder = operation_root() / operation_id
    request = json.loads((folder / "request.json").read_text())
    # The lifetime lock covers planning, downloading, install and readback.
    with exclusive_file_lock(operation_root() / (request["installation_key"] + ".worker"),
                             timeout_seconds=0, operation="update_worker"):
        os.environ["LOOPX_UPDATE_OPERATION_ID"] = operation_id
        os.environ["LOOPX_UPDATE_STATUS_PATH"] = str(folder / "status.json")
        os.environ["LOOPX_UPDATE_WORKER"] = "1"
        from ..cli import main
        from contextlib import redirect_stdout
        import io
        output = io.StringIO()
        try:
            with redirect_stdout(output):
                code = main(request["argv"])
            payload = json.loads(output.getvalue())
        except BaseException:
            payload = {"ok": False, "changes_applied": False,
                "error": "Update worker failed; inspect private worker.log and installed doctor before retrying"}
            code = 1
        try:
            progress = json.loads((folder / "status.json").read_text())
            progress.update(status="succeeded" if payload.get("ok") else "failed", updated_at=time.time())
            atomic_json(folder / "status.json", progress)
        except (OSError, ValueError):
            pass
        atomic_json(folder / "result.json", payload)
        return code or 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", required=True)
    raise SystemExit(worker(parser.parse_args().worker))
