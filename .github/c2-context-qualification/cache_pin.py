"""Hold immutable source and runtime file pages in memory for one command."""
import ctypes
import hashlib
import json
import mmap
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
PAGE = os.sysconf("SC_PAGE_SIZE")
MAX_BYTES = 2 * 1024**3
SETUP_SECONDS = 60
COMMAND_SECONDS = 80 * 60
REPORT = ROOT / "cache-control-report.json"
LIBC = ctypes.CDLL(None, use_errno=True)
LIBC.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int,
                      ctypes.c_int, ctypes.c_int, ctypes.c_longlong]
LIBC.mmap.restype = ctypes.c_void_p
for _name in ("mlock", "munlock", "munmap", "mincore"):
    getattr(LIBC, _name).restype = ctypes.c_int
for _name in ("mlock", "munlock", "munmap"):
    getattr(LIBC, _name).argtypes = [ctypes.c_void_p, ctypes.c_size_t]
LIBC.mincore.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p]


def check(code, operation):
    if code != 0:
        error = ctypes.get_errno()
        raise OSError(error, f"{operation}: {os.strerror(error)}")


def vm_locked_bytes():
    line = next(row for row in Path("/proc/self/status").read_text().splitlines()
                if row.startswith("VmLck:"))
    return int(line.split()[1]) * 1024


def resident(address, length):
    pages = (length + PAGE - 1) // PAGE
    vector = (ctypes.c_ubyte * pages)()
    check(LIBC.mincore(address, length, vector), "mincore")
    if not all(value & 1 for value in vector):
        raise RuntimeError("one or more locked pages are not resident")


def files_under(root):
    if not root.exists():
        raise FileNotFoundError(f"cache root missing: {root}")
    if root.is_file():
        yield root
        return
    for directory, dirs, names in os.walk(root, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(directory) / name).is_symlink()]
        for name in names:
            path = Path(directory) / name
            if path.is_file():
                yield path


def roots(config):
    result = []
    for key in ("baseline_source", "candidate_source"):
        source = Path(config[key])
        result.extend(source / name for name in ("loopx", ".venv", "node_modules"))
    result.extend([
        Path(config["cache_python_base_lib"]),
        Path(config["node_bin"]) / "node",
        ROOT / "clock-hooks",
        ROOT / "bin",
    ])
    return result


def benchmark_environment(config):
    environment = config["benchmark_identity"]["environment"]
    if any(not environment.get(key) for key in ("HOME", "USER", "LOGNAME", "PATH")):
        raise ValueError("benchmark identity must record HOME, USER, LOGNAME, and PATH")
    return {key: value for key, value in environment.items() if value is not None}


def process_identity(pid):
    fields = {line.split(":", 1)[0]: line.split(":", 1)[1].strip()
              for line in Path(f"/proc/{pid}/status").read_text().splitlines()
              if line.startswith(("Uid:", "Gid:", "Groups:"))}
    stat = Path(f"/proc/{pid}/stat").read_text()
    return {"pid": pid, "starttime_ticks": int(stat[stat.rfind(")") + 2:].split()[19]),
            "uid": int(fields["Uid"].split()[0]),
            "gid": int(fields["Gid"].split()[0]),
            "uid_slots": list(map(int, fields["Uid"].split())),
            "gid_slots": list(map(int, fields["Gid"].split())),
            "groups": [int(value) for value in fields.get("Groups", "").split()]}


def stop_command_group(child):
    """A private session owns the evaluator and its active CLI descendants."""
    def present():
        try:
            os.killpg(child.pid, 0)
            return True
        except ProcessLookupError:
            return False
    if present():
        try:
            os.killpg(child.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + 5
        while present() and time.monotonic() < deadline:
            child.poll()
            time.sleep(.05)
        if present():
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    child.wait(timeout=5)
    deadline = time.monotonic() + 5
    while present() and time.monotonic() < deadline:
        time.sleep(.05)
    if present():
        raise RuntimeError("owned command process group did not disappear")


def identity_self_check(config):
    identity = config["benchmark_identity"]
    expected = {"uid": identity["uid"], "gid": identity["gid"],
                "groups": sorted(identity["extra_groups"])}
    result = subprocess.run(
        [sys.executable, "-c", "import json,os;print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'groups':sorted(os.getgroups())}))"],
        check=True, capture_output=True, text=True, timeout=10,
        user=identity["uid"], group=identity["gid"],
        extra_groups=identity["extra_groups"], env=benchmark_environment(config),
    )
    observed = json.loads(result.stdout)
    if observed != expected:
        raise RuntimeError(f"benchmark UID self-check mismatch: {observed} != {expected}")
    print(json.dumps({"uid_self_check": "passed", **observed}))


def inventory(config):
    files = {}
    permission_checks = 0
    for root in roots(config):
        for path in files_under(root):
            with path.open("rb") as source:
                stat = os.fstat(source.fileno())
                key = (stat.st_dev, stat.st_ino)
                if key in files:
                    continue
                permitted = (stat.st_uid == os.geteuid()
                             or os.access(path, os.W_OK, effective_ids=True))
                if not permitted:
                    raise PermissionError(f"cannot verify residency for {path}")
                permission_checks += 1
                files[key] = {"path": str(path), "dev": stat.st_dev, "ino": stat.st_ino,
                              "size": stat.st_size, "mtime_ns": stat.st_mtime_ns}
    return list(files.values()), permission_checks


def main():
    report = {
        "purpose": "controlled file-page cache; no claim of zero physical I/O",
        "max_pinned_bytes": MAX_BYTES,
        "setup_cap_seconds": SETUP_SECONDS,
        "permission_rule": "same-inode owner or effective write permission",
        "files_locked": 0,
        "cleanup_errors": [],
        "command_returncode": None,
        "command_started": False,
        "holder_uid": os.geteuid(),
    }
    mappings = []
    child = None
    old_limit = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    old_vm_locked = vm_locked_bytes()
    start = time.monotonic()
    command = sys.argv[1:]
    try:
        if not command:
            raise ValueError("pass the benchmark command after --")
        if command[0] == "--":
            command = command[1:]
        if not command:
            raise ValueError("pass the benchmark command after --")
        config = json.loads((ROOT / "evaluation_config.json").read_text())
        report["evaluation_config_sha256"] = hashlib.sha256(json.dumps(config, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (config.get("cache_max_bytes") != MAX_BYTES
                or config.get("cache_max_seconds") != SETUP_SECONDS):
            raise ValueError("cache limits in evaluation_config.json do not match the fixed caps")
        rows, permission_checks = inventory(config)
        (ROOT / "cache-pinned-files.json").write_text(json.dumps(rows, sort_keys=True))
        required = sum(((row["size"] + PAGE - 1) // PAGE) * PAGE for row in rows)
        available = int(next(row.split()[1] for row in Path("/proc/meminfo").read_text().splitlines()
                             if row.startswith("MemAvailable:"))) * 1024
        report.update(required_page_bytes=required, available_bytes=available,
                      residency_permissions_verified=permission_checks,
                      unique_files=len(rows))
        if required > MAX_BYTES:
            raise MemoryError(f"pin set is {required} bytes; cap is {MAX_BYTES}")
        if available < required + 1024**3:
            raise MemoryError("MemAvailable is below rounded pin bytes plus 1 GiB reserve")
        if time.monotonic() - start >= SETUP_SECONDS - 5:
            raise TimeoutError("cache inventory reached setup cap")

        soft, hard = old_limit
        target_soft = soft if soft == resource.RLIM_INFINITY else max(soft, required)
        target_hard = hard if hard == resource.RLIM_INFINITY else max(hard, required)
        resource.setrlimit(resource.RLIMIT_MEMLOCK, (target_soft, target_hard))
        report["memlock_limit_raised"] = resource.getrlimit(resource.RLIMIT_MEMLOCK) != old_limit

        for row in rows:
            if time.monotonic() - start >= SETUP_SECONDS - 5:
                raise TimeoutError("cache pin setup reached 60-second cap")
            with open(row["path"], "rb") as source:
                stat = os.fstat(source.fileno())
                expected = (row["dev"], row["ino"], row["size"], row["mtime_ns"])
                if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != expected:
                    raise RuntimeError(f"file changed after inventory: {row['path']}")
                if not stat.st_size:
                    continue
                address = LIBC.mmap(None, stat.st_size, mmap.PROT_READ,
                                   mmap.MAP_SHARED, source.fileno(), 0)
                if address == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_errno(), f"mmap failed: {row['path']}")
                mappings.append((address, stat.st_size))
                check(LIBC.mlock(address, stat.st_size), f"mlock {row['path']}")
                resident(address, stat.st_size)
                report["files_locked"] += 1
        report["actual_vm_locked_bytes"] = vm_locked_bytes()
        if report["actual_vm_locked_bytes"] - old_vm_locked < required:
            raise RuntimeError("VmLck does not cover all rounded unique file pages")
        for row in rows:
            stat = os.stat(row["path"])
            if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != (
                    row["dev"], row["ino"], row["size"], row["mtime_ns"]):
                raise RuntimeError(f"mapped file changed before command: {row['path']}")
        report["residency_held_before_command"] = True
        report["setup_seconds"] = time.monotonic() - start
        job_started = float(config["qualification_job_started_epoch"])
        remaining = job_started + 90 * 60 - time.time() - 5 * 60
        command_seconds = min(COMMAND_SECONDS, remaining)
        if command_seconds <= 0:
            raise TimeoutError("job budget leaves no measurement time after cleanup/upload reserve")
        report["command_started"] = True
        identity = config["benchmark_identity"]
        report.update(benchmark_uid=identity["uid"], benchmark_gid=identity["gid"],
                      benchmark_groups=identity["extra_groups"],
                      command_timeout_seconds=command_seconds,
                      cleanup_upload_reserve_seconds=300)
        command_env = benchmark_environment(config)
        child = subprocess.Popen(command, user=identity["uid"], group=identity["gid"],
                                 extra_groups=identity["extra_groups"],
                                 env=command_env, start_new_session=True)
        report["benchmark_pid"] = child.pid
        report["benchmark_environment_keys"] = sorted(command_env)
        actual_identity = process_identity(child.pid)
        report["benchmark_process_identity"] = actual_identity
        if (actual_identity["uid"] != identity["uid"]
                or actual_identity["gid"] != identity["gid"]
                or set(actual_identity["uid_slots"]) != {identity["uid"]}
                or set(actual_identity["gid_slots"]) != {identity["gid"]}
                or actual_identity["groups"] != sorted(identity["extra_groups"])):
            raise RuntimeError(f"benchmark process identity mismatch: {actual_identity}")
        try:
            report["command_returncode"] = child.wait(timeout=command_seconds)
        except subprocess.TimeoutExpired as exc:
            report["command_timed_out"] = True
            raise TimeoutError(f"benchmark command exceeded {command_seconds} seconds") from exc
        for row in rows:
            stat = os.stat(row["path"])
            if (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) != (
                    row["dev"], row["ino"], row["size"], row["mtime_ns"]):
                raise RuntimeError(f"mapped file changed during command: {row['path']}")
        for address, length in mappings:
            resident(address, length)
        report["residency_held_after_command"] = True
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        if child is not None:
            try:
                stop_command_group(child)
                report["command_group_gone"] = True
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                report["cleanup_errors"].append(f"stop benchmark command: {exc}")
        if report["command_started"]:
            try:
                identity = config["benchmark_identity"]
                cleanup = subprocess.run(
                    [str(Path(config["candidate_source"]) / ".venv/bin/python"), str(ROOT / "stop_all.py")],
                    user=identity["uid"], group=identity["gid"], extra_groups=identity["extra_groups"],
                    env=benchmark_environment(config), timeout=180, capture_output=True, text=True,
                )
                if cleanup.returncode:
                    raise RuntimeError(cleanup.stderr[-1500:])
                report["normal_user_runtime_cleanup"] = True
            except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
                report["cleanup_errors"].append(f"owned runtime cleanup: {exc}")
        for address, length in reversed(mappings):
            try:
                check(LIBC.munlock(address, length), "munlock")
            except OSError as exc:
                report["cleanup_errors"].append(str(exc))
            try:
                check(LIBC.munmap(address, length), "munmap")
            except OSError as exc:
                report["cleanup_errors"].append(str(exc))
        try:
            resource.setrlimit(resource.RLIMIT_MEMLOCK, old_limit)
        except OSError as exc:
            report["cleanup_errors"].append(f"restore memlock limit: {exc}")
        report["actual_vm_locked_bytes_after"] = vm_locked_bytes()
        report["memlock_limit_restored"] = resource.getrlimit(resource.RLIMIT_MEMLOCK) == old_limit
        report["all_memory_released"] = report["actual_vm_locked_bytes_after"] == old_vm_locked
        report["elapsed_seconds"] = time.monotonic() - start
        REPORT.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2), file=sys.stderr, flush=True)
    if report.get("error") or report["cleanup_errors"] or not report["all_memory_released"]:
        return 1
    return report["command_returncode"] if report["command_returncode"] is not None else 1


def self_check():
    """Check one disposable page only; never pin qualification roots."""
    old = resource.getrlimit(resource.RLIMIT_MEMLOCK)
    old_vm_locked = vm_locked_bytes()
    source = tempfile.TemporaryFile(dir=ROOT)
    address = None
    failure = None
    cleanup_errors = []
    try:
        source.write(b"x" * PAGE)
        source.flush()
        soft, hard = old
        need = PAGE
        resource.setrlimit(resource.RLIMIT_MEMLOCK,
                           (soft if soft == resource.RLIM_INFINITY else max(soft, need),
                            hard if hard == resource.RLIM_INFINITY else max(hard, need)))
        address = LIBC.mmap(None, PAGE, mmap.PROT_READ, mmap.MAP_SHARED, source.fileno(), 0)
        if address == ctypes.c_void_p(-1).value:
            raise OSError(ctypes.get_errno(), "self-check mmap failed")
        check(LIBC.mlock(address, PAGE), "self-check mlock")
        resident(address, PAGE)
        if vm_locked_bytes() - old_vm_locked < PAGE:
            raise RuntimeError("self-check VmLck did not increase by one page")
    except BaseException as exc:
        failure = exc
    finally:
        if address is not None and address != ctypes.c_void_p(-1).value:
            for operation in ("munlock", "munmap"):
                try:
                    check(getattr(LIBC, operation)(address, PAGE), f"self-check {operation}")
                except OSError as exc:
                    cleanup_errors.append(str(exc))
        source.close()
        try:
            resource.setrlimit(resource.RLIMIT_MEMLOCK, old)
        except OSError as exc:
            cleanup_errors.append(f"restore self-check memlock limit: {exc}")
    if resource.getrlimit(resource.RLIMIT_MEMLOCK) != old:
        cleanup_errors.append("self-check memlock limit was not restored")
    if vm_locked_bytes() != 0:
        cleanup_errors.append("self-check VmLck is not zero after cleanup")
    if cleanup_errors:
        raise RuntimeError("; ".join(cleanup_errors)) from failure
    if failure:
        raise failure
    print("self-check passed: 4 KiB resident; VmLck and memlock limit restored")


if __name__ == "__main__":
    if sys.argv[1:] == ["--self-check"]:
        self_check()
        raise SystemExit(0)
    if sys.argv[1:] == ["--identity-self-check"]:
        identity_self_check(json.loads((ROOT / "evaluation_config.json").read_text()))
        raise SystemExit(0)
    raise SystemExit(main())
