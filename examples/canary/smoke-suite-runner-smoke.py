#!/usr/bin/env python3
"""Smoke-test the canary smoke-suite runner contract."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from loopx.canary import runner as canary_runner  # noqa: E402
from loopx.canary.runner import (  # noqa: E402
    build_canary_smoke_suite_profiles,
    build_canary_smoke_suite_run,
)


def assert_default_public_preview_excludes_grouped_smokes() -> None:
    payload = build_canary_smoke_suite_run(
        suite="default-public",
        execute=False,
        limit=20,
    )
    assert payload["ok"] is True, payload
    assert payload["dry_run"] is True, payload
    commands = [check["command"] for check in payload["selected_checks"]]
    assert commands, payload
    assert all("canary-promotion-readiness-smoke.py" not in item for item in commands), payload
    assert all("dashboard-demo-readiness-smoke.py" not in item for item in commands), payload


def assert_full_public_preview_injects_safe_group_args() -> None:
    payload = build_canary_smoke_suite_run(
        suite="full-public",
        scripts=[
            "examples/canary/canary-promotion-readiness-smoke.py",
            "dashboard-demo-readiness-smoke.py",
        ],
        execute=False,
    )
    assert payload["ok"] is True, payload
    by_script = {
        check["normalized"]["script"]: check["normalized"]
        for check in payload["selected_checks"]
    }
    assert "--no-write-evidence" in by_script["examples/canary/canary-promotion-readiness-smoke.py"]["argv"], payload
    assert "--dashboard-mode=skip" in by_script["examples/canary/canary-promotion-readiness-smoke.py"]["argv"], payload
    assert "--skip-browser" in by_script["examples/dashboard-demo-readiness-smoke.py"]["argv"], payload


def assert_module_preview_selects_matching_scripts() -> None:
    payload = build_canary_smoke_suite_run(
        suite="default-public",
        modules=["quota"],
        execute=False,
        limit=10,
    )
    assert payload["ok"] is True, payload
    assert payload["selected_check_count"] > 0, payload
    commands = [check["command"] for check in payload["selected_checks"]]
    assert all("quota" in command for command in commands), payload


def assert_module_preview_supports_exclusions() -> None:
    payload = build_canary_smoke_suite_run(
        suite="full-public",
        modules=["status"],
        exclude_modules=["benchmark", "skillsbench", "terminal-bench", "swe-marathon"],
        execute=False,
    )
    assert payload["ok"] is True, payload
    assert payload["warning_count"] == 0, payload
    assert payload["selected_check_count"] > 0, payload
    scripts = [check["normalized"]["script"] for check in payload["selected_checks"]]
    assert all("status" in script for script in scripts), payload
    assert all("benchmark" not in script for script in scripts), payload
    assert all("skillsbench" not in script for script in scripts), payload
    assert all("terminal-bench" not in script for script in scripts), payload
    assert payload["selection_inputs"]["exclude_modules"] == [
        "benchmark",
        "skillsbench",
        "terminal-bench",
        "swe-marathon",
    ], payload


def assert_subdirectory_smoke_selection_is_supported() -> None:
    script = "examples/canary/smoke-suite-subdir-discovery-smoke.py"
    for selector in [script, "canary/smoke-suite-subdir-discovery-smoke.py", Path(script).name]:
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=[selector],
            execute=False,
        )
        assert payload["ok"] is True, payload
        assert payload["warning_count"] == 0, payload
        assert payload["selected_check_count"] == 1, payload
        assert payload["selected_checks"][0]["normalized"]["script"] == script, payload

    module_payload = build_canary_smoke_suite_run(
        suite="default-public",
        modules=["canary"],
        scripts=[script],
        execute=False,
    )
    assert module_payload["ok"] is True, module_payload
    assert module_payload["selected_check_count"] == 1, module_payload


def assert_legacy_root_script_selector_matches_moved_smokes() -> None:
    moved_scripts = {
        "examples/readme-demo-surface-smoke.py": "examples/public_entry/readme-demo-surface-smoke.py",
        "examples/release-readiness-doc-smoke.py": "examples/release/release-readiness-doc-smoke.py",
    }
    for legacy_selector, expected_script in moved_scripts.items():
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=[legacy_selector],
            execute=False,
        )
        assert payload["ok"] is True, payload
        assert payload["warning_count"] == 0, payload
        assert payload["selected_check_count"] == 1, payload
        assert payload["selected_checks"][0]["normalized"]["script"] == expected_script, payload


def assert_catalog_profile_preview_is_supported() -> None:
    payload = build_canary_smoke_suite_run(
        suite="catalog-plan",
        profiles=["repo-architecture-budget"],
        execute=False,
    )
    assert payload["ok"] is True, payload
    assert payload["selected_check_count"] == 2, payload
    commands = [check["command"] for check in payload["selected_checks"]]
    assert commands == [
        "python3 examples/control_plane/control-plane-maintainability-ratchet-smoke.py",
        "python3 examples/semantic-vocabulary-drift-smoke.py",
    ], payload
    assert payload["catalog_plan"]["planned_check_count"] == 2, payload


def assert_named_smoke_profile_expands_to_suite_selection() -> None:
    payload = build_canary_smoke_suite_run(
        suite="default-public",
        profiles=["core-control-plane"],
        execute=False,
    )
    assert payload["ok"] is True, payload
    assert payload["suite"] == "full-public", payload
    assert payload["catalog_plan"] is None, payload
    assert payload["selection_inputs"]["profiles"] == ["core-control-plane"], payload
    assert payload["selection_inputs"]["smoke_profiles"] == ["core-control-plane"], payload
    assert payload["selection_inputs"]["catalog_profiles"] == [], payload
    assert "quota" in payload["selection_inputs"]["modules"], payload
    assert "benchmark" in payload["selection_inputs"]["exclude_modules"], payload
    scripts = [check["normalized"]["script"] for check in payload["selected_checks"]]
    assert scripts, payload
    assert any(script.startswith("examples/control_plane/") for script in scripts), payload
    assert "examples/control_plane/bounded-context-namespace-smoke.py" in scripts, payload
    assert all("benchmark" not in script for script in scripts), payload
    assert all("skillsbench" not in script for script in scripts), payload


def assert_smoke_profile_offset_windows_selection() -> None:
    first_window = build_canary_smoke_suite_run(
        suite="default-public",
        profiles=["core-control-plane"],
        execute=False,
        limit=5,
    )
    second_window = build_canary_smoke_suite_run(
        suite="default-public",
        profiles=["core-control-plane"],
        execute=False,
        offset=5,
        limit=5,
    )
    assert first_window["ok"] is True, first_window
    assert second_window["ok"] is True, second_window
    assert first_window["matched_check_count"] >= 10, first_window
    assert second_window["matched_check_count"] == first_window["matched_check_count"], second_window
    assert first_window["offset"] == 0, first_window
    assert second_window["offset"] == 5, second_window
    assert first_window["limit"] == 5, first_window
    assert second_window["limit"] == 5, second_window
    assert first_window["selected_check_count"] == 5, first_window
    assert second_window["selected_check_count"] == 5, second_window
    assert second_window["selection_inputs"]["offset"] == 5, second_window
    first_scripts = [
        check["normalized"]["script"] for check in first_window["selected_checks"]
    ]
    second_scripts = [
        check["normalized"]["script"] for check in second_window["selected_checks"]
    ]
    assert set(first_scripts).isdisjoint(second_scripts), {
        "first": first_scripts,
        "second": second_scripts,
    }


def assert_named_smoke_profiles_are_discoverable() -> None:
    payload = build_canary_smoke_suite_profiles()
    assert payload["ok"] is True, payload
    assert payload["schema_version"] == "canary_smoke_suite_profiles_v0", payload
    assert payload["executes_checks"] is False, payload
    assert payload["writes_evidence"] is False, payload
    profiles = {profile["id"]: profile for profile in payload["profiles"]}
    for profile_id in [
        "core-control-plane",
        "canary-runner",
        "public-entry-install-release",
        "docs-project-content-ops",
        "extension-runtime",
        "public-smoke-watch",
    ]:
        assert profile_id in profiles, payload
        assert profiles[profile_id]["modules"], payload
        assert "benchmark" in profiles[profile_id]["exclude_modules"], payload


def assert_public_smoke_watch_profile_covers_health_surfaces() -> None:
    payload = build_canary_smoke_suite_run(
        suite="default-public",
        profiles=["public-smoke-watch"],
        execute=False,
    )
    assert payload["ok"] is True, payload
    assert payload["suite"] == "full-public", payload
    assert payload["selection_inputs"]["smoke_profiles"] == ["public-smoke-watch"], payload
    scripts = [check["normalized"]["script"] for check in payload["selected_checks"]]
    assert "examples/canary/smoke-suite-runner-smoke.py" in scripts, payload
    assert "examples/control_plane/control-plane-maintainability-ratchet-smoke.py" in scripts, payload
    assert "examples/issue-fix-workflow-e2e-smoke.py" in scripts, payload
    assert any("monitor" in script for script in scripts), payload
    assert all("skillsbench" not in script for script in scripts), payload
    assert all("terminal-bench" not in script for script in scripts), payload


def assert_named_smoke_profile_can_mix_with_catalog_profile() -> None:
    payload = build_canary_smoke_suite_run(
        suite="default-public",
        profiles=["canary-runner", "repo-architecture-budget"],
        execute=False,
    )
    assert payload["ok"] is True, payload
    assert payload["selection_inputs"]["smoke_profiles"] == ["canary-runner"], payload
    assert payload["selection_inputs"]["catalog_profiles"] == ["repo-architecture-budget"], payload
    assert payload["catalog_plan"]["planned_check_count"] == 2, payload
    commands = [check["command"] for check in payload["selected_checks"]]
    assert any("examples/canary/" in command for command in commands), payload
    assert all("canary-promotion" not in command for command in commands), payload
    assert any("examples/control_plane/control-plane-maintainability-ratchet-smoke.py" in command for command in commands), payload
    assert any("examples/semantic-vocabulary-drift-smoke.py" in command for command in commands), payload


def assert_cli_json_preview_works() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "smoke-suite",
            "--suite",
            "default-public",
            "--module",
            "status",
            "--exclude-module",
            "benchmark",
            "--limit",
            "2",
            "--no-execute",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True, payload
    assert payload["schema_version"] == "canary_smoke_suite_run_v0", payload
    assert payload["selected_check_count"] == 2, payload
    assert payload["selection_inputs"]["exclude_modules"] == ["benchmark"], payload
    assert payload["executes_checks"] is False, payload


def assert_cli_named_smoke_profile_preview_works() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "smoke-suite",
            "--profile",
            "canary-runner",
            "--offset",
            "1",
            "--limit",
            "3",
            "--no-execute",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True, payload
    assert payload["suite"] == "full-public", payload
    assert payload["offset"] == 1, payload
    assert payload["selected_check_count"] == 3, payload
    assert payload["matched_check_count"] >= 4, payload
    assert payload["selection_inputs"]["smoke_profiles"] == ["canary-runner"], payload
    assert payload["selection_inputs"]["catalog_profiles"] == [], payload
    scripts = [check["normalized"]["script"] for check in payload["selected_checks"]]
    assert any(script.startswith("examples/canary/") for script in scripts), payload
    assert all("canary-promotion" not in script for script in scripts), payload
    assert all("benchmark" not in script for script in scripts), payload
    assert payload["executes_checks"] is False, payload


def assert_cli_named_smoke_profiles_list_works() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "loopx.cli",
            "--format",
            "json",
            "canary",
            "smoke-profiles",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True, payload
    assert payload["schema_version"] == "canary_smoke_suite_profiles_v0", payload
    profile_ids = {profile["id"] for profile in payload["profiles"]}
    assert "core-control-plane" in profile_ids, payload
    assert "canary-runner" in profile_ids, payload


def assert_run_smokes_profile_preview_matches_runner_selection() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "examples/run-smokes.py",
            "--json",
            "--profile",
            "core-control-plane",
            "--exclude-module",
            "status",
            "--offset",
            "1",
            "--limit",
            "2",
            "--no-execute",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    payload = json.loads(completed.stdout)
    assert payload["ok"] is True, payload
    assert payload["offset"] == 1, payload
    assert payload["selection_inputs"]["smoke_profiles"] == ["core-control-plane"], payload
    assert "status" in payload["selection_inputs"]["exclude_modules"], payload
    scripts = [check["normalized"]["script"] for check in payload["selected_checks"]]
    assert scripts, payload
    assert all("benchmark" not in script for script in scripts), payload
    assert all("status" not in script for script in scripts), payload


def assert_run_smokes_help_describes_recursive_discovery() -> None:
    completed = subprocess.run(
        [
            sys.executable,
            "examples/run-smokes.py",
            "--help",
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        stdout=subprocess.PIPE,
    )
    assert "examples/**/*-smoke.py" in completed.stdout, completed.stdout


def assert_execution_reports_progress_indices() -> None:
    events: list[dict[str, object]] = []
    payload = build_canary_smoke_suite_run(
        suite="default-public",
        scripts=["todo-contract-smoke.py"],
        execute=True,
        timeout_seconds=60,
        progress_callback=events.append,
    )
    assert payload["ok"] is True, payload
    assert payload["executed_check_count"] == 1, payload
    check = payload["selected_checks"][0]
    assert check["check_index"] == 1, payload
    assert check["check_count"] == 1, payload
    assert [event["event"] for event in events] == ["check_started", "check_finished"], events
    assert events[0]["check_index"] == 1, events
    assert events[0]["check_count"] == 1, events
    assert events[1]["status"] == "passed", events


def assert_parallel_jobs_execute_and_preserve_report_order() -> None:
    original_run_check = canary_runner._run_check
    original_tracked_change_paths = canary_runner._tracked_change_paths
    events: list[dict[str, object]] = []

    def fake_tracked_change_paths(repo_root: Path | None = None) -> tuple[bool, list[str], str]:
        assert repo_root == REPO_ROOT.resolve(), repo_root
        return True, [], ""

    try:
        canary_runner._run_check = _fake_passed_check
        canary_runner._tracked_change_paths = fake_tracked_change_paths
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=[
                "todo-contract-smoke.py",
                "canary/smoke-suite-subdir-discovery-smoke.py",
            ],
            execute=True,
            timeout_seconds=60,
            parallel_jobs=4,
            progress_callback=events.append,
        )
    finally:
        canary_runner._run_check = original_run_check
        canary_runner._tracked_change_paths = original_tracked_change_paths

    assert payload["ok"] is True, payload
    assert payload["parallel_jobs"] == 4, payload
    assert payload["effective_parallel_jobs"] == 2, payload
    assert payload["executed_check_count"] == 2, payload
    assert [check["check_index"] for check in payload["selected_checks"]] == [1, 2], payload
    assert [event["event"] for event in events].count("check_started") == 2, events
    assert [event["event"] for event in events].count("check_finished") == 2, events
    rendered = canary_runner.render_canary_smoke_suite_run_markdown(payload)
    assert "- parallel_jobs: `4`" in rendered, rendered
    assert "- effective_parallel_jobs: `2`" in rendered, rendered
    assert "- serial_check_count: `0`" in rendered, rendered


def assert_parallel_jobs_keep_marked_smokes_serial() -> None:
    sensitive_scripts = {
        "issue-fix-acceptance-loop-smoke.py": "temporary git repositories",
        "issue-fix-workflow-e2e-smoke.py": "temporary git repository",
    }
    for script, expected_reason in sensitive_scripts.items():
        reason = canary_runner._serial_smoke_reason(
            {"command": f"python3 examples/{script}"}
        )
        assert expected_reason in reason, (script, reason)
    original_run_check = canary_runner._run_check
    original_tracked_change_paths = canary_runner._tracked_change_paths
    observed: list[tuple[str, int | None]] = []

    def fake_tracked_change_paths(repo_root: Path | None = None) -> tuple[bool, list[str], str]:
        assert repo_root == REPO_ROOT.resolve(), repo_root
        return True, [], ""

    def fake_run_check(
        check: dict[str, object],
        *,
        timeout_seconds: float,
        check_index: int | None = None,
        check_count: int | None = None,
        repo_root: Path | None = None,
    ) -> dict[str, object]:
        normalized = canary_runner.normalize_canary_command(str(check.get("command") or ""), repo_root=repo_root)
        observed.append((str(normalized.get("script") or ""), check_index))
        return _fake_passed_check(
            check,
            timeout_seconds=timeout_seconds,
            check_index=check_index,
            check_count=check_count,
        )

    try:
        canary_runner._run_check = fake_run_check
        canary_runner._tracked_change_paths = fake_tracked_change_paths
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=[
                "issue-fix-workflow-e2e-smoke.py",
                "todo-contract-smoke.py",
                "canary/smoke-suite-subdir-discovery-smoke.py",
            ],
            execute=True,
            timeout_seconds=60,
            parallel_jobs=4,
        )
    finally:
        canary_runner._run_check = original_run_check
        canary_runner._tracked_change_paths = original_tracked_change_paths

    assert payload["ok"] is True, payload
    assert payload["parallel_jobs"] == 4, payload
    assert payload["effective_parallel_jobs"] == 2, payload
    assert payload["serial_check_count"] == 1, payload
    assert observed[-1][0] == "examples/issue-fix-workflow-e2e-smoke.py", observed
    serial_check = next(
        check
        for check in payload["selected_checks"]
        if check.get("serial_execution_required")
    )
    assert serial_check["serial_execution_required"] is True, payload
    assert "temporary git repository" in serial_check["serial_execution_reason"], payload
    rendered = canary_runner.render_canary_smoke_suite_run_markdown(payload)
    assert "- serial_check_count: `1`" in rendered, rendered
    assert "serial_execution_reason" in rendered, rendered


def assert_fail_fast_keeps_parallel_jobs_serial() -> None:
    original_run_check = canary_runner._run_check
    original_tracked_change_paths = canary_runner._tracked_change_paths

    def fake_tracked_change_paths(repo_root: Path | None = None) -> tuple[bool, list[str], str]:
        assert repo_root == REPO_ROOT.resolve(), repo_root
        return True, [], ""

    try:
        canary_runner._run_check = _fake_passed_check
        canary_runner._tracked_change_paths = fake_tracked_change_paths
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=[
                "todo-contract-smoke.py",
                "canary/smoke-suite-subdir-discovery-smoke.py",
            ],
            execute=True,
            timeout_seconds=60,
            fail_fast=True,
            parallel_jobs=4,
        )
    finally:
        canary_runner._run_check = original_run_check
        canary_runner._tracked_change_paths = original_tracked_change_paths

    assert payload["ok"] is True, payload
    assert payload["parallel_jobs"] == 4, payload
    assert payload["effective_parallel_jobs"] == 1, payload


def assert_git_probe_contract_is_explicit() -> None:
    ok, paths, detail = canary_runner._tracked_change_paths()
    assert isinstance(paths, list), paths
    if ok:
        assert detail == "", detail
        return
    assert detail.startswith(("not_a_git_worktree:", "git_diff_failed:")), detail


def _fake_passed_check(
    check: dict[str, object],
    *,
    timeout_seconds: float,
    check_index: int | None = None,
    check_count: int | None = None,
    repo_root: Path | None = None,
) -> dict[str, object]:
    normalized = canary_runner.normalize_canary_command(str(check.get("command") or ""), repo_root=repo_root)
    result: dict[str, object] = {
        **check,
        "normalized": normalized,
        "status": "passed",
        "ok": True,
        "returncode": 0,
        "duration_seconds": 0.0,
        "stdout_tail": "",
        "stderr_tail": "",
    }
    if check_index is not None and check_count is not None:
        result.update({"check_index": check_index, "check_count": check_count})
    return result


def assert_readonly_run_rejects_and_restores_tracked_side_effects() -> None:
    original_run_check = canary_runner._run_check
    original_tracked_change_paths = canary_runner._tracked_change_paths
    original_restore_tracked_paths = canary_runner._restore_tracked_paths
    restored: list[list[str]] = []
    calls = {"tracked": 0}

    def fake_tracked_change_paths(repo_root: Path | None = None) -> tuple[bool, list[str], str]:
        assert repo_root == REPO_ROOT.resolve(), repo_root
        calls["tracked"] += 1
        if calls["tracked"] == 1 or restored:
            return True, [], ""
        return True, ["examples/generated-tracked-side-effect.txt"], ""

    def fake_restore_tracked_paths(paths: list[str], repo_root: Path | None = None) -> dict[str, object]:
        restored.append(paths)
        return {"ok": True, "restored_paths": paths}

    try:
        canary_runner._run_check = _fake_passed_check
        canary_runner._tracked_change_paths = fake_tracked_change_paths
        canary_runner._restore_tracked_paths = fake_restore_tracked_paths
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=["todo-contract-smoke.py"],
            execute=True,
            timeout_seconds=60,
        )
    finally:
        canary_runner._run_check = original_run_check
        canary_runner._tracked_change_paths = original_tracked_change_paths
        canary_runner._restore_tracked_paths = original_restore_tracked_paths

    assert payload["ok"] is False, payload
    assert payload["writes_evidence"] is False, payload
    assert payload["tracked_side_effect_failure_count"] == 1, payload
    assert payload["side_effect_guard"]["enforced"] is True, payload
    assert (
        payload["side_effect_guard"]["enforcement_reason"]
        == "tracked_side_effect_guard_active"
    ), payload
    assert payload["side_effect_guard"]["auto_restored"] is True, payload
    assert restored == [["examples/generated-tracked-side-effect.txt"]], payload
    check = payload["selected_checks"][0]
    assert check["status"] == "failed_tracked_side_effect", payload
    assert check["tracked_side_effects"] == ["examples/generated-tracked-side-effect.txt"], payload
    rendered = canary_runner.render_canary_smoke_suite_run_markdown(payload)
    assert "- tracked_side_effect_failures: `1`" in rendered, rendered
    assert "- read_only_guard_reason: `tracked_side_effect_guard_active`" in rendered, rendered
    assert "- tracked_side_effect_restore_ok: `true`" in rendered, rendered


def assert_unavailable_git_worktree_is_reported_explicitly() -> None:
    original_run_check = canary_runner._run_check
    original_tracked_change_paths = canary_runner._tracked_change_paths

    def fake_tracked_change_paths(repo_root: Path | None = None) -> tuple[bool, list[str], str]:
        assert repo_root == REPO_ROOT.resolve(), repo_root
        return False, [], "not_a_git_worktree: fixture"

    try:
        canary_runner._run_check = _fake_passed_check
        canary_runner._tracked_change_paths = fake_tracked_change_paths
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=["todo-contract-smoke.py"],
            execute=True,
            timeout_seconds=60,
        )
    finally:
        canary_runner._run_check = original_run_check
        canary_runner._tracked_change_paths = original_tracked_change_paths

    assert payload["ok"] is True, payload
    assert payload["writes_evidence"] is False, payload
    assert payload["tracked_side_effect_failure_count"] == 0, payload
    assert payload["side_effect_guard"]["git_status_ok"] is False, payload
    assert payload["side_effect_guard"]["enforced"] is False, payload
    assert payload["side_effect_guard"]["enforcement_reason"] == "git_worktree_unavailable", payload
    assert (
        payload["side_effect_guard"]["git_status_unavailable_reason"]
        == "not_a_git_worktree: fixture"
    ), payload
    rendered = canary_runner.render_canary_smoke_suite_run_markdown(payload)
    assert "- read_only_guard_enforced: `false`" in rendered, rendered
    assert "- read_only_guard_reason: `git_worktree_unavailable`" in rendered, rendered


def assert_tracked_side_effects_require_explicit_allow() -> None:
    original_run_check = canary_runner._run_check
    original_tracked_change_paths = canary_runner._tracked_change_paths
    original_restore_tracked_paths = canary_runner._restore_tracked_paths

    def fake_tracked_change_paths(repo_root: Path | None = None) -> tuple[bool, list[str], str]:
        assert repo_root == REPO_ROOT.resolve(), repo_root
        return True, [], ""

    def fail_restore_tracked_paths(paths: list[str]) -> dict[str, object]:
        raise AssertionError(f"restore should not run when side effects are allowed: {paths}")

    try:
        canary_runner._run_check = _fake_passed_check
        canary_runner._tracked_change_paths = fake_tracked_change_paths
        canary_runner._restore_tracked_paths = fail_restore_tracked_paths
        payload = build_canary_smoke_suite_run(
            suite="default-public",
            scripts=["todo-contract-smoke.py"],
            execute=True,
            timeout_seconds=60,
            allow_tracked_side_effects=True,
        )
    finally:
        canary_runner._run_check = original_run_check
        canary_runner._tracked_change_paths = original_tracked_change_paths
        canary_runner._restore_tracked_paths = original_restore_tracked_paths

    assert payload["ok"] is True, payload
    assert payload["writes_evidence"] is True, payload
    assert payload["tracked_side_effect_failure_count"] == 0, payload
    assert payload["side_effect_guard"]["tracked_side_effects_allowed"] is True, payload
    assert payload["side_effect_guard"]["enforced"] is False, payload
    assert (
        payload["side_effect_guard"]["enforcement_reason"]
        == "tracked_side_effects_explicitly_allowed"
    ), payload
    rendered = canary_runner.render_canary_smoke_suite_run_markdown(payload)
    assert "- writes_evidence: `true`" in rendered, rendered
    assert "- read_only_guard_enforced: `false`" in rendered, rendered
    assert "- read_only_guard_reason: `tracked_side_effects_explicitly_allowed`" in rendered, rendered


def main() -> int:
    assert_default_public_preview_excludes_grouped_smokes()
    assert_full_public_preview_injects_safe_group_args()
    assert_module_preview_selects_matching_scripts()
    assert_module_preview_supports_exclusions()
    assert_subdirectory_smoke_selection_is_supported()
    assert_legacy_root_script_selector_matches_moved_smokes()
    assert_catalog_profile_preview_is_supported()
    assert_named_smoke_profiles_are_discoverable()
    assert_public_smoke_watch_profile_covers_health_surfaces()
    assert_named_smoke_profile_expands_to_suite_selection()
    assert_smoke_profile_offset_windows_selection()
    assert_named_smoke_profile_can_mix_with_catalog_profile()
    assert_cli_json_preview_works()
    assert_cli_named_smoke_profile_preview_works()
    assert_cli_named_smoke_profiles_list_works()
    assert_run_smokes_profile_preview_matches_runner_selection()
    assert_run_smokes_help_describes_recursive_discovery()
    assert_execution_reports_progress_indices()
    assert_parallel_jobs_execute_and_preserve_report_order()
    assert_parallel_jobs_keep_marked_smokes_serial()
    assert_fail_fast_keeps_parallel_jobs_serial()
    assert_git_probe_contract_is_explicit()
    assert_readonly_run_rejects_and_restores_tracked_side_effects()
    assert_unavailable_git_worktree_is_reported_explicitly()
    assert_tracked_side_effects_require_explicit_allow()
    print("canary-smoke-suite-runner-smoke ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
