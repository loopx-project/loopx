"""Classify whole-PR ownership; exempt unrelated heavy jobs, never select tests."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
import subprocess

SCHEMA = "loopx_ci_job_plan_v1"
ROOT_DOCS = {"README.md", "README.zh-CN.md", "CHANGELOG.md", "CONTRIBUTING.md"}
POLICY_PATHS = ("scripts/ci/impact_plan.py", "scripts/ci/review_gate.py", ".github/workflows/python-tests.yml")
OUTPUTS = ("core_tests", "backend_tests", "python_tests", "stage2c_tests", "presentation_tests")
PRESENTATION_ROOTS = ("apps/presentation/dashboard/src/", "apps/presentation/dashboard/public/", "loopx/web/chat/")
PRESENTATION_SUFFIXES = {".ts", ".tsx", ".js", ".mjs", ".css", ".html", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2"}


@dataclass(frozen=True)
class Change:
    status: str
    path: str


def is_document(path: str) -> bool:
    return path in ROOT_DOCS or (path.startswith("docs/") and PurePosixPath(path).suffix == ".md")


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
    return "full", "runtime, prompts, tests, dependencies, build policy or unknown paths may affect backend behavior"


def job_flags(kind: str, *, presentation: bool = False) -> dict[str, bool]:
    if kind not in {"docs", "presentation", "full"}:
        raise ValueError("unknown CI change kind")
    return {"core_tests": kind != "docs", "backend_tests": kind == "full", "python_tests": kind == "full",
            "stage2c_tests": kind == "full", "presentation_tests": kind == "presentation" or presentation}


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


def plan(base: str, head: str, *, pull_request: bool = True, force_full: bool = False) -> dict:
    base_sha, head_sha = revision(base), revision(head)
    merge_base = git("merge-base", base_sha, head_sha).decode().strip()
    changes = diff_changes(merge_base, head_sha)
    kind, reason = candidate(changes, pull_request=pull_request)
    presentation = kind == "presentation"
    policy_change = any(item.path.startswith("scripts/ci/") or item.path == POLICY_PATHS[-1] for item in changes)
    if kind != "full":
        for item in changes:
            for sha in (merge_base, head_sha):
                mode = git("ls-tree", "--format=%(objectmode)", sha, "--", item.path).decode().strip()
                if mode and mode not in {"100644", "100755"}:
                    kind, reason = "full", "symlinks and nonregular Git objects cannot grant an exemption"
    if force_full:
        kind, reason = "full", "explicit force-full override"
    elif kind == "presentation" and not trusted_policy(base_sha, head_sha):
        kind, reason = "full", "presentation exemption policy is not already trusted on the target branch"
    flags = job_flags(kind, presentation=presentation or policy_change)
    return {"schema_version": SCHEMA, "base_sha": base_sha, "head_sha": head_sha,
            "merge_base_sha": merge_base, "checkout_sha": revision("HEAD"), "change_kind": kind,
            "force_full": force_full, "reason": reason, **flags,
            "changes": [{"status": item.status, "path": item.path} for item in changes],
            "coverage_scope": "full Python suite" if flags["python_tests"] else "no Python coverage claim",
            "python_shards": 4 if flags["python_tests"] else 0}


def write_plan(packet: dict, output: str) -> None:
    Path(output).write_text(json.dumps(packet, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")
