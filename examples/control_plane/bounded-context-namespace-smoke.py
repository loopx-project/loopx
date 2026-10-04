#!/usr/bin/env python3
"""Guard repo-local control-plane code against legacy root/shim namespaces."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve()
REPO_ROOT = SCRIPT_PATH.parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
LEGACY_IMPORT = "loopx.projections"
LEGACY_PACKAGE_PREFIX = "loopx/projections/"
LEGACY_ROOT_MODULES = {
    "loopx/decision_scope.py": "loopx.control_plane.todos.decision_scope",
    "loopx/delivery_batch_scale.py": "loopx.control_plane.work_items.delivery_batch_scale",
    "loopx/delivery_outcome.py": "loopx.control_plane.work_items.delivery_outcome",
    "loopx/local_state_write_correctness.py": "loopx.control_plane.runtime.local_state_write_correctness",
    "loopx/scheduler_state.py": "loopx.control_plane.scheduler.state",
    "loopx/status_projection_cache.py": "loopx.control_plane.runtime.status_projection_cache",
    "loopx/task_lease.py": "loopx.control_plane.work_items.task_lease",
    "loopx/control_plane/status_agent_lane_projection.py": (
        "loopx.control_plane.status.agent_lane_projection"
    ),
    "loopx/control_plane/status_collection.py": "loopx.control_plane.status.collection",
    "loopx/control_plane/status_runtime_summaries.py": (
        "loopx.control_plane.status.runtime_summaries"
    ),
}
LEGACY_CAPABILITY_PACKAGES = {
    "loopx/capabilities/cross_runtime/": "loopx.control_plane.handoff.cross_runtime_impl_review",
    "loopx/capabilities/multi_agent/": "demo.multi_agent",
    "loopx/capabilities/review_batch/": "loopx.control_plane.handoff.review_batch",
}
CANONICAL_MODULES = {
    "loopx.control_plane.runtime.local_state_write_correctness",
    "loopx.control_plane.runtime.status_projection_cache",
    "loopx.control_plane.todos.decision_scope",
    "loopx.control_plane.scheduler.state",
    "loopx.control_plane.work_items.delivery_batch_scale",
    "loopx.control_plane.work_items.delivery_outcome",
    "loopx.control_plane.work_items.task_lease",
    "loopx.control_plane.status.agent_lane_projection",
    "loopx.control_plane.status.collection",
    "loopx.control_plane.status.runtime_summaries",
}
LEGACY_STATUS_IMPORTS = (
    "loopx.control_plane.status_agent_lane_projection",
    "loopx.control_plane.status_collection",
    "loopx.control_plane.status_runtime_summaries",
    "loopx/control_plane/status_agent_lane_projection.py",
    "loopx/control_plane/status_collection.py",
    "loopx/control_plane/status_runtime_summaries.py",
)
TEXT_SUFFIXES = {".py", ".md"}
SKIP_DIRS = {"__pycache__", ".git", ".pytest_cache"}


def _git_ls_files(*patterns: str) -> list[str] | None:
    completed = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files", *patterns],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if completed.returncode != 0:
        return None
    return [line.strip() for line in completed.stdout.splitlines() if line.strip()]


def _fallback_repo_files() -> list[str]:
    paths: list[str] = []
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(REPO_ROOT).parts):
            continue
        paths.append(path.relative_to(REPO_ROOT).as_posix())
    return sorted(paths)


def tracked_text_files() -> list[Path]:
    files = _git_ls_files("*.py", "*.md")
    if files is None:
        files = [
            path
            for path in _fallback_repo_files()
            if Path(path).suffix in TEXT_SUFFIXES
        ]
    return [
        REPO_ROOT / line.strip()
        for line in files
        if line.strip() and (REPO_ROOT / line.strip()).exists()
    ]


def tracked_files() -> list[str]:
    files = _git_ls_files()
    if files is not None:
        return files
    return _fallback_repo_files()


def assert_no_tracked_legacy_projection_package() -> None:
    offenders = [
        path
        for path in tracked_files()
        if path.startswith(LEGACY_PACKAGE_PREFIX)
        and (REPO_ROOT / path).exists()
    ]
    assert offenders == [], (
        "legacy loopx/projections shims should not be kept for repo-local code; "
        "move implementations to loopx.control_plane.<bounded_context>",
        offenders,
    )


def assert_moved_root_modules_stay_in_bounded_contexts() -> None:
    files = set(tracked_files())
    offenders = sorted(
        path
        for path in LEGACY_ROOT_MODULES
        if path in files and (REPO_ROOT / path).exists()
    )
    assert offenders == [], {
        "reason": "moved root control-plane modules should stay in their owning bounded contexts",
        "offenders": offenders,
        "expected_modules": {path: LEGACY_ROOT_MODULES[path] for path in offenders},
    }


def assert_rehomed_capability_packages_stay_in_bounded_contexts() -> None:
    files = tracked_files()
    offenders = sorted(
        prefix
        for prefix in LEGACY_CAPABILITY_PACKAGES
        if any(
            path.startswith(prefix) and (REPO_ROOT / path).exists()
            for path in files
        )
    )
    assert offenders == [], {
        "reason": "internal control-plane packages should not return under capabilities",
        "offenders": offenders,
        "canonical_modules": {
            prefix: LEGACY_CAPABILITY_PACKAGES[prefix] for prefix in offenders
        },
    }

    for prefix, canonical_module in LEGACY_CAPABILITY_PACKAGES.items():
        legacy_module = prefix.rstrip("/").replace("/", ".")
        assert importlib.util.find_spec(legacy_module) is None, {
            "reason": "removed internal capability imports should fail instead of using shims",
            "legacy_module": legacy_module,
            "canonical_module": canonical_module,
        }
        assert importlib.util.find_spec(canonical_module) is not None, {
            "reason": "canonical bounded-context import must be available",
            "canonical_module": canonical_module,
        }


def assert_moved_root_imports_are_not_shimmed() -> None:
    for path, canonical_module in LEGACY_ROOT_MODULES.items():
        root_module = path[:-3].replace("/", ".")
        assert importlib.util.find_spec(root_module) is None, {
            "reason": "moved root control-plane imports should fail instead of using shims",
            "root_module": root_module,
            "canonical_module": canonical_module,
        }
    for canonical_module in CANONICAL_MODULES:
        assert importlib.util.find_spec(canonical_module) is not None, {
            "reason": "canonical bounded-context import must be available",
            "canonical_module": canonical_module,
        }


def assert_repo_local_imports_use_bounded_contexts() -> None:
    offenders: list[str] = []
    for path in tracked_text_files():
        if path == SCRIPT_PATH:
            continue
        text = path.read_text(encoding="utf-8")
        if LEGACY_IMPORT in text:
            offenders.append(str(path.relative_to(REPO_ROOT)))
    assert offenders == [], {
        "legacy_import": LEGACY_IMPORT,
        "offenders": offenders[:20],
        "offender_count": len(offenders),
    }


def assert_repo_local_imports_avoid_legacy_status_paths() -> None:
    offenders: list[str] = []
    for path in tracked_text_files():
        if path == SCRIPT_PATH:
            continue
        text = path.read_text(encoding="utf-8")
        for legacy in LEGACY_STATUS_IMPORTS:
            if legacy in text:
                offenders.append(f"{path.relative_to(REPO_ROOT)}:{legacy}")
    assert offenders == [], {
        "reason": "status read models belong in loopx.control_plane.status",
        "offenders": offenders[:20],
        "offender_count": len(offenders),
    }


def main() -> int:
    assert_no_tracked_legacy_projection_package()
    assert_moved_root_modules_stay_in_bounded_contexts()
    assert_rehomed_capability_packages_stay_in_bounded_contexts()
    assert_moved_root_imports_are_not_shimmed()
    assert_repo_local_imports_use_bounded_contexts()
    assert_repo_local_imports_avoid_legacy_status_paths()
    print("bounded-context-namespace-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
