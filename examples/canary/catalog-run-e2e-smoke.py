#!/usr/bin/env python3
"""Smoke-test catalog-informed canary execution without writeback."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

import loopx.canary.runner as runner_module  # noqa: E402
from loopx.canary.runner import (  # noqa: E402
    build_catalog_canary_run,
    normalize_canary_command,
)


def assert_release_readiness_gets_no_write_argument() -> None:
    normalized = normalize_canary_command("python3 examples/canary/canary-promotion-readiness-smoke.py")
    assert normalized["ok"] is True, normalized
    assert "--no-write-evidence" in normalized["argv"], normalized
    assert normalized["injected_args"] == [
        "--no-write-evidence",
        "--dashboard-mode=skip",
    ], normalized


def assert_preview_does_not_execute_or_write() -> None:
    payload = build_catalog_canary_run(
        profiles=["release-promotion"],
        max_checks_per_profile=1,
        check_limit=1,
        execute=False,
    )
    assert payload["ok"] is True, payload
    assert payload["dry_run"] is True, payload
    assert payload["executes_checks"] is False, payload
    assert payload["writes_evidence"] is False, payload
    assert payload["creates_runtime_contract"] is False, payload
    assert payload["selected_check_count"] == 1, payload
    selected = payload["selected_checks"][0]
    assert selected["normalized"]["ok"] is True, selected
    assert selected["normalized"]["script"] == (
        "examples/control_plane/promotion-readiness-readmodel-smoke.py"
    ), selected
    assert selected["normalized"]["injected_args"] == [], selected


def assert_profile_fixture_executes() -> None:
    payload = build_catalog_canary_run(
        profiles=["control-plane-refactor"],
        max_checks_per_profile=1,
        check_limit=1,
        execute=True,
        timeout_seconds=60,
    )
    assert payload["ok"] is True, payload
    assert payload["dry_run"] is False, payload
    assert payload["executes_checks"] is True, payload
    assert payload["writes_evidence"] is False, payload
    assert payload["executed_check_count"] == 1, payload
    result = payload["selected_checks"][0]
    assert result["status"] == "passed", result
    assert result["profile_id"] == "control-plane-refactor", result


def assert_install_update_preview_stays_dashboard_free() -> None:
    payload = build_catalog_canary_run(
        changed_files=["loopx/doctor.py", "examples/install-local-smoke.py"],
        max_checks_per_profile=3,
        check_limit=4,
        execute=False,
    )
    assert payload["ok"] is True, payload
    profile_ids = {profile["id"] for profile in payload["domain_profiles"]}
    assert "install-update" in profile_ids, payload
    assert "release-promotion" not in profile_ids, payload
    commands = [check["command"] for check in payload["selected_checks"]]
    assert "python3 examples/install-local-smoke.py" in commands, payload
    assert "python3 examples/loopx-update-smoke.py" in commands, payload
    assert all("canary-promotion-readiness-smoke.py" not in command for command in commands), payload
    assert all("dashboard-demo-readiness-smoke.py" not in command for command in commands), payload


def assert_domain_checks_precede_family_checks() -> None:
    payload = build_catalog_canary_run(
        changed_files=["loopx/control_plane/scheduler/monitor_todo.py"],
        surfaces=["resume_when work-lane policy seam"],
        max_checks_per_family=1,
        max_checks_per_profile=3,
        check_limit=3,
        execute=False,
    )
    assert payload["ok"] is True, payload
    commands = [check["command"] for check in payload["selected_checks"]]
    assert commands == [
        "python3 examples/control_plane/bounded-context-namespace-smoke.py",
        "python3 examples/control_plane/control-plane-risk-characterization-smoke.py",
        "python3 examples/control_plane/hot-path-interface-budget-smoke.py",
    ], payload
    assert all(check["source"] == "domain_profile" for check in payload["selected_checks"]), payload


def assert_git_required_smoke_skips_without_git_worktree() -> None:
    original_root = runner_module.REPO_ROOT
    with tempfile.TemporaryDirectory() as temp_dir:
        temp_root = Path(temp_dir).resolve()
        (temp_root / "examples" / "control_plane").mkdir(parents=True)
        runner_module.REPO_ROOT = temp_root
        try:
            payload = build_catalog_canary_run(
                catalog_path=REPO_ROOT / "docs/concepts/interaction-pattern-catalog.md",
                profiles=["repo-architecture-budget"],
                max_checks_per_profile=1,
                check_limit=1,
                execute=True,
                timeout_seconds=10,
            )
        finally:
            runner_module.REPO_ROOT = original_root

    assert payload["ok"] is True, payload
    assert payload["failure_count"] == 0, payload
    assert payload["git_required_skip_count"] == 1, payload
    result = payload["selected_checks"][0]
    assert result["status"] == "skipped_git_required", result
    assert result["ok"] is True, result
    assert result["git_required"] is True, result
    assert result["normalized"]["script"] == (
        "examples/control_plane/control-plane-maintainability-ratchet-smoke.py"
    ), result
    assert payload["git_required_skips"] == [result], payload


def assert_cli_run_executes_catalog_selected_check() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "run",
            "--changed-file",
            "loopx/quota.py",
            "--surface",
            "scheduler hint",
            "--max-checks-per-family",
            "1",
            "--max-checks-per-profile",
            "1",
            "--check-limit",
            "1",
            "--timeout-seconds",
            "60",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True, payload
    assert payload["executes_checks"] is True, payload
    assert payload["writes_evidence"] is False, payload
    assert payload["selected_check_count"] == 1, payload
    assert payload["selected_checks"][0]["status"] == "passed", payload


def main() -> int:
    assert_release_readiness_gets_no_write_argument()
    assert_preview_does_not_execute_or_write()
    assert_profile_fixture_executes()
    assert_install_update_preview_stays_dashboard_free()
    assert_domain_checks_precede_family_checks()
    assert_git_required_smoke_skips_without_git_worktree()
    assert_cli_run_executes_catalog_selected_check()
    print("catalog-canary-run-e2e-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
