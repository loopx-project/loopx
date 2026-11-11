from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

from loopx import __version__

GLOBAL_SKILL_SCOPE = "global"
PROJECT_SKILL_SCOPE = "project"
PROJECT_SKILL_SCOPE_FILE = ".loopx-skill-scope"
PROJECT_SKILL_MANAGED_MARKER = ".loopx-managed-project-skill.json"
PROJECT_SKILL_MARKER_SCHEMA_VERSION = "loopx_managed_project_skill_v0"
PROJECT_SKILL_SURFACE_ROOTS = {
    "codex": Path(".agents") / "skills",
    "antigravity": Path(".agent") / "skills",
    "claude-code": Path(".claude") / "skills",
    "kiro-cli": Path(".kiro") / "skills",
    "opencode": Path(".opencode") / "skills",
    "pi": Path(".pi") / "skills",
}
PROJECT_SKILL_SURFACES = tuple(PROJECT_SKILL_SURFACE_ROOTS)
SKILL_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _normalize_skill_id(skill_id: str) -> str:
    normalized = str(skill_id or "").strip()
    if not SKILL_ID_RE.fullmatch(normalized):
        raise ValueError(
            "project skill id must use lowercase alphanumeric words separated by hyphens"
        )
    return normalized


def _normalize_surfaces(surfaces: Iterable[str] | None) -> tuple[str, ...]:
    requested = tuple(dict.fromkeys(surfaces or ("codex",)))
    invalid = sorted(set(requested) - set(PROJECT_SKILL_SURFACES))
    if invalid:
        raise ValueError(f"unsupported project skill surfaces: {','.join(invalid)}")
    if not requested:
        raise ValueError("at least one project skill surface is required")
    return requested


def canonical_project_skill_source(skill_id: str) -> Path:
    normalized = _normalize_skill_id(skill_id)
    # Share the active installation's resource owner with workflow-skills,
    # including wheel --target and frozen bundles. Never borrow a host copy.
    from loopx.workflow_skill_install import resolve_workflow_skill_source

    source = resolve_workflow_skill_source()
    if source.get("available"):
        return Path(source["skills_root"]) / normalized
    raise ValueError(str(source["reason"]))


def project_skill_target(
    project: str | Path,
    skill_id: str,
    surface: str,
) -> Path:
    normalized = _normalize_skill_id(skill_id)
    canonical_surface = _normalize_surfaces((surface,))[0]
    return (
        Path(project).expanduser().resolve()
        / PROJECT_SKILL_SURFACE_ROOTS[canonical_surface]
        / normalized
    )


def _iter_skill_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise ValueError(f"skill trees cannot contain symlinks: {path}")
        if path.is_file() and path.name != PROJECT_SKILL_MANAGED_MARKER:
            files.append(path)
    return files


def project_skill_digest(root: Path) -> str:
    if not root.is_dir():
        raise ValueError(f"project skill source is missing: {root}")
    digest = hashlib.sha256()
    for path in _iter_skill_files(root):
        relative = path.relative_to(root).as_posix()
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def discover_project_scoped_skill_ids(skills_root: Path) -> tuple[str, ...]:
    if not skills_root.is_dir():
        return ()
    result = []
    for candidate in sorted(skills_root.iterdir()):
        scope_file = candidate / PROJECT_SKILL_SCOPE_FILE
        if (
            candidate.is_dir()
            and scope_file.is_file()
            and scope_file.read_text(encoding="utf-8").strip()
            == PROJECT_SKILL_SCOPE
        ):
            result.append(candidate.name)
    return tuple(result)


def classify_host_skill_sources(skills_root: Path) -> dict[str, tuple[str, ...]]:
    """Split skill sources by what a fixed host install may deliver.

    ``global`` is the only scope a host install may materialize. A source with a
    ``project`` marker is installed explicitly per project, and a source with no
    marker is repo-only: it stays in the checkout, and no delivery path may copy
    it onto a host that did not deliberately adopt it. An unrecognized marker is
    a configuration error rather than a silent skip.
    """
    deliverable: list[str] = []
    project_scoped: list[str] = []
    repo_only: list[str] = []
    if not skills_root.is_dir():
        return {
            "deliverable_skill_ids": (),
            "project_skill_ids": (),
            "repo_only_skill_ids": (),
        }
    for candidate in sorted(skills_root.iterdir()):
        if not candidate.is_dir():
            continue
        scope_file = candidate / PROJECT_SKILL_SCOPE_FILE
        if not scope_file.is_file():
            repo_only.append(candidate.name)
            continue
        scope = scope_file.read_text(encoding="utf-8").strip()
        if scope == GLOBAL_SKILL_SCOPE:
            deliverable.append(candidate.name)
        elif scope == PROJECT_SKILL_SCOPE:
            project_scoped.append(candidate.name)
        else:
            raise ValueError(
                f"skill scope must be 'global' or 'project', got {scope!r}: {scope_file}"
            )
    return {
        "deliverable_skill_ids": tuple(deliverable),
        "project_skill_ids": tuple(project_scoped),
        "repo_only_skill_ids": tuple(repo_only),
    }


def _read_scope(source_root: Path) -> str:
    scope_path = source_root / PROJECT_SKILL_SCOPE_FILE
    if not scope_path.is_file():
        raise ValueError(f"project skill scope marker is missing: {scope_path}")
    scope = scope_path.read_text(encoding="utf-8").strip()
    # Scope declares default discovery, not exclusive installation eligibility.
    # Both release-declared scopes permit an explicit managed project copy.
    if scope not in (GLOBAL_SKILL_SCOPE, PROJECT_SKILL_SCOPE):
        raise ValueError(
            f"skill scope must be 'global' or 'project', got {scope!r}"
        )
    return scope


def _read_managed_marker(
    target: Path,
    *,
    skill_id: str,
    surface: str,
) -> dict[str, Any] | None:
    marker_path = target / PROJECT_SKILL_MANAGED_MARKER
    if not marker_path.is_file():
        return None
    try:
        payload = json.loads(marker_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid managed skill marker: {marker_path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError(f"invalid managed skill marker object: {marker_path}")
    if (
        payload.get("schema_version") != PROJECT_SKILL_MARKER_SCHEMA_VERSION
        or payload.get("skill_id") != skill_id
        or payload.get("surface") != surface
        or payload.get("delivery") != "project_managed_copy"
    ):
        raise ValueError(f"unrecognized managed skill marker: {marker_path}")
    return payload


def _assert_project_boundary(project_root: Path, target: Path) -> None:
    skills_root = target.parent
    existing_ancestor = skills_root
    while (
        existing_ancestor != project_root
        and not existing_ancestor.exists()
        and not existing_ancestor.is_symlink()
    ):
        existing_ancestor = existing_ancestor.parent
    if existing_ancestor.exists() or existing_ancestor.is_symlink():
        try:
            existing_ancestor.resolve().relative_to(project_root)
        except ValueError as exc:
            raise ValueError(
                f"project skill root escapes the project boundary: {skills_root}"
            ) from exc
    if target.is_symlink():
        raise ValueError(f"project skill target cannot be a symlink: {target}")


def _inspect_surface(
    *,
    project_root: Path,
    source_digest: str,
    skill_id: str,
    surface: str,
) -> dict[str, Any]:
    target = project_skill_target(project_root, skill_id, surface)
    _assert_project_boundary(project_root, target)
    status = "missing"
    managed = False
    installed_digest: str | None = None
    recorded_source_digest: str | None = None
    if target.exists():
        marker = _read_managed_marker(
            target,
            skill_id=skill_id,
            surface=surface,
        )
        if marker is None:
            status = "unmanaged"
        else:
            managed = True
            recorded_source_digest = str(marker.get("source_digest") or "") or None
            installed_digest = project_skill_digest(target)
            if installed_digest != recorded_source_digest:
                status = "locally_modified"
            elif installed_digest == source_digest:
                status = "current"
            else:
                status = "stale"
    return {
        "surface": surface,
        "target": str(target),
        "status": status,
        "managed": managed,
        "installed_digest": installed_digest,
        "recorded_source_digest": recorded_source_digest,
    }


def inspect_project_skill(
    project: str | Path,
    skill_id: str,
    *,
    surfaces: Iterable[str] | None = None,
    source_root: Path | None = None,
) -> dict[str, Any]:
    normalized_skill_id = _normalize_skill_id(skill_id)
    normalized_surfaces = _normalize_surfaces(surfaces)
    project_root = Path(project).expanduser().resolve()
    source = (
        source_root or canonical_project_skill_source(normalized_skill_id)
    ).resolve()
    _read_scope(source)
    source_digest = project_skill_digest(source)
    surface_status = [
        _inspect_surface(
            project_root=project_root,
            source_digest=source_digest,
            skill_id=normalized_skill_id,
            surface=surface,
        )
        for surface in normalized_surfaces
    ]
    distinct_statuses = {item["status"] for item in surface_status}
    aggregate_status = (
        str(surface_status[0]["status"])
        if len(distinct_statuses) == 1
        else "mixed"
    )
    packet: dict[str, Any] = {
        "schema_version": "loopx_project_skill_status_v0",
        "skill_id": normalized_skill_id,
        "delivery": "project_managed_copy",
        "project": str(project_root),
        "project_connected": (project_root / ".loopx" / "registry.json").is_file(),
        "source": str(source),
        "source_digest": source_digest,
        "status": aggregate_status,
        "surfaces": surface_status,
        "managed": all(bool(item["managed"]) for item in surface_status),
    }
    if len(surface_status) == 1:
        packet.update(
            {
                "surface": surface_status[0]["surface"],
                "target": surface_status[0]["target"],
                "installed_digest": surface_status[0]["installed_digest"],
                "recorded_source_digest": surface_status[0][
                    "recorded_source_digest"
                ],
            }
        )
    return packet


def install_project_skill(
    project: str | Path,
    skill_id: str,
    *,
    surfaces: Iterable[str] | None = None,
    execute: bool = False,
    source_root: Path | None = None,
) -> dict[str, Any]:
    status = inspect_project_skill(
        project,
        skill_id,
        surfaces=surfaces,
        source_root=source_root,
    )
    if not status["project_connected"]:
        raise ValueError(
            "project is not connected: expected .loopx/registry.json before skill install"
        )
    blocked = [
        item
        for item in status["surfaces"]
        if item["status"] in {"unmanaged", "locally_modified"}
    ]
    if blocked:
        details = ", ".join(
            f"{item['surface']}={item['status']}" for item in blocked
        )
        raise ValueError(f"refusing to overwrite project skill targets: {details}")

    changed_surfaces = [
        item for item in status["surfaces"] if item["status"] != "current"
    ]
    plan = {
        **status,
        "operation": "install_or_upgrade",
        "mode": "execute" if execute else "preview",
        "changed": bool(changed_surfaces),
        "changed_surfaces": [item["surface"] for item in changed_surfaces],
    }
    if not execute or not changed_surfaces:
        return plan

    project_root = Path(str(status["project"]))
    source = Path(str(status["source"]))
    staged: list[tuple[dict[str, Any], Path, Path]] = []
    committed: list[tuple[Path, Path | None]] = []
    try:
        for item in changed_surfaces:
            target = Path(str(item["target"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            _assert_project_boundary(project_root, target)
            stage_parent = Path(
                tempfile.mkdtemp(
                    prefix=f".{status['skill_id']}.stage.",
                    dir=target.parent,
                )
            )
            staged_target = stage_parent / str(status["skill_id"])
            shutil.copytree(source, staged_target)
            marker = {
                "schema_version": PROJECT_SKILL_MARKER_SCHEMA_VERSION,
                "skill_id": status["skill_id"],
                "surface": item["surface"],
                "delivery": "project_managed_copy",
                "loopx_version": __version__,
                "source_digest": status["source_digest"],
            }
            (staged_target / PROJECT_SKILL_MANAGED_MARKER).write_text(
                json.dumps(marker, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            if project_skill_digest(staged_target) != status["source_digest"]:
                raise ValueError("staged project skill digest does not match its source")
            staged.append((item, stage_parent, staged_target))

        for item, _, staged_target in staged:
            target = Path(str(item["target"]))
            backup = (
                target.parent
                / f".{status['skill_id']}.backup.{item['surface']}.{os.getpid()}"
            )
            if backup.exists():
                raise ValueError(f"project skill backup path already exists: {backup}")
            previous = backup if target.exists() else None
            if previous is not None:
                os.replace(target, previous)
            try:
                os.replace(staged_target, target)
            except Exception:
                if previous is not None and previous.exists() and not target.exists():
                    os.replace(previous, target)
                raise
            committed.append((target, previous))
    except Exception:
        for target, previous in reversed(committed):
            if target.exists():
                shutil.rmtree(target)
            if previous is not None and previous.exists():
                os.replace(previous, target)
        raise
    finally:
        for _, stage_parent, _ in staged:
            if stage_parent.exists():
                shutil.rmtree(stage_parent)

    try:
        applied = inspect_project_skill(
            project_root,
            str(status["skill_id"]),
            surfaces=[str(item["surface"]) for item in status["surfaces"]],
            source_root=source,
        )
        if any(item["status"] != "current" for item in applied["surfaces"]):
            raise ValueError("project skill install readback failed")
    except Exception:
        for target, previous in reversed(committed):
            if target.exists():
                shutil.rmtree(target)
            if previous is not None and previous.exists():
                os.replace(previous, target)
        raise
    for _, previous in committed:
        if previous is not None and previous.exists():
            shutil.rmtree(previous)
    return {
        **applied,
        "operation": "install_or_upgrade",
        "mode": "execute",
        "changed": True,
        "changed_surfaces": [item["surface"] for item in changed_surfaces],
    }


def uninstall_project_skill(
    project: str | Path,
    skill_id: str,
    *,
    surfaces: Iterable[str] | None = None,
    execute: bool = False,
    source_root: Path | None = None,
) -> dict[str, Any]:
    status = inspect_project_skill(
        project,
        skill_id,
        surfaces=surfaces,
        source_root=source_root,
    )
    blocked = [
        item
        for item in status["surfaces"]
        if item["status"] in {"unmanaged", "locally_modified"}
    ]
    if blocked:
        details = ", ".join(
            f"{item['surface']}={item['status']}" for item in blocked
        )
        raise ValueError(f"refusing to remove project skill targets: {details}")
    changed_surfaces = [
        item for item in status["surfaces"] if item["status"] in {"current", "stale"}
    ]
    plan = {
        **status,
        "operation": "uninstall",
        "mode": "execute" if execute else "preview",
        "changed": bool(changed_surfaces),
        "changed_surfaces": [item["surface"] for item in changed_surfaces],
    }
    if not execute or not changed_surfaces:
        return plan

    moved: list[tuple[Path, Path]] = []
    try:
        for item in changed_surfaces:
            target = Path(str(item["target"]))
            tombstone = (
                target.parent
                / f".{status['skill_id']}.remove.{item['surface']}.{os.getpid()}"
            )
            if tombstone.exists():
                raise ValueError(
                    f"project skill removal path already exists: {tombstone}"
                )
            os.replace(target, tombstone)
            moved.append((target, tombstone))
    except Exception:
        for target, tombstone in reversed(moved):
            if tombstone.exists() and not target.exists():
                os.replace(tombstone, target)
        raise
    try:
        applied = inspect_project_skill(
            status["project"],
            str(status["skill_id"]),
            surfaces=[str(item["surface"]) for item in status["surfaces"]],
            source_root=Path(str(status["source"])),
        )
        if any(item["status"] != "missing" for item in applied["surfaces"]):
            raise ValueError("project skill uninstall readback failed")
    except Exception:
        for target, tombstone in reversed(moved):
            if tombstone.exists() and not target.exists():
                os.replace(tombstone, target)
        raise
    for _, tombstone in moved:
        shutil.rmtree(tombstone)
    return {
        **applied,
        "operation": "uninstall",
        "mode": "execute",
        "changed": True,
        "changed_surfaces": [item["surface"] for item in changed_surfaces],
    }
