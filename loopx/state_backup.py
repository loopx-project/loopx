from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
from typing import Any

from . import __version__
from .paths import select_default_runtime_root


STATE_BACKUP_SCHEMA_VERSION = "loopx_state_backup_v0"
ARCHIVE_SEGMENT_PATTERN = re.compile(r"[^a-zA-Z0-9._-]+")
STAT_FIELDS = ("paths", "files", "directories", "symlinks", "bytes")


def _utc_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _resolved(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


def _archive_segment(value: Any, *, fallback: str) -> str:
    raw = str(value or "").strip()
    compact = ARCHIVE_SEGMENT_PATTERN.sub("-", raw).strip("-._") or fallback
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:8]
    return f"{compact[:48]}-{digest}"


def _registry_goals(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    goals = payload.get("goals")
    if not isinstance(goals, list):
        return []
    return [goal for goal in goals if isinstance(goal, dict) and goal.get("id")]


def _should_skip(path: Path, exclude_roots: list[Path]) -> bool:
    resolved = _resolved(path)
    return any(resolved == root or _is_relative_to(resolved, root) for root in exclude_roots)


def _path_stats(path: Path, exclude_roots: list[Path]) -> tuple[dict[str, int], list[str]]:
    stats = {"paths": 0, "files": 0, "directories": 0, "symlinks": 0, "bytes": 0}
    warnings: list[str] = []

    def visit(item: Path) -> None:
        if _should_skip(item, exclude_roots):
            return
        try:
            stat = item.lstat()
        except OSError as exc:
            warnings.append(f"could not stat {item}: {exc}")
            return
        stats["paths"] += 1
        stats["bytes"] += int(stat.st_size)
        if item.is_symlink():
            stats["symlinks"] += 1
            return
        if item.is_dir():
            stats["directories"] += 1
            try:
                children = sorted(item.iterdir(), key=lambda child: child.name)
            except OSError as exc:
                warnings.append(f"could not list {item}: {exc}")
                return
            for child in children:
                visit(child)
            return
        stats["files"] += 1

    visit(path)
    return stats, warnings


def _target(
    *,
    key: str,
    source_path: Path,
    archive_path: str,
    exclude_roots: list[Path],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None, list[str]]:
    if not source_path.exists() and not source_path.is_symlink():
        return None, {"key": key, "source_path": str(source_path), "reason": "path does not exist"}, []
    stats, warnings = _path_stats(source_path, exclude_roots)
    return (
        {
            "key": key,
            "source_path": str(source_path),
            "archive_path": archive_path,
            "stats": stats,
        },
        None,
        warnings,
    )


def _discover_targets(
    *,
    project: Path,
    runtime_root: Path,
    output_dir: Path,
    include_automations: bool,
    include_skills: bool,
    include_registry_projects: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str], dict[str, Any]]:
    targets: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_sources: set[str] = set()
    exclude_roots = [_resolved(output_dir)]

    def add(key: str, source_path: Path, archive_path: str) -> None:
        source_key = str(_resolved(source_path))
        if source_key in seen_sources:
            return
        seen_sources.add(source_key)
        found, absent, target_warnings = _target(
            key=key,
            source_path=source_path.expanduser(),
            archive_path=archive_path,
            exclude_roots=exclude_roots,
        )
        if found is not None:
            targets.append(found)
        if absent is not None:
            missing.append(absent)
        warnings.extend(target_warnings)

    add("runtime_root", runtime_root, "runtime-root")
    add("project_loopx", project / ".loopx", "project/.loopx")
    add("project_codex_goals", project / ".codex" / "goals", "project/.codex/goals")
    add("project_claude_goals", project / ".claude" / "goals", "project/.claude/goals")
    add("project_local_goals", project / ".local" / "goals", "project/.local/goals")

    global_registry = runtime_root / "registry.global.json"
    registry_goal_count = 0
    registry_project_roots: set[str] = set()
    reachable_project_roots: set[str] = set()
    registry_active_state_count = 0
    registry_active_state_included_count = 0
    registry_source_registry_count = 0
    registry_source_registry_included_count = 0
    if include_registry_projects:
        try:
            goals = _registry_goals(global_registry)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            goals = []
            warnings.append(f"could not discover global registry projects from {global_registry}: {exc}")
        registry_goal_count = len(goals)
        for goal in goals:
            goal_id = str(goal.get("id") or "").strip()
            goal_segment = _archive_segment(goal_id, fallback="goal")
            repo_text = str(goal.get("repo") or "").strip()
            if not repo_text:
                missing.append(
                    {
                        "key": f"registry_project:{goal_id}",
                        "source_path": "",
                        "reason": "registry goal has no repo",
                    }
                )
                continue
            repo = _resolved(Path(repo_text))
            registry_project_roots.add(str(repo))
            project_segment = _archive_segment(repo, fallback="project")
            if not repo.exists():
                missing.append(
                    {
                        "key": f"registry_project:{goal_id}",
                        "source_path": str(repo),
                        "reason": "registry-declared project root does not exist",
                    }
                )
            else:
                reachable_project_roots.add(str(repo))
                add(
                    f"registry_project_loopx:{goal_id}",
                    repo / ".loopx",
                    f"registry-projects/{project_segment}/.loopx",
                )
                add(
                    f"registry_project_codex_goals:{goal_id}",
                    repo / ".codex" / "goals",
                    f"registry-projects/{project_segment}/.codex/goals",
                )
                add(
                    f"registry_project_claude_goals:{goal_id}",
                    repo / ".claude" / "goals",
                    f"registry-projects/{project_segment}/.claude/goals",
                )
                add(
                    f"registry_project_local_goals:{goal_id}",
                    repo / ".local" / "goals",
                    f"registry-projects/{project_segment}/.local/goals",
                )

            state_text = str(goal.get("state_file") or "").strip()
            if state_text:
                state_path = Path(state_text).expanduser()
                if not state_path.is_absolute():
                    state_path = repo / state_path
                add(
                    f"registry_active_state:{goal_id}",
                    state_path,
                    f"registry-goals/{goal_segment}/active-state/{state_path.name}",
                )
                registry_active_state_count += 1
                if state_path.exists() or state_path.is_symlink():
                    registry_active_state_included_count += 1
            else:
                missing.append(
                    {
                        "key": f"registry_active_state:{goal_id}",
                        "source_path": "",
                        "reason": "registry goal has no state_file",
                    }
                )

            source_registry_text = str(goal.get("source_registry") or "").strip()
            if source_registry_text:
                source_registry = Path(source_registry_text).expanduser()
                if not source_registry.is_absolute():
                    source_registry = repo / source_registry
                add(
                    f"registry_source_registry:{goal_id}",
                    source_registry,
                    f"registry-goals/{goal_segment}/source-registry/{source_registry.name}",
                )
                registry_source_registry_count += 1
                if source_registry.exists() or source_registry.is_symlink():
                    registry_source_registry_included_count += 1

    codex_home = _codex_home()
    if include_automations:
        add("codex_automations", codex_home / "automations", "codex/automations")
    if include_skills:
        skills_root = codex_home / "skills"
        skill_dirs = sorted(skills_root.glob("loopx-*")) if skills_root.exists() else []
        if skill_dirs:
            for skill_dir in skill_dirs:
                add(f"codex_skill:{skill_dir.name}", skill_dir, f"codex/skills/{skill_dir.name}")
        else:
            missing.append(
                {
                    "key": "codex_loopx_skills",
                    "source_path": str(skills_root / "loopx-*"),
                    "reason": "no loopx-* skills found",
                }
            )
    discovery = {
        "enabled": include_registry_projects,
        "global_registry": str(global_registry),
        "goal_count": registry_goal_count,
        "project_count": len(registry_project_roots),
        "reachable_project_count": len(reachable_project_roots),
        "missing_project_count": len(registry_project_roots - reachable_project_roots),
        "active_state_route_count": registry_active_state_count,
        "active_state_included_count": registry_active_state_included_count,
        "active_state_missing_count": (
            registry_active_state_count - registry_active_state_included_count
        ),
        "source_registry_route_count": registry_source_registry_count,
        "source_registry_included_count": registry_source_registry_included_count,
        "source_registry_missing_count": (
            registry_source_registry_count - registry_source_registry_included_count
        ),
    }
    return targets, missing, warnings, discovery


def _empty_stats() -> dict[str, int]:
    return {field: 0 for field in STAT_FIELDS}


def _sum_target_stats(targets: list[dict[str, Any]]) -> dict[str, int]:
    return {
        field: sum(int(item.get("stats", {}).get(field, 0)) for item in targets)
        for field in STAT_FIELDS
    }


def _target_category(key: str) -> str:
    if key == "runtime_root":
        return "runtime"
    if key.startswith("project_") or key.startswith("registry_project_"):
        return "project_state"
    if key.startswith("registry_active_state:"):
        return "active_state_routes"
    if key.startswith("registry_source_registry:"):
        return "source_registries"
    if key == "codex_automations":
        return "automations"
    if key.startswith("codex_skill:"):
        return "skills"
    return "other"


def _category_stats(targets: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    categories: dict[str, dict[str, int]] = {}
    for item in targets:
        category = _target_category(str(item.get("key") or ""))
        stats = categories.setdefault(category, {"target_count": 0, **_empty_stats()})
        stats["target_count"] += 1
        item_stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
        for field in STAT_FIELDS:
            stats[field] += int(item_stats.get(field, 0))
    return categories


def _contained_overlap_stats(
    targets: list[dict[str, Any]],
    *,
    logical_source_bytes: int,
) -> dict[str, int]:
    source_paths = [
        _resolved(Path(str(item.get("source_path") or "")))
        for item in targets
    ]
    contained: list[dict[str, Any]] = []
    for index, item in enumerate(targets):
        source = source_paths[index]
        if any(
            index != parent_index
            and source != parent
            and _is_relative_to(source, parent)
            for parent_index, parent in enumerate(source_paths)
        ):
            contained.append(item)
    overlap_bytes = _sum_target_stats(contained)["bytes"]
    return {
        "contained_target_count": len(contained),
        "logical_bytes": overlap_bytes,
        "unique_source_bytes_estimate": max(logical_source_bytes - overlap_bytes, 0),
    }


def build_state_backup_plan(
    *,
    project: Path | str = ".",
    runtime_root: Path | str | None = None,
    output_dir: Path | str | None = None,
    backup_id: str | None = None,
    include_automations: bool = True,
    include_skills: bool = True,
    include_registry_projects: bool = True,
) -> dict[str, Any]:
    resolved_project = _resolved(Path(project))
    resolved_runtime_root = _resolved(Path(runtime_root).expanduser() if runtime_root else select_default_runtime_root())
    resolved_output_dir = _resolved(Path(output_dir).expanduser() if output_dir else resolved_runtime_root / "backups")
    resolved_backup_id = backup_id or _utc_timestamp()
    archive_path = resolved_output_dir / f"loopx-state-{resolved_backup_id}.tar.gz"
    manifest_path = resolved_output_dir / f"loopx-state-{resolved_backup_id}.manifest.json"
    targets, missing, warnings, registry_discovery = _discover_targets(
        project=resolved_project,
        runtime_root=resolved_runtime_root,
        output_dir=resolved_output_dir,
        include_automations=include_automations,
        include_skills=include_skills,
        include_registry_projects=include_registry_projects,
    )
    total_stats = _sum_target_stats(targets)
    logical_source_bytes = total_stats["bytes"]
    return {
        "ok": True,
        "schema_version": STATE_BACKUP_SCHEMA_VERSION,
        "mode": "state_backup",
        "dry_run": True,
        "execute_requested": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backup_id": resolved_backup_id,
        "project": str(resolved_project),
        "runtime_root": str(resolved_runtime_root),
        "registry_discovery": registry_discovery,
        "codex_home": str(_codex_home()),
        "output_dir": str(resolved_output_dir),
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
        "included": targets,
        "missing": missing,
        "warnings": warnings,
        "summary": {
            "included_target_count": len(targets),
            "missing_target_count": len(missing),
            "warning_count": len(warnings),
            "total_stats": total_stats,
            "logical_source_bytes": logical_source_bytes,
            "category_stats": _category_stats(targets),
            "contained_overlap_stats": _contained_overlap_stats(
                targets,
                logical_source_bytes=logical_source_bytes,
            ),
        },
        "execution": None,
        "recommended_action": (
            "run `loopx backup-state --execute` to write the private local backup"
            if targets
            else "no backup targets found; check --project, --runtime-root, and CODEX_HOME"
        ),
    }


def _add_path_to_tar(tar: tarfile.TarFile, source: Path, archive_path: str, exclude_roots: list[Path]) -> None:
    if _should_skip(source, exclude_roots):
        return
    if source.is_dir() and not source.is_symlink():
        tar.add(source, arcname=archive_path, recursive=False)
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            _add_path_to_tar(tar, child, f"{archive_path}/{child.name}", exclude_roots)
        return
    tar.add(source, arcname=archive_path, recursive=False)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def execute_state_backup_plan(payload: dict[str, Any]) -> dict[str, Any]:
    archive_path = Path(str(payload["archive_path"])).expanduser()
    manifest_path = Path(str(payload["manifest_path"])).expanduser()
    output_dir = Path(str(payload["output_dir"])).expanduser()
    included = payload.get("included") if isinstance(payload.get("included"), list) else []
    if not included:
        updated = dict(payload)
        updated["ok"] = False
        updated["execute_requested"] = True
        updated["recommended_action"] = "no backup targets found; nothing was written"
        return updated

    output_dir.mkdir(parents=True, exist_ok=True)
    exclude_roots = [_resolved(output_dir)]
    updated = dict(payload)
    updated["dry_run"] = False
    updated["execute_requested"] = True
    updated["package_version"] = __version__
    updated["execution"] = {
        "archive_path": str(archive_path),
        "manifest_path": str(manifest_path),
        "archive_sha256": None,
        "archive_size_bytes": None,
    }
    updated["recommended_action"] = "backup written; keep the archive local and private"

    manifest_for_archive = dict(updated)
    manifest_bytes = json.dumps(manifest_for_archive, ensure_ascii=False, indent=2).encode("utf-8")
    with tarfile.open(archive_path, "w:gz", dereference=False) as tar:
        for item in included:
            if not isinstance(item, dict):
                continue
            source = Path(str(item.get("source_path") or "")).expanduser()
            archive_name = str(item.get("archive_path") or source.name)
            if source.exists() or source.is_symlink():
                _add_path_to_tar(tar, source, archive_name, exclude_roots)
        info = tarfile.TarInfo("manifest.json")
        info.size = len(manifest_bytes)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        tar.addfile(info, io.BytesIO(manifest_bytes))

    execution = dict(updated["execution"])
    execution["archive_sha256"] = _sha256_file(archive_path)
    execution["archive_size_bytes"] = archive_path.stat().st_size
    summary = updated.get("summary") if isinstance(updated.get("summary"), dict) else {}
    logical_source_bytes = int(summary.get("logical_source_bytes") or 0)
    if logical_source_bytes:
        ratio = execution["archive_size_bytes"] / logical_source_bytes
        execution["archive_to_logical_ratio"] = round(ratio, 6)
    else:
        execution["archive_to_logical_ratio"] = None
    updated["execution"] = execution
    manifest_path.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return updated


def render_state_backup_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    total = summary.get("total_stats") if isinstance(summary.get("total_stats"), dict) else {}
    category_stats = (
        summary.get("category_stats") if isinstance(summary.get("category_stats"), dict) else {}
    )
    overlap = (
        summary.get("contained_overlap_stats")
        if isinstance(summary.get("contained_overlap_stats"), dict)
        else {}
    )
    logical_source_bytes = summary.get("logical_source_bytes", total.get("bytes"))
    lines = [
        "# LoopX State Backup",
        "",
        f"- OK: `{payload.get('ok')}`",
        f"- Dry run: `{payload.get('dry_run')}`",
        f"- Backup id: `{payload.get('backup_id')}`",
        f"- Project: `{payload.get('project')}`",
        f"- Runtime root: `{payload.get('runtime_root')}`",
        f"- Output dir: `{payload.get('output_dir')}`",
        f"- Included targets: `{summary.get('included_target_count')}`",
        f"- Missing targets: `{summary.get('missing_target_count')}`",
        f"- Total paths: `{total.get('paths')}`",
        f"- Logical source bytes (before compression): `{logical_source_bytes}`",
        f"- Contained target overlap bytes: `{overlap.get('logical_bytes')}`",
        f"- Unique source bytes (estimate): `{overlap.get('unique_source_bytes_estimate')}`",
        f"- Recommended action: {payload.get('recommended_action')}",
    ]
    if category_stats:
        lines.extend(["", "## Logical Size By Category", ""])
        for category, stats in category_stats.items():
            if isinstance(stats, dict):
                lines.append(
                    f"- `{category}`: `{stats.get('bytes')}` bytes "
                    f"across `{stats.get('target_count')}` targets"
                )
    execution = payload.get("execution")
    if isinstance(execution, dict):
        lines.extend(
            [
                "",
                "## Written Files",
                "",
                f"- Archive: `{execution.get('archive_path')}`",
                f"- Manifest: `{execution.get('manifest_path')}`",
                f"- Archive sha256: `{execution.get('archive_sha256')}`",
                f"- Archive bytes: `{execution.get('archive_size_bytes')}`",
                f"- Archive/logical ratio: `{execution.get('archive_to_logical_ratio')}`",
            ]
        )
    included = payload.get("included") if isinstance(payload.get("included"), list) else []
    if included:
        lines.extend(["", "## Included", ""])
        for item in included:
            if isinstance(item, dict):
                stats = item.get("stats") if isinstance(item.get("stats"), dict) else {}
                lines.append(
                    f"- `{item.get('key')}` -> `{item.get('archive_path')}` "
                    f"({stats.get('paths')} paths)"
                )
    missing = payload.get("missing") if isinstance(payload.get("missing"), list) else []
    if missing:
        lines.extend(["", "## Missing", ""])
        for item in missing:
            if isinstance(item, dict):
                lines.append(f"- `{item.get('key')}`: {item.get('reason')}")
    warnings = payload.get("warnings") if isinstance(payload.get("warnings"), list) else []
    if warnings:
        lines.extend(["", "## Warnings", ""])
        for warning in warnings:
            lines.append(f"- {warning}")
    return "\n".join(lines) + "\n"
