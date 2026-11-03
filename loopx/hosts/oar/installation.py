"""Explicit, version-locked installation of the optional OAR host dependency.

Wheels carry the source and npm lock, not a machine-specific node_modules tree.
Only ``python -m loopx.hosts.oar install`` performs network/package-manager IO.
Ordinary Core commands and model turns never install or upgrade dependencies.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from ...control_plane.runtime.node_probe import probe_node
from ...file_lock import exclusive_file_lock

OAR_VERSION = "0.75.0"
MINIMUM_OAR_NODE_VERSION = (24, 0, 0)
_SOURCE = Path(__file__).parent
_REMEDIATION = "Run `python -m loopx.hosts.oar install` with Node.js 24+ and npm in the LoopX service environment."


def _assets() -> list[Path]:
    return sorted([
        _SOURCE / "package.json", _SOURCE / "package-lock.json",
        *(path for path in _SOURCE.glob("*.ts") if not path.name.endswith(".test.ts")),
    ])


def _source_digest() -> str:
    digest = hashlib.sha256()
    for path in _assets():
        digest.update(path.name.encode("utf-8") + b"\0" + path.read_bytes() + b"\0")
    return digest.hexdigest()


def _cache_directory() -> Path:
    root = Path(os.environ.get("LOOPX_OAR_HOME", "~/.cache/loopx/oar")).expanduser().resolve()
    return root / _source_digest()


def _dependency_present(directory: Path) -> bool:
    try:
        manifest = json.loads((directory / "node_modules/@botiverse/oar/package.json").read_text(encoding="utf-8"))
        return manifest.get("version") == OAR_VERSION and (directory / "stdio.ts").is_file()
    except (OSError, ValueError, AttributeError):
        return False


def _cached_source_matches(directory: Path) -> bool:
    try:
        return all((directory / path.name).read_bytes() == path.read_bytes() for path in _assets())
    except OSError:
        return False


def runtime_directory() -> Path:
    # Source checkouts can use the same locked npm package directly. An installed
    # wheel uses the immutable, source-bound directory created by install().
    if _dependency_present(_SOURCE):
        return _SOURCE
    cached = _cache_directory()
    if _dependency_present(cached) and _cached_source_matches(cached):
        return cached
    raise RuntimeError("OAR runtime dependency is unavailable. " + _REMEDIATION)


def node_executable() -> str:
    probe = probe_node()
    if not probe.ready or probe.executable is None:
        raise RuntimeError(probe.failure_message + ". " + _REMEDIATION)
    version = tuple(int(part) for part in str(probe.version).split("-", 1)[0].split("."))
    if version < MINIMUM_OAR_NODE_VERSION or (version == MINIMUM_OAR_NODE_VERSION and "-" in str(probe.version)):
        raise RuntimeError("OAR runtime requires Node.js 24.0.0 or newer. " + _REMEDIATION)
    return probe.executable


def inspect_installation() -> dict[str, Any]:
    try:
        node = node_executable()
        directory = runtime_directory()
    except (OSError, ValueError, RuntimeError) as exc:
        return {"ready": False, "oar_version": OAR_VERSION, "reason": str(exc), "next_action": _REMEDIATION}
    return {"ready": True, "oar_version": OAR_VERSION, "node": node, "runtime_directory": str(directory)}


def install() -> dict[str, Any]:
    """Install exactly the packaged lock into a private cache, then publish it.

    The previous source revision remains usable; an interrupted installation
    cannot expose half a dependency tree to a concurrent model turn.
    """
    node = node_executable()
    npm = shutil.which("npm")
    if npm is None:
        raise RuntimeError("npm is required to install the OAR runtime. " + _REMEDIATION)
    target = _cache_directory()
    target.parent.mkdir(parents=True, exist_ok=True)
    with exclusive_file_lock(target):
        if _dependency_present(target) and _cached_source_matches(target):
            return {"ready": True, "oar_version": OAR_VERSION, "node": node, "runtime_directory": str(target)}
        with tempfile.TemporaryDirectory(prefix=".install-", dir=target.parent) as temporary:
            staging = Path(temporary) / "runtime"
            staging.mkdir(mode=0o700)
            for source in _assets():
                shutil.copyfile(source, staging / source.name)
            # No lifecycle scripts, global mutation, floating version, or
            # on-demand install during execution. npm verifies lock integrity.
            completed = subprocess.run(
                [npm, "ci", "--omit=dev", "--ignore-scripts", "--no-audit", "--no-fund"],
                cwd=staging, check=False, timeout=600,
            )
            if completed.returncode:
                raise RuntimeError("The locked OAR dependency installation failed; the previous revision is unchanged.")
            checked = subprocess.run(
                [node, "--input-type=module", "-e",
                 "await import('@botiverse/oar/kernel'); await import('@botiverse/oar/runtimes/codex'); await import('@botiverse/oar/runtimes/pi');"],
                cwd=staging, capture_output=True, check=False, timeout=60,
            )
            if checked.returncode or not _dependency_present(staging):
                raise RuntimeError("Installed OAR runtime failed its import check; the previous revision is unchanged.")
            if target.is_symlink():
                raise RuntimeError("OAR installation target must not be a symbolic link.")
            if target.exists():
                shutil.rmtree(target)
            staging.replace(target)
    return {"ready": True, "oar_version": OAR_VERSION, "node": node, "runtime_directory": str(target)}
