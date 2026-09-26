from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .paths import global_registry_path, select_default_runtime_root
from .registry import registry_goals


def _resolve_path(path: Path) -> Path:
    try:
        return path.expanduser().resolve()
    except OSError:
        return path.expanduser().absolute()


def _run_git(project: Path, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", str(project), *args],
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_root(project: Path) -> Path | None:
    result = _run_git(project, "rev-parse", "--show-toplevel")
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.strip()
    return _resolve_path(Path(value)) if value else None


def _git_common_dir(project: Path) -> Path | None:
    root = _git_root(project)
    if root is None:
        return None
    result = _run_git(root, "rev-parse", "--git-common-dir")
    if result is None or result.returncode != 0:
        return None
    value = result.stdout.strip()
    if not value:
        return None
    common = Path(value).expanduser()
    if not common.is_absolute():
        common = root / common
    return _resolve_path(common)


def _primary_repo_from_common_dir(common_dir: Path | None) -> Path | None:
    if common_dir is None:
        return None
    if common_dir.name == ".git":
        return _resolve_path(common_dir.parent)
    return None


def _default_global_registry_path() -> Path:
    runtime_env = os.environ.get("LOOPX_RUNTIME_ROOT")
    runtime_root = Path(runtime_env).expanduser() if runtime_env else select_default_runtime_root()
    return global_registry_path(runtime_root)


def _read_global_registry(path: Path) -> dict[str, Any] | None:
    try:
        with path.expanduser().open(encoding="utf-8") as f:
            payload = json.load(f)
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _source_repo_for_goal(goal: dict[str, Any]) -> Path | None:
    repo = goal.get("repo")
    if repo:
        return _resolve_path(Path(str(repo)))
    source_registry = goal.get("source_registry")
    if not source_registry:
        return None
    path = _resolve_path(Path(str(source_registry)))
    try:
        return path.parents[1]
    except IndexError:
        return None


def _same_path(left: Path | None, right: Path | None) -> bool:
    if left is None or right is None:
        return False
    return _resolve_path(left) == _resolve_path(right)


def resolve_canonical_project_alias(
    project: Path,
    *,
    goal_id: str | None = None,
    global_registry: Path | None = None,
) -> dict[str, Any]:
    """Map linked git worktrees back to the canonical LoopX source project."""

    resolved_project = _resolve_path(project)
    target_common_dir = _git_common_dir(resolved_project)
    global_path = _resolve_path(global_registry or _default_global_registry_path())
    payload = _read_global_registry(global_path)
    if not payload or target_common_dir is None:
        return {
            "applied": False,
            "kind": "none",
            "input_project": str(resolved_project),
            "global_registry": str(global_path),
            "git_common_dir": str(target_common_dir) if target_common_dir else None,
            "reason": "global registry or git common-dir unavailable",
        }

    primary_repo = _primary_repo_from_common_dir(target_common_dir)
    current_registry = _resolve_path(resolved_project / ".loopx" / "registry.json")
    candidates: list[dict[str, Any]] = []
    for goal in registry_goals(payload):
        candidate_goal_id = str(goal.get("id") or "")
        if goal_id and candidate_goal_id != goal_id:
            continue
        source_repo = _source_repo_for_goal(goal)
        if source_repo is None:
            continue
        source_common_dir = _git_common_dir(source_repo)
        if not _same_path(source_common_dir, target_common_dir):
            continue
        source_registry_value = goal.get("source_registry")
        source_registry = _resolve_path(Path(str(source_registry_value))) if source_registry_value else None
        candidates.append(
            {
                "goal_id": candidate_goal_id,
                "source_repo": str(source_repo),
                "source_registry": str(source_registry) if source_registry else None,
                "source_is_primary_repo": _same_path(source_repo, primary_repo),
                "source_is_current_project": _same_path(source_repo, resolved_project),
                "source_registry_is_current_project": _same_path(source_registry, current_registry),
            }
        )

    if not candidates:
        return {
            "applied": False,
            "kind": "none",
            "input_project": str(resolved_project),
            "global_registry": str(global_path),
            "git_common_dir": str(target_common_dir),
            "primary_repo": str(primary_repo) if primary_repo else None,
            "reason": "no global source_registry shares this git common-dir",
        }

    if any(item["source_registry_is_current_project"] for item in candidates):
        current_ids = [
            str(item["goal_id"])
            for item in candidates
            if item["source_registry_is_current_project"] and item.get("goal_id")
        ]
        non_current_primary = [
            item
            for item in candidates
            if item["source_is_primary_repo"] and not item["source_registry_is_current_project"]
        ]
        if not non_current_primary:
            return {
                "applied": False,
                "kind": "current_project_is_registered_source",
                "input_project": str(resolved_project),
                "global_registry": str(global_path),
                "git_common_dir": str(target_common_dir),
                "primary_repo": str(primary_repo) if primary_repo else None,
                "registered_goal_ids": current_ids,
                "reason": "current project already owns a registered source_registry route",
            }

    def rank(item: dict[str, Any]) -> tuple[int, int, int, str]:
        return (
            0 if item["source_is_primary_repo"] else 1,
            0 if not item["source_is_current_project"] else 1,
            0 if item.get("source_registry") else 1,
            str(item.get("source_registry") or item.get("source_repo") or ""),
        )

    selected = sorted(candidates, key=rank)[0]
    selected_repo = Path(str(selected["source_repo"]))
    if _same_path(selected_repo, resolved_project):
        return {
            "applied": False,
            "kind": "selected_current_project",
            "input_project": str(resolved_project),
            "global_registry": str(global_path),
            "git_common_dir": str(target_common_dir),
            "primary_repo": str(primary_repo) if primary_repo else None,
            "candidate_goal_ids": sorted({str(item["goal_id"]) for item in candidates if item.get("goal_id")}),
            "reason": "best global route is the current project",
        }

    return {
        "applied": True,
        "kind": "git_worktree_canonical_source_registry",
        "input_project": str(resolved_project),
        "canonical_project": str(selected_repo),
        "global_registry": str(global_path),
        "git_common_dir": str(target_common_dir),
        "primary_repo": str(primary_repo) if primary_repo else None,
        "source_registry": selected.get("source_registry"),
        "candidate_goal_ids": sorted({str(item["goal_id"]) for item in candidates if item.get("goal_id")}),
        "selected_goal_id": selected.get("goal_id"),
        "reason": "project is a git worktree; reusing canonical source_registry route",
    }
