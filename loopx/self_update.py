from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from typing import IO
from urllib.parse import quote
from urllib.request import Request, urlopen
import zipimport

from .activation_qualification import (
    RUNTIME_ACTIVATION_QUALIFICATION_SCHEMA_VERSION,
    runtime_activation_qualification,
)
from .doctor import collect_doctor
from .install_contract import NO_CLONE_INSTALL_URL
from .python_install_owner import interpreter_invocation
from .runtime_activation import restart_services_for_runtime_activation
from .self_update_download import run_archive_installer


UPDATE_PLAN_SCHEMA_VERSION = "loopx_update_plan_v0"
DEFAULT_UPDATE_REPO = "loopx-project/loopx"
DEFAULT_UPDATE_REF = "stable"
ROLLBACK_PREVIOUS_ALIAS = "previous"
SOURCE_VERSION_CHECK_SCHEMA_VERSION = "loopx_source_version_check_v0"
SOURCE_COMMIT_CHECK_SCHEMA_VERSION = "loopx_source_commit_check_v0"
SOURCE_VERSION_CHECK_TIMEOUT_SECONDS = 3
SOURCE_VERSION_READ_LIMIT_BYTES = 64 * 1024
SOURCE_COMMIT_CHECK_TIMEOUT_SECONDS = 3
SOURCE_COMMIT_READ_LIMIT_BYTES = 256
PERSISTED_PYTHON_FILENAME = ".loopx-python"
_PACKAGE_VERSION_PATTERN = re.compile(r'^__version__\s*=\s*"([^"]+)"$', re.MULTILINE)

Payload = dict[str, object]
PayloadView = Mapping[str, object]


def _as_dict(value: object) -> Payload:
    """Coerce a mapping into a string-keyed dictionary with concrete value types."""
    if not isinstance(value, Mapping):
        return {}
    out: Payload = {}
    for key, item in value.items():
        out[str(key)] = item
    return out


def _nested_dict(mapping: PayloadView, key: str) -> Payload:
    return _as_dict(mapping.get(key))


def _as_dict_list(value: object) -> list[Payload]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_as_dict(item) for item in value if isinstance(item, Mapping)]
    return []


def _nonempty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _parse_json_dict(raw: str) -> Payload:
    try:
        return _as_dict(json.loads(raw))
    except (ValueError, TypeError):
        return {}


def _next_action(kind: str, command: str | None, reason: str, *, mutating: bool = False) -> Payload:
    return {
        "kind": kind,
        "command": command,
        "mutating": mutating,
        "requires_explicit_approval": mutating,
        "reason": reason,
    }


def _run_captured(
    command: Sequence[str], *, timeout: float, env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command capturing UTF-8 output with replacement decoding."""
    return subprocess.run(
        list(command),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        env=dict(env) if env is not None else None,
        timeout=timeout,
    )


def _run_and_record(
    execution: Payload,
    prefix: str,
    command: Sequence[str],
    *,
    timeout: float,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess step and record bounded status/stdout/stderr tails."""
    result = _run_captured(command, timeout=timeout, env=env)
    execution[f"{prefix}_returncode"] = result.returncode
    execution[f"{prefix}_stdout_tail"] = result.stdout[-2000:]
    execution[f"{prefix}_stderr_tail"] = result.stderr[-2000:]
    return result


def _loopx_json_command(
    executable: str | Path, route_arguments: Sequence[str], *subcommand: str,
) -> list[str]:
    return [str(executable), *route_arguments, "--format", "json", *subcommand]


def _resolve_doctor(
    doctor_payload: PayloadView | None, *, registry_path: Path | None, runtime_root: str | None,
) -> Payload:
    if doctor_payload:
        return _as_dict(doctor_payload)
    if registry_path is not None or runtime_root is not None:
        return _as_dict(collect_doctor(registry_path=registry_path, runtime_root_override=runtime_root))
    return _as_dict(collect_doctor())


def _unsupported_windows_result(payload: PayloadView, reason: str) -> Payload:
    return {
        **dict(payload),
        "ok": False,
        "execution": {"status": "unsupported_platform", "reason": reason},
        "recommended_action": reason,
    }


def _installed_readback(result: subprocess.CompletedProcess[str]) -> Payload:
    return _nested_dict(_parse_json_dict(result.stdout), "service_runtime_identity")


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
    installed: PayloadView | None = None,
    build_from_source: bool = False,
) -> Payload:
    installed_map = installed or {}
    selected_repo = (
        repo or os.environ.get("LOOPX_REPO") or _nonempty_str(installed_map.get("manifest_source_repo"))
        or DEFAULT_UPDATE_REPO
    )
    ref_override = ref or os.environ.get("LOOPX_REF")
    inherited_ref = _nonempty_str(installed_map.get("manifest_source_ref"))
    selected_ref = ref_override or inherited_ref or DEFAULT_UPDATE_REF
    archive_url_override = archive_url or os.environ.get("LOOPX_ARCHIVE_URL")
    selected_archive_url = archive_url_override or f"https://codeload.github.com/{selected_repo}/tar.gz/{selected_ref}"
    channel = (
        "github_archive_url_override" if archive_url_override
        else "github_archive_override" if ref_override
        else "github_archive_stable"
    )
    return {
        "channel": channel,
        "repo": selected_repo,
        "ref": selected_ref,
        "ref_source": "override" if ref_override else "installed_manifest" if inherited_ref else "default_stable",
        "archive_url": selected_archive_url,
        "installer_url": NO_CLONE_INSTALL_URL,
        "distribution": "source" if build_from_source or archive_url_override else "runtime",
        "explicit_archive": bool(archive_url_override),
    }


def _command_for_source(source: PayloadView) -> str:
    if os.name == "nt":
        return (
            "Native Windows automatic archive update is not available. "
            "Update a trusted LoopX checkout, then run its "
            "`scripts/install-windows.ps1` with PowerShell 7."
        )
    return _update_action_command(UpdateAction.APPLY, source)


def _installer_env_for_source(
    source: PayloadView,
    *,
    base_env: Mapping[str, str] | None = None,
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
    env["LOOPX_REF"] = str(source.get("resolved_revision") or source.get("ref") or DEFAULT_UPDATE_REF)
    env["LOOPX_UPDATE_CHANNEL"] = str(source.get("ref") or DEFAULT_UPDATE_REF)
    env["LOOPX_BUILD_FROM_SOURCE"] = "1" if source.get("distribution", "source") == "source" else "0"
    env["LOOPX_UPDATE_EVENTS"] = "1"
    if source.get("channel") == "github_archive_url_override" and source.get("archive_url"):
        env["LOOPX_ARCHIVE_URL"] = str(source["archive_url"])
    else:
        env.pop("LOOPX_ARCHIVE_URL", None)
    return env


def _github_repo_ref(source: PayloadView, *, combined_error: bool = False) -> tuple[str, str, str] | str:
    repo, ref = str(source.get("repo") or ""), str(source.get("ref") or "")
    parts = repo.split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        return "GitHub owner/name and ref are required" if combined_error else "GitHub repo must use owner/name syntax"
    if not ref:
        return "GitHub owner/name and ref are required" if combined_error else "GitHub ref is missing"
    return parts[0], parts[1], ref


def _github_probe(
    source_url: str, *, accept: str, timeout: int, read_limit: int, encoding: str, label: str,
) -> tuple[str | None, str | None]:
    """Fetch bounded text from a fixed GitHub endpoint, degrading offline."""
    request = Request(source_url, headers={"Accept": accept, "User-Agent": "LoopX-update-check"})
    try:
        with urlopen(  # noqa: S310 - repo/ref are validated and the host is fixed.
            request, timeout=timeout,
        ) as response:
            raw: bytes = response.read(read_limit)
            return raw.decode(encoding), None
    except Exception as exc:  # Remote comparison is advisory and must degrade offline.
        return None, f"remote {label} check unavailable ({type(exc).__name__})"


def _source_version_check(source: PayloadView) -> Payload:
    base: Payload = {
        "schema_version": SOURCE_VERSION_CHECK_SCHEMA_VERSION,
        "attempted": False, "status": "skipped", "version": None, "version_tag": None,
        "matches_current": None, "source_url": None, "reason": None,
    }
    if source.get("channel") == "github_archive_url_override":
        return {**base, "reason": "custom archive URL is not assumed to match the configured GitHub repo/ref"}
    parsed = _github_repo_ref(source)
    if isinstance(parsed, str):
        return {**base, "reason": parsed}
    owner, name, ref = parsed
    source_url = (
        "https://raw.githubusercontent.com/"
        f"{quote(owner, safe='')}/{quote(name, safe='')}/{quote(ref, safe='')}/loopx/__init__.py"
    )
    body, error = _github_probe(
        source_url,
        accept="text/plain",
        timeout=SOURCE_VERSION_CHECK_TIMEOUT_SECONDS,
        read_limit=SOURCE_VERSION_READ_LIMIT_BYTES,
        encoding="utf-8",
        label="version",
    )
    if error is not None or body is None:
        return {**base, "attempted": True, "status": "unavailable", "source_url": source_url, "reason": error}
    match = _PACKAGE_VERSION_PATTERN.search(body)
    if not match:
        return {
            **base, "attempted": True, "status": "unavailable",
            "source_url": source_url, "reason": "remote package version was not found",
        }
    version = match.group(1)
    return {
        **base, "attempted": True, "status": "available",
        "version": version, "version_tag": f"v{version}", "source_url": source_url,
    }


def _source_commit_check(
    source: PayloadView, *, installed_commit: object, allow_remote: bool,
) -> Payload:
    """Resolve the selected archive ref, not merely its package version.

    A moving branch can change while ``__version__`` and the local install-age
    window stay the same. The GitHub SHA media type keeps this read bounded;
    an offline/invalid response remains unknown, never evidence of equality.
    """
    base: Payload = {
        "schema_version": SOURCE_COMMIT_CHECK_SCHEMA_VERSION,
        "attempted": False, "status": "skipped", "installed_commit": installed_commit,
        "target_commit": None, "matches_current": None, "source_url": None, "reason": None,
    }
    if source.get("channel") == "github_archive_url_override":
        return {**base, "reason": "custom archive URL has no trusted GitHub ref identity"}
    parsed = _github_repo_ref(source, combined_error=True)
    if isinstance(parsed, str):
        return {**base, "reason": parsed}
    owner, name, ref = parsed
    if re.fullmatch(r"[0-9a-fA-F]{40}", ref):
        target = ref.lower()
        return {
            **base, "status": "available", "target_commit": target,
            "matches_current": installed_commit.lower() == target if isinstance(installed_commit, str) else None,
            "reason": "immutable ref identifies its own commit",
        }
    if not allow_remote:
        return {**base, "status": "not_requested", "reason": "source comparison omitted for an injected doctor snapshot"}
    source_url = (
        "https://api.github.com/repos/"
        f"{quote(owner, safe='')}/{quote(name, safe='')}/commits/{quote(ref, safe='')}"
    )
    body, error = _github_probe(
        source_url,
        accept="application/vnd.github.sha",
        timeout=SOURCE_COMMIT_CHECK_TIMEOUT_SECONDS,
        read_limit=SOURCE_COMMIT_READ_LIMIT_BYTES,
        encoding="ascii",
        label="commit",
    )
    if error is not None or body is None:
        return {**base, "attempted": True, "status": "unavailable", "source_url": source_url, "reason": error}
    body = body.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}", body):
        return {
            **base, "attempted": True, "status": "unavailable",
            "source_url": source_url, "reason": "remote commit response was not a full SHA",
        }
    target = body.lower()
    return {
        **base, "attempted": True, "status": "available", "target_commit": target,
        "matches_current": installed_commit.lower() == target if isinstance(installed_commit, str) else None,
        "source_url": source_url,
    }


def _release_root_from_doctor(doctor_payload: PayloadView) -> str | None:
    release_root = _nonempty_str(_nested_dict(doctor_payload, "package").get("release_root"))
    if release_root:
        return release_root
    realpath = _nonempty_str(_nested_dict(doctor_payload, "path").get("loopx_realpath"))
    if realpath:
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
        (item for item in releases_dir.iterdir() if item.is_dir() and _release_script(item).exists()),
        key=lambda item: item.name,
        reverse=True,
    )


def _rollback_release_command(release_id: str) -> str:
    return f"loopx update --rollback {shlex.quote(release_id)}\nloopx doctor"


def _rollback_plan(doctor_payload: PayloadView) -> Payload:
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


def _install_lifecycle(doctor_payload: PayloadView) -> Payload:
    package = _nested_dict(doctor_payload, "package")
    freshness = _nested_dict(doctor_payload, "install_freshness")
    install_kind = _nonempty_str(package.get("install_kind"))
    if not install_kind:
        if freshness.get("install_kind") == "python_distribution":
            install_kind = "python_distribution"
        elif _release_root_from_doctor(doctor_payload):
            install_kind = "release_snapshot"
        else:
            install_kind = "live_checkout"

    installer: object = None
    installer_environment: object = None
    if install_kind == "release_snapshot":
        owner = "loopx_release_snapshot"
        apply_supported = os.name != "nt"
        execution_driver: str | None = "archive_snapshot" if apply_supported else None
        reason = (
            "LoopX owns this archive release snapshot and can atomically replace it"
            if apply_supported
            else "native Windows snapshot updates remain owned by install-windows.ps1"
        )
        owner_command: object = None
    elif install_kind == "python_distribution":
        owner = "python_package_manager"
        installer = _nonempty_str(freshness.get("python_distribution_installer"))
        installer_environment = freshness.get("python_distribution_installer_environment")
        if not installer:
            distribution = _nested_dict(package, "python_distribution")
            installer = distribution.get("installer")
            installer_environment = distribution.get("installer_environment")
        apply_supported = installer in {"pip", "pipx"}
        execution_driver = "python_pipx" if installer == "pipx" else "python_pip" if installer == "pip" else None
        reason = (
            "LoopX can ask the owning Python interpreter's pip to upgrade the same environment"
            if installer == "pip"
            else "LoopX can ask pipx to upgrade its recorded LoopX environment"
            if installer == "pipx"
            else f"the active Python environment is owned by {installer or 'an unknown installer'}; use that package manager directly"
        )
        owner_command = freshness.get("upgrade_command")
    else:
        owner = "source_checkout"
        apply_supported = False
        execution_driver = None
        reason = "the active checkout owns contributor updates; LoopX must not replace it with an archive snapshot"
        owner_command = freshness.get("contributor_upgrade_command")

    return {
        "install_kind": install_kind,
        "owner": owner,
        "loopx_apply_supported": apply_supported,
        "execution_driver": execution_driver,
        "package_manager": installer if install_kind == "python_distribution" else None,
        "package_manager_environment": installer_environment if install_kind == "python_distribution" else None,
        "reason": reason,
        "owner_upgrade_command": owner_command,
    }


def _update_action_command(action: UpdateAction, source: PayloadView) -> str:
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
    if source.get("distribution") == "source" and not source.get("explicit_archive"):
        parts.append("--source")
    if source.get("allow_downgrade"):
        parts.append("--allow-downgrade")
    return shlex.join(parts)


def _python_distribution_rollback_plan(current_version: object, lifecycle: PayloadView) -> Payload:
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
        f"{interpreter_invocation(sys.executable)} -m pip install "
        f"{shlex.quote(f'loopx=={current_version}')}\n"
        "loopx workflow-skills --install\nloopx slash-commands --install\nloopx doctor"
    )
    return {
        "available": True,
        "reason": "pip can request the previously installed LoopX version",
        "rollback_version": current_version,
        "rollback_command": command,
    }


def _windows_console_update_refusal(payload: PayloadView) -> Payload | None:
    """A distlib launcher holds its own executable open until Python exits.

    Use its actual zip loader, not argv[0]: distlib removes the .exe suffix.
    The module entry has a source loader and can safely replace the launcher.
    """
    loader = getattr(sys.modules.get("__main__"), "__loader__", None)
    lifecycle = _nested_dict(payload, "install_lifecycle")
    if (
        sys.platform != "win32"
        or not isinstance(loader, zipimport.zipimporter)
        or lifecycle.get("execution_driver") not in {"python_pip", "python_pipx"}
    ):
        return None
    launcher = Path(loader.archive)
    if launcher.name.casefold() != "loopx.exe":
        # pipx suffixes the exposed app name, but its symlink/hardlink can still
        # hold this installation's actual console executable open.
        owned_launcher = Path(sys.executable).with_name("loopx.exe")
        try:
            if not launcher.samefile(owned_launcher):
                return None
        except FileNotFoundError:
            return None
    retry = interpreter_invocation(sys.executable, ["-m", "loopx.entrypoint", *sys.argv[1:]])
    reason = (
        "The running Windows console launcher cannot be replaced by pip. "
        "No update was started; retry through the owning Python interpreter."
    )
    return {
        **payload,
        "ok": False,
        "changes_applied": False,
        "execution": None,
        "error": reason,
        "recommended_action": f"{reason}\n{retry}",
        "install_lifecycle": {**lifecycle, "loopx_apply_supported": False, "reason": reason},
        "plan": {
            **_nested_dict(payload, "plan"),
            "apply_supported": False, "blocked_reason": reason, "mutates_release_install": False,
        },
        "commands": {**_nested_dict(payload, "commands"), "apply": retry},
        "next_action": _next_action("use_installation_owner", retry, reason, mutating=True),
    }


def build_rollback_plan(
    *,
    release_id: str,
    doctor_payload: PayloadView | None = None,
    home: Path | None = None,
    registry_path: Path | None = None,
    runtime_root: str | None = None,
) -> Payload:
    doctor = _resolve_doctor(doctor_payload, registry_path=registry_path, runtime_root=runtime_root)
    current_release_root = _release_root_from_doctor(doctor)
    releases_dir = _user_releases_dir(home)
    releases = _list_release_roots(releases_dir)
    current_root_path = Path(current_release_root).resolve() if current_release_root else None
    requested_release_id = release_id.strip()
    selected: Path | None = None
    reason: str | None = None

    if not requested_release_id:
        reason = "rollback release id is required"
    elif requested_release_id == ROLLBACK_PREVIOUS_ALIAS:
        selected = next(
            (candidate for candidate in releases if not current_root_path or candidate.resolve() != current_root_path),
            None,
        )
        if selected is None:
            reason = "no previous release snapshot found"
    elif "/" in requested_release_id or requested_release_id in {".", ".."}:
        reason = "rollback release id must be a release directory name"
    else:
        candidate = releases_dir / requested_release_id
        if _release_script(candidate).exists():
            selected = candidate
        else:
            reason = f"release snapshot not found: {requested_release_id}"

    available = selected is not None
    return {
        "ok": available,
        "schema_version": UPDATE_PLAN_SCHEMA_VERSION,
        "mode": "rollback",
        "dry_run": False,
        "execute_requested": True,
        "requested_release_id": requested_release_id,
        "current": {"current_release_root": current_release_root},
        "plan": {
            "action": "rollback",
            "available": available,
            "reason": reason,
            "releases_dir": str(releases_dir),
            "release_count": len(releases),
            "selected_release_id": selected.name if selected else None,
            "selected_release_root": str(selected) if selected else None,
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


def _recommend_update_action(
    *,
    execute: bool,
    check_only: bool,
    apply_supported: bool,
    lifecycle: PayloadView,
    owner_upgrade_command: object,
    runtime_activation: PayloadView,
    source_version_check: PayloadView,
    source_commit_check: PayloadView,
    current_version: object,
    requires_upgrade: object,
) -> str:
    """Select operator guidance for the update plan or check result."""
    owner = str(lifecycle["owner"])
    owner_hint = (
        f"use the active {owner} upgrade path:\n{owner_upgrade_command}"
        if owner_upgrade_command
        else f"use the active {owner} upgrade path"
    )
    if execute:
        return "review execution result and post-update doctor output" if apply_supported else owner_hint
    if owner != "loopx_release_snapshot":
        if not check_only and apply_supported:
            return "run `loopx update apply` to use the active Python package manager, then review host-material readback"
        return owner_hint
    decision = runtime_activation.get("decision")
    if check_only:
        if decision == "release_or_install_successor_required":
            return (
                "installed runtime does not contain the trusted target source; "
                "project a release/install successor, then run `loopx update plan`"
            )
        if source_version_check.get("matches_current") is False:
            return (
                f"source version v{source_version_check.get('version')} differs from installed version "
                f"v{current_version}; run `loopx update plan`, then `loopx update apply`"
            )
        if source_version_check.get("status") == "unavailable":
            return (
                "local install health is known, but the selected source version could not be checked; "
                "retry online or run `loopx update apply` to refresh"
            )
        if source_version_check.get("status") == "skipped":
            return (
                "local install health is known, but source version comparison was skipped; "
                "run `loopx update plan` to review the source"
            )
        if decision == "activation_qualification_required":
            return (
                "installed runtime activation is not proven; refresh trusted source lineage "
                "before claiming the merged behavior is active"
            )
    if source_commit_check.get("matches_current") is False:
        return (
            "selected source commit differs from the installed release; compare its "
            "lineage or run `loopx update apply` to install the selected source"
        )
    if decision == "activation_qualification_required":
        return (
            "selected source activation is unproven; do not treat matching package "
            "version or install age as current. Retry online or select an immutable "
            "commit with `--ref` before deciding whether to update"
        )
    if check_only and source_version_check.get("matches_current") is True and requires_upgrade is False:
        return "installed version and trusted source lineage match; no update needed"
    if check_only:
        return "run `loopx update plan` to review the source and rollback plan"
    if requires_upgrade is False:
        return "no update needed; use `loopx update plan` or `loopx update apply` only to force a refresh"
    return "run `loopx update apply` if you accept the source and rollback plan"


def build_update_plan(
    *,
    repo: str | None = None,
    ref: str | None = None,
    archive_url: str | None = None,
    action: UpdateAction | str | None = None,
    check_only: bool = False,
    execute: bool = False,
    doctor_payload: PayloadView | None = None,
    registry_path: Path | None = None,
    runtime_root: str | None = None,
    build_from_source: bool = False,
    allow_downgrade: bool = False,
    resolve_remote: bool | None = None,
) -> Payload:
    requested_action = resolve_update_action(action, check=check_only, execute=execute)
    check_only = requested_action is UpdateAction.CHECK
    execute = requested_action is UpdateAction.APPLY
    doctor = _resolve_doctor(doctor_payload, registry_path=registry_path, runtime_root=runtime_root)
    install_freshness = _nested_dict(doctor, "install_freshness")
    release_manifest = _nested_dict(doctor, "release_manifest")
    lifecycle = _install_lifecycle(doctor)
    is_snapshot = lifecycle["owner"] == "loopx_release_snapshot"
    source = _source_config(
        repo=repo,
        ref=ref,
        archive_url=archive_url,
        installed=install_freshness if is_snapshot else None,
        build_from_source=build_from_source,
    )
    source["allow_downgrade"] = allow_downgrade
    remote = doctor_payload is None if resolve_remote is None else resolve_remote
    path = _nested_dict(doctor, "path")
    requires_upgrade = install_freshness.get("requires_upgrade")
    current_version = install_freshness.get("current_version")
    source_version_check: Payload = (
        _source_version_check(source)
        if check_only and is_snapshot and source["distribution"] == "source"
        else {
            "schema_version": SOURCE_VERSION_CHECK_SCHEMA_VERSION,
            "attempted": False,
            "status": "delegated" if check_only and not is_snapshot else "not_requested",
            "version": None,
            "version_tag": None,
            "matches_current": None,
            "source_url": None,
            "reason": (
                f"update availability is owned by {lifecycle['owner']}"
                if check_only and not is_snapshot
                else "remote source comparison runs only for `loopx update check`"
            ),
        }
    )
    if source_version_check.get("status") == "available":
        source_version_check["matches_current"] = (
            source_version_check.get("version") == current_version
            if isinstance(current_version, str) and current_version
            else None
        )
    installed_commit = install_freshness.get("manifest_source_git_commit")
    source_commit_check: Payload = (
        _source_commit_check(source, installed_commit=installed_commit, allow_remote=remote)
        if is_snapshot and source["distribution"] == "source"
        else {
            "schema_version": SOURCE_COMMIT_CHECK_SCHEMA_VERSION,
            "attempted": False,
            "status": "not_requested",
            "installed_commit": installed_commit,
            "target_commit": None,
            "matches_current": None,
            "source_url": None,
            "reason": "source commit comparison applies to snapshot check/plan",
        }
    )
    raw_target_commit = source_commit_check.get("target_commit")
    runtime_activation: Payload = (
        _as_dict(
            runtime_activation_qualification(
                install_freshness=install_freshness,
                source=source,
                resolved_source_commit=(
                    raw_target_commit
                    if source_commit_check.get("status") == "available" and isinstance(raw_target_commit, str)
                    else None
                ),
                selected_source_unresolved=(
                    not execute and source_commit_check.get("status") in {"unavailable", "not_requested"}
                ),
            )
        )
        if is_snapshot
        else {
            "schema_version": RUNTIME_ACTIVATION_QUALIFICATION_SCHEMA_VERSION,
            "decision": "not_applicable",
            "runtime_active": None,
            "successor": {"required": False, "kind": None},
            "reason": f"runtime activation is governed by {lifecycle['owner']}, not an archive snapshot",
        }
    )
    apply_supported = bool(lifecycle["loopx_apply_supported"])
    owner_upgrade_command = _nonempty_str(lifecycle.get("owner_upgrade_command"))
    recommended_action = _recommend_update_action(
        execute=execute, check_only=check_only, apply_supported=apply_supported,
        lifecycle=lifecycle, owner_upgrade_command=owner_upgrade_command,
        runtime_activation=runtime_activation, source_version_check=source_version_check,
        source_commit_check=source_commit_check, current_version=current_version,
        requires_upgrade=requires_upgrade,
    )
    apply_command = _update_action_command(UpdateAction.APPLY, source) if apply_supported else None
    commands: Payload = {
        "check": _update_action_command(UpdateAction.CHECK, source),
        "plan": _update_action_command(UpdateAction.PLAN, source),
        "apply": apply_command,
        "owner_upgrade": owner_upgrade_command,
    }
    if execute and apply_supported:
        next_action = _next_action("review_execution", "loopx doctor", recommended_action)
    elif apply_command:
        next_action = _next_action("apply_update", apply_command, recommended_action, mutating=True)
    else:
        next_action = _next_action(
            "use_installation_owner", owner_upgrade_command, recommended_action, mutating=bool(owner_upgrade_command),
        )
    backup = (
        _python_distribution_rollback_plan(current_version, lifecycle)
        if lifecycle["execution_driver"] in {"python_pip", "python_pipx"}
        else _rollback_plan(doctor)
    )
    payload: Payload = {
        "ok": not execute or apply_supported,
        "schema_version": UPDATE_PLAN_SCHEMA_VERSION,
        "mode": "update",
        "requested_action": requested_action.value,
        "check_only": check_only,
        "dry_run": not execute,
        "execute_requested": execute,
        "changes_applied": False,
        "install_lifecycle": lifecycle,
        "commands": commands,
        "next_action": next_action,
        "source": source,
        "source_version_check": source_version_check,
        "source_commit_check": source_commit_check,
        "runtime_activation_qualification": runtime_activation,
        "current": {
            "loopx_command": path.get("loopx"),
            "loopx_realpath": path.get("loopx_realpath"),
            "install_freshness_status": install_freshness.get("status"),
            "release_manifest_available": release_manifest.get("available"),
            "release_manifest_path": release_manifest.get("path"),
            "release_manifest": _nested_dict(release_manifest, "manifest"),
            **{
                key: install_freshness.get(key)
                for key in (
                    "current_version", "current_version_tag", "manifest_package_version",
                    "manifest_package_version_tag", "manifest_package_version_matches_runtime",
                    "release_id", "requires_upgrade", "reason",
                )
            },
        },
        "plan": {
            "action": "check_only" if check_only else "execute_installer" if execute else "dry_run",
            "apply_supported": apply_supported,
            "blocked_reason": lifecycle["reason"] if execute and not apply_supported else None,
            "install_command": _command_for_source(source) if is_snapshot else owner_upgrade_command,
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
    from .cli_commands.update_source import prepare_target

    payload = _as_dict(
        prepare_target(payload, freshness=install_freshness, resolve_remote=remote, allow_downgrade=allow_downgrade)
    )
    if lifecycle["owner"] == "python_package_manager" and (ref or build_from_source or archive_url):
        reason = "The Python package manager owns this installation; choose main/source in a separate snapshot installation"
        payload.update(ok=not execute, error=reason, recommended_action=reason)
        payload["plan"] = {
            **_nested_dict(payload, "plan"),
            "apply_supported": False, "blocked_reason": reason, "mutates_release_install": False,
        }
    return (_windows_console_update_refusal(payload) if execute else None) or payload


def _retain_pending_service_activation(updated: Payload) -> bool:
    """Keep installation success distinct from switching its live consumers."""
    if not _nested_dict(updated, "execution").get("activation_pending"):
        return False
    updated["ok"] = False
    reason = (
        "the runtime is installed, but managed-service activation is incomplete. "
        "Let original Chat work finish, or inspect the reported service-state or "
        "restart failure, then retry the same update source"
    )
    updated["recommended_action"] = reason
    updated["next_action"] = _next_action(
        "retry_runtime_activation",
        _nonempty_str(_nested_dict(updated, "commands").get("apply"))
        or _update_action_command(UpdateAction.APPLY, _nested_dict(updated, "source")),
        reason,
        mutating=True,
    )
    return True


def _apply_activation_outcome(
    updated: Payload,
    execution: Payload,
    *,
    runtime_ready: bool,
    installed_bin: Path | None,
    success_message: str,
    blocked_extensions_message: str,
    failure_next_action: Payload,
) -> Payload:
    """Restart managed services if ready and set post-update next_action guidance."""
    activation_kwargs: dict[str, Path] = {"installed_bin": installed_bin} if installed_bin is not None else {}
    execution.update(
        restart_services_for_runtime_activation(
            changes_applied=bool(updated["changes_applied"]),
            runtime_ready=runtime_ready,
            **activation_kwargs,
        )
    )
    if _retain_pending_service_activation(updated):
        return updated
    if updated["ok"]:
        updated["recommended_action"] = success_message
        updated["next_action"] = _next_action("use_updated_runtime", "loopx doctor", success_message)
    elif runtime_ready:
        updated["recommended_action"] = blocked_extensions_message
        updated["next_action"] = _next_action(
            "repair_blocked_extensions", "loopx extension doctor --all-enabled --execute", blocked_extensions_message,
        )
    else:
        updated["recommended_action"] = str(failure_next_action["reason"])
        updated["next_action"] = failure_next_action
    return updated


def _execute_python_distribution_update(
    payload: PayloadView,
    *,
    timeout_seconds: int,
    route_arguments: list[str],
    progress: Callable[[str], None] = lambda _stage: None,
) -> Payload:
    refused = _windows_console_update_refusal(payload)
    if refused is not None:
        return refused
    lifecycle = _nested_dict(payload, "install_lifecycle")
    driver = lifecycle.get("execution_driver")
    package_manager_environment = lifecycle.get("package_manager_environment")
    install_command = (
        ["pipx", "upgrade", str(package_manager_environment or "loopx")]
        if driver == "python_pipx"
        else [sys.executable, "-m", "pip", "install", "--upgrade", "loopx"]
    )
    cli_prefix = [sys.executable, "-m", "loopx.cli", *route_arguments]
    commands = {
        "install": install_command,
        "authority_upgrade": [*cli_prefix, "--format", "json", "authority-archive", "upgrade", "--all-known", "--execute"],
        "workflow_skills": [*cli_prefix, "workflow-skills", "--install", "--format", "json"],
        "slash_commands": [*cli_prefix, "slash-commands", "--install", "--format", "json"],
        "doctor": [*cli_prefix, "doctor", "--format", "json"],
        "extension_doctor": [*cli_prefix, "extension", "doctor", "--all-enabled", "--execute", "--format", "json"],
    }
    execution: Payload = {"driver": driver, "python_executable": sys.executable}
    results: dict[str, subprocess.CompletedProcess[str]] = {}
    progress("Updating CLI through its Python package manager")
    results["install"] = _run_and_record(execution, "install", commands["install"], timeout=timeout_seconds)
    validation_steps = ("authority_upgrade", "workflow_skills", "slash_commands", "doctor")
    if results["install"].returncode == 0:
        for step in validation_steps:
            progress(f"Validating updated runtime: {step}")
            results[step] = _run_and_record(execution, step, commands[step], timeout=timeout_seconds)
            if results[step].returncode != 0:
                break
        if all(step in results and results[step].returncode == 0 for step in validation_steps):
            results["extension_doctor"] = _run_and_record(
                execution, "extension_doctor", commands["extension_doctor"], timeout=timeout_seconds,
            )

    for step in (*validation_steps, "extension_doctor"):
        result = results.get(step)
        if result is None:
            execution[f"{step}_status"] = "skipped_prior_step_failed"
        elif step == "doctor" and result.returncode == 0:
            execution["installed_runtime_identity"] = _installed_readback(result)

    runtime_ready = all(step in results and results[step].returncode == 0 for step in ("install", *validation_steps))
    extension_ready = "extension_doctor" in results and results["extension_doctor"].returncode == 0
    updated: Payload = dict(payload)
    updated["execution"] = execution
    updated["ok"] = runtime_ready and extension_ready
    updated["changes_applied"] = results["install"].returncode == 0
    return _apply_activation_outcome(
        updated,
        execution,
        runtime_ready=runtime_ready,
        installed_bin=None,
        success_message="PyPI update and host-material readback passed; use the new LoopX process",
        blocked_extensions_message=(
            "the updated runtime is installed and serving; repair the blocked "
            "enabled extension providers, then rerun `loopx extension doctor --all-enabled --execute`"
        ),
        failure_next_action=_next_action(
            "review_update_failure",
            "loopx --format json authority-archive upgrade --all-known",
            "inspect the failed update step and retry the verified authority upgrade; "
            "do not roll back the package without checking current data-format compatibility",
        ),
    )


def _runtime_route_arguments(registry_path: Path | None, runtime_root: str | None) -> list[str]:
    arguments: list[str] = []
    if registry_path is not None:
        arguments += ["--registry", str(registry_path.resolve())]
    if runtime_root is not None:
        arguments += ["--runtime-root", runtime_root]
    return arguments


def execute_update_plan(
    payload: PayloadView,
    *,
    timeout_seconds: int = 600,
    registry_path: Path | None = None,
    runtime_root: str | None = None,
    progress: Callable[[str], None] = lambda _stage: None,
) -> Payload:
    plan = _nested_dict(payload, "plan")
    if not plan.get("apply_supported", True):
        return {**dict(payload), "ok": False, "changes_applied": False}
    route_arguments = _runtime_route_arguments(registry_path, runtime_root)
    lifecycle = _nested_dict(payload, "install_lifecycle")
    driver = lifecycle.get("execution_driver")
    if driver is None and isinstance(payload.get("source"), Mapping):
        # Compatibility for callers that persisted the v0 archive plan before
        # install_lifecycle became explicit.
        driver = "archive_snapshot"
    if driver in {"python_pip", "python_pipx"}:
        return _execute_python_distribution_update(
            payload, timeout_seconds=timeout_seconds, route_arguments=route_arguments, progress=progress,
        )
    if driver != "archive_snapshot":
        return {
            **dict(payload),
            "ok": False,
            "changes_applied": False,
            "execution": {
                "status": "unsupported_install_owner",
                "owner": lifecycle.get("owner"),
                "reason": lifecycle.get("reason"),
            },
        }
    if os.name == "nt":
        return _unsupported_windows_result(
            payload,
            "native Windows automatic archive update is not available; "
            "rerun scripts/install-windows.ps1 from an updated trusted checkout",
        )
    source = _nested_dict(payload, "source")
    installer_url = str(source.get("installer_url") or NO_CLONE_INSTALL_URL)
    backup = _nested_dict(plan, "backup")
    raw_release_root = backup.get("current_release_root")
    env = _installer_env_for_source(
        source,
        current_release_root=raw_release_root if isinstance(raw_release_root, str) else None,
    )
    if registry_path is not None:
        env["LOOPX_REGISTRY"] = str(registry_path.resolve())
    if runtime_root is not None:
        env["LOOPX_RUNTIME_ROOT"] = runtime_root
    if source.get("distribution") == "runtime":
        from .cli_commands.update_artifact import install_artifact

        artifact = _nested_dict(source, "artifact")
        if not artifact:
            return {
                **dict(payload),
                "ok": False,
                "changes_applied": False,
                "error": "No resolved runtime artifact; rebuild the update plan online before applying",
            }
        install_result, download_observation = install_artifact(
            artifact, env=env, timeout_seconds=timeout_seconds, progress=progress,
        )
    else:
        install_result, download_observation = run_archive_installer(
            installer_url, env=env, timeout_seconds=timeout_seconds, progress=progress,
        )
    # Match install-local.sh's LOOPX_BIN_DIR override, including its empty-value
    # default. Read back the installation that this update actually replaced.
    loopx_bin = Path(env.get("LOOPX_BIN_DIR") or _user_loopx_bin().parent) / "loopx"
    execution: Payload = {
        "installer_download": download_observation,
        "install_returncode": install_result.returncode,
        "install_stdout_tail": install_result.stdout[-2000:],
        "install_stderr_tail": install_result.stderr[-2000:],
    }
    progress("Validating installed CLI")
    doctor_result = _run_and_record(
        execution, "doctor", _loopx_json_command(loopx_bin, route_arguments, "doctor"),
        timeout=timeout_seconds, env=env,
    )
    execution["installed_runtime_identity"] = _installed_readback(doctor_result) if doctor_result.returncode == 0 else {}
    installed_freshness = _nested_dict(_parse_json_dict(doctor_result.stdout), "install_freshness")
    # A host-material step can fail after a healthy candidate became active.
    # Do not report "nothing changed" merely from the installer's exit code.
    activation_observed = bool(
        doctor_result.returncode == 0
        and source.get("resolved_revision")
        and installed_freshness.get("manifest_source_git_commit") == source["resolved_revision"]
        and installed_freshness.get("release_id")
        and installed_freshness["release_id"] != _nested_dict(payload, "current").get("release_id")
    )
    execution["activation_observed"] = activation_observed
    extension_doctor_result: subprocess.CompletedProcess[str] | None = None
    runtime_ready = (install_result.returncode == 0 or activation_observed) and doctor_result.returncode == 0
    if runtime_ready:
        progress("Validating enabled extensions")
        extension_doctor_result = _run_and_record(
            execution,
            "extension_doctor",
            _loopx_json_command(loopx_bin, route_arguments, "extension", "doctor", "--all-enabled", "--execute"),
            timeout=timeout_seconds,
            env=env,
        )
    else:
        execution["extension_doctor_status"] = "skipped_release_update_failed"
    updated: Payload = dict(payload)
    updated["execution"] = execution
    updated["ok"] = (
        install_result.returncode == 0
        and runtime_ready
        and extension_doctor_result is not None
        and extension_doctor_result.returncode == 0
    )
    updated["changes_applied"] = install_result.returncode == 0 or activation_observed
    rollback_command = _nonempty_str(backup.get("rollback_command"))
    return _apply_activation_outcome(
        updated,
        execution,
        runtime_ready=runtime_ready,
        installed_bin=loopx_bin,
        success_message="archive update and readback passed; use the new LoopX process",
        blocked_extensions_message=(
            "the updated runtime is installed; repair the blocked "
            "enabled extension providers, then rerun `loopx extension doctor --all-enabled --execute`"
        ),
        failure_next_action=_next_action(
            "review_or_rollback",
            rollback_command,
            "inspect update execution tails and restore from rollback plan if needed",
            mutating=bool(rollback_command),
        ),
    )


def _verify_rollback_todos(
    target_script: Path,
    registry_path: Path,
    runtime_root: str | None,
    *,
    timeout_seconds: int,
) -> None:
    """Verify registered local Todo heads with the executable to be activated."""
    from .control_plane.coordination.local_authority import (
        local_authority_is_promoted,
        read_canonical_todos_if_promoted,
    )
    from .control_plane.goals.activation_service import authority_upgrade_registries
    from .control_plane.projects.registry_codec import read_activation_upgrade_registry
    from .paths import GLOBAL_REGISTRY_FILENAME, resolve_runtime_root
    from .registry import registry_goals

    selected = registry_path.expanduser().resolve()
    routes: list[tuple[Path, str | None]] = [
        (registry, None) for registry in authority_upgrade_registries(selected, runtime_root, all_known=True)
    ]
    if runtime_root:
        routes.append((selected, runtime_root))
    checked: set[tuple[Path, str]] = set()
    deadline = time.monotonic() + timeout_seconds
    for registry, override in routes:
        document = read_activation_upgrade_registry(registry)
        root = (
            registry.parent
            if registry.name == GLOBAL_REGISTRY_FILENAME and override is None
            else resolve_runtime_root(document, override, registry_path=registry)
        ).resolve()
        for goal in registry_goals(document):
            goal_id = str(goal["id"])
            key = (root, goal_id)
            if key in checked or not local_authority_is_promoted(runtime_root=root, goal_id=goal_id):
                continue
            checked.add(key)
            live = read_canonical_todos_if_promoted(runtime_root=root, goal_id=goal_id)
            if live["source_authority"] not in ("file_v0", "sqlite_v0"):
                continue
            refusal = (
                f"Rollback target could not verify current Todo state for Goal {goal_id!r}. "
                "Keep the current runtime and verify the target version with a matching state backup."
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError(refusal)
            result = _run_captured(
                _loopx_json_command(
                    target_script, _runtime_route_arguments(registry, str(root)),
                    "todo", "list", "--goal-id", goal_id, "--limit", "1",
                ),
                timeout=remaining,
            )
            if result.returncode != 0:
                raise RuntimeError(refusal)
            projected = _parse_json_dict(result.stdout)
            authority = _nested_dict(projected, "authority_read")
            if (
                not authority
                or projected.get("ok") is not True
                or projected.get("goal_id") != goal_id
                or authority.get("decision_read_from_provider") is not True
                or authority.get("legacy_fallback_used") is not False
                or authority.get("source_authority") != live["source_authority"]
                or authority.get("provider_revision") != live["provider_revision"]
            ):
                raise RuntimeError(refusal)


def execute_rollback_plan(
    payload: PayloadView,
    *,
    timeout_seconds: int = 600,
    home: Path | None = None,
    registry_path: Path | None = None,
    runtime_root: str | None = None,
) -> Payload:
    if os.name == "nt":
        return _unsupported_windows_result(
            payload,
            "native Windows automatic rollback is not available; "
            "rerun scripts/install-windows.ps1 for the selected trusted checkout",
        )
    plan = _nested_dict(payload, "plan")
    selected_release_root = _nonempty_str(plan.get("selected_release_root"))
    if not payload.get("ok") or not selected_release_root:
        return dict(payload)
    release_root = Path(selected_release_root)
    target_script = _release_script(release_root)
    route_arguments = _runtime_route_arguments(registry_path, runtime_root)
    loopx_bin = _user_loopx_bin(home)
    previous_link_target = os.readlink(loopx_bin) if loopx_bin.is_symlink() else None
    execution: Payload = {
        "target_script": str(target_script),
        "loopx_command": str(loopx_bin),
        "previous_link_target": previous_link_target,
        "restored_previous_on_failure": False,
    }
    updated: Payload = dict(payload)
    switched = False
    promotion_guard: IO[str] | None = None
    temp_link: Path | None = None
    restore_link: Path | None = None
    promoted_link_stat: os.stat_result | None = None
    try:
        compatible = _run_and_record(
            execution,
            "compatibility",
            _loopx_json_command(
                target_script, route_arguments, "authority-archive", "upgrade", "--all-known", "--require-current",
            ),
            timeout=timeout_seconds,
        )
        if compatible.returncode != 0:
            raise RuntimeError(
                "Rollback target authority check failed. Keep the current runtime "
                "and inspect the compatibility readback before retrying."
            )
        from .file_lock import _open_lock_descriptor, _try_acquire_kernel_lock

        # Share the archive installer's exact guard through checks and recovery.
        guard_path = release_root.parent / ".install-guard"
        guard_path.parent.mkdir(parents=True, exist_ok=True)
        promotion_guard = os.fdopen(
            _open_lock_descriptor(guard_path, flags=os.O_CREAT | os.O_RDWR), "r+", encoding="utf-8",
        )
        deadline = time.monotonic() + timeout_seconds
        while not _try_acquire_kernel_lock(promotion_guard):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("Timed out waiting for another local install to finish before rollback.")
            time.sleep(min(0.1, remaining))
        if registry_path is not None:
            _verify_rollback_todos(target_script, registry_path, runtime_root, timeout_seconds=timeout_seconds)
        observed_link = os.readlink(loopx_bin) if loopx_bin.is_symlink() else None
        if observed_link != previous_link_target:
            raise RuntimeError("LoopX launcher changed during rollback checks. Read it back before retrying.")
        loopx_bin.parent.mkdir(parents=True, exist_ok=True)
        temp_link = loopx_bin.with_name(f".{loopx_bin.name}.rollback.{os.getpid()}")
        if temp_link.exists() or temp_link.is_symlink():
            temp_link.unlink()
        temp_link.symlink_to(target_script)
        promoted_link_stat = temp_link.lstat()
        os.replace(temp_link, loopx_bin)
        switched = True
        doctor_result = _run_and_record(
            execution, "doctor", _loopx_json_command(loopx_bin, route_arguments, "doctor"), timeout=timeout_seconds,
        )
        if doctor_result.returncode == 0:
            extension_doctor_result = _run_and_record(
                execution,
                "extension_doctor",
                _loopx_json_command(loopx_bin, route_arguments, "extension", "doctor", "--all-enabled", "--execute"),
                timeout=timeout_seconds,
            )
            updated["ok"] = extension_doctor_result.returncode == 0
        else:
            execution["extension_doctor_status"] = "skipped_core_doctor_failed"
            updated["ok"] = False
            raise RuntimeError("Rollback target failed runtime checks; inspect the Doctor output.")
    except Exception as exc:
        execution["error"] = str(exc)
        updated["ok"] = False
        if switched and previous_link_target and promoted_link_stat is not None:
            restore_link = loopx_bin.with_name(f".{loopx_bin.name}.rollback.restore.{os.getpid()}")
            try:
                if os.path.samestat(promoted_link_stat, loopx_bin.lstat()):
                    if restore_link.exists() or restore_link.is_symlink():
                        restore_link.unlink()
                    restore_link.symlink_to(previous_link_target)
                    os.replace(restore_link, loopx_bin)
                    execution["restored_previous_on_failure"] = True
            except Exception as restore_exc:
                execution["restore_error"] = str(restore_exc)
    finally:
        for stale_link in (temp_link, restore_link):
            if stale_link is not None and (stale_link.exists() or stale_link.is_symlink()):
                stale_link.unlink()
        if promotion_guard is not None:
            promotion_guard.close()
    updated["execution"] = execution
    if updated["ok"]:
        updated["recommended_action"] = "rollback complete; review doctor output"
    elif execution.get("doctor_returncode") == 0 and execution.get("extension_doctor_returncode") not in {None, 0}:
        updated["recommended_action"] = (
            "rollback activated, but one or more enabled extensions remain fail "
            "closed; repair the provider and run `loopx extension doctor --all-enabled --execute`"
        )
    else:
        updated["recommended_action"] = "rollback failed; inspect execution error before retrying"
    return updated


def _render_rollback_markdown(payload: PayloadView) -> str:
    plan = _nested_dict(payload, "plan")
    raw_exec = payload.get("execution")
    execution = _as_dict(raw_exec) if isinstance(raw_exec, Mapping) else None
    lines = [
        "# LoopX Update Rollback", "",
        f"- OK: `{payload.get('ok')}`",
        f"- Requested release: `{payload.get('requested_release_id')}`",
        f"- Selected release: `{plan.get('selected_release_id')}`",
        f"- Selected root: `{plan.get('selected_release_root')}`",
        f"- Reason: `{plan.get('reason')}`",
        f"- Runtime state mutation: `{plan.get('mutates_loopx_runtime_state')}`",
        f"- Release install mutation: `{plan.get('mutates_release_install')}`",
        f"- Recommended action: {payload.get('recommended_action')}",
    ]
    if rollback_command := plan.get("rollback_command"):
        lines.extend(["", "## Rollback Command", "", "```bash", str(rollback_command), "```"])
    if execution is not None:
        lines.extend(["", "## Execution", ""])
        for stage, label in (
            ("compatibility", "Authority compatibility"),
            ("doctor", "Doctor"),
            ("extension_doctor", "Extension doctor"),
        ):
            returncode = execution.get(f"{stage}_returncode")
            if returncode is None:
                continue
            lines.append(f"- {label} return code: `{returncode}`")
            if returncode != 0:
                for stream in ("stdout", "stderr"):
                    if output := execution.get(f"{stage}_{stream}_tail"):
                        lines.extend(["", f"### {label} {stream}", "", "```text", str(output), "```"])
        if execution.get("error"):
            lines.extend(["", f"Error: {execution['error']}"])
    return "\n".join(lines) + "\n"


def render_update_plan_markdown(payload: PayloadView) -> str:
    if payload.get("mode") == "rollback":
        return _render_rollback_markdown(payload)

    source = _nested_dict(payload, "source")
    source_version_check = _nested_dict(payload, "source_version_check")
    source_commit_check = _nested_dict(payload, "source_commit_check")
    runtime_activation = _nested_dict(payload, "runtime_activation_qualification")
    current = _nested_dict(payload, "current")
    plan = _nested_dict(payload, "plan")
    backup = _nested_dict(plan, "backup")
    next_action = _nested_dict(payload, "next_action")
    lifecycle = _nested_dict(payload, "install_lifecycle")
    commands = _nested_dict(payload, "commands")
    raw_exec = payload.get("execution")
    execution = _as_dict(raw_exec) if isinstance(raw_exec, Mapping) else None
    notice = (
        "> **No update was applied.** This is a read-only check/plan."
        if not payload.get("execute_requested")
        else "> **Apply was requested.** Review the execution and validation result below."
    )
    lines = [
        "# LoopX Update", "", notice, "", "## Next Action", "",
        f"- Kind: `{next_action.get('kind')}`",
        f"- Mutating: `{next_action.get('mutating')}`",
        f"- Requires explicit approval: `{next_action.get('requires_explicit_approval')}`",
    ]
    if next_command := next_action.get("command"):
        lines.extend(["", "```bash", str(next_command), "```"])
    if isinstance(raw_prompt := payload.get("automation_prompt_upgrade"), Mapping):
        prompt_upgrade = _as_dict(raw_prompt)
        lines.extend(["", "## Automation Prompts", "", f"- Status: `{prompt_upgrade.get('status')}`"])
        if "upgrade_complete" in payload:
            lines.append(f"- Upgrade complete (runtime + prompts): `{payload['upgrade_complete']}`")
        for item in _as_dict_list(prompt_upgrade.get("results")):
            lines.append(f"- `{item.get('automation_id')}`: `{item.get('status')}`")
            if item.get("reason"):
                lines.append(f"  - {item['reason']}")
        if prompt_upgrade.get("snapshot_file"):
            lines.append(f"- Private recovery snapshot: `{prompt_upgrade['snapshot_file']}`")
        if prompt_upgrade.get("next_action"):
            lines.append(f"- Next: {prompt_upgrade['next_action']}")
    installed_identity = _nested_dict(execution, "installed_runtime_identity") if execution else {}
    if installed_identity:
        lines.extend([
            "", "## Installed CLI Readback", "",
            f"- Version: `{installed_identity.get('package_version')}`",
            f"- Source revision: `{installed_identity.get('source_revision')}`",
            "- Fresh process installation check passed; the plan below describes the pre-update installation.",
        ])
    if execution and execution.get("activation_pending"):
        lines.extend([
            "", "## Service activation pending", "",
            "- The CLI is installed; managed-service activation is incomplete.",
            f"- Reason: `{execution.get('restart_reason')}`",
            "- Accepted Chat work stays with its original consumer. Retry the same update after it finishes or its unreadable state is repaired.",
        ])
    lines.extend([
        "", "## Plan (before execution)" if payload.get("execute_requested") else "## Details", "",
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
        f"- Source commit check: `{source_commit_check.get('status')}`",
        f"- Source commit matches current: `{source_commit_check.get('matches_current')}`",
        f"- Source commit check reason: {source_commit_check.get('reason')}",
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
        f"- Install kind: `{lifecycle.get('install_kind')}`",
        f"- Install owner: `{lifecycle.get('owner')}`",
        f"- Apply supported: `{plan.get('apply_supported')}`",
        f"- Rollback available: `{backup.get('available')}`",
        f"- Recommended action: {payload.get('recommended_action')}",
        "", "## Planned Installation-owner Command", "", "```bash",
        str(plan.get("install_command") or ""), "```",
        "", "## Intent Commands", "",
        f"- Check: `{commands.get('check')}`",
        f"- Plan: `{commands.get('plan')}`",
        f"- Apply: `{commands.get('apply')}`",
    ])
    if rollback_command := backup.get("rollback_command"):
        lines.extend(["", "## Rollback Command", "", "```bash", str(rollback_command), "```"])
    if execution is not None:
        lines.extend([
            "", "## Execution", "",
            f"- Install return code: `{execution.get('install_returncode')}`",
            f"- Doctor return code: `{execution.get('doctor_returncode')}`",
        ])
        if isinstance(raw_download := execution.get("installer_download"), Mapping):
            download = _as_dict(raw_download)
            lines.append(f"- Installer stage: `{download.get('stage')}`")
            for attempt in _as_dict_list(download.get("attempts")):
                lines.append(
                    f"- Download attempt {attempt.get('attempt')}: "
                    f"HTTP `{attempt.get('http_status')}`, curl `{attempt.get('curl_returncode')}`"
                )
    return "\n".join(lines) + "\n"
