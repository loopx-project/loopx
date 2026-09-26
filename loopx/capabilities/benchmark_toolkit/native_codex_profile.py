"""Formal LoopX installation profile for isolated native Codex Goal runs.

Benchmark runners should not hand-copy LoopX skills or point Codex at an
arbitrary source checkout.  This module prepares an isolated local release by
calling LoopX's shipped installer, then verifies the installed CLI and skill
readback before the profile can be used by an app-server process.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ...skill_install_readback import (
    PACKAGED_HOST_SKILL_IDS,
    SKILL_INSTALL_READBACK_FILENAME,
    inspect_skill_install_readback,
)

NATIVE_CODEX_PROFILE_SCHEMA_VERSION = "loopx_native_codex_goal_profile_v0"
NATIVE_CODEX_GOAL_PROMPT_SCHEMA_VERSION = "loopx_native_codex_goal_prompt_v0"
NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS = (
    "loopx",
    *PACKAGED_HOST_SKILL_IDS,
)
_DEFAULT_GLOBAL_REGISTRY_TOKENS = (
    "$HOME/.loopx/registry.global.json",
    "$HOME/.codex/loopx/registry.global.json",  # Explicit legacy route before migration.
)
_SAFE_RELEASE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_SAFE_ENV_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INSTALL_ENV_PASSTHROUGH = (
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "PATH",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TZ",
)
_AGENT_SHELL_ENV_INCLUDE_ONLY = (
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LOGNAME",
    "PATH",
    "SHELL",
    "SSL_CERT_DIR",
    "SSL_CERT_FILE",
    "TERM",
    "TMPDIR",
    "TZ",
    "USER",
)


class NativeCodexProfileError(RuntimeError):
    """The isolated profile did not prove a formal LoopX installation."""


@dataclass(frozen=True)
class NativeCodexProfile:
    """Verified paths and public-safe identity for one isolated installation."""

    root: Path
    home: Path
    codex_home: Path
    skills_dir: Path
    bin_dir: Path
    cli_bin: Path
    release_root: Path
    source_revision: str
    source_clean: bool
    skills_digest: str
    required_skill_ids: tuple[str, ...]
    materialized_skill_ids: tuple[str, ...]


@dataclass(frozen=True)
class NativeCodexGoalPrompt:
    """A Goal body rendered by the verified release-snapshot CLI."""

    task_body: str
    task_body_sha256: str
    task_body_chars: int
    runtime_profile: str
    source_revision: str
    installed_cli_bound: bool
    runtime_registry_bound: bool


def _digest_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _resolved_executable(value: str | None) -> str:
    requested = value or sys.executable
    resolved = shutil.which(requested) if not os.path.isabs(requested) else requested
    if not resolved or not Path(resolved).is_file():
        raise NativeCodexProfileError("profile_python_executable_missing")
    return str(Path(resolved).resolve(strict=True))


def _normalized_ids(values: Sequence[str], *, field: str) -> tuple[str, ...]:
    normalized = tuple(
        sorted({str(value).strip() for value in values if str(value).strip()})
    )
    if not normalized:
        raise ValueError(f"{field} must be non-empty")
    return normalized


def _manifest_source_is_clean(source: Mapping[str, Any]) -> bool:
    if source.get("git_dirty") is False:
        return True
    return bool(
        source.get("git_dirty") is None
        and (
            source.get("revision_kind") == "archive_sha256"
            or (source.get("kind") == "github_archive" and source.get("archive_sha256"))
        )
    )


def _source_clean_preflight(source_root: Path) -> bool | None:
    """Return clean, dirty, or unproven without mutating the install target."""

    try:
        top_level = subprocess.run(
            ["git", "-C", str(source_root), "rev-parse", "--show-toplevel"],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
    except OSError:
        top_level = None
    if top_level is not None and top_level.returncode == 0:
        try:
            repository_root = Path(top_level.stdout.strip()).resolve(strict=True)
        except OSError:
            return None
        if repository_root != source_root:
            return None
        status = subprocess.run(
            ["git", "-C", str(source_root), "status", "--porcelain"],
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
        )
        if status.returncode != 0:
            return None
        return not bool(status.stdout.strip())

    try:
        release_manifest = json.loads(
            (source_root / "release.json").read_text(encoding="utf-8")
        )
    except (OSError, json.JSONDecodeError):
        return None
    release_source = (
        release_manifest.get("source")
        if isinstance(release_manifest, dict)
        and isinstance(release_manifest.get("source"), dict)
        else {}
    )
    return True if _manifest_source_is_clean(release_source) else None


def _profile_paths(profile_root: Path, release_id: str) -> dict[str, Path]:
    root = profile_root.expanduser().resolve()
    return {
        "root": root,
        "home": root / "home",
        "codex_home": root / "codex-home",
        "skills_dir": root / "codex-home" / "skills",
        "bin_dir": root / "bin",
        "cli_bin": root / "bin" / "loopx",
        "release_root": root / "releases" / release_id,
        "man_root": root / "man",
        "shell_profile": root / "home" / ".profile",
    }


def _formal_install_environment(
    *,
    paths: Mapping[str, Path],
    python_executable: str,
    release_id: str,
    base_env: Mapping[str, str] | None,
) -> dict[str, str]:
    source = os.environ if base_env is None else base_env
    env = {key: str(source[key]) for key in _INSTALL_ENV_PASSTHROUGH if source.get(key)}
    env.setdefault("PATH", os.defpath)
    env.update(
        {
            "HOME": str(paths["home"]),
            "SHELL": "/bin/sh",
            "CODEX_HOME": str(paths["codex_home"]),
            "LOOPX_PYTHON": python_executable,
            "LOOPX_PROMOTE_DEFAULT": "1",
            "LOOPX_INSTALL_CANARY": "0",
            "LOOPX_BIN_DIR": str(paths["bin_dir"]),
            "LOOPX_RELEASES_DIR": str(paths["release_root"].parent),
            "LOOPX_RELEASE_ID": release_id,
            "LOOPX_MAN_ROOT": str(paths["man_root"]),
            "LOOPX_MAN_DIR": str(paths["man_root"] / "man1"),
            "LOOPX_SHELL_PROFILE": str(paths["shell_profile"]),
            "LOOPX_SKILLS_DIR": str(paths["skills_dir"]),
            # The fixed installer already materializes the generated `$loopx`
            # entry skill.  Extra slash-command surfaces are irrelevant to a
            # non-interactive Goal worker and would mutate that tree after its
            # installer readback was written.
            "LOOPX_INSTALL_SLASH_COMMANDS": "0",
            "LOOPX_INSTALL_OPENCODE": "0",
            "LOOPX_INSTALL_CLAUDE": "0",
            "LOOPX_SKILL_DEDUPE_OTHER_ROOT": "0",
        }
    )
    return env


def native_codex_profile_environment(
    profile: NativeCodexProfile,
    *,
    base_env: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Build the minimal environment shared by profile CLI and Codex processes."""

    source = os.environ if base_env is None else base_env
    env = {key: str(source[key]) for key in _INSTALL_ENV_PASSTHROUGH if source.get(key)}
    inherited_path = env.get("PATH", os.defpath)
    env.update(
        {
            "HOME": str(profile.home),
            "CODEX_HOME": str(profile.codex_home),
            "PATH": f"{profile.bin_dir}{os.pathsep}{inherited_path}",
        }
    )
    return env


def native_codex_app_server_shell_policy_args(
    *,
    excluded_env_keys: Sequence[str],
) -> tuple[str, ...]:
    """Build a minimal Codex shell environment as defense in depth.

    Credential isolation must come from a separate OS authority boundary. This
    helper only prevents ordinary inheritance of explicitly named non-secret
    runtime values such as a runner-gateway sentinel.
    """

    normalized = _normalized_ids(
        excluded_env_keys,
        field="excluded_env_keys",
    )
    invalid = [key for key in normalized if not _SAFE_ENV_KEY.fullmatch(key)]
    if invalid:
        raise ValueError(
            "excluded_env_keys must contain safe environment variable names"
        )
    return (
        "-c",
        'shell_environment_policy.inherit="core"',
        "-c",
        "shell_environment_policy.ignore_default_excludes=false",
        "-c",
        f"shell_environment_policy.include_only={json.dumps(_AGENT_SHELL_ENV_INCLUDE_ONLY)}",
        "-c",
        f"shell_environment_policy.exclude={json.dumps(normalized)}",
    )


def render_native_codex_goal_prompt(
    profile: NativeCodexProfile,
    *,
    project_root: str | Path,
    goal_id: str,
    agent_id: str,
    registry_path: str | Path | None = None,
    runtime_root: str | Path | None = None,
    runtime_registry_path: str | Path | None = None,
    available_capabilities: Sequence[str] = ("shell", "filesystem_write"),
    base_env: Mapping[str, str] | None = None,
    timeout_sec: float = 30,
) -> NativeCodexGoalPrompt:
    """Render the real thin visible-Goal body with the installed profile CLI."""

    project = Path(project_root).expanduser().resolve(strict=True)
    if not project.is_dir():
        raise NativeCodexProfileError("goal_prompt_project_not_directory")
    if not goal_id.strip() or not agent_id.strip():
        raise ValueError("goal_id and agent_id must be non-empty")
    if timeout_sec <= 0:
        raise ValueError("timeout_sec must be positive")
    capabilities = _normalized_ids(
        available_capabilities,
        field="available_capabilities",
    )
    registry = (
        Path(registry_path).expanduser()
        if registry_path is not None
        else project / ".loopx" / "registry.json"
    ).resolve()
    runtime = (
        Path(runtime_root).expanduser()
        if runtime_root is not None
        else project / ".loopx" / "runtime"
    ).resolve()
    command = [
        str(profile.cli_bin),
        "--registry",
        str(registry),
        "--runtime-root",
        str(runtime),
        "--format",
        "json",
        "heartbeat-prompt",
        "--thin",
        "--goal-id",
        goal_id,
        "--agent-id",
        agent_id,
    ]
    for capability in capabilities:
        command.extend(("--available-capability", capability))
    command.extend(
        (
            "--runtime-profile",
            "codex_app_ssh_goal",
            "--cli-bin",
            str(profile.cli_bin),
        )
    )
    try:
        completed = subprocess.run(
            command,
            cwd=project,
            env=native_codex_profile_environment(profile, base_env=base_env),
            check=False,
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired as exc:
        raise NativeCodexProfileError("goal_prompt_cli_timeout") from exc
    if completed.returncode:
        raise NativeCodexProfileError(
            "goal_prompt_cli_failed:"
            f"returncode={completed.returncode}:stderr_sha256={_digest_text(completed.stderr)}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NativeCodexProfileError("goal_prompt_cli_invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise NativeCodexProfileError("goal_prompt_cli_not_ready")
    if payload.get("runtime_profile") != "codex_app_ssh_goal":
        raise NativeCodexProfileError("goal_prompt_runtime_profile_mismatch")
    interface_budget = payload.get("interface_budget")
    if (
        not isinstance(interface_budget, dict)
        or interface_budget.get("within_budget") is not True
    ):
        raise NativeCodexProfileError("goal_prompt_interface_budget_invalid")
    task_body = payload.get("task_body")
    if not isinstance(task_body, str) or not task_body.strip():
        raise NativeCodexProfileError("goal_prompt_task_body_missing")
    installed_cli = str(profile.cli_bin)
    installed_cli_bound = installed_cli in task_body
    if not installed_cli_bound:
        raise NativeCodexProfileError("goal_prompt_installed_cli_not_bound")

    runtime_registry_bound = runtime_registry_path is None
    if runtime_registry_path is not None:
        runtime_registry = str(Path(runtime_registry_path).expanduser().resolve())
        if any(token in task_body for token in _DEFAULT_GLOBAL_REGISTRY_TOKENS):
            for token in _DEFAULT_GLOBAL_REGISTRY_TOKENS:
                task_body = task_body.replace(token, runtime_registry)
            runtime_registry_bound = runtime_registry in task_body
        else:
            # Explicit runtime-root commands resolve their own global registry;
            # no separate --registry token is emitted or needs rewriting.
            runtime_registry_bound = str(runtime) in task_body
        if not runtime_registry_bound:
            raise NativeCodexProfileError("goal_prompt_runtime_registry_not_bound")

    return NativeCodexGoalPrompt(
        task_body=task_body,
        task_body_sha256=hashlib.sha256(task_body.encode("utf-8")).hexdigest(),
        task_body_chars=len(task_body),
        runtime_profile="codex_app_ssh_goal",
        source_revision=profile.source_revision,
        installed_cli_bound=installed_cli_bound,
        runtime_registry_bound=runtime_registry_bound,
    )


def _doctor_payload(
    *, cli_bin: Path, paths: Mapping[str, Path], env: Mapping[str, str]
) -> dict[str, Any]:
    doctor_env = dict(env)
    doctor_env["PATH"] = f"{paths['bin_dir']}{os.pathsep}{env.get('PATH', os.defpath)}"
    completed = subprocess.run(
        [str(cli_bin), "--format", "json", "doctor", "--agent-type", "codex-app-ssh"],
        cwd=paths["root"],
        env=doctor_env,
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode:
        raise NativeCodexProfileError(
            "profile_doctor_failed:"
            f"returncode={completed.returncode}:stderr_sha256={_digest_text(completed.stderr)}"
        )
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise NativeCodexProfileError("profile_doctor_invalid_json") from exc
    if not isinstance(payload, dict) or payload.get("ok") is not True:
        raise NativeCodexProfileError("profile_doctor_not_ready")
    if payload.get("agent_type") != "codex-app-ssh":
        raise NativeCodexProfileError("profile_doctor_host_surface_mismatch")
    if (payload.get("skill_delivery") or {}).get("status") != "ready":
        raise NativeCodexProfileError("profile_doctor_skill_delivery_not_ready")
    return payload


def inspect_native_codex_profile(
    profile_root: str | Path,
    *,
    source_root: str | Path | None = None,
    release_id: str = "native-goal-profile",
    required_skill_ids: Sequence[str] = NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS,
    base_env: Mapping[str, str] | None = None,
    require_clean_source: bool = True,
) -> NativeCodexProfile:
    """Verify one existing profile without repairing or reinstalling it."""

    if not _SAFE_RELEASE_ID.fullmatch(release_id):
        raise ValueError("release_id must be a safe directory name")
    required = _normalized_ids(required_skill_ids, field="required_skill_ids")
    paths = _profile_paths(Path(profile_root), release_id)
    release_root = paths["release_root"]
    cli_bin = paths["cli_bin"]
    if not release_root.is_dir() or not cli_bin.exists():
        raise NativeCodexProfileError("formal_install_outputs_missing")
    try:
        resolved_cli = cli_bin.resolve(strict=True)
        resolved_release = release_root.resolve(strict=True)
    except OSError as exc:
        raise NativeCodexProfileError("formal_install_outputs_unreadable") from exc
    if resolved_release not in resolved_cli.parents:
        raise NativeCodexProfileError("profile_cli_not_release_snapshot")

    env = _formal_install_environment(
        paths=paths,
        python_executable=_resolved_executable(None),
        release_id=release_id,
        base_env=base_env,
    )
    # The profile's CLI owns the loaded skills; the inspecting supervisor may
    # run a different LoopX version.
    try:
        version_readback = subprocess.run(
            [str(cli_bin), "--version"],
            cwd=paths["root"], env=env, check=False, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise NativeCodexProfileError("profile_cli_version_unavailable") from exc
    version_parts = version_readback.stdout.split()
    if version_readback.returncode or len(version_parts) != 2 or version_parts[0] != "loopx":
        raise NativeCodexProfileError("profile_cli_version_invalid")

    expected_source = Path(source_root).expanduser().resolve() if source_root else None
    skill_readback = inspect_skill_install_readback(
        skills_dir=paths["skills_dir"],
        required_skill_ids=required,
        source_root=expected_source,
        expected_loopx_version_override=version_parts[1],
    )
    if skill_readback.get("ready") is not True:
        status = re.sub(r"[^A-Za-z0-9_.:-]", "_", str(skill_readback.get("status")))
        raise NativeCodexProfileError(f"profile_skill_readback_not_ready:{status}")

    manifest_path = paths["skills_dir"] / SKILL_INSTALL_READBACK_FILENAME
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NativeCodexProfileError("profile_skill_manifest_unreadable") from exc
    skills = manifest.get("skills") if isinstance(manifest, dict) else None
    skills_digest = skills.get("digest") if isinstance(skills, dict) else None
    source_revision = skill_readback.get("source_revision")
    if not isinstance(skills_digest, str) or not isinstance(source_revision, str):
        raise NativeCodexProfileError("profile_identity_missing")

    doctor = _doctor_payload(cli_bin=cli_bin, paths=paths, env=env)
    release_manifest = (doctor.get("release_manifest") or {}).get("manifest") or {}
    release_source = release_manifest.get("source") or {}
    doctor_revision = release_source.get("git_commit") or release_source.get(
        "archive_sha256"
    )
    if doctor_revision and str(doctor_revision) != source_revision:
        raise NativeCodexProfileError("profile_doctor_source_revision_mismatch")
    skill_source = manifest.get("source") if isinstance(manifest, dict) else {}
    if not isinstance(skill_source, dict):
        skill_source = {}
    source_clean = _manifest_source_is_clean(
        skill_source
    ) and _manifest_source_is_clean(release_source)
    if require_clean_source and not source_clean:
        raise NativeCodexProfileError("profile_source_not_clean")

    return NativeCodexProfile(
        root=paths["root"],
        home=paths["home"],
        codex_home=paths["codex_home"],
        skills_dir=paths["skills_dir"],
        bin_dir=paths["bin_dir"],
        cli_bin=cli_bin,
        release_root=release_root,
        source_revision=source_revision,
        source_clean=source_clean,
        skills_digest=skills_digest,
        required_skill_ids=required,
        materialized_skill_ids=tuple(
            skill_readback.get("materialized_skill_ids") or ()
        ),
    )


def install_native_codex_profile(
    source_root: str | Path,
    profile_root: str | Path,
    *,
    python_executable: str | None = None,
    release_id: str = "native-goal-profile",
    required_skill_ids: Sequence[str] = NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS,
    base_env: Mapping[str, str] | None = None,
    require_clean_source: bool = True,
) -> NativeCodexProfile:
    """Create and verify an isolated profile through ``install-local.sh``.

    The target must be absent or empty.  The helper never repairs a partial
    profile because silently mixing installation revisions would invalidate a
    benchmark treatment.
    """

    if not _SAFE_RELEASE_ID.fullmatch(release_id):
        raise ValueError("release_id must be a safe directory name")
    source = Path(source_root).expanduser().resolve(strict=True)
    installer = source / "scripts" / "install-local.sh"
    if not installer.is_file():
        raise NativeCodexProfileError("formal_installer_missing")
    target = Path(profile_root).expanduser()
    if target.exists() and (not target.is_dir() or any(target.iterdir())):
        raise NativeCodexProfileError("profile_root_not_empty")
    if require_clean_source:
        source_clean = _source_clean_preflight(source)
        if source_clean is False:
            raise NativeCodexProfileError("profile_source_not_clean")
        if source_clean is None:
            raise NativeCodexProfileError("profile_source_cleanliness_unproven")
    target.mkdir(parents=True, exist_ok=True)
    paths = _profile_paths(target, release_id)
    paths["home"].mkdir(parents=True, exist_ok=True)
    env = _formal_install_environment(
        paths=paths,
        python_executable=_resolved_executable(python_executable),
        release_id=release_id,
        base_env=base_env,
    )
    completed = subprocess.run(
        [str(installer)],
        cwd=source,
        env=env,
        check=False,
        capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if completed.returncode:
        raise NativeCodexProfileError(
            "formal_installer_failed:"
            f"returncode={completed.returncode}:stderr_sha256={_digest_text(completed.stderr)}"
        )
    return inspect_native_codex_profile(
        target,
        source_root=source,
        release_id=release_id,
        required_skill_ids=required_skill_ids,
        base_env=base_env,
        require_clean_source=require_clean_source,
    )


def compact_native_codex_profile_receipt(profile: NativeCodexProfile) -> dict[str, Any]:
    """Return a path-free receipt suitable for benchmark result metadata."""

    return {
        "schema_version": NATIVE_CODEX_PROFILE_SCHEMA_VERSION,
        "host_surface": "codex-app-ssh",
        "install_mode": "formal_local_release",
        "cli_release_snapshot": True,
        "source_revision": profile.source_revision,
        "source_clean": profile.source_clean,
        "skills_digest": profile.skills_digest,
        "required_skill_ids": list(profile.required_skill_ids),
        "materialized_skill_ids": list(profile.materialized_skill_ids),
        "skill_readback_ready": True,
        "doctor_ready": True,
    }


def compact_native_codex_goal_prompt_receipt(
    prompt: NativeCodexGoalPrompt,
) -> dict[str, Any]:
    """Return prompt provenance without exposing the Goal body or local paths."""

    return {
        "schema_version": NATIVE_CODEX_GOAL_PROMPT_SCHEMA_VERSION,
        "prompt_source": "formal_profile_cli",
        "runtime_profile": prompt.runtime_profile,
        "source_revision": prompt.source_revision,
        "task_body_sha256": prompt.task_body_sha256,
        "task_body_chars": prompt.task_body_chars,
        "installed_cli_bound": prompt.installed_cli_bound,
        "runtime_registry_bound": prompt.runtime_registry_bound,
        "raw_task_body_recorded": False,
        "local_paths_recorded": False,
    }


__all__ = [
    "NATIVE_CODEX_GOAL_PROMPT_SCHEMA_VERSION",
    "NATIVE_CODEX_PROFILE_REQUIRED_SKILL_IDS",
    "NATIVE_CODEX_PROFILE_SCHEMA_VERSION",
    "NativeCodexGoalPrompt",
    "NativeCodexProfile",
    "NativeCodexProfileError",
    "compact_native_codex_goal_prompt_receipt",
    "compact_native_codex_profile_receipt",
    "inspect_native_codex_profile",
    "install_native_codex_profile",
    "native_codex_app_server_shell_policy_args",
    "native_codex_profile_environment",
    "render_native_codex_goal_prompt",
]
