from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

EXTENSION_DOCTOR_SCHEMA_VERSION = "loopx_extension_doctor_v0"
RUNTIME_ENTRYPOINT_IDENTITY_SCHEMA_VERSION = "loopx_runtime_entrypoint_identity_v1"
RUNTIME_EXECUTABLE_IDENTITY_SCHEMA_VERSION = "loopx_runtime_executable_identity_v1"

# LoopX-owned validators execute in the LoopX process and are not extension
# artifacts. Binding them would invalidate every installed extension whenever
# LoopX itself is upgraded.
CORE_VIEW_VALIDATORS = frozenset(
    {"loopx.extensions.presentation:validate_opaque_presentation_view"}
)


@dataclass(frozen=True)
class ResolvedRuntimeEntrypoint:
    argv_prefix: tuple[str, ...]
    identity: str
    path_prefix: str | None = None
    python_executable: str | None = None


def _python_executable_for_script(path: Path) -> str | None:
    """Resolve the interpreter selected by one verified console script."""

    try:
        first_line = path.open("rb").readline(4096).decode("utf-8")
    except (OSError, UnicodeDecodeError):
        first_line = ""
    selected: Path | None = None
    if first_line.startswith("#!"):
        try:
            command = shlex.split(first_line[2:].strip())
        except ValueError:
            command = []
        if command:
            executable = command[0]
            if Path(executable).name == "env":
                command_candidates = [
                    item for item in command[1:] if not item.startswith("-")
                ]
                executable = (
                    shutil.which(
                        command_candidates[0],
                        path=str(path.parent)
                        + os.pathsep
                        + os.environ.get("PATH", os.defpath),
                    )
                    if command_candidates
                    else None
                ) or ""
            selected = Path(executable).expanduser()
            if not selected.is_absolute():
                selected = Path(os.path.abspath(selected))

    candidates = [selected] if selected is not None else []
    # Windows console-script launchers are executable wrappers rather than
    # text shebang scripts. Their venv interpreter remains a sibling in the
    # same Scripts directory; the same fallback is safe for opaque POSIX
    # launchers and fails closed for non-Python runtimes.
    candidates.extend(
        path.parent / name
        for name in ("python.exe", "python3.exe", "python", "python3")
    )
    for candidate in candidates:
        identified = _file_identity(candidate, executable=True)
        if identified is None or not candidate.name.lower().startswith("python"):
            continue
        # Preserve the selected venv launcher path. Resolving the symlink to
        # the base interpreter would discard pyvenv.cfg and load the wrong
        # packages.
        return str(candidate)
    return None


def runtime_process_environment(
    path_prefix: str | None,
    base: Mapping[str, str] | None = None,
) -> Mapping[str, str] | None:
    if path_prefix is None:
        return base
    if not isinstance(path_prefix, str) or not Path(path_prefix).is_absolute():
        raise ValueError("extension runtime search directory must be absolute")
    environment = dict(os.environ if base is None else base)
    environment["PATH"] = path_prefix + os.pathsep + environment.get("PATH", os.defpath)
    return environment


def extension_runtime(manifest: Mapping[str, Any]) -> Mapping[str, Any]:
    runtime = manifest.get("runtime")
    if not isinstance(runtime, Mapping):
        raise ValueError("extension manifest does not declare an executable runtime")
    return runtime


def _file_identity(path: Path, *, executable: bool) -> tuple[Path, str] | None:
    try:
        path = path.resolve(strict=True)
        stat = path.stat()
        if not path.is_file() or (executable and stat.st_mode & 0o111 == 0):
            return None
        with path.open("rb") as file:
            content_digest = hashlib.file_digest(file, "sha256").hexdigest()
    except OSError:
        return None
    identity = {
        "schema_version": RUNTIME_ENTRYPOINT_IDENTITY_SCHEMA_VERSION,
        "kind": "file_artifact",
        "executable": executable,
        "size": stat.st_size,
        "content_sha256": content_digest,
    }
    serialized = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    return path, hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolved_entrypoint_identity(command: str) -> tuple[Path, str] | None:
    if "/" in command or "\\" in command:
        path = Path(command).expanduser()
        if not path.is_absolute():
            path = Path(os.path.abspath(path))
    else:
        resolved = shutil.which(command)
        if resolved is None:
            return None
        path = Path(resolved)
    identified = _file_identity(path, executable=True)
    if identified is None:
        return None
    # Hash the final executable artifact, but retain the launcher path selected
    # by the operator. Package managers commonly expose console scripts through
    # a shared symlink directory; sibling tools in that directory must remain
    # available to the provider subprocess even when the link target lives in
    # an isolated package environment.
    return path, identified[1]


_RESOLVE_DECLARED_MODULE_ORIGIN = """\
import importlib.util
import json
import sys

try:
    spec = importlib.util.find_spec(sys.argv[1])
except Exception:
    spec = None
origin = getattr(spec, "origin", None) if spec is not None else None
json.dump({"origin": origin if isinstance(origin, str) else None}, sys.stdout)
"""


def declared_view_validators(manifest: Mapping[str, Any]) -> tuple[str, ...]:
    """List the extension-owned validator references one manifest declares."""

    references = {
        str(surface["view_validator"])
        for surface in manifest.get("presentation_surfaces") or []
        if isinstance(surface, Mapping)
        and isinstance(surface.get("view_validator"), str)
        and str(surface["view_validator"]) not in CORE_VIEW_VALIDATORS
    }
    return tuple(sorted(references))


@lru_cache(maxsize=64)
def _declared_module_origin(
    python_executable: str,
    module_name: str,
) -> str | None:
    """Resolve one declared module's source file in the runtime interpreter.

    The isolated validator imports this module in a ``python -I`` process, so
    the identity owner asks that same interpreter where the implementation lives
    instead of guessing a site-packages layout or reusing LoopX's own import
    state. Only the resolved path is memoized; the artifact bytes are re-read on
    every identity computation so a content mutation still changes the identity.
    """

    try:
        completed = subprocess.run(
            [
                python_executable,
                "-I",
                "-c",
                _RESOLVE_DECLARED_MODULE_ORIGIN,
                module_name,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
            text=True,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    origin = payload.get("origin") if isinstance(payload, Mapping) else None
    return origin if isinstance(origin, str) and origin else None


def _declared_validator_artifacts(
    python_executable: str,
    view_validators: tuple[str, ...],
) -> dict[str, str | None]:
    """Bind every declared validator implementation the runtime can resolve.

    A validator that resolves has its implementation bytes bound, so replacing
    the decision code invalidates the doctor identity even when the launcher,
    the interpreter and the declared reference are unchanged. A validator the
    runtime interpreter cannot resolve cannot execute either - the isolated
    runner resolves the same module with the same interpreter and flags - so its
    marker records that state instead of refusing the provider runtime
    readiness. Resolving the module file deliberately does not import it: an
    implementation that resolves but raises on import is an execution failure at
    the surface that uses it, not a provider-runtime identity change.
    """

    artifacts: dict[str, str | None] = {}
    for reference in view_validators:
        module_name = reference.split(":", 1)[0]
        origin = _declared_module_origin(python_executable, module_name)
        artifact = (
            None
            if origin is None
            else _file_identity(Path(origin), executable=False)
        )
        artifacts[module_name] = None if artifact is None else artifact[1]
    return artifacts


def _runtime_executable_identity(
    entrypoint_identity: str,
    python_executable: str | None,
    *,
    view_validators: tuple[str, ...] = (),
) -> str | None:
    """Bind every executable artifact one runtime selects for its provider.

    A runtime that declares no extension-owned validator keeps the identity of
    its launcher and selected interpreter alone, so an extension whose code did
    not change does not have to be re-doctored. A runtime that does declare one
    binds the implementation each reference resolves to, because that code runs
    in the runtime interpreter and outside LoopX's own verified launcher.
    """

    if python_executable is None and not view_validators:
        return entrypoint_identity
    interpreter_identity: str | None = None
    if python_executable is not None:
        interpreter = _file_identity(Path(python_executable), executable=True)
        if interpreter is None:
            return None
        interpreter_identity = interpreter[1]
    identity_payload: dict[str, Any] = {
        "schema_version": RUNTIME_EXECUTABLE_IDENTITY_SCHEMA_VERSION,
        "kind": "executable_runtime",
        "entrypoint_identity": entrypoint_identity,
        "python_interpreter_identity": interpreter_identity,
    }
    if view_validators:
        identity_payload["view_validator_artifacts"] = (
            _declared_validator_artifacts(python_executable, view_validators)
            if python_executable is not None
            # No interpreter means no isolated validator can run; record the
            # declared references so adding one later still changes the identity.
            else {reference.split(":", 1)[0]: None for reference in view_validators}
        )
    serialized = json.dumps(
        identity_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def resolve_runtime_entrypoint(
    runtime: Mapping[str, Any],
    *,
    view_validators: tuple[str, ...] = (),
) -> ResolvedRuntimeEntrypoint | None:
    python_module = runtime.get("python_module")
    if python_module is None:
        resolved = resolved_entrypoint_identity(str(runtime["entrypoint"]))
        if resolved is None:
            return None
        python_executable = _python_executable_for_script(resolved[0])
        identity = _runtime_executable_identity(
            resolved[1],
            python_executable,
            view_validators=view_validators,
        )
        if identity is None:
            return None
        return ResolvedRuntimeEntrypoint(
            argv_prefix=(str(resolved[0]),),
            identity=identity,
            path_prefix=str(resolved[0].parent),
            python_executable=python_executable,
        )

    interpreter_path = Path(sys.executable).expanduser()
    interpreter = _file_identity(interpreter_path, executable=True)
    try:
        spec = importlib.util.find_spec(str(python_module))
    except (ImportError, AttributeError, ValueError):
        spec = None
    if interpreter is None or spec is None or not spec.origin:
        return None
    module = _file_identity(Path(spec.origin), executable=False)
    if module is None:
        return None
    identity_payload: dict[str, Any] = {
        "kind": "python_module",
        "interpreter_identity": interpreter[1],
        "module": str(python_module),
        "module_identity": module[1],
    }
    if view_validators:
        identity_payload["view_validator_artifacts"] = _declared_validator_artifacts(
            str(interpreter_path),
            view_validators,
        )
    serialized = json.dumps(
        identity_payload,
        sort_keys=True,
        separators=(",", ":"),
    )
    return ResolvedRuntimeEntrypoint(
        argv_prefix=(str(interpreter_path), "-m", str(python_module)),
        identity=hashlib.sha256(serialized.encode("utf-8")).hexdigest(),
        python_executable=str(interpreter_path),
    )


def extension_doctor(
    manifest: Mapping[str, Any],
    *,
    execute: bool = False,
) -> dict[str, Any]:
    runtime = extension_runtime(manifest)
    view_validators = declared_view_validators(manifest)
    identity_before = resolve_runtime_entrypoint(
        runtime,
        view_validators=view_validators,
    )
    available = identity_before is not None
    doctor_args = [str(value) for value in runtime.get("doctor_args") or []]
    status = "ready" if available else "entrypoint_missing"
    verified = False
    failure_kind = None
    if not doctor_args:
        status = "doctor_not_configured"
        available = False
        failure_kind = "doctor_not_configured"
    elif available and not execute:
        status = "probe_required"
    elif available:
        assert identity_before is not None
        argv = [
            *identity_before.argv_prefix,
            *[str(value) for value in runtime.get("args") or []],
            *doctor_args,
        ]
        try:
            completed = subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=int(runtime["timeout_seconds"]),
                check=False,
                env=runtime_process_environment(identity_before.path_prefix),
            )
        except (OSError, subprocess.TimeoutExpired):
            completed = None
            failure_kind = "probe_execution_failed"
        if completed is None or completed.returncode != 0:
            status = "provider_unavailable"
            available = False
            failure_kind = failure_kind or "probe_nonzero_exit"
        else:
            identity_after = resolve_runtime_entrypoint(
                runtime,
                view_validators=view_validators,
            )
            if (
                identity_after is None
                or identity_after.identity != identity_before.identity
            ):
                status = "provider_unavailable"
                available = False
                failure_kind = "entrypoint_changed_during_probe"
            else:
                status = "ready"
                verified = True
    return {
        "ok": True,
        "schema_version": EXTENSION_DOCTOR_SCHEMA_VERSION,
        "extension_id": manifest["provider"]["id"],
        "version": manifest["provider"]["version"],
        "status": status,
        "available": available,
        "verified": verified,
        "entrypoint_identity": (
            identity_before.identity
            if verified and identity_before is not None
            else None
        ),
        # Observation, never admission evidence: failed probes revoke only this
        # exact artifact, not another independently verified local runtime.
        "probed_entrypoint_identity": (
            identity_before.identity if identity_before is not None else None
        ),
        "failure_kind": failure_kind,
        "external_writes_performed": False,
    }
