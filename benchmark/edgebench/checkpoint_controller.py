"""Controller-only transport for the native SForge checkpoint API.

Native admission/ranking retain their native owner. The existing typed artifact
owner seals and verifies the bank bytes. Nothing here belongs in a solver payload.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
import re
import shutil
import stat
import tempfile
import time
from time import monotonic, sleep
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from loopx.control_plane.effect_runtime import effect_runtime_result
from benchmark.runtime.worker import PHASE_WORK_RESERVE_SECONDS


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward controller credentials to another origin.


def _physical(path: Path) -> Path:
    path = Path(path)
    if not path.is_absolute() or path.resolve(strict=True) != path:
        raise ValueError("Controller paths must be normalized, physical and absolute")
    return path


def _controller(path: Path) -> dict:
    path = _physical(path)
    if path.stat().st_mode & 0o077:
        raise ValueError("Controller configuration must be owner-only")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or set(value) != {"url", "admin_secret", "bank_root"}:
        raise ValueError("Controller configuration requires url, admin_secret and bank_root")
    if not isinstance(value["admin_secret"], str) or not value["admin_secret"]:
        raise ValueError("Fresh controller authority is unavailable")
    url = urlsplit(value["url"])
    if (url.scheme not in {"http", "https"} or not url.hostname or url.username or
            url.password or url.query or url.fragment or url.path not in {"", "/"}):
        raise ValueError("Controller URL must identify one explicit HTTP origin")
    value["bank_root"] = _physical(Path(value["bank_root"]))
    return value


def _post(controller: dict, route: str, body: dict, timeout: float) -> dict:
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Controller timeout must be finite and positive")
    request = Request(controller["url"].rstrip("/") + route,
        data=json.dumps({**body, "admin_secret": controller["admin_secret"]}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        # Controller authority belongs to this explicit origin. Environment
        # proxies must not redirect private native traffic or its credentials.
        with build_opener(ProxyHandler({}), _NoRedirect).open(request, timeout=timeout) as response:
            result = json.load(response)
    except HTTPError as error:
        # A remote body can echo submitted credentials. Keep only the status.
        raise ValueError(f"Native checkpoint operation rejected: HTTP {error.code}") from None
    if not isinstance(result, dict):
        raise ValueError("Native checkpoint operation returned an invalid object")
    return result


def _reference(value: dict) -> dict:
    if (set(value) != {"checkpoint_id", "manifest_sha256", "task_id", "source_run_id", "opportunity", "incumbent"} or
            not re.fullmatch(r"[0-9a-f]{32}", value.get("checkpoint_id", "")) or
            not re.fullmatch(r"[0-9a-f]{64}", value.get("manifest_sha256", ""))):
        raise ValueError("Native checkpoint reference is malformed")
    return value


def _bound_bank(folder: Path, reference: dict) -> dict:
    """Byte/source binding only; native restore owns eligibility and validity."""
    raw = _physical(folder / "manifest.json").read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    witnessed = _physical(folder / "manifest.sha256").read_text()
    packet = json.loads(raw)
    if (digest != reference["manifest_sha256"] or witnessed != digest or
            packet.get("schema_version") != "sforge_judge_checkpoint_v1" or
            any(packet.get(key) != reference[key] for key in (
                "task_id", "source_run_id", "opportunity", "incumbent"))):
        raise ValueError("Native bank bytes differ from the captured reference")
    expected = {"manifest.json", "manifest.sha256"}
    incumbent = reference["incumbent"]
    if incumbent is not None:
        expected.add("incumbent.tar.gz")
        archive = _physical(folder / "incumbent.tar.gz").read_bytes()
        if (len(archive) != incumbent["archive_bytes"] or
                hashlib.sha256(archive).hexdigest() != incumbent["archive_sha256"]):
            raise ValueError("Native selected archive differs from the captured reference")
    if {p.name for p in folder.iterdir()} != expected:
        raise ValueError("Native bank contains undeclared members")
    return packet


def _phase_budget(raw: bytes, native: dict) -> dict:
    """Adapt the existing SForge integer phase clock, not quota authority.

    Native capture owns this time. The declared physical Host file owns its
    deadline. Neither fact qualifies complete Host/research-state capture.
    """
    if len(raw) > 64 or not re.fullmatch(rb"[0-9]+\n?", raw):
        raise ValueError("Host phase deadline must be an integer epoch file")
    epoch = int(raw)
    captured = native.get("captured_at_epoch")
    if (not 0 < epoch < 2 ** 53 or type(captured) not in (int, float) or
            not math.isfinite(captured) or captured <= 0):
        raise ValueError("Host/native phase clocks cannot be represented exactly")
    return {"component_id": "host_phase_deadline", "source_deadline_epoch": epoch,
            "captured_at_epoch": captured, "remaining_seconds": max(0, epoch - captured)}


def _phase_destination(path: Path, *, root: Path, bank: Path, recover: bool = False) -> Path:
    path = Path(path)
    _physical(path.parent)
    if (not path.is_absolute() or path.resolve() != path or (path.exists() and not recover) or path.is_symlink()
            or path.is_relative_to(root) or path.is_relative_to(bank)):
        raise ValueError("Host phase restoration requires a fresh physical file outside artifact/bank roots")
    return path


def _private_file(path: Path) -> None:
    info = _physical(path).stat()
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
        raise ValueError("Recovery requires an owner-only physical regular file")


def _sync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _activation_epoch(path: Path, binding: dict, remaining: float, recover: bool) -> int:
    """Provider-local attempt binding; no token or historical authority is stored."""
    record = path.with_name(path.name + ".restore.json")
    if recover:
        _private_file(record)
        value = json.loads(record.read_bytes())
        if (not isinstance(value, dict) or set(value) != {"binding", "deadline_epoch"}
                or value["binding"] != binding):
            raise ValueError("Host recovery does not match the original activation")
        epoch = value["deadline_epoch"]
    else:
        epoch = math.floor(time.time() + remaining)
        # Commit this before contacting native admission. A partial record
        # cannot be recovered, and retry never computes another deadline.
        with os.fdopen(os.open(record, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w") as target:
            json.dump({"binding": binding, "deadline_epoch": epoch}, target)
            target.flush()
            os.fsync(target.fileno())
        _sync_parent(record)
    if type(epoch) is not int or not 0 < epoch < 2 ** 53:
        raise ValueError("Destination Host phase clock is incompatible")
    if epoch - time.time() <= PHASE_WORK_RESERVE_SECONDS:
        raise ValueError("Original Host activation budget is exhausted")
    return epoch


def _activation_result(result: dict, body: dict) -> None:
    if (set(result) != {*body, "token"} or any(result.get(k) != v for k, v in body.items())
            or not isinstance(result.get("token"), str) or not result["token"]):
        raise ValueError("Native activation result differs from the original request")


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("Checkpoint capture window closed")
    return remaining


def capture(controller_file: Path, *, token: str, capture_id: str, destination: Path,
            timeout: float = 30, phase_deadline_file: Path | None = None) -> dict:
    """Capture one native-owned point without exporting controller configuration."""
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Checkpoint capture timeout must be finite and positive")
    deadline = time.monotonic() + timeout
    if not re.fullmatch(r"[0-9a-f]{32}", capture_id):
        raise ValueError("Capture identity must be the declared 32-hex native identity")
    destination = Path(destination)
    _physical(destination.parent)
    if not destination.is_absolute() or destination != destination.resolve() or destination.exists():
        raise ValueError("Capture export requires a fresh physical destination")
    controller = _controller(controller_file)
    phase_raw = None
    if phase_deadline_file is not None:
        phase_deadline_file = _physical(phase_deadline_file)
        phase_raw = phase_deadline_file.read_bytes()
        _phase_budget(phase_raw, {"captured_at_epoch": time.time()})
        # An earlier native-only point cannot acquire a later Host clock.
        if (controller["bank_root"] / capture_id).exists():
            raise ValueError("Existing native point has no new Host phase capture boundary")
        attempts = controller["bank_root"] / ".loopx-phase-attempts"
        attempts.mkdir(mode=0o700, exist_ok=True)
        _physical(attempts)
        # First attempt wins, including lost responses or failed exports.
        # A later source cannot replace the Host clock at that native point.
        (attempts / capture_id).mkdir(mode=0o700)
    reference = _reference(_post(controller, "/api/v1/checkpoint/capture",
        {"token": token, "capture_id": capture_id}, _remaining(deadline)))
    if reference["checkpoint_id"] != capture_id:
        raise ValueError("Native capture returned a different identity")
    folder = _physical(controller["bank_root"] / capture_id)
    native = _bound_bank(folder, reference)
    components = [{"id": "native_judge", "source_path": str(folder)}]
    metadata = {"native_reference": reference}
    if phase_raw is not None:
        if phase_deadline_file.read_bytes() != phase_raw:
            raise ValueError("Host phase deadline moved across native capture")
        components.append({"id": "host_phase_deadline", "source_path": str(phase_deadline_file)})
        metadata["host_phase_budget"] = _phase_budget(phase_raw, native)
    probe = effect_runtime_result("runtime.checkpoint_artifacts", {"action": "probe", "components": components},
                                  timeout=_remaining(deadline), retry_safe=False, large_local_snapshot=True)
    result = effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "capture", "components": components, "destination": str(destination),
        "expected_source_sha256": probe["source_sha256"],
        "point_metadata": metadata,
    }, timeout=_remaining(deadline), retry_safe=False, large_local_snapshot=True)
    # Bind the actually copied bytes, not merely a prior source observation.
    _bound_bank(destination / "components/native_judge", reference)
    if phase_raw is not None and (destination / "components/host_phase_deadline").read_bytes() != phase_raw:
        raise ValueError("Captured Host phase bytes differ from the native boundary")
    return {**result, "native_reference": reference}


@contextmanager
def _bank_import_lock(target: Path, timeout: float):
    """Serialize publication of one provider bank member, never run admission.

    Keep the lock inode: unlinking it would let a third importer bypass a waiter.
    Staging stays outside the lock; partial destinations still fail verification.
    """
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Native bank import timeout must be finite and positive")
    lock = target.with_name(".loopx-import-" + target.name + ".lock")
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_nlink != 1:
            raise ValueError("Native bank import lock must be an owner-only regular file")
        deadline = monotonic() + timeout
        while True:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError("Native bank import is busy; inspect the original attempt before retry")
                sleep(min(.02, remaining))
        yield
    finally:
        os.close(descriptor)


def restore(controller_file: Path, *, root: Path, expected_sha256: str, run_id: str,
            timeout: float = 30, phase_deadline_file: Path | None = None, recover: bool = False,
            phase_extension_seconds: int = 0) -> dict:
    """Import verified inert bytes, then ask fresh native authority to admit them.

    An explicitly captured Host clock preserves its remaining phase duration.
    An operator can explicitly grant additional continuation time for a new
    study. The grant is bound to the first activation, never to recovery time;
    it changes no native cooldown, round limit, history or scoring rule.
    Does not restore a model, LoopX Goal or effect authority.
    An uncertain response is not replayed under a different run or capture id.
    """
    if type(phase_extension_seconds) is not int or not 0 <= phase_extension_seconds < 2 ** 53:
        raise ValueError("Continuation phase extension must be a non-negative integer number of seconds")
    if phase_extension_seconds and phase_deadline_file is None:
        raise ValueError("Continuation phase extension requires the captured Host phase clock")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id):
        raise ValueError("Continuation requires one explicit native run component")
    controller = _controller(controller_file)
    root = _physical(root)
    effect_runtime_result("runtime.checkpoint_artifacts", {
        "action": "verify", "root": str(root), "expected_sha256": expected_sha256}, large_local_snapshot=True)
    manifest = json.loads((root / "checkpoint.json").read_bytes())
    expected_components = ["native_judge"]
    if phase_deadline_file is not None:
        expected_components.append("host_phase_deadline")
    if [c["id"] for c in manifest["components"]] != expected_components:
        raise ValueError("Native import requires its declared capsule and explicit matching Host clock")
    reference = _reference(manifest["point_metadata"]["native_reference"])
    if run_id == reference["source_run_id"]:
        raise ValueError("Continuation requires a fresh run identity")
    folder = root / "components/native_judge"
    native = _bound_bank(folder, reference)
    bank = controller["bank_root"]
    phase_path = None
    phase = None
    if phase_deadline_file is not None:
        phase = _phase_budget((root / "components/host_phase_deadline").read_bytes(), native)
        if manifest["point_metadata"].get("host_phase_budget") != phase:
            raise ValueError("Host phase metadata differs from sealed deadline/native clock")
        if phase["remaining_seconds"] + phase_extension_seconds <= PHASE_WORK_RESERVE_SECONDS:
            raise ValueError("Captured Host phase has no work allowance beyond its existing reserve")
        phase_path = _phase_destination(phase_deadline_file, root=root, bank=bank, recover=recover)
    target = bank / reference["checkpoint_id"]
    stage = Path(tempfile.mkdtemp(prefix=".loopx-native-import-", dir=bank))
    phase_fd = None
    phase_published = False
    phase_locked = False
    native_attempted = False
    try:
        if phase_path is not None:
            # Empty is unusable by the existing SForge phase-entry shell.
            # An uncertain native response must not fall back to a new budget.
            if recover:
                _private_file(phase_path)
            phase_fd = os.open(phase_path, os.O_RDWR | os.O_NOFOLLOW | (0 if recover else os.O_CREAT | os.O_EXCL), 0o600)
            fcntl.flock(phase_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            phase_locked = True
            original_phase = phase_path.read_bytes()
            phase_published = bool(original_phase)
        if recover:
            _bound_bank(_physical(target), reference)
        else:
            effect_runtime_result("runtime.checkpoint_artifacts", {
                "action": "materialize", "root": str(root), "expected_sha256": expected_sha256,
                "destination": str(stage / "unit"),
            }, retry_safe=False, large_local_snapshot=True)
            imported = stage / "unit/components/native_judge"
            _bound_bank(imported, reference)
            with _bank_import_lock(target, timeout):
                if target.exists():
                    _bound_bank(_physical(target), reference)  # No overwrite, even on retry.
                else:
                    # POSIX rename can replace an empty destination. Reserve its name
                    # exclusively; interruption leaves an unusable point, never grants.
                    target.mkdir(mode=0o700)
                    try:
                        imported.rename(target)
                    except BaseException:
                        target.rmdir()
                        raise
                    descriptor = os.open(bank, os.O_RDONLY | os.O_DIRECTORY)
                    try:
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
        body = {"checkpoint_id": reference["checkpoint_id"],
                "manifest_sha256": reference["manifest_sha256"], "run_id": run_id}
        phase_epoch = None
        if phase_path is not None:
            phase_epoch = _activation_epoch(phase_path, {
                **body, "artifact_sha256": expected_sha256, "controller_url": controller["url"],
                "bank_root": str(bank), "phase_path": str(phase_path),
                # Keep pre-existing zero-extension receipt bytes recoverable.
                **({"phase_extension_seconds": phase_extension_seconds} if phase_extension_seconds else {}),
            }, phase["remaining_seconds"] + phase_extension_seconds, recover)
            if original_phase not in (b"", f"{phase_epoch}\n".encode()):
                raise ValueError("Host phase bytes differ from original activation")
        native_attempted = True
        route = "/api/v1/checkpoint/restore-result" if recover else "/api/v1/checkpoint/restore"
        result = _post(controller, route, body, timeout)
        _activation_result(result, body)
        if phase_fd is not None:
            if phase_epoch - time.time() <= PHASE_WORK_RESERVE_SECONDS:
                raise ValueError("Original Host activation budget is exhausted")
            # Charge activation/transport, not offline time or a fresh trial.
            # Native alone owns accepted counters and remaining cooldown.
            encoded = f"{phase_epoch}\n".encode()
            os.lseek(phase_fd, 0, os.SEEK_SET)
            if os.write(phase_fd, encoded) != len(encoded):
                raise OSError("Destination Host phase write was incomplete")
            os.fsync(phase_fd)
            _sync_parent(phase_path)
            if phase_path.read_bytes() != f"{phase_epoch}\n".encode():
                raise ValueError("Destination Host phase readback differs from activation")
            phase_published = True
        return result
    finally:
        if phase_fd is not None:
            try:
                if phase_locked and native_attempted and not phase_published:
                    os.ftruncate(phase_fd, 0)
                    os.fsync(phase_fd)
            finally:
                os.close(phase_fd)
            if phase_locked and not native_attempted and not recover:
                phase_path.unlink()
        shutil.rmtree(stage)


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller-file", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    capture_parser = commands.add_parser("capture")
    capture_parser.add_argument("--token-file", required=True, type=Path)
    capture_parser.add_argument("--capture-id", required=True)
    capture_parser.add_argument("--destination", required=True, type=Path)
    capture_parser.add_argument("--phase-deadline-file", type=Path,
                                help="Explicit physical SForge Host phase file; omitted keeps native-only capture")
    worker_parser = commands.add_parser("capture-worker",
        help="Controller-owned Docker pause/archive plus native capsule; inert files, not process restoration")
    worker_parser.add_argument("--token-file", required=True, type=Path)
    worker_parser.add_argument("--capture-id", required=True)
    worker_parser.add_argument("--destination", required=True, type=Path)
    worker_parser.add_argument("--worker-request", required=True, type=Path)
    restore_parser = commands.add_parser("restore")
    restore_parser.add_argument("--root", required=True, type=Path)
    restore_parser.add_argument("--expected-sha256", required=True)
    restore_parser.add_argument("--run-id", required=True)
    restore_parser.add_argument("--result-file", required=True, type=Path)
    restore_parser.add_argument("--phase-deadline-file", type=Path,
                                help="Fresh physical Host phase file, required for a capsule containing that participant")
    restore_parser.add_argument("--recover", action="store_true",
                                help="Read the exact original native result and retain its original Host deadline")
    restore_parser.add_argument("--phase-extension-seconds", type=int, default=0,
                                help="Explicit operator-authorized extra time for a new continuation study; recovery never refills it")
    args = parser.parse_args(argv)
    if args.command in {"capture", "capture-worker"}:
        source_token = _physical(args.token_file).read_text().strip()
        if args.command == "capture-worker":
            from benchmark.edgebench.checkpoint_worker import capture_worker

            result = capture_worker(args.controller_file, token=source_token,
                capture_id=args.capture_id, destination=args.destination,
                request_file=args.worker_request)
        else:
            result = capture(args.controller_file, token=source_token,
                             capture_id=args.capture_id, destination=args.destination,
                             phase_deadline_file=args.phase_deadline_file)
        print(json.dumps(result))
    else:
        # Reserve before admission. A lost output is explicit recovery, never a
        # fresh registration that silently refills the captured opportunity.
        _physical(args.result_file.parent)
        if args.result_file != args.result_file.resolve():
            raise ValueError("Continuation result must be a normalized physical path")
        if args.recover:
            _private_file(args.result_file)
        with args.result_file.open("r+" if args.recover else "x") as result_file:
            args.result_file.chmod(0o600)
            fcntl.flock(result_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            prior_raw = result_file.read() if args.recover else ""
            prior_result = json.loads(prior_raw) if prior_raw else None
            result = restore(args.controller_file, root=args.root,
                expected_sha256=args.expected_sha256, run_id=args.run_id,
                phase_deadline_file=args.phase_deadline_file, recover=args.recover,
                phase_extension_seconds=args.phase_extension_seconds)
            if prior_result is not None and prior_result != result:
                raise ValueError("Existing private result differs from original native activation")
            result_file.seek(0)
            json.dump(result, result_file)
            result_file.truncate()
            result_file.flush()
            os.fsync(result_file.fileno())
            _sync_parent(args.result_file)
        print(json.dumps({"result_file": str(args.result_file), "run_id": args.run_id}))


if __name__ == "__main__":
    main()
