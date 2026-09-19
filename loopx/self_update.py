from __future__ import annotations

from enum import StrEnum
from pathlib import Path
import os
import re
import shlex
import subprocess
import sys
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from .activation_qualification import (
    RUNTIME_ACTIVATION_QUALIFICATION_SCHEMA_VERSION,
    runtime_activation_qualification,
)
from .doctor import collect_doctor
from .install_contract import NO_CLONE_INSTALL_URL
from .runtime_activation import restart_services_for_runtime_activation
from .self_update_download import run_archive_installer


UPDATE_PLAN_SCHEMA_VERSION = "loopx_update_plan_v0"
DEFAULT_UPDATE_REPO = "loopx-project/loopx"
DEFAULT_UPDATE_REF = "stable"
ROLLBACK_PREVIOUS_ALIAS = "previous"
SOURCE_VERSION_CHECK_SCHEMA_VERSION = "loopx_source_version_check_v0"
SOURCE_VERSION_CHECK_TIMEOUT_SECONDS = 3
SOURCE_VERSION_READ_LIMIT_BYTES = 64 * 1024
PERSISTED_PYTHON_FILENAME = ".loopx-python"
_PACKAGE_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"$', re.MULTILINE)


class UpdateAction(StrEnum):
    """Stable operator intent for the self-update boundary."""

    CHECK = "check"
    PLAN = "plan"
    APPLY = "apply"


def resolve_update_action(
    action: UpdateAction | str | None = None,
    *,
    check: bool = False,
    dry_run: bool = False,
    execute: bool = False,
) -> UpdateAction:
    """Resolve explicit actions and legacy flag aliases without ambiguity."""

    explicit = UpdateAction(action) if action is not None else None
    legacy_actions = [
        candidate
        for enabled, candidate in (
            (check, UpdateAction.CHECK),
            (dry_run, UpdateAction.PLAN),
            (execute, UpdateAction.APPLY),
        )
        if enabled
    ]
    if len(legacy_actions) > 1:
        raise ValueError("choose one update action: check, plan, or apply")
    legacy = legacy_actions[0] if legacy_actions else None
    if explicit is not None and legacy is not None and explicit != legacy:
        raise ValueError(
            f"update action `{explicit.value}` conflicts with the legacy option for "
            f"`{legacy.value}`; use `loopx update {explicit.value}` alone"
        )
    return explicit or legacy or UpdateAction.PLAN


def _source_config(
    *,
    repo: str | None,
    ref: str | None,
    archive_url: str | None,
) -> dict[str, Any]:
    selected_repo = repo or os.environ.get("LOOPX_REPO") or DEFAULT_UPDATE_REPO
    ref_override = ref or os.environ.get("LOOPX_REF")
    selected_ref = ref_override or DEFAULT_UPDATE_REF
    archive_url_override = archive_url or os.environ.get("LOOPX_ARCHIVE_URL")
    selected_archive_url = archive_url_override
    if not selected_archive_url:
        selected_archive_url = (
            f"https://codeload.github.com/{selected_repo}/tar.gz/{selected_ref}"
        )
    if archive_url_override:
        channel = "github_archive_url_override"
    elif ref_override:
        channel = "github_archive_override"
    else:
        channel = "github_archive_stable"
    return {
        "channel": channel,
        "repo": selected_repo,
        "ref": selected_ref,
        "ref_source": "default_stable" if not ref_override else "override",
        "archive_url": selected_archive_url,
        "installer_url": NO_CLONE_INSTALL_URL,
    }


def _command_for_source(source: dict[str, Any]) -> str:
    if os.name == "nt":
        return (
            "Native Windows automatic archive update is not available. "
            "Update a trusted LoopX checkout, then run its "
            "`scripts/install-windows.ps1` with PowerShell 7."
        )
    return _update_action_command(UpdateAction.APPLY, source)


def _installer_env_for_source(
    source: dict[str, Any],
    *,
    base_env: dict[str, str] | None = None,
    current_release_root: str | Path | None = None,
) -> dict[str, str]:
    env = dict(os.environ if base_env is None else base_env)
    if not env.get("LOOPX_PYTHON") and current_release_root is not None:
        marker = Path(current_release_root) / PERSISTED_PYTHON_FILENAME
        try:
            persisted_python = marker.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise ValueError(
                "active LoopX release snapshot is missing its persisted Python runtime; "
                "set LOOPX_PYTHON to a Python 3.11+ executable before retrying"
            ) from exc
        if not persisted_python:
            raise ValueError(
                "active LoopX release snapshot has an empty persisted Python runtime; "
                "set LOOPX_PYTHON to a Python 3.11+ executable before retrying"
            )
        env["LOOPX_PYTHON"] = persisted_python
    env["LOOPX_REPO"] = str(source.get("repo") or DEFAULT_UPDATE_REPO)
    env["LOOPX_REF"] = str(source.get("ref") or DEFAULT_UPDATE_REF)
    if source.get("channel") == "github_archive_url_override" and source.get(
        "archive_url"
    ):
        env["LOOPX_ARCHIVE_URL"] = str(source["archive_url"])
    else:
        env.pop("LOOPX_ARCHIVE_URL", None)
    return env


def _source_version_check(source: dict[str, Any]) -> dict[str, Any]:
    base = {
        "schema_version": SOURCE_VERSION_CHECK_SCHEMA_VERSION,
        "attempted": False,
        "status": "skipped",
        "version": None,
        "version_tag": None,
        "matches_current": None,
        "source_url": None,
        "reason": None,
    }
    if source.get("channel") == "github_archive_url_override":
        return {
            **base,
            "reason": "custom archive URL is not assumed to match the configured GitHub repo/ref",
        }

    repo = str(source.get("repo") or "")
    ref = str(source.get("ref") or "")
    repo_parts = repo.split("/")
    if len(repo_parts) != 2 or not all(
        re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in repo_parts
    ):
        return {**base, "reason": "GitHub repo must use owner/name syntax"}
    if not ref:
        return {**base, "reason": "GitHub ref is missing"}

    owner, name = repo_parts
    source_url = (
        "https://raw.githubusercontent.com/"
        f"{quote(owner, safe='')}/{quote(name, safe='')}/{quote(ref, safe='')}/loopx/__init__.py"
    )
    request = Request(
        source_url,
        headers={"Accept": "text/plain", "User-Agent": "LoopX-update-check"},
    )
    try:
        with urlopen(  # noqa: S310 - the host is fixed to raw.githubusercontent.com.
            request,
            timeout=SOURCE_VERSION_CHECK_TIMEOUT_SECONDS,
        ) as response:
            body = response.read(SOURCE_VERSION_READ_LIMIT_BYTES).decode("utf-8")
    except Exception as exc:  # Remote comparison is advisory and must degrade offline.
        return {
            **base,
            "attempted": True,
            "status": "unavailable",
            "source_url": source_url,
            "reason": f"remote version check unavailable ({type(exc).__name__})",
        }

    match = _PACKAGE_VERSION_PATTERN.search(body)
    if not match:
        return {
            **base,
            "attempted": True,
            "status": "unavailable",
            "source_url": source_url,
            "reason": "remote package version was not found",
        }
    version = match.group(1)
    return {
        **base,
        "attempted": True,
        "status": "available",
        "version": version,
        "version_tag": f"v{version}",
        "source_url": source_url,
    }


def _release_root_from_doctor(doctor_payload: dict[str, Any]) -> str | None:
    package = (
        doctor_payload.get("package")
        if isinstance(doctor_payload.get("package"), dict)
        else {}
    )
    release_root = package.get("release_root")
    if isinstance(release_root, str) and release_root:
        return release_root
    path = (
        doctor_payload.get("path")
        if isinstance(doctor_payload.get("path"), dict)
        else {}
    )
    realpath = path.get("loopx_realpath")
    if isinstance(realpath, str) and realpath:
        realpath_path = Path(realpath)
        if realpath_path.name == "loopx" and realpath_path.parent.name == "scripts":
            return str(realpath_path.parent.parent)
    return None


def _user_loopx_bin(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local" / "bin" / "loopx"


def _user_releases_dir(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".local" / "share" / "loopx" / "releases"


def _release_script(release_root: Path) -> Path:
    return release_root / "scripts" / "loopx"


def _list_release_roots(releases_dir: Path) -> list[Path]:
    if not releases_dir.exists():
        return []
    return sorted(
        (
            item
            for item in releases_dir.iterdir()
            if item.is_dir() and _release_script(item).exists()
        ),
        key=lambda item: item.name,
        reverse=True,
    )


def _rollback_release_command(release_id: str) -> str:
    return f"loopx update --rollback {shlex.quote(release_id)}\nloopx doctor"


def _rollback_plan(doctor_payload: dict[str, Any]) -> dict[str, Any]:
    release_root = _release_root_from_doctor(doctor_payload)
    if not release_root:
        return {
            "available": False,
            "reason": "current loopx command is not a release snapshot",
            "current_release_root": None,
            "rollback_command": None,
        }
    release_id = Path(release_root).name
    return {
        "available": True,
        "reason": "current release snapshot can be restored with the first-class rollback CLI",
        "rollback_release_id": release_id,
        "current_release_root": release_root,
        "rollback_command": _rollback_release_command(release_id),
    }


def _install_lifecycle(doctor_payload: dict[str, Any]) -> dict[str, Any]:
    package = (
        doctor_payload.get("package")
        if isinstance(doctor_payload.get("package"), dict)
        else {}
    )
    freshness = (
        doctor_payload.get("install_freshness")
        if isinstance(doctor_payload.get("install_freshness"), dict)
        else {}
    )
    install_kind = package.get("install_kind")
    if not isinstance(install_kind, str) or not install_kind:
        if freshness.get("install_kind") == "python_distribution":
            install_kind = "python_distribution"
        elif _release_root_from_doctor(doctor_payload):
            install_kind = "release_snapshot"
        else:
            install_kind = "live_checkout"

    installer = None
    installer_environment = None
    if install_kind == "release_snapshot":
        owner = "loopx_release_snapshot"
        apply_supported = os.name != "nt"
        execution_driver = "archive_snapshot" if apply_supported else None
        reason = (
            "LoopX owns this archive release snapshot and can atomically replace it"
            if apply_supported
            else "native Windows snapshot updates remain owned by install-windows.ps1"
        )
        owner_command = None
    elif install_kind == "python_distribution":
        owner = "python_package_manager"
        installer = freshness.get("python_distribution_installer")
        installer_environment = freshness.get(
            "python_distribution_installer_environment"
        )
        if not isinstance(installer, str) or not installer:
            distribution = (
                package.get("python_distribution")
                if isinstance(package.get("python_distribution"), dict)
                else {}
            )
            installer = distribution.get("installer")
            installer_environment = distribution.get("installer_environment")
        apply_supported = installer in {"pip", "pipx"}
        execution_driver = (
            "python_pipx"
            if installer == "pipx"
            else "python_pip"
            if installer == "pip"
            else None
        )
        reason = (
            "LoopX can ask the owning Python interpreter's pip to upgrade the same environment"
            if installer == "pip"
            else "LoopX can ask pipx to upgrade its recorded LoopX environment"
            if installer == "pipx"
            else (
                f"the active Python environment is owned by {installer or 'an unknown installer'}; "
                "use that package manager directly"
            )
        )
        owner_command = freshness.get("upgrade_command")
    else:
        owner = "source_checkout"
        apply_supported = False
        execution_driver = None
        reason = (
            "the active checkout owns contributor updates; LoopX must not replace it with "
            "an archive snapshot"
        )
        owner_command = freshness.get("contributor_upgrade_command")

    return {
        "install_kind": install_kind,
        "owner": owner,
        "loopx_apply_supported": apply_supported,
        "execution_driver": execution_driver,
        "package_manager": installer if install_kind == "python_distribution" else None,
        "package_manager_environment": (
            installer_environment if install_kind == "python_distribution" else None
        ),
        "reason": reason,
        "owner_upgrade_command": owner_command,
    }


def _update_action_command(action: UpdateAction, source: dict[str, Any]) -> str:
    parts = ["loopx", "update", action.value]
    default_source = (
        source.get("repo") == DEFAULT_UPDATE_REPO
        and source.get("ref") == DEFAULT_UPDATE_REF
        and source.get("channel") == "github_archive_stable"
    )
    if not default_source:
        parts.extend(["--repo", str(source.get("repo") or DEFAULT_UPDATE_REPO)])
        parts.extend(["--ref", str(source.get("ref") or DEFAULT_UPDATE_REF)])
        if source.get("channel") == "github_archive_url_override":
            parts.extend(["--archive-url", str(source.get("archive_url") or "")])
    return shlex.join(parts)


def _python_distribution_rollback_plan(
    current_version: object,
    lifecycle: dict[str, Any],
) -> dict[str, Any]:
    if lifecycle.get("execution_driver") == "python_pipx":
        return {
            "available": False,
            "reason": (
                "pipx owns its environment metadata; inspect the recorded spec before "
                "requesting a version rollback with pipx"
            ),
            "rollback_command": None,
        }
    if not isinstance(current_version, str) or not current_version:
        return {
            "available": False,
            "reason": "the installed Python distribution version is unavailable",
            "rollback_command": None,
        }
    command = (
        f"{shlex.quote(sys.executable)} -m pip install "
        f"{shlex.quote(f'loopx=={current_version}')}\n"
        "loopx workflow-skills --install\n"
        "loopx slash-commands --install\n"
        "loopx doctor"
    )
    return {
        "available": True,
        "reason": "pip can request the previously installed LoopX version",
        "rollback_version": current_version,
        "rollback_command": command,
    }


def build_rollback_plan(
    *,
    release_id: str,
    doctor_payload: dict[str, Any] | None = None,
    home: Path | None = None,
) -> dict[str, Any]:
    doctor = doctor_payload or collect_doctor()
    current_release_root = _release_root_from_doctor(doctor)
    releases_dir = _user_releases_dir(home)
    releases = _list_release_roots(releases_dir)
    current_root_path = (
        Path(current_release_root).resolve() if current_release_root else None
    )
    requested_release_id = release_id.strip()
    selected: Path | None = None
    reason = None

    if not requested_release_id:
        reason = "rollback release id is required"
    elif requested_release_id == ROLLBACK_PREVIOUS_ALIAS:
        for candidate in releases:
            if current_root_path and candidate.resolve() == current_root_path:
                continue
            selected = candidate
            break
        if selected is None:
            reason = "no previous release snapshot found"
    elif "/" in requested_release_id or requested_release_id in {".", ".."}:
        reason = "rollback release id must be a release directory name"
    else:
        candidate = releases_dir / requested_release_id
        if not _release_script(candidate).exists():
            reason = f"release snapshot not found: {requested_release_id}"
        else:
            selected = candidate

    available = selected is not None
    selected_release_id = selected.name if selected else None
    selected_release_root = str(selected) if selected else None
    return {
        "ok": available,
        "schema_version": UPDATE_PLAN_SCHEMA_VERSION,
        "mode": "rollback",
        "dry_run": False,
        "execute_requested": True,
        "requested_release_id": requested_release_id,
        "current": {
            "current_release_root": current_release_root,
        },
        "plan": {
            "action": "rollback",
            "available": available,
            "reason": reason,
            "releases_dir": str(releases_dir),
            "release_count": len(releases),
            "selected_release_id": selected_release_id,
            "selected_release_root": selected_release_root,
            "rollback_command": _rollback_release_command(requested_release_id),
            "mutates_loopx_runtime_state": False,
            "mutates_release_install": True,
            "post_rollback_validation": "loopx doctor",
        },
        "execution": None,
        "recommended_action": (
            "rollback target selected; execute rollback and validate with doctor"
            if available
            else "choose an existing release id or use `loopx update --rollback previous` when available"
        ),
    }


def build_update_plan(
    *,
    repo: str | None = None,
    ref: str | None = None,
    archive_url: str | None = None,
    action: UpdateAction | str | None = None,
    check_only: bool = False,
    execute: bool = False,
    doctor_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    requested_action = resolve_update_action(
        action,
        check=check_only,
        execute=execute,
    )
    check_only = requested_action is UpdateAction.CHECK
    execute = requested_action is UpdateAction.APPLY
    doctor = doctor_payload or collect_doctor()
    install_freshness = (
        doctor.get("install_freshness")
        if isinstance(doctor.get("install_freshness"), dict)
        else {}
    )
    release_manifest = (
        doctor.get("release_manifest")
        if isinstance(doctor.get("release_manifest"), dict)
        else {}
    )
    release_manifest_body = (
        release_manifest.get("manifest")
        if isinstance(release_manifest.get("manifest"), dict)
        else {}
    )
    source = _source_config(repo=repo, ref=ref, archive_url=archive_url)
    lifecycle = _install_lifecycle(doctor)
    archive_install_command = _command_for_source(source)
    dry_run = not execute
    path = doctor.get("path") if isinstance(doctor.get("path"), dict) else {}
    requires_upgrade = install_freshness.get("requires_upgrade")
    current_version = install_freshness.get("current_version")
    source_version_check = (
        _source_version_check(source)
        if check_only and lifecycle["owner"] == "loopx_release_snapshot"
        else {
            "schema_version": SOURCE_VERSION_CHECK_SCHEMA_VERSION,
            "attempted": False,
            "status": (
                "delegated"
                if check_only and lifecycle["owner"] != "loopx_release_snapshot"
                else "not_requested"
            ),
            "version": None,
            "version_tag": None,
            "matches_current": None,
            "source_url": None,
            "reason": (
                f"update availability is owned by {lifecycle['owner']}"
                if check_only and lifecycle["owner"] != "loopx_release_snapshot"
                else "remote source comparison runs only for `loopx update check`"
            ),
        }
    )
    source_version = source_version_check.get("version")
    if source_version_check.get("status") == "available":
        source_version_check["matches_current"] = (
            source_version == current_version
            if isinstance(current_version, str) and current_version
            else None
        )
    runtime_activation = (
        runtime_activation_qualification(
            install_freshness=install_freshness,
            source=source,
        )
        if lifecycle["owner"] == "loopx_release_snapshot"
        else {
            "schema_version": RUNTIME_ACTIVATION_QUALIFICATION_SCHEMA_VERSION,
            "decision": "not_applicable",
            "runtime_active": None,
            "successor": {"required": False, "kind": None},
            "reason": (
                f"runtime activation is governed by {lifecycle['owner']}, not an archive snapshot"
            ),
        }
    )
    apply_supported = bool(lifecycle["loopx_apply_supported"])
    owner_upgrade_command = lifecycle.get("owner_upgrade_command")
    install_command = (
        archive_install_command
        if lifecycle["owner"] == "loopx_release_snapshot"
        else owner_upgrade_command
    )
    backup = (
        _python_distribution_rollback_plan(current_version, lifecycle)
        if lifecycle["execution_driver"] in {"python_pip", "python_pipx"}
        else _rollback_plan(doctor)
    )
    if execute and not apply_supported:
        recommended_action = (
            f"use the active {lifecycle['owner']} upgrade path:\n{owner_upgrade_command}"
            if owner_upgrade_command
            else f"use the active {lifecycle['owner']} upgrade path"
        )
    elif execute:
        recommended_action = "review execution result and post-update doctor output"
    elif check_only and lifecycle["owner"] != "loopx_release_snapshot":
        recommended_action = (
            f"use the active {lifecycle['owner']} upgrade path:\n{owner_upgrade_command}"
            if owner_upgrade_command
            else f"use the active {lifecycle['owner']} upgrade path"
        )
    elif lifecycle["owner"] != "loopx_release_snapshot":
        recommended_action = (
            "run `loopx update apply` to use the active Python package manager, "
            "then review host-material readback"
            if apply_supported
            else (
                f"use the active {lifecycle['owner']} upgrade path:\n{owner_upgrade_command}"
                if owner_upgrade_command
                else f"use the active {lifecycle['owner']} upgrade path"
            )
        )
    elif (
        check_only
        and runtime_activation.get("decision")
        == "release_or_install_successor_required"
    ):
        recommended_action = (
            "installed runtime does not contain the trusted target source; "
            "project a release/install successor, then run `loopx update plan`"
        )
    elif check_only and source_version_check.get("matches_current") is False:
        recommended_action = (
            f"source version v{source_version} differs from installed version "
            f"v{current_version}; run `loopx update plan`, then `loopx update apply`"
        )
    elif check_only and source_version_check.get("status") == "unavailable":
        recommended_action = (
            "local install health is known, but the selected source version could not be checked; "
            "retry online or run `loopx update apply` to refresh"
        )
    elif check_only and source_version_check.get("status") == "skipped":
        recommended_action = (
            "local install health is known, but source version comparison was skipped; "
            "run `loopx update plan` to review the source"
        )
    elif (
        check_only
        and runtime_activation.get("decision") == "activation_qualification_required"
    ):
        recommended_action = (
            "installed runtime activation is not proven; refresh trusted source lineage "
            "before claiming the merged behavior is active"
        )
    elif (
        check_only
        and source_version_check.get("matches_current") is True
        and requires_upgrade is False
    ):
        recommended_action = (
            "installed version and trusted source lineage match; no update needed"
        )
    elif check_only and requires_upgrade is True:
        recommended_action = (
            "run `loopx update plan` to review the source and rollback plan"
        )
    elif requires_upgrade is False:
        recommended_action = (
            "no update needed; use `loopx update plan` or "
            "`loopx update apply` only to force a refresh"
        )
    elif check_only:
        recommended_action = (
            "run `loopx update plan` to review the source and rollback plan"
        )
    else:
        recommended_action = (
            "run `loopx update apply` if you accept the source and rollback plan"
        )
    apply_command = (
        _update_action_command(UpdateAction.APPLY, source)
        if apply_supported
        else None
    )
    commands = {
        "check": _update_action_command(UpdateAction.CHECK, source),
        "plan": _update_action_command(UpdateAction.PLAN, source),
        "apply": apply_command,
        "owner_upgrade": owner_upgrade_command,
    }
    if execute and apply_supported:
        next_action = {
            "kind": "review_execution",
            "command": "loopx doctor",
            "mutating": False,
            "requires_explicit_approval": False,
            "reason": recommended_action,
        }
    elif apply_command:
        next_action = {
            "kind": "apply_update",
            "command": apply_command,
            "mutating": True,
            "requires_explicit_approval": True,
            "reason": recommended_action,
        }
    else:
        next_action = {
            "kind": "use_installation_owner",
            "command": owner_upgrade_command,
            "mutating": bool(owner_upgrade_command),
            "requires_explicit_approval": bool(owner_upgrade_command),
            "reason": recommended_action,
        }
    return {
        "ok": not execute or apply_supported,
        "schema_version": UPDATE_PLAN_SCHEMA_VERSION,
        "mode": "update",
        "requested_action": requested_action.value,
        "check_only": check_only,
        "dry_run": dry_run,
        "execute_requested": execute,
        "changes_applied": False,
        "install_lifecycle": lifecycle,
        "commands": commands,
        "next_action": next_action,
        "source": source,
        "source_version_check": source_version_check,
        "runtime_activation_qualification": runtime_activation,
        "current": {
            "loopx_command": path.get("loopx"),
            "loopx_realpath": path.get("loopx_realpath"),
            "current_version": current_version,
            "current_version_tag": install_freshness.get("current_version_tag"),
            "manifest_package_version": install_freshness.get(
                "manifest_package_version"
            ),
            "manifest_package_version_tag": install_freshness.get(
                "manifest_package_version_tag"
            ),
            "manifest_package_version_matches_runtime": install_freshness.get(
                "manifest_package_version_matches_runtime"
            ),
            "release_id": install_freshness.get("release_id"),
            "install_freshness_status": install_freshness.get("status"),
            "requires_upgrade": requires_upgrade,
            "reason": install_freshness.get("reason"),
            "release_manifest_available": release_manifest.get("available"),
            "release_manifest_path": release_manifest.get("path"),
            "release_manifest": release_manifest_body,
        },
        "plan": {
            "action": "check_only"
            if check_only
            else "execute_installer"
            if execute
            else "dry_run",
            "apply_supported": apply_supported,
            "blocked_reason": lifecycle["reason"]
            if execute and not apply_supported
            else None,
            "install_command": install_command,
            "post_update_validation": "loopx doctor",
            "mutates_loopx_runtime_state": False,
            "mutates_release_install": execute and apply_supported,
            "backup": backup,
        },
        "execution": None,
        "error": (
            f"`loopx update apply` cannot mutate an installation owned by {lifecycle['owner']}"
            if execute and not apply_supported
            else None
        ),
        "recommended_action": recommended_action,
    }


def _execute_python_distribution_update(
    payload: dict[str, Any],
    *,
    timeout_seconds: int,
) -> dict[str, Any]:
    lifecycle = (
        payload.get("install_lifecycle")
        if isinstance(payload.get("install_lifecycle"), dict)
        else {}
    )
    driver = lifecycle.get("execution_driver")
    package_manager_environment = lifecycle.get("package_manager_environment")
    install_command = (
        ["pipx", "upgrade", str(package_manager_environment or "loopx")]
        if driver == "python_pipx"
        else [sys.executable, "-m", "pip", "install", "--upgrade", "loopx"]
    )
    commands = {
        "install": install_command,
        "workflow_skills": [
            sys.executable,
            "-m",
            "loopx.cli",
            "workflow-skills",
            "--install",
            "--format",
            "json",
        ],
        "slash_commands": [
            sys.executable,
            "-m",
            "loopx.cli",
            "slash-commands",
            "--install",
            "--format",
            "json",
        ],
        "doctor": [
            sys.executable,
            "-m",
            "loopx.cli",
            "doctor",
            "--format",
            "json",
        ],
        "extension_doctor": [
            sys.executable,
            "-m",
            "loopx.cli",
            "extension",
            "doctor",
            "--all-enabled",
            "--execute",
            "--format",
            "json",
        ],
    }
    results: dict[str, subprocess.CompletedProcess[str]] = {}
    results["install"] = subprocess.run(
        commands["install"],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        timeout=timeout_seconds,
    )
    if results["install"].returncode == 0:
        for step in ("workflow_skills", "slash_commands", "doctor"):
            results[step] = subprocess.run(
                commands[step],
                text=True, encoding="utf-8", errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
            )
        if all(
            results[step].returncode == 0
            for step in ("workflow_skills", "slash_commands", "doctor")
        ):
            results["extension_doctor"] = subprocess.run(
                commands["extension_doctor"],
                text=True, encoding="utf-8", errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
            )

    execution: dict[str, Any] = {
        "driver": driver,
        "python_executable": sys.executable,
        "install_returncode": results["install"].returncode,
        "install_stdout_tail": results["install"].stdout[-2000:],
        "install_stderr_tail": results["install"].stderr[-2000:],
    }
    for step in ("workflow_skills", "slash_commands", "doctor", "extension_doctor"):
        result = results.get(step)
        if result is None:
            execution[f"{step}_status"] = "skipped_prior_step_failed"
            continue
        execution[f"{step}_returncode"] = result.returncode
        execution[f"{step}_stdout_tail"] = result.stdout[-2000:]
        execution[f"{step}_stderr_tail"] = result.stderr[-2000:]

    runtime_steps = (
        "install",
        "workflow_skills",
        "slash_commands",
        "doctor",
    )
    runtime_ready = all(
        step in results and results[step].returncode == 0 for step in runtime_steps
    )
    extension_ready = (
        "extension_doctor" in results and results["extension_doctor"].returncode == 0
    )
    updated = dict(payload)
    updated["execution"] = execution
    updated["ok"] = runtime_ready and extension_ready
    updated["changes_applied"] = results["install"].returncode == 0
    execution.update(
        restart_services_for_runtime_activation(
            changes_applied=updated["changes_applied"],
            runtime_ready=runtime_ready,
        )
    )
    if updated["ok"]:
        updated["recommended_action"] = (
            "PyPI update and host-material readback passed; use the new LoopX process"
        )
        updated["next_action"] = {
            "kind": "use_updated_runtime",
            "command": "loopx doctor",
            "mutating": False,
            "requires_explicit_approval": False,
            "reason": updated["recommended_action"],
        }
    elif runtime_ready:
        updated["recommended_action"] = (
            "the updated runtime is installed and serving; repair the blocked "
            "enabled extension providers, then rerun "
            "`loopx extension doctor --all-enabled --execute`"
        )
        updated["next_action"] = {
            "kind": "repair_blocked_extensions",
            "command": "loopx extension doctor --all-enabled --execute",
            "mutating": False,
            "requires_explicit_approval": False,
            "reason": updated["recommended_action"],
        }
    else:
        backup = (
            payload.get("plan", {}).get("backup")
            if isinstance(payload.get("plan"), dict)
            else {}
        )
        rollback_command = (
            backup.get("rollback_command") if isinstance(backup, dict) else None
        )
        updated["recommended_action"] = (
            "inspect the failed update step, then use the recorded package rollback command"
            if rollback_command
            else "inspect the failed package-manager or host-material update step"
        )
        updated["next_action"] = {
            "kind": "review_or_rollback",
            "command": rollback_command,
            "mutating": bool(rollback_command),
            "requires_explicit_approval": bool(rollback_command),
            "reason": updated["recommended_action"],
        }
    return updated


def execute_update_plan(
    payload: dict[str, Any], *, timeout_seconds: int = 600
) -> dict[str, Any]:
    lifecycle = (
        payload.get("install_lifecycle")
        if isinstance(payload.get("install_lifecycle"), dict)
        else {}
    )
    driver = lifecycle.get("execution_driver")
    if driver is None and isinstance(payload.get("source"), dict):
        # Compatibility for callers that persisted the v0 archive plan before
        # install_lifecycle became explicit.
        driver = "archive_snapshot"
    if driver in {"python_pip", "python_pipx"}:
        return _execute_python_distribution_update(
            payload,
            timeout_seconds=timeout_seconds,
        )
    if driver != "archive_snapshot":
        updated = dict(payload)
        updated["ok"] = False
        updated["changes_applied"] = False
        updated["execution"] = {
            "status": "unsupported_install_owner",
            "owner": lifecycle.get("owner"),
            "reason": lifecycle.get("reason"),
        }
        return updated
    if os.name == "nt":
        updated = dict(payload)
        updated["execution"] = {
            "status": "unsupported_platform",
            "reason": (
                "native Windows automatic archive update is not available; "
                "rerun scripts/install-windows.ps1 from an updated trusted checkout"
            ),
        }
        updated["ok"] = False
        updated["recommended_action"] = updated["execution"]["reason"]
        return updated
    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    installer_url = str(source.get("installer_url") or NO_CLONE_INSTALL_URL)
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    backup = plan.get("backup") if isinstance(plan.get("backup"), dict) else {}
    current_release_root = backup.get("current_release_root")
    env = _installer_env_for_source(
        source,
        current_release_root=(
            current_release_root if isinstance(current_release_root, str) else None
        ),
    )
    install_result, download_observation = run_archive_installer(
        installer_url,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    loopx_bin = Path.home() / ".local" / "bin" / "loopx"
    doctor_result = subprocess.run(
        [str(loopx_bin), "--format", "json", "doctor"],
        text=True, encoding="utf-8", errors="replace",
        capture_output=True,
        env=env,
        timeout=timeout_seconds,
    )
    execution = {
        "installer_download": download_observation,
        "install_returncode": install_result.returncode,
        "doctor_returncode": doctor_result.returncode,
        "install_stdout_tail": install_result.stdout[-2000:],
        "install_stderr_tail": install_result.stderr[-2000:],
        "doctor_stdout_tail": doctor_result.stdout[-2000:],
        "doctor_stderr_tail": doctor_result.stderr[-2000:],
    }
    extension_doctor_result = None
    if install_result.returncode == 0 and doctor_result.returncode == 0:
        extension_doctor_result = subprocess.run(
            [
                str(loopx_bin),
                "--format",
                "json",
                "extension",
                "doctor",
                "--all-enabled",
                "--execute",
            ],
            text=True, encoding="utf-8", errors="replace",
            capture_output=True,
            env=env,
            timeout=timeout_seconds,
        )
        execution.update(
            {
                "extension_doctor_returncode": extension_doctor_result.returncode,
                "extension_doctor_stdout_tail": extension_doctor_result.stdout[-2000:],
                "extension_doctor_stderr_tail": extension_doctor_result.stderr[-2000:],
            }
        )
    else:
        execution["extension_doctor_status"] = "skipped_release_update_failed"
    runtime_ready = install_result.returncode == 0 and doctor_result.returncode == 0
    updated = dict(payload)
    updated["execution"] = execution
    updated["ok"] = (
        runtime_ready
        and extension_doctor_result is not None
        and extension_doctor_result.returncode == 0
    )
    updated["changes_applied"] = install_result.returncode == 0
    execution.update(
        restart_services_for_runtime_activation(
            changes_applied=updated["changes_applied"],
            runtime_ready=runtime_ready,
        )
    )
    if not updated["ok"] and runtime_ready:
        updated["recommended_action"] = (
            "the updated runtime is installed and serving; repair the blocked "
            "enabled extension providers, then rerun "
            "`loopx extension doctor --all-enabled --execute`"
        )
        updated["next_action"] = {
            "kind": "repair_blocked_extensions",
            "command": "loopx extension doctor --all-enabled --execute",
            "mutating": False,
            "requires_explicit_approval": False,
            "reason": updated["recommended_action"],
        }
        return updated
    if not updated["ok"]:
        updated["recommended_action"] = (
            "inspect update execution tails and restore from rollback plan if needed"
        )
        rollback_command = backup.get("rollback_command")
        updated["next_action"] = {
            "kind": "review_or_rollback",
            "command": rollback_command,
            "mutating": bool(rollback_command),
            "requires_explicit_approval": bool(rollback_command),
            "reason": updated["recommended_action"],
        }
        return updated
    updated["recommended_action"] = (
        "archive update and readback passed; use the new LoopX process"
    )
    updated["next_action"] = {
        "kind": "use_updated_runtime",
        "command": "loopx doctor",
        "mutating": False,
        "requires_explicit_approval": False,
        "reason": updated["recommended_action"],
    }
    return updated


def execute_rollback_plan(
    payload: dict[str, Any],
    *,
    timeout_seconds: int = 600,
    home: Path | None = None,
) -> dict[str, Any]:
    if os.name == "nt":
        updated = dict(payload)
        updated["ok"] = False
        updated["execution"] = {
            "status": "unsupported_platform",
            "reason": (
                "native Windows automatic rollback is not available; "
                "rerun scripts/install-windows.ps1 for the selected trusted checkout"
            ),
        }
        updated["recommended_action"] = updated["execution"]["reason"]
        return updated
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    selected_release_root = plan.get("selected_release_root")
    if not payload.get("ok") or not selected_release_root:
        return payload
    release_root = Path(str(selected_release_root))
    target_script = _release_script(release_root)
    loopx_bin = _user_loopx_bin(home)
    previous_link_target = os.readlink(loopx_bin) if loopx_bin.is_symlink() else None
    execution: dict[str, Any] = {
        "target_script": str(target_script),
        "loopx_command": str(loopx_bin),
        "previous_link_target": previous_link_target,
        "restored_previous_on_failure": False,
    }
    updated = dict(payload)
    try:
        loopx_bin.parent.mkdir(parents=True, exist_ok=True)
        temp_link = loopx_bin.with_name(f".{loopx_bin.name}.rollback.{os.getpid()}")
        if temp_link.exists() or temp_link.is_symlink():
            temp_link.unlink()
        temp_link.symlink_to(target_script)
        os.replace(temp_link, loopx_bin)
        doctor_result = subprocess.run(
            [str(loopx_bin), "--format", "json", "doctor"],
            text=True, encoding="utf-8", errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
        )
        execution.update(
            {
                "doctor_returncode": doctor_result.returncode,
                "doctor_stdout_tail": doctor_result.stdout[-2000:],
                "doctor_stderr_tail": doctor_result.stderr[-2000:],
            }
        )
        if doctor_result.returncode == 0:
            extension_doctor_result = subprocess.run(
                [
                    str(loopx_bin),
                    "--format",
                    "json",
                    "extension",
                    "doctor",
                    "--all-enabled",
                    "--execute",
                ],
                text=True, encoding="utf-8", errors="replace",
                capture_output=True,
                timeout=timeout_seconds,
            )
            execution.update(
                {
                    "extension_doctor_returncode": extension_doctor_result.returncode,
                    "extension_doctor_stdout_tail": extension_doctor_result.stdout[
                        -2000:
                    ],
                    "extension_doctor_stderr_tail": extension_doctor_result.stderr[
                        -2000:
                    ],
                }
            )
            updated["ok"] = extension_doctor_result.returncode == 0
        else:
            execution["extension_doctor_status"] = "skipped_core_doctor_failed"
            updated["ok"] = False
        if doctor_result.returncode != 0 and previous_link_target:
            restore_link = loopx_bin.with_name(
                f".{loopx_bin.name}.rollback.restore.{os.getpid()}"
            )
            if restore_link.exists() or restore_link.is_symlink():
                restore_link.unlink()
            restore_link.symlink_to(previous_link_target)
            os.replace(restore_link, loopx_bin)
            execution["restored_previous_on_failure"] = True
    except Exception as exc:
        execution["error"] = str(exc)
        updated["ok"] = False
        if previous_link_target:
            restore_link = loopx_bin.with_name(
                f".{loopx_bin.name}.rollback.restore.{os.getpid()}"
            )
            try:
                if restore_link.exists() or restore_link.is_symlink():
                    restore_link.unlink()
                restore_link.symlink_to(previous_link_target)
                os.replace(restore_link, loopx_bin)
                execution["restored_previous_on_failure"] = True
            except Exception as restore_exc:
                execution["restore_error"] = str(restore_exc)
    finally:
        if "temp_link" in locals() and (temp_link.exists() or temp_link.is_symlink()):
            temp_link.unlink()
        if "restore_link" in locals() and (
            restore_link.exists() or restore_link.is_symlink()
        ):
            restore_link.unlink()
    updated["execution"] = execution
    if updated["ok"]:
        updated["recommended_action"] = "rollback complete; review doctor output"
    elif execution.get("doctor_returncode") == 0 and execution.get(
        "extension_doctor_returncode"
    ) not in {None, 0}:
        updated["recommended_action"] = (
            "rollback activated, but one or more enabled extensions remain fail "
            "closed; repair the provider and run `loopx extension doctor "
            "--all-enabled --execute`"
        )
    else:
        updated["recommended_action"] = (
            "rollback failed; inspect execution error before retrying"
        )
    return updated


def render_update_plan_markdown(payload: dict[str, Any]) -> str:
    if payload.get("mode") == "rollback":
        plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
        execution = (
            payload.get("execution")
            if isinstance(payload.get("execution"), dict)
            else None
        )
        lines = [
            "# LoopX Update Rollback",
            "",
            f"- OK: `{payload.get('ok')}`",
            f"- Requested release: `{payload.get('requested_release_id')}`",
            f"- Selected release: `{plan.get('selected_release_id')}`",
            f"- Selected root: `{plan.get('selected_release_root')}`",
            f"- Reason: `{plan.get('reason')}`",
            f"- Runtime state mutation: `{plan.get('mutates_loopx_runtime_state')}`",
            f"- Release install mutation: `{plan.get('mutates_release_install')}`",
            f"- Recommended action: {payload.get('recommended_action')}",
        ]
        rollback_command = plan.get("rollback_command")
        if rollback_command:
            lines.extend(
                ["", "## Rollback Command", "", "```bash", str(rollback_command), "```"]
            )
        if execution:
            lines.extend(
                [
                    "",
                    "## Execution",
                    "",
                    f"- Doctor return code: `{execution.get('doctor_returncode')}`",
                ]
            )
        return "\n".join(lines) + "\n"

    source = payload.get("source") if isinstance(payload.get("source"), dict) else {}
    source_version_check = (
        payload.get("source_version_check")
        if isinstance(payload.get("source_version_check"), dict)
        else {}
    )
    runtime_activation = (
        payload.get("runtime_activation_qualification")
        if isinstance(payload.get("runtime_activation_qualification"), dict)
        else {}
    )
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    plan = payload.get("plan") if isinstance(payload.get("plan"), dict) else {}
    backup = plan.get("backup") if isinstance(plan.get("backup"), dict) else {}
    next_action = (
        payload.get("next_action")
        if isinstance(payload.get("next_action"), dict)
        else {}
    )
    notice = (
        "> **No update was applied.** This is a read-only check/plan."
        if not payload.get("execute_requested")
        else "> **Apply was requested.** Review the execution and validation result below."
    )
    lines = [
        "# LoopX Update",
        "",
        notice,
        "",
        "## Next Action",
        "",
        f"- Kind: `{next_action.get('kind')}`",
        f"- Mutating: `{next_action.get('mutating')}`",
        f"- Requires explicit approval: `{next_action.get('requires_explicit_approval')}`",
    ]
    next_command = next_action.get("command")
    if next_command:
        lines.extend(["", "```bash", str(next_command), "```"])
    prompt_upgrade = payload.get("automation_prompt_upgrade")
    if isinstance(prompt_upgrade, dict):
        lines.extend(["", "## Automation Prompts", "",
                      f"- Status: `{prompt_upgrade.get('status')}`"])
        if "upgrade_complete" in payload:
            lines.append(f"- Upgrade complete (runtime + prompts): `{payload['upgrade_complete']}`")
        for item in prompt_upgrade.get("results", []):
            lines.append(f"- `{item['automation_id']}`: `{item['status']}`")
            if item.get("reason"):
                lines.append(f"  - {item['reason']}")
        if prompt_upgrade.get("snapshot_file"):
            lines.append(f"- Private recovery snapshot: `{prompt_upgrade['snapshot_file']}`")
        if prompt_upgrade.get("next_action"):
            lines.append(f"- Next: {prompt_upgrade['next_action']}")
    lines.extend(
        [
            "",
            "## Details",
            "",
            f"- OK: `{payload.get('ok')}`",
            f"- Requested action: `{payload.get('requested_action')}`",
            f"- Changes applied: `{payload.get('changes_applied')}`",
            f"- Mode: `{plan.get('action')}`",
            f"- Dry run: `{payload.get('dry_run')}`",
            f"- Source: `{source.get('repo')}` @ `{source.get('ref')}`",
            f"- Source version check: `{source_version_check.get('status')}`",
            f"- Source version: `{source_version_check.get('version')}`",
            f"- Source version tag: `{source_version_check.get('version_tag')}`",
            f"- Source version matches current: `{source_version_check.get('matches_current')}`",
            f"- Source version check reason: {source_version_check.get('reason')}",
            f"- Runtime activation decision: `{runtime_activation.get('decision')}`",
            f"- Runtime active: `{runtime_activation.get('runtime_active')}`",
            f"- Installed source commit: `{runtime_activation.get('installed_source_commit')}`",
            f"- Target source commit: `{runtime_activation.get('target_source_commit')}`",
            f"- Source revision relation: `{runtime_activation.get('revision_relation')}`",
            f"- Runtime activation reason: {runtime_activation.get('reason')}",
            f"- Current version: `{current.get('current_version')}`",
            f"- Current version tag: `{current.get('current_version_tag')}`",
            f"- Manifest package version: `{current.get('manifest_package_version')}`",
            f"- Manifest package version tag: `{current.get('manifest_package_version_tag')}`",
            f"- Manifest package matches runtime: `{current.get('manifest_package_version_matches_runtime')}`",
            f"- Freshness: `{current.get('install_freshness_status')}`",
            f"- Requires upgrade: `{current.get('requires_upgrade')}`",
            f"- Runtime state mutation: `{plan.get('mutates_loopx_runtime_state')}`",
            f"- Release install mutation: `{plan.get('mutates_release_install')}`",
            f"- Install kind: `{(payload.get('install_lifecycle') or {}).get('install_kind')}`",
            f"- Install owner: `{(payload.get('install_lifecycle') or {}).get('owner')}`",
            f"- Apply supported: `{plan.get('apply_supported')}`",
            f"- Rollback available: `{backup.get('available')}`",
            f"- Recommended action: {payload.get('recommended_action')}",
            "",
            "## Planned Installation-owner Command",
            "",
            "```bash",
            str(plan.get("install_command") or ""),
            "```",
        ]
    )
    commands = (
        payload.get("commands") if isinstance(payload.get("commands"), dict) else {}
    )
    lines.extend(
        [
            "",
            "## Intent Commands",
            "",
            f"- Check: `{commands.get('check')}`",
            f"- Plan: `{commands.get('plan')}`",
            f"- Apply: `{commands.get('apply')}`",
        ]
    )
    rollback_command = backup.get("rollback_command")
    if rollback_command:
        lines.extend(
            ["", "## Rollback Command", "", "```bash", str(rollback_command), "```"]
        )
    execution = payload.get("execution")
    if isinstance(execution, dict):
        lines.extend(
            [
                "",
                "## Execution",
                "",
                f"- Install return code: `{execution.get('install_returncode')}`",
                f"- Doctor return code: `{execution.get('doctor_returncode')}`",
            ]
        )
        download = execution.get("installer_download")
        if isinstance(download, dict):
            lines.append(f"- Installer stage: `{download.get('stage')}`")
            for attempt in download.get("attempts", []):
                lines.append(
                    f"- Download attempt {attempt['attempt']}: "
                    f"HTTP `{attempt['http_status']}`, curl `{attempt['curl_returncode']}`"
                )
    return "\n".join(lines) + "\n"
