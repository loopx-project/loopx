from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
from typing import Any

from ...registry import atomic_write_json
from .ledger import record_pending_change
from .policy import ChangeWindowPolicy, evaluate_policy
from .repository import (
    RepositoryChangeWindowError,
    git,
    git_text,
    repository_root,
    repository_common_dir,
    repository_identity,
    resolve_repository_context,
)


PROVIDER_STATE_SCHEMA_VERSION = "repository_change_window_git_hook_state_v2"
PREVIOUS_PROVIDER_STATE_SCHEMA_VERSION = "repository_change_window_git_hook_state_v1"
LEGACY_PROVIDER_STATE_SCHEMA_VERSION = "repository_change_window_git_hook_state_v0"
PROVIDER_STATUS_SCHEMA_VERSION = "repository_change_window_git_hook_status_v2"
EXTERNAL_GUARD_DIAGNOSTIC_SCHEMA_VERSION = (
    "repository_change_window_external_guard_diagnostic_v0"
)
HOOK_RUNTIME_SCHEMA_VERSION = "repository_change_window_hook_runtime_v1"
PROVIDER_RELATIVE_DIR = Path("loopx") / "repository-change-window"


class EnforcementLevel(str, Enum):
    HOOK_ONLY = "hook_only"
    REFERENCE_GUARD = "reference_guard"

    @classmethod
    def parse(cls, value: str | EnforcementLevel) -> EnforcementLevel:
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value or "").strip())
        except ValueError as exc:
            supported = ", ".join(item.value for item in cls)
            raise RepositoryChangeWindowError(
                f"unsupported enforcement level `{value}`; use one of: {supported}"
            ) from exc


class GitSshService(str, Enum):
    RECEIVE_PACK = "git-receive-pack"
    UPLOAD_PACK = "git-upload-pack"
    UPLOAD_ARCHIVE = "git-upload-archive"


class ReferenceTransactionPhase(str, Enum):
    PREPARING = "preparing"
    PREPARED = "prepared"
    COMMITTED = "committed"
    ABORTED = "aborted"


BASE_HOOK_NAMES = ("pre-commit", "pre-push")
REFERENCE_GUARD_HOOK_NAME = "reference-transaction"
SSH_TRANSPORT_EVENT = "ssh-transport"
SSH_COMMAND_NAME = "ssh-command"
LEGACY_GLOBAL_GUARD_KIND = "loopx_global_commit_time_gate_v0"
LEGACY_MARKER_READ_LIMIT = 64 * 1024


def hook_runtime_contract() -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": HOOK_RUNTIME_SCHEMA_VERSION,
        "supported_events": list(
            (*BASE_HOOK_NAMES, REFERENCE_GUARD_HOOK_NAME, SSH_TRANSPORT_EVENT)
        ),
    }


def _hook_runtime_check(level: EnforcementLevel) -> dict[str, object]:
    expected_events = set(_managed_events(level))
    try:
        result = subprocess.run(
            [
                "loopx",
                "--format",
                "json",
                "change-window",
                "hook-runtime",
            ],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {
            "check": "hook_runtime_contract",
            "ok": False,
            "status": "unavailable",
            "error": type(exc).__name__,
        }
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = None
    supported_events = (
        set(payload.get("supported_events", ()))
        if isinstance(payload, Mapping)
        else set()
    )
    current = bool(
        result.returncode == 0
        and isinstance(payload, Mapping)
        and payload.get("ok") is True
        and payload.get("schema_version") == HOOK_RUNTIME_SCHEMA_VERSION
        and expected_events <= supported_events
    )
    return {
        "check": "hook_runtime_contract",
        "ok": current,
        "status": "current" if current else "incompatible",
        "required_schema_version": HOOK_RUNTIME_SCHEMA_VERSION,
        "required_events": sorted(expected_events),
    }


def _managed_hook_names(level: EnforcementLevel) -> tuple[str, ...]:
    if level is EnforcementLevel.REFERENCE_GUARD:
        return (*BASE_HOOK_NAMES, REFERENCE_GUARD_HOOK_NAME)
    return BASE_HOOK_NAMES


def _managed_events(level: EnforcementLevel) -> tuple[str, ...]:
    events = _managed_hook_names(level)
    if level is EnforcementLevel.REFERENCE_GUARD:
        return (*events, SSH_TRANSPORT_EVENT)
    return events


def _state_enforcement_level(state: Mapping[str, Any]) -> EnforcementLevel:
    return EnforcementLevel.parse(
        str(state.get("enforcement_level") or EnforcementLevel.HOOK_ONLY.value)
    )


def _state_manages_ssh_transport(state: Mapping[str, Any]) -> bool:
    """Return whether this state schema owns the repository SSH route.

    State v0 only managed pre-commit/pre-push. State v1 added the typed hook
    enforcement level, including reference-transaction, but still did not own
    core.sshCommand. State v2 is the first schema that persists enough
    information to restore the SSH route safely.
    """

    return state.get("schema_version") == PROVIDER_STATE_SCHEMA_VERSION


def _provider_dir(common_dir: Path) -> Path:
    return common_dir / PROVIDER_RELATIVE_DIR


def _state_path(common_dir: Path) -> Path:
    return _provider_dir(common_dir) / "provider.json"


def _hooks_dir(common_dir: Path) -> Path:
    return _provider_dir(common_dir) / "hooks"


def _ssh_command_path(common_dir: Path) -> Path:
    return _provider_dir(common_dir) / SSH_COMMAND_NAME


def _hook_script(event: str) -> str:
    return "\n".join(
        (
            "#!/bin/sh",
            "set -u",
            "export LOOPX_GIT_HOOK_QUIET_SUCCESS=1",
            (
                "exec loopx --format json change-window hook "
                f'--repo-path . --event {event} -- "$@"'
            ),
            "",
        )
    )


def _ssh_command_script(previous_command: str | None) -> str:
    if previous_command:
        delegated = "exec " + previous_command + ' "$@"'
        delegate_line = (
            "exec /bin/sh -c " + shlex.quote(delegated) + ' loopx-previous-ssh "$@"'
        )
    else:
        delegate_line = 'exec ssh "$@"'
    return "\n".join(
        (
            "#!/bin/sh",
            "set -u",
            (
                "if ! loopx --format json change-window hook --repo-path . "
                '--event ssh-transport -- "$@" </dev/null >/dev/null; then'
            ),
            '  printf "%s\\n" "LoopX repository change window blocked SSH transport" >&2',
            "  exit 1",
            "fi",
            delegate_line,
            "",
        )
    )


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_state(path: Path) -> dict[str, Any]:
    if path.is_symlink():
        raise RepositoryChangeWindowError(
            "repository change-window provider state must not be a symlink"
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise RepositoryChangeWindowError(
            "repository change-window provider is not installed"
        ) from exc
    except json.JSONDecodeError as exc:
        raise RepositoryChangeWindowError(
            "repository change-window provider state is invalid JSON"
        ) from exc
    if not isinstance(raw, dict) or raw.get("schema_version") not in {
        PROVIDER_STATE_SCHEMA_VERSION,
        PREVIOUS_PROVIDER_STATE_SCHEMA_VERSION,
        LEGACY_PROVIDER_STATE_SCHEMA_VERSION,
    }:
        raise RepositoryChangeWindowError(
            "repository change-window provider state has an unsupported schema"
        )
    policy_raw = raw.get("policy")
    ChangeWindowPolicy.from_dict(policy_raw if isinstance(policy_raw, Mapping) else {})
    _state_enforcement_level(raw)
    return raw


def _effective_hooks_path(repo: Path) -> Path:
    raw = Path(git_text(repo, "rev-parse", "--git-path", "hooks"))
    return (raw if raw.is_absolute() else repo / raw).resolve()


def _effective_config_scope(repo: Path, key: str) -> str | None:
    output = git_text(
        repo,
        "config",
        "--show-scope",
        "--get",
        key,
        check=False,
    )
    if not output:
        return None
    scope = output.split(None, 1)[0].strip().lower()
    return (
        scope
        if scope in {"system", "global", "local", "worktree", "command"}
        else "unknown"
    )


def _bounded_regular_text(path: Path) -> str | None:
    if path.is_symlink():
        return None
    try:
        stat_result = path.stat()
    except OSError:
        return None
    if not path.is_file() or stat_result.st_size > LEGACY_MARKER_READ_LIMIT:
        return None
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _recognized_legacy_global_guard(
    *, hooks_root: Path, hooks_scope: str | None
) -> str | None:
    """Recognize the one bounded legacy LoopX layout without trusting its policy."""

    if hooks_scope != "global" or hooks_root.is_symlink():
        return None
    readme = _bounded_regular_text(hooks_root / "README.md")
    common = _bounded_regular_text(hooks_root / "gate-common.sh")
    pre_commit = _bounded_regular_text(hooks_root / "pre-commit")
    pre_push = _bounded_regular_text(hooks_root / "pre-push")
    if (
        readme is not None
        and common is not None
        and pre_commit is not None
        and pre_push is not None
        and "LoopX Local Commit Time Gate" in readme
        and "loopx_commit_gate_enforce" in common
        and "gate-common.sh" in pre_commit
        and "gate-common.sh" in pre_push
    ):
        return LEGACY_GLOBAL_GUARD_KIND
    return None


def _external_guard_diagnostic(*, repo: Path) -> dict[str, Any]:
    hooks_scope = _effective_config_scope(repo, "core.hooksPath")
    ssh_scope = _effective_config_scope(repo, "core.sshCommand")
    hooks_root = _effective_hooks_path(repo)
    hooks_configured = hooks_scope is not None
    ssh_configured = ssh_scope is not None
    surfaces = [
        surface
        for surface, configured in (
            ("hooks_path", hooks_configured),
            ("ssh_command", ssh_configured),
        )
        if configured
    ]
    legacy_kind = (
        _recognized_legacy_global_guard(
            hooks_root=hooks_root,
            hooks_scope=hooks_scope,
        )
        if hooks_configured
        else None
    )
    diagnostic: dict[str, Any] = {
        "schema_version": EXTERNAL_GUARD_DIAGNOSTIC_SCHEMA_VERSION,
        "detected": bool(surfaces),
        "status": "detected" if surfaces else "not_detected",
        "effective_surfaces": surfaces,
        "configuration_scopes": {
            "hooks_path": hooks_scope,
            "ssh_command": ssh_scope,
        },
        "diagnostic_only": True,
        "trusted_provider": False,
        "policy_known": False,
        "admission_known": False,
        "recognized_legacy_kind": legacy_kind,
        "path_free": True,
        "remote_write_authority_granted": False,
    }
    if legacy_kind is not None:
        diagnostic["migration_preview"] = {
            "schema_version": "repository_change_window_legacy_migration_preview_v0",
            "action": "install_repository_provider",
            "integration_mode": "layered_legacy_global_guard",
            "preview_required": True,
            "execute_required": True,
            "automatic_migration": False,
            "preserves_effective_guard": True,
            "mutates_global_configuration": False,
            "grants_remote_write_authority": False,
        }
    return diagnostic


def _installation_mode(
    *, existing: Mapping[str, Any] | None, diagnostic: Mapping[str, Any]
) -> str:
    if existing is not None:
        schema = existing.get("schema_version")
        if schema in {
            PREVIOUS_PROVIDER_STATE_SCHEMA_VERSION,
            LEGACY_PROVIDER_STATE_SCHEMA_VERSION,
        }:
            return "migrated_legacy_repository_provider"
        return "reconfigured_repository_provider"
    if diagnostic.get("recognized_legacy_kind") == LEGACY_GLOBAL_GUARD_KIND:
        return "layered_legacy_global_guard"
    if diagnostic.get("detected") is True:
        return "layered_external_guard"
    return "fresh_repository_provider"


def _local_hooks_override(repo: Path) -> tuple[bool, str | None]:
    value = git_text(repo, "config", "--local", "--get", "core.hooksPath", check=False)
    return bool(value), value or None


def _ssh_command_config(repo: Path) -> tuple[bool, str | None, str | None]:
    local_value = git_text(
        repo, "config", "--local", "--get", "core.sshCommand", check=False
    )
    effective_value = git_text(repo, "config", "--get", "core.sshCommand", check=False)
    return bool(local_value), local_value or None, effective_value or None


def _restore_local_ssh_command(repo: Path, state: Mapping[str, Any]) -> None:
    if state.get("previous_ssh_local_override"):
        previous = str(state.get("previous_ssh_local_value") or "")
        if not previous:
            raise RepositoryChangeWindowError(
                "provider state lost the previous local core.sshCommand value"
            )
        git(repo, "config", "--local", "core.sshCommand", previous)
        return
    result = git(
        repo,
        "config",
        "--local",
        "--unset-all",
        "core.sshCommand",
        check=False,
    )
    if result.returncode not in {0, 5}:
        raise RepositoryChangeWindowError(
            "failed to remove the managed local core.sshCommand override"
        )


def _write_provider_files(
    *,
    state_path: Path,
    hooks_dir: Path,
    state: Mapping[str, Any],
) -> None:
    if state_path.is_symlink() or hooks_dir.is_symlink():
        raise RepositoryChangeWindowError("managed provider paths must not be symlinks")
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks_dir.parent.chmod(0o700)
    hooks_dir.chmod(0o700)
    managed_events = _managed_hook_names(_state_enforcement_level(state))
    for event in (*BASE_HOOK_NAMES, REFERENCE_GUARD_HOOK_NAME):
        path = hooks_dir / event
        if path.is_symlink():
            raise RepositoryChangeWindowError(
                f"managed hook `{event}` must not be a symlink"
            )
        if event not in managed_events:
            path.unlink(missing_ok=True)
            continue
        path.write_text(_hook_script(event), encoding="utf-8", newline="\n")
        path.chmod(0o755)
    ssh_command = hooks_dir.parent / SSH_COMMAND_NAME
    if ssh_command.is_symlink():
        raise RepositoryChangeWindowError("managed SSH command must not be a symlink")
    if _state_enforcement_level(state) is EnforcementLevel.REFERENCE_GUARD:
        ssh_command.write_text(
            _ssh_command_script(
                str(state.get("previous_ssh_command"))
                if state.get("previous_ssh_command") is not None
                else None
            ),
            encoding="utf-8",
            newline="\n",
        )
        ssh_command.chmod(0o755)
    else:
        ssh_command.unlink(missing_ok=True)
    atomic_write_json(state_path, dict(state))
    state_path.chmod(0o600)


def _provider_checks(
    *,
    repo: Path,
    common_dir: Path,
    state: Mapping[str, Any],
    include_runtime: bool = True,
) -> list[dict[str, object]]:
    hooks_dir = _hooks_dir(common_dir)
    repository_matches = state.get("repository_id") == repository_identity(repo, common_dir)
    configured = git_text(
        repo, "config", "--local", "--get", "core.hooksPath", check=False
    )
    expected_config = str(hooks_dir)
    checks: list[dict[str, object]] = [
        {
            "check": "provider_schema",
            "ok": state.get("schema_version") == PROVIDER_STATE_SCHEMA_VERSION,
            "status": (
                "current"
                if state.get("schema_version") == PROVIDER_STATE_SCHEMA_VERSION
                else "migration_required"
            ),
        },
        {
            "check": "repository_identity",
            "ok": repository_matches,
            "status": "match" if repository_matches else "mismatch",
        },
        {
            "check": "local_core_hooks_path",
            "ok": configured == expected_config,
            "status": "match" if configured == expected_config else "drifted",
        },
    ]
    expected_digests = state.get("hook_digests")
    managed_events = _managed_hook_names(_state_enforcement_level(state))
    for event in managed_events:
        path = hooks_dir / event
        expected = (
            expected_digests.get(event)
            if isinstance(expected_digests, Mapping)
            else None
        )
        actual = (
            hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        )
        executable = path.is_file() and os.access(path, os.X_OK)
        ok = bool(actual == expected and executable)
        checks.append(
            {
                "check": f"hook:{event}",
                "ok": ok,
                "status": "current" if ok else "missing_or_modified",
            }
        )
    for event in (*BASE_HOOK_NAMES, REFERENCE_GUARD_HOOK_NAME):
        if event in managed_events:
            continue
        path = hooks_dir / event
        absent = not path.exists() and not path.is_symlink()
        checks.append(
            {
                "check": f"hook:{event}:absent",
                "ok": absent,
                "status": "absent" if absent else "unexpected_managed_hook",
            }
        )
    enforcement = _state_enforcement_level(state)
    ssh_command = _ssh_command_path(common_dir)
    configured_ssh = git_text(
        repo, "config", "--local", "--get", "core.sshCommand", check=False
    )
    if not _state_manages_ssh_transport(state):
        command_absent = not ssh_command.exists() and not ssh_command.is_symlink()
        route_unowned = configured_ssh != str(ssh_command)
        checks.extend(
            [
                {
                    "check": "legacy_ssh-command:absent",
                    "ok": command_absent,
                    "status": (
                        "not_managed" if command_absent else "unexpected_managed_command"
                    ),
                },
                {
                    "check": "legacy_local_core_ssh_command:unowned",
                    "ok": route_unowned,
                    "status": (
                        "not_managed" if route_unowned else "ambiguous_managed_route"
                    ),
                },
            ]
        )
    elif enforcement is EnforcementLevel.REFERENCE_GUARD:
        expected_ssh_digest = state.get("ssh_command_digest")
        actual_ssh_digest = (
            hashlib.sha256(ssh_command.read_bytes()).hexdigest()
            if ssh_command.is_file()
            else None
        )
        executable = ssh_command.is_file() and os.access(ssh_command, os.X_OK)
        command_ok = configured_ssh == str(ssh_command)
        checks.extend(
            [
                {
                    "check": "local_core_ssh_command",
                    "ok": command_ok,
                    "status": "match" if command_ok else "drifted",
                },
                {
                    "check": "ssh-command",
                    "ok": bool(actual_ssh_digest == expected_ssh_digest and executable),
                    "status": (
                        "current"
                        if actual_ssh_digest == expected_ssh_digest and executable
                        else "missing_or_modified"
                    ),
                },
            ]
        )
    else:
        absent = not ssh_command.exists() and not ssh_command.is_symlink()
        expected_local_ssh = (
            str(state.get("previous_ssh_local_value") or "")
            if state.get("previous_ssh_local_override")
            else ""
        )
        checks.extend(
            [
                {
                    "check": "ssh-command:absent",
                    "ok": absent,
                    "status": "absent" if absent else "unexpected_managed_command",
                },
                {
                    "check": "local_core_ssh_command:restored",
                    "ok": configured_ssh == expected_local_ssh,
                    "status": (
                        "restored"
                        if configured_ssh == expected_local_ssh
                        else "drifted"
                    ),
                },
            ]
        )
    if include_runtime:
        checks.append(_hook_runtime_check(enforcement))
    return checks


def install_git_hook_provider(
    *,
    repo_path: str | Path,
    policy: ChangeWindowPolicy,
    enforcement_level: EnforcementLevel | str = EnforcementLevel.HOOK_ONLY,
    execute: bool = False,
    replace: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    enforcement = EnforcementLevel.parse(enforcement_level)
    context = resolve_repository_context(repo_path)
    provider_dir = _provider_dir(context.common_dir)
    if provider_dir.is_symlink():
        raise RepositoryChangeWindowError(
            "managed provider directory must not be a symlink"
        )
    state_path = _state_path(context.common_dir)
    hooks_dir = _hooks_dir(context.common_dir)
    external_guard: dict[str, Any] | None = None
    existing: dict[str, Any] | None = None
    if state_path.exists():
        existing = _read_state(state_path)
        checks = _provider_checks(
            repo=context.root,
            common_dir=context.common_dir,
            state=existing,
        )
        existing_policy = ChangeWindowPolicy.from_dict(existing["policy"])
        if (
            existing.get("schema_version") == PROVIDER_STATE_SCHEMA_VERSION
            and existing_policy == policy
            and _state_enforcement_level(existing) is enforcement
            and all(bool(item["ok"]) for item in checks)
            and existing.get("hook_digests") == {
                event: _digest_text(_hook_script(event))
                for event in _managed_hook_names(enforcement)
            }
        ):
            installation_mode = str(
                existing.get("installation_mode") or "fresh_repository_provider"
            )
            return {
                "ok": True,
                "schema_version": "repository_change_window_install_v1",
                "dry_run": not execute,
                "changed": False,
                "status": "already_installed",
                "repository_id": context.repository_id,
                "provider_id": "git-hook",
                "enforcement_level": enforcement.value,
                "policy": policy.to_dict(),
                "installation_mode": installation_mode,
            }
        if not replace:
            raise RepositoryChangeWindowError(
                "provider already exists with different policy or local drift; rerun install with --replace only after review"
            )

    if existing is None:
        external_guard = _external_guard_diagnostic(repo=context.root)
        previous_hook_root = _effective_hooks_path(context.root)
        previous_local_override, previous_local_value = _local_hooks_override(
            context.root
        )
        if previous_hook_root == hooks_dir:
            raise RepositoryChangeWindowError(
                "effective hook root already points at the managed provider without valid state"
            )
        (
            previous_ssh_local_override,
            previous_ssh_local_value,
            previous_ssh_command,
        ) = _ssh_command_config(context.root)
    else:
        previous_hook_root = Path(str(existing.get("previous_hook_root") or ""))
        previous_local_override = bool(existing.get("previous_local_override"))
        previous_local_value = (
            str(existing.get("previous_local_value"))
            if existing.get("previous_local_value") is not None
            else None
        )
        has_preserved_ssh_route = all(
            key in existing
            for key in (
                "previous_ssh_local_override",
                "previous_ssh_local_value",
                "previous_ssh_command",
            )
        )
        if has_preserved_ssh_route:
            previous_ssh_local_override = bool(
                existing.get("previous_ssh_local_override")
            )
            previous_ssh_local_value = (
                str(existing.get("previous_ssh_local_value"))
                if existing.get("previous_ssh_local_value") is not None
                else None
            )
            previous_ssh_command = (
                str(existing.get("previous_ssh_command"))
                if existing.get("previous_ssh_command") is not None
                else None
            )
        else:
            (
                previous_ssh_local_override,
                previous_ssh_local_value,
                previous_ssh_command,
            ) = _ssh_command_config(context.root)
            if previous_ssh_command == str(_ssh_command_path(context.common_dir)):
                raise RepositoryChangeWindowError(
                    "legacy provider state does not preserve the SSH route that "
                    "preceded the managed command; uninstall the legacy provider "
                    "with its matching LoopX runtime before replacing it"
                )
    installation_mode = _installation_mode(
        existing=existing,
        diagnostic=external_guard or {},
    )
    installed_at = (
        (now or datetime.now(timezone.utc))
        .astimezone(timezone.utc)
        .isoformat()
        .replace("+00:00", "Z")
    )
    state = {
        "schema_version": PROVIDER_STATE_SCHEMA_VERSION,
        "provider_id": "git-hook",
        "enforcement_level": enforcement.value,
        "repository_id": context.repository_id,
        "policy": policy.to_dict(),
        "previous_hook_root": str(previous_hook_root),
        "previous_local_override": previous_local_override,
        "previous_local_value": previous_local_value,
        "previous_ssh_local_override": previous_ssh_local_override,
        "previous_ssh_local_value": previous_ssh_local_value,
        "previous_ssh_command": previous_ssh_command,
        "managed_hooks_path": str(hooks_dir),
        "hook_digests": {
            event: _digest_text(_hook_script(event))
            for event in _managed_hook_names(enforcement)
        },
        "ssh_command_digest": (
            _digest_text(_ssh_command_script(previous_ssh_command))
            if enforcement is EnforcementLevel.REFERENCE_GUARD
            else None
        ),
        "installed_at": installed_at,
        "installation_mode": installation_mode,
    }
    if execute:
        runtime_check = _hook_runtime_check(enforcement)
        if not runtime_check["ok"]:
            raise RepositoryChangeWindowError(
                "the `loopx` executable on PATH does not satisfy the managed Git-hook "
                "runtime contract; activate or install the current LoopX runtime before "
                "installing the provider"
            )
        _write_provider_files(state_path=state_path, hooks_dir=hooks_dir, state=state)
        try:
            git(context.root, "config", "--local", "core.hooksPath", str(hooks_dir))
            if enforcement is EnforcementLevel.REFERENCE_GUARD:
                git(
                    context.root,
                    "config",
                    "--local",
                    "core.sshCommand",
                    str(_ssh_command_path(context.common_dir)),
                )
            else:
                _restore_local_ssh_command(context.root, state)
        except BaseException:
            if existing is None:
                shutil.rmtree(_provider_dir(context.common_dir), ignore_errors=True)
            raise
    return {
        "ok": True,
        "schema_version": "repository_change_window_install_v1",
        "dry_run": not execute,
        "changed": execute,
        "status": "installed" if execute else "preview",
        "repository_id": context.repository_id,
        "provider_id": "git-hook",
        "enforcement_level": enforcement.value,
        "default_enabled": False,
        "enabled": execute,
        "preserves_previous_hooks": True,
        "installation_mode": installation_mode,
        **(
            {"previous_guard_diagnostic": external_guard}
            if external_guard is not None
            else {}
        ),
        "policy": policy.to_dict(),
        "managed_hook_names": list(_managed_hook_names(enforcement)),
        "guards_ssh_transport": enforcement is EnforcementLevel.REFERENCE_GUARD,
        "contains_personal_path": False,
    }


def git_hook_provider_status(
    *,
    repo_path: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    context = resolve_repository_context(repo_path)
    state_path = _state_path(context.common_dir)
    if not state_path.exists():
        external_guard = _external_guard_diagnostic(repo=context.root)
        return {
            "ok": True,
            "schema_version": PROVIDER_STATUS_SCHEMA_VERSION,
            "status": (
                "effective_external_guard_detected"
                if external_guard["detected"]
                else "provider_not_installed"
            ),
            "installed": False,
            "enabled": False,
            "repository_id": context.repository_id,
            "provider_id": "git-hook",
            "default_enabled": False,
            "enforcement_level": None,
            "supported_enforcement_levels": [item.value for item in EnforcementLevel],
            "external_guard": external_guard,
            "contains_personal_path": False,
        }
    state = _read_state(state_path)
    enforcement = _state_enforcement_level(state)
    checks = _provider_checks(
        repo=context.root,
        common_dir=context.common_dir,
        state=state,
    )
    policy = ChangeWindowPolicy.from_dict(state["policy"])
    decision = evaluate_policy(policy, now=now)
    healthy = all(bool(item["ok"]) for item in checks)
    return {
        "ok": healthy,
        "schema_version": PROVIDER_STATUS_SCHEMA_VERSION,
        "status": "ready" if healthy else "drifted",
        "installed": True,
        "enabled": healthy,
        "repository_id": context.repository_id,
        "provider_id": "git-hook",
        "enforcement_level": enforcement.value,
        "managed_hook_names": list(_managed_hook_names(enforcement)),
        "guards_ssh_transport": enforcement is EnforcementLevel.REFERENCE_GUARD,
        "policy": policy.to_dict(),
        "decision": decision,
        "checks": checks,
        "preserves_previous_hooks": True,
        "installation_mode": str(
            state.get("installation_mode") or "fresh_repository_provider"
        ),
        "contains_personal_path": False,
    }


def verify_git_hook_provider(
    *,
    repo_path: str | Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    payload = git_hook_provider_status(repo_path=repo_path, now=now)
    verified = payload.get("ok") is True and payload.get("installed") is True
    return {
        **payload,
        "ok": verified,
        "schema_version": "repository_change_window_provider_verify_v1",
        "verified": verified,
    }


def uninstall_git_hook_provider(
    *,
    repo_path: str | Path,
    execute: bool = False,
) -> dict[str, Any]:
    context = resolve_repository_context(repo_path)
    if _provider_dir(context.common_dir).is_symlink():
        raise RepositoryChangeWindowError(
            "managed provider directory must not be a symlink"
        )
    state = _read_state(_state_path(context.common_dir))
    checks = _provider_checks(
        repo=context.root,
        common_dir=context.common_dir,
        state=state,
        include_runtime=False,
    )
    asset_checks = [item for item in checks if item["check"] != "provider_schema"]
    if not all(bool(item["ok"]) for item in asset_checks):
        raise RepositoryChangeWindowError(
            "provider drift detected; uninstall will not overwrite modified hook state"
        )
    if execute:
        if state.get("previous_local_override"):
            previous = str(state.get("previous_local_value") or "")
            if not previous:
                raise RepositoryChangeWindowError(
                    "provider state lost the previous local core.hooksPath value"
                )
            git(context.root, "config", "--local", "core.hooksPath", previous)
        else:
            result = git(
                context.root,
                "config",
                "--local",
                "--unset-all",
                "core.hooksPath",
                check=False,
            )
            if result.returncode not in {0, 5}:
                raise RepositoryChangeWindowError(
                    "failed to remove the managed local core.hooksPath override"
                )
        if _state_manages_ssh_transport(state):
            _restore_local_ssh_command(context.root, state)
        shutil.rmtree(_provider_dir(context.common_dir))
    return {
        "ok": True,
        "schema_version": "repository_change_window_uninstall_v0",
        "dry_run": not execute,
        "changed": execute,
        "status": "uninstalled" if execute else "preview",
        "repository_id": context.repository_id,
        "provider_id": "git-hook",
        "restores_previous_hook_route": True,
        "restores_previous_ssh_route": _state_manages_ssh_transport(state),
        "installed_state_schema_version": state.get("schema_version"),
        "contains_personal_path": False,
    }


def _commit_reachable_from_existing_branch_or_head(repo: Path, oid: str) -> bool:
    containing_branches = git_text(
        repo,
        "for-each-ref",
        f"--contains={oid}",
        "--format=%(refname)",
        "refs/heads",
        "refs/remotes",
        check=False,
    )
    if containing_branches:
        return True
    return bool(
        git(repo, "merge-base", "--is-ancestor", oid, "HEAD", check=False).returncode
        == 0
    )


def _reference_transaction_introduces_commit(
    *,
    repo: Path,
    hook_args: Sequence[str],
    hook_stdin: bytes,
) -> bool:
    if len(hook_args) != 1:
        raise RepositoryChangeWindowError(
            "reference-transaction requires exactly one phase"
        )
    try:
        phase = ReferenceTransactionPhase(hook_args[0])
    except ValueError as exc:
        supported = ", ".join(item.value for item in ReferenceTransactionPhase)
        raise RepositoryChangeWindowError(
            f"unsupported reference-transaction phase `{hook_args[0]}`; use one of: {supported}"
        ) from exc
    if phase not in {
        ReferenceTransactionPhase.PREPARING,
        ReferenceTransactionPhase.PREPARED,
    }:
        return False
    # Git can invoke admission hooks for a zero-update transaction (including
    # Git 2.55's pull path). No refs means no new commit to guard; provider
    # integrity and previous-hook delegation remain owned by the caller.
    if not hook_stdin:
        return False
    if not hook_stdin.strip():
        raise RepositoryChangeWindowError(
            f"reference-transaction {phase.value} phase requires at least one ref update"
        )
    try:
        lines = hook_stdin.decode("ascii").splitlines()
    except UnicodeDecodeError as exc:
        raise RepositoryChangeWindowError(
            "reference-transaction input must contain ASCII object ids and refs"
        ) from exc
    for line in lines:
        fields = line.split(" ", 2)
        if len(fields) != 3:
            raise RepositoryChangeWindowError(
                "reference-transaction input must use `<old-oid> <new-oid> <ref>` rows"
            )
        _old_oid, new_oid, ref_name = fields
        if ref_name != "HEAD" and not ref_name.startswith("refs/heads/"):
            continue
        if new_oid and not new_oid.strip("0"):
            continue
        if git_text(repo, "cat-file", "-t", new_oid, check=False) != "commit":
            continue
        if not _commit_reachable_from_existing_branch_or_head(repo, new_oid):
            return True
    return False


def _ssh_transport_is_push(hook_args: Sequence[str]) -> bool:
    if not hook_args:
        raise RepositoryChangeWindowError(
            "SSH transport requires Git's remote service argument"
        )
    try:
        service_args = shlex.split(hook_args[-1])
    except ValueError as exc:
        raise RepositoryChangeWindowError(
            "SSH transport remote service argument is malformed"
        ) from exc
    if not service_args:
        raise RepositoryChangeWindowError(
            "SSH transport remote service argument is empty"
        )
    service_token = Path(service_args[0]).name
    try:
        service = GitSshService(service_token)
    except ValueError as exc:
        raise RepositoryChangeWindowError(
            f"unsupported Git SSH service `{service_token}`"
        ) from exc
    return service is GitSshService.RECEIVE_PACK


def run_git_hook_provider(
    *,
    repo_path: str | Path,
    runtime_root: Path,
    event: str,
    hook_args: Sequence[str] = (),
    hook_stdin: bytes = b"",
    now: datetime | None = None,
) -> dict[str, Any]:
    if event == SSH_TRANSPORT_EVENT and not _ssh_transport_is_push(hook_args):
        return {
            "ok": True,
            "schema_version": "repository_change_window_hook_result_v1",
            "status": "allowed",
            "event": event,
            "exit_code": 0,
            "guarded_change": False,
            "previous_hook_invoked": False,
            "policy_evaluated": False,
            "reason": "read_only_ssh_transport",
        }

    # Hook admission needs repository identity, not a checkout snapshot. In
    # particular, reference hooks can run before HEAD exists in a new worktree.
    root = repository_root(repo_path)
    common_dir = repository_common_dir(root)
    state = _read_state(_state_path(common_dir))
    enforcement = _state_enforcement_level(state)
    if event not in _managed_events(enforcement):
        raise RepositoryChangeWindowError(
            f"hook event `{event}` is not managed by enforcement level `{enforcement.value}`"
        )
    checks = _provider_checks(
        repo=root,
        common_dir=common_dir,
        state=state,
        include_runtime=False,
    )
    if not all(bool(item["ok"]) for item in checks):
        return {
            "ok": False,
            "schema_version": "repository_change_window_hook_result_v1",
            "status": "provider_drift",
            "event": event,
            "enforcement_level": enforcement.value,
            "exit_code": 1,
            "checks": checks,
        }
    if event == REFERENCE_GUARD_HOOK_NAME:
        guarded_change = _reference_transaction_introduces_commit(
            repo=root,
            hook_args=hook_args,
            hook_stdin=hook_stdin,
        )
    else:
        guarded_change = True
    decision = (
        evaluate_policy(ChangeWindowPolicy.from_dict(state["policy"]), now=now)
        if guarded_change else None
    )
    if decision is not None and not decision["allowed"]:
        try:
            ledger_record: dict[str, Any] = record_pending_change(
                runtime_root=runtime_root,
                repo_path=root,
                decision=decision,
                source=f"git_hook:{event}",
                execute=True,
                now=now,
            )
        except (OSError, ValueError) as exc:
            ledger_record = {
                "ok": False,
                "status": "record_failed",
                "error": str(exc),
            }
        return {
            "ok": False,
            "schema_version": "repository_change_window_hook_result_v1",
            "status": "blocked_by_policy",
            "event": event,
            "enforcement_level": enforcement.value,
            "guarded_change": guarded_change,
            "exit_code": 1,
            "decision": decision,
            "pending_change": ledger_record,
        }

    previous_root = Path(str(state.get("previous_hook_root") or ""))
    previous_hook = previous_root / event
    previous_exit_code = 0
    previous_hook_invoked = (
        event != SSH_TRANSPORT_EVENT
        and previous_hook.is_file()
        and os.access(previous_hook, os.X_OK)
    )
    if previous_hook_invoked:
        result = subprocess.run(
            [str(previous_hook), *hook_args],
            cwd=root,
            input=hook_stdin,
            check=False,
        )
        previous_exit_code = result.returncode
    return {
        "ok": previous_exit_code == 0,
        "schema_version": "repository_change_window_hook_result_v1",
        "status": "allowed" if previous_exit_code == 0 else "previous_hook_failed",
        "event": event,
        "exit_code": previous_exit_code,
        "decision": decision,
        "policy_evaluated": guarded_change,
        "enforcement_level": enforcement.value,
        "guarded_change": guarded_change,
        "previous_hook_invoked": previous_hook_invoked,
    }
