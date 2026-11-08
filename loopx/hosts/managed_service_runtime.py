"""macOS adapter for aligning the existing service manager with a qualified Core.

Keep the CLI installation, service arguments and Chat store under their existing
owners. A restart alone cannot adopt an App-private release: launchd must load
the new executable before it can start that release.
"""

from __future__ import annotations

import copy
import base64
from contextlib import ExitStack, contextmanager
import importlib
import json
import math
import os
from pathlib import Path
import plistlib
import shlex
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request

from ..file_lock import exclusive_file_lock, write_bytes_atomic, write_json_atomic
from .. import runtime_activation as activation


_OBSERVER_LABEL = "com.loopx.codex-native-accounts"
_OBSERVER_MODULE = "loopx_codex_provider_routing.native"


def _literal_arguments(command: str) -> list[str]:
    # Only the installer's final literal exec is editable. Never evaluate a
    # custom shell expansion, pipeline, redirection or command substitution.
    quote = None
    escaped = False
    for char in command:
        if escaped:
            escaped = False
        elif char == "\\" and quote != "'":
            escaped = True
        elif char == quote:
            quote = None
        elif char in "\"'" and quote is None:
            quote = char
        elif (char in "$`" and quote != "'") or (quote is None and char in ";&|()<>\n"):
            raise ValueError("service_runtime_route_unverified")
    words = shlex.split(command)
    if not words or quote or escaped:
        raise ValueError("service_runtime_route_unverified")
    return words


def _launcher(payload: dict) -> tuple[list[str], str | None]:
    if "Program" in payload:
        raise ValueError("service_runtime_route_unverified")
    args = payload.get("ProgramArguments")
    if not isinstance(args, list) or not args or not all(isinstance(a, str) for a in args):
        raise ValueError("service_runtime_route_unverified")
    if Path(args[0]).name in {"sh", "bash", "zsh"}:
        if len(args) != 3 or args[1] != "-c":
            raise ValueError("service_runtime_route_unverified")
        prefix, separator, command = args[2].rpartition("; exec ")
        if not separator:
            if not args[2].startswith("exec "):
                raise ValueError("service_runtime_route_unverified")
            prefix, command = "", args[2][5:]
        # A split inside a quoted argument is not the generated exec boundary.
        shlex.split(prefix)
        words = _literal_arguments(command)
        if words[:5] == ["/usr/bin/env", "-u", "PYTHONPATH", "-u", "LOOPX_RELEASE_ROOT"]:
            words = [words[0], *words[5:]]
        if words[0] == "/usr/bin/env" and len(words) > 2 and words[1].startswith("LOOPX_PYTHON="):
            words = words[2:]
        if not words or not Path(words[0]).is_absolute():
            raise ValueError("service_runtime_route_unverified")
        return words, prefix
    if not Path(args[0]).is_absolute():
        raise ValueError("service_runtime_route_unverified")
    return args, None


def _retarget(payload: dict, executable: str, python: str) -> dict:
    result = copy.deepcopy(payload)
    words, prefix = _launcher(payload)
    if prefix is not None:
        launch = ["/usr/bin/env", "-u", "PYTHONPATH", "-u", "LOOPX_RELEASE_ROOT",
            f"LOOPX_PYTHON={python}", executable, *words[1:]]
        result["ProgramArguments"] = [payload["ProgramArguments"][0], "-c", (prefix + "; " if prefix else "") + "exec " + shlex.join(launch)]
    else:
        result["ProgramArguments"] = [executable, *words[1:]]
        result.setdefault("EnvironmentVariables", {})["LOOPX_PYTHON"] = python
        result["EnvironmentVariables"].pop("PYTHONPATH", None)
        result["EnvironmentVariables"].pop("LOOPX_RELEASE_ROOT", None)
    result["LoopXRuntimeBinding"] = {"schema_version": "loopx_managed_runtime_binding_v1"}
    return result


def _launchctl(*args: str) -> None:
    result = subprocess.run(["launchctl", *args], capture_output=True, timeout=5)
    if result.returncode:
        raise RuntimeError("service_runtime_alignment_failed")


def _wait_unloaded(label: str) -> None:
    # bootout can return before launchd removes the job. Loading its replacement
    # immediately can fail in both the forward handoff and rollback. Keep the
    # ingress fence until the original manager proves the label is gone.
    deadline = time.monotonic() + 5
    while True:
        result = subprocess.run(["launchctl", "print", label],
            capture_output=True, timeout=5)
        if result.returncode == 113:  # launchd: service not found
            return
        if result.returncode or time.monotonic() >= deadline:
            raise RuntimeError("service_runtime_alignment_failed")
        time.sleep(0.05)


def has_runtime_binding() -> bool:
    for kind in ("status", "chat"):
        path = Path.home() / "Library/LaunchAgents" / f"com.loopx.{kind}.plist"
        if not path.exists():
            continue
        payload = plistlib.loads(path.read_bytes())
        if not isinstance(payload, dict):
            raise ValueError("service_runtime_route_unverified")
        if payload.get("LoopXRuntimeBinding") == {"schema_version": "loopx_managed_runtime_binding_v1"}:
            return True
    return False


def _service_command(payload: dict) -> tuple[dict[str, str], list[str]]:
    arguments, _ = _launcher(payload)
    # A standard label is not evidence of the service's role. Parse the command
    # position, rather than finding a word that could be an option's value.
    # This adapter accepts the installer's global options; custom invocations
    # remain under their original owner without any signal or persistent edit.
    expected = {"com.loopx.status": "serve-status", "com.loopx.chat": "chat"}.get(payload.get("Label"))
    if expected is None or Path(arguments[0]).name != "loopx":
        raise ValueError("service_runtime_route_unverified")
    index = 1
    options = {}
    while index < len(arguments) and arguments[index].startswith("--"):
        flag, separator, value = arguments[index].partition("=")
        if (flag not in {"--registry", "--runtime-root", "--format"} or flag in options
                or not separator and index + 1 >= len(arguments)):
            raise ValueError("service_runtime_route_unverified")
        options[flag] = value if separator else arguments[index + 1]
        index += 1 if separator else 2
    if index >= len(arguments) or arguments[index] != expected:
        raise ValueError("service_runtime_route_unverified")
    return options, arguments[index + 1:]


def _endpoint(payload: dict) -> int:
    _, arguments = _service_command(payload)
    def option(flag):
        values = [word.split("=", 1)[1] if word.startswith(flag + "=")
            else arguments[i + 1] if i + 1 < len(arguments) else ""
            for i, word in enumerate(arguments) if word == flag or word.startswith(flag + "=")]
        if len(values) != 1:
            raise ValueError("service_runtime_route_unverified")
        return values[0]
    if option("--host") != "127.0.0.1":
        raise ValueError("service_runtime_route_unverified")
    try:
        port = int(option("--port"))
    except ValueError as exc:
        raise ValueError("service_runtime_route_unverified") from exc
    if not 1 <= port <= 65535:
        raise ValueError("service_runtime_route_unverified")
    return port


def _observer_route(payload: dict) -> Path:
    if not isinstance(payload, dict):
        raise ValueError("native_observer_route_unverified")
    arguments, shell = _launcher(payload)
    rest = arguments[1:]
    if (payload.get("Label") != _OBSERVER_LABEL or shell is not None or len(rest) != 5
            or rest[:4] != ["-m", _OBSERVER_MODULE, "watch", "--runtime-root"]
            or not Path(rest[4]).is_absolute()):
        raise ValueError("native_observer_route_unverified")
    return Path(rest[4])


def _retarget_observer(payload: dict, python: str, core: Path) -> dict:
    _observer_route(payload)
    # The standalone watcher needs the *same* Core's supported Chat fence
    # during a native account handoff, including in its detached worker.
    root = core.parent.parent
    if core.parent.name != "scripts" or not (root / "loopx/extensions/chat_service_guard.py").is_file():
        raise ValueError("native_observer_core_unverified")
    env = payload.get("EnvironmentVariables", {})
    if not isinstance(env, dict):
        raise ValueError("native_observer_route_unverified")
    env = {key: value for key, value in env.items() if key != "PYTHONHOME"}
    env.update(PYTHONPATH=str(root), PYTHONSAFEPATH="1", PYTHONNOUSERSITE="1",
               LOOPX_PYTHON=python, LOOPX_RELEASE_ROOT=str(root))
    return {**payload, "EnvironmentVariables": env,
            "ProgramArguments": [python, *payload["ProgramArguments"][1:]]}


def _provider_pinned(python: str) -> bool:
    # A generic Core update must not take over an independently installed
    # observer. Only a Core explicitly pinned to a venv's own provider can
    # adopt that observer. A venv's inherited system package does not qualify.
    bin_dir = Path(python).parent
    return (bin_dir.parent / "pyvenv.cfg").is_file() and (bin_dir / "loopx-native-account-provider").is_file()


def _native_package(python: str):
    if Path(python) != Path(sys.executable):
        raise activation.RuntimeActivationDeferred("native_observer_requires_target_runtime", [_OBSERVER_LABEL])
    try:
        module = importlib.import_module(_OBSERVER_MODULE)
    except ImportError as exc:
        raise ValueError("native_observer_route_unverified") from exc
    prefix = Path(python).parent.parent.resolve()
    if not module.__file__ or not Path(module.__file__).resolve().is_relative_to(prefix):
        raise ValueError("native_observer_route_unverified")
    if not callable(getattr(module, "observer_build_identity", None)):
        raise activation.RuntimeActivationDeferred("native_observer_provider_upgrade_required", [_OBSERVER_LABEL])
    return module


def _chat_account_root(payload: dict) -> Path:
    from ..control_plane.projects.registry_codec import load_project_registry
    from ..paths import resolve_runtime_root

    options, _ = _service_command(payload)
    registry = Path(options.get("--registry", ""))
    if not registry.is_absolute() or not registry.is_file():
        raise ValueError("native_observer_route_unverified")
    config = load_project_registry(registry)
    override = None if config.get("common_runtime_root") else options.get("--runtime-root")
    return resolve_runtime_root(config, override, registry_path=registry).resolve()


@contextmanager
def _alignment_guard(chat: list[Path], observer_root: Path | None):
    with activation.managed_chat_activation_guard(chat):
        if observer_root is None:
            yield None
            return
        try:
            module = importlib.import_module(_OBSERVER_MODULE)
        except ImportError as exc:
            raise ValueError("native_observer_requires_target_runtime") from exc
        owner = module.NativeAccounts(observer_root)
        with ExitStack() as locks:
            try:
                locks.enter_context(owner._lock())
                locks.enter_context(owner._lock(name="api-relay-state.lock"))
                state = owner._load()
            except BlockingIOError as exc:
                raise activation.RuntimeActivationDeferred("native_account_work_pending", [_OBSERVER_LABEL]) from exc
            except (module.HostError, AttributeError, TypeError) as exc:
                raise ValueError("native_observer_owner_unverified") from exc
            if state.get("intent") is not None or state.get("action") in {"restarting", "handoff_interrupted"}:
                raise activation.RuntimeActivationDeferred("native_account_work_pending", [_OBSERVER_LABEL])
            yield owner


def _observer_lock_pid(owner) -> int | None:
    lock = owner.root / "observer.lock"
    try:
        info = lock.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError("native_observer_owner_unverified")
    result = subprocess.run(["/usr/sbin/lsof", "-t", str(lock)],
        capture_output=True, text=True, encoding="utf-8", timeout=5)
    if result.returncode == 1 and not result.stdout and not result.stderr:
        return None
    pids = result.stdout.splitlines()
    if result.returncode or len(pids) != 1 or not pids[0].isdecimal():
        raise ValueError("native_observer_owner_unverified")
    return int(pids[0])


def _wait_observer_released(owner) -> None:
    deadline = time.monotonic() + 5
    while _observer_lock_pid(owner) is not None:
        if time.monotonic() >= deadline:
            raise RuntimeError("native_observer_owner_unverified")
        time.sleep(0.05)


def _verify_observer(path: Path, owner, after: float, expected: dict[str, str] | None) -> None:
    module = importlib.import_module(_OBSERVER_MODULE)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        loaded, pid = activation._managed_service_state(path)
        if loaded and pid and _observer_lock_pid(owner) == pid:
            try:
                value = module.private_read(owner.root / "observer.json", schema=module.OBSERVER_SCHEMA)
                checked = value.get("checked_at")
                expires = value.get("deadline_at")
                now = time.time()
                if (type(checked) in (int, float) and after <= checked <= now
                        and type(expires) in (int, float) and math.isfinite(expires) and expires > now
                        and value.get("phase") in {"waiting", "retrying"}
                        and all(value.get(key) == build for key, build in (expected or {}).items())):
                    return
            except FileNotFoundError:
                pass
            except (module.HostError, AttributeError, TypeError) as exc:
                raise ValueError("native_observer_readiness_unverified") from exc
        time.sleep(0.1)
    raise RuntimeError("native_observer_readiness_unverified")


def _start_order(paths):
    # Start the registered lock owner before the hosted Chat contender.
    return sorted(paths, key=lambda path: path.stem != _OBSERVER_LABEL)


def _verify_services(services: dict[Path, int], identity: dict) -> None:
    # A working CLI does not prove its Chat assets can serve. Read back the
    # exact HTTP runtime before committing the handoff; a failed start restores
    # the original manager configuration instead of leaving the App spinning.
    pending = dict(services)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    deadline = time.monotonic() + 15
    while pending and time.monotonic() < deadline:
        for path, port in list(pending.items()):
            status = path.stem.endswith(".status")
            url = f"http://127.0.0.1:{port}" + ("/?readiness=1" if status else "/api/chat/readiness")
            try:
                try:
                    response = opener.open(url, timeout=0.5)
                except urllib.error.HTTPError as exc:
                    if status or exc.code != 404:
                        raise
                    response = opener.open(f"http://127.0.0.1:{port}/api/chat/capabilities", timeout=0.5)
                with response:
                    body = response.read(1024 * 1024 + 1)
                    if len(body) > 1024 * 1024:
                        continue
                    observed = json.loads(body)
                if not isinstance(observed, dict):
                    continue
                readiness = observed.get("readiness")
                ready = (observed.get("source") == "serve-status"
                    and isinstance(readiness, dict) and readiness.get("state") == "ready") if status else (
                    observed.get("schema_version") == "loopx_chat_capabilities_v1" or
                    (observed.get("schema_version") == "loopx_chat_readiness_v1"
                        and observed.get("ok") is True and observed.get("state") == "ready"))
                if ready and observed.get("runtime_identity") == identity:
                    pending.pop(path)
            except (OSError, ValueError):
                pass
        if pending:
            time.sleep(0.1)
    if pending:
        raise RuntimeError("service_runtime_alignment_failed")


def align_managed_runtime(executable: str) -> dict:
    """Align the standard desktop services once, with rollback and Chat fencing.

    This is an explicit activation operation, not an installer or a change to
    the user's default CLI. Unsupported custom launchers remain untouched.
    """
    directory = Path.home() / "Library/LaunchAgents"
    directory.mkdir(parents=True, exist_ok=True)
    journal = directory / ".loopx-runtime-activation.json"
    with exclusive_file_lock(directory / ".loopx-runtime-activation.lock", timeout_seconds=2):
        _recover_alignment(journal)
        return _align_managed_runtime(executable, journal)


def _recover_alignment(journal: Path) -> None:
    if not journal.exists():
        return
    saved = json.loads(journal.read_text(encoding="utf-8"))
    if not isinstance(saved, dict) or saved.get("schema_version") != "loopx_managed_runtime_handoff_v1":
        raise ValueError("service_runtime_route_unverified")
    entries = saved.get("services")
    if not isinstance(entries, dict) or not entries or not set(entries) <= {"com.loopx.status", "com.loopx.chat", _OBSERVER_LABEL}:
        raise ValueError("service_runtime_route_unverified")
    originals, candidates = {}, {}
    observer_root = None
    for label, item in entries.items():
        if (not isinstance(item, dict) or set(item) != {"original", "candidate"}
                or not all(isinstance(value, str) for value in item.values())):
            raise ValueError("service_runtime_route_unverified")
        path = journal.parent / f"{label}.plist"
        originals[path] = base64.b64decode(item["original"], validate=True)
        candidates[path] = base64.b64decode(item["candidate"], validate=True)
        original = plistlib.loads(originals[path])
        candidate = plistlib.loads(candidates[path])
        if (not isinstance(original, dict) or not isinstance(candidate, dict)
                or original.get("Label") != label or candidate.get("Label") != label):
            raise ValueError("service_runtime_route_unverified")
        if label == _OBSERVER_LABEL:
            observer_root = _observer_route(original)
            if _observer_route(candidate) != observer_root:
                raise ValueError("native_observer_route_unverified")
        elif _endpoint(original) != _endpoint(candidate) or _launcher(original)[0][1:] != _launcher(candidate)[0][1:]:
            raise ValueError("service_runtime_route_unverified")
        if path.is_symlink() or path.read_bytes() not in {originals[path], candidates[path]}:
            raise ValueError("service_runtime_route_unverified")
    chat = journal.parent / "com.loopx.chat.plist"
    if observer_root is not None:
        if chat not in originals or _chat_account_root(plistlib.loads(originals[chat])) != observer_root.resolve():
            raise ValueError("native_observer_route_unverified")
        observer = journal.parent / f"{_OBSERVER_LABEL}.plist"
        candidate = plistlib.loads(candidates[observer])
        core = Path(_launcher(plistlib.loads(candidates[chat]))[0][0])
        if _retarget_observer(plistlib.loads(originals[observer]), candidate["ProgramArguments"][0], core) != candidate:
            raise ValueError("native_observer_route_unverified")
    with _alignment_guard([chat] if chat.exists() else [], observer_root) as owner:
        domain = f"gui/{os.getuid()}"
        loaded = []
        for path in originals:
            old, old_pid = activation._managed_service_state(path, payload=plistlib.loads(originals[path]))
            new, new_pid = activation._managed_service_state(path, payload=plistlib.loads(candidates[path]))
            if old is False and new is False:
                raise ValueError("service_runtime_route_unverified")
            if path.stem == _OBSERVER_LABEL:
                pid = old_pid or new_pid
                if ((old or new) and not pid) or _observer_lock_pid(owner) != pid:
                    raise ValueError("native_observer_owner_unverified")
                if not pid:
                    _wait_unloaded(f"{domain}/{path.stem}")
            if old or new:
                loaded.append(path)
        try:
            for path in loaded:
                _launchctl("bootout", f"{domain}/{path.stem}")
                _wait_unloaded(f"{domain}/{path.stem}")
            for path, content in originals.items():
                write_bytes_atomic(path, content, preserve_mode=True, fsync_directory=True)
            for path in _start_order(originals):
                _wait_unloaded(f"{domain}/{path.stem}")
                if path.stem == _OBSERVER_LABEL:
                    _wait_observer_released(owner)
                started = time.time()
                _launchctl("bootstrap", domain, str(path))
                if path.stem == _OBSERVER_LABEL:
                    _verify_observer(path, owner, started, None)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise RuntimeError("service_runtime_rollback_failed") from exc
        journal.unlink()


def _align_managed_runtime(executable: str, journal: Path) -> dict:
    target = Path(executable)
    if not target.is_absolute() or not target.is_file() or not os.access(target, os.X_OK):
        raise ValueError("service_runtime_unverified")
    # Bind the executable qualified below, not a mutable installer alias. The
    # same CLI symlink can name a new release while both services still run the
    # old one; unchanged launcher bytes must not suppress that handoff.
    target = target.resolve(strict=True)
    environment = {k: v for k, v in os.environ.items()
        if k not in {"LOOPX_PYTHON", "PYTHONPATH", "LOOPX_RELEASE_ROOT"}}
    doctor = subprocess.run([str(target), "--format", "json", "doctor", "--installation-only"],
        env=environment, capture_output=True, text=True, encoding="utf-8", timeout=15)
    qualified = json.loads(doctor.stdout)
    if not isinstance(qualified, dict):
        raise ValueError("service_runtime_unverified")
    identity = qualified.get("service_runtime_identity")
    python_info = qualified.get("python")
    python = python_info.get("executable") if isinstance(python_info, dict) else None
    if (doctor.returncode or qualified.get("ok") is not True
            or qualified.get("scope") != "installation_only"
            or not isinstance(identity, dict) or identity.get("schema_version") != "loopx_runtime_identity_v1"
            or not isinstance(python, str) or not Path(python).is_absolute()):
        raise ValueError("service_runtime_unverified")

    plists = [Path.home() / "Library/LaunchAgents" / f"com.loopx.{kind}.plist"
        for kind in ("status", "chat")]
    originals, updates, pids, endpoints = {}, {}, {}, {}
    for plist in plists:
        if not plist.exists():
            continue
        if plist.is_symlink():
            raise ValueError("service_runtime_route_unverified")
        original = plist.read_bytes()
        payload = plistlib.loads(original)
        if not isinstance(payload, dict) or payload.get("Label") != plist.stem:
            raise ValueError("service_runtime_route_unverified")
        loaded, pid = activation._managed_service_state(plist)
        if not loaded:
            raise ValueError("service_runtime_route_unverified")
        endpoints[plist] = _endpoint(payload)
        updated = _retarget(payload, str(target), python)
        if updated != payload:
            originals[plist] = original
            updates[plist] = plistlib.dumps(updated)
            pids[plist] = pid
    chat = [p for p in plists if p.exists() and p.stem.endswith(".chat")]
    observer = journal.parent / f"{_OBSERVER_LABEL}.plist"
    observer_root = None
    native = expected_observer = None
    if chat and observer.exists() and _provider_pinned(python):
        if observer.is_symlink():
            raise ValueError("native_observer_route_unverified")
        original = observer.read_bytes()
        payload = plistlib.loads(original)
        root = _observer_route(payload)
        if root.resolve() == _chat_account_root(plistlib.loads(chat[0].read_bytes())):
            native = _native_package(python)
            expected_observer = native.observer_build_identity()
            loaded, pid = activation._managed_service_state(observer)
            if not loaded or not pid:
                raise ValueError("native_observer_owner_unverified")
            observer_root = root
            candidate = _retarget_observer(payload, python, target)
            if candidate != payload:
                originals[observer] = original
                updates[observer] = plistlib.dumps(candidate)
                pids[observer] = pid
                # The hosted contender must stop before the standalone owner.
                if chat[0] not in updates:
                    originals[chat[0]] = updates[chat[0]] = chat[0].read_bytes()
                    pids[chat[0]] = activation._managed_service_pid(chat[0])
    if not updates:
        _verify_services(endpoints, identity)
        if observer_root is not None:
            _verify_observer(observer, native.NativeAccounts(observer_root), time.time() - 480, expected_observer)
        return {"status": "aligned", "runtime_identity": identity, "restarted_services": [],
            "readiness_verified": bool(endpoints)}

    stopped, written, restarted = [], [], []
    domain = f"gui/{os.getuid()}"
    with _alignment_guard(chat, observer_root) as owner:
        # Fence all participating services before the first signal or write.
        for plist in updates:
            if (plist.read_bytes() != originals[plist]
                    or activation._managed_service_state(plist) != (True, pids[plist])):
                raise ValueError("service_runtime_route_unverified")
        if observer_root is not None:
            loaded, pid = activation._managed_service_state(observer)
            if (not loaded or not pid or _observer_lock_pid(owner) != pid
                    or _chat_account_root(plistlib.loads(chat[0].read_bytes())) != observer_root.resolve()):
                raise ValueError("native_observer_owner_unverified")
        write_json_atomic(journal, {"schema_version": "loopx_managed_runtime_handoff_v1",
            "services": {p.stem: {"original": base64.b64encode(originals[p]).decode(),
                "candidate": base64.b64encode(updates[p]).decode()} for p in updates}},
            mode=0o600, fsync_directory=True)
        try:
            for plist in updates:
                _launchctl("bootout", f"{domain}/{plist.stem}")
                stopped.append(plist)
                _wait_unloaded(f"{domain}/{plist.stem}")
            for plist, content in updates.items():
                if plist.read_bytes() != originals[plist]:
                    raise RuntimeError("service_runtime_route_unverified")
                write_bytes_atomic(plist, content, preserve_mode=True, fsync_directory=True)
                written.append(plist)
            observer_after = time.time() - 480
            for plist in _start_order(updates):
                if plist == observer:
                    _wait_observer_released(owner)
                    observer_after = time.time()
                _launchctl("bootstrap", domain, str(plist))
                restarted.append(plist)
                if plist == observer:
                    _verify_observer(plist, owner, observer_after, expected_observer)
            _verify_services(endpoints, identity)
            if observer_root is not None:
                _verify_observer(observer, owner, observer_after, expected_observer)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
            # Restore the manager's original source as well as its file. Keep
            # rollback failure explicit; never declare HTTP readiness here.
            try:
                for plist in sorted(restarted, key=lambda path: path == observer):
                    _launchctl("bootout", f"{domain}/{plist.stem}")
                    _wait_unloaded(f"{domain}/{plist.stem}")
                for plist in written:
                    if plist.read_bytes() != updates[plist]:
                        raise RuntimeError("service_runtime_route_unverified")
                    write_bytes_atomic(plist, originals[plist], preserve_mode=True, fsync_directory=True)
                for plist in _start_order(stopped):
                    _wait_unloaded(f"{domain}/{plist.stem}")
                    if plist == observer:
                        _wait_observer_released(owner)
                    started = time.time()
                    _launchctl("bootstrap", domain, str(plist))
                    if plist == observer:
                        _verify_observer(plist, owner, started, None)
            except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
                raise RuntimeError("service_runtime_rollback_failed") from exc
            journal.unlink()
            raise RuntimeError("service_runtime_alignment_failed")
        journal.unlink()
    return {"status": "aligned", "runtime_identity": identity, "readiness_verified": True,
        "restarted_services": [p.stem for p in restarted]}
