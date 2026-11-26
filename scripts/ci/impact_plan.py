"""Classify whole-PR ownership; exempt unrelated heavy jobs, never select tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

SCHEMA = "loopx_ci_job_plan_v1"
ROOT_DOCS = {"README.md", "README.zh-CN.md", "README.ja.md", "CHANGELOG.md", "CONTRIBUTING.md"}
POLICY_PATHS = ("scripts/ci/impact_plan.py", "scripts/ci/review_gate.py", ".github/workflows/python-tests.yml")
OUTPUTS = (
    "core_tests",
    "backend_tests",
    "frontend_tests",
    "compat_tests",
    "python_tests",
    "stage2c_tests",
    "presentation_tests",
)
CHANGE_KINDS = ("docs", "presentation", "backend", "mixed", "full")
PRESENTATION_ROOTS = ("apps/presentation/dashboard/src/", "apps/presentation/dashboard/public/", "loopx/web/chat/")
PRESENTATION_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}
FRONTEND_ROOTS = ("apps/presentation/", "loopx/web/chat/", "loopx/presentation/", "examples/personal-workspace-browser/")
FRONTEND_SCRIPTS = ("scripts/chat_bundle.py", "scripts/chat_bundle_launcher.mjs", "scripts/verify_installed_chat.py")
FULL_EXACT_PATHS = {
    "pyproject.toml",
    "setup.py",
    "MANIFEST.in",
    "package.json",
    "package-lock.json",
    "tsconfig.control-plane.json",
    "uv.lock",
    "sonar-project.properties",
    "apps/presentation/dashboard/package.json",
    "apps/presentation/dashboard/package-lock.json",
    "apps/presentation/dashboard/vite.config.ts",
}
FULL_PREFIXES = (
    "scripts/ci/",
    ".github/",
    "examples/shared-goal-authority-e2e/",
    "packages/loopx-core/",
    "tests/requirements-",
)
LIGHT_EXACT_PATHS = {
    "AGENTS.md",
    "CLAUDE.md",
    "CONTEXT.md",
    "ADOPTERS.md",
    "CODE_OF_CONDUCT.md",
    "DCO",
    "LICENSE",
    "LICENSE-MIT",
    "NOTICE",
    "mkdocs.yaml",
}
LIGHT_PREFIXES = (
    "loopx/",
    "tests/",
    "scripts/",
    "examples/",
    "packages/",
    "apps/",
    "docs/",
    "skills/",
    "demo/",
    "man/",
    "regression/",
    "benchmark/",
)


@dataclass(frozen=True)
class Change:
    status: str
    path: str


def is_document(path: str) -> bool:
    return path in ROOT_DOCS or (path.startswith("docs/") and PurePosixPath(path).suffix == ".md")


def is_frontend_path(path: str) -> bool:
    if path.startswith(FRONTEND_ROOTS) or path in FRONTEND_SCRIPTS:
        return True
    if path.startswith("examples/"):
        name = PurePosixPath(path).name
        return any(token in name for token in ("browser", "dashboard")) or name.startswith(("chat-", "frontstage-"))
    return False


def is_full_risk_path(path: str) -> bool:
    if path in FULL_EXACT_PATHS or path.startswith(FULL_PREFIXES):
        return True
    return path not in LIGHT_EXACT_PATHS and not path.startswith(LIGHT_PREFIXES)


def candidate(changes: list[Change], *, pull_request: bool = True) -> tuple[str, str]:
    if not pull_request:
        return "full", "non-PR runs retain full qualification"
    if not changes:
        return "full", "empty or unavailable diff is not an exemption"
    for change in changes:
        path = PurePosixPath(change.path)
        if path.is_absolute() or ".." in path.parts or str(path) != change.path or change.status not in {"A", "M", "D"}:
            return "full", "noncanonical paths or type changes require full qualification"
    code = [item for item in changes if not is_document(item.path)]
    if not code:
        return "docs", "documentation only; existing runtime exemption"
    if all(item.path.startswith(PRESENTATION_ROOTS) and PurePosixPath(item.path).suffix in PRESENTATION_SUFFIXES for item in code):
        return "presentation", "client-only source/assets; retain frontend type-check, build and browser checks"
    if any(is_full_risk_path(item.path) for item in code):
        return "full", "build, dependency, packaging, CI policy or unknown paths require full qualification"
    if any(is_frontend_path(item.path) for item in code):
        return "mixed", "backend and UI changes; run fast static/contract/TS checks plus frontend browser checks (heavy matrices nightly/main/ci:full)"
    return "backend", "backend/tests/docs-support changes without UI or manifest edits; run fast static/contract/TS checks (heavy matrices nightly/main/ci:full)"


def job_flags(kind: str, *, presentation: bool = False) -> dict[str, bool]:
    if kind not in CHANGE_KINDS:
        raise ValueError("unknown CI change kind")
    return {
        "core_tests": kind != "docs",
        "backend_tests": kind in {"backend", "mixed", "full"},
        "frontend_tests": kind in {"presentation", "mixed", "full"},
        "compat_tests": kind == "full",
        "python_tests": kind == "full",
        "stage2c_tests": kind == "full",
        "presentation_tests": kind in {"presentation", "mixed"} or presentation,
    }


def git(*args: str) -> bytes:
    return subprocess.check_output(["git", *args])


def revision(ref: str) -> str:
    return git("rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}").decode().strip()


def diff_changes(base: str, head: str) -> list[Change]:
    raw = git("diff", "--name-status", "--no-renames", "-z", base, head, "--").split(b"\0")
    if raw[-1] != b"" or (len(raw) - 1) % 2:
        raise ValueError("malformed NUL-delimited Git changes")
    return [Change(os.fsdecode(raw[i]), os.fsdecode(raw[i + 1])) for i in range(0, len(raw) - 1, 2)]


def trusted_policy(base: str, head: str) -> bool:
    # A PR cannot introduce its own exemption. No manifest or per-test inventory.
    for path in POLICY_PATHS:
        try:
            old = subprocess.check_output(["git", "rev-parse", f"{base}:{path}"], stderr=subprocess.DEVNULL)
            new = subprocess.check_output(["git", "rev-parse", f"{head}:{path}"], stderr=subprocess.DEVNULL)
        except subprocess.CalledProcessError:
            return False
        if old != new:
            return False
    return True


def declared_python_shards(checkout_sha: str) -> int:
    # Only classification needs the parser already installed in the changes
    # job. Aggregate verification must remain usable with the standard library.
    import yaml

    workflow = yaml.load(git("show", f"{checkout_sha}:{POLICY_PATHS[-1]}"), Loader=yaml.BaseLoader)
    message = "test-shard.strategy.matrix must declare only a static contiguous shard axis [1..N]"
    try:
        matrix = workflow["jobs"]["test-shard"]["strategy"]["matrix"]
    except (KeyError, TypeError):
        raise ValueError(message) from None
    if not isinstance(matrix, dict) or set(matrix) != {"shard"}:
        raise ValueError(message)
    shards = matrix["shard"]
    if not isinstance(shards, list) or not shards or shards != [str(i) for i in range(1, len(shards) + 1)]:
        raise ValueError(message)
    return len(shards)


def plan(base: str, head: str, *, pull_request: bool = True, force_full: bool = False) -> dict:
    base_sha, head_sha = revision(base), revision(head)
    merge_base = git("merge-base", base_sha, head_sha).decode().strip()
    changes = diff_changes(merge_base, head_sha)
    kind, reason = candidate(changes, pull_request=pull_request)
    presentation = kind in {"presentation", "mixed"}
    policy_change = any(item.path.startswith("scripts/ci/") or item.path == POLICY_PATHS[-1] for item in changes)
    if kind != "full":
        for item in changes:
            for sha in (merge_base, head_sha):
                mode = git("ls-tree", "--format=%(objectmode)", sha, "--", item.path).decode().strip()
                if mode and mode not in {"100644", "100755"}:
                    kind, reason = "full", "symlinks and nonregular Git objects cannot grant an exemption"
    if force_full:
        kind, reason = "full", "explicit force-full override"
    elif kind in {"presentation", "backend", "mixed"} and not trusted_policy(base_sha, head_sha):
        kind, reason = "full", "CI exemption policy is not already trusted on the target branch"
    flags = job_flags(kind, presentation=presentation or policy_change)
    checkout_sha = revision("HEAD")
    coverage_scope = (
        "full Python suite"
        if flags["python_tests"]
        else "fast static/contract/TS checks (heavy matrices nightly/main/merge_group/ci:full)"
        if flags["backend_tests"]
        else "no Python coverage claim"
    )
    return {"schema_version": SCHEMA, "base_sha": base_sha, "head_sha": head_sha,
            "merge_base_sha": merge_base, "checkout_sha": checkout_sha, "change_kind": kind,
            "force_full": force_full, "reason": reason, **flags,
            "changes": [{"status": item.status, "path": item.path} for item in changes],
            "coverage_scope": coverage_scope,
            "python_shards": declared_python_shards(checkout_sha) if flags["python_tests"] else 0}


def write_plan(packet: dict, output: str) -> None:
    Path(output).write_text(json.dumps(packet, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
