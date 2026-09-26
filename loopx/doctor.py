from __future__ import annotations

from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, distribution
import json
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Any

from . import __version__, doctor_git
from .command_invocation import resolve_command_path
from .control_plane.runtime.promotion_readiness import (
    PROMOTION_READINESS_CLASSIFICATION,
    PROMOTION_READINESS_RUNTIME_INDEX,
)
from .control_plane.runtime.time import chronology_key
from .install_contract import NO_CLONE_INSTALL_URL
from .paths import DEFAULT_RUNTIME_ROOT, global_registry_path
from .python_install_owner import PythonInstallOwner, python_distribution_upgrade_command, resolve_python_install_owner
from .capabilities.project_skill_delivery import discover_project_scoped_skill_ids
from .registry_writability import probe_registry_write_path
from .release_manifest import load_release_manifest, release_version_tag
from .skill_install_readback import (
    ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
    configured_host_skills_dir,
    inspect_skill_install_readback,
    skill_install_doctor_checks,
)


PROMOTION_READINESS_CLASSIFICATIONS = {
    PROMOTION_READINESS_CLASSIFICATION,
}
PROMOTION_READINESS_FRESHNESS_HOURS = 24
INSTALL_FRESHNESS_STALE_HOURS = 168
RELEASE_ID_TIMESTAMP_RE = re.compile(r"^\d{8}T\d{6}Z$")
REQUIRED_INSTALLED_SKILL_PHRASES = {
    "loopx-project": (
        "--classification <PUBLIC_SAFE_PROGRESS_CLASSIFICATION>",
        "--delivery-batch-scale <ACTUAL_DELIVERY_BATCH_SCALE>",
        "--delivery-outcome <ACTUAL_DELIVERY_OUTCOME>",
    ),
    "loopx-pr-review": (
        "keeps ordinary queue discovery open-only",
        "explicit `--state merged|all`",
        "thin host adapter",
        "agent_response_contract.review_execution_contract",
        "pull_requests[review_action_kind!=null].review_plan",
        "completion_gate",
        "loopx-pr-merge",
    ),
    "loopx-pr-program": (
        "one `continuous_monitor` todo",
        "result_completeness.complete=true",
        "diff_snapshot.py",
        "Product requirements set priority",
        "Quiet monitor polls",
    ),
    "loopx-doc-registry": (
        "Use even when the user does not mention LoopX or doc registry",
        "use `.loopx/registry.json` as the project-local doc registry",
        "not a substitute for project-local authority registration",
    ),
    "loopx-benchmark": (
        "builtin `benchmark-toolkit` capability owns provider-neutral",
        "without a per-Goal enable switch",
        "Installing this skill does not grant runner",
        "For a generic library microbenchmark",
    ),
    "loopx-self-repair": (
        "Build a compact evidence packet",
        "loopx --format json diagnose --goal-id <goal-id>",
        "loopx --format json status --goal-id <goal-id> --limit 20",
        "registry-declared active state file",
        "references/repair-patterns.md",
        "Repair at the lowest durable layer",
    ),
}


def _powershell_literal(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def local_install_command(repo_root: Path, *, skip_skills: bool = False) -> str:
    if os.name == "nt":
        return (
            "pwsh -NoLogo -NoProfile -File "
            f"{_powershell_literal(repo_root / 'scripts' / 'install-windows.ps1')} "
            f"-Python {_powershell_literal(sys.executable)}"
            + (" -SkipSkills" if skip_skills else "")
        )
    command = str(repo_root / "scripts" / "install-local.sh")
    return f"LOOPX_INSTALL_SKILL=0 {command}" if skip_skills else command


def no_clone_upgrade_command(
    source_ref: Any = None,
    *,
    doctor_agent_type: str | None = None,
    skip_skills: bool = False,
) -> str:
    if os.name == "nt":
        repo_root = Path(__file__).resolve().parents[1]
        doctor_agent_arg = (
            f" --agent-type {_powershell_literal(doctor_agent_type)}"
            if doctor_agent_type
            else ""
        )
        return (
            f"{local_install_command(repo_root, skip_skills=skip_skills)}\n"
            f"loopx doctor{doctor_agent_arg}"
        )
    ref = str(source_ref or "").strip()
    installer = f"curl -fsSL {NO_CLONE_INSTALL_URL}"
    doctor_agent_arg = (
        f" --agent-type {shlex.quote(doctor_agent_type)}"
        if doctor_agent_type
        else ""
    )
    install_env: list[str] = []
    if ref and ref != "stable":
        install_env.append(f"LOOPX_REF={shlex.quote(ref)}")
    if skip_skills:
        install_env.append("LOOPX_INSTALL_SKILL=0")
    if install_env:
        installer = f"{installer} | env {' '.join(install_env)} bash"
    else:
        installer = f"{installer} | bash"
    return (
        f"{installer}\n"
        'export PATH="$HOME/.local/bin:$PATH"\n'
        f"loopx doctor{doctor_agent_arg}"
    )


def user_local_bin() -> Path:
    return Path.home() / ".local" / "bin"


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").expanduser()


def codex_skill_roots() -> tuple[Path, ...]:
    roots = (codex_home() / "skills", Path.home() / ".agents" / "skills")
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        key = os.path.normcase(os.path.abspath(root))
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return tuple(unique)


def command_release_root(command_realpath: Path | None) -> Path | None:
    if (
        command_realpath
        and command_realpath.name == "loopx"
        and command_realpath.parent.name == "scripts"
    ):
        return command_realpath.parent.parent
    return None


def current_script_invocation_path() -> Path | None:
    """Return the active LoopX executable when invoked through its console script."""
    if not sys.argv or not sys.argv[0]:
        return None
    path = Path(sys.argv[0]).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    try:
        path = path.resolve()
    except OSError:
        path = path.absolute()
    if path.exists() and path.name.lower() in {"loopx", "loopx.exe"}:
        return path
    return None


def python_distribution_install(module_path: Path) -> dict[str, Any]:
    """Identify a wheel/sdist install that owns the imported LoopX module."""

    try:
        installed = distribution("loopx")
    except PackageNotFoundError:
        return {"available": False}
    installer = (installed.read_text("INSTALLER") or "").strip() or "unknown"
    expected = module_path.resolve()
    owns_module = any(
        item.as_posix().endswith("loopx/doctor.py")
        and Path(item.locate()).resolve() == expected
        for item in installed.files or ()
    )
    if not owns_module:
        return {"available": False}
    owner = resolve_python_install_owner(default_installer=installer, prefix=Path(sys.prefix))
    return {
        "available": True,
        "kind": "python_distribution",
        "version": installed.version,
        "installer": owner.manager,
        "installer_environment": owner.environment,
        "root": str(Path(installed.locate_file("")).resolve()),
    }


def is_release_snapshot(root: Path | None) -> bool:
    return bool(root and "releases" in root.parts)


def command_root_summary(
    command_path: Path | None,
    command_realpath: Path | None,
    *,
    release_root: Path | None = None,
) -> dict[str, Any]:
    root = release_root or command_release_root(command_realpath)
    return {
        "command": str(command_path) if command_path else None,
        "realpath": str(command_realpath) if command_realpath else None,
        "root": str(root) if root else None,
        "release_id": root.name if is_release_snapshot(root) else None,
        "is_release_snapshot": is_release_snapshot(root),
    }


def parse_release_id_time(release_id: str | None) -> datetime | None:
    if not release_id or not RELEASE_ID_TIMESTAMP_RE.match(release_id):
        return None
    try:
        return datetime.strptime(release_id, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def short_revision(value: Any, *, length: int = 12) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text[:length] if len(text) > length else text


def build_install_freshness(
    *,
    command_path: Path | None,
    release_root: Path | None,
    repo_root: Path,
    skills: dict[str, dict[str, Any]],
    release_manifest: dict[str, Any] | None = None,
    comparison_source: dict[str, Any] | None = None,
    freshness_source: dict[str, Any] | None = None,
    require_installed_skills: bool = True,
    doctor_agent_type: str | None = None,
    python_distribution: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    release_id = release_root.name if is_release_snapshot(release_root) else None
    release_time = parse_release_id_time(release_id)
    age_hours: float | None = None
    if release_time:
        age_hours = round(max(0, (reference - release_time.astimezone(timezone.utc)).total_seconds()) / 3600, 2)

    skills_ready = bool(skills) and all(
        skill.get("exists")
        and skill.get("required_phrases")
        and not skill.get("route_conflict")
        for skill in skills.values()
    )
    distribution_install = (
        python_distribution
        if isinstance(python_distribution, dict) and python_distribution.get("available")
        else None
    )
    externally_managed_skills = skills_ready and any(
        skill.get("managed_externally") for skill in skills.values()
    )
    skill_problem = require_installed_skills and not skills_ready
    if command_path is None:
        status = "missing"
        reason = "loopx is not on PATH"
        requires_upgrade = True
    elif skill_problem:
        status = "repair_recommended"
        reason = "installed LoopX skills are missing or stale"
        requires_upgrade = True
    elif distribution_install:
        status = "python_distribution"
        reason = "LoopX is installed as a managed Python distribution"
        requires_upgrade = False
    elif release_id and age_hours is not None and age_hours > INSTALL_FRESHNESS_STALE_HOURS:
        status = "stale"
        reason = f"default release snapshot is older than {INSTALL_FRESHNESS_STALE_HOURS} hours"
        requires_upgrade = True
    elif release_id and age_hours is not None:
        status = "fresh"
        reason = "default release snapshot timestamp is within the freshness window"
        requires_upgrade = False
    elif is_release_snapshot(release_root):
        status = "unknown"
        reason = "default release snapshot has a non-timestamp release id"
        requires_upgrade = False
    else:
        status = "live_checkout"
        reason = "current command is not a timestamped release snapshot"
        requires_upgrade = False

    manifest = release_manifest if isinstance(release_manifest, dict) else {}
    manifest_body = manifest.get("manifest") if isinstance(manifest.get("manifest"), dict) else {}
    manifest_package = (
        manifest_body.get("package") if isinstance(manifest_body.get("package"), dict) else {}
    )
    manifest_package_version = manifest_package.get("version")
    manifest_package_version_tag = manifest_package.get("version_tag") or (
        release_version_tag(manifest_package_version)
        if isinstance(manifest_package_version, str)
        else None
    )
    current_version_tag = release_version_tag(__version__)
    manifest_package_version_matches_runtime = (
        manifest_package_version == __version__
        if isinstance(manifest_package_version, str) and manifest_package_version
        else None
    )
    manifest_source = (
        manifest_body.get("source") if isinstance(manifest_body.get("source"), dict) else {}
    )
    no_clone_command = no_clone_upgrade_command(
        manifest_source.get("ref"),
        doctor_agent_type=doctor_agent_type,
        skip_skills=externally_managed_skills,
    )
    doctor_agent_arg = (
        f" --agent-type {shlex.quote(doctor_agent_type)}"
        if doctor_agent_type
        else ""
    )
    contributor_upgrade_command = (
        f"{local_install_command(repo_root, skip_skills=externally_managed_skills)}\n"
        f"loopx doctor{doctor_agent_arg}"
    )
    distribution_owner = PythonInstallOwner(
        str(distribution_install.get("installer") or "unknown"),
        distribution_install.get("installer_environment"),
    ) if distribution_install else None
    upgrade_command = python_distribution_upgrade_command(
        owner=distribution_owner,
        python_executable=sys.executable,
        doctor_command=f"loopx doctor{doctor_agent_arg}",
    ) if distribution_owner else no_clone_command
    manifest_source_git_commit = manifest_source.get("git_commit")
    manifest_source_revision = (
        manifest_source_git_commit
        or manifest_source.get("git_ref")
        or manifest_source.get("ref")
    )
    comparison = comparison_source if isinstance(comparison_source, dict) else {}
    comparison_source_git_commit = comparison.get("git_commit")
    comparison_source_label = comparison.get("label") or "comparison_source"
    comparison_revision_relation = comparison.get("revision_relation")
    manifest_source_matches_comparison = (
        manifest_source_git_commit == comparison_source_git_commit
        if manifest_source_git_commit and comparison_source_git_commit
        else None
    )
    trusted = freshness_source if isinstance(freshness_source, dict) else {}
    freshness_source_git_commit = trusted.get("git_commit")
    freshness_source_label = trusted.get("label") or "trusted release source"
    freshness_revision_relation = trusted.get("revision_relation")
    manifest_source_matches_freshness_source = (
        manifest_source_git_commit == freshness_source_git_commit
        if manifest_source_git_commit and freshness_source_git_commit
        else None
    )
    source_commit_is_behind = (
        manifest_source_matches_freshness_source is False
        and freshness_revision_relation == doctor_git.GitRevisionRelation.INSTALLED_BEHIND
    )

    if (
        not distribution_install
        and release_id
        and source_commit_is_behind
        and command_path is not None
        and not skill_problem
    ):
        status = "stale"
        reason = f"release manifest source commit is behind {freshness_source_label} commit"
        requires_upgrade = True

    if (
        not distribution_install
        and manifest_package_version_matches_runtime is False
        and command_path is not None
        and not skill_problem
    ):
        status = "repair_recommended"
        reason = "release manifest package version differs from runtime package version"
        requires_upgrade = True

    manifest_skills = (
        manifest_body.get("skills") if isinstance(manifest_body.get("skills"), dict) else {}
    )
    return {
        "schema_version": "loopx_install_freshness_v0",
        "status": status,
        "requires_upgrade": requires_upgrade,
        "reason": reason,
        "current_version": __version__,
        "current_version_tag": current_version_tag,
        "stale_after_hours": INSTALL_FRESHNESS_STALE_HOURS,
        "release_id": release_id,
        "release_age_hours": age_hours,
        "upgrade_command": upgrade_command,
        "no_clone_upgrade_command": no_clone_command,
        "contributor_upgrade_command": contributor_upgrade_command,
        "doctor_after_upgrade": f"loopx doctor{doctor_agent_arg}",
        "installed_skills_required": require_installed_skills,
        "install_kind": (
            "python_distribution" if distribution_install else "release_or_checkout"
        ),
        "python_distribution_version": (
            distribution_install.get("version") if distribution_install else None
        ),
        "python_distribution_installer": (
            distribution_install.get("installer") if distribution_install else None
        ),
        "python_distribution_installer_environment": (
            distribution_install.get("installer_environment") if distribution_install else None
        ),
        "externally_managed_skills": externally_managed_skills,
        "release_manifest_available": manifest.get("available"),
        "release_manifest_path": manifest.get("path"),
        "release_manifest_reason": manifest.get("reason"),
        "manifest_package_version": manifest_package_version,
        "manifest_package_version_tag": manifest_package_version_tag,
        "manifest_package_version_matches_runtime": manifest_package_version_matches_runtime,
        "manifest_source_kind": manifest_source.get("kind"),
        "manifest_source_repo": manifest_source.get("repo"),
        "manifest_source_ref": manifest_source.get("ref"),
        "manifest_source_git_commit": manifest_source_git_commit,
        "manifest_source_git_commit_short": short_revision(manifest_source_git_commit),
        "manifest_source_git_ref": manifest_source.get("git_ref"),
        "manifest_source_git_dirty": manifest_source.get("git_dirty"),
        "manifest_source_revision": manifest_source_revision,
        "manifest_source_revision_short": short_revision(manifest_source_revision),
        "comparison_source_label": comparison_source_label if comparison else None,
        "comparison_source_root": comparison.get("root"),
        "comparison_source_git_commit": comparison_source_git_commit,
        "comparison_source_git_commit_short": short_revision(comparison_source_git_commit),
        "comparison_source_git_ref": comparison.get("git_ref"),
        "comparison_source_git_dirty": comparison.get("git_dirty"),
        "manifest_source_matches_comparison": manifest_source_matches_comparison,
        "manifest_source_comparison_relation": (
            comparison_revision_relation.value
            if isinstance(comparison_revision_relation, doctor_git.GitRevisionRelation)
            else comparison_revision_relation
        ),
        "freshness_source_label": freshness_source_label if trusted else None,
        "freshness_source_root": trusted.get("root"),
        "freshness_source_git_commit": freshness_source_git_commit,
        "freshness_source_git_commit_short": short_revision(freshness_source_git_commit),
        "freshness_source_git_ref": trusted.get("git_ref"),
        "manifest_source_matches_freshness_source": manifest_source_matches_freshness_source,
        "manifest_source_freshness_relation": (
            freshness_revision_relation.value
            if isinstance(freshness_revision_relation, doctor_git.GitRevisionRelation)
            else freshness_revision_relation
        ),
        "manifest_archive_sha256": manifest_source.get("archive_sha256"),
        "manifest_skills_digest": manifest_skills.get("digest"),
    }


def parse_event_time(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def add_promotion_readiness_freshness(
    event: dict[str, Any],
    *,
    now: datetime | None = None,
    freshness_hours: int = PROMOTION_READINESS_FRESHNESS_HOURS,
) -> dict[str, Any]:
    result = dict(event)
    result["freshness_window_hours"] = freshness_hours
    if not result.get("available"):
        result.update(
            {
                "freshness_status": "missing",
                "is_fresh": False,
                "requires_readiness_run": True,
                "age_seconds": None,
                "age_hours": None,
            }
        )
        return result

    reference = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    generated_at = parse_event_time(result.get("generated_at"))
    if generated_at is None:
        result.update(
            {
                "freshness_status": "unknown",
                "is_fresh": False,
                "requires_readiness_run": True,
                "age_seconds": None,
                "age_hours": None,
            }
        )
        return result

    age_seconds = max(0, int((reference - generated_at).total_seconds()))
    is_fresh = age_seconds <= freshness_hours * 3600
    result.update(
        {
            "freshness_status": "fresh" if is_fresh else "stale",
            "is_fresh": is_fresh,
            "requires_readiness_run": not is_fresh,
            "age_seconds": age_seconds,
            "age_hours": round(age_seconds / 3600, 2),
            "freshness_reference_time": reference.isoformat(),
        }
    )
    return result


def skill_has_required_phrases(skill_path: Path, phrases: tuple[str, ...]) -> bool:
    if not skill_path.exists():
        return False
    text = " ".join(skill_path.read_text(encoding="utf-8").split())
    return all(phrase in text for phrase in phrases)


def installed_skill_summary(skills_roots: tuple[Path, ...]) -> dict[str, dict[str, Any]]:
    if not skills_roots:
        raise ValueError("at least one Codex skill root is required")
    primary_root = skills_roots[0]
    summaries: dict[str, dict[str, Any]] = {}
    for skill_name, phrases in REQUIRED_INSTALLED_SKILL_PHRASES.items():
        candidates = [
            (root, root / skill_name / "SKILL.md")
            for root in skills_roots
            if (root / skill_name / "SKILL.md").exists()
        ]
        selected = candidates[0] if len(candidates) == 1 else None
        selected_root = selected[0] if selected else None
        skill_path = selected[1] if selected else primary_root / skill_name / "SKILL.md"
        route_conflict = len(candidates) > 1
        summaries[skill_name] = {
            "path": str(skill_path),
            "candidate_paths": [str(path) for _, path in candidates],
            "route_count": len(candidates),
            "route_conflict": route_conflict,
            "source_root": str(selected_root) if selected_root else None,
            "managed_externally": bool(selected_root and selected_root != primary_root),
            "exists": bool(candidates),
            "required_phrases": bool(
                selected and skill_has_required_phrases(skill_path, phrases)
            ),
        }
    return summaries


def installed_skill_check(
    check_id: str,
    *,
    actual_ok: bool,
    detail: str,
    applicable: bool,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "required": False,
        "ok": actual_ok if applicable else True,
        "applicable": applicable,
        "detail": (
            detail
            if applicable
            else "not applicable: skill delivery is owned by the selected host integration"
        ),
    }


def latest_promotion_readiness_event(runtime_root: Path, goal_id: str | None = None) -> dict[str, Any]:
    goals_dir = runtime_root / "goals"
    runtime_index = runtime_root / PROMOTION_READINESS_RUNTIME_INDEX
    if not goals_dir.exists() and not runtime_index.is_file():
        return {
            "available": False,
            "runtime_root": str(runtime_root),
            "reason": "promotion readiness ledger does not exist",
        }

    runtime_matches: list[dict[str, Any]] = []
    legacy_matches: list[dict[str, Any]] = []
    indexes: list[tuple[Path, str | None, str]] = []
    if runtime_index.is_file():
        indexes.append((runtime_index, None, "runtime_release_ledger"))
    if goals_dir.exists():
        index_glob = f"{goal_id}/runs/index.jsonl" if goal_id else "*/runs/index.jsonl"
        indexes.extend(
            (path, path.parent.parent.name, "goal_run_history_legacy")
            for path in goals_dir.glob(index_glob)
        )
    for index_path, current_goal_id, source in indexes:
        try:
            lines = index_path.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(item, dict):
                continue
            classification = str(item.get("classification") or "")
            if classification not in PROMOTION_READINESS_CLASSIFICATIONS:
                continue
            json_path = Path(str(item.get("json_path") or ""))
            markdown_path = Path(str(item.get("markdown_path") or ""))
            target_matches = (
                runtime_matches if source == "runtime_release_ledger" else legacy_matches
            )
            target_matches.append(
                {
                    "available": True,
                    "source": source,
                    "goal_id": str(item.get("goal_id") or current_goal_id or "") or None,
                    "generated_at": item.get("generated_at"),
                    "classification": classification,
                    "evidence_scope": item.get("evidence_scope") or (
                        "goal_run_history" if current_goal_id else "runtime_release"
                    ),
                    "dashboard_readiness": item.get("dashboard_readiness"),
                    "delivery_batch_scale": item.get("delivery_batch_scale"),
                    "delivery_outcome": item.get("delivery_outcome"),
                    "recommended_action": item.get("recommended_action"),
                    "json_exists": json_path.exists() if str(json_path) else False,
                    "markdown_exists": markdown_path.exists() if str(markdown_path) else False,
                }
            )
        if runtime_matches:
            break

    matches = runtime_matches or legacy_matches
    if not matches:
        return {
            "available": False,
            "runtime_root": str(runtime_root),
            "goal_id": goal_id,
            "reason": (
                f"no canary promotion readiness run found for `{goal_id}`"
                if goal_id
                else "no canary promotion readiness run found"
            ),
        }
    matches.sort(
        key=lambda item: chronology_key(item.get("generated_at")),
        reverse=True,
    )
    latest = matches[0]
    latest["runtime_root"] = str(runtime_root)
    return latest


def collect_doctor(
    *,
    deep: bool = False,
    agent_type: str | None = None,
    installation_only: bool = False,
) -> dict[str, Any]:
    if installation_only:
        from .release_candidate import collect_installation_doctor

        return collect_installation_doctor(deep=deep)
    from .control_plane.runtime.runtime_projection_route import (
        collect_runtime_projection_route_diagnostics,
    )
    from .control_plane.effect_runtime import collect_effect_runtime_readiness

    from .host_loop_activation import (
        agent_type_uses_host_managed_skills,
        normalize_agent_type,
    )

    canonical_agent_type = normalize_agent_type(agent_type) if agent_type else None
    host_managed_skill_delivery = bool(
        canonical_agent_type
        and agent_type_uses_host_managed_skills(canonical_agent_type)
    )
    installed_skills_required = not host_managed_skill_delivery
    host_skill_install_readback = (
        inspect_skill_install_readback(
            skills_dir=configured_host_skills_dir(os.environ),
            required_skill_ids=ARK_MANAGED_AGENT_REQUIRED_SKILL_IDS,
            source_root=Path(__file__).resolve().parents[1],
        )
        if canonical_agent_type == "ark-managed-agent"
        else None
    )
    loopx_path = resolve_command_path("loopx")
    invocation_path = current_script_invocation_path()
    loopx_canary_path = resolve_command_path("loopx-canary")
    command_path_primary = loopx_path or invocation_path
    canary_path = loopx_canary_path
    command_path = command_path_primary
    command_realpath = command_path.resolve() if command_path else None
    canary_realpath = canary_path.resolve() if canary_path else None
    loopx_canary_realpath = loopx_canary_path.resolve() if loopx_canary_path else None
    module_path = Path(__file__).resolve()
    python_distribution = python_distribution_install(module_path)
    package_dir = module_path.parent
    repo_root = package_dir.parent
    install_script = repo_root / "scripts" / (
        "install-windows.ps1" if os.name == "nt" else "install-local.sh"
    )
    wrapper_script = repo_root / "scripts" / (
        "loopx.ps1" if os.name == "nt" else "loopx"
    )
    active_release_root_text = os.environ.get("LOOPX_RELEASE_ROOT")
    release_root = (
        Path(active_release_root_text).expanduser().resolve()
        if active_release_root_text
        else command_release_root(command_realpath)
    )
    canary_root = command_release_root(canary_realpath)
    release_manifest = load_release_manifest(release_root)
    comparison_source = None
    if canary_realpath and command_realpath and canary_realpath != command_realpath:
        comparison_source = doctor_git.git_metadata_for_root(
            command_release_root(canary_realpath)
        )
        comparison_source["label"] = "loopx-canary"
    path_entries = os.environ.get("PATH", "").split(os.pathsep)
    local_bin = user_local_bin()
    skill_roots = codex_skill_roots()
    skills = installed_skill_summary(skill_roots)
    project_skill = skills["loopx-project"]
    skill_path = Path(str(project_skill["path"]))
    project_scoped_skill_ids = discover_project_scoped_skill_ids(
        repo_root / "skills"
    )
    globally_visible_project_skills = [
        skill_name
        for skill_name in project_scoped_skill_ids
        if any((root / skill_name).exists() for root in skill_roots)
    ]
    default_release = command_root_summary(
        command_path,
        command_realpath,
        release_root=release_root,
    )
    default_release["release_manifest_available"] = release_manifest.get("available")
    default_release["release_manifest_path"] = release_manifest.get("path")
    release_manifest_body = (
        release_manifest.get("manifest")
        if isinstance(release_manifest.get("manifest"), dict)
        else {}
    )
    release_manifest_source = (
        release_manifest_body.get("source")
        if isinstance(release_manifest_body.get("source"), dict)
        else {}
    )
    if comparison_source:
        comparison_root = comparison_source.get("root")
        comparison_source["revision_relation"] = doctor_git.git_revision_relation(
            Path(str(comparison_root)) if comparison_root else None,
            installed_commit=release_manifest_source.get("git_commit"),
            comparison_commit=comparison_source.get("git_commit"),
        )
    freshness_source = doctor_git.trusted_release_ref_for_root(
        Path(str(comparison_source.get("root")))
        if comparison_source and comparison_source.get("root")
        else None,
        repository=release_manifest_source.get("repo"),
        ref=release_manifest_source.get("ref"),
    )
    if freshness_source:
        freshness_source["revision_relation"] = doctor_git.git_revision_relation(
            Path(str(freshness_source.get("root"))),
            installed_commit=release_manifest_source.get("git_commit"),
            comparison_commit=freshness_source.get("git_commit"),
        )
    default_release["promotion_mode"] = release_manifest_source.get("promotion_mode")
    release_provenance = {
        "runtime_root": str(DEFAULT_RUNTIME_ROOT),
        "default_release": default_release,
        "live_canary": {
            **command_root_summary(canary_path, canary_realpath),
            "separate_from_default": bool(canary_realpath and command_realpath and canary_realpath != command_realpath),
        },
        "current_invocation": {
            "module_path": str(module_path),
            "repo_root": str(repo_root),
            "source": (
                "python_distribution"
                if python_distribution.get("available")
                else "release_snapshot"
                if is_release_snapshot(repo_root)
                else "live_checkout"
            ),
        },
        "promotion_readiness": add_promotion_readiness_freshness(
            latest_promotion_readiness_event(DEFAULT_RUNTIME_ROOT)
        ),
    }
    install_freshness = build_install_freshness(
        command_path=command_path,
        release_root=release_root,
        repo_root=repo_root,
        skills=skills,
        release_manifest=release_manifest,
        comparison_source=comparison_source,
        freshness_source=freshness_source,
        require_installed_skills=installed_skills_required,
        doctor_agent_type=canonical_agent_type,
        python_distribution=python_distribution,
    )
    externally_managed_skills = bool(
        install_freshness.get("externally_managed_skills")
    )
    external_skill_delivery = externally_managed_skills and all(
        skill.get("managed_externally") for skill in skills.values()
    )
    if installed_skills_required:
        skill_delivery_status = (
            "ready"
            if all(
                skill.get("exists") and skill.get("required_phrases")
                for skill in skills.values()
            )
            else "repair_recommended"
        )
    elif host_skill_install_readback:
        skill_delivery_status = str(host_skill_install_readback["status"])
    else:
        skill_delivery_status = "external_readback_required"
    skill_delivery = {
        "agent_type": canonical_agent_type,
        "owner": (
            (
                "external_skill_manager"
                if external_skill_delivery
                else "loopx_surface_installer"
            )
            if installed_skills_required
            else (
                "loopx_install_script"
                if canonical_agent_type == "ark-managed-agent"
                else "custom_agent_host"
            )
        ),
        "mode": "surface_managed" if installed_skills_required else "host_managed",
        "codex_skills_root_applicable": installed_skills_required
        and not external_skill_delivery,
        "installed_skills_required_for_freshness": installed_skills_required,
        "skill_roots": [str(root) for root in skill_roots],
        "status": skill_delivery_status,
        **(
            {"filesystem_readback": host_skill_install_readback}
            if host_skill_install_readback
            else {}
        ),
    }
    default_global_registry = global_registry_path(DEFAULT_RUNTIME_ROOT)
    global_registry_writability = probe_registry_write_path(default_global_registry, create_parent=True)
    runtime_projection_routes = (
        collect_runtime_projection_route_diagnostics(
            registry_path=default_global_registry,
            runtime_root=DEFAULT_RUNTIME_ROOT,
        )
        if default_global_registry.exists()
        else {
            "schema_version": "runtime_projection_route_diagnostics_v0",
            "registry": str(default_global_registry.resolve()),
            "runtime_root": str(DEFAULT_RUNTIME_ROOT.resolve()),
            "goal_filter": None,
            "activation_state_filter": None,
            "available": False,
            "goal_count": 0,
            "healthy": True,
            "counts": {},
            "items": [],
        }
    )
    from .capabilities.decision_context.freshness import (
        capture_host_diagnostics_detail,
        collect_capture_host_diagnostics,
    )

    decision_context_capture = collect_capture_host_diagnostics(DEFAULT_RUNTIME_ROOT)
    typescript_control_plane = collect_effect_runtime_readiness(deep=deep)
    typescript_runtime_required = True
    deep_validation = None
    if deep:
        from .release_candidate import collect_deep_install_checks

        invocation_root_text = os.environ.get("LOOPX_RELEASE_ROOT")
        invocation_root = (
            Path(invocation_root_text).expanduser().resolve()
            if invocation_root_text
            else release_root
        )
        deep_validation = collect_deep_install_checks(
            command_path=command_path,
            invocation_path=invocation_path,
            package_root=repo_root,
            invocation_root=invocation_root,
            distribution_root=python_distribution.get("root"),
        )
    checks = [
        {
            "id": "command_available",
            "required": True,
            "ok": command_path is not None,
            "detail": str(loopx_path)
            if loopx_path
            else (
                f"loopx was not found on PATH; using current invocation {invocation_path}"
                if invocation_path
                else "loopx was not found on PATH"
            ),
        },
        {
            "id": "command_on_path",
            "required": False,
            "ok": loopx_path is not None,
            "detail": str(loopx_path) if loopx_path else "loopx was not found on PATH",
        },
        {
            "id": "command_resolves",
            "required": True,
            "ok": bool(command_realpath and command_realpath.exists()),
            "detail": str(command_realpath) if command_realpath else "no command realpath",
        },
        {
            "id": "default_command_is_release_snapshot",
            "required": False,
            "ok": bool(release_root and "releases" in release_root.parts),
            "detail": str(release_root) if release_root else "default command is not a release snapshot",
        },
        {
            "id": "canary_command_on_path",
            "required": False,
            "ok": canary_path is not None,
            "detail": str(canary_path) if canary_path else "loopx-canary was not found on PATH",
        },
        {
            "id": "canary_separate_from_default",
            "required": False,
            "ok": bool(canary_realpath and command_realpath and canary_realpath != command_realpath),
            "detail": str(canary_realpath) if canary_realpath else "no canary realpath",
        },
        {
            "id": "module_importable",
            "required": True,
            "ok": module_path.exists(),
            "detail": str(module_path),
        },
        {
            "id": "install_script_exists",
            "required": False,
            "ok": install_script.exists(),
            "detail": str(install_script),
        },
        {
            "id": "wrapper_script_exists",
            "required": False,
            "ok": wrapper_script.exists(),
            "detail": str(wrapper_script),
        },
        {
            "id": "local_bin_on_path",
            "required": False,
            "ok": str(local_bin) in path_entries,
            "detail": str(local_bin),
        },
        installed_skill_check(
            "installed_skill_exists",
            actual_ok=bool(project_skill.get("exists")),
            detail=str(skill_path),
            applicable=installed_skills_required,
        ),
        installed_skill_check(
            "installed_skill_delivery_hints",
            actual_ok=bool(project_skill.get("required_phrases")),
            detail=str(skill_path),
            applicable=installed_skills_required,
        ),
        installed_skill_check(
            "installed_required_skills",
            actual_ok=all(skill.get("exists") for skill in skills.values()),
            detail=",".join(sorted(skills)),
            applicable=installed_skills_required,
        ),
        installed_skill_check(
            "installed_required_skill_routes",
            actual_ok=all(
                skill.get("required_phrases") and not skill.get("route_conflict")
                for skill in skills.values()
            ),
            detail=",".join(
                f"{name}=valid:{skill.get('required_phrases')};"
                f"routes:{skill.get('route_count')};path:{skill.get('path')}"
                for name, skill in sorted(skills.items())
            ),
            applicable=installed_skills_required,
        ),
        {
            "id": "project_scoped_skills_absent_globally",
            "required": False,
            "ok": not globally_visible_project_skills,
            "detail": (
                "none"
                if not globally_visible_project_skills
                else ",".join(globally_visible_project_skills)
            ),
        },
        *skill_install_doctor_checks(host_skill_install_readback),
        {
            "id": "global_registry_writable",
            "required": True,
            "ok": bool(global_registry_writability.get("ok")),
            "detail": str(default_global_registry)
            if global_registry_writability.get("ok")
            else str(global_registry_writability.get("error") or default_global_registry),
        },
        {
            "id": "runtime_projection_routes_healthy",
            "required": False,
            "ok": bool(runtime_projection_routes.get("healthy", True)),
            "detail": json.dumps(
                runtime_projection_routes.get("counts") or {},
                ensure_ascii=False,
                sort_keys=True,
            ),
        },
        {
            "id": "decision_context_capture_hosts_healthy",
            "required": False,
            "ok": bool(decision_context_capture["healthy"]),
            "detail": capture_host_diagnostics_detail(decision_context_capture),
        },
        {
            "id": "typescript_effect_runtime_ready",
            "required": typescript_runtime_required,
            "ok": bool(typescript_control_plane.get("ready")),
            "detail": str(typescript_control_plane.get("status")),
        },
    ]
    from .desktop_installation import desktop_installation_status

    desktop_installation = desktop_installation_status(release_manifest_source.get("git_commit"))
    if desktop_installation["apps"]:
        checks.append({
            "id": "desktop_app_runtime_pairing",
            "required": False,
            "ok": desktop_installation["status"] == "paired",
            "detail": desktop_installation["recommended_action"] or "App bundle and CLI source revisions match; running App not verified",
        })
    if deep_validation:
        checks.extend(deep_validation["checks"])
    payload = {
        "ok": all(check["ok"] for check in checks if check["required"]),
        "mode": "deep" if deep else "standard",
        "agent_type": canonical_agent_type,
        "python": {
            "executable": sys.executable,
            "version": sys.version.split()[0],
        },
        "path": {
            "loopx": str(command_path) if command_path else None,
            "loopx_realpath": str(command_realpath) if command_realpath else None,
            "loopx_on_path": str(loopx_path) if loopx_path else None,
            "current_invocation": str(invocation_path) if invocation_path else None,
            "loopx_canary": str(loopx_canary_path) if loopx_canary_path else None,
            "loopx_canary_realpath": str(loopx_canary_realpath) if loopx_canary_realpath else None,
            "user_local_bin": str(local_bin),
            "user_local_bin_on_path": str(local_bin) in path_entries,
        },
        "package": {
            "module_path": str(module_path),
            "repo_root": str(repo_root),
            "release_root": str(release_root) if release_root else None,
            "canary_root": str(canary_root) if canary_root else None,
            "install_script": str(install_script),
            "wrapper_script": str(wrapper_script),
            "release_manifest_path": release_manifest.get("path"),
            "install_kind": (
                "python_distribution"
                if python_distribution.get("available")
                else "release_snapshot"
                if release_root
                else "live_checkout"
            ),
            "python_distribution": python_distribution,
        },
        "release_manifest": release_manifest,
        "desktop_installation": desktop_installation,
        "release_provenance": release_provenance,
        "global_registry_writability": global_registry_writability,
        "runtime_projection_routes": runtime_projection_routes,
        "decision_context_capture": decision_context_capture,
        "typescript_control_plane": typescript_control_plane,
        "install_freshness": install_freshness,
        "upgrade_hint": install_freshness,
        "skill": {
            "path": str(skill_path),
            "exists": bool(project_skill.get("exists")),
            "delivery_hints": bool(project_skill.get("required_phrases")),
            "route_conflict": bool(project_skill.get("route_conflict")),
            "candidate_paths": list(project_skill.get("candidate_paths") or []),
        },
        "skill_delivery": skill_delivery,
        "skills": skills,
        "project_scoped_skill_ids": list(project_scoped_skill_ids),
        "globally_visible_project_skills": globally_visible_project_skills,
        "checks": checks,
        "fix": (
            "Set `LOOPX_SKILLS_DIR=<PROJECT_WORKSPACE>/.agents/skills` and rerun "
            f"`{local_install_command(repo_root)}`; then rerun doctor "
            "with the same environment. Filesystem readback proves materialization; "
            "the host must still report its runtime loaded-skill readback."
            if canonical_agent_type == "ark-managed-agent"
            else (
                "Do not infer custom-host skill delivery from `~/.codex/skills`; "
                "verify the host-managed loaded-skill readback from `loopx agent-onboard`."
            )
            if canonical_agent_type
            and agent_type_uses_host_managed_skills(canonical_agent_type)
            else (
                "Run `loopx workflow-skills --install`, then "
                "`loopx slash-commands --install`, and rerun doctor. Upgrade this "
                f"installation with `{shlex.quote(sys.executable)} -m pip install --upgrade loopx`."
                if python_distribution.get("available")
                else (
                    f"Run `{local_install_command(repo_root, skip_skills=externally_managed_skills)}` "
                    "and start a new shell. "
                    + (
                        f"Ensure `{local_bin}` is on the Windows user PATH."
                        if os.name == "nt"
                        else (
                            f"Or export PATH=\"{local_bin}:$PATH\". For no-clone repair, run "
                            f"`curl -fsSL {NO_CLONE_INSTALL_URL} | bash`."
                        )
                    )
                )
            )
        ),
    }
    if deep_validation:
        payload["release_candidate"] = deep_validation
    return payload


def render_doctor_markdown(payload: dict[str, Any]) -> str:
    release_provenance = payload.get("release_provenance") or {}
    default_release = release_provenance.get("default_release") or {}
    typescript_control_plane = (
        payload.get("typescript_control_plane")
        if isinstance(payload.get("typescript_control_plane"), dict)
        else {}
    )
    typescript_runtime_lifecycle = (
        typescript_control_plane.get("runtime_lifecycle")
        if isinstance(typescript_control_plane.get("runtime_lifecycle"), dict)
        else {}
    )
    lines = [
        "# LoopX Doctor",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- agent_type: `{payload.get('agent_type')}`",
        f"- loopx: `{(payload.get('path') or {}).get('loopx')}`",
        f"- loopx_realpath: `{(payload.get('path') or {}).get('loopx_realpath')}`",
        f"- loopx_canary: `{(payload.get('path') or {}).get('loopx_canary')}`",
        f"- loopx_canary_realpath: `{(payload.get('path') or {}).get('loopx_canary_realpath')}`",
        f"- release_root: `{(payload.get('package') or {}).get('release_root')}`",
        f"- canary_root: `{(payload.get('package') or {}).get('canary_root')}`",
        f"- default_promotion_mode: `{default_release.get('promotion_mode')}`",
        f"- installed_skill: `{(payload.get('skill') or {}).get('path')}`",
        f"- installed_skill_delivery_hints: `{(payload.get('skill') or {}).get('delivery_hints')}`",
        f"- installed_required_skills: `{','.join(sorted((payload.get('skills') or {}).keys()))}`",
        f"- skill_delivery_mode: `{(payload.get('skill_delivery') or {}).get('mode')}`",
        f"- skill_delivery_status: `{(payload.get('skill_delivery') or {}).get('status')}`",
        f"- global_registry_writable: `{(payload.get('global_registry_writability') or {}).get('ok')}`",
        f"- runtime_projection_routes_healthy: `{(payload.get('runtime_projection_routes') or {}).get('healthy')}`"
        f" (registry=`{(payload.get('runtime_projection_routes') or {}).get('registry')}`,"
        f" goals=`{(payload.get('runtime_projection_routes') or {}).get('goal_count')}`,"
        f" counts=`{json.dumps((payload.get('runtime_projection_routes') or {}).get('counts') or {}, sort_keys=True)}`)",
        f"- decision_context_capture_hosts_healthy: `{(payload.get('decision_context_capture') or {}).get('healthy')}`"
        f" (hosts=`{(payload.get('decision_context_capture') or {}).get('host_count')}`,"
        f" unhealthy=`{(payload.get('decision_context_capture') or {}).get('unhealthy_count')}`)",
        f"- user_local_bin_on_path: `{(payload.get('path') or {}).get('user_local_bin_on_path')}`",
        f"- python: `{(payload.get('python') or {}).get('executable')}`",
        f"- typescript_control_plane: `{typescript_control_plane.get('status')}`",
        f"- typescript_runtime_state: `{typescript_runtime_lifecycle.get('state')}`",
        f"- typescript_runtime_diagnostic: `{typescript_runtime_lifecycle.get('diagnostic_code')}`",
        "",
        "## Checks",
    ]
    for check in payload.get("checks") or []:
        required = "required" if check.get("required") else "optional"
        lines.append(f"- {check.get('id')} ({required}): `{check.get('ok')}` - {check.get('detail')}")
    provenance = payload.get("release_provenance") if isinstance(payload.get("release_provenance"), dict) else {}
    if provenance:
        default_release = provenance.get("default_release") if isinstance(provenance.get("default_release"), dict) else {}
        live_canary = provenance.get("live_canary") if isinstance(provenance.get("live_canary"), dict) else {}
        current = provenance.get("current_invocation") if isinstance(provenance.get("current_invocation"), dict) else {}
        readiness = (
            provenance.get("promotion_readiness")
            if isinstance(provenance.get("promotion_readiness"), dict)
            else {}
        )
        lines.extend(
            [
                "",
                "## Release Provenance",
                (
                    "- default_release: "
                    f"root=`{default_release.get('root')}`, "
                    f"release_id=`{default_release.get('release_id')}`, "
                    f"is_release_snapshot=`{default_release.get('is_release_snapshot')}`"
                ),
                (
                    "- live_canary: "
                    f"root=`{live_canary.get('root')}`, "
                    f"separate_from_default=`{live_canary.get('separate_from_default')}`"
                ),
                (
                    "- current_invocation: "
                    f"source=`{current.get('source')}`, "
                    f"repo_root=`{current.get('repo_root')}`"
                ),
                (
                    "- latest_promotion_readiness: "
                    f"available=`{readiness.get('available')}`, "
                    f"goal=`{readiness.get('goal_id')}`, "
                    f"generated_at=`{readiness.get('generated_at')}`, "
                    f"classification=`{readiness.get('classification')}`, "
                    f"outcome=`{readiness.get('delivery_outcome')}`, "
                    f"freshness=`{readiness.get('freshness_status')}`, "
                    f"age_hours=`{readiness.get('age_hours')}`, "
                    f"requires_readiness_run=`{readiness.get('requires_readiness_run')}`"
                ),
            ]
        )
    freshness = payload.get("install_freshness") if isinstance(payload.get("install_freshness"), dict) else {}
    if freshness:
        manifest_source_repo = freshness.get("manifest_source_repo") or freshness.get("manifest_source_kind")
        manifest_source_ref = (
            freshness.get("manifest_source_git_ref")
            or freshness.get("manifest_source_ref")
            or freshness.get("manifest_source_git_commit_short")
            or "n/a"
        )
        lines.extend(
            [
                "",
                "## Install Freshness",
                f"- schema_version: `{freshness.get('schema_version')}`",
                f"- status: `{freshness.get('status')}`",
                f"- install_kind: `{freshness.get('install_kind')}`",
                f"- requires_upgrade: `{freshness.get('requires_upgrade')}`",
                f"- current_version: `{freshness.get('current_version')}`",
                f"- current_version_tag: `{freshness.get('current_version_tag')}`",
                f"- python_distribution_version: `{freshness.get('python_distribution_version')}`",
                f"- python_distribution_installer: `{freshness.get('python_distribution_installer')}`",
                f"- manifest_package_version: `{freshness.get('manifest_package_version')}`",
                f"- manifest_package_version_tag: `{freshness.get('manifest_package_version_tag')}`",
                f"- manifest_package_version_matches_runtime: `{freshness.get('manifest_package_version_matches_runtime')}`",
                f"- release_id: `{freshness.get('release_id')}`",
                f"- release_age_hours: `{freshness.get('release_age_hours')}`",
                f"- reason: `{freshness.get('reason')}`",
                f"- release_manifest_available: `{freshness.get('release_manifest_available')}`",
                f"- manifest_source: `{manifest_source_repo}` @ `{manifest_source_ref}`",
                f"- manifest_source_git_commit: `{freshness.get('manifest_source_git_commit_short')}`",
                f"- manifest_source_git_dirty: `{freshness.get('manifest_source_git_dirty')}`",
                f"- comparison_source: `{freshness.get('comparison_source_label')}` @ `{freshness.get('comparison_source_git_commit_short')}`",
                f"- manifest_source_matches_comparison: `{freshness.get('manifest_source_matches_comparison')}`",
                f"- manifest_source_comparison_relation: `{freshness.get('manifest_source_comparison_relation')}`",
                f"- freshness_source: `{freshness.get('freshness_source_label')}` @ `{freshness.get('freshness_source_git_commit_short')}`",
                f"- manifest_source_matches_freshness_source: `{freshness.get('manifest_source_matches_freshness_source')}`",
                f"- manifest_source_freshness_relation: `{freshness.get('manifest_source_freshness_relation')}`",
                f"- manifest_archive_sha256: `{freshness.get('manifest_archive_sha256')}`",
                f"- manifest_skills_digest: `{freshness.get('manifest_skills_digest')}`",
                "- upgrade_command:",
                "```bash",
                str(freshness.get("upgrade_command") or ""),
                "```",
            ]
        )
    if typescript_control_plane:
        lines.extend(
            [
                "",
                "## TypeScript Control Plane",
                f"- status: `{typescript_control_plane.get('status')}`",
                f"- ready: `{typescript_control_plane.get('ready')}`",
                f"- required_for: `{','.join(typescript_control_plane.get('required_for') or [])}`",
                f"- default_cli_blocking: `{typescript_control_plane.get('default_cli_blocking')}`",
                f"- minimum_node_version: `{typescript_control_plane.get('minimum_node_version')}`",
                f"- detected_node_version: `{typescript_control_plane.get('detected_node_version')}`",
                f"- semantic_probe: `{typescript_control_plane.get('semantic_probe')}`",
            ]
        )
        runtime_identity = typescript_control_plane.get("runtime_identity")
        if isinstance(runtime_identity, dict):
            lines.append(
                "- runtime_identity: "
                f"node=`{runtime_identity.get('node_version')}`, "
                f"sqlite=`{runtime_identity.get('sqlite_version')}`, "
                "sqlite_authority_qualified="
                f"`{runtime_identity.get('sqlite_authority_qualified')}`"
            )
        recommended_action = typescript_control_plane.get("recommended_action")
        if recommended_action:
            lines.append(f"- recommended_action: {recommended_action}")
    restart = payload.get("effect_runtime_restart")
    if isinstance(restart, dict):
        previous = restart.get("previous_runtime_identity")
        previous_text = (
            f"Node {previous.get('node_version')} / SQLite "
            f"{previous.get('sqlite_version')}"
            if isinstance(previous, dict)
            else "no runtime was serving"
        )
        lines.extend(
            [
                "",
                "## Effect Runtime Restart",
                f"- status: `{restart.get('status')}`",
                f"- stopped_runtime: {previous_text}",
            ]
        )
    if not payload.get("ok"):
        lines.extend(["", "## Fix", str(payload.get("fix"))])
        writable = payload.get("global_registry_writability")
        if isinstance(writable, dict) and not writable.get("ok") and writable.get("recommended_action"):
            lines.append(str(writable.get("recommended_action")))
    return "\n".join(lines)
