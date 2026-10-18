from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .intake_surface import build_content_ops_issue_fix_metadata_preview_packet
from ...control_plane.runtime.validation_command import (
    run_caller_validation as _run_caller_validation,
    require_validation_passed as _require_passed,
)
from ...extensions.process_runtime import remove_owned_tree


ISSUE_FIX_ACCEPTANCE_LOOP_SCHEMA_VERSION = "issue_fix_acceptance_loop_v0"
ISSUE_FIX_VALIDATED_FIX_ARTIFACT_SCHEMA_VERSION = "issue_fix_validated_fix_artifact_v0"
ISSUE_FIX_CALLER_REPO_BRANCH_PACKET_SCHEMA_VERSION = (
    "issue_fix_caller_repo_branch_packet_v0"
)
ISSUE_FIX_CALLER_REPO_BRANCH_ARTIFACT_SCHEMA_VERSION = (
    "issue_fix_caller_repo_branch_artifact_v0"
)


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_fixture_smoke(workspace: Path) -> dict[str, Any]:
    result = subprocess.run(
        [sys.executable, "test_calculator.py"],
        cwd=workspace,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return {
        "schema_version": "issue_fix_validation_command_v0",
        "command_label": "python test_calculator.py",
        "exit_code": result.returncode,
        "passed": result.returncode == 0,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }


def _run_git_step(
    workspace: Path,
    args: list[str],
    label: str | None = None,
    *,
    expected_exit_codes: tuple[int, ...] = (0,),
) -> dict[str, Any]:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return {
        "schema_version": "issue_fix_git_step_v0",
        "command_label": label or "git " + " ".join(args),
        "exit_code": result.returncode,
        "expected_exit_codes": list(expected_exit_codes),
        "passed": result.returncode in expected_exit_codes,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }


def _run_git_capture(
    workspace: Path,
    args: list[str],
    *,
    expected_exit_codes: tuple[int, ...] = (0,),
    timeout: int = 10,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=workspace,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout,
    )
    if result.returncode not in expected_exit_codes:
        raise RuntimeError(
            f"git {' '.join(args)} failed with exit code {result.returncode}"
        )
    return result


def _validate_branch_name(branch: str, *, field: str) -> str:
    value = str(branch or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    if any(ch in value for ch in "\0\r\n"):
        raise ValueError(f"{field} must be a single git branch name")
    result = subprocess.run(
        ["git", "check-ref-format", "--branch", value],
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    if result.returncode != 0:
        raise ValueError(f"{field} must be a valid git branch name")
    return value


def _branch_slug(value: Any) -> str:
    text = str(value or "").strip().lower()
    chars = [ch if ch.isalnum() else "-" for ch in text]
    slug = "-".join(part for part in "".join(chars).split("-") if part)
    return slug[:60] or "public-issue"


def _derive_issue_branch(metadata: Mapping[str, Any]) -> str:
    kind = "pr" if metadata.get("kind") == "pull_request" else "issue"
    number = metadata.get("number")
    suffix = f"{kind}-{number}" if isinstance(number, int) else _branch_slug(metadata.get("issue_ref"))
    return _validate_branch_name(f"codex/{suffix}-fix", field="issue_branch")


def _git_branch_exists(workspace: Path, branch: str) -> bool:
    result = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        cwd=workspace,
        text=True, encoding="utf-8", errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=10,
    )
    return result.returncode == 0


def _git_current_branch(workspace: Path) -> str:
    result = _run_git_capture(workspace, ["branch", "--show-current"])
    return result.stdout.strip() or "detached"


def _git_status_lines(workspace: Path) -> list[str]:
    result = _run_git_capture(workspace, ["status", "--porcelain"])
    return [line for line in result.stdout.splitlines() if line.strip()]


def _git_ref_oid(workspace: Path, ref: str) -> str:
    result = _run_git_capture(
        workspace, ["rev-parse", "--verify", f"{ref}^{{commit}}"]
    )
    oid = result.stdout.strip()
    if not oid:
        raise RuntimeError(f"git ref {ref} did not resolve to a commit")
    return oid


def _base_snapshot(workspace: Path, base_branch: str) -> dict[str, Any]:
    base_revision = _git_ref_oid(workspace, base_branch)
    upstream = _run_git_capture(
        workspace,
        [
            "for-each-ref",
            "--format=%(upstream:short)",
            f"refs/heads/{base_branch}",
        ],
    )
    tracking_ref = upstream.stdout.strip() or None
    tracking_revision = (
        _git_ref_oid(workspace, tracking_ref) if tracking_ref is not None else None
    )
    if tracking_revision is None:
        relation = "no_tracking_ref"
    elif tracking_revision == base_revision:
        relation = "current"
    else:
        base_is_ancestor = _run_git_capture(
            workspace,
            ["merge-base", "--is-ancestor", base_revision, tracking_revision],
            expected_exit_codes=(0, 1),
        ).returncode == 0
        tracking_is_ancestor = _run_git_capture(
            workspace,
            ["merge-base", "--is-ancestor", tracking_revision, base_revision],
            expected_exit_codes=(0, 1),
        ).returncode == 0
        if base_is_ancestor:
            relation = "behind_tracking_ref"
        elif tracking_is_ancestor:
            relation = "ahead_of_tracking_ref"
        else:
            relation = "diverged_from_tracking_ref"
    return {
        "schema_version": "issue_fix_base_snapshot_v0",
        "base_branch": base_branch,
        "base_revision": base_revision,
        "tracking_ref": tracking_ref,
        "tracking_revision": tracking_revision,
        "relation": relation,
        "tracking_ref_refresh_performed": False,
        "external_remote_used": False,
        "local_path_captured": False,
    }


def _changed_files(workspace: Path, *, base_branch: str) -> tuple[list[str], bool]:
    files: list[str] = []
    truncated = False

    def add_lines(lines: list[str]) -> None:
        nonlocal truncated
        for raw in lines:
            path = raw.strip()
            if not path or path.startswith("/"):
                continue
            if path not in files:
                files.append(path)
            if len(files) >= 20:
                truncated = True
                return

    if _git_branch_exists(workspace, base_branch):
        result = subprocess.run(
            ["git", "diff", "--name-only", f"{base_branch}...HEAD"],
            cwd=workspace,
            text=True, encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=10,
        )
        if result.returncode == 0:
            add_lines(result.stdout.splitlines())
    for args in (["diff", "--name-only"], ["diff", "--cached", "--name-only"]):
        result = _run_git_capture(workspace, args)
        add_lines(result.stdout.splitlines())
    for line in _git_status_lines(workspace):
        if len(line) >= 4 and line[:2] == "??":
            add_lines([line[3:]])
    return files[:20], truncated


# Caller-validation execution (_run_caller_validation / _require_passed) is
# imported from control_plane.runtime.validation_command so the todo-completion
# path can reuse the same privacy-safe handler.


def _remove_temporary_git_workspace(workspace: Path) -> None:
    """Drop this packet's own temporary repository, read-only objects included.

    Git marks every loose object read-only, which Windows refuses to unlink, so
    the retry below only ever helped a transient lock; the host attribute is
    cleared by the shared owner before each attempt.
    """

    for attempt in range(5):
        try:
            remove_owned_tree(workspace)
            return
        except OSError:
            if attempt == 4:
                raise
            time.sleep(0.1 * (attempt + 1))


def _write_fixture_workspace(workspace: Path) -> None:
    (workspace / "calculator.py").write_text(
        "\n".join(
            [
                "def add(left, right):",
                "    return left - right",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (workspace / "test_calculator.py").write_text(
        "\n".join(
            [
                "from calculator import add",
                "",
                "",
                "def main():",
                "    assert add(2, 3) == 5, 'add should sum two integers'",
                "",
                "",
                "if __name__ == '__main__':",
                "    main()",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _apply_fixture_patch(workspace: Path) -> dict[str, Any]:
    target = workspace / "calculator.py"
    before = target.read_text(encoding="utf-8")
    broken = "return left - right"
    fixed = "return left + right"
    if broken not in before:
        raise ValueError("fixture patch expected the known subtraction bug")
    after = before.replace(broken, fixed, 1)
    target.write_text(after, encoding="utf-8")
    shutil.rmtree(workspace / "__pycache__", ignore_errors=True)
    return {
        "schema_version": "issue_fix_patch_step_v0",
        "patch_applied": True,
        "file": "calculator.py",
        "change_summary": "replace subtraction with addition in add()",
        "before_hash": _sha256_text(before),
        "after_hash": _sha256_text(after),
        "local_path_captured": False,
        "destructive_git_used": False,
    }


def _build_metadata_packet(
    *,
    repo: str,
    issue_ref: str,
    url: str | None,
    generated_at: str | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    metadata_packet = build_content_ops_issue_fix_metadata_preview_packet(
        repo=repo,
        issue_ref=issue_ref,
        url=url,
        provider_payload={
            "state": "open",
            "title": "add() returns the wrong arithmetic result",
            "labels": [{"name": "bug"}, {"name": "has-repro"}],
            "comments_count": 0,
        },
        generated_at=generated_at,
    )
    return metadata_packet, dict(metadata_packet["github_metadata_preview"])


def build_issue_fix_acceptance_fixture_packet(
    *,
    repo: str = "public_repo_fixture",
    issue_ref: str = "issue_123_public_metadata_fixture",
    url: str | None = None,
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    """Run a deterministic issue-fix acceptance loop against a temp fixture."""

    metadata_packet, metadata = _build_metadata_packet(
        repo=repo,
        issue_ref=issue_ref,
        url=url,
        generated_at=generated_at,
    )

    with tempfile.TemporaryDirectory(prefix="loopx-issue-fix-") as tmpdir:
        workspace = Path(tmpdir)
        _write_fixture_workspace(workspace)
        repro_before = _run_fixture_smoke(workspace)
        route = {
            "schema_version": "issue_fix_code_route_v0",
            "route_id": "fixture_calculator_add_route",
            "selected": True,
            "source": "public issue labels plus failing repro smoke",
            "files_examined": ["calculator.py", "test_calculator.py"],
            "requires_private_repo_state": False,
            "reads_private_material": False,
        }
        patch_step = _apply_fixture_patch(workspace)
        validation_after = _run_fixture_smoke(workspace)

    artifact = {
        "schema_version": ISSUE_FIX_VALIDATED_FIX_ARTIFACT_SCHEMA_VERSION,
        "fix_artifact_ready": validation_after["passed"],
        "pr_review_packet_ready": validation_after["passed"],
        "issue_signal": {
            "repo": metadata["repo"],
            "issue_ref": metadata["issue_ref"],
            "kind": metadata["kind"],
            "labels": metadata["labels"],
            "body_captured": False,
            "comment_bodies_captured": False,
        },
        "repro_before": repro_before,
        "code_route": route,
        "patch": patch_step,
        "validation_after": validation_after,
        "review_packet": {
            "schema_version": "issue_fix_pr_review_packet_v0",
            "ready": validation_after["passed"],
            "summary": "Focused fixture repro failed, minimal patch applied, focused validation passed.",
            "files_changed": ["calculator.py"],
            "validation_commands": ["python test_calculator.py"],
            "external_issue_comment_performed": False,
            "external_pr_created": False,
            "merge_performed": False,
        },
    }
    steps = [
        {
            "step": "metadata_intake",
            "result": "public metadata preview built without issue body or comments",
        },
        {
            "step": "repro_smoke",
            "result": "failed before patch" if not repro_before["passed"] else "unexpected pass",
        },
        {"step": "code_route", "result": "fixture calculator route selected"},
        {
            "step": "patch",
            "result": "minimal patch applied" if patch_step["patch_applied"] else "not applied",
        },
        {
            "step": "validation",
            "result": "passed after patch" if validation_after["passed"] else "failed after patch",
        },
    ]
    packet: dict[str, Any] = {
        "ok": bool(
            not repro_before["passed"]
            and patch_step["patch_applied"]
            and validation_after["passed"]
        ),
        "schema_version": ISSUE_FIX_ACCEPTANCE_LOOP_SCHEMA_VERSION,
        "mode": "issue-fix-acceptance-fixture",
        "generated_at": generated_at,
        "workspace_mode": "temporary_fixture",
        "metadata_preview_schema_version": metadata_packet["schema_version"],
        "validated_fix_artifact": artifact,
        "steps": steps,
        "external_reads_performed": False,
        "external_writes_performed": False,
        "issue_body_captured": False,
        "comment_bodies_captured": False,
        "local_paths_captured": False,
        "private_repo_state_read": False,
        "destructive_git_used": False,
        "next_safe_action": (
            "promote this acceptance loop from deterministic fixture to a real "
            "repo-local issue branch only after a focused repro can be run safely"
        ),
    }
    validation = validate_issue_fix_acceptance_loop_packet(packet)
    packet["ok"] = bool(packet["ok"] and validation["ok"])
    packet["validation"] = validation
    return packet


def build_issue_fix_repo_branch_fixture_packet(
    *,
    repo: str = "public_repo_fixture",
    issue_ref: str = "issue_123_public_metadata_fixture",
    url: str | None = None,
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    """Run the issue-fix loop through a real temporary git branch lifecycle."""

    metadata_packet, metadata = _build_metadata_packet(
        repo=repo,
        issue_ref=issue_ref,
        url=url,
        generated_at=generated_at,
    )
    branch_name = "codex/issue-123-public-metadata-fixture"

    tmpdir = tempfile.mkdtemp(prefix="loopx-issue-fix-git-")
    try:
        workspace = Path(tmpdir)
        git_steps: list[dict[str, Any]] = []
        for args, label in (
            (["init", "-b", "main"], "git init fixture repo"),
            (["config", "user.name", "LoopX Fixture"], "git config fixture user.name"),
            (["config", "user.email", "loopx-fixture@example.invalid"], "git config fixture user.email"),
            (["config", "gc.auto", "0"], "git config fixture gc.auto"),
            (["config", "maintenance.auto", "false"], "git config fixture maintenance.auto"),
        ):
            step = _run_git_step(workspace, args, label)
            git_steps.append(step)
            _require_passed(step)

        _write_fixture_workspace(workspace)
        for args, label in (
            (["add", "calculator.py", "test_calculator.py"], "git add fixture files"),
            (["commit", "-m", "Add failing calculator fixture"], "git commit baseline fixture"),
            (["checkout", "-b", branch_name], "git create issue fix branch"),
        ):
            step = _run_git_step(workspace, args, label)
            git_steps.append(step)
            _require_passed(step)

        repro_before = _run_fixture_smoke(workspace)
        route = {
            "schema_version": "issue_fix_code_route_v0",
            "route_id": "fixture_git_calculator_add_route",
            "selected": True,
            "source": "public issue labels plus local branch repro smoke",
            "files_examined": ["calculator.py", "test_calculator.py"],
            "requires_private_repo_state": False,
            "reads_private_material": False,
        }
        patch_step = _apply_fixture_patch(workspace)
        validation_after = _run_fixture_smoke(workspace)
        diff_step = _run_git_step(
            workspace,
            ["diff", "--quiet", "--", "calculator.py"],
            "git diff confirms branch patch",
            expected_exit_codes=(1,),
        )
        git_steps.append(diff_step)
        _require_passed(diff_step)
    finally:
        _remove_temporary_git_workspace(Path(tmpdir))

    artifact = {
        "schema_version": ISSUE_FIX_VALIDATED_FIX_ARTIFACT_SCHEMA_VERSION,
        "fix_artifact_ready": validation_after["passed"],
        "pr_review_packet_ready": validation_after["passed"],
        "issue_signal": {
            "repo": metadata["repo"],
            "issue_ref": metadata["issue_ref"],
            "kind": metadata["kind"],
            "labels": metadata["labels"],
            "body_captured": False,
            "comment_bodies_captured": False,
        },
        "repo_branch": {
            "schema_version": "issue_fix_repo_branch_artifact_v0",
            "repo_mode": "temporary_git_repo",
            "base_branch": "main",
            "issue_branch": branch_name,
            "branch_created": all(step.get("passed") for step in git_steps),
            "external_remote_used": False,
            "local_path_captured": False,
        },
        "git_steps": git_steps,
        "repro_before": repro_before,
        "code_route": route,
        "patch": patch_step,
        "validation_after": validation_after,
        "review_packet": {
            "schema_version": "issue_fix_pr_review_packet_v0",
            "ready": validation_after["passed"],
            "summary": (
                "Temporary git repo issue branch created, focused repro failed, "
                "minimal patch applied, focused validation passed."
            ),
            "files_changed": ["calculator.py"],
            "validation_commands": ["python test_calculator.py"],
            "external_issue_comment_performed": False,
            "external_pr_created": False,
            "merge_performed": False,
        },
    }
    packet: dict[str, Any] = {
        "ok": bool(
            artifact["repo_branch"]["branch_created"]
            and not repro_before["passed"]
            and patch_step["patch_applied"]
            and validation_after["passed"]
        ),
        "schema_version": ISSUE_FIX_ACCEPTANCE_LOOP_SCHEMA_VERSION,
        "mode": "issue-fix-repo-branch-fixture",
        "generated_at": generated_at,
        "workspace_mode": "temporary_git_repo",
        "metadata_preview_schema_version": metadata_packet["schema_version"],
        "validated_fix_artifact": artifact,
        "steps": [
            {"step": "metadata_intake", "result": "public metadata preview built"},
            {"step": "repo_branch", "result": f"created {branch_name} in a temporary git repo"},
            {"step": "repro_smoke", "result": "failed before patch" if not repro_before["passed"] else "unexpected pass"},
            {"step": "patch", "result": "minimal patch applied"},
            {"step": "validation", "result": "passed after patch" if validation_after["passed"] else "failed after patch"},
        ],
        "external_reads_performed": False,
        "external_writes_performed": False,
        "issue_body_captured": False,
        "comment_bodies_captured": False,
        "local_paths_captured": False,
        "private_repo_state_read": False,
        "destructive_git_used": False,
        "next_safe_action": (
            "promote from the temporary git fixture to an approved caller-provided "
            "local repo path with explicit branch and validation controls"
        ),
    }
    validation = validate_issue_fix_acceptance_loop_packet(packet)
    packet["ok"] = bool(packet["ok"] and validation["ok"])
    packet["validation"] = validation
    return packet


def build_issue_fix_caller_repo_branch_packet(
    *,
    repo_path: str,
    repo: str = "approved_local_repo",
    issue_ref: str = "issue_123_public_metadata",
    url: str | None = None,
    base_branch: str = "main",
    issue_branch: str | None = None,
    validation_command: str | None = None,
    validation_label: str = "caller-declared validation",
    execute: bool = False,
    timeout_seconds: int = 60,
    generated_at: str | None = "2026-06-23T00:00:00Z",
) -> dict[str, Any]:
    """Prepare or execute a caller-approved local repo issue branch workflow."""

    metadata_packet, metadata = _build_metadata_packet(
        repo=repo,
        issue_ref=issue_ref,
        url=url,
        generated_at=generated_at,
    )
    base = _validate_branch_name(base_branch, field="base_branch")
    branch = (
        _validate_branch_name(issue_branch, field="issue_branch")
        if issue_branch
        else _derive_issue_branch(metadata)
    )
    validation_label = validation_label or "caller-declared validation"
    workspace = Path(repo_path).expanduser()

    git_steps: list[dict[str, Any]] = []
    validation_command_result: dict[str, Any] = {
        "schema_version": "issue_fix_validation_command_v0",
        "command_label": validation_label,
        "executed": False,
        "passed": False,
        "stdout_captured": False,
        "stderr_captured": False,
        "local_path_captured": False,
    }
    branch_action = "dry_run"
    base_snapshot: dict[str, Any] = {
        "schema_version": "issue_fix_base_snapshot_v0",
        "base_branch": base,
        "base_revision": None,
        "tracking_ref": None,
        "tracking_revision": None,
        "relation": "not_inspected",
        "tracking_ref_refresh_performed": False,
        "external_remote_used": False,
        "local_path_captured": False,
    }
    changed_files: list[str] = []
    changed_files_truncated = False

    if execute:
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError("repo_path must point to an existing local git repository")
        _run_git_capture(workspace, ["rev-parse", "--is-inside-work-tree"])
        current_branch = _git_current_branch(workspace)
        dirty_before = bool(_git_status_lines(workspace))
        branch_exists = _git_branch_exists(workspace, branch)
        base_exists = _git_branch_exists(workspace, base)
        if base_exists:
            base_snapshot = _base_snapshot(workspace, base)
        if current_branch == branch:
            branch_action = "claimed_current"
        else:
            if dirty_before:
                raise RuntimeError(
                    "refusing to switch branches with uncommitted changes; "
                    "rerun from the target issue branch or clean the repo after approval"
                )
            if branch_exists:
                step = _run_git_step(workspace, ["checkout", branch], "git claim existing issue branch")
                git_steps.append(step)
                _require_passed(step)
                branch_action = "claimed_existing"
            else:
                if not base_exists:
                    raise RuntimeError("base_branch does not exist in the approved local repo")
                if base_snapshot["relation"] not in {"current", "no_tracking_ref"}:
                    raise RuntimeError(
                        "refusing to create an issue branch because base_branch does "
                        "not match its local tracking ref; refresh or reconcile the "
                        "approved base, then rerun"
                    )
                base_revision = str(base_snapshot["base_revision"])
                step = _run_git_step(
                    workspace,
                    ["checkout", "-b", branch, base_revision],
                    "git create approved issue branch from base snapshot",
                )
                git_steps.append(step)
                _require_passed(step)
                if _git_ref_oid(workspace, branch) != base_revision:
                    raise RuntimeError(
                        "created issue branch does not match the approved base snapshot"
                    )
                branch_action = "created"
        if not validation_command:
            raise ValueError("validation_command is required when --execute is used")
        validation_command_result = _run_caller_validation(
            workspace,
            validation_command=validation_command,
            validation_label=validation_label,
            timeout_seconds=timeout_seconds,
        )
        validation_command_result["executed"] = True
        changed_files, changed_files_truncated = _changed_files(workspace, base_branch=base)

    review_ready = bool(
        execute
        and validation_command_result.get("passed") is True
        and len(changed_files) > 0
    )
    artifact = {
        "schema_version": ISSUE_FIX_CALLER_REPO_BRANCH_ARTIFACT_SCHEMA_VERSION,
        "repo_mode": "approved_local_repo",
        "repo_label": metadata["repo"],
        "repo_path_captured": False,
        "base_branch": base,
        "issue_branch": branch,
        "branch_action": branch_action,
        "branch_ready": execute and branch_action in {"claimed_current", "claimed_existing", "created"},
        "base_snapshot": base_snapshot,
        "external_remote_used": False,
        "local_path_captured": False,
        "validation": validation_command_result,
        "changed_files": changed_files,
        "changed_files_truncated": changed_files_truncated,
        "changed_file_count": len(changed_files),
    }
    packet: dict[str, Any] = {
        "ok": True,
        "schema_version": ISSUE_FIX_CALLER_REPO_BRANCH_PACKET_SCHEMA_VERSION,
        "mode": "issue-fix-caller-repo-branch",
        "generated_at": generated_at,
        "dry_run": not execute,
        "workspace_mode": "approved_local_repo",
        "metadata_preview_schema_version": metadata_packet["schema_version"],
        "caller_repo_branch": artifact,
        "issue_signal": {
            "repo": metadata["repo"],
            "issue_ref": metadata["issue_ref"],
            "kind": metadata["kind"],
            "labels": metadata["labels"],
            "body_captured": False,
            "comment_bodies_captured": False,
        },
        "review_packet": {
            "schema_version": "issue_fix_pr_review_packet_v0",
            "ready": review_ready,
            "summary": (
                "Approved local issue branch has validation evidence and repo-relative "
                "change evidence."
                if review_ready
                else "Approved local issue branch prepared; validation or change evidence is not PR-ready yet."
            ),
            "files_changed": changed_files,
            "files_changed_truncated": changed_files_truncated,
            "validation_commands": [validation_label],
            "external_issue_comment_performed": False,
            "external_pr_created": False,
            "merge_performed": False,
        },
        "external_reads_performed": False,
        "external_writes_performed": False,
        "issue_body_captured": False,
        "comment_bodies_captured": False,
        "local_paths_captured": False,
        "private_repo_state_read": bool(execute),
        "destructive_git_used": False,
        "next_safe_action": (
            "apply or review the branch-local fix and rerun this command until "
            "validation passes with repo-relative change evidence; external PR/comment/merge "
            "actions still require explicit caller action"
        ),
    }
    validation = validate_issue_fix_caller_repo_branch_packet(packet)
    packet["ok"] = bool(packet["ok"] and validation["ok"])
    packet["validation"] = validation
    return packet


def validate_issue_fix_acceptance_loop_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_ACCEPTANCE_LOOP_SCHEMA_VERSION:
        errors.append("packet schema_version must be issue_fix_acceptance_loop_v0")
    for key in (
        "external_reads_performed",
        "external_writes_performed",
        "issue_body_captured",
        "comment_bodies_captured",
        "local_paths_captured",
        "private_repo_state_read",
        "destructive_git_used",
    ):
        if packet.get(key) is not False:
            errors.append(f"packet {key} must be false")

    artifact = (
        packet.get("validated_fix_artifact")
        if isinstance(packet.get("validated_fix_artifact"), Mapping)
        else {}
    )
    if artifact.get("schema_version") != ISSUE_FIX_VALIDATED_FIX_ARTIFACT_SCHEMA_VERSION:
        errors.append("validated fix artifact has wrong schema")
    if artifact.get("fix_artifact_ready") is not True:
        errors.append("validated fix artifact must be ready")
    repro = artifact.get("repro_before") if isinstance(artifact.get("repro_before"), Mapping) else {}
    after = (
        artifact.get("validation_after")
        if isinstance(artifact.get("validation_after"), Mapping)
        else {}
    )
    patch = artifact.get("patch") if isinstance(artifact.get("patch"), Mapping) else {}
    if repro.get("passed") is not False:
        errors.append("repro must fail before patch")
    if patch.get("patch_applied") is not True:
        errors.append("patch must be applied")
    if after.get("passed") is not True:
        errors.append("validation must pass after patch")
    for command in (repro, after):
        if command.get("stdout_captured") is not False:
            errors.append("validation stdout must not be captured")
        if command.get("stderr_captured") is not False:
            errors.append("validation stderr must not be captured")
        if command.get("local_path_captured") is not False:
            errors.append("validation local path must not be captured")
    if patch.get("file") != "calculator.py":
        errors.append("patch file must be repo-relative")
    if patch.get("local_path_captured") is not False:
        errors.append("patch local path must not be captured")

    repo_branch = artifact.get("repo_branch")
    if isinstance(repo_branch, Mapping):
        if repo_branch.get("branch_created") is not True:
            errors.append("repo branch must be created")
        if repo_branch.get("external_remote_used") is not False:
            errors.append("repo branch fixture must not use an external remote")
        if repo_branch.get("local_path_captured") is not False:
            errors.append("repo branch local path must not be captured")
    git_steps = artifact.get("git_steps")
    if isinstance(git_steps, list):
        for step in git_steps:
            if not isinstance(step, Mapping):
                errors.append("git_steps must contain objects")
                continue
            if step.get("passed") is not True:
                errors.append(f"git step failed: {step.get('command_label')}")
            if step.get("stdout_captured") is not False:
                errors.append("git stdout must not be captured")
            if step.get("stderr_captured") is not False:
                errors.append("git stderr must not be captured")
            if step.get("local_path_captured") is not False:
                errors.append("git local path must not be captured")

    review = (
        artifact.get("review_packet")
        if isinstance(artifact.get("review_packet"), Mapping)
        else {}
    )
    if review.get("ready") is not True:
        errors.append("review packet must be ready")
    for key in ("external_issue_comment_performed", "external_pr_created", "merge_performed"):
        if review.get(key) is not False:
            errors.append(f"review packet {key} must be false")

    return {
        "schema_version": "issue_fix_acceptance_loop_validation_v0",
        "ok": not errors,
        "errors": errors,
        "steps_count": len(packet.get("steps") or []),
        "validated_fix_artifact_ready": artifact.get("fix_artifact_ready") is True,
    }


def validate_issue_fix_caller_repo_branch_packet(packet: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if packet.get("schema_version") != ISSUE_FIX_CALLER_REPO_BRANCH_PACKET_SCHEMA_VERSION:
        errors.append("packet schema_version must be issue_fix_caller_repo_branch_packet_v0")
    for key in (
        "external_reads_performed",
        "external_writes_performed",
        "issue_body_captured",
        "comment_bodies_captured",
        "local_paths_captured",
        "destructive_git_used",
    ):
        if packet.get(key) is not False:
            errors.append(f"packet {key} must be false")
    artifact = (
        packet.get("caller_repo_branch")
        if isinstance(packet.get("caller_repo_branch"), Mapping)
        else {}
    )
    if artifact.get("schema_version") != ISSUE_FIX_CALLER_REPO_BRANCH_ARTIFACT_SCHEMA_VERSION:
        errors.append("caller repo branch artifact has wrong schema")
    if artifact.get("repo_path_captured") is not False:
        errors.append("caller repo path must not be captured")
    if artifact.get("external_remote_used") is not False:
        errors.append("caller repo branch must not use external remote")
    if artifact.get("local_path_captured") is not False:
        errors.append("caller repo branch local path must not be captured")
    base_snapshot = (
        artifact.get("base_snapshot")
        if isinstance(artifact.get("base_snapshot"), Mapping)
        else {}
    )
    if base_snapshot.get("schema_version") != "issue_fix_base_snapshot_v0":
        errors.append("caller repo branch base snapshot has wrong schema")
    if base_snapshot.get("relation") not in {
        "not_inspected",
        "no_tracking_ref",
        "current",
        "behind_tracking_ref",
        "ahead_of_tracking_ref",
        "diverged_from_tracking_ref",
    }:
        errors.append("caller repo branch base snapshot has invalid relation")
    if base_snapshot.get("external_remote_used") is not False:
        errors.append("caller repo branch base snapshot must not use an external remote")
    if base_snapshot.get("tracking_ref_refresh_performed") is not False:
        errors.append("caller repo branch base snapshot must not refresh tracking refs")
    if base_snapshot.get("local_path_captured") is not False:
        errors.append("caller repo branch base snapshot must not capture local paths")
    validation = (
        artifact.get("validation")
        if isinstance(artifact.get("validation"), Mapping)
        else {}
    )
    for key in ("stdout_captured", "stderr_captured", "local_path_captured"):
        if validation.get(key) is not False:
            errors.append(f"validation {key} must be false")
    files = artifact.get("changed_files")
    if isinstance(files, list):
        for path in files:
            if not isinstance(path, str) or path.startswith("/"):
                errors.append("changed files must be repo-relative public-safe paths")
    else:
        errors.append("changed_files must be a list")
    review = (
        packet.get("review_packet")
        if isinstance(packet.get("review_packet"), Mapping)
        else {}
    )
    for key in ("external_issue_comment_performed", "external_pr_created", "merge_performed"):
        if review.get(key) is not False:
            errors.append(f"review packet {key} must be false")
    return {
        "schema_version": "issue_fix_caller_repo_branch_validation_v0",
        "ok": not errors,
        "errors": errors,
        "review_packet_ready": review.get("ready") is True,
        "changed_file_count": artifact.get("changed_file_count"),
    }


def render_issue_fix_acceptance_loop_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "# LoopX Issue Fix Acceptance Loop",
        "",
        f"- ok: `{payload.get('ok')}`",
        f"- schema_version: `{payload.get('schema_version')}`",
        f"- workspace_mode: `{payload.get('workspace_mode')}`",
        f"- external_reads_performed: `{payload.get('external_reads_performed')}`",
        f"- external_writes_performed: `{payload.get('external_writes_performed')}`",
        f"- local_paths_captured: `{payload.get('local_paths_captured')}`",
        f"- destructive_git_used: `{payload.get('destructive_git_used')}`",
    ]
    artifact = payload.get("validated_fix_artifact")
    if isinstance(artifact, Mapping):
        lines.extend(
            [
                "",
                "## Validated Fix Artifact",
                "",
                f"- fix_artifact_ready: `{artifact.get('fix_artifact_ready')}`",
                f"- pr_review_packet_ready: `{artifact.get('pr_review_packet_ready')}`",
            ]
        )
        repo_branch = artifact.get("repo_branch")
        if isinstance(repo_branch, Mapping):
            lines.extend(
                [
                    f"- repo_mode: `{repo_branch.get('repo_mode')}`",
                    f"- issue_branch: `{repo_branch.get('issue_branch')}`",
                    f"- branch_created: `{repo_branch.get('branch_created')}`",
                ]
            )
        repro = artifact.get("repro_before")
        after = artifact.get("validation_after")
        patch = artifact.get("patch")
        if isinstance(repro, Mapping) and isinstance(after, Mapping):
            lines.extend(
                [
                    f"- repro_before_passed: `{repro.get('passed')}`",
                    f"- validation_after_passed: `{after.get('passed')}`",
                ]
            )
        if isinstance(patch, Mapping):
            lines.append(f"- patch_file: `{patch.get('file')}`")
    caller_repo = payload.get("caller_repo_branch")
    if isinstance(caller_repo, Mapping):
        lines.extend(
            [
                "",
                "## Caller Repo Branch",
                "",
                f"- repo_mode: `{caller_repo.get('repo_mode')}`",
                f"- base_branch: `{caller_repo.get('base_branch')}`",
                f"- issue_branch: `{caller_repo.get('issue_branch')}`",
                f"- branch_action: `{caller_repo.get('branch_action')}`",
                f"- branch_ready: `{caller_repo.get('branch_ready')}`",
                f"- changed_file_count: `{caller_repo.get('changed_file_count')}`",
            ]
        )
        validation_command = caller_repo.get("validation")
        if isinstance(validation_command, Mapping):
            lines.append(f"- validation_passed: `{validation_command.get('passed')}`")
    review = payload.get("review_packet")
    if isinstance(review, Mapping):
        lines.extend(
            [
                "",
                "## Review Packet",
                "",
                f"- ready: `{review.get('ready')}`",
                f"- external_pr_created: `{review.get('external_pr_created')}`",
                f"- merge_performed: `{review.get('merge_performed')}`",
            ]
        )
    steps = payload.get("steps")
    if isinstance(steps, list):
        lines.extend(["", "## Steps", ""])
        for step in steps:
            if isinstance(step, Mapping):
                lines.append(f"- `{step.get('step')}`: {step.get('result')}")
    validation = payload.get("validation")
    if isinstance(validation, Mapping):
        errors = validation.get("errors") if isinstance(validation.get("errors"), list) else []
        lines.extend(
            [
                "",
                "## Validation",
                "",
                f"- validation_ok: `{validation.get('ok')}`",
                f"- validated_fix_artifact_ready: `{validation.get('validated_fix_artifact_ready')}`",
                f"- error_count: `{len(errors)}`",
            ]
        )
    if payload.get("error"):
        lines.extend(["", "## Error", "", str(payload.get("error"))])
    return "\n".join(lines) + "\n"
