"""Identify the Python installer that owns this LoopX install, and render the
commands that installer needs so the host that prints them can run them.

Native Windows prints PowerShell guidance, where a quoted executable path is a
string literal rather than a command; POSIX keeps shell quoting.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from collections.abc import Callable
from importlib.metadata import Distribution, PackageNotFoundError, PackagePath, distribution
from importlib.util import find_spec
import json
import os
from pathlib import Path
import shlex
from typing import Any


def installed_loopx_distribution(lookup: Callable[[str], Distribution]) -> Distribution:
    """Select the existing distribution, or the standalone core when absent.

    Callers still verify that its RECORD owns their actual imported file.
    Missing metadata, redirected records and content checks keep their original
    failure behavior.
    """
    try:
        return lookup("loopx")
    except PackageNotFoundError:
        return lookup("loopx-core")


def distribution_record_path(item: PackagePath) -> Path:
    # PackagePath uses POSIX components and can lose a Windows drive root on
    # Python 3.11. Interpret absolute RECORD entries under native path rules.
    path = Path(str(item))
    return path if path.is_absolute() else Path(item.locate())


def verified_distribution_console(recorded: Path) -> Path | None:
    """Keep the recorded launcher identity when resolving its directory."""
    try:
        expected = recorded.parent.resolve(strict=True) / recorded.name
        resolved = recorded.resolve(strict=True)
        if resolved == expected and resolved.is_file():
            return resolved
    except (OSError, RuntimeError):
        pass
    return None


def is_distribution_artifact(path: Path) -> bool:
    """Whether a recorded package path is an installed runtime artifact rather than bytecode."""
    return "__pycache__" not in path.parts and path.suffix not in {".pyc", ".pyo"}


def distribution_recorded_files(installed: Distribution) -> list[tuple[str, Path]] | None:
    """Read raw wheel RECORD entries without filtering deleted files on disk."""
    try:
        record_text = installed.read_text("RECORD")
    except (OSError, ValueError):
        return None
    if record_text is None:
        if installed.files is None:
            return None
        return [
            (
                item.as_posix(),
                distribution_record_path(item)
                if isinstance(item, PackagePath)
                else Path(item.locate()),
            )
            for item in installed.files
        ]
    try:
        rows = list(csv.reader(record_text.splitlines()))
    except csv.Error:
        return None
    entries: list[tuple[str, Path]] = []
    for row in rows:
        if not row or not row[0]:
            continue
        item = PackagePath(row[0])
        item.dist = installed
        entries.append((item.as_posix(), distribution_record_path(item)))
    return entries


def read_editable_direct_url(installed: Distribution) -> bool | None:
    """Inspect ``direct_url.json`` for editable metadata, returning ``None`` when malformed."""
    try:
        raw_direct_url = installed.read_text("direct_url.json")
        if raw_direct_url is None:
            dist_path = getattr(installed, "_path", None)
            if isinstance(dist_path, (str, os.PathLike)):
                direct_url_path = Path(dist_path) / "direct_url.json"
                try:
                    direct_url_path.lstat()
                except FileNotFoundError:
                    raw_direct_url = None
                else:
                    # PathDistribution.read_text swallows PermissionError and IsADirectoryError as None.
                    raw_direct_url = direct_url_path.read_text(encoding="utf-8")
        direct_url = json.loads(raw_direct_url) if raw_direct_url is not None else {}
    except (OSError, ValueError):
        return None
    if not isinstance(direct_url, dict):
        return None
    if "dir_info" in direct_url:
        dir_info = direct_url["dir_info"]
        if not isinstance(dir_info, dict):
            return None
        return bool(dir_info.get("editable"))
    return False


def inspect_builtin_capability_distribution(
    distribution_name: str,
    modules: tuple[str, ...],
    *,
    builtin_cli_present: bool | None = None,
    resources: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Inspect companion capability installation, version, and file ownership."""
    if builtin_cli_present is None:
        builtin_cli_present = find_spec("loopx.cli") is not None
    if builtin_cli_present:
        return {
            "distribution": distribution_name,
            "installed": True,
            "ready": True,
            "version": None,
            "core_version": None,
            "reason": None,
            "reasons": [],
            "missing_modules": [],
            "missing_resources": [],
            "unowned_modules": [],
            "unowned_resources": [],
        }
    try:
        core_version: str | None = distribution("loopx-core").version
    except PackageNotFoundError:
        core_version = None
    try:
        implementation = distribution(distribution_name)
    except PackageNotFoundError:
        return {
            "distribution": distribution_name,
            "installed": False,
            "ready": False,
            "version": None,
            "core_version": core_version,
            "reason": "not_installed",
            "reasons": ["not_installed"],
            "missing_modules": [],
            "missing_resources": [],
            "unowned_modules": [],
            "unowned_resources": [],
        }

    editable_flag = read_editable_direct_url(implementation)
    invalid_metadata = editable_flag is None
    editable = bool(editable_flag)
    entries = distribution_recorded_files(implementation)
    package_parent = Path(__file__).resolve().parent.parent
    recorded = {path.resolve() for _name, path in entries or ()}
    missing_recorded_assets: list[str] = []
    unowned_recorded_assets: list[str] = []
    if entries is None:
        missing_recorded_assets.append("RECORD")
    else:
        for name, path in entries:
            if not (name.startswith("loopx/") and is_distribution_artifact(Path(name))):
                continue
            target = package_parent / name
            if ".." in Path(name).parts or target.is_symlink() or not target.is_file():
                missing_recorded_assets.append(name)
            elif path.resolve() != target.resolve():
                unowned_recorded_assets.append(name)

    missing_modules: list[str] = []
    unowned_modules: list[str] = []
    for module in modules:
        spec = find_spec(module)
        if spec is None or spec.origin is None or not Path(spec.origin).is_file():
            missing_modules.append(module)
            continue
        if Path(spec.origin).resolve() not in recorded:
            unowned_modules.append(module)
    missing_resources: list[str] = list(missing_recorded_assets)
    unowned_resources: list[str] = list(unowned_recorded_assets)
    for resource in resources:
        target = package_parent / resource
        if target.is_symlink() or not target.is_file():
            if resource not in missing_resources:
                missing_resources.append(resource)
        elif target.resolve() not in recorded:
            if resource not in unowned_resources:
                unowned_resources.append(resource)

    reasons: list[str] = []
    if core_version is None:
        reasons.append("core_distribution_missing")
    elif implementation.version != core_version:
        reasons.append("version_mismatch")
    if invalid_metadata:
        reasons.append("invalid_companion_metadata")
    if editable:
        reasons.append("editable_companion_unsupported")
    if missing_modules or missing_resources:
        reasons.append("missing_files")
    if unowned_modules or unowned_resources:
        reasons.append("unowned_files")
    return {
        "distribution": distribution_name,
        "installed": True,
        "ready": not reasons,
        "version": implementation.version,
        "core_version": core_version,
        "reason": reasons[0] if reasons else None,
        "reasons": reasons,
        "missing_modules": missing_modules,
        "missing_resources": missing_resources,
        "unowned_modules": unowned_modules,
        "unowned_resources": unowned_resources,
    }


def builtin_capability_installed(
    distribution_name: str,
    modules: tuple[str, ...],
    *,
    builtin_cli_present: bool | None = None,
    resources: tuple[str, ...] = (),
) -> bool:
    """Check import locations and matching installed wheel ownership without loading code."""
    return bool(
        inspect_builtin_capability_distribution(
            distribution_name,
            modules,
            builtin_cli_present=builtin_cli_present,
            resources=resources,
        )["ready"]
    )


WORKSPACE_DISTRIBUTION = "loopx-workspace"
RESEARCH_DISTRIBUTION = "loopx-capability-research"
BENCHMARK_TOOLKIT_DISTRIBUTION = "loopx-capability-benchmark-toolkit"
@dataclass(frozen=True)
class PythonInstallOwner:
    manager: str
    environment: str | None = None


def _powershell_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _is_windows_host() -> bool:
    """Whether printed guidance targets PowerShell instead of a POSIX shell."""

    return os.name == "nt"


def interpreter_invocation(python_executable: str, arguments: list[str] | None = None) -> str:
    """The text that runs an interpreter path on the host that prints it.

    POSIX shells run a quoted path directly. PowerShell reads a bare quoted
    path as a string literal, so the ``&`` call operator with a single-quoted
    literal is required; that form also carries spaces and apostrophes in the
    interpreter path. This module owns the renderer because the pip-owned
    upgrade command is the first caller; doctor and update reuse it.
    Arguments, when supplied, use the same host quoting and retain empty values.
    """

    if _is_windows_host():
        return " ".join(
            [f"& {_powershell_literal(python_executable)}",
             *(_powershell_literal(value) for value in arguments or [])]
        )
    return shlex.join([python_executable, *(arguments or [])])


def resolve_python_install_owner(
    *,
    default_installer: str,
    prefix: Path,
) -> PythonInstallOwner:
    """Preserve pipx ownership instead of treating its internal pip as plain pip."""

    metadata_path = prefix / "pipx_metadata.json"
    if not metadata_path.is_file():
        return PythonInstallOwner(manager=default_installer)
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        metadata = {}
    recorded_environment = metadata.get("environment") if isinstance(metadata, dict) else None
    environment = (
        recorded_environment
        if isinstance(recorded_environment, str) and recorded_environment
        else prefix.name
    )
    return PythonInstallOwner(manager="pipx", environment=environment)


def python_distribution_upgrade_command(
    *,
    owner: PythonInstallOwner,
    python_executable: str,
    doctor_command: str,
    distribution_name: str = "loopx",
) -> str | None:
    if owner.manager == "pipx":
        package_command = f"pipx upgrade {shlex.quote(owner.environment or distribution_name)}"
    elif owner.manager == "pip":
        package_command = (
            f"{interpreter_invocation(python_executable)} -m pip install --upgrade {shlex.quote(distribution_name)}"
        )
    else:
        return None
    steps = [package_command]
    if distribution_name == "loopx":
        steps.extend(["loopx workflow-skills --install", "loopx slash-commands --install"])
    return "\n".join([*steps, doctor_command])
