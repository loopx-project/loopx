#!/usr/bin/env python3
"""Manually inspect Node dependencies; never install, remove or record ownership."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]
NODE_PROJECTS = {
    "node": ".",
    "dashboard": "apps/presentation/dashboard",
    "site": "apps/presentation/site",
    "desktop": "apps/desktop/loopx-control-plane",
}


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def _healthy_node_tree(project: Path, npm: str) -> bool:
    target = project / "node_modules"
    packages = _read(target / ".package-lock.json").get("packages")
    if not isinstance(packages, dict):
        return False
    for name in packages:
        path = project / name
        if not path.resolve().is_relative_to(target.resolve()):
            return False
        metadata = _read(path / "package.json")
        if not metadata:
            return False
        bins = metadata.get("bin") or {}
        if isinstance(bins, str):
            bins = {metadata.get("name", "").rsplit("/", 1)[-1]: bins}
        if not isinstance(bins, dict):
            return False
        for command, relative in bins.items():
            if not isinstance(relative, str) or not (path / relative).is_file():
                return False
            # npm puts executables in the nearest node_modules/.bin directory.
            modules = next((p for p in path.parents if p.name == "node_modules"), target)
            executable = modules / ".bin" / (command + (".cmd" if os.name == "nt" else ""))
            if not executable.is_file():
                return False
    return subprocess.run(
        [npm, "ls", "--all", "--include=dev", "--json"], cwd=project,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0


def _consistent_pure_js_tree(project: Path, target: Path, npm: str) -> bool:
    """Conservatively check a pure-JS install without modifying it."""
    lifecycle = ("preinstall", "install", "postinstall", "prepare")
    if any(key in (_read(project / "package.json").get("scripts") or {}) for key in lifecycle):
        return False
    wanted = _read(project / "package-lock.json")
    installed = _read(target / ".package-lock.json")
    if wanted.get("lockfileVersion") != 3 or installed.get("lockfileVersion") != 3:
        return False
    packages = installed.get("packages")
    expected = wanted.get("packages")
    if not isinstance(packages, dict) or not packages or not isinstance(expected, dict):
        return False
    for name, entry in packages.items():
        source = expected.get(name)
        if not isinstance(entry, dict) or not isinstance(source, dict):
            return False
        if entry.get("link") or any(
            entry.get(key) != source.get(key) for key in ("version", "resolved", "integrity")
        ):
            return False
        # Do not infer native ABI or lifecycle-script compatibility from a lock.
        path = project / name
        if not path.resolve().is_relative_to(target.resolve()) or path.is_symlink():
            return False
        metadata = _read(path / "package.json")
        if metadata.get("version") != source.get("version"):
            return False
        scripts = metadata.get("scripts") or {}
        if any(key in scripts for key in ("preinstall", "install", "postinstall")):
            return False
        bins = metadata.get("bin") or {}
        if isinstance(bins, str):
            bins = {metadata.get("name", ""): bins}
        if not isinstance(bins, dict):
            return False
        if any(not isinstance(value, str) or not (path / value).is_file() for value in bins.values()):
            return False
    if any(target.rglob("*.node")):
        return False
    return _healthy_node_tree(project, npm)


def inspect_node_dependencies(root: Path, scope: str) -> dict:
    root = root.resolve()
    project = root / NODE_PROJECTS[scope]
    target = project / "node_modules"
    result = {"scope": scope, "status": "needs_manual_check"}
    if not project.resolve().is_relative_to(root) or any(
        path.is_symlink() for path in (project, *project.parents, target)
    ):
        return {**result, "reason": "linked_environment"}
    if not all((project / name).is_file() for name in ("package.json", "package-lock.json")):
        return {**result, "reason": "missing_dependency_definitions"}
    if not target.is_dir():
        return {**result, "status": "missing", "reason": "no_local_node_modules"}
    npm = shutil.which("npm.cmd" if os.name == "nt" else "npm")
    if npm is None:
        return {**result, "reason": "npm_unavailable"}
    if not _consistent_pure_js_tree(project, target, npm):
        return {**result, "reason": "incomplete_changed_or_unverified_install"}
    return {**result, "status": "check_passed", "reason": "pure_js_lock_and_graph_consistent"}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", choices=NODE_PROJECTS, action="append", required=True)
    args = parser.parse_args()
    results = [inspect_node_dependencies(ROOT, scope) for scope in dict.fromkeys(args.scope)]
    for result in results:
        print(json.dumps(result))
    return 0 if all(result["status"] == "check_passed" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
