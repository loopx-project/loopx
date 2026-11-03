"""macOS adapter for aligning the existing service manager with a qualified Core.

Keep the CLI installation, service arguments and Chat store under their existing
owners. A restart alone cannot adopt an App-private release: launchd must load
the new executable before it can start that release.
"""

from __future__ import annotations

import copy
import base64
import json
import os
from pathlib import Path
import plistlib
import shlex
import subprocess
import time
import urllib.error
import urllib.request

from ..file_lock import exclusive_file_lock, write_bytes_atomic, write_json_atomic
from .. import runtime_activation as activation


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


def _endpoint(payload: dict) -> int:
    arguments, _ = _launcher(payload)
    # A standard label is not evidence of the service's role. Parse the command
    # position, rather than finding a word that could be an option's value.
    # This adapter accepts the installer's global options; custom invocations
    # remain under their original owner without any signal or persistent edit.
    expected = {"com.loopx.status": "serve-status", "com.loopx.chat": "chat"}.get(payload.get("Label"))
    if expected is None or Path(arguments[0]).name != "loopx":
        raise ValueError("service_runtime_route_unverified")
    index = 1
    while index < len(arguments) and arguments[index].startswith("--"):
        flag, separator, _ = arguments[index].partition("=")
        if flag not in {"--registry", "--runtime-root", "--format"}:
            raise ValueError("service_runtime_route_unverified")
        index += 1 if separator else 2
    if index >= len(arguments) or arguments[index] != expected:
        raise ValueError("service_runtime_route_unverified")
    arguments = arguments[index + 1:]
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
    saved = json.loads(journal.read_text())
    if not isinstance(saved, dict) or saved.get("schema_version") != "loopx_managed_runtime_handoff_v1":
        raise ValueError("service_runtime_route_unverified")
    entries = saved.get("services")
    if not isinstance(entries, dict) or not entries or not set(entries) <= {"com.loopx.status", "com.loopx.chat"}:
        raise ValueError("service_runtime_route_unverified")
    originals, candidates = {}, {}
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
                or original.get("Label") != label or candidate.get("Label") != label
                or _endpoint(original) != _endpoint(candidate)
                or _launcher(original)[0][1:] != _launcher(candidate)[0][1:]):
            raise ValueError("service_runtime_route_unverified")
        if path.is_symlink() or path.read_bytes() not in {originals[path], candidates[path]}:
            raise ValueError("service_runtime_route_unverified")
    chat = journal.parent / "com.loopx.chat.plist"
    with activation.managed_chat_activation_guard([chat] if chat.exists() else []):
        domain = f"gui/{os.getuid()}"
        loaded = []
        for path in originals:
            old, _ = activation._managed_service_state(path, payload=plistlib.loads(originals[path]))
            new, _ = activation._managed_service_state(path, payload=plistlib.loads(candidates[path]))
            if old is False and new is False:
                raise ValueError("service_runtime_route_unverified")
            if old or new:
                loaded.append(path)
        try:
            for path in loaded:
                _launchctl("bootout", f"{domain}/{path.stem}")
            for path, content in originals.items():
                write_bytes_atomic(path, content, preserve_mode=True, fsync_directory=True)
            for path in originals:
                _launchctl("bootstrap", domain, str(path))
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            raise RuntimeError("service_runtime_rollback_failed") from exc
        journal.unlink()


def _align_managed_runtime(executable: str, journal: Path) -> dict:
    target = Path(executable)
    if not target.is_absolute() or not target.is_file() or not os.access(target, os.X_OK):
        raise ValueError("service_runtime_unverified")
    environment = {k: v for k, v in os.environ.items()
        if k not in {"LOOPX_PYTHON", "PYTHONPATH", "LOOPX_RELEASE_ROOT"}}
    doctor = subprocess.run([str(target), "--format", "json", "doctor", "--installation-only"],
        env=environment, capture_output=True, text=True, timeout=15)
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
    if not updates:
        _verify_services(endpoints, identity)
        return {"status": "aligned", "runtime_identity": identity, "restarted_services": [],
            "readiness_verified": bool(endpoints)}

    chat = [p for p in plists if p.exists() and p.stem.endswith(".chat")]
    stopped, written, restarted = [], [], []
    domain = f"gui/{os.getuid()}"
    with activation.managed_chat_activation_guard(chat):
        # Fence the complete pair before the first signal or persistent edit.
        for plist in updates:
            if (plist.read_bytes() != originals[plist]
                    or activation._managed_service_state(plist) != (True, pids[plist])):
                raise ValueError("service_runtime_route_unverified")
        write_json_atomic(journal, {"schema_version": "loopx_managed_runtime_handoff_v1",
            "services": {p.stem: {"original": base64.b64encode(originals[p]).decode(),
                "candidate": base64.b64encode(updates[p]).decode()} for p in updates}},
            mode=0o600, fsync_directory=True)
        try:
            for plist in updates:
                _launchctl("bootout", f"{domain}/{plist.stem}")
                stopped.append(plist)
            for plist, content in updates.items():
                if plist.read_bytes() != originals[plist]:
                    raise RuntimeError("service_runtime_route_unverified")
                write_bytes_atomic(plist, content, preserve_mode=True, fsync_directory=True)
                written.append(plist)
            for plist in updates:
                _launchctl("bootstrap", domain, str(plist))
                restarted.append(plist)
            _verify_services(endpoints, identity)
        except (OSError, RuntimeError, subprocess.SubprocessError):
            # Restore the manager's original source as well as its file. Keep
            # rollback failure explicit; never declare HTTP readiness here.
            try:
                for plist in restarted:
                    _launchctl("bootout", f"{domain}/{plist.stem}")
                for plist in written:
                    if plist.read_bytes() != updates[plist]:
                        raise RuntimeError("service_runtime_route_unverified")
                    write_bytes_atomic(plist, originals[plist], preserve_mode=True, fsync_directory=True)
                for plist in stopped:
                    _launchctl("bootstrap", domain, str(plist))
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                raise RuntimeError("service_runtime_rollback_failed") from exc
            journal.unlink()
            raise RuntimeError("service_runtime_alignment_failed")
        journal.unlink()
    return {"status": "aligned", "runtime_identity": identity, "readiness_verified": True,
        "restarted_services": [p.stem for p in restarted]}
