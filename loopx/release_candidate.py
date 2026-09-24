from __future__ import annotations

import importlib
from concurrent.futures import ThreadPoolExecutor
import os
from importlib.metadata import PackageNotFoundError, distribution
import subprocess
from pathlib import Path
from typing import Any, Sequence

from .command_invocation import command_argv


REPRESENTATIVE_CLI_IMPORTS = (
    "loopx.cli",
    "loopx.history",
    "loopx.quota",
    "loopx.status",
)
REPRESENTATIVE_CLI_COMMANDS = (
    ("version", ("--version",)),
    ("commands", ("commands", "--format", "json")),
    ("status_help", ("status", "--help")),
    ("quota_help", ("quota", "--help")),
)
REPRESENTATIVE_DISTRIBUTION_PATHS = (
    "loopx/cli.py",
    "loopx/doctor.py",
    "loopx/history.py",
    "loopx/quota.py",
    "loopx/release_candidate.py",
    "loopx/status.py",
    "loopx/cli_commands/doctor.py",
    "loopx/cli_commands/quota.py",
    "loopx/cli_commands/status.py",
)
REPRESENTATIVE_PACKAGE_PATHS = (
    *REPRESENTATIVE_DISTRIBUTION_PATHS,
    "scripts/loopx",
)


def _import_summary() -> dict[str, Any]:
    results: dict[str, str] = {}
    for module_name in REPRESENTATIVE_CLI_IMPORTS:
        try:
            importlib.import_module(module_name)
        except Exception as exc:  # pragma: no cover - partial-package errors vary
            results[module_name] = f"{type(exc).__name__}: {exc}"
        else:
            results[module_name] = "ok"
    failed = sorted(name for name, result in results.items() if result != "ok")
    return {
        "ok": not failed,
        "results": results,
        "failed": failed,
    }


def _command_summary(command_path: Path | None) -> dict[str, Any]:
    if command_path is None:
        return {"ok": False, "results": {}, "failed": ["command_missing"]}

    def probe(item: tuple[str, tuple[str, ...]]) -> tuple[str, dict[str, Any]]:
        probe_name, args = item
        try:
            result = subprocess.run(
                command_argv(command_path, args),
                check=False,
                capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return probe_name, {"ok": False, "detail": f"{type(exc).__name__}: {exc}"}
        return probe_name, {"ok": result.returncode == 0, "returncode": result.returncode}

    # These are read-only version/catalog/help probes. Preserve every result
    # and its timeout while avoiding serial interpreter startup costs.
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = dict(executor.map(probe, REPRESENTATIVE_CLI_COMMANDS))
    failed = sorted(name for name, result in results.items() if not result.get("ok"))
    return {
        "ok": not failed,
        "results": results,
        "failed": failed,
    }


def _package_path_summary(
    package_root: Path,
    required_paths: Sequence[str] = REPRESENTATIVE_PACKAGE_PATHS,
) -> dict[str, Any]:
    missing = [
        relative
        for relative in required_paths
        if not (package_root / relative).is_file()
    ]
    return {
        "ok": not missing,
        "required": list(required_paths),
        "missing": missing,
    }


def _distribution_command_summary(command_path: Path | None) -> dict[str, Any]:
    if command_path is None:
        return {
            "ok": False,
            "command": None,
            "recorded_commands": [],
            "error": "command_missing",
        }

    try:
        installed = distribution("loopx")
    except PackageNotFoundError:
        return {
            "ok": False,
            "command": str(command_path),
            "recorded_commands": [],
            "error": "distribution_missing",
        }

    recorded_commands: list[Path] = []
    for item in installed.files or ():
        if Path(str(item)).name.lower() not in {
            "loopx",
            "loopx.exe",
            "loopx-script.py",
        }:
            continue
        try:
            resolved_path = Path(item.locate()).resolve()
        except OSError:
            continue
        if resolved_path not in recorded_commands:
            recorded_commands.append(resolved_path)
    recorded_commands.sort(key=str)
    resolved_command = command_path.resolve()
    return {
        "ok": resolved_command in recorded_commands,
        "command": str(resolved_command),
        "recorded_commands": [str(path) for path in recorded_commands],
        "error": None if recorded_commands else "console_script_not_recorded",
    }


def _representative_cli_checks(
    imports: dict[str, Any],
    commands: dict[str, Any],
) -> list[dict[str, Any]]:
    return [
        {
            "id": "representative_cli_imports",
            "required": True,
            "ok": bool(imports.get("ok")),
            "detail": ",".join(imports.get("failed") or []) or "ok",
        },
        {
            "id": "representative_cli_commands",
            "required": True,
            "ok": bool(commands.get("ok")),
            "detail": ",".join(commands.get("failed") or []) or "ok",
        },
    ]


def collect_release_candidate_checks(
    *,
    command_path: Path | None,
    package_root: Path,
    invocation_root: Path | None,
) -> dict[str, Any]:
    """Run the slower checks used only before promoting a release candidate."""

    imports = _import_summary()
    commands = _command_summary(command_path)
    package_paths = _package_path_summary(package_root)
    command_package_same_root = bool(
        invocation_root and package_root.resolve() == invocation_root.resolve()
    )
    checks = [
        {
            "id": "command_package_same_root",
            "required": True,
            "ok": command_package_same_root,
            "detail": (
                f"invocation_root={invocation_root}; package_root={package_root}"
            ),
        },
        *_representative_cli_checks(imports, commands),
        {
            "id": "representative_package_paths",
            "required": True,
            "ok": bool(package_paths.get("ok")),
            "detail": ",".join(package_paths.get("missing") or []) or "ok",
        },
    ]
    return {
        "schema_version": "loopx_release_candidate_checks_v0",
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "representative_cli": {
            "imports": imports,
            "commands": commands,
            "package_paths": package_paths,
        },
    }


def collect_python_distribution_checks(
    *,
    command_path: Path | None,
    package_root: Path,
) -> dict[str, Any]:
    """Validate a wheel/sdist install without requiring source-only wrappers."""

    imports = _import_summary()
    commands = _command_summary(command_path)
    package_paths = _package_path_summary(
        package_root,
        REPRESENTATIVE_DISTRIBUTION_PATHS,
    )
    distribution_command = _distribution_command_summary(command_path)
    checks = [
        {
            "id": "command_package_same_distribution",
            "required": True,
            "ok": bool(distribution_command.get("ok")),
            "detail": (
                f"command={distribution_command.get('command')}; "
                "recorded_commands="
                f"{','.join(distribution_command.get('recorded_commands') or []) or 'none'}"
            ),
        },
        *_representative_cli_checks(imports, commands),
        {
            "id": "representative_distribution_paths",
            "required": True,
            "ok": bool(package_paths.get("ok")),
            "detail": ",".join(package_paths.get("missing") or []) or "ok",
        },
    ]
    return {
        "schema_version": "loopx_python_distribution_checks_v0",
        "ok": all(check["ok"] for check in checks),
        "checks": checks,
        "representative_cli": {
            "imports": imports,
            "commands": commands,
            "package_paths": package_paths,
        },
        "distribution_command": distribution_command,
    }


def collect_deep_install_checks(
    *,
    command_path: Path | None,
    invocation_path: Path | None,
    package_root: Path,
    invocation_root: Path | None,
    distribution_root: str | Path | None,
) -> dict[str, Any]:
    """Select the deep-check contract for the proven installation shape."""

    if distribution_root is not None:
        return collect_python_distribution_checks(
            command_path=invocation_path or command_path,
            package_root=Path(distribution_root),
        )
    return collect_release_candidate_checks(
        command_path=command_path,
        package_root=package_root,
        invocation_root=invocation_root,
    )


def collect_installation_doctor(*, deep: bool) -> dict[str, Any]:
    """Validate the package/toolchain without opening registered user projects.

    Installers must not depend on workspace availability or macOS folder
    consent. Keep the same representative package and runtime semantic checks
    as deep doctor; ambient Goal, skill and provider health is a separate read.
    """
    from .control_plane.effect_runtime import collect_effect_runtime_readiness
    from .command_invocation import resolve_command_path
    from .doctor import current_script_invocation_path, python_distribution_install

    invocation = current_script_invocation_path()
    command = invocation or resolve_command_path("loopx")
    package_root = Path(__file__).resolve().parents[1]
    selected = os.environ.get("LOOPX_RELEASE_ROOT")
    runtime = collect_effect_runtime_readiness(deep=deep)
    checks = [
        {"id": "command_available", "required": True, "ok": command is not None},
        {"id": "typescript_effect_runtime_ready", "required": True,
         "ok": bool(runtime.get("ready")), "detail": runtime.get("status")},
    ]
    payload: dict[str, Any] = {
        "mode": "deep" if deep else "standard",
        "scope": "installation_only",
        "checks": checks,
        "typescript_control_plane": runtime,
        "path": {"loopx": str(command) if command else None},
    }
    if deep:
        candidate = collect_deep_install_checks(
            command_path=command,
            invocation_path=invocation,
            package_root=package_root,
            invocation_root=Path(selected).expanduser().resolve() if selected else package_root,
            distribution_root=python_distribution_install(
                Path(__file__).with_name("doctor.py").resolve()
            ).get("root"),
        )
        checks.extend(candidate["checks"])
        payload["release_candidate"] = candidate
    payload["ok"] = all(check["ok"] for check in checks)
    return payload
