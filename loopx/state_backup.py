from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import re
import tarfile
import tempfile
from typing import Any, Protocol

from . import __version__
from .paths import select_default_runtime_root
from .control_plane.effect_runtime import effect_runtime_result


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
    configuration_source_registry: Path,
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
    # Import must bind the original registry bytes, even when its caller-owned
    # route lives outside .loopx; configuration projection is not that source.
    add("configuration_source_registry", configuration_source_registry, "configuration/registry.source.json")
    add("project_loopx", project / ".loopx", "project/.loopx")
    add("project_codex_goals", project / ".codex" / "goals", "project/.codex/goals")
    add("project_claude_goals", project / ".claude" / "goals", "project/.claude/goals")
    add("project_local_goals", project / ".local" / "goals", "project/.local/goals")

    global_registry = runtime_root / "registry.global.json"
    # Current-project scope still owns its registered routes, including files
    # outside the conventional Goal directories. Keep cross-project discovery
    # on the global registry; configuration capture uses the same source below.
    discovery_registry = (
        global_registry if include_registry_projects else configuration_source_registry
    )
    registry_goal_count = 0
    registry_project_roots: set[str] = set()
    reachable_project_roots: set[str] = set()
    registry_active_state_count = 0
    registry_active_state_included_count = 0
    registry_source_registry_count = 0
    registry_source_registry_included_count = 0
    if include_registry_projects or discovery_registry.exists():
        try:
            goals = _registry_goals(discovery_registry)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            goals = []
            warnings.append(f"could not discover registry projects from {discovery_registry}: {exc}")
        if not include_registry_projects:
            goals = [
                goal for goal in goals
                if not str(goal.get("repo") or "").strip()
                or _resolved(Path(str(goal["repo"]).strip())) == project
            ]
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
        item_stats = item.get("stats")
        if not isinstance(item_stats, dict):
            item_stats = {}
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
    registry_path: Path | None = None,
) -> dict[str, Any]:
    resolved_project = _resolved(Path(project))
    resolved_runtime_root = _resolved(Path(runtime_root).expanduser() if runtime_root else select_default_runtime_root())
    resolved_output_dir = _resolved(Path(output_dir).expanduser() if output_dir else resolved_runtime_root / "backups")
    resolved_backup_id = backup_id or _utc_timestamp()
    archive_path = resolved_output_dir / f"loopx-state-{resolved_backup_id}.tar.gz"
    manifest_path = resolved_output_dir / f"loopx-state-{resolved_backup_id}.manifest.json"
    configuration_source_registry = registry_path or (
        resolved_runtime_root / "registry.global.json" if include_registry_projects
        else resolved_project / ".loopx/registry.json"
    )
    targets, missing, warnings, registry_discovery = _discover_targets(
        project=resolved_project,
        runtime_root=resolved_runtime_root,
        output_dir=resolved_output_dir,
        include_automations=include_automations,
        include_skills=include_skills,
        include_registry_projects=include_registry_projects,
        configuration_source_registry=configuration_source_registry,
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
        "configuration_source_registry": str(configuration_source_registry),
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


class _BackupReadStream(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _BackupMemberReader:
    """Witness the stream tarfile copies, without rereading a changing source."""

    def __init__(self, source: _BackupReadStream) -> None:
        self.source = source
        self.digest = hashlib.sha256()
        self.size = 0

    def read(self, size: int = -1, /) -> bytes:
        data = self.source.read(size)
        self.digest.update(data)
        self.size += len(data)
        return data


class _BackupTarFile(tarfile.TarFile):
    """Retain tarfile's link/metadata behavior and witness every copied member."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.file_members: list[dict[str, Any]] = []
        super().__init__(*args, **kwargs)

    def addfile(self, tarinfo: tarfile.TarInfo, fileobj: _BackupReadStream | None = None) -> None:
        # The manifest contains this list; the whole-archive digest covers it.
        if not tarinfo.isfile() or tarinfo.name == "manifest.json":
            super().addfile(tarinfo, fileobj)
            return
        if fileobj is None:
            raise ValueError("backup regular member requires its copied byte stream")
        reader = _BackupMemberReader(fileobj)
        super().addfile(tarinfo, reader)
        self.file_members.append({"archive_path": tarinfo.name,
            "size_bytes": reader.size, "sha256": reader.digest.hexdigest()})


def _add_path_to_tar(
    tar: tarfile.TarFile, source: Path, archive_path: str, exclude_roots: list[Path],
    staging: Path, snapshots: dict[Path, tuple[Path, dict[str, Any]]],
) -> None:
    if _should_skip(source, exclude_roots):
        return
    if not source.is_symlink():
        for suffix in ("-wal", "-shm", "-journal"):
            if source.name.endswith(suffix) and _resolved(source.with_name(source.name[:-len(suffix)])) in snapshots:
                return
    if source.is_dir() and not source.is_symlink():
        tar.add(source, arcname=archive_path, recursive=False)
        for child in sorted(source.iterdir(), key=lambda item: item.name):
            _add_path_to_tar(tar, child, f"{archive_path}/{child.name}", exclude_roots, staging, snapshots)
        return
    if source.is_file() and not source.is_symlink():
        resolved = _resolved(source)
        snapshot = snapshots.get(resolved)
        if snapshot is None:
            with source.open("rb") as handle:
                is_sqlite = handle.read(16) == b"SQLite format 3\x00"
            if is_sqlite:
                snapshot_path = staging / f"sqlite-{len(snapshots)}.db"
                runtime = effect_runtime_result(
                    "coordination.sqlite_backup.snapshot",
                    {"source_path": str(resolved), "destination_path": str(snapshot_path)},
                    timeout=300.0, retry_safe=False,
                )
                snapshot = (snapshot_path, {
                    "source_path": str(source), "archive_paths": [],
                    "snapshot_sha256": _sha256_file(snapshot_path),
                    "snapshot_size_bytes": snapshot_path.stat().st_size,
                    "runtime_identity": runtime,
                })
                snapshots[resolved] = snapshot
        if snapshot is not None:
            snapshot[1]["archive_paths"].append(archive_path)
            tar.add(snapshot[0], arcname=archive_path, recursive=False)
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

    # A failed SQLite snapshot must not publish a partial archive or replace an
    # earlier successful backup. Keep staging private and outside discovery.
    with tempfile.TemporaryDirectory(prefix=".loopx-state-backup-", dir=output_dir) as temporary:
        staging = Path(temporary)
        staged_archive = staging / "archive.tar.gz"
        snapshots: dict[Path, tuple[Path, dict[str, Any]]] = {}
        with _BackupTarFile.open(staged_archive, "w:gz", dereference=False) as tar:
            for item in included:
                if not isinstance(item, dict):
                    continue
                source = Path(str(item.get("source_path") or "")).expanduser()
                archive_name = str(item.get("archive_path") or source.name)
                _add_path_to_tar(tar, source, archive_name, exclude_roots, staging, snapshots)
            from .capabilities.configuration_backup import (
                capture_configuration_backup,
                verify_configuration_backup,
            )
            configuration = capture_configuration_backup(
                registry_path=Path(payload["configuration_source_registry"]),
                runtime_root=Path(payload["runtime_root"]),
            )
            configuration_bytes = json.dumps(configuration, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo("configuration-backup.json")
            info.size, info.mode = len(configuration_bytes), 0o600
            tar.addfile(info, io.BytesIO(configuration_bytes))
            updated["execution"]["configuration_backup"] = verify_configuration_backup(configuration)
            updated["execution"]["sqlite_snapshots"] = [entry[1] for entry in snapshots.values()]
            # manifest.json is deliberately excluded: it contains this list.
            # The existing external archive checksum witnesses the whole tar.
            updated["execution"]["file_members"] = tar.file_members
            manifest_bytes = json.dumps(updated, ensure_ascii=False, indent=2).encode("utf-8")
            info = tarfile.TarInfo("manifest.json")
            info.size = len(manifest_bytes)
            info.mtime = int(datetime.now(timezone.utc).timestamp())
            tar.addfile(info, io.BytesIO(manifest_bytes))

        execution = dict(updated["execution"])
        execution["archive_sha256"] = _sha256_file(staged_archive)
        execution["archive_size_bytes"] = staged_archive.stat().st_size
        summary = updated.get("summary")
        if not isinstance(summary, dict):
            summary = {}
        logical_source_bytes = int(summary.get("logical_source_bytes") or 0)
        execution["archive_to_logical_ratio"] = (
            round(execution["archive_size_bytes"] / logical_source_bytes, 6)
            if logical_source_bytes else None
        )
        updated["execution"] = execution
        staged_manifest = staging / "manifest.json"
        staged_manifest.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        for path in (staged_archive, staged_manifest):
            path.chmod(0o600)
            with path.open("rb") as handle:
                os.fsync(handle.fileno())
        os.replace(staged_archive, archive_path)
        os.replace(staged_manifest, manifest_path)
    return updated


def render_state_backup_markdown(payload: dict[str, Any]) -> str:
    summary = payload.get("summary")
    if not isinstance(summary, dict):
        summary = {}
    total = summary.get("total_stats")
    if not isinstance(total, dict):
        total = {}
    category_stats = summary.get("category_stats")
    if not isinstance(category_stats, dict):
        category_stats = {}
    overlap = summary.get("contained_overlap_stats")
    if not isinstance(overlap, dict):
        overlap = {}
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
                stats = item.get("stats")
                if not isinstance(stats, dict):
                    stats = {}
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
